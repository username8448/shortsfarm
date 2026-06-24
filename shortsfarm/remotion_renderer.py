"""Background runner for Remotion Studio render jobs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import db
from .ffmpeg_tools import probe_duration
from .studio import (
    build_remotion_output_paths,
    normalize_studio_recipe,
    resolved_studio_recipe,
)
from .workspace_fs import get_workspace_root


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
RENDER_SCRIPT = FRONTEND_ROOT / "scripts" / "render-remotion.mjs"
_threads: dict[int, threading.Thread] = {}
_threads_lock = threading.Lock()


def _required_node() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js не найден. Установите Node.js для Remotion render.")
    return node


def _required_remotion_dependencies() -> None:
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
    if missing:
        raise RuntimeError(
            "Remotion dependencies не установлены: "
            + ", ".join(missing)
            + ". Выполните npm --prefix frontend install."
        )
    if not RENDER_SCRIPT.is_file():
        raise RuntimeError(f"Remotion render script не найден: {RENDER_SCRIPT}")


def _required_browser() -> str:
    configured = os.environ.get("SHORTSFARM_CHROMIUM")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError(
            f"SHORTSFARM_CHROMIUM указывает на отсутствующий файл: {path}"
        )
    for name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError(
        "Chromium/Chrome не найден. Установите Chromium или задайте "
        "SHORTSFARM_CHROMIUM."
    )


def _ensure_inside_workspace_edits(path: Path) -> None:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    edits_root = (root / "edits").resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(edits_root)
    except ValueError as exc:
        raise PermissionError(
            "Remotion output должен находиться внутри workspace_root/edits."
        ) from exc


def _stderr_tail(stderr: str, limit: int = 80) -> str:
    lines = str(stderr or "").splitlines()
    return "\n".join(lines[-limit:]).strip()


def run_remotion_render_job(job_id: int, base_url: str) -> None:
    temp_path: Path | None = None
    final_path: Path | None = None
    try:
        job = db.claim_remotion_render_job(int(job_id))
        if job is None:
            return
        project = db.get_studio_project(int(job["studio_project_id"]))
        if project is None:
            raise FileNotFoundError("Studio project не найден.")

        recipe = normalize_studio_recipe(json.loads(str(project["recipe_json"])))
        resolved_recipe = resolved_studio_recipe(recipe, base_url=base_url)
        temp_path, final_path = build_remotion_output_paths(
            str(project["main_workspace_path"]),
            int(project["id"]),
            int(job_id),
        )
        _ensure_inside_workspace_edits(temp_path)
        _ensure_inside_workspace_edits(final_path)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.unlink(missing_ok=True)

        node = _required_node()
        _required_remotion_dependencies()
        browser = _required_browser()
        payload: dict[str, Any] = {
            "recipe": resolved_recipe,
            "outputPath": str(temp_path),
            "browserExecutable": browser,
        }
        result = subprocess.run(
            [node, str(RENDER_SCRIPT)],
            cwd=str(FRONTEND_ROOT),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=7200,
        )
        if result.returncode != 0:
            details = _stderr_tail(result.stderr) or _stderr_tail(result.stdout)
            raise RuntimeError(
                "Remotion render завершился с ошибкой"
                + (f":\n{details}" if details else ".")
            )
        if temp_path.is_symlink() or not temp_path.is_file():
            raise RuntimeError("Remotion не создал временный MP4.")
        if temp_path.stat().st_size <= 0:
            raise RuntimeError("Remotion создал пустой временный MP4.")
        duration = probe_duration(temp_path)
        if duration is None or duration <= 0:
            raise RuntimeError("Временный Remotion MP4 не прошёл ffprobe validation.")

        os.replace(temp_path, final_path)
        if not db.mark_remotion_render_job_done(job_id, str(final_path)):
            raise RuntimeError("Не удалось сохранить done status Remotion job.")
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        db.mark_remotion_render_job_failed(
            int(job_id),
            str(exc) or exc.__class__.__name__,
        )
    finally:
        with _threads_lock:
            _threads.pop(int(job_id), None)


def start_remotion_render_job(job_id: int, base_url: str) -> None:
    with _threads_lock:
        existing = _threads.get(int(job_id))
        if existing is not None and existing.is_alive():
            return
        thread = threading.Thread(
            target=run_remotion_render_job,
            args=(int(job_id), str(base_url)),
            name=f"remotion-render-{int(job_id)}",
            daemon=True,
        )
        _threads[int(job_id)] = thread
        thread.start()
