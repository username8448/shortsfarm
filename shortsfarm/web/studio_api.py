"""HTTP API for the Remotion Studio vertical slice."""
from __future__ import annotations

import json
import mimetypes
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..remotion_renderer import (
    recover_remotion_render_queue,
    remotion_render_queue_status,
    start_remotion_render_queue,
)
from ..render_profiles import (
    DEFAULT_RENDER_ENGINE,
    DEFAULT_RENDER_PROFILE,
    get_render_profile,
    normalize_duration_limit,
    normalize_render_engine,
    normalize_start_offset,
    render_profiles_payload,
)
from ..studio import (
    build_batch_remotion_output_paths,
    build_remotion_output_paths,
    choose_reaction_asset,
    collect_apply_media_paths,
    list_studio_apply_sources,
    list_studio_media_items,
    normalize_studio_recipe,
    parameterized_recipe_from_template,
    resolve_reaction_media_path,
    resolve_studio_media_path,
    resolved_studio_recipe,
    studio_project_payload,
)
from ..studio_templates import (
    TEMPLATE_STATUSES,
    ensure_default_studio_templates,
    normalize_template_definition,
    require_remotion_adapter,
    template_row_payload,
    unique_duplicate_key,
)
from ..workspace_fs import get_workspace_root


router = APIRouter()
SEEDED_STUDIO_TEMPLATE_KEYS = {
    "reaction_top_25",
    "reaction_top_33",
    "reaction_top_50",
    "reaction_bottom_25",
    "reaction_pip_corner",
}


class StudioProjectRequest(BaseModel):
    recipe_json: dict[str, Any]
    workspace_item_key: str | None = None
    studio_template_id: int | None = None
    reaction_pool_id: int | None = None


class StudioTemplateRequest(BaseModel):
    name: str
    status: str = "draft"
    definition: dict[str, Any]


class StudioApplyRequest(BaseModel):
    name: str | None = None
    source_mode: str = "selected"
    source_paths: list[str] = []
    source_path: str | None = None
    recursive: bool = False
    reaction_strategy: str = "fixed_asset"
    reaction_asset_id: int | None = None
    reaction_pool_id: int | None = None
    parameter_values: dict[str, Any] = {}
    renderer_engine: str = DEFAULT_RENDER_ENGINE
    render_profile: str = DEFAULT_RENDER_PROFILE
    duration_limit_sec: float | None = None
    start_offset_sec: float = 0
    full_length: bool = False
    start: bool = True


class StudioPipelineRequest(BaseModel):
    name: str
    studio_template_id: int
    source_mode: str = "selected"
    source_paths: list[str] = []
    source_path: str | None = None
    recursive: bool = False
    reaction_strategy: str = "fixed_asset"
    reaction_asset_id: int | None = None
    reaction_pool_id: int | None = None
    parameter_values: dict[str, Any] = {}
    renderer_engine: str = DEFAULT_RENDER_ENGINE
    render_profile: str = DEFAULT_RENDER_PROFILE
    duration_limit_sec: float | None = None
    start_offset_sec: float = 0
    full_length: bool = False
    enabled: bool = True


def _fail(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"message": str(exc) or exc.__class__.__name__},
    )


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _project_columns(recipe: dict[str, Any]) -> tuple[str, str, int | None]:
    normalized = normalize_studio_recipe(recipe)
    return (
        str(normalized["media"]["main"]["workspace_path"]),
        str(normalized["template"]["key"]),
        normalized["media"]["reaction"]["asset_id"],
    )


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _workspace_relative_output_path(output_path: str | None) -> str | None:
    raw = str(output_path or "").strip()
    if not raw:
        return None
    root = get_workspace_root()
    if root is None:
        return None
    try:
        return Path(raw).expanduser().resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _render_job_payload(row: Any) -> dict[str, Any]:
    payload = _row_dict(row)
    payload["full_length"] = bool(payload.get("full_length"))
    payload["progress_percent"] = float(payload.get("progress_percent") or 0)
    payload["output_workspace_path"] = _workspace_relative_output_path(
        payload.get("output_path")
    )
    payload["media_url"] = (
        f"/api/studio/render-jobs/{int(row['id'])}/media"
        if str(row["status"]) == "done"
        else None
    )
    return payload


def _job_completion_percent(item: dict[str, Any]) -> float:
    status = str(item.get("render_status") or item.get("status") or "")
    if status in {"done", "failed", "cancelled"}:
        return 100.0
    if status == "rendering":
        return min(99.0, max(0.0, float(item.get("progress_percent") or 0)))
    return 0.0


def _batch_progress_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    queued = sum(1 for item in items if str(item.get("render_status") or item.get("status")) == "queued")
    rendering_items = [
        (index, item)
        for index, item in enumerate(items, start=1)
        if str(item.get("render_status") or item.get("status")) == "rendering"
    ]
    done = sum(1 for item in items if str(item.get("render_status") or item.get("status")) == "done")
    failed = sum(1 for item in items if str(item.get("render_status") or item.get("status")) == "failed")
    cancelled = sum(1 for item in items if str(item.get("render_status") or item.get("status")) == "cancelled")
    percent = (
        sum(_job_completion_percent(item) for item in items) / total
        if total
        else 0.0
    )
    current_job_id = None
    message = "Нет render jobs"
    if rendering_items:
        index, current = rendering_items[0]
        current_job_id = int(current["render_job_id"])
        message = f"Рендерится {index} из {total}"
    elif queued:
        message = f"В очереди: {queued} из {total}"
    elif failed or cancelled:
        message = f"Batch завершён с ошибками: готово {done}, ошибок {failed}"
    elif done:
        message = f"Batch готов: {done} видео создано"
    return {
        "percent": round(percent, 1),
        "queued": queued,
        "rendering": len(rendering_items),
        "done": done,
        "failed": failed,
        "cancelled": cancelled,
        "total": total,
        "current_job_id": current_job_id,
        "message": message,
    }


def _batch_payload(row: Any, *, include_items: bool = False) -> dict[str, Any]:
    payload = _row_dict(row)
    payload["parameter_values"] = json.loads(str(row["parameter_values_json"] or "{}"))
    payload["full_length"] = bool(payload.get("full_length"))
    payload.pop("parameter_values_json", None)
    items: list[dict[str, Any]] = []
    for item in db.list_remotion_render_batch_items(int(row["id"])):
        item_payload = _row_dict(item)
        item_payload["full_length"] = bool(item_payload.get("full_length"))
        item_payload["progress_percent"] = float(item_payload.get("progress_percent") or 0)
        item_payload["output_workspace_path"] = _workspace_relative_output_path(
            item_payload.get("output_path")
        )
        item_payload["media_url"] = (
            f"/api/studio/render-jobs/{int(item['render_job_id'])}/media"
            if str(item["render_status"]) == "done"
            else None
        )
        items.append(item_payload)
    payload["progress"] = _batch_progress_summary(items)
    if include_items:
        payload["items"] = items
    return payload


def _completed_render_payload(row: Any) -> dict[str, Any]:
    payload = _render_job_payload(row)
    payload["template_key"] = row["template_key"]
    payload["main_workspace_path"] = row["main_workspace_path"]
    payload["studio_template_id"] = row["studio_template_id"]
    return payload


def _pipeline_payload(row: Any) -> dict[str, Any]:
    payload = _row_dict(row)
    payload["source_paths"] = json.loads(str(row["source_paths_json"] or "[]"))
    payload["parameter_values"] = json.loads(str(row["parameter_values_json"] or "{}"))
    payload["output_policy"] = json.loads(str(row["output_policy_json"] or "{}"))
    payload["enabled"] = bool(row["enabled"])
    payload["recursive"] = bool(row["recursive"])
    payload["full_length"] = bool(row["full_length"])
    for key in ("source_paths_json", "parameter_values_json", "output_policy_json"):
        payload.pop(key, None)
    return payload


def _template_for_apply(template_id: int) -> Any:
    ensure_default_studio_templates()
    row = db.get_studio_template(int(template_id))
    if row is None:
        raise FileNotFoundError("Studio template не найден.")
    if row["deleted_at"] is not None:
        raise ValueError("Studio template удалён/скрыт и не может использоваться.")
    if str(row["status"] or "").lower() == "archived":
        raise ValueError("Studio template архивирован и не может использоваться.")
    definition = normalize_template_definition(json.loads(str(row["definition_json"])))
    require_remotion_adapter(definition)
    return row, definition


def _create_apply_batch(
    template_id: int,
    req: StudioApplyRequest,
    *,
    request: Request,
    source_mode_override: str | None = None,
) -> dict[str, Any]:
    template, definition = _template_for_apply(template_id)
    renderer_engine = normalize_render_engine(req.renderer_engine)
    profile = get_render_profile(req.render_profile)
    start_offset_sec = normalize_start_offset(req.start_offset_sec)
    duration_limit_sec = normalize_duration_limit(
        req.duration_limit_sec,
        profile=profile,
        full_length=req.full_length,
    )
    allowed_sections = definition.get("slots", {}).get("main", {}).get(
        "allowed_sections",
        ["sources", "cuts", "prepared"],
    )
    source_mode = str(source_mode_override or req.source_mode or "selected")
    if source_mode == "folder" and req.recursive:
        batch_source_mode = "folder_recursive"
    else:
        batch_source_mode = source_mode
    media_paths = collect_apply_media_paths(
        source_mode=batch_source_mode,
        source_paths=req.source_paths,
        source_path=req.source_path,
        recursive=req.recursive,
        allowed_sections=allowed_sections,
    )
    name = str(req.name or "").strip() or f"{template['name']} batch"
    batch_id = db.create_remotion_render_batch(
        studio_template_id=int(template["id"]),
        template_key=str(template["template_key"]),
        name=name,
        source_mode=batch_source_mode,
        source_path=req.source_path,
        reaction_strategy=req.reaction_strategy,
        reaction_asset_id=req.reaction_asset_id,
        reaction_pool_id=req.reaction_pool_id,
        parameter_values_json=req.parameter_values,
        renderer_engine=renderer_engine,
        render_profile=profile.key,
        duration_limit_sec=duration_limit_sec,
        start_offset_sec=start_offset_sec,
        full_length=req.full_length,
    )
    created_jobs: list[dict[str, Any]] = []
    for main_workspace_path in media_paths:
        reaction_asset_id = choose_reaction_asset(
            reaction_strategy=req.reaction_strategy,
            reaction_asset_id=req.reaction_asset_id,
            reaction_pool_id=req.reaction_pool_id,
        )
        recipe = parameterized_recipe_from_template(
            definition,
            main_workspace_path=main_workspace_path,
            reaction_asset_id=reaction_asset_id,
            parameter_values=req.parameter_values,
            studio_template_id=int(template["id"]),
            template_version=int(template["version"]),
        )
        resolved_studio_recipe(
            recipe,
            base_url=_base_url(request),
            require_reaction=True,
            render_profile=profile.key,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=req.full_length,
        )
        project_id = db.create_studio_project(
            workspace_item_key=None,
            main_workspace_path=main_workspace_path,
            template_key=str(template["template_key"]),
            reaction_asset_id=reaction_asset_id,
            recipe_json=recipe,
            studio_template_id=int(template["id"]),
            reaction_pool_id=req.reaction_pool_id,
        )
        job_id = db.create_remotion_render_job(
            project_id,
            renderer_engine=renderer_engine,
            render_profile=profile.key,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=req.full_length,
        )
        _temp_path, final_path = build_batch_remotion_output_paths(
            main_workspace_path,
            str(template["template_key"]),
            job_id,
        )
        db.update_remotion_render_job_output(job_id, str(final_path))
        db.create_remotion_render_batch_item(
            batch_id=batch_id,
            studio_project_id=project_id,
            render_job_id=job_id,
            main_workspace_path=main_workspace_path,
        )
        job = db.get_remotion_render_job(job_id)
        if job is not None:
            created_jobs.append(_render_job_payload(job))
    db.sync_remotion_render_batch(batch_id)
    queue = None
    if req.start:
        queue = start_remotion_render_queue(_base_url(request))
    batch = db.get_remotion_render_batch(batch_id)
    assert batch is not None
    return {
        "batch": _batch_payload(batch, include_items=True),
        "jobs": created_jobs,
        "queue": queue,
    }


def _parse_byte_range(value: str, size: int) -> tuple[int, int]:
    text = str(value or "").strip()
    if not text.startswith("bytes=") or "," in text:
        raise ValueError("Некорректный HTTP Range.")
    spec = text[6:].strip()
    if "-" not in spec:
        raise ValueError("Некорректный HTTP Range.")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        try:
            suffix = int(end_text)
        except ValueError as exc:
            raise ValueError("Некорректный HTTP Range.") from exc
        if suffix <= 0:
            raise ValueError("Некорректный HTTP Range.")
        start = max(0, size - suffix)
        end = size - 1
    else:
        try:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        except ValueError as exc:
            raise ValueError("Некорректный HTTP Range.") from exc
        if start < 0 or end < start:
            raise ValueError("Некорректный HTTP Range.")
        end = min(end, size - 1)
    if size <= 0 or start >= size:
        raise ValueError("HTTP Range находится вне файла.")
    return start, end


def _range_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _media_response(path: Path, request: Request) -> Response:
    size = int(path.stat().st_size)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    common_headers = {
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
    }
    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            headers=common_headers,
            content_disposition_type="inline",
        )
    try:
        start, end = _parse_byte_range(range_header, size)
    except ValueError:
        return Response(
            status_code=416,
            headers={
                **common_headers,
                "Content-Range": f"bytes */{size}",
            },
        )
    length = end - start + 1
    return StreamingResponse(
        _range_chunks(path, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


@router.get("/media-items")
def studio_media_items() -> dict[str, Any]:
    try:
        db.init_db()
        return {"sections": list_studio_media_items()}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/apply/sources")
def studio_apply_sources() -> dict[str, Any]:
    try:
        db.init_db()
        return list_studio_apply_sources()
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/media")
def studio_media(path: str, request: Request) -> Response:
    try:
        db.init_db()
        return _media_response(resolve_studio_media_path(path), request)
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/reactions")
def studio_reactions() -> dict[str, Any]:
    db.init_db()
    items: list[dict[str, Any]] = []
    for row in db.list_reaction_assets(enabled=True):
        payload = {key: row[key] for key in row.keys()}
        try:
            _asset, path = resolve_reaction_media_path(int(row["id"]))
            payload["available"] = True
            payload["file_name"] = path.name
            payload["url"] = f"/api/studio/reaction-media/{int(row['id'])}"
        except (ValueError, PermissionError, FileNotFoundError) as exc:
            payload["available"] = False
            payload["unavailable_reason"] = str(exc)
        items.append(payload)
    return {"items": items}


@router.get("/reaction-pools")
def studio_reaction_pools() -> dict[str, Any]:
    db.init_db()
    pools: list[dict[str, Any]] = []
    for pool in db.list_reaction_pools(enabled=True):
        items: list[dict[str, Any]] = []
        for row in db.list_reaction_pool_items_with_assets(int(pool["id"])):
            if not bool(row["enabled"]) or not bool(row["asset_enabled"]):
                continue
            try:
                asset, _path = resolve_reaction_media_path(
                    int(row["reaction_asset_id"])
                )
            except (ValueError, PermissionError, FileNotFoundError):
                continue
            items.append({
                "asset_id": int(asset["id"]),
                "name": str(asset["name"]),
                "weight": int(row["weight"]),
            })
        pools.append({
            "id": int(pool["id"]),
            "name": str(pool["name"]),
            "description": pool["description"],
            "items": items,
        })
    return {"items": pools}


@router.get("/reaction-media/{asset_id}")
def studio_reaction_media(asset_id: int, request: Request) -> Response:
    try:
        db.init_db()
        _asset, path = resolve_reaction_media_path(asset_id)
        return _media_response(path, request)
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/render-profiles")
def studio_render_profiles() -> dict[str, Any]:
    return render_profiles_payload()


@router.get("/templates")
def studio_templates(
    include_deleted: bool = False,
    status: str | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    db.init_db()
    ensure_default_studio_templates()
    resolved_status = status if status and status != "all" else None
    return {
        "items": [
            template_row_payload(row)
            for row in db.list_studio_templates(
                include_deleted=include_deleted,
                status=resolved_status,
            )
        ]
    }


@router.get("/templates/{template_identifier}")
def studio_template(template_identifier: str) -> dict[str, Any]:
    db.init_db()
    ensure_default_studio_templates()
    row = (
        db.get_studio_template(int(template_identifier))
        if template_identifier.isdigit()
        else db.get_latest_studio_template_by_key(template_identifier)
    )
    if row is None:
        raise _fail(ValueError("Studio template не найден."), 404)
    return {"item": template_row_payload(row)}


@router.patch("/templates/{template_id}")
def studio_template_update(
    template_id: int,
    req: StudioTemplateRequest,
) -> dict[str, Any]:
    try:
        db.init_db()
        row = db.get_studio_template(template_id)
        if row is None:
            raise FileNotFoundError("Studio template не найден.")
        status = str(req.status or "").strip().lower()
        if status not in TEMPLATE_STATUSES:
            raise ValueError("Template status должен быть draft, active или archived.")
        definition = normalize_template_definition(req.definition)
        if definition["key"] != str(row["template_key"]):
            raise ValueError("Template key нельзя менять внутри существующей версии.")
        definition["name"] = str(req.name or "").strip()
        if not definition["name"]:
            raise ValueError("Template name обязателен.")
        db.update_studio_template(
            template_id,
            name=definition["name"],
            status=status,
            definition_json=definition,
        )
        updated = db.get_studio_template(template_id)
        assert updated is not None
        return {"item": template_row_payload(updated)}
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/duplicate")
def studio_template_duplicate(template_id: int) -> dict[str, Any]:
    try:
        db.init_db()
        row = db.get_studio_template(template_id)
        if row is None:
            raise FileNotFoundError("Studio template не найден.")
        definition = normalize_template_definition(
            json.loads(str(row["definition_json"]))
        )
        duplicate_key = unique_duplicate_key(str(row["template_key"]))
        definition["key"] = duplicate_key
        definition["name"] = f"{row['name']} Copy"
        new_id = db.create_studio_template(
            template_key=duplicate_key,
            name=definition["name"],
            engine=str(row["engine"]),
            version=1,
            status="draft",
            definition_json=definition,
        )
        created = db.get_studio_template(new_id)
        assert created is not None
        return {"item": template_row_payload(created)}
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/versions")
def studio_template_create_version(
    template_id: int,
    req: StudioTemplateRequest,
) -> dict[str, Any]:
    try:
        db.init_db()
        row = db.get_studio_template(template_id)
        if row is None:
            raise FileNotFoundError("Studio template не найден.")
        status = str(req.status or "draft").strip().lower()
        if status not in TEMPLATE_STATUSES:
            raise ValueError("Template status должен быть draft, active или archived.")
        definition = normalize_template_definition(req.definition)
        definition["key"] = str(row["template_key"])
        definition["name"] = str(req.name or "").strip()
        version = db.next_studio_template_version(str(row["template_key"]))
        new_id = db.create_studio_template(
            template_key=str(row["template_key"]),
            name=definition["name"],
            engine=str(row["engine"]),
            version=version,
            status=status,
            definition_json=definition,
        )
        created = db.get_studio_template(new_id)
        assert created is not None
        return {"item": template_row_payload(created)}
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/templates/{template_id}")
def studio_template_delete(template_id: int) -> dict[str, Any]:
    try:
        db.init_db()
        result = db.delete_studio_template_safe(
            template_id,
            seeded_keys=SEEDED_STUDIO_TEMPLATE_KEYS,
        )
        row = db.get_studio_template(template_id)
        return {
            "status": "ok",
            **result,
            "item": template_row_payload(row) if row is not None else None,
        }
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/restore")
def studio_template_restore(template_id: int) -> dict[str, Any]:
    try:
        db.init_db()
        if not db.restore_studio_template(template_id):
            raise FileNotFoundError("Studio template не найден.")
        row = db.get_studio_template(template_id)
        assert row is not None
        return {
            "status": "ok",
            "item": template_row_payload(row),
            "usage": db.studio_template_usage_counts(template_id),
        }
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/apply", status_code=202)
def studio_template_apply(
    template_id: int,
    req: StudioApplyRequest,
    request: Request,
) -> JSONResponse:
    try:
        db.init_db()
        return JSONResponse(
            _create_apply_batch(template_id, req, request=request),
            status_code=202,
        )
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/render-batches")
def studio_render_batches(limit: int = 100) -> dict[str, Any]:
    db.init_db()
    return {
        "items": [
            _batch_payload(row)
            for row in db.list_remotion_render_batches(limit=limit)
        ]
    }


@router.get("/render-batches/{batch_id}")
def studio_render_batch(batch_id: int) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_batch(batch_id)
    if row is None:
        raise _fail(FileNotFoundError("Render batch не найден."), 404)
    return {"batch": _batch_payload(row, include_items=True)}


@router.post("/render-batches/{batch_id}/start")
def studio_render_batch_start(batch_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_batch(batch_id)
    if row is None:
        raise _fail(FileNotFoundError("Render batch не найден."), 404)
    queue = start_remotion_render_queue(_base_url(request))
    updated = db.get_remotion_render_batch(batch_id)
    assert updated is not None
    return {"batch": _batch_payload(updated, include_items=True), "queue": queue}


@router.post("/render-batches/{batch_id}/cancel")
def studio_render_batch_cancel(batch_id: int) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_batch(batch_id)
    if row is None:
        raise _fail(FileNotFoundError("Render batch не найден."), 404)
    cancelled = db.cancel_remotion_render_batch(batch_id)
    updated = db.get_remotion_render_batch(batch_id)
    assert updated is not None
    return {
        "cancelled": cancelled,
        "batch": _batch_payload(updated, include_items=True),
    }


@router.post("/render-batches/{batch_id}/retry-failed")
def studio_render_batch_retry_failed(batch_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_batch(batch_id)
    if row is None:
        raise _fail(FileNotFoundError("Render batch не найден."), 404)
    retried = db.retry_failed_remotion_render_batch(batch_id)
    queue = start_remotion_render_queue(_base_url(request)) if retried else None
    updated = db.get_remotion_render_batch(batch_id)
    assert updated is not None
    return {
        "retried": retried,
        "batch": _batch_payload(updated, include_items=True),
        "queue": queue,
    }


@router.get("/render-queue/status")
def studio_render_queue_status() -> dict[str, Any]:
    db.init_db()
    return {"queue": remotion_render_queue_status()}


@router.post("/render-queue/recover")
def studio_render_queue_recover() -> dict[str, Any]:
    db.init_db()
    return recover_remotion_render_queue()


@router.get("/render-jobs/completed")
def studio_completed_render_jobs(limit: int = 5) -> dict[str, Any]:
    db.init_db()
    safe_limit = max(1, min(25, int(limit)))
    return {
        "items": [
            _completed_render_payload(row)
            for row in db.list_completed_remotion_render_jobs(limit=safe_limit)
        ]
    }


@router.get("/pipelines")
def studio_pipelines() -> dict[str, Any]:
    db.init_db()
    return {
        "items": [
            _pipeline_payload(row)
            for row in db.list_remotion_pipelines()
        ]
    }


@router.post("/pipelines")
def studio_pipeline_create(req: StudioPipelineRequest) -> dict[str, Any]:
    try:
        db.init_db()
        _template_for_apply(req.studio_template_id)
        renderer_engine = normalize_render_engine(req.renderer_engine)
        profile = get_render_profile(req.render_profile)
        start_offset_sec = normalize_start_offset(req.start_offset_sec)
        duration_limit_sec = normalize_duration_limit(
            req.duration_limit_sec,
            profile=profile,
            full_length=req.full_length,
        )
        pipeline_id = db.create_remotion_pipeline(
            name=req.name.strip(),
            studio_template_id=req.studio_template_id,
            source_mode=req.source_mode,
            source_path=req.source_path,
            source_paths_json=req.source_paths,
            recursive=req.recursive,
            reaction_strategy=req.reaction_strategy,
            reaction_asset_id=req.reaction_asset_id,
            reaction_pool_id=req.reaction_pool_id,
            parameter_values_json=req.parameter_values,
            output_policy_json={"folder": "workspace_root/edits"},
            enabled=req.enabled,
            renderer_engine=renderer_engine,
            render_profile=profile.key,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=req.full_length,
        )
        row = db.get_remotion_pipeline(pipeline_id)
        assert row is not None
        return {"item": _pipeline_payload(row)}
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/pipelines/{pipeline_id}/run", status_code=202)
def studio_pipeline_run(pipeline_id: int, request: Request) -> JSONResponse:
    try:
        db.init_db()
        row = db.get_remotion_pipeline(pipeline_id)
        if row is None:
            raise FileNotFoundError("Pipeline не найден.")
        payload = _pipeline_payload(row)
        if not payload["enabled"]:
            raise ValueError("Pipeline отключён.")
        apply_req = StudioApplyRequest(
            name=f"{payload['name']} run",
            source_mode=payload["source_mode"],
            source_paths=payload["source_paths"],
            source_path=payload["source_path"],
            recursive=payload["recursive"],
            reaction_strategy=payload["reaction_strategy"],
            reaction_asset_id=payload["reaction_asset_id"],
            reaction_pool_id=payload["reaction_pool_id"],
            parameter_values=payload["parameter_values"],
            renderer_engine=payload["renderer_engine"],
            render_profile=payload["render_profile"],
            duration_limit_sec=payload["duration_limit_sec"],
            start_offset_sec=payload["start_offset_sec"],
            full_length=payload["full_length"],
            start=True,
        )
        result = _create_apply_batch(
            int(payload["studio_template_id"]),
            apply_req,
            request=request,
            source_mode_override=payload["source_mode"],
        )
        db.update_remotion_pipeline_last_batch(pipeline_id, int(result["batch"]["id"]))
        return JSONResponse(result, status_code=202)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/projects")
def studio_project_create(
    req: StudioProjectRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        db.init_db()
        recipe = normalize_studio_recipe(req.recipe_json)
        resolved_studio_recipe(
            recipe,
            base_url=_base_url(request),
            require_reaction=False,
        )
        main_path, template_key, reaction_id = _project_columns(recipe)
        project_id = db.create_studio_project(
            workspace_item_key=req.workspace_item_key,
            main_workspace_path=main_path,
            template_key=template_key,
            reaction_asset_id=reaction_id,
            recipe_json=recipe,
            studio_template_id=req.studio_template_id,
            reaction_pool_id=req.reaction_pool_id,
        )
        row = db.get_studio_project(project_id)
        assert row is not None
        return {"item": studio_project_payload(row, base_url=_base_url(request))}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/projects/{project_id}")
def studio_project_get(project_id: int, request: Request) -> dict[str, Any]:
    try:
        db.init_db()
        row = db.get_studio_project(project_id)
        if row is None:
            raise FileNotFoundError("Studio project не найден.")
        return {"item": studio_project_payload(row, base_url=_base_url(request))}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/projects/{project_id}")
def studio_project_update(
    project_id: int,
    req: StudioProjectRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        db.init_db()
        if db.get_studio_project(project_id) is None:
            raise FileNotFoundError("Studio project не найден.")
        recipe = normalize_studio_recipe(req.recipe_json)
        resolved_studio_recipe(
            recipe,
            base_url=_base_url(request),
            require_reaction=False,
        )
        main_path, template_key, reaction_id = _project_columns(recipe)
        db.update_studio_project(
            project_id,
            workspace_item_key=req.workspace_item_key,
            main_workspace_path=main_path,
            template_key=template_key,
            reaction_asset_id=reaction_id,
            recipe_json=recipe,
            studio_template_id=req.studio_template_id,
            reaction_pool_id=req.reaction_pool_id,
        )
        row = db.get_studio_project(project_id)
        assert row is not None
        return {"item": studio_project_payload(row, base_url=_base_url(request))}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/projects/{project_id}/render", status_code=202)
def studio_project_render(project_id: int, request: Request) -> JSONResponse:
    job_id: int | None = None
    try:
        db.init_db()
        project = db.get_studio_project(project_id)
        if project is None:
            raise FileNotFoundError("Studio project не найден.")
        recipe = json.loads(str(project["recipe_json"]))
        resolved_studio_recipe(
            recipe,
            base_url=_base_url(request),
            require_reaction=True,
        )
        job_id = db.create_remotion_render_job(project_id)
        _temp_path, final_path = build_remotion_output_paths(
            str(project["main_workspace_path"]),
            project_id,
            job_id,
        )
        db.update_remotion_render_job_output(job_id, str(final_path))
        queue = start_remotion_render_queue(_base_url(request))
        row = db.get_remotion_render_job(job_id)
        assert row is not None
        return JSONResponse(
            {"job": _render_job_payload(row), "queue": queue},
            status_code=202,
        )
    except sqlite3.IntegrityError:
        raise _fail(
            RuntimeError("Другой Remotion render уже находится в работе."),
            409,
        )
    except PermissionError as exc:
        if job_id is not None:
            db.mark_remotion_render_job_failed(job_id, str(exc))
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        if job_id is not None:
            db.mark_remotion_render_job_failed(job_id, str(exc))
        raise _fail(exc, 404)
    except Exception as exc:
        if job_id is not None:
            db.mark_remotion_render_job_failed(job_id, str(exc))
        raise _fail(exc)


@router.get("/render-jobs/{job_id}")
def studio_render_job(job_id: int) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_job(job_id)
    if row is None:
        raise _fail(FileNotFoundError("Remotion render job не найден."), 404)
    return {"job": _render_job_payload(row)}


@router.post("/render-jobs/{job_id}/retry")
def studio_render_job_retry(job_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = db.get_remotion_render_job(job_id)
    if row is None:
        raise _fail(FileNotFoundError("Remotion render job не найден."), 404)
    retried = db.retry_remotion_render_job(job_id)
    queue = start_remotion_render_queue(_base_url(request)) if retried else None
    updated = db.get_remotion_render_job(job_id)
    assert updated is not None
    return {
        "retried": retried,
        "job": _render_job_payload(updated),
        "queue": queue,
    }


@router.get("/render-jobs/{job_id}/media")
def studio_render_job_media(job_id: int, request: Request) -> Response:
    try:
        db.init_db()
        row = db.get_remotion_render_job(job_id)
        if row is None:
            raise FileNotFoundError("Remotion render job не найден.")
        if str(row["status"]) != "done":
            raise ValueError("Remotion render ещё не готов.")
        raw_path = str(row["output_path"] or "").strip()
        if not raw_path:
            raise FileNotFoundError("У Remotion render отсутствует output path.")
        path = Path(raw_path).expanduser().resolve()
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        try:
            path.relative_to((root / "edits").resolve())
        except ValueError as exc:
            raise PermissionError(
                "Remotion output должен находиться внутри workspace_root/edits."
            ) from exc
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"Remotion output file не найден: {path}")
        return _media_response(path, request)
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)
