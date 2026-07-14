from __future__ import annotations

import secrets
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter

from .. import db
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import SYSTEM_FOLDERS, get_workspace_root, resolve_workspace_path
from .api_common import fail, init_api
from .schemas import CatalogVideoTagsRequest, TagCreateRequest, TagUpdateRequest

router = APIRouter()

LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS = {"edits", "ready", "published"}


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


def _request_updates(req: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    fields_set = getattr(req, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(req, "__fields_set__", set())
    return {
        field: getattr(req, field)
        for field in fields
        if field in fields_set
    }


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
