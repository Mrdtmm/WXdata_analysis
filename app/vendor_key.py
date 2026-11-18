import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import decrypt, keys
from .config import CACHE_DIR, ROOT

VENDOR_SCRIPT = ROOT / "vendor" / "wcdb-key-tool" / "wcdb_key_tool_windows.py"


def _safe_output(text: str) -> str:
    redacted = re.sub(r"(?i)\b[0-9a-f]{64}\b", "<已隐藏>", text)
    lines = redacted.strip().splitlines()
    return "\n".join(lines[-12:])[-4000:]


def _extract_keys(account: Path, relative_dbs: list[str], scan_name: str) -> dict[str, bytes]:
    if not VENDOR_SCRIPT.is_file():
        raise RuntimeError("第三方密钥组件缺失：vendor/wcdb-key-tool")
    scan_dir = CACHE_DIR / account.name / scan_name
    for relative_db in relative_dbs:
        snapshot = decrypt.snapshot_db(account, relative_db)
        target = scan_dir / relative_db
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot, target)
    output = CACHE_DIR / account.name / f"{scan_name}_keys.json"
    output.unlink(missing_ok=True)
    isolated_home = CACHE_DIR / "vendor_home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "HOME": str(isolated_home),
        "USERPROFILE": str(isolated_home),
        "APPDATA": str(isolated_home),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    command = [
        sys.executable,
        str(VENDOR_SCRIPT),
        "extract",
        "--db-dir",
        str(scan_dir),
        "--output",
        str(output),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            detail = _safe_output(result.stdout + "\n" + result.stderr)
            raise RuntimeError(f"第三方密钥提取失败：\n{detail or '没有错误详情'}")
        if not output.is_file():
            raise RuntimeError("第三方工具结束但没有生成密钥结果")
        data = json.loads(output.read_text(encoding="utf-8"))
        extracted = {}
        for name, value in data.items():
            if name.startswith("_") or not isinstance(value, dict):
                continue
            normalized = name.replace("\\", "/")
            extracted[normalized] = keys.parse_hex_key(str(value.get("enc_key", "")))
        missing = [name for name in relative_dbs if name not in extracted]
        if missing:
            raise RuntimeError("这些数据库没有取得密钥：" + "、".join(missing))
        return {name: extracted[name] for name in relative_dbs}
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("第三方密钥提取超过 10 分钟，已停止") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"第三方密钥结果无法解析：{exc}") from exc
    finally:
        output.unlink(missing_ok=True)


def extract_contact_key(account: Path) -> bytes:
    relative_db = "db_storage/contact/contact.db"
    return _extract_keys(account, [relative_db], "vendor_scan_contact")[relative_db]


def extract_message_keys(account: Path) -> dict[str, bytes]:
    message_dir = account / "db_storage" / "message"
    message_dbs = sorted(message_dir.glob("message_[0-9]*.db"))
    if not message_dbs:
        raise RuntimeError("找不到 message_数字.db 消息数据库")
    relative_dbs = ["db_storage/contact/contact.db"] + [
        f"db_storage/message/{path.name}" for path in message_dbs
    ]
    return _extract_keys(account, relative_dbs, "vendor_scan_messages")


def extract_media_keys(account: Path) -> tuple[dict[str, bytes], list[dict]]:
    message_dir = account / "db_storage" / "message"
    relative_dbs = [
        f"db_storage/message/{path.name}"
        for path in sorted(message_dir.glob("media_*.db"))
        if path.is_file() and re.fullmatch(r"media_[0-9]+\.db", path.name)
    ]
    relative_dbs.extend(
        name for name in (
            "db_storage/message/message_resource.db",
            "db_storage/hardlink/hardlink.db",
            "db_storage/emoticon/emoticon.db",
        ) if (account / name).is_file()
    )
    if not relative_dbs:
        raise RuntimeError("所选账号没有找到可准备的媒体数据库")
    scan_name = f"vendor_scan_media_{uuid.uuid4().hex}"
    try:
        db_keys = _extract_keys(account, relative_dbs, scan_name)
        reports = []
        for name in relative_dbs:
            snapshot = CACHE_DIR / account.name / "raw" / Path(name).name
            report = decrypt.decrypt_snapshot(snapshot, db_keys[name])
            if not report.get("ok"):
                raise RuntimeError(f"媒体库校验失败：{name}；本次媒体密钥未保存")
            reports.append({"database": name, **report})
        return db_keys, reports
    finally:
        shutil.rmtree(CACHE_DIR / account.name / scan_name, ignore_errors=True)
