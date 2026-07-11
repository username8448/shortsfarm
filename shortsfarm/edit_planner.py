"""Planning for template-driven edit jobs without rendering media."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from . import db
from .config import output_dir
from .render_profiles import DEFAULT_RENDER_ENGINE, DEFAULT_RENDER_PROFILE
from .services import safe_filename
from .studio import (
    build_batch_remotion_output_paths,
    parameterized_recipe_from_template,
)
from .studio_templates import normalize_template_definition
from .workspace_fs import (
    build_edit_output_path,
    get_workspace_root,
    workspace_source_relative_path,
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


def _resolve_template(profile: Any, template_id: int | None) -> Any:
    resolved_id = template_id if template_id is not None else profile["default_template_id"]
    if resolved_id is None:
        raise ValueError("У channel profile не выбран default template.")
    template = db.get_edit_template(int(resolved_id))
    if template is None:
        raise ValueError("Edit template не найден.")
    if not bool(template["enabled"]):
        raise ValueError("Edit template отключён.")
    return template


def _resolve_studio_template(profile: Any, studio_template_id: int | None) -> Any | None:
    resolved_id = (
        studio_template_id
        if studio_template_id is not None
        else profile["default_studio_template_id"]
    )
    if resolved_id is None:
        return None
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

    studio_template = _resolve_studio_template(profile, studio_template_id)
    if studio_template is not None:
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
        reaction = _resolve_reaction(profile, reaction_asset_id)
        main_workspace_path = _workspace_relative_path(input_path)
        try:
            definition = normalize_template_definition(
                json.loads(str(studio_template["definition_json"]))
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"Studio template definition_json invalid: {exc.msg}") from exc
        recipe = parameterized_recipe_from_template(
            definition,
            main_workspace_path=main_workspace_path,
            reaction_asset_id=int(reaction["id"]) if reaction is not None else None,
            parameter_values=parameter_values or {},
            studio_template_id=int(studio_template["id"]),
            template_version=int(studio_template["version"]),
        )
        source_path = str(item.get("source_path") or "")
        project_id = db.create_studio_project(
            workspace_item_key=normalized_item_key,
            main_workspace_path=main_workspace_path,
            template_key=str(studio_template["template_key"]),
            reaction_asset_id=int(reaction["id"]) if reaction is not None else None,
            reaction_pool_id=profile["reaction_pool_id"],
            studio_template_id=int(studio_template["id"]),
            recipe_json=recipe,
        )
        render_job_id = db.create_remotion_render_job(
            project_id,
            output_path=None,
            renderer_engine=renderer_engine,
            render_profile=render_profile,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=full_length,
        )
        _tmp_path, output_path = build_batch_remotion_output_paths(
            main_workspace_path,
            str(studio_template["template_key"]),
            render_job_id,
        )
        db.update_remotion_render_job_output(render_job_id, str(output_path))
        job_id = db.create_edit_job(
            workspace_item_key=normalized_item_key,
            channel_profile_id=int(profile["id"]),
            template_id=None,
            studio_template_id=int(studio_template["id"]),
            studio_project_id=project_id,
            remotion_render_job_id=render_job_id,
            reaction_asset_id=int(reaction["id"]) if reaction is not None else None,
            input_path=str(input_path),
            output_path=str(output_path),
            renderer="remotion",
            recipe_json={
                **recipe,
                "workspace": {
                    "item_key": normalized_item_key,
                    "item_type": item_type,
                    "item_id": item_id,
                    "main_input_path": str(input_path),
                    "source_path": source_path,
                },
                "channel_profile": {
                    "id": int(profile["id"]),
                    "name": str(profile["name"]),
                },
                "output": {"path": str(output_path)},
                "remotion": {
                    "studio_project_id": project_id,
                    "render_job_id": render_job_id,
                    "renderer_engine": renderer_engine,
                    "render_profile": render_profile,
                    "duration_limit_sec": duration_limit_sec,
                    "start_offset_sec": start_offset_sec,
                    "full_length": bool(full_length),
                },
            },
        )
        job = db.get_edit_job(job_id)
        if job is None:
            raise RuntimeError("Edit job создан, но не найден.")
        return {
            "item_key": normalized_item_key,
            "status": "created",
            "job": _job_dict(job),
        }

    template = _resolve_template(profile, template_id)

    existing = db.find_existing_edit_job(
        normalized_item_key,
        int(profile["id"]),
        int(template["id"]),
        include_done=not force_new,
    )
    if existing is not None:
        return {
            "item_key": normalized_item_key,
            "status": "existing",
            "job": _job_dict(existing),
        }

    item_type, item_id, item, input_path = _resolve_workspace_item(normalized_item_key)
    reaction = _resolve_reaction(profile, reaction_asset_id)
    renderer = str(template["renderer"] or "ffmpeg")

    job_id = db.create_edit_job(
        workspace_item_key=normalized_item_key,
        channel_profile_id=int(profile["id"]),
        template_id=int(template["id"]),
        reaction_asset_id=int(reaction["id"]) if reaction is not None else None,
        input_path=str(input_path),
        renderer=renderer,
    )
    source_path = str(item.get("source_path") or "")
    source_relative_path = workspace_source_relative_path(source_path)
    if source_relative_path is not None:
        output_path = build_edit_output_path(
            source_relative_path,
            item_type,
            item_id,
            job_id,
            create=False,
        ).resolve()
    else:
        template_key = safe_filename(
            str(template["key"] or f"template_{template['id']}")
        )
        output_path = (
            output_dir()
            / "edited"
            / template_key
            / (
                f"{item_type}_{item_id}__profile_{int(profile['id'])}"
                f"__job_{job_id}.mp4"
            )
        ).resolve()
    try:
        template_recipe = json.loads(str(template["recipe_json"]))
    except json.JSONDecodeError as exc:
        db.mark_edit_job_failed(job_id, f"Template recipe_json invalid: {exc.msg}")
        raise ValueError(f"Template recipe_json invalid: {exc.msg}") from exc

    recipe = {
        "version": 1,
        "template": {
            "id": int(template["id"]),
            "key": str(template["key"]),
            "renderer": renderer,
            "recipe": template_recipe,
        },
        "workspace": {
            "item_key": normalized_item_key,
            "item_type": item_type,
            "item_id": item_id,
            "main_input_path": str(input_path),
            "source_path": source_path,
        },
        "channel_profile": {
            "id": int(profile["id"]),
            "name": str(profile["name"]),
        },
        "reaction": {
            "asset_id": int(reaction["id"]) if reaction is not None else None,
            "file_path": (
                str(Path(str(reaction["file_path"])).expanduser().resolve())
                if reaction is not None
                else None
            ),
        },
        "output": {"path": str(output_path)},
    }
    if not db.update_edit_job_plan(
        job_id,
        input_path=str(input_path),
        output_path=str(output_path),
        recipe_json=recipe,
    ):
        raise RuntimeError("Edit job создан, но не удалось сохранить materialized recipe.")
    job = db.get_edit_job(job_id)
    if job is None:
        raise RuntimeError("Edit job создан, но не найден.")
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
