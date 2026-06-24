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
from ..remotion_renderer import start_remotion_render_job
from ..studio import (
    STUDIO_TEMPLATE_KEY,
    build_remotion_output_paths,
    list_studio_media_items,
    normalize_studio_recipe,
    resolve_reaction_media_path,
    resolve_studio_media_path,
    resolved_studio_recipe,
    studio_project_payload,
)
from ..workspace_fs import get_workspace_root


router = APIRouter()


class StudioProjectRequest(BaseModel):
    recipe_json: dict[str, Any]
    workspace_item_key: str | None = None


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


def _template_payload() -> dict[str, Any]:
    template = db.ensure_default_edit_templates()
    defaults = {
        "version": 1,
        "template": {"key": STUDIO_TEMPLATE_KEY, "renderer": "remotion"},
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "media": {
            "main": {"workspace_path": ""},
            "reaction": {"asset_id": None},
        },
        "layout": {
            "reaction_height": 480,
            "main_fit": "cover",
            "reaction_fit": "cover",
            "background_color": "#000000",
        },
        "audio": {
            "main_volume": 1,
            "reaction_volume": 0,
            "mute_reaction": True,
        },
        "overlays": {"top_text": "", "bottom_text": ""},
    }
    return {
        "id": int(template["id"]),
        "key": STUDIO_TEMPLATE_KEY,
        "name": str(template["name"]),
        "description": template["description"],
        "renderer": "remotion",
        "recipe_defaults": defaults,
    }


@router.get("/templates")
def studio_templates() -> dict[str, Any]:
    db.init_db()
    return {"items": [_template_payload()]}


@router.get("/templates/{template_key}")
def studio_template(template_key: str) -> dict[str, Any]:
    db.init_db()
    if template_key != STUDIO_TEMPLATE_KEY:
        raise _fail(ValueError("Studio template не найден."), 404)
    return {"item": _template_payload()}


@router.post("/projects")
def studio_project_create(
    req: StudioProjectRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        db.init_db()
        recipe = normalize_studio_recipe(req.recipe_json)
        resolved_studio_recipe(recipe, base_url=_base_url(request))
        main_path, template_key, reaction_id = _project_columns(recipe)
        project_id = db.create_studio_project(
            workspace_item_key=req.workspace_item_key,
            main_workspace_path=main_path,
            template_key=template_key,
            reaction_asset_id=reaction_id,
            recipe_json=recipe,
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
        resolved_studio_recipe(recipe, base_url=_base_url(request))
        main_path, template_key, reaction_id = _project_columns(recipe)
        db.update_studio_project(
            project_id,
            workspace_item_key=req.workspace_item_key,
            main_workspace_path=main_path,
            template_key=template_key,
            reaction_asset_id=reaction_id,
            recipe_json=recipe,
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
        resolved_studio_recipe(recipe, base_url=_base_url(request))
        job_id = db.create_remotion_render_job(project_id)
        _temp_path, final_path = build_remotion_output_paths(
            str(project["main_workspace_path"]),
            project_id,
            job_id,
        )
        db.update_remotion_render_job_output(job_id, str(final_path))
        start_remotion_render_job(job_id, _base_url(request))
        row = db.get_remotion_render_job(job_id)
        assert row is not None
        return JSONResponse(
            {"job": {key: row[key] for key in row.keys()}},
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
    payload = {key: row[key] for key in row.keys()}
    payload["media_url"] = (
        f"/api/studio/render-jobs/{job_id}/media"
        if str(row["status"]) == "done"
        else None
    )
    return {"job": payload}


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
