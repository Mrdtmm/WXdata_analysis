import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import decrypt, keys
from .config import CACHE_DIR, OUTPUT_DIR

INDEX_DIR = OUTPUT_DIR / "_index"
MESSAGE_TABLE_PATTERN = re.compile(r"^Msg_([0-9a-f]{32})$")
CONTACT_DB = "db_storage/contact/contact.db"


def build_index(account: Path, reuse_cache: bool = False) -> dict:
    account_dir = account.name
    db_keys = keys.load_db_keys(account_dir)
    if not db_keys:
        raise RuntimeError("还没有分库密钥，请先在第 2 步准备消息库密钥")

    raw_dir = CACHE_DIR / account_dir / "raw"
    if reuse_cache:
        message_dbs = sorted(raw_dir.glob("message_[0-9]*.db"), key=_message_db_order)
    else:
        message_dbs = sorted(
            (account / "db_storage" / "message").glob("message_[0-9]*.db"),
            key=_message_db_order,
        )
    if not message_dbs:
        raise RuntimeError("找不到 message_数字.db 消息数据库")

    relative_dbs = [CONTACT_DB] + [f"db_storage/message/{path.name}" for path in message_dbs]
    missing = [name for name in relative_dbs if name not in db_keys]
    if missing:
        raise RuntimeError("这些数据库还没有密钥：" + "、".join(Path(name).name for name in missing))

    plain_paths = {}
    database_reports = {}
    for relative_db in relative_dbs:
        if reuse_cache:
            snapshot = raw_dir / Path(relative_db).name
            if not snapshot.is_file():
                raise RuntimeError(f"缺少缓存副本：{Path(relative_db).name}")
        else:
            snapshot = decrypt.snapshot_db(account, relative_db)
        report = decrypt.decrypt_snapshot(snapshot, db_keys[relative_db])
        if not report.get("ok"):
            raise RuntimeError(f"{Path(relative_db).name} 解密失败：{report.get('reason', '未知原因')}")
        plain_paths[relative_db] = Path(report["plain_file"])
        database_reports[relative_db] = {
            "pages": report["pages"],
            "hmac_valid_pages": report["hmac_valid_pages"],
        }

    contact_map = _read_contacts(plain_paths[CONTACT_DB])
    conversations, warnings = _read_conversations(plain_paths, contact_map)
    conversations.sort(key=lambda item: item["last_timestamp"], reverse=True)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "version": 1,
        "account_dir": account_dir,
        "generated_at": generated_at,
        "main_db_only": True,
        "wal_merged": False,
        "cache_reused": reuse_cache,
        "conversation_count": len(conversations),
        "total_messages": sum(item["message_count"] for item in conversations),
        "conversations": conversations,
        "warnings": warnings,
        "databases": database_reports,
    }
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    target = INDEX_DIR / f"{account_dir}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return payload


def load_index(account_dir: str) -> dict | None:
    target = INDEX_DIR / f"{account_dir}.json"
    if not target.is_file():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def _read_contacts(path: Path) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT username, nick_name, remark, local_type FROM contact"
        ).fetchall()
    finally:
        conn.close()
    return {
        username: {"nickname": nickname or "", "remark": remark or "", "local_type": local_type}
        for username, nickname, remark, local_type in rows
    }


def _read_conversations(plain_paths: dict[str, Path], contact_map: dict[str, dict]) -> tuple[list[dict], list[str]]:
    entries = {}
    warnings = []
    for relative_db, path in plain_paths.items():
        if relative_db == CONTACT_DB:
            continue
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            mappings = {
                hashlib.md5(username.encode("utf-8")).hexdigest(): username
                for username, in conn.execute("SELECT user_name FROM Name2Id")
            }
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                match = MESSAGE_TABLE_PATTERN.fullmatch(table)
                if not match:
                    continue
                username = mappings.get(match.group(1))
                if username is None:
                    warnings.append(f"{path.stem}/{table} 没有会话映射，未计入列表")
                    continue
                quoted = table.replace('"', '""')
                count, first, last = conn.execute(
                    f'SELECT COUNT(*), MIN(create_time), MAX(create_time) FROM "{quoted}"'
                ).fetchone()
                if not count:
                    continue
                entry = entries.setdefault(
                    username,
                    {
                        "username": username,
                        "nickname": contact_map.get(username, {}).get("nickname", ""),
                        "remark": contact_map.get(username, {}).get("remark", ""),
                        "session_type": "群聊" if username.endswith("@chatroom") else "单聊",
                        "message_count": 0,
                        "first_timestamp": first,
                        "last_timestamp": last,
                        "source_dbs": set(),
                    },
                )
                entry["message_count"] += count
                entry["first_timestamp"] = min(entry["first_timestamp"], first)
                entry["last_timestamp"] = max(entry["last_timestamp"], last)
                entry["source_dbs"].add(path.name)
        finally:
            conn.close()

    conversations = []
    for entry in entries.values():
        entry["source_dbs"] = sorted(entry["source_dbs"])
        entry["first_time"] = _format_time(entry["first_timestamp"])
        entry["last_time"] = _format_time(entry["last_timestamp"])
        conversations.append(entry)
    return conversations, warnings


def _format_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds")


def _message_db_order(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"message_(\d+)\.db", path.name)
    return (int(match.group(1)) if match else 0, path.name)
