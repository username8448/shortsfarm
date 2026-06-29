from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def require_binary(name: str) -> str:
    path = shutil.which(name)

    if not path:
        raise RuntimeError(
            f"Required binary not found: {name}. "
            f"Install it first. On Arch Linux: sudo pacman -S ffmpeg"
        )

    return path


def probe_duration(input_path: Path) -> float | None:
    ffprobe = require_binary("ffprobe")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]

    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        return None

    value = result.stdout.strip()

    if not value or value == "N/A":
        return None

    try:
        return float(value)
    except ValueError:
        return None


def probe_media_metadata(input_path: Path) -> dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return {
            "duration_sec": probe_duration(input_path),
            "width": None,
            "height": None,
            "fps": None,
            "video_codec": None,
            "audio_codec": None,
            "has_audio": False,
            "container": input_path.suffix.lower().lstrip("."),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        payload = {}
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video = next(
        (item for item in streams if item.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (item for item in streams if item.get("codec_type") == "audio"),
        {},
    )
    fmt = payload.get("format") if isinstance(payload, dict) else {}
    fmt = fmt if isinstance(fmt, dict) else {}
    duration = fmt.get("duration") or video.get("duration")
    try:
        duration_sec = float(duration) if duration not in {None, "N/A", ""} else None
    except (TypeError, ValueError):
        duration_sec = None

    fps: float | None = None
    rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
    if "/" in rate:
        numerator, denominator = rate.split("/", 1)
        try:
            denominator_float = float(denominator)
            if denominator_float:
                fps = float(numerator) / denominator_float
        except (TypeError, ValueError):
            fps = None
    else:
        try:
            fps = float(rate) if rate else None
        except ValueError:
            fps = None

    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "duration_sec": duration_sec,
        "width": _int_or_none(video.get("width")),
        "height": _int_or_none(video.get("height")),
        "fps": round(fps, 3) if fps else None,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_audio": bool(audio),
        "container": (
            str(fmt.get("format_name") or input_path.suffix.lower().lstrip("."))
            .split(",", 1)[0]
        ),
    }


def split_video(
    input_path: Path,
    output_dir: Path,
    output_pattern: str,
    segment_seconds: int,
    mode: str,
) -> list[Path]:
    ffmpeg = require_binary("ffmpeg")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = output_dir / output_pattern

    if mode == "fast":
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-sn",
            "-dn",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(output_template),
        ]

    elif mode == "exact":
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(output_template),
        ]

    else:
        raise ValueError(f"Unknown split mode: {mode}")

    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.splitlines()[-60:])
        raise RuntimeError(f"ffmpeg failed:\n{stderr_tail}")

    files = sorted(output_dir.glob("*.mp4"))

    if not files:
        raise RuntimeError("ffmpeg finished, but no segment files were created")

    return files


def fast_cut_range(
    input_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
) -> Path:
    """Cut one range using stream copy."""
    if end_sec <= start_sec:
        raise ValueError("end_sec must be greater than start_sec")

    ffmpeg = require_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end_sec - start_sec

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        str(input_path),
        "-t",
        str(duration),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-dn",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
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

    return output_path
