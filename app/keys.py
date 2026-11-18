"""密钥的读取、保存与有效性校验。

密钥只写入程序根目录 keys/（该目录已 gitignore），不写日志、不外发。
"""

import json
import re
from pathlib import Path

from .config import KEYS_DIR

HEX_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def parse_hex_key(text: str) -> bytes:
    cleaned = text.strip().replace(" ", "").replace(":", "")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if not HEX_KEY_PATTERN.match(cleaned):
        raise ValueError("密钥应当是 64 个十六进制字符（32 字节）")
    return bytes.fromhex(cleaned)


def load_key_entries(path: Path) -> list[bytes]:
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = []
    if isinstance(data, dict):
        for field in ("key", "key_hex", "db_key", "raw_key"):
            if isinstance(data.get(field), str):
                candidates.append(data[field])
        keys = data.get("keys")
        if isinstance(keys, list):
            candidates.extend(item for item in keys if isinstance(item, str))
        elif isinstance(keys, dict):
            candidates.extend(item for item in keys.values() if isinstance(item, str))
    elif isinstance(data, list):
        candidates.extend(item for item in data if isinstance(item, str))
    if not candidates:
        raise ValueError("密钥文件里没有找到可识别的密钥字段")
    return [parse_hex_key(item) for item in candidates]


def save_key(account_dir_name: str, key_hex: str, extra: dict | None = None) -> Path:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    target = KEYS_DIR / f"{account_dir_name}.json"
    payload = {"key_hex": key_hex}
    if extra:
        payload.update(extra)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_saved_key(account_dir_name: str) -> dict | None:
    target = KEYS_DIR / f"{account_dir_name}.json"
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data.get("key_hex"), str):
        return None
    return data


def save_db_keys(account_dir_name: str, db_keys: dict[str, bytes]) -> Path:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    target = KEYS_DIR / f"{account_dir_name}.dbs.json"
    merged = load_db_keys(account_dir_name)
    merged.update(db_keys)
    payload = {
        "version": 1,
        "dbs": {name: {"key_hex": key.hex()} for name, key in merged.items()},
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_db_keys(account_dir_name: str) -> dict[str, bytes]:
    target = KEYS_DIR / f"{account_dir_name}.dbs.json"
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        entries = data["dbs"]
        return {
            name: parse_hex_key(value["key_hex"])
            for name, value in entries.items()
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}
