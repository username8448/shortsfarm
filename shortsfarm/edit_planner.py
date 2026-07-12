"""Planning for template-driven edit jobs without rendering media."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from . import db
from .render_profiles import DEFAULT_RENDER_ENGINE, DEFAULT_RENDER_PROFILE
from .studio_service import create_edit_render_graph
from .studio_templates import (
    normalize_template_definition,
    reaction_required_for_definition,
)
from .workspace_fs import (
    get_workspace_root,
)


def parse_workspace_item_key(item_key: str) -> tuple[str, int]:
    text = str(item_key or "").strip()
    if ":" not in text:
        raise ValueError("Workspace item key должен быть segment:<id> или clip:<id>.")
    item_type, raw_id = text.split(":", 1)
    item_type = item_type.strip().lower()
    if item_type not in {"segment", "clip"}:
        raise ValueError("Workspace item type должен быть segment или clip.")
    try:
        item_id = int(raw_id)
    except ValueError as exc:
        raise ValueError("Workspace item id должен быть положительным integer.") from exc
    if item_id <= 0:
        raise ValueError("Workspace item id должен быть положительным integer.")
    return item_type, item_id


def select_reaction_asset_from_pool(pool_id: int) -> Any | None:
    candidates: list[Any] = []
    weights: list[int] = []
    for row in db.list_reaction_pool_items_with_assets(pool_id):
        file_path = Path(str(row["file_path"] or "")).expanduser()
        if not bool(row["enabled"]) or not bool(row["asset_enabled"]):
            continue
        if not file_path.exists() or not file_path.is_file():
            continue
        weight = int(row["weight"] or 0)
        if weight <= 0:
            continue
        asset = db.get_reaction_asset(int(row["reaction_asset_id"]))
        if asset is not None:
            candidates.append(asset)
            weights.append(weight)
    if not candidates:
        return None
    return random.choices(candidates, weights=weights, k=1)[0]


def _resolve_profile(profile_id: int) -> Any:
    profile = db.get_channel_profile(int(profile_id))
    if profile is None:
        raise ValueError("Channel profile не найден.")
    if not bool(profile["enabled"]):
        raise ValueError("Channel profile отключён.")
    return profile


def _resolve_studio_template(profile: Any, studio_template_id: int | None) -> Any:
    resolved_id = (
        studio_template_id
        if studio_template_id is not None
        else profile["default_studio_template_id"]
    )
    if resolved_id is None:
        raise ValueError("У channel profile не выбран default Studio template.")
    template = db.get_studio_template(int(resolved_id))
    if template is None:
        raise ValueError("Studio template не найден.")
    if template["deleted_at"] is not None:
        raise ValueError("Studio template удалён/скрыт и не может использоваться для новых задач.")
    if str(template["status"] or "").lower() == "archived":
        raise ValueError("Studio template архивирован и не может использоваться для новых задач.")
    return template


def _resolve_reaction(profile: Any, reaction_asset_id: int | None) -> Any | None:
    if reaction_asset_id is not None:
        asset = db.get_reaction_asset(int(reaction_asset_id))
        if asset is None:
            raise ValueError("Reaction asset не найден.")
        if not bool(asset["enabled"]):
            raise ValueError("Reaction asset отключён.")
        file_path = Path(str(asset["file_path"] or "")).expanduser()
        if not file_path.exists() or not file_path.is_file():
            raise ValueError("Reaction file отсутствует.")
        return asset

    pool_id = profile["reaction_pool_id"]
    if pool_id is None:
        return None
    asset = select_reaction_asset_from_pool(int(pool_id))
    if asset is None:
        raise ValueError("В пуле нет доступных reaction files.")
    return asset


def _resolve_workspace_item(item_key: str) -> tuple[str, int, dict[str, Any], Path]:
    item_type, item_id = parse_workspace_item_key(item_key)
    item = db.get_workspace_item(item_type, item_id)
    if item is None:
        raise ValueError("Workspace item не найден.")
    if str(item.get("workspace_status") or "draft") != "ready":
        raise ValueError("В монтаж можно ставить только ready items.")
    if item.get("missing"):
        raise ValueError("Видео отсутствует.")

    prepared_path = Path(str(item.get("prepared_path") or "")).expanduser()
    if (
        str(item.get("prepare_status") or "none") == "done"
        and prepared_path.exists()
        and prepared_path.is_file()
    ):
        return item_type, item_id, item, prepared_path.resolve()

    if str(item.get("target_aspect") or "original") != "original":
        raise ValueError("Клип требует подготовку формата, но prepared file отсутствует.")

    input_path = Path(str(item.get("path") or "")).expanduser()
    if not input_path.exists() or not input_path.is_file():
        raise ValueError("Видео отсутствует.")
    return item_type, item_id, item, input_path.resolve()


def _workspace_relative_path(path: Path) -> str:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Studio template требует файл внутри workspace.") from exc
    return relative.as_posix()


def _job_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def plan_edit_job_for_workspace_item(
    item_key: str,
    channel_profile_id: int,
    *,
    reaction_asset_id: int | None = None,
    template_id: int | None = None,
    studio_template_id: int | None = None,
    parameter_values: dict[str, Any] | None = None,
    renderer_engine: str = DEFAULT_RENDER_ENGINE,
    render_profile: str = DEFAULT_RENDER_PROFILE,
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
    force_new: bool = False,
) -> dict[str, Any]:
    profile = _resolve_profile(channel_profile_id)
    parsed_type, parsed_id = parse_workspace_item_key(item_key)
    normalized_item_key = f"{parsed_type}:{parsed_id}"

    if template_id is not None:
        raise ValueError("Legacy templates are no longer supported.")

    studio_template = _resolve_studio_template(profile, studio_template_id)
    existing = db.find_existing_studio_edit_job(
        normalized_item_key,
        int(profile["id"]),
        int(studio_template["id"]),
        include_done=not force_new,
    )
    if existing is not None:
        return {
            "item_key": normalized_item_key,
            "status": "existing",
            "job": _job_dict(existing),
        }

    item_type, item_id, item, input_path = _resolve_workspace_item(normalized_item_key)
    try:
        definition = normalize_template_definition(
            json.loads(str(studio_template["definition_json"]))
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Studio template definition_json invalid: {exc.msg}") from exc
    reaction_needed = reaction_required_for_definition(definition, parameter_values or {})
    reaction = (
        _resolve_reaction(profile, reaction_asset_id)
        if reaction_needed or reaction_asset_id is not None
        else None
    )
    if reaction_needed and reaction is None:
        raise ValueError("Для выбранного Studio template требуется reaction asset или reaction pool.")
    source_path = str(item.get("source_path") or "")
    graph = create_edit_render_graph(
        workspace_item_key=normalized_item_key,
        item_type=item_type,
        item_id=item_id,
        input_path=str(input_path),
        source_path=source_path,
        channel_profile_id=int(profile["id"]),
        channel_profile_name=str(profile["name"]),
        studio_template_id=int(studio_template["id"]),
        reaction_asset_id=int(reaction["id"]) if reaction is not None else None,
        reaction_pool_id=profile["reaction_pool_id"],
        main_workspace_path=_workspace_relative_path(input_path),
        parameter_values=parameter_values or {},
        renderer_engine=renderer_engine,
        render_profile=render_profile,
        duration_limit_sec=duration_limit_sec,
        start_offset_sec=start_offset_sec,
        full_length=full_length,
    )
    job = graph["job"]
    return {
        "item_key": normalized_item_key,
        "status": "created",
        "job": _job_dict(job),
    }


def plan_edit_jobs_for_workspace_items(
    item_keys: list[str],
    channel_profile_id: int,
    *,
    reaction_asset_id: int | None = None,
    template_id: int | None = None,
    studio_template_id: int | None = None,
    parameter_values: dict[str, Any] | None = None,
    renderer_engine: str = DEFAULT_RENDER_ENGINE,
    render_profile: str = DEFAULT_RENDER_PROFILE,
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
    force_new: bool = False,
) -> dict[str, Any]:
    summary = {"created": 0, "existing": 0, "skipped": 0, "errors": 0}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_key in item_keys:
        raw_key = str(item_key or "").strip()
        if raw_key in seen:
            continue
        seen.add(raw_key)
        try:
            result = plan_edit_job_for_workspace_item(
                raw_key,
                channel_profile_id,
                reaction_asset_id=reaction_asset_id,
                template_id=template_id,
                studio_template_id=studio_template_id,
                parameter_values=parameter_values,
                renderer_engine=renderer_engine,
                render_profile=render_profile,
                duration_limit_sec=duration_limit_sec,
                start_offset_sec=start_offset_sec,
                full_length=full_length,
                force_new=force_new,
            )
            summary[result["status"]] += 1
            results.append(result)
        except (ValueError, FileNotFoundError) as exc:
            summary["skipped"] += 1
            results.append({
                "item_key": raw_key,
                "status": "skipped",
                "reason": str(exc) or exc.__class__.__name__,
            })
        except Exception as exc:
            summary["errors"] += 1
            results.append({
                "item_key": raw_key,
                "status": "error",
                "reason": str(exc) or exc.__class__.__name__,
            })
    return {"status": "ok", "summary": summary, "results": results}
