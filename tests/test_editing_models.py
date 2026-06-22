"""Tests for the template-driven editing data model."""
from __future__ import annotations

import json
import sqlite3

import pytest


def _recipe() -> dict:
    return {
        "version": 1,
        "canvas": {"width": 1080, "height": 1920},
        "slots": {},
    }


def test_create_reaction_asset_and_unique_file_path():
    from shortsfarm import db

    asset_id = db.create_reaction_asset(
        name="Surprised",
        file_path="/reactions/surprised.mp4",
        duration_sec=2.5,
        tags="surprise,positive",
        mood="surprised",
        language="ru",
    )

    row = db.get_reaction_asset(asset_id)
    assert row is not None
    assert row["name"] == "Surprised"
    assert row["enabled"] == 1

    with pytest.raises(sqlite3.IntegrityError):
        db.create_reaction_asset(
            name="Duplicate",
            file_path="/reactions/surprised.mp4",
        )


def test_reaction_asset_update_disable_and_filter():
    from shortsfarm import db

    asset_id = db.create_reaction_asset(
        name="Original",
        file_path="/reactions/original.mp4",
    )
    assert db.update_reaction_asset(asset_id, name="Updated", mood="happy")
    assert db.disable_reaction_asset(asset_id)

    row = db.get_reaction_asset(asset_id)
    assert row is not None
    assert row["name"] == "Updated"
    assert row["mood"] == "happy"
    assert row["enabled"] == 0
    assert [item["id"] for item in db.list_reaction_assets(enabled=False)] == [asset_id]


def test_create_reaction_pool_add_and_remove_asset():
    from shortsfarm import db

    asset_id = db.create_reaction_asset(
        name="Laugh",
        file_path="/reactions/laugh.mp4",
    )
    pool_id = db.create_reaction_pool(
        name="Positive reactions",
        description="Reusable positive reactions",
    )

    item_id = db.add_reaction_to_pool(pool_id, asset_id, weight=3)
    items = db.list_reaction_pool_items(pool_id)
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["reaction_asset_id"] == asset_id
    assert items[0]["weight"] == 3

    assert db.remove_reaction_from_pool(pool_id, asset_id)
    assert db.list_reaction_pool_items(pool_id) == []


def test_create_edit_template_validates_and_finds_recipe():
    from shortsfarm import db

    template_id = db.create_edit_template(
        key="test_template",
        name="Test template",
        recipe_json=_recipe(),
    )

    row = db.get_edit_template_by_key("test_template")
    assert row is not None
    assert row["id"] == template_id
    assert json.loads(row["recipe_json"])["canvas"]["width"] == 1080

    with pytest.raises(ValueError, match="valid JSON"):
        db.create_edit_template(
            key="broken",
            name="Broken",
            recipe_json="{not-json",
        )


def test_update_and_disable_edit_template():
    from shortsfarm import db

    template_id = db.create_edit_template(
        key="updatable",
        name="Before",
        recipe_json=_recipe(),
    )
    updated_recipe = {"version": 2, "canvas": {"width": 720, "height": 1280}}
    assert db.update_edit_template(
        template_id,
        name="After",
        recipe_json=updated_recipe,
    )
    assert db.disable_edit_template(template_id)

    row = db.get_edit_template(template_id)
    assert row is not None
    assert row["name"] == "After"
    assert json.loads(row["recipe_json"])["version"] == 2
    assert row["enabled"] == 0

    with pytest.raises(ValueError, match="valid JSON"):
        db.update_edit_template(template_id, recipe_json="not-json")


def test_channel_profile_allows_null_youtube_account():
    from shortsfarm import db

    profile_id = db.create_channel_profile(
        name="Future channel",
        youtube_account_id=None,
        default_privacy="private",
        default_category_id="22",
    )

    row = db.get_channel_profile(profile_id)
    assert row is not None
    assert row["youtube_account_id"] is None
    assert row["enabled"] == 1


def test_channel_profile_update_clear_and_disable():
    from shortsfarm import db

    template_id = db.create_edit_template(
        key="profile_template",
        name="Profile template",
        recipe_json=_recipe(),
    )
    profile_id = db.create_channel_profile(
        name="Profile",
        default_template_id=template_id,
    )

    assert db.update_channel_profile(
        profile_id,
        name="Updated profile",
        default_template_id=None,
    )
    assert db.disable_channel_profile(profile_id)

    row = db.get_channel_profile(profile_id)
    assert row is not None
    assert row["name"] == "Updated profile"
    assert row["default_template_id"] is None
    assert row["enabled"] == 0


def test_edit_job_lifecycle():
    from shortsfarm import db

    job_id = db.create_edit_job(
        workspace_item_key="segment:42",
        input_path="/input/segment.mp4",
        output_path="/output/edit.mp4",
        recipe_json=_recipe(),
    )
    row = db.get_edit_job(job_id)
    assert row is not None
    assert row["status"] == "queued"
    assert json.loads(row["recipe_json"])["version"] == 1

    assert db.mark_edit_job_rendering(job_id)
    row = db.get_edit_job(job_id)
    assert row["status"] == "rendering"
    assert row["started_at"] is not None

    assert db.mark_edit_job_done(job_id, "/output/edited.mp4")
    row = db.get_edit_job(job_id)
    assert row["status"] == "done"
    assert row["edited_path"] == "/output/edited.mp4"
    assert row["finished_at"] is not None


def test_edit_job_failed_and_cancelled_states():
    from shortsfarm import db

    failed_id = db.create_edit_job(workspace_item_key="clip:1")
    assert db.mark_edit_job_failed(failed_id, "render failed")
    failed = db.get_edit_job(failed_id)
    assert failed["status"] == "failed"
    assert failed["error"] == "render failed"

    cancelled_id = db.create_edit_job(workspace_item_key="clip:2")
    assert db.cancel_edit_job(cancelled_id)
    cancelled = db.get_edit_job(cancelled_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None

    assert [row["id"] for row in db.list_edit_jobs(status="failed")] == [failed_id]


def test_edit_job_rejects_invalid_recipe_json():
    from shortsfarm import db

    with pytest.raises(ValueError, match="valid JSON"):
        db.create_edit_job(
            workspace_item_key="segment:99",
            recipe_json="{broken",
        )


def test_ensure_default_edit_templates_is_idempotent():
    from shortsfarm import db

    first = db.ensure_default_edit_templates()
    second = db.ensure_default_edit_templates()

    assert first["id"] == second["id"]
    assert first["key"] == "reaction_top_25"
    assert first["name"] == "Reaction Top 25%"
    assert first["renderer"] == "ffmpeg"
    recipe = json.loads(first["recipe_json"])
    assert recipe["canvas"] == {"width": 1080, "height": 1920}
    assert recipe["slots"]["reaction"] == {"x": 0, "y": 0, "w": 1080, "h": 480}
    assert recipe["slots"]["main"] == {"x": 0, "y": 480, "w": 1080, "h": 1440}
    assert recipe["audio"]["mode"] == "main_only"
    assert len(db.list_edit_templates()) == 1
