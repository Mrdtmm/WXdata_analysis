import json
import sqlite3
import string
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import contacts, decrypt, exporter, keys, processes, scanner, vendor_key
from .config import load_config

UI_DIR = Path(__file__).resolve().parent / "ui"

app = FastAPI(title="微信聊天记录提取工具", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


class ScanRequest(BaseModel):
    root: str | None = None


class ManualKeyRequest(BaseModel):
    root: str
    account_dir: str
    key_hex: str


class MemoryKeyRequest(BaseModel):
    root: str
    account_dir: str
    offline_confirmed: bool
    risk_confirmed: bool
    scope: str = "contact"
    media_confirmed: bool = False


class RevalidateRequest(BaseModel):
    root: str
    account_dir: str


class ContactBuildRequest(BaseModel):
    root: str
    account_dir: str


class ExportRequest(BaseModel):
    root: str
    account_dir: str
    usernames: list[str]
    date_mode: str = "all"
    start_date: str | None = None
    end_date: str | None = None
    formats: list[str]
    include_images: bool = False
    image_quality: str = "best"
    include_files: bool = False
    include_videos: bool = False
    include_voices: bool = False
    include_emojis: bool = False


class ExportJobRequest(BaseModel):
    job_id: str


def _account_path(root: str, account_dir: str) -> Path:
    account = (Path(root) / account_dir).resolve()
    if account.parent != Path(root).resolve():
        raise ValueError("账号目录不合法")
    if not scanner.ACCOUNT_DIR_PATTERN.match(account_dir):
        raise ValueError("账号目录名不合法")
    if not account.is_dir():
        raise ValueError("账号目录不存在")
    return account


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/config")
def read_config() -> dict:
    config = load_config()
    roots = [{"path": item, "exists": Path(item).is_dir()} for item in config["wechat_data_roots"]]
    return {"roots": roots}


@app.post("/api/scan")
async def scan(request: ScanRequest) -> dict:
    config = load_config()
    if request.root and request.root.strip():
        roots = [request.root.strip()]
    else:
        roots = config["wechat_data_roots"]
    if not roots:
        return {"accounts": [], "errors": [{"root": "", "message": "还没有配置数据目录，请先选择或填写。"}]}
    return await run_in_threadpool(scanner.scan_roots, roots)


def _list_drives() -> list[dict]:
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if Path(drive).exists():
            drives.append({"name": f"{letter}:", "path": drive})
    return drives


@app.get("/api/browse")
def browse_directory(path: str = "") -> dict:
    drives = _list_drives()
    if not path or not path.strip():
        return {"ok": True, "path": "", "parent": None, "dirs": [], "wxid_count": 0, "drives": drives}
    target = Path(path.strip()).resolve()
    if not target.is_dir():
        return {"ok": False, "reason": "目录不存在或不是文件夹", "drives": drives}
    dirs = []
    wxid_count = 0
    try:
        entries = sorted(target.iterdir(), key=lambda entry: entry.name.lower())
    except PermissionError:
        return {"ok": False, "reason": "没有权限访问此目录", "drives": drives}
    except OSError as exc:
        return {"ok": False, "reason": f"读取目录失败：{exc}", "drives": drives}
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        is_wxid = bool(scanner.ACCOUNT_DIR_PATTERN.match(entry.name))
        if is_wxid:
            wxid_count += 1
        dirs.append({"name": entry.name, "path": str(entry), "is_wxid": is_wxid})
    parent = str(target.parent) if target.parent != target else None
    return {"ok": True, "path": str(target), "parent": parent, "dirs": dirs, "wxid_count": wxid_count, "drives": drives}


@app.get("/api/memory/processes")
def memory_processes() -> dict:
    try:
        instances = processes.list_weixin_instances()
        return {
            "ok": True,
            "instances": instances,
            "instance_count": len(instances),
            "can_extract": len(instances) == 1,
        }
    except OSError as exc:
        return {"ok": False, "reason": f"读取微信进程列表失败：{exc}", "instances": []}


@app.post("/api/memory/extract")
async def memory_extract(request: MemoryKeyRequest) -> dict:
    if not (request.offline_confirmed and request.risk_confirmed):
        return {"ok": False, "reason": "必须先确认已自行断网并了解内存读取风险"}
    if request.scope == "media" and not request.media_confirmed:
        return {"ok": False, "reason": "必须另外确认本次扩展到媒体数据库的提取范围"}
    try:
        account = _account_path(request.root, request.account_dir)
        instances = processes.list_weixin_instances()
    except (ValueError, OSError) as exc:
        return {"ok": False, "reason": str(exc)}
    if not instances:
        return {"ok": False, "reason": "没有找到正在运行的微信，请先登录所选账号"}
    if len(instances) != 1:
        return {
            "ok": False,
            "reason": f"检测到 {len(instances)} 个微信登录实例。多微信读取冲突，已停止；请只保留所选账号登录。",
            "instance_count": len(instances),
        }
    if request.scope not in {"contact", "messages", "media"}:
        return {"ok": False, "reason": "未知的密钥提取范围"}
    try:
        if request.scope == "media":
            db_keys, reports = await run_in_threadpool(vendor_key.extract_media_keys, account)
            keys.save_db_keys(request.account_dir, db_keys)
            return {"ok": True, "media_key_count": len(db_keys), "databases": reports}
        if request.scope == "messages":
            db_keys = await run_in_threadpool(vendor_key.extract_message_keys, account)
            key = db_keys["db_storage/contact/contact.db"]
        else:
            db_keys = {}
            key = await run_in_threadpool(vendor_key.extract_contact_key, account)
        report = await run_in_threadpool(_validate_and_save, account, request.account_dir, key)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {"ok": False, "reason": str(exc)}
    if report.get("ok"):
        if db_keys:
            keys.save_db_keys(request.account_dir, db_keys)
            report["message_key_count"] = len(db_keys) - 1
        report["key_hex"] = key.hex()
    return report


@app.get("/api/key/status")
def key_status(root: str, account_dir: str) -> dict:
    saved = keys.load_saved_key(account_dir)
    db_keys = keys.load_db_keys(account_dir)
    message_dbs = sorted(
        name for name in db_keys
        if name.startswith("db_storage/message/message_")
        and name[len("db_storage/message/message_"):].split(".")[0].isdigit()
    )
    media_dbs = sorted(
        name for name in db_keys
        if name not in message_dbs and name != "db_storage/contact/contact.db"
    )
    return {
        "configured": saved is not None,
        "params": saved.get("params") if saved else None,
        "validated_db": saved.get("validated_db") if saved else None,
        "db_keys": {
            "total": len(db_keys),
            "contact": "db_storage/contact/contact.db" in db_keys,
            "message_count": len(message_dbs),
            "message_dbs": message_dbs,
            "media_count": len(media_dbs),
            "media_dbs": media_dbs,
        },
    }


@app.post("/api/key/revalidate")
async def revalidate_key(request: RevalidateRequest) -> dict:
    try:
        account = _account_path(request.root, request.account_dir)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    saved = keys.load_saved_key(request.account_dir)
    if not saved:
        return {"ok": False, "reason": "这个账号还没有保存的密钥，请先提取或手动输入密钥"}
    try:
        key = keys.parse_hex_key(saved["key_hex"])
    except (KeyError, ValueError) as exc:
        return {"ok": False, "reason": f"已保存的密钥格式无效：{exc}"}
    return await run_in_threadpool(_validate_and_save, account, request.account_dir, key)


@app.post("/api/key/manual")
async def manual_key(request: ManualKeyRequest) -> dict:
    try:
        account = _account_path(request.root, request.account_dir)
        key = keys.parse_hex_key(request.key_hex)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    return await run_in_threadpool(_validate_and_save, account, request.account_dir, key)


@app.get("/api/contacts")
def read_contacts(account_dir: str) -> dict:
    if not scanner.ACCOUNT_DIR_PATTERN.fullmatch(account_dir):
        return {"ok": False, "reason": "账号目录名不合法"}
    try:
        index = contacts.load_index(account_dir)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"读取联系人索引失败：{exc}"}
    return {"ok": True, "configured": index is not None, "index": index}


@app.post("/api/contacts/build")
async def build_contacts(request: ContactBuildRequest) -> dict:
    try:
        account = _account_path(request.root, request.account_dir)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    if not keys.load_db_keys(request.account_dir):
        return {"ok": False, "reason": "还没有分库密钥，请先在第 2 步准备消息库密钥"}
    try:
        instances = processes.list_weixin_instances()
    except OSError as exc:
        return {"ok": False, "reason": f"读取微信进程列表失败：{exc}"}
    if instances:
        return {"ok": False, "reason": "复制数据库快照前请先完全退出微信，再点「生成联系人列表」。已保存的密钥不会受影响。"}
    try:
        index = await run_in_threadpool(contacts.build_index, account)
    except (FileNotFoundError, RuntimeError, OSError, sqlite3.Error) as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "index": index}


@app.post("/api/export/start")
def start_export(request: ExportRequest) -> dict:
    if not scanner.ACCOUNT_DIR_PATTERN.fullmatch(request.account_dir):
        return {"ok": False, "reason": "账号目录名不合法"}
    try:
        account = _account_path(request.root, request.account_dir)
        job = exporter.start_export(
            account,
            request.account_dir,
            request.usernames,
            request.date_mode,
            request.start_date,
            request.end_date,
            request.formats,
            request.include_images,
            request.image_quality,
            request.include_files,
            request.include_videos,
            request.include_voices,
            request.include_emojis,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "job": job}


@app.get("/api/export/status")
def export_status(job_id: str) -> dict:
    try:
        return {"ok": True, "job": exporter.get_export(job_id)}
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}


@app.post("/api/export/cancel")
def export_cancel(request: ExportJobRequest) -> dict:
    try:
        changed = exporter.cancel_export(request.job_id)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "changed": changed}


@app.post("/api/export/open")
def export_open(request: ExportJobRequest) -> dict:
    try:
        output_dir = exporter.open_export(request.job_id)
    except (ValueError, RuntimeError, OSError) as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "output_dir": output_dir}


def _validate_and_save(account: Path, account_dir: str, key: bytes) -> dict:
    try:
        snapshot = decrypt.snapshot_db(account, "db_storage/contact/contact.db")
    except FileNotFoundError as exc:
        return {"ok": False, "reason": str(exc)}
    report = decrypt.decrypt_snapshot(snapshot, key)
    if report.get("ok"):
        keys.save_key(
            account_dir,
            key.hex(),
            {"params": report["params"], "validated_db": "contact.db"},
        )
    return report


def main() -> None:
    config = load_config()
    host = config["server"]["host"]
    port = int(config["server"]["port"])
    if config["server"].get("open_browser", True):
        url = f"http://{host}:{port}/"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
