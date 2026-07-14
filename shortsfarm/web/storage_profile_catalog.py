from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .. import db
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import get_workspace_root, resolve_workspace_path
from .storage_profile_payloads import (
    storage_profile_dict,
    storage_profile_item_dict,
    tag_rule_dict,
)
from .tag_catalog import list_catalog_videos
from .tag_catalog import row_value as _row


LOCAL_STORAGE_PROFILE_VIDEO_FOLDERS = {"edits", "ready", "published"}


def validate_local_storage_workspace_video(value: str) -> tuple[str, Path]:
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


def local_storage_candidate_dict(path: Path, root: Path) -> dict[str, Any]:
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


def list_local_storage_candidate_videos(limit: int = 1000) -> list[dict[str, Any]]:
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
                    items.append(local_storage_candidate_dict(path, root))
            except (OSError, ValueError):
                continue
    items.sort(key=lambda item: str(item.get("modified_at") or ""), reverse=True)
    return items[: max(1, min(int(limit or 1000), 5000))]


def local_storage_candidate_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict(Counter(item["section"] for item in items))
    counts["all"] = len(items)
    return counts


def storage_profile_auto_import_sections(row: Any) -> set[str]:
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


def storage_profile_prefix_matches(workspace_path: str, prefix: str) -> bool:
    clean_prefix = str(prefix or "").strip().strip("/")
    if not clean_prefix:
        return True
    return workspace_path == clean_prefix or workspace_path.startswith(f"{clean_prefix}/")


def run_local_storage_profile_auto_import(
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
                storage_profile_item_dict(item)
                for item in db.list_local_storage_profile_items(profile_id)
            ],
            "profile": storage_profile_dict(profile, include_links=True),
        }

    sections = storage_profile_auto_import_sections(profile)
    prefix = _row(profile, "auto_import_prefix", "") or ""
    candidates = list_local_storage_candidate_videos(limit=5000)
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
        if section not in sections or not storage_profile_prefix_matches(workspace_path, prefix):
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
                added.append(storage_profile_item_dict(row))
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
            storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ],
        "profile": storage_profile_dict(profile, include_links=True) if profile else None,
    }


def storage_profile_tag_rules_payload(profile_id: int) -> dict[str, Any]:
    profile = db.get_local_storage_profile(profile_id)
    if profile is None:
        raise FileNotFoundError("Локальный профиль не найден.")
    db.reconcile_local_storage_profile_channel_tags(profile_id)
    profile = db.get_local_storage_profile(profile_id)
    assert profile is not None
    rules = [tag_rule_dict(row) for row in db.list_local_storage_profile_tag_rules(profile_id)]
    return {
        "profile": storage_profile_dict(profile, include_links=True),
        "tag_match_mode": _row(profile, "tag_match_mode", "any") or "any",
        "rules": rules,
        "include_tag_ids": [rule["tag_id"] for rule in rules if rule["mode"] == "include"],
        "exclude_tag_ids": [rule["tag_id"] for rule in rules if rule["mode"] == "exclude"],
    }


def catalog_video_matches_profile_rules(
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


def run_local_storage_profile_tag_sync(profile_id: int) -> dict[str, Any]:
    rules_payload = storage_profile_tag_rules_payload(profile_id)
    include_tag_ids = set(int(tag_id) for tag_id in rules_payload["include_tag_ids"])
    exclude_tag_ids = set(int(tag_id) for tag_id in rules_payload["exclude_tag_ids"])
    tag_match_mode = str(rules_payload["tag_match_mode"] or "any")
    candidates = list_catalog_videos(limit=5000)
    existing_paths = {
        str(item["workspace_path"])
        for item in db.list_local_storage_profile_items(profile_id)
    }
    summary = {"scanned": 0, "matched": 0, "added": 0, "existing": 0, "skipped": 0, "errors": 0}
    added = []
    skipped = []
    for candidate in candidates:
        summary["scanned"] += 1
        if not catalog_video_matches_profile_rules(
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
                added.append(storage_profile_item_dict(row))
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
        "profile": storage_profile_dict(profile, include_links=True) if profile else None,
        "items": [
            storage_profile_item_dict(item)
            for item in db.list_local_storage_profile_items(profile_id)
        ],
    }
