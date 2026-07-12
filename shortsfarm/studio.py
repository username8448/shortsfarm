"""Core validation and filesystem helpers for Remotion Studio."""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from . import db
from .ffmpeg_tools import probe_duration
from .render_profiles import get_render_profile
from .services import VIDEO_EXTENSIONS, safe_filename
from .studio_templates import (
    composition_id_for_definition,
    effective_parameter_values,
    reaction_required_for_definition,
    require_template_adapter,
    validate_renderer_for_definition,
)
from .workspace_fs import get_workspace_root, resolve_workspace_path


STUDIO_RENDERER = "remotion"
STUDIO_RENDERERS = {"ffmpeg_fast", "remotion"}
STUDIO_MEDIA_SECTIONS = (
    ("sources", "Исходники", "source"),
    ("cuts", "Нарезки", "cut"),
    ("prepared", "Подготовленные", "prepared"),
    ("edits", "Результаты монтажа", "edited"),
)
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TEMPLATE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")
_REACTION_POSITIONS = {"top", "bottom", "pip", "none"}
_PIP_POSITIONS = {"top_left", "top_right", "bottom_left", "bottom_right"}


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
    template = _dict(recipe.get("template") or {}, "recipe.template")
    media = _dict(recipe.get("media"), "recipe.media")
    main = _dict(media.get("main"), "recipe.media.main")
    reaction = _dict(media.get("reaction") or {}, "recipe.media.reaction")
    canvas = _dict(recipe.get("canvas") or {}, "recipe.canvas")
    layout = _dict(recipe.get("layout") or {}, "recipe.layout")
    audio = _dict(recipe.get("audio") or {}, "recipe.audio")
    overlays = _dict(recipe.get("overlays") or {}, "recipe.overlays")

    template_key = str(template.get("key") or "reaction_top_25").strip().lower()
    if not _TEMPLATE_KEY_RE.fullmatch(template_key):
        raise ValueError("recipe.template.key имеет некорректный формат.")
    renderer = str(template.get("renderer") or STUDIO_RENDERER).strip().lower()
    if renderer == "ffmpeg":
        renderer = "ffmpeg_fast"
    if renderer not in STUDIO_RENDERERS:
        raise ValueError("Studio recipe renderer должен быть ffmpeg_fast или remotion.")
    renderer_adapter = str(
        template.get("adapter") or template.get("renderer_adapter") or ""
    ).strip()
    composition_id = str(template.get("composition_id") or "").strip()
    raw_studio_template_id = template.get("studio_template_id")
    studio_template_id: int | None
    if raw_studio_template_id in {None, ""}:
        studio_template_id = None
    else:
        try:
            studio_template_id = int(raw_studio_template_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("recipe.template.studio_template_id должен быть integer.") from exc
        if studio_template_id <= 0:
            raise ValueError("recipe.template.studio_template_id должен быть положительным integer.")
    raw_template_version = template.get("template_version", template.get("version"))
    template_version: int | None
    if raw_template_version in {None, ""}:
        template_version = None
    else:
        try:
            template_version = int(raw_template_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("recipe.template.template_version должен быть integer.") from exc
        if template_version <= 0:
            raise ValueError("recipe.template.template_version должен быть положительным integer.")
    raw_definition_schema_version = template.get("definition_schema_version")
    definition_schema_version: int | None
    if raw_definition_schema_version in {None, ""}:
        definition_schema_version = None
    else:
        try:
            definition_schema_version = int(raw_definition_schema_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("recipe.template.definition_schema_version должен быть integer.") from exc
        if definition_schema_version <= 0:
            raise ValueError("recipe.template.definition_schema_version должен быть положительным integer.")

    width = int(canvas.get("width", 1080))
    height = int(canvas.get("height", 1920))
    fps = int(canvas.get("fps", 30))
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("Canvas width, height и fps должны быть положительными.")

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

    reaction_position = str(
        layout.get("reaction_position") or "top"
    ).strip().lower()
    if reaction_position not in _REACTION_POSITIONS:
        raise ValueError("reaction_position должен быть top, bottom, pip или none.")
    pip_position = str(
        layout.get("pip_position") or "top_right"
    ).strip().lower()
    if pip_position not in _PIP_POSITIONS:
        raise ValueError(
            "pip_position должен быть top_left, top_right, bottom_left или bottom_right."
        )

    background_color = str(
        layout.get("background_color") or "#000000"
    ).strip()
    if not _COLOR_RE.fullmatch(background_color):
        raise ValueError("background_color должен быть в формате #RRGGBB.")

    adapter_key = renderer_adapter or str(template.get("renderer_adapter") or "")
    has_reaction_layout = adapter_key != "main_only" and reaction_position != "none"
    if adapter_key == "main_only" or reaction_position == "none":
        reaction_enabled = False
        reaction_required = False
        normalized_asset_id = None
        reaction_position = "none"
    else:
        reaction_required = (
            bool(reaction.get("required"))
            if "required" in reaction
            else True
        )
        reaction_enabled = (
            bool(reaction.get("enabled"))
            if "enabled" in reaction
            else bool(reaction_required or normalized_asset_id is not None)
        )
    if reaction_required:
        reaction_enabled = True
    if not reaction_enabled:
        reaction_required = False
        normalized_asset_id = None
        reaction_position = "none"
    elif normalized_asset_id is None:
        raise ValueError("Enabled reaction требует reaction asset_id.")

    return {
        "version": 1,
        "template": {
            "key": template_key,
            "renderer": renderer,
            **({"studio_template_id": studio_template_id} if studio_template_id else {}),
            **({"template_version": template_version} if template_version else {}),
            **({"definition_schema_version": definition_schema_version} if definition_schema_version else {}),
            **({"adapter": renderer_adapter} if renderer_adapter else {}),
            **({"renderer_adapter": renderer_adapter} if renderer_adapter else {}),
            **({"composition_id": composition_id} if composition_id else {}),
        },
        "parameters": dict(recipe.get("parameters") or {}),
        "canvas": {"width": width, "height": height, "fps": fps},
        "media": {
            "main": {"workspace_path": workspace_path},
            "reaction": {
                "enabled": reaction_enabled,
                "required": reaction_required,
                "asset_id": normalized_asset_id,
            },
        },
        "layout": {
            "reaction_position": reaction_position,
            "reaction_height": reaction_height,
            "pip_position": pip_position,
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
    template_key: str = "reaction_top_25",
) -> dict[str, Any]:
    return normalize_studio_recipe({
        "template": {
            "key": template_key,
            "renderer": STUDIO_RENDERER,
            "adapter": "reaction_layout",
            "renderer_adapter": "reaction_layout",
            "composition_id": "ReactionLayoutTemplate",
        },
        "parameters": {},
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


def list_studio_apply_sources() -> dict[str, Any]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    sections = list_studio_media_items()
    folder_paths: set[str] = set()
    for folder_name, _label, _kind in STUDIO_MEDIA_SECTIONS:
        folder = root / folder_name
        if folder.is_dir() and not folder.is_symlink():
            folder_paths.add(folder_name)
            for candidate in folder.rglob("*"):
                if (
                    candidate.is_symlink()
                    or not candidate.is_file()
                    or candidate.suffix.lower() not in VIDEO_EXTENSIONS
                ):
                    continue
                try:
                    relative_parent = candidate.parent.relative_to(root).as_posix()
                except ValueError:
                    continue
                folder_paths.add(relative_parent)
    return {
        "sections": sections,
        "folders": [
            {"path": path, "name": path}
            for path in sorted(folder_paths)
        ],
    }


def _validate_workspace_folder(relative_path: str, allowed_sections: set[str]) -> Path:
    text = str(relative_path or "").strip().strip("/")
    if not text:
        raise ValueError("Папка source не выбрана.")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or "\\" in text:
        raise ValueError("Source folder должен быть относительным workspace path.")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Path traversal в source folder запрещён.")
    if not candidate.parts or candidate.parts[0] not in allowed_sections:
        raise PermissionError(
            "Source folder не разрешён схемой main slot этого template."
        )
    path = resolve_workspace_path(candidate.as_posix())
    if path.is_symlink():
        raise PermissionError("Symlink source folder запрещён.")
    if not path.is_dir():
        raise FileNotFoundError(f"Source folder не найден: {text}")
    return path


def collect_apply_media_paths(
    *,
    source_mode: str,
    source_paths: list[str] | None = None,
    source_path: str | None = None,
    recursive: bool = False,
    allowed_sections: list[str] | None = None,
) -> list[str]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    allowed = set(allowed_sections or ["sources", "cuts", "prepared"])
    mode = str(source_mode or "selected").strip().lower()
    resolved: list[str] = []
    if mode == "selected":
        for raw in source_paths or []:
            path = resolve_studio_media_path(raw)
            relative = path.relative_to(root).as_posix()
            first = relative.split("/", 1)[0]
            if first not in allowed:
                raise PermissionError(
                    f"Файл {relative} не разрешён схемой main slot этого template."
                )
            resolved.append(relative)
    elif mode in {"folder", "folder_recursive"}:
        folder = _validate_workspace_folder(str(source_path or ""), allowed)
        use_recursive = recursive or mode == "folder_recursive"
        iterator = folder.rglob("*") if use_recursive else folder.glob("*")
        for candidate in iterator:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or candidate.suffix.lower() not in VIDEO_EXTENSIONS
            ):
                continue
            relative = candidate.relative_to(root).as_posix()
            try:
                resolve_studio_media_path(relative)
            except (ValueError, PermissionError, FileNotFoundError):
                continue
            resolved.append(relative)
    else:
        raise ValueError("source_mode должен быть selected, folder или folder_recursive.")
    unique = list(dict.fromkeys(sorted(resolved)))
    if not unique:
        raise ValueError("Видео для batch не найдены.")
    return unique


def parameterized_recipe_from_template(
    definition: dict[str, Any],
    *,
    main_workspace_path: str,
    reaction_asset_id: int | None,
    parameter_values: dict[str, Any] | None = None,
    studio_template_id: int | None = None,
    template_version: int | None = None,
    renderer_engine: str | None = None,
) -> dict[str, Any]:
    adapter = require_template_adapter(definition)
    composition_id = composition_id_for_definition(definition)
    renderer = validate_renderer_for_definition(definition, renderer_engine)
    values = effective_parameter_values(definition, parameter_values)
    reaction_required = reaction_required_for_definition(definition, values)

    def value(key: str, fallback: Any) -> Any:
        return values.get(key, fallback)

    reaction_position = value(
        "reaction_position",
        "none" if adapter.key == "main_only" else "top",
    )
    reaction_slot = (definition.get("slots") or {}).get("reaction")
    has_reaction_slot = isinstance(reaction_slot, dict)
    reaction_enabled = bool(
        has_reaction_slot
        and reaction_position != "none"
        and (reaction_required or reaction_asset_id is not None)
    )
    if not reaction_enabled:
        reaction_position = "none"
        reaction_asset_id = None

    return normalize_studio_recipe({
        "version": 1,
        "template": {
            "key": str(definition["key"]),
            "renderer": renderer,
            **({"studio_template_id": int(studio_template_id)} if studio_template_id else {}),
            **({"template_version": int(template_version)} if template_version else {}),
            "definition_schema_version": int(definition.get("schema_version") or 2),
            "adapter": adapter.key,
            "renderer_adapter": adapter.key,
            "composition_id": composition_id,
        },
        "parameters": values,
        "canvas": definition.get("canvas") or {"width": 1080, "height": 1920, "fps": 30},
        "media": {
            "main": {"workspace_path": main_workspace_path},
            "reaction": {
                "enabled": reaction_enabled,
                "required": reaction_required,
                "asset_id": reaction_asset_id if reaction_enabled else None,
            },
        },
        "layout": {
            "reaction_position": reaction_position,
            "reaction_height": value("reaction_height", 480),
            "pip_position": value("pip_position", "top_right"),
            "main_fit": value("main_fit", "cover"),
            "reaction_fit": value("reaction_fit", "cover"),
            "background_color": value("background_color", "#000000"),
        },
        "audio": {
            "main_volume": value("main_volume", 1),
            "reaction_volume": value("reaction_volume", 0),
            "mute_reaction": value("mute_reaction", True),
        },
        "overlays": {
            "top_text": value("top_text", ""),
            "bottom_text": value("bottom_text", ""),
        },
    })


def choose_reaction_asset(
    *,
    reaction_strategy: str,
    reaction_asset_id: int | None = None,
    reaction_pool_id: int | None = None,
) -> int:
    strategy = str(reaction_strategy or "fixed_asset").strip().lower()
    if strategy == "fixed_asset":
        if reaction_asset_id is None:
            raise ValueError("Выберите reaction asset.")
        resolve_reaction_media_path(int(reaction_asset_id))
        return int(reaction_asset_id)
    if reaction_pool_id is None:
        raise ValueError("Выберите reaction pool.")
    candidates: list[tuple[int, int]] = []
    for row in db.list_reaction_pool_items_with_assets(int(reaction_pool_id)):
        if not bool(row["enabled"]) or not bool(row["asset_enabled"]):
            continue
        asset_id = int(row["reaction_asset_id"])
        try:
            resolve_reaction_media_path(asset_id)
        except (ValueError, PermissionError, FileNotFoundError):
            continue
        candidates.append((asset_id, max(1, int(row["weight"] or 1))))
    if not candidates:
        raise ValueError("В выбранном reaction pool нет доступных reaction-файлов.")
    if strategy == "pool_first":
        return candidates[0][0]
    if strategy == "pool_weighted":
        return random.choices(
            [item[0] for item in candidates],
            weights=[item[1] for item in candidates],
            k=1,
        )[0]
    raise ValueError("reaction_strategy должен быть fixed_asset, pool_first или pool_weighted.")


def resolved_studio_recipe(
    recipe: dict[str, Any],
    *,
    base_url: str = "",
    require_reaction: bool = True,
    render_profile: str | None = None,
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
) -> dict[str, Any]:
    normalized = normalize_studio_recipe(recipe)
    main_path = resolve_studio_media_path(
        normalized["media"]["main"]["workspace_path"]
    )
    duration = probe_duration(main_path)
    if duration is None or duration <= 0:
        raise ValueError(f"Не удалось определить duration main media: {main_path}")

    reaction_media = normalized["media"]["reaction"]
    asset_id = reaction_media["asset_id"]
    reaction_enabled = bool(reaction_media.get("enabled", asset_id is not None))
    reaction_required = bool(reaction_media.get("required", require_reaction))
    if asset_id is None and reaction_enabled and reaction_required and require_reaction:
        raise ValueError("Выберите reaction asset.")

    profile = get_render_profile(render_profile)
    start_sec = max(0.0, float(start_offset_sec or 0))
    if start_sec >= duration:
        raise ValueError("start_offset_sec находится за пределами main media.")
    available = max(0.001, duration - start_sec)
    if full_length:
        render_duration = available
    else:
        limit = (
            float(duration_limit_sec)
            if duration_limit_sec not in {None, ""}
            else float(profile.max_duration_sec)
        )
        if limit <= 0:
            raise ValueError("duration_limit_sec должен быть больше 0.")
        render_duration = min(available, limit)
    end_sec = start_sec + render_duration

    prefix = str(base_url or "").rstrip("/")
    main_relative = normalized["media"]["main"]["workspace_path"]
    resolved = json.loads(json.dumps(normalized, ensure_ascii=False))
    resolved["canvas"] = {
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
    }
    resolved["media"]["main"].update({
        "url": (
            f"{prefix}/api/studio/media?path={quote(main_relative, safe='')}"
        ),
        "duration_sec": duration,
    })
    if asset_id is not None and reaction_enabled:
        _asset, reaction_path = resolve_reaction_media_path(asset_id)
        reaction_duration = probe_duration(reaction_path)
        resolved["media"]["reaction"].update({
            "url": f"{prefix}/api/studio/reaction-media/{asset_id}",
            "duration_sec": reaction_duration,
        })
    resolved["trim"] = {
        "start_sec": start_sec,
        "duration_sec": render_duration,
        "end_sec": end_sec,
        "source_duration_sec": duration,
        "full_length": bool(full_length),
    }
    resolved["render_profile"] = profile.payload()
    resolved["duration_in_frames"] = max(
        1,
        int(round(render_duration * profile.fps)),
    )
    return resolved


def studio_project_payload(
    row: Any,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    recipe = normalize_studio_recipe(json.loads(str(row["recipe_json"])))
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


def build_batch_remotion_output_paths(
    main_workspace_path: str,
    template_key: str,
    job_id: int,
) -> tuple[Path, Path]:
    main_path = resolve_studio_media_path(main_workspace_path)
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    relative = main_path.relative_to(root)
    subpath = Path(*relative.parts[1:])
    source_tree = subpath.with_suffix("")
    safe_parts = [safe_filename(part) for part in source_tree.parts if part]
    output_dir = root / "edits" / Path(*safe_parts) / safe_filename(template_key)
    final_path = output_dir / f"render_job_{int(job_id)}.mp4"
    temp_path = output_dir / f"render_job_{int(job_id)}.tmp.mp4"
    return temp_path, final_path
