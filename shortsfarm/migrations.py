"""Lightweight SQLite migration runner for ShortsFarm."""
from __future__ import annotations

import sqlite3
import json
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
    "edit_templates": [
        ("studio_template_id", "INTEGER REFERENCES studio_templates(id) ON DELETE SET NULL"),
    ],
    "channel_profiles": [
        ("default_studio_template_id", "INTEGER REFERENCES studio_templates(id) ON DELETE SET NULL"),
    ],
    "edit_jobs": [
        ("studio_template_id", "INTEGER REFERENCES studio_templates(id) ON DELETE SET NULL"),
        ("studio_project_id", "INTEGER REFERENCES studio_projects(id) ON DELETE SET NULL"),
        ("remotion_render_job_id", "INTEGER REFERENCES remotion_render_jobs(id) ON DELETE SET NULL"),
    ],
    "studio_templates": [
        ("deleted_at", "TEXT"),
    ],
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
    if _table_exists(con, "edit_templates"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edit_templates_studio_template
                ON edit_templates(studio_template_id)
            """
        )
        repaired = True
    if _table_exists(con, "channel_profiles"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_profiles_default_studio_template
                ON channel_profiles(default_studio_template_id)
            """
        )
        repaired = True
    if _table_exists(con, "edit_jobs"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_template
                ON edit_jobs(studio_template_id)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_project
                ON edit_jobs(studio_project_id)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edit_jobs_remotion_render_job
                ON edit_jobs(remotion_render_job_id)
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_duplicate
                ON edit_jobs(workspace_item_key, channel_profile_id, studio_template_id, status)
            """
        )
        repaired = True
    if _table_exists(con, "studio_templates"):
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_studio_templates_deleted_at
                ON studio_templates(deleted_at)
            """
        )
        repaired = True
    if repaired:
        con.commit()


def _ensure_migration_reports(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_reports (
            migration_key TEXT PRIMARY KEY,
            report_json   TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
        """
    )


def _store_migration_report(
    con: sqlite3.Connection,
    migration_key: str,
    report: dict[str, object],
) -> None:
    _ensure_migration_reports(con)
    con.execute(
        """
        INSERT INTO migration_reports (migration_key, report_json, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(migration_key) DO UPDATE SET
            report_json=excluded.report_json,
            created_at=excluded.created_at
        """,
        (migration_key, json.dumps(report, ensure_ascii=False), _now_utc()),
    )


def _next_template_version(con: sqlite3.Connection, key: str) -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(version), 0) AS max_version FROM studio_templates WHERE template_key=?",
        (key,),
    ).fetchone()
    return int(row["max_version"] if isinstance(row, sqlite3.Row) else row[0]) + 1


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _studio_definition_from_row(row: sqlite3.Row) -> dict[str, object]:
    from .studio_templates import normalize_template_definition

    return normalize_template_definition(json.loads(str(row["definition_json"])))


def _definitions_match(expected: dict[str, object], current: dict[str, object]) -> bool:
    return _canonical_json(expected) == _canonical_json(current)


def _latest_active_studio_template_id(
    con: sqlite3.Connection,
    key: str = "reaction_top_25",
) -> int | None:
    row = con.execute(
        """
        SELECT id
        FROM studio_templates
        WHERE template_key=?
          AND status='active'
          AND deleted_at IS NULL
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (key,),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _create_studio_template_version_in_connection(
    con: sqlite3.Connection,
    definition: dict[str, object],
    *,
    status: str,
    now: str,
) -> int:
    version = _next_template_version(con, str(definition["key"]))
    cur = con.execute(
        """
        INSERT INTO studio_templates
            (template_key, name, engine, version, status,
             definition_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(definition["key"]),
            str(definition["name"]),
            "remotion",
            version,
            status,
            json.dumps(definition, ensure_ascii=False),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _create_archived_migration_template_in_connection(
    con: sqlite3.Connection,
    legacy_row: sqlite3.Row,
    reason: str,
    *,
    now: str,
) -> int:
    from .studio_templates import archived_legacy_edit_template_definition

    definition = archived_legacy_edit_template_definition(legacy_row, reason)
    return _create_studio_template_version_in_connection(
        con,
        definition,
        status="archived",
        now=now,
    )


def _ensure_default_studio_templates_for_migration(con: sqlite3.Connection) -> None:
    from .studio_templates import default_studio_template_definitions, normalize_template_definition

    now = _now_utc()
    for raw_definition in default_studio_template_definitions():
        definition = normalize_template_definition(raw_definition)
        row = con.execute(
            "SELECT id FROM studio_templates WHERE template_key=? LIMIT 1",
            (definition["key"],),
        ).fetchone()
        if row is not None:
            continue
        con.execute(
            """
            INSERT INTO studio_templates
                (template_key, name, engine, version, status,
                 definition_json, created_at, updated_at)
            VALUES (?, ?, ?, 1, 'active', ?, ?, ?)
            """,
            (
                definition["key"],
                definition["name"],
                "remotion",
                json.dumps(definition, ensure_ascii=False),
                now,
                now,
            ),
        )


def _run_045_studio_template_data_migration(con: sqlite3.Connection) -> None:
    if not all(_table_exists(con, table) for table in ("edit_templates", "studio_templates")):
        return
    from .studio_templates import (
        archived_legacy_edit_template_definition,
        legacy_edit_template_to_definition,
    )

    _ensure_default_studio_templates_for_migration(con)
    report: dict[str, object] = {
        "templates": {
            "migrated": [],
            "already_linked": [],
            "archived": [],
            "failed": [],
        },
        "channel_profiles": {"updated": [], "defaulted": []},
        "edit_jobs": {"linked": [], "legacy_history": []},
    }
    now = _now_utc()
    template_rows = con.execute(
        "SELECT * FROM edit_templates ORDER BY id ASC"
    ).fetchall()
    for row in template_rows:
        legacy_id = int(row["id"])
        existing_link = row["studio_template_id"] if "studio_template_id" in row.keys() else None
        if existing_link:
            report["templates"]["already_linked"].append(legacy_id)  # type: ignore[index]
            continue
        key = str(row["key"])
        studio = con.execute(
            """
            SELECT id
            FROM studio_templates
            WHERE template_key=? AND deleted_at IS NULL
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        try:
            archived_reason: str | None = None
            if studio is None:
                try:
                    definition = legacy_edit_template_to_definition(row)
                    status = "active" if bool(row["enabled"]) else "archived"
                except Exception as exc:
                    archived_reason = str(exc) or exc.__class__.__name__
                    definition = archived_legacy_edit_template_definition(
                        row,
                        archived_reason,
                    )
                    status = "archived"
                version = _next_template_version(con, definition["key"])
                cur = con.execute(
                    """
                    INSERT INTO studio_templates
                        (template_key, name, engine, version, status,
                         definition_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        definition["key"],
                        definition["name"],
                        "remotion",
                        version,
                        status,
                        json.dumps(definition, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                studio_id = int(cur.lastrowid)
            else:
                studio_id = int(studio["id"])
            con.execute(
                "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
                (studio_id, now, legacy_id),
            )
            if archived_reason is not None:
                report["templates"]["archived"].append({  # type: ignore[index]
                    "id": legacy_id,
                    "studio_template_id": studio_id,
                    "reason": archived_reason,
                })
            else:
                report["templates"]["migrated"].append(legacy_id)  # type: ignore[index]
        except Exception as exc:  # migration report must preserve the DB
            report["templates"]["failed"].append({  # type: ignore[index]
                "id": legacy_id,
                "reason": str(exc) or exc.__class__.__name__,
            })

    if _table_exists(con, "channel_profiles"):
        profiles = con.execute(
            "SELECT id, default_template_id, default_studio_template_id FROM channel_profiles ORDER BY id ASC"
        ).fetchall()
        default_row = con.execute(
            """
            SELECT id FROM studio_templates
            WHERE template_key='reaction_top_25' AND deleted_at IS NULL
            ORDER BY version DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        default_studio_id = int(default_row["id"]) if default_row else None
        for profile in profiles:
            if profile["default_studio_template_id"] is not None:
                continue
            studio_id = None
            if profile["default_template_id"] is not None:
                linked = con.execute(
                    """
                    SELECT et.studio_template_id, st.status, st.deleted_at
                    FROM edit_templates et
                    LEFT JOIN studio_templates st ON st.id=et.studio_template_id
                    WHERE et.id=?
                    """,
                    (int(profile["default_template_id"]),),
                ).fetchone()
                if (
                    linked is not None
                    and linked["studio_template_id"] is not None
                    and linked["deleted_at"] is None
                    and str(linked["status"] or "") == "active"
                ):
                    studio_id = int(linked["studio_template_id"])
            if studio_id is None:
                studio_id = default_studio_id
                bucket = "defaulted"
            else:
                bucket = "updated"
            if studio_id is not None:
                con.execute(
                    "UPDATE channel_profiles SET default_studio_template_id=?, updated_at=? WHERE id=?",
                    (studio_id, now, int(profile["id"])),
                )
                report["channel_profiles"][bucket].append(int(profile["id"]))  # type: ignore[index]

    if _table_exists(con, "edit_jobs"):
        rows = con.execute(
            """
            SELECT ej.id, ej.template_id, ej.studio_template_id, et.studio_template_id AS linked_studio_template_id
            FROM edit_jobs ej
            LEFT JOIN edit_templates et ON et.id=ej.template_id
            ORDER BY ej.id ASC
            """
        ).fetchall()
        for row in rows:
            if row["studio_template_id"] is not None:
                continue
            linked_id = row["linked_studio_template_id"]
            if linked_id is not None:
                con.execute(
                    "UPDATE edit_jobs SET studio_template_id=? WHERE id=?",
                    (int(linked_id), int(row["id"])),
                )
                report["edit_jobs"]["linked"].append(int(row["id"]))  # type: ignore[index]
            else:
                report["edit_jobs"]["legacy_history"].append(int(row["id"]))  # type: ignore[index]
    _store_migration_report(con, "045_studio_template_data_migration", report)


def _run_046_studio_only_cutover(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "app_settings"):
        return
    now = _now_utc()
    existing = con.execute(
        "SELECT value FROM app_settings WHERE key='studio_templates_cutover_at'"
    ).fetchone()
    settings = [
        ("studio_templates_runtime_mode", "studio_only", 0),
    ]
    if existing is None:
        settings.append(("studio_templates_cutover_at", now, 0))
    for key, value, is_secret in settings:
        con.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                is_secret=excluded.is_secret,
                updated_at=excluded.updated_at
            """,
            (key, value, is_secret, now, now),
        )


def _set_app_setting_in_connection(
    con: sqlite3.Connection,
    key: str,
    value: str,
    *,
    is_secret: int = 0,
) -> None:
    if not _table_exists(con, "app_settings"):
        return
    now = _now_utc()
    con.execute(
        """
        INSERT INTO app_settings (key, value, is_secret, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            is_secret=excluded.is_secret,
            updated_at=excluded.updated_at
        """,
        (key, value, is_secret, now, now),
    )


def _run_047_studio_only_verification(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "app_settings"):
        return
    _ensure_migration_reports(con)
    from .studio_templates import legacy_edit_template_to_definition

    report: dict[str, object] = {
        "critical_errors": [],
        "warnings": [],
        "repairs": {
            "relinked_unsafe_templates": [],
            "defaulted_channel_profiles": [],
        },
        "checks": {},
    }
    critical: list[dict[str, object]] = report["critical_errors"]  # type: ignore[assignment]
    warnings: list[dict[str, object]] = report["warnings"]  # type: ignore[assignment]
    repairs: dict[str, list[dict[str, object]]] = report["repairs"]  # type: ignore[assignment]
    now = _now_utc()

    report_045 = con.execute(
        """
        SELECT report_json
        FROM migration_reports
        WHERE migration_key='045_studio_template_data_migration'
        """
    ).fetchone()
    if report_045 is None:
        warnings.append({
            "kind": "missing_045_report",
            "reason": "045 migration report не найден.",
        })

    if _table_exists(con, "edit_templates") and _table_exists(con, "studio_templates"):
        rows = con.execute(
            """
            SELECT et.*, st.id AS linked_studio_id, st.status AS linked_status
            FROM edit_templates et
            LEFT JOIN studio_templates st ON st.id=et.studio_template_id
            ORDER BY et.id ASC
            """
        ).fetchall()
        for row in rows:
            if row["studio_template_id"] is not None and row["linked_studio_id"] is None:
                critical.append({
                    "kind": "missing_linked_studio_template",
                    "edit_template_id": int(row["id"]),
                    "studio_template_id": int(row["studio_template_id"]),
                })
                continue
            if row["studio_template_id"] is None:
                warnings.append({
                    "kind": "unlinked_legacy_template",
                    "edit_template_id": int(row["id"]),
                })
                continue
            try:
                legacy_edit_template_to_definition(row)
            except Exception as exc:
                reason = str(exc) or exc.__class__.__name__
                if str(row["linked_status"] or "") == "archived":
                    linked = con.execute(
                        "SELECT * FROM studio_templates WHERE id=?",
                        (int(row["studio_template_id"]),),
                    ).fetchone()
                    if linked is not None:
                        try:
                            current = _studio_definition_from_row(linked)
                            rules = current.get("rules") if isinstance(current.get("rules"), dict) else {}
                            if int(rules.get("legacy_edit_template_id") or 0) == int(row["id"]):
                                continue
                        except Exception:
                            pass
                archived_id = _create_archived_migration_template_in_connection(
                    con,
                    row,
                    reason,
                    now=now,
                )
                con.execute(
                    "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
                    (archived_id, now, int(row["id"])),
                )
                repairs["relinked_unsafe_templates"].append({
                    "edit_template_id": int(row["id"]),
                    "old_studio_template_id": int(row["studio_template_id"]),
                    "archived_studio_template_id": archived_id,
                    "reason": reason,
                })

    if _table_exists(con, "channel_profiles"):
        default_studio_id = (
            _latest_active_studio_template_id(con)
            if _table_exists(con, "studio_templates")
            else None
        )
        profile_rows = con.execute(
            """
            SELECT cp.id, cp.enabled, cp.default_studio_template_id,
                   st.status AS studio_status, st.deleted_at AS studio_deleted_at
            FROM channel_profiles cp
            LEFT JOIN studio_templates st ON st.id=cp.default_studio_template_id
            ORDER BY cp.id ASC
            """
        ).fetchall()
        for profile in profile_rows:
            if not bool(profile["enabled"]):
                continue
            invalid = (
                profile["default_studio_template_id"] is None
                or profile["studio_status"] is None
                or profile["studio_deleted_at"] is not None
                or str(profile["studio_status"]) != "active"
            )
            if not invalid:
                continue
            fallback_valid = None
            if default_studio_id is not None:
                fallback_valid = con.execute(
                    """
                    SELECT id
                    FROM studio_templates
                    WHERE id=? AND status='active' AND deleted_at IS NULL
                    """,
                    (default_studio_id,),
                ).fetchone()
            if fallback_valid is not None:
                con.execute(
                    """
                    UPDATE channel_profiles
                    SET default_studio_template_id=?, updated_at=?
                    WHERE id=?
                    """,
                    (default_studio_id, now, int(profile["id"])),
                )
                repairs["defaulted_channel_profiles"].append({
                    "profile_id": int(profile["id"]),
                    "studio_template_id": default_studio_id,
                })
            else:
                critical.append({
                    "kind": "active_profile_without_valid_studio_template",
                    "profile_id": int(profile["id"]),
                })

    if _table_exists(con, "edit_jobs"):
        legacy_jobs = con.execute(
            """
            SELECT id, status
            FROM edit_jobs
            WHERE status IN ('queued', 'rendering', 'failed')
              AND (studio_project_id IS NULL OR remotion_render_job_id IS NULL)
            ORDER BY id ASC
            """
        ).fetchall()
        for row in legacy_jobs:
            critical.append({
                "kind": "active_legacy_edit_job",
                "edit_job_id": int(row["id"]),
                "status": str(row["status"]),
                "reason": "Legacy edit job будет доступен только для просмотра.",
            })
        report["checks"] = {
            "active_legacy_jobs": len(legacy_jobs),
        }

    mode = "studio_only_with_migration_errors" if critical else "studio_only"
    _set_app_setting_in_connection(
        con,
        "studio_templates_runtime_mode",
        mode,
    )
    _store_migration_report(con, "047_studio_only_verification", report)


def _run_048_studio_migration_repair(con: sqlite3.Connection) -> None:
    if not all(_table_exists(con, table) for table in ("edit_templates", "studio_templates")):
        return
    _ensure_migration_reports(con)
    from .studio_templates import legacy_edit_template_to_definition

    now = _now_utc()
    report: dict[str, object] = {
        "repaired": [],
        "version_created": [],
        "relinked_profiles": [],
        "archived": [],
        "failed": [],
    }
    rows = con.execute(
        """
        SELECT et.*, st.id AS linked_studio_id,
               st.definition_json AS linked_definition_json,
               st.status AS linked_status
        FROM edit_templates et
        LEFT JOIN studio_templates st ON st.id=et.studio_template_id
        ORDER BY et.id ASC
        """
    ).fetchall()
    for row in rows:
        legacy_id = int(row["id"])
        old_studio_id = (
            int(row["studio_template_id"])
            if row["studio_template_id"] is not None and row["linked_studio_id"] is not None
            else None
        )
        try:
            try:
                expected = legacy_edit_template_to_definition(row)
                status = "active" if bool(row["enabled"]) else "archived"
            except Exception as exc:
                reason = str(exc) or exc.__class__.__name__
                if old_studio_id is not None and str(row["linked_status"] or "") == "archived":
                    linked = con.execute(
                        "SELECT * FROM studio_templates WHERE id=?",
                        (old_studio_id,),
                    ).fetchone()
                    if linked is not None:
                        try:
                            current = _studio_definition_from_row(linked)
                            rules = current.get("rules") if isinstance(current.get("rules"), dict) else {}
                            if int(rules.get("legacy_edit_template_id") or 0) == legacy_id:
                                continue
                        except Exception:
                            pass
                archived_id = _create_archived_migration_template_in_connection(
                    con,
                    row,
                    reason,
                    now=now,
                )
                con.execute(
                    "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
                    (archived_id, now, legacy_id),
                )
                report["archived"].append({  # type: ignore[index]
                    "edit_template_id": legacy_id,
                    "old_studio_template_id": old_studio_id,
                    "archived_studio_template_id": archived_id,
                    "reason": reason,
                })
                continue

            if old_studio_id is not None:
                linked = con.execute(
                    "SELECT * FROM studio_templates WHERE id=?",
                    (old_studio_id,),
                ).fetchone()
                if linked is not None:
                    try:
                        current = _studio_definition_from_row(linked)
                    except Exception:
                        current = {}
                    if current and _definitions_match(expected, current):
                        continue

            new_studio_id = _create_studio_template_version_in_connection(
                con,
                expected,
                status=status,
                now=now,
            )
            con.execute(
                "UPDATE edit_templates SET studio_template_id=?, updated_at=? WHERE id=?",
                (new_studio_id, now, legacy_id),
            )
            report["version_created"].append({  # type: ignore[index]
                "edit_template_id": legacy_id,
                "old_studio_template_id": old_studio_id,
                "new_studio_template_id": new_studio_id,
                "status": status,
            })

            if _table_exists(con, "channel_profiles"):
                updated_profile_rows = con.execute(
                    """
                    SELECT id
                    FROM channel_profiles
                    WHERE default_template_id=?
                       OR (
                            default_template_id IS NULL
                            AND default_studio_template_id=?
                            AND ? IS NOT NULL
                       )
                    ORDER BY id ASC
                    """,
                    (legacy_id, old_studio_id, old_studio_id),
                ).fetchall()
                if updated_profile_rows:
                    con.execute(
                        """
                        UPDATE channel_profiles
                        SET default_studio_template_id=?, updated_at=?
                        WHERE default_template_id=?
                           OR (
                                default_template_id IS NULL
                                AND default_studio_template_id=?
                                AND ? IS NOT NULL
                           )
                        """,
                        (new_studio_id, now, legacy_id, old_studio_id, old_studio_id),
                    )
                    for profile in updated_profile_rows:
                        report["relinked_profiles"].append({  # type: ignore[index]
                            "profile_id": int(profile["id"]),
                            "edit_template_id": legacy_id,
                            "studio_template_id": new_studio_id,
                        })

            report["repaired"].append(legacy_id)  # type: ignore[index]
        except Exception as exc:
            report["failed"].append({  # type: ignore[index]
                "edit_template_id": legacy_id,
                "reason": str(exc) or exc.__class__.__name__,
            })

    _store_migration_report(con, "048_studio_migration_repair", report)


def _run_python_migration(version: str, con: sqlite3.Connection) -> None:
    if version.startswith("045_"):
        _run_045_studio_template_data_migration(con)
    elif version.startswith("046_"):
        _run_046_studio_only_cutover(con)
    elif version.startswith("047_"):
        _run_047_studio_only_verification(con)
    elif version.startswith("048_"):
        _run_048_studio_migration_repair(con)


def apply_all(con: sqlite3.Connection) -> list[str]:
    """Apply every pending *.sql migration.  Returns list of newly applied versions."""
    con.row_factory = sqlite3.Row
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

        _run_python_migration(version, con)

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
