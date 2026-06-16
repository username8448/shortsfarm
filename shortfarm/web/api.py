from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from .. import db
from ..config import (
    DEFAULT_YOUTUBE_REDIRECT_URI,
    YOUTUBE_CLIENT_ID_SETTING,
    YOUTUBE_CLIENT_SECRET_SETTING,
    YOUTUBE_REDIRECT_URI_SETTING,
    data_dir,
    db_path,
    ensure_dirs,
    input_dir,
    logs_dir,
    output_dir,
    youtube_client_id,
    youtube_client_secret,
    youtube_redirect_uri,
)
from ..ffmpeg_tools import probe_duration, require_binary
from ..mpv_session import require_mpv
from ..publish_youtube import (
    parse_tags,
    run_publish_job_now,
    run_publish_queue_once,
    upload_clip_to_youtube,
    validate_publish_job,
    validate_publish_options,
)
from ..render import render_queued, retry_failed_clips
from ..services import (
    VIDEO_EXTENSIONS,
    FileSplitResult,
    FolderSplitItem,
    split_video_file,
    split_video_folder,
)
from ..youtube_oauth import YOUTUBE_SCOPES
from .schemas import (
    OpenMpvRequest,
    PublishJobRetryRequest,
    PublishWorkerRunOnceRequest,
    RenderRequest,
    RetryFailedRequest,
    SplitRequest,
    YouTubeClientJsonImportRequest,
    YouTubeConnectStartRequest,
    YouTubeOAuthProfileCreateRequest,
    YouTubeOAuthProfileImportRequest,
    YouTubeOAuthProfileUpdateRequest,
    YouTubeSettingsRequest,
    YouTubeUploadRequest,
)

router = APIRouter()


def _init() -> None:
    ensure_dirs()
    db.init_db()


def _fail(exc: Exception, status_code: int = 400) -> HTTPException:
    message = str(exc) or exc.__class__.__name__
    return HTTPException(status_code=status_code, detail={"message": message})


def _status_value(value: str | None) -> str:
    return value or "inbox"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    sec = total % 60
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes}:{sec:02d}"


def _row(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return default


def _segment_dict(index: int, start: float, end: float, path: str | None = None) -> dict[str, Any]:
    return {
        "index": index,
        "start_sec": float(start),
        "end_sec": float(end),
        "duration_sec": float(end - start),
        "path": path,
    }


def _segment_output_dir(rows: list[Any]) -> str:
    for row in rows:
        path = _row(row, "path")
        if path:
            return str(Path(str(path)).parent)
    return ""


def _latest_output_dir(video_id: int) -> str:
    path = db.latest_segment_path(video_id)
    return str(Path(path).parent) if path else ""


def _split_result(result: FileSplitResult) -> dict[str, Any]:
    return {
        "status": "preview" if result.dry_run else "done",
        "dry_run": result.dry_run,
        "source_path": str(result.source_path),
        "video_id": result.video_id,
        "job_id": result.job_id,
        "duration_sec": result.duration_sec,
        "duration_text": _format_duration(result.duration_sec),
        "output_dir": str(result.output_dir),
        "segments_count": len(result.segment_ranges),
        "segments": [
            _segment_dict(i, start, end, str(result.files[i - 1]) if i - 1 < len(result.files) else None)
            for i, (start, end) in enumerate(result.segment_ranges, start=1)
        ],
        "files": [str(path) for path in result.files],
    }


def _folder_item(item: FolderSplitItem) -> dict[str, Any]:
    if item.error:
        return {"path": str(item.source_path), "status": "failed", "error": item.error}
    assert item.result is not None
    data = _split_result(item.result)
    return {"path": str(item.source_path), "status": "ok", "error": None, "result": data}


def _job_dict(row: Any) -> dict[str, Any]:
    video_id = _row(row, "video_id")
    job_id = int(row["id"])
    segments = db.list_segments(int(video_id), job_id=job_id) if video_id is not None else []
    segment_count = len(segments)
    status = str(_row(row, "status", ""))
    source_path = ""
    if video_id is not None:
        video = db.get_video(int(video_id))
        source_path = str(_row(video, "source_path", "")) if video is not None else ""
    if status == "done":
        progress = 100
    elif status == "failed":
        progress = 100
    elif status == "running":
        progress = 50
    else:
        progress = 0
    return {
        "id": job_id,
        "video_id": video_id,
        "type": _row(row, "type", "split"),
        "status": status,
        "mode": _row(row, "mode", ""),
        "segment_seconds": _row(row, "segment_seconds"),
        "progress": progress,
        "done_items": segment_count,
        "total_items": segment_count if status in {"done", "failed"} else None,
        "current_file": _row(row, "video_title", "") or "",
        "source_path": source_path,
        "output_dir": _segment_output_dir(segments),
        "error": _row(row, "error", "") or "",
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "started_at"),
        "finished_at": _row(row, "finished_at"),
    }


def _video_dict(row: Any) -> dict[str, Any]:
    video_id = int(row["id"])
    return {
        "id": video_id,
        "title": row["title"],
        "source_path": row["source_path"],
        "duration_sec": row["duration_sec"],
        "duration_text": _format_duration(row["duration_sec"]),
        "review_status": _status_value(_row(row, "review_status")),
        "mark_count": int(_row(row, "mark_count", db.count_marks(video_id))),
        "clip_count": int(_row(row, "clip_count", db.count_clips(video_id))),
        "output_dir": _latest_output_dir(video_id),
    }


def _clip_dict(row: Any) -> dict[str, Any]:
    video_id = int(row["video_id"])
    video = db.get_video(video_id)
    return {
        "id": int(row["id"]),
        "video_id": video_id,
        "video_title": _row(row, "video_title", ""),
        "source_path": str(_row(video, "source_path", "")) if video is not None else "",
        "mark_id": _row(row, "mark_id"),
        "status": _row(row, "status", ""),
        "cut_mode": _row(row, "cut_mode", ""),
        "output_path": _row(row, "output_path", "") or "",
        "temp_path": _row(row, "temp_path", "") or "",
        "error": _row(row, "error", "") or "",
    }


def _social_account_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "platform": row["platform"],
        "oauth_profile_id": _row(row, "oauth_profile_id"),
        "profile_name": _row(row, "profile_name", "") or "",
        "display_name": _row(row, "display_name", "") or "",
        "account_email": _row(row, "account_email", "") or "",
        "channel_id": _row(row, "channel_id", "") or "",
        "channel_title": _row(row, "channel_title", "") or "",
        "scopes": _row(row, "scopes", "") or "",
        "status": _row(row, "status", "active") or "active",
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
        "last_connected_at": _row(row, "last_connected_at"),
        "error": _row(row, "error"),
    }


def _youtube_oauth_profile_dict(row: Any) -> dict[str, Any]:
    mode = _row(row, "mode", "custom") or "custom"
    client_id = _row(row, "client_id", "") or ""
    redirect_uri = _row(row, "redirect_uri", DEFAULT_YOUTUBE_REDIRECT_URI) or DEFAULT_YOUTUBE_REDIRECT_URI
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "mode": mode,
        "client_id": client_id,
        "client_secret_set": bool(_normalize_setting_text(_row(row, "client_secret"))),
        "redirect_uri": redirect_uri,
        "status": _row(row, "status", "active") or "active",
        "is_default": bool(_row(row, "is_default", 0)),
        "notes": _row(row, "notes"),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _publish_job_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "platform": _row(row, "platform", "youtube"),
        "account_id": int(row["account_id"]),
        "clip_id": int(row["clip_id"]),
        "status": _row(row, "status", "queued"),
        "title": _row(row, "title", ""),
        "description": _row(row, "description"),
        "tags": _row(row, "tags"),
        "category_id": _row(row, "category_id", "22"),
        "privacy_status": _row(row, "privacy_status", "private"),
        "publish_mode": _row(row, "publish_mode", "private"),
        "publish_at": _row(row, "publish_at"),
        "made_for_kids": bool(_row(row, "made_for_kids", 0)),
        "youtube_video_id": _row(row, "youtube_video_id"),
        "youtube_url": _row(row, "youtube_url"),
        "error": _row(row, "error"),
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "started_at"),
        "finished_at": _row(row, "finished_at"),
        "updated_at": _row(row, "updated_at"),
        "attempt_count": int(_row(row, "attempt_count", 0) or 0),
        "last_attempt_at": _row(row, "last_attempt_at"),
        "next_attempt_at": _row(row, "next_attempt_at"),
        "oauth_profile_id": _row(row, "oauth_profile_id"),
        "profile_name": _row(row, "profile_name", "") or "",
        "account_display_name": _row(row, "account_display_name", "") or "",
        "account_email": _row(row, "account_email", "") or "",
        "channel_id": _row(row, "channel_id", "") or "",
        "channel_title": _row(row, "channel_title", "") or "",
        "clip_video_id": _row(row, "clip_video_id"),
        "clip_status": _row(row, "clip_status", "") or "",
        "clip_output_path": _row(row, "clip_output_path", "") or "",
        "clip_cut_mode": _row(row, "clip_cut_mode", "") or "",
        "video_title": _row(row, "video_title", "") or "",
        "video_source_path": _row(row, "video_source_path", "") or "",
        "can_retry": _row(row, "status") in {"failed", "cancelled"},
        "can_run": _row(row, "status") in {"queued", "failed"},
        "can_cancel": _row(row, "status") in {"queued", "failed"},
    }


def _status_counts(values: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: int(values.get(key, 0)) for key in keys}


def _latest_outputs(limit: int = 30) -> list[dict[str, Any]]:
    rows = db.list_recent_segments(limit=300)
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        path = Path(str(row["path"]))
        output = str(path.parent)
        item = grouped.setdefault(
            output,
            {
                "output_dir": output,
                "segments_count": 0,
                "latest_file": path.name,
                "video_title": _row(row, "video_title", ""),
            },
        )
        item["segments_count"] += 1
    return list(grouped.values())[:limit]


def _resolve_fs_path(value: str | None = None) -> Path:
    if not value:
        return Path.home().resolve()
    return Path(value).expanduser().resolve()


def _resolve_video_path(value: str) -> Path:
    video = _resolve_fs_path(value)
    if not video.exists():
        raise FileNotFoundError(f"Видео не найдено: {video}")
    if not video.is_file():
        raise ValueError(f"Это не файл: {video}")
    if video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"Это не поддерживаемый видеофайл: {video.name}")
    return video


def _mtime_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _fs_root(label: str, path: Path) -> dict[str, str] | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_dir():
        return None
    return {"label": label, "path": str(resolved)}


def _fs_item(path: Path) -> dict[str, Any] | None:
    try:
        if path.name.startswith("."):
            return None
        stat = path.stat()
        if path.is_dir():
            return {
                "type": "dir",
                "name": path.name,
                "path": str(path),
                "size": None,
                "mtime": _mtime_iso(stat.st_mtime),
            }
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            return {
                "type": "video",
                "name": path.name,
                "path": str(path),
                "size": int(stat.st_size),
                "mtime": _mtime_iso(stat.st_mtime),
                "ext": path.suffix.lower(),
            }
    except (OSError, PermissionError):
        return None
    return None


def _thumbnail_cache_path(video: Path) -> Path:
    stat = video.stat()
    key = f"{video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", errors="ignore")
    name = hashlib.sha256(key).hexdigest()[:32] + ".jpg"
    cache_dir = data_dir() / "cache" / "thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / name


def _thumbnail_placeholder(message: str = "Нет миниатюры") -> Response:
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="96" height="54" viewBox="0 0 96 54">
  <rect width="96" height="54" rx="8" fill="#273244"/>
  <path d="M40 18v18l17-9z" fill="#94a3b8"/>
  <text x="48" y="47" text-anchor="middle" font-family="Arial,sans-serif" font-size="7" fill="#cbd5e1">{message}</text>
</svg>"""
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


def _normalize_setting_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _choose_redirect_uri(candidates: list[str] | None) -> str:
    if not candidates:
        return DEFAULT_YOUTUBE_REDIRECT_URI
    for item in candidates:
        uri = _normalize_setting_text(item)
        if uri and uri.startswith(("http://", "https://")):
            return uri
    return DEFAULT_YOUTUBE_REDIRECT_URI


def _next_imported_profile_name() -> str:
    existing_names = {str(row["name"]) for row in db.list_youtube_oauth_profiles()}
    if "Imported YouTube OAuth" not in existing_names:
        return "Imported YouTube OAuth"
    index = 2
    while True:
        candidate = f"Imported YouTube OAuth {index}"
        if candidate not in existing_names:
            return candidate
        index += 1


def _extract_oauth_client_json(payload: dict[str, Any]) -> tuple[str, str, str]:
    section = payload.get("web")
    if not isinstance(section, dict):
        section = payload.get("installed")
    if not isinstance(section, dict):
        raise ValueError("Не найден блок web или installed в OAuth Client JSON.")

    client_id = _normalize_setting_text(section.get("client_id"))
    client_secret = _normalize_setting_text(section.get("client_secret"))
    redirect_uris = section.get("redirect_uris")
    redirect_uri = _choose_redirect_uri(redirect_uris if isinstance(redirect_uris, list) else None)
    if not client_id or not client_secret:
        raise ValueError("OAuth Client JSON должен содержать client_id и client_secret.")
    return client_id, client_secret, redirect_uri


def _youtube_client_config_from_profile(profile: Any) -> dict[str, Any]:
    client_id = _normalize_setting_text(_row(profile, "client_id"))
    client_secret = _normalize_setting_text(_row(profile, "client_secret"))
    redirect_uri = (
        _normalize_setting_text(_row(profile, "redirect_uri"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    if not client_id or not client_secret:
        raise RuntimeError("У выбранного OAuth Profile не заполнены client_id/client_secret.")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _youtube_flow_from_profile(flow_cls: Any, profile: Any) -> Any:
    redirect_uri = (
        _normalize_setting_text(_row(profile, "redirect_uri"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    return flow_cls.from_client_config(
        _youtube_client_config_from_profile(profile),
        scopes=YOUTUBE_SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False,
    )


def _select_youtube_oauth_profile(profile_id: int | None = None) -> Any:
    if profile_id is not None:
        profile = db.get_youtube_oauth_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        if _row(profile, "status", "active") != "active":
            raise ValueError("YouTube OAuth Profile не активен.")
        return profile

    default_profile = db.get_default_youtube_oauth_profile()
    if default_profile is not None and _row(default_profile, "status", "active") == "active":
        return default_profile

    active_profiles = [
        row for row in db.list_youtube_oauth_profiles()
        if _row(row, "status", "active") == "active"
    ]
    if not active_profiles:
        raise RuntimeError("Сначала создайте YouTube OAuth Profile в настройках.")
    if len(active_profiles) == 1:
        return active_profiles[0]
    raise RuntimeError("Выберите OAuth Profile для подключения YouTube-канала или назначьте профиль по умолчанию.")


def _fetch_google_account_email(credentials: Any) -> str | None:
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None

    try:
        oauth2 = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        payload = oauth2.userinfo().get().execute()
        return _normalize_setting_text(payload.get("email"))
    except Exception:
        return None


def _youtube_settings_status() -> dict[str, Any]:
    stored_client_id = _normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_ID_SETTING))
    stored_client_secret = _normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_SECRET_SETTING))
    stored_redirect_uri = _normalize_setting_text(db.get_setting(YOUTUBE_REDIRECT_URI_SETTING))
    env_client_id = bool(_normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_ID")))
    env_client_secret = bool(_normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_SECRET")))
    env_redirect_uri = bool(_normalize_setting_text(os.environ.get("YOUTUBE_REDIRECT_URI")))

    client_id = stored_client_id or _normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_ID")) or ""
    redirect_uri = (
        stored_redirect_uri
        or _normalize_setting_text(os.environ.get("YOUTUBE_REDIRECT_URI"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    client_secret_set = bool(
        stored_client_secret
        or _normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_SECRET"))
    )
    configured = bool(client_id and client_secret_set)
    return {
        "configured": configured,
        "client_id": client_id,
        "client_secret_set": client_secret_set,
        "redirect_uri": redirect_uri,
        "env_fallback": {
            "client_id": env_client_id and not stored_client_id,
            "client_secret": env_client_secret and not stored_client_secret,
            "redirect_uri": env_redirect_uri and not stored_redirect_uri,
        },
    }


def _create_publish_job_from_request(clip_id: int, req: YouTubeUploadRequest) -> int:
    validated = validate_publish_options(
        title=req.title,
        publish_mode=req.publish_mode,
        publish_at=req.publish_at,
        category_id=req.category_id,
    )
    tags = parse_tags(req.tags)
    return db.create_publish_job(
        account_id=req.account_id,
        clip_id=clip_id,
        title=req.title.strip(),
        description=req.description,
        tags=json.dumps(tags, ensure_ascii=False),
        category_id=req.category_id,
        privacy_status=str(validated["privacy_status"]),
        publish_mode=req.publish_mode,
        publish_at=validated["publish_at"],
        made_for_kids=req.made_for_kids,
        platform="youtube",
    )


def _save_youtube_settings(
    *,
    client_id: str | None,
    client_secret: str | None,
    redirect_uri: str | None,
) -> dict[str, Any]:
    existing_client_id = _normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_ID_SETTING))
    existing_client_secret = _normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_SECRET_SETTING))

    resolved_client_id = _normalize_setting_text(client_id) or existing_client_id
    resolved_client_secret = _normalize_setting_text(client_secret) or existing_client_secret
    resolved_redirect_uri = _normalize_setting_text(redirect_uri) or DEFAULT_YOUTUBE_REDIRECT_URI

    if not resolved_client_id:
        raise ValueError("Укажите YouTube client_id.")
    if not resolved_client_secret and not _normalize_setting_text(youtube_client_secret()):
        raise ValueError("Укажите YouTube client_secret.")

    db.set_setting(YOUTUBE_CLIENT_ID_SETTING, resolved_client_id, is_secret=False)
    if _normalize_setting_text(client_secret):
        db.set_setting(YOUTUBE_CLIENT_SECRET_SETTING, _normalize_setting_text(client_secret), is_secret=True)
    elif existing_client_secret:
        db.set_setting(YOUTUBE_CLIENT_SECRET_SETTING, existing_client_secret, is_secret=True)
    db.set_setting(YOUTUBE_REDIRECT_URI_SETTING, resolved_redirect_uri, is_secret=False)
    return _youtube_settings_status()


@router.get("/fs/roots")
def fs_roots() -> dict[str, Any]:
    _init()
    candidates = [
        _fs_root("Дом", Path.home()),
        _fs_root("Видео", Path.home() / "Videos"),
        _fs_root("Проект", Path.cwd()),
        _fs_root("Input", input_dir()),
        _fs_root("Output", output_dir()),
    ]

    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if item is None or item["path"] in seen:
            continue
        roots.append(item)
        seen.add(item["path"])
    return {"roots": roots}


@router.get("/fs/list")
def fs_list(path: str | None = None) -> dict[str, Any]:
    try:
        _init()
        folder = _resolve_fs_path(path)
        if not folder.exists():
            raise FileNotFoundError(f"Папка не найдена: {folder}")
        if not folder.is_dir():
            raise ValueError(f"Это не папка: {folder}")

        items: list[dict[str, Any]] = []
        try:
            children = list(folder.iterdir())
        except PermissionError as exc:
            raise PermissionError(f"Нет доступа к папке: {folder}") from exc

        for child in children:
            item = _fs_item(child)
            if item is not None:
                items.append(item)

        items.sort(key=lambda item: (0 if item["type"] == "dir" else 1, item["name"].casefold()))
        parent = str(folder.parent) if folder.parent != folder else None
        return {"path": str(folder), "parent": parent, "items": items}
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/fs/video-info")
def fs_video_info(path: str) -> dict[str, Any]:
    try:
        _init()
        video = _resolve_video_path(path)

        stat = video.stat()
        duration = probe_duration(video)
        return {
            "path": str(video),
            "name": video.name,
            "duration_sec": duration,
            "duration_text": _format_duration(duration),
            "size": int(stat.st_size),
            "mtime": _mtime_iso(stat.st_mtime),
            "ext": video.suffix.lower(),
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/fs/thumbnail")
def fs_thumbnail(path: str) -> Response:
    try:
        _init()
        video = _resolve_video_path(path)
        thumb = _thumbnail_cache_path(video)

        if not thumb.exists():
            ffmpeg = require_binary("ffmpeg")
            commands = [
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                    "-q:v",
                    "4",
                    str(thumb),
                ],
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                    "-q:v",
                    "4",
                    str(thumb),
                ],
            ]

            ok = False
            for cmd in commands:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    check=False,
                )
                if result.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0:
                    ok = True
                    break
            if not ok:
                try:
                    thumb.unlink(missing_ok=True)
                except OSError:
                    pass
                return _thumbnail_placeholder("Нет кадра")

        return FileResponse(
            thumb,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return _thumbnail_placeholder("Нет кадра")


@router.post("/fs/open-mpv")
def fs_open_mpv(req: OpenMpvRequest) -> dict[str, Any]:
    try:
        _init()
        video = _resolve_video_path(req.path)
        mpv = require_mpv()
        subprocess.Popen(
            [mpv, str(video)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"status": "opened", "path": str(video), "player": "mpv"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


def _youtube_oauth_config() -> tuple[str, str, str]:
    client_id = youtube_client_id()
    client_secret = youtube_client_secret()
    redirect_uri = youtube_redirect_uri()
    if not client_id or not client_secret:
        raise RuntimeError(
            "YouTube OAuth не настроен. Откройте Настройки → YouTube и добавьте OAuth Client JSON или client_id/client_secret."
        )
    return client_id, client_secret, redirect_uri


def _oauth_page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    color = "#86efac" if ok else "#fca5a5"
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    payload = json.dumps(
        {
            "type": "shortfarm-youtube-oauth-complete" if ok else "shortfarm-youtube-oauth-error",
            "ok": ok,
            "message": message,
        },
        ensure_ascii=False,
    )
    callback_script = f"""
  <script>
    (function () {{
      const payload = {payload};
      try {{
        localStorage.setItem('shortfarm.youtube.oauth.event', JSON.stringify(payload));
        localStorage.setItem('shortfarm.youtube.oauth.updated', String(Date.now()));
      }} catch (err) {{}}
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, window.location.origin);
        }}
      }} catch (err) {{}}
    }})();
  </script>"""
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#111827; color:#f8fafc; font-family:-apple-system,Segoe UI,system-ui,sans-serif; }}
    main {{ width:min(560px, calc(100vw - 32px)); padding:28px; border:1px solid #3f4b5c; border-radius:16px; background:#1f2937; box-shadow:0 18px 44px rgba(0,0,0,.32); }}
    h1 {{ margin:0 0 10px; font-size:22px; color:{color}; }}
    p {{ margin:0; color:#cbd5e1; line-height:1.6; }}
    a {{ color:#bfdbfe; }}
  </style>
</head>
<body><main><h1>{safe_title}</h1><p>{safe_message}</p></main>{callback_script}</body>
</html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("/settings/youtube")
def youtube_settings() -> dict[str, Any]:
    try:
        _init()
        return _youtube_settings_status()
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube")
def youtube_settings_save(req: YouTubeSettingsRequest) -> dict[str, Any]:
    try:
        _init()
        return _save_youtube_settings(
            client_id=req.client_id,
            client_secret=req.client_secret,
            redirect_uri=req.redirect_uri,
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube/import-client-json")
def youtube_settings_import_client_json(req: YouTubeClientJsonImportRequest) -> dict[str, Any]:
    try:
        _init()
        text = _normalize_setting_text(req.json_text)
        if not text:
            raise ValueError("Вставьте OAuth Client JSON.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("OAuth Client JSON должен быть JSON-объектом.")
        client_id, client_secret, redirect_uri = _extract_oauth_client_json(payload)

        return _save_youtube_settings(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except json.JSONDecodeError as exc:
        raise _fail(ValueError(f"Не удалось разобрать JSON: {exc.msg}"))
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube/clear")
def youtube_settings_clear() -> dict[str, Any]:
    try:
        _init()
        db.delete_setting(YOUTUBE_CLIENT_ID_SETTING)
        db.delete_setting(YOUTUBE_CLIENT_SECRET_SETTING)
        db.delete_setting(YOUTUBE_REDIRECT_URI_SETTING)
        return {"status": "ok", "settings": _youtube_settings_status()}
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/oauth-profiles")
def youtube_oauth_profiles() -> dict[str, Any]:
    try:
        _init()
        rows = db.list_youtube_oauth_profiles()
        return {"profiles": [_youtube_oauth_profile_dict(row) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles")
def youtube_oauth_profiles_create(req: YouTubeOAuthProfileCreateRequest) -> dict[str, Any]:
    try:
        _init()
        name = _normalize_setting_text(req.name)
        client_id = _normalize_setting_text(req.client_id)
        client_secret = _normalize_setting_text(req.client_secret)
        redirect_uri = _normalize_setting_text(req.redirect_uri) or DEFAULT_YOUTUBE_REDIRECT_URI
        if not name:
            raise ValueError("Укажите название OAuth Profile.")
        if not client_id or not client_secret:
            raise ValueError("Укажите client_id и client_secret.")
        profile_id = db.create_youtube_oauth_profile(
            name=name,
            mode="custom",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            status="active",
            is_default=req.is_default,
            notes=req.notes,
        )
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": _youtube_oauth_profile_dict(profile)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles/import-client-json")
def youtube_oauth_profiles_import(req: YouTubeOAuthProfileImportRequest) -> dict[str, Any]:
    try:
        _init()
        text = _normalize_setting_text(req.json_text)
        if not text:
            raise ValueError("Вставьте OAuth Client JSON.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("OAuth Client JSON должен быть JSON-объектом.")

        client_id, client_secret, redirect_uri = _extract_oauth_client_json(payload)
        profile_id = db.create_youtube_oauth_profile(
            name=_normalize_setting_text(req.name) or _next_imported_profile_name(),
            mode="custom",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            status="active",
            is_default=req.is_default or not db.list_youtube_oauth_profiles(),
            notes=req.notes,
        )
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": _youtube_oauth_profile_dict(profile)}
    except json.JSONDecodeError as exc:
        raise _fail(ValueError(f"Не удалось разобрать JSON: {exc.msg}"))
    except Exception as exc:
        raise _fail(exc)


@router.patch("/publish/youtube/oauth-profiles/{profile_id}")
def youtube_oauth_profiles_update(profile_id: int, req: YouTubeOAuthProfileUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        ok = db.update_youtube_oauth_profile(
            profile_id,
            name=req.name,
            client_id=req.client_id,
            client_secret=req.client_secret,
            redirect_uri=req.redirect_uri,
            status=req.status,
            notes=req.notes,
        )
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": _youtube_oauth_profile_dict(profile)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/publish/youtube/oauth-profiles/{profile_id}")
def youtube_oauth_profiles_delete(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.delete_youtube_oauth_profile(profile_id)
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles/{profile_id}/set-default")
def youtube_oauth_profiles_set_default(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.set_default_youtube_oauth_profile(profile_id)
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": _youtube_oauth_profile_dict(profile)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/accounts")
def youtube_accounts() -> dict[str, Any]:
    try:
        _init()
        rows = db.list_social_accounts(platform="youtube")
        return {"accounts": [_social_account_dict(row) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/connect/start")
def youtube_connect_start(req: YouTubeConnectStartRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        profile = _select_youtube_oauth_profile(req.oauth_profile_id if req else None)
        state = secrets.token_urlsafe(32)
        db.create_oauth_state("youtube", state, oauth_profile_id=int(profile["id"]))
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise RuntimeError(
                "Google OAuth зависимости не установлены. Выполните: pip install -e ."
            ) from exc

        flow = _youtube_flow_from_profile(Flow, profile)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent select_account",
            state=state,
        )
        return {
            "auth_url": auth_url,
            "oauth_profile_id": int(profile["id"]),
            "profile_name": _row(profile, "name", "") or "",
        }
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/oauth/callback")
def youtube_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    try:
        _init()
        if error:
            if state:
                try:
                    db.consume_oauth_state("youtube", state)
                except Exception:
                    pass
            return _oauth_page(
                "Ошибка YouTube OAuth",
                f"Google вернул ошибку: {error}. Можно закрыть эту вкладку и попробовать подключение ещё раз.",
                ok=False,
            )
        if not code:
            return _oauth_page("Ошибка YouTube OAuth", "Callback не содержит code.", ok=False)
        if not state:
            return _oauth_page("Ошибка YouTube OAuth", "Callback не содержит state.", ok=False)
        state_row = db.consume_oauth_state("youtube", state)
        if state_row is None:
            return _oauth_page(
                "Ошибка YouTube OAuth",
                "OAuth state не найден или уже был использован. Запустите подключение заново.",
                ok=False,
            )

        oauth_profile_id = _row(state_row, "oauth_profile_id")
        profile = _select_youtube_oauth_profile(int(oauth_profile_id) if oauth_profile_id is not None else None)
        try:
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google OAuth зависимости не установлены. Выполните: pip install -e ."
            ) from exc

        flow = _youtube_flow_from_profile(Flow, profile)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        account_email = _fetch_google_account_email(credentials)

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        response = youtube.channels().list(part="snippet", mine=True).execute()
        items = response.get("items") or []
        if not items:
            message = "YouTube канал для этого аккаунта не найден."
            expires_at = credentials.expiry.isoformat() if credentials.expiry else None
            scopes = " ".join(credentials.scopes or YOUTUBE_SCOPES)
            # TODO: encrypt tokens before production use.
            db.save_social_account(
                platform="youtube",
                display_name="YouTube аккаунт",
                channel_id=None,
                channel_title=None,
                access_token=credentials.token,
                refresh_token=credentials.refresh_token,
                token_expires_at=expires_at,
                scopes=scopes,
                oauth_profile_id=int(profile["id"]),
                account_email=account_email,
                last_connected_at=db.now_utc(),
                status="error",
                error=message,
            )
            return _oauth_page("Ошибка YouTube OAuth", message, ok=False)

        channel = items[0]
        snippet = channel.get("snippet") or {}
        channel_id = channel.get("id") or ""
        channel_title = snippet.get("title") or "YouTube канал"
        expires_at = credentials.expiry.isoformat() if credentials.expiry else None
        scopes = " ".join(credentials.scopes or YOUTUBE_SCOPES)

        # TODO: encrypt tokens before production use.
        account_id = db.save_social_account(
            platform="youtube",
            display_name=channel_title,
            channel_id=channel_id,
            channel_title=channel_title,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_expires_at=expires_at,
            scopes=scopes,
            oauth_profile_id=int(profile["id"]),
            account_email=account_email,
            last_connected_at=db.now_utc(),
            status="active",
            error=None,
        )
        return _oauth_page(
            "YouTube аккаунт подключён",
            "Можно закрыть эту вкладку и вернуться в ShortFarm. Затем нажмите «Обновить» в разделе «Публикация».",
            ok=True,
        )
    except Exception as exc:
        return _oauth_page("Ошибка YouTube OAuth", str(exc) or exc.__class__.__name__, ok=False)


@router.post("/publish/youtube/accounts/{account_id}/disconnect")
def youtube_disconnect(account_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.disconnect_social_account(account_id, platform="youtube")
        if not ok:
            raise FileNotFoundError("YouTube аккаунт не найден.")
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/clips/{clip_id}/upload")
def youtube_upload_clip(clip_id: int, req: YouTubeUploadRequest) -> dict[str, Any]:
    try:
        _init()
        job_id = _create_publish_job_from_request(clip_id, req)
        job = upload_clip_to_youtube(job_id)
        return {
            "job": _publish_job_dict(job),
            "youtube_url": _row(job, "youtube_url"),
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/clips/{clip_id}/enqueue")
def youtube_enqueue_clip(clip_id: int, req: YouTubeUploadRequest) -> dict[str, Any]:
    try:
        _init()
        job_id = _create_publish_job_from_request(clip_id, req)
        job = db.get_publish_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Publish job {job_id} not found")
        validate_publish_job(job)
        return {"job": _publish_job_dict(job)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/jobs")
def publish_jobs(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    try:
        _init()
        rows = db.list_publish_jobs(status=status if status != "all" else None, limit=limit)
        return {"jobs": [_publish_job_dict(row) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/{job_id}/retry")
def publish_job_retry(job_id: int, req: PublishJobRetryRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        job = db.get_publish_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Publish job {job_id} not found")
        if job["status"] not in {"failed", "cancelled"}:
            raise ValueError("Повторить можно только publish_jobs со статусом failed или cancelled.")
        if not db.retry_publish_job(job_id):
            raise ValueError("Не удалось вернуть publish job в очередь.")
        updated = db.get_publish_job(job_id)
        return {"job": _publish_job_dict(updated)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/{job_id}/cancel")
def publish_job_cancel(job_id: int) -> dict[str, Any]:
    try:
        _init()
        job = db.get_publish_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Publish job {job_id} not found")
        if not db.cancel_publish_job(job_id):
            raise ValueError("Отменить можно только publish_jobs со статусом queued или failed.")
        updated = db.get_publish_job(job_id)
        return {"job": _publish_job_dict(updated)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/{job_id}/run")
def publish_job_run(job_id: int) -> dict[str, Any]:
    try:
        _init()
        job = run_publish_job_now(job_id)
        return {
            "job": _publish_job_dict(job),
            "youtube_url": _row(job, "youtube_url"),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/worker/run-once")
def publish_worker_run_once(req: PublishWorkerRunOnceRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        rows = run_publish_queue_once(limit=req.limit if req else 3)
        return {
            "jobs": [_publish_job_dict(row) for row in rows if row is not None],
            "processed": len(rows),
        }
    except Exception as exc:
        raise _fail(exc)


@router.get("/status")
def status() -> dict[str, Any]:
    try:
        _init()
        videos = [_video_dict(row) for row in db.list_videos_with_counts()]
        jobs = [_job_dict(row) for row in db.list_jobs(limit=20)]
        clip_counts = _status_counts(
            db.count_clips_by_status(),
            ("queued", "rendering", "done", "failed"),
        )
        job_counts = _status_counts(
            db.count_jobs_by_status(),
            ("queued", "running", "done", "failed"),
        )
        video_counts = db.count_videos_by_review_status()
        errors = [
            {
                "kind": row["kind"], "id": row["id"], "video_id": row["video_id"],
                "status": row["status"], "error": row["error"], "at": row["at"],
            }
            for row in db.list_recent_errors(limit=5)
        ]
        return {
            "videos_total": db.count_videos(),
            "segments_total": db.count_segments(),
            "videos_by_status": video_counts,
            "jobs": job_counts,
            "clips": clip_counts,
            "latest_jobs": jobs[:5],
            "latest_outputs": _latest_outputs(),
            "recent_errors": errors,
            "latest_videos": videos[:10],
        }
    except Exception as exc:
        raise _fail(exc)


@router.get("/jobs")
def jobs(limit: int = 100) -> dict[str, Any]:
    try:
        _init()
        rows = [_job_dict(row) for row in db.list_jobs(limit=limit)]
        return {"jobs": rows, "counts": dict(Counter(row["status"] for row in rows))}
    except Exception as exc:
        raise _fail(exc)


@router.get("/videos")
def videos() -> dict[str, Any]:
    try:
        _init()
        rows = [_video_dict(row) for row in db.list_videos_with_counts()]
        return {"videos": rows, "counts": dict(Counter(row["review_status"] for row in rows))}
    except Exception as exc:
        raise _fail(exc)


@router.get("/clips")
def clips(status: str | None = None, limit: int = 500) -> dict[str, Any]:
    try:
        _init()
        rows = [_clip_dict(row) for row in db.list_clips(status=status if status != "all" else None, limit=limit)]
        all_counts = db.count_clips_by_status()
        return {"clips": rows, "counts": all_counts}
    except Exception as exc:
        raise _fail(exc)


@router.get("/outputs")
def outputs() -> dict[str, Any]:
    try:
        _init()
        return {"outputs": _latest_outputs()}
    except Exception as exc:
        raise _fail(exc)


@router.post("/split")
def split(req: SplitRequest) -> dict[str, Any]:
    try:
        _init()
        if req.kind == "folder":
            items = split_video_folder(
                Path(req.path),
                segment_seconds=req.seconds,
                skip_specs=req.skip,
                dry_run=req.dry_run,
                overwrite=req.overwrite,
            )
            files = [_folder_item(item) for item in items]
            return {
                "kind": "folder",
                "status": "preview" if req.dry_run else "done",
                "dry_run": req.dry_run,
                "files": files,
                "files_count": len(files),
                "ok_count": sum(1 for item in files if item["status"] == "ok"),
                "failed_count": sum(1 for item in files if item["status"] == "failed"),
                "segments_count": sum((item.get("result") or {}).get("segments_count", 0) for item in files),
            }
        if req.kind != "file":
            raise ValueError("kind must be 'file' or 'folder'")
        result = split_video_file(
            Path(req.path),
            segment_seconds=req.seconds,
            skip_specs=req.skip,
            dry_run=req.dry_run,
            overwrite=req.overwrite,
        )
        data = _split_result(result)
        data["kind"] = "file"
        return data
    except Exception as exc:
        raise _fail(exc)


# Compatibility endpoints for the earlier UI layer.
@router.post("/split-dry-run")
def split_dry_run(req: SplitRequest) -> dict[str, Any]:
    req.kind = "file"
    req.dry_run = True
    return split(req)


@router.post("/split-jobs")
def split_jobs(req: SplitRequest) -> dict[str, Any]:
    req.kind = "file"
    req.dry_run = False
    return split(req)


@router.post("/split-folder-dry-run")
def split_folder_dry_run(req: SplitRequest) -> dict[str, Any]:
    req.kind = "folder"
    req.dry_run = True
    return split(req)


@router.post("/split-folder-jobs")
def split_folder_jobs(req: SplitRequest) -> dict[str, Any]:
    req.kind = "folder"
    req.dry_run = False
    return split(req)


@router.post("/render")
def render(req: RenderRequest) -> dict[str, Any]:
    try:
        _init()
        results = render_queued(limit=req.limit)
        return {"count": len(results), "rendered": [{"clip_id": cid, "path": str(path)} for cid, path in results]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/retry-failed")
def retry_failed(req: RetryFailedRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        reset_ids, skipped_ids = retry_failed_clips(clip_id=req.clip_id if req else None)
        return {"reset_ids": reset_ids, "skipped_ids": skipped_ids, "reset_count": len(reset_ids), "skipped_count": len(skipped_ids)}
    except Exception as exc:
        raise _fail(exc)


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    _init()
    checks: dict[str, str] = {}
    for binary in ("ffmpeg", "ffprobe"):
        try:
            checks[binary] = f"OK: {require_binary(binary)}"
        except Exception as exc:
            checks[binary] = f"ERROR: {exc}"
    try:
        from ..mpv_session import LUA_SCRIPT, require_mpv
        checks["mpv"] = f"OK: {require_mpv()}"
        checks["lua"] = f"{'OK' if LUA_SCRIPT.exists() else 'MISSING'}: {LUA_SCRIPT}"
    except Exception as exc:
        checks["mpv"] = f"ERROR: {exc}"
    checks["data_dir"] = str(data_dir())
    checks["input_dir"] = str(input_dir())
    checks["output_dir"] = str(output_dir())
    checks["logs_dir"] = str(logs_dir())
    checks["db_path"] = str(db_path())
    return checks
