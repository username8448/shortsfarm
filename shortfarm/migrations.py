"""Lightweight SQLite migration runner for ShortFarm."""
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


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_all(con: sqlite3.Connection) -> list[str]:
    """Apply every pending *.sql migration.  Returns list of newly applied versions."""
    con.execute(_TRACKING_DDL)
    con.commit()

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
