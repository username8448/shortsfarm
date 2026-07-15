"""Background runner for Studio render jobs.

Despite the historical module name, this worker can run either Remotion or the
fast FFmpeg renderer.  The public function names stay stable for existing code.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from . import db
from .ffmpeg_tools import probe_duration, probe_media_metadata, require_binary
from .render_profiles import get_render_profile, normalize_render_engine
from .studio import (
    build_remotion_output_paths,
    normalize_studio_recipe,
    resolve_reaction_media_path,
    resolve_studio_media_path,
    resolved_studio_recipe,
)
from .workspace_fs import get_workspace_root


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
RENDER_SCRIPT = FRONTEND_ROOT / "scripts" / "render-remotion.mjs"
_threads: dict[int, threading.Thread] = {}
_threads_lock = threading.Lock()
_queue_thread: threading.Thread | None = None
_current_job_id: int | None = None
_last_error: str | None = None


@dataclass
class ProcessResult:
    returncode: int | None
    stdout_tail: str
    stderr_tail: str
    elapsed_sec: float
    timed_out: bool = False


class RenderProcessError(RuntimeError):
    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


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
            "Studio render output должен находиться внутри workspace_root/edits."
        ) from exc


def _tail(text: str, limit: int = 80) -> str:
    lines = str(text or "").splitlines()
    return "\n".join(lines[-limit:]).strip()


def _temp_path_for_final(final_path: Path) -> Path:
    if final_path.name.endswith(".mp4"):
        return final_path.with_name(final_path.name[:-4] + ".tmp.mp4")
    return final_path.with_name(final_path.name + ".tmp")


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _terminate_process_group(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.killpg(int(pid), signal.SIGTERM)
        time.sleep(0.5)
    except OSError:
        return
    if _process_alive(pid):
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except OSError:
            pass


def _run_process(
    job_id: int,
    command: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout_sec: int,
) -> ProcessResult:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    db.update_remotion_render_job_process(job_id, worker_pid=proc.pid)
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout_sec)
        return ProcessResult(
            returncode=proc.returncode,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            elapsed_sec=time.monotonic() - started,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        _terminate_process_group(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return ProcessResult(
            returncode=proc.returncode,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            elapsed_sec=time.monotonic() - started,
            timed_out=True,
        )


def _pipe_reader(pipe: Any, queue: Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            queue.put(str(line))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _drain_lines(queue: Queue[str], target: deque[str]) -> list[str]:
    lines: list[str] = []
    while True:
        try:
            line = queue.get_nowait()
        except Empty:
            break
        target.append(str(line).rstrip("\n"))
        lines.append(str(line))
    return lines


def _parse_key_value_line(line: str) -> tuple[str, str] | None:
    text = str(line or "").strip()
    if "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def _parse_ffmpeg_timecode(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        hours, minutes, seconds = text.split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None


def _parse_speed_multiplier(value: str | None) -> float | None:
    text = str(value or "").strip().lower()
    if not text or text in {"n/a", "nan"}:
        return None
    if text.endswith("x"):
        text = text[:-1]
    try:
        speed = float(text)
    except ValueError:
        return None
    return speed if speed > 0 else None


def _ffmpeg_progress_payload(
    values: dict[str, str],
    *,
    duration_sec: float,
    total_frames: int | None = None,
) -> dict[str, Any]:
    out_time_sec: float | None = None
    for key in ("out_time_us", "out_time_ms"):
        if key in values:
            try:
                out_time_sec = float(values[key]) / 1_000_000
                break
            except (TypeError, ValueError):
                pass
    if out_time_sec is None and "out_time" in values:
        out_time_sec = _parse_ffmpeg_timecode(values["out_time"])
    if out_time_sec is None:
        out_time_sec = 0.0
    duration = max(0.001, float(duration_sec or 0.001))
    percent = min(99.0, max(0.0, (float(out_time_sec) / duration) * 100))
    speed_text = values.get("speed")
    speed = _parse_speed_multiplier(speed_text)
    eta_sec = (
        max(0.0, (duration - float(out_time_sec)) / speed)
        if speed
        else None
    )
    current_frame: int | None = None
    if values.get("frame"):
        try:
            current_frame = int(float(values["frame"]))
        except (TypeError, ValueError):
            current_frame = None
    return {
        "progress_percent": percent,
        "progress_stage": "rendering",
        "progress_message": f"Rendering {percent:.0f}%",
        "current_frame": current_frame,
        "total_frames": total_frames,
        "out_time_sec": float(out_time_sec),
        "speed": speed_text,
        "eta_sec": eta_sec,
    }


def _make_ffmpeg_progress_handler(
    job_id: int,
    *,
    duration_sec: float,
    total_frames: int | None,
) -> Callable[[str, bool], None]:
    values: dict[str, str] = {}
    last_update = 0.0

    def handle(line: str, force: bool = False) -> None:
        nonlocal last_update
        parsed = _parse_key_value_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
        now = time.monotonic()
        should_update = (
            force
            or parsed is not None and parsed[0] in {"out_time_us", "out_time_ms", "out_time", "progress"}
        )
        if not should_update or (now - last_update < 0.5 and not force):
            return
        payload = _ffmpeg_progress_payload(
            values,
            duration_sec=duration_sec,
            total_frames=total_frames,
        )
        db.update_remotion_render_job_progress(job_id, **payload)
        last_update = now

    return handle


def _make_remotion_progress_handler(job_id: int) -> Callable[[str, bool], None]:
    last_update = 0.0

    def handle(line: str, force: bool = False) -> None:
        nonlocal last_update
        try:
            payload = json.loads(str(line or "").strip())
        except json.JSONDecodeError:
            return
        if payload.get("type") != "progress":
            return
        now = time.monotonic()
        if now - last_update < 0.5 and not force:
            return
        progress = float(payload.get("progress") or 0)
        percent = min(99.0, max(0.0, progress * 100))
        frame = payload.get("renderedFrames")
        if frame is None:
            frame = payload.get("encodedFrames")
        total = payload.get("totalFrames")
        db.update_remotion_render_job_progress(
            job_id,
            progress_percent=percent,
            progress_stage="rendering",
            progress_message="Remotion rendering",
            current_frame=int(frame) if frame is not None else None,
            total_frames=int(total) if total is not None else None,
        )
        last_update = now

    return handle


def _run_process_streaming_progress(
    job_id: int,
    command: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout_sec: int,
    progress_handler: Callable[[str, bool], None] | None = None,
) -> ProcessResult:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    db.update_remotion_render_job_process(job_id, worker_pid=proc.pid)
    if proc.stdin is not None and input_text is not None:
        try:
            proc.stdin.write(input_text)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    stdout_queue: Queue[str] = Queue()
    stderr_queue: Queue[str] = Queue()
    stdout_tail: deque[str] = deque(maxlen=80)
    stderr_tail: deque[str] = deque(maxlen=80)
    stdout_thread = threading.Thread(
        target=_pipe_reader,
        args=(proc.stdout, stdout_queue),
        name=f"studio-render-{job_id}-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_pipe_reader,
        args=(proc.stderr, stderr_queue),
        name=f"studio-render-{job_id}-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        while True:
            for line in _drain_lines(stdout_queue, stdout_tail):
                if progress_handler is not None:
                    progress_handler(line, False)
            _drain_lines(stderr_queue, stderr_tail)

            if proc.poll() is not None:
                if (
                    stdout_queue.empty()
                    and stderr_queue.empty()
                    and not stdout_thread.is_alive()
                    and not stderr_thread.is_alive()
                ):
                    break
            if time.monotonic() - started > timeout_sec:
                timed_out = True
                _terminate_process_group(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process_group(proc.pid)
                break
            time.sleep(0.05)
    finally:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        for line in _drain_lines(stdout_queue, stdout_tail):
            if progress_handler is not None:
                progress_handler(line, False)
        _drain_lines(stderr_queue, stderr_tail)
        if progress_handler is not None:
            progress_handler("", True)

    return ProcessResult(
        returncode=proc.returncode,
        stdout_tail="\n".join(stdout_tail).strip(),
        stderr_tail="\n".join(stderr_tail).strip(),
        elapsed_sec=time.monotonic() - started,
        timed_out=timed_out,
    )


def _validate_and_finalize(
    job_id: int,
    temp_path: Path,
    final_path: Path,
    result: ProcessResult,
) -> None:
    if result.returncode != 0:
        details = result.stderr_tail or result.stdout_tail
        raise RenderProcessError(
            "Render завершился с ошибкой"
            + (f":\n{details}" if details else "."),
            result,
        )
    if temp_path.is_symlink() or not temp_path.is_file():
        raise RenderProcessError("Render не создал временный MP4.", result)
    if temp_path.stat().st_size <= 0:
        raise RenderProcessError("Render создал пустой временный MP4.", result)
    duration = probe_duration(temp_path)
    if duration is None or duration <= 0:
        raise RenderProcessError(
            "Временный Studio MP4 не прошёл ffprobe validation.",
            result,
        )
    os.replace(temp_path, final_path)
    if not db.mark_remotion_render_job_done(
        job_id,
        str(final_path),
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        returncode=result.returncode,
        elapsed_sec=result.elapsed_sec,
    ):
        raise RuntimeError("Не удалось сохранить done status Studio render job.")


def _safe_color(value: str) -> str:
    text = str(value or "#000000").strip()
    return f"0x{text[1:]}" if text.startswith("#") and len(text) == 7 else "black"


def _fit_filter(
    source: str,
    target: str,
    *,
    width: int,
    height: int,
    fit: str,
    background: str,
    fps: int,
) -> str:
    if fit == "contain":
        return (
            f"{source}fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background},"
            f"setsar=1{target}"
        )
    return (
        f"{source}fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1{target}"
    )


def _escape_drawtext(value: Any) -> str:
    text = str(value or "")
    return (
        text
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _with_text_overlays(
    parts: list[str],
    source_label: str,
    recipe: dict[str, Any],
) -> str:
    overlays = recipe.get("overlays") or {}
    width = int(recipe["canvas"]["width"])
    height = int(recipe["canvas"]["height"])
    current = source_label
    font_size = max(28, int(round(width * 0.055)))
    box_border = max(12, int(round(width * 0.018)))
    specs = [
        ("top_text", f"(w-text_w)/2", str(max(24, int(round(height * 0.035))))),
        ("bottom_text", f"(w-text_w)/2", f"h-text_h-{max(34, int(round(height * 0.055)))}"),
    ]
    idx = 0
    for key, x, y in specs:
        text = str(overlays.get(key) or "").strip()
        if not text:
            continue
        target = f"[txt{idx}]"
        parts.append(
            f"{current}drawtext="
            f"text='{_escape_drawtext(text)}':"
            f"x={x}:y={y}:"
            f"fontsize={font_size}:"
            "fontcolor=white:"
            "box=1:boxcolor=black@0.55:"
            f"boxborderw={box_border}"
            f"{target}"
        )
        current = target
        idx += 1
    if current != "[v]":
        parts.append(f"{current}null[v]")
    return ";".join(parts)


def _ffmpeg_filter(recipe: dict[str, Any], *, original_canvas_height: int) -> str:
    width = int(recipe["canvas"]["width"])
    height = int(recipe["canvas"]["height"])
    fps = int(recipe["canvas"]["fps"])
    layout = recipe["layout"]
    original_height = max(1, int(original_canvas_height or 1920))
    ratio = float(layout.get("reaction_height", 480)) / original_height
    reaction_height = max(120, min(height - 120, int(round(height * ratio))))
    background = _safe_color(str(layout.get("background_color") or "#000000"))
    position = str(layout.get("reaction_position") or "top")
    main_fit = str(layout.get("main_fit") or "cover")
    reaction_fit = str(layout.get("reaction_fit") or "cover")

    if position == "none":
        parts = [_fit_filter(
            "[0:v]",
            "[basev]",
            width=width,
            height=height,
            fit=main_fit,
            background=background,
            fps=fps,
        )]
        return _with_text_overlays(parts, "[basev]", recipe)

    if position in {"top", "bottom"}:
        main_height = max(120, height - reaction_height)
        main = _fit_filter(
            "[0:v]",
            "[main]",
            width=width,
            height=main_height,
            fit=main_fit,
            background=background,
            fps=fps,
        )
        reaction = _fit_filter(
            "[1:v]",
            "[react]",
            width=width,
            height=reaction_height,
            fit=reaction_fit,
            background=background,
            fps=fps,
        )
        stack = (
            "[react][main]vstack=inputs=2[basev]"
            if position == "top"
            else "[main][react]vstack=inputs=2[basev]"
        )
        return _with_text_overlays([main, reaction, stack], "[basev]", recipe)

    pip_height = max(140, min(int(round(height * 0.42)), reaction_height))
    pip_width = max(80, int(round(pip_height * 9 / 16)))
    margin = max(16, int(round(width * 0.04)))
    pip_position = str(layout.get("pip_position") or "top_right")
    x = margin if pip_position.endswith("left") else f"W-w-{margin}"
    y = margin if pip_position.startswith("top") else f"H-h-{margin}"
    main = _fit_filter(
        "[0:v]",
        "[main]",
        width=width,
        height=height,
        fit=main_fit,
        background=background,
        fps=fps,
    )
    reaction = _fit_filter(
        "[1:v]",
        "[react]",
        width=pip_width,
        height=pip_height,
        fit=reaction_fit,
        background=background,
        fps=fps,
    )
    return _with_text_overlays([
        main,
        reaction,
        f"[main][react]overlay={x}:{y}:format=auto[basev]",
    ], "[basev]", recipe)


def _run_ffmpeg_fast(
    job_id: int,
    normalized_recipe: dict[str, Any],
    resolved_recipe: dict[str, Any],
    temp_path: Path,
) -> ProcessResult:
    ffmpeg = require_binary("ffmpeg")
    main_path = resolve_studio_media_path(
        normalized_recipe["media"]["main"]["workspace_path"]
    )
    reaction_media = normalized_recipe["media"]["reaction"]
    asset_id = reaction_media["asset_id"]
    reaction_enabled = bool(reaction_media.get("enabled", asset_id is not None))
    reaction_required = bool(reaction_media.get("required", reaction_enabled))
    reaction_path: Path | None = None
    if asset_id is not None and reaction_enabled:
        _asset, reaction_path = resolve_reaction_media_path(int(asset_id))
    elif reaction_enabled and reaction_required:
        raise ValueError("FFmpeg fast renderer требует reaction asset для этого template.")
    profile = get_render_profile(str(resolved_recipe.get("render_profile", {}).get("key")))
    trim = resolved_recipe["trim"]
    duration = float(trim["duration_sec"])
    start = float(trim["start_sec"])
    filter_complex = _ffmpeg_filter(
        resolved_recipe,
        original_canvas_height=int(normalized_recipe["canvas"]["height"]),
    )
    audio = normalized_recipe.get("audio") or {}
    main_volume = float(audio.get("main_volume", 1))
    reaction_volume = float(audio.get("reaction_volume", 0))
    mute_reaction = bool(audio.get("mute_reaction", True))
    main_has_audio = bool(probe_media_metadata(main_path).get("has_audio"))
    reaction_has_audio = (
        bool(probe_media_metadata(reaction_path).get("has_audio"))
        if reaction_path is not None and not mute_reaction and reaction_volume > 0
        else False
    )
    audio_filters: list[str] = []
    audio_map: str | None = None
    if main_has_audio and reaction_has_audio:
        audio_filters.extend([
            f"[0:a]volume={main_volume:.6f}[maina]",
            f"[1:a]volume={reaction_volume:.6f},apad[reacta]",
            (
                "[maina][reacta]amix=inputs=2:duration=longest:"
                f"dropout_transition=0,atrim=duration={duration:.6f}[a]"
            ),
        ])
        audio_map = "[a]"
    elif main_has_audio and abs(main_volume - 1.0) > 0.000001:
        audio_filters.append(f"[0:a]volume={main_volume:.6f}[a]")
        audio_map = "[a]"
    elif reaction_has_audio:
        audio_filters.append(f"[1:a]volume={reaction_volume:.6f}[a]")
        audio_map = "[a]"
    if audio_filters:
        filter_complex = ";".join([filter_complex, *audio_filters])
    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-nostats",
        "-progress",
        "pipe:1",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(main_path),
    ]
    if reaction_path is not None:
        command.extend([
            "-stream_loop",
            "-1",
            "-i",
            str(reaction_path),
        ])
    command.extend([
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        audio_map or "0:a?",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        "-preset",
        profile.preset,
        "-crf",
        str(profile.crf),
        "-r",
        str(profile.fps),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(temp_path),
    ])
    total_frames = max(1, int(round(duration * profile.fps)))
    db.update_remotion_render_job_progress(
        job_id,
        progress_percent=0,
        progress_stage="rendering",
        progress_message="FFmpeg rendering",
        total_frames=total_frames,
    )
    return _run_process_streaming_progress(
        job_id,
        command,
        timeout_sec=profile.timeout_sec,
        progress_handler=_make_ffmpeg_progress_handler(
            job_id,
            duration_sec=duration,
            total_frames=total_frames,
        ),
    )


def _run_remotion(
    job_id: int,
    resolved_recipe: dict[str, Any],
    temp_path: Path,
) -> ProcessResult:
    node = _required_node()
    _required_remotion_dependencies()
    browser = _required_browser()
    profile = get_render_profile(str(resolved_recipe.get("render_profile", {}).get("key")))
    payload: dict[str, Any] = {
        "recipe": resolved_recipe,
        "outputPath": str(temp_path),
        "browserExecutable": browser,
        "renderProfile": profile.payload(),
    }
    db.update_remotion_render_job_progress(
        job_id,
        progress_percent=0,
        progress_stage="rendering",
        progress_message="Remotion rendering",
        total_frames=int(resolved_recipe.get("duration_in_frames") or 0) or None,
    )
    return _run_process_streaming_progress(
        job_id,
        [node, str(RENDER_SCRIPT)],
        cwd=FRONTEND_ROOT,
        input_text=json.dumps(payload, ensure_ascii=False),
        timeout_sec=profile.timeout_sec,
        progress_handler=_make_remotion_progress_handler(job_id),
    )


def run_remotion_render_job(job_id: int, base_url: str) -> None:
    job = db.claim_remotion_render_job(int(job_id))
    if job is None:
        return
    _run_claimed_remotion_render_job(job, base_url)


def _run_claimed_remotion_render_job(job: Any, base_url: str) -> None:
    global _current_job_id, _last_error
    job_id = int(job["id"])
    _current_job_id = job_id
    temp_path: Path | None = None
    final_path: Path | None = None
    result: ProcessResult | None = None
    try:
        project = db.get_studio_project(int(job["studio_project_id"]))
        if project is None:
            raise FileNotFoundError("Studio project не найден.")

        renderer_engine = normalize_render_engine(str(job["renderer_engine"]))
        render_profile = str(job["render_profile"] or "low_540p")
        normalized_recipe = normalize_studio_recipe(
            json.loads(str(project["recipe_json"]))
        )
        reaction_media = normalized_recipe.get("media", {}).get("reaction", {})
        require_reaction = bool(
            reaction_media.get("enabled")
            and reaction_media.get("required")
        )
        resolved_recipe = resolved_studio_recipe(
            normalized_recipe,
            base_url=base_url,
            require_reaction=require_reaction,
            render_profile=render_profile,
            duration_limit_sec=job["duration_limit_sec"],
            start_offset_sec=float(job["start_offset_sec"] or 0),
            full_length=bool(job["full_length"]),
        )
        raw_output = str(job["output_path"] or "").strip()
        if raw_output:
            final_path = Path(raw_output).expanduser()
            temp_path = _temp_path_for_final(final_path)
        else:
            temp_path, final_path = build_remotion_output_paths(
                str(project["main_workspace_path"]),
                int(project["id"]),
                job_id,
            )
            db.update_remotion_render_job_output(job_id, str(final_path))
        _ensure_inside_workspace_edits(temp_path)
        _ensure_inside_workspace_edits(final_path)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.unlink(missing_ok=True)
        if final_path.exists():
            raise RuntimeError(f"Studio render output уже существует: {final_path}")

        if renderer_engine == "ffmpeg_fast":
            result = _run_ffmpeg_fast(
                job_id,
                normalized_recipe,
                resolved_recipe,
                temp_path,
            )
        else:
            result = _run_remotion(job_id, resolved_recipe, temp_path)
        if result.timed_out:
            raise RenderProcessError(
                f"Render timeout after {int(result.elapsed_sec)} seconds.",
                result,
            )
        _validate_and_finalize(job_id, temp_path, final_path, result)
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        process_result = exc.result if isinstance(exc, RenderProcessError) else result
        message = str(exc) or exc.__class__.__name__
        _last_error = message
        db.mark_remotion_render_job_failed(
            int(job_id),
            message,
            stdout_tail=process_result.stdout_tail if process_result else None,
            stderr_tail=process_result.stderr_tail if process_result else None,
            returncode=process_result.returncode if process_result else None,
            elapsed_sec=process_result.elapsed_sec if process_result else None,
        )
    finally:
        _current_job_id = None
        with _threads_lock:
            _threads.pop(int(job_id), None)


def _run_remotion_queue(base_url: str) -> None:
    global _queue_thread
    try:
        while True:
            job = db.claim_next_remotion_render_job()
            if job is None:
                return
            _run_claimed_remotion_render_job(job, base_url)
    finally:
        with _threads_lock:
            _queue_thread = None


def wait_for_studio_render_queue(timeout_sec: float = 10.0) -> bool:
    """Wait for the current Studio render queue thread to finish.

    This is a lifecycle helper for tests and controlled shutdown paths.  It does
    not stop a running render process; it only waits for the queue daemon thread
    that was already started by ``start_studio_render_queue``.
    """
    global _queue_thread
    with _threads_lock:
        thread = _queue_thread
    if thread is None:
        return True
    if thread is threading.current_thread():
        return not thread.is_alive()
    if not thread.is_alive():
        with _threads_lock:
            if _queue_thread is thread:
                _queue_thread = None
        return True
    thread.join(max(0.0, float(timeout_sec)))
    finished = not thread.is_alive()
    if finished:
        with _threads_lock:
            if _queue_thread is thread:
                _queue_thread = None
    return finished


def _job_timeout_sec(job: Any) -> int:
    try:
        return get_render_profile(str(job["render_profile"])).timeout_sec
    except Exception:
        return get_render_profile("low_540p").timeout_sec


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _active_job_state() -> tuple[Any | None, bool, bool]:
    job = db.get_active_remotion_render_job()
    if job is None:
        return None, False, False
    pid_alive = _process_alive(int(job["worker_pid"])) if job["worker_pid"] else False
    thread_alive = (
        _queue_thread is not None
        and _queue_thread.is_alive()
        and _current_job_id == int(job["id"])
    )
    alive = pid_alive or thread_alive
    started = _parse_time(job["worker_started_at"]) or _parse_time(job["started_at"])
    too_long = False
    if started is not None:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        too_long = elapsed > _job_timeout_sec(job)
    stale = not alive or too_long
    return job, alive, stale


def studio_render_queue_status() -> dict[str, Any]:
    job, alive, stale = _active_job_state()
    with db.connect() as con:
        queued_count = int(con.execute(
            "SELECT COUNT(*) AS count FROM remotion_render_jobs WHERE status='queued'"
        ).fetchone()["count"])
        rendering_count = int(con.execute(
            "SELECT COUNT(*) AS count FROM remotion_render_jobs WHERE status='rendering'"
        ).fetchone()["count"])
        failed_count = int(con.execute(
            "SELECT COUNT(*) AS count FROM remotion_render_jobs WHERE status='failed'"
        ).fetchone()["count"])
        last_failed = con.execute(
            """
            SELECT error
            FROM remotion_render_jobs
            WHERE status='failed' AND error IS NOT NULL
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return {
        "status": "stale" if stale else ("running" if job is not None else "idle"),
        "current_job_id": int(job["id"]) if job is not None else None,
        "worker_pid": int(job["worker_pid"]) if job is not None and job["worker_pid"] else None,
        "alive": bool(alive),
        "queued_count": queued_count,
        "rendering_count": rendering_count,
        "failed_count": failed_count,
        "last_error": _last_error or (str(last_failed["error"]) if last_failed else None),
    }


def recover_studio_render_queue() -> dict[str, Any]:
    job, alive, stale = _active_job_state()
    recovered = 0
    reason = "no rendering job"
    if job is not None and stale:
        pid = int(job["worker_pid"]) if job["worker_pid"] else None
        if pid and _process_alive(pid):
            _terminate_process_group(pid)
            reason = "stale rendering job process stopped"
        else:
            reason = "stale rendering job had no alive worker process"
        db.mark_remotion_render_job_failed(
            int(job["id"]),
            f"Render queue recovery: {reason}.",
        )
        recovered = 1
    elif job is not None and alive:
        reason = "current render job is still alive"
    return {
        "recovered": recovered,
        "reason": reason,
        "queue": studio_render_queue_status(),
    }


def start_studio_render_queue(base_url: str) -> dict[str, Any]:
    global _queue_thread
    job, alive, stale = _active_job_state()
    if stale:
        return {
            "started": False,
            "reason": "stale rendering job; run recovery",
            "current_job_id": int(job["id"]) if job is not None else None,
        }
    with _threads_lock:
        if _queue_thread is not None and _queue_thread.is_alive():
            return {
                "started": False,
                "reason": "queue already running",
                "current_job_id": _current_job_id,
            }
        with db.connect() as con:
            queued = con.execute(
                "SELECT id FROM remotion_render_jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
            ).fetchone()
        if queued is None:
            return {
                "started": False,
                "reason": "no queued render jobs",
                "current_job_id": None,
            }
        thread = threading.Thread(
            target=_run_remotion_queue,
            args=(str(base_url),),
            name="studio-render-queue",
            daemon=True,
        )
        _queue_thread = thread
        thread.start()
        return {
            "started": True,
            "reason": "started",
            "current_job_id": None,
        }


def ensure_studio_render_queue_running(base_url: str) -> dict[str, Any]:
    """Recover a stale queue if needed and start queued render jobs.

    This is intentionally idempotent and safe to call from polling endpoints:
    it starts a worker only when jobs are queued and no worker is alive.
    """
    recovered: dict[str, Any] | None = None
    queue = studio_render_queue_status()
    if queue["status"] == "stale":
        recovered = recover_studio_render_queue()
        queue = recovered["queue"]
    started: dict[str, Any] | None = None
    if (
        int(queue.get("queued_count") or 0) > 0
        and queue.get("status") == "idle"
        and int(queue.get("rendering_count") or 0) == 0
    ):
        started = start_studio_render_queue(base_url)
        queue = studio_render_queue_status()
    return {
        "queue": queue,
        "recovered": recovered,
        "started": started,
    }


# Compatibility aliases kept while older imports/tests migrate to Studio naming.
remotion_render_queue_status = studio_render_queue_status
recover_remotion_render_queue = recover_studio_render_queue
start_remotion_render_queue = start_studio_render_queue
ensure_remotion_render_queue_running = ensure_studio_render_queue_running


def start_remotion_render_job(job_id: int, base_url: str) -> None:
    with _threads_lock:
        existing = _threads.get(int(job_id))
        if existing is not None and existing.is_alive():
            return
        thread = threading.Thread(
            target=run_remotion_render_job,
            args=(int(job_id), str(base_url)),
            name=f"studio-render-{int(job_id)}",
            daemon=True,
        )
        _threads[int(job_id)] = thread
        thread.start()
