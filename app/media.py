import base64
import configparser
import hashlib
import io
import os
import re
import shutil
import sqlite3
import stat
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import decrypt, keys
from .config import CACHE_DIR

try:
    import pilk as _pilk
except ImportError:
    _pilk = None

V2_MAGIC = bytes.fromhex("070856320807")
MEDIA_ID_PATTERN = re.compile(rb"[0-9a-f]{32}")
MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")
HEVC_VPS_PATTERN = re.compile(rb"\x00\x00\x00\x01\x40\x01")
IMAGE_QUALITIES = {"best", "thumb"}


class ImageDecoder:
    def __init__(self, account_dir: str):
        self.account_dir = account_dir
        self.wxid, suffix = _split_account_dir(account_dir)
        self.uin = _resolve_uin(suffix)
        self.aes_key = hashlib.md5(f"{self.uin}{self.wxid}".encode("utf-8")).hexdigest()[:16].encode("ascii")
        self.xor_key = self.uin & 0xFF

    def export_conversation(
        self,
        account: Path,
        username: str,
        messages: list[dict],
        conversation_dir: Path,
        quality: str,
        check_cancel,
    ) -> dict:
        stats = {
            "image_messages": 0,
            "exported": 0,
            "missing": 0,
            "failed": 0,
            "unique_files": 0,
            "hd": 0,
            "standard": 0,
            "thumbnail": 0,
        }
        resolved = {}
        for message in messages:
            if message.get("base_type") != 3:
                continue
            stats["image_messages"] += 1
            check_cancel()
            media_id = message.get("media_id")
            if not media_id:
                stats["missing"] += 1
                _mark_media(message, "missing")
                continue
            if media_id not in resolved:
                resolved[media_id] = self._decode_one(
                    account, username, message, media_id, conversation_dir, quality,
                )
            result = resolved[media_id]
            if result["status"] == "exported":
                message["media_path"] = result["media_path"]
                message["media_tier"] = result["media_tier"]
                message["media_status"] = "exported"
                stats["exported"] += 1
                stats[result["media_tier"]] += 1
            elif result["status"] == "missing":
                stats["missing"] += 1
                _mark_media(message, "missing")
            else:
                stats["failed"] += 1
                _mark_media(message, "failed")
        stats["unique_files"] = sum(
            1 for result in resolved.values() if result["status"] == "exported"
        )
        return stats

    def _decode_one(
        self,
        account: Path,
        username: str,
        message: dict,
        media_id: str,
        conversation_dir: Path,
        quality: str,
    ) -> dict:
        sources = _find_sources(account, username, message["timestamp"], media_id, quality)
        if not sources:
            return {"status": "missing"}
        for source_path, tier in sources:
            target = None
            try:
                snapshot_dir = CACHE_DIR / self.account_dir / "media_raw" / hashlib.md5(username.encode("utf-8")).hexdigest()
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot = snapshot_dir / source_path.name
                if snapshot.exists():
                    snapshot.chmod(snapshot.stat().st_mode | stat.S_IWRITE)
                shutil.copy2(source_path, snapshot)
                snapshot.chmod(snapshot.stat().st_mode | stat.S_IWRITE)
                payload = _decrypt_dat(snapshot.read_bytes(), self.aes_key, self.xor_key)
                image_kind = _image_kind(payload)
                media_dir = conversation_dir / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                if image_kind == "hevc":
                    target = media_dir / f"{media_id}.jpg"
                    _decode_hevc(payload, target)
                else:
                    extension = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[image_kind]
                    target = media_dir / f"{media_id}{extension}"
                    target.write_bytes(payload)
            except Exception:
                if target is not None:
                    target.unlink(missing_ok=True)
                continue
            return {
                "status": "exported",
                "media_path": target.relative_to(conversation_dir).as_posix(),
                "media_tier": tier,
            }
        return {"status": "failed"}


def export_files(account: Path, messages: list[dict], conversation_dir: Path, check_cancel) -> dict:
    stats = {
        "file_messages": 0,
        "exported": 0,
        "missing": 0,
        "failed": 0,
        "unique_files": 0,
        "bytes": 0,
    }
    resolved = {}
    for message in messages:
        if message.get("message_type") != "file":
            continue
        stats["file_messages"] += 1
        check_cancel()
        source = _find_file_source(account, message)
        if source is None:
            stats["missing"] += 1
            _mark_file(message, "missing")
            continue
        source_key = str(source)
        if source_key not in resolved:
            resolved[source_key] = _copy_file(source, message, conversation_dir)
        result = resolved[source_key]
        if result["status"] == "exported":
            message["media_path"] = result["media_path"]
            message["media_status"] = "exported"
            stats["exported"] += 1
        else:
            stats["failed"] += 1
            _mark_file(message, "failed")
    stats["unique_files"] = sum(1 for result in resolved.values() if result["status"] == "exported")
    stats["bytes"] = sum(result.get("size", 0) for result in resolved.values() if result["status"] == "exported")
    return stats


def export_videos(account: Path, messages: list[dict], conversation_dir: Path, check_cancel) -> dict:
    stats = {
        "video_messages": 0,
        "exported": 0,
        "missing": 0,
        "failed": 0,
        "unverified": 0,
        "unique_files": 0,
        "bytes": 0,
    }
    resolved = {}
    for message in messages:
        if message.get("message_type") != "video":
            continue
        stats["video_messages"] += 1
        check_cancel()
        source = _find_video_source(account, message)
        if source is None:
            stats["missing"] += 1
            _mark_other(message, "missing")
            continue
        source_key = str(source)
        if source_key not in resolved:
            resolved[source_key] = _copy_video(source, message, conversation_dir)
        result = resolved[source_key]
        if result["status"] == "exported":
            message["media_path"] = result["media_path"]
            message["media_tier"] = "standard"
            message["media_status"] = "exported"
            stats["exported"] += 1
        else:
            stats["failed"] += 1
            _mark_other(message, "failed")
    stats["unique_files"] = sum(1 for result in resolved.values() if result["status"] == "exported")
    stats["bytes"] = sum(result.get("size", 0) for result in resolved.values() if result["status"] == "exported")
    return stats


def _find_video_source(account: Path, message: dict) -> Path | None:
    media_id = message.get("media_id")
    if not media_id or not re.fullmatch(r"[0-9a-f]{32}", media_id):
        return None
    month = datetime.fromtimestamp(message["timestamp"], timezone.utc).astimezone().strftime("%Y-%m")
    root = account / "msg" / "video"
    exact = root / month / f"{media_id}.mp4"
    if exact.is_file():
        return exact
    if root.is_dir():
        for month_dir in sorted(root.iterdir()):
            if month_dir.is_dir() and MONTH_PATTERN.fullmatch(month_dir.name):
                candidate = month_dir / f"{media_id}.mp4"
                if candidate.is_file():
                    return candidate
    return None


def _copy_video(source: Path, message: dict, conversation_dir: Path) -> dict:
    media_dir = conversation_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / f"{message['media_id']}.mp4"
    try:
        shutil.copy2(source, target)
        _probe_video(target)
    except Exception:
        target.unlink(missing_ok=True)
        return {"status": "failed"}
    return {
        "status": "exported",
        "media_path": target.relative_to(conversation_dir).as_posix(),
        "size": source.stat().st_size,
    }


def _probe_video(target: Path) -> None:
    import av

    container = av.open(str(target))
    try:
        if not container.streams.video:
            raise ValueError("MP4 没有视频流")
        for _ in container.decode(video=0):
            return
    finally:
        container.close()
    raise ValueError("MP4 没有可解码的画面")


def _find_file_source(account: Path, message: dict) -> Path | None:
    file_name = message.get("file_name")
    file_size = message.get("file_size")
    if not file_name or file_size is None or Path(file_name).name != file_name:
        return None
    month = datetime.fromtimestamp(message["timestamp"], timezone.utc).astimezone().strftime("%Y-%m")
    source = account / "msg" / "file" / month / file_name
    if source.is_file() and source.stat().st_size == file_size:
        return source
    return None


def _copy_file(source: Path, message: dict, conversation_dir: Path) -> dict:
    files_dir = conversation_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / message["file_name"]
    if target.exists():
        target = files_dir / f"{hashlib.md5(message['message_uid'].encode('utf-8')).hexdigest()[:8]}_{message['file_name']}"
    try:
        shutil.copy2(source, target)
    except OSError:
        return {"status": "failed"}
    return {
        "status": "exported",
        "media_path": target.relative_to(conversation_dir).as_posix(),
        "size": source.stat().st_size,
    }


def _mark_file(message: dict, status: str) -> None:
    message["media_path"] = None
    message["media_tier"] = None
    message["media_status"] = status
    suffix = "未找到本地文件" if status == "missing" else "文件复制失败"
    message["content"] = f"{message['content']}（{suffix}）"


class VoiceStore:
    def __init__(self, plain_dir: Path):
        self._connections = []
        for path in sorted(plain_dir.glob("media_*.sqlite")):
            try:
                conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
                chat_ids = {
                    name: rowid
                    for rowid, name in conn.execute("SELECT rowid, user_name FROM Name2Id")
                }
                self._connections.append((conn, chat_ids))
            except sqlite3.Error:
                continue

    @property
    def available(self) -> bool:
        return bool(self._connections)

    def read(self, username: str, local_id: int, create_time: int, svr_id: int) -> bytes | None:
        for conn, chat_ids in self._connections:
            chat_id = chat_ids.get(username)
            if chat_id is None:
                continue
            try:
                row = conn.execute(
                    "SELECT voice_data FROM VoiceInfo "
                    "WHERE chat_name_id = ? AND local_id = ? AND create_time = ? AND svr_id = ?",
                    (chat_id, local_id, create_time, svr_id),
                ).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return bytes(row[0])
        return None

    def close(self) -> None:
        for conn, _ in self._connections:
            conn.close()
        self._connections = []


def prepare_voice_store(account_dir: str, account: Path) -> VoiceStore:
    plain_dir = CACHE_DIR / account_dir / "plain"
    db_keys = keys.load_db_keys(account_dir)
    for relative_db, key in sorted(db_keys.items()):
        if not re.fullmatch(r"db_storage/message/media_[0-9]+\.db", relative_db):
            continue
        source = account / relative_db
        if not source.is_file():
            continue
        try:
            snapshot = decrypt.snapshot_db(account, relative_db)
            report = decrypt.decrypt_snapshot(snapshot, key)
        except (OSError, RuntimeError, FileNotFoundError):
            continue
        if not report.get("ok"):
            continue
    return VoiceStore(plain_dir)


def export_voices(
    account: Path,
    messages: list[dict],
    conversation_dir: Path,
    check_cancel,
    voice_store: VoiceStore | None,
) -> dict:
    stats = {
        "voice_messages": 0,
        "exported": 0,
        "missing": 0,
        "failed": 0,
        "unverified": 0,
        "unique_files": 0,
        "bytes": 0,
    }
    for message in messages:
        if message.get("message_type") != "voice":
            continue
        stats["voice_messages"] += 1
        check_cancel()
        data = None
        if voice_store is not None and voice_store.available:
            data = voice_store.read(
                message["conversation"], message["local_id"],
                message["timestamp"], message["server_id"],
            )
        if data is None:
            stats["unverified"] += 1
            message["media_reason"] = (
                "voice_mapping_unverified"
                if voice_store is not None and voice_store.available
                else "voice_media_db_unavailable"
            )
            _mark_other(message, "unverified")
            continue
        try:
            media_dir = conversation_dir / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            name = hashlib.md5(message["message_uid"].encode("utf-8")).hexdigest()
            silk_path = media_dir / f"{name}.silk"
            silk_path.write_bytes(data)
        except OSError:
            stats["failed"] += 1
            _mark_other(message, "failed")
            continue
        wav_path = media_dir / f"{name}.wav"
        decoded = False
        if _pilk is not None:
            try:
                _pilk.silk_to_wav(str(silk_path), str(wav_path), rate=24000)
                with wave.open(str(wav_path), "rb") as handle:
                    if handle.getnframes() == 0:
                        raise ValueError("WAV 没有音频帧")
                silk_path.unlink()
                decoded = True
            except Exception:
                wav_path.unlink(missing_ok=True)
        if decoded:
            message["media_path"] = wav_path.relative_to(conversation_dir).as_posix()
            message["media_tier"] = "wav"
            message["media_status"] = "exported"
            message["content"] = f"{message['content']}（语音已解码为 WAV）"
            stats["bytes"] += wav_path.stat().st_size
        else:
            message["media_path"] = silk_path.relative_to(conversation_dir).as_posix()
            message["media_tier"] = "silk_raw"
            message["media_status"] = "exported"
            message["media_reason"] = "voice_silk_decoder_missing"
            message["content"] = f"{message['content']}（语音载荷已导出为 SILK，尚未解码播放）"
            stats["bytes"] += len(data)
        stats["exported"] += 1
        stats["unique_files"] += 1
    return stats


def mark_other_media(account: Path, messages: list[dict], media_type: str, check_cancel) -> dict:
    stats = {
        f"{media_type}_messages": 0,
        "exported": 0,
        "missing": 0,
        "failed": 0,
        "unverified": 0,
        "unique_files": 0,
        "bytes": 0,
    }
    for message in messages:
        if message.get("message_type") != media_type:
            continue
        stats[f"{media_type}_messages"] += 1
        check_cancel()
        if media_type == "voice":
            status = "unverified"
            message["media_reason"] = "voice_mapping_unverified"
        else:
            status = "unverified" if _has_unverified_payload(account, message, media_type) else "missing"
            if status == "unverified":
                message["media_reason"] = "emoji_candidate_unverified"
        stats[status] += 1
        _mark_other(message, status)
    return stats


def _mark_other(message: dict, status: str) -> None:
    message["media_path"] = None
    message["media_tier"] = None
    message["media_status"] = status
    suffix = {
        "missing": "未找到本地文件",
        "unverified": "媒体载荷尚未验证",
        "failed": "媒体还原失败",
    }[status]
    if status == "unverified":
        suffix = {
            "voice_mapping_unverified": "语音载荷尚未定位，不能判定本地缺失",
            "voice_media_db_unavailable": "媒体库尚未解密，语音载荷无法定位",
            "emoji_candidate_unverified": "找到表情候选，尚未验证能否还原",
        }.get(message.get("media_reason"), "媒体载荷尚未验证")
    message["content"] = f"{message['content']}（{suffix}）"


def _has_unverified_payload(account: Path, message: dict, media_type: str) -> bool:
    month = datetime.fromtimestamp(message["timestamp"], timezone.utc).astimezone().strftime("%Y-%m")
    if media_type == "video":
        return _find_video_source(account, message) is not None
    if media_type == "emoji":
        md5 = message.get("emoji_md5") or message.get("media_id")
        if not md5 or not re.fullmatch(r"[0-9a-f]{32}", md5):
            return False
        candidates = [
            account / "cache" / month / "Emoticon" / md5[:2] / md5,
            account / "business" / "emoticon" / "Persist" / md5[:2] / md5,
            account / "business" / "emoticon" / "Thumb" / md5[:2] / f"{md5}.thumb",
        ]
        return any(candidate.is_file() for candidate in candidates)
    return False


def extract_media_id(packed_info_data) -> str | None:
    if not packed_info_data:
        return None
    raw = bytes(packed_info_data)
    match = MEDIA_ID_PATTERN.search(raw)
    return match.group(0).decode("ascii") if match else None


def _split_account_dir(account_dir: str) -> tuple[str, str]:
    wxid, separator, suffix = account_dir.rpartition("_")
    if not separator or not wxid.startswith("wxid_") or not suffix:
        raise RuntimeError("这个账号目录缺少后缀，无法定位本机 UIN 配置")
    return wxid, suffix


def _resolve_uin(suffix: str) -> int:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("读取不到 APPDATA，无法定位本机微信 UIN 配置")
    ilink_dir = Path(appdata) / "Tencent" / "xwechat" / "radium" / "ilink"
    if not ilink_dir.is_dir():
        raise RuntimeError("找不到本机微信 ilink 配置目录，无法取得图片解密所需 UIN")
    candidates = [
        path for path in ilink_dir.iterdir()
        if path.is_dir() and path.name.lower().startswith(suffix.lower())
    ]
    if len(candidates) != 1:
        raise RuntimeError("无法把这个账号唯一对应到本机 UIN 配置")
    config_path = candidates[0] / "kvcomm" / "config.ini"
    parser = configparser.ConfigParser()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
        encoded = parser.get("default", "last_uin")
        uin = int(base64.b64decode(encoded).decode("ascii"))
    except (OSError, configparser.Error, ValueError) as exc:
        raise RuntimeError(f"读取账号 UIN 失败：{exc}") from exc
    if uin <= 0:
        raise RuntimeError("账号 UIN 配置无效")
    return uin


def _find_sources(account: Path, username: str, timestamp: int, media_id: str, quality: str):
    attach_dir = account / "msg" / "attach" / hashlib.md5(username.encode("utf-8")).hexdigest()
    if not attach_dir.is_dir():
        return []
    month = datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%Y-%m")
    month_dirs = [attach_dir / month]
    month_dirs.extend(
        path for path in sorted(attach_dir.iterdir())
        if path.is_dir() and path.name != month and MONTH_PATTERN.fullmatch(path.name)
    )
    variants = (
        (("_t.dat", "thumbnail"), ("_t_W.dat", "thumbnail"), ("_W.dat", "thumbnail"),
         (".dat", "standard"), ("_h.dat", "hd"))
        if quality == "thumb"
        else (("_h.dat", "hd"), (".dat", "standard"), ("_t.dat", "thumbnail"),
              ("_t_W.dat", "thumbnail"), ("_W.dat", "thumbnail"))
    )
    sources = []
    for suffix, tier in variants:
        for month_dir in month_dirs:
            candidate = month_dir / "Img" / f"{media_id}{suffix}"
            if candidate.is_file():
                sources.append((candidate, tier))
    return sources


def _decrypt_dat(data: bytes, aes_key: bytes, xor_key: int) -> bytes:
    if data[:6] != V2_MAGIC:
        return bytes(value ^ xor_key for value in data)
    if len(data) < 31:
        raise ValueError("V2 图片文件过短")
    aes_size = struct.unpack("<I", data[6:10])[0]
    xor_size = struct.unpack("<I", data[10:14])[0]
    payload_start = 15 + aes_size + 16
    if aes_size % 16 or payload_start + xor_size != len(data):
        raise ValueError("V2 图片文件长度不符")
    decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    aes_plain = decryptor.update(data[15:15 + aes_size]) + decryptor.finalize()
    xor_plain = bytes(value ^ xor_key for value in data[payload_start:])
    return aes_plain + xor_plain


def _image_kind(payload: bytes) -> str:
    if payload[:2] == b"\xff\xd8":
        return "jpeg"
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if payload[:6] in {b"GIF87a", b"GIF89a"}:
        return "gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    if payload[:4] == b"wxgf" or HEVC_VPS_PATTERN.search(payload):
        return "hevc"
    raise ValueError("解密后不是可识别的图片格式")


def _decode_hevc(payload: bytes, target: Path) -> None:
    import av

    match = HEVC_VPS_PATTERN.search(payload)
    if match is None:
        raise ValueError("wxgf 中找不到 HEVC 起点")
    container = av.open(io.BytesIO(payload[match.start():]), format="hevc")
    try:
        for frame in container.decode(video=0):
            image = frame.to_image()
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(target, quality=95)
            return
    finally:
        container.close()
    raise ValueError("HEVC 没有可解码的画面")


def _mark_media(message: dict, status: str) -> None:
    message["media_path"] = None
    message["media_tier"] = None
    message["media_status"] = status
    message["content"] = "[图片]（未找到本地文件）" if status == "missing" else "[图片]（解码失败）"
