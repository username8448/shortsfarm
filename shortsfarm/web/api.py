from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

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
from ..edit_renderer import resolve_edit_job_media_path
from ..ffmpeg_tools import require_binary
from ..local_dialogs import (
    LocalDialogUnavailable,
    pick_directory_dialog,
    pick_file_dialog,
)
from ..mpv_session import require_mpv
from ..remotion_renderer import start_studio_render_queue
from ..studio_templates import ensure_default_studio_templates, template_row_payload
from ..publish_youtube import (
    fetch_youtube_channel_videos,
    fetch_youtube_channel_metadata_items,
    parse_tags,
    run_publish_job_now,
    run_publish_queue_once,
    save_youtube_channel_metadata,
    sync_youtube_account_metadata,
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
    get_workspace_root,
    resolve_workspace_path,
)
from .schemas import (
    ChannelProfileCreateRequest,
    ChannelProfileUpdateRequest,
    DatabaseResetRequest,
    EditJobRenderRequest,
    EditJobReviewRequest,
    EditJobsBulkRenderRequest,
    EditJobsPlanRequest,
    EditTemplateUpdateRequest,
    EditWorkerRunOnceRequest,
    LocalDialogPickRequest,
    LocalStorageProfileAutoImportRunRequest,
    LocalStorageProfileCreateRequest,
    LocalStorageProfileItemCreateRequest,
    LocalStorageProfilePublishSettingsRequest,
    LocalStorageProfileTagRulesRequest,
    LocalStorageProfileUpdateRequest,
    LocalStorageProfileYouTubeLinkRequest,
    LocalStorageProfileYouTubePublishRequest,
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
    VideoChildClipsDeleteRequest,
    VideoBulkDeleteRequest,
    VideoRelinkSourceRequest,
    WorkspaceBulkDeleteRequest,
    WorkspaceBulkPrepareRequest,
    WorkspaceBulkStatusRequest,
    WorkspaceItemUpdateRequest,
    WorkspacePrepareRequest,
    WorkspaceYouTubeEnqueueRequest,
    YouTubeMetadataUpdateRequest,
    YouTubeAccountUpdateRequest,
    YouTubeClientJsonImportRequest,
    YouTubeConnectStartRequest,
    YouTubeOAuthProfileCreateRequest,
    YouTubeOAuthProfileImportRequest,
    YouTubeOAuthProfileUpdateRequest,
    YouTubeSettingsRequest,
    YouTubeUploadRequest,
)
from .api_common import fail, init_api
from .tag_catalog import (
    catalog_tags_for_video as _catalog_tags_for_video,
    list_catalog_videos as _list_catalog_videos,
    tag_dict as _tag_dict,
)

router = APIRouter()
DATABASE_RESET_CONFIRMATION = "УДАЛИТЬ БАЗУ"

_init = init_api
_fail = fail


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
    source_state = _video_source_state(row)
    return {
        "id": video_id,
        "title": row["title"],
        "source_path": row["source_path"],
        "duration_sec": row["duration_sec"],
        "duration_text": _format_duration(row["duration_sec"]),
        "review_status": _status_value(_row(row, "review_status")),
        "mark_count": int(_row(row, "mark_count", db.count_marks(video_id))),
        "clip_count": int(_row(row, "clip_count", db.count_clips(video_id))),
        "segment_count": int(_row(row, "segment_count", db.count_segments(video_id))),
        "deleted_at": _row(row, "deleted_at"),
        "source_file_deleted_at": _row(row, "source_file_deleted_at"),
        "created_at": _row(row, "created_at"),
        **source_state,
        "output_dir": _latest_output_dir(video_id),
    }


def _video_source_state(row: Any) -> dict[str, Any]:
    deleted_at = _row(row, "deleted_at")
    source_file_deleted_at = _row(row, "source_file_deleted_at")
    source_path = str(_row(row, "source_path", "") or "")
    exists = False
    missing = True
    if source_path:
        try:
            candidate = Path(source_path).expanduser()
            exists = candidate.exists() and candidate.is_file()
            missing = not exists
        except OSError:
            exists = False
            missing = True
    if deleted_at is not None:
        state = "hidden_deleted"
        label = "Удалено из списка"
    elif source_file_deleted_at is not None:
        state = "source_deleted"
        label = "Исходник удалён"
    elif missing:
        state = "missing_or_moved"
        label = "Отсутствует/перемещён"
    else:
        state = "ok"
        label = "Файл на месте"
    return {
        "source_file_exists": exists,
        "source_state": state,
        "source_state_label": label,
        "source_missing": missing,
        "source_deleted": source_file_deleted_at is not None,
        "source_hidden": deleted_at is not None,
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


def _field_was_supplied(req: Any, field: str) -> bool:
    supplied = getattr(req, "model_fields_set", None)
    if supplied is None:
        supplied = getattr(req, "__fields_set__", set())
    return field in supplied


def _reject_legacy_field(req: Any, field: str) -> None:
    if _field_was_supplied(req, field) and getattr(req, field, None) is not None:
        raise ValueError("Legacy templates are no longer supported.")


def _base_url(request: Request | None) -> str:
    if request is None:
        return ""
    return str(request.base_url).rstrip("/")


LEGACY_EDIT_JOB_READONLY_MESSAGE = (
    "Legacy edit job доступен только для просмотра. "
    "Повторный render запрещён."
)


def _require_studio_edit_job(row: Any) -> None:
    if _row(row, "studio_project_id") is None or _row(row, "remotion_render_job_id") is None:
        raise ValueError(LEGACY_EDIT_JOB_READONLY_MESSAGE)


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
        "studio_template_id": _row(row, "studio_template_id"),
        "source": "legacy",
        "legacy": True,
        "readonly": True,
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def _studio_template_edit_dict(row: Any) -> dict[str, Any]:
    payload = template_row_payload(row)
    definition = payload.get("definition") or {}
    return {
        "id": int(row["id"]),
        "studio_template_id": int(row["id"]),
        "key": str(row["template_key"]),
        "name": str(row["name"]),
        "description": str(definition.get("description") or ""),
        "renderer": str(definition.get("default_renderer") or payload.get("default_renderer") or row["engine"]),
        "recipe_json": json.dumps(definition, ensure_ascii=False),
        "definition": definition,
        "parameters": definition.get("parameters") or {},
        "enabled": row["deleted_at"] is None and str(row["status"]) != "archived",
        "status": str(row["status"]),
        "version": int(row["version"]),
        "deleted_at": row["deleted_at"],
        "source": "studio",
        "legacy": False,
        "readonly": False,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
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
        "default_template_key": _row(row, "default_template_key", "") or "",
        "default_studio_template_id": _row(row, "default_studio_template_id"),
        "default_studio_template_name": _row(row, "default_studio_template_name", "") or "",
        "default_studio_template_key": _row(row, "default_studio_template_key", "") or "",
        "default_studio_template_version": _row(row, "default_studio_template_version"),
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
    remotion_status = _row(row, "remotion_status")
    status = remotion_status or _row(row, "status", "queued") or "queued"
    remotion_output = _row(row, "remotion_output_path")
    remotion_error = _row(row, "remotion_error")
    template_name = (
        _row(row, "studio_template_name")
        or _row(row, "template_name", "")
        or ""
    )
    template_key = (
        _row(row, "studio_template_key")
        or _row(row, "template_key", "")
        or ""
    )
    return {
        "id": int(row["id"]),
        "status": status,
        "edit_status": _row(row, "status", "queued") or "queued",
        "remotion_status": remotion_status,
        "remotion_render_job_id": _row(row, "remotion_render_job_id"),
        "studio_project_id": _row(row, "studio_project_id"),
        "studio_template_id": _row(row, "studio_template_id"),
        "remotion_progress_percent": _row(row, "remotion_progress_percent"),
        "remotion_progress_stage": _row(row, "remotion_progress_stage"),
        "remotion_progress_message": _row(row, "remotion_progress_message"),
        "workspace_item_key": _row(row, "workspace_item_key", "") or "",
        "channel_profile_id": _row(row, "channel_profile_id"),
        "channel_profile_name": _row(row, "channel_profile_name", "") or "",
        "template_id": _row(row, "template_id"),
        "template_name": template_name,
        "template_key": template_key,
        "reaction_asset_id": _row(row, "reaction_asset_id"),
        "reaction_asset_name": _row(row, "reaction_asset_name", "") or "",
        "input_path": _row(row, "input_path"),
        "output_path": remotion_output or _row(row, "output_path"),
        "edited_path": (
            remotion_output
            if status == "done"
            else _row(row, "edited_path")
        ),
        "renderer": _row(row, "renderer", "ffmpeg") or "ffmpeg",
        "recipe_json": _row(row, "recipe_json"),
        "error": remotion_error or _row(row, "error"),
        "review_status": _row(row, "review_status", "pending") or "pending",
        "reviewed_at": _row(row, "reviewed_at"),
        "review_note": _row(row, "review_note"),
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "remotion_started_at") or _row(row, "started_at"),
        "finished_at": _row(row, "remotion_finished_at") or _row(row, "finished_at"),
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


def _local_storage_service_link_dict(
    row: Any,
    *,
    social_accounts_by_id: dict[int, Any] | None = None,
) -> dict[str, Any]:
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
        account_id = int(external_account_id)
        account = (
            social_accounts_by_id.get(account_id)
            if social_accounts_by_id is not None
            else db.get_social_account(account_id)
        )
        if account is not None:
            data["youtube_account"] = _social_account_dict(account)
    return data


def _clean_youtube_profile_handle(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("@"):
        text = text[1:]
    return text.strip().strip("/")


def _first_youtube_service_link(service_links: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            link for link in service_links
            if link.get("platform") == "youtube" and link.get("status") == "linked"
        ),
        None,
    )


def _local_storage_effective_profile_fields(
    row: Any,
    service_links: list[dict[str, Any]],
) -> dict[str, Any]:
    youtube_link = _first_youtube_service_link(service_links)
    account = (youtube_link or {}).get("youtube_account") or {}
    sync_enabled = bool(_row(row, "youtube_branding_sync_enabled", 1))
    use_youtube = bool(sync_enabled and account)
    local_name = _row(row, "name", "") or ""
    local_handle = _row(row, "handle", "") or ""
    local_description = _row(row, "description", "") or ""
    local_avatar_url = _row(row, "avatar_url", "") or ""
    local_initials = _row(row, "avatar_initials", "") or ""
    official_name = account.get("channel_title") or account.get("official_channel_title") or ""
    official_handle = _clean_youtube_profile_handle(
        account.get("channel_handle") or account.get("channel_custom_url") or ""
    )
    official_description = account.get("channel_description") or ""
    official_avatar_url = account.get("channel_avatar_url") or ""
    official_banner_url = account.get("channel_banner_url") or ""
    local_banner_url = _row(row, "banner_url", "") or ""
    name_override = bool(_row(row, "name_override", 0))
    handle_override = bool(_row(row, "handle_override", 0))
    description_override = bool(_row(row, "description_override", 0))
    avatar_override = bool(_row(row, "avatar_override", 0))
    banner_override = bool(_row(row, "banner_override", 0))
    effective_name = (
        official_name
        if use_youtube and not name_override and official_name
        else local_name
    )
    effective_handle = (
        official_handle
        if use_youtube and not handle_override and official_handle
        else local_handle
    )
    effective_description = (
        official_description
        if use_youtube and not description_override and official_description
        else local_description
    )
    effective_avatar_url = (
        official_avatar_url
        if use_youtube and not avatar_override and official_avatar_url
        else local_avatar_url
    )
    effective_banner_url = (
        official_banner_url
        if use_youtube and not banner_override and official_banner_url
        else local_banner_url
    )
    fallback_initials = (effective_name or local_name or "SF").strip()[:2].upper()
    if avatar_override or not use_youtube:
        avatar_initials = local_initials or fallback_initials
    else:
        avatar_initials = fallback_initials
    return {
        "effective_name": effective_name or local_name,
        "effective_handle": effective_handle or local_handle,
        "effective_description": effective_description or local_description,
        "effective_avatar_url": effective_avatar_url or "",
        "effective_avatar_initials": avatar_initials,
        "effective_avatar_color": _row(row, "avatar_color", "#3b82f6") or "#3b82f6",
        "effective_banner_url": effective_banner_url or "",
        "effective_banner_color": _row(row, "banner_color", "#111827") or "#111827",
        "youtube_branding": {
            "sync_enabled": sync_enabled,
            "source": "youtube" if use_youtube else "local",
            "synced_at": _row(row, "youtube_branding_synced_at"),
            "attempted_at": _row(row, "youtube_branding_attempted_at"),
            "sync_error": _row(row, "youtube_branding_sync_error"),
            "overrides": {
                "name": name_override,
                "handle": handle_override,
                "description": description_override,
                "avatar": avatar_override,
                "banner": banner_override,
            },
        },
    }


def _local_storage_profile_dict(
    row: Any,
    *,
    include_links: bool = False,
    service_links_by_profile: dict[int, list[dict[str, Any]]] | None = None,
    social_accounts_by_id: dict[int, Any] | None = None,
) -> dict[str, Any]:
    profile_id = int(row["id"])
    try:
        auto_sections = json.loads(str(_row(row, "auto_import_sections", "[]") or "[]"))
    except json.JSONDecodeError:
        auto_sections = ["edits", "ready", "published"]
    if not isinstance(auto_sections, list):
        auto_sections = ["edits", "ready", "published"]
    if include_links and service_links_by_profile is not None:
        service_links = service_links_by_profile.get(profile_id, [])
    elif include_links:
        service_links = [
            _local_storage_service_link_dict(link, social_accounts_by_id=social_accounts_by_id)
            for link in db.list_local_storage_profile_service_links(profile_id)
        ]
    else:
        service_links = []
    data = {
        "id": profile_id,
        "name": _row(row, "name", "") or "",
        "handle": _row(row, "handle", "") or "",
        "description": _row(row, "description", "") or "",
        "avatar_initials": _row(row, "avatar_initials", "") or "",
        "avatar_color": _row(row, "avatar_color", "#3b82f6") or "#3b82f6",
        "avatar_url": _row(row, "avatar_url", "") or "",
        "banner_color": _row(row, "banner_color", "#111827") or "#111827",
        "banner_url": _row(row, "banner_url", "") or "",
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
    data.update(_local_storage_effective_profile_fields(row, service_links))
    if include_links:
        data["service_links"] = service_links
        data["tag_rules"] = [
            _tag_rule_dict(rule)
            for rule in db.list_local_storage_profile_tag_rules(profile_id)
        ]
    return data


def _local_storage_profile_service_link_context(
    profile_ids: list[int] | tuple[int, ...] | set[int],
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, Any]]:
    link_rows = db.list_local_storage_profile_service_links_for_profiles(profile_ids)
    account_ids = {
        int(_row(link, "external_account_id"))
        for link in link_rows
        if _row(link, "platform") == "youtube" and _row(link, "external_account_id") is not None
    }
    accounts_by_id = {
        int(row["id"]): row
        for row in db.list_social_accounts_by_ids(account_ids)
    }
    links_by_profile: dict[int, list[dict[str, Any]]] = {}
    for link in link_rows:
        profile_id = int(_row(link, "profile_id"))
        links_by_profile.setdefault(profile_id, []).append(
            _local_storage_service_link_dict(
                link,
                social_accounts_by_id=accounts_by_id,
            )
        )
    return links_by_profile, accounts_by_id


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
    root = get_workspace_root()
    if root is not None:
        allowed_roots.extend(
            (root / folder_name).resolve()
            for folder_name in SYSTEM_FOLDERS
            if folder_name != "sources"
        )
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise PermissionError("Удалять можно только файлы внутри output или рабочих папок результатов ShortsFarm.")

    source_path = _normalize_setting_text(item.get("source_path"))
    if source_path:
        try:
            if resolved == Path(source_path).expanduser().resolve():
                raise PermissionError("Нельзя удалить исходное видео из workspace.")
        except FileNotFoundError:
            pass
    return resolved


def _workspace_relative_path_from_file_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    root = get_workspace_root()
    if root is None:
        return None
    try:
        resolved_root = root.resolve()
        resolved_path = Path(path).expanduser().resolve()
        return resolved_path.relative_to(resolved_root).as_posix()
    except (OSError, ValueError):
        return None


def _workspace_paths_from_items(items: list[dict[str, Any]]) -> list[str]:
    paths: set[str] = set()
    for item in items:
        for key in ("path", "prepared_path"):
            relative = _workspace_relative_path_from_file_path(item.get(key))
            if relative:
                paths.add(relative)
    return sorted(paths)


def _profile_cleanup_summary(workspace_paths: list[str], *, remove_from_profiles: bool) -> dict[str, Any]:
    normalized_paths = sorted({str(path).strip() for path in workspace_paths if str(path).strip()})
    if not remove_from_profiles:
        return {
            "requested": False,
            "requested_paths": len(normalized_paths),
            "matched_items": 0,
            "removed": 0,
            "affected_profiles": 0,
            "paths": normalized_paths,
        }
    result = db.remove_local_storage_profile_items_by_workspace_paths(normalized_paths)
    return {
        "requested": True,
        **result,
    }


def _profile_workspace_paths_for_video(video_id: int) -> list[str]:
    paths: set[str] = set()
    video = db.get_video(video_id)
    if video is not None:
        source_relative = _workspace_relative_path_from_file_path(_row(video, "source_path", ""))
        if source_relative:
            paths.add(source_relative)
    items = [
        item
        for item in db.list_workspace_items(limit=10000, include_hidden=True)
        if int(item.get("video_id") or 0) == int(video_id)
    ]
    paths.update(_workspace_paths_from_items(items))
    for _ in range(3):
        before = len(paths)
        for row in db.list_shorts_pipeline_run_items_matching_workspace_paths(sorted(paths)):
            for key in ("source_workspace_path", "segment_workspace_path", "output_workspace_path"):
                value = str(_row(row, key, "") or "").strip()
                if value:
                    paths.add(value)
        if len(paths) == before:
            break
    return sorted(paths)


def _delete_workspace_item(item_key: str, *, remove_from_profiles: bool = False) -> dict[str, Any]:
    item_type, item_id = _parse_workspace_key(item_key)
    item = db.get_workspace_item(item_type, item_id)
    if item is None:
        raise FileNotFoundError("Элемент рабочего пространства не найден.")
    profile_paths = _workspace_paths_from_items([item])

    result = {
        "id": item["id"],
        "item_type": item_type,
        "item_id": item_id,
        "file_deleted": False,
        "already_missing": False,
        "hidden": False,
        "message": "",
        "profile_items": _profile_cleanup_summary(profile_paths, remove_from_profiles=False),
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
    result["profile_items"] = _profile_cleanup_summary(
        profile_paths,
        remove_from_profiles=remove_from_profiles,
    )
    return result


def _delete_workspace_items(item_keys: list[str], *, remove_from_profiles: bool = False) -> dict[str, Any]:
    summary = {
        "requested": len(item_keys),
        "deleted_files": 0,
        "already_missing": 0,
        "hidden": 0,
        "errors": 0,
        "profile_items_removed": 0,
        "profile_items_matched": 0,
        "profile_paths": 0,
    }
    results: list[dict[str, Any]] = []
    for item_key in item_keys:
        try:
            result = _delete_workspace_item(item_key, remove_from_profiles=remove_from_profiles)
            if result["file_deleted"]:
                summary["deleted_files"] += 1
            if result["already_missing"]:
                summary["already_missing"] += 1
            if result["hidden"]:
                summary["hidden"] += 1
            profile_items = result.get("profile_items") or {}
            summary["profile_items_removed"] += int(profile_items.get("removed") or 0)
            summary["profile_items_matched"] += int(profile_items.get("matched_items") or 0)
            summary["profile_paths"] += int(profile_items.get("requested_paths") or 0)
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
    return {"summary": summary, "results": results}


def _delete_workspace_items_for_video(video_id: int, *, remove_from_profiles: bool = False) -> dict[str, Any]:
    item_keys = db.list_workspace_item_keys_for_video(video_id)
    profile_paths = _profile_workspace_paths_for_video(video_id)
    result = _delete_workspace_items(item_keys, remove_from_profiles=False)
    profile_cleanup = _profile_cleanup_summary(profile_paths, remove_from_profiles=remove_from_profiles)
    result["summary"] = dict(result["summary"])
    result["video_id"] = int(video_id)
    result["summary"]["video_id"] = int(video_id)
    result["summary"]["found"] = result["summary"].pop("requested", len(item_keys))
    result["summary"]["profile_items_removed"] = int(profile_cleanup.get("removed") or 0)
    result["summary"]["profile_items_matched"] = int(profile_cleanup.get("matched_items") or 0)
    result["summary"]["profile_paths"] = int(profile_cleanup.get("requested_paths") or 0)
    result["profile_items"] = profile_cleanup
    return result


def _social_account_dict(row: Any, *, include_profile_links: bool = False) -> dict[str, Any]:
    oauth_profile_id = _row(row, "oauth_profile_id")
    def _json_field(key: str) -> Any:
        value = _row(row, key)
        if not value:
            return {} if key.endswith("_json") else None
        try:
            return json.loads(str(value))
        except Exception:
            return {}

    local_alias = _row(row, "display_name", "") or ""
    official_title = _row(row, "channel_title", "") or ""
    data = {
        "id": int(row["id"]),
        "platform": row["platform"],
        "oauth_profile_id": oauth_profile_id,
        "profile_name": _row(row, "profile_name", "") or "",
        "oauth_profile": (
            {
                "id": int(oauth_profile_id),
                "name": _row(row, "profile_name", "") or f"OAuth Profile #{int(oauth_profile_id)}",
            }
            if oauth_profile_id is not None
            else None
        ),
        "display_name": local_alias,
        "local_alias": local_alias,
        "account_email": _row(row, "account_email", "") or "",
        "channel_id": _row(row, "channel_id", "") or "",
        "channel_title": official_title,
        "official_channel_title": official_title,
        "channel_description": _row(row, "channel_description", "") or "",
        "channel_custom_url": _row(row, "channel_custom_url", "") or "",
        "channel_handle": _row(row, "channel_handle", "") or "",
        "channel_country": _row(row, "channel_country", "") or "",
        "channel_published_at": _row(row, "channel_published_at"),
        "channel_avatar_url": _row(row, "channel_avatar_url", "") or "",
        "channel_thumbnails": _json_field("channel_thumbnails_json"),
        "channel_banner_url": _row(row, "channel_banner_url", "") or "",
        "channel_branding": _json_field("channel_branding_json"),
        "subscriber_count": _row(row, "subscriber_count"),
        "view_count": _row(row, "view_count"),
        "video_count": _row(row, "video_count"),
        "hidden_subscriber_count": bool(_row(row, "hidden_subscriber_count", 0) or 0),
        "uploads_playlist_id": _row(row, "uploads_playlist_id", "") or "",
        "channel_status": _json_field("channel_status_json"),
        "metadata_synced_at": _row(row, "metadata_synced_at"),
        "metadata_sync_error": _row(row, "metadata_sync_error"),
        "scopes": _row(row, "scopes", "") or "",
        "status": _row(row, "status", "active") or "active",
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
        "last_connected_at": _row(row, "last_connected_at"),
        "error": _row(row, "error"),
    }
    if include_profile_links:
        data["linked_storage_profiles"] = [
            _local_storage_profile_dict(profile)
            for profile in db.list_local_storage_profiles_for_service_account(
                platform=_row(row, "platform", "youtube") or "youtube",
                external_account_id=int(row["id"]),
            )
        ]
    return data


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
        "privacy_status": _row(row, "privacy_status", "public"),
        "publish_mode": _row(row, "publish_mode", "public"),
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


def _sqlite_sidecar_paths(path: Path) -> list[Path]:
    return [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ]


def _checkpoint_database(path: Path) -> None:
    if not path.exists():
        return
    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        if con is not None:
            con.close()


def _reset_database_file(*, create_backup: bool = True) -> dict[str, Any]:
    ensure_dirs()
    path = db_path()
    backup_path: Path | None = None
    if path.exists():
        _checkpoint_database(path)
        if create_backup:
            backup_dir = data_dir() / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"db-reset-{stamp}.sqlite"
            shutil.copy2(path, backup_path)
    removed_files: list[str] = []
    for candidate in _sqlite_sidecar_paths(path):
        if candidate.exists():
            candidate.unlink()
            removed_files.append(str(candidate))
    db.init_db()
    return {
        "status": "ok",
        "reset": True,
        "backup_path": str(backup_path) if backup_path else None,
        "removed_files": removed_files,
    }


@router.post("/settings/database/reset")
def settings_database_reset(req: DatabaseResetRequest) -> dict[str, Any]:
    try:
        if (req.confirmation or "").strip() != DATABASE_RESET_CONFIRMATION:
            raise ValueError(f"Для сброса базы введите: {DATABASE_RESET_CONFIRMATION}")
        return _reset_database_file(create_backup=bool(req.create_backup))
    except ValueError as exc:
        raise _fail(exc, status_code=400)
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
        return {"accounts": [_social_account_dict(row, include_profile_links=True) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.patch("/publish/youtube/accounts/{account_id}")
def youtube_account_update(account_id: int, req: YouTubeAccountUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        account = db.get_social_account(account_id)
        if account is None or str(account["platform"] or "") != "youtube":
            raise FileNotFoundError("YouTube аккаунт не найден.")
        alias = req.local_alias if req.local_alias is not None else req.display_name
        if alias is None:
            raise ValueError("Укажите локальное название аккаунта.")
        if len(str(alias).strip()) > 160:
            raise ValueError("Локальное название слишком длинное.")
        db.update_social_account_alias(account_id, str(alias).strip() or None)
        updated = db.get_social_account(account_id)
        assert updated is not None
        return {"account": _social_account_dict(updated, include_profile_links=True)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/accounts/sync-metadata")
def youtube_accounts_sync_metadata() -> dict[str, Any]:
    try:
        _init()
        summary = {"ok": 0, "failed": 0, "skipped": 0}
        items: list[dict[str, Any]] = []
        for account in db.list_social_accounts(platform="youtube"):
            account_id = int(account["id"])
            if str(account["status"] or "") != "active":
                summary["skipped"] += 1
                items.append({
                    "account_id": account_id,
                    "status": "skipped",
                    "reason": "Аккаунт не активен.",
                })
                continue
            try:
                updated = sync_youtube_account_metadata(account)
                branding_profiles = _touch_linked_storage_profiles_youtube_branding(
                    account_id,
                    account=updated,
                    error=None,
                )
                summary["ok"] += 1
                items.append({
                    "account_id": account_id,
                    "status": "ok",
                    "account": _social_account_dict(updated, include_profile_links=True),
                    "branding_profiles": branding_profiles,
                })
            except Exception as sync_exc:
                message = str(sync_exc) or sync_exc.__class__.__name__
                db.set_social_account_metadata_sync_error(account_id, message)
                branding_profiles = _touch_linked_storage_profiles_youtube_branding(
                    account_id,
                    account=account,
                    error=message,
                )
                updated = db.get_social_account(account_id)
                summary["failed"] += 1
                items.append({
                    "account_id": account_id,
                    "status": "failed",
                    "error": message,
                    "branding_profiles": branding_profiles,
                    "account": _social_account_dict(updated, include_profile_links=True) if updated else None,
                })
        return {"status": "ok", "summary": summary, "items": items}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/accounts/{account_id}/sync-metadata")
def youtube_account_sync_metadata(account_id: int) -> dict[str, Any]:
    try:
        _init()
        account = db.get_social_account(account_id)
        if account is None or str(account["platform"] or "") != "youtube":
            raise FileNotFoundError("YouTube аккаунт не найден.")
        try:
            updated = sync_youtube_account_metadata(account)
            branding_profiles = _touch_linked_storage_profiles_youtube_branding(
                account_id,
                account=updated,
                error=None,
            )
            return {
                "status": "ok",
                "account": _social_account_dict(updated, include_profile_links=True),
                "branding_profiles": branding_profiles,
            }
        except Exception as sync_exc:
            message = str(sync_exc) or sync_exc.__class__.__name__
            db.set_social_account_metadata_sync_error(account_id, message)
            branding_profiles = _touch_linked_storage_profiles_youtube_branding(
                account_id,
                account=account,
                error=message,
            )
            updated = db.get_social_account(account_id)
            return {
                "status": "failed",
                "error": message,
                "branding_profiles": branding_profiles,
                "account": _social_account_dict(updated, include_profile_links=True) if updated else None,
            }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
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
        items = fetch_youtube_channel_metadata_items(youtube, mine=True)
        if not items:
            message = "YouTube канал для этого аккаунта не найден."
            return _oauth_page("Ошибка YouTube OAuth", message, ok=False)

        expires_at = credentials.expiry.isoformat() if credentials.expiry else None
        scopes = " ".join(credentials.scopes or YOUTUBE_SCOPES)
        saved_ids: list[int] = []

        for channel in items:
            snippet = channel.get("snippet") or {}
            channel_id = str(channel.get("id") or "").strip()
            if not channel_id:
                continue
            channel_title = str(snippet.get("title") or "").strip() or "YouTube канал"
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
                preserve_display_name=True,
            )
            try:
                save_youtube_channel_metadata(account_id, channel)
            except Exception as sync_exc:
                db.set_social_account_metadata_sync_error(
                    account_id,
                    str(sync_exc) or sync_exc.__class__.__name__,
                )
            saved_ids.append(account_id)

        if not saved_ids:
            message = "YouTube канал для этого аккаунта не найден."
            return _oauth_page("Ошибка YouTube OAuth", message, ok=False)
        channel_word = "канал" if len(saved_ids) == 1 else "каналов"
        return _oauth_page(
            "YouTube аккаунт подключён",
            f"Импортировано YouTube {channel_word}: {len(saved_ids)}. Можно закрыть эту вкладку и вернуться в ShortsFarm → Интеграции.",
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
def editing_templates(
    enabled: bool | None = None,
    include_deleted: bool = False,
    status: str | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    try:
        _init()
        ensure_default_studio_templates()
        studio_rows = db.list_studio_templates(
            include_deleted=include_deleted,
            status=status if status and status != "all" else None,
        )
        studio_items = [_studio_template_edit_dict(row) for row in studio_rows]
        if enabled is True:
            studio_items = [item for item in studio_items if item["enabled"]]
        elif enabled is False:
            studio_items = [item for item in studio_items if not item["enabled"]]
        items = studio_items
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/templates/ensure-defaults")
def editing_templates_ensure_defaults() -> dict[str, Any]:
    try:
        _init()
        items = ensure_default_studio_templates()
        item = db.get_latest_studio_template_by_key("reaction_top_25", include_deleted=False)
        return {"item": _studio_template_edit_dict(item or items[0])}
    except Exception as exc:
        raise _fail(exc)


@router.patch("/editing/templates/{template_id}")
def editing_template_update(
    template_id: int,
    req: EditTemplateUpdateRequest,
) -> dict[str, Any]:
    raise _fail(ValueError("Legacy templates are no longer supported."), status_code=400)


@router.get("/editing/channel-profiles")
def editing_channel_profiles(enabled: bool | None = None) -> dict[str, Any]:
    try:
        _init()
        ensure_default_studio_templates()
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
        _reject_legacy_field(req, "default_template_id")
        name = str(req.name or "").strip()
        if not name:
            raise ValueError("Название профиля канала обязательно.")
        profile_id = db.create_channel_profile(
            name=name,
            youtube_account_id=req.youtube_account_id,
            default_template_id=None,
            default_studio_template_id=req.default_studio_template_id,
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
        _reject_legacy_field(req, "default_template_id")
        updates = _request_updates(
            req,
            (
                "name", "youtube_account_id",
                "default_studio_template_id", "reaction_pool_id",
                "title_template", "description_template",
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
        _reject_legacy_field(req, "template_id")
        if not req.item_keys:
            raise ValueError("Выберите хотя бы один workspace item.")
        return plan_edit_jobs_for_workspace_items(
            req.item_keys,
            req.channel_profile_id,
            reaction_asset_id=req.reaction_asset_id,
            template_id=None,
            studio_template_id=req.studio_template_id,
            parameter_values=req.parameter_values,
            renderer_engine=req.renderer_engine,
            render_profile=req.render_profile,
            duration_limit_sec=req.duration_limit_sec,
            start_offset_sec=req.start_offset_sec,
            full_length=req.full_length,
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
def editing_jobs_bulk_render(
    req: EditJobsBulkRenderRequest,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
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
                _require_studio_edit_job(current)
                render_job_id = int(current["remotion_render_job_id"])
                render_row = db.get_remotion_render_job(render_job_id)
                if render_row is None:
                    raise FileNotFoundError(f"Remotion render job {render_job_id} не найден.")
                render_status = str(render_row["status"])
                if render_status in {"failed", "cancelled"} and req.force:
                    db.retry_remotion_render_job(render_job_id)
                    render_status = "queued"
                if render_status != "queued":
                    summary["skipped"] += 1
                    results.append({
                        "job_id": job_id,
                        "status": "skipped",
                        "reason": f"Remotion job со status={render_status} не запускается bulk-render.",
                    })
                    continue
                start_studio_render_queue(_base_url(request))
                summary["processed"] += 1
                db.sync_edit_job_from_remotion_render_job(render_job_id)
                updated = db.get_edit_job(job_id)
                results.append({
                    "job_id": job_id,
                    "status": "queued",
                    "job": _edit_job_dict(updated),
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


@router.post("/editing/worker/start")
def editing_worker_start(
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    try:
        _init()
        with db.connect() as con:
            queued_studio = int(con.execute(
                """
                SELECT COUNT(*) AS count
                FROM remotion_render_jobs
                WHERE status='queued'
                """
            ).fetchone()["count"])
            legacy_skipped = int(con.execute(
                """
                SELECT COUNT(*) AS count
                FROM edit_jobs
                WHERE status='queued'
                  AND (studio_project_id IS NULL OR remotion_render_job_id IS NULL)
                """
            ).fetchone()["count"])
        queue = start_studio_render_queue(_base_url(request))
        return {
            "status": "ok",
            "queue": queue,
            "queued_studio": queued_studio,
            "legacy_skipped": legacy_skipped,
        }
    except Exception as exc:
        raise _fail(exc)


@router.post("/editing/worker/run-once")
def editing_worker_run_once(
    req: EditWorkerRunOnceRequest | None = None,
) -> dict[str, Any]:
    raise _fail(
        ValueError("Endpoint переименован в /api/editing/worker/start."),
        status_code=410,
    )


@router.post("/editing/jobs/{job_id}/render")
def editing_job_render(
    job_id: int,
    request: Request = None,  # type: ignore[assignment]
    req: EditJobRenderRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        if isinstance(request, EditJobRenderRequest):
            req = request
            request = None
        current = db.get_edit_job(job_id)
        if current is None:
            raise FileNotFoundError("Edit job не найден.")
        _require_studio_edit_job(current)
        render_job_id = int(current["remotion_render_job_id"])
        render_row = db.get_remotion_render_job(render_job_id)
        if render_row is None:
            raise FileNotFoundError("Remotion render job не найден.")
        render_status = str(render_row["status"])
        force = bool(req.force) if req else False
        if render_status == "done" and not force:
            return {"status": "ok", "job": _edit_job_dict(current)}
        if render_status in {"failed", "cancelled"} and force:
            db.retry_remotion_render_job(render_job_id)
            render_status = "queued"
        elif render_status in {"failed", "cancelled"}:
            raise ValueError(
                f"Remotion job со status={render_status} можно повторить только с force=true."
            )
        elif render_status == "done" and force:
            raise ValueError("Готовый Remotion render нельзя перезаписать этим действием; создайте задачу заново.")
        if render_status != "queued":
            raise ValueError(f"Remotion job со status={render_status} уже не queued.")
        started = start_studio_render_queue(_base_url(request))
        db.sync_edit_job_from_remotion_render_job(render_job_id)
        updated = db.get_edit_job(job_id)
        return {
            "status": "queued",
            "queue": started,
            "job": _edit_job_dict(updated or current),
        }
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
        _require_studio_edit_job(job)
        if not db.cancel_remotion_render_job(int(job["remotion_render_job_id"])):
            raise ValueError("Отменить можно только queued Remotion job.")
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
        job = db.get_edit_job(job_id)
        if job is None:
            raise FileNotFoundError("Edit job не найден.")
        _require_studio_edit_job(job)
        if not db.retry_remotion_render_job(int(job["remotion_render_job_id"])):
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
        studio_runtime_mode = db.get_setting(
            "studio_templates_runtime_mode",
            "studio_only",
        )
        studio_migration_warning = (
            {
                "mode": studio_runtime_mode,
                "message": (
                    "Template Studio работает в режиме Studio-only, "
                    "но проверка миграции нашла проблемы. "
                    "Откройте migration report 047_studio_only_verification."
                ),
            }
            if studio_runtime_mode == "studio_only_with_migration_errors"
            else None
        )
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
            "studio_templates_runtime_mode": studio_runtime_mode,
            "studio_migration_warning": studio_migration_warning,
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


def _queue_progress_for_status(status: str) -> int:
    status = str(status or "")
    if status in {"done", "reviewed", "uploaded", "published"}:
        return 100
    if status in {"failed", "cancelled", "skipped"}:
        return 100
    if status in {"running", "rendering", "uploading"}:
        return 50
    return 0


def _queue_source_item(video: dict[str, Any], linked_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    latest_job = linked_jobs[0] if linked_jobs else None
    return {
        "kind": "source",
        "kind_label": "Источник",
        "id": f"source:{video['id']}",
        "video_id": video["id"],
        "title": video["title"],
        "status": video["review_status"],
        "status_label": video["review_status"],
        "progress": latest_job["progress"] if latest_job else _queue_progress_for_status(video["review_status"]),
        "source_path": video["source_path"],
        "output_dir": video["output_dir"],
        "source_state": video["source_state"],
        "source_state_label": video["source_state_label"],
        "source_file_exists": video["source_file_exists"],
        "source_missing": video["source_missing"],
        "source_deleted": video["source_deleted"],
        "source_hidden": video["source_hidden"],
        "counts": {
            "marks": video["mark_count"],
            "clips": video["clip_count"],
            "segments": video["segment_count"],
            "jobs": len(linked_jobs),
        },
        "duration_sec": video["duration_sec"],
        "duration_text": video["duration_text"],
        "created_at": video.get("created_at"),
        "updated_at": video.get("deleted_at") or video.get("created_at"),
        "jobs": linked_jobs[:8],
        "actions": {
            "watch": bool(video["source_file_exists"]),
            "show_clips": True,
            "open_output": bool(video["output_dir"]),
            "delete": True,
            "delete_child_clips": True,
            "relink_source": bool(video["source_missing"] or video["source_deleted"]),
            "restore": bool(video["source_hidden"]),
        },
    }


def _queue_split_item(job: dict[str, Any], source: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "kind": "split",
        "kind_label": "Split",
        "id": f"split:{job['id']}",
        "job_id": job["id"],
        "video_id": job.get("video_id"),
        "title": job.get("current_file") or (source or {}).get("title") or f"Split #{job['id']}",
        "status": job.get("status") or "",
        "status_label": job.get("status") or "",
        "progress": job.get("progress", 0),
        "source_path": job.get("source_path") or (source or {}).get("source_path", ""),
        "output_dir": job.get("output_dir") or "",
        "source_state": (source or {}).get("source_state", "ok"),
        "source_state_label": (source or {}).get("source_state_label", ""),
        "source_file_exists": (source or {}).get("source_file_exists", bool(job.get("source_path"))),
        "source_missing": (source or {}).get("source_missing", False),
        "source_deleted": (source or {}).get("source_deleted", False),
        "source_hidden": (source or {}).get("source_hidden", False),
        "counts": {
            "done_items": job.get("done_items") or 0,
            "total_items": job.get("total_items") or 0,
        },
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "actions": {
            "watch": bool(job.get("source_path")),
            "show_clips": bool(job.get("video_id")),
            "open_output": bool(job.get("output_dir")),
        },
    }


def _queue_render_item(row: Any, workspace_items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    job = _edit_job_dict(row)
    item_key = str(_row(row, "workspace_item_key", "") or "")
    workspace_item = workspace_items.get(item_key) or {}
    video_id = workspace_item.get("video_id")
    title = (
        job.get("template_name")
        or job.get("template_key")
        or f"Studio render #{int(row['id'])}"
    )
    output_path = job.get("output_path") or ""
    edited_path = job.get("edited_path") or ""
    progress = job.get("remotion_progress_percent")
    if progress is None:
        progress = _queue_progress_for_status(job.get("status") or "")
    return {
        "kind": "render",
        "kind_label": "Studio render",
        "id": f"render:{int(row['id'])}",
        "job_id": int(row["id"]),
        "video_id": video_id,
        "title": title,
        "status": job.get("status") or "",
        "status_label": job.get("status") or "",
        "progress": int(progress or 0),
        "workspace_item_key": item_key,
        "channel_profile_id": job.get("channel_profile_id"),
        "channel_profile_name": job.get("channel_profile_name") or "",
        "youtube_account_id": _row(row, "channel_profile_youtube_account_id"),
        "studio_template_id": job.get("studio_template_id"),
        "studio_project_id": job.get("studio_project_id"),
        "remotion_render_job_id": job.get("remotion_render_job_id"),
        "template_name": job.get("template_name") or "",
        "template_key": job.get("template_key") or "",
        "reaction_asset_id": job.get("reaction_asset_id"),
        "reaction_asset_name": job.get("reaction_asset_name") or "",
        "renderer": job.get("renderer") or "",
        "review_status": job.get("review_status") or "pending",
        "review_note": job.get("review_note") or "",
        "reviewed_at": job.get("reviewed_at"),
        "output_path": output_path,
        "edited_path": edited_path,
        "source_path": workspace_item.get("source_path", ""),
        "output_dir": str(Path(str(output_path)).parent) if output_path else "",
        "source_state": "ok",
        "source_state_label": "",
        "source_file_exists": bool(workspace_item.get("file_exists", False)),
        "source_missing": bool(workspace_item.get("missing", False)),
        "source_deleted": bool(workspace_item.get("source_deleted", False)),
        "source_hidden": False,
        "counts": {},
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "actions": {
            "show_clips": video_id is not None,
            "open_output": bool(output_path),
            "watch": bool(edited_path or output_path),
            "render": job.get("status") == "queued",
            "retry": job.get("status") in {"failed", "cancelled"},
            "cancel": job.get("status") in {"queued", "failed"},
            "review": job.get("status") == "done",
        },
    }


def _queue_publish_item(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "publish",
        "kind_label": "Publish",
        "id": f"publish:{job['id']}",
        "job_id": job["id"],
        "video_id": job.get("clip_video_id"),
        "title": job.get("title") or job.get("video_title") or f"Publish #{job['id']}",
        "status": job.get("status") or "",
        "status_label": job.get("status") or "",
        "progress": _queue_progress_for_status(job.get("status") or ""),
        "source_path": job.get("video_source_path") or "",
        "output_dir": str(Path(str(job.get("clip_output_path") or "")).parent) if job.get("clip_output_path") else "",
        "source_state": "ok",
        "source_state_label": "",
        "source_file_exists": bool(job.get("clip_output_path")),
        "source_missing": False,
        "source_deleted": False,
        "source_hidden": False,
        "counts": {"attempts": job.get("attempt_count") or 0},
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "actions": {
            "show_clips": job.get("clip_video_id") is not None,
            "open_output": bool(job.get("clip_output_path")),
        },
    }


def _queue_item_matches(item: dict[str, Any], *, q: str) -> bool:
    query = str(q or "").strip().lower()
    if not query:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in (
            "id", "kind_label", "title", "status", "source_path", "output_dir",
            "source_state_label", "error", "workspace_item_key",
            "channel_profile_name", "template_name", "template_key",
            "reaction_asset_name", "review_status",
        )
    ).lower()
    return all(part in haystack for part in query.split())


@router.get("/queue/items")
def queue_items(
    kind: str | None = None,
    status: str | None = None,
    review_status: str | None = None,
    source_state: str | None = None,
    q: str = "",
    include_deleted: bool = False,
    limit: int = 300,
) -> dict[str, Any]:
    try:
        _init()
        normalized_kind = str(kind or "all").strip().lower()
        source_rows = [_video_dict(row) for row in db.list_videos_with_counts(include_deleted=include_deleted)]
        sources_by_id = {int(row["id"]): row for row in source_rows}
        jobs = [_job_dict(row) for row in db.list_jobs(limit=1000)]
        jobs_by_video: dict[int, list[dict[str, Any]]] = {}
        for job in jobs:
            video_id = job.get("video_id")
            if video_id is not None:
                jobs_by_video.setdefault(int(video_id), []).append(job)
        for rows in jobs_by_video.values():
            rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

        workspace_items = {
            str(item["id"]): item
            for item in db.list_workspace_items(limit=10000, include_hidden=True)
        }

        items: list[dict[str, Any]] = []
        if normalized_kind in {"all", "source", "sources"}:
            items.extend(
                _queue_source_item(video, jobs_by_video.get(int(video["id"]), []))
                for video in source_rows
            )
        if normalized_kind in {"all", "job", "jobs", "split"}:
            for job in jobs:
                source = sources_by_id.get(int(job["video_id"])) if job.get("video_id") is not None else None
                if source is None and not include_deleted:
                    continue
                if source and source.get("source_hidden") and not include_deleted:
                    continue
                items.append(_queue_split_item(job, source))
        if normalized_kind in {"all", "job", "jobs", "render"}:
            items.extend(
                _queue_render_item(row, workspace_items)
                for row in db.list_edit_jobs_with_details(
                    review_status=(
                        review_status
                        if review_status and review_status != "all"
                        else None
                    ),
                    limit=200,
                )
            )
        if normalized_kind in {"all", "job", "jobs", "publish"}:
            items.extend(
                _queue_publish_item(_publish_job_dict(row))
                for row in db.list_publish_jobs(platform=None, limit=200)
            )

        if status and status != "all":
            items = [item for item in items if str(item.get("status") or "") == status]
        if review_status and review_status != "all":
            items = [
                item for item in items
                if item.get("kind") == "render"
                and str(item.get("review_status") or "pending") == review_status
            ]
        if source_state and source_state != "all":
            items = [item for item in items if str(item.get("source_state") or "") == source_state]
        items = [item for item in items if _queue_item_matches(item, q=q)]
        items.sort(key=lambda item: str(item.get("created_at") or item.get("started_at") or ""), reverse=True)
        limited = items[: max(1, min(int(limit), 1000))]
        return {
            "items": limited,
            "counts": {
                "all": len(items),
                "source": sum(1 for item in items if item["kind"] == "source"),
                "jobs": sum(1 for item in items if item["kind"] != "source"),
                "split": sum(1 for item in items if item["kind"] == "split"),
                "prepare": sum(1 for item in items if item.get("status") in {"preparing", "prepared"}),
                "render": sum(1 for item in items if item["kind"] == "render"),
                "publish": sum(1 for item in items if item["kind"] == "publish"),
                "review": sum(
                    1 for item in items
                    if item["kind"] == "render"
                    and item.get("status") == "done"
                    and str(item.get("review_status") or "pending") == "pending"
                ),
                "errors": sum(1 for item in items if item.get("status") == "failed" or item.get("error")),
                "missing": sum(1 for item in items if item.get("source_state") == "missing_or_moved"),
                "deleted": sum(1 for item in items if item.get("source_state") == "hidden_deleted"),
            },
        }
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


def _delete_source_files(source_paths: list[str]) -> dict[str, Any]:
    summary = {
        "deleted": 0,
        "missing": 0,
        "skipped": 0,
        "errors": 0,
    }
    results: list[dict[str, Any]] = []
    for raw_path in sorted({str(path) for path in source_paths if path}):
        result = {
            "path": raw_path,
            "deleted": False,
            "missing": False,
            "skipped": False,
            "error": None,
        }
        try:
            path = Path(raw_path).expanduser()
            if not path.exists() and not path.is_symlink():
                result["missing"] = True
                summary["missing"] += 1
            elif path.is_dir():
                result["skipped"] = True
                result["error"] = "Это папка, а не файл."
                summary["skipped"] += 1
            elif not path.is_file() and not path.is_symlink():
                result["skipped"] = True
                result["error"] = "Удалять можно только обычные файлы или symlink-файлы."
                summary["skipped"] += 1
            else:
                path.unlink()
                result["deleted"] = True
                summary["deleted"] += 1
        except Exception as exc:
            result["error"] = str(exc) or exc.__class__.__name__
            summary["errors"] += 1
        results.append(result)
    return {"summary": summary, "results": results}


def _deleted_or_missing_source_video_ids(
    source_file_results: list[dict[str, Any]],
    source_path_by_id: dict[str, str],
) -> list[int]:
    completed_paths = {
        str(item.get("path") or "")
        for item in source_file_results
        if item.get("deleted") or item.get("missing")
    }
    return [
        int(video_id)
        for video_id, path in source_path_by_id.items()
        if path in completed_paths
    ]


def _delete_video_child_workspace_items(video_id: int, *, remove_from_profiles: bool = False) -> dict[str, Any]:
    return _delete_workspace_items_for_video(video_id, remove_from_profiles=remove_from_profiles)


def _validate_relink_source_path(value: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Путь к новому исходнику не задан.")
    path = Path(text).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    if path.is_symlink():
        raise PermissionError("Symlink запрещён.")
    if not path.is_file():
        raise ValueError("Новый исходник должен быть обычным файлом.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Новый исходник должен быть поддерживаемым video file.")
    return path.resolve()


def _video_payload_by_id(video_id: int, *, include_deleted: bool = True) -> dict[str, Any] | None:
    for row in db.list_videos_with_counts(include_deleted=include_deleted):
        if int(row["id"]) == int(video_id):
            return _video_dict(row)
    return None


@router.post("/videos/bulk-delete")
def videos_bulk_delete(req: VideoBulkDeleteRequest) -> dict[str, Any]:
    try:
        _init()
        video_ids = [int(value) for value in req.video_ids]
        if not video_ids:
            raise ValueError("Выберите хотя бы одно видео для удаления.")
        child_results: list[dict[str, Any]] = []
        child_summary = {
            "found": 0,
            "deleted_files": 0,
            "already_missing": 0,
            "hidden": 0,
            "errors": 0,
        }
        if req.delete_child_clips:
            for video_id in video_ids:
                child_result = _delete_video_child_workspace_items(video_id, remove_from_profiles=False)
                child_results.append(child_result)
                summary = child_result["summary"]
                for key in child_summary:
                    child_summary[key] += int(summary.get(key) or 0)
        profile_paths: set[str] = set()
        if req.remove_from_profiles:
            for video_id in video_ids:
                profile_paths.update(_profile_workspace_paths_for_video(video_id))
        result = db.soft_delete_videos(video_ids)
        profile_cleanup = _profile_cleanup_summary(
            sorted(profile_paths),
            remove_from_profiles=bool(req.remove_from_profiles),
        )
        source_files = (
            _delete_source_files(result.get("source_paths") or [])
            if req.delete_source_files
            else {"summary": {"deleted": 0, "missing": 0, "skipped": 0, "errors": 0}, "results": []}
        )
        if req.delete_source_files:
            marked_ids = _deleted_or_missing_source_video_ids(
                source_files["results"],
                result.get("source_path_by_id") or {},
            )
            if marked_ids:
                db.mark_video_source_files_deleted(marked_ids)
        rows = [_video_dict(row) for row in db.list_videos_with_counts()]
        return {
            "status": "ok",
            "summary": {
                "requested": result["requested"],
                "deleted": result["deleted"],
                "missing": len(result["missing_ids"]),
                "delete_source_files": bool(req.delete_source_files),
                "delete_child_clips": bool(req.delete_child_clips),
                "remove_from_profiles": bool(req.remove_from_profiles),
                "source_files": source_files["summary"],
                "child_clips": child_summary,
                "profile_items": profile_cleanup,
            },
            "result": result,
            "child_clip_results": child_results,
            "profile_item_result": profile_cleanup,
            "source_file_results": source_files["results"],
            "videos": rows,
            "counts": dict(Counter(row["review_status"] for row in rows)),
        }
    except ValueError as exc:
        raise _fail(exc, status_code=400)
    except Exception as exc:
        raise _fail(exc)


@router.post("/videos/{video_id}/restore")
def video_restore(video_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_video(video_id) is None:
            raise FileNotFoundError("Родительское видео не найдено.")
        db.restore_video(video_id)
        video = _video_payload_by_id(video_id, include_deleted=True)
        return {"status": "ok", "video": video}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/videos/{video_id}/relink-source")
def video_relink_source(video_id: int, req: VideoRelinkSourceRequest) -> dict[str, Any]:
    try:
        _init()
        if db.get_video(video_id) is None:
            raise FileNotFoundError("Родительское видео не найдено.")
        path = _validate_relink_source_path(req.source_path)
        db.relink_video_source(video_id, path)
        video = _video_payload_by_id(video_id, include_deleted=True)
        return {"status": "ok", "video": video}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except PermissionError as exc:
        raise _fail(exc, status_code=403)
    except ValueError as exc:
        raise _fail(exc, status_code=400)
    except Exception as exc:
        raise _fail(exc)


@router.post("/videos/{video_id}/clips/delete")
def video_child_clips_delete(
    video_id: int,
    req: VideoChildClipsDeleteRequest | None = None,
) -> dict[str, Any]:
    try:
        _init()
        if db.get_video(video_id) is None:
            raise FileNotFoundError("Родительское видео не найдено.")
        result = _delete_video_child_workspace_items(
            video_id,
            remove_from_profiles=bool(req and req.remove_from_profiles),
        )
        items = db.list_workspace_items(limit=1000)
        return {"status": "ok", **result, **_workspace_items_response(items)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
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
def workspace_clip_delete(item_key: str, remove_from_profiles: bool = False) -> dict[str, Any]:
    try:
        _init()
        result = _delete_workspace_item(item_key, remove_from_profiles=remove_from_profiles)
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
        delete_result = _delete_workspace_items(
            list(req.items or []),
            remove_from_profiles=bool(req.remove_from_profiles),
        )
        items = db.list_workspace_items(limit=1000)
        return {
            "status": "ok",
            "summary": delete_result["summary"],
            "results": delete_result["results"],
            **_workspace_items_response(items),
        }
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
        profile_ids = [int(row["id"]) for row in rows]
        links_by_profile, accounts_by_id = _local_storage_profile_service_link_context(profile_ids)
        return {
            "items": [
                _local_storage_profile_dict(
                    row,
                    include_links=True,
                    service_links_by_profile=links_by_profile,
                    social_accounts_by_id=accounts_by_id,
                )
                for row in rows
            ]
        }
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
        current = db.get_local_storage_profile(profile_id)
        if current is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        updates = _request_updates(
            req,
            (
                "name",
                "handle",
                "description",
                "avatar_initials",
                "avatar_color",
                "avatar_url",
                "banner_color",
                "banner_url",
                "youtube_branding_sync_enabled",
                "name_override",
                "handle_override",
                "description_override",
                "avatar_override",
                "banner_override",
                "auto_import_enabled",
                "auto_import_sections",
                "auto_import_prefix",
                "tag_match_mode",
                "enabled",
            ),
        )
        if (
            "name" in updates
            and "name_override" not in updates
            and str(updates["name"] or "").strip() != str(_row(current, "name", "") or "")
        ):
            updates["name_override"] = True
        if (
            "handle" in updates
            and "handle_override" not in updates
            and str(updates["handle"] or "").strip().lstrip("@") != str(_row(current, "handle", "") or "")
        ):
            updates["handle_override"] = True
        if (
            "description" in updates
            and "description_override" not in updates
            and str(updates["description"] or "").strip() != str(_row(current, "description", "") or "")
        ):
            updates["description_override"] = True
        if (
            "avatar_override" not in updates
            and any(
                field in updates
                and str(updates[field] or "").strip() != str(_row(current, field, "") or "")
                for field in ("avatar_initials", "avatar_color", "avatar_url")
            )
        ):
            updates["avatar_override"] = True
        if (
            "banner_override" not in updates
            and any(
                field in updates
                and str(updates[field] or "").strip() != str(_row(current, field, "") or "")
                for field in ("banner_color", "banner_url")
            )
        ):
            updates["banner_override"] = True
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


def _youtube_account_display_name(account: Any, account_id: int | None = None) -> str:
    return (
        _row(account, "channel_title")
        or _row(account, "display_name")
        or (f"YouTube аккаунт #{int(account_id)}" if account_id is not None else "YouTube аккаунт")
    )


def _touch_storage_profile_youtube_branding(
    profile_id: int,
    *,
    account: Any | None = None,
    error: str | None = None,
) -> None:
    attempted_at = db.now_utc()
    if account is not None:
        account_id = int(_row(account, "id"))
        db.upsert_local_storage_profile_service_link(
            profile_id,
            platform="youtube",
            external_account_id=account_id,
            display_name=_youtube_account_display_name(account, account_id),
            status="linked",
        )
    db.update_local_storage_profile_youtube_branding_sync(
        profile_id,
        synced_at=attempted_at if not error else None,
        attempted_at=attempted_at,
        error=error,
    )


def _touch_linked_storage_profiles_youtube_branding(
    account_id: int,
    *,
    account: Any | None = None,
    error: str | None = None,
) -> int:
    touched = 0
    for profile in db.list_local_storage_profiles_for_service_account(
        platform="youtube",
        external_account_id=int(account_id),
    ):
        _touch_storage_profile_youtube_branding(
            int(profile["id"]),
            account=account,
            error=error,
        )
        touched += 1
    return touched


def _sync_storage_profile_youtube_branding(profile_id: int) -> dict[str, Any]:
    link = db.get_local_storage_profile_service_link(profile_id, "youtube")
    if (
        link is None
        or _row(link, "status", "not_connected") != "linked"
        or _row(link, "external_account_id") is None
    ):
        raise ValueError("Сначала привяжите YouTube-канал к профилю.")
    account_id = int(_row(link, "external_account_id"))
    account = _active_youtube_account(account_id)
    try:
        updated = sync_youtube_account_metadata(account)
        _touch_storage_profile_youtube_branding(profile_id, account=updated, error=None)
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {
            "status": "ok",
            "profile": _local_storage_profile_dict(profile, include_links=True),
            "account": _social_account_dict(updated, include_profile_links=True),
        }
    except Exception as sync_exc:
        message = str(sync_exc) or sync_exc.__class__.__name__
        db.set_social_account_metadata_sync_error(account_id, message)
        _touch_storage_profile_youtube_branding(profile_id, account=account, error=message)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {
            "status": "failed",
            "error": message,
            "profile": _local_storage_profile_dict(profile, include_links=True),
        }


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


PROFILE_PUBLISH_DEFAULTS = {
    "publish_mode": "public",
    "category_id": "22",
    "made_for_kids": False,
    "title_template": "",
    "description_template": "",
    "tags_template": "",
    "default_action": "queue",
}


def _normalize_profile_publish_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(PROFILE_PUBLISH_DEFAULTS)
    if value:
        raw.update(value)
    publish_mode = str(raw.get("publish_mode") or "public").strip().lower()
    if publish_mode not in {"private", "unlisted", "public"}:
        raise ValueError("publish_mode должен быть private, unlisted или public. Таймер задаётся отдельным расписанием профиля.")
    default_action = str(raw.get("default_action") or "queue").strip().lower()
    if default_action not in {"queue", "run", "schedule"}:
        raise ValueError("default_action должен быть queue, run или schedule.")
    category_id = str(raw.get("category_id") or "22").strip() or "22"
    result = {
        "publish_mode": publish_mode,
        "category_id": category_id,
        "made_for_kids": bool(raw.get("made_for_kids", False)),
        "title_template": str(raw.get("title_template") or "").strip()[:200],
        "description_template": str(raw.get("description_template") or "").strip()[:5000],
        "tags_template": str(raw.get("tags_template") or "").strip()[:500],
        "default_action": default_action,
    }
    validate_publish_options(
        title="ShortsFarm",
        publish_mode=result["publish_mode"],
        publish_at=None,
        category_id=result["category_id"],
    )
    return result


def _storage_profile_publish_settings(profile_id: int) -> dict[str, Any]:
    link = db.get_local_storage_profile_service_link(profile_id, "youtube")
    if link is None:
        return dict(PROFILE_PUBLISH_DEFAULTS)
    settings_json = _row(link, "settings_json")
    if not settings_json:
        return dict(PROFILE_PUBLISH_DEFAULTS)
    try:
        payload = json.loads(str(settings_json))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return _normalize_profile_publish_settings(payload.get("publish") if isinstance(payload.get("publish"), dict) else payload)


def _storage_profile_publish_settings_payload(settings: dict[str, Any]) -> str:
    return json.dumps({"publish": _normalize_profile_publish_settings(settings)}, ensure_ascii=False, sort_keys=True)


def _request_publish_settings(
    profile_id: int,
    req: LocalStorageProfileYouTubePublishRequest,
) -> dict[str, Any]:
    settings = _storage_profile_publish_settings(profile_id)
    overrides: dict[str, Any] = {}
    for key in ("publish_mode", "category_id", "made_for_kids", "title_template", "description_template", "tags_template"):
        value = getattr(req, key, None)
        if value is not None:
            overrides[key] = value
    if overrides:
        settings.update(overrides)
    return _normalize_profile_publish_settings(settings)


def _render_profile_publish_template(template: str, *, item: Any, profile: Any | None, fallback: str) -> str:
    text = str(template or "").strip()
    if not text:
        return fallback
    workspace_path = _row(item, "workspace_path", "") or ""
    context = {
        "title": _storage_profile_publish_title(item),
        "file_name": Path(workspace_path).name,
        "stem": Path(workspace_path).stem,
        "path": workspace_path,
        "profile": _row(profile, "name", "") if profile is not None else "",
        "handle": _row(profile, "handle", "") if profile is not None else "",
    }
    try:
        rendered = text.format(**context).strip()
    except (KeyError, ValueError):
        rendered = text
    return rendered or fallback


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
    profile = db.get_local_storage_profile(profile_id)
    settings = _request_publish_settings(profile_id, req)
    base_title = _storage_profile_publish_title(item)
    title = _render_profile_publish_template(
        settings.get("title_template", ""),
        item=item,
        profile=profile,
        fallback=base_title,
    )
    description = _render_profile_publish_template(
        settings.get("description_template", ""),
        item=item,
        profile=profile,
        fallback=_row(item, "description", "") or "",
    )
    tags_text = _render_profile_publish_template(
        settings.get("tags_template", ""),
        item=item,
        profile=profile,
        fallback=_row(item, "tags") or "",
    )
    clip_id = db.get_or_create_publish_clip_for_file(
        abs_path,
        title=title,
    )
    existing_job = db.get_publish_job_for_clip(account_id=account_id, clip_id=clip_id)
    validated = validate_publish_options(
        title=title,
        publish_mode=settings["publish_mode"],
        publish_at=None,
        category_id=settings["category_id"],
    )
    tags = parse_tags(tags_text)
    job_id = db.create_publish_job(
        account_id=account_id,
        clip_id=clip_id,
        title=title,
        description=description,
        tags=json.dumps(tags, ensure_ascii=False),
        category_id=settings["category_id"],
        privacy_status=str(validated["privacy_status"]),
        publish_mode=settings["publish_mode"],
        publish_at=validated["publish_at"],
        made_for_kids=bool(settings["made_for_kids"]),
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
        try:
            updated_account = sync_youtube_account_metadata(account)
            _touch_storage_profile_youtube_branding(profile_id, account=updated_account, error=None)
            sync_error = None
        except Exception as sync_exc:
            message = str(sync_exc) or sync_exc.__class__.__name__
            db.set_social_account_metadata_sync_error(int(req.account_id), message)
            _touch_storage_profile_youtube_branding(profile_id, account=account, error=message)
            sync_error = message
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        if sync_error:
            return {
                "status": "linked_with_sync_error",
                "sync_error": sync_error,
                "profile": _local_storage_profile_dict(profile, include_links=True),
            }
        return {"status": "linked", "profile": _local_storage_profile_dict(profile, include_links=True)}
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
        db.update_local_storage_profile(
            profile_id,
            youtube_branding_synced_at=None,
            youtube_branding_attempted_at=None,
            youtube_branding_sync_error=None,
        )
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {"profile": _local_storage_profile_dict(profile, include_links=True)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/sync-branding")
def local_storage_profile_youtube_sync_branding(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        return _sync_storage_profile_youtube_branding(profile_id)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except ValueError as exc:
        raise _fail(exc, status_code=400)
    except Exception as exc:
        raise _fail(exc)


@router.get("/storage-profiles/{profile_id}/publish-settings")
def local_storage_profile_publish_settings(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        profile = db.get_local_storage_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        return {
            "settings": _storage_profile_publish_settings(profile_id),
            "profile": _local_storage_profile_dict(profile, include_links=True),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/storage-profiles/{profile_id}/publish-settings")
def local_storage_profile_publish_settings_update(
    profile_id: int,
    req: LocalStorageProfilePublishSettingsRequest,
) -> dict[str, Any]:
    try:
        _init()
        profile = db.get_local_storage_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        current = _storage_profile_publish_settings(profile_id)
        raw_update = req.model_dump(exclude_unset=True)
        current.update({key: value for key, value in raw_update.items() if value is not None})
        settings = _normalize_profile_publish_settings(current)
        settings_json = _storage_profile_publish_settings_payload(settings)
        link = db.get_local_storage_profile_service_link(profile_id, "youtube")
        if link is None:
            db.upsert_local_storage_profile_service_link(
                profile_id,
                platform="youtube",
                display_name="",
                status="not_connected",
                settings_json=settings_json,
            )
        else:
            db.update_local_storage_profile_service_link_settings(
                profile_id,
                "youtube",
                settings_json,
            )
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {
            "settings": settings,
            "profile": _local_storage_profile_dict(profile, include_links=True),
        }
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
