"""Tests for the migration runner."""
from __future__ import annotations

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


def test_idempotent(tmp_data_dir):
    """Running migrations many times must not raise."""
    from shortsfarm.migrations import run_migrations
    for _ in range(3):
        run_migrations()
