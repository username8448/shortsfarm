"""MPV integration: launch mpv with a Lua review script, then import the
resulting JSONL event file into the database.
"""
from __future__ import annotations

import json
import random
import shutil
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import data_dir

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
LUA_SCRIPT   = SCRIPTS_DIR / "shortfarm.lua"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_suffix(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_file_path(video_id: int) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"{video_id}_{ts}_{_random_suffix()}.jsonl"
    return _sessions_dir() / name


def require_mpv() -> str:
    path = shutil.which("mpv")
    if not path:
        raise RuntimeError(
            "mpv not found in PATH.\n"
            "Install it first.  Arch Linux: sudo pacman -S mpv"
        )
    return path


# ---------------------------------------------------------------------------
# Event parsing - replay including undo
# ---------------------------------------------------------------------------

def _normalize_rating(value: object) -> int | None:
    if value is None:
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 5 else None


def _parse_marks(events: list[dict]) -> list[dict]:
    """Return final mark list after replaying undo events."""
    marks: list[dict] = []
    for ev in events:
        etype = ev.get("event")
        if etype in ("mark", "quick_clip"):
            try:
                in_sec = float(ev.get("in", 0))
                out_sec = float(ev.get("out", 0))
            except (TypeError, ValueError):
                continue
            if out_sec > in_sec:
                marks.append(
                    {
                        "in_sec":  in_sec,
                        "out_sec": out_sec,
                        "rating":  _normalize_rating(ev.get("rating")),
                        "label": (
                            ev.get("label")
                            if isinstance(ev.get("label"), str)
                            else None
                        ),
                    }
                )
        elif etype == "undo" and marks:
            marks.pop()
    return marks


# ---------------------------------------------------------------------------
# Core launch / import
# ---------------------------------------------------------------------------

def launch_review(video_id: int) -> tuple[int, Path]:
    """Launch mpv for the given video, then import the JSONL session.

    The video must already be in 'reviewing' status - the caller is
    responsible for the status transition.

    Returns (session_id, session_file_path).
    """
    video = db.get_video(video_id)
    if video is None:
        raise ValueError(f"Video {video_id} not found")

    session_file = _session_file_path(video_id)
    session_id   = db.create_review_session(
        video_id     = video_id,
        session_file = str(session_file),
    )

    source_path = Path(str(video["source_path"]))

    try:
        if not source_path.exists():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")

        mpv = require_mpv()

        if not LUA_SCRIPT.exists():
            raise RuntimeError(
                f"Lua script not found: {LUA_SCRIPT}\n"
                "Make sure shortfarm/scripts/shortfarm.lua is present."
            )

        cmd = [
            mpv,
            str(source_path),
            f"--script={LUA_SCRIPT}",
            f"--script-opts=sf-marks-file={session_file}",
        ]
        subprocess.run(cmd, check=False)          # non-zero exit is ok (user q'd)
    except Exception as exc:
        db.fail_review_session(session_id, str(exc))
        db.update_video_review_status(video_id, "inbox")
        raise RuntimeError(f"mpv failed to start: {exc}") from exc

    # mpv has exited - mark session as closed before importing
    db.close_review_session(session_id)

    _import_session(session_id=session_id, video_id=video_id, session_file=session_file)

    return session_id, session_file


def _import_session(session_id: int, video_id: int, session_file: Path) -> None:
    """Read JSONL events, write marks+clips to DB, set final statuses."""
    session = db.get_review_session(session_id)
    if session is not None and session["status"] == "imported":
        return

    # --- no file at all: mpv likely crashed before writing anything ----------
    if not session_file.exists():
        db.fail_review_session(session_id, "Session file was never created")
        db.update_video_review_status(video_id, "inbox")
        return

    # --- read & parse lines --------------------------------------------------
    raw_events: list[dict] = []
    parse_warnings: list[str] = []

    for i, line in enumerate(session_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_warnings.append(f"line {i}: {exc}")
            continue
        if not isinstance(event, dict):
            parse_warnings.append(f"line {i}: expected JSON object event")
            continue
        raw_events.append(event)

    # --- determine final event (done / skip / quit) --------------------------
    final: str | None = None
    for ev in reversed(raw_events):
        etype = ev.get("event")
        if etype in ("done", "skip", "quit"):
            final = etype
            break

    # --- insert marks and queue clips ----------------------------------------
    marks = _parse_marks(raw_events)
    for m in marks:
        mark_id = db.insert_mark(
            video_id   = video_id,
            session_id = session_id,
            in_sec     = m["in_sec"],
            out_sec    = m["out_sec"],
            rating     = m["rating"],
            label      = m["label"],
            source     = "mpv",
        )
        db.insert_clip(video_id=video_id, mark_id=mark_id, cut_mode="exact")

    # --- set video review_status based on final event ------------------------
    if final == "done":
        db.update_video_review_status(video_id, "reviewed")
    elif final == "skip":
        db.update_video_review_status(video_id, "skipped")
    elif final == "quit":
        db.update_video_review_status(video_id, "inbox")
        msg = "Session ended with quit - video returned to inbox"
        parse_warnings.append(msg)
        print(f"[shortfarm] Warning: {msg}")
    else:
        # quit or no final event -> return to inbox, keep marks
        db.update_video_review_status(video_id, "inbox")
        msg = "Session ended without done/skip/quit - video returned to inbox"
        parse_warnings.append(msg)
        print(f"[shortfarm] Warning: {msg}")

    warning_text = "; ".join(parse_warnings) if parse_warnings else None
    db.import_review_session(session_id, warning=warning_text)
