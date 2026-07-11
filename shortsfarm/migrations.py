"""Lightweight SQLite migration runner for ShortsFarm."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

_COLUMN_REPAIRS: dict[str, list[tuple[str, str]]] = {
    "local_storage_profiles": [
        ("avatar_url", "TEXT"),
        ("youtube_branding_sync_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("name_override", "INTEGER NOT NULL DEFAULT 0"),
        ("handle_override", "INTEGER NOT NULL DEFAULT 0"),
        ("description_override", "INTEGER NOT NULL DEFAULT 0"),
        ("avatar_override", "INTEGER NOT NULL DEFAULT 0"),
        ("banner_override", "INTEGER NOT NULL DEFAULT 0"),
        ("youtube_branding_synced_at", "TEXT"),
        ("youtube_branding_sync_error", "TEXT"),
        ("youtube_branding_attempted_at", "TEXT"),
        ("banner_url", "TEXT"),
    ],
    "social_accounts": [
        ("channel_description", "TEXT"),
        ("channel_custom_url", "TEXT"),
        ("channel_handle", "TEXT"),
        ("channel_country", "TEXT"),
        ("channel_published_at", "TEXT"),
        ("channel_avatar_url", "TEXT"),
        ("channel_thumbnails_json", "TEXT"),
        ("subscriber_count", "INTEGER"),
        ("view_count", "INTEGER"),
        ("video_count", "INTEGER"),
        ("hidden_subscriber_count", "INTEGER"),
        ("uploads_playlist_id", "TEXT"),
        ("channel_status_json", "TEXT"),
        ("channel_metadata_json", "TEXT"),
        ("metadata_synced_at", "TEXT"),
        ("metadata_sync_error", "TEXT"),
        ("channel_banner_url", "TEXT"),
        ("channel_branding_json", "TEXT"),
    ],
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _repair_known_columns(con: sqlite3.Connection) -> None:
    """Repair idempotent columns for migrations that used sequential ALTERs.

    Older builds could mark a migration as applied after SQLite stopped on the
    first duplicate-column error.  This hook is intentionally independent from
    schema_migrations and always checks the real table shape.
    """
    repaired = False
    for table, specs in _COLUMN_REPAIRS.items():
        if not _table_exists(con, table):
            continue
        existing = {
            row[1]
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, ddl in specs:
            if name in existing:
                continue
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            existing.add(name)
            repaired = True

    if _table_exists(con, "local_storage_profiles"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_local_storage_profiles_youtube_branding
                ON local_storage_profiles(youtube_branding_sync_enabled)
            """
        )
        repaired = True
    if _table_exists(con, "social_accounts"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_social_accounts_channel_id
                ON social_accounts(platform, channel_id)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_social_accounts_metadata_synced_at
                ON social_accounts(metadata_synced_at)
            """
        )
        repaired = True
    if repaired:
        con.commit()


def apply_all(con: sqlite3.Connection) -> list[str]:
    """Apply every pending *.sql migration.  Returns list of newly applied versions."""
    con.execute(_TRACKING_DDL)
    con.commit()
    _repair_known_columns(con)

    applied: set[str] = {
        row[0]
        for row in con.execute("SELECT version FROM schema_migrations").fetchall()
    }

    newly: list[str] = []

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = sql_file.stem
        if version in applied:
            continue

        sql = sql_file.read_text(encoding="utf-8")
        try:
            # executescript issues an implicit COMMIT first, then runs DDL
            con.executescript(sql)
        except sqlite3.OperationalError as exc:
            # "duplicate column name" means the migration was already applied
            # outside the tracking system - register it and continue.
            if "duplicate column name" in str(exc).lower():
                pass
            else:
                raise RuntimeError(f"Migration {version} failed: {exc}") from exc

        con.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, _now_utc()),
        )
        con.commit()
        newly.append(version)

    _repair_known_columns(con)
    return newly


def run_migrations() -> list[str]:
    """Open the project DB and apply all pending migrations."""
    from .config import db_path, ensure_dirs

    ensure_dirs()

    con = sqlite3.connect(str(db_path()))
    con.execute("PRAGMA journal_mode = WAL")
    try:
        return apply_all(con)
    finally:
        con.close()
