"""Business service for Studio templates, projects and render graphs."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from . import db
from .render_profiles import (
    get_render_profile,
    normalize_duration_limit,
    normalize_render_engine,
    normalize_start_offset,
)
from .services import safe_filename
from .studio import (
    collect_apply_media_paths,
    parameterized_recipe_from_template,
    resolved_studio_recipe,
)
from .studio_templates import (
    normalize_template_definition,
    reaction_required_for_definition,
    validate_renderer_for_definition,
)
from .workspace_fs import get_workspace_root


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def get_studio_template_for_use(template_id: int) -> tuple[Any, dict[str, Any]]:
    row = db.get_studio_template(int(template_id))
    if row is None:
        raise FileNotFoundError("Studio template не найден.")
    if row["deleted_at"] is not None:
        raise ValueError("Studio template удалён/скрыт и не может использоваться.")
    if str(row["status"] or "").lower() == "archived":
        raise ValueError("Studio template архивирован и не может использоваться.")
    definition = normalize_template_definition(json.loads(str(row["definition_json"])))
    return row, definition


def resolve_template_render_settings(
    definition: dict[str, Any],
    *,
    renderer_engine: str | None,
    render_profile: str | None,
    duration_limit_sec: float | None,
    start_offset_sec: float = 0,
    full_length: bool = False,
) -> tuple[str, Any, float | None, float]:
    renderer = validate_renderer_for_definition(
        definition,
        normalize_render_engine(renderer_engine or definition.get("default_renderer")),
    )
    profile = get_render_profile(render_profile)
    start = normalize_start_offset(start_offset_sec)
    duration = normalize_duration_limit(
        duration_limit_sec,
        profile=profile,
        full_length=full_length,
    )
    return renderer, profile, duration, start


def reaction_required(
    definition: dict[str, Any],
    parameter_values: dict[str, Any] | None,
) -> bool:
    return reaction_required_for_definition(definition, parameter_values or {})


def choose_reaction_for_template(
    definition: dict[str, Any],
    *,
    reaction_strategy: str,
    reaction_asset_id: int | None,
    reaction_pool_id: int | None,
    parameter_values: dict[str, Any] | None,
) -> int | None:
    if not reaction_required(definition, parameter_values):
        return None
    from .studio import choose_reaction_asset

    return choose_reaction_asset(
        reaction_strategy=reaction_strategy,
        reaction_asset_id=reaction_asset_id,
        reaction_pool_id=reaction_pool_id,
    )


def _batch_output_paths(
    main_workspace_path: str,
    template_key: str,
    job_id: int,
) -> tuple[Path, Path]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    relative = PurePosixPath(str(main_workspace_path))
    if not relative.parts:
        raise ValueError("main workspace path не задан.")
    subpath = Path(*relative.parts[1:]).with_suffix("")
    safe_parts = [safe_filename(part) for part in subpath.parts if part]
    output_dir = root / "edits" / Path(*safe_parts) / safe_filename(template_key)
    final_path = output_dir / f"render_job_{int(job_id)}.mp4"
    temp_path = output_dir / f"render_job_{int(job_id)}.tmp.mp4"
    return temp_path, final_path


def create_apply_batch(
    *,
    template_id: int,
    name: str | None,
    source_mode: str,
    source_path: str | None,
    source_paths: list[str],
    recursive: bool,
    reaction_strategy: str,
    reaction_asset_id: int | None,
    reaction_pool_id: int | None,
    parameter_values: dict[str, Any],
    renderer_engine: str | None,
    render_profile: str | None,
    duration_limit_sec: float | None,
    start_offset_sec: float,
    full_length: bool,
    start: bool,
    base_url: str,
    source_mode_override: str | None = None,
) -> dict[str, Any]:
    template, definition = get_studio_template_for_use(template_id)
    renderer, profile, duration_limit, start_offset = resolve_template_render_settings(
        definition,
        renderer_engine=renderer_engine,
        render_profile=render_profile,
        duration_limit_sec=duration_limit_sec,
        start_offset_sec=start_offset_sec,
        full_length=full_length,
    )
    allowed_sections = definition.get("slots", {}).get("main", {}).get(
        "allowed_sections",
        ["sources", "cuts", "prepared"],
    )
    requested_mode = str(source_mode_override or source_mode or "selected")
    batch_source_mode = (
        "folder_recursive"
        if requested_mode == "folder" and recursive
        else requested_mode
    )
    media_paths = collect_apply_media_paths(
        source_mode=batch_source_mode,
        source_paths=source_paths,
        source_path=source_path,
        recursive=recursive,
        allowed_sections=allowed_sections,
    )
    batch_name = str(name or "").strip() or f"{template['name']} batch"
    template_key = str(template["template_key"])

    prepared_items: list[dict[str, Any]] = []
    for main_workspace_path in media_paths:
        selected_reaction_id = choose_reaction_for_template(
            definition,
            reaction_strategy=reaction_strategy,
            reaction_asset_id=reaction_asset_id,
            reaction_pool_id=reaction_pool_id,
            parameter_values=parameter_values,
        )
        recipe = parameterized_recipe_from_template(
            definition,
            main_workspace_path=main_workspace_path,
            reaction_asset_id=selected_reaction_id,
            parameter_values=parameter_values,
            studio_template_id=int(template["id"]),
            template_version=int(template["version"]),
            renderer_engine=renderer,
        )
        resolved_studio_recipe(
            recipe,
            base_url=base_url,
            require_reaction=selected_reaction_id is not None,
            render_profile=profile.key,
            duration_limit_sec=duration_limit,
            start_offset_sec=start_offset,
            full_length=full_length,
        )
        prepared_items.append({
            "main_workspace_path": main_workspace_path,
            "reaction_asset_id": selected_reaction_id,
            "recipe": recipe,
        })

    now = db.now_utc()
    created_job_ids: list[int] = []
    with db.connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_render_batches
                (studio_template_id, template_key, name, source_mode, source_path,
                 reaction_strategy, reaction_asset_id, reaction_pool_id,
                 parameter_values_json, renderer_engine, render_profile,
                 duration_limit_sec, start_offset_sec, full_length,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                int(template["id"]),
                template_key,
                batch_name,
                batch_source_mode,
                source_path,
                reaction_strategy,
                reaction_asset_id,
                reaction_pool_id,
                _json(parameter_values),
                renderer,
                profile.key,
                duration_limit,
                start_offset,
                1 if full_length else 0,
                now,
                now,
            ),
        )
        batch_id = int(cur.lastrowid)
        for item in prepared_items:
            cur = con.execute(
                """
                INSERT INTO studio_projects
                    (workspace_item_key, main_workspace_path, template_key,
                     reaction_asset_id, recipe_json, created_at, updated_at,
                     studio_template_id, reaction_pool_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    item["main_workspace_path"],
                    template_key,
                    item["reaction_asset_id"],
                    _json(item["recipe"]),
                    now,
                    now,
                    int(template["id"]),
                    reaction_pool_id,
                ),
            )
            project_id = int(cur.lastrowid)
            cur = con.execute(
                """
                INSERT INTO remotion_render_jobs
                    (studio_project_id, status, output_path, renderer_engine,
                     render_profile, duration_limit_sec, start_offset_sec,
                     full_length, max_auto_retries, created_at)
                VALUES (?, 'queued', NULL, ?, ?, ?, ?, ?, 2, ?)
                """,
                (
                    project_id,
                    renderer,
                    profile.key,
                    duration_limit,
                    start_offset,
                    1 if full_length else 0,
                    now,
                ),
            )
            job_id = int(cur.lastrowid)
            _temp_path, final_path = _batch_output_paths(
                item["main_workspace_path"],
                template_key,
                job_id,
            )
            con.execute(
                "UPDATE remotion_render_jobs SET output_path=? WHERE id=?",
                (str(final_path), job_id),
            )
            con.execute(
                """
                INSERT INTO remotion_render_batch_items
                    (batch_id, studio_project_id, render_job_id,
                     main_workspace_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    batch_id,
                    project_id,
                    job_id,
                    item["main_workspace_path"],
                    now,
                    now,
                ),
            )
            created_job_ids.append(job_id)
        db._sync_remotion_render_batch_in_connection(con, batch_id)  # type: ignore[attr-defined]

    queue = None
    if start:
        from .remotion_renderer import start_studio_render_queue

        queue = start_studio_render_queue(base_url)
    return {"batch_id": batch_id, "job_ids": created_job_ids, "queue": queue}


def create_edit_render_graph(
    *,
    workspace_item_key: str,
    item_type: str,
    item_id: int,
    input_path: str,
    source_path: str,
    channel_profile_id: int,
    channel_profile_name: str,
    studio_template_id: int,
    reaction_asset_id: int | None,
    reaction_pool_id: int | None,
    main_workspace_path: str,
    parameter_values: dict[str, Any],
    renderer_engine: str | None,
    render_profile: str | None,
    duration_limit_sec: float | None,
    start_offset_sec: float,
    full_length: bool,
    base_url: str = "",
) -> dict[str, Any]:
    template, definition = get_studio_template_for_use(studio_template_id)
    renderer, profile, duration_limit, start_offset = resolve_template_render_settings(
        definition,
        renderer_engine=renderer_engine,
        render_profile=render_profile,
        duration_limit_sec=duration_limit_sec,
        start_offset_sec=start_offset_sec,
        full_length=full_length,
    )
    recipe = parameterized_recipe_from_template(
        definition,
        main_workspace_path=main_workspace_path,
        reaction_asset_id=reaction_asset_id,
        parameter_values=parameter_values,
        studio_template_id=int(template["id"]),
        template_version=int(template["version"]),
        renderer_engine=renderer,
    )
    resolved_studio_recipe(
        recipe,
        base_url=base_url,
        require_reaction=reaction_required(definition, parameter_values),
        render_profile=profile.key,
        duration_limit_sec=duration_limit,
        start_offset_sec=start_offset,
        full_length=full_length,
    )

    template_key = str(template["template_key"])
    now = db.now_utc()
    with db.connect() as con:
        cur = con.execute(
            """
            INSERT INTO studio_projects
                (workspace_item_key, main_workspace_path, template_key,
                 reaction_asset_id, recipe_json, created_at, updated_at,
                 studio_template_id, reaction_pool_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_item_key,
                main_workspace_path,
                template_key,
                reaction_asset_id,
                _json(recipe),
                now,
                now,
                int(template["id"]),
                reaction_pool_id,
            ),
        )
        project_id = int(cur.lastrowid)
        cur = con.execute(
            """
            INSERT INTO remotion_render_jobs
                (studio_project_id, status, output_path, renderer_engine,
                 render_profile, duration_limit_sec, start_offset_sec,
                 full_length, max_auto_retries, created_at)
            VALUES (?, 'queued', NULL, ?, ?, ?, ?, ?, 2, ?)
            """,
            (
                project_id,
                renderer,
                profile.key,
                duration_limit,
                start_offset,
                1 if full_length else 0,
                now,
            ),
        )
        render_job_id = int(cur.lastrowid)
        _temp_path, output_path = _batch_output_paths(
            main_workspace_path,
            template_key,
            render_job_id,
        )
        con.execute(
            "UPDATE remotion_render_jobs SET output_path=? WHERE id=?",
            (str(output_path), render_job_id),
        )
        cur = con.execute(
            """
            INSERT INTO edit_jobs
                (workspace_item_key, channel_profile_id, template_id,
                 reaction_asset_id, input_path, output_path, edited_path,
                 status, renderer, recipe_json, created_at,
                 studio_template_id, studio_project_id, remotion_render_job_id)
            VALUES (?, ?, NULL, ?, ?, ?, NULL, 'queued', ?, NULL, ?, ?, ?, ?)
            """,
            (
                workspace_item_key,
                int(channel_profile_id),
                reaction_asset_id,
                input_path,
                str(output_path),
                renderer,
                now,
                int(template["id"]),
                project_id,
                render_job_id,
            ),
        )
        edit_job_id = int(cur.lastrowid)

    job = db.get_edit_job(edit_job_id)
    if job is None:
        raise RuntimeError("Edit job создан, но не найден.")
    return {
        "edit_job_id": edit_job_id,
        "studio_project_id": project_id,
        "render_job_id": render_job_id,
        "output_path": str(output_path),
        "job": job,
        "recipe": recipe,
    }
