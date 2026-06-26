"""Core validation and filesystem helpers for Remotion Studio."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from . import db
from .ffmpeg_tools import probe_duration
from .services import VIDEO_EXTENSIONS, safe_filename
from .workspace_fs import get_workspace_root, resolve_workspace_path


STUDIO_TEMPLATE_KEY = "reaction_top_25"
STUDIO_RENDERER = "remotion"
STUDIO_MEDIA_SECTIONS = (
    ("sources", "Исходники", "source"),
    ("cuts", "Нарезки", "cut"),
    ("prepared", "Подготовленные", "prepared"),
    ("edits", "Результаты монтажа", "edited"),
)
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} должен быть JSON object.")
    return value


def _fit(value: Any, name: str) -> str:
    normalized = str(value or "cover").strip().lower()
    if normalized not in {"cover", "contain"}:
        raise ValueError(f"{name} должен быть cover или contain.")
    return normalized


def _volume(value: Any, name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} должен быть числом от 0 до 1.") from exc
    if not 0 <= normalized <= 1:
        raise ValueError(f"{name} должен быть числом от 0 до 1.")
    return normalized


def _overlay_text(value: Any, name: str) -> str:
    text = str(value or "")
    if len(text) > 200:
        raise ValueError(f"{name} не может быть длиннее 200 символов.")
    return text


def normalize_studio_recipe(value: Any) -> dict[str, Any]:
    recipe = _dict(value, "recipe_json")
    media = _dict(recipe.get("media"), "recipe.media")
    main = _dict(media.get("main"), "recipe.media.main")
    reaction = _dict(media.get("reaction") or {}, "recipe.media.reaction")
    layout = _dict(recipe.get("layout") or {}, "recipe.layout")
    audio = _dict(recipe.get("audio") or {}, "recipe.audio")
    overlays = _dict(recipe.get("overlays") or {}, "recipe.overlays")

    workspace_path = str(main.get("workspace_path") or "").strip()
    if not workspace_path:
        raise ValueError("recipe.media.main.workspace_path не задан.")
    reaction_asset_id = reaction.get("asset_id")
    if reaction_asset_id in {"", None}:
        normalized_asset_id = None
    else:
        try:
            normalized_asset_id = int(reaction_asset_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("reaction asset_id должен быть integer.") from exc
        if normalized_asset_id <= 0:
            raise ValueError("reaction asset_id должен быть положительным integer.")

    try:
        reaction_height = int(layout.get("reaction_height", 480))
    except (TypeError, ValueError) as exc:
        raise ValueError("reaction_height должен быть integer.") from exc
    if not 240 <= reaction_height <= 960:
        raise ValueError("reaction_height должен быть от 240 до 960.")

    background_color = str(
        layout.get("background_color") or "#000000"
    ).strip()
    if not _COLOR_RE.fullmatch(background_color):
        raise ValueError("background_color должен быть в формате #RRGGBB.")

    return {
        "version": 1,
        "template": {
            "key": STUDIO_TEMPLATE_KEY,
            "renderer": STUDIO_RENDERER,
        },
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "media": {
            "main": {"workspace_path": workspace_path},
            "reaction": {"asset_id": normalized_asset_id},
        },
        "layout": {
            "reaction_height": reaction_height,
            "main_fit": _fit(layout.get("main_fit"), "main_fit"),
            "reaction_fit": _fit(
                layout.get("reaction_fit"),
                "reaction_fit",
            ),
            "background_color": background_color.lower(),
        },
        "audio": {
            "main_volume": _volume(
                audio.get("main_volume", 1),
                "main_volume",
            ),
            "reaction_volume": _volume(
                audio.get("reaction_volume", 0),
                "reaction_volume",
            ),
            "mute_reaction": bool(audio.get("mute_reaction", True)),
        },
        "overlays": {
            "top_text": _overlay_text(
                overlays.get("top_text"),
                "top_text",
            ),
            "bottom_text": _overlay_text(
                overlays.get("bottom_text"),
                "bottom_text",
            ),
        },
    }


def default_studio_recipe(
    main_workspace_path: str = "",
    reaction_asset_id: int | None = None,
) -> dict[str, Any]:
    return normalize_studio_recipe({
        "media": {
            "main": {"workspace_path": main_workspace_path or "sources/placeholder.mp4"},
            "reaction": {"asset_id": reaction_asset_id},
        },
        "layout": {},
        "audio": {},
        "overlays": {},
    })


def resolve_studio_media_path(relative_path: str) -> Path:
    text = str(relative_path or "").strip()
    if not text:
        raise ValueError("Workspace media path не задан.")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or "\\" in text:
        raise ValueError("Studio media path должен быть относительным workspace path.")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Path traversal в Studio media запрещён.")
    if not candidate.parts or candidate.parts[0] not in {
        section[0] for section in STUDIO_MEDIA_SECTIONS
    }:
        raise PermissionError(
            "Studio media разрешены только из sources/cuts/prepared/edits."
        )
    path = resolve_workspace_path(text)
    if not path.exists():
        raise FileNotFoundError(f"Studio media file не найден: {text}")
    if not path.is_file():
        raise ValueError("Studio media path должен указывать на обычный файл.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Studio поддерживает только video files.")
    return path


def _has_symlink_component(path: Path) -> bool:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else expanded.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def resolve_reaction_media_path(asset_id: int) -> tuple[Any, Path]:
    asset = db.get_reaction_asset(int(asset_id))
    if asset is None:
        raise FileNotFoundError("Reaction asset не найден.")
    if not bool(asset["enabled"]):
        raise ValueError("Reaction asset отключён.")
    unresolved = Path(str(asset["file_path"] or "")).expanduser()
    if _has_symlink_component(unresolved):
        raise PermissionError("Symlink reaction media запрещён.")
    path = unresolved.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Reaction media file не найден: {path}")
    if not path.is_file():
        raise ValueError("Reaction media должна быть обычным файлом.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Reaction asset не является поддерживаемым video file.")
    return asset, path


def list_studio_media_items() -> list[dict[str, Any]]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    sections: list[dict[str, Any]] = []
    for folder_name, label, kind in STUDIO_MEDIA_SECTIONS:
        folder = root / folder_name
        items: list[dict[str, Any]] = []
        if folder.is_dir() and not folder.is_symlink():
            for candidate in sorted(folder.rglob("*")):
                if (
                    candidate.is_symlink()
                    or not candidate.is_file()
                    or candidate.suffix.lower() not in VIDEO_EXTENSIONS
                ):
                    continue
                relative = candidate.relative_to(root).as_posix()
                try:
                    path = resolve_studio_media_path(relative)
                    stat = path.stat()
                except (OSError, ValueError, PermissionError):
                    continue
                items.append({
                    "name": path.name,
                    "workspace_path": relative,
                    "kind": kind,
                    "size": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                    "duration_sec": probe_duration(path),
                    "url": f"/api/studio/media?path={quote(relative, safe='')}",
                })
        sections.append({
            "key": folder_name,
            "label": label,
            "kind": kind,
            "items": items,
        })
    return sections


def resolved_studio_recipe(
    recipe: dict[str, Any],
    *,
    base_url: str = "",
    require_reaction: bool = True,
) -> dict[str, Any]:
    normalized = normalize_studio_recipe(recipe)
    main_path = resolve_studio_media_path(
        normalized["media"]["main"]["workspace_path"]
    )
    duration = probe_duration(main_path)
    if duration is None or duration <= 0:
        raise ValueError(f"Не удалось определить duration main media: {main_path}")

    asset_id = normalized["media"]["reaction"]["asset_id"]
    if asset_id is None and require_reaction:
        raise ValueError("Выберите reaction asset.")

    prefix = str(base_url or "").rstrip("/")
    main_relative = normalized["media"]["main"]["workspace_path"]
    resolved = json.loads(json.dumps(normalized, ensure_ascii=False))
    resolved["media"]["main"].update({
        "url": (
            f"{prefix}/api/studio/media?path={quote(main_relative, safe='')}"
        ),
        "duration_sec": duration,
    })
    if asset_id is not None:
        _asset, reaction_path = resolve_reaction_media_path(asset_id)
        reaction_duration = probe_duration(reaction_path)
        resolved["media"]["reaction"].update({
            "url": f"{prefix}/api/studio/reaction-media/{asset_id}",
            "duration_sec": reaction_duration,
        })
    resolved["duration_in_frames"] = max(
        1,
        int(round(duration * normalized["canvas"]["fps"])),
    )
    return resolved


def studio_project_payload(
    row: Any,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    recipe = json.loads(str(row["recipe_json"]))
    return {
        key: row[key]
        for key in row.keys()
        if key != "recipe_json"
    } | {
        "recipe_json": recipe,
        "resolved_recipe_json": resolved_studio_recipe(
            recipe,
            base_url=base_url,
            require_reaction=False,
        ),
    }


def build_remotion_output_paths(
    main_workspace_path: str,
    project_id: int,
    job_id: int,
) -> tuple[Path, Path]:
    main_path = resolve_studio_media_path(main_workspace_path)
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    relative = main_path.relative_to(root)
    subpath = Path(*relative.parts[1:])
    output_dir = (
        root
        / "edits"
        / subpath.parent
        / safe_filename(subpath.stem)
        / f"remotion_project_{int(project_id)}"
    )
    final_path = output_dir / f"render_job_{int(job_id)}.mp4"
    temp_path = output_dir / f"render_job_{int(job_id)}.tmp.mp4"
    return temp_path, final_path
