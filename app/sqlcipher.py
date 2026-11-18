"""SQLCipher 数据库页的解密。

只使用标准库 hashlib/hmac 与 cryptography 的 AES，页布局处理为本项目自研。
密钥来源由调用方提供，本模块不读取任何进程、不联网、不写日志。

注意：页布局常量（salt 位置、IV/HMAC 位置、HMAC 输入构成）按 SQLCipher 公开格式实现，
在拿到真实密钥并用真实微信库验证之前，兼容性标记为"未验证"。
"""

import hashlib
import hmac
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SQLITE_MAGIC = b"SQLite format 3\x00"
SALT_SIZE = 16
IV_SIZE = 16
HMAC_KEY_ITERATIONS = 2
HMAC_SALT_XOR = 0x3A


@dataclass(frozen=True)
class CipherParams:
    page_size: int = 4096
    hmac_hash: str = "sha512"
    kdf_hash: str = "sha512"
    kdf_iterations: int = 256000
    raw_key: bool = True

    @property
    def hmac_size(self) -> int:
        return hashlib.new(self.hmac_hash).digest_size

    @property
    def reserve(self) -> int:
        return IV_SIZE + self.hmac_size


def _derive_encryption_key(key: bytes, salt: bytes, params: CipherParams) -> bytes:
    if params.raw_key:
        if len(key) != 32:
            raise ValueError("raw key 模式要求 32 字节密钥")
        return key
    return hashlib.pbkdf2_hmac(
        params.kdf_hash, key, salt, params.kdf_iterations, dklen=32
    )


def _derive_hmac_key(enc_key: bytes, salt: bytes, params: CipherParams) -> bytes:
    hmac_salt = bytes(byte ^ HMAC_SALT_XOR for byte in salt)
    return hashlib.pbkdf2_hmac(
        params.hmac_hash, enc_key, hmac_salt, HMAC_KEY_ITERATIONS, dklen=32
    )


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def decrypt_page(
    page: bytes, page_number: int, enc_key: bytes, hmac_key: bytes, params: CipherParams
) -> tuple[bytes, bool]:
    reserve = params.reserve
    body_size = params.page_size - reserve
    body = page[:body_size]
    iv = page[body_size : body_size + IV_SIZE]
    stored_hmac = page[body_size + IV_SIZE : params.page_size]
    expected = hmac.new(
        hmac_key, body + iv + struct.pack("<I", page_number), params.hmac_hash
    ).digest()
    hmac_ok = hmac.compare_digest(expected, stored_hmac)
    # 第 1 页的前 16 字节是明文 salt，不参与解密；其余页整页密文。
    encrypted = page[SALT_SIZE:body_size] if page_number == 1 else body
    plain = _aes_cbc_decrypt(enc_key, iv, encrypted)
    if page_number == 1:
        plain = SQLITE_MAGIC + plain
    return plain + bytes(reserve), hmac_ok


def decrypt_database(
    source: bytes, key: bytes, params: CipherParams
) -> tuple[bytes, int, int]:
    if len(source) < params.page_size:
        raise ValueError("文件小于一个页，不是有效的加密数据库")
    salt = source[:SALT_SIZE]
    enc_key = _derive_encryption_key(key, salt, params)
    hmac_key = _derive_hmac_key(enc_key, salt, params)
    output = bytearray()
    total_pages = len(source) // params.page_size
    hmac_valid = 0
    for index in range(total_pages):
        start = index * params.page_size
        page = source[start : start + params.page_size]
        plain, ok = decrypt_page(page, index + 1, enc_key, hmac_key, params)
        output.extend(plain)
        if ok:
            hmac_valid += 1
    return bytes(output), total_pages, hmac_valid


def looks_like_sqlite_header(page_one_plain: bytes, page_size: int) -> bool:
    if not page_one_plain.startswith(SQLITE_MAGIC):
        return False
    declared = struct.unpack(">H", page_one_plain[16:18])[0]
    if declared == 1:
        declared = 65536
    return declared == page_size and page_one_plain[18] in (1, 2)


def probe_params(source: bytes, key: bytes) -> CipherParams | None:
    """用第一页实测参数，不凭记忆认定。全部失败返回 None。"""
    candidates = []
    for page_size in (4096, 8192, 16384, 2048, 1024):
        if len(source) % page_size:
            continue
        for raw_key in (True, False):
            for hmac_hash, kdf_hash, iterations in (
                ("sha512", "sha512", 256000),
                ("sha1", "sha1", 64000),
                ("sha256", "sha256", 256000),
            ):
                if not raw_key and iterations == 256000 and hmac_hash != "sha512":
                    continue
                candidates.append(
                    CipherParams(
                        page_size=page_size,
                        hmac_hash=hmac_hash,
                        kdf_hash=kdf_hash,
                        kdf_iterations=iterations,
                        raw_key=raw_key,
                    )
                )
    for params in candidates:
        try:
            plain, _ = decrypt_page(
                source[: params.page_size], 1,
                _derive_encryption_key(key, source[:SALT_SIZE], params),
                _derive_hmac_key(
                    _derive_encryption_key(key, source[:SALT_SIZE], params),
                    source[:SALT_SIZE],
                    params,
                ),
                params,
            )
        except Exception:
            continue
        if looks_like_sqlite_header(plain, params.page_size):
            return params
    return None
