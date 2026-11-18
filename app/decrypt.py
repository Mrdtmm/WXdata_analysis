"""把加密库复制到 cache/ 副本，再解密验证。源目录全程只读。"""

import shutil
import sqlite3
from pathlib import Path

from . import sqlcipher
from .config import CACHE_DIR

SNAPSHOT_SUFFIXES = ("", "-wal", "-shm", "-first.material", "-incremental.material", "-last.material")


def snapshot_db(account_path: Path, relative_db: str) -> Path:
    source = account_path / relative_db
    if not source.is_file():
        raise FileNotFoundError(f"找不到数据库文件：{relative_db}")
    target_dir = CACHE_DIR / account_path.name / "raw"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    for suffix in SNAPSHOT_SUFFIXES[1:]:
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, Path(str(target) + suffix))
    return target


def decrypt_snapshot(snapshot: Path, key: bytes) -> dict:
    source = snapshot.read_bytes()
    params = sqlcipher.probe_params(source, key)
    if params is None:
        return {
            "ok": False,
            "reason": "用这把钥匙和现有参数组合都打不开这个库。可能是密钥不对，也可能是这个库的加密参数还没覆盖到。",
        }
    plain, total_pages, hmac_valid = sqlcipher.decrypt_database(source, key, params)
    plain_dir = CACHE_DIR / snapshot.parent.parent.name / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    target = plain_dir / f"{snapshot.stem}.sqlite"
    target.write_bytes(plain)
    tables = []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 20"
        ).fetchall()
        tables = [row[0] for row in rows]
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "reason": f"文件头解出来了，但 SQLite 打不开：{exc}",
            "params": _params_dict(params),
        }
    finally:
        if conn is not None:
            conn.close()
    return {
        "ok": True,
        "params": _params_dict(params),
        "pages": total_pages,
        "hmac_valid_pages": hmac_valid,
        "hmac_note": "HMAC 校验用于参考；页数对不上不代表解密失败" if hmac_valid != total_pages else "全部页 HMAC 校验通过",
        "tables": tables,
        "plain_file": str(target),
        "wal_present": Path(str(snapshot) + "-wal").exists(),
        "wal_note": "本次只解密主库文件，未合并 -wal，最近尚未落库的消息可能缺失",
    }


def _params_dict(params: sqlcipher.CipherParams) -> dict:
    return {
        "page_size": params.page_size,
        "hmac_hash": params.hmac_hash,
        "kdf_hash": params.kdf_hash,
        "kdf_iterations": params.kdf_iterations,
        "raw_key": params.raw_key,
    }
