"""SQLite persistence layer for ShortsFarm.

All public functions open/close their own connection so callers never have
to manage transactions themselves.  The lone exception is `claim_inbox_video`,
which uses an explicit BEGIN IMMEDIATE to guarantee atomic claim-and-update.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import (
    DEFAULT_YOUTUBE_REDIRECT_URI,
    YOUTUBE_CLIENT_ID_SETTING,
    YOUTUBE_CLIENT_SECRET_SETTING,
    YOUTUBE_REDIRECT_URI_SETTING,
    db_path,
    ensure_dirs,
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode  = WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    """Ensure schema is up-to-date by running all pending migrations."""
    from .migrations import run_migrations
    run_migrations()
    bootstrap_legacy_youtube_oauth_profile()


# ---------------------------------------------------------------------------
# videos
# ---------------------------------------------------------------------------

def add_video(source_path: Path, title: str, duration_sec: float | None) -> int:
    with connect() as con:
        try:
            cur = con.execute(
                """
                INSERT INTO videos (source_path, title, duration_sec, status, created_at)
                VALUES (?, ?, ?, 'added', ?)
                """,
                (str(source_path), title, duration_sec, now_utc()),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = con.execute(
                "SELECT id FROM videos WHERE source_path = ?", (str(source_path),)
            ).fetchone()
            if row is None:
                raise
            return int(row["id"])


def get_video(video_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        ).fetchone()


def get_video_by_source_path(source_path: Path) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM videos WHERE source_path = ?", (str(source_path),)
        ).fetchone()


def list_videos() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM videos ORDER BY id DESC"
        ).fetchall()


def list_videos_with_counts() -> list[sqlite3.Row]:
    """Return videos with mark/clip counters for the CLI inbox view."""
    with connect() as con:
        return con.execute(
            """
            SELECT
                v.*,
                COUNT(DISTINCT m.id) AS mark_count,
                COUNT(DISTINCT c.id) AS clip_count
            FROM videos v
            LEFT JOIN marks m ON m.video_id = v.id
            LEFT JOIN clips c ON c.video_id = v.id
            GROUP BY v.id
            ORDER BY v.id ASC
            """
        ).fetchall()


def count_videos_by_review_status() -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT COALESCE(review_status, 'inbox') AS status, COUNT(*) AS count
            FROM videos
            GROUP BY COALESCE(review_status, 'inbox')
            """
        ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def count_videos() -> int:
    with connect() as con:
        row = con.execute("SELECT COUNT(*) FROM videos").fetchone()
    return int(row[0]) if row else 0


def update_video_review_status(video_id: int, review_status: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE videos SET review_status = ? WHERE id = ?",
            (review_status, video_id),
        )


def claim_inbox_video() -> sqlite3.Row | None:
    """Atomically pick the first 'inbox' video and set it to 'reviewing'.

    Uses BEGIN IMMEDIATE so two concurrent terminals cannot claim the same
    video.  Returns the (now-'reviewing') row or None if nothing available.
    """
    ensure_dirs()
    con = sqlite3.connect(str(db_path()), isolation_level=None)  # autocommit
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode  = WAL")
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM videos WHERE review_status = 'inbox' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None

        result = con.execute(
            "UPDATE videos SET review_status = 'reviewing' "
            "WHERE id = ? AND review_status = 'inbox'",
            (int(row["id"]),),
        )
        # rowcount == 0 means someone else grabbed it between our SELECT and UPDATE
        if result.rowcount == 0:
            con.execute("COMMIT")
            return None

        updated = con.execute(
            "SELECT * FROM videos WHERE id = ?", (int(row["id"]),)
        ).fetchone()
        con.execute("COMMIT")
        return updated
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# jobs (split workflow - unchanged logic)
# ---------------------------------------------------------------------------

def create_job(video_id: int, mode: str, segment_seconds: int) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO jobs (video_id, type, status, mode, segment_seconds, created_at)
            VALUES (?, 'split', 'queued', ?, ?, ?)
            """,
            (video_id, mode, segment_seconds, now_utc()),
        )
        return int(cur.lastrowid)


def mark_job_running(job_id: int) -> None:
    with connect() as con:
        con.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (now_utc(), job_id),
        )


def mark_job_done(job_id: int) -> None:
    with connect() as con:
        con.execute(
            "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
            (now_utc(), job_id),
        )


def mark_job_failed(job_id: int, error: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (error, now_utc(), job_id),
        )


def list_jobs(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT jobs.*, videos.title AS video_title
            FROM jobs
            JOIN videos ON videos.id = jobs.video_id
            ORDER BY jobs.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def count_jobs_by_status() -> dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


# ---------------------------------------------------------------------------
# segments
# ---------------------------------------------------------------------------

def insert_segment(
    video_id: int,
    job_id: int,
    segment_index: int,
    start_sec: float,
    end_sec: float,
    path: Path,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO segments
                (video_id, job_id, segment_index, start_sec, end_sec, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, job_id, segment_index, start_sec, end_sec, str(path), now_utc()),
        )
        return int(cur.lastrowid)


def _latest_done_job_id(video_id: int) -> int | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT id FROM jobs
            WHERE video_id=? AND type='split' AND status='done'
            ORDER BY id DESC LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return int(row["id"]) if row else None


def list_segments(video_id: int, job_id: int | None = None) -> list[sqlite3.Row]:
    if job_id is None:
        job_id = _latest_done_job_id(video_id)
    if job_id is None:
        return []
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM segments
            WHERE video_id=? AND job_id=?
            ORDER BY segment_index ASC
            """,
            (video_id, job_id),
        ).fetchall()


def count_segments(video_id: int | None = None) -> int:
    with connect() as con:
        if video_id is None:
            row = con.execute("SELECT COUNT(*) FROM segments").fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) FROM segments WHERE video_id=?", (video_id,)
            ).fetchone()
    return int(row[0]) if row else 0


def latest_segment_path(video_id: int | None = None) -> str | None:
    clauses: list[str] = []
    params: list = []
    if video_id is not None:
        clauses.append("video_id = ?")
        params.append(video_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        row = con.execute(
            f"""
            SELECT path FROM segments
            {where}
            ORDER BY id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return str(row["path"]) if row else None


def list_recent_segments(
    video_id: int | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []
    if video_id is not None:
        clauses.append("s.video_id = ?")
        params.append(video_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as con:
        return con.execute(
            f"""
            SELECT s.*, v.title AS video_title
            FROM segments s
            JOIN videos v ON v.id = s.video_id
            {where}
            ORDER BY s.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()


# ---------------------------------------------------------------------------
# Input-video status helpers (split workflow)
# ---------------------------------------------------------------------------

def has_done_split_job(video_id: int) -> bool:
    with connect() as con:
        row = con.execute(
            """
            SELECT id FROM jobs
            WHERE video_id=? AND type='split' AND status='done'
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return row is not None


def input_video_status(source_path: Path) -> str:
    video = get_video_by_source_path(source_path)
    if video is None:
        return "pending"
    return "done" if has_done_split_job(int(video["id"])) else "pending"


# ---------------------------------------------------------------------------
# review_sessions
# ---------------------------------------------------------------------------

def create_review_session(video_id: int, session_file: str) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO review_sessions (video_id, session_file, status, started_at)
            VALUES (?, ?, 'open', ?)
            """,
            (video_id, session_file, now_utc()),
        )
        return int(cur.lastrowid)


def get_review_session(session_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM review_sessions WHERE id=?", (session_id,)
        ).fetchone()


def close_review_session(session_id: int) -> None:
    """mpv closed - waiting for import."""
    with connect() as con:
        con.execute(
            "UPDATE review_sessions SET status='closed', finished_at=? WHERE id=?",
            (now_utc(), session_id),
        )


def fail_review_session(session_id: int, error: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE review_sessions SET status='failed', finished_at=?, error=? WHERE id=?",
            (now_utc(), error, session_id),
        )


def import_review_session(session_id: int, warning: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "UPDATE review_sessions SET status='imported', imported_at=?, finished_at=?, error=? WHERE id=?",
            (now_utc(), now_utc(), warning, session_id),
        )


def abandon_open_sessions(video_id: int) -> int:
    """Mark all open/closed sessions for a video as abandoned. Returns count."""
    with connect() as con:
        result = con.execute(
            """
            UPDATE review_sessions
            SET status='abandoned', finished_at=?, error='Abandoned by user reset'
            WHERE video_id=? AND status IN ('open', 'closed')
            """,
            (now_utc(), video_id),
        )
        return result.rowcount


def list_review_sessions(video_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM review_sessions WHERE video_id=? ORDER BY id DESC",
            (video_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------

def insert_mark(
    video_id: int,
    session_id: int | None,
    in_sec: float,
    out_sec: float,
    rating: int | None = None,
    label: str | None = None,
    source: str = "mpv",
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO marks
                (video_id, session_id, in_sec, out_sec, rating, label, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, session_id, in_sec, out_sec, rating, label, source, now_utc()),
        )
        return int(cur.lastrowid)


def list_marks(video_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM marks WHERE video_id=? ORDER BY in_sec ASC",
            (video_id,),
        ).fetchall()


def count_marks(video_id: int) -> int:
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM marks WHERE video_id=?", (video_id,)
        ).fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# clips
# ---------------------------------------------------------------------------

def insert_clip(
    video_id: int,
    mark_id: int | None,
    cut_mode: str = "exact",
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO clips (video_id, mark_id, status, cut_mode, created_at)
            VALUES (?, ?, 'queued', ?, ?)
            """,
            (video_id, mark_id, cut_mode, now_utc()),
        )
        return int(cur.lastrowid)


def get_clip(clip_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM clips WHERE id=?", (clip_id,)
        ).fetchone()


def get_or_create_publish_clip_from_segment(
    segment_id: int,
    *,
    output_path: str | None = None,
    target_aspect: str | None = None,
) -> int:
    segment_id = int(segment_id)
    now = now_utc()
    aspect = _normalize_workspace_target_aspect(target_aspect)
    with connect() as con:
        segment = con.execute(
            "SELECT * FROM segments WHERE id=?",
            (segment_id,),
        ).fetchone()
        if segment is None:
            raise FileNotFoundError("Сегмент не найден.")

        resolved_output_path = str(output_path or segment["path"] or "")
        if not resolved_output_path:
            raise ValueError("У сегмента не задан путь к файлу.")
        candidate = Path(resolved_output_path).expanduser()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Файл сегмента не найден: {candidate}")

        existing = con.execute(
            "SELECT id FROM clips WHERE source_segment_id=?",
            (segment_id,),
        ).fetchone()
        if existing is not None:
            clip_id = int(existing["id"])
            con.execute(
                """
                UPDATE clips
                SET video_id=?, mark_id=NULL, status='done', cut_mode='segment',
                    output_path=?, temp_path=NULL, error=NULL,
                    rendered_at=COALESCE(rendered_at, ?), source_aspect=?
                WHERE id=?
                """,
                (int(segment["video_id"]), resolved_output_path, now, aspect, clip_id),
            )
            return clip_id

        cur = con.execute(
            """
            INSERT INTO clips
                (video_id, mark_id, status, cut_mode, output_path, temp_path,
                 error, created_at, started_at, rendered_at, source_segment_id, source_aspect)
            VALUES (?, NULL, 'done', 'segment', ?, NULL, NULL, ?, NULL, ?, ?, ?)
            """,
            (int(segment["video_id"]), resolved_output_path, now, now, segment_id, aspect),
        )
        return int(cur.lastrowid)


def get_or_create_publish_clip_for_workspace_item(
    item_type: str,
    item_id: int,
    *,
    output_path: str | None = None,
    target_aspect: str | None = None,
) -> int:
    item_type = _normalize_workspace_item_type(item_type)
    item_id = int(item_id)
    aspect = _normalize_workspace_target_aspect(target_aspect)
    if item_type == "segment":
        return get_or_create_publish_clip_from_segment(
            item_id,
            output_path=output_path,
            target_aspect=aspect,
        )

    now = now_utc()
    with connect() as con:
        clip = con.execute("SELECT * FROM clips WHERE id=?", (item_id,)).fetchone()
        if clip is None:
            raise FileNotFoundError("Клип не найден.")
        original_output_path = str(clip["output_path"] or "")
        resolved_output_path = str(output_path or original_output_path)
        if not resolved_output_path:
            raise ValueError("У клипа нет output_path.")
        candidate = Path(resolved_output_path).expanduser()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Файл клипа не найден: {candidate}")

        if not output_path or resolved_output_path == original_output_path:
            return item_id

        existing = con.execute(
            """
            SELECT id FROM clips
            WHERE source_clip_id=? AND source_aspect=?
            """,
            (item_id, aspect),
        ).fetchone()
        if existing is not None:
            service_clip_id = int(existing["id"])
            con.execute(
                """
                UPDATE clips
                SET video_id=?, mark_id=NULL, status='done', cut_mode='prepared',
                    output_path=?, temp_path=NULL, error=NULL,
                    rendered_at=COALESCE(rendered_at, ?)
                WHERE id=?
                """,
                (int(clip["video_id"]), resolved_output_path, now, service_clip_id),
            )
            return service_clip_id

        cur = con.execute(
            """
            INSERT INTO clips
                (video_id, mark_id, status, cut_mode, output_path, temp_path,
                 error, created_at, started_at, rendered_at, source_clip_id, source_aspect)
            VALUES (?, NULL, 'done', 'prepared', ?, NULL, NULL, ?, NULL, ?, ?, ?)
            """,
            (int(clip["video_id"]), resolved_output_path, now, now, item_id, aspect),
        )
        return int(cur.lastrowid)


def list_clips(
    status: str | None = None,
    video_id: int | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []
    if status is not None:
        clauses.append("c.status = ?")
        params.append(status)
    if video_id is not None:
        clauses.append("c.video_id = ?")
        params.append(video_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as con:
        return con.execute(
            f"""
            SELECT c.*, v.title AS video_title
            FROM clips c
            JOIN videos v ON v.id = c.video_id
            {where}
            ORDER BY c.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()


def count_clips(video_id: int, status: str | None = None) -> int:
    if status:
        with connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM clips WHERE video_id=? AND status=?",
                (video_id, status),
            ).fetchone()
    else:
        with connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM clips WHERE video_id=?", (video_id,)
            ).fetchone()
    return int(row[0]) if row else 0


def count_clips_by_status(video_id: int | None = None) -> dict[str, int]:
    clauses: list[str] = []
    params: list = []
    if video_id is not None:
        clauses.append("video_id = ?")
        params.append(video_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        rows = con.execute(
            f"SELECT status, COUNT(*) AS count FROM clips {where} GROUP BY status",
            params,
        ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


# ---------------------------------------------------------------------------
# clip workspace metadata
# ---------------------------------------------------------------------------

WORKSPACE_ITEM_TYPES = {"segment", "clip"}
WORKSPACE_STATUSES = {"draft", "ready", "queued", "uploaded", "failed"}
WORKSPACE_TARGET_ASPECTS = {"original", "16x9", "9x16"}
WORKSPACE_PREPARE_STATUSES = {"none", "queued", "processing", "done", "failed"}


def _normalize_workspace_item_type(value: str) -> str:
    item_type = str(value or "").strip().lower()
    if item_type not in WORKSPACE_ITEM_TYPES:
        raise ValueError("Workspace item type must be 'segment' or 'clip'.")
    return item_type


def _normalize_workspace_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in WORKSPACE_STATUSES:
        raise ValueError("Workspace status must be one of: draft, ready, queued, uploaded, failed.")
    return status


def _normalize_workspace_target_aspect(value: str | None) -> str:
    aspect = str(value or "original").strip().lower().replace(":", "x")
    if aspect not in WORKSPACE_TARGET_ASPECTS:
        raise ValueError("Workspace target_aspect must be one of: original, 16x9, 9x16.")
    return aspect


def _normalize_workspace_prepare_status(value: str | None) -> str:
    status = str(value or "none").strip().lower()
    if status not in WORKSPACE_PREPARE_STATUSES:
        raise ValueError("Workspace prepare_status must be one of: none, queued, processing, done, failed.")
    return status


def _workspace_item_exists(con: sqlite3.Connection, item_type: str, item_id: int) -> bool:
    table = "segments" if item_type == "segment" else "clips"
    row = con.execute(f"SELECT id FROM {table} WHERE id=?", (item_id,)).fetchone()
    return row is not None


def _workspace_metadata_row(
    con: sqlite3.Connection,
    item_type: str,
    item_id: int,
) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM clip_workspace_metadata
        WHERE item_type=? AND item_id=?
        """,
        (item_type, item_id),
    ).fetchone()


def _workspace_path_state(path: str) -> dict[str, Any]:
    if not path:
        return {
            "file_exists": False,
            "folder_exists": False,
            "missing": True,
            "path_error": "Путь к файлу не задан.",
        }
    try:
        candidate = Path(path).expanduser()
        parent = candidate.parent
        folder_exists = parent.exists() and parent.is_dir()
        file_exists = candidate.exists() and candidate.is_file()
        path_error = None
        if not file_exists:
            if not folder_exists:
                path_error = f"Папка не найдена: {parent}"
            elif candidate.exists() and not candidate.is_file():
                path_error = f"Путь не является файлом: {candidate}"
            else:
                path_error = f"Файл не найден: {candidate}"
        return {
            "file_exists": file_exists,
            "folder_exists": folder_exists,
            "missing": not file_exists,
            "path_error": path_error,
        }
    except OSError as exc:
        return {
            "file_exists": False,
            "folder_exists": False,
            "missing": True,
            "path_error": str(exc),
        }


def _workspace_uploaded_clip_ids(con: sqlite3.Connection) -> set[int]:
    rows = con.execute(
        """
           SELECT DISTINCT clip_id
        FROM publish_jobs
        WHERE status='done'
          AND (youtube_video_id IS NOT NULL OR youtube_url IS NOT NULL)
        """
    ).fetchall()
    return {int(row["clip_id"]) for row in rows}


def _derive_clip_workspace_status(row: sqlite3.Row, uploaded_clip_ids: set[int]) -> str:
    metadata_status = row["workspace_status"]
    if metadata_status:
        return str(metadata_status)
    clip_id = int(row["id"])
    if clip_id in uploaded_clip_ids:
        return "uploaded"
    clip_status = str(row["status"] or "")
    if clip_status == "done":
        return "ready"
    if clip_status in {"queued", "rendering"}:
        return "queued"
    if clip_status == "failed":
        return "failed"
    return "draft"


def _workspace_item_dict(
    *,
    item_type: str,
    item_id: int,
    video_id: int,
    video_title: str,
    source_path: str,
    path: str,
    duration_sec: float | None,
    workspace_status: str,
    title: str | None,
    description: str | None,
    tags: str | None,
    target_aspect: str | None = None,
    prepared_path: str | None = None,
    prepared_at: str | None = None,
    prepare_status: str | None = None,
    prepare_error: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    segment_id: int | None = None,
    clip_id: int | None = None,
    publish_clip_id: int | None = None,
    publish_job_id: int | None = None,
    publish_job_status: str | None = None,
    publish_youtube_url: str | None = None,
    publish_youtube_video_id: str | None = None,
    publish_error: str | None = None,
    mark_id: int | None = None,
    cut_mode: str | None = None,
    render_status: str | None = None,
    error: str | None = None,
    hidden_at: str | None = None,
    missing_confirmed_at: str | None = None,
) -> dict[str, Any]:
    item_path = Path(path) if path else None
    path_state = _workspace_path_state(path)
    prepared = str(prepared_path or "")
    prepared_state = _workspace_path_state(prepared) if prepared else {
        "file_exists": False,
        "folder_exists": False,
        "missing": True,
        "path_error": None,
    }
    return {
        "id": f"{item_type}:{item_id}",
        "item_type": item_type,
        "item_id": item_id,
        "segment_id": segment_id,
        "clip_id": clip_id,
        "publish_clip_id": publish_clip_id,
        "publish_job_id": publish_job_id,
        "publish_job_status": publish_job_status or "",
        "publish_youtube_url": publish_youtube_url or "",
        "publish_youtube_video_id": publish_youtube_video_id or "",
        "publish_error": publish_error or "",
        "video_id": video_id,
        "video_title": video_title,
        "source_path": source_path,
        "path": path,
        "folder_path": str(item_path.parent) if item_path else "",
        "file_name": item_path.name if item_path else "",
        "duration_sec": duration_sec,
        "workspace_status": workspace_status,
        "title": title or "",
        "description": description or "",
        "tags": tags or "",
        "target_aspect": _normalize_workspace_target_aspect(target_aspect),
        "prepared_path": prepared,
        "prepared_file_exists": prepared_state["file_exists"],
        "prepared_folder_exists": prepared_state["folder_exists"],
        "prepared_at": prepared_at,
        "prepare_status": _normalize_workspace_prepare_status(prepare_status),
        "prepare_error": prepare_error or "",
        "mark_id": mark_id,
        "cut_mode": cut_mode or "",
        "render_status": render_status or "",
        "error": error or "",
        "created_at": created_at,
        "updated_at": updated_at,
        "hidden_at": hidden_at,
        "missing_confirmed_at": missing_confirmed_at,
        **path_state,
    }


def list_workspace_items(
    status: str | None = None,
    limit: int = 1000,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    normalized_status = None
    missing_only = status == "missing"
    if status not in (None, "", "all", "missing"):
        normalized_status = _normalize_workspace_status(status)
    uploaded_clip_ids: set[int]
    items: list[dict[str, Any]] = []
    hidden_clause = "" if include_hidden else "WHERE wm.hidden_at IS NULL"

    with connect() as con:
        uploaded_clip_ids = _workspace_uploaded_clip_ids(con)

        segment_rows = con.execute(
            f"""
            SELECT
                s.*,
                v.title AS video_title,
                v.source_path AS source_path,
                pc.id AS publish_clip_id,
                pj.id AS publish_job_id,
                pj.status AS publish_job_status,
                pj.youtube_url AS publish_youtube_url,
                pj.youtube_video_id AS publish_youtube_video_id,
                pj.error AS publish_error,
                wm.workspace_status,
                wm.title AS workspace_title,
                wm.description AS workspace_description,
                wm.tags AS workspace_tags,
                wm.target_aspect AS workspace_target_aspect,
                wm.prepared_path AS workspace_prepared_path,
                wm.prepared_at AS workspace_prepared_at,
                wm.prepare_status AS workspace_prepare_status,
                wm.prepare_error AS workspace_prepare_error,
                wm.created_at AS workspace_created_at,
                wm.updated_at AS workspace_updated_at,
                wm.hidden_at AS workspace_hidden_at,
                wm.missing_confirmed_at AS workspace_missing_confirmed_at
            FROM segments s
            JOIN videos v ON v.id = s.video_id
            LEFT JOIN clips pc ON pc.source_segment_id=s.id
            LEFT JOIN publish_jobs pj ON pj.id = (
                SELECT latest_pj.id
                FROM publish_jobs latest_pj
                WHERE latest_pj.clip_id=pc.id
                ORDER BY latest_pj.id DESC
                LIMIT 1
            )
            LEFT JOIN clip_workspace_metadata wm
              ON wm.item_type='segment' AND wm.item_id=s.id
            {hidden_clause}
            ORDER BY s.id DESC
            """
        ).fetchall()

        for row in segment_rows:
            item_status = str(row["workspace_status"] or "draft")
            items.append(
                _workspace_item_dict(
                    item_type="segment",
                    item_id=int(row["id"]),
                    segment_id=int(row["id"]),
                    clip_id=None,
                    publish_clip_id=row["publish_clip_id"],
                    publish_job_id=row["publish_job_id"],
                    publish_job_status=row["publish_job_status"],
                    publish_youtube_url=row["publish_youtube_url"],
                    publish_youtube_video_id=row["publish_youtube_video_id"],
                    publish_error=row["publish_error"],
                    video_id=int(row["video_id"]),
                    video_title=str(row["video_title"] or ""),
                    source_path=str(row["source_path"] or ""),
                    path=str(row["path"] or ""),
                    duration_sec=float(row["end_sec"] - row["start_sec"]),
                    workspace_status=item_status,
                    title=row["workspace_title"],
                    description=row["workspace_description"],
                    tags=row["workspace_tags"],
                    target_aspect=row["workspace_target_aspect"],
                    prepared_path=row["workspace_prepared_path"],
                    prepared_at=row["workspace_prepared_at"],
                    prepare_status=row["workspace_prepare_status"],
                    prepare_error=row["workspace_prepare_error"],
                    created_at=row["created_at"],
                    updated_at=row["workspace_updated_at"] or row["workspace_created_at"],
                    render_status=None,
                    hidden_at=row["workspace_hidden_at"],
                    missing_confirmed_at=row["workspace_missing_confirmed_at"],
                )
            )

        clip_rows = con.execute(
            f"""
            SELECT
                c.*,
                v.title AS video_title,
                v.source_path AS source_path,
                m.in_sec AS mark_in_sec,
                m.out_sec AS mark_out_sec,
                wm.workspace_status,
                wm.title AS workspace_title,
                wm.description AS workspace_description,
                wm.tags AS workspace_tags,
                wm.target_aspect AS workspace_target_aspect,
                wm.prepared_path AS workspace_prepared_path,
                wm.prepared_at AS workspace_prepared_at,
                wm.prepare_status AS workspace_prepare_status,
                wm.prepare_error AS workspace_prepare_error,
                wm.created_at AS workspace_created_at,
                wm.updated_at AS workspace_updated_at,
                wm.hidden_at AS workspace_hidden_at,
                wm.missing_confirmed_at AS workspace_missing_confirmed_at,
                pj.id AS publish_job_id,
                pj.status AS publish_job_status,
                pj.youtube_url AS publish_youtube_url,
                pj.youtube_video_id AS publish_youtube_video_id,
                pj.error AS publish_error
            FROM clips c
            JOIN videos v ON v.id = c.video_id
            LEFT JOIN marks m ON m.id = c.mark_id
            LEFT JOIN publish_jobs pj ON pj.id = (
                SELECT latest_pj.id
                FROM publish_jobs latest_pj
                LEFT JOIN clips latest_publish_clip
                  ON latest_publish_clip.id=latest_pj.clip_id
                WHERE latest_pj.clip_id=c.id
                   OR latest_publish_clip.source_clip_id=c.id
                ORDER BY latest_pj.id DESC
                LIMIT 1
            )
            LEFT JOIN clip_workspace_metadata wm
              ON wm.item_type='clip' AND wm.item_id=c.id
            {hidden_clause}
            {"AND" if hidden_clause else "WHERE"} c.source_segment_id IS NULL AND c.source_clip_id IS NULL
            ORDER BY c.id DESC
            """
        ).fetchall()

        for row in clip_rows:
            duration_sec = None
            if row["mark_in_sec"] is not None and row["mark_out_sec"] is not None:
                duration_sec = float(row["mark_out_sec"] - row["mark_in_sec"])
            item_path = row["output_path"] or row["temp_path"] or row["source_path"] or ""
            items.append(
                _workspace_item_dict(
                    item_type="clip",
                    item_id=int(row["id"]),
                    segment_id=None,
                    clip_id=int(row["id"]),
                    publish_clip_id=int(row["id"]),
                    publish_job_id=row["publish_job_id"],
                    publish_job_status=row["publish_job_status"],
                    publish_youtube_url=row["publish_youtube_url"],
                    publish_youtube_video_id=row["publish_youtube_video_id"],
                    publish_error=row["publish_error"],
                    video_id=int(row["video_id"]),
                    video_title=str(row["video_title"] or ""),
                    source_path=str(row["source_path"] or ""),
                    path=str(item_path),
                    duration_sec=duration_sec,
                    workspace_status=_derive_clip_workspace_status(row, uploaded_clip_ids),
                    title=row["workspace_title"],
                    description=row["workspace_description"],
                    tags=row["workspace_tags"],
                    target_aspect=row["workspace_target_aspect"],
                    prepared_path=row["workspace_prepared_path"],
                    prepared_at=row["workspace_prepared_at"],
                    prepare_status=row["workspace_prepare_status"],
                    prepare_error=row["workspace_prepare_error"],
                    created_at=row["created_at"],
                    updated_at=row["workspace_updated_at"] or row["workspace_created_at"],
                    mark_id=row["mark_id"],
                    cut_mode=row["cut_mode"],
                    render_status=row["status"],
                    error=row["error"],
                    hidden_at=row["workspace_hidden_at"],
                    missing_confirmed_at=row["workspace_missing_confirmed_at"],
                )
            )

    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if missing_only:
        items = [item for item in items if item["missing"]]
    if normalized_status is not None:
        items = [item for item in items if item["workspace_status"] == normalized_status]
    return items[:limit]


def get_workspace_item(item_type: str, item_id: int) -> dict[str, Any] | None:
    item_type = _normalize_workspace_item_type(item_type)
    item_id = int(item_id)
    for item in list_workspace_items(limit=10000):
        if item["item_type"] == item_type and int(item["item_id"]) == item_id:
            return item
    return None


def update_workspace_item(
    item_type: str,
    item_id: int,
    *,
    workspace_status: str | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: str | None = None,
    target_aspect: str | None = None,
    prepared_path: str | None = None,
    prepared_at: str | None = None,
    prepare_status: str | None = None,
    prepare_error: str | None = None,
    hidden_at: str | None = None,
    missing_confirmed_at: str | None = None,
) -> bool:
    item_type = _normalize_workspace_item_type(item_type)
    item_id = int(item_id)
    now = now_utc()

    with connect() as con:
        if not _workspace_item_exists(con, item_type, item_id):
            return False
        existing = _workspace_metadata_row(con, item_type, item_id)
        resolved_status = (
            _normalize_workspace_status(workspace_status)
            if workspace_status is not None
            else str(existing["workspace_status"]) if existing is not None else "draft"
        )
        resolved_title = title if title is not None else (existing["title"] if existing is not None else None)
        resolved_description = (
            description
            if description is not None
            else (existing["description"] if existing is not None else None)
        )
        resolved_tags = tags if tags is not None else (existing["tags"] if existing is not None else None)
        resolved_target_aspect = (
            _normalize_workspace_target_aspect(target_aspect)
            if target_aspect is not None
            else str(existing["target_aspect"] or "original") if existing is not None else "original"
        )
        resolved_prepared_path = (
            prepared_path
            if prepared_path is not None
            else (existing["prepared_path"] if existing is not None else None)
        )
        resolved_prepared_at = (
            prepared_at
            if prepared_at is not None
            else (existing["prepared_at"] if existing is not None else None)
        )
        resolved_prepare_status = (
            _normalize_workspace_prepare_status(prepare_status)
            if prepare_status is not None
            else str(existing["prepare_status"] or "none") if existing is not None else "none"
        )
        resolved_prepare_error = (
            prepare_error
            if prepare_error is not None
            else (existing["prepare_error"] if existing is not None else None)
        )
        resolved_hidden_at = (
            hidden_at
            if hidden_at is not None
            else (existing["hidden_at"] if existing is not None else None)
        )
        resolved_missing_confirmed_at = (
            missing_confirmed_at
            if missing_confirmed_at is not None
            else (existing["missing_confirmed_at"] if existing is not None else None)
        )

        con.execute(
            """
            INSERT INTO clip_workspace_metadata
                (item_type, item_id, workspace_status, title, description, tags,
                 target_aspect, prepared_path, prepared_at, prepare_status, prepare_error,
                 created_at, updated_at, hidden_at, missing_confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_type, item_id) DO UPDATE SET
                workspace_status=excluded.workspace_status,
                title=excluded.title,
                description=excluded.description,
                tags=excluded.tags,
                target_aspect=excluded.target_aspect,
                prepared_path=excluded.prepared_path,
                prepared_at=excluded.prepared_at,
                prepare_status=excluded.prepare_status,
                prepare_error=excluded.prepare_error,
                updated_at=excluded.updated_at,
                hidden_at=excluded.hidden_at,
                missing_confirmed_at=excluded.missing_confirmed_at
            """,
            (
                item_type,
                item_id,
                resolved_status,
                resolved_title,
                resolved_description,
                resolved_tags,
                resolved_target_aspect,
                resolved_prepared_path,
                resolved_prepared_at,
                resolved_prepare_status,
                resolved_prepare_error,
                now,
                now,
                resolved_hidden_at,
                resolved_missing_confirmed_at,
            ),
        )
        return True


def set_workspace_prepare_status(
    item_type: str,
    item_id: int,
    *,
    prepare_status: str,
    target_aspect: str | None = None,
    prepared_path: str | None = None,
    prepare_error: str | None = None,
    prepared_at: str | None = None,
) -> bool:
    return update_workspace_item(
        item_type,
        item_id,
        target_aspect=target_aspect,
        prepared_path=prepared_path,
        prepared_at=prepared_at,
        prepare_status=prepare_status,
        prepare_error=prepare_error,
    )


def bulk_update_workspace_status(items: list[tuple[str, int]], workspace_status: str) -> int:
    status = _normalize_workspace_status(workspace_status)
    updated = 0
    for item_type, item_id in items:
        if update_workspace_item(item_type, item_id, workspace_status=status):
            updated += 1
    return updated


def hide_workspace_item(
    item_type: str,
    item_id: int,
    *,
    missing_confirmed: bool = False,
) -> bool:
    now = now_utc()
    return update_workspace_item(
        item_type,
        item_id,
        hidden_at=now,
        missing_confirmed_at=now if missing_confirmed else None,
    )


def cleanup_missing_workspace_items() -> int:
    items = list_workspace_items(status="missing", limit=10000)
    hidden = 0
    for item in items:
        if hide_workspace_item(item["item_type"], int(item["item_id"]), missing_confirmed=True):
            hidden += 1
    return hidden


def _workspace_identity_for_publish_clip(con: sqlite3.Connection, clip_id: int) -> tuple[str, int] | None:
    row = con.execute(
        "SELECT id, source_segment_id, source_clip_id FROM clips WHERE id=?",
        (clip_id,),
    ).fetchone()
    if row is None:
        return None
    source_segment_id = row["source_segment_id"]
    if source_segment_id is not None:
        return "segment", int(source_segment_id)
    source_clip_id = row["source_clip_id"]
    if source_clip_id is not None:
        return "clip", int(source_clip_id)
    return "clip", int(row["id"])


def _upsert_workspace_status(
    con: sqlite3.Connection,
    item_type: str,
    item_id: int,
    workspace_status: str,
    now: str,
) -> None:
    con.execute(
        """
        INSERT INTO clip_workspace_metadata
            (item_type, item_id, workspace_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(item_type, item_id) DO UPDATE SET
            workspace_status=excluded.workspace_status,
            updated_at=excluded.updated_at
        """,
        (item_type, item_id, workspace_status, now, now),
    )


# ---------------------------------------------------------------------------
# social_accounts (publishing integrations)
# ---------------------------------------------------------------------------

def _normalize_token(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (key,),
        ).fetchone()
    if row is None:
        return default
    value = row["value"]
    if value is None:
        return default
    return str(value)


def set_setting(key: str, value: str | None, is_secret: bool = False) -> None:
    now = now_utc()
    with connect() as con:
        con.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                is_secret=excluded.is_secret,
                updated_at=excluded.updated_at
            """,
            (key, value, 1 if is_secret else 0, now, now),
        )


def delete_setting(key: str) -> bool:
    with connect() as con:
        result = con.execute(
            "DELETE FROM app_settings WHERE key=?",
            (key,),
        )
        return result.rowcount > 0


def list_settings(mask_secrets: bool = True) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM app_settings ORDER BY key ASC"
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        is_secret = bool(row["is_secret"])
        value = row["value"]
        if mask_secrets and is_secret and value:
            masked_value = "********"
        else:
            masked_value = value
        items.append(
            {
                "key": row["key"],
                "value": masked_value,
                "is_secret": is_secret,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return items


# ---------------------------------------------------------------------------
# managed workspace folder metadata
# ---------------------------------------------------------------------------

WORKSPACE_FOLDER_KINDS = {
    "custom", "collection", "project", "source_group", "podcast", "episode",
}


def upsert_workspace_folder_metadata(
    workspace_root: str,
    relative_path: str,
    *,
    display_name: str | None = None,
    kind: str = "custom",
    description: str | None = None,
) -> int:
    normalized_kind = str(kind or "custom").strip().lower()
    if normalized_kind not in WORKSPACE_FOLDER_KINDS:
        raise ValueError(
            "Workspace folder kind must be one of: "
            + ", ".join(sorted(WORKSPACE_FOLDER_KINDS))
        )
    now = now_utc()
    with connect() as con:
        con.execute(
            """
            INSERT INTO workspace_folders
                (workspace_root, relative_path, display_name, kind,
                 description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_root, relative_path) DO UPDATE SET
                display_name=excluded.display_name,
                kind=excluded.kind,
                description=excluded.description,
                updated_at=excluded.updated_at
            """,
            (
                workspace_root,
                relative_path,
                display_name,
                normalized_kind,
                description,
                now,
                now,
            ),
        )
        row = con.execute(
            """
            SELECT id FROM workspace_folders
            WHERE workspace_root=? AND relative_path=?
            """,
            (workspace_root, relative_path),
        ).fetchone()
        if row is None:
            raise RuntimeError("Workspace folder metadata was not saved.")
        return int(row["id"])


def get_workspace_folder_metadata(
    workspace_root: str,
    relative_path: str,
) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM workspace_folders
            WHERE workspace_root=? AND relative_path=?
            """,
            (workspace_root, relative_path),
        ).fetchone()


def list_workspace_folder_metadata(workspace_root: str) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM workspace_folders
            WHERE workspace_root=?
            ORDER BY relative_path ASC
            """,
            (workspace_root,),
        ).fetchall()


def delete_workspace_folder_metadata(
    workspace_root: str,
    relative_path: str,
    *,
    include_descendants: bool = False,
) -> int:
    with connect() as con:
        if include_descendants:
            escaped = (
                relative_path.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            result = con.execute(
                """
                DELETE FROM workspace_folders
                WHERE workspace_root=?
                  AND (relative_path=? OR relative_path LIKE ? ESCAPE '\\')
                """,
                (workspace_root, relative_path, f"{escaped}/%"),
            )
        else:
            result = con.execute(
                """
                DELETE FROM workspace_folders
                WHERE workspace_root=? AND relative_path=?
                """,
                (workspace_root, relative_path),
            )
        return int(result.rowcount)


def move_workspace_folder_metadata(
    workspace_root: str,
    source_relative_path: str,
    target_relative_path: str,
) -> int:
    rows = list_workspace_folder_metadata(workspace_root)
    affected = [
        row for row in rows
        if (
            str(row["relative_path"]) == source_relative_path
            or str(row["relative_path"]).startswith(source_relative_path + "/")
        )
    ]
    if not affected:
        return 0

    with connect() as con:
        for row in sorted(
            affected,
            key=lambda item: len(str(item["relative_path"])),
        ):
            old_path = str(row["relative_path"])
            suffix = old_path[len(source_relative_path):]
            con.execute(
                """
                UPDATE workspace_folders
                SET relative_path=?, updated_at=?
                WHERE id=?
                """,
                (target_relative_path + suffix, now_utc(), int(row["id"])),
            )
    return len(affected)


def create_youtube_oauth_profile(
    *,
    name: str,
    client_id: str,
    client_secret: str | None,
    redirect_uri: str,
    mode: str = "custom",
    status: str = "active",
    is_default: bool = False,
    notes: str | None = None,
) -> int:
    now = now_utc()
    with connect() as con:
        if is_default:
            con.execute("UPDATE youtube_oauth_profiles SET is_default = 0 WHERE is_default != 0")
        cur = con.execute(
            """
            INSERT INTO youtube_oauth_profiles
                (name, mode, client_id, client_secret, redirect_uri, status,
                 is_default, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                mode,
                client_id,
                client_secret,
                redirect_uri,
                status,
                1 if is_default else 0,
                notes,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_youtube_oauth_profile(
    profile_id: int,
    *,
    name: str | None = None,
    mode: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM youtube_oauth_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return False

        resolved_client_secret = row["client_secret"]
        normalized_secret = _normalize_text(client_secret)
        if client_secret is not None and normalized_secret:
            resolved_client_secret = normalized_secret

        con.execute(
            """
            UPDATE youtube_oauth_profiles
            SET name=?, mode=?, client_id=?, client_secret=?, redirect_uri=?,
                status=?, notes=?, updated_at=?
            WHERE id=?
            """,
            (
                name if name is not None else row["name"],
                mode if mode is not None else row["mode"],
                client_id if client_id is not None else row["client_id"],
                resolved_client_secret,
                redirect_uri if redirect_uri is not None else row["redirect_uri"],
                status if status is not None else row["status"],
                notes if notes is not None else row["notes"],
                now_utc(),
                profile_id,
            ),
        )
        return True


def get_youtube_oauth_profile(profile_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM youtube_oauth_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()


def list_youtube_oauth_profiles() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM youtube_oauth_profiles
            ORDER BY is_default DESC, id ASC
            """
        ).fetchall()


def set_default_youtube_oauth_profile(profile_id: int) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT id FROM youtube_oauth_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return False
        con.execute("UPDATE youtube_oauth_profiles SET is_default = 0 WHERE is_default != 0")
        con.execute(
            """
            UPDATE youtube_oauth_profiles
            SET is_default=1, updated_at=?
            WHERE id=?
            """,
            (now_utc(), profile_id),
        )
        return True


def get_default_youtube_oauth_profile() -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM youtube_oauth_profiles
            WHERE is_default = 1
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()


def delete_youtube_oauth_profile(profile_id: int) -> bool:
    with connect() as con:
        profile = con.execute(
            "SELECT * FROM youtube_oauth_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        if profile is None:
            return False

        active_channels = con.execute(
            """
            SELECT COUNT(*)
            FROM social_accounts
            WHERE oauth_profile_id=? AND status='active'
            """,
            (profile_id,),
        ).fetchone()
        if active_channels and int(active_channels[0]) > 0:
            raise ValueError("Нельзя удалить OAuth Profile с активными YouTube-каналами.")

        con.execute(
            """
            UPDATE social_accounts
            SET oauth_profile_id=NULL, updated_at=?
            WHERE oauth_profile_id=?
            """,
            (now_utc(), profile_id),
        )
        con.execute("DELETE FROM youtube_oauth_profiles WHERE id=?", (profile_id,))

        if int(profile["is_default"] or 0):
            next_profile = con.execute(
                """
                SELECT id
                FROM youtube_oauth_profiles
                ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, id ASC
                LIMIT 1
                """
            ).fetchone()
            if next_profile is not None:
                con.execute(
                    """
                    UPDATE youtube_oauth_profiles
                    SET is_default=1, updated_at=?
                    WHERE id=?
                    """,
                    (now_utc(), int(next_profile["id"])),
                )
        return True


def bootstrap_legacy_youtube_oauth_profile() -> sqlite3.Row | None:
    existing = list_youtube_oauth_profiles()
    if existing:
        return get_default_youtube_oauth_profile() or existing[0]

    stored_client_id = _normalize_text(get_setting(YOUTUBE_CLIENT_ID_SETTING))
    stored_client_secret = _normalize_text(get_setting(YOUTUBE_CLIENT_SECRET_SETTING))
    stored_redirect_uri = _normalize_text(get_setting(YOUTUBE_REDIRECT_URI_SETTING))
    if not stored_client_id or not stored_client_secret:
        return None

    profile_id = create_youtube_oauth_profile(
        name="Legacy YouTube OAuth",
        mode="custom",
        client_id=stored_client_id,
        client_secret=stored_client_secret,
        redirect_uri=stored_redirect_uri or DEFAULT_YOUTUBE_REDIRECT_URI,
        status="active",
        is_default=True,
        notes="Bootstrapped from legacy app settings.",
    )
    return get_youtube_oauth_profile(profile_id)


def list_social_accounts(
    platform: str | None = None,
    oauth_profile_id: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []
    if platform is not None:
        clauses.append("sa.platform = ?")
        params.append(platform)
    if oauth_profile_id is not None:
        clauses.append("sa.oauth_profile_id = ?")
        params.append(oauth_profile_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        return con.execute(
            f"""
            SELECT sa.*, yp.name AS profile_name
            FROM social_accounts sa
            LEFT JOIN youtube_oauth_profiles yp ON yp.id = sa.oauth_profile_id
            {where}
            ORDER BY sa.id DESC
            """,
            params,
        ).fetchall()


def get_social_account(account_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT sa.*, yp.name AS profile_name
            FROM social_accounts sa
            LEFT JOIN youtube_oauth_profiles yp ON yp.id = sa.oauth_profile_id
            WHERE sa.id=?
            """,
            (account_id,),
        ).fetchone()


def save_social_account(
    *,
    platform: str,
    display_name: str | None,
    channel_id: str | None,
    channel_title: str | None,
    access_token: str | None,
    refresh_token: str | None,
    token_expires_at: str | None,
    scopes: str | None,
    oauth_profile_id: int | None = None,
    account_email: str | None = None,
    last_connected_at: str | None = None,
    status: str = "active",
    error: str | None = None,
) -> int:
    """Insert or update a publishing account.

    TODO: encrypt tokens before production use.
    """
    with connect() as con:
        existing = None
        if channel_id:
            existing = con.execute(
                """
                SELECT *
                FROM social_accounts
                WHERE platform = ? AND channel_id = ?
                """,
                (platform, channel_id),
            ).fetchone()

        if existing is not None:
            account_id = int(existing["id"])
            stored_refresh_token = _normalize_token(refresh_token) or existing["refresh_token"]
            con.execute(
                """
                UPDATE social_accounts
                SET display_name=?, channel_title=?, access_token=?, refresh_token=?,
                    token_expires_at=?, scopes=?, oauth_profile_id=?, account_email=?,
                    last_connected_at=?, status=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    display_name if display_name is not None else existing["display_name"],
                    channel_title if channel_title is not None else existing["channel_title"],
                    access_token if access_token is not None else existing["access_token"],
                    stored_refresh_token,
                    token_expires_at if token_expires_at is not None else existing["token_expires_at"],
                    scopes if scopes is not None else existing["scopes"],
                    oauth_profile_id if oauth_profile_id is not None else existing["oauth_profile_id"],
                    _normalize_text(account_email) or existing["account_email"],
                    last_connected_at if last_connected_at is not None else existing["last_connected_at"],
                    status,
                    error,
                    now_utc(),
                    account_id,
                ),
            )
            return account_id

        cur = con.execute(
            """
            INSERT INTO social_accounts
                (platform, display_name, channel_id, channel_title, access_token,
                 refresh_token, token_expires_at, scopes, oauth_profile_id, account_email,
                 last_connected_at, status, created_at, updated_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                display_name,
                channel_id,
                channel_title,
                access_token,
                refresh_token,
                token_expires_at,
                scopes,
                oauth_profile_id,
                _normalize_text(account_email),
                last_connected_at,
                status,
                now_utc(),
                now_utc(),
                error,
            ),
        )
        return int(cur.lastrowid)


def disconnect_social_account(account_id: int, platform: str | None = None) -> bool:
    clauses = ["id = ?"]
    where_params: list = [account_id]
    if platform is not None:
        clauses.append("platform = ?")
        where_params.append(platform)
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE social_accounts
            SET status='disconnected', updated_at=?
            WHERE {" AND ".join(clauses)}
            """,
            [now_utc(), *where_params],
        )
        return result.rowcount > 0


def create_oauth_state(provider: str, state: str, oauth_profile_id: int | None = None) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO oauth_states (provider, state, created_at, oauth_profile_id)
            VALUES (?, ?, ?, ?)
            """,
            (provider, state, now_utc(), oauth_profile_id),
        )
        return int(cur.lastrowid)


def consume_oauth_state(provider: str, state: str) -> sqlite3.Row | None:
    """Mark OAuth state as consumed. Returns the consumed row or None.

    TODO: expire old OAuth states by created_at.
    """
    with connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM oauth_states
            WHERE provider=? AND state=? AND consumed_at IS NULL
            """,
            (provider, state),
        ).fetchone()
        if row is None:
            return None
        result = con.execute(
            """
            UPDATE oauth_states
            SET consumed_at=?
            WHERE provider=? AND state=? AND consumed_at IS NULL
            """,
            (now_utc(), provider, state),
        )
        if result.rowcount == 0:
            return None
        return con.execute(
            """
            SELECT *
            FROM oauth_states
            WHERE id=?
            """,
            (int(row["id"]),),
        ).fetchone()


# ---------------------------------------------------------------------------
# publish_jobs (YouTube uploads)
# ---------------------------------------------------------------------------

_PUBLISH_JOB_SELECT = """
    SELECT pj.*,
           sa.display_name AS account_display_name,
           sa.account_email AS account_email,
           sa.channel_id AS channel_id,
           sa.channel_title AS channel_title,
           sa.oauth_profile_id AS oauth_profile_id,
           yp.name AS profile_name,
           c.video_id AS clip_video_id,
           c.status AS clip_status,
           c.output_path AS clip_output_path,
           c.cut_mode AS clip_cut_mode,
           c.error AS clip_error,
           c.source_segment_id AS clip_source_segment_id,
           c.source_clip_id AS clip_source_clip_id,
           c.source_aspect AS clip_source_aspect,
           psg.name AS schedule_group_name,
           v.title AS video_title,
           v.source_path AS video_source_path
    FROM publish_jobs pj
    LEFT JOIN social_accounts sa ON sa.id = pj.account_id
    LEFT JOIN youtube_oauth_profiles yp ON yp.id = sa.oauth_profile_id
    LEFT JOIN clips c ON c.id = pj.clip_id
    LEFT JOIN publish_schedule_groups psg ON psg.id = pj.schedule_group_id
    LEFT JOIN videos v ON v.id = c.video_id
"""

def create_publish_job(
    *,
    account_id: int,
    clip_id: int,
    title: str,
    description: str | None = None,
    tags: str | None = None,
    category_id: str = "22",
    privacy_status: str,
    publish_mode: str,
    publish_at: str | None = None,
    made_for_kids: bool = False,
    platform: str = "youtube",
) -> int:
    now = now_utc()
    with connect() as con:
        try:
            cur = con.execute(
                """
                INSERT INTO publish_jobs
                    (platform, account_id, clip_id, status, title, description, tags,
                     category_id, privacy_status, publish_mode, publish_at,
                     made_for_kids, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    account_id,
                    clip_id,
                    title,
                    description,
                    tags,
                    category_id,
                    privacy_status,
                    publish_mode,
                    publish_at,
                    1 if made_for_kids else 0,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = con.execute(
                """
                SELECT *
                FROM publish_jobs
                WHERE platform=? AND account_id=? AND clip_id=?
                """,
                (platform, account_id, clip_id),
            ).fetchone()
            if row is None:
                raise
            if row["status"] in {"queued", "failed", "cancelled"}:
                con.execute(
                    """
                    UPDATE publish_jobs
                    SET status='queued',
                        title=?,
                        description=?,
                        tags=?,
                        category_id=?,
                        privacy_status=?,
                        publish_mode=?,
                        publish_at=?,
                        made_for_kids=?,
                        error=NULL,
                        youtube_video_id=NULL,
                        youtube_url=NULL,
                        started_at=NULL,
                        finished_at=NULL,
                        next_attempt_at=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        title,
                        description,
                        tags,
                        category_id,
                        privacy_status,
                        publish_mode,
                        publish_at,
                        1 if made_for_kids else 0,
                        now,
                        int(row["id"]),
                    ),
                )
            return int(row["id"])


def get_publish_job(job_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            WHERE pj.id=?
            """,
            (job_id,),
        ).fetchone()


def get_publish_job_for_clip(
    *,
    account_id: int,
    clip_id: int,
    platform: str = "youtube",
) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            WHERE pj.platform=? AND pj.account_id=? AND pj.clip_id=?
            """,
            (platform, account_id, clip_id),
        ).fetchone()


def update_publish_job_metadata(
    job_id: int,
    *,
    title: str,
    description: str,
    tags: str,
    category_id: str,
    privacy_status: str,
    made_for_kids: bool,
    error: str | None = None,
) -> bool:
    with connect() as con:
        cur = con.execute(
            """
            UPDATE publish_jobs
            SET title=?, description=?, tags=?, category_id=?,
                privacy_status=?, made_for_kids=?, error=?, updated_at=?
            WHERE id=?
            """,
            (
                title,
                description,
                tags,
                category_id,
                privacy_status,
                1 if made_for_kids else 0,
                error,
                now_utc(),
                int(job_id),
            ),
        )
        return cur.rowcount > 0


def set_publish_job_error(job_id: int, error: str | None) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE publish_jobs SET error=?, updated_at=? WHERE id=?",
            (error, now_utc(), int(job_id)),
        )
        return cur.rowcount > 0


def list_publish_schedule_groups() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT psg.*,
                   COUNT(pj.id) AS job_count,
                   SUM(CASE WHEN pj.status='queued' THEN 1 ELSE 0 END) AS queued_count
            FROM publish_schedule_groups psg
            LEFT JOIN publish_jobs pj ON pj.schedule_group_id=psg.id
            GROUP BY psg.id
            ORDER BY psg.id DESC
            """
        ).fetchall()


def get_publish_schedule_group(group_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM publish_schedule_groups WHERE id=?",
            (int(group_id),),
        ).fetchone()


def list_publish_schedule_group_jobs(group_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            WHERE pj.schedule_group_id=?
            ORDER BY pj.schedule_position ASC, pj.id ASC
            """,
            (int(group_id),),
        ).fetchall()


def save_publish_schedule_group(
    *,
    name: str,
    job_ids: list[int],
    upload_spec: dict[str, Any],
    publish_spec: dict[str, Any],
    group_id: int | None = None,
) -> int:
    from .publish_schedule import (
        expand_schedule,
        normalize_schedule_spec,
        schedule_spec_json,
        validate_schedule_pair,
    )

    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Название группы расписания обязательно.")
    ordered_job_ids = list(dict.fromkeys(int(job_id) for job_id in job_ids))
    if not ordered_job_ids:
        raise ValueError("Выберите хотя бы одну publish job.")

    normalized_upload = normalize_schedule_spec(upload_spec)
    normalized_publish = normalize_schedule_spec(publish_spec)
    upload_times = expand_schedule(ordered_job_ids, normalized_upload)
    publish_times = expand_schedule(ordered_job_ids, normalized_publish)
    for job_id in ordered_job_ids:
        validate_schedule_pair(upload_times[job_id], publish_times[job_id])

    now = now_utc()
    with connect() as con:
        placeholders = ",".join("?" for _ in ordered_job_ids)
        rows = con.execute(
            f"SELECT id, status FROM publish_jobs WHERE id IN ({placeholders})",
            ordered_job_ids,
        ).fetchall()
        found = {int(row["id"]): str(row["status"]) for row in rows}
        missing = [job_id for job_id in ordered_job_ids if job_id not in found]
        if missing:
            raise FileNotFoundError(f"Publish jobs не найдены: {', '.join(map(str, missing))}")
        invalid = [job_id for job_id, status in found.items() if status != "queued"]
        if invalid:
            raise ValueError(
                "Расписание можно назначить только queued jobs: "
                + ", ".join(map(str, invalid))
            )

        values = (
            clean_name,
            normalized_upload["mode"],
            normalized_upload["start_at"],
            normalized_upload["interval_minutes"],
            schedule_spec_json(normalized_upload),
            normalized_publish["mode"],
            normalized_publish["start_at"],
            normalized_publish["interval_minutes"],
            schedule_spec_json(normalized_publish),
            now,
        )
        if group_id is None:
            cur = con.execute(
                """
                INSERT INTO publish_schedule_groups
                    (name, upload_mode, upload_start_at, upload_interval_minutes,
                     upload_item_times, publish_mode, publish_start_at,
                     publish_interval_minutes, publish_item_times, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values[:-1], now, now),
            )
            resolved_group_id = int(cur.lastrowid)
        else:
            resolved_group_id = int(group_id)
            if con.execute(
                "SELECT id FROM publish_schedule_groups WHERE id=?",
                (resolved_group_id,),
            ).fetchone() is None:
                raise FileNotFoundError("Группа расписания не найдена.")
            con.execute(
                """
                UPDATE publish_schedule_groups
                SET name=?, upload_mode=?, upload_start_at=?, upload_interval_minutes=?,
                    upload_item_times=?, publish_mode=?, publish_start_at=?,
                    publish_interval_minutes=?, publish_item_times=?, updated_at=?
                WHERE id=?
                """,
                (*values, resolved_group_id),
            )
            con.execute(
                f"""
                UPDATE publish_jobs
                SET schedule_group_id=NULL, schedule_position=NULL, upload_at=NULL,
                    overdue_approved_at=NULL, updated_at=?
                WHERE schedule_group_id=? AND status='queued'
                  AND id NOT IN ({placeholders})
                """,
                (now, resolved_group_id, *ordered_job_ids),
            )

        for position, job_id in enumerate(ordered_job_ids, start=1):
            publish_at = publish_times[job_id]
            if normalized_publish["mode"] == "none":
                publish_sql = """
                    publish_at=CASE WHEN publish_mode='schedule' THEN NULL ELSE publish_at END,
                    publish_mode=CASE WHEN publish_mode='schedule' THEN 'private' ELSE publish_mode END,
                    privacy_status=CASE WHEN publish_mode='schedule' THEN 'private' ELSE privacy_status END,
                """
            else:
                publish_sql = """
                    publish_at=?,
                    publish_mode='schedule',
                    privacy_status='private',
                """
            params: list[Any] = []
            if normalized_publish["mode"] != "none":
                params.append(publish_at)
            params.extend([
                resolved_group_id,
                position,
                upload_times[job_id],
                now,
                job_id,
            ])
            con.execute(
                f"""
                UPDATE publish_jobs
                SET {publish_sql}
                    schedule_group_id=?, schedule_position=?, upload_at=?,
                    overdue_approved_at=NULL, updated_at=?
                WHERE id=? AND status='queued'
                """,
                params,
            )
        return resolved_group_id


def remove_publish_schedule_group(group_id: int) -> bool:
    now = now_utc()
    with connect() as con:
        group = con.execute(
            "SELECT id FROM publish_schedule_groups WHERE id=?",
            (int(group_id),),
        ).fetchone()
        if group is None:
            return False
        con.execute(
            """
            UPDATE publish_jobs
            SET schedule_group_id=NULL, schedule_position=NULL, upload_at=NULL,
                overdue_approved_at=NULL,
                publish_at=CASE
                    WHEN status IN ('queued','failed','cancelled') AND publish_mode='schedule'
                    THEN NULL ELSE publish_at END,
                publish_mode=CASE
                    WHEN status IN ('queued','failed','cancelled') AND publish_mode='schedule'
                    THEN 'private' ELSE publish_mode END,
                privacy_status=CASE
                    WHEN status IN ('queued','failed','cancelled') AND publish_mode='schedule'
                    THEN 'private' ELSE privacy_status END,
                updated_at=?
            WHERE schedule_group_id=?
            """,
            (now, int(group_id)),
        )
        con.execute("DELETE FROM publish_schedule_groups WHERE id=?", (int(group_id),))
        return True


def approve_overdue_publish_schedule_group(group_id: int) -> int:
    from .publish_schedule import OVERDUE_GRACE, utc_iso
    from datetime import datetime, timezone

    now_dt = datetime.now(timezone.utc)
    cutoff = utc_iso(now_dt - OVERDUE_GRACE)
    now = utc_iso(now_dt)
    with connect() as con:
        result = con.execute(
            """
            UPDATE publish_jobs
            SET overdue_approved_at=?, updated_at=?
            WHERE schedule_group_id=? AND status='queued'
              AND upload_at IS NOT NULL AND upload_at < ?
            """,
            (now, now, int(group_id), cutoff),
        )
        return int(result.rowcount)


def list_publish_jobs(
    status: str | None = None,
    platform: str | None = "youtube",
    limit: int = 100,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []
    if platform is not None:
        clauses.append("pj.platform = ?")
        params.append(platform)
    if status is not None:
        clauses.append("pj.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            {where}
            ORDER BY pj.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()


def _claim_publish_job_where(
    where_sql: str,
    params: tuple[Any, ...],
    *,
    order_by: str = "",
) -> sqlite3.Row | None:
    ensure_dirs()
    con = sqlite3.connect(str(db_path()), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode  = WAL")
    now = now_utc()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            f"""
            SELECT *
            FROM publish_jobs
            WHERE {where_sql}
            {order_by}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None

        attempt_count = int(row["attempt_count"] or 0) + 1
        result = con.execute(
            """
            UPDATE publish_jobs
            SET status='uploading',
                started_at=COALESCE(started_at, ?),
                last_attempt_at=?,
                next_attempt_at=NULL,
                error=NULL,
                updated_at=?,
                attempt_count=?
            WHERE id=? AND status=?
            """,
            (
                now,
                now,
                now,
                attempt_count,
                int(row["id"]),
                row["status"],
            ),
        )
        if result.rowcount == 0:
            con.execute("COMMIT")
            return None

        updated = con.execute(
            "SELECT * FROM publish_jobs WHERE id=?",
            (int(row["id"]),),
        ).fetchone()
        con.execute("COMMIT")
        return updated
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def claim_publish_job(job_id: int, *, force: bool = False) -> sqlite3.Row | None:
    from .publish_schedule import schedule_state

    job = get_publish_job(int(job_id))
    if job is None:
        return None
    state = schedule_state(job["upload_at"], job["overdue_approved_at"])
    if not force and state in {"waiting", "overdue"}:
        if state == "waiting":
            raise ValueError("Время загрузки этой задачи ещё не наступило.")
        raise ValueError("Задача просрочена. Сначала разрешите её запуск.")
    return _claim_publish_job_where(
        "id=? AND status IN ('queued', 'failed')",
        (job_id,),
    )


def claim_next_publish_job() -> sqlite3.Row | None:
    now = now_utc()
    return _claim_publish_job_where(
        """
        upload_at IS NULL AND (
            status='queued'
            OR (status='failed' AND next_attempt_at IS NOT NULL AND next_attempt_at <= ?)
        )
        """,
        (now,),
        order_by=(
            "ORDER BY CASE WHEN status='queued' THEN 0 ELSE 1 END, "
            "COALESCE(next_attempt_at, created_at) ASC, id ASC"
        ),
    )


def claim_next_scheduled_publish_job() -> sqlite3.Row | None:
    from .publish_schedule import OVERDUE_GRACE, utc_iso
    from datetime import datetime, timezone

    now_dt = datetime.now(timezone.utc)
    now = utc_iso(now_dt)
    cutoff = utc_iso(now_dt - OVERDUE_GRACE)
    return _claim_publish_job_where(
        """
        upload_at IS NOT NULL AND (
            (
                status='queued'
                AND upload_at <= ?
                AND (upload_at >= ? OR overdue_approved_at IS NOT NULL)
            )
            OR (
                status='failed'
                AND next_attempt_at IS NOT NULL
                AND next_attempt_at <= ?
                AND (upload_at >= ? OR overdue_approved_at IS NOT NULL)
            )
        )
        """,
        (now, cutoff, now, cutoff),
        order_by=(
            "ORDER BY CASE WHEN status='queued' THEN 0 ELSE 1 END, "
            "COALESCE(next_attempt_at, upload_at) ASC, id ASC"
        ),
    )


def mark_publish_uploading(job_id: int) -> None:
    now = now_utc()
    with connect() as con:
        con.execute(
            """
            UPDATE publish_jobs
            SET status='uploading',
                started_at=COALESCE(started_at, ?),
                last_attempt_at=COALESCE(last_attempt_at, ?),
                next_attempt_at=NULL,
                error=NULL,
                updated_at=?
            WHERE id=?
            """,
            (now, now, now, job_id),
        )


def mark_publish_done(job_id: int, youtube_video_id: str, youtube_url: str) -> None:
    now = now_utc()
    with connect() as con:
        con.execute(
            """
            UPDATE publish_jobs
            SET status='done', youtube_video_id=?, youtube_url=?,
                error=NULL, next_attempt_at=NULL, finished_at=?, updated_at=?
            WHERE id=?
            """,
            (youtube_video_id, youtube_url, now, now, job_id),
        )
        job = con.execute("SELECT clip_id FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
        if job is not None:
            identity = _workspace_identity_for_publish_clip(con, int(job["clip_id"]))
            if identity is not None:
                _upsert_workspace_status(con, identity[0], identity[1], "uploaded", now)


def mark_publish_failed(
    job_id: int,
    error: str,
    retryable: bool = True,
    next_attempt_at: str | None = None,
) -> None:
    now = now_utc()
    with connect() as con:
        con.execute(
            """
            UPDATE publish_jobs
            SET status='failed', error=?, next_attempt_at=?, finished_at=?, updated_at=?
            WHERE id=?
            """,
            (
                error,
                next_attempt_at if retryable else None,
                now,
                now,
                job_id,
            ),
        )
        job = con.execute("SELECT clip_id FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
        if job is not None:
            identity = _workspace_identity_for_publish_clip(con, int(job["clip_id"]))
            if identity is not None:
                _upsert_workspace_status(con, identity[0], identity[1], "failed", now)


def retry_publish_job(job_id: int) -> bool:
    now = now_utc()
    with connect() as con:
        job = con.execute("SELECT clip_id FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
        result = con.execute(
            """
            UPDATE publish_jobs
            SET status='queued',
                error=NULL,
                started_at=NULL,
                finished_at=NULL,
                youtube_video_id=NULL,
                youtube_url=NULL,
                next_attempt_at=NULL,
                updated_at=?
            WHERE id=? AND status IN ('failed', 'cancelled')
            """,
            (now, job_id),
        )
        ok = result.rowcount > 0
        if ok and job is not None:
            identity = _workspace_identity_for_publish_clip(con, int(job["clip_id"]))
            if identity is not None:
                _upsert_workspace_status(con, identity[0], identity[1], "queued", now)
        return ok


def cancel_publish_job(job_id: int) -> bool:
    now = now_utc()
    with connect() as con:
        job = con.execute("SELECT clip_id FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
        result = con.execute(
            """
            UPDATE publish_jobs
            SET status='cancelled', next_attempt_at=NULL, finished_at=?, updated_at=?
            WHERE id=? AND status IN ('queued', 'failed')
            """,
            (now, now, job_id),
        )
        ok = result.rowcount > 0
        if ok and job is not None:
            identity = _workspace_identity_for_publish_clip(con, int(job["clip_id"]))
            if identity is not None:
                _upsert_workspace_status(con, identity[0], identity[1], "draft", now)
        return ok


def list_recent_errors(limit: int = 5) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT 'job' AS kind, id, video_id, status, error, finished_at AS at
            FROM jobs
            WHERE error IS NOT NULL AND error != ''
            UNION ALL
            SELECT 'clip' AS kind, id, video_id, status, error, rendered_at AS at
            FROM clips
            WHERE error IS NOT NULL AND error != ''
            UNION ALL
            SELECT 'review' AS kind, id, video_id, status, error, finished_at AS at
            FROM review_sessions
            WHERE error IS NOT NULL AND error != ''
            ORDER BY at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def set_clip_rendering(clip_id: int, temp_path: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE clips SET status='rendering', started_at=?, temp_path=? WHERE id=?",
            (now_utc(), temp_path, clip_id),
        )


def set_clip_done(clip_id: int, output_path: str) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE clips
            SET status='done', rendered_at=?, output_path=?, temp_path=NULL
            WHERE id=?
            """,
            (now_utc(), output_path, clip_id),
        )


def set_clip_failed(clip_id: int, error: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE clips SET status='failed', rendered_at=?, error=? WHERE id=?",
            (now_utc(), error, clip_id),
        )


def reset_clip_to_queued(clip_id: int) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE clips
            SET status='queued', error=NULL, started_at=NULL,
                rendered_at=NULL, temp_path=NULL
            WHERE id=?
            """,
            (clip_id,),
        )


# ---------------------------------------------------------------------------
# template-driven editing models
# ---------------------------------------------------------------------------

_UNSET = object()
EDIT_JOB_STATUSES = {"queued", "rendering", "done", "failed", "cancelled"}
EDIT_JOB_REVIEW_STATUSES = {"pending", "approved", "rejected"}


def _normalize_recipe_json(value: Any, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("recipe_json is required.")
        return None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"recipe_json must contain valid JSON: {exc.msg}") from exc
        return value
    raise ValueError("recipe_json must be a dict, JSON string, or None.")


def _updated_value(row: sqlite3.Row, field: str, value: Any) -> Any:
    return row[field] if value is _UNSET else value


def create_reaction_asset(
    *,
    name: str,
    file_path: str,
    duration_sec: float | None = None,
    tags: str | None = None,
    mood: str | None = None,
    language: str | None = None,
    enabled: bool = True,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO reaction_assets
                (name, file_path, duration_sec, tags, mood, language,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                file_path,
                duration_sec,
                tags,
                mood,
                language,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_reaction_asset(asset_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM reaction_assets WHERE id=?",
            (int(asset_id),),
        ).fetchone()


def list_reaction_assets(enabled: bool | None = None) -> list[sqlite3.Row]:
    with connect() as con:
        if enabled is None:
            return con.execute(
                "SELECT * FROM reaction_assets ORDER BY id ASC"
            ).fetchall()
        return con.execute(
            "SELECT * FROM reaction_assets WHERE enabled=? ORDER BY id ASC",
            (1 if enabled else 0,),
        ).fetchall()


def update_reaction_asset(
    asset_id: int,
    *,
    name: Any = _UNSET,
    file_path: Any = _UNSET,
    duration_sec: Any = _UNSET,
    tags: Any = _UNSET,
    mood: Any = _UNSET,
    language: Any = _UNSET,
    enabled: Any = _UNSET,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM reaction_assets WHERE id=?",
            (int(asset_id),),
        ).fetchone()
        if row is None:
            return False
        enabled_value = row["enabled"] if enabled is _UNSET else (1 if enabled else 0)
        con.execute(
            """
            UPDATE reaction_assets
            SET name=?, file_path=?, duration_sec=?, tags=?, mood=?, language=?,
                enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                _updated_value(row, "name", name),
                _updated_value(row, "file_path", file_path),
                _updated_value(row, "duration_sec", duration_sec),
                _updated_value(row, "tags", tags),
                _updated_value(row, "mood", mood),
                _updated_value(row, "language", language),
                enabled_value,
                now_utc(),
                int(asset_id),
            ),
        )
        return True


def disable_reaction_asset(asset_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "UPDATE reaction_assets SET enabled=0, updated_at=? WHERE id=?",
            (now_utc(), int(asset_id)),
        )
        return result.rowcount > 0


def create_reaction_pool(
    *,
    name: str,
    description: str | None = None,
    enabled: bool = True,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO reaction_pools
                (name, description, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, description, 1 if enabled else 0, now, now),
        )
        return int(cur.lastrowid)


def get_reaction_pool(pool_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM reaction_pools WHERE id=?",
            (int(pool_id),),
        ).fetchone()


def list_reaction_pools(enabled: bool | None = None) -> list[sqlite3.Row]:
    with connect() as con:
        if enabled is None:
            return con.execute(
                "SELECT * FROM reaction_pools ORDER BY id ASC"
            ).fetchall()
        return con.execute(
            "SELECT * FROM reaction_pools WHERE enabled=? ORDER BY id ASC",
            (1 if enabled else 0,),
        ).fetchall()


def update_reaction_pool(
    pool_id: int,
    *,
    name: Any = _UNSET,
    description: Any = _UNSET,
    enabled: Any = _UNSET,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM reaction_pools WHERE id=?",
            (int(pool_id),),
        ).fetchone()
        if row is None:
            return False
        enabled_value = row["enabled"] if enabled is _UNSET else (1 if enabled else 0)
        con.execute(
            """
            UPDATE reaction_pools
            SET name=?, description=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                _updated_value(row, "name", name),
                _updated_value(row, "description", description),
                enabled_value,
                now_utc(),
                int(pool_id),
            ),
        )
        return True


def list_reaction_pools_with_counts(enabled: bool | None = None) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("rp.enabled=?")
        params.append(1 if enabled else 0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        return con.execute(
            f"""
            SELECT rp.*, COUNT(rpi.id) AS item_count
            FROM reaction_pools rp
            LEFT JOIN reaction_pool_items rpi
              ON rpi.pool_id=rp.id AND rpi.enabled=1
            {where}
            GROUP BY rp.id
            ORDER BY rp.id ASC
            """,
            params,
        ).fetchall()


def add_reaction_to_pool(
    pool_id: int,
    reaction_asset_id: int,
    weight: int = 1,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO reaction_pool_items
                (pool_id, reaction_asset_id, weight, enabled, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (int(pool_id), int(reaction_asset_id), int(weight), now_utc()),
        )
        return int(cur.lastrowid)


def upsert_reaction_pool_item(
    pool_id: int,
    reaction_asset_id: int,
    weight: int = 1,
) -> int:
    normalized_weight = int(weight)
    if normalized_weight <= 0:
        raise ValueError("Reaction pool item weight must be greater than zero.")
    with connect() as con:
        con.execute(
            """
            INSERT INTO reaction_pool_items
                (pool_id, reaction_asset_id, weight, enabled, created_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(pool_id, reaction_asset_id) DO UPDATE SET
                weight=excluded.weight,
                enabled=1
            """,
            (int(pool_id), int(reaction_asset_id), normalized_weight, now_utc()),
        )
        row = con.execute(
            """
            SELECT id
            FROM reaction_pool_items
            WHERE pool_id=? AND reaction_asset_id=?
            """,
            (int(pool_id), int(reaction_asset_id)),
        ).fetchone()
        if row is None:
            raise RuntimeError("Reaction pool item was saved but cannot be loaded.")
        return int(row["id"])


def list_reaction_pool_items(pool_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM reaction_pool_items
            WHERE pool_id=?
            ORDER BY id ASC
            """,
            (int(pool_id),),
        ).fetchall()


def list_reaction_pool_items_with_assets(pool_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT
                rpi.id AS item_id,
                rpi.pool_id,
                rpi.reaction_asset_id,
                rpi.weight,
                rpi.enabled,
                rpi.created_at,
                ra.name AS asset_name,
                ra.file_path,
                ra.tags,
                ra.mood,
                ra.language,
                ra.enabled AS asset_enabled
            FROM reaction_pool_items rpi
            JOIN reaction_assets ra ON ra.id=rpi.reaction_asset_id
            WHERE rpi.pool_id=?
            ORDER BY rpi.id ASC
            """,
            (int(pool_id),),
        ).fetchall()


def remove_reaction_from_pool(pool_id: int, reaction_asset_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            DELETE FROM reaction_pool_items
            WHERE pool_id=? AND reaction_asset_id=?
            """,
            (int(pool_id), int(reaction_asset_id)),
        )
        return result.rowcount > 0


def create_edit_template(
    *,
    key: str,
    name: str,
    recipe_json: dict[str, Any] | str,
    description: str | None = None,
    renderer: str = "ffmpeg",
    enabled: bool = True,
) -> int:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=True)
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO edit_templates
                (key, name, description, renderer, recipe_json,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                name,
                description,
                renderer,
                normalized_recipe,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_edit_template(template_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM edit_templates WHERE id=?",
            (int(template_id),),
        ).fetchone()


def get_edit_template_by_key(key: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM edit_templates WHERE key=?",
            (key,),
        ).fetchone()


def list_edit_templates(enabled: bool | None = None) -> list[sqlite3.Row]:
    with connect() as con:
        if enabled is None:
            return con.execute(
                "SELECT * FROM edit_templates ORDER BY id ASC"
            ).fetchall()
        return con.execute(
            "SELECT * FROM edit_templates WHERE enabled=? ORDER BY id ASC",
            (1 if enabled else 0,),
        ).fetchall()


def update_edit_template(
    template_id: int,
    *,
    key: Any = _UNSET,
    name: Any = _UNSET,
    description: Any = _UNSET,
    renderer: Any = _UNSET,
    recipe_json: Any = _UNSET,
    enabled: Any = _UNSET,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM edit_templates WHERE id=?",
            (int(template_id),),
        ).fetchone()
        if row is None:
            return False
        normalized_recipe = (
            row["recipe_json"]
            if recipe_json is _UNSET
            else _normalize_recipe_json(recipe_json, required=True)
        )
        enabled_value = row["enabled"] if enabled is _UNSET else (1 if enabled else 0)
        con.execute(
            """
            UPDATE edit_templates
            SET key=?, name=?, description=?, renderer=?, recipe_json=?,
                enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                _updated_value(row, "key", key),
                _updated_value(row, "name", name),
                _updated_value(row, "description", description),
                _updated_value(row, "renderer", renderer),
                normalized_recipe,
                enabled_value,
                now_utc(),
                int(template_id),
            ),
        )
        return True


def disable_edit_template(template_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "UPDATE edit_templates SET enabled=0, updated_at=? WHERE id=?",
            (now_utc(), int(template_id)),
        )
        return result.rowcount > 0


def create_channel_profile(
    *,
    name: str,
    youtube_account_id: int | None = None,
    default_template_id: int | None = None,
    reaction_pool_id: int | None = None,
    title_template: str | None = None,
    description_template: str | None = None,
    tags_template: str | None = None,
    default_privacy: str | None = None,
    default_category_id: str | None = None,
    enabled: bool = True,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO channel_profiles
                (name, youtube_account_id, default_template_id, reaction_pool_id,
                 title_template, description_template, tags_template,
                 default_privacy, default_category_id, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                youtube_account_id,
                default_template_id,
                reaction_pool_id,
                title_template,
                description_template,
                tags_template,
                default_privacy,
                default_category_id,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_channel_profile(profile_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM channel_profiles WHERE id=?",
            (int(profile_id),),
        ).fetchone()


def get_channel_profile_by_youtube_account(account_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM channel_profiles
            WHERE youtube_account_id=?
            ORDER BY enabled DESC, id ASC
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()


def list_channel_profiles(enabled: bool | None = None) -> list[sqlite3.Row]:
    with connect() as con:
        if enabled is None:
            return con.execute(
                "SELECT * FROM channel_profiles ORDER BY id ASC"
            ).fetchall()
        return con.execute(
            "SELECT * FROM channel_profiles WHERE enabled=? ORDER BY id ASC",
            (1 if enabled else 0,),
        ).fetchall()


def list_channel_profiles_with_details(enabled: bool | None = None) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("cp.enabled=?")
        params.append(1 if enabled else 0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        return con.execute(
            f"""
            SELECT
                cp.*,
                sa.channel_title AS youtube_channel_title,
                sa.display_name AS youtube_display_name,
                et.name AS default_template_name,
                rp.name AS reaction_pool_name
            FROM channel_profiles cp
            LEFT JOIN social_accounts sa ON sa.id=cp.youtube_account_id
            LEFT JOIN edit_templates et ON et.id=cp.default_template_id
            LEFT JOIN reaction_pools rp ON rp.id=cp.reaction_pool_id
            {where}
            ORDER BY cp.id ASC
            """,
            params,
        ).fetchall()


def update_channel_profile(
    profile_id: int,
    *,
    name: Any = _UNSET,
    youtube_account_id: Any = _UNSET,
    default_template_id: Any = _UNSET,
    reaction_pool_id: Any = _UNSET,
    title_template: Any = _UNSET,
    description_template: Any = _UNSET,
    tags_template: Any = _UNSET,
    default_privacy: Any = _UNSET,
    default_category_id: Any = _UNSET,
    enabled: Any = _UNSET,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM channel_profiles WHERE id=?",
            (int(profile_id),),
        ).fetchone()
        if row is None:
            return False
        enabled_value = row["enabled"] if enabled is _UNSET else (1 if enabled else 0)
        con.execute(
            """
            UPDATE channel_profiles
            SET name=?, youtube_account_id=?, default_template_id=?, reaction_pool_id=?,
                title_template=?, description_template=?, tags_template=?,
                default_privacy=?, default_category_id=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                _updated_value(row, "name", name),
                _updated_value(row, "youtube_account_id", youtube_account_id),
                _updated_value(row, "default_template_id", default_template_id),
                _updated_value(row, "reaction_pool_id", reaction_pool_id),
                _updated_value(row, "title_template", title_template),
                _updated_value(row, "description_template", description_template),
                _updated_value(row, "tags_template", tags_template),
                _updated_value(row, "default_privacy", default_privacy),
                _updated_value(row, "default_category_id", default_category_id),
                enabled_value,
                now_utc(),
                int(profile_id),
            ),
        )
        return True


def disable_channel_profile(profile_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "UPDATE channel_profiles SET enabled=0, updated_at=? WHERE id=?",
            (now_utc(), int(profile_id)),
        )
        return result.rowcount > 0


def create_edit_job(
    *,
    workspace_item_key: str,
    channel_profile_id: int | None = None,
    template_id: int | None = None,
    reaction_asset_id: int | None = None,
    input_path: str | None = None,
    output_path: str | None = None,
    edited_path: str | None = None,
    renderer: str = "ffmpeg",
    recipe_json: dict[str, Any] | str | None = None,
) -> int:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=False)
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO edit_jobs
                (workspace_item_key, channel_profile_id, template_id,
                 reaction_asset_id, input_path, output_path, edited_path,
                 status, renderer, recipe_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (
                workspace_item_key,
                channel_profile_id,
                template_id,
                reaction_asset_id,
                input_path,
                output_path,
                edited_path,
                renderer,
                normalized_recipe,
                now_utc(),
            ),
        )
        return int(cur.lastrowid)


def get_edit_job(job_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM edit_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()


def list_edit_jobs(
    status: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if status is not None and status not in EDIT_JOB_STATUSES:
        raise ValueError(
            "edit job status must be one of: queued, rendering, done, failed, cancelled."
        )
    with connect() as con:
        if status is None:
            return con.execute(
                "SELECT * FROM edit_jobs ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return con.execute(
            "SELECT * FROM edit_jobs WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, int(limit)),
        ).fetchall()


def list_edit_jobs_with_details(
    status: str | None = None,
    review_status: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    if status is not None and status not in EDIT_JOB_STATUSES:
        raise ValueError(
            "edit job status must be one of: queued, rendering, done, failed, cancelled."
        )
    if (
        review_status is not None
        and review_status not in EDIT_JOB_REVIEW_STATUSES
    ):
        raise ValueError(
            "edit job review status must be one of: pending, approved, rejected."
        )
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("ej.status=?")
        params.append(status)
    if review_status is not None:
        clauses.append("ej.review_status=?")
        params.append(review_status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    with connect() as con:
        return con.execute(
            f"""
            SELECT
                ej.*,
                cp.name AS channel_profile_name,
                et.name AS template_name,
                et.key AS template_key,
                ra.name AS reaction_asset_name
            FROM edit_jobs ej
            LEFT JOIN channel_profiles cp ON cp.id=ej.channel_profile_id
            LEFT JOIN edit_templates et ON et.id=ej.template_id
            LEFT JOIN reaction_assets ra ON ra.id=ej.reaction_asset_id
            {where}
            ORDER BY ej.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()


def find_existing_edit_job(
    workspace_item_key: str,
    channel_profile_id: int,
    template_id: int,
    *,
    include_done: bool = True,
) -> sqlite3.Row | None:
    statuses = ("queued", "rendering", "done") if include_done else ("queued", "rendering")
    placeholders = ",".join("?" for _ in statuses)
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM edit_jobs
            WHERE workspace_item_key=?
              AND channel_profile_id=?
              AND template_id=?
              AND status IN ({placeholders})
            ORDER BY
                CASE status
                    WHEN 'rendering' THEN 0
                    WHEN 'queued' THEN 1
                    WHEN 'done' THEN 2
                    ELSE 3
                END,
                id DESC
            LIMIT 1
            """,
            (
                workspace_item_key,
                int(channel_profile_id),
                int(template_id),
                *statuses,
            ),
        ).fetchone()


def update_edit_job_plan(
    job_id: int,
    *,
    input_path: str | None,
    output_path: str | None,
    recipe_json: dict[str, Any] | str | None,
) -> bool:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=False)
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET input_path=?, output_path=?, recipe_json=?
            WHERE id=?
            """,
            (input_path, output_path, normalized_recipe, int(job_id)),
        )
        return result.rowcount > 0


def _claim_edit_job_where(
    where_sql: str,
    params: tuple[Any, ...],
    *,
    order_by: str = "",
) -> sqlite3.Row | None:
    ensure_dirs()
    con = sqlite3.connect(str(db_path()), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    now = now_utc()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            f"""
            SELECT *
            FROM edit_jobs
            WHERE {where_sql}
            {order_by}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None

        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='rendering',
                edited_path=NULL,
                error=NULL,
                started_at=?,
                finished_at=NULL,
                review_status='pending',
                reviewed_at=NULL,
                review_note=NULL
            WHERE id=? AND status=?
            """,
            (now, int(row["id"]), str(row["status"])),
        )
        if result.rowcount == 0:
            con.execute("COMMIT")
            return None

        updated = con.execute(
            "SELECT * FROM edit_jobs WHERE id=?",
            (int(row["id"]),),
        ).fetchone()
        con.execute("COMMIT")
        return updated
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def claim_edit_job(
    job_id: int,
    *,
    allowed_statuses: tuple[str, ...] = ("queued",),
) -> sqlite3.Row | None:
    if not allowed_statuses:
        return None
    invalid = set(allowed_statuses) - EDIT_JOB_STATUSES
    if invalid:
        raise ValueError(f"Unknown edit job statuses: {', '.join(sorted(invalid))}")
    placeholders = ",".join("?" for _ in allowed_statuses)
    return _claim_edit_job_where(
        f"id=? AND status IN ({placeholders})",
        (int(job_id), *allowed_statuses),
    )


def claim_next_edit_job() -> sqlite3.Row | None:
    return _claim_edit_job_where(
        "status='queued'",
        (),
        order_by="ORDER BY created_at ASC, id ASC",
    )


def mark_edit_job_rendering(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='rendering',
                started_at=?,
                finished_at=NULL,
                error=NULL,
                review_status='pending',
                reviewed_at=NULL,
                review_note=NULL
            WHERE id=?
            """,
            (now_utc(), int(job_id)),
        )
        return result.rowcount > 0


def mark_edit_job_done(job_id: int, edited_path: str) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='done', edited_path=?, error=NULL, finished_at=?
            WHERE id=?
            """,
            (edited_path, now_utc(), int(job_id)),
        )
        return result.rowcount > 0


def mark_edit_job_failed(job_id: int, error: str) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='failed', error=?, finished_at=?
            WHERE id=?
            """,
            (error, now_utc(), int(job_id)),
        )
        return result.rowcount > 0


def set_edit_job_review_status(
    job_id: int,
    review_status: str,
    review_note: str | None = None,
) -> bool:
    normalized_status = str(review_status or "").strip().lower()
    if normalized_status not in EDIT_JOB_REVIEW_STATUSES:
        raise ValueError(
            "review_status must be one of: pending, approved, rejected."
        )

    with connect() as con:
        job = con.execute(
            "SELECT status FROM edit_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if job is None:
            return False
        if (
            normalized_status in {"approved", "rejected"}
            and str(job["status"]) != "done"
        ):
            raise ValueError(
                "Approve/reject разрешены только для edit job со status=done."
            )

        reviewed_at = (
            now_utc()
            if normalized_status in {"approved", "rejected"}
            else None
        )
        result = con.execute(
            """
            UPDATE edit_jobs
            SET review_status=?, reviewed_at=?, review_note=?
            WHERE id=?
            """,
            (
                normalized_status,
                reviewed_at,
                review_note,
                int(job_id),
            ),
        )
        return result.rowcount > 0


def cancel_edit_job(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='cancelled', finished_at=?
            WHERE id=? AND status IN ('queued', 'failed')
            """,
            (now_utc(), int(job_id)),
        )
        return result.rowcount > 0


def retry_edit_job(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status='queued', error=NULL, started_at=NULL, finished_at=NULL
            WHERE id=? AND status IN ('failed', 'cancelled')
            """,
            (int(job_id),),
        )
        return result.rowcount > 0


def create_studio_project(
    *,
    main_workspace_path: str,
    template_key: str,
    reaction_asset_id: int | None,
    recipe_json: dict[str, Any] | str,
    workspace_item_key: str | None = None,
    studio_template_id: int | None = None,
    reaction_pool_id: int | None = None,
) -> int:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=True)
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO studio_projects
                (workspace_item_key, main_workspace_path, template_key,
                 reaction_asset_id, recipe_json, created_at, updated_at,
                 studio_template_id, reaction_pool_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_item_key,
                main_workspace_path,
                template_key,
                reaction_asset_id,
                normalized_recipe,
                now,
                now,
                studio_template_id,
                reaction_pool_id,
            ),
        )
        return int(cur.lastrowid)


def get_studio_project(project_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM studio_projects WHERE id=?",
            (int(project_id),),
        ).fetchone()


def update_studio_project(
    project_id: int,
    *,
    main_workspace_path: str,
    template_key: str,
    reaction_asset_id: int | None,
    recipe_json: dict[str, Any] | str,
    workspace_item_key: str | None = None,
    studio_template_id: int | None = None,
    reaction_pool_id: int | None = None,
) -> bool:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=True)
    with connect() as con:
        result = con.execute(
            """
            UPDATE studio_projects
            SET workspace_item_key=?, main_workspace_path=?, template_key=?,
                reaction_asset_id=?, recipe_json=?, updated_at=?,
                studio_template_id=?, reaction_pool_id=?
            WHERE id=?
            """,
            (
                workspace_item_key,
                main_workspace_path,
                template_key,
                reaction_asset_id,
                normalized_recipe,
                now_utc(),
                studio_template_id,
                reaction_pool_id,
                int(project_id),
            ),
        )
        return result.rowcount > 0


def create_remotion_render_job(
    studio_project_id: int,
    output_path: str | None = None,
    *,
    renderer_engine: str = "ffmpeg_fast",
    render_profile: str = "low_540p",
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_render_jobs
                (studio_project_id, status, output_path, renderer_engine,
                 render_profile, duration_limit_sec, start_offset_sec,
                 full_length, created_at)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(studio_project_id),
                output_path,
                renderer_engine,
                render_profile,
                duration_limit_sec,
                float(start_offset_sec or 0),
                1 if full_length else 0,
                now_utc(),
            ),
        )
        return int(cur.lastrowid)


def _normalize_json_object(value: Any, *, default: dict[str, Any] | None = None) -> str:
    resolved = default if value is None else value
    if resolved is None:
        resolved = {}
    if isinstance(resolved, dict):
        return json.dumps(resolved, ensure_ascii=False)
    if isinstance(resolved, str):
        try:
            parsed = json.loads(resolved)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON object must be valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON value must be an object.")
        return resolved
    raise ValueError("JSON value must be a dict or JSON string.")


def _normalize_json_array(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON array must be valid JSON: {exc.msg}") from exc
        if not isinstance(parsed, list):
            raise ValueError("JSON value must be an array.")
        return value
    raise ValueError("JSON value must be a list or JSON string.")


def update_remotion_render_job_output(job_id: int, output_path: str) -> bool:
    with connect() as con:
        result = con.execute(
            "UPDATE remotion_render_jobs SET output_path=? WHERE id=?",
            (output_path, int(job_id)),
        )
        return result.rowcount > 0


def update_remotion_render_job_process(
    job_id: int,
    *,
    worker_pid: int | None = None,
    heartbeat: bool = True,
) -> bool:
    assignments = ["last_heartbeat_at=?"]
    values: list[Any] = [now_utc()]
    if worker_pid is not None:
        assignments.extend(["worker_pid=?", "worker_started_at=COALESCE(worker_started_at, ?)"])
        values.extend([int(worker_pid), now_utc()])
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE remotion_render_jobs
            SET {", ".join(assignments)}
            WHERE id=? AND status='rendering'
            """,
            (*values, int(job_id)),
        )
        return result.rowcount > 0


def get_remotion_render_job(job_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM remotion_render_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()


def _sync_remotion_render_batch_in_connection(
    con: sqlite3.Connection,
    batch_id: int,
) -> None:
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM remotion_render_batch_items
        WHERE batch_id=?
        GROUP BY status
        """,
        (int(batch_id),),
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    total = sum(counts.values())
    done = counts.get("done", 0)
    failed = counts.get("failed", 0)
    cancelled = counts.get("cancelled", 0)
    rendering = counts.get("rendering", 0)
    queued = counts.get("queued", 0)
    now = now_utc()
    if total == 0:
        status = "draft"
        finished_at = None
    elif rendering:
        status = "running"
        finished_at = None
    elif queued:
        status = "queued"
        finished_at = None
    elif failed:
        status = "failed"
        finished_at = now
    elif cancelled:
        status = "cancelled"
        finished_at = now
    else:
        status = "done"
        finished_at = now
    con.execute(
        """
        UPDATE remotion_render_batches
        SET status=?,
            total_items=?,
            done_items=?,
            failed_items=?,
            finished_at=CASE WHEN ? IS NULL THEN finished_at ELSE COALESCE(finished_at, ?) END,
            updated_at=?
        WHERE id=?
        """,
        (
            status,
            total,
            done,
            failed,
            finished_at,
            finished_at,
            now,
            int(batch_id),
        ),
    )


def sync_remotion_render_batch(batch_id: int) -> None:
    with connect() as con:
        _sync_remotion_render_batch_in_connection(con, int(batch_id))


def _batch_id_for_render_job(
    con: sqlite3.Connection,
    job_id: int,
) -> int | None:
    row = con.execute(
        "SELECT batch_id FROM remotion_render_batch_items WHERE render_job_id=?",
        (int(job_id),),
    ).fetchone()
    return int(row["batch_id"]) if row is not None else None


def get_active_remotion_render_job() -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM remotion_render_jobs
            WHERE status = 'rendering'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()


def claim_remotion_render_job(job_id: int) -> sqlite3.Row | None:
    ensure_dirs()
    con = sqlite3.connect(str(db_path()), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    try:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM remotion_render_jobs WHERE status='rendering' LIMIT 1"
        ).fetchone() is not None:
            con.execute("COMMIT")
            return None
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='rendering',
                started_at=?,
                finished_at=NULL,
                error=NULL,
                worker_pid=NULL,
                worker_started_at=?,
                last_heartbeat_at=?,
                stdout_tail=NULL,
                stderr_tail=NULL,
                returncode=NULL,
                elapsed_sec=NULL
            WHERE id=? AND status='queued'
            """,
            (now_utc(), now_utc(), now_utc(), int(job_id)),
        )
        if result.rowcount == 0:
            con.execute("COMMIT")
            return None
        row = con.execute(
            "SELECT * FROM remotion_render_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        batch_id = _batch_id_for_render_job(con, int(job_id))
        if batch_id is not None:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='rendering', error=NULL, updated_at=?
                WHERE render_job_id=?
                """,
                (now_utc(), int(job_id)),
            )
            con.execute(
                """
                UPDATE remotion_render_batches
                SET status='running',
                    started_at=COALESCE(started_at, ?),
                    updated_at=?
                WHERE id=?
                """,
                (now_utc(), now_utc(), batch_id),
            )
        con.execute("COMMIT")
        return row
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def claim_next_remotion_render_job() -> sqlite3.Row | None:
    ensure_dirs()
    con = sqlite3.connect(str(db_path()), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    try:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM remotion_render_jobs WHERE status='rendering' LIMIT 1"
        ).fetchone() is not None:
            con.execute("COMMIT")
            return None
        queued = con.execute(
            """
            SELECT id
            FROM remotion_render_jobs
            WHERE status='queued'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if queued is None:
            con.execute("COMMIT")
            return None
        job_id = int(queued["id"])
        now = now_utc()
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='rendering',
                started_at=?,
                finished_at=NULL,
                error=NULL,
                worker_pid=NULL,
                worker_started_at=?,
                last_heartbeat_at=?,
                stdout_tail=NULL,
                stderr_tail=NULL,
                returncode=NULL,
                elapsed_sec=NULL
            WHERE id=? AND status='queued'
            """,
            (now, now, now, job_id),
        )
        if result.rowcount == 0:
            con.execute("COMMIT")
            return None
        row = con.execute(
            "SELECT * FROM remotion_render_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        batch_id = _batch_id_for_render_job(con, job_id)
        if batch_id is not None:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='rendering', error=NULL, updated_at=?
                WHERE render_job_id=?
                """,
                (now, job_id),
            )
            con.execute(
                """
                UPDATE remotion_render_batches
                SET status='running',
                    started_at=COALESCE(started_at, ?),
                    updated_at=?
                WHERE id=?
                """,
                (now, now, batch_id),
            )
        con.execute("COMMIT")
        return row
    except sqlite3.IntegrityError:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        return None
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def mark_remotion_render_job_done(
    job_id: int,
    output_path: str,
    *,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    returncode: int | None = 0,
    elapsed_sec: float | None = None,
) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='done',
                output_path=?,
                error=NULL,
                finished_at=?,
                last_heartbeat_at=?,
                stdout_tail=?,
                stderr_tail=?,
                returncode=?,
                elapsed_sec=?
            WHERE id=?
            """,
            (
                output_path,
                now_utc(),
                now_utc(),
                stdout_tail,
                stderr_tail,
                returncode,
                elapsed_sec,
                int(job_id),
            ),
        )
        batch_id = _batch_id_for_render_job(con, int(job_id))
        if batch_id is not None:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='done', error=NULL, updated_at=?
                WHERE render_job_id=?
                """,
                (now_utc(), int(job_id)),
            )
            _sync_remotion_render_batch_in_connection(con, batch_id)
        return result.rowcount > 0


def mark_remotion_render_job_failed(
    job_id: int,
    error: str,
    *,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    returncode: int | None = None,
    elapsed_sec: float | None = None,
) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='failed',
                error=?,
                finished_at=?,
                last_heartbeat_at=?,
                stdout_tail=COALESCE(?, stdout_tail),
                stderr_tail=COALESCE(?, stderr_tail),
                returncode=COALESCE(?, returncode),
                elapsed_sec=COALESCE(?, elapsed_sec)
            WHERE id=?
            """,
            (
                error,
                now_utc(),
                now_utc(),
                stdout_tail,
                stderr_tail,
                returncode,
                elapsed_sec,
                int(job_id),
            ),
        )
        batch_id = _batch_id_for_render_job(con, int(job_id))
        if batch_id is not None:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='failed', error=?, updated_at=?
                WHERE render_job_id=?
                """,
                (error, now_utc(), int(job_id)),
            )
            _sync_remotion_render_batch_in_connection(con, batch_id)
        return result.rowcount > 0


def cancel_remotion_render_job(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='cancelled', finished_at=?
            WHERE id=? AND status='queued'
            """,
            (now_utc(), int(job_id)),
        )
        batch_id = _batch_id_for_render_job(con, int(job_id))
        if batch_id is not None and result.rowcount > 0:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='cancelled', updated_at=?
                WHERE render_job_id=?
                """,
                (now_utc(), int(job_id)),
            )
            _sync_remotion_render_batch_in_connection(con, batch_id)
        return result.rowcount > 0


def retry_remotion_render_job(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='queued',
                error=NULL,
                started_at=NULL,
                finished_at=NULL,
                worker_pid=NULL,
                worker_started_at=NULL,
                last_heartbeat_at=NULL,
                stdout_tail=NULL,
                stderr_tail=NULL,
                returncode=NULL,
                elapsed_sec=NULL
            WHERE id=? AND status IN ('failed', 'cancelled')
            """,
            (int(job_id),),
        )
        batch_id = _batch_id_for_render_job(con, int(job_id))
        if batch_id is not None and result.rowcount > 0:
            con.execute(
                """
                UPDATE remotion_render_batch_items
                SET status='queued', error=NULL, updated_at=?
                WHERE render_job_id=?
                """,
                (now_utc(), int(job_id)),
            )
            _sync_remotion_render_batch_in_connection(con, batch_id)
        return result.rowcount > 0


def retry_failed_remotion_render_batch(batch_id: int) -> int:
    with connect() as con:
        rows = con.execute(
            """
            SELECT render_job_id
            FROM remotion_render_batch_items
            WHERE batch_id=? AND status IN ('failed', 'cancelled')
            """,
            (int(batch_id),),
        ).fetchall()
        job_ids = [int(row["render_job_id"]) for row in rows]
        if not job_ids:
            _sync_remotion_render_batch_in_connection(con, int(batch_id))
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        con.execute(
            f"""
            UPDATE remotion_render_jobs
            SET status='queued',
                error=NULL,
                started_at=NULL,
                finished_at=NULL,
                worker_pid=NULL,
                worker_started_at=NULL,
                last_heartbeat_at=NULL,
                stdout_tail=NULL,
                stderr_tail=NULL,
                returncode=NULL,
                elapsed_sec=NULL
            WHERE id IN ({placeholders}) AND status IN ('failed', 'cancelled')
            """,
            job_ids,
        )
        con.execute(
            f"""
            UPDATE remotion_render_batch_items
            SET status='queued', error=NULL, updated_at=?
            WHERE render_job_id IN ({placeholders})
              AND status IN ('failed', 'cancelled')
            """,
            [now_utc(), *job_ids],
        )
        _sync_remotion_render_batch_in_connection(con, int(batch_id))
        return len(job_ids)


def fail_interrupted_remotion_render_jobs() -> int:
    with connect() as con:
        affected_batches = {
            int(row["batch_id"])
            for row in con.execute(
                """
                SELECT DISTINCT rbi.batch_id
                FROM remotion_render_batch_items rbi
                JOIN remotion_render_jobs rrj ON rrj.id = rbi.render_job_id
                WHERE rrj.status = 'rendering'
                """
            ).fetchall()
        }
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='failed',
                error='Remotion render был прерван перезапуском backend.',
                finished_at=?
            WHERE status = 'rendering'
            """,
            (now_utc(),),
        )
        con.execute(
            """
            UPDATE remotion_render_batch_items
            SET status='failed',
                error='Remotion render был прерван перезапуском backend.',
                updated_at=?
            WHERE render_job_id IN (
                SELECT id FROM remotion_render_jobs
                WHERE error='Remotion render был прерван перезапуском backend.'
            )
            """,
            (now_utc(),),
        )
        for batch_id in affected_batches:
            _sync_remotion_render_batch_in_connection(con, batch_id)
        return int(result.rowcount)


def create_remotion_render_batch(
    *,
    studio_template_id: int | None,
    template_key: str,
    name: str,
    source_mode: str,
    source_path: str | None = None,
    reaction_strategy: str = "fixed_asset",
    reaction_asset_id: int | None = None,
    reaction_pool_id: int | None = None,
    parameter_values_json: dict[str, Any] | str | None = None,
    renderer_engine: str = "ffmpeg_fast",
    render_profile: str = "low_540p",
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_render_batches
                (studio_template_id, template_key, name, source_mode, source_path,
                 reaction_strategy, reaction_asset_id, reaction_pool_id,
                 parameter_values_json, renderer_engine, render_profile,
                 duration_limit_sec, start_offset_sec, full_length,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                studio_template_id,
                template_key,
                name,
                source_mode,
                source_path,
                reaction_strategy,
                reaction_asset_id,
                reaction_pool_id,
                _normalize_json_object(parameter_values_json),
                renderer_engine,
                render_profile,
                duration_limit_sec,
                float(start_offset_sec or 0),
                1 if full_length else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def create_remotion_render_batch_item(
    *,
    batch_id: int,
    studio_project_id: int,
    render_job_id: int,
    main_workspace_path: str,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_render_batch_items
                (batch_id, studio_project_id, render_job_id,
                 main_workspace_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                int(batch_id),
                int(studio_project_id),
                int(render_job_id),
                main_workspace_path,
                now,
                now,
            ),
        )
        _sync_remotion_render_batch_in_connection(con, int(batch_id))
        return int(cur.lastrowid)


def get_remotion_render_batch(batch_id: int) -> sqlite3.Row | None:
    sync_remotion_render_batch(int(batch_id))
    with connect() as con:
        return con.execute(
            "SELECT * FROM remotion_render_batches WHERE id=?",
            (int(batch_id),),
        ).fetchone()


def list_remotion_render_batches(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM remotion_render_batches
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()


def list_remotion_render_batch_items(batch_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT rbi.*,
                   rrj.status AS render_status,
                   rrj.output_path AS output_path,
                   rrj.error AS render_error,
                   rrj.started_at AS render_started_at,
                   rrj.finished_at AS render_finished_at,
                   rrj.renderer_engine AS renderer_engine,
                   rrj.render_profile AS render_profile,
                   rrj.duration_limit_sec AS duration_limit_sec,
                   rrj.start_offset_sec AS start_offset_sec,
                   rrj.full_length AS full_length,
                   rrj.stdout_tail AS stdout_tail,
                   rrj.stderr_tail AS stderr_tail,
                   rrj.returncode AS returncode,
                   rrj.elapsed_sec AS elapsed_sec
            FROM remotion_render_batch_items rbi
            LEFT JOIN remotion_render_jobs rrj ON rrj.id = rbi.render_job_id
            WHERE rbi.batch_id=?
            ORDER BY rbi.id ASC
            """,
            (int(batch_id),),
        ).fetchall()


def cancel_remotion_render_batch(batch_id: int) -> int:
    with connect() as con:
        rows = con.execute(
            """
            SELECT render_job_id
            FROM remotion_render_batch_items
            WHERE batch_id=? AND status='queued'
            """,
            (int(batch_id),),
        ).fetchall()
        job_ids = [int(row["render_job_id"]) for row in rows]
        if not job_ids:
            _sync_remotion_render_batch_in_connection(con, int(batch_id))
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        now = now_utc()
        con.execute(
            f"""
            UPDATE remotion_render_jobs
            SET status='cancelled', finished_at=?
            WHERE id IN ({placeholders}) AND status='queued'
            """,
            [now, *job_ids],
        )
        con.execute(
            f"""
            UPDATE remotion_render_batch_items
            SET status='cancelled', updated_at=?
            WHERE render_job_id IN ({placeholders}) AND status='queued'
            """,
            [now, *job_ids],
        )
        _sync_remotion_render_batch_in_connection(con, int(batch_id))
        return len(job_ids)


def create_remotion_pipeline(
    *,
    name: str,
    studio_template_id: int | None,
    source_mode: str,
    source_path: str | None = None,
    source_paths_json: list[str] | str | None = None,
    recursive: bool = False,
    reaction_strategy: str = "fixed_asset",
    reaction_asset_id: int | None = None,
    reaction_pool_id: int | None = None,
    parameter_values_json: dict[str, Any] | str | None = None,
    output_policy_json: dict[str, Any] | str | None = None,
    enabled: bool = True,
    renderer_engine: str = "ffmpeg_fast",
    render_profile: str = "low_540p",
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_pipelines
                (name, studio_template_id, source_mode, source_path,
                 source_paths_json, recursive, reaction_strategy,
                 reaction_asset_id, reaction_pool_id, parameter_values_json,
                 output_policy_json, enabled, renderer_engine, render_profile,
                 duration_limit_sec, start_offset_sec, full_length,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                studio_template_id,
                source_mode,
                source_path,
                _normalize_json_array(source_paths_json),
                1 if recursive else 0,
                reaction_strategy,
                reaction_asset_id,
                reaction_pool_id,
                _normalize_json_object(parameter_values_json),
                _normalize_json_object(output_policy_json),
                1 if enabled else 0,
                renderer_engine,
                render_profile,
                duration_limit_sec,
                float(start_offset_sec or 0),
                1 if full_length else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def list_remotion_pipelines() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM remotion_pipelines
            ORDER BY id DESC
            """
        ).fetchall()


def get_remotion_pipeline(pipeline_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM remotion_pipelines WHERE id=?",
            (int(pipeline_id),),
        ).fetchone()


def update_remotion_pipeline_last_batch(pipeline_id: int, batch_id: int) -> None:
    with connect() as con:
        con.execute(
            """
            UPDATE remotion_pipelines
            SET last_batch_id=?, updated_at=?
            WHERE id=?
            """,
            (int(batch_id), now_utc(), int(pipeline_id)),
        )


def create_studio_template(
    *,
    template_key: str,
    name: str,
    engine: str,
    version: int,
    status: str,
    definition_json: dict[str, Any] | str,
) -> int:
    normalized = _normalize_recipe_json(definition_json, required=True)
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO studio_templates
                (template_key, name, engine, version, status,
                 definition_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_key,
                name,
                engine,
                int(version),
                status,
                normalized,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_studio_template(template_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM studio_templates WHERE id=?",
            (int(template_id),),
        ).fetchone()


def get_latest_studio_template_by_key(
    template_key: str,
) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM studio_templates
            WHERE template_key=?
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (template_key,),
        ).fetchone()


def list_studio_templates() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM studio_templates
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()


def update_studio_template(
    template_id: int,
    *,
    name: str,
    status: str,
    definition_json: dict[str, Any] | str,
) -> bool:
    normalized = _normalize_recipe_json(definition_json, required=True)
    with connect() as con:
        result = con.execute(
            """
            UPDATE studio_templates
            SET name=?, status=?, definition_json=?, updated_at=?
            WHERE id=?
            """,
            (name, status, normalized, now_utc(), int(template_id)),
        )
        return result.rowcount > 0


def next_studio_template_version(template_key: str) -> int:
    with connect() as con:
        row = con.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS max_version
            FROM studio_templates
            WHERE template_key=?
            """,
            (template_key,),
        ).fetchone()
    return int(row["max_version"]) + 1


def ensure_default_edit_templates() -> sqlite3.Row:
    key = "reaction_top_25"
    existing = get_edit_template_by_key(key)
    if existing is not None:
        return existing

    recipe = {
        "version": 1,
        "template_key": key,
        "canvas": {"width": 1080, "height": 1920},
        "slots": {
            "reaction": {"x": 0, "y": 0, "w": 1080, "h": 480},
            "main": {"x": 0, "y": 480, "w": 1080, "h": 1440},
        },
        "audio": {"mode": "main_only"},
        "overlays": [],
    }
    try:
        template_id = create_edit_template(
            key=key,
            name="Reaction Top 25%",
            renderer="ffmpeg",
            recipe_json=recipe,
            enabled=True,
        )
    except sqlite3.IntegrityError:
        created = get_edit_template_by_key(key)
        if created is None:
            raise
        return created
    created = get_edit_template(template_id)
    if created is None:
        raise RuntimeError("Default edit template was created but cannot be loaded.")
    return created
