"""自研 SQLCipher 页处理的自洽性自测。

做法：先用 sqlite3 造一个真实明文库，再用本模块的逆过程按 SQLCipher 布局加密，
最后跑 probe_params + decrypt_database，验证能还原成 SQLite 可打开的库。

这只证明"页布局、HMAC、密钥派生、文件拼装"在本实现内自洽，
不等于已与真实微信库交叉验证。真实验证需要真实密钥。
"""

import hashlib
import hmac
import os
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app import sqlcipher


def encrypt_database(plain: bytes, key: bytes, params: sqlcipher.CipherParams) -> bytes:
    salt = os.urandom(sqlcipher.SALT_SIZE)
    enc_key = sqlcipher._derive_encryption_key(key, salt, params)
    hmac_key = sqlcipher._derive_hmac_key(enc_key, salt, params)
    body_size = params.page_size - params.reserve
    output = bytearray()
    for index in range(len(plain) // params.page_size):
        page = plain[index * params.page_size : (index + 1) * params.page_size]
        number = index + 1
        iv = os.urandom(sqlcipher.IV_SIZE)
        body_plain = page[sqlcipher.SALT_SIZE : body_size] if number == 1 else page[:body_size]
        encryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(body_plain) + encryptor.finalize()
        body = (salt + ciphertext) if number == 1 else ciphertext
        digest = hmac.new(
            hmac_key, body + iv + struct.pack("<I", number), params.hmac_hash
        ).digest()
        output.extend(body + iv + digest)
    return bytes(output)


def main() -> int:
    params = sqlcipher.CipherParams()
    key = os.urandom(32)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sample.db"
        conn = sqlite3.connect(source)
        with conn:
            conn.execute("PRAGMA page_size=4096")
            conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, note TEXT)")
            conn.executemany(
                "INSERT INTO demo(note) VALUES (?)", [("甲",), ("乙",), ("丙",)]
            )
        conn.close()
        plain = source.read_bytes()
        assert plain.startswith(sqlcipher.SQLITE_MAGIC), "样本库不是明文 SQLite"
        assert len(plain) % params.page_size == 0

        encrypted = encrypt_database(plain, key, params)
        assert not encrypted.startswith(sqlcipher.SQLITE_MAGIC)

        probed = sqlcipher.probe_params(encrypted, key)
        assert probed is not None, "参数探测失败"
        assert probed.page_size == params.page_size and probed.raw_key

        restored, pages, hmac_valid = sqlcipher.decrypt_database(encrypted, key, probed)
        target = Path(tmp) / "restored.sqlite"
        target.write_bytes(restored)
        assert restored.startswith(sqlcipher.SQLITE_MAGIC), "还原后缺少 SQLite 文件头"
        # 不能用"sqlite3 打开并查询"做判定：合成明文库没有 SQLCipher 的 reserve 区，
        # 而 SQLite 的 B-tree 单元从页尾分配，解密时把 reserve 置零必然破坏它。
        # 真正的"能打开并读出表"验证，必须留给真实微信库（或真实 SQLCipher 产物）。
        body_size = probed.page_size - probed.reserve
        for index in range(pages):
            start = index * probed.page_size
            assert restored[start : start + body_size] == plain[start : start + body_size], (
                f"第 {index + 1} 页有效区域不一致"
            )

        wrong = sqlcipher.probe_params(encrypted, os.urandom(32))
        assert wrong is None, "错误密钥不应探测成功"

        print(f"自测通过：{pages} 页，HMAC 通过 {hmac_valid} 页，各页有效区域与原始库一致")
        print("注意：这是自洽性自测；'能打开并读出表'尚未验证，需真实微信库与真实密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
