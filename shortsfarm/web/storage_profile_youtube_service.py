from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..publish_youtube import (
    fetch_youtube_channel_videos,
    parse_tags,
    sync_youtube_account_metadata,
    validate_publish_options,
)
from .social_account_payloads import social_account_base_dict
from .storage_profile_catalog import (
    validate_local_storage_workspace_video,
)
from .storage_profile_payloads import storage_profile_dict
from .api_common import row_value as _row


PROFILE_PUBLISH_DEFAULTS = {
    "publish_mode": "public",
    "category_id": "22",
    "made_for_kids": False,
    "title_template": "",
    "description_template": "",
    "tags_template": "",
    "default_action": "queue",
}


def local_storage_external_video_dict(row: Any) -> dict[str, Any]:
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


def social_account_dict(row: Any, *, include_profile_links: bool = False) -> dict[str, Any]:
    data = social_account_base_dict(row)
    if include_profile_links:
        data["linked_storage_profiles"] = [
            storage_profile_dict(profile)
            for profile in db.list_local_storage_profiles_for_service_account(
                platform=str(_row(row, "platform", "") or ""),
                external_account_id=int(row["id"]),
            )
        ]
    return data


def active_youtube_account(account_id: int) -> Any:
    account = db.get_social_account(account_id)
    if account is None or _row(account, "platform") != "youtube":
        raise FileNotFoundError("YouTube аккаунт не найден.")
    if _row(account, "status", "active") != "active":
        raise ValueError("YouTube аккаунт не активен.")
    return account


def linked_storage_profile_youtube_account_id(
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
    active_youtube_account(linked_account_id)
    return linked_account_id


def youtube_account_display_name(account: Any, account_id: int | None = None) -> str:
    return (
        _row(account, "channel_title")
        or _row(account, "display_name")
        or (f"YouTube аккаунт #{int(account_id)}" if account_id is not None else "YouTube аккаунт")
    )


def touch_storage_profile_youtube_branding(
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
            display_name=youtube_account_display_name(account, account_id),
            status="linked",
        )
    db.update_local_storage_profile_youtube_branding_sync(
        profile_id,
        synced_at=attempted_at if not error else None,
        attempted_at=attempted_at,
        error=error,
    )


def touch_linked_storage_profiles_youtube_branding(
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
        touch_storage_profile_youtube_branding(
            int(profile["id"]),
            account=account,
            error=error,
        )
        touched += 1
    return touched


def sync_storage_profile_youtube_branding(profile_id: int) -> dict[str, Any]:
    link = db.get_local_storage_profile_service_link(profile_id, "youtube")
    if (
        link is None
        or _row(link, "status", "not_connected") != "linked"
        or _row(link, "external_account_id") is None
    ):
        raise ValueError("Сначала привяжите YouTube-канал к профилю.")
    account_id = int(_row(link, "external_account_id"))
    account = active_youtube_account(account_id)
    try:
        updated = sync_youtube_account_metadata(account)
        touch_storage_profile_youtube_branding(profile_id, account=updated, error=None)
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {
            "status": "ok",
            "profile": storage_profile_dict(profile, include_links=True),
            "account": social_account_dict(updated, include_profile_links=True),
        }
    except Exception as sync_exc:
        message = str(sync_exc) or sync_exc.__class__.__name__
        db.set_social_account_metadata_sync_error(account_id, message)
        touch_storage_profile_youtube_branding(profile_id, account=account, error=message)
        profile = db.get_local_storage_profile(profile_id)
        assert profile is not None
        return {
            "status": "failed",
            "error": message,
            "profile": storage_profile_dict(profile, include_links=True),
        }


def link_storage_profile_youtube(profile_id: int, account_id: int) -> dict[str, Any]:
    if db.get_local_storage_profile(profile_id) is None:
        raise FileNotFoundError("Локальный профиль не найден.")
    account = active_youtube_account(account_id)
    display_name = youtube_account_display_name(account, account_id)
    db.upsert_local_storage_profile_service_link(
        profile_id,
        platform="youtube",
        external_account_id=int(account_id),
        display_name=display_name,
        status="linked",
    )
    try:
        updated_account = sync_youtube_account_metadata(account)
        touch_storage_profile_youtube_branding(profile_id, account=updated_account, error=None)
        sync_error = None
    except Exception as sync_exc:
        message = str(sync_exc) or sync_exc.__class__.__name__
        db.set_social_account_metadata_sync_error(int(account_id), message)
        touch_storage_profile_youtube_branding(profile_id, account=account, error=message)
        sync_error = message
    db.reconcile_local_storage_profile_channel_tags(profile_id)
    profile = db.get_local_storage_profile(profile_id)
    assert profile is not None
    if sync_error:
        return {
            "status": "linked_with_sync_error",
            "sync_error": sync_error,
            "profile": storage_profile_dict(profile, include_links=True),
        }
    return {"status": "linked", "profile": storage_profile_dict(profile, include_links=True)}


def unlink_storage_profile_youtube(profile_id: int) -> dict[str, Any]:
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
    return {"profile": storage_profile_dict(profile, include_links=True)}


def storage_profile_publish_title(item: Any) -> str:
    title = _normalize_setting_text(_row(item, "title"))
    if title:
        return title
    workspace_path = _row(item, "workspace_path", "") or ""
    stem = Path(workspace_path).stem
    return stem or f"ShortsFarm profile video {int(item['id'])}"


def profile_item_has_status_ready_tag(item: Any) -> bool:
    workspace_path = _row(item, "workspace_path", "") or ""
    if not workspace_path:
        return False
    try:
        tags = db.list_workspace_tag_links(workspace_path=workspace_path)
    except Exception:
        tags = []
    return any(_row(tag, "slug") == "status-ready" for tag in tags)


def normalize_profile_publish_settings(value: dict[str, Any] | None = None) -> dict[str, Any]:
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


def storage_profile_publish_settings(profile_id: int) -> dict[str, Any]:
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
    return normalize_profile_publish_settings(payload.get("publish") if isinstance(payload.get("publish"), dict) else payload)


def storage_profile_publish_settings_payload(settings: dict[str, Any]) -> str:
    return json.dumps({"publish": normalize_profile_publish_settings(settings)}, ensure_ascii=False, sort_keys=True)


def request_publish_settings(
    profile_id: int,
    req: Any,
) -> dict[str, Any]:
    settings = storage_profile_publish_settings(profile_id)
    overrides: dict[str, Any] = {}
    for key in ("publish_mode", "category_id", "made_for_kids", "title_template", "description_template", "tags_template"):
        value = getattr(req, key, None)
        if value is not None:
            overrides[key] = value
    if overrides:
        settings.update(overrides)
    return normalize_profile_publish_settings(settings)


def render_profile_publish_template(template: str, *, item: Any, profile: Any | None, fallback: str) -> str:
    text = str(template or "").strip()
    if not text:
        return fallback
    workspace_path = _row(item, "workspace_path", "") or ""
    context = {
        "title": storage_profile_publish_title(item),
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


def create_publish_job_from_storage_profile_item(
    *,
    profile_id: int,
    item: Any,
    account_id: int,
    req: Any,
) -> tuple[int, int, str | None]:
    _workspace_path, abs_path = validate_local_storage_workspace_video(_row(item, "workspace_path"))
    if not profile_item_has_status_ready_tag(item):
        raise ValueError("Публикация доступна только для видео с тегом «Готово».")
    profile = db.get_local_storage_profile(profile_id)
    settings = request_publish_settings(profile_id, req)
    base_title = storage_profile_publish_title(item)
    title = render_profile_publish_template(
        settings.get("title_template", ""),
        item=item,
        profile=profile,
        fallback=base_title,
    )
    description = render_profile_publish_template(
        settings.get("description_template", ""),
        item=item,
        profile=profile,
        fallback=_row(item, "description", "") or "",
    )
    tags_text = render_profile_publish_template(
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


def sync_storage_profile_youtube_inventory(profile_id: int) -> dict[str, Any]:
    account_id = linked_storage_profile_youtube_account_id(profile_id)
    account = active_youtube_account(account_id)
    synced_at = db.now_utc()
    display_name = youtube_account_display_name(account, account_id)
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
    return {
        "summary": summary,
        "channel": inventory.get("channel") or {},
    }


def mark_storage_profile_youtube_sync_error(profile_id: int, exc: Exception) -> None:
    try:
        db.update_local_storage_profile_service_link_sync(
            profile_id,
            "youtube",
            last_sync_error=str(exc) or exc.__class__.__name__,
        )
    except Exception:
        pass


def _normalize_setting_text(value: Any) -> str:
    return str(value or "").strip()
