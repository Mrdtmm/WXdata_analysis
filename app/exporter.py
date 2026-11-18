import csv
import hashlib
import html
import json
import os
import re
import shutil
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .config import OUTPUT_DIR
from .contacts import load_index
from .media import (
    IMAGE_QUALITIES, ImageDecoder, export_files, export_videos, export_voices,
    mark_other_media, prepare_voice_store,
)
from .messages import CollectionCancelled, collect_messages

SUPPORTED_FORMATS = {"html", "csv", "jsonl"}
JOBS = {}
CANCEL_EVENTS = {}
JOB_LOCK = threading.Lock()


def start_export(
    account: Path,
    account_dir: str,
    usernames: list[str],
    date_mode: str,
    start_date: str | None,
    end_date: str | None,
    formats: list[str],
    include_images: bool = False,
    image_quality: str = "best",
    include_files: bool = False,
    include_videos: bool = False,
    include_voices: bool = False,
    include_emojis: bool = False,
) -> dict:
    index = load_index(account_dir)
    if index is None:
        raise ValueError("这个账号还没有联系人索引，请先生成联系人列表")
    selected_names = list(dict.fromkeys(usernames))
    by_username = {item["username"]: item for item in index["conversations"]}
    missing = [username for username in selected_names if username not in by_username]
    if not selected_names or missing:
        raise ValueError("请选择当前联系人索引中的至少一个会话")
    selected = [by_username[username] for username in selected_names]
    selected_formats = list(dict.fromkeys(formats))
    if not selected_formats or any(item not in SUPPORTED_FORMATS for item in selected_formats):
        raise ValueError("请至少选择一种有效的导出格式")
    if image_quality not in IMAGE_QUALITIES:
        raise ValueError("未知的图片画质选项")
    start_timestamp, end_timestamp = _parse_date_range(date_mode, start_date, end_date)

    job_id = uuid.uuid4().hex
    cancel_event = threading.Event()
    job = {
        "job_id": job_id,
        "account_dir": account_dir,
        "state": "running",
        "stage": "正在读取消息",
        "processed": 0,
        "total": sum(item["message_count"] for item in selected),
        "percent": 0,
        "message_count": 0,
        "conversation_count": len(selected),
        "formats": selected_formats,
        "include_images": include_images,
        "image_quality": image_quality,
        "include_files": include_files,
        "include_videos": include_videos,
        "include_voices": include_voices,
        "include_emojis": include_emojis,
        "media": None,
        "file_media": None,
        "other_media": None,
        "output_dir": None,
        "error": None,
    }
    with JOB_LOCK:
        JOBS[job_id] = job
        CANCEL_EVENTS[job_id] = cancel_event
    thread = threading.Thread(
        target=_run_export,
        args=(
            job_id, account, selected, date_mode, start_date, end_date,
            start_timestamp, end_timestamp, include_images, image_quality, include_files,
            include_videos, include_voices, include_emojis,
        ),
        daemon=True,
    )
    thread.start()
    return get_export(job_id)


def get_export(job_id: str) -> dict:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("导出任务不存在，服务重启后需要重新导出")
        return dict(job)


def cancel_export(job_id: str) -> bool:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        event = CANCEL_EVENTS.get(job_id)
        if job is None or event is None:
            raise ValueError("导出任务不存在")
        if job["state"] != "running":
            return False
        event.set()
        job["stage"] = "正在取消"
    return True


def open_export(job_id: str) -> str:
    job = get_export(job_id)
    if job["state"] != "completed" or not job["output_dir"]:
        raise ValueError("导出尚未完成，不能打开输出目录")
    target = Path(job["output_dir"]).resolve()
    if not target.is_relative_to(OUTPUT_DIR.resolve()) or not target.is_dir():
        raise ValueError("输出目录不合法或已经不存在")
    if not hasattr(os, "startfile"):
        raise RuntimeError("当前系统不支持自动打开目录")
    os.startfile(str(target))
    return str(target)


def _run_export(
    job_id, account, selected, date_mode, start_date, end_date,
    start_timestamp, end_timestamp, include_images, image_quality, include_files,
    include_videos, include_voices, include_emojis,
):
    cancel_event = CANCEL_EVENTS[job_id]
    account_dir = JOBS[job_id]["account_dir"]
    temporary = OUTPUT_DIR / account_dir / f".export-{job_id}.tmp"
    voice_store = None
    try:
        def progress(scanned):
            total = max(JOBS[job_id]["total"], 1)
            _update_job(
                job_id,
                processed=scanned,
                percent=min(70, int(scanned * 70 / total)),
            )

        collected, warnings = collect_messages(
            account_dir, selected, start_timestamp, end_timestamp,
            progress, cancel_event.is_set,
        )
        message_count = sum(len(items) for items in collected.values())
        _update_job(
            job_id,
            stage="正在写入导出文件",
            total=message_count,
            processed=message_count,
            message_count=message_count,
            percent=75,
        )
        _check_cancel(cancel_event)

        if include_voices:
            voice_store = prepare_voice_store(account_dir, account)

        range_label = _range_label(date_mode, start_date, end_date)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final = OUTPUT_DIR / account_dir / f"{range_label}_{timestamp}_{job_id[:8]}"
        temporary.mkdir(parents=True, exist_ok=False)
        manifest_files = []
        conversation_reports = []
        image_decoder = None
        media_totals = {
            "image_messages": 0, "exported": 0, "missing": 0, "failed": 0,
            "unique_files": 0, "hd": 0, "standard": 0, "thumbnail": 0,
        } if include_images else None
        file_totals = {
            "file_messages": 0, "exported": 0, "missing": 0, "failed": 0,
            "unique_files": 0, "bytes": 0,
        } if include_files else None
        other_options = {"video": include_videos, "voice": include_voices, "emoji": include_emojis}
        other_totals = {
            media_type: {
                f"{media_type}_messages": 0, "exported": 0, "missing": 0,
                "failed": 0, "unverified": 0, "unique_files": 0, "bytes": 0,
            }
            for media_type, enabled in other_options.items() if enabled
        }
        for position, conversation in enumerate(selected, start=1):
            _check_cancel(cancel_event)
            username = conversation["username"]
            items = collected[username]
            folder_name = _conversation_folder(conversation)
            conversation_dir = temporary / folder_name
            conversation_dir.mkdir()
            image_stats = None
            if include_images and any(item.get("base_type") == 3 for item in items):
                if image_decoder is None:
                    image_decoder = ImageDecoder(account_dir)
                _update_job(job_id, stage="正在还原图片")
                image_stats = image_decoder.export_conversation(
                    account, username, items, conversation_dir, image_quality,
                    lambda: _check_cancel(cancel_event),
                )
                for key, value in image_stats.items():
                    media_totals[key] += value
                if image_stats["missing"] or image_stats["failed"]:
                    warnings.append(
                        f"{username}：{image_stats['missing']} 张图片没有本地文件，"
                        f"{image_stats['failed']} 张图片解码失败"
                    )
                _update_job(job_id, stage="正在写入导出文件")
            file_stats = None
            if include_files and any(item.get("message_type") == "file" for item in items):
                _update_job(job_id, stage="正在复制接收文件")
                file_stats = export_files(account, items, conversation_dir, lambda: _check_cancel(cancel_event))
                for key, value in file_stats.items():
                    file_totals[key] += value
                if file_stats["missing"] or file_stats["failed"]:
                    warnings.append(
                        f"{username}：{file_stats['missing']} 个接收文件未找到本地文件，"
                        f"{file_stats['failed']} 个接收文件复制失败"
                    )
                _update_job(job_id, stage="正在写入导出文件")
            other_reports = {}
            media_labels = {"video": "视频", "voice": "语音", "emoji": "表情"}
            for media_type, enabled in other_options.items():
                if not enabled or not any(item.get("message_type") == media_type for item in items):
                    continue
                _update_job(
                    job_id,
                    stage="正在还原视频" if media_type == "video"
                    else "正在解码语音" if media_type == "voice"
                    else "正在记录媒体状态",
                )
                if media_type == "video":
                    other_stats = export_videos(
                        account, items, conversation_dir, lambda: _check_cancel(cancel_event),
                    )
                elif media_type == "voice":
                    other_stats = export_voices(
                        account, items, conversation_dir, lambda: _check_cancel(cancel_event),
                        voice_store,
                    )
                    if other_stats["exported"]:
                        warnings.append(
                            f"{username}：{other_stats['exported']} 条语音已导出"
                        )
                else:
                    other_stats = mark_other_media(
                        account, items, media_type, lambda: _check_cancel(cancel_event),
                    )
                other_reports[media_type] = other_stats
                for key, value in other_stats.items():
                    other_totals[media_type][key] += value
                if other_stats["missing"] or other_stats["failed"] or other_stats["unverified"]:
                    warnings.append(
                        f"{username}：{media_labels[media_type]}缺失 {other_stats['missing']} 条，"
                        f"尚未验证 {other_stats['unverified']} 条，失败 {other_stats['failed']} 条"
                    )
            written = _write_conversation(
                conversation_dir, items, JOBS[job_id]["formats"], cancel_event,
                include_images, include_files, any(other_options.values()),
            )
            for path in conversation_dir.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(temporary).as_posix()
                    if relative not in manifest_files:
                        manifest_files.append(relative)
            conversation_reports.append({
                "username": username,
                "nickname": conversation.get("nickname", ""),
                "remark": conversation.get("remark", ""),
                "message_count": len(items),
                "formats": [path.suffix.lstrip(".") for path in written],
                "images": image_stats,
                "files": file_stats,
                "other_media": other_reports,
            })
            _update_job(job_id, percent=75 + int(position * 20 / len(selected)))

        manifest = {
            "version": 1,
            "account_dir": account_dir,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "date_mode": date_mode,
            "start_date": start_date if date_mode == "custom" else None,
            "end_date": end_date if date_mode == "custom" else None,
            "formats": JOBS[job_id]["formats"],
            "include_images": include_images,
            "image_quality": image_quality if include_images else None,
            "include_files": include_files,
            "include_videos": include_videos,
            "include_voices": include_voices,
            "include_emojis": include_emojis,
            "media": media_totals,
            "file_media": file_totals,
            "other_media": other_totals or None,
            "message_count": message_count,
            "conversation_count": len(selected),
            "conversations": conversation_reports,
            "warnings": warnings,
            "files": manifest_files + ["export_manifest.json"],
            "wal_merged": False,
        }
        (temporary / "export_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        _check_cancel(cancel_event)
        temporary.replace(final)
        _update_job(
            job_id,
            state="completed",
            stage="导出完成",
            output_dir=str(final),
            media=media_totals,
            file_media=file_totals,
            other_media=other_totals or None,
            percent=100,
        )
    except CollectionCancelled:
        shutil.rmtree(temporary, ignore_errors=True)
        _update_job(job_id, state="cancelled", stage="已取消", error=None)
    except Exception as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        _update_job(job_id, state="failed", stage="导出失败", error=str(exc))
    finally:
        if voice_store is not None:
            voice_store.close()


def _write_conversation(
    directory: Path, messages: list[dict], formats: list[str], cancel_event,
    include_images: bool, include_files: bool, include_other: bool,
) -> list[Path]:
    written = []
    if "jsonl" in formats:
        path = directory / "messages.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for position, message in enumerate(messages, start=1):
                if position % 100 == 0:
                    _check_cancel(cancel_event)
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
        written.append(path)
    if "csv" in formats:
        path = directory / "messages.csv"
        fields = [
            "time", "conversation", "sender_username", "sender_name", "direction",
            "message_type", "local_type", "app_type", "content", "raw",
            "media_path", "media_tier", "media_status", "media_reason", "file_name", "file_size",
            "server_id", "local_id", "source_db", "source_table", "message_uid",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for position, message in enumerate(messages, start=1):
                if position % 100 == 0:
                    _check_cancel(cancel_event)
                writer.writerow({key: _csv_safe(message.get(key)) for key in fields})
        written.append(path)
    if "html" in formats:
        _check_cancel(cancel_event)
        path = directory / "messages.html"
        path.write_text(_render_html(messages, include_images, include_files, include_other), encoding="utf-8")
        written.append(path)
    return written


def _render_html(messages: list[dict], include_images: bool, include_files: bool, include_other: bool) -> str:
    title = messages[0]["conversation_name"] if messages else "没有消息"
    media_notes = []
    if include_images:
        media_notes.append("图片按所选画质还原")
    if include_files:
        media_notes.append("接收文件按本地匹配结果复制")
    if include_other:
        media_notes.append("视频按本地 MP4 复制；已定位语音解码为 WAV 可直接播放；表情仅检查已知本地候选，尚未完成还原")
    media_note = "；".join(media_notes) + "，缺失、未验证或失败的媒体已标注。" if media_notes else "本次未导出媒体文件。"
    payload = json.dumps(messages, ensure_ascii=False)
    payload = (
        payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data: file:; media-src 'self' file:">
<title>{html.escape(title)} - 聊天记录</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;color:#1f2937;font:14px/1.65 "Microsoft YaHei",sans-serif}}main{{max-width:960px;margin:auto;padding:24px 14px}}h1{{font-size:22px;margin:0}}.meta{{color:#6b7280;margin:4px 0 18px}}.search{{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font:inherit}}.summary{{color:#6b7280;margin:12px 0}}.message{{max-width:82%;margin:10px 0;padding:10px 13px;border-radius:12px;background:#fff;box-shadow:0 1px 2px #0001;overflow-wrap:anywhere}}.self{{margin-left:auto;background:#d9fdd3}}.unknown{{border:1px dashed #9ca3af}}.system{{max-width:100%;background:transparent;box-shadow:none;text-align:center;color:#6b7280}}.sender{{font-size:12px;color:#6b7280;margin-bottom:3px}}.content{{white-space:pre-wrap}}details{{margin-top:8px;font-size:12px;color:#6b7280}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#00000008;padding:8px;border-radius:6px;max-height:320px;overflow:auto}}.empty{{text-align:center;color:#6b7280;padding:40px}}.media-image{{display:block;max-width:min(100%,420px);max-height:420px;margin-top:6px;border-radius:8px;object-fit:contain}}.media-note{{margin-top:4px;font-size:12px;color:#6b7280}}.media-file{{display:inline-block;margin-top:6px;color:#175cd3;text-decoration:underline}}.media-video{{display:block;max-width:min(100%,420px);max-height:420px;margin-top:6px;border-radius:8px;background:#000}}.media-audio{{display:block;width:100%;margin-top:6px}}
</style>
</head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="meta">本地离线导出；{media_note}</p>
<input id="search" class="search" type="search" placeholder="搜索正文、发送者或消息类型">
<p id="summary" class="summary"></p>
<section id="messages"></section>
<p id="empty" class="empty" hidden>没有符合搜索条件的消息</p>
</main>
<script>
const messages = {payload};
const container = document.getElementById('messages');
const summary = document.getElementById('summary');
const empty = document.getElementById('empty');
const search = document.getElementById('search');
const directionNames = {{self:'我',other:'对方',system:'系统',unknown:'方向未知'}};
function addTextElement(tag, className, text, parent) {{ const node = document.createElement(tag); node.className = className; node.textContent = text; parent.appendChild(node); return node; }}
function render() {{
  const query = search.value.trim().toLocaleLowerCase();
  const visible = messages.filter(item => `${{item.content}} ${{item.sender_name}} ${{item.sender_username}} ${{item.message_type}} ${{directionNames[item.direction] || item.direction}} ${{item.direction === 'system' ? '系统消息' : ''}}`.toLocaleLowerCase().includes(query));
  container.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const item of visible) {{
    const article = document.createElement('article');
    article.className = `message ${{item.direction}}`;
    const sender = item.direction === 'self' ? '我' : (item.direction === 'system' ? '系统消息' : `${{item.sender_name}} · ${{directionNames[item.direction] || item.direction}}`);
    addTextElement('div','sender',`${{sender}} · ${{item.time}}`,article);
    if (item.message_type === 'image' && item.media_path) {{
      const image = document.createElement('img');
      image.className = 'media-image';
      image.src = item.media_path;
      image.alt = '聊天图片';
      image.loading = 'lazy';
      article.appendChild(image);
      if (item.media_tier === 'thumbnail') addTextElement('div','media-note','本地仅保留缩略图',article);
    }} else if (item.message_type === 'file' && item.media_path) {{
      const link = document.createElement('a');
      link.className = 'media-file';
      link.href = item.media_path;
      link.textContent = item.content;
      if (item.file_name) link.download = item.file_name;
      article.appendChild(link);
    }} else if ((item.message_type === 'video' || item.message_type === 'emoji') && item.media_path && /\\.mp4$/i.test(item.media_path)) {{
      const video = document.createElement('video');
      video.className = 'media-video';
      video.controls = true;
      video.preload = 'metadata';
      video.src = item.media_path;
      article.appendChild(video);
    }} else if (item.message_type === 'voice' && item.media_path) {{
      if (item.media_tier === 'silk_raw') {{
        const link = document.createElement('a');
        link.className = 'media-file';
        link.href = item.media_path;
        link.textContent = '下载语音原始载荷（SILK）';
        link.download = item.media_path.split('/').pop();
        article.appendChild(link);
        addTextElement('div','media-note','SILK 原始音频，浏览器暂不能直接播放，需独立解码器',article);
      }} else {{
        const audio = document.createElement('audio');
        audio.className = 'media-audio';
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = item.media_path;
        article.appendChild(audio);
      }}
    }} else if (item.message_type === 'emoji' && item.media_path) {{
      const image = document.createElement('img');
      image.className = 'media-image';
      image.src = item.media_path;
      image.alt = '聊天表情';
      image.loading = 'lazy';
      article.appendChild(image);
    }} else {{
      addTextElement('div','content',item.content,article);
    }}
    if (item.raw && item.raw !== item.content) {{
      const details = document.createElement('details');
      addTextElement('summary','',`原始信息 · ${{item.message_type}} · local_type=${{item.local_type}}`,details);
      addTextElement('pre','',item.raw_encoding === 'base64' ? `[Base64] ${{item.raw}}` : item.raw,details);
      article.appendChild(details);
    }}
    fragment.appendChild(article);
  }}
  container.appendChild(fragment);
  summary.textContent = `显示 ${{visible.length}} / ${{messages.length}} 条消息`;
  empty.hidden = visible.length !== 0;
}}
search.addEventListener('input',render);
render();
</script></body></html>'''


def _parse_date_range(date_mode, start_date, end_date):
    if date_mode == "all":
        return None, None
    if date_mode != "custom" or not start_date or not end_date:
        raise ValueError("自定义导出需要完整的开始和结束日期")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("日期格式不正确") from exc
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    start_dt = datetime.combine(start, time.min).astimezone()
    end_dt = datetime.combine(end + timedelta(days=1), time.min).astimezone()
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _range_label(date_mode, start_date, end_date):
    return "all" if date_mode == "all" else f"{start_date}_{end_date}"


def _conversation_folder(conversation):
    display = conversation.get("remark") or conversation.get("nickname") or conversation["username"]
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", display).strip(" .") or "conversation"
    digest = hashlib.md5(conversation["username"].encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[:48]}_{digest}"


def _csv_safe(value):
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
            return "'" + value
    return value


def _check_cancel(event):
    if event.is_set():
        raise CollectionCancelled()


def _update_job(job_id, **values):
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)
