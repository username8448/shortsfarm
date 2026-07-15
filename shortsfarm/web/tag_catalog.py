from __future__ import annotations

import secrets
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import SYSTEM_FOLDERS, get_workspace_root, resolve_workspace_path
from .api_common import row_value


def tag_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row_value(row, "name", "") or "",
        "slug": row_value(row, "slug", "") or "",
        "kind": row_value(row, "kind", "user") or "user",
        "color": row_value(row, "color", "#64748b") or "#64748b",
        "description": row_value(row, "description", "") or "",
        "system_key": row_value(row, "system_key"),
        "locked": bool(row_value(row, "locked", 0)),
        "enabled": bool(row_value(row, "enabled", 1)),
        "created_at": row_value(row, "created_at"),
        "updated_at": row_value(row, "updated_at"),
    }


def workspace_item_for_catalog_path(workspace_path: str) -> dict[str, Any] | None:
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


def catalog_tags_for_video(workspace_path: str, item: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
        payload = tag_dict(tag)
        tags_by_id[payload["id"]] = payload
    if item:
        status_slug = f"status-{item.get('workspace_status') or 'draft'}"
        if not any(tag.get("slug") == status_slug for tag in tags_by_id.values()):
            status_tag = db.get_tag_by_slug(status_slug)
            if status_tag is not None:
                payload = tag_dict(status_tag)
                tags_by_id[payload["id"]] = payload
    return list(tags_by_id.values())


def catalog_video_dict(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    item = workspace_item_for_catalog_path(relative)
    tags = catalog_tags_for_video(relative, item)
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


def catalog_video_matches_query(item: dict[str, Any], query: str) -> bool:
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


def parse_tag_filter(value: str | None) -> set[int]:
    result: set[int] = set()
    for part in str(value or "").split(","):
        text = part.strip()
        if text.isdigit():
            result.add(int(text))
    return result


def catalog_video_matches_tag_filter(item: dict[str, Any], tag_ids: set[int]) -> bool:
    if not tag_ids:
        return True
    item_tag_ids = {int(tag["id"]) for tag in item.get("tags") or []}
    return tag_ids <= item_tag_ids


def list_catalog_videos(
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
    tag_filter = parse_tag_filter(tags)
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
                payload = catalog_video_dict(path, root)
                if not catalog_video_matches_query(payload, q):
                    continue
                if not catalog_video_matches_tag_filter(payload, tag_filter):
                    continue
                items.append(payload)
            except (OSError, ValueError):
                continue
    if randomize:
        secrets.SystemRandom().shuffle(items)
    else:
        items.sort(key=lambda item: str(item.get("modified_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 100), 500))]
