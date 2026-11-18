import os
import re
from datetime import datetime
from pathlib import Path

ACCOUNT_DIR_PATTERN = re.compile(r"^(?P<wxid>wxid_[0-9a-z]+)(?:_[0-9a-f]+)?$", re.IGNORECASE)
SKIPPED_DIR_NAMES = {"all_users", "backup"}


def scan_roots(roots: list[str]) -> dict:
    accounts = []
    errors = []
    for root_text in roots:
        root = Path(root_text)
        if not root.is_dir():
            errors.append({"root": str(root), "message": "这个目录不存在，或当前账号没有权限读取。"})
            continue
        try:
            entries = list(os.scandir(root))
        except OSError as exc:
            errors.append({"root": str(root), "message": f"读取目录失败：{exc.strerror or exc}"})
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name.lower() in SKIPPED_DIR_NAMES:
                continue
            match = ACCOUNT_DIR_PATTERN.match(entry.name)
            if not match:
                continue
            account_path = Path(entry.path)
            try:
                stats = _collect_stats(account_path)
            except OSError as exc:
                errors.append({"root": str(account_path), "message": f"统计目录失败：{exc.strerror or exc}"})
                continue
            accounts.append(
                {
                    "wxid": match.group("wxid"),
                    "dir_name": entry.name,
                    "path": str(account_path),
                    **stats,
                }
            )
    accounts.sort(key=lambda item: item["db_storage_mtime"] or "", reverse=True)
    if accounts:
        accounts[0]["is_most_recent"] = True
    return {"accounts": accounts, "errors": errors}


def _collect_stats(account_path: Path) -> dict:
    total_bytes = 0
    section_bytes = {}
    latest_db_storage = 0.0
    for dirpath, _dirnames, filenames in os.walk(account_path):
        relative = Path(dirpath).relative_to(account_path)
        section = relative.parts[0] if relative.parts else ""
        for filename in filenames:
            try:
                info = os.stat(os.path.join(dirpath, filename))
            except OSError:
                continue
            total_bytes += info.st_size
            section_bytes[section] = section_bytes.get(section, 0) + info.st_size
            if section == "db_storage" and info.st_mtime > latest_db_storage:
                latest_db_storage = info.st_mtime
    return {
        "total_bytes": total_bytes,
        "msg_bytes": section_bytes.get("msg", 0),
        "db_storage_bytes": section_bytes.get("db_storage", 0),
        "db_storage_mtime": _format_time(latest_db_storage),
        "has_db_storage": (account_path / "db_storage").is_dir(),
        "has_msg": (account_path / "msg").is_dir(),
        "nickname": None,
    }


def _format_time(timestamp: float) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
