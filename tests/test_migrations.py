"""Tests for the migration runner."""
from __future__ import annotations

import json
import sqlite3

import pytest


def test_migrations_applied(tmp_data_dir):
    from shortsfarm import db
    from shortsfarm.migrations import run_migrations
    applied = run_migrations()
    # second run should apply nothing new
    assert run_migrations() == []


def test_schema_versions_recorded(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        versions = {
            row[0]
            for row in con.execute("SELECT version FROM schema_migrations").fetchall()
        }
    assert "001_initial_existing_schema" in versions
    assert "002_add_review_status"       in versions
    assert "003_create_review_sessions"  in versions
    assert "004_create_marks"            in versions
    assert "005_create_clips"            in versions
    assert "018_create_clip_workspace_metadata" in versions
    assert "019_add_workspace_hidden_at" in versions
    assert "021_add_workspace_preparation" in versions
    assert "022_add_publish_schedules" in versions
    assert "023_create_editing_models" in versions
    assert "024_add_edit_job_review_status" in versions
    assert "025_create_workspace_folders" in versions
    assert "026_create_remotion_studio" in versions
    assert "027_create_studio_templates" in versions
    assert "032_create_local_storage_profiles" in versions
    assert "033_local_storage_profile_service_link_unique" in versions
    assert "034_link_storage_profiles_to_publish_jobs" in versions
    assert "035_add_storage_profile_auto_import" in versions
    assert "036_create_storage_profile_youtube_sync" in versions
    assert "037_create_tag_catalog" in versions
    assert "038_create_shorts_pipeline" in versions
    assert "039_remotion_auto_retry" in versions
    assert "041_unify_studio_editing_templates" in versions
    assert "045_studio_template_data_migration" in versions
    assert "046_studio_only_runtime_cutover" in versions
    assert "047_studio_only_verification" in versions
    assert "048_studio_migration_repair" in versions
    assert "049_studio_only_final_repair" in versions


def test_studio_only_cutover_records_settings_and_report(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        settings = {
            row["key"]: row["value"]
            for row in con.execute(
                """
                SELECT key, value
                FROM app_settings
                WHERE key IN (
                    'studio_templates_runtime_mode',
                    'studio_templates_cutover_at'
                )
                """
            ).fetchall()
        }
        report_row = con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='045_studio_template_data_migration'
            """
        ).fetchone()

    assert settings["studio_templates_runtime_mode"] == "studio_only"
    assert settings["studio_templates_cutover_at"]
    assert report_row is not None
    report = json.loads(str(report_row["report_json"]))
    assert {"templates", "channel_profiles", "edit_jobs"} <= set(report)


def test_studio_only_verification_relinks_unsafe_legacy_without_archiving_shared_template(tmp_data_dir):
    from shortsfarm import db
    from shortsfarm.migrations import _run_047_studio_only_verification
    from shortsfarm.studio_templates import ensure_default_studio_templates

    safe_template_id = int(ensure_default_studio_templates()[0]["id"])
    existing = db.get_edit_template_by_key("reaction_top_25")
    if existing is None:
        legacy_template_id = db.create_edit_template(
            key="reaction_top_25",
            name="Broken legacy default",
            recipe_json="[]",
            studio_template_id=safe_template_id,
        )
    else:
        legacy_template_id = int(existing["id"])
        with db.connect() as con:
            con.execute(
                """
                UPDATE edit_templates
                SET recipe_json='[]', studio_template_id=?
                WHERE id=?
                """,
                (safe_template_id, legacy_template_id),
            )
    profile_id = db.create_channel_profile(
        name="Unsafe profile",
        default_studio_template_id=safe_template_id,
    )
    db.create_edit_job(
        workspace_item_key="segment:404",
        recipe_json={"version": 1},
    )
    with db.connect() as con:
        con.execute(
            "UPDATE edit_templates SET studio_template_id=? WHERE id=?",
            (safe_template_id, legacy_template_id),
        )
        _run_047_studio_only_verification(con)
        canonical = con.execute(
            "SELECT status, deleted_at FROM studio_templates WHERE id=?",
            (safe_template_id,),
        ).fetchone()
        legacy = con.execute(
            "SELECT studio_template_id FROM edit_templates WHERE id=?",
            (legacy_template_id,),
        ).fetchone()
        archived = con.execute(
            "SELECT status, deleted_at FROM studio_templates WHERE id=?",
            (int(legacy["studio_template_id"]),),
        ).fetchone()
        profile = con.execute(
            "SELECT default_studio_template_id FROM channel_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        mode = con.execute(
            "SELECT value FROM app_settings WHERE key='studio_templates_runtime_mode'"
        ).fetchone()
        report = con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='047_studio_only_verification'
            """
        ).fetchone()

    assert canonical["status"] == "active"
    assert canonical["deleted_at"] is None
    assert int(legacy["studio_template_id"]) != safe_template_id
    assert archived["status"] == "archived"
    assert profile["default_studio_template_id"] == safe_template_id
    assert mode["value"] == "studio_only_with_migration_errors"
    payload = json.loads(str(report["report_json"]))
    assert payload["repairs"]["relinked_unsafe_templates"]
    assert payload["critical_errors"]


def test_studio_migration_repair_creates_correct_version_for_previously_wrong_link(tmp_data_dir):
    from shortsfarm import db
    from shortsfarm.migrations import _run_048_studio_migration_repair

    legacy_template_id = db.create_edit_template(
        key="custom_top",
        name="Custom Top",
        recipe_json={
            "version": 1,
            "slots": {
                "reaction": {"x": 0, "y": 0, "w": 1080, "h": 600, "fit": "contain"},
                "main": {"x": 0, "y": 600, "w": 1080, "h": 1320, "fit": "cover"},
            },
            "layout": {"background_color": "#123456"},
            "audio": {"main_volume": 0.8, "reaction_volume": 0.2, "mute_reaction": False},
        },
    )
    wrong_studio_id = db.create_studio_template(
        template_key="custom_top",
        name="Wrong Custom Top",
        engine="remotion",
        version=1,
        status="active",
        definition_json={
            "schema_version": 2,
            "key": "custom_top",
            "name": "Wrong Custom Top",
            "adapter": "main_only",
            "supported_renderers": ["ffmpeg_fast"],
            "default_renderer": "ffmpeg_fast",
            "canvas": {"width": 1080, "height": 1920, "fps": 30},
            "slots": {"main": {"type": "video", "required": True}},
            "parameters": {},
            "rules": {},
        },
    )
    profile_id = db.create_channel_profile(
        name="Custom profile",
        default_studio_template_id=wrong_studio_id,
    )
    with db.connect() as con:
        con.execute(
            "UPDATE edit_templates SET studio_template_id=? WHERE id=?",
            (wrong_studio_id, legacy_template_id),
        )
        con.execute(
            "UPDATE channel_profiles SET default_template_id=? WHERE id=?",
            (legacy_template_id, profile_id),
        )
        _run_048_studio_migration_repair(con)
        legacy = con.execute(
            "SELECT studio_template_id FROM edit_templates WHERE id=?",
            (legacy_template_id,),
        ).fetchone()
        repaired = con.execute(
            "SELECT version, definition_json FROM studio_templates WHERE id=?",
            (int(legacy["studio_template_id"]),),
        ).fetchone()
        profile = con.execute(
            "SELECT default_studio_template_id FROM channel_profiles WHERE id=?",
            (profile_id,),
        ).fetchone()
        report = con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='048_studio_migration_repair'
            """
        ).fetchone()

    definition = json.loads(str(repaired["definition_json"]))
    assert int(legacy["studio_template_id"]) != wrong_studio_id
    assert repaired["version"] == 2
    assert definition["adapter"] == "reaction_layout"
    assert definition["parameters"]["reaction_height"]["default"] == 600
    assert definition["parameters"]["background_color"]["default"] == "#123456"
    assert profile["default_studio_template_id"] == legacy["studio_template_id"]
    payload = json.loads(str(report["report_json"]))
    assert payload["version_created"]
    assert payload["relinked_profiles"]


def test_studio_only_final_repair_is_safe_and_idempotent(tmp_data_dir):
    from shortsfarm import db
    from shortsfarm.migrations import _run_049_studio_only_final_repair
    from shortsfarm.studio_templates import ensure_default_studio_templates

    ensure_default_studio_templates()
    fallback = db.get_latest_studio_template_by_key(
        "reaction_top_25",
        include_deleted=False,
    )
    assert fallback is not None
    fallback_id = int(fallback["id"])
    shared_id = db.create_studio_template(
        template_key="shared_wrong",
        name="Shared Wrong",
        engine="remotion",
        version=1,
        status="active",
        definition_json={
            "schema_version": 2,
            "key": "shared_wrong",
            "name": "Shared Wrong",
            "adapter": "main_only",
            "supported_renderers": ["ffmpeg_fast"],
            "default_renderer": "ffmpeg_fast",
            "canvas": {"width": 1080, "height": 1920, "fps": 30},
            "slots": {"main": {"type": "video", "required": True}},
            "parameters": {},
            "rules": {},
        },
    )
    supported_legacy_id = db.create_edit_template(
        key="shared_wrong",
        name="Shared Wrong",
        recipe_json={
            "version": 1,
            "slots": {
                "reaction": {"x": 0, "y": 0, "w": 1080, "h": 480},
                "main": {"x": 0, "y": 480, "w": 1080, "h": 1440},
            },
        },
        studio_template_id=shared_id,
    )
    unsupported_legacy_id = db.create_edit_template(
        key="unsupported_legacy",
        name="Unsupported Legacy",
        recipe_json="[]",
        studio_template_id=shared_id,
    )
    explicit_profile_id = db.create_channel_profile(
        name="Explicit legacy profile",
        default_studio_template_id=shared_id,
    )
    unsupported_profile_id = db.create_channel_profile(
        name="Unsupported legacy profile",
        default_studio_template_id=shared_id,
    )
    ambiguous_profile_id = db.create_channel_profile(
        name="Studio-only ambiguous profile",
        default_studio_template_id=shared_id,
    )
    with db.connect() as con:
        con.execute(
            "UPDATE channel_profiles SET default_template_id=? WHERE id=?",
            (supported_legacy_id, explicit_profile_id),
        )
        con.execute(
            "UPDATE channel_profiles SET default_template_id=? WHERE id=?",
            (unsupported_legacy_id, unsupported_profile_id),
        )
        _run_049_studio_only_final_repair(con)
        first_report_row = con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='049_studio_only_final_repair'
            """
        ).fetchone()
        first_counts = {
            row["template_key"]: row["count"]
            for row in con.execute(
                """
                SELECT template_key, COUNT(*) AS count
                FROM studio_templates
                GROUP BY template_key
                """
            ).fetchall()
        }
        _run_049_studio_only_final_repair(con)
        second_counts = {
            row["template_key"]: row["count"]
            for row in con.execute(
                """
                SELECT template_key, COUNT(*) AS count
                FROM studio_templates
                GROUP BY template_key
                """
            ).fetchall()
        }
        shared = con.execute(
            "SELECT status, deleted_at FROM studio_templates WHERE id=?",
            (shared_id,),
        ).fetchone()
        supported_legacy = con.execute(
            "SELECT studio_template_id FROM edit_templates WHERE id=?",
            (supported_legacy_id,),
        ).fetchone()
        unsupported_legacy = con.execute(
            "SELECT studio_template_id FROM edit_templates WHERE id=?",
            (unsupported_legacy_id,),
        ).fetchone()
        supported_profile = con.execute(
            "SELECT default_studio_template_id FROM channel_profiles WHERE id=?",
            (explicit_profile_id,),
        ).fetchone()
        unsupported_profile = con.execute(
            "SELECT default_studio_template_id FROM channel_profiles WHERE id=?",
            (unsupported_profile_id,),
        ).fetchone()
        ambiguous_profile = con.execute(
            "SELECT default_studio_template_id FROM channel_profiles WHERE id=?",
            (ambiguous_profile_id,),
        ).fetchone()
        archived = con.execute(
            "SELECT status FROM studio_templates WHERE id=?",
            (int(unsupported_legacy["studio_template_id"]),),
        ).fetchone()
        report_row = first_report_row or con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='049_studio_only_final_repair'
            """
        ).fetchone()
        verification_row = con.execute(
            """
            SELECT report_json
            FROM migration_reports
            WHERE migration_key='049_studio_only_post_repair_verification'
            """
        ).fetchone()
        mode = con.execute(
            "SELECT value FROM app_settings WHERE key='studio_templates_runtime_mode'"
        ).fetchone()

    assert first_counts == second_counts
    assert shared["status"] == "active"
    assert shared["deleted_at"] is None
    assert int(supported_legacy["studio_template_id"]) != shared_id
    assert int(unsupported_legacy["studio_template_id"]) != shared_id
    assert archived["status"] == "archived"
    assert supported_profile["default_studio_template_id"] == supported_legacy["studio_template_id"]
    assert unsupported_profile["default_studio_template_id"] == fallback_id
    assert ambiguous_profile["default_studio_template_id"] == shared_id
    report = json.loads(str(report_row["report_json"]))
    assert report["versions_created"]
    assert report["archived_created"]
    assert report["profiles_relinked"]
    assert report["profiles_defaulted"]
    assert report["ambiguous_profiles_skipped"]
    verification = json.loads(str(verification_row["report_json"]))
    assert verification["critical_errors"] == []
    assert mode["value"] == "studio_only"


def test_studio_editing_bridge_schema_exists(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        edit_template_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(edit_templates)").fetchall()
        }
        channel_profile_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(channel_profiles)").fetchall()
        }
        edit_job_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(edit_jobs)").fetchall()
        }
        studio_template_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(studio_templates)").fetchall()
        }
        edit_job_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(edit_jobs)").fetchall()
        }
    assert "studio_template_id" in edit_template_columns
    assert "default_studio_template_id" in channel_profile_columns
    assert {
        "studio_template_id",
        "studio_project_id",
        "remotion_render_job_id",
    } <= edit_job_columns
    assert "deleted_at" in studio_template_columns
    assert "idx_edit_jobs_studio_duplicate" in edit_job_indexes


def test_review_status_column_exists(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("INSERT INTO videos (source_path, title, created_at) "
                    "VALUES ('/x.mp4', 'x', '2026-01-01T00:00:00')")
        row = con.execute("SELECT review_status FROM videos "
                         "WHERE source_path='/x.mp4'").fetchone()
    assert row["review_status"] == "inbox"


def test_review_sessions_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM review_sessions LIMIT 0")


def test_marks_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM marks LIMIT 0")


def test_clips_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM clips LIMIT 0")


def test_app_settings_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM app_settings LIMIT 0")


def test_youtube_oauth_profiles_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM youtube_oauth_profiles LIMIT 0")


def test_social_accounts_oauth_profile_columns_exist(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(social_accounts)").fetchall()
        }
    assert "oauth_profile_id" in columns
    assert "account_email" in columns
    assert "last_connected_at" in columns
    assert "channel_avatar_url" in columns
    assert "subscriber_count" in columns
    assert "uploads_playlist_id" in columns
    assert "metadata_synced_at" in columns
    assert "metadata_sync_error" in columns
    assert "channel_banner_url" in columns
    assert "channel_branding_json" in columns


def test_local_storage_profile_youtube_branding_columns_exist(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profiles)").fetchall()
        }
    assert "avatar_url" in columns
    assert "youtube_branding_sync_enabled" in columns
    assert "name_override" in columns
    assert "handle_override" in columns
    assert "description_override" in columns
    assert "avatar_override" in columns
    assert "banner_override" in columns
    assert "youtube_branding_synced_at" in columns
    assert "youtube_branding_attempted_at" in columns
    assert "youtube_branding_sync_error" in columns
    assert "banner_url" in columns


def test_local_storage_profile_youtube_branding_repair_partial_043(tmp_path):
    from shortsfarm.migrations import MIGRATIONS_DIR, apply_all

    con = sqlite3.connect(tmp_path / "partial-043.sqlite")
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        for sql_file in MIGRATIONS_DIR.glob("*.sql"):
            con.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, '2026-07-11T00:00:00+00:00')",
                (sql_file.stem,),
            )
        con.execute(
            """
            CREATE TABLE local_storage_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                handle TEXT NOT NULL UNIQUE,
                description TEXT,
                avatar_initials TEXT,
                avatar_color TEXT NOT NULL DEFAULT '#3b82f6',
                banner_color TEXT NOT NULL DEFAULT '#111827',
                avatar_url TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE social_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                display_name TEXT,
                channel_id TEXT,
                channel_title TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expires_at TEXT,
                scopes TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.commit()

        assert apply_all(con) == []

        profile_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profiles)")
        }
        account_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(social_accounts)")
        }

        assert {
            "youtube_branding_sync_enabled",
            "name_override",
            "handle_override",
            "description_override",
            "avatar_override",
            "banner_override",
            "youtube_branding_synced_at",
            "youtube_branding_attempted_at",
            "youtube_branding_sync_error",
            "banner_url",
        } <= profile_columns
        assert {"channel_banner_url", "channel_branding_json"} <= account_columns
    finally:
        con.close()


def test_oauth_states_oauth_profile_column_exists(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(oauth_states)").fetchall()
        }
    assert "oauth_profile_id" in columns


def test_publish_jobs_retry_columns_exist(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(publish_jobs)").fetchall()
        }
    assert "attempt_count" in columns
    assert "last_attempt_at" in columns
    assert "next_attempt_at" in columns


def test_publish_schedule_schema_exists(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM publish_schedule_groups LIMIT 0")
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(publish_jobs)").fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(publish_jobs)").fetchall()
        }
    assert {"schedule_group_id", "schedule_position", "upload_at", "overdue_approved_at"} <= columns
    assert "idx_publish_jobs_upload_at" in indexes
    assert "idx_publish_jobs_schedule_group" in indexes


def test_clip_workspace_metadata_table(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        con.execute("SELECT * FROM clip_workspace_metadata LIMIT 0")
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(clip_workspace_metadata)").fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(clip_workspace_metadata)").fetchall()
        }
    assert "hidden_at" in columns
    assert "missing_confirmed_at" in columns
    assert "target_aspect" in columns
    assert "prepared_path" in columns
    assert "prepared_at" in columns
    assert "prepare_status" in columns
    assert "prepare_error" in columns
    assert "idx_clip_workspace_metadata_item" in indexes
    assert "idx_clip_workspace_metadata_status" in indexes
    assert "idx_clip_workspace_metadata_hidden" in indexes


def test_clips_source_segment_id_column_exists(tmp_data_dir):
    from shortsfarm import db
    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(clips)").fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(clips)").fetchall()
        }
    assert "source_segment_id" in columns
    assert "source_clip_id" in columns
    assert "source_aspect" in columns
    assert "idx_clips_source_segment_id" in indexes
    assert "idx_clips_source_clip_aspect" in indexes


def test_editing_model_tables_exist(tmp_data_dir):
    from shortsfarm import db
    table_names = {
        "reaction_assets",
        "reaction_pools",
        "reaction_pool_items",
        "edit_templates",
        "channel_profiles",
        "edit_jobs",
    }
    with db.connect() as con:
        rows = con.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            """
        ).fetchall()
        existing = {str(row["name"]) for row in rows}
    assert table_names <= existing


def test_edit_job_review_columns_exist(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(edit_jobs)").fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(edit_jobs)").fetchall()
        }

    assert {"review_status", "reviewed_at", "review_note"} <= columns
    assert "idx_edit_jobs_review_status" in indexes


def test_workspace_folders_metadata_table_exists(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(workspace_folders)").fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(workspace_folders)").fetchall()
        }

    assert {
        "workspace_root", "relative_path", "display_name",
        "kind", "description", "created_at", "updated_at",
    } <= columns
    assert "idx_workspace_folders_root" in indexes
    assert "idx_workspace_folders_kind" in indexes


def test_remotion_studio_schema_exists(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        con.execute("SELECT * FROM studio_projects LIMIT 0")
        con.execute("SELECT * FROM remotion_render_jobs LIMIT 0")
        indexes = {
            row["name"]
            for row in con.execute(
                "PRAGMA index_list(remotion_render_jobs)"
            ).fetchall()
        }

    assert "idx_remotion_render_jobs_one_active_global" in indexes
    assert "idx_remotion_render_jobs_one_active_project" in indexes


def test_studio_template_definition_schema_exists(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(studio_templates)"
            ).fetchall()
        }
        project_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(studio_projects)"
            ).fetchall()
        }

    assert {
        "template_key", "name", "engine", "version", "status",
        "definition_json", "created_at", "updated_at",
    } <= columns
    assert {"studio_template_id", "reaction_pool_id"} <= project_columns


def test_remotion_batch_and_pipeline_schema_exists(tmp_data_dir):
    from shortsfarm import db

    with db.connect() as con:
        con.execute("SELECT * FROM remotion_render_batches LIMIT 0")
        con.execute("SELECT * FROM remotion_render_batch_items LIMIT 0")
        con.execute("SELECT * FROM remotion_pipelines LIMIT 0")
        batch_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(remotion_render_batches)"
            ).fetchall()
        }
        item_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(remotion_render_batch_items)"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in con.execute(
                "PRAGMA index_list(remotion_render_jobs)"
            ).fetchall()
        }

    assert {
        "studio_template_id", "template_key", "source_mode",
        "reaction_strategy", "total_items", "done_items", "failed_items",
    } <= batch_columns
    assert {
        "batch_id", "studio_project_id", "render_job_id",
        "main_workspace_path", "status", "error",
    } <= item_columns
    assert "idx_remotion_render_jobs_one_active_global" in indexes


def test_studio_render_profile_diagnostics_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        job_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(remotion_render_jobs)")
        }
        batch_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(remotion_render_batches)")
        }
        pipeline_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(remotion_pipelines)")
        }

    expected = {
        "renderer_engine", "render_profile", "duration_limit_sec",
        "start_offset_sec", "full_length", "worker_pid",
        "worker_started_at", "last_heartbeat_at", "stdout_tail",
        "stderr_tail", "returncode", "elapsed_sec",
    }
    assert expected <= job_columns
    assert {
        "renderer_engine", "render_profile", "duration_limit_sec",
        "start_offset_sec", "full_length",
    } <= batch_columns
    assert {
        "renderer_engine", "render_profile", "duration_limit_sec",
        "start_offset_sec", "full_length",
    } <= pipeline_columns


def test_studio_render_progress_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        job_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(remotion_render_jobs)")
        }

    assert {
        "progress_percent", "progress_stage", "progress_message",
        "current_frame", "total_frames", "out_time_sec", "speed",
        "eta_sec", "output_size_bytes", "completed_message",
    } <= job_columns


def test_remotion_auto_retry_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        job_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(remotion_render_jobs)")
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(remotion_render_jobs)")
        }

    assert {"auto_retry_count", "max_auto_retries"} <= job_columns
    assert "idx_remotion_render_jobs_auto_retry" in indexes


def test_video_segments_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        con.execute("SELECT * FROM video_segments LIMIT 0")
        columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(video_segments)")
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(video_segments)")
        }

    assert {
        "source_path", "label", "start_sec", "end_sec", "duration_sec",
        "status", "notes", "created_at", "updated_at",
    } <= columns
    assert "idx_video_segments_source_path" in indexes
    assert "idx_video_segments_status" in indexes


def test_local_storage_profiles_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        con.execute("SELECT * FROM local_storage_profiles LIMIT 0")
        con.execute("SELECT * FROM local_storage_profile_items LIMIT 0")
        con.execute("SELECT * FROM local_storage_profile_service_links LIMIT 0")
        con.execute("SELECT * FROM local_storage_profile_publish_jobs LIMIT 0")
        con.execute("SELECT * FROM local_storage_profile_external_videos LIMIT 0")
        con.execute("SELECT * FROM tags LIMIT 0")
        con.execute("SELECT * FROM workspace_tag_links LIMIT 0")
        con.execute("SELECT * FROM local_storage_profile_tag_rules LIMIT 0")
        profile_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profiles)")
        }
        profile_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(local_storage_profiles)")
        }
        item_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profile_items)")
        }
        link_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profile_service_links)")
        }
        item_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(local_storage_profile_items)")
        }
        link_indexes = {
            row["name"]
            for row in con.execute(
                "PRAGMA index_list(local_storage_profile_service_links)"
            )
        }
        publish_link_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(local_storage_profile_publish_jobs)"
            )
        }
        publish_link_indexes = {
            row["name"]
            for row in con.execute(
                "PRAGMA index_list(local_storage_profile_publish_jobs)"
            )
        }
        external_columns = {
            row["name"]
            for row in con.execute(
                "PRAGMA table_info(local_storage_profile_external_videos)"
            )
        }
        external_indexes = {
            row["name"]
            for row in con.execute(
                "PRAGMA index_list(local_storage_profile_external_videos)"
            )
        }
        tag_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(tags)")
        }
        tag_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(tags)")
        }
        workspace_tag_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(workspace_tag_links)")
        }
        workspace_tag_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(workspace_tag_links)")
        }
        profile_tag_rule_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(local_storage_profile_tag_rules)")
        }
        profile_tag_rule_indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(local_storage_profile_tag_rules)")
        }
        status_tags = {
            row["slug"]
            for row in con.execute("SELECT slug FROM tags WHERE kind='status'")
        }

    assert {
        "name", "handle", "description", "avatar_initials",
        "avatar_color", "banner_color", "enabled",
        "auto_import_enabled", "auto_import_sections",
        "auto_import_prefix", "auto_import_last_scan_at",
        "tag_match_mode",
    } <= profile_columns
    assert {"profile_id", "workspace_path", "title", "status", "added_at"} <= item_columns
    assert {
        "profile_id", "platform", "external_account_id", "status",
        "last_sync_at", "last_sync_error", "synced_video_count",
    } <= link_columns
    assert {"profile_id", "profile_item_id", "publish_job_id", "platform"} <= publish_link_columns
    assert {
        "profile_id", "platform", "external_video_id", "external_url",
        "title", "privacy_status", "profile_item_id", "publish_job_id",
    } <= external_columns
    assert "idx_local_storage_profile_items_path" in item_indexes
    assert "idx_local_storage_profiles_auto_import" in profile_indexes
    assert "idx_local_storage_profile_service_links_unique_profile_platform" in link_indexes
    assert "idx_local_storage_profile_publish_jobs_profile" in publish_link_indexes
    assert "idx_local_storage_profile_external_videos_profile" in external_indexes
    assert {"name", "slug", "kind", "color", "system_key", "locked", "enabled"} <= tag_columns
    assert {"workspace_path", "item_type", "item_id", "tag_id", "source"} <= workspace_tag_columns
    assert {"profile_id", "tag_id", "mode", "locked", "source"} <= profile_tag_rule_columns
    assert "idx_tags_system_key" in tag_indexes
    assert "idx_workspace_tag_links_path_tag" in workspace_tag_indexes
    assert "idx_workspace_tag_links_item_tag" in workspace_tag_indexes
    assert "idx_local_storage_profile_tag_rules_profile" in profile_tag_rule_indexes
    assert {"status-draft", "status-ready", "status-queued", "status-uploaded", "status-failed"} <= status_tags


def test_shorts_pipeline_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        con.execute("SELECT * FROM shorts_pipeline_runs LIMIT 0")
        con.execute("SELECT * FROM shorts_pipeline_run_items LIMIT 0")
        run_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(shorts_pipeline_runs)")
        }
        item_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(shorts_pipeline_run_items)")
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(shorts_pipeline_runs)")
        }

    assert {
        "status", "source_mode", "split_seconds", "studio_template_id",
        "tag_ids_json", "channel_tag_id", "remotion_batch_id",
    } <= run_columns
    assert {
        "run_id", "source_workspace_path", "segment_workspace_path",
        "render_job_id", "output_workspace_path", "status",
    } <= item_columns
    assert "idx_shorts_pipeline_runs_one_active" in indexes


def test_video_soft_delete_schema_exists(tmp_data_dir):
    from shortsfarm import db

    db.init_db()
    with db.connect() as con:
        video_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(videos)")
        }
        indexes = {
            row["name"]
            for row in con.execute("PRAGMA index_list(videos)")
        }

    assert {"deleted_at", "source_file_deleted_at"} <= video_columns
    assert "idx_videos_deleted_at" in indexes


def test_idempotent(tmp_data_dir):
    """Running migrations many times must not raise."""
    from shortsfarm.migrations import run_migrations
    for _ in range(3):
        run_migrations()
