from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter

from .. import db
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import SYSTEM_FOLDERS, resolve_workspace_path
from .api_common import fail, init_api
from .schemas import CatalogVideoTagsRequest, TagCreateRequest, TagUpdateRequest
from .tag_catalog import (
    catalog_tags_for_video as _catalog_tags_for_video,
    list_catalog_videos as _list_catalog_videos,
    tag_dict as _tag_dict,
    workspace_item_for_catalog_path as _workspace_item_for_catalog_path,
)

router = APIRouter()

LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS = {"edits", "ready", "published"}


def _request_updates(req: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    fields_set = getattr(req, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(req, "__fields_set__", set())
    return {
        field: getattr(req, field)
        for field in fields
        if field in fields_set
    }


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


@router.get("/tags")
def tags_list(
    enabled: bool | None = True,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    try:
        init_api()
        db.ensure_system_tags()
        rows = db.list_tags(
            enabled=enabled,
            kind=kind,
            q=q,
            limit=max(1, min(int(limit or 500), 2000)),
        )
        return {"items": [_tag_dict(row) for row in rows]}
    except Exception as exc:
        raise fail(exc)


@router.post("/tags")
def tag_create(req: TagCreateRequest) -> dict[str, Any]:
    try:
        init_api()
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
        raise fail(ValueError("Тег с таким slug уже существует."))
    except Exception as exc:
        raise fail(exc)


@router.patch("/tags/{tag_id}")
def tag_update(tag_id: int, req: TagUpdateRequest) -> dict[str, Any]:
    try:
        init_api()
        updates = _request_updates(req, ("name", "slug", "color", "description", "enabled"))
        if not db.update_tag(tag_id, **updates):
            raise FileNotFoundError("Тег не найден.")
        row = db.get_tag(tag_id)
        assert row is not None
        return {"tag": _tag_dict(row)}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except sqlite3.IntegrityError:
        raise fail(ValueError("Тег с таким slug уже существует."))
    except Exception as exc:
        raise fail(exc)


@router.delete("/tags/{tag_id}")
def tag_disable(tag_id: int) -> dict[str, Any]:
    try:
        init_api()
        if not db.disable_tag(tag_id):
            raise FileNotFoundError("Тег не найден.")
        return {"status": "ok", "tag_id": int(tag_id)}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.get("/catalog/videos/search")
def catalog_videos_search(
    q: str = "",
    tags: str | None = None,
    limit: int = 60,
    scope: str = "ready",
) -> dict[str, Any]:
    try:
        init_api()
        items = _list_catalog_videos(q=q, tags=tags, limit=limit, scope=scope)
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise fail(exc)


@router.get("/catalog/videos/random")
def catalog_videos_random(
    tags: str | None = None,
    limit: int = 24,
    scope: str = "ready",
) -> dict[str, Any]:
    try:
        init_api()
        items = _list_catalog_videos(tags=tags, limit=limit, randomize=True, scope=scope)
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise fail(exc)


@router.get("/catalog/videos/tags")
def catalog_video_tags(workspace_path: str) -> dict[str, Any]:
    try:
        init_api()
        relative, _ = _validate_catalog_workspace_video(workspace_path)
        item = _workspace_item_for_catalog_path(relative)
        return {
            "workspace_path": relative,
            "tags": _catalog_tags_for_video(relative, item),
            "item": item,
        }
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.post("/catalog/videos/tags")
def catalog_video_tags_update(req: CatalogVideoTagsRequest) -> dict[str, Any]:
    try:
        init_api()
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
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)
