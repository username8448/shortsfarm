from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import db
from ..publish_youtube import validate_publish_job
from .api_common import fail, init_api
from .publish_payloads import publish_job_dict
from .schemas import (
    LocalStorageProfilePublishSettingsRequest,
    LocalStorageProfileYouTubeLinkRequest,
    LocalStorageProfileYouTubePublishRequest,
)
from .storage_profile_payloads import (
    storage_profile_dict,
    storage_profile_item_dict,
)
from .storage_profile_youtube_service import (
    active_youtube_account,
    create_publish_job_from_storage_profile_item,
    link_storage_profile_youtube,
    linked_storage_profile_youtube_account_id,
    local_storage_external_video_dict,
    mark_storage_profile_youtube_sync_error,
    normalize_profile_publish_settings,
    storage_profile_publish_settings,
    storage_profile_publish_settings_payload,
    sync_storage_profile_youtube_branding,
    sync_storage_profile_youtube_inventory,
    unlink_storage_profile_youtube,
)

router = APIRouter()


@router.post("/storage-profiles/{profile_id}/youtube/link")
def local_storage_profile_youtube_link(
    profile_id: int,
    req: LocalStorageProfileYouTubeLinkRequest,
) -> dict[str, Any]:
    try:
        init_api()
        return link_storage_profile_youtube(profile_id, int(req.account_id))
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.delete("/storage-profiles/{profile_id}/youtube/link")
def local_storage_profile_youtube_unlink(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        return unlink_storage_profile_youtube(profile_id)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/sync-branding")
def local_storage_profile_youtube_sync_branding(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        return sync_storage_profile_youtube_branding(profile_id)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except ValueError as exc:
        raise fail(exc, status_code=400)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}/publish-settings")
def local_storage_profile_publish_settings(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        profile = db.get_local_storage_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        return {
            "settings": storage_profile_publish_settings(profile_id),
            "profile": storage_profile_dict(profile, include_links=True),
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.patch("/storage-profiles/{profile_id}/publish-settings")
def local_storage_profile_publish_settings_update(
    profile_id: int,
    req: LocalStorageProfilePublishSettingsRequest,
) -> dict[str, Any]:
    try:
        init_api()
        profile = db.get_local_storage_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        current = storage_profile_publish_settings(profile_id)
        raw_update = req.model_dump(exclude_unset=True)
        current.update({key: value for key, value in raw_update.items() if value is not None})
        settings = normalize_profile_publish_settings(current)
        settings_json = storage_profile_publish_settings_payload(settings)
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
            "profile": storage_profile_dict(profile, include_links=True),
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}/publish-jobs")
def local_storage_profile_publish_jobs(profile_id: int, limit: int = 100) -> dict[str, Any]:
    try:
        init_api()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        rows = db.list_local_storage_profile_publish_jobs(
            profile_id,
            platform="youtube",
            limit=max(1, min(int(limit or 100), 500)),
        )
        return {"jobs": [publish_job_dict(row) for row in rows]}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}/youtube/videos")
def local_storage_profile_youtube_videos(profile_id: int, limit: int = 200) -> dict[str, Any]:
    try:
        init_api()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        rows = db.list_local_storage_profile_external_videos(
            profile_id,
            platform="youtube",
            limit=max(1, min(int(limit or 200), 500)),
        )
        return {"videos": [local_storage_external_video_dict(row) for row in rows]}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/enqueue")
def local_storage_profile_youtube_enqueue(
    profile_id: int,
    req: LocalStorageProfileYouTubePublishRequest,
) -> dict[str, Any]:
    try:
        init_api()
        if not req.item_ids:
            raise ValueError("Выберите видео профиля для публикации.")
        account_id = linked_storage_profile_youtube_account_id(profile_id, req.account_id)

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
                clip_id, job_id, previous_status = create_publish_job_from_storage_profile_item(
                    profile_id=profile_id,
                    item=item,
                    account_id=account_id,
                    req=req,
                )
                job = db.get_publish_job(job_id)
                if job is None:
                    raise FileNotFoundError(f"Publish job {job_id} не найден")
                validate_publish_job(job)
                job_payload = publish_job_dict(job)
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
            storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {
            "status": "ok",
            "summary": summary,
            "jobs": jobs,
            "items": items,
            "skipped_items": skipped_items,
            "profile": storage_profile_dict(profile, include_links=True) if profile else None,
            "profile_items": detail_items,
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/youtube/sync")
def local_storage_profile_youtube_sync(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        active_youtube_account(linked_storage_profile_youtube_account_id(profile_id))
        sync_data = sync_storage_profile_youtube_inventory(profile_id)
        profile = db.get_local_storage_profile(profile_id)
        jobs = db.list_local_storage_profile_publish_jobs(profile_id, platform="youtube", limit=200)
        external_rows = db.list_local_storage_profile_external_videos(profile_id, platform="youtube", limit=200)
        return {
            "status": "ok",
            "summary": sync_data["summary"],
            "channel": sync_data["channel"],
            "profile": storage_profile_dict(profile, include_links=True) if profile else None,
            "items": [
                storage_profile_item_dict(item)
                for item in db.list_local_storage_profile_items(profile_id)
            ],
            "jobs": [publish_job_dict(job) for job in jobs],
            "youtube_videos": [local_storage_external_video_dict(row) for row in external_rows],
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        mark_storage_profile_youtube_sync_error(profile_id, exc)
        raise fail(exc)
