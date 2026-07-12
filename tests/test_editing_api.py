"""Backend API tests for template-driven editing settings."""
from __future__ import annotations

from fastapi import HTTPException

import pytest


def _template_recipe() -> dict:
    return {
        "version": 1,
        "canvas": {"width": 1080, "height": 1920},
        "slots": {},
    }


def _youtube_account() -> int:
    from shortsfarm import db

    oauth_profile_id = db.create_youtube_oauth_profile(
        name="Editing API OAuth",
        mode="custom",
        client_id="client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    return db.save_social_account(
        platform="youtube",
        display_name="Editing Channel",
        channel_id="editing-channel",
        channel_title="Editing Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=oauth_profile_id,
        status="active",
    )


def test_reactions_api_create_list_and_duplicate(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import ReactionAssetCreateRequest

    path = tmp_path / "laugh.mp4"
    path.write_bytes(b"reaction")
    created = api.editing_reaction_create(
        ReactionAssetCreateRequest(
            name="Laugh",
            file_path=str(path),
            tags="laugh,funny",
            mood="funny",
            language="ru",
        )
    )

    assert created["item"]["name"] == "Laugh"
    assert created["item"]["file_exists"] is True
    listed = api.editing_reactions(q="FUNNY")
    assert [item["id"] for item in listed["items"]] == [created["item"]["id"]]

    with pytest.raises(HTTPException) as exc:
        api.editing_reaction_create(
            ReactionAssetCreateRequest(name="Duplicate", file_path=str(path))
        )
    assert exc.value.status_code == 400
    assert "file_path" in exc.value.detail["message"]


def test_reactions_import_folder_creates_assets_and_skips_duplicates(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import ReactionFolderImportRequest

    folder = tmp_path / "reactions"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    (folder / "one.mp4").write_bytes(b"one")
    (nested / "two.webm").write_bytes(b"two")
    (nested / "ignore.txt").write_text("ignore", encoding="utf-8")
    request = ReactionFolderImportRequest(
        folder_path=str(folder),
        recursive=True,
        tags="reaction",
        mood="funny",
        language="ru",
    )

    first = api.editing_reactions_import_folder(request)
    second = api.editing_reactions_import_folder(request)

    assert first["created"] == 2
    assert first["skipped"] == 0
    assert first["errors"] == 0
    assert second["created"] == 0
    assert second["skipped"] == 2
    assert len(api.editing_reactions()["items"]) == 2


def test_reaction_pool_api_add_list_upsert_and_remove(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        ReactionAssetCreateRequest,
        ReactionPoolCreateRequest,
        ReactionPoolItemRequest,
    )

    path = tmp_path / "wow.mkv"
    path.write_bytes(b"wow")
    asset_id = api.editing_reaction_create(
        ReactionAssetCreateRequest(name="Wow", file_path=str(path), mood="surprised")
    )["item"]["id"]
    pool = api.editing_reaction_pool_create(
        ReactionPoolCreateRequest(name="Funny RU", description="RU reactions")
    )["item"]

    api.editing_reaction_pool_item_add(
        pool["id"],
        ReactionPoolItemRequest(reaction_asset_id=asset_id, weight=1),
    )
    api.editing_reaction_pool_item_add(
        pool["id"],
        ReactionPoolItemRequest(reaction_asset_id=asset_id, weight=4),
    )
    items = api.editing_reaction_pool_items(pool["id"])["items"]

    assert len(items) == 1
    assert items[0]["weight"] == 4
    assert items[0]["asset_name"] == "Wow"
    assert items[0]["file_path"] == str(path)
    assert items[0]["mood"] == "surprised"
    assert items[0]["file_exists"] is True
    assert api.editing_reaction_pools()["items"][0]["item_count"] == 1

    assert api.editing_reaction_pool_item_delete(pool["id"], asset_id)["status"] == "ok"
    assert api.editing_reaction_pool_items(pool["id"])["items"] == []


def test_templates_api_ensure_defaults_and_reject_invalid_json():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditTemplateUpdateRequest

    item = api.editing_templates_ensure_defaults()["item"]
    assert item["key"] == "reaction_top_25"
    items = api.editing_templates()["items"]
    assert len([row for row in items if row["source"] == "studio"]) >= 6
    assert len([row for row in items if row["source"] == "legacy"]) == 0
    assert any(
        row["key"] == "reaction_top_25" and row["source"] == "studio"
        for row in items
    )
    assert any(row["key"] == "main_only" for row in items)

    with pytest.raises(HTTPException) as exc:
        api.editing_template_update(
            item["id"],
            EditTemplateUpdateRequest(recipe_json="{broken"),
        )
    assert exc.value.status_code == 400
    assert "Legacy templates are no longer supported" in exc.value.detail["message"]


def test_channel_profile_api_create_with_null_account():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import ChannelProfileCreateRequest

    item = api.editing_channel_profile_create(
        ChannelProfileCreateRequest(
            name="Future Channel",
            youtube_account_id=None,
            default_privacy="private",
        )
    )["item"]

    assert item["youtube_account_id"] is None
    assert item["default_privacy"] == "private"
    assert api.editing_channel_profiles()["items"][0]["name"] == "Future Channel"


def test_channel_profile_api_links_and_updates_account_template_pool():
    from shortsfarm import db
    from shortsfarm.studio_templates import ensure_default_studio_templates
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        ChannelProfileCreateRequest,
        ChannelProfileUpdateRequest,
        ReactionPoolCreateRequest,
    )

    account_id = _youtube_account()
    templates = ensure_default_studio_templates()
    template_id = int(next(row for row in templates if row["template_key"] == "main_only")["id"])
    second_template_id = int(next(row for row in templates if row["template_key"] == "reaction_top_25")["id"])
    pool_id = api.editing_reaction_pool_create(
        ReactionPoolCreateRequest(name="Pool One")
    )["item"]["id"]
    second_pool_id = api.editing_reaction_pool_create(
        ReactionPoolCreateRequest(name="Pool Two")
    )["item"]["id"]

    created = api.editing_channel_profile_create(
        ChannelProfileCreateRequest(
                name="Funny RU Channel",
                youtube_account_id=account_id,
                default_studio_template_id=template_id,
                reaction_pool_id=pool_id,
                default_privacy="private",
                default_category_id="22",
            )
        )["item"]
    assert created["youtube_channel_title"] == "Editing Channel"
    assert created["default_template_name"] == ""
    assert created["default_studio_template_name"] == "Main Only"
    assert created["reaction_pool_name"] == "Pool One"

    updated = api.editing_channel_profile_update(
        created["id"],
        ChannelProfileUpdateRequest(
            default_studio_template_id=second_template_id,
            reaction_pool_id=second_pool_id,
            default_privacy="unlisted",
        ),
    )["item"]

    assert updated["default_template_id"] is None
    assert updated["default_studio_template_id"] == second_template_id
    assert updated["default_studio_template_name"] == "Reaction Top 25%"
    assert updated["reaction_pool_id"] == second_pool_id
    assert updated["reaction_pool_name"] == "Pool Two"
    assert updated["default_privacy"] == "unlisted"

    legacy_id = db.create_edit_template(
        key="legacy_profile_template",
        name="Legacy Profile Template",
        recipe_json=_template_recipe(),
    )
    with pytest.raises(HTTPException) as exc:
        api.editing_channel_profile_update(
            created["id"],
            ChannelProfileUpdateRequest(default_template_id=legacy_id),
        )
    assert exc.value.status_code == 400
    assert "Legacy templates are no longer supported" in exc.value.detail["message"]
