"""FFmpeg renderer for materialized template edit jobs."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import db
from .config import output_dir
from .ffmpeg_tools import probe_duration, require_binary


SUPPORTED_TEMPLATE = "reaction_top_25"
SUPPORTED_RENDERER = "ffmpeg"


def _required_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} должен быть JSON object.")
    return value


def _required_path(value: Any, name: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} не задан.")
    return Path(text).expanduser()


def _load_recipe(job: sqlite3.Row) -> dict[str, Any]:
    raw_recipe = job["recipe_json"]
    if not raw_recipe:
        raise ValueError("У edit job отсутствует recipe_json.")
    try:
        recipe = json.loads(str(raw_recipe))
    except json.JSONDecodeError as exc:
        raise ValueError(f"recipe_json содержит невалидный JSON: {exc.msg}") from exc
    return _required_dict(recipe, "recipe_json")


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_edit_job_media_path(job_id: int) -> Path:
    job = db.get_edit_job(int(job_id))
    if job is None:
        raise FileNotFoundError("Edit job не найден.")
    if str(job["status"]) != "done":
        raise ValueError("Edit job ещё не готов.")

    raw_path = job["edited_path"] or job["output_path"]
    if not raw_path:
        raise FileNotFoundError("У edit job отсутствует rendered media path.")
    media_path = Path(str(raw_path)).expanduser().resolve()
    edited_root = (output_dir() / "edited").resolve()
    if not _path_inside(media_path, edited_root):
        raise PermissionError(
            f"Rendered media должен находиться внутри {edited_root}."
        )
    if not media_path.exists() or not media_path.is_file():
        raise FileNotFoundError(f"Rendered media file не найден: {media_path}")
    return media_path


def _validated_render_paths(
    job: sqlite3.Row,
    recipe: dict[str, Any],
) -> tuple[Path, Path, Path]:
    template = _required_dict(recipe.get("template"), "recipe.template")
    workspace = _required_dict(recipe.get("workspace"), "recipe.workspace")
    reaction = _required_dict(recipe.get("reaction"), "recipe.reaction")
    output = _required_dict(recipe.get("output"), "recipe.output")

    template_key = str(template.get("key") or "").strip()
    if template_key != SUPPORTED_TEMPLATE:
        raise ValueError(f"Unsupported edit template: {template_key or 'not set'}")

    job_renderer = str(job["renderer"] or "").strip()
    recipe_renderer = str(template.get("renderer") or "").strip()
    if job_renderer != SUPPORTED_RENDERER or recipe_renderer != SUPPORTED_RENDERER:
        raise ValueError("Этап 4 поддерживает только renderer=ffmpeg.")

    main_path = _required_path(
        workspace.get("main_input_path"),
        "recipe.workspace.main_input_path",
    ).resolve()
    reaction_path = _required_path(
        reaction.get("file_path"),
        "recipe.reaction.file_path",
    ).resolve()
    output_path = _required_path(output.get("path"), "recipe.output.path").resolve()

    if not main_path.is_file():
        raise FileNotFoundError(f"Main input file не найден: {main_path}")
    if not reaction_path.is_file():
        raise FileNotFoundError(f"Reaction input file не найден: {reaction_path}")

    edited_root = (output_dir() / "edited").resolve()
    if not _path_inside(output_path, edited_root):
        raise ValueError(f"Output path должен находиться внутри {edited_root}.")
    if output_path in {main_path, reaction_path}:
        raise ValueError("Output path не может совпадать с input path.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return main_path, reaction_path, output_path


def render_reaction_top_25(
    job: sqlite3.Row,
    recipe: dict[str, Any],
) -> Path:
    main_path, reaction_path, output_path = _validated_render_paths(job, recipe)
    duration = probe_duration(main_path)
    if duration is None or duration <= 0:
        raise ValueError(f"Не удалось определить duration main input: {main_path}")

    ffmpeg = require_binary("ffmpeg")
    filter_complex = (
        "[1:v]"
        "scale=1080:480:force_original_aspect_ratio=decrease,"
        "pad=1080:480:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1[react];"
        "[0:v]"
        "scale=1080:1440:force_original_aspect_ratio=decrease,"
        "pad=1080:1440:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1[main];"
        "[react][main]vstack=inputs=2[v]"
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(main_path),
        "-stream_loop",
        "-1",
        "-i",
        str(reaction_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "0:a:0?",
        "-t",
        f"{duration:.6f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.splitlines()[-60:])
        raise RuntimeError(f"ffmpeg failed:\n{stderr_tail}")
    if not output_path.is_file():
        raise RuntimeError("ffmpeg завершился успешно, но output file не создан.")
    return output_path


def _render_claimed_job(job: sqlite3.Row) -> sqlite3.Row:
    job_id = int(job["id"])
    try:
        if str(job["status"]) != "rendering":
            raise ValueError("Edit job не захвачен для rendering.")
        recipe = _load_recipe(job)
        template = _required_dict(recipe.get("template"), "recipe.template")
        template_key = str(template.get("key") or "").strip()
        if template_key != SUPPORTED_TEMPLATE:
            raise ValueError(f"Unsupported edit template: {template_key or 'not set'}")
        output_path = render_reaction_top_25(job, recipe)
        if not db.mark_edit_job_done(job_id, str(output_path)):
            raise RuntimeError("Не удалось сохранить done status edit job.")
    except Exception as exc:
        db.mark_edit_job_failed(job_id, str(exc) or exc.__class__.__name__)
        raise

    updated = db.get_edit_job(job_id)
    if updated is None:
        raise RuntimeError("Rendered edit job не найден.")
    return updated


def render_edit_job(job_id: int, *, force: bool = False) -> sqlite3.Row:
    job = db.get_edit_job(int(job_id))
    if job is None:
        raise FileNotFoundError("Edit job не найден.")

    status = str(job["status"])
    if status == "done" and not force:
        return job
    if status == "rendering":
        raise ValueError("Edit job already rendering.")
    if status in {"failed", "cancelled"} and not force:
        raise ValueError(
            f"Edit job со status={status} можно рендерить только с force=true."
        )
    if status not in {"queued", "failed", "cancelled", "done"}:
        raise ValueError(f"Нельзя рендерить edit job со status={status}.")

    claimed = db.claim_edit_job(int(job_id), allowed_statuses=(status,))
    if claimed is None:
        raise RuntimeError("Edit job не удалось атомарно захватить для rendering.")
    return _render_claimed_job(claimed)


def run_edit_queue_once(limit: int = 1) -> list[sqlite3.Row]:
    if limit <= 0:
        raise ValueError("limit должен быть больше нуля.")

    handled: list[sqlite3.Row] = []
    for _ in range(int(limit)):
        job = db.claim_next_edit_job()
        if job is None:
            break
        try:
            handled.append(_render_claimed_job(job))
        except Exception:
            failed = db.get_edit_job(int(job["id"]))
            if failed is not None:
                handled.append(failed)
    return handled
