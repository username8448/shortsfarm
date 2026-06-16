from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import shutil
from pathlib import Path

from . import db
from .config import output_dir
from .ffmpeg_tools import fast_cut_range, probe_duration, split_video


# ---------------------------------------------------------------------------
# Filename utilities (shared with render.py)
# ---------------------------------------------------------------------------

_WINDOWS_FORBIDDEN = r'[<>:"/\\|?*\x00-\x1F]'


def safe_filename(value: str) -> str:
    cleaned = re.sub(_WINDOWS_FORBIDDEN, "_", value)
    cleaned = cleaned.strip().strip(".")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "video")[:100]


# ---------------------------------------------------------------------------
# Video extensions
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",
}


@dataclass(frozen=True)
class FileSplitResult:
    source_path: Path
    video_id: int | None
    job_id: int | None
    duration_sec: float
    output_dir: Path
    segment_ranges: list[tuple[float, float]]
    files: list[Path]
    dry_run: bool = False


@dataclass(frozen=True)
class FolderSplitItem:
    source_path: Path
    result: FileSplitResult | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Add a video to the DB
# ---------------------------------------------------------------------------

def _validated_video_path(source_path: Path) -> Path:
    path = source_path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Video file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    return path


def add_video(source_path: Path) -> int:
    path = _validated_video_path(source_path)
    duration_sec = probe_duration(path)
    return db.add_video(
        source_path  = path,
        title        = path.stem,
        duration_sec = duration_sec,
    )


def get_or_add_video(source_path: Path, duration_sec: float | None = None) -> int:
    path = _validated_video_path(source_path)
    if duration_sec is None:
        duration_sec = probe_duration(path)
    return db.add_video(
        source_path  = path,
        title        = path.stem,
        duration_sec = duration_sec,
    )


# ---------------------------------------------------------------------------
# Split workflow (existing commands, unchanged logic)
# ---------------------------------------------------------------------------

def split_existing_video(
    video_id: int,
    segment_seconds: int = 60,
    mode: str = "fast",
    overwrite: bool = False,
) -> tuple[int, list[Path]]:
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be > 0")
    if mode not in {"fast", "exact"}:
        raise ValueError("mode must be 'fast' or 'exact'")

    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video not found: {video_id}")

    source_path = Path(str(video["source_path"]))
    if not source_path.exists():
        raise FileNotFoundError(f"Source file no longer exists: {source_path}")

    job_id = db.create_job(video_id, mode, segment_seconds)
    db.mark_job_running(job_id)

    safe_title        = safe_filename(str(video["title"]))
    video_output_dir  = output_dir() / f"{video_id:06d}_{safe_title}"
    output_pattern    = f"{safe_title}_%04d.mp4"

    try:
        if video_output_dir.exists():
            if overwrite:
                shutil.rmtree(video_output_dir)
            else:
                raise RuntimeError(
                    f"Output directory already exists: {video_output_dir}\n"
                    "Use --overwrite to replace it."
                )

        segment_files = split_video(
            input_path      = source_path,
            output_dir      = video_output_dir,
            output_pattern  = output_pattern,
            segment_seconds = segment_seconds,
            mode            = mode,
        )

        for index, seg_path in enumerate(segment_files, start=1):
            db.insert_segment(
                video_id      = video_id,
                job_id        = job_id,
                segment_index = index,
                start_sec     = float((index - 1) * segment_seconds),
                end_sec       = float(index * segment_seconds),
                path          = seg_path.resolve(),
            )

        db.mark_job_done(job_id)
        return job_id, segment_files

    except Exception as exc:
        db.mark_job_failed(job_id, str(exc))
        raise


def split_file(
    source_path: Path,
    segment_seconds: int = 60,
    mode: str = "fast",
    overwrite: bool = False,
) -> tuple[int, int, list[Path]]:
    video_id = add_video(source_path)
    job_id, segment_files = split_existing_video(
        video_id        = video_id,
        segment_seconds = segment_seconds,
        mode            = mode,
        overwrite       = overwrite,
    )
    return video_id, job_id, segment_files


# ---------------------------------------------------------------------------
# Fast split with skip ranges
# ---------------------------------------------------------------------------

_SKIP_WRAPPER_RE = re.compile(r"^skip\((.*)\)$", re.IGNORECASE)


def parse_timecode(value: str, duration_sec: float) -> float:
    token = value.strip().lower()
    if token == "start":
        return 0.0
    if token == "end":
        return float(duration_sec)

    parts = token.split(":")
    if len(parts) == 1:
        try:
            seconds = float(parts[0])
        except ValueError as exc:
            raise ValueError(f"Invalid time value: {value}") from exc
    elif len(parts) == 2:
        minutes, seconds_part = parts
        seconds = int(minutes) * 60 + float(seconds_part)
    elif len(parts) == 3:
        hours, minutes, seconds_part = parts
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds_part)
    else:
        raise ValueError(f"Invalid time value: {value}")

    if seconds < 0:
        raise ValueError(f"Time value cannot be negative: {value}")
    return seconds


def _split_skip_items(skip_specs: list[str]) -> list[str]:
    items: list[str] = []
    for spec in skip_specs:
        text = spec.strip()
        if not text:
            continue
        match = _SKIP_WRAPPER_RE.match(text)
        if match:
            text = match.group(1)
        items.extend(part.strip() for part in text.split(",") if part.strip())
    return items


def parse_skip_ranges(
    skip_specs: list[str],
    duration_sec: float,
) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for item in _split_skip_items(skip_specs):
        if "-" not in item:
            raise ValueError(f"Invalid skip range: {item}")
        start_text, end_text = item.split("-", 1)
        start = parse_timecode(start_text, duration_sec)
        end = parse_timecode(end_text, duration_sec)
        start = max(0.0, min(start, duration_sec))
        end = max(0.0, min(end, duration_sec))
        if start >= end:
            raise ValueError(f"Invalid skip range after clamping: {item}")
        ranges.append((start, end))
    return merge_ranges(ranges)


def merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def build_keep_intervals(
    duration_sec: float,
    skip_ranges: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if duration_sec <= 0:
        raise ValueError("duration_sec must be > 0")

    merged = merge_ranges(skip_ranges)

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_sec:
        keep.append((cursor, duration_sec))
    return keep


def build_segment_ranges(
    keep_intervals: list[tuple[float, float]],
    segment_seconds: int,
) -> list[tuple[float, float]]:
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be > 0")

    segments: list[tuple[float, float]] = []
    for keep_start, keep_end in keep_intervals:
        current = keep_start
        while current < keep_end:
            end = min(current + segment_seconds, keep_end)
            if end > current:
                segments.append((current, end))
            current = end
    return segments


def _format_range_tag(seconds: float) -> str:
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    sec = total % 60
    return f"{hours:02d}{minutes:02d}{sec:02d}"


def _run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _build_segment_ranges_for_source(
    source_path: Path,
    duration_sec: float,
    segment_seconds: int,
    skip_specs: list[str] | None,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be > 0")
    if duration_sec <= 0:
        raise RuntimeError(f"Could not determine duration for video: {source_path}")

    skip_ranges = parse_skip_ranges(skip_specs or [], float(duration_sec))
    keep_intervals = build_keep_intervals(float(duration_sec), skip_ranges)
    segment_ranges = build_segment_ranges(keep_intervals, segment_seconds)
    if not segment_ranges:
        raise RuntimeError("No output segments: skip ranges cover the whole video")
    return skip_ranges, segment_ranges


def split_video_file(
    source_path: Path,
    segment_seconds: int = 60,
    skip_specs: list[str] | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
    run_timestamp: str | None = None,
) -> FileSplitResult:
    """Split a video path with stream-copy cuts and optional skip ranges.

    This is the user-facing split workflow.  In dry-run mode it only probes and
    computes intervals; it does not add the video to the DB, create jobs, make
    directories, or call ffmpeg.
    """
    source = _validated_video_path(source_path)
    duration_sec = float(probe_duration(source))
    _, segment_ranges = _build_segment_ranges_for_source(
        source,
        duration_sec,
        segment_seconds,
        skip_specs,
    )

    safe_stem = safe_filename(source.stem)
    timestamp = run_timestamp or _run_timestamp()
    video_output_dir = output_dir() / "split" / safe_stem / timestamp

    if dry_run:
        return FileSplitResult(
            source_path     = source,
            video_id        = None,
            job_id          = None,
            duration_sec    = duration_sec,
            output_dir      = video_output_dir,
            segment_ranges  = segment_ranges,
            files           = [],
            dry_run         = True,
        )

    video_id = get_or_add_video(source, duration_sec=duration_sec)
    job_id = db.create_job(video_id, "fast", segment_seconds)
    db.mark_job_running(job_id)

    try:
        if video_output_dir.exists():
            if overwrite:
                shutil.rmtree(video_output_dir)
            else:
                raise RuntimeError(
                    f"Output directory already exists: {video_output_dir}\n"
                    "Use --overwrite to replace it."
                )
        video_output_dir.mkdir(parents=True, exist_ok=True)

        files: list[Path] = []
        for index, (start, end) in enumerate(segment_ranges, start=1):
            filename = (
                f"{safe_stem}_{index:04d}_"
                f"{_format_range_tag(start)}-{_format_range_tag(end)}.mp4"
            )
            output_path = video_output_dir / filename
            fast_cut_range(source, output_path, start, end)
            resolved = output_path.resolve()
            files.append(resolved)
            db.insert_segment(
                video_id      = video_id,
                job_id        = job_id,
                segment_index = index,
                start_sec     = start,
                end_sec       = end,
                path          = resolved,
            )

        db.mark_job_done(job_id)
        return FileSplitResult(
            source_path     = source,
            video_id        = video_id,
            job_id          = job_id,
            duration_sec    = duration_sec,
            output_dir      = video_output_dir,
            segment_ranges  = segment_ranges,
            files           = files,
            dry_run         = False,
        )

    except Exception as exc:
        db.mark_job_failed(job_id, str(exc))
        raise


def list_video_files(folder: Path) -> list[Path]:
    root = folder.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Folder does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Path is not a folder: {root}")
    return sorted(
        path.resolve()
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def split_video_folder(
    folder: Path,
    segment_seconds: int = 60,
    skip_specs: list[str] | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> list[FolderSplitItem]:
    files = list_video_files(folder)
    results: list[FolderSplitItem] = []
    for path in files:
        try:
            result = split_video_file(
                path,
                segment_seconds = segment_seconds,
                skip_specs      = skip_specs,
                dry_run         = dry_run,
                overwrite       = overwrite,
            )
            results.append(FolderSplitItem(source_path=path, result=result))
        except Exception as exc:
            results.append(FolderSplitItem(source_path=path, error=str(exc)))
    return results


def fast_split_video(
    video_id: int,
    segment_seconds: int = 60,
    skip_specs: list[str] | None = None,
    overwrite: bool = False,
) -> tuple[int, list[Path]]:
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be > 0")

    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video not found: {video_id}")

    source_path = Path(str(video["source_path"]))
    if not source_path.exists():
        raise FileNotFoundError(f"Source file no longer exists: {source_path}")

    duration_sec = video["duration_sec"]
    if duration_sec is None:
        duration_sec = probe_duration(source_path)
    if duration_sec is None or float(duration_sec) <= 0:
        raise RuntimeError(f"Could not determine duration for video {video_id}")
    duration = float(duration_sec)

    skip_ranges = parse_skip_ranges(skip_specs or [], duration)
    keep_intervals = build_keep_intervals(duration, skip_ranges)
    segment_ranges = build_segment_ranges(keep_intervals, segment_seconds)
    if not segment_ranges:
        raise RuntimeError("No output segments: skip ranges cover the whole video")

    job_id = db.create_job(video_id, "fast", segment_seconds)
    db.mark_job_running(job_id)

    safe_title = safe_filename(str(video["title"]))
    video_output_dir = output_dir() / f"{video_id:06d}_{safe_title}_fast_split"

    try:
        if video_output_dir.exists():
            if overwrite:
                shutil.rmtree(video_output_dir)
            else:
                raise RuntimeError(
                    f"Output directory already exists: {video_output_dir}\n"
                    "Use --overwrite to replace it."
                )
        video_output_dir.mkdir(parents=True, exist_ok=True)

        files: list[Path] = []
        for index, (start, end) in enumerate(segment_ranges, start=1):
            filename = (
                f"{safe_title}_fast_{index:04d}_"
                f"{_format_range_tag(start)}-{_format_range_tag(end)}.mp4"
            )
            output_path = video_output_dir / filename
            fast_cut_range(source_path, output_path, start, end)
            resolved = output_path.resolve()
            files.append(resolved)
            db.insert_segment(
                video_id      = video_id,
                job_id        = job_id,
                segment_index = index,
                start_sec     = start,
                end_sec       = end,
                path          = resolved,
            )

        db.mark_job_done(job_id)
        return job_id, files

    except Exception as exc:
        db.mark_job_failed(job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# Input-folder helpers (split workflow)
# ---------------------------------------------------------------------------

def list_input_videos() -> list[Path]:
    from .config import input_dir
    folder = input_dir()
    if not folder.exists():
        return []
    return sorted(
        p.resolve()
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def list_pending_input_videos() -> list[Path]:
    return [p for p in list_input_videos() if db.input_video_status(p) != "done"]


def split_first_input_video(
    segment_seconds: int = 60,
    mode: str = "fast",
    overwrite: bool = True,
) -> tuple[int, int, list[Path]]:
    files = list_pending_input_videos()
    if not files:
        raise FileNotFoundError(
            "No pending video files in input folder - all are already cut."
        )
    return split_file(files[0], segment_seconds, mode, overwrite)


def split_all_input_videos(
    segment_seconds: int = 60,
    mode: str = "fast",
    overwrite: bool = True,
) -> list[tuple[Path, int, int, list[Path]]]:
    files = list_pending_input_videos()
    if not files:
        raise FileNotFoundError(
            "No pending video files in input folder - all are already cut."
        )
    results = []
    for path in files:
        video_id, job_id, segments = split_file(path, segment_seconds, mode, overwrite)
        results.append((path, video_id, job_id, segments))
    return results


# ---------------------------------------------------------------------------
# Review workflow helpers
# ---------------------------------------------------------------------------

def open_video_for_review(
    video_id: int,
    force: bool = False,
) -> None:
    """Validate and update review_status -> 'reviewing'.

    Raises ValueError with a human-readable message on invalid transitions.
    The actual mpv launch happens in mpv_session.launch_review().
    """
    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video {video_id} not found")

    status = video["review_status"]

    if status == "inbox":
        db.update_video_review_status(video_id, "reviewing")

    elif status == "reviewing":
        raise ValueError(
            f"Video {video_id} is already being reviewed.\n"
            "Use:  shortsfarm review reset <video_id>  to reset it."
        )

    elif status in ("reviewed", "skipped", "failed"):
        if not force:
            raise ValueError(
                f"Video {video_id} has status '{status}'.\n"
                "Use --force to re-review it."
            )
        db.update_video_review_status(video_id, "reviewing")

    else:
        raise ValueError(f"Unexpected review_status '{status}' for video {video_id}")


def reset_video_review(video_id: int) -> int:
    """Abandon open sessions and return video to inbox.  Returns # sessions abandoned."""
    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video {video_id} not found")
    count = db.abandon_open_sessions(video_id)
    db.update_video_review_status(video_id, "inbox")
    return count
