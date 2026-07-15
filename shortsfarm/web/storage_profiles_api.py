from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter

from .. import db
from .api_common import fail, init_api
from .schemas import (
    LocalStorageProfileAutoImportRunRequest,
    LocalStorageProfileCreateRequest,
    LocalStorageProfileItemCreateRequest,
    LocalStorageProfileTagRulesRequest,
    LocalStorageProfileUpdateRequest,
)
from .storage_profile_catalog import (
    list_local_storage_candidate_videos,
    local_storage_candidate_counts,
    run_local_storage_profile_auto_import,
    run_local_storage_profile_tag_sync,
    storage_profile_tag_rules_payload,
    validate_local_storage_workspace_video,
)
from .storage_profile_payloads import (
    storage_profile_dict,
    storage_profile_item_dict,
    storage_profile_service_link_context,
)
from .api_common import row_value as _row

router = APIRouter()


def _request_updates(req: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    supplied = getattr(req, "model_fields_set", None)
    if supplied is None:
        supplied = getattr(req, "__fields_set__", set())
    return {field: getattr(req, field) for field in fields if field in supplied}


@router.get("/storage-profiles/ready-videos")
def local_storage_profile_ready_videos(limit: int = 1000) -> dict[str, Any]:
    try:
        init_api()
        items = list_local_storage_candidate_videos(limit=limit)
        return {"items": items, "counts": local_storage_candidate_counts(items)}
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/auto-import/run")
def local_storage_profile_auto_import_run(
    profile_id: int,
    req: LocalStorageProfileAutoImportRunRequest | None = None,
) -> dict[str, Any]:
    try:
        init_api()
        return run_local_storage_profile_auto_import(
            profile_id,
            force=bool(req.force) if req is not None else False,
        )
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles")
def local_storage_profiles(enabled: bool | None = True) -> dict[str, Any]:
    try:
        init_api()
        rows = db.list_local_storage_profiles(enabled=enabled)
        for row in rows:
            try:
                db.reconcile_local_storage_profile_channel_tags(int(row["id"]))
            except Exception:
                pass
        rows = db.list_local_storage_profiles(enabled=enabled)
        profile_ids = [int(row["id"]) for row in rows]
        links_by_profile, accounts_by_id = storage_profile_service_link_context(profile_ids)
        return {
            "items": [
                storage_profile_dict(
                    row,
                    include_links=True,
                    service_links_by_profile=links_by_profile,
                    social_accounts_by_id=accounts_by_id,
                )
                for row in rows
            ]
        }
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles")
def local_storage_profile_create(req: LocalStorageProfileCreateRequest) -> dict[str, Any]:
    try:
        init_api()
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
        return {"profile": storage_profile_dict(row, include_links=True)}
    except sqlite3.IntegrityError:
        raise fail(ValueError("Профиль с таким handle уже существует."))
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}")
def local_storage_profile_detail(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        row = db.get_local_storage_profile(profile_id)
        if row is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        row = db.get_local_storage_profile(profile_id)
        assert row is not None
        items = [
            storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {
            "profile": storage_profile_dict(row, include_links=True),
            "items": items,
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.patch("/storage-profiles/{profile_id}")
def local_storage_profile_update(
    profile_id: int,
    req: LocalStorageProfileUpdateRequest,
) -> dict[str, Any]:
    try:
        init_api()
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
        return {"profile": storage_profile_dict(row, include_links=True)}
    except sqlite3.IntegrityError:
        raise fail(ValueError("Профиль с таким handle уже существует."))
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.delete("/storage-profiles/{profile_id}")
def local_storage_profile_disable(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        if not db.disable_local_storage_profile(profile_id):
            raise FileNotFoundError("Локальный профиль не найден.")
        return {"status": "ok", "profile_id": int(profile_id)}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}/tag-rules")
def local_storage_profile_tag_rules(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        return storage_profile_tag_rules_payload(profile_id)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.patch("/storage-profiles/{profile_id}/tag-rules")
def local_storage_profile_tag_rules_update(
    profile_id: int,
    req: LocalStorageProfileTagRulesRequest,
) -> dict[str, Any]:
    try:
        init_api()
        db.replace_local_storage_profile_tag_rules(
            profile_id,
            include_tag_ids=req.include_tag_ids,
            exclude_tag_ids=req.exclude_tag_ids,
            tag_match_mode=req.tag_match_mode,
        )
        db.reconcile_local_storage_profile_channel_tags(profile_id)
        return storage_profile_tag_rules_payload(profile_id)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/tag-sync/run")
def local_storage_profile_tag_sync_run(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        return run_local_storage_profile_tag_sync(profile_id)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/storage-profiles/{profile_id}/items")
def local_storage_profile_items(profile_id: int) -> dict[str, Any]:
    try:
        init_api()
        if db.get_local_storage_profile(profile_id) is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        items = [
            storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ]
        return {"items": items}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/storage-profiles/{profile_id}/items")
def local_storage_profile_item_add(
    profile_id: int,
    req: LocalStorageProfileItemCreateRequest,
) -> dict[str, Any]:
    try:
        init_api()
        workspace_path, _ = validate_local_storage_workspace_video(req.workspace_path)
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
            "item": storage_profile_item_dict(row),
            "profile": storage_profile_dict(profile, include_links=True),
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.delete("/storage-profiles/{profile_id}/items/{item_id}")
def local_storage_profile_item_remove(profile_id: int, item_id: int) -> dict[str, Any]:
    try:
        init_api()
        if not db.remove_local_storage_profile_item(profile_id, item_id):
            raise FileNotFoundError("Видео в профиле не найдено.")
        profile = db.get_local_storage_profile(profile_id)
        return {
            "status": "ok",
            "profile": storage_profile_dict(profile, include_links=True) if profile else None,
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)
