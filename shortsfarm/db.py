"""SQLite persistence layer for ShortsFarm.

All public functions open/close their own connection so callers never have
to manage transactions themselves.  The lone exception is `claim_inbox_video`,
which uses an explicit BEGIN IMMEDIATE to guarantee atomic claim-and-update.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
                "SELECT id, deleted_at FROM videos WHERE source_path = ?", (str(source_path),)
            ).fetchone()
            if row is None:
                raise
            if row["deleted_at"] is not None:
                con.execute(
                    """
                    UPDATE videos
                    SET title=?, duration_sec=?, status='added',
                        deleted_at=NULL, source_file_deleted_at=NULL
                    WHERE id=?
                    """,
                    (title, duration_sec, int(row["id"])),
                )
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
            "SELECT * FROM videos WHERE deleted_at IS NULL ORDER BY id DESC"
        ).fetchall()


def list_videos_with_counts(*, include_deleted: bool = False) -> list[sqlite3.Row]:
    """Return videos with mark/clip counters for the CLI inbox view."""
    deleted_clause = "" if include_deleted else "WHERE v.deleted_at IS NULL"
    with connect() as con:
        return con.execute(
            f"""
            SELECT
                v.*,
                COUNT(DISTINCT m.id) AS mark_count,
                COUNT(DISTINCT c.id) AS clip_count,
                COUNT(DISTINCT s.id) AS segment_count
            FROM videos v
            LEFT JOIN marks m ON m.video_id = v.id
            LEFT JOIN clips c ON c.video_id = v.id
            LEFT JOIN segments s ON s.video_id = v.id
            {deleted_clause}
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
            WHERE deleted_at IS NULL
            GROUP BY COALESCE(review_status, 'inbox')
            """
        ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def count_videos() -> int:
    with connect() as con:
        row = con.execute("SELECT COUNT(*) FROM videos WHERE deleted_at IS NULL").fetchone()
    return int(row[0]) if row else 0


def update_video_review_status(video_id: int, review_status: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE videos SET review_status = ? WHERE id = ?",
            (review_status, video_id),
        )


def _delete_where_in(
    con: sqlite3.Connection,
    table: str,
    column: str,
    values: list[int | str],
) -> int:
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    cur = con.execute(
        f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
        values,
    )
    return int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)


def _update_null_where_in(
    con: sqlite3.Connection,
    table: str,
    column: str,
    values: list[int | str],
    *,
    target_column: str,
) -> int:
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    cur = con.execute(
        f"UPDATE {table} SET {target_column}=NULL WHERE {column} IN ({placeholders})",
        values,
    )
    return int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)


def delete_videos(video_ids: list[int]) -> dict[str, Any]:
    """Delete source video rows and their DB-only dependants.

    This intentionally does not remove media files from disk.  The web API can
    optionally unlink the original source files after this transaction commits.
    Generated workspace files are not removed here; they are separate user
    artifacts and should be deleted from the workspace UI.
    """
    normalized_ids = sorted({int(value) for value in video_ids if int(value) > 0})
    if not normalized_ids:
        return {
            "requested": 0,
            "deleted": 0,
            "missing_ids": [],
            "deleted_ids": [],
            "source_paths": [],
            "removed": {},
        }

    placeholders = ",".join("?" for _ in normalized_ids)
    with connect() as con:
        video_rows = con.execute(
            f"SELECT id, source_path FROM videos WHERE id IN ({placeholders})",
            normalized_ids,
        ).fetchall()
        found_ids = sorted(int(row["id"]) for row in video_rows)
        source_paths = [str(row["source_path"]) for row in video_rows if row["source_path"]]
        missing_ids = [video_id for video_id in normalized_ids if video_id not in found_ids]
        if not found_ids:
            return {
                "requested": len(normalized_ids),
                "deleted": 0,
                "missing_ids": missing_ids,
                "deleted_ids": [],
                "source_paths": [],
                "removed": {},
            }

        found_placeholders = ",".join("?" for _ in found_ids)
        clip_rows = con.execute(
            f"SELECT id, output_path, temp_path FROM clips WHERE video_id IN ({found_placeholders})",
            found_ids,
        ).fetchall()
        clip_ids = [int(row["id"]) for row in clip_rows]
        clip_paths = [
            str(value)
            for row in clip_rows
            for value in (row["output_path"], row["temp_path"])
            if value
        ]

        segment_rows = con.execute(
            f"SELECT id, path FROM segments WHERE video_id IN ({found_placeholders})",
            found_ids,
        ).fetchall()
        segment_ids = [int(row["id"]) for row in segment_rows]
        segment_paths = [str(row["path"]) for row in segment_rows if row["path"]]

        mark_rows = con.execute(
            f"SELECT id FROM marks WHERE video_id IN ({found_placeholders})",
            found_ids,
        ).fetchall()
        mark_ids = [int(row["id"]) for row in mark_rows]

        publish_job_rows: list[sqlite3.Row] = []
        if clip_ids:
            clip_placeholders = ",".join("?" for _ in clip_ids)
            publish_job_rows = con.execute(
                f"SELECT id FROM publish_jobs WHERE clip_id IN ({clip_placeholders})",
                clip_ids,
            ).fetchall()
        publish_job_ids = [int(row["id"]) for row in publish_job_rows]

        workspace_item_keys = [
            *(f"segment:{item_id}" for item_id in segment_ids),
            *(f"clip:{item_id}" for item_id in clip_ids),
        ]
        workspace_paths = sorted(set(source_paths + segment_paths + clip_paths))

        removed: dict[str, int] = {}
        removed["local_storage_profile_publish_jobs"] = _delete_where_in(
            con,
            "local_storage_profile_publish_jobs",
            "publish_job_id",
            publish_job_ids,
        )
        removed["external_publish_job_refs"] = _update_null_where_in(
            con,
            "local_storage_profile_external_videos",
            "publish_job_id",
            publish_job_ids,
            target_column="publish_job_id",
        )
        removed["publish_jobs"] = _delete_where_in(
            con,
            "publish_jobs",
            "id",
            publish_job_ids,
        )

        if segment_ids:
            seg_placeholders = ",".join("?" for _ in segment_ids)
            cur = con.execute(
                f"DELETE FROM workspace_tag_links WHERE item_type='segment' AND item_id IN ({seg_placeholders})",
                segment_ids,
            )
            removed["workspace_tag_links_segment"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
            cur = con.execute(
                f"DELETE FROM clip_workspace_metadata WHERE item_type='segment' AND item_id IN ({seg_placeholders})",
                segment_ids,
            )
            removed["workspace_metadata_segment"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        else:
            removed["workspace_tag_links_segment"] = 0
            removed["workspace_metadata_segment"] = 0

        if clip_ids:
            clip_placeholders = ",".join("?" for _ in clip_ids)
            cur = con.execute(
                f"DELETE FROM workspace_tag_links WHERE item_type='clip' AND item_id IN ({clip_placeholders})",
                clip_ids,
            )
            removed["workspace_tag_links_clip"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
            cur = con.execute(
                f"DELETE FROM clip_workspace_metadata WHERE item_type='clip' AND item_id IN ({clip_placeholders})",
                clip_ids,
            )
            removed["workspace_metadata_clip"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        else:
            removed["workspace_tag_links_clip"] = 0
            removed["workspace_metadata_clip"] = 0

        removed["workspace_tag_links_path"] = _delete_where_in(
            con,
            "workspace_tag_links",
            "workspace_path",
            workspace_paths,
        )
        removed["edit_jobs"] = _delete_where_in(
            con,
            "edit_jobs",
            "workspace_item_key",
            workspace_item_keys,
        )
        removed["video_segments"] = _delete_where_in(
            con,
            "video_segments",
            "source_path",
            source_paths,
        )
        removed["clips"] = _delete_where_in(con, "clips", "id", clip_ids)
        removed["marks"] = _delete_where_in(con, "marks", "id", mark_ids)

        cur = con.execute(
            f"DELETE FROM review_sessions WHERE video_id IN ({found_placeholders})",
            found_ids,
        )
        removed["review_sessions"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        cur = con.execute(
            f"DELETE FROM segments WHERE video_id IN ({found_placeholders})",
            found_ids,
        )
        removed["segments"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        cur = con.execute(
            f"DELETE FROM jobs WHERE video_id IN ({found_placeholders})",
            found_ids,
        )
        removed["jobs"] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        cur = con.execute(
            f"DELETE FROM videos WHERE id IN ({found_placeholders})",
            found_ids,
        )
        deleted = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)

        return {
            "requested": len(normalized_ids),
            "deleted": deleted,
            "missing_ids": missing_ids,
            "deleted_ids": found_ids,
            "source_paths": source_paths,
            "removed": removed,
        }


def soft_delete_videos(video_ids: list[int]) -> dict[str, Any]:
    """Hide source video rows while keeping generated clips linked and visible."""
    normalized_ids = sorted({int(value) for value in video_ids if int(value) > 0})
    if not normalized_ids:
        return {
            "requested": 0,
            "deleted": 0,
            "missing_ids": [],
            "already_deleted_ids": [],
            "deleted_ids": [],
            "source_paths": [],
            "source_path_by_id": {},
        }

    placeholders = ",".join("?" for _ in normalized_ids)
    now = now_utc()
    with connect() as con:
        rows = con.execute(
            f"SELECT id, source_path, deleted_at FROM videos WHERE id IN ({placeholders})",
            normalized_ids,
        ).fetchall()
        found_ids = sorted(int(row["id"]) for row in rows)
        active_rows = [row for row in rows if row["deleted_at"] is None]
        active_ids = sorted(int(row["id"]) for row in active_rows)
        missing_ids = [video_id for video_id in normalized_ids if video_id not in found_ids]
        already_deleted_ids = sorted(int(row["id"]) for row in rows if row["deleted_at"] is not None)
        source_path_by_id = {
            str(int(row["id"])): str(row["source_path"])
            for row in active_rows
            if row["source_path"]
        }
        source_paths = list(source_path_by_id.values())

        if active_ids:
            active_placeholders = ",".join("?" for _ in active_ids)
            cur = con.execute(
                f"UPDATE videos SET deleted_at=? WHERE id IN ({active_placeholders}) AND deleted_at IS NULL",
                [now, *active_ids],
            )
            deleted = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        else:
            deleted = 0

        return {
            "requested": len(normalized_ids),
            "deleted": deleted,
            "missing_ids": missing_ids,
            "already_deleted_ids": already_deleted_ids,
            "deleted_ids": active_ids,
            "source_paths": source_paths,
            "source_path_by_id": source_path_by_id,
        }


def mark_video_source_files_deleted(video_ids: list[int]) -> int:
    normalized_ids = sorted({int(value) for value in video_ids if int(value) > 0})
    if not normalized_ids:
        return 0
    placeholders = ",".join("?" for _ in normalized_ids)
    with connect() as con:
        cur = con.execute(
            f"""
            UPDATE videos
            SET source_file_deleted_at=COALESCE(source_file_deleted_at, ?)
            WHERE id IN ({placeholders})
            """,
            [now_utc(), *normalized_ids],
        )
        return int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)


def restore_video(video_id: int) -> bool:
    with connect() as con:
        cur = con.execute(
            "UPDATE videos SET deleted_at=NULL WHERE id=?",
            (int(video_id),),
        )
        return bool(cur.rowcount and cur.rowcount > 0)


def relink_video_source(video_id: int, source_path: str | Path) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT id FROM videos WHERE id=?",
            (int(video_id),),
        ).fetchone()
        if row is None:
            return False
        try:
            con.execute(
                """
                UPDATE videos
                SET source_path=?, source_file_deleted_at=NULL, deleted_at=NULL
                WHERE id=?
                """,
                (str(Path(source_path).expanduser().resolve()), int(video_id)),
            )
            return True
        except sqlite3.IntegrityError as exc:
            raise ValueError("Этот файл уже зарегистрирован как другое исходное видео.") from exc


def list_workspace_item_keys_for_video(video_id: int, *, include_hidden: bool = False) -> list[str]:
    video_id = int(video_id)
    if video_id <= 0:
        return []
    return [
        str(item["id"])
        for item in list_workspace_items(limit=10000, include_hidden=include_hidden)
        if int(item.get("video_id") or 0) == video_id
    ]


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
            """
            SELECT * FROM videos
            WHERE review_status = 'inbox' AND deleted_at IS NULL
            ORDER BY id LIMIT 1
            """
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None

        result = con.execute(
            "UPDATE videos SET review_status = 'reviewing' "
            "WHERE id = ? AND review_status = 'inbox' AND deleted_at IS NULL",
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


def get_or_create_publish_clip_for_file(
    output_path: str | Path,
    *,
    title: str | None = None,
    duration_sec: float | None = None,
) -> int:
    """Return a done clip row that points at an already prepared local file.

    This is used by local storage profiles: the file is not copied, rendered or
    sliced; we only create the minimal videos/clips metadata needed by the
    existing YouTube publish pipeline.
    """
    path = Path(output_path).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Файл для публикации не найден: {path}")
    resolved_path = str(path)
    clean_title = str(title or path.stem).strip() or path.stem
    now = now_utc()
    with connect() as con:
        video = con.execute(
            "SELECT id FROM videos WHERE source_path=?",
            (resolved_path,),
        ).fetchone()
        if video is None:
            cur = con.execute(
                """
                INSERT INTO videos (source_path, title, duration_sec, status, created_at)
                VALUES (?, ?, ?, 'added', ?)
                """,
                (resolved_path, clean_title, duration_sec, now),
            )
            video_id = int(cur.lastrowid)
        else:
            video_id = int(video["id"])

        existing = con.execute(
            """
            SELECT id FROM clips
            WHERE output_path=?
            ORDER BY CASE WHEN cut_mode='profile' THEN 0 ELSE 1 END, id ASC
            LIMIT 1
            """,
            (resolved_path,),
        ).fetchone()
        if existing is not None:
            clip_id = int(existing["id"])
            con.execute(
                """
                UPDATE clips
                SET video_id=?, status='done', output_path=?, temp_path=NULL,
                    error=NULL, rendered_at=COALESCE(rendered_at, ?)
                WHERE id=?
                """,
                (video_id, resolved_path, now, clip_id),
            )
            return clip_id

        cur = con.execute(
            """
            INSERT INTO clips
                (video_id, mark_id, status, cut_mode, output_path, temp_path,
                 error, created_at, started_at, rendered_at)
            VALUES (?, NULL, 'done', 'profile', ?, NULL, NULL, ?, NULL, ?)
            """,
            (video_id, resolved_path, now, now),
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
TAG_KINDS = {"user", "system", "status", "channel"}
PROFILE_TAG_RULE_MODES = {"include", "exclude"}
PROFILE_TAG_MATCH_MODES = {"any", "all"}
CHANNEL_TAG_COLOR = "#f59e0b"
STATUS_TAG_DEFINITIONS = {
    "draft": {
        "name": "Черновик",
        "slug": "status-draft",
        "color": "#64748b",
        "description": "Системный статус: черновик",
    },
    "ready": {
        "name": "Готово",
        "slug": "status-ready",
        "color": "#22c55e",
        "description": "Системный статус: готово к публикации",
    },
    "queued": {
        "name": "В очереди",
        "slug": "status-queued",
        "color": "#38bdf8",
        "description": "Системный статус: в очереди",
    },
    "uploaded": {
        "name": "Загружено",
        "slug": "status-uploaded",
        "color": "#a78bfa",
        "description": "Системный статус: загружено",
    },
    "failed": {
        "name": "Ошибка",
        "slug": "status-failed",
        "color": "#ef4444",
        "description": "Системный статус: ошибка",
    },
}


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
    source_deleted_at: str | None = None,
    source_file_deleted_at: str | None = None,
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
        "source_deleted_at": source_deleted_at,
        "source_file_deleted_at": source_file_deleted_at,
        "source_deleted": source_deleted_at is not None,
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
                v.deleted_at AS source_deleted_at,
                v.source_file_deleted_at AS source_file_deleted_at,
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
                    source_deleted_at=row["source_deleted_at"],
                    source_file_deleted_at=row["source_file_deleted_at"],
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
                v.deleted_at AS source_deleted_at,
                v.source_file_deleted_at AS source_file_deleted_at,
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
                    source_deleted_at=row["source_deleted_at"],
                    source_file_deleted_at=row["source_file_deleted_at"],
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
        _sync_workspace_status_tag(
            con,
            status=resolved_status,
            item_type=item_type,
            item_id=item_id,
            now=now,
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


# ---------------------------------------------------------------------------
# local storage profiles (local content vitrines)
# ---------------------------------------------------------------------------

_PROFILE_UNSET = object()
LOCAL_STORAGE_PROFILE_STATUSES = {"draft", "ready", "published", "archived"}
LOCAL_STORAGE_PROFILE_AUTO_IMPORT_SECTIONS = {"edits", "ready", "published"}


def _normalize_profile_text(value: str | None, *, max_length: int = 240) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:max_length] if text else None


def _normalize_local_storage_profile_name(name: str) -> str:
    normalized = _normalize_profile_text(name, max_length=120)
    if not normalized:
        raise ValueError("Название профиля не может быть пустым.")
    return normalized


def _normalize_profile_handle(value: str) -> str:
    raw = re.sub(r"\s+", "-", str(value or "").strip().lower())
    cleaned = re.sub(r"[^\w.-]+", "-", raw, flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    if not cleaned:
        cleaned = "profile"
    return cleaned[:80]


def _unique_profile_handle(con: sqlite3.Connection, base: str) -> str:
    normalized = _normalize_profile_handle(base)
    candidate = normalized
    suffix = 2
    while con.execute(
        "SELECT 1 FROM local_storage_profiles WHERE handle=?",
        (candidate,),
    ).fetchone() is not None:
        tail = f"-{suffix}"
        candidate = f"{normalized[:80 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _normalize_profile_color(value: str | None, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.lower()
    return default


def _profile_initials(name: str, initials: str | None = None) -> str:
    explicit = _normalize_profile_text(initials, max_length=4)
    if explicit:
        return explicit.upper()
    letters = [part[0] for part in re.split(r"\s+", name) if part]
    return "".join(letters[:2]).upper() or "SF"


def _normalize_profile_item_status(value: str | None) -> str:
    status = str(value or "draft").strip().lower()
    if status not in LOCAL_STORAGE_PROFILE_STATUSES:
        raise ValueError(
            "Storage profile item status must be one of: "
            + ", ".join(sorted(LOCAL_STORAGE_PROFILE_STATUSES))
        )
    return status


def _profile_item_status_to_workspace_status(status: str) -> str | None:
    normalized = _normalize_profile_item_status(status)
    return {
        "draft": "draft",
        "ready": "ready",
        "published": "uploaded",
    }.get(normalized)


def _normalize_auto_import_sections(value: Any = None) -> str:
    if value is None or value is _PROFILE_UNSET:
        sections = sorted(LOCAL_STORAGE_PROFILE_AUTO_IMPORT_SECTIONS)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in value.split(",")]
        sections = [str(item).strip().lower() for item in parsed if str(item).strip()]
    else:
        sections = [str(item).strip().lower() for item in value if str(item).strip()]
    clean = []
    for section in sections:
        if section not in LOCAL_STORAGE_PROFILE_AUTO_IMPORT_SECTIONS:
            raise ValueError(
                "Auto import sections must be one of: "
                + ", ".join(sorted(LOCAL_STORAGE_PROFILE_AUTO_IMPORT_SECTIONS))
            )
        if section not in clean:
            clean.append(section)
    if not clean:
        raise ValueError("Выберите хотя бы одну папку для автоимпорта.")
    return json.dumps(clean, ensure_ascii=False)


def _normalize_auto_import_prefix(value: str | None) -> str | None:
    text = str(value or "").strip().strip("/")
    if not text:
        return None
    if "\\" in text or text.startswith("/") or (len(text) >= 3 and text[1:3] == ":/"):
        raise ValueError("Префикс автоимпорта должен быть workspace-relative path.")
    relative = PurePosixPath(text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Path traversal в префиксе автоимпорта запрещён.")
    if any(part == ".shortsfarm" for part in relative.parts):
        raise PermissionError("Доступ к .shortsfarm запрещён.")
    if relative.parts[0] not in LOCAL_STORAGE_PROFILE_AUTO_IMPORT_SECTIONS:
        raise ValueError("Префикс автоимпорта должен начинаться с edits/, ready/ или published/.")
    return relative.as_posix()


def _normalize_tag_kind(value: str | None) -> str:
    kind = str(value or "user").strip().lower()
    if kind not in TAG_KINDS:
        raise ValueError("Tag kind must be one of: user, system, status, channel.")
    return kind


def _normalize_tag_rule_mode(value: str | None) -> str:
    mode = str(value or "include").strip().lower()
    if mode not in PROFILE_TAG_RULE_MODES:
        raise ValueError("Profile tag rule mode must be include or exclude.")
    return mode


def _normalize_profile_tag_match_mode(value: str | None) -> str:
    mode = str(value or "any").strip().lower()
    if mode not in PROFILE_TAG_MATCH_MODES:
        raise ValueError("Profile tag match mode must be any or all.")
    return mode


def _normalize_tag_slug(value: str) -> str:
    raw = re.sub(r"\s+", "-", str(value or "").strip().lower())
    raw = raw.replace("(", "-").replace(")", "-")
    cleaned = re.sub(r"[^\w.-]+", "-", raw, flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned[:96] or "tag"


def _unique_tag_slug(
    con: sqlite3.Connection,
    base: str,
    *,
    tag_id: int | None = None,
) -> str:
    normalized = _normalize_tag_slug(base)
    candidate = normalized
    suffix = 2
    while True:
        row = con.execute("SELECT id FROM tags WHERE slug=?", (candidate,)).fetchone()
        if row is None or (tag_id is not None and int(row["id"]) == int(tag_id)):
            return candidate
        tail = f"-{suffix}"
        candidate = f"{normalized[:96 - len(tail)]}{tail}"
        suffix += 1


def _normalize_tag_color(value: str | None, default: str = "#64748b") -> str:
    return _normalize_profile_color(value, default)


def _status_tag_system_key(status: str) -> str:
    return f"status:{_normalize_workspace_status(status)}"


def _ensure_status_tags(con: sqlite3.Connection) -> None:
    now = now_utc()
    for status, spec in STATUS_TAG_DEFINITIONS.items():
        con.execute(
            """
            INSERT INTO tags
                (name, slug, kind, color, description, system_key,
                 locked, enabled, created_at, updated_at)
            VALUES (?, ?, 'status', ?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                slug=excluded.slug,
                kind=excluded.kind,
                color=excluded.color,
                description=excluded.description,
                locked=excluded.locked,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                spec["name"],
                spec["slug"],
                spec["color"],
                spec["description"],
                f"status:{status}",
                now,
                now,
            ),
        )


def ensure_system_tags() -> None:
    with connect() as con:
        _ensure_status_tags(con)


def create_tag(
    *,
    name: str,
    slug: str | None = None,
    kind: str = "user",
    color: str | None = None,
    description: str | None = None,
    system_key: str | None = None,
    locked: bool = False,
    enabled: bool = True,
) -> int:
    normalized_name = _normalize_local_storage_profile_name(name)
    normalized_kind = _normalize_tag_kind(kind)
    now = now_utc()
    with connect() as con:
        normalized_slug = _unique_tag_slug(con, slug or normalized_name)
        cur = con.execute(
            """
            INSERT INTO tags
                (name, slug, kind, color, description, system_key,
                 locked, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_name,
                normalized_slug,
                normalized_kind,
                _normalize_tag_color(color, CHANNEL_TAG_COLOR if normalized_kind == "channel" else "#64748b"),
                _normalize_profile_text(description, max_length=2000),
                _normalize_profile_text(system_key, max_length=240),
                1 if locked else 0,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_tag(
    tag_id: int,
    *,
    name: Any = _PROFILE_UNSET,
    slug: Any = _PROFILE_UNSET,
    color: Any = _PROFILE_UNSET,
    description: Any = _PROFILE_UNSET,
    enabled: Any = _PROFILE_UNSET,
) -> bool:
    with connect() as con:
        row = con.execute("SELECT * FROM tags WHERE id=?", (int(tag_id),)).fetchone()
        if row is None:
            return False
        if bool(row["locked"]) and (name is not _PROFILE_UNSET or slug is not _PROFILE_UNSET):
            raise PermissionError("Системный тег нельзя переименовать вручную.")
        resolved_name = row["name"] if name is _PROFILE_UNSET else _normalize_local_storage_profile_name(name)
        resolved_slug = (
            row["slug"]
            if slug is _PROFILE_UNSET
            else _unique_tag_slug(con, slug or resolved_name, tag_id=int(tag_id))
        )
        con.execute(
            """
            UPDATE tags
            SET name=?, slug=?, color=?, description=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                resolved_name,
                resolved_slug,
                row["color"] if color is _PROFILE_UNSET else _normalize_tag_color(color, row["color"]),
                row["description"]
                if description is _PROFILE_UNSET
                else _normalize_profile_text(description, max_length=2000),
                row["enabled"] if enabled is _PROFILE_UNSET else (1 if enabled else 0),
                now_utc(),
                int(tag_id),
            ),
        )
        return True


def disable_tag(tag_id: int) -> bool:
    with connect() as con:
        row = con.execute("SELECT * FROM tags WHERE id=?", (int(tag_id),)).fetchone()
        if row is None:
            return False
        if bool(row["locked"]):
            raise PermissionError("Системный тег нельзя отключить.")
        result = con.execute(
            "UPDATE tags SET enabled=0, updated_at=? WHERE id=?",
            (now_utc(), int(tag_id)),
        )
        return result.rowcount > 0


def get_tag(tag_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute("SELECT * FROM tags WHERE id=?", (int(tag_id),)).fetchone()


def get_tag_by_slug(slug: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM tags WHERE slug=?",
            (_normalize_tag_slug(slug),),
        ).fetchone()


def get_tag_by_system_key(system_key: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM tags WHERE system_key=?",
            (_normalize_profile_text(system_key, max_length=240),),
        ).fetchone()


def list_tags(
    *,
    enabled: bool | None = True,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("enabled=?")
        params.append(1 if enabled else 0)
    if kind:
        clauses.append("kind=?")
        params.append(_normalize_tag_kind(kind))
    query = str(q or "").strip().lower()
    if query:
        clauses.append("(LOWER(name) LIKE ? OR LOWER(slug) LIKE ? OR LOWER(COALESCE(description,'')) LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit or 500), 2000)))
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM tags
            {where}
            ORDER BY
              CASE kind
                WHEN 'channel' THEN 0
                WHEN 'user' THEN 1
                WHEN 'status' THEN 2
                ELSE 3
              END,
              name COLLATE NOCASE ASC,
              id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()


def _status_tag_id(con: sqlite3.Connection, status: str) -> int:
    _ensure_status_tags(con)
    row = con.execute(
        "SELECT id FROM tags WHERE system_key=?",
        (_status_tag_system_key(status),),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def _remove_existing_status_tag_links(
    con: sqlite3.Connection,
    *,
    workspace_path: str | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
) -> None:
    if workspace_path:
        con.execute(
            """
            DELETE FROM workspace_tag_links
            WHERE workspace_path=?
              AND tag_id IN (SELECT id FROM tags WHERE kind='status')
            """,
            (workspace_path,),
        )
    if item_type is not None and item_id is not None:
        con.execute(
            """
            DELETE FROM workspace_tag_links
            WHERE item_type=? AND item_id=?
              AND tag_id IN (SELECT id FROM tags WHERE kind='status')
            """,
            (item_type, int(item_id)),
        )


def _add_workspace_tag_link_in_con(
    con: sqlite3.Connection,
    tag_id: int,
    *,
    workspace_path: str | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
    source: str = "manual",
    now: str | None = None,
) -> None:
    if not workspace_path and (item_type is None or item_id is None):
        raise ValueError("Нужен workspace_path или item_type/item_id для тега видео.")
    timestamp = now or now_utc()
    if workspace_path:
        con.execute(
            """
            INSERT INTO workspace_tag_links
                (workspace_path, item_type, item_id, tag_id, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_path, tag_id) WHERE workspace_path IS NOT NULL DO UPDATE SET
                item_type=COALESCE(excluded.item_type, workspace_tag_links.item_type),
                item_id=COALESCE(excluded.item_id, workspace_tag_links.item_id),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                workspace_path,
                item_type,
                int(item_id) if item_id is not None else None,
                int(tag_id),
                source,
                timestamp,
                timestamp,
            ),
        )
    if item_type is not None and item_id is not None:
        con.execute(
            """
            INSERT INTO workspace_tag_links
                (workspace_path, item_type, item_id, tag_id, source, created_at, updated_at)
            VALUES (NULL, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_type, item_id, tag_id)
              WHERE item_type IS NOT NULL AND item_id IS NOT NULL
            DO UPDATE SET
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (item_type, int(item_id), int(tag_id), source, timestamp, timestamp),
        )


def _sync_workspace_status_tag(
    con: sqlite3.Connection,
    *,
    status: str,
    workspace_path: str | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
    now: str | None = None,
) -> None:
    normalized_status = _normalize_workspace_status(status)
    tag_id = _status_tag_id(con, normalized_status)
    _remove_existing_status_tag_links(
        con,
        workspace_path=workspace_path,
        item_type=item_type,
        item_id=item_id,
    )
    _add_workspace_tag_link_in_con(
        con,
        tag_id,
        workspace_path=workspace_path,
        item_type=item_type,
        item_id=item_id,
        source="workspace_status",
        now=now,
    )


def add_workspace_tag_link(
    tag_id: int,
    *,
    workspace_path: str | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
    source: str = "manual",
) -> None:
    with connect() as con:
        tag = con.execute("SELECT * FROM tags WHERE id=?", (int(tag_id),)).fetchone()
        if tag is None or not bool(tag["enabled"]):
            raise FileNotFoundError("Тег не найден.")
        _add_workspace_tag_link_in_con(
            con,
            int(tag_id),
            workspace_path=workspace_path,
            item_type=_normalize_workspace_item_type(item_type) if item_type else None,
            item_id=int(item_id) if item_id is not None else None,
            source=source,
        )
        if tag["kind"] == "status":
            status = str(tag["system_key"] or "").split(":", 1)[-1]
            if item_type and item_id is not None:
                _upsert_workspace_status(
                    con,
                    _normalize_workspace_item_type(item_type),
                    int(item_id),
                    status,
                    now_utc(),
                )


def replace_workspace_tags(
    *,
    workspace_path: str,
    tag_ids: list[int],
    item_type: str | None = None,
    item_id: int | None = None,
) -> None:
    clean_ids = []
    for raw_id in tag_ids:
        tag_id = int(raw_id)
        if tag_id not in clean_ids:
            clean_ids.append(tag_id)
    with connect() as con:
        if clean_ids:
            rows = con.execute(
                f"SELECT * FROM tags WHERE enabled=1 AND id IN ({','.join('?' for _ in clean_ids)})",
                clean_ids,
            ).fetchall()
        else:
            rows = []
        found = {int(row["id"]): row for row in rows}
        if set(clean_ids) != set(found):
            raise FileNotFoundError("Один или несколько тегов не найдены.")
        con.execute("DELETE FROM workspace_tag_links WHERE workspace_path=?", (workspace_path,))
        normalized_item_type = _normalize_workspace_item_type(item_type) if item_type else None
        if normalized_item_type is not None and item_id is not None:
            con.execute(
                "DELETE FROM workspace_tag_links WHERE item_type=? AND item_id=?",
                (normalized_item_type, int(item_id)),
            )
        status_to_sync = None
        for tag_id in clean_ids:
            tag = found[tag_id]
            _add_workspace_tag_link_in_con(
                con,
                tag_id,
                workspace_path=workspace_path,
                item_type=normalized_item_type,
                item_id=int(item_id) if item_id is not None else None,
                source="manual",
            )
            if tag["kind"] == "status":
                status_to_sync = str(tag["system_key"] or "").split(":", 1)[-1]
        if status_to_sync and normalized_item_type is not None and item_id is not None:
            # Avoid recursive DB calls by updating metadata directly.
            _upsert_workspace_status(con, normalized_item_type, int(item_id), status_to_sync, now_utc())


def list_workspace_tag_links(
    *,
    workspace_path: str | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
) -> list[sqlite3.Row]:
    match_clauses: list[str] = []
    params: list[Any] = []
    if workspace_path:
        match_clauses.append("wtl.workspace_path=?")
        params.append(workspace_path)
    if item_type is not None and item_id is not None:
        match_clauses.append("(wtl.item_type=? AND wtl.item_id=?)")
        params.extend([_normalize_workspace_item_type(item_type), int(item_id)])
    if not match_clauses:
        raise ValueError("Нужен workspace_path или item_type/item_id.")
    with connect() as con:
        return con.execute(
            f"""
            SELECT DISTINCT t.*, wtl.source AS link_source
            FROM workspace_tag_links wtl
            JOIN tags t ON t.id=wtl.tag_id
            WHERE ({" OR ".join(match_clauses)})
              AND t.enabled=1
            ORDER BY
              CASE t.kind
                WHEN 'channel' THEN 0
                WHEN 'user' THEN 1
                WHEN 'status' THEN 2
                ELSE 3
              END,
              t.name COLLATE NOCASE ASC
            """,
            params,
        ).fetchall()


def _channel_tag_name(display_name: str) -> str:
    cleaned = re.sub(r"[()]+", " ", str(display_name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"channel-{cleaned or 'youtube'}"


def _ensure_channel_tag_in_con(
    con: sqlite3.Connection,
    *,
    account_id: int,
    display_name: str,
) -> int:
    now = now_utc()
    system_key = f"youtube:{int(account_id)}"
    name = _channel_tag_name(display_name)
    existing = con.execute(
        "SELECT * FROM tags WHERE system_key=?",
        (system_key,),
    ).fetchone()
    if existing is not None:
        slug = _unique_tag_slug(con, name, tag_id=int(existing["id"]))
        con.execute(
            """
            UPDATE tags
            SET name=?, slug=?, kind='channel', color=?, locked=1,
                enabled=1, updated_at=?
            WHERE id=?
            """,
            (name, slug, CHANNEL_TAG_COLOR, now, int(existing["id"])),
        )
        return int(existing["id"])
    slug = _unique_tag_slug(con, name)
    cur = con.execute(
        """
        INSERT INTO tags
            (name, slug, kind, color, description, system_key,
             locked, enabled, created_at, updated_at)
        VALUES (?, ?, 'channel', ?, ?, ?, 1, 1, ?, ?)
        """,
        (
            name,
            slug,
            CHANNEL_TAG_COLOR,
            "Автоматический тег YouTube-канала",
            system_key,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def ensure_channel_tag_for_account(
    *,
    account_id: int,
    display_name: str,
) -> sqlite3.Row:
    with connect() as con:
        tag_id = _ensure_channel_tag_in_con(
            con,
            account_id=int(account_id),
            display_name=display_name,
        )
        row = con.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        assert row is not None
        return row


def list_local_storage_profile_tag_rules(profile_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT ptr.*, t.name, t.slug, t.kind, t.color, t.description,
                   t.system_key, t.locked AS tag_locked, t.enabled AS tag_enabled
            FROM local_storage_profile_tag_rules ptr
            JOIN tags t ON t.id=ptr.tag_id
            WHERE ptr.profile_id=? AND t.enabled=1
            ORDER BY ptr.mode ASC,
              CASE t.kind WHEN 'channel' THEN 0 WHEN 'user' THEN 1 WHEN 'status' THEN 2 ELSE 3 END,
              t.name COLLATE NOCASE ASC
            """,
            (int(profile_id),),
        ).fetchall()


def replace_local_storage_profile_tag_rules(
    profile_id: int,
    *,
    include_tag_ids: list[int],
    exclude_tag_ids: list[int],
    tag_match_mode: str = "any",
) -> None:
    mode = _normalize_profile_tag_match_mode(tag_match_mode)
    include_ids = list(dict.fromkeys(int(tag_id) for tag_id in include_tag_ids))
    exclude_ids = list(dict.fromkeys(int(tag_id) for tag_id in exclude_tag_ids))
    all_ids = list(dict.fromkeys(include_ids + exclude_ids))
    now = now_utc()
    with connect() as con:
        profile = con.execute(
            "SELECT * FROM local_storage_profiles WHERE id=? AND enabled=1",
            (int(profile_id),),
        ).fetchone()
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        if all_ids:
            rows = con.execute(
                f"SELECT id FROM tags WHERE enabled=1 AND id IN ({','.join('?' for _ in all_ids)})",
                all_ids,
            ).fetchall()
            found = {int(row["id"]) for row in rows}
            if found != set(all_ids):
                raise FileNotFoundError("Один или несколько тегов не найдены.")
        con.execute(
            """
            DELETE FROM local_storage_profile_tag_rules
            WHERE profile_id=? AND locked=0
            """,
            (int(profile_id),),
        )
        con.execute(
            """
            UPDATE local_storage_profiles
            SET tag_match_mode=?, updated_at=?
            WHERE id=?
            """,
            (mode, now, int(profile_id)),
        )
        for tag_id in include_ids:
            locked = con.execute(
                """
                SELECT locked FROM local_storage_profile_tag_rules
                WHERE profile_id=? AND tag_id=? AND mode='include'
                """,
                (int(profile_id), tag_id),
            ).fetchone()
            if locked is not None:
                continue
            con.execute(
                """
                INSERT OR IGNORE INTO local_storage_profile_tag_rules
                    (profile_id, tag_id, mode, locked, source, created_at, updated_at)
                VALUES (?, ?, 'include', 0, 'manual', ?, ?)
                """,
                (int(profile_id), tag_id, now, now),
            )
        for tag_id in exclude_ids:
            con.execute(
                """
                INSERT OR IGNORE INTO local_storage_profile_tag_rules
                    (profile_id, tag_id, mode, locked, source, created_at, updated_at)
                VALUES (?, ?, 'exclude', 0, 'manual', ?, ?)
                """,
                (int(profile_id), tag_id, now, now),
            )


def reconcile_local_storage_profile_channel_tags(profile_id: int) -> int | None:
    now = now_utc()
    with connect() as con:
        profile = con.execute(
            "SELECT * FROM local_storage_profiles WHERE id=? AND enabled=1",
            (int(profile_id),),
        ).fetchone()
        if profile is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        link = con.execute(
            """
            SELECT *
            FROM local_storage_profile_service_links
            WHERE profile_id=? AND platform='youtube' AND status='linked'
            """,
            (int(profile_id),),
        ).fetchone()
        if link is None or link["external_account_id"] is None:
            con.execute(
                """
                DELETE FROM local_storage_profile_tag_rules
                WHERE profile_id=?
                  AND tag_id IN (SELECT id FROM tags WHERE kind='channel')
                """,
                (int(profile_id),),
            )
            return None
        account_id = int(link["external_account_id"])
        account = con.execute(
            "SELECT * FROM social_accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        display_name = (
            (account["channel_title"] if account is not None else None)
            or (account["display_name"] if account is not None else None)
            or link["display_name"]
            or f"YouTube аккаунт #{account_id}"
        )
        tag_id = _ensure_channel_tag_in_con(
            con,
            account_id=account_id,
            display_name=display_name,
        )
        con.execute(
            """
            DELETE FROM local_storage_profile_tag_rules
            WHERE profile_id=?
              AND tag_id IN (
                SELECT id FROM tags
                WHERE kind='channel' AND COALESCE(system_key, '') != ?
              )
            """,
            (int(profile_id), f"youtube:{account_id}"),
        )
        con.execute(
            """
            INSERT INTO local_storage_profile_tag_rules
                (profile_id, tag_id, mode, locked, source, created_at, updated_at)
            VALUES (?, ?, 'include', 1, 'youtube_link', ?, ?)
            ON CONFLICT(profile_id, tag_id, mode) DO UPDATE SET
                locked=1,
                source='youtube_link',
                updated_at=excluded.updated_at
            """,
            (int(profile_id), tag_id, now, now),
        )
        return tag_id


def create_local_storage_profile(
    *,
    name: str,
    handle: str | None = None,
    description: str | None = None,
    avatar_initials: str | None = None,
    avatar_color: str | None = None,
    banner_color: str | None = None,
    auto_import_enabled: bool = False,
    auto_import_sections: Any = None,
    auto_import_prefix: str | None = None,
    tag_match_mode: str = "any",
    enabled: bool = True,
) -> int:
    normalized_name = _normalize_local_storage_profile_name(name)
    now = now_utc()
    with connect() as con:
        normalized_handle = (
            _normalize_profile_handle(handle)
            if handle and str(handle).strip()
            else _unique_profile_handle(con, normalized_name)
        )
        cur = con.execute(
            """
            INSERT INTO local_storage_profiles
                (name, handle, description, avatar_initials, avatar_color,
                 banner_color, auto_import_enabled, auto_import_sections,
                 auto_import_prefix, tag_match_mode, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_name,
                normalized_handle,
                _normalize_profile_text(description, max_length=2000),
                _profile_initials(normalized_name, avatar_initials),
                _normalize_profile_color(avatar_color, "#3b82f6"),
                _normalize_profile_color(banner_color, "#111827"),
                1 if auto_import_enabled else 0,
                _normalize_auto_import_sections(auto_import_sections),
                _normalize_auto_import_prefix(auto_import_prefix),
                _normalize_profile_tag_match_mode(tag_match_mode),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_local_storage_profile(profile_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT
                p.*,
                COUNT(i.id) AS item_count
            FROM local_storage_profiles p
            LEFT JOIN local_storage_profile_items i ON i.profile_id=p.id
            WHERE p.id=?
            GROUP BY p.id
            """,
            (int(profile_id),),
        ).fetchone()


def list_local_storage_profiles(enabled: bool | None = None) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("p.enabled=?")
        params.append(1 if enabled else 0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        return con.execute(
            f"""
            SELECT
                p.*,
                COUNT(i.id) AS item_count
            FROM local_storage_profiles p
            LEFT JOIN local_storage_profile_items i ON i.profile_id=p.id
            {where}
            GROUP BY p.id
            ORDER BY p.created_at DESC, p.id DESC
            """,
            params,
        ).fetchall()


def update_local_storage_profile(
    profile_id: int,
    *,
    name: Any = _PROFILE_UNSET,
    handle: Any = _PROFILE_UNSET,
    description: Any = _PROFILE_UNSET,
    avatar_initials: Any = _PROFILE_UNSET,
    avatar_color: Any = _PROFILE_UNSET,
    avatar_url: Any = _PROFILE_UNSET,
    banner_color: Any = _PROFILE_UNSET,
    banner_url: Any = _PROFILE_UNSET,
    youtube_branding_sync_enabled: Any = _PROFILE_UNSET,
    name_override: Any = _PROFILE_UNSET,
    handle_override: Any = _PROFILE_UNSET,
    description_override: Any = _PROFILE_UNSET,
    avatar_override: Any = _PROFILE_UNSET,
    banner_override: Any = _PROFILE_UNSET,
    youtube_branding_synced_at: Any = _PROFILE_UNSET,
    youtube_branding_attempted_at: Any = _PROFILE_UNSET,
    youtube_branding_sync_error: Any = _PROFILE_UNSET,
    auto_import_enabled: Any = _PROFILE_UNSET,
    auto_import_sections: Any = _PROFILE_UNSET,
    auto_import_prefix: Any = _PROFILE_UNSET,
    auto_import_last_scan_at: Any = _PROFILE_UNSET,
    tag_match_mode: Any = _PROFILE_UNSET,
    enabled: Any = _PROFILE_UNSET,
) -> bool:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM local_storage_profiles WHERE id=?",
            (int(profile_id),),
        ).fetchone()
        if row is None:
            return False
        resolved_name = (
            row["name"]
            if name is _PROFILE_UNSET
            else _normalize_local_storage_profile_name(name)
        )
        resolved_handle = (
            row["handle"]
            if handle is _PROFILE_UNSET
            else _normalize_profile_handle(handle or resolved_name)
        )
        con.execute(
            """
            UPDATE local_storage_profiles
            SET name=?, handle=?, description=?, avatar_initials=?,
                avatar_color=?, avatar_url=?, banner_color=?, banner_url=?,
                youtube_branding_sync_enabled=?,
                name_override=?, handle_override=?, description_override=?,
                avatar_override=?, banner_override=?,
                youtube_branding_synced_at=?, youtube_branding_attempted_at=?,
                youtube_branding_sync_error=?,
                auto_import_enabled=?, auto_import_sections=?,
                auto_import_prefix=?, auto_import_last_scan_at=?,
                tag_match_mode=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (
                resolved_name,
                resolved_handle,
                row["description"]
                if description is _PROFILE_UNSET
                else _normalize_profile_text(description, max_length=2000),
                row["avatar_initials"]
                if avatar_initials is _PROFILE_UNSET
                else _profile_initials(resolved_name, avatar_initials),
                row["avatar_color"]
                if avatar_color is _PROFILE_UNSET
                else _normalize_profile_color(avatar_color, row["avatar_color"]),
                row["avatar_url"]
                if avatar_url is _PROFILE_UNSET
                else _normalize_profile_text(avatar_url, max_length=2000),
                row["banner_color"]
                if banner_color is _PROFILE_UNSET
                else _normalize_profile_color(banner_color, row["banner_color"]),
                row["banner_url"]
                if banner_url is _PROFILE_UNSET
                else _normalize_profile_text(banner_url, max_length=2000),
                row["youtube_branding_sync_enabled"]
                if youtube_branding_sync_enabled is _PROFILE_UNSET
                else (1 if youtube_branding_sync_enabled else 0),
                row["name_override"]
                if name_override is _PROFILE_UNSET
                else (1 if name_override else 0),
                row["handle_override"]
                if handle_override is _PROFILE_UNSET
                else (1 if handle_override else 0),
                row["description_override"]
                if description_override is _PROFILE_UNSET
                else (1 if description_override else 0),
                row["avatar_override"]
                if avatar_override is _PROFILE_UNSET
                else (1 if avatar_override else 0),
                row["banner_override"]
                if banner_override is _PROFILE_UNSET
                else (1 if banner_override else 0),
                row["youtube_branding_synced_at"]
                if youtube_branding_synced_at is _PROFILE_UNSET
                else youtube_branding_synced_at,
                row["youtube_branding_attempted_at"]
                if youtube_branding_attempted_at is _PROFILE_UNSET
                else youtube_branding_attempted_at,
                row["youtube_branding_sync_error"]
                if youtube_branding_sync_error is _PROFILE_UNSET
                else _normalize_profile_text(youtube_branding_sync_error, max_length=2000),
                row["auto_import_enabled"]
                if auto_import_enabled is _PROFILE_UNSET
                else (1 if auto_import_enabled else 0),
                row["auto_import_sections"]
                if auto_import_sections is _PROFILE_UNSET
                else _normalize_auto_import_sections(auto_import_sections),
                row["auto_import_prefix"]
                if auto_import_prefix is _PROFILE_UNSET
                else _normalize_auto_import_prefix(auto_import_prefix),
                row["auto_import_last_scan_at"]
                if auto_import_last_scan_at is _PROFILE_UNSET
                else auto_import_last_scan_at,
                row["tag_match_mode"]
                if tag_match_mode is _PROFILE_UNSET
                else _normalize_profile_tag_match_mode(tag_match_mode),
                row["enabled"] if enabled is _PROFILE_UNSET else (1 if enabled else 0),
                now_utc(),
                int(profile_id),
            ),
        )
        return True


def update_local_storage_profile_youtube_branding_sync(
    profile_id: int,
    *,
    synced_at: str | None = None,
    attempted_at: str | None = None,
    error: str | None = None,
) -> bool:
    attempt_time = attempted_at or now_utc()
    if error:
        return update_local_storage_profile(
            profile_id,
            youtube_branding_attempted_at=attempt_time,
            youtube_branding_sync_error=error,
        )
    success_time = synced_at or attempt_time
    return update_local_storage_profile(
        profile_id,
        youtube_branding_synced_at=success_time,
        youtube_branding_attempted_at=attempt_time,
        youtube_branding_sync_error=None,
    )


def disable_local_storage_profile(profile_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "UPDATE local_storage_profiles SET enabled=0, updated_at=? WHERE id=?",
            (now_utc(), int(profile_id)),
        )
        return result.rowcount > 0


def add_local_storage_profile_item(
    profile_id: int,
    *,
    workspace_path: str,
    title: str | None = None,
    description: str | None = None,
    tags: str | None = None,
    status: str = "draft",
) -> int:
    now = now_utc()
    normalized_status = _normalize_profile_item_status(status)
    with connect() as con:
        if con.execute(
            "SELECT 1 FROM local_storage_profiles WHERE id=? AND enabled=1",
            (int(profile_id),),
        ).fetchone() is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        cur = con.execute(
            """
            INSERT INTO local_storage_profile_items
                (profile_id, workspace_path, title, description, tags, status, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, workspace_path) DO UPDATE SET
                title=COALESCE(excluded.title, local_storage_profile_items.title),
                description=COALESCE(excluded.description, local_storage_profile_items.description),
                tags=COALESCE(excluded.tags, local_storage_profile_items.tags),
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                int(profile_id),
                str(workspace_path),
                _normalize_profile_text(title, max_length=240),
                _normalize_profile_text(description, max_length=2000),
                _normalize_profile_text(tags, max_length=1000),
                normalized_status,
                now,
                now,
            ),
        )
        row = con.execute(
            """
            SELECT id FROM local_storage_profile_items
            WHERE profile_id=? AND workspace_path=?
            """,
            (int(profile_id), str(workspace_path)),
        ).fetchone()
        workspace_status = _profile_item_status_to_workspace_status(normalized_status)
        if workspace_status:
            _sync_workspace_status_tag(
                con,
                status=workspace_status,
                workspace_path=str(workspace_path),
                now=now,
            )
        return int(row["id"] if row is not None else cur.lastrowid)


def list_local_storage_profile_items(profile_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM local_storage_profile_items
            WHERE profile_id=?
            ORDER BY added_at DESC, id DESC
            """,
            (int(profile_id),),
        ).fetchall()


def get_local_storage_profile_item(item_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM local_storage_profile_items WHERE id=?",
            (int(item_id),),
        ).fetchone()


def remove_local_storage_profile_item(profile_id: int, item_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "DELETE FROM local_storage_profile_items WHERE profile_id=? AND id=?",
            (int(profile_id), int(item_id)),
        )
        return result.rowcount > 0


def list_local_storage_profile_items_by_workspace_paths(
    workspace_paths: list[str],
) -> list[sqlite3.Row]:
    normalized_paths = sorted({str(path).strip() for path in workspace_paths if str(path).strip()})
    if not normalized_paths:
        return []
    placeholders = ",".join("?" for _ in normalized_paths)
    with connect() as con:
        return con.execute(
            f"""
            SELECT i.*, p.name AS profile_name, p.handle AS profile_handle
            FROM local_storage_profile_items i
            JOIN local_storage_profiles p ON p.id=i.profile_id
            WHERE i.workspace_path IN ({placeholders})
            ORDER BY p.name ASC, i.id ASC
            """,
            normalized_paths,
        ).fetchall()


def remove_local_storage_profile_items_by_workspace_paths(
    workspace_paths: list[str],
) -> dict[str, Any]:
    normalized_paths = sorted({str(path).strip() for path in workspace_paths if str(path).strip()})
    if not normalized_paths:
        return {
            "requested_paths": 0,
            "matched_items": 0,
            "removed": 0,
            "affected_profiles": 0,
            "paths": [],
        }
    placeholders = ",".join("?" for _ in normalized_paths)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT id, profile_id, workspace_path
            FROM local_storage_profile_items
            WHERE workspace_path IN ({placeholders})
            """,
            normalized_paths,
        ).fetchall()
        profile_ids = {int(row["profile_id"]) for row in rows}
        cur = con.execute(
            f"""
            DELETE FROM local_storage_profile_items
            WHERE workspace_path IN ({placeholders})
            """,
            normalized_paths,
        )
        removed = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)
        return {
            "requested_paths": len(normalized_paths),
            "matched_items": len(rows),
            "removed": removed,
            "affected_profiles": len(profile_ids),
            "paths": normalized_paths,
        }


def update_local_storage_profile_item_status(
    profile_item_id: int,
    status: str,
) -> bool:
    normalized_status = _normalize_profile_item_status(status)
    with connect() as con:
        result = con.execute(
            """
            UPDATE local_storage_profile_items
            SET status=?, updated_at=?
            WHERE id=?
            """,
            (normalized_status, now_utc(), int(profile_item_id)),
        )
        row = con.execute(
            "SELECT workspace_path FROM local_storage_profile_items WHERE id=?",
            (int(profile_item_id),),
        ).fetchone()
        workspace_status = _profile_item_status_to_workspace_status(normalized_status)
        if row is not None and workspace_status:
            _sync_workspace_status_tag(
                con,
                status=workspace_status,
                workspace_path=str(row["workspace_path"]),
            )
        return result.rowcount > 0


def list_local_storage_profile_service_links(profile_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM local_storage_profile_service_links
            WHERE profile_id=?
            ORDER BY platform ASC, id ASC
            """,
            (int(profile_id),),
        ).fetchall()


def list_local_storage_profile_service_links_for_profiles(
    profile_ids: list[int] | tuple[int, ...] | set[int],
) -> list[sqlite3.Row]:
    ids = sorted({int(value) for value in profile_ids if int(value) > 0})
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM local_storage_profile_service_links
            WHERE profile_id IN ({placeholders})
            ORDER BY profile_id ASC, platform ASC, id ASC
            """,
            ids,
        ).fetchall()


def get_local_storage_profile_service_link(
    profile_id: int,
    platform: str,
) -> sqlite3.Row | None:
    normalized_platform = str(platform or "").strip().lower()
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM local_storage_profile_service_links
            WHERE profile_id=? AND platform=?
            """,
            (int(profile_id), normalized_platform),
        ).fetchone()


def upsert_local_storage_profile_service_link(
    profile_id: int,
    *,
    platform: str,
    external_account_id: int | None = None,
    display_name: str | None = None,
    status: str = "linked",
    settings_json: str | None = None,
) -> int:
    normalized_platform = str(platform or "").strip().lower()
    if not normalized_platform:
        raise ValueError("platform не может быть пустым.")
    now = now_utc()
    with connect() as con:
        if con.execute(
            "SELECT 1 FROM local_storage_profiles WHERE id=? AND enabled=1",
            (int(profile_id),),
        ).fetchone() is None:
            raise FileNotFoundError("Локальный профиль не найден.")
        cur = con.execute(
            """
            INSERT INTO local_storage_profile_service_links
                (profile_id, platform, external_account_id, display_name,
                 status, settings_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, platform) DO UPDATE SET
                external_account_id=excluded.external_account_id,
                display_name=excluded.display_name,
                status=excluded.status,
                settings_json=COALESCE(excluded.settings_json, local_storage_profile_service_links.settings_json),
                updated_at=excluded.updated_at
            """,
            (
                int(profile_id),
                normalized_platform,
                external_account_id,
                _normalize_profile_text(display_name, max_length=240),
                str(status or "linked").strip().lower() or "linked",
                settings_json,
                now,
                now,
            ),
        )
        row = con.execute(
            """
            SELECT id FROM local_storage_profile_service_links
            WHERE profile_id=? AND platform=?
            """,
            (int(profile_id), normalized_platform),
        ).fetchone()
        return int(row["id"] if row is not None else cur.lastrowid)


def update_local_storage_profile_service_link_settings(
    profile_id: int,
    platform: str,
    settings_json: str,
) -> bool:
    normalized_platform = str(platform or "").strip().lower()
    if not normalized_platform:
        raise ValueError("platform не может быть пустым.")
    with connect() as con:
        result = con.execute(
            """
            UPDATE local_storage_profile_service_links
            SET settings_json=?, updated_at=?
            WHERE profile_id=? AND platform=?
            """,
            (settings_json, now_utc(), int(profile_id), normalized_platform),
        )
        return result.rowcount > 0


def update_local_storage_profile_service_link_sync(
    profile_id: int,
    platform: str,
    *,
    last_sync_at: str | None = None,
    last_sync_error: str | None = None,
    synced_video_count: int | None = None,
) -> bool:
    normalized_platform = str(platform or "").strip().lower()
    updates = ["updated_at=?"]
    params: list[Any] = [now_utc()]
    if last_sync_at is not None:
        updates.append("last_sync_at=?")
        params.append(last_sync_at)
    updates.append("last_sync_error=?")
    params.append(last_sync_error)
    if synced_video_count is not None:
        updates.append("synced_video_count=?")
        params.append(int(synced_video_count))
    params.extend([int(profile_id), normalized_platform])
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE local_storage_profile_service_links
            SET {", ".join(updates)}
            WHERE profile_id=? AND platform=?
            """,
            params,
        )
        return result.rowcount > 0


def list_local_storage_profiles_for_service_account(
    *,
    platform: str,
    external_account_id: int,
) -> list[sqlite3.Row]:
    normalized_platform = str(platform or "").strip().lower()
    with connect() as con:
        return con.execute(
            """
            SELECT lsp.*
            FROM local_storage_profiles lsp
            JOIN local_storage_profile_service_links link ON link.profile_id=lsp.id
            WHERE link.platform=?
              AND link.external_account_id=?
              AND link.status='linked'
              AND lsp.enabled=1
            ORDER BY lsp.name COLLATE NOCASE ASC, lsp.id ASC
            """,
            (normalized_platform, int(external_account_id)),
        ).fetchall()


def list_social_accounts_by_ids(
    account_ids: list[int] | tuple[int, ...] | set[int],
) -> list[sqlite3.Row]:
    ids = sorted({int(value) for value in account_ids if int(value) > 0})
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with connect() as con:
        return con.execute(
            f"""
            SELECT sa.*, yop.name AS profile_name
            FROM social_accounts sa
            LEFT JOIN youtube_oauth_profiles yop ON yop.id=sa.oauth_profile_id
            WHERE sa.id IN ({placeholders})
            ORDER BY sa.id ASC
            """,
            ids,
        ).fetchall()


def remove_local_storage_profile_service_link(profile_id: int, platform: str) -> bool:
    normalized_platform = str(platform or "").strip().lower()
    with connect() as con:
        result = con.execute(
            """
            DELETE FROM local_storage_profile_service_links
            WHERE profile_id=? AND platform=?
            """,
            (int(profile_id), normalized_platform),
        )
        return result.rowcount > 0


def get_publish_job_by_youtube_video_id(
    *,
    account_id: int,
    youtube_video_id: str,
    platform: str = "youtube",
) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            WHERE pj.platform=? AND pj.account_id=? AND pj.youtube_video_id=?
            ORDER BY pj.id DESC
            LIMIT 1
            """,
            (platform, int(account_id), str(youtube_video_id)),
        ).fetchone()


def get_local_storage_profile_publish_link_for_job(
    profile_id: int,
    publish_job_id: int,
    *,
    platform: str = "youtube",
) -> sqlite3.Row | None:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    with connect() as con:
        return con.execute(
            """
            SELECT lspj.*, lspi.workspace_path
            FROM local_storage_profile_publish_jobs lspj
            LEFT JOIN local_storage_profile_items lspi ON lspi.id=lspj.profile_item_id
            WHERE lspj.profile_id=? AND lspj.publish_job_id=? AND lspj.platform=?
            ORDER BY lspj.id DESC
            LIMIT 1
            """,
            (int(profile_id), int(publish_job_id), normalized_platform),
        ).fetchone()


def update_publish_job_from_youtube_sync(
    job_id: int,
    *,
    youtube_video_id: str,
    youtube_url: str,
    title: str | None = None,
    description: str | None = None,
    tags: str | list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
    publish_at: str | None = None,
) -> bool:
    now = now_utc()
    if isinstance(tags, list):
        tags_value = json.dumps([str(item) for item in tags], ensure_ascii=False)
    else:
        tags_value = tags
    with connect() as con:
        row = con.execute("SELECT * FROM publish_jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None:
            return False
        result = con.execute(
            """
            UPDATE publish_jobs
            SET status='done',
                title=COALESCE(?, title),
                description=COALESCE(?, description),
                tags=COALESCE(?, tags),
                category_id=COALESCE(?, category_id),
                privacy_status=COALESCE(?, privacy_status),
                publish_at=?,
                publish_mode=CASE WHEN ? IS NOT NULL THEN 'schedule' ELSE publish_mode END,
                youtube_video_id=?,
                youtube_url=?,
                error=NULL,
                next_attempt_at=NULL,
                finished_at=COALESCE(finished_at, ?),
                updated_at=?
            WHERE id=?
            """,
            (
                title,
                description,
                tags_value,
                category_id,
                privacy_status,
                publish_at,
                publish_at,
                youtube_video_id,
                youtube_url,
                now,
                now,
                int(job_id),
            ),
        )
        return result.rowcount > 0


def link_local_storage_profile_publish_job(
    profile_id: int,
    profile_item_id: int,
    publish_job_id: int,
    *,
    platform: str = "youtube",
) -> int:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    now = now_utc()
    with connect() as con:
        item = con.execute(
            """
            SELECT id FROM local_storage_profile_items
            WHERE id=? AND profile_id=?
            """,
            (int(profile_item_id), int(profile_id)),
        ).fetchone()
        if item is None:
            raise FileNotFoundError("Видео в локальном профиле не найдено.")
        job = con.execute(
            "SELECT id FROM publish_jobs WHERE id=? AND platform=?",
            (int(publish_job_id), normalized_platform),
        ).fetchone()
        if job is None:
            raise FileNotFoundError("Publish job не найден.")
        cur = con.execute(
            """
            INSERT INTO local_storage_profile_publish_jobs
                (profile_id, profile_item_id, publish_job_id, platform, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_item_id, publish_job_id) DO UPDATE SET
                profile_id=excluded.profile_id,
                platform=excluded.platform,
                updated_at=excluded.updated_at
            """,
            (
                int(profile_id),
                int(profile_item_id),
                int(publish_job_id),
                normalized_platform,
                now,
                now,
            ),
        )
        row = con.execute(
            """
            SELECT id FROM local_storage_profile_publish_jobs
            WHERE profile_item_id=? AND publish_job_id=?
            """,
            (int(profile_item_id), int(publish_job_id)),
        ).fetchone()
        return int(row["id"] if row is not None else cur.lastrowid)


def upsert_local_storage_profile_external_video(
    profile_id: int,
    *,
    platform: str = "youtube",
    external_video_id: str,
    external_url: str | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: str | list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
    publish_at: str | None = None,
    published_at: str | None = None,
    duration: str | None = None,
    thumbnail_url: str | None = None,
    profile_item_id: int | None = None,
    publish_job_id: int | None = None,
    raw_json: str | dict[str, Any] | None = None,
) -> int:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    clean_video_id = str(external_video_id or "").strip()
    if not clean_video_id:
        raise ValueError("external_video_id не может быть пустым.")
    now = now_utc()
    if isinstance(tags, list):
        tags_value = json.dumps([str(item) for item in tags], ensure_ascii=False)
    else:
        tags_value = tags
    if isinstance(raw_json, dict):
        raw_json_value = json.dumps(raw_json, ensure_ascii=False)
    else:
        raw_json_value = raw_json
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO local_storage_profile_external_videos
                (profile_id, platform, external_video_id, external_url, title,
                 description, tags, category_id, privacy_status, publish_at,
                 published_at, duration, thumbnail_url, profile_item_id,
                 publish_job_id, raw_json, first_seen_at, last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, platform, external_video_id) DO UPDATE SET
                external_url=excluded.external_url,
                title=excluded.title,
                description=excluded.description,
                tags=excluded.tags,
                category_id=excluded.category_id,
                privacy_status=excluded.privacy_status,
                publish_at=excluded.publish_at,
                published_at=excluded.published_at,
                duration=excluded.duration,
                thumbnail_url=excluded.thumbnail_url,
                profile_item_id=excluded.profile_item_id,
                publish_job_id=excluded.publish_job_id,
                raw_json=excluded.raw_json,
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at
            """,
            (
                int(profile_id),
                normalized_platform,
                clean_video_id,
                external_url,
                _normalize_profile_text(title, max_length=240),
                description,
                tags_value,
                category_id,
                privacy_status,
                publish_at,
                published_at,
                duration,
                thumbnail_url,
                int(profile_item_id) if profile_item_id is not None else None,
                int(publish_job_id) if publish_job_id is not None else None,
                raw_json_value,
                now,
                now,
                now,
            ),
        )
        row = con.execute(
            """
            SELECT id FROM local_storage_profile_external_videos
            WHERE profile_id=? AND platform=? AND external_video_id=?
            """,
            (int(profile_id), normalized_platform, clean_video_id),
        ).fetchone()
        return int(row["id"] if row is not None else cur.lastrowid)


def list_local_storage_profile_external_videos(
    profile_id: int,
    *,
    platform: str = "youtube",
    limit: int = 200,
) -> list[sqlite3.Row]:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    with connect() as con:
        return con.execute(
            """
            SELECT ev.*,
                   lspi.workspace_path AS profile_item_workspace_path,
                   pj.status AS publish_job_status,
                   pj.youtube_url AS publish_job_youtube_url
            FROM local_storage_profile_external_videos ev
            LEFT JOIN local_storage_profile_items lspi ON lspi.id=ev.profile_item_id
            LEFT JOIN publish_jobs pj ON pj.id=ev.publish_job_id
            WHERE ev.profile_id=? AND ev.platform=?
            ORDER BY COALESCE(ev.published_at, ev.last_seen_at) DESC, ev.id DESC
            LIMIT ?
            """,
            (int(profile_id), normalized_platform, int(limit)),
        ).fetchall()


def get_latest_local_storage_profile_item_publish_job(
    profile_item_id: int,
    *,
    platform: str = "youtube",
) -> sqlite3.Row | None:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            JOIN local_storage_profile_publish_jobs lspj
              ON lspj.publish_job_id=pj.id
            WHERE lspj.profile_item_id=? AND lspj.platform=?
            ORDER BY pj.id DESC
            LIMIT 1
            """,
            (int(profile_item_id), normalized_platform),
        ).fetchone()


def list_local_storage_profile_publish_jobs(
    profile_id: int,
    *,
    platform: str = "youtube",
    limit: int = 100,
) -> list[sqlite3.Row]:
    normalized_platform = str(platform or "youtube").strip().lower() or "youtube"
    with connect() as con:
        return con.execute(
            f"""
            {_PUBLISH_JOB_SELECT}
            JOIN local_storage_profile_publish_jobs lspj
              ON lspj.publish_job_id=pj.id
            WHERE lspj.profile_id=? AND lspj.platform=?
            ORDER BY pj.id DESC
            LIMIT ?
            """,
            (int(profile_id), normalized_platform, int(limit)),
        ).fetchall()


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
    _sync_workspace_status_tag(
        con,
        status=workspace_status,
        item_type=item_type,
        item_id=item_id,
        now=now,
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
    preserve_display_name: bool = False,
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
            next_display_name = (
                existing["display_name"]
                if preserve_display_name and _normalize_text(existing["display_name"])
                else (display_name if display_name is not None else existing["display_name"])
            )
            con.execute(
                """
                UPDATE social_accounts
                SET display_name=?, channel_title=?, access_token=?, refresh_token=?,
                    token_expires_at=?, scopes=?, oauth_profile_id=?, account_email=?,
                    last_connected_at=?, status=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    next_display_name,
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


def update_social_account_alias(account_id: int, display_name: str | None) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE social_accounts
            SET display_name=?, updated_at=?
            WHERE id=?
            """,
            (_normalize_text(display_name), now_utc(), int(account_id)),
        )
        return result.rowcount > 0


def set_social_account_metadata_sync_error(account_id: int, error: str | None) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE social_accounts
            SET metadata_sync_error=?, updated_at=?
            WHERE id=?
            """,
            (_normalize_text(error), now_utc(), int(account_id)),
        )
        return result.rowcount > 0


def update_social_account_channel_metadata(
    account_id: int,
    *,
    channel_id: str | None = None,
    channel_title: str | None = None,
    channel_description: str | None = None,
    channel_custom_url: str | None = None,
    channel_handle: str | None = None,
    channel_country: str | None = None,
    channel_published_at: str | None = None,
    channel_avatar_url: str | None = None,
    channel_thumbnails_json: str | None = None,
    channel_banner_url: str | None = None,
    channel_branding_json: str | None = None,
    subscriber_count: int | None = None,
    view_count: int | None = None,
    video_count: int | None = None,
    hidden_subscriber_count: bool | int | None = None,
    uploads_playlist_id: str | None = None,
    channel_status_json: str | None = None,
    channel_metadata_json: str | None = None,
    metadata_synced_at: str | None = None,
    metadata_sync_error: str | None = None,
) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE social_accounts
            SET channel_id=COALESCE(?, channel_id),
                channel_title=?,
                channel_description=?,
                channel_custom_url=?,
                channel_handle=?,
                channel_country=?,
                channel_published_at=?,
                channel_avatar_url=?,
                channel_thumbnails_json=?,
                channel_banner_url=?,
                channel_branding_json=?,
                subscriber_count=?,
                view_count=?,
                video_count=?,
                hidden_subscriber_count=?,
                uploads_playlist_id=?,
                channel_status_json=?,
                channel_metadata_json=?,
                metadata_synced_at=?,
                metadata_sync_error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                _normalize_text(channel_id),
                _normalize_text(channel_title),
                _normalize_text(channel_description),
                _normalize_text(channel_custom_url),
                _normalize_text(channel_handle),
                _normalize_text(channel_country),
                _normalize_text(channel_published_at),
                _normalize_text(channel_avatar_url),
                channel_thumbnails_json,
                _normalize_text(channel_banner_url),
                channel_branding_json,
                subscriber_count,
                view_count,
                video_count,
                int(bool(hidden_subscriber_count)) if hidden_subscriber_count is not None else None,
                _normalize_text(uploads_playlist_id),
                channel_status_json,
                channel_metadata_json,
                metadata_synced_at or now_utc(),
                _normalize_text(metadata_sync_error),
                now_utc(),
                int(account_id),
            ),
        )
        return result.rowcount > 0


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
    studio_template_id: int | None = None,
) -> int:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=True)
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO edit_templates
                (key, name, description, renderer, recipe_json,
                 enabled, created_at, updated_at, studio_template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                studio_template_id,
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
    studio_template_id: Any = _UNSET,
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
                enabled=?, updated_at=?, studio_template_id=?
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
                _updated_value(row, "studio_template_id", studio_template_id),
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


def _legacy_edit_template_to_studio_template_id_in_connection(
    con: sqlite3.Connection,
    template_id: int | None,
) -> int | None:
    if template_id is None:
        return None
    row = con.execute(
        "SELECT * FROM edit_templates WHERE id=?",
        (int(template_id),),
    ).fetchone()
    if row is None:
        return None
    if row["studio_template_id"] is not None:
        return int(row["studio_template_id"])
    studio = con.execute(
        """
        SELECT id
        FROM studio_templates
        WHERE template_key=? AND deleted_at IS NULL
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (str(row["key"]),),
    ).fetchone()
    if studio is not None:
        studio_id = int(studio["id"])
    else:
        from .studio_templates import legacy_edit_template_to_definition

        definition = legacy_edit_template_to_definition(row)
        version_row = con.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS max_version
            FROM studio_templates
            WHERE template_key=?
            """,
            (definition["key"],),
        ).fetchone()
        version = int(version_row["max_version"] or 0) + 1
        now = now_utc()
        cur = con.execute(
            """
            INSERT INTO studio_templates
                (template_key, name, engine, version, status,
                 definition_json, created_at, updated_at)
            VALUES (?, ?, 'remotion', ?, ?, ?, ?, ?)
            """,
            (
                definition["key"],
                definition["name"],
                version,
                "active" if bool(row["enabled"]) else "archived",
                json.dumps(definition, ensure_ascii=False),
                now,
                now,
            ),
        )
        studio_id = int(cur.lastrowid)
    con.execute(
        "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
        (studio_id, now_utc(), int(template_id)),
    )
    return studio_id


def create_channel_profile(
    *,
    name: str,
    youtube_account_id: int | None = None,
    default_template_id: int | None = None,
    default_studio_template_id: int | None = None,
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
        if default_studio_template_id is None:
            default_studio_template_id = _legacy_edit_template_to_studio_template_id_in_connection(
                con,
                default_template_id,
            )
        cur = con.execute(
            """
            INSERT INTO channel_profiles
                (name, youtube_account_id, default_template_id, reaction_pool_id,
                 title_template, description_template, tags_template,
                 default_privacy, default_category_id, enabled, created_at, updated_at,
                 default_studio_template_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                default_studio_template_id,
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
                et.key AS default_template_key,
                st.name AS default_studio_template_name,
                st.template_key AS default_studio_template_key,
                st.version AS default_studio_template_version,
                rp.name AS reaction_pool_name
            FROM channel_profiles cp
            LEFT JOIN social_accounts sa ON sa.id=cp.youtube_account_id
            LEFT JOIN edit_templates et ON et.id=cp.default_template_id
            LEFT JOIN studio_templates st ON st.id=cp.default_studio_template_id
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
    default_studio_template_id: Any = _UNSET,
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
        if (
            default_studio_template_id is _UNSET
            and default_template_id is not _UNSET
            and default_template_id is not None
        ):
            default_studio_template_id = _legacy_edit_template_to_studio_template_id_in_connection(
                con,
                int(default_template_id),
            )
        enabled_value = row["enabled"] if enabled is _UNSET else (1 if enabled else 0)
        con.execute(
            """
            UPDATE channel_profiles
            SET name=?, youtube_account_id=?, default_template_id=?, reaction_pool_id=?,
                title_template=?, description_template=?, tags_template=?,
                default_privacy=?, default_category_id=?, enabled=?, updated_at=?,
                default_studio_template_id=?
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
                _updated_value(
                    row,
                    "default_studio_template_id",
                    default_studio_template_id,
                ),
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
    studio_template_id: int | None = None,
    studio_project_id: int | None = None,
    remotion_render_job_id: int | None = None,
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
                 status, renderer, recipe_json, created_at,
                 studio_template_id, studio_project_id, remotion_render_job_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
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
                studio_template_id,
                studio_project_id,
                remotion_render_job_id,
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
                st.name AS studio_template_name,
                st.template_key AS studio_template_key,
                st.version AS studio_template_version,
                st.status AS studio_template_status,
                st.deleted_at AS studio_template_deleted_at,
                rrj.status AS remotion_status,
                rrj.output_path AS remotion_output_path,
                rrj.error AS remotion_error,
                rrj.progress_percent AS remotion_progress_percent,
                rrj.progress_stage AS remotion_progress_stage,
                rrj.progress_message AS remotion_progress_message,
                rrj.started_at AS remotion_started_at,
                rrj.finished_at AS remotion_finished_at,
                ra.name AS reaction_asset_name
            FROM edit_jobs ej
            LEFT JOIN channel_profiles cp ON cp.id=ej.channel_profile_id
            LEFT JOIN edit_templates et ON et.id=ej.template_id
            LEFT JOIN studio_templates st ON st.id=ej.studio_template_id
            LEFT JOIN remotion_render_jobs rrj ON rrj.id=ej.remotion_render_job_id
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


def find_existing_studio_edit_job(
    workspace_item_key: str,
    channel_profile_id: int,
    studio_template_id: int,
    *,
    include_done: bool = True,
) -> sqlite3.Row | None:
    statuses = ("queued", "rendering", "done") if include_done else ("queued", "rendering")
    placeholders = ",".join("?" for _ in statuses)
    with connect() as con:
        return con.execute(
            f"""
            SELECT ej.*
            FROM edit_jobs ej
            LEFT JOIN remotion_render_jobs rrj ON rrj.id=ej.remotion_render_job_id
            WHERE ej.workspace_item_key=?
              AND ej.channel_profile_id=?
              AND ej.studio_template_id=?
              AND COALESCE(rrj.status, ej.status) IN ({placeholders})
            ORDER BY
                CASE COALESCE(rrj.status, ej.status)
                    WHEN 'rendering' THEN 0
                    WHEN 'queued' THEN 1
                    WHEN 'done' THEN 2
                    ELSE 3
                END,
                ej.id DESC
            LIMIT 1
            """,
            (
                workspace_item_key,
                int(channel_profile_id),
                int(studio_template_id),
                *statuses,
            ),
        ).fetchone()


def update_edit_job_plan(
    job_id: int,
    *,
    input_path: str | None,
    output_path: str | None,
    recipe_json: dict[str, Any] | str | None,
    studio_template_id: int | None = None,
    studio_project_id: int | None = None,
    remotion_render_job_id: int | None = None,
    renderer: str | None = None,
) -> bool:
    normalized_recipe = _normalize_recipe_json(recipe_json, required=False)
    assignments = ["input_path=?", "output_path=?", "recipe_json=?"]
    values: list[Any] = [input_path, output_path, normalized_recipe]
    if studio_template_id is not None:
        assignments.append("studio_template_id=?")
        values.append(int(studio_template_id))
    if studio_project_id is not None:
        assignments.append("studio_project_id=?")
        values.append(int(studio_project_id))
    if remotion_render_job_id is not None:
        assignments.append("remotion_render_job_id=?")
        values.append(int(remotion_render_job_id))
    if renderer is not None:
        assignments.append("renderer=?")
        values.append(renderer)
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE edit_jobs
            SET {", ".join(assignments)}
            WHERE id=?
            """,
            (*values, int(job_id)),
        )
        return result.rowcount > 0


def update_edit_job_remotion_links(
    job_id: int,
    *,
    input_path: str | None,
    studio_template_id: int,
    studio_project_id: int,
    remotion_render_job_id: int,
    output_path: str | None,
    recipe_json: dict[str, Any] | str | None,
    renderer: str = "remotion",
) -> bool:
    return update_edit_job_plan(
        job_id,
        input_path=input_path,
        output_path=output_path,
        recipe_json=recipe_json,
        studio_template_id=studio_template_id,
        studio_project_id=studio_project_id,
        remotion_render_job_id=remotion_render_job_id,
        renderer=renderer,
    )


def sync_edit_job_from_remotion_render_job(render_job_id: int) -> bool:
    with connect() as con:
        row = con.execute(
            """
            SELECT id, status, output_path, error, started_at, finished_at
            FROM remotion_render_jobs
            WHERE id=?
            """,
            (int(render_job_id),),
        ).fetchone()
        if row is None:
            return False
        status = str(row["status"] or "queued")
        edit_status = status if status in EDIT_JOB_STATUSES else "queued"
        edited_path = row["output_path"] if edit_status == "done" else None
        result = con.execute(
            """
            UPDATE edit_jobs
            SET status=?,
                edited_path=COALESCE(?, edited_path),
                error=?,
                started_at=COALESCE(started_at, ?),
                finished_at=?
            WHERE remotion_render_job_id=?
            """,
            (
                edit_status,
                edited_path,
                row["error"],
                row["started_at"],
                row["finished_at"],
                int(render_job_id),
            ),
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
        "status='queued' AND remotion_render_job_id IS NULL",
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


# ---------------------------------------------------------------------------
# Universal Video Workbench manual segments
# ---------------------------------------------------------------------------

def create_video_segment(
    *,
    source_path: str,
    start_sec: float,
    end_sec: float,
    duration_sec: float,
    label: str | None = None,
    notes: str | None = None,
    status: str = "draft",
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO video_segments
                (source_path, label, start_sec, end_sec, duration_sec,
                 status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_path,
                label,
                float(start_sec),
                float(end_sec),
                float(duration_sec),
                status,
                notes,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_video_segment(segment_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM video_segments WHERE id=?",
            (int(segment_id),),
        ).fetchone()


def list_video_segments_for_source(source_path: str) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM video_segments
            WHERE source_path=?
            ORDER BY start_sec ASC, id ASC
            """,
            (source_path,),
        ).fetchall()


def update_video_segment(
    segment_id: int,
    *,
    label: str | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    duration_sec: float | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> bool:
    fields: dict[str, Any] = {
        "label": label,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "duration_sec": duration_sec,
        "status": status,
        "notes": notes,
    }
    assignments: list[str] = []
    values: list[Any] = []
    for name, value in fields.items():
        if value is None:
            continue
        assignments.append(f"{name}=?")
        values.append(value)
    if not assignments:
        return False
    assignments.append("updated_at=?")
    values.append(now_utc())
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE video_segments
            SET {", ".join(assignments)}
            WHERE id=?
            """,
            (*values, int(segment_id)),
        )
        return result.rowcount > 0


def delete_video_segment(segment_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            "DELETE FROM video_segments WHERE id=?",
            (int(segment_id),),
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
    max_auto_retries: int = 2,
) -> int:
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO remotion_render_jobs
                (studio_project_id, status, output_path, renderer_engine,
                 render_profile, duration_limit_sec, start_offset_sec,
                 full_length, max_auto_retries, created_at)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(studio_project_id),
                output_path,
                renderer_engine,
                render_profile,
                duration_limit_sec,
                float(start_offset_sec or 0),
                1 if full_length else 0,
                max(0, int(max_auto_retries)),
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


def create_shorts_pipeline_run(
    *,
    source_mode: str,
    source_path: str | None = None,
    source_paths_json: list[str] | str | None = None,
    split_seconds: int = 60,
    skip_json: list[str] | str | None = None,
    overwrite: bool = False,
    studio_template_id: int | None = None,
    template_key: str | None = None,
    reaction_strategy: str = "fixed_asset",
    reaction_asset_id: int | None = None,
    reaction_pool_id: int | None = None,
    parameter_values_json: dict[str, Any] | str | None = None,
    renderer_engine: str = "ffmpeg_fast",
    render_profile: str = "low_540p",
    duration_limit_sec: float | None = None,
    start_offset_sec: float = 0,
    full_length: bool = False,
    tag_ids_json: list[int] | str | None = None,
    channel_tag_id: int | None = None,
    summary_json: dict[str, Any] | str | None = None,
) -> int:
    mode = str(source_mode or "").strip().lower()
    if mode not in {"external_file", "workspace"}:
        raise ValueError("source_mode должен быть external_file или workspace.")
    seconds = int(split_seconds or 0)
    if seconds <= 0:
        raise ValueError("split_seconds должен быть больше 0.")
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO shorts_pipeline_runs
                (status, source_mode, source_path, source_paths_json,
                 split_seconds, skip_json, overwrite, studio_template_id,
                 template_key, reaction_strategy, reaction_asset_id,
                 reaction_pool_id, parameter_values_json, renderer_engine,
                 render_profile, duration_limit_sec, start_offset_sec,
                 full_length, tag_ids_json, channel_tag_id, summary_json,
                 created_at, started_at, updated_at)
            VALUES ('queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                source_path,
                _normalize_json_array(source_paths_json),
                seconds,
                _normalize_json_array(skip_json),
                1 if overwrite else 0,
                studio_template_id,
                template_key,
                reaction_strategy,
                reaction_asset_id,
                reaction_pool_id,
                _normalize_json_object(parameter_values_json),
                renderer_engine,
                render_profile,
                duration_limit_sec,
                float(start_offset_sec or 0),
                1 if full_length else 0,
                _normalize_json_array(tag_ids_json),
                channel_tag_id,
                _normalize_json_object(summary_json),
                now,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_shorts_pipeline_run(
    run_id: int,
    *,
    status: str | None = None,
    imported_source_path: str | None = None,
    remotion_batch_id: int | None = None,
    summary_json: dict[str, Any] | str | None = None,
    error: str | None = None,
    finish: bool = False,
) -> bool:
    allowed = {
        "queued", "splitting", "rendering", "syncing_profile",
        "done", "failed", "cancelled",
    }
    assignments: list[str] = []
    values: list[Any] = []
    if status is not None:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in allowed:
            raise ValueError("Некорректный status shorts pipeline run.")
        assignments.append("status=?")
        values.append(normalized_status)
    if imported_source_path is not None:
        assignments.append("imported_source_path=?")
        values.append(imported_source_path)
    if remotion_batch_id is not None:
        assignments.append("remotion_batch_id=?")
        values.append(int(remotion_batch_id))
    if summary_json is not None:
        assignments.append("summary_json=?")
        values.append(_normalize_json_object(summary_json))
    if error is not None:
        assignments.append("error=?")
        values.append(error)
    if finish:
        assignments.append("finished_at=COALESCE(finished_at, ?)")
        values.append(now_utc())
    if not assignments:
        return False
    assignments.append("updated_at=?")
    values.append(now_utc())
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE shorts_pipeline_runs
            SET {", ".join(assignments)}
            WHERE id=?
            """,
            (*values, int(run_id)),
        )
        return result.rowcount > 0


def get_shorts_pipeline_run(run_id: int) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM shorts_pipeline_runs WHERE id=?",
            (int(run_id),),
        ).fetchone()


def list_shorts_pipeline_runs(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM shorts_pipeline_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 50), 200)),),
        ).fetchall()


def get_active_shorts_pipeline_run() -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM shorts_pipeline_runs
            WHERE status IN ('queued', 'splitting', 'rendering', 'syncing_profile')
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()


def create_shorts_pipeline_run_item(
    *,
    run_id: int,
    source_workspace_path: str | None = None,
    segment_workspace_path: str | None = None,
    render_job_id: int | None = None,
    output_workspace_path: str | None = None,
    status: str = "queued",
    error: str | None = None,
) -> int:
    now = now_utc()
    with connect() as con:
        cur = con.execute(
            """
            INSERT INTO shorts_pipeline_run_items
                (run_id, source_workspace_path, segment_workspace_path,
                 render_job_id, output_workspace_path, status, error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(run_id),
                source_workspace_path,
                segment_workspace_path,
                render_job_id,
                output_workspace_path,
                status,
                error,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_shorts_pipeline_run_item(
    item_id: int,
    *,
    render_job_id: int | None = None,
    output_workspace_path: str | None = None,
    status: str | None = None,
    error: str | None = None,
) -> bool:
    assignments: list[str] = []
    values: list[Any] = []
    if render_job_id is not None:
        assignments.append("render_job_id=?")
        values.append(int(render_job_id))
    if output_workspace_path is not None:
        assignments.append("output_workspace_path=?")
        values.append(output_workspace_path)
    if status is not None:
        assignments.append("status=?")
        values.append(status)
    if error is not None:
        assignments.append("error=?")
        values.append(error)
    if not assignments:
        return False
    assignments.append("updated_at=?")
    values.append(now_utc())
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE shorts_pipeline_run_items
            SET {", ".join(assignments)}
            WHERE id=?
            """,
            (*values, int(item_id)),
        )
        return result.rowcount > 0


def update_shorts_pipeline_run_item_by_render_job(
    render_job_id: int,
    *,
    output_workspace_path: str | None = None,
    status: str | None = None,
    error: str | None = None,
) -> bool:
    assignments: list[str] = []
    values: list[Any] = []
    if output_workspace_path is not None:
        assignments.append("output_workspace_path=?")
        values.append(output_workspace_path)
    if status is not None:
        assignments.append("status=?")
        values.append(status)
    if error is not None:
        assignments.append("error=?")
        values.append(error)
    if not assignments:
        return False
    assignments.append("updated_at=?")
    values.append(now_utc())
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE shorts_pipeline_run_items
            SET {", ".join(assignments)}
            WHERE render_job_id=?
            """,
            (*values, int(render_job_id)),
        )
        return result.rowcount > 0


def list_shorts_pipeline_run_items(run_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT *
            FROM shorts_pipeline_run_items
            WHERE run_id=?
            ORDER BY id ASC
            """,
            (int(run_id),),
        ).fetchall()


def list_shorts_pipeline_run_items_matching_workspace_paths(
    workspace_paths: list[str],
) -> list[sqlite3.Row]:
    normalized_paths = sorted({str(path).strip() for path in workspace_paths if str(path).strip()})
    if not normalized_paths:
        return []
    placeholders = ",".join("?" for _ in normalized_paths)
    params = [*normalized_paths, *normalized_paths, *normalized_paths]
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM shorts_pipeline_run_items
            WHERE source_workspace_path IN ({placeholders})
               OR segment_workspace_path IN ({placeholders})
               OR output_workspace_path IN ({placeholders})
            ORDER BY id ASC
            """,
            params,
        ).fetchall()


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


def update_remotion_render_job_progress(
    job_id: int,
    *,
    progress_percent: float | None = None,
    progress_stage: str | None = None,
    progress_message: str | None = None,
    current_frame: int | None = None,
    total_frames: int | None = None,
    out_time_sec: float | None = None,
    speed: str | None = None,
    eta_sec: float | None = None,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
) -> bool:
    assignments: list[str] = ["last_heartbeat_at=?"]
    values: list[Any] = [now_utc()]
    fields: dict[str, Any] = {
        "progress_percent": progress_percent,
        "progress_stage": progress_stage,
        "progress_message": progress_message,
        "current_frame": current_frame,
        "total_frames": total_frames,
        "out_time_sec": out_time_sec,
        "speed": speed,
        "eta_sec": eta_sec,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }
    for name, value in fields.items():
        if value is None:
            continue
        assignments.append(f"{name}=?")
        values.append(value)
    with connect() as con:
        result = con.execute(
            f"""
            UPDATE remotion_render_jobs
            SET {", ".join(assignments)}
            WHERE id=?
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
                elapsed_sec=NULL,
                progress_percent=0,
                progress_stage='starting',
                progress_message='Render starting',
                current_frame=NULL,
                total_frames=NULL,
                out_time_sec=NULL,
                speed=NULL,
                eta_sec=NULL,
                output_size_bytes=NULL,
                completed_message=NULL
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
        con.execute(
            """
            UPDATE edit_jobs
            SET status='rendering',
                started_at=COALESCE(started_at, ?),
                finished_at=NULL,
                error=NULL,
                review_status='pending',
                reviewed_at=NULL,
                review_note=NULL
            WHERE remotion_render_job_id=?
            """,
            (now_utc(), int(job_id)),
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
                elapsed_sec=NULL,
                progress_percent=0,
                progress_stage='starting',
                progress_message='Render starting',
                current_frame=NULL,
                total_frames=NULL,
                out_time_sec=NULL,
                speed=NULL,
                eta_sec=NULL,
                output_size_bytes=NULL,
                completed_message=NULL
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
        con.execute(
            """
            UPDATE edit_jobs
            SET status='rendering',
                started_at=COALESCE(started_at, ?),
                finished_at=NULL,
                error=NULL,
                review_status='pending',
                reviewed_at=NULL,
                review_note=NULL
            WHERE remotion_render_job_id=?
            """,
            (now, job_id),
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
    output_size_bytes: int | None = None
    try:
        output = Path(output_path).expanduser()
        if output.is_file() and not output.is_symlink():
            output_size_bytes = int(output.stat().st_size)
    except OSError:
        output_size_bytes = None
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
                elapsed_sec=?,
                progress_percent=100,
                progress_stage='done',
                progress_message='Render completed',
                output_size_bytes=?,
                completed_message='Готово'
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
                output_size_bytes,
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
        con.execute(
            """
            UPDATE edit_jobs
            SET status='done',
                edited_path=?,
                error=NULL,
                finished_at=?,
                review_status='pending',
                reviewed_at=NULL,
                review_note=NULL
            WHERE remotion_render_job_id=?
            """,
            (output_path, now_utc(), int(job_id)),
        )
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
                elapsed_sec=COALESCE(?, elapsed_sec),
                progress_stage='failed',
                progress_message=?
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
                error,
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
        con.execute(
            """
            UPDATE edit_jobs
            SET status='failed', error=?, finished_at=?
            WHERE remotion_render_job_id=?
            """,
            (error, now_utc(), int(job_id)),
        )
        return result.rowcount > 0


def cancel_remotion_render_job(job_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE remotion_render_jobs
            SET status='cancelled',
                finished_at=?,
                progress_stage='cancelled',
                progress_message='Render cancelled'
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
        if result.rowcount > 0:
            con.execute(
                """
                UPDATE edit_jobs
                SET status='cancelled', finished_at=?
                WHERE remotion_render_job_id=?
                """,
                (now_utc(), int(job_id)),
            )
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
                elapsed_sec=NULL,
                progress_percent=0,
                progress_stage=NULL,
                progress_message=NULL,
                current_frame=NULL,
                total_frames=NULL,
                out_time_sec=NULL,
                speed=NULL,
                eta_sec=NULL,
                output_size_bytes=NULL,
                completed_message=NULL,
                auto_retry_count=0
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
        if result.rowcount > 0:
            con.execute(
                """
                UPDATE edit_jobs
                SET status='queued',
                    edited_path=NULL,
                    error=NULL,
                    started_at=NULL,
                    finished_at=NULL
                WHERE remotion_render_job_id=?
                """,
                (int(job_id),),
            )
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
                elapsed_sec=NULL,
                progress_percent=0,
                progress_stage=NULL,
                progress_message=NULL,
                current_frame=NULL,
                total_frames=NULL,
                out_time_sec=NULL,
                speed=NULL,
                eta_sec=NULL,
                output_size_bytes=NULL,
                completed_message=NULL,
                auto_retry_count=0
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


def auto_retry_failed_remotion_render_batch(
    batch_id: int,
    *,
    default_max_retries: int = 2,
) -> int:
    max_retries = max(0, int(default_max_retries))
    with connect() as con:
        rows = con.execute(
            """
            SELECT rrj.id AS render_job_id
            FROM remotion_render_batch_items rbi
            JOIN remotion_render_jobs rrj ON rrj.id = rbi.render_job_id
            WHERE rbi.batch_id=?
              AND rbi.status='failed'
              AND rrj.status='failed'
              AND COALESCE(rrj.auto_retry_count, 0) < COALESCE(rrj.max_auto_retries, ?)
            ORDER BY rrj.id
            """,
            (int(batch_id), max_retries),
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
                elapsed_sec=NULL,
                progress_percent=0,
                progress_stage=NULL,
                progress_message=NULL,
                current_frame=NULL,
                total_frames=NULL,
                out_time_sec=NULL,
                speed=NULL,
                eta_sec=NULL,
                output_size_bytes=NULL,
                completed_message=NULL,
                auto_retry_count=COALESCE(auto_retry_count, 0) + 1,
                max_auto_retries=COALESCE(max_auto_retries, ?)
            WHERE id IN ({placeholders})
              AND status='failed'
              AND COALESCE(auto_retry_count, 0) < COALESCE(max_auto_retries, ?)
            """,
            [max_retries, *job_ids, max_retries],
        )
        con.execute(
            f"""
            UPDATE remotion_render_batch_items
            SET status='queued', error=NULL, updated_at=?
            WHERE render_job_id IN ({placeholders})
              AND status='failed'
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
                finished_at=?,
                progress_stage='failed',
                progress_message='Remotion render был прерван перезапуском backend.'
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


def list_completed_remotion_render_jobs(limit: int = 5) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            """
            SELECT rrj.*,
                   sp.template_key AS template_key,
                   sp.main_workspace_path AS main_workspace_path,
                   sp.studio_template_id AS studio_template_id
            FROM remotion_render_jobs rrj
            JOIN studio_projects sp ON sp.id = rrj.studio_project_id
            WHERE rrj.status='done'
            ORDER BY rrj.finished_at DESC, rrj.id DESC
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
                   rrj.elapsed_sec AS elapsed_sec,
                   rrj.progress_percent AS progress_percent,
                   rrj.progress_stage AS progress_stage,
                   rrj.progress_message AS progress_message,
                   rrj.current_frame AS current_frame,
                   rrj.total_frames AS total_frames,
                   rrj.out_time_sec AS out_time_sec,
                   rrj.speed AS speed,
                   rrj.eta_sec AS eta_sec,
                   rrj.output_size_bytes AS output_size_bytes,
                   rrj.completed_message AS completed_message
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
            SET status='cancelled',
                finished_at=?,
                progress_stage='cancelled',
                progress_message='Render cancelled'
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
    *,
    include_deleted: bool = True,
) -> sqlite3.Row | None:
    deleted_clause = "" if include_deleted else "AND deleted_at IS NULL"
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM studio_templates
            WHERE template_key=?
              {deleted_clause}
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (template_key,),
        ).fetchone()


def list_studio_templates(
    *,
    include_deleted: bool = False,
    status: str | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if status is not None:
        clauses.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as con:
        return con.execute(
            f"""
            SELECT *
            FROM studio_templates
            {where}
            ORDER BY updated_at DESC, id DESC
            """,
            params,
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


def studio_template_usage_counts(template_id: int) -> dict[str, int]:
    tid = int(template_id)
    with connect() as con:
        counts = {
            "studio_projects": int(con.execute(
                "SELECT COUNT(*) AS count FROM studio_projects WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "remotion_batches": int(con.execute(
                "SELECT COUNT(*) AS count FROM remotion_render_batches WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "remotion_pipelines": int(con.execute(
                "SELECT COUNT(*) AS count FROM remotion_pipelines WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "channel_profiles": int(con.execute(
                "SELECT COUNT(*) AS count FROM channel_profiles WHERE default_studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "edit_jobs": int(con.execute(
                "SELECT COUNT(*) AS count FROM edit_jobs WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "edit_templates": int(con.execute(
                "SELECT COUNT(*) AS count FROM edit_templates WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
        }
    counts["total"] = sum(counts.values())
    return counts


def delete_studio_template_safe(
    template_id: int,
    *,
    seeded_keys: set[str] | None = None,
) -> dict[str, Any]:
    seeded = seeded_keys or set()
    tid = int(template_id)
    with connect() as con:
        row = con.execute(
            "SELECT * FROM studio_templates WHERE id=?",
            (tid,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError("Studio template не найден.")
        counts = {
            "studio_projects": int(con.execute(
                "SELECT COUNT(*) AS count FROM studio_projects WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "remotion_batches": int(con.execute(
                "SELECT COUNT(*) AS count FROM remotion_render_batches WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "remotion_pipelines": int(con.execute(
                "SELECT COUNT(*) AS count FROM remotion_pipelines WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "channel_profiles": int(con.execute(
                "SELECT COUNT(*) AS count FROM channel_profiles WHERE default_studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "edit_jobs": int(con.execute(
                "SELECT COUNT(*) AS count FROM edit_jobs WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
            "edit_templates": int(con.execute(
                "SELECT COUNT(*) AS count FROM edit_templates WHERE studio_template_id=?",
                (tid,),
            ).fetchone()["count"]),
        }
        total = sum(counts.values())
        hard_delete = (
            total == 0
            and str(row["status"]) == "draft"
            and str(row["template_key"]) not in seeded
        )
        if hard_delete:
            con.execute("DELETE FROM studio_templates WHERE id=?", (tid,))
            action = "hard_deleted"
        else:
            con.execute(
                """
                UPDATE studio_templates
                SET deleted_at=COALESCE(deleted_at, ?),
                    status=CASE WHEN status='draft' THEN 'archived' ELSE status END,
                    updated_at=?
                WHERE id=?
                """,
                (now_utc(), now_utc(), tid),
            )
            con.execute(
                """
                UPDATE channel_profiles
                SET default_studio_template_id=NULL, updated_at=?
                WHERE default_studio_template_id=?
                """,
                (now_utc(), tid),
            )
            action = "soft_deleted"
        return {
            "action": action,
            "template_id": tid,
            "usage": counts | {"total": total},
        }


def restore_studio_template(template_id: int) -> bool:
    with connect() as con:
        result = con.execute(
            """
            UPDATE studio_templates
            SET deleted_at=NULL,
                updated_at=?,
                status=CASE WHEN status='archived' THEN 'draft' ELSE status END
            WHERE id=?
            """,
            (now_utc(), int(template_id)),
        )
        return result.rowcount > 0


def link_legacy_edit_templates_to_studio() -> int:
    """Attach legacy edit_templates/channel defaults to matching Studio templates."""
    with connect() as con:
        rows = con.execute(
            """
            SELECT et.id, et.key
            FROM edit_templates et
            WHERE et.studio_template_id IS NULL
            """
        ).fetchall()
        updated = 0
        for row in rows:
            studio = con.execute(
                """
                SELECT id
                FROM studio_templates
                WHERE template_key=? AND deleted_at IS NULL
                ORDER BY version DESC, id DESC
                LIMIT 1
                """,
                (row["key"],),
            ).fetchone()
            if studio is None:
                continue
            con.execute(
                "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
                (int(studio["id"]), now_utc(), int(row["id"])),
            )
            con.execute(
                """
                UPDATE channel_profiles
                SET default_studio_template_id=?
                WHERE default_template_id=? AND default_studio_template_id IS NULL
                """,
                (int(studio["id"]), int(row["id"])),
            )
            updated += 1
        return updated


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
