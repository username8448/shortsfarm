from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
from ..edit_planner import parse_workspace_item_key, plan_edit_jobs_for_workspace_items
from ..edit_renderer import (
    render_edit_job,
    resolve_edit_job_media_path,
    run_edit_queue_once,
)
from ..ffmpeg_tools import probe_duration, require_binary
from ..local_dialogs import (
    LocalDialogUnavailable,
    pick_directory_dialog,
    pick_file_dialog,
)
from ..mpv_session import require_mpv
from ..publish_youtube import (
    fetch_youtube_channel_videos,
    parse_tags,
    run_publish_job_now,
    run_publish_queue_once,
    update_youtube_video_metadata,
    upload_clip_to_youtube,
    validate_publish_job,
    validate_publish_options,
)
from ..prepare_video import prepare_workspace_video
from ..publish_schedule import schedule_state, seconds_until
from ..render import render_queued, retry_failed_clips
from ..services import (
    VIDEO_EXTENSIONS,
    FileSplitResult,
    FolderSplitItem,
    split_video_file,
    split_video_folder,
)
from ..youtube_oauth import YOUTUBE_SCOPES
from ..workspace_fs import (
    SYSTEM_FOLDERS,
    create_workspace_folder,
    delete_workspace_item as delete_managed_workspace_item,
    ensure_workspace_layout,
    get_workspace_root,
    import_source_file,
    list_workspace_dir,
    move_workspace_item as move_managed_workspace_item,
    register_workspace_source,
    rename_workspace_item as rename_managed_workspace_item,
    resolve_workspace_path,
    set_workspace_root,
)
from .schemas import (
    ChannelProfileCreateRequest,
    ChannelProfileUpdateRequest,
    CatalogVideoTagsRequest,
    EditJobRenderRequest,
    EditJobReviewRequest,
    EditJobsBulkRenderRequest,
    EditJobsPlanRequest,
    EditTemplateUpdateRequest,
    EditWorkerRunOnceRequest,
    FileFolderCreateRequest,
    FileImportSourceRequest,
    FileMoveRequest,
    FileRegisterSourceRequest,
    FileRenameRequest,
    LocalDialogPickRequest,
    LocalStorageProfileAutoImportRunRequest,
    LocalStorageProfileCreateRequest,
    LocalStorageProfileItemCreateRequest,
    LocalStorageProfileTagRulesRequest,
    LocalStorageProfileUpdateRequest,
    LocalStorageProfileYouTubeLinkRequest,
    LocalStorageProfileYouTubePublishRequest,
    OpenMpvRequest,
    PublishJobRetryRequest,
    PublishJobRunRequest,
    PublishJobsBulkRequest,
    PublishScheduleGroupRequest,
    PublishWorkerRunOnceRequest,
    ReactionAssetCreateRequest,
    ReactionAssetUpdateRequest,
    ReactionFolderImportRequest,
    ReactionPoolCreateRequest,
    ReactionPoolItemRequest,
    ReactionPoolUpdateRequest,
    RenderRequest,
    RetryFailedRequest,
    SplitRequest,
    TagCreateRequest,
    TagUpdateRequest,
    WorkspaceBulkDeleteRequest,
    WorkspaceBulkPrepareRequest,
    WorkspaceBulkStatusRequest,
    WorkspaceItemUpdateRequest,
    WorkspacePrepareRequest,
    WorkspaceRootRequest,
    WorkspaceYouTubeEnqueueRequest,
    YouTubeMetadataUpdateRequest,
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


def _request_updates(req: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    supplied = getattr(req, "model_fields_set", None)
    if supplied is None:
        supplied = getattr(req, "__fields_set__", set())
    return {field: getattr(req, field) for field in fields if field in supplied}


def _reaction_asset_dict(row: Any) -> dict[str, Any]:
    file_path = str(_row(row, "file_path", "") or "")
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "file_path": file_path,
        "duration_sec": _row(row, "duration_sec"),
        "tags": _row(row, "tags"),
        "mood": _row(row, "mood"),
        "language": _row(row, "language"),
        "enabled": bool(_row(row, "enabled", 1)),
        "file_exists": bool(file_path and Path(file_path).expanduser().exists()),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _reaction_pool_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "description": _row(row, "description"),
        "enabled": bool(_row(row, "enabled", 1)),
        "item_count": int(_row(row, "item_count", 0) or 0),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _reaction_pool_item_dict(row: Any) -> dict[str, Any]:
    file_path = str(_row(row, "file_path", "") or "")
    return {
        "item_id": int(row["item_id"]),
        "pool_id": int(row["pool_id"]),
        "reaction_asset_id": int(row["reaction_asset_id"]),
        "weight": int(_row(row, "weight", 1)),
        "enabled": bool(_row(row, "enabled", 1)),
        "asset_name": _row(row, "asset_name", "") or "",
        "file_path": file_path,
        "tags": _row(row, "tags"),
        "mood": _row(row, "mood"),
        "language": _row(row, "language"),
        "file_exists": bool(file_path and Path(file_path).expanduser().exists()),
    }


def _edit_template_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "key": _row(row, "key", "") or "",
        "name": _row(row, "name", "") or "",
        "description": _row(row, "description"),
        "renderer": _row(row, "renderer", "ffmpeg") or "ffmpeg",
        "recipe_json": _row(row, "recipe_json", "") or "",
        "enabled": bool(_row(row, "enabled", 1)),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _channel_profile_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "youtube_account_id": _row(row, "youtube_account_id"),
        "youtube_channel_title": _row(row, "youtube_channel_title", "") or "",
        "youtube_display_name": _row(row, "youtube_display_name", "") or "",
        "default_template_id": _row(row, "default_template_id"),
        "default_template_name": _row(row, "default_template_name", "") or "",
        "reaction_pool_id": _row(row, "reaction_pool_id"),
        "reaction_pool_name": _row(row, "reaction_pool_name", "") or "",
        "title_template": _row(row, "title_template"),
        "description_template": _row(row, "description_template"),
        "tags_template": _row(row, "tags_template"),
        "default_privacy": _row(row, "default_privacy"),
        "default_category_id": _row(row, "default_category_id"),
        "enabled": bool(_row(row, "enabled", 1)),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _edit_job_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "status": _row(row, "status", "queued") or "queued",
        "workspace_item_key": _row(row, "workspace_item_key", "") or "",
        "channel_profile_id": _row(row, "channel_profile_id"),
        "channel_profile_name": _row(row, "channel_profile_name", "") or "",
        "template_id": _row(row, "template_id"),
        "template_name": _row(row, "template_name", "") or "",
        "template_key": _row(row, "template_key", "") or "",
        "reaction_asset_id": _row(row, "reaction_asset_id"),
        "reaction_asset_name": _row(row, "reaction_asset_name", "") or "",
        "input_path": _row(row, "input_path"),
        "output_path": _row(row, "output_path"),
        "edited_path": _row(row, "edited_path"),
        "renderer": _row(row, "renderer", "ffmpeg") or "ffmpeg",
        "recipe_json": _row(row, "recipe_json"),
        "error": _row(row, "error"),
        "review_status": _row(row, "review_status", "pending") or "pending",
        "reviewed_at": _row(row, "reviewed_at"),
        "review_note": _row(row, "review_note"),
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "started_at"),
        "finished_at": _row(row, "finished_at"),
    }


def _parse_workspace_key(value: str) -> tuple[str, int]:
    return parse_workspace_item_key(value)


def _workspace_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in ("draft", "ready", "queued", "uploaded", "failed")}
    for item in items:
        status = str(item.get("workspace_status") or "draft")
        if status in counts:
            counts[status] += 1
    counts["all"] = len(items)
    counts["missing"] = sum(1 for item in items if item.get("missing"))
    return counts


def _workspace_items_response(
    items: list[dict[str, Any]],
    *,
    counts_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = _workspace_items_with_catalog_tags(items)
    return {
        "items": enriched,
        "counts": _workspace_counts(counts_items if counts_items is not None else items),
    }


LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS = {"edits", "ready", "published"}


def _tag_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "slug": _row(row, "slug", "") or "",
        "kind": _row(row, "kind", "user") or "user",
        "color": _row(row, "color", "#64748b") or "#64748b",
        "description": _row(row, "description", "") or "",
        "system_key": _row(row, "system_key"),
        "locked": bool(_row(row, "locked", 0)),
        "enabled": bool(_row(row, "enabled", 1)),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _tag_rule_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "tag_id": int(row["tag_id"]),
        "mode": _row(row, "mode", "include") or "include",
        "locked": bool(_row(row, "locked", 0)),
        "source": _row(row, "source", "manual") or "manual",
        "tag": {
            "id": int(row["tag_id"]),
            "name": _row(row, "name", "") or "",
            "slug": _row(row, "slug", "") or "",
            "kind": _row(row, "kind", "user") or "user",
            "color": _row(row, "color", "#64748b") or "#64748b",
            "description": _row(row, "description", "") or "",
            "system_key": _row(row, "system_key"),
            "locked": bool(_row(row, "tag_locked", 0)),
            "enabled": bool(_row(row, "tag_enabled", 1)),
        },
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _local_storage_service_link_dict(row: Any) -> dict[str, Any]:
    platform = _row(row, "platform", "") or ""
    external_account_id = _row(row, "external_account_id")
    data = {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "platform": platform,
        "external_account_id": external_account_id,
        "display_name": _row(row, "display_name", "") or "",
        "status": _row(row, "status", "not_connected") or "not_connected",
        "settings_json": _row(row, "settings_json"),
        "last_sync_at": _row(row, "last_sync_at"),
        "last_sync_error": _row(row, "last_sync_error"),
        "synced_video_count": int(_row(row, "synced_video_count", 0) or 0),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }
    if platform == "youtube" and external_account_id is not None:
        account = db.get_social_account(int(external_account_id))
        if account is not None:
            data["youtube_account"] = _social_account_dict(account)
    return data


def _local_storage_profile_dict(row: Any, *, include_links: bool = False) -> dict[str, Any]:
    profile_id = int(row["id"])
    try:
        auto_sections = json.loads(str(_row(row, "auto_import_sections", "[]") or "[]"))
    except json.JSONDecodeError:
        auto_sections = ["edits", "ready", "published"]
    if not isinstance(auto_sections, list):
        auto_sections = ["edits", "ready", "published"]
    data = {
        "id": profile_id,
        "name": _row(row, "name", "") or "",
        "handle": _row(row, "handle", "") or "",
        "description": _row(row, "description", "") or "",
        "avatar_initials": _row(row, "avatar_initials", "") or "",
        "avatar_color": _row(row, "avatar_color", "#3b82f6") or "#3b82f6",
        "banner_color": _row(row, "banner_color", "#111827") or "#111827",
        "enabled": bool(_row(row, "enabled", 1)),
        "item_count": int(_row(row, "item_count", 0) or 0),
        "tag_match_mode": _row(row, "tag_match_mode", "any") or "any",
        "auto_import": {
            "enabled": bool(_row(row, "auto_import_enabled", 0)),
            "sections": [str(item) for item in auto_sections],
            "prefix": _row(row, "auto_import_prefix", "") or "",
            "last_scan_at": _row(row, "auto_import_last_scan_at"),
        },
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }
    if include_links:
        data["service_links"] = [
            _local_storage_service_link_dict(link)
            for link in db.list_local_storage_profile_service_links(profile_id)
        ]
        data["tag_rules"] = [
            _tag_rule_dict(rule)
            for rule in db.list_local_storage_profile_tag_rules(profile_id)
        ]
    return data


def _validate_local_storage_workspace_video(value: str) -> tuple[str, Path]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("workspace_path не задан.")
    if "\\" in text:
        raise ValueError("Используйте '/' в workspace paths.")
    if text.startswith("/") or (len(text) >= 3 and text[1:3] == ":/"):
        raise ValueError("Абсолютные пути запрещены. Используйте workspace-relative path.")
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Path traversal в workspace запрещён.")
    if any(part == ".shortsfarm" for part in relative.parts):
        raise PermissionError("Доступ к .shortsfarm запрещён.")
    if not relative.parts or relative.parts[0] not in LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS:
        raise PermissionError("В профиль можно добавлять только готовые видео из edits/, ready/ или published/.")
    path = resolve_workspace_path(relative.as_posix())
    if not path.exists():
        raise FileNotFoundError(f"Workspace video не найден: {relative.as_posix()}")
    if not path.is_file():
        raise ValueError("В профиль можно добавить только обычный файл.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("В профиль можно добавить только поддерживаемый video file.")
    return relative.as_posix(), path


def _validate_catalog_workspace_video(value: str, *, ready_only: bool = False) -> tuple[str, Path]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("workspace_path не задан.")
    if "\\" in text:
        raise ValueError("Используйте '/' в workspace paths.")
    if text.startswith("/") or (len(text) >= 3 and text[1:3] == ":/"):
        raise ValueError("Абсолютные пути запрещены. Используйте workspace-relative path.")
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Path traversal в workspace запрещён.")
    if any(part == ".shortsfarm" for part in relative.parts):
        raise PermissionError("Доступ к .shortsfarm запрещён.")
    allowed_sections = LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS if ready_only else set(SYSTEM_FOLDERS)
    if not relative.parts or relative.parts[0] not in allowed_sections:
        if ready_only:
            raise PermissionError("В профиль можно добавлять только готовые видео из edits/, ready/ или published/.")
        raise PermissionError("Видео должно находиться внутри workspace.")
    path = resolve_workspace_path(relative.as_posix())
    if path.is_symlink():
        raise PermissionError("Symlink запрещён.")
    if not path.exists():
        raise FileNotFoundError(f"Workspace video не найден: {relative.as_posix()}")
    if not path.is_file():
        raise ValueError("Можно тегировать только обычный файл.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Можно тегировать только поддерживаемый video file.")
    return relative.as_posix(), path


def _workspace_item_for_catalog_path(workspace_path: str) -> dict[str, Any] | None:
    try:
        abs_path = resolve_workspace_path(workspace_path).resolve()
    except Exception:
        return None
    for item in db.list_workspace_items(limit=10000, include_hidden=True):
        for key in ("path", "prepared_path"):
            raw = item.get(key)
            if not raw:
                continue
            try:
                if Path(str(raw)).expanduser().resolve() == abs_path:
                    return item
            except OSError:
                continue
    return None


def _catalog_tags_for_video(workspace_path: str, item: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    tag_rows = []
    try:
        if item:
            tag_rows = db.list_workspace_tag_links(
                workspace_path=workspace_path,
                item_type=str(item.get("item_type") or ""),
                item_id=int(item.get("item_id") or 0),
            )
        else:
            tag_rows = db.list_workspace_tag_links(workspace_path=workspace_path)
    except Exception:
        tag_rows = []
    tags_by_id: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for tag in tag_rows:
        payload = _tag_dict(tag)
        tags_by_id[payload["id"]] = payload
    if item:
        status_slug = f"status-{item.get('workspace_status') or 'draft'}"
        if not any(tag.get("slug") == status_slug for tag in tags_by_id.values()):
            status_tag = db.get_tag_by_slug(status_slug)
            if status_tag is not None:
                payload = _tag_dict(status_tag)
                tags_by_id[payload["id"]] = payload
    return list(tags_by_id.values())


def _catalog_video_dict(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    item = _workspace_item_for_catalog_path(relative)
    tags = _catalog_tags_for_video(relative, item)
    title = str(item.get("title") or "") if item else ""
    title = title or path.stem
    return {
        "workspace_path": relative,
        "section": relative.split("/", 1)[0],
        "file_name": path.name,
        "title": title,
        "workspace_status": str(item.get("workspace_status") or "") if item else "",
        "item_key": str(item.get("id") or "") if item else "",
        "item_type": str(item.get("item_type") or "") if item else "",
        "item_id": int(item.get("item_id") or 0) if item else None,
        "tags": tags,
        "is_publish_ready": any(tag.get("slug") == "status-ready" for tag in tags),
        "size": int(stat.st_size),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }


def _workspace_relative_path_for_item(item: dict[str, Any]) -> str:
    raw = str(item.get("path") or item.get("prepared_path") or "")
    if not raw:
        return ""
    try:
        if "/" in raw and not raw.startswith("/") and not (len(raw) >= 3 and raw[1:3] == ":/"):
            relative = PurePosixPath(raw)
            if relative.parts and relative.parts[0] in set(SYSTEM_FOLDERS):
                return relative.as_posix()
        root = get_workspace_root()
        if root is None:
            return ""
        resolved = Path(raw).expanduser().resolve()
        return resolved.relative_to(root.resolve()).as_posix()
    except Exception:
        return ""


def _workspace_item_with_catalog_tags(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    relative = _workspace_relative_path_for_item(payload)
    payload["workspace_path"] = relative
    if not relative:
        payload["catalog_tags"] = []
        payload["is_publish_ready"] = payload.get("workspace_status") == "ready"
        return payload
    try:
        tags = _catalog_tags_for_video(relative, payload)
    except Exception:
        tags = []
    payload["catalog_tags"] = tags
    payload["is_publish_ready"] = any(tag.get("slug") == "status-ready" for tag in tags)
    return payload


def _workspace_items_with_catalog_tags(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_workspace_item_with_catalog_tags(item) for item in items]


def _catalog_video_matches_query(item: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(item.get("workspace_path") or ""),
            str(item.get("file_name") or ""),
            str(item.get("title") or ""),
            str(item.get("workspace_status") or ""),
            " ".join(str(tag.get("name") or "") for tag in item.get("tags") or []),
            " ".join(str(tag.get("slug") or "") for tag in item.get("tags") or []),
        ]
    ).lower()
    return query.lower() in haystack


def _parse_tag_filter(value: str | None) -> set[int]:
    result: set[int] = set()
    for part in str(value or "").split(","):
        text = part.strip()
        if text.isdigit():
            result.add(int(text))
    return result


def _catalog_video_matches_tag_filter(item: dict[str, Any], tag_ids: set[int]) -> bool:
    if not tag_ids:
        return True
    item_tag_ids = {int(tag["id"]) for tag in item.get("tags") or []}
    return tag_ids <= item_tag_ids


def _list_catalog_videos(
    *,
    q: str = "",
    tags: str | None = None,
    limit: int = 100,
    randomize: bool = False,
    scope: str = "ready",
) -> list[dict[str, Any]]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    tag_filter = _parse_tag_filter(tags)
    items: list[dict[str, Any]] = []
    folders = tuple(SYSTEM_FOLDERS) if str(scope or "").lower() == "all" else ("edits", "ready", "published")
    for folder_name in folders:
        folder = root / folder_name
        if not folder.exists() or folder.is_symlink() or not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                relative = path.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                payload = _catalog_video_dict(path, root)
                if not _catalog_video_matches_query(payload, q):
                    continue
                if not _catalog_video_matches_tag_filter(payload, tag_filter):
                    continue
                items.append(payload)
            except (OSError, ValueError):
                continue
    if randomize:
        secrets.SystemRandom().shuffle(items)
    else:
        items.sort(key=lambda item: str(item.get("modified_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 100), 500))]


def _local_storage_profile_item_dict(row: Any) -> dict[str, Any]:
    workspace_path = _row(row, "workspace_path", "") or ""
    section = workspace_path.split("/", 1)[0] if workspace_path else ""
    file_name = workspace_path.split("/")[-1] if workspace_path else ""
    resolved_path = None
    file_exists = False
    size = None
    modified_at = None
    path_error = ""
    try:
        resolved_path = resolve_workspace_path(workspace_path)
        file_exists = resolved_path.exists() and resolved_path.is_file()
        if file_exists:
            stat = resolved_path.stat()
            size = int(stat.st_size)
            modified_at = datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
            ).isoformat()
    except Exception as exc:
        path_error = str(exc) or exc.__class__.__name__
    publish_job_row = db.get_latest_local_storage_profile_item_publish_job(int(row["id"]))
    publish_job = _publish_job_dict(publish_job_row) if publish_job_row is not None else None
    tag_rows = []
    if workspace_path:
        try:
            tag_rows = db.list_workspace_tag_links(workspace_path=workspace_path)
        except Exception:
            tag_rows = []
    catalog_tags = [_tag_dict(tag) for tag in tag_rows]
    is_publish_ready = any(tag.get("slug") == "status-ready" for tag in catalog_tags)
    return {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "workspace_path": workspace_path,
        "section": section,
        "file_name": file_name,
        "title": _row(row, "title", "") or Path(file_name).stem,
        "description": _row(row, "description", "") or "",
        "tags": _row(row, "tags", "") or "",
        "catalog_tags": catalog_tags,
        "is_publish_ready": is_publish_ready,
        "status": _row(row, "status", "draft") or "draft",
        "file_exists": file_exists,
        "size": size,
        "modified_at": modified_at,
        "path_error": path_error,
        "absolute_path": str(resolved_path) if resolved_path is not None else "",
        "publish_job": publish_job,
        "added_at": _row(row, "added_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _local_storage_external_video_dict(row: Any) -> dict[str, Any]:
    tags_raw = _row(row, "tags")
    try:
        tags = json.loads(str(tags_raw or "[]"))
    except json.JSONDecodeError:
        tags = parse_tags(tags_raw)
    if not isinstance(tags, list):
        tags = []
    profile_item_id = _row(row, "profile_item_id")
    publish_job_id = _row(row, "publish_job_id")
    return {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "platform": _row(row, "platform", "youtube") or "youtube",
        "external_video_id": _row(row, "external_video_id", "") or "",
        "external_url": _row(row, "external_url", "") or "",
        "title": _row(row, "title", "") or "",
        "description": _row(row, "description", "") or "",
        "tags": tags,
        "category_id": _row(row, "category_id", "") or "",
        "privacy_status": _row(row, "privacy_status", "") or "",
        "publish_at": _row(row, "publish_at"),
        "published_at": _row(row, "published_at"),
        "duration": _row(row, "duration", "") or "",
        "thumbnail_url": _row(row, "thumbnail_url", "") or "",
        "profile_item_id": profile_item_id,
        "publish_job_id": publish_job_id,
        "profile_item_workspace_path": _row(row, "profile_item_workspace_path", "") or "",
        "publish_job_status": _row(row, "publish_job_status", "") or "",
        "matched": profile_item_id is not None or publish_job_id is not None,
        "first_seen_at": _row(row, "first_seen_at"),
        "last_seen_at": _row(row, "last_seen_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_workspace_delete_path(item: dict[str, Any]) -> Path:
    raw_path = _normalize_setting_text(item.get("path"))
    if not raw_path:
        raise ValueError("Путь к файлу не задан.")

    candidate = Path(raw_path).expanduser()
    if not candidate.exists():
        raise FileNotFoundError(f"Файл уже отсутствует: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"Удалять можно только файлы, не папки: {candidate}")

    resolved = candidate.resolve()
    allowed_roots = [
        output_dir().resolve(),
        (data_dir() / "output").resolve(),
    ]
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise PermissionError("Удалять можно только файлы внутри output-директории ShortsFarm.")

    source_path = _normalize_setting_text(item.get("source_path"))
    if source_path:
        try:
            if resolved == Path(source_path).expanduser().resolve():
                raise PermissionError("Нельзя удалить исходное видео из workspace.")
        except FileNotFoundError:
            pass
    return resolved


def _delete_workspace_item(item_key: str) -> dict[str, Any]:
    item_type, item_id = _parse_workspace_key(item_key)
    item = db.get_workspace_item(item_type, item_id)
    if item is None:
        raise FileNotFoundError("Элемент рабочего пространства не найден.")

    result = {
        "id": item["id"],
        "item_type": item_type,
        "item_id": item_id,
        "file_deleted": False,
        "already_missing": False,
        "hidden": False,
        "message": "",
    }

    if item.get("file_exists"):
        delete_path = _safe_workspace_delete_path(item)
        delete_path.unlink()
        result["file_deleted"] = True
        result["message"] = "Файл удалён."
    else:
        result["already_missing"] = True
        result["message"] = "Файл уже отсутствовал."

    result["hidden"] = db.hide_workspace_item(
        item_type,
        item_id,
        missing_confirmed=bool(result["already_missing"]),
    )
    return result


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
    source_segment_id = _row(row, "clip_source_segment_id")
    source_clip_id = _row(row, "clip_source_clip_id")
    workspace_item_key = (
        f"segment:{source_segment_id}"
        if source_segment_id is not None
        else f"clip:{source_clip_id}" if source_clip_id is not None else f"clip:{int(row['clip_id'])}"
    )
    current_schedule_state = schedule_state(
        _row(row, "upload_at"),
        _row(row, "overdue_approved_at"),
    )
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
        "upload_at": _row(row, "upload_at"),
        "schedule_group_id": _row(row, "schedule_group_id"),
        "schedule_group_name": _row(row, "schedule_group_name", "") or "",
        "schedule_position": _row(row, "schedule_position"),
        "overdue_approved_at": _row(row, "overdue_approved_at"),
        "schedule_state": current_schedule_state,
        "is_overdue": current_schedule_state == "overdue",
        "seconds_until_upload": seconds_until(_row(row, "upload_at")),
        "seconds_until_publish": seconds_until(_row(row, "publish_at")),
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
        "clip_source_segment_id": source_segment_id,
        "clip_source_clip_id": source_clip_id,
        "clip_source_aspect": _row(row, "clip_source_aspect", "") or "",
        "workspace_item_key": workspace_item_key,
        "video_title": _row(row, "video_title", "") or "",
        "video_source_path": _row(row, "video_source_path", "") or "",
        "can_retry": _row(row, "status") in {"failed", "cancelled"},
        "can_run": (
            _row(row, "status") in {"queued", "failed"}
            and current_schedule_state not in {"waiting", "overdue"}
        ),
        "can_force_run": _row(row, "status") in {"queued", "failed"},
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


def _workspace_publish_title(item: dict[str, Any]) -> str:
    title = _normalize_setting_text(item.get("title"))
    if title:
        return title
    file_name = _normalize_setting_text(item.get("file_name"))
    if file_name:
        return Path(file_name).stem or file_name
    item_id = item.get("item_id") or item.get("id") or ""
    return f"ShortsFarm clip {item_id}".strip()


def _workspace_create_publish_job(
    *,
    item: dict[str, Any],
    clip_id: int,
    req: WorkspaceYouTubeEnqueueRequest,
) -> int:
    title = _workspace_publish_title(item)
    validated = validate_publish_options(
        title=title,
        publish_mode=req.publish_mode,
        publish_at=None,
        category_id=req.category_id,
    )
    tags = parse_tags(item.get("tags") or "")
    return db.create_publish_job(
        account_id=req.account_id,
        clip_id=clip_id,
        title=title,
        description=item.get("description") or "",
        tags=json.dumps(tags, ensure_ascii=False),
        category_id=req.category_id,
        privacy_status=str(validated["privacy_status"]),
        publish_mode=req.publish_mode,
        publish_at=validated["publish_at"],
        made_for_kids=req.made_for_kids,
        platform="youtube",
    )


def _workspace_youtube_skip(item_key: str, reason: str) -> dict[str, str]:
    return {"item_key": item_key, "reason": reason}


def _workspace_prepared_publish_path(item: dict[str, Any]) -> str | None:
    prepared_path = _normalize_setting_text(item.get("prepared_path"))
    if item.get("prepare_status") == "done" and prepared_path:
        candidate = Path(prepared_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _workspace_target_needs_prepare(item: dict[str, Any]) -> bool:
    target_aspect = str(item.get("target_aspect") or "original")
    return target_aspect != "original" and _workspace_prepared_publish_path(item) is None


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


def _workspace_settings_payload() -> dict[str, Any]:
    root = get_workspace_root()
    if root is None:
        return {
            "workspace_root": None,
            "exists": False,
            "layout": {},
        }
    exists = root.exists() and root.is_dir()
    layout = {
        name: str(root / name)
        for name in SYSTEM_FOLDERS
        if exists and (root / name).is_dir()
    }
    return {
        "workspace_root": str(root),
        "exists": exists,
        "layout": layout,
    }


@router.get("/settings/workspace")
def workspace_settings_get() -> dict[str, Any]:
    try:
        _init()
        return _workspace_settings_payload()
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/workspace")
def workspace_settings_save(req: WorkspaceRootRequest) -> dict[str, Any]:
    try:
        _init()
        set_workspace_root(req.workspace_root)
        return _workspace_settings_payload()
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/workspace/pick-directory")
def workspace_settings_pick_directory() -> dict[str, Any]:
    try:
        _init()
        selected_path = pick_directory_dialog()
        if selected_path is None:
            return {
                "selected": False,
                "workspace_root": _workspace_settings_payload()["workspace_root"],
            }
        set_workspace_root(selected_path)
        return {
            "selected": True,
            **_workspace_settings_payload(),
        }
    except LocalDialogUnavailable as exc:
        raise _fail(exc, status_code=409)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.post("/local-dialogs/pick")
def local_dialog_pick(req: LocalDialogPickRequest) -> dict[str, Any]:
    try:
        _init()
        kind = str(req.kind or "").strip().lower()
        if kind == "file":
            selected_path = pick_file_dialog(req.title or "Выберите файл")
        elif kind in {"directory", "folder"}:
            selected_path = pick_directory_dialog(req.title or "Выберите папку")
        else:
            raise ValueError("kind должен быть file или directory.")
        if selected_path is None:
            return {"selected": False, "path": None}
        return {"selected": True, "path": selected_path}
    except LocalDialogUnavailable as exc:
        raise _fail(exc, status_code=409)
    except Exception as exc:
        raise _fail(exc)


@router.get("/files")
def files_list(path: str = "") -> dict[str, Any]:
    try:
        _init()
        return list_workspace_dir(path)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/files/folder")
def files_folder_create(req: FileFolderCreateRequest) -> dict[str, Any]:
    try:
        _init()
        created = create_workspace_folder(
            req.parent_path,
            req.name,
            kind=req.kind,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": created.relative_to(root).as_posix(),
            "name": created.name,
            "kind": req.kind,
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileExistsError as exc:
        raise _fail(exc, status_code=409)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/files/rename")
def files_rename(req: FileRenameRequest) -> dict[str, Any]:
    try:
        _init()
        renamed = rename_managed_workspace_item(req.path, req.new_name)
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": renamed.relative_to(root).as_posix(),
            "name": renamed.name,
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except FileExistsError as exc:
        raise _fail(exc, status_code=409)
    except Exception as exc:
        raise _fail(exc)


@router.post("/files/move")
def files_move(req: FileMoveRequest) -> dict[str, Any]:
    try:
        _init()
        moved = move_managed_workspace_item(
            req.source_path,
            req.target_folder,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": moved.relative_to(root).as_posix(),
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except FileExistsError as exc:
        raise _fail(exc, status_code=409)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/files")
def files_delete(path: str, recursive: bool = False) -> dict[str, Any]:
    try:
        _init()
        deleted = delete_managed_workspace_item(path, recursive=recursive)
        return {"status": "ok", "path": path, "deleted": deleted}
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except OSError as exc:
        raise _fail(
            ValueError(
                "Папка не пуста. Подтвердите recursive delete."
                if not recursive
                else str(exc)
            )
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/files/import-source")
def files_import_source(req: FileImportSourceRequest) -> dict[str, Any]:
    try:
        _init()
        imported, video_id = import_source_file(
            req.source_path,
            req.target_folder,
            mode=req.mode,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": imported.relative_to(root).as_posix(),
            "name": imported.name,
            "video_id": video_id,
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/files/register-source")
def files_register_source(req: FileRegisterSourceRequest) -> dict[str, Any]:
    try:
        _init()
        path, video_id = register_workspace_source(req.path)
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": path.relative_to(root).as_posix(),
            "video_id": video_id,
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


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
            "type": "shortsfarm-youtube-oauth-complete" if ok else "shortsfarm-youtube-oauth-error",
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
        localStorage.setItem('shortsfarm.youtube.oauth.event', JSON.stringify(payload));
        localStorage.setItem('shortsfarm.youtube.oauth.updated', String(Date.now()));
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
            "Можно закрыть эту вкладку и вернуться в ShortsFarm. Затем нажмите «Обновить» в разделе «Публикация».",
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


def _schedule_group_dict(row: Any, *, include_jobs: bool = True) -> dict[str, Any]:
    def _json_times(value: Any) -> dict[str, str]:
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return {str(key): str(item) for key, item in parsed.items()}

    group_id = int(row["id"])
    payload = {
        "id": group_id,
        "name": _row(row, "name", "") or "",
        "upload": {
            "mode": _row(row, "upload_mode", "none") or "none",
            "start_at": _row(row, "upload_start_at"),
            "interval_minutes": _row(row, "upload_interval_minutes"),
            "item_times": _json_times(_row(row, "upload_item_times")),
        },
        "publish": {
            "mode": _row(row, "publish_mode", "none") or "none",
            "start_at": _row(row, "publish_start_at"),
            "interval_minutes": _row(row, "publish_interval_minutes"),
            "item_times": _json_times(_row(row, "publish_item_times")),
        },
        "job_count": int(_row(row, "job_count", 0) or 0),
        "queued_count": int(_row(row, "queued_count", 0) or 0),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }
    if include_jobs:
        payload["jobs"] = [
            _publish_job_dict(job)
            for job in db.list_publish_schedule_group_jobs(group_id)
        ]
    return payload


@router.get("/publish/schedule-groups")
def publish_schedule_groups() -> dict[str, Any]:
    try:
        _init()
        return {
            "groups": [
                _schedule_group_dict(row)
                for row in db.list_publish_schedule_groups()
            ]
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/schedule-groups")
def publish_schedule_group_create(req: PublishScheduleGroupRequest) -> dict[str, Any]:
    try:
        _init()
        group_id = db.save_publish_schedule_group(
            name=req.name,
            job_ids=req.job_ids,
            upload_spec=req.upload.model_dump(),
            publish_spec=req.publish.model_dump(),
        )
        group = db.get_publish_schedule_group(group_id)
        return {"status": "ok", "group": _schedule_group_dict(group)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/publish/schedule-groups/{group_id}")
def publish_schedule_group_update(
    group_id: int,
    req: PublishScheduleGroupRequest,
) -> dict[str, Any]:
    try:
        _init()
        resolved_id = db.save_publish_schedule_group(
            group_id=group_id,
            name=req.name,
            job_ids=req.job_ids,
            upload_spec=req.upload.model_dump(),
            publish_spec=req.publish.model_dump(),
        )
        group = db.get_publish_schedule_group(resolved_id)
        return {"status": "ok", "group": _schedule_group_dict(group)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/publish/schedule-groups/{group_id}")
def publish_schedule_group_delete(group_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.remove_publish_schedule_group(group_id):
            raise FileNotFoundError("Группа расписания не найдена.")
        return {"status": "ok", "group_id": group_id}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/schedule-groups/{group_id}/approve-overdue")
def publish_schedule_group_approve_overdue(group_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_publish_schedule_group(group_id) is None:
            raise FileNotFoundError("Группа расписания не найдена.")
        approved = db.approve_overdue_publish_schedule_group(group_id)
        group = db.get_publish_schedule_group(group_id)
        return {
            "status": "ok",
            "approved": approved,
            "group": _schedule_group_dict(group),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/{job_id}/youtube/update-metadata")
def publish_job_update_youtube_metadata(
    job_id: int,
    req: YouTubeMetadataUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        job = db.get_publish_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Publish job {job_id} не найден")

        job_payload = _publish_job_dict(job)
        item_type, item_id = _parse_workspace_key(job_payload["workspace_item_key"])
        workspace_item = db.get_workspace_item(item_type, item_id)

        title = req.title
        description = req.description
        tags = req.tags
        if workspace_item is not None:
            if title is None:
                title = _workspace_publish_title(workspace_item)
            if description is None:
                description = workspace_item.get("description") or ""
            if tags is None:
                tags = workspace_item.get("tags") or ""

        updated = update_youtube_video_metadata(
            job_id,
            title=title,
            description=description,
            tags=tags,
            category_id=req.category_id,
            privacy_status=req.privacy_status,
            made_for_kids=req.made_for_kids,
        )
        return {
            "status": "ok",
            "message": "Данные видео на YouTube обновлены.",
            "job": _publish_job_dict(updated),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
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


@router.post("/publish/jobs/bulk-run")
def publish_jobs_bulk_run(req: PublishJobsBulkRequest) -> dict[str, Any]:
    try:
        _init()
        results: list[dict[str, Any]] = []
        summary = {"processed": 0, "errors": 0}
        for job_id in req.job_ids:
            try:
                job = (
                    run_publish_job_now(int(job_id), force=True)
                    if req.force
                    else run_publish_job_now(int(job_id))
                )
                results.append({"job_id": int(job_id), "status": "done", "job": _publish_job_dict(job)})
                summary["processed"] += 1
            except Exception as exc:
                results.append({"job_id": int(job_id), "status": "error", "error": str(exc) or exc.__class__.__name__})
                summary["errors"] += 1
        return {"status": "ok", "summary": summary, "results": results}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/bulk-retry")
def publish_jobs_bulk_retry(req: PublishJobsBulkRequest) -> dict[str, Any]:
    try:
        _init()
        results: list[dict[str, Any]] = []
        summary = {"updated": 0, "skipped": 0, "errors": 0}
        for job_id in req.job_ids:
            try:
                job = db.get_publish_job(int(job_id))
                if job is None:
                    raise FileNotFoundError(f"Publish job {job_id} not found")
                if job["status"] not in {"failed", "cancelled"}:
                    summary["skipped"] += 1
                    results.append({"job_id": int(job_id), "status": "skipped", "reason": "Можно повторить только failed или cancelled."})
                    continue
                ok = db.retry_publish_job(int(job_id))
                updated = db.get_publish_job(int(job_id))
                results.append({"job_id": int(job_id), "status": "queued" if ok else "skipped", "job": _publish_job_dict(updated) if updated else None})
                summary["updated" if ok else "skipped"] += 1
            except Exception as exc:
                summary["errors"] += 1
                results.append({"job_id": int(job_id), "status": "error", "error": str(exc) or exc.__class__.__name__})
        return {"status": "ok", "summary": summary, "results": results}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/bulk-cancel")
def publish_jobs_bulk_cancel(req: PublishJobsBulkRequest) -> dict[str, Any]:
    try:
        _init()
        results: list[dict[str, Any]] = []
        summary = {"updated": 0, "skipped": 0, "errors": 0}
        for job_id in req.job_ids:
            try:
                job = db.get_publish_job(int(job_id))
                if job is None:
                    raise FileNotFoundError(f"Publish job {job_id} not found")
                if job["status"] not in {"queued", "failed"}:
                    summary["skipped"] += 1
                    results.append({"job_id": int(job_id), "status": "skipped", "reason": "Можно отменить только queued или failed."})
                    continue
                ok = db.cancel_publish_job(int(job_id))
                updated = db.get_publish_job(int(job_id))
                results.append({"job_id": int(job_id), "status": "cancelled" if ok else "skipped", "job": _publish_job_dict(updated) if updated else None})
                summary["updated" if ok else "skipped"] += 1
            except Exception as exc:
                summary["errors"] += 1
                results.append({"job_id": int(job_id), "status": "error", "error": str(exc) or exc.__class__.__name__})
        return {"status": "ok", "summary": summary, "results": results}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/jobs/{job_id}/run")
def publish_job_run(
    job_id: int,
    req: PublishJobRunRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        force = bool(req.force) if req else False
        job = (
            run_publish_job_now(job_id, force=True)
            if force
            else run_publish_job_now(job_id)
        )
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


@router.get("/editing/reactions")
def editing_reactions(
    enabled: bool | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    try:
        _init()
        items = [_reaction_asset_dict(row) for row in db.list_reaction_assets(enabled=enabled)]
        query = str(q or "").strip().casefold()
        if query:
            fields = ("name", "tags", "mood", "language", "file_path")
            items = [
                item for item in items
                if any(query in str(item.get(field) or "").casefold() for field in fields)
            ]
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/reactions")
def editing_reaction_create(req: ReactionAssetCreateRequest) -> dict[str, Any]:
    try:
        _init()
        name = str(req.name or "").strip()
        file_path = str(req.file_path or "").strip()
        if not name:
            raise ValueError("Название реакции обязательно.")
        if not file_path:
            raise ValueError("Путь к файлу реакции обязателен.")
        asset_id = db.create_reaction_asset(
            name=name,
            file_path=file_path,
            duration_sec=req.duration_sec,
            tags=req.tags,
            mood=req.mood,
            language=req.language,
            enabled=req.enabled,
        )
        return {"item": _reaction_asset_dict(db.get_reaction_asset(asset_id))}
    except sqlite3.IntegrityError as exc:
        if "file_path" in str(exc):
            raise _fail(ValueError("Реакция с таким file_path уже существует."))
        raise _fail(exc)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/editing/reactions/{asset_id}")
def editing_reaction_update(
    asset_id: int,
    req: ReactionAssetUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(
            req,
            ("name", "file_path", "duration_sec", "tags", "mood", "language", "enabled"),
        )
        if "name" in updates and not str(updates["name"] or "").strip():
            raise ValueError("Название реакции обязательно.")
        if "file_path" in updates and not str(updates["file_path"] or "").strip():
            raise ValueError("Путь к файлу реакции обязателен.")
        ok = db.update_reaction_asset(asset_id, **updates)
        if not ok:
            raise FileNotFoundError("Reaction asset не найден.")
        return {"item": _reaction_asset_dict(db.get_reaction_asset(asset_id))}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except sqlite3.IntegrityError as exc:
        if "file_path" in str(exc):
            raise _fail(ValueError("Реакция с таким file_path уже существует."))
        raise _fail(exc)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/reactions/{asset_id}/disable")
def editing_reaction_disable(asset_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.disable_reaction_asset(asset_id):
            raise FileNotFoundError("Reaction asset не найден.")
        return {"item": _reaction_asset_dict(db.get_reaction_asset(asset_id))}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/reactions/import-folder")
def editing_reactions_import_folder(req: ReactionFolderImportRequest) -> dict[str, Any]:
    try:
        _init()
        folder = Path(str(req.folder_path or "")).expanduser().resolve()
        if not folder.exists():
            raise FileNotFoundError(f"Папка не найдена: {folder}")
        if not folder.is_dir():
            raise ValueError(f"Это не папка: {folder}")
        extensions = {".mp4", ".mov", ".mkv", ".webm"}
        candidates = folder.rglob("*") if req.recursive else folder.iterdir()
        files = sorted(
            path.resolve()
            for path in candidates
            if path.is_file() and path.suffix.lower() in extensions
        )
        existing_paths = {
            str(row["file_path"])
            for row in db.list_reaction_assets()
        }
        created = 0
        skipped = 0
        errors = 0
        items: list[dict[str, Any]] = []
        for path in files:
            file_path = str(path)
            if file_path in existing_paths:
                skipped += 1
                continue
            try:
                asset_id = db.create_reaction_asset(
                    name=path.stem,
                    file_path=file_path,
                    tags=req.tags,
                    mood=req.mood,
                    language=req.language,
                )
                item = _reaction_asset_dict(db.get_reaction_asset(asset_id))
                items.append(item)
                existing_paths.add(file_path)
                created += 1
            except sqlite3.IntegrityError:
                skipped += 1
            except Exception as exc:
                errors += 1
                items.append({"file_path": file_path, "error": str(exc) or exc.__class__.__name__})
        return {
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "items": items,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/reaction-pools")
def editing_reaction_pools(enabled: bool | None = None) -> dict[str, Any]:
    try:
        _init()
        items = [
            _reaction_pool_dict(row)
            for row in db.list_reaction_pools_with_counts(enabled=enabled)
        ]
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/reaction-pools")
def editing_reaction_pool_create(req: ReactionPoolCreateRequest) -> dict[str, Any]:
    try:
        _init()
        name = str(req.name or "").strip()
        if not name:
            raise ValueError("Название пула обязательно.")
        pool_id = db.create_reaction_pool(
            name=name,
            description=req.description,
            enabled=req.enabled,
        )
        row = next(
            row for row in db.list_reaction_pools_with_counts()
            if int(row["id"]) == pool_id
        )
        return {"item": _reaction_pool_dict(row)}
    except sqlite3.IntegrityError:
        raise _fail(ValueError("Пул с таким названием уже существует."))
    except Exception as exc:
        raise _fail(exc)


@router.patch("/editing/reaction-pools/{pool_id}")
def editing_reaction_pool_update(
    pool_id: int,
    req: ReactionPoolUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(req, ("name", "description", "enabled"))
        if "name" in updates and not str(updates["name"] or "").strip():
            raise ValueError("Название пула обязательно.")
        if not db.update_reaction_pool(pool_id, **updates):
            raise FileNotFoundError("Reaction pool не найден.")
        row = next(
            row for row in db.list_reaction_pools_with_counts()
            if int(row["id"]) == pool_id
        )
        return {"item": _reaction_pool_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except sqlite3.IntegrityError:
        raise _fail(ValueError("Пул с таким названием уже существует."))
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/reaction-pools/{pool_id}/items")
def editing_reaction_pool_item_add(
    pool_id: int,
    req: ReactionPoolItemRequest,
) -> dict[str, Any]:
    try:
        _init()
        if db.get_reaction_pool(pool_id) is None:
            raise FileNotFoundError("Reaction pool не найден.")
        if db.get_reaction_asset(req.reaction_asset_id) is None:
            raise FileNotFoundError("Reaction asset не найден.")
        db.upsert_reaction_pool_item(pool_id, req.reaction_asset_id, req.weight)
        items = [
            _reaction_pool_item_dict(row)
            for row in db.list_reaction_pool_items_with_assets(pool_id)
        ]
        return {"items": items}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/reaction-pools/{pool_id}/items")
def editing_reaction_pool_items(pool_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_reaction_pool(pool_id) is None:
            raise FileNotFoundError("Reaction pool не найден.")
        items = [
            _reaction_pool_item_dict(row)
            for row in db.list_reaction_pool_items_with_assets(pool_id)
        ]
        return {"items": items, "count": len(items)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/editing/reaction-pools/{pool_id}/items/{reaction_asset_id}")
def editing_reaction_pool_item_delete(
    pool_id: int,
    reaction_asset_id: int,
) -> dict[str, Any]:
    try:
        _init()
        if not db.remove_reaction_from_pool(pool_id, reaction_asset_id):
            raise FileNotFoundError("Reaction asset не найден в этом пуле.")
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/templates")
def editing_templates(enabled: bool | None = None) -> dict[str, Any]:
    try:
        _init()
        items = [_edit_template_dict(row) for row in db.list_edit_templates(enabled=enabled)]
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/templates/ensure-defaults")
def editing_templates_ensure_defaults() -> dict[str, Any]:
    try:
        _init()
        item = db.ensure_default_edit_templates()
        return {"item": _edit_template_dict(item)}
    except Exception as exc:
        raise _fail(exc)


@router.patch("/editing/templates/{template_id}")
def editing_template_update(
    template_id: int,
    req: EditTemplateUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(
            req,
            ("name", "description", "renderer", "recipe_json", "enabled"),
        )
        if "name" in updates and not str(updates["name"] or "").strip():
            raise ValueError("Название шаблона обязательно.")
        if "renderer" in updates and not str(updates["renderer"] or "").strip():
            raise ValueError("Renderer обязателен.")
        if not db.update_edit_template(template_id, **updates):
            raise FileNotFoundError("Edit template не найден.")
        return {"item": _edit_template_dict(db.get_edit_template(template_id))}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/channel-profiles")
def editing_channel_profiles(enabled: bool | None = None) -> dict[str, Any]:
    try:
        _init()
        items = [
            _channel_profile_dict(row)
            for row in db.list_channel_profiles_with_details(enabled=enabled)
        ]
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/channel-profiles")
def editing_channel_profile_create(req: ChannelProfileCreateRequest) -> dict[str, Any]:
    try:
        _init()
        name = str(req.name or "").strip()
        if not name:
            raise ValueError("Название профиля канала обязательно.")
        profile_id = db.create_channel_profile(
            name=name,
            youtube_account_id=req.youtube_account_id,
            default_template_id=req.default_template_id,
            reaction_pool_id=req.reaction_pool_id,
            title_template=req.title_template,
            description_template=req.description_template,
            tags_template=req.tags_template,
            default_privacy=req.default_privacy,
            default_category_id=req.default_category_id,
            enabled=req.enabled,
        )
        row = next(
            row for row in db.list_channel_profiles_with_details()
            if int(row["id"]) == profile_id
        )
        return {"item": _channel_profile_dict(row)}
    except sqlite3.IntegrityError as exc:
        raise _fail(ValueError(f"Не удалось связать профиль: {exc}"))
    except Exception as exc:
        raise _fail(exc)


@router.patch("/editing/channel-profiles/{profile_id}")
def editing_channel_profile_update(
    profile_id: int,
    req: ChannelProfileUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(
            req,
            (
                "name", "youtube_account_id", "default_template_id",
                "reaction_pool_id", "title_template", "description_template",
                "tags_template", "default_privacy", "default_category_id", "enabled",
            ),
        )
        if "name" in updates and not str(updates["name"] or "").strip():
            raise ValueError("Название профиля канала обязательно.")
        if not db.update_channel_profile(profile_id, **updates):
            raise FileNotFoundError("Channel profile не найден.")
        row = next(
            row for row in db.list_channel_profiles_with_details()
            if int(row["id"]) == profile_id
        )
        return {"item": _channel_profile_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except sqlite3.IntegrityError as exc:
        raise _fail(ValueError(f"Не удалось связать профиль: {exc}"))
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/channel-profiles/{profile_id}/disable")
def editing_channel_profile_disable(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.disable_channel_profile(profile_id):
            raise FileNotFoundError("Channel profile не найден.")
        row = next(
            row for row in db.list_channel_profiles_with_details()
            if int(row["id"]) == profile_id
        )
        return {"item": _channel_profile_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/plan")
def editing_jobs_plan(req: EditJobsPlanRequest) -> dict[str, Any]:
    try:
        _init()
        if not req.item_keys:
            raise ValueError("Выберите хотя бы один workspace item.")
        return plan_edit_jobs_for_workspace_items(
            req.item_keys,
            req.channel_profile_id,
            reaction_asset_id=req.reaction_asset_id,
            template_id=req.template_id,
            force_new=req.force_new,
        )
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/jobs")
def editing_jobs(
    status: str | None = None,
    review_status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    try:
        _init()
        rows = db.list_edit_jobs_with_details(
            status=status if status and status != "all" else None,
            review_status=(
                review_status
                if review_status and review_status != "all"
                else None
            ),
            limit=limit,
        )
        return {"items": [_edit_job_dict(row) for row in rows], "count": len(rows)}
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/jobs/{job_id}/media")
def editing_job_media(job_id: int) -> FileResponse:
    try:
        _init()
        media_path = resolve_edit_job_media_path(job_id)
        return FileResponse(media_path, media_type="video/mp4")
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/editing/jobs/{job_id}/folder")
def editing_job_folder(job_id: int) -> dict[str, Any]:
    try:
        _init()
        media_path = resolve_edit_job_media_path(job_id)
        return {"status": "ok", "path": str(media_path.parent)}
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/open")
def editing_job_open(job_id: int) -> dict[str, Any]:
    try:
        _init()
        media_path = resolve_edit_job_media_path(job_id)
        mpv = require_mpv()
        subprocess.Popen(
            [mpv, str(media_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "status": "opened",
            "job_id": int(job_id),
            "player": "mpv",
        }
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


def _set_edit_job_review(
    job_id: int,
    review_status: str,
    req: EditJobReviewRequest | None,
) -> dict[str, Any]:
    if review_status in {"approved", "rejected"}:
        resolve_edit_job_media_path(job_id)
    elif db.get_edit_job(job_id) is None:
        raise FileNotFoundError("Edit job не найден.")

    note = req.note if req else None
    if not db.set_edit_job_review_status(job_id, review_status, note):
        raise FileNotFoundError("Edit job не найден.")
    updated = db.get_edit_job(job_id)
    if updated is None:
        raise FileNotFoundError("Edit job не найден.")
    return {"status": "ok", "job": _edit_job_dict(updated)}


@router.post("/editing/jobs/{job_id}/approve")
def editing_job_approve(
    job_id: int,
    req: EditJobReviewRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        return _set_edit_job_review(job_id, "approved", req)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/reject")
def editing_job_reject(
    job_id: int,
    req: EditJobReviewRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        return _set_edit_job_review(job_id, "rejected", req)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/reset-review")
def editing_job_reset_review(
    job_id: int,
    req: EditJobReviewRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        return _set_edit_job_review(job_id, "pending", req)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/bulk-render")
def editing_jobs_bulk_render(req: EditJobsBulkRenderRequest) -> dict[str, Any]:
    try:
        _init()
        if not req.job_ids:
            raise ValueError("Выберите хотя бы один edit job.")

        results: list[dict[str, Any]] = []
        summary = {"processed": 0, "skipped": 0, "errors": 0}
        seen: set[int] = set()
        for raw_job_id in req.job_ids:
            job_id = int(raw_job_id)
            if job_id in seen:
                continue
            seen.add(job_id)
            try:
                current = db.get_edit_job(job_id)
                if current is None:
                    raise FileNotFoundError(f"Edit job {job_id} не найден.")
                current_status = str(current["status"])
                runnable = (
                    current_status == "queued"
                    or (
                        req.force
                        and current_status in {"done", "failed", "cancelled"}
                    )
                )
                if not runnable:
                    summary["skipped"] += 1
                    results.append({
                        "job_id": job_id,
                        "status": "skipped",
                        "reason": (
                            "Job уже rendering."
                            if current_status == "rendering"
                            else f"Для status={current_status} требуется force=true."
                        ),
                    })
                    continue

                rendered = render_edit_job(job_id, force=req.force)
                summary["processed"] += 1
                results.append({
                    "job_id": job_id,
                    "status": str(rendered["status"]),
                    "job": _edit_job_dict(rendered),
                })
            except Exception as exc:
                summary["errors"] += 1
                failed = db.get_edit_job(job_id)
                results.append({
                    "job_id": job_id,
                    "status": "error",
                    "error": str(exc) or exc.__class__.__name__,
                    "job": _edit_job_dict(failed) if failed is not None else None,
                })
        return {"status": "ok", "summary": summary, "results": results}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/worker/run-once")
def editing_worker_run_once(
    req: EditWorkerRunOnceRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        rows = run_edit_queue_once(limit=req.limit if req else 1)
        return {
            "status": "ok",
            "jobs": [_edit_job_dict(row) for row in rows],
            "processed": len(rows),
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/render")
def editing_job_render(
    job_id: int,
    req: EditJobRenderRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        rendered = render_edit_job(job_id, force=bool(req.force) if req else False)
        return {"status": "ok", "job": _edit_job_dict(rendered)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/cancel")
def editing_job_cancel(job_id: int) -> dict[str, Any]:
    try:
        _init()
        job = db.get_edit_job(job_id)
        if job is None:
            raise FileNotFoundError("Edit job не найден.")
        if str(job["status"]) == "rendering":
            raise ValueError("Нельзя отменить rendering job в этом этапе.")
        if not db.cancel_edit_job(job_id):
            raise ValueError("Отменить можно только queued или failed edit job.")
        row = next(
            row for row in db.list_edit_jobs_with_details(limit=10000)
            if int(row["id"]) == job_id
        )
        return {"item": _edit_job_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/jobs/{job_id}/retry")
def editing_job_retry(job_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_edit_job(job_id) is None:
            raise FileNotFoundError("Edit job не найден.")
        if not db.retry_edit_job(job_id):
            raise ValueError("Повторить можно только failed или cancelled edit job.")
        row = next(
            row for row in db.list_edit_jobs_with_details(limit=10000)
            if int(row["id"]) == job_id
        )
        return {"item": _edit_job_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
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


@router.get("/workspace/clips")
def workspace_clips(status: str | None = None, limit: int = 1000) -> dict[str, Any]:
    try:
        _init()
        all_items = db.list_workspace_items(limit=limit)
        if status == "missing":
            items = [item for item in all_items if item["missing"]]
        elif status and status != "all":
            items = [item for item in all_items if item["workspace_status"] == status]
        else:
            items = all_items
        return _workspace_items_response(items, counts_items=all_items)
    except Exception as exc:
        raise _fail(exc)


@router.get("/workspace/clips/{item_key}")
def workspace_clip_detail(item_key: str) -> dict[str, Any]:
    try:
        _init()
        item_type, item_id = _parse_workspace_key(item_key)
        item = db.get_workspace_item(item_type, item_id)
        if item is None:
            raise FileNotFoundError("Элемент рабочего пространства не найден.")
        return {"item": _workspace_item_with_catalog_tags(item)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/workspace/clips/{item_key}")
def workspace_clip_delete(item_key: str) -> dict[str, Any]:
    try:
        _init()
        result = _delete_workspace_item(item_key)
        items = db.list_workspace_items(limit=1000)
        return {"status": "ok", "result": result, **_workspace_items_response(items)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/workspace/clips/{item_key}")
def workspace_clip_update(item_key: str, req: WorkspaceItemUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        item_type, item_id = _parse_workspace_key(item_key)
        ok = db.update_workspace_item(
            item_type,
            item_id,
            workspace_status=req.workspace_status,
            title=req.title,
            description=req.description,
            tags=req.tags,
            target_aspect=req.target_aspect,
        )
        if not ok:
            raise FileNotFoundError("Элемент рабочего пространства не найден.")
        item = db.get_workspace_item(item_type, item_id)
        return {"item": _workspace_item_with_catalog_tags(item)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/{item_key}/prepare")
def workspace_clip_prepare(item_key: str, req: WorkspacePrepareRequest) -> dict[str, Any]:
    try:
        _init()
        item_type, item_id = _parse_workspace_key(item_key)
        path = prepare_workspace_video(item_type, item_id, req.target_aspect)
        item = db.get_workspace_item(item_type, item_id)
        return {
            "status": "ok",
            "item": _workspace_item_with_catalog_tags(item) if item else None,
            "prepared_path": str(path),
            "prepare_status": item["prepare_status"] if item else "done",
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/bulk-prepare")
def workspace_clips_bulk_prepare(req: WorkspaceBulkPrepareRequest) -> dict[str, Any]:
    try:
        _init()
        summary = {"prepared": 0, "skipped": 0, "errors": 0}
        items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, str]] = []
        for item_key in req.item_keys:
            try:
                item_type, item_id = _parse_workspace_key(item_key)
                item = db.get_workspace_item(item_type, item_id)
                if item is None:
                    summary["skipped"] += 1
                    skipped_items.append(_workspace_youtube_skip(item_key, "Элемент не найден"))
                    continue
                if item.get("missing") or not item.get("file_exists"):
                    summary["skipped"] += 1
                    skipped_items.append(_workspace_youtube_skip(item_key, "Файл отсутствует"))
                    continue
                prepare_workspace_video(item_type, item_id, req.target_aspect)
                updated = db.get_workspace_item(item_type, item_id)
                if updated:
                    items.append(_workspace_item_with_catalog_tags(updated))
                summary["prepared"] += 1
            except Exception as exc:
                summary["errors"] += 1
                skipped_items.append(_workspace_youtube_skip(item_key, str(exc) or exc.__class__.__name__))
        workspace_items = db.list_workspace_items(limit=1000)
        return {
            "status": "ok",
            **summary,
            "items": items,
            "skipped_items": skipped_items,
            "workspace": _workspace_items_response(workspace_items),
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/bulk-delete")
def workspace_clips_bulk_delete(req: WorkspaceBulkDeleteRequest) -> dict[str, Any]:
    try:
        _init()
        summary = {
            "deleted_files": 0,
            "already_missing": 0,
            "hidden": 0,
            "errors": 0,
        }
        results: list[dict[str, Any]] = []
        for item_key in req.items:
            try:
                result = _delete_workspace_item(item_key)
                if result["file_deleted"]:
                    summary["deleted_files"] += 1
                if result["already_missing"]:
                    summary["already_missing"] += 1
                if result["hidden"]:
                    summary["hidden"] += 1
                results.append(result)
            except Exception as exc:
                summary["errors"] += 1
                results.append({
                    "id": item_key,
                    "file_deleted": False,
                    "already_missing": False,
                    "hidden": False,
                    "error": str(exc) or exc.__class__.__name__,
                })
        items = db.list_workspace_items(limit=1000)
        return {"status": "ok", "summary": summary, "results": results, **_workspace_items_response(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/cleanup-missing")
def workspace_clips_cleanup_missing() -> dict[str, Any]:
    try:
        _init()
        before_items = db.list_workspace_items(limit=10000)
        missing_count = sum(1 for item in before_items if item["missing"])
        hidden = db.cleanup_missing_workspace_items()
        items = db.list_workspace_items(limit=1000)
        return {
            "status": "ok",
            "summary": {
                "missing": missing_count,
                "hidden": hidden,
                "errors": 0,
            },
            **_workspace_items_response(items),
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/scan-missing")
def workspace_clips_scan_missing() -> dict[str, Any]:
    try:
        _init()
        items = db.list_workspace_items(limit=10000)
        missing = [item for item in items if item["missing"]]
        return {
            "status": "ok",
            "missing": len(missing),
            "items": _workspace_items_with_catalog_tags(missing),
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/bulk-status")
def workspace_clips_bulk_status(req: WorkspaceBulkStatusRequest) -> dict[str, Any]:
    try:
        _init()
        parsed = [_parse_workspace_key(item) for item in req.items]
        updated = db.bulk_update_workspace_status(parsed, req.workspace_status)
        items = db.list_workspace_items(limit=1000)
        return {"status": "ok", "updated": updated, **_workspace_items_response(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/workspace/clips/youtube/enqueue")
def workspace_clips_youtube_enqueue(req: WorkspaceYouTubeEnqueueRequest) -> dict[str, Any]:
    try:
        _init()
        if not req.item_keys:
            raise ValueError("Выберите элементы рабочего пространства.")
        account = db.get_social_account(req.account_id)
        if account is None or _row(account, "platform") != "youtube":
            raise FileNotFoundError("YouTube аккаунт не найден.")
        if _row(account, "status", "active") != "active":
            raise ValueError("YouTube аккаунт не активен.")

        items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, str]] = []
        jobs: list[dict[str, Any]] = []
        created = 0
        updated = 0
        prepared = 0
        errors = 0
        seen: set[str] = set()

        for item_key in req.item_keys:
            if item_key in seen:
                continue
            seen.add(item_key)
            try:
                item_type, item_id = _parse_workspace_key(item_key)
                item = db.get_workspace_item(item_type, item_id)
                if item is None:
                    skipped_items.append(_workspace_youtube_skip(item_key, "Элемент не найден"))
                    continue
                if item.get("missing") or not item.get("file_exists"):
                    skipped_items.append(_workspace_youtube_skip(item_key, "Файл отсутствует"))
                    continue
                workspace_status = str(item.get("workspace_status") or "draft")
                publish_status = str(item.get("publish_job_status") or "")
                can_refresh_existing_job = publish_status in {"queued", "failed", "cancelled"}
                if workspace_status != "ready" and not can_refresh_existing_job:
                    skipped_items.append(_workspace_youtube_skip(item_key, "В очередь добавляются только элементы со статусом Готово"))
                    continue

                if _workspace_target_needs_prepare(item):
                    prepare_workspace_video(item_type, item_id, str(item.get("target_aspect") or "original"))
                    prepared += 1
                    item = db.get_workspace_item(item_type, item_id) or item

                publish_path = _workspace_prepared_publish_path(item)
                clip_id = db.get_or_create_publish_clip_for_workspace_item(
                    item_type,
                    item_id,
                    output_path=publish_path,
                    target_aspect=str(item.get("target_aspect") or "original"),
                )

                existing_job = db.get_publish_job_for_clip(account_id=req.account_id, clip_id=clip_id)
                job_id = _workspace_create_publish_job(item=item, clip_id=clip_id, req=req)
                job = db.get_publish_job(job_id)
                if job is None:
                    raise FileNotFoundError(f"Publish job {job_id} не найден")
                validate_publish_job(job)
                job_payload = _publish_job_dict(job)
                workspace_status = "uploaded" if job_payload["status"] == "done" else "queued"
                if job_payload["status"] == "done":
                    skipped_items.append(_workspace_youtube_skip(item_key, "Уже загружено"))
                elif existing_job is None:
                    created += 1
                else:
                    updated += 1
                db.update_workspace_item(item_type, item_id, workspace_status=workspace_status)
                items.append({
                    "item_key": item["id"],
                    "status": workspace_status,
                    "job_id": job_id,
                    "clip_id": clip_id,
                })
                jobs.append(job_payload)
            except Exception as exc:
                errors += 1
                skipped_items.append(_workspace_youtube_skip(item_key, str(exc) or exc.__class__.__name__))

        workspace_items = db.list_workspace_items(limit=1000)
        return {
            "status": "ok",
            "prepared": prepared,
            "created": created,
            "updated": updated,
            "skipped": len(skipped_items),
            "errors": errors,
            "jobs": jobs,
            "items": items,
            "skipped_items": skipped_items,
            "workspace": _workspace_items_response(workspace_items),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


def _local_storage_candidate_dict(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    return {
        "workspace_path": relative,
        "section": relative.split("/", 1)[0],
        "file_name": path.name,
        "title": path.stem,
        "size": int(stat.st_size),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }


def _list_local_storage_candidate_videos(limit: int = 1000) -> list[dict[str, Any]]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    items: list[dict[str, Any]] = []
    for folder_name in ("edits", "ready", "published"):
        folder = root / folder_name
        if not folder.exists() or folder.is_symlink() or not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            try:
                if path.is_symlink():
                    continue
                relative = path.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                    items.append(_local_storage_candidate_dict(path, root))
            except (OSError, ValueError):
                continue
    items.sort(key=lambda item: str(item.get("modified_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 1000), 5000))]


def _storage_profile_auto_import_sections(row: Any) -> set[str]:
    try:
        parsed = json.loads(str(_row(row, "auto_import_sections", "[]") or "[]"))
    except json.JSONDecodeError:
        parsed = ["edits", "ready", "published"]
    sections = {
        str(item).strip().lower()
        for item in parsed
        if str(item).strip().lower() in LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS
    }
    return sections or set(LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS)


def _storage_profile_prefix_matches(workspace_path: str, prefix: str) -> bool:
    clean_prefix = str(prefix or "").strip().strip("/")
    if not clean_prefix:
        return True
    return workspace_path == clean_prefix or workspace_path.startswith(f"{clean_prefix}/")


def _run_local_storage_profile_auto_import(
    profile_id: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    profile = db.get_local_storage_profile(profile_id)
    if profile is None or not bool(_row(profile, "enabled", 1)):
        raise FileNotFoundError("Локальный профиль не найден.")
    enabled = bool(_row(profile, "auto_import_enabled", 0))
    if not enabled and not force:
        return {
            "status": "ok",
            "disabled": True,
            "summary": {"scanned": 0, "added": 0, "existing": 0, "skipped": 0, "errors": 0},
            "items": [
                _local_storage_profile_item_dict(item)
                for item in db.list_local_storage_profile_items(profile_id)
            ],
            "profile": _local_storage_profile_dict(profile, include_links=True),
        }

    sections = _storage_profile_auto_import_sections(profile)
    prefix = _row(profile, "auto_import_prefix", "") or ""
    candidates = _list_local_storage_candidate_videos(limit=5000)
    existing_paths = {
        str(item["workspace_path"])
        for item in db.list_local_storage_profile_items(profile_id)
    }
    summary = {"scanned": 0, "added": 0, "existing": 0, "skipped": 0, "errors": 0}
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for candidate in candidates:
        workspace_path = str(candidate["workspace_path"])
        section = str(candidate.get("section") or workspace_path.split("/", 1)[0])
        if section not in sections or not _storage_profile_prefix_matches(workspace_path, prefix):
            continue
        summary["scanned"] += 1
        if workspace_path in existing_paths:
            summary["existing"] += 1
            continue
        try:
            item_id = db.add_local_storage_profile_item(
                profile_id,
                workspace_path=workspace_path,
                title=str(candidate.get("title") or candidate.get("file_name") or ""),
                status="ready",
            )
            row = db.get_local_storage_profile_item(item_id)
            if row is not None:
                added.append(_local_storage_profile_item_dict(row))
            existing_paths.add(workspace_path)
            summary["added"] += 1
        except Exception as exc:
            summary["errors"] += 1
            skipped.append({"workspace_path": workspace_path, "reason": str(exc) or exc.__class__.__name__})

    db.update_local_storage_profile(
        profile_id,
        auto_import_last_scan_at=db.now_utc(),
    )
    profile = db.get_local_storage_profile(profile_id)
    return {
        "status": "ok",
        "disabled": False,
        "summary": summary,
        "added": added,
        "skipped_items": skipped,
        "items": [
            _local_storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ],
        "profile": _local_storage_profile_dict(profile, include_links=True) if profile else None,
    }


@router.get("/tags")
def tags_list(
    enabled: bool | None = True,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    try:
        _init()
        db.ensure_system_tags()
        rows = db.list_tags(
            enabled=enabled,
            kind=kind,
            q=q,
            limit=max(1, min(int(limit or 500), 2000)),
        )
        return {"items": [_tag_dict(row) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/tags")
def tag_create(req: TagCreateRequest) -> dict[str, Any]:
    try:
        _init()
        tag_id = db.create_tag(
            name=req.name,
            slug=req.slug,
            kind=req.kind,
            color=req.color,
            description=req.description,
        )
        row = db.get_tag(tag_id)
        assert row is not None
        return {"tag": _tag_dict(row)}
    except sqlite3.IntegrityError:
        raise _fail(ValueError("Тег с таким slug уже существует."))
    except Exception as exc:
        raise _fail(exc)


@router.patch("/tags/{tag_id}")
def tag_update(tag_id: int, req: TagUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(req, ("name", "slug", "color", "description", "enabled"))
        if not db.update_tag(tag_id, **updates):
            raise FileNotFoundError("Тег не найден.")
        row = db.get_tag(tag_id)
        assert row is not None
        return {"tag": _tag_dict(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except sqlite3.IntegrityError:
        raise _fail(ValueError("Тег с таким slug уже существует."))
    except Exception as exc:
        raise _fail(exc)


@router.delete("/tags/{tag_id}")
def tag_disable(tag_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.disable_tag(tag_id):
            raise FileNotFoundError("Тег не найден.")
        return {"status": "ok", "tag_id": int(tag_id)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.get("/catalog/videos/search")
def catalog_videos_search(
    q: str = "",
    tags: str | None = None,
    limit: int = 60,
    scope: str = "ready",
) -> dict[str, Any]:
    try:
        _init()
        items = _list_catalog_videos(q=q, tags=tags, limit=limit, scope=scope)
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.get("/catalog/videos/random")
def catalog_videos_random(
    tags: str | None = None,
    limit: int = 24,
    scope: str = "ready",
) -> dict[str, Any]:
    try:
        _init()
        items = _list_catalog_videos(tags=tags, limit=limit, randomize=True, scope=scope)
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.get("/catalog/videos/tags")
def catalog_video_tags(workspace_path: str) -> dict[str, Any]:
    try:
        _init()
        relative, _ = _validate_catalog_workspace_video(workspace_path)
        item = _workspace_item_for_catalog_path(relative)
        return {
            "workspace_path": relative,
            "tags": _catalog_tags_for_video(relative, item),
            "item": item,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.post("/catalog/videos/tags")
def catalog_video_tags_update(req: CatalogVideoTagsRequest) -> dict[str, Any]:
    try:
        _init()
        relative, _ = _validate_catalog_workspace_video(req.workspace_path)
        item = _workspace_item_for_catalog_path(relative)
        mode = str(req.mode or "replace").strip().lower()
        if mode != "replace":
            raise ValueError("Поддерживается только mode=replace.")
        db.replace_workspace_tags(
            workspace_path=relative,
            tag_ids=req.tag_ids,
            item_type=str(item.get("item_type")) if item else None,
            item_id=int(item.get("item_id")) if item else None,
        )
        item = _workspace_item_for_catalog_path(relative)
        return {
            "workspace_path": relative,
            "tags": _catalog_tags_for_video(relative, item),
            "item": item,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


def _storage_profile_tag_rules_payload(profile_id: int) -> dict[str, Any]:
    profile = db.get_local_storage_profile(profile_id)
    if profile is None:
        raise FileNotFoundError("Локальный профиль не найден.")
    db.reconcile_local_storage_profile_channel_tags(profile_id)
    profile = db.get_local_storage_profile(profile_id)
    assert profile is not None
    rules = [_tag_rule_dict(row) for row in db.list_local_storage_profile_tag_rules(profile_id)]
    return {
        "profile": _local_storage_profile_dict(profile, include_links=True),
        "tag_match_mode": _row(profile, "tag_match_mode", "any") or "any",
        "rules": rules,
        "include_tag_ids": [rule["tag_id"] for rule in rules if rule["mode"] == "include"],
        "exclude_tag_ids": [rule["tag_id"] for rule in rules if rule["mode"] == "exclude"],
    }


@router.get("/storage-profiles/{profile_id}/tag-rules")
def local_storage_profile_tag_rules(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        return _storage_profile_tag_rules_payload(profile_id)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/storage-profiles/{profile_id}/tag-rules")
def local_storage_profile_tag_rules_update(
    profile_id: int,
    req: LocalStorageProfileTagRulesRequest,
) -> dict[str, Any]:
    try:
        _init()
        db.replace_local_storage_profile_tag_rules(
            profile_id,
            include_tag_ids=req.include_tag_ids,
            exclude_tag_ids=req.exclude_tag_ids,
            tag_match_mode=req.tag_match_mode,
        )
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        return _storage_profile_tag_rules_payload(profile_id)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


def _catalog_video_matches_profile_rules(
    item: dict[str, Any],
    *,
    include_tag_ids: set[int],
    exclude_tag_ids: set[int],
    tag_match_mode: str,
) -> bool:
    item_tag_ids = {int(tag["id"]) for tag in item.get("tags") or []}
    if exclude_tag_ids & item_tag_ids:
        return False
    if not include_tag_ids:
        return False
    if tag_match_mode == "all":
        return include_tag_ids <= item_tag_ids
    return bool(include_tag_ids & item_tag_ids)


@router.post("/storage-profiles/{profile_id}/tag-sync/run")
def local_storage_profile_tag_sync_run(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        rules_payload = _storage_profile_tag_rules_payload(profile_id)
        include_tag_ids = set(int(tag_id) for tag_id in rules_payload["include_tag_ids"])
        exclude_tag_ids = set(int(tag_id) for tag_id in rules_payload["exclude_tag_ids"])
        tag_match_mode = str(rules_payload["tag_match_mode"] or "any")
        candidates = _list_catalog_videos(limit=5000)
        existing_paths = {
            str(item["workspace_path"])
            for item in db.list_local_storage_profile_items(profile_id)
        }
        summary = {"scanned": 0, "matched": 0, "added": 0, "existing": 0, "skipped": 0, "errors": 0}
        added = []
        skipped = []
        for candidate in candidates:
            summary["scanned"] += 1
            if not _catalog_video_matches_profile_rules(
                candidate,
                include_tag_ids=include_tag_ids,
                exclude_tag_ids=exclude_tag_ids,
                tag_match_mode=tag_match_mode,
            ):
                continue
            summary["matched"] += 1
            workspace_path = str(candidate["workspace_path"])
            if workspace_path in existing_paths:
                summary["existing"] += 1
                continue
            try:
                item_id = db.add_local_storage_profile_item(
                    profile_id,
                    workspace_path=workspace_path,
                    title=str(candidate.get("title") or candidate.get("file_name") or ""),
                    status="ready" if candidate.get("is_publish_ready") else "draft",
                )
                row = db.get_local_storage_profile_item(item_id)
                if row is not None:
                    added.append(_local_storage_profile_item_dict(row))
                existing_paths.add(workspace_path)
                summary["added"] += 1
            except Exception as exc:
                summary["errors"] += 1
                summary["skipped"] += 1
                skipped.append({"workspace_path": workspace_path, "reason": str(exc) or exc.__class__.__name__})
        profile = db.get_local_storage_profile(profile_id)
        return {
            "status": "ok",
            "summary": summary,
            "added": added,
            "skipped_items": skipped,
            "profile": _local_storage_profile_dict(profile, include_links=True) if profile else None,
            "items": [
                _local_storage_profile_item_dict(item)
                for item in db.list_local_storage_profile_items(profile_id)
            ],
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles/ready-videos")
def local_storage_profile_ready_videos(limit: int = 1000) -> dict[str, Any]:
    try:
        _init()
        items = _list_local_storage_candidate_videos(limit=limit)
        counts = dict(Counter(item["section"] for item in items))
        counts["all"] = len(items)
        return {"items": items, "counts": counts}
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles/{profile_id}/auto-import/run")
def local_storage_profile_auto_import_run(
    profile_id: int,
    req: LocalStorageProfileAutoImportRunRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        return _run_local_storage_profile_auto_import(
            profile_id,
            force=bool(req.force) if req is not None else False,
        )
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles")
def local_storage_profiles(enabled: bool | None = True) -> dict[str, Any]:
    try:
        _init()
        rows = db.list_local_storage_profiles(enabled=enabled)
        for row in rows:
            try:
                db.reconcile_local_storage_profile_channel_tags(int(row["id"]))
            except Exception:
                pass
        rows = db.list_local_storage_profiles(enabled=enabled)
        return {"items": [_local_storage_profile_dict(row, include_links=True) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles")
def local_storage_profile_create(req: LocalStorageProfileCreateRequest) -> dict[str, Any]:
    try:
        _init()
        profile_id = db.create_local_storage_profile(
            name=req.name,
            handle=req.handle,
            description=req.description,
            avatar_initials=req.avatar_initials,
            avatar_color=req.avatar_color,
            banner_color=req.banner_color,
            auto_import_enabled=req.auto_import_enabled,
            auto_import_sections=req.auto_import_sections,
            auto_import_prefix=req.auto_import_prefix,
            tag_match_mode=req.tag_match_mode,
        )
        row = db.get_local_storage_profile(profile_id)
        assert row is not None
        return {"profile": _local_storage_profile_dict(row, include_links=True)}
    except sqlite3.IntegrityError as exc:
        raise _fail(ValueError("Профиль с таким handle уже существует."))
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles/{profile_id}")
def local_storage_profile_detail(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        row = db.get_local_storage_profile(profile_id)
        if row is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        row = db.get_local_storage_profile(profile_id)
        assert row is not None
        items = [
            _local_storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {
            "profile": _local_storage_profile_dict(row, include_links=True),
            "items": items,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/storage-profiles/{profile_id}")
def local_storage_profile_update(
    profile_id: int,
    req: LocalStorageProfileUpdateRequest,
) -> dict[str, Any]:
    try:
        _init()
        updates = _request_updates(
            req,
            (
                "name",
                "handle",
                "description",
                "avatar_initials",
                "avatar_color",
                "banner_color",
                "auto_import_enabled",
                "auto_import_sections",
                "auto_import_prefix",
                "tag_match_mode",
                "enabled",
            ),
        )
        if not db.update_local_storage_profile(profile_id, **updates):
            raise FileNotFoundError("Локальный профиль не найден.")
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        row = db.get_local_storage_profile(profile_id)
        assert row is not None
        return {"profile": _local_storage_profile_dict(row, include_links=True)}
    except sqlite3.IntegrityError:
        raise _fail(ValueError("Профиль с таким handle уже существует."))
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/storage-profiles/{profile_id}")
def local_storage_profile_disable(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.disable_local_storage_profile(profile_id):
            raise FileNotFoundError("Локальный профиль не найден.")
        return {"status": "ok", "profile_id": int(profile_id)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


def _active_youtube_account(account_id: int) -> Any:
    account = db.get_social_account(account_id)
    if account is None or _row(account, "platform") != "youtube":
        raise FileNotFoundError("YouTube аккаунт не найден.")
    if _row(account, "status", "active") != "active":
        raise ValueError("YouTube аккаунт не активен.")
    return account


def _linked_storage_profile_youtube_account_id(
    profile_id: int,
    requested_account_id: int | None = None,
) -> int:
    profile = db.get_local_storage_profile(profile_id)
    if profile is None or not bool(_row(profile, "enabled", 1)):
        raise FileNotFoundError("Локальный профиль не найден.")
    link = db.get_local_storage_profile_service_link(profile_id, "youtube")
    if (
        link is None
        or _row(link, "status", "not_connected") != "linked"
        or _row(link, "external_account_id") is None
    ):
        raise ValueError("Сначала привяжите YouTube-канал к профилю.")
    linked_account_id = int(_row(link, "external_account_id"))
    if requested_account_id is not None and int(requested_account_id) != linked_account_id:
        raise ValueError("Профиль привязан к другому YouTube-каналу.")
    _active_youtube_account(linked_account_id)
    return linked_account_id


def _storage_profile_publish_title(item: Any) -> str:
    title = _normalize_setting_text(_row(item, "title"))
    if title:
        return title
    workspace_path = _row(item, "workspace_path", "") or ""
    stem = Path(workspace_path).stem
    return stem or f"ShortsFarm profile video {int(item['id'])}"


def _profile_item_has_status_ready_tag(item: Any) -> bool:
    workspace_path = _row(item, "workspace_path", "") or ""
    if not workspace_path:
        return False
    try:
        tags = db.list_workspace_tag_links(workspace_path=workspace_path)
    except Exception:
        tags = []
    return any(_row(tag, "slug") == "status-ready" for tag in tags)


def _create_publish_job_from_storage_profile_item(
    *,
    profile_id: int,
    item: Any,
    account_id: int,
    req: LocalStorageProfileYouTubePublishRequest,
) -> tuple[int, int, str | None]:
    workspace_path, abs_path = _validate_local_storage_workspace_video(_row(item, "workspace_path"))
    if not _profile_item_has_status_ready_tag(item):
        raise ValueError("Публикация доступна только для видео с тегом «Готово».")
    title = _storage_profile_publish_title(item)
    clip_id = db.get_or_create_publish_clip_for_file(
        abs_path,
        title=title,
    )
    existing_job = db.get_publish_job_for_clip(account_id=account_id, clip_id=clip_id)
    validated = validate_publish_options(
        title=title,
        publish_mode=req.publish_mode,
        publish_at=None,
        category_id=req.category_id,
    )
    tags = parse_tags(_row(item, "tags") or "")
    job_id = db.create_publish_job(
        account_id=account_id,
        clip_id=clip_id,
        title=title,
        description=_row(item, "description", "") or "",
        tags=json.dumps(tags, ensure_ascii=False),
        category_id=req.category_id,
        privacy_status=str(validated["privacy_status"]),
        publish_mode=req.publish_mode,
        publish_at=validated["publish_at"],
        made_for_kids=req.made_for_kids,
        platform="youtube",
    )
    db.link_local_storage_profile_publish_job(
        profile_id,
        int(item["id"]),
        job_id,
        platform="youtube",
    )
    return clip_id, job_id, _row(existing_job, "status") if existing_job is not None else None


@router.post("/storage-profiles/{profile_id}/youtube/link")
def local_storage_profile_youtube_link(
    profile_id: int,
    req: LocalStorageProfileYouTubeLinkRequest,
) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        account = _active_youtube_account(req.account_id)
        display_name = (
            _row(account, "channel_title")
            or _row(account, "display_name")
            or f"YouTube аккаунт #{int(req.account_id)}"
        )
        db.upsert_local_storage_profile_service_link(
            profile_id,
            platform="youtube",
            external_account_id=int(req.account_id),
            display_name=display_name,
            status="linked",
        )
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {"profile": _local_storage_profile_dict(profile, include_links=True)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/storage-profiles/{profile_id}/youtube/link")
def local_storage_profile_youtube_unlink(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        db.remove_local_storage_profile_service_link(profile_id, "youtube")
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {"profile": _local_storage_profile_dict(profile, include_links=True)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles/{profile_id}/publish-jobs")
def local_storage_profile_publish_jobs(profile_id: int, limit: int = 100) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        rows = db.list_local_storage_profile_publish_jobs(
            profile_id,
            platform="youtube",
            limit=max(1, min(int(limit or 100), 500)),
        )
        return {"jobs": [_publish_job_dict(row) for row in rows]}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles/{profile_id}/youtube/videos")
def local_storage_profile_youtube_videos(profile_id: int, limit: int = 200) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        rows = db.list_local_storage_profile_external_videos(
            profile_id,
            platform="youtube",
            limit=max(1, min(int(limit or 200), 500)),
        )
        return {"videos": [_local_storage_external_video_dict(row) for row in rows]}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/enqueue")
def local_storage_profile_youtube_enqueue(
    profile_id: int,
    req: LocalStorageProfileYouTubePublishRequest,
) -> dict[str, Any]:
    try:
        _init()
        if not req.item_ids:
            raise ValueError("Выберите видео профиля для публикации.")
        account_id = _linked_storage_profile_youtube_account_id(profile_id, req.account_id)

        seen: set[int] = set()
        jobs: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        summary = {
            "created": 0,
            "updated": 0,
            "already_done": 0,
            "skipped": 0,
            "errors": 0,
        }

        for raw_item_id in req.item_ids:
            item_id = int(raw_item_id)
            if item_id in seen:
                continue
            seen.add(item_id)
            try:
                item = db.get_local_storage_profile_item(item_id)
                if item is None or int(item["profile_id"]) != int(profile_id):
                    skipped_items.append({"item_id": item_id, "reason": "Видео в профиле не найдено"})
                    summary["skipped"] += 1
                    continue
                clip_id, job_id, previous_status = _create_publish_job_from_storage_profile_item(
                    profile_id=profile_id,
                    item=item,
                    account_id=account_id,
                    req=req,
                )
                job = db.get_publish_job(job_id)
                if job is None:
                    raise FileNotFoundError(f"Publish job {job_id} не найден")
                validate_publish_job(job)
                job_payload = _publish_job_dict(job)
                if previous_status is None:
                    summary["created"] += 1
                elif previous_status == "done":
                    summary["already_done"] += 1
                    skipped_items.append({"item_id": item_id, "reason": "Уже загружено"})
                else:
                    summary["updated"] += 1
                items.append({
                    "item_id": item_id,
                    "clip_id": clip_id,
                    "job_id": job_id,
                    "status": job_payload["status"],
                })
                jobs.append(job_payload)
            except Exception as exc:
                summary["errors"] += 1
                summary["skipped"] += 1
                skipped_items.append({
                    "item_id": item_id,
                    "reason": str(exc) or exc.__class__.__name__,
                })

        profile = db.get_local_storage_profile(profile_id)
        detail_items = [
            _local_storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {
            "status": "ok",
            "summary": summary,
            "jobs": jobs,
            "items": items,
            "skipped_items": skipped_items,
            "profile": _local_storage_profile_dict(profile, include_links=True) if profile else None,
            "profile_items": detail_items,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/sync")
def local_storage_profile_youtube_sync(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        account_id = _linked_storage_profile_youtube_account_id(profile_id)
        account = _active_youtube_account(account_id)
        synced_at = db.now_utc()
        display_name = (
            _row(account, "channel_title")
            or _row(account, "display_name")
            or f"YouTube аккаунт #{account_id}"
        )
        inventory = fetch_youtube_channel_videos(account, max_results=500)
        summary = {
            "fetched": 0,
            "matched_jobs": 0,
            "matched_profile_items": 0,
            "external_only": 0,
            "published": 0,
            "metadata_updated": 0,
        }
        for video in inventory.get("videos", []):
            youtube_video_id = str(video.get("video_id") or "").strip()
            if not youtube_video_id:
                continue
            summary["fetched"] += 1
            youtube_url = str(video.get("url") or f"https://www.youtube.com/watch?v={youtube_video_id}")
            job = db.get_publish_job_by_youtube_video_id(
                account_id=account_id,
                youtube_video_id=youtube_video_id,
            )
            publish_job_id = int(job["id"]) if job is not None else None
            profile_item_id = None
            if job is not None:
                summary["matched_jobs"] += 1
                db.update_publish_job_from_youtube_sync(
                    int(job["id"]),
                    youtube_video_id=youtube_video_id,
                    youtube_url=youtube_url,
                    title=video.get("title") or None,
                    description=video.get("description") or None,
                    tags=video.get("tags") or None,
                    category_id=video.get("category_id") or None,
                    privacy_status=video.get("privacy_status") or None,
                    publish_at=video.get("publish_at") or None,
                )
                summary["metadata_updated"] += 1
                link = db.get_local_storage_profile_publish_link_for_job(
                    profile_id,
                    int(job["id"]),
                    platform="youtube",
                )
                if link is not None and _row(link, "profile_item_id") is not None:
                    profile_item_id = int(_row(link, "profile_item_id"))
                    db.update_local_storage_profile_item_status(profile_item_id, "published")
                    summary["matched_profile_items"] += 1
                    summary["published"] += 1
            else:
                summary["external_only"] += 1
            db.upsert_local_storage_profile_external_video(
                profile_id,
                platform="youtube",
                external_video_id=youtube_video_id,
                external_url=youtube_url,
                title=video.get("title") or "",
                description=video.get("description") or "",
                tags=video.get("tags") or [],
                category_id=video.get("category_id") or "",
                privacy_status=video.get("privacy_status") or "",
                publish_at=video.get("publish_at"),
                published_at=video.get("published_at"),
                duration=video.get("duration") or "",
                thumbnail_url=video.get("thumbnail_url") or "",
                profile_item_id=profile_item_id,
                publish_job_id=publish_job_id,
                raw_json=video.get("raw") or {},
            )

        db.upsert_local_storage_profile_service_link(
            profile_id,
            platform="youtube",
            external_account_id=account_id,
            display_name=display_name,
            status="linked",
        )
        db.update_local_storage_profile_service_link_sync(
            profile_id,
            "youtube",
            last_sync_at=synced_at,
            last_sync_error=None,
            synced_video_count=summary["fetched"],
        )
        db.reconcile_local_storage_profile_channel_tags(profile_id)

        profile = db.get_local_storage_profile(profile_id)
        jobs = db.list_local_storage_profile_publish_jobs(profile_id, platform="youtube", limit=200)
        external_rows = db.list_local_storage_profile_external_videos(profile_id, platform="youtube", limit=200)
        return {
            "status": "ok",
            "summary": summary,
            "channel": inventory.get("channel") or {},
            "profile": _local_storage_profile_dict(profile, include_links=True) if profile else None,
            "items": [
                _local_storage_profile_item_dict(item)
                for item in db.list_local_storage_profile_items(profile_id)
            ],
            "jobs": [_publish_job_dict(job) for job in jobs],
            "youtube_videos": [_local_storage_external_video_dict(row) for row in external_rows],
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        try:
            db.update_local_storage_profile_service_link_sync(
                profile_id,
                "youtube",
                last_sync_error=str(exc) or exc.__class__.__name__,
            )
        except Exception:
            pass
        raise _fail(exc)


@router.get("/storage-profiles/{profile_id}/items")
def local_storage_profile_items(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        items = [
            _local_storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {"items": items}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles/{profile_id}/items")
def local_storage_profile_item_add(
    profile_id: int,
    req: LocalStorageProfileItemCreateRequest,
) -> dict[str, Any]:
    try:
        _init()
        workspace_path, _ = _validate_local_storage_workspace_video(req.workspace_path)
        item_id = db.add_local_storage_profile_item(
            profile_id,
            workspace_path=workspace_path,
            title=req.title,
            description=req.description,
            tags=req.tags,
            status=req.status,
        )
        row = db.get_local_storage_profile_item(item_id)
        profile = db.get_local_storage_profile(profile_id)
        if row is None or profile is None:
            raise FileNotFoundError("Локальный профиль или видео не найдены.")
        return {
            "item": _local_storage_profile_item_dict(row),
            "profile": _local_storage_profile_dict(profile, include_links=True),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/storage-profiles/{profile_id}/items/{item_id}")
def local_storage_profile_item_remove(profile_id: int, item_id: int) -> dict[str, Any]:
    try:
        _init()
        if not db.remove_local_storage_profile_item(profile_id, item_id):
            raise FileNotFoundError("Видео в профиле не найдено.")
        profile = db.get_local_storage_profile(profile_id)
        return {
            "status": "ok",
            "profile": _local_storage_profile_dict(profile, include_links=True) if profile else None,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
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
