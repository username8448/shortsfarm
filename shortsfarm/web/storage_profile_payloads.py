from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db
from ..workspace_fs import resolve_workspace_path
from .publish_payloads import publish_job_dict
from .social_account_payloads import social_account_base_dict
from .api_common import row_value as _row
from .tag_catalog import tag_dict


def tag_rule_dict(row: Any) -> dict[str, Any]:
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


def storage_profile_service_link_dict(
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
            data["youtube_account"] = social_account_base_dict(account)
    return data


def clean_youtube_profile_handle(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("@"):
        text = text[1:]
    return text.strip().strip("/")


def first_youtube_service_link(service_links: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            link
            for link in service_links
            if link.get("platform") == "youtube" and link.get("status") == "linked"
        ),
        None,
    )


def storage_profile_effective_fields(
    row: Any,
    service_links: list[dict[str, Any]],
) -> dict[str, Any]:
    youtube_link = first_youtube_service_link(service_links)
    account = (youtube_link or {}).get("youtube_account") or {}
    sync_enabled = bool(_row(row, "youtube_branding_sync_enabled", 1))
    use_youtube = bool(sync_enabled and account)
    local_name = _row(row, "name", "") or ""
    local_handle = _row(row, "handle", "") or ""
    local_description = _row(row, "description", "") or ""
    local_avatar_url = _row(row, "avatar_url", "") or ""
    local_initials = _row(row, "avatar_initials", "") or ""
    official_name = account.get("channel_title") or account.get("official_channel_title") or ""
    official_handle = clean_youtube_profile_handle(
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


def storage_profile_dict(
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
            storage_profile_service_link_dict(link, social_accounts_by_id=social_accounts_by_id)
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
    data.update(storage_profile_effective_fields(row, service_links))
    if include_links:
        data["service_links"] = service_links
        data["tag_rules"] = [
            tag_rule_dict(rule)
            for rule in db.list_local_storage_profile_tag_rules(profile_id)
        ]
    return data


def storage_profile_service_link_context(
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
            storage_profile_service_link_dict(
                link,
                social_accounts_by_id=accounts_by_id,
            )
        )
    return links_by_profile, accounts_by_id


def storage_profile_item_dict(row: Any) -> dict[str, Any]:
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
    publish_job = publish_job_dict(publish_job_row) if publish_job_row is not None else None
    tag_rows = []
    if workspace_path:
        try:
            tag_rows = db.list_workspace_tag_links(workspace_path=workspace_path)
        except Exception:
            tag_rows = []
    catalog_tags = [tag_dict(tag) for tag in tag_rows]
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
