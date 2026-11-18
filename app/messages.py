import base64
import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import zstandard

from .config import CACHE_DIR
from .media import extract_media_id
from .scanner import ACCOUNT_DIR_PATTERN

MESSAGE_TABLE_PATTERN = re.compile(r"^Msg_([0-9a-f]{32})$")
DECOMPRESSOR = zstandard.ZstdDecompressor()


class CollectionCancelled(Exception):
    pass


def collect_messages(
    account_dir: str,
    selected_conversations: list[dict],
    start_timestamp: int | None,
    end_timestamp: int | None,
    progress_callback,
    is_cancelled,
) -> tuple[dict[str, list[dict]], list[str]]:
    plain_dir = CACHE_DIR / account_dir / "plain"
    selected = {item["username"]: item for item in selected_conversations}
    collected = {username: [] for username in selected}
    warnings = []
    contact_names = _read_contact_names(plain_dir / "contact.sqlite")
    match = ACCOUNT_DIR_PATTERN.fullmatch(account_dir)
    own_username = match.group("wxid") if match else ""
    scanned = 0

    for path in sorted(plain_dir.glob("message_[0-9]*.sqlite"), key=_message_db_order):
        _check_cancel(is_cancelled)
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            id_names = {row[0]: row[1] for row in conn.execute("SELECT rowid, user_name FROM Name2Id")}
            table_users = {hashlib.md5(name.encode("utf-8")).hexdigest(): name for name in id_names.values()}
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                table_match = MESSAGE_TABLE_PATTERN.fullmatch(table)
                if not table_match:
                    continue
                username = table_users.get(table_match.group(1))
                if username not in selected:
                    continue
                quoted = table.replace('"', '""')
                sql = f'''SELECT local_id, server_id, local_type, sort_seq, real_sender_id, create_time,
                                 source, message_content, packed_info_data,
                                 COALESCE(WCDB_CT_source, 0), COALESCE(WCDB_CT_message_content, 0)
                          FROM "{quoted}"'''
                clauses = []
                params = []
                if start_timestamp is not None:
                    clauses.append("create_time >= ?")
                    params.append(start_timestamp)
                if end_timestamp is not None:
                    clauses.append("create_time < ?")
                    params.append(end_timestamp)
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                sql += " ORDER BY create_time, sort_seq, server_id, local_id"
                for row in conn.execute(sql, params):
                    _check_cancel(is_cancelled)
                    scanned += 1
                    message, warning = _build_message(
                        row, path.stem, table, username, selected[username],
                        id_names, contact_names, own_username,
                    )
                    if warning:
                        warnings.append(warning)
                    if message is not None:
                        collected[username].append(message)
                    if scanned % 100 == 0:
                        progress_callback(scanned)
        finally:
            conn.close()

    for username, messages in collected.items():
        messages.sort(key=_message_order)
        deduplicated, duplicate_count, conflict_count = _deduplicate(messages)
        collected[username] = deduplicated
        if duplicate_count:
            warnings.append(f"{username} 去除了 {duplicate_count} 条明确重复的跨库消息")
        if conflict_count:
            warnings.append(f"{username} 有 {conflict_count} 条相同 server_id 但内容不同的消息，已全部保留")
    progress_callback(scanned)
    return collected, warnings


def _build_message(row, source_db, source_table, username, conversation, id_names, contact_names, own_username):
    (
        local_id, server_id, local_type, sort_seq, real_sender_id, create_time,
        source, content, packed_info_data, source_flag, content_flag,
    ) = row
    base_type = local_type & 0xFFFFFFFF
    sender_username = id_names.get(real_sender_id, "")
    if base_type == 10000:
        direction = "system"
    elif sender_username and sender_username == own_username:
        direction = "self"
    elif sender_username:
        direction = "other"
    else:
        direction = "unknown"
    try:
        raw = _decode_content(content, content_flag)
        raw_encoding = "utf-8"
        decode_warning = None
    except (UnicodeDecodeError, zstandard.ZstdError, ValueError) as exc:
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
        raw = base64.b64encode(raw_bytes).decode("ascii")
        raw_encoding = "base64"
        decode_warning = f"{source_db}/{source_table}/local_id={local_id} 解码失败：{type(exc).__name__}，raw 已按 Base64 保留"

    if raw_encoding == "utf-8":
        display_text = raw
        if base_type == 1 and username.endswith("@chatroom") and sender_username:
            prefix = f"{sender_username}:\n"
            if display_text.startswith(prefix):
                display_text = display_text[len(prefix):]
        message_type, app_type, readable = _summarize(base_type, display_text)
    else:
        message_type, app_type, readable = f"unknown_{base_type}", None, "[消息内容无法解码，已保留 Base64 raw]"
    try:
        decoded_source = _decode_content(source, source_flag) if source else ""
    except (UnicodeDecodeError, zstandard.ZstdError, ValueError):
        decoded_source = ""
    sender_name = contact_names.get(sender_username, "") or sender_username or "未知发送者"
    message = {
        "message_uid": f"{source_db}:{source_table}:{local_id}",
        "conversation": username,
        "conversation_name": conversation.get("remark") or conversation.get("nickname") or username,
        "sender_username": sender_username,
        "sender_name": sender_name,
        "real_sender_id": real_sender_id,
        "direction": direction,
        "timestamp": create_time,
        "time": _format_time(create_time),
        "local_type": local_type,
        "base_type": base_type,
        "app_type": app_type,
        "message_type": message_type,
        "content": readable,
        "raw": raw,
        "raw_encoding": raw_encoding,
        "source": decoded_source,
        "server_id": server_id,
        "sort_seq": sort_seq,
        "local_id": local_id,
        "source_db": source_db,
        "source_table": source_table,
    }
    if base_type in {3, 34, 43, 47}:
        message.update({
            "media_id": extract_media_id(packed_info_data),
            "media_path": None,
            "media_tier": None,
            "media_status": "not_requested",
        })
    if base_type == 47 and raw_encoding == "utf-8":
        root = _parse_xml(raw)
        emoji = root.find(".//emoji") if root is not None else None
        if emoji is not None:
            md5 = emoji.get("md5") or emoji.get("androidmd5") or ""
            message["emoji_md5"] = md5 if re.fullmatch(r"[0-9a-f]{32}", md5) else None
            size_text = emoji.get("len", "")
            message["emoji_size"] = int(size_text) if size_text.isdigit() else None
            if not message.get("media_id") and message["emoji_md5"]:
                message["media_id"] = message["emoji_md5"]
    if message_type == "file":
        root = _parse_xml(raw)
        file_name = _element_text(root, "./appmsg/title") if root is not None else ""
        size_text = _element_text(root, "./appmsg/appattach/totallen") if root is not None else ""
        message.update({
            "file_name": file_name or None,
            "file_size": int(size_text) if size_text.isdigit() else None,
            "media_path": None,
            "media_tier": None,
            "media_status": "not_requested",
        })
    return message, decode_warning


def _summarize(base_type: int, text: str) -> tuple[str, str | None, str]:
    if base_type == 1:
        return "text", None, text
    if base_type == 3:
        return "image", None, "[图片]"
    if base_type == 34:
        return "voice", None, "[语音]"
    if base_type == 43:
        return "video", None, "[视频]"
    if base_type == 47:
        return "emoji", None, "[表情]"
    if base_type == 50:
        return "call", None, "[通话消息]"
    if base_type == 10000:
        return "system", None, text
    if base_type != 49:
        return f"unknown_{base_type}", None, text or f"[未知消息类型 {base_type}]"

    root = _parse_xml(text)
    if root is None:
        return "app", None, text or "[应用消息]"
    app_type = _element_text(root, "./appmsg/type")
    title = _element_text(root, "./appmsg/title")
    description = _element_text(root, "./appmsg/des")
    if app_type == "6":
        filename = title or "未读取到文件名"
        size = _format_bytes(_element_text(root, "./appmsg/appattach/totallen"))
        return "file", app_type, f"[文件] {filename}" + (f"（{size}）" if size else "")
    if app_type == "57":
        quoted_name = _element_text(root, "./appmsg/refermsg/displayname")
        quoted_content = _element_text(root, "./appmsg/refermsg/content")
        quoted = "\n引用"
        if quoted_name:
            quoted += f" {quoted_name}"
        if quoted_content:
            quoted += f"：{quoted_content}"
        return "quote", app_type, (title or "[引用消息]") + quoted
    if app_type == "2001":
        details = "；".join(dict.fromkeys(part for part in (title, description) if part))
        return "transfer", app_type, "[转账]" + (f" {details}" if details else "")
    if app_type == "62":
        template = _element_text(root, "./appmsg/patinfo/template")
        return "pat", app_type, "[拍一拍]" + (f" {title or template}" if title or template else "")
    if app_type == "33":
        details = title or description
        return "mini_program", app_type, "[小程序]" + (f" {details}" if details else "")
    if app_type == "1":
        details = title or description
        return "share", app_type, "[分享]" + (f" {details}" if details else "")
    details = title or description or _element_text(root, "./appmsg/content") or text
    label = f"app_{app_type}" if app_type else "app"
    return label, app_type, details


def _decode_content(content, compression_flag: int) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    raw = bytes(content)
    if compression_flag == 4:
        with DECOMPRESSOR.stream_reader(raw) as reader:
            raw = reader.read()
    elif compression_flag != 0:
        raise ValueError(f"未知压缩标记 {compression_flag}")
    return raw.decode("utf-8")


def _parse_xml(text: str) -> ET.Element | None:
    start = text.find("<")
    if start < 0:
        return None
    try:
        return ET.fromstring(text[start:])
    except ET.ParseError:
        return None


def _element_text(root: ET.Element, path: str) -> str:
    value = root.findtext(path)
    return value.strip() if value else ""


def _deduplicate(messages: list[dict]) -> tuple[list[dict], int, int]:
    result = []
    seen = {}
    duplicate_count = 0
    conflict_count = 0
    for message in messages:
        server_id = message["server_id"]
        if not server_id:
            result.append(message)
            continue
        previous = seen.get(server_id)
        if previous is None:
            seen[server_id] = message
            result.append(message)
            continue
        if previous["timestamp"] == message["timestamp"] and previous["raw"] == message["raw"]:
            duplicate_count += 1
            continue
        conflict_count += 1
        result.append(message)
    return result, duplicate_count, conflict_count


def _read_contact_names(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            username: remark or nickname or ""
            for username, nickname, remark in conn.execute(
                "SELECT username, nick_name, remark FROM contact"
            )
        }
    finally:
        conn.close()


def _message_order(message: dict) -> tuple:
    return (
        message["timestamp"], message["sort_seq"], message["server_id"],
        message["local_id"], message["source_db"], message["source_table"],
    )


def _message_db_order(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"message_(\d+)\.sqlite", path.name)
    return (int(match.group(1)) if match else 0, path.name)


def _format_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds")


def _format_bytes(value: str) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return ""


def _check_cancel(is_cancelled) -> None:
    if is_cancelled():
        raise CollectionCancelled()
