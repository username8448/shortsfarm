"""Workspace video preparation before YouTube publishing."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import db
from .config import output_dir
from .ffmpeg_tools import require_binary
from .services import safe_filename
from .workspace_fs import (
    build_prepared_output_dir,
    workspace_source_relative_path,
)


TARGET_SPECS = {
    "16x9": ("1920", "1080"),
    "9x16": ("1080", "1920"),
}


def _normalize_target_aspect(value: str | None) -> str:
    aspect = str(value or "original").strip().lower().replace(":", "x")
    if aspect not in {"original", *TARGET_SPECS.keys()}:
        raise ValueError("Формат видео должен быть original, 16x9 или 9x16.")
    return aspect


def _prepared_output_path(item: dict, target_aspect: str) -> Path:
    source = Path(str(item["path"]))
    stem = safe_filename(source.stem or f"{item['item_type']}_{item['item_id']}")
    source_relative = workspace_source_relative_path(
        str(item.get("source_path") or "")
    )
    folder = (
        build_prepared_output_dir(source_relative, target_aspect)
        if source_relative is not None
        else output_dir() / "prepared" / target_aspect
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{item['item_type']}_{int(item['item_id']):06d}_{stem}_{target_aspect}.mp4"


def _ffmpeg_prepare(input_path: Path, output_path: Path, target_aspect: str) -> None:
    width, height = TARGET_SPECS[target_aspect]
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    ffmpeg = require_binary("ffmpeg")
    cmd = [
        ffmpeg, "-hide_banner", "-y",
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-sn", "-dn",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-40:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")


def prepare_workspace_video(item_type: str, item_id: int, target_aspect: str) -> Path:
    """Prepare a workspace item and return the path that should be published."""
    aspect = _normalize_target_aspect(target_aspect)
    item = db.get_workspace_item(item_type, item_id)
    if item is None:
        raise FileNotFoundError("Элемент рабочего пространства не найден.")
    source = Path(str(item.get("path") or "")).expanduser()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Файл для подготовки не найден: {source}")

    db.set_workspace_prepare_status(
        item_type,
        item_id,
        prepare_status="processing",
        target_aspect=aspect,
        prepare_error="",
    )

    try:
        if aspect == "original":
            db.set_workspace_prepare_status(
                item_type,
                item_id,
                prepare_status="done",
                target_aspect=aspect,
                prepared_path=str(source),
                prepared_at=db.now_utc(),
                prepare_error="",
            )
            return source

        output = _prepared_output_path(item, aspect)
        if not output.exists() or not output.is_file():
            temp = output.with_suffix(".tmp.mp4")
            try:
                _ffmpeg_prepare(source, temp, aspect)
                temp.replace(output)
            finally:
                if temp.exists():
                    temp.unlink(missing_ok=True)

        db.set_workspace_prepare_status(
            item_type,
            item_id,
            prepare_status="done",
            target_aspect=aspect,
            prepared_path=str(output),
            prepared_at=db.now_utc(),
            prepare_error="",
        )
        return output
    except Exception as exc:
        db.set_workspace_prepare_status(
            item_type,
            item_id,
            prepare_status="failed",
            target_aspect=aspect,
            prepare_error=str(exc) or exc.__class__.__name__,
        )
        raise
