"""Unified ShortsFarm shorts pipeline API."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import db
from ..remotion_renderer import (
    ensure_studio_render_queue_running as _ensure_studio_render_queue_running,
    recover_studio_render_queue as _recover_studio_render_queue,
    studio_render_queue_status as _studio_render_queue_status,
)
from ..render_profiles import (
    DEFAULT_RENDER_ENGINE,
    DEFAULT_RENDER_PROFILE,
    get_render_profile,
    normalize_duration_limit,
    normalize_render_engine,
    normalize_start_offset,
)
from ..services import VIDEO_EXTENSIONS, split_video_file
from ..studio_service import (
    choose_reaction_for_template,
    create_apply_batch as service_create_apply_batch,
    get_studio_template_for_use,
    resolve_template_render_settings,
)
from ..studio import choose_reaction_asset as _choose_reaction_asset
from ..workspace_fs import (
    get_workspace_root,
    import_source_file,
    register_workspace_source,
    resolve_workspace_path,
)
from .studio_api import _base_url, _batch_payload


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
RENDER_SCRIPT = FRONTEND_ROOT / "scripts" / "render-remotion.mjs"


def choose_reaction_asset(**kwargs: Any) -> int:
    return _choose_reaction_asset(**kwargs)


ensure_remotion_render_queue_running = _ensure_studio_render_queue_running
recover_remotion_render_queue = _recover_studio_render_queue
remotion_render_queue_status = _studio_render_queue_status


def ensure_studio_render_queue_running(base_url: str) -> dict[str, Any]:
    return ensure_remotion_render_queue_running(base_url)


def recover_studio_render_queue() -> dict[str, Any]:
    return recover_remotion_render_queue()


def studio_render_queue_status() -> dict[str, Any]:
    return remotion_render_queue_status()


class ShortsPipelineRequest(BaseModel):
    source_mode: str = "workspace"  # external_file | workspace
    source_path: str | None = None
    source_paths: list[str] = Field(default_factory=list)
    import_target_folder: str = "sources"
    split_seconds: int = Field(default=60, gt=0)
    skip: list[str] = Field(default_factory=list)
    overwrite: bool = False
    studio_template_id: int
    reaction_strategy: str = "fixed_asset"
    reaction_asset_id: int | None = None
    reaction_pool_id: int | None = None
    parameter_values: dict[str, Any] = Field(default_factory=dict)
    renderer_engine: str = DEFAULT_RENDER_ENGINE
    render_profile: str = DEFAULT_RENDER_PROFILE
    duration_limit_sec: float | None = None
    start_offset_sec: float = 0
    full_length: bool = False
    tag_ids: list[int] = Field(default_factory=list)
    channel_tag_id: int | None = None


def _fail(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"message": str(exc) or exc.__class__.__name__},
    )


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _check_payload(key: str, label: str, ok: bool, message: str, *, value: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "ok": bool(ok),
        "message": message,
        "value": value,
    }


def _binary_check(name: str, label: str) -> dict[str, Any]:
    resolved = shutil.which(name)
    return _check_payload(
        name,
        label,
        bool(resolved),
        f"{label} найден." if resolved else f"{label} не найден.",
        value=resolved,
    )


def _chromium_check() -> dict[str, Any]:
    configured = os.environ.get("SHORTSFARM_CHROMIUM")
    if configured:
        path = Path(configured).expanduser()
        return _check_payload(
            "chromium",
            "Chromium/Chrome",
            path.is_file(),
            "Chromium найден через SHORTSFARM_CHROMIUM."
            if path.is_file()
            else f"SHORTSFARM_CHROMIUM указывает на отсутствующий файл: {path}",
            value=str(path),
        )
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        resolved = shutil.which(name)
        if resolved:
            return _check_payload(
                "chromium",
                "Chromium/Chrome",
                True,
                "Chromium/Chrome найден.",
                value=resolved,
            )
    return _check_payload(
        "chromium",
        "Chromium/Chrome",
        False,
        "Chromium/Chrome не найден. Установите Chromium или задайте SHORTSFARM_CHROMIUM.",
    )


def _remotion_dependencies_check() -> dict[str, Any]:
    required = (
        "remotion",
        "@remotion/player",
        "@remotion/renderer",
        "@remotion/bundler",
    )
    missing = [
        name
        for name in required
        if not (FRONTEND_ROOT / "node_modules" / name / "package.json").is_file()
    ]
    script_ok = RENDER_SCRIPT.is_file()
    ok = not missing and script_ok
    parts: list[str] = []
    if missing:
        parts.append("нет dependencies: " + ", ".join(missing))
    if not script_ok:
        parts.append(f"нет render script: {RENDER_SCRIPT}")
    return _check_payload(
        "remotion",
        "Remotion dependencies",
        ok,
        "Remotion dependencies установлены." if ok else "Remotion не готов: " + "; ".join(parts),
        value=str(FRONTEND_ROOT),
    )


def _workspace_check() -> dict[str, Any]:
    root = get_workspace_root()
    ok = root is not None and root.exists() and root.is_dir()
    return _check_payload(
        "workspace",
        "Workspace",
        ok,
        "workspace_root настроен." if ok else "workspace_root не настроен или папка недоступна.",
        value=str(root) if root is not None else None,
    )


def _pipeline_preflight(renderer_engine: str | None = None) -> dict[str, Any]:
    renderer = normalize_render_engine(renderer_engine or DEFAULT_RENDER_ENGINE)
    checks = [
        _workspace_check(),
        _binary_check("ffmpeg", "FFmpeg"),
        _binary_check("ffprobe", "FFprobe"),
    ]
    if renderer == "remotion":
        checks.extend([
            _binary_check("node", "Node.js"),
            _remotion_dependencies_check(),
            _chromium_check(),
        ])
    blocking = [item for item in checks if not item["ok"]]
    return {
        "ok": not blocking,
        "renderer_engine": renderer,
        "checks": checks,
        "blocking": blocking,
    }


def _workspace_relative(path: Path) -> str:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    return path.expanduser().resolve().relative_to(root.resolve()).as_posix()


def _validate_tags(req: ShortsPipelineRequest) -> tuple[list[int], int | None]:
    db.ensure_system_tags()
    tag_ids: list[int] = []
    for raw_id in req.tag_ids:
        tag_id = int(raw_id)
        if tag_id in tag_ids:
            continue
        row = db.get_tag(tag_id)
        if row is None or not bool(row["enabled"]):
            raise FileNotFoundError("Один или несколько тегов не найдены.")
        if str(row["kind"]) in {"channel", "status"}:
            raise ValueError(
                "Обычные теги конвейера не должны быть channel/status; channel-тег выбирается отдельно."
            )
        tag_ids.append(tag_id)
    channel_tag_id = int(req.channel_tag_id) if req.channel_tag_id else None
    if channel_tag_id is not None:
        row = db.get_tag(channel_tag_id)
        if row is None or not bool(row["enabled"]):
            raise FileNotFoundError("Channel-тег не найден.")
        if str(row["kind"]) != "channel":
            raise ValueError("channel_tag_id должен указывать на channel-тег.")
    return tag_ids, channel_tag_id


def _validate_template_and_render(req: ShortsPipelineRequest) -> tuple[Any, dict[str, Any], str, str, float | None, float]:
    template, definition = get_studio_template_for_use(req.studio_template_id)
    renderer_engine, profile, duration_limit_sec, start_offset_sec = resolve_template_render_settings(
        definition,
        renderer_engine=req.renderer_engine,
        render_profile=req.render_profile,
        duration_limit_sec=req.duration_limit_sec,
        start_offset_sec=req.start_offset_sec,
        full_length=req.full_length,
    )
    choose_reaction_for_template(
        definition,
        reaction_strategy=req.reaction_strategy,
        reaction_asset_id=req.reaction_asset_id,
        reaction_pool_id=req.reaction_pool_id,
        parameter_values=req.parameter_values,
    )
    return template, definition, renderer_engine, profile.key, duration_limit_sec, start_offset_sec


def _create_apply_batch(
    template_id: int,
    req: Any,
    *,
    request: Request,
    source_mode_override: str | None = None,
) -> dict[str, Any]:
    result = service_create_apply_batch(
        template_id=template_id,
        name=req.name,
        source_mode=req.source_mode,
        source_path=req.source_path,
        source_paths=req.source_paths,
        recursive=req.recursive,
        reaction_strategy=req.reaction_strategy,
        reaction_asset_id=req.reaction_asset_id,
        reaction_pool_id=req.reaction_pool_id,
        parameter_values=req.parameter_values,
        renderer_engine=req.renderer_engine,
        render_profile=req.render_profile,
        duration_limit_sec=req.duration_limit_sec,
        start_offset_sec=req.start_offset_sec,
        full_length=req.full_length,
        start=req.start,
        base_url=_base_url(request),
        source_mode_override=source_mode_override,
    )
    batch_row = db.get_remotion_render_batch(int(result["batch_id"]))
    if batch_row is None:
        raise RuntimeError("Studio batch создан, но не найден.")
    jobs = []
    for job_id in result["job_ids"]:
        job = db.get_remotion_render_job(int(job_id))
        if job is not None:
            jobs.append({key: job[key] for key in job.keys()})
    return {
        "batch": _batch_payload(batch_row, include_items=True),
        "jobs": jobs,
        "queue": result["queue"],
    }


def _source_paths_for_request(req: ShortsPipelineRequest, *, import_external: bool) -> list[tuple[str, Path]]:
    mode = str(req.source_mode or "workspace").strip().lower()
    if mode == "external_file":
        if not req.source_path:
            raise ValueError("Выберите внешний video-файл для импорта.")
        if import_external:
            path, _video_id = import_source_file(
                req.source_path,
                req.import_target_folder or "sources",
                mode="copy",
            )
        else:
            path = Path(req.source_path).expanduser()
            if path.is_symlink():
                raise PermissionError("Импорт symlink запрещён.")
            path = path.resolve()
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"Source video не найден: {path}")
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError("Можно импортировать только поддерживаемые video files.")
        relative = _workspace_relative(path) if import_external else str(path)
        return [(relative, path)]
    if mode != "workspace":
        raise ValueError("source_mode должен быть external_file или workspace.")
    raw_paths = req.source_paths or ([req.source_path] if req.source_path else [])
    clean_paths = [str(item or "").strip() for item in raw_paths if str(item or "").strip()]
    if not clean_paths:
        raise ValueError("Выберите хотя бы одно workspace-видео из sources/.")
    resolved: list[tuple[str, Path]] = []
    for raw in clean_paths:
        path, _video_id = register_workspace_source(raw)
        resolved.append((_workspace_relative(path), path))
    return resolved


def _plan_for_sources(req: ShortsPipelineRequest, sources: list[tuple[str, Path]]) -> dict[str, Any]:
    items = []
    total_segments = 0
    for relative, path in sources:
        result = split_video_file(
            path,
            segment_seconds=req.split_seconds,
            skip_specs=req.skip,
            dry_run=True,
            overwrite=req.overwrite,
        )
        segments_count = len(result.segment_ranges)
        total_segments += segments_count
        items.append({
            "source_path": str(path),
            "workspace_path": relative if relative.startswith("sources/") else None,
            "duration_sec": result.duration_sec,
            "segments_count": segments_count,
            "output_dir": str(result.output_dir),
            "segments": [
                {
                    "index": index,
                    "start_sec": start,
                    "end_sec": end,
                    "duration_sec": end - start,
                }
                for index, (start, end) in enumerate(result.segment_ranges, start=1)
            ],
        })
    return {
        "source_count": len(items),
        "segments_count": total_segments,
        "sources": items,
    }


def _run_tag_ids(row: Any) -> tuple[list[int], int | None]:
    tag_ids = [int(item) for item in _json_array(row["tag_ids_json"]) if int(item)]
    channel_tag_id = int(row["channel_tag_id"]) if row["channel_tag_id"] else None
    return tag_ids, channel_tag_id


def _final_output_tag_ids(row: Any) -> list[int]:
    tag_ids, channel_tag_id = _run_tag_ids(row)
    if channel_tag_id and channel_tag_id not in tag_ids:
        tag_ids.append(channel_tag_id)
    ready = db.get_tag_by_slug("status-ready")
    if ready is not None and int(ready["id"]) not in tag_ids:
        tag_ids.append(int(ready["id"]))
    return tag_ids


def _profile_matches_tags(profile: Any, rules: list[Any], tag_ids: set[int]) -> bool:
    include = {int(rule["tag_id"]) for rule in rules if str(rule["mode"]) == "include"}
    exclude = {int(rule["tag_id"]) for rule in rules if str(rule["mode"]) == "exclude"}
    if not include or exclude & tag_ids:
        return False
    if str(profile["tag_match_mode"] or "any") == "all":
        return include <= tag_ids
    return bool(include & tag_ids)


def _sync_profiles_for_outputs(output_paths: list[str], tag_ids: list[int], *, channel_tag_id: int | None) -> dict[str, Any]:
    if not channel_tag_id:
        return {"profiles": 0, "added": 0}
    tag_set = set(int(tag_id) for tag_id in tag_ids)
    profiles = db.list_local_storage_profiles(enabled=True)
    matched_profiles = 0
    added = 0
    for profile in profiles:
        rules = db.list_local_storage_profile_tag_rules(int(profile["id"]))
        if not any(int(rule["tag_id"]) == int(channel_tag_id) and str(rule["mode"]) == "include" for rule in rules):
            continue
        if not _profile_matches_tags(profile, rules, tag_set):
            continue
        matched_profiles += 1
        for workspace_path in output_paths:
            before = len(db.list_local_storage_profile_items(int(profile["id"])))
            db.add_local_storage_profile_item(
                int(profile["id"]),
                workspace_path=workspace_path,
                title=Path(workspace_path).stem,
                status="ready",
            )
            after = len(db.list_local_storage_profile_items(int(profile["id"])))
            if after > before:
                added += 1
    return {"profiles": matched_profiles, "added": added}


def _sync_pipeline_done_outputs(row: Any, batch: dict[str, Any]) -> dict[str, Any]:
    output_paths: list[str] = []
    final_tag_ids = _final_output_tag_ids(row)
    for item in batch.get("items") or []:
        render_status = str(item.get("render_status") or item.get("status") or "")
        output = str(item.get("output_workspace_path") or "").strip()
        db.update_shorts_pipeline_run_item_by_render_job(
            int(item["render_job_id"]),
            output_workspace_path=output or None,
            status=render_status or "failed",
            error=item.get("render_error") or None,
        )
        if render_status != "done" or not output:
            continue
        db.replace_workspace_tags(workspace_path=output, tag_ids=final_tag_ids)
        output_paths.append(output)
    _tag_ids, channel_tag_id = _run_tag_ids(row)
    profile_sync = _sync_profiles_for_outputs(
        output_paths,
        final_tag_ids,
        channel_tag_id=channel_tag_id,
    )
    failed_items = [
        item for item in batch.get("items") or []
        if str(item.get("render_status") or item.get("status")) == "failed"
    ]
    cancelled_items = [
        item for item in batch.get("items") or []
        if str(item.get("render_status") or item.get("status")) == "cancelled"
    ]
    return {
        "rendered": len(output_paths),
        "failed": len(failed_items),
        "cancelled": len(cancelled_items),
        "profile_sync": profile_sync,
        "batch": batch.get("progress") or {},
    }


def _refresh_run(row: Any, *, base_url: str | None = None) -> Any:
    status = str(row["status"])
    batch_id = int(row["remotion_batch_id"]) if row["remotion_batch_id"] else None
    if status != "rendering" or batch_id is None:
        return row
    batch_row = db.get_remotion_render_batch(batch_id)
    if batch_row is None:
        db.update_shorts_pipeline_run(
            int(row["id"]),
            status="failed",
            error="Remotion batch не найден.",
            finish=True,
        )
        return db.get_shorts_pipeline_run(int(row["id"])) or row
    batch = _batch_payload(batch_row, include_items=True)
    batch_status = str(batch["status"])
    if batch_status in {"queued", "running"}:
        if base_url:
            ensure_result = ensure_studio_render_queue_running(base_url)
            if ensure_result.get("recovered") or ensure_result.get("started"):
                summary = {
                    **_json_object(row["summary_json"]),
                    "render_queue": ensure_result,
                }
                db.update_shorts_pipeline_run(
                    int(row["id"]),
                    summary_json=summary,
                )
                return db.get_shorts_pipeline_run(int(row["id"])) or row
        return row
    if batch_status == "failed":
        retried = db.auto_retry_failed_remotion_render_batch(batch_id)
        if retried:
            summary = {
                **_json_object(row["summary_json"]),
                "auto_retry": {
                    "retried": retried,
                    "batch_id": batch_id,
                },
            }
            if base_url:
                summary["render_queue"] = ensure_studio_render_queue_running(base_url)
            db.update_shorts_pipeline_run(
                int(row["id"]),
                status="rendering",
                summary_json=summary,
            )
            return db.get_shorts_pipeline_run(int(row["id"])) or row
        sync_summary = _sync_pipeline_done_outputs(row, batch)
        failed = int(sync_summary["failed"])
        rendered = int(sync_summary["rendered"])
        message = (
            f"Конвейер завершён с ошибками: готово {rendered}, failed {failed}."
            if failed
            else "Конвейер завершён с ошибкой batch."
        )
        db.update_shorts_pipeline_run(
            int(row["id"]),
            status="done",
            error=message,
            summary_json={**_json_object(row["summary_json"]), **sync_summary},
            finish=True,
        )
        return db.get_shorts_pipeline_run(int(row["id"])) or row
    if batch_status == "cancelled":
        for item in batch.get("items") or []:
            db.update_shorts_pipeline_run_item_by_render_job(
                int(item["render_job_id"]),
                status=str(item.get("render_status") or "cancelled"),
                error=item.get("render_error") or None,
            )
        db.update_shorts_pipeline_run(
            int(row["id"]),
            status="cancelled",
            error=batch.get("error") or "Remotion batch был отменён.",
            summary_json={**_json_object(row["summary_json"]), "batch": batch.get("progress") or {}},
            finish=True,
        )
        return db.get_shorts_pipeline_run(int(row["id"])) or row

    db.update_shorts_pipeline_run(int(row["id"]), status="syncing_profile")
    updated_row = db.get_shorts_pipeline_run(int(row["id"])) or row
    summary = {
        **_json_object(updated_row["summary_json"]),
        **_sync_pipeline_done_outputs(updated_row, batch),
    }
    db.update_shorts_pipeline_run(
        int(row["id"]),
        status="done",
        summary_json=summary,
        finish=True,
    )
    return db.get_shorts_pipeline_run(int(row["id"])) or updated_row


def _run_payload(
    row: Any,
    *,
    include_items: bool = True,
    base_url: str | None = None,
) -> dict[str, Any]:
    row = _refresh_run(row, base_url=base_url)
    payload = _row_dict(row)
    payload["source_paths"] = _json_array(payload.pop("source_paths_json", "[]"))
    payload["skip"] = _json_array(payload.pop("skip_json", "[]"))
    payload["parameter_values"] = _json_object(payload.pop("parameter_values_json", "{}"))
    payload["tag_ids"] = _json_array(payload.pop("tag_ids_json", "[]"))
    payload["summary"] = _json_object(payload.pop("summary_json", "{}"))
    payload["overwrite"] = bool(payload["overwrite"])
    payload["full_length"] = bool(payload["full_length"])
    if payload.get("remotion_batch_id"):
        batch = db.get_remotion_render_batch(int(payload["remotion_batch_id"]))
        payload["batch"] = _batch_payload(batch, include_items=True) if batch is not None else None
    else:
        payload["batch"] = None
    if include_items:
        payload["items"] = [_row_dict(item) for item in db.list_shorts_pipeline_run_items(int(row["id"]))]
    return payload


def _run_health_notes(run: dict[str, Any] | None, queue: dict[str, Any], preflight: dict[str, Any]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    if not preflight.get("ok"):
        names = ", ".join(str(item["label"]) for item in preflight.get("blocking") or [])
        notes.append({
            "level": "error",
            "message": f"Preflight не готов: {names}.",
        })
    if run is None:
        notes.append({"level": "ok", "message": "Активного запуска конвейера нет."})
        return notes
    status = str(run.get("status") or "")
    batch = run.get("batch") or {}
    progress = batch.get("progress") or {}
    queued = int(progress.get("queued") or 0)
    rendering = int(progress.get("rendering") or 0)
    failed = int(progress.get("failed") or 0)
    done = int(progress.get("done") or 0)
    total = int(progress.get("total") or 0)
    queue_status = str(queue.get("status") or "idle")
    if queue_status == "stale":
        notes.append({
            "level": "error",
            "message": "Render queue выглядит зависшей. Нажмите «Починить зависший запуск».",
        })
    if status in {"queued", "splitting", "rendering", "syncing_profile"}:
        if queued and queue_status == "idle" and not rendering:
            notes.append({
                "level": "warn",
                "message": f"В Remotion очереди {queued} jobs, но worker не работает. Нажмите «Продолжить очередь».",
            })
        if failed:
            notes.append({
                "level": "warn",
                "message": f"Есть failed render jobs: {failed}. Их можно повторить.",
            })
        if total:
            notes.append({
                "level": "info",
                "message": f"Прогресс batch: готово {done} из {total}, в очереди {queued}, ошибок {failed}.",
            })
    elif status == "done" and (failed or run.get("error")):
        notes.append({
            "level": "warn",
            "message": f"Конвейер завершён с ошибками: готово {done} из {total}, ошибок {failed}.",
        })
    elif status == "done":
        notes.append({"level": "ok", "message": "Последний запуск конвейера завершён успешно."})
    elif status == "failed":
        notes.append({"level": "error", "message": str(run.get("error") or "Конвейер завершился ошибкой.")})
    if not notes:
        notes.append({"level": "info", "message": "Конвейер ожидает действий."})
    return notes


def _pipeline_health_payload(
    *,
    request: Request | None = None,
    include_items: bool = True,
    renderer_engine: str | None = None,
) -> dict[str, Any]:
    active_row = db.get_active_shorts_pipeline_run()
    latest_row = db.list_shorts_pipeline_runs(limit=1)
    row = active_row or (latest_row[0] if latest_row else None)
    run = _run_payload(row, include_items=include_items, base_url=None) if row is not None else None
    queue = studio_render_queue_status()
    try:
        preflight = _pipeline_preflight(renderer_engine)
    except TypeError:
        # Compatibility for tests/extensions that monkeypatch the older
        # zero-argument health helper.
        preflight = _pipeline_preflight()
    notes = _run_health_notes(run, queue, preflight)
    return {
        "ok": preflight["ok"] and not any(note["level"] == "error" for note in notes),
        "active": active_row is not None,
        "run": run,
        "queue": queue,
        "preflight": preflight,
        "notes": notes,
    }


def _get_pipeline_run_or_404(run_id: int) -> Any:
    row = db.get_shorts_pipeline_run(run_id)
    if row is None:
        raise _fail(FileNotFoundError("Запуск конвейера не найден."), 404)
    return row


def _update_run_summary(row: Any, patch: dict[str, Any]) -> None:
    summary = {**_json_object(row["summary_json"]), **patch}
    db.update_shorts_pipeline_run(int(row["id"]), summary_json=summary)


@router.post("/plan")
def shorts_pipeline_plan(req: ShortsPipelineRequest) -> dict[str, Any]:
    try:
        db.init_db()
        template, _definition, renderer_engine, render_profile, duration_limit_sec, start_offset_sec = _validate_template_and_render(req)
        tag_ids, channel_tag_id = _validate_tags(req)
        sources = _source_paths_for_request(req, import_external=False)
        plan = _plan_for_sources(req, sources)
        return {
            "valid": True,
            "plan": {
                **plan,
                "template": {
                    "id": int(template["id"]),
                    "key": str(template["template_key"]),
                    "name": str(template["name"]),
                },
                "renderer_engine": renderer_engine,
                "render_profile": render_profile,
                "duration_limit_sec": duration_limit_sec,
                "start_offset_sec": start_offset_sec,
                "tag_ids": tag_ids,
                "channel_tag_id": channel_tag_id,
                "will_sync_profiles": bool(channel_tag_id),
            },
        }
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/preflight")
def shorts_pipeline_preflight(req: ShortsPipelineRequest, request: Request) -> dict[str, Any]:
    try:
        db.init_db()
        template, definition, renderer_engine, render_profile, duration_limit_sec, start_offset_sec = _validate_template_and_render(req)
        _validate_tags(req)
        sources = _source_paths_for_request(req, import_external=False)
        basic = _pipeline_preflight(renderer_engine)
        plan = _plan_for_sources(req, sources)
        return {
            "ok": bool(basic["ok"]),
            "mode": "deep",
            "template": {
                "id": int(template["id"]),
                "key": str(template["template_key"]),
                "name": str(template["name"]),
                "definition": definition,
            },
            "renderer_engine": renderer_engine,
            "render_profile": render_profile,
            "duration_limit_sec": duration_limit_sec,
            "start_offset_sec": start_offset_sec,
            "preflight": basic,
            "plan": plan,
            "base_url": _base_url(request),
        }
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/runs", status_code=202)
def shorts_pipeline_run_create(req: ShortsPipelineRequest, request: Request) -> JSONResponse:
    run_id: int | None = None
    try:
        db.init_db()
        active = db.get_active_shorts_pipeline_run()
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"message": f"Уже есть активный запуск конвейера #{active['id']}."},
            )
        template, _definition, renderer_engine, render_profile, duration_limit_sec, start_offset_sec = _validate_template_and_render(req)
        tag_ids, channel_tag_id = _validate_tags(req)
        _source_paths_for_request(req, import_external=False)
        run_id = db.create_shorts_pipeline_run(
            source_mode=req.source_mode,
            source_path=req.source_path,
            source_paths_json=req.source_paths,
            split_seconds=req.split_seconds,
            skip_json=req.skip,
            overwrite=req.overwrite,
            studio_template_id=int(template["id"]),
            template_key=str(template["template_key"]),
            reaction_strategy=req.reaction_strategy,
            reaction_asset_id=req.reaction_asset_id,
            reaction_pool_id=req.reaction_pool_id,
            parameter_values_json=req.parameter_values,
            renderer_engine=renderer_engine,
            render_profile=render_profile,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=req.full_length,
            tag_ids_json=tag_ids,
            channel_tag_id=channel_tag_id,
            summary_json={"created": True},
        )
        db.update_shorts_pipeline_run(run_id, status="splitting")
        sources = _source_paths_for_request(req, import_external=True)
        source_paths_json = [relative for relative, _path in sources if relative.startswith("sources/")]
        if source_paths_json and req.source_mode == "external_file":
            db.update_shorts_pipeline_run(run_id, imported_source_path=source_paths_json[0])
        segment_paths: list[str] = []
        segment_source: dict[str, str] = {}
        for source_relative, source_path in sources:
            result = split_video_file(
                source_path,
                segment_seconds=req.split_seconds,
                skip_specs=req.skip,
                dry_run=False,
                overwrite=req.overwrite,
                run_timestamp=f"pipeline_{run_id}",
            )
            rows = db.list_segments(int(result.video_id), job_id=int(result.job_id)) if result.video_id and result.job_id else []
            rows_by_path = {str(Path(row["path"]).expanduser().resolve()): row for row in rows}
            for path in result.files:
                relative = _workspace_relative(path)
                row = rows_by_path.get(str(path.expanduser().resolve()))
                db.replace_workspace_tags(
                    workspace_path=relative,
                    tag_ids=tag_ids + ([channel_tag_id] if channel_tag_id else []),
                    item_type="segment" if row is not None else None,
                    item_id=int(row["id"]) if row is not None else None,
                )
                segment_paths.append(relative)
                segment_source[relative] = source_relative
        if not segment_paths:
            raise RuntimeError("Нарезка не создала сегменты.")
        apply_req = SimpleNamespace(
            name=f"Конвейер #{run_id}",
            source_mode="selected",
            source_path=None,
            source_paths=segment_paths,
            recursive=False,
            reaction_strategy=req.reaction_strategy,
            reaction_asset_id=req.reaction_asset_id,
            reaction_pool_id=req.reaction_pool_id,
            parameter_values=req.parameter_values,
            renderer_engine=renderer_engine,
            render_profile=render_profile,
            duration_limit_sec=duration_limit_sec,
            start_offset_sec=start_offset_sec,
            full_length=req.full_length,
            start=True,
        )
        result = _create_apply_batch(int(template["id"]), apply_req, request=request)
        batch = result["batch"]
        for item in batch.get("items") or []:
            segment_path = str(item["main_workspace_path"])
            db.create_shorts_pipeline_run_item(
                run_id=run_id,
                source_workspace_path=segment_source.get(segment_path),
                segment_workspace_path=segment_path,
                render_job_id=int(item["render_job_id"]),
                output_workspace_path=item.get("output_workspace_path"),
                status=str(item.get("render_status") or item.get("status") or "queued"),
            )
        db.update_shorts_pipeline_run(
            run_id,
            status="rendering",
            remotion_batch_id=int(batch["id"]),
            summary_json={
                "sources": len(sources),
                "segments": len(segment_paths),
                "render_jobs": len(batch.get("items") or []),
                "profile_sync_pending": bool(channel_tag_id),
            },
        )
        row = db.get_shorts_pipeline_run(run_id)
        assert row is not None
        return JSONResponse({"run": _run_payload(row)}, status_code=202)
    except HTTPException:
        raise
    except sqlite3.IntegrityError as exc:
        if run_id is None:
            raise _fail(RuntimeError("Другой запуск конвейера уже активен."), 409)
        db.update_shorts_pipeline_run(run_id, status="failed", error=str(exc), finish=True)
        raise _fail(exc)
    except PermissionError as exc:
        if run_id is not None:
            db.update_shorts_pipeline_run(run_id, status="failed", error=str(exc), finish=True)
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        if run_id is not None:
            db.update_shorts_pipeline_run(run_id, status="failed", error=str(exc), finish=True)
        raise _fail(exc, 404)
    except Exception as exc:
        if run_id is not None:
            db.update_shorts_pipeline_run(run_id, status="failed", error=str(exc), finish=True)
        raise _fail(exc)


@router.get("/runs")
def shorts_pipeline_runs(request: Request = None, limit: int = 30) -> dict[str, Any]:
    db.init_db()
    base_url = _base_url(request) if request is not None else None
    return {
        "items": [
            _run_payload(row, include_items=False, base_url=base_url)
            for row in db.list_shorts_pipeline_runs(limit=limit)
        ]
    }


@router.get("/runs/{run_id}")
def shorts_pipeline_run_get(run_id: int, request: Request = None) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    base_url = _base_url(request) if request is not None else None
    return {"run": _run_payload(row, base_url=base_url)}


@router.get("/health")
def shorts_pipeline_health(
    request: Request = None,
    renderer_engine: str | None = None,
) -> dict[str, Any]:
    db.init_db()
    return _pipeline_health_payload(request=request, renderer_engine=renderer_engine)


@router.post("/runs/{run_id}/continue")
def shorts_pipeline_run_continue(run_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    if str(row["status"]) in {"done", "failed", "cancelled"}:
        return {
            "run": _run_payload(row, base_url=_base_url(request)),
            "queue": studio_render_queue_status(),
            "retried": 0,
            "continued": False,
        }
    retried = 0
    if row["remotion_batch_id"]:
        batch_row = db.get_remotion_render_batch(int(row["remotion_batch_id"]))
        if batch_row is not None and str(batch_row["status"]) == "failed":
            retried = db.auto_retry_failed_remotion_render_batch(int(row["remotion_batch_id"]))
    queue = ensure_studio_render_queue_running(_base_url(request))
    updated = db.get_shorts_pipeline_run(run_id) or row
    _update_run_summary(updated, {
        "manual_continue": {
            "retried": retried,
            "queue": queue,
        }
    })
    updated = db.get_shorts_pipeline_run(run_id) or updated
    return {
        "run": _run_payload(updated),
        "queue": studio_render_queue_status(),
        "retried": retried,
        "continued": True,
    }


@router.post("/runs/{run_id}/repair")
def shorts_pipeline_run_repair(run_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    recovered = recover_studio_render_queue()
    retried = 0
    if row["remotion_batch_id"]:
        batch_row = db.get_remotion_render_batch(int(row["remotion_batch_id"]))
        if batch_row is not None and str(batch_row["status"]) == "failed":
            retried = db.auto_retry_failed_remotion_render_batch(int(row["remotion_batch_id"]))
    queue = ensure_studio_render_queue_running(_base_url(request))
    updated = db.get_shorts_pipeline_run(run_id) or row
    if str(updated["status"]) not in {"done", "failed", "cancelled"}:
        _update_run_summary(updated, {
            "manual_repair": {
                "recovered": recovered,
                "retried": retried,
                "queue": queue,
            }
        })
    updated = db.get_shorts_pipeline_run(run_id) or updated
    return {
        "run": _run_payload(updated),
        "health": _pipeline_health_payload(request=request),
        "recovered": recovered,
        "retried": retried,
        "queue": studio_render_queue_status(),
    }


@router.post("/runs/{run_id}/retry-failed")
def shorts_pipeline_run_retry_failed(run_id: int, request: Request) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    if str(row["status"]) in {"done", "failed", "cancelled"}:
        raise _fail(RuntimeError("Повтор failed jobs доступен только для активного запуска."), 409)
    if not row["remotion_batch_id"]:
        raise _fail(RuntimeError("У запуска конвейера нет Remotion batch."), 409)
    retried = db.retry_failed_remotion_render_batch(int(row["remotion_batch_id"]))
    queue = ensure_studio_render_queue_running(_base_url(request)) if retried else {
        "queue": studio_render_queue_status(),
        "recovered": None,
        "started": None,
    }
    updated = db.get_shorts_pipeline_run(run_id) or row
    _update_run_summary(updated, {
        "manual_retry_failed": {
            "retried": retried,
            "queue": queue,
        }
    })
    updated = db.get_shorts_pipeline_run(run_id) or updated
    return {
        "run": _run_payload(updated),
        "retried": retried,
        "queue": studio_render_queue_status(),
    }


@router.post("/runs/{run_id}/finish-with-errors")
def shorts_pipeline_run_finish_with_errors(run_id: int) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    if str(row["status"]) in {"done", "failed", "cancelled"}:
        return {"run": _run_payload(row), "finished": False}
    if not row["remotion_batch_id"]:
        db.update_shorts_pipeline_run(
            run_id,
            status="failed",
            error="Конвейер завершён вручную: Remotion batch ещё не был создан.",
            finish=True,
        )
        updated = db.get_shorts_pipeline_run(run_id) or row
        return {"run": _run_payload(updated), "finished": True}
    batch_id = int(row["remotion_batch_id"])
    batch_row = db.get_remotion_render_batch(batch_id)
    if batch_row is None:
        db.update_shorts_pipeline_run(
            run_id,
            status="failed",
            error="Конвейер завершён вручную: Remotion batch не найден.",
            finish=True,
        )
        updated = db.get_shorts_pipeline_run(run_id) or row
        return {"run": _run_payload(updated), "finished": True}
    batch = _batch_payload(batch_row, include_items=True)
    rendering = int((batch.get("progress") or {}).get("rendering") or 0)
    if rendering:
        raise _fail(
            RuntimeError("Сейчас есть активный render job. Сначала отмените или почините очередь."),
            409,
        )
    cancelled = db.cancel_remotion_render_batch(batch_id)
    updated_batch_row = db.get_remotion_render_batch(batch_id)
    assert updated_batch_row is not None
    updated_batch = _batch_payload(updated_batch_row, include_items=True)
    sync_summary = _sync_pipeline_done_outputs(row, updated_batch)
    rendered = int(sync_summary["rendered"])
    failed = int(sync_summary["failed"])
    cancelled_count = int(sync_summary["cancelled"])
    message = (
        f"Конвейер завершён вручную с ошибками: готово {rendered}, "
        f"failed {failed}, отменено {cancelled_count}."
    )
    db.update_shorts_pipeline_run(
        run_id,
        status="done",
        error=message,
        summary_json={
            **_json_object(row["summary_json"]),
            **sync_summary,
            "manual_finish_with_errors": {
                "cancelled": cancelled,
            },
        },
        finish=True,
    )
    updated = db.get_shorts_pipeline_run(run_id) or row
    return {"run": _run_payload(updated), "finished": True}


@router.post("/runs/{run_id}/cancel")
def shorts_pipeline_run_cancel(run_id: int) -> dict[str, Any]:
    db.init_db()
    row = _get_pipeline_run_or_404(run_id)
    if str(row["status"]) in {"done", "failed", "cancelled"}:
        return {"run": _run_payload(row)}
    if row["remotion_batch_id"]:
        db.cancel_remotion_render_batch(int(row["remotion_batch_id"]))
    for item in db.list_shorts_pipeline_run_items(run_id):
        if str(item["status"]) in {"queued", "rendering"}:
            db.update_shorts_pipeline_run_item(int(item["id"]), status="cancelled")
    db.update_shorts_pipeline_run(run_id, status="cancelled", finish=True)
    updated = db.get_shorts_pipeline_run(run_id)
    assert updated is not None
    return {"run": _run_payload(updated)}
