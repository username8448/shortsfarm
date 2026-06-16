"""Clip rendering queue: takes queued clips from the DB and cuts them with
ffmpeg using exact re-encoding.  Temp files prevent partial writes from
leaving corrupted output.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import db
from .config import output_dir
from .ffmpeg_tools import require_binary
from .services import safe_filename


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _clip_output_path(clip_id: int, video_title: str, mark_id: int | None) -> Path:
    clips_dir = output_dir() / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_filename(video_title)
    mark_tag = f"_m{mark_id:06d}" if mark_id is not None else ""
    return clips_dir / f"clip_{clip_id:06d}_{safe}{mark_tag}.mp4"


# ---------------------------------------------------------------------------
# Single-clip render
# ---------------------------------------------------------------------------

def render_clip(clip_id: int) -> Path:
    """Render one queued clip.  Returns the output path on success."""
    clip = db.get_clip(clip_id)
    if clip is None:
        raise ValueError(f"Clip {clip_id} not found")
    if clip["status"] != "queued":
        raise ValueError(
            f"Clip {clip_id} is '{clip['status']}', expected 'queued'"
        )

    temp: Path | None = None

    try:
        video = db.get_video(int(clip["video_id"]))
        if video is None:
            raise ValueError(f"Video {clip['video_id']} not found")

        # Fetch the mark for in/out times
        with db.connect() as con:
            mark = con.execute(
                "SELECT * FROM marks WHERE id=?", (clip["mark_id"],)
            ).fetchone()

        if mark is None:
            raise ValueError(f"Mark {clip['mark_id']} not found for clip {clip_id}")

        source = Path(str(video["source_path"]))
        if not source.exists():
            raise FileNotFoundError(f"Source file gone: {source}")

        output = _clip_output_path(clip_id, str(video["title"]), clip["mark_id"])
        temp = output.with_suffix(".tmp.mp4")

        db.set_clip_rendering(clip_id, str(temp))
        _ffmpeg_exact(source, temp, float(mark["in_sec"]), float(mark["out_sec"]))
        shutil.move(str(temp), str(output))
        db.set_clip_done(clip_id, str(output))
        return output

    except Exception as exc:
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        db.set_clip_failed(clip_id, str(exc))
        raise


def _ffmpeg_exact(
    input_path: Path,
    output_path: Path,
    in_sec: float,
    out_sec: float,
) -> None:
    ffmpeg   = require_binary("ffmpeg")
    duration = out_sec - in_sec

    cmd = [
        ffmpeg, "-hide_banner", "-y",
        "-ss", str(in_sec),
        "-i",  str(input_path),
        "-t",  str(duration),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-sn", "-dn",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        str(output_path),
    ]

    result = subprocess.run(cmd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-40:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def render_queued(limit: int = 10) -> list[tuple[int, Path]]:
    """Render up to *limit* queued clips.  Errors are printed, not re-raised."""
    clips   = db.list_clips(status="queued", limit=limit)
    results: list[tuple[int, Path]] = []
    for clip in clips:
        cid = int(clip["id"])
        try:
            path = render_clip(cid)
            results.append((cid, path))
        except Exception as exc:
            print(f"  [render] Clip {cid} failed: {exc}")
    return results


def render_all_queued() -> list[tuple[int, Path]]:
    """Keep rendering batches until the queue is empty."""
    results: list[tuple[int, Path]] = []
    while True:
        batch = render_queued(limit=50)
        if not batch and not db.list_clips(status="queued", limit=1):
            break
        results.extend(batch)
    return results


# ---------------------------------------------------------------------------
# Retry failed clips
# ---------------------------------------------------------------------------

def retry_failed_clips(clip_id: int | None = None) -> tuple[list[int], list[int]]:
    """Reset failed clips to queued.

    Returns (reset_ids, skipped_ids).
    Clips whose output file already exists are skipped with a warning.
    """
    if clip_id is not None:
        clip = db.get_clip(clip_id)
        if clip is None:
            raise ValueError(f"Clip {clip_id} not found")
        clips = [clip]
    else:
        clips = db.list_clips(status="failed", limit=10_000)

    reset_ids:   list[int] = []
    skipped_ids: list[int] = []

    for clip in clips:
        if clip["status"] != "failed":
            continue
        cid = int(clip["id"])

        # If output already exists, warn and skip
        if clip["output_path"] and Path(str(clip["output_path"])).exists():
            print(
                f"  [retry] Clip {cid}: output file already exists "
                f"({clip['output_path']}) - skipping"
            )
            skipped_ids.append(cid)
            continue

        # Remove leftover temp file
        if clip["temp_path"]:
            tmp = Path(str(clip["temp_path"]))
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError as exc:
                    print(f"  [retry] Clip {cid}: could not remove temp file: {exc}")

        db.reset_clip_to_queued(cid)
        reset_ids.append(cid)

    return reset_ids, skipped_ids
