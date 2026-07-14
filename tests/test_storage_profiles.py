from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


def _workspace(tmp_path: Path) -> Path:
    from shortsfarm.workspace_fs import set_workspace_root

    return set_workspace_root(tmp_path / "workspace")


def _video(root: Path, relative: str, content: bytes = b"video") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _profile_id(name: str = "Local Shorts") -> int:
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileCreateRequest

    return int(
        api.local_storage_profile_create(
            LocalStorageProfileCreateRequest(name=name)
        )["profile"]["id"]
    )


def _youtube_account(
    *,
    channel_id: str = "channel-1",
    channel_title: str = "Channel One",
    status: str = "active",
) -> int:
    from shortsfarm import db

    profile_id = db.create_youtube_oauth_profile(
        name=f"OAuth {channel_id}",
        mode="custom",
        client_id=f"client-{channel_id}",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    return db.save_social_account(
        platform="youtube",
        display_name=channel_title,
        channel_id=channel_id,
        channel_title=channel_title,
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=profile_id,
        account_email=f"{channel_id}@example.com",
        status=status,
    )


def _set_youtube_account_metadata(
    account_id: int,
    *,
    title: str = "Official Channel",
    handle: str = "@official",
    description: str = "Official description",
    avatar_url: str = "https://img.example/avatar.jpg",
    banner_url: str = "https://img.example/banner.jpg",
) -> None:
    from shortsfarm import db

    db.update_social_account_channel_metadata(
        account_id,
        channel_title=title,
        channel_description=description,
        channel_custom_url=handle,
        channel_handle=handle,
        channel_avatar_url=avatar_url,
        channel_banner_url=banner_url,
        channel_branding_json=f'{{"image":{{"bannerExternalUrl":"{banner_url}"}}}}',
        uploads_playlist_id="UU-official",
        metadata_synced_at=db.now_utc(),
        metadata_sync_error=None,
    )


def _patch_youtube_channel_metadata(monkeypatch, *, title="Synced Channel", avatar_url="https://img.example/synced.jpg"):
    from shortsfarm import publish_youtube

    class Channels:
        def list(self, **kwargs):
            class Request:
                def execute(self):
                    return {
                        "items": [
                            {
                                "id": kwargs.get("id") or "channel-1",
                                "snippet": {
                                    "title": title,
                                    "description": "Synced description",
                                    "customUrl": "@synced",
                                    "thumbnails": {"high": {"url": avatar_url}},
                                },
                                "brandingSettings": {"image": {"bannerExternalUrl": "https://img.example/synced-banner.jpg"}},
                                "statistics": {"subscriberCount": "10", "viewCount": "20", "videoCount": "3"},
                                "contentDetails": {"relatedPlaylists": {"uploads": "UU-synced"}},
                                "status": {"privacyStatus": "public"},
                            }
                        ]
                    }

            return Request()

    class YouTube:
        def channels(self):
            return Channels()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())


def test_storage_profile_create_update_and_list(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileCreateRequest,
        LocalStorageProfileUpdateRequest,
    )

    data = api.local_storage_profile_create(
        LocalStorageProfileCreateRequest(
            name="Gaming Shorts",
            description="Локальная витрина",
            avatar_color="#ff0000",
            banner_color="#00ff00",
        )
    )
    profile_id = data["profile"]["id"]

    updated = api.local_storage_profile_update(
        profile_id,
        LocalStorageProfileUpdateRequest(
            name="Gaming Shorts RU",
            handle="gaming-ru",
            avatar_initials="gs",
        ),
    )["profile"]
    listed = api.local_storage_profiles()["items"]

    assert updated["name"] == "Gaming Shorts RU"
    assert updated["handle"] == "gaming-ru"
    assert updated["avatar_initials"] == "GS"
    assert any(item["id"] == profile_id for item in listed)


def test_storage_profile_youtube_link_and_unlink(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id()
    account_id = _youtube_account(channel_title="Shorts Channel")

    linked = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )["profile"]
    link = linked["service_links"][0]

    assert link["platform"] == "youtube"
    assert link["external_account_id"] == account_id
    assert link["display_name"] == "Shorts Channel"
    assert link["youtube_account"]["channel_title"] == "Shorts Channel"
    listed = api.local_storage_profiles()["items"]
    listed_profile = next(item for item in listed if item["id"] == profile_id)
    assert listed_profile["service_links"][0]["external_account_id"] == account_id

    unlinked = api.local_storage_profile_youtube_unlink(profile_id)["profile"]

    assert unlinked["service_links"] == []


def test_storage_profile_youtube_link_uses_effective_channel_branding(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Old Channel")
    _set_youtube_account_metadata(
        account_id,
        title="Official Shorts",
        handle="@official-shorts",
        description="Official YouTube channel description",
        avatar_url="https://img.example/official.jpg",
    )

    profile = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )["profile"]

    assert profile["name"] == "Local Name"
    assert profile["effective_name"] == "Official Shorts"
    assert profile["effective_handle"] == "official-shorts"
    assert profile["effective_description"] == "Official YouTube channel description"
    assert profile["effective_avatar_url"] == "https://img.example/official.jpg"
    assert profile["effective_banner_url"] == "https://img.example/banner.jpg"
    assert profile["effective_avatar_initials"] == "OF"


def test_storage_profile_manual_name_override_wins_over_youtube_branding(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileUpdateRequest,
        LocalStorageProfileYouTubeLinkRequest,
    )

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Official One")
    _set_youtube_account_metadata(account_id, title="Official One")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    updated = api.local_storage_profile_update(
        profile_id,
        LocalStorageProfileUpdateRequest(name="Manual Profile Name"),
    )["profile"]
    assert updated["effective_name"] == "Manual Profile Name"
    assert updated["youtube_branding"]["overrides"]["name"] is True

    db.update_social_account_channel_metadata(
        account_id,
        channel_title="Official Two",
        channel_avatar_url="https://img.example/two.jpg",
        metadata_synced_at=db.now_utc(),
        metadata_sync_error=None,
    )
    detail = api.local_storage_profile_detail(profile_id)["profile"]

    assert detail["effective_name"] == "Manual Profile Name"
    assert detail["effective_avatar_url"] == "https://img.example/two.jpg"


def test_storage_profile_field_overrides_can_return_to_youtube(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileUpdateRequest,
        LocalStorageProfileYouTubeLinkRequest,
    )

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Official One")
    _set_youtube_account_metadata(
        account_id,
        title="Official One",
        avatar_url="https://img.example/youtube-avatar.jpg",
    )
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    local = api.local_storage_profile_update(
        profile_id,
        LocalStorageProfileUpdateRequest(
            name="Manual Name",
            avatar_url="https://img.example/local-avatar.jpg",
        ),
    )["profile"]
    assert local["effective_name"] == "Manual Name"
    assert local["effective_avatar_url"] == "https://img.example/local-avatar.jpg"
    assert local["youtube_branding"]["overrides"]["name"] is True
    assert local["youtube_branding"]["overrides"]["avatar"] is True

    reset = api.local_storage_profile_update(
        profile_id,
        LocalStorageProfileUpdateRequest(name_override=False, avatar_override=False),
    )["profile"]
    assert reset["effective_name"] == "Official One"
    assert reset["effective_avatar_url"] == "https://img.example/youtube-avatar.jpg"
    assert reset["youtube_branding"]["overrides"]["name"] is False
    assert reset["youtube_branding"]["overrides"]["avatar"] is False


def test_storage_profile_youtube_branding_disabled_uses_local_values(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileUpdateRequest,
        LocalStorageProfileYouTubeLinkRequest,
    )

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Official One")
    _set_youtube_account_metadata(account_id, title="Official One")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    profile = api.local_storage_profile_update(
        profile_id,
        LocalStorageProfileUpdateRequest(youtube_branding_sync_enabled=False),
    )["profile"]

    assert profile["effective_name"] == "Local Name"
    assert profile["effective_handle"] == "local-name"
    assert profile["effective_avatar_url"] == ""
    assert profile["youtube_branding"]["sync_enabled"] is False


def test_youtube_account_sync_updates_linked_profile_branding(monkeypatch, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_id="sync-channel", channel_title="Before Sync")
    _patch_youtube_channel_metadata(
        monkeypatch,
        title="Synced Official",
        avatar_url="https://img.example/synced-official.jpg",
    )
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    result = api.youtube_account_sync_metadata(account_id)
    profile = api.local_storage_profile_detail(profile_id)["profile"]

    assert result["status"] == "ok"
    assert result["branding_profiles"] == 1
    assert profile["effective_name"] == "Synced Official"
    assert profile["effective_handle"] == "synced"
    assert profile["effective_avatar_url"] == "https://img.example/synced-official.jpg"
    assert profile["effective_banner_url"] == "https://img.example/synced-banner.jpg"
    assert profile["youtube_branding"]["sync_error"] is None
    assert profile["youtube_branding"]["synced_at"] is not None
    assert profile["youtube_branding"]["attempted_at"] is not None


def test_storage_profile_youtube_branding_sync_error_is_stored(monkeypatch, tmp_path):
    from shortsfarm import publish_youtube
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Broken Channel")
    _set_youtube_account_metadata(account_id, title="Broken Channel")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    def fail_build(account):
        raise RuntimeError("YouTube metadata unavailable")

    monkeypatch.setattr(publish_youtube, "build_youtube_client", fail_build)

    result = api.local_storage_profile_youtube_sync_branding(profile_id)

    assert result["status"] == "failed"
    assert "YouTube metadata unavailable" in result["error"]
    assert result["profile"]["service_links"][0]["external_account_id"] == account_id
    assert result["profile"]["youtube_branding"]["sync_error"] == "YouTube metadata unavailable"


def test_storage_profile_sync_error_keeps_last_success_time(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm import publish_youtube
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Broken Channel")
    _set_youtube_account_metadata(account_id, title="Broken Channel")
    success_at = "2026-07-01T10:00:00+00:00"
    db.update_local_storage_profile_youtube_branding_sync(profile_id, synced_at=success_at, error=None)
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    def fail_build(account):
        raise RuntimeError("YouTube metadata unavailable")

    monkeypatch.setattr(publish_youtube, "build_youtube_client", fail_build)
    result = api.local_storage_profile_youtube_sync_branding(profile_id)

    assert result["status"] == "failed"
    assert result["profile"]["youtube_branding"]["synced_at"] == success_at
    assert result["profile"]["youtube_branding"]["attempted_at"] != success_at


def test_storage_profile_youtube_link_keeps_link_on_sync_error(monkeypatch, tmp_path):
    from shortsfarm import publish_youtube
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Broken Channel")

    def fail_build(account):
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(publish_youtube, "build_youtube_client", fail_build)
    result = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    assert result["status"] == "linked_with_sync_error"
    assert result["sync_error"] == "metadata failed"
    assert result["profile"]["service_links"][0]["external_account_id"] == account_id
    assert result["profile"]["youtube_branding"]["sync_error"] == "metadata failed"


def test_storage_profile_youtube_unlink_clears_branding_status(monkeypatch, tmp_path):
    from shortsfarm import publish_youtube
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Local Name")
    account_id = _youtube_account(channel_title="Broken Channel")

    def fail_build(account):
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(publish_youtube, "build_youtube_client", fail_build)
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    profile = api.local_storage_profile_youtube_unlink(profile_id)["profile"]

    assert profile["service_links"] == []
    assert profile["youtube_branding"]["sync_error"] is None
    assert profile["youtube_branding"]["synced_at"] is None
    assert profile["youtube_branding"]["attempted_at"] is None


def test_youtube_account_sync_updates_multiple_linked_profiles(monkeypatch, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    first_profile_id = _profile_id(name="First")
    second_profile_id = _profile_id(name="Second")
    account_id = _youtube_account(channel_id="sync-channel", channel_title="Before Sync")
    _patch_youtube_channel_metadata(
        monkeypatch,
        title="Synced Official",
        avatar_url="https://img.example/synced-official.jpg",
    )

    for profile_id in (first_profile_id, second_profile_id):
        api.local_storage_profile_youtube_link(
            profile_id,
            LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
        )

    result = api.youtube_account_sync_metadata(account_id)

    assert result["status"] == "ok"
    assert result["branding_profiles"] == 2
    assert api.local_storage_profile_detail(first_profile_id)["profile"]["effective_name"] == "Synced Official"
    assert api.local_storage_profile_detail(second_profile_id)["profile"]["effective_name"] == "Synced Official"


def test_storage_profile_youtube_link_updates_existing_link(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id()
    first_id = _youtube_account(channel_id="channel-1", channel_title="First")
    second_id = _youtube_account(channel_id="channel-2", channel_title="Second")

    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=first_id),
    )
    updated = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=second_id),
    )["profile"]

    assert len(updated["service_links"]) == 1
    assert updated["service_links"][0]["external_account_id"] == second_id
    assert updated["service_links"][0]["display_name"] == "Second"


def test_storage_profile_publish_settings_persist_and_apply_defaults(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfilePublishSettingsRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "edits/channel/defaults.mp4"
    _video(root, relative)
    profile_id = _profile_id(name="Hello World")

    saved = api.local_storage_profile_publish_settings_update(
        profile_id,
        LocalStorageProfilePublishSettingsRequest(
            publish_mode="unlisted",
            category_id="24",
            made_for_kids=True,
            title_template="{profile} · {stem}",
            description_template="Профиль {profile}: {file_name}",
            tags_template="shorts, {handle}",
            default_action="schedule",
        ),
    )

    assert saved["settings"]["publish_mode"] == "unlisted"
    assert saved["settings"]["category_id"] == "24"
    assert saved["profile"]["service_links"][0]["status"] == "not_connected"

    account_id = _youtube_account(channel_title="Defaults Channel")
    linked = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )["profile"]

    assert linked["service_links"][0]["settings_json"]

    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, status="ready"),
    )["item"]
    data = api.local_storage_profile_youtube_enqueue(
        profile_id,
        LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]]),
    )
    job = data["jobs"][0]

    assert job["title"] == "Hello World · defaults"
    assert job["description"] == "Профиль Hello World: defaults.mp4"
    assert job["category_id"] == "24"
    assert job["privacy_status"] == "unlisted"
    assert job["publish_mode"] == "unlisted"
    assert job["made_for_kids"] is True
    assert "shorts" in job["tags"]


def test_youtube_accounts_include_oauth_and_linked_storage_profiles(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id(name="Linked Local Profile")
    account_id = _youtube_account(channel_title="Mapped Channel")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )

    accounts = api.youtube_accounts()["accounts"]
    account = next(item for item in accounts if item["id"] == account_id)

    assert account["oauth_profile"]["name"].startswith("OAuth")
    assert account["profile_name"] == account["oauth_profile"]["name"]
    assert [profile["id"] for profile in account["linked_storage_profiles"]] == [profile_id]


def test_storage_profile_youtube_link_rejects_inactive_account(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id()
    account_id = _youtube_account(status="disconnected")

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_youtube_link(
            profile_id,
            LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
        )

    assert exc.value.status_code == 400
    assert "не активен" in str(exc.value.detail)


def test_storage_profile_youtube_enqueue_creates_publish_job(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "edits/channel/final.mp4"
    video = _video(root, relative)
    profile_id = _profile_id()
    account_id = _youtube_account(channel_title="Profile Channel")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(
            workspace_path=relative,
            title="Ready Short",
            description="Profile description",
            tags="one, two",
            status="ready",
        ),
    )["item"]

    data = api.local_storage_profile_youtube_enqueue(
        profile_id,
        LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]]),
    )

    assert data["summary"]["created"] == 1
    assert data["summary"]["errors"] == 0
    assert data["jobs"][0]["title"] == "Ready Short"
    assert data["jobs"][0]["account_id"] == account_id
    assert data["jobs"][0]["privacy_status"] == "public"
    assert data["jobs"][0]["publish_mode"] == "public"
    assert data["jobs"][0]["clip_output_path"] == str(video)
    assert data["profile_items"][0]["publish_job"]["id"] == data["jobs"][0]["id"]
    assert api.local_storage_profile_publish_jobs(profile_id)["jobs"][0]["id"] == data["jobs"][0]["id"]

    clip = db.get_clip(data["jobs"][0]["clip_id"])
    assert clip["status"] == "done"
    assert clip["cut_mode"] == "profile"
    assert clip["output_path"] == str(video)


def test_storage_profile_youtube_enqueue_requires_linked_account(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "ready/channel/final.mp4"
    _video(root, relative)
    profile_id = _profile_id()
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, status="ready"),
    )["item"]

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_youtube_enqueue(
            profile_id,
            LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]]),
        )

    assert exc.value.status_code == 400
    assert "привяжите YouTube" in str(exc.value.detail)


def test_storage_profile_youtube_enqueue_reuses_existing_job(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "published/channel/final.mp4"
    _video(root, relative)
    profile_id = _profile_id()
    account_id = _youtube_account()
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, status="ready"),
    )["item"]
    req = LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]])

    first = api.local_storage_profile_youtube_enqueue(profile_id, req)
    second = api.local_storage_profile_youtube_enqueue(profile_id, req)

    assert first["jobs"][0]["id"] == second["jobs"][0]["id"]
    assert second["summary"]["created"] == 0
    assert second["summary"]["updated"] == 1


def test_storage_profile_youtube_enqueue_rejects_other_account(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "edits/channel/final.mp4"
    _video(root, relative)
    profile_id = _profile_id()
    linked_id = _youtube_account(channel_id="linked", channel_title="Linked")
    other_id = _youtube_account(channel_id="other", channel_title="Other")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=linked_id),
    )
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, status="ready"),
    )["item"]

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_youtube_enqueue(
            profile_id,
            LocalStorageProfileYouTubePublishRequest(
                item_ids=[item["id"]],
                account_id=other_id,
            ),
        )

    assert exc.value.status_code == 400
    assert "другому YouTube" in str(exc.value.detail)


def test_storage_profile_auto_import_adds_matching_ready_videos(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileAutoImportRunRequest,
        LocalStorageProfileCreateRequest,
    )

    root = _workspace(tmp_path)
    _video(root, "edits/channel/one.mp4")
    _video(root, "edits/other/two.mp4")
    _video(root, "ready/channel/three.mp4")
    _video(root, "published/channel/four.mp4")
    profile_id = int(
        api.local_storage_profile_create(
            LocalStorageProfileCreateRequest(
                name="Auto Profile",
                auto_import_enabled=True,
                auto_import_sections=["edits", "published"],
                auto_import_prefix="edits/channel",
            )
        )["profile"]["id"]
    )

    data = api.local_storage_profile_auto_import_run(
        profile_id,
        LocalStorageProfileAutoImportRunRequest(),
    )
    paths = {item["workspace_path"] for item in data["items"]}

    assert data["summary"]["added"] == 1
    assert paths == {"edits/channel/one.mp4"}
    assert data["profile"]["auto_import"]["last_scan_at"]


def test_storage_profile_auto_import_disabled_requires_force(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileAutoImportRunRequest

    root = _workspace(tmp_path)
    _video(root, "ready/channel/one.mp4")
    profile_id = _profile_id()

    disabled = api.local_storage_profile_auto_import_run(
        profile_id,
        LocalStorageProfileAutoImportRunRequest(),
    )
    forced = api.local_storage_profile_auto_import_run(
        profile_id,
        LocalStorageProfileAutoImportRunRequest(force=True),
    )

    assert disabled["disabled"] is True
    assert disabled["summary"]["added"] == 0
    assert forced["disabled"] is False
    assert forced["summary"]["added"] == 1
    assert forced["items"][0]["workspace_path"] == "ready/channel/one.mp4"


def test_storage_profile_youtube_sync_fetches_channel_inventory(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm import publish_youtube
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    class Request:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return self.payload

    class Channels:
        def list(self, **kwargs):
            return Request({
                "items": [{
                    "id": "channel-1",
                    "snippet": {"title": "Sync Channel"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "uploads-1"}},
                }]
            })

    class PlaylistItems:
        def list(self, **kwargs):
            return Request({
                "items": [
                    {"contentDetails": {"videoId": "yt-sync", "videoPublishedAt": "2026-07-01T10:00:00Z"}},
                    {"contentDetails": {"videoId": "yt-external", "videoPublishedAt": "2026-07-02T10:00:00Z"}},
                ]
            })

    class Videos:
        def list(self, **kwargs):
            return Request({
                "items": [
                    {
                        "id": "yt-sync",
                        "snippet": {
                            "title": "Synced title",
                            "description": "Synced description",
                            "tags": ["one", "two"],
                            "categoryId": "22",
                            "publishedAt": "2026-07-01T10:00:00Z",
                            "thumbnails": {"high": {"url": "https://img.example/yt-sync.jpg"}},
                        },
                        "status": {"privacyStatus": "public"},
                        "contentDetails": {"duration": "PT42S"},
                    },
                    {
                        "id": "yt-external",
                        "snippet": {
                            "title": "External channel video",
                            "description": "Not in local profile",
                            "tags": [],
                            "categoryId": "22",
                            "publishedAt": "2026-07-02T10:00:00Z",
                        },
                        "status": {"privacyStatus": "unlisted"},
                        "contentDetails": {"duration": "PT1M"},
                    },
                ]
            })

    class YouTube:
        def channels(self):
            return Channels()

        def playlistItems(self):
            return PlaylistItems()

        def videos(self):
            return Videos()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())
    root = _workspace(tmp_path)
    relative = "edits/channel/final.mp4"
    _video(root, relative)
    profile_id = _profile_id()
    account_id = _youtube_account(channel_title="Sync Channel")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, status="ready"),
    )["item"]
    job = api.local_storage_profile_youtube_enqueue(
        profile_id,
        LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]]),
    )["jobs"][0]
    db.mark_publish_done(job["id"], "yt-sync", "https://youtu.be/yt-sync")

    synced = api.local_storage_profile_youtube_sync(profile_id)
    youtube_videos = api.local_storage_profile_youtube_videos(profile_id)["videos"]

    assert synced["summary"]["fetched"] == 2
    assert synced["summary"]["matched_jobs"] == 1
    assert synced["summary"]["matched_profile_items"] == 1
    assert synced["summary"]["external_only"] == 1
    assert synced["summary"]["published"] == 1
    assert synced["items"][0]["status"] == "published"
    assert synced["items"][0]["publish_job"]["youtube_url"] == "https://www.youtube.com/watch?v=yt-sync"
    assert synced["items"][0]["publish_job"]["title"] == "Synced title"
    assert synced["items"][0]["publish_job"]["privacy_status"] == "public"
    assert synced["profile"]["service_links"][0]["display_name"] == "Sync Channel"
    assert synced["profile"]["service_links"][0]["last_sync_at"]
    assert {video["external_video_id"] for video in youtube_videos} == {"yt-sync", "yt-external"}
    external = next(video for video in youtube_videos if video["external_video_id"] == "yt-external")
    assert external["matched"] is False
    matched = next(video for video in youtube_videos if video["external_video_id"] == "yt-sync")
    assert matched["matched"] is True


@pytest.mark.parametrize("folder", ["edits", "ready", "published"])
def test_storage_profile_adds_ready_video_from_allowed_folders(tmp_path, folder):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    root = _workspace(tmp_path)
    relative = f"{folder}/channel/video.mp4"
    _video(root, relative)
    profile_id = _profile_id()

    data = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative),
    )
    detail = api.local_storage_profile_detail(profile_id)

    assert data["item"]["workspace_path"] == relative
    assert data["item"]["file_exists"] is True
    assert detail["profile"]["item_count"] == 1
    assert detail["items"][0]["section"] == folder


def test_storage_profile_duplicate_video_updates_existing_item(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    root = _workspace(tmp_path)
    relative = "edits/channel/video.mp4"
    _video(root, relative)
    profile_id = _profile_id()

    first = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, title="Old"),
    )["item"]
    second = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative, title="New"),
    )["item"]
    detail = api.local_storage_profile_detail(profile_id)

    assert second["id"] == first["id"]
    assert detail["profile"]["item_count"] == 1
    assert detail["items"][0]["title"] == "New"


@pytest.mark.parametrize(
    "relative",
    [
        "sources/raw.mp4",
        "cuts/raw/segment.mp4",
        "prepared/raw/9x16/video.mp4",
    ],
)
def test_storage_profile_rejects_non_ready_workspace_sections(tmp_path, relative):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    root = _workspace(tmp_path)
    _video(root, relative)
    profile_id = _profile_id()

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_item_add(
            profile_id,
            LocalStorageProfileItemCreateRequest(workspace_path=relative),
        )

    assert exc.value.status_code == 403
    assert "готовые видео" in str(exc.value.detail)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/tmp/video.mp4",
        "../edits/video.mp4",
        "edits/../video.mp4",
        ".shortsfarm/metadata/video.mp4",
    ],
)
def test_storage_profile_rejects_unsafe_paths(tmp_path, bad_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    _workspace(tmp_path)
    profile_id = _profile_id()

    with pytest.raises(HTTPException):
        api.local_storage_profile_item_add(
            profile_id,
            LocalStorageProfileItemCreateRequest(workspace_path=bad_path),
        )


def test_storage_profile_rejects_non_video_inside_ready_folder(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    root = _workspace(tmp_path)
    text = root / "ready" / "note.txt"
    text.parent.mkdir(parents=True, exist_ok=True)
    text.write_text("not video", encoding="utf-8")
    profile_id = _profile_id()

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_item_add(
            profile_id,
            LocalStorageProfileItemCreateRequest(workspace_path="ready/note.txt"),
        )

    assert exc.value.status_code == 400


def test_storage_profile_rejects_symlink(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileItemCreateRequest

    root = _workspace(tmp_path)
    target = _video(root, "ready/target.mp4")
    link = root / "ready" / "link.mp4"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable.")
    profile_id = _profile_id()

    with pytest.raises(HTTPException) as exc:
        api.local_storage_profile_item_add(
            profile_id,
            LocalStorageProfileItemCreateRequest(workspace_path="ready/link.mp4"),
        )

    assert exc.value.status_code == 403


def test_storage_profile_ready_videos_lists_only_allowed_video_folders(tmp_path):
    from shortsfarm.web import api

    root = _workspace(tmp_path)
    _video(root, "edits/one.mp4")
    _video(root, "ready/two.mov")
    _video(root, "published/three.mkv")
    _video(root, "sources/raw.mp4")
    (root / "ready" / "note.txt").write_text("not video", encoding="utf-8")

    data = api.local_storage_profile_ready_videos()
    paths = {item["workspace_path"] for item in data["items"]}

    assert {"edits/one.mp4", "ready/two.mov", "published/three.mkv"} <= paths
    assert "sources/raw.mp4" not in paths
    assert "ready/note.txt" not in paths


def test_channel_tag_is_created_reconciled_and_removed_from_profile_rules(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalStorageProfileTagRulesRequest, LocalStorageProfileYouTubeLinkRequest

    _workspace(tmp_path)
    profile_id = _profile_id()
    account_id = _youtube_account(channel_title="Hello World")

    linked = api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )["profile"]
    channel_rules = [
        rule for rule in linked["tag_rules"]
        if rule["mode"] == "include" and rule["tag"]["kind"] == "channel"
    ]
    assert len(channel_rules) == 1
    assert channel_rules[0]["locked"] is True
    assert channel_rules[0]["tag"]["name"] == "channel-Hello World"
    assert channel_rules[0]["tag"]["slug"] == "channel-hello-world"
    assert channel_rules[0]["tag"]["color"] == "#f59e0b"

    extra = db.ensure_channel_tag_for_account(account_id=999, display_name="Wrong Channel")
    api.local_storage_profile_tag_rules_update(
        profile_id,
        LocalStorageProfileTagRulesRequest(
            include_tag_ids=[channel_rules[0]["tag_id"], int(extra["id"])],
            exclude_tag_ids=[],
            tag_match_mode="any",
        ),
    )
    detail = api.local_storage_profile_detail(profile_id)["profile"]
    channel_slugs = [
        rule["tag"]["slug"] for rule in detail["tag_rules"]
        if rule["tag"]["kind"] == "channel"
    ]
    assert channel_slugs == ["channel-hello-world"]

    unlinked = api.local_storage_profile_youtube_unlink(profile_id)["profile"]
    assert [
        rule for rule in unlinked["tag_rules"]
        if rule["tag"]["kind"] == "channel"
    ] == []


def test_catalog_tags_search_random_and_status_compat(tmp_path):
    from shortsfarm.web import tags_api
    from shortsfarm.web.schemas import CatalogVideoTagsRequest, TagCreateRequest

    root = _workspace(tmp_path)
    _video(root, "ready/anime/clip-one.mp4")
    _video(root, "edits/cinema/clip-two.mp4")
    _video(root, "sources/raw.mp4")

    anime = tags_api.tag_create(TagCreateRequest(name="аниме", color="#ff77aa"))["tag"]
    ready = next(tag for tag in tags_api.tags_list()["items"] if tag["slug"] == "status-ready")
    tags_api.catalog_video_tags_update(
        CatalogVideoTagsRequest(
            workspace_path="ready/anime/clip-one.mp4",
            tag_ids=[anime["id"], ready["id"]],
        )
    )

    tags = tags_api.catalog_video_tags("ready/anime/clip-one.mp4")["tags"]
    assert {tag["slug"] for tag in tags} >= {"аниме", "status-ready"}
    raw_tags = tags_api.catalog_video_tags_update(
        CatalogVideoTagsRequest(
            workspace_path="sources/raw.mp4",
            tag_ids=[anime["id"]],
        )
    )["tags"]
    assert {tag["slug"] for tag in raw_tags} == {"аниме"}

    search = tags_api.catalog_videos_search(q="аниме")["items"]
    assert [item["workspace_path"] for item in search] == ["ready/anime/clip-one.mp4"]
    assert search[0]["is_publish_ready"] is True
    all_scope_search = tags_api.catalog_videos_search(q="raw", scope="all")["items"]
    assert [item["workspace_path"] for item in all_scope_search] == ["sources/raw.mp4"]

    random_items = tags_api.catalog_videos_random(limit=20)["items"]
    paths = {item["workspace_path"] for item in random_items}
    assert "ready/anime/clip-one.mp4" in paths
    assert "edits/cinema/clip-two.mp4" in paths
    assert "sources/raw.mp4" not in paths
    all_random_paths = {item["workspace_path"] for item in tags_api.catalog_videos_random(scope="all", limit=20)["items"]}
    assert "sources/raw.mp4" in all_random_paths


def test_profile_tag_sync_any_all_and_exclude(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web import tags_api
    from shortsfarm.web.schemas import (
        CatalogVideoTagsRequest,
        LocalStorageProfileTagRulesRequest,
        TagCreateRequest,
    )

    root = _workspace(tmp_path)
    _video(root, "ready/channel/anime.mp4")
    _video(root, "ready/channel/anime-film.mp4")
    _video(root, "ready/channel/film.mp4")

    anime = tags_api.tag_create(TagCreateRequest(name="аниме"))["tag"]
    film = tags_api.tag_create(TagCreateRequest(name="кино"))["tag"]
    ready = next(tag for tag in tags_api.tags_list()["items"] if tag["slug"] == "status-ready")
    tags_api.catalog_video_tags_update(CatalogVideoTagsRequest(
        workspace_path="ready/channel/anime.mp4",
        tag_ids=[anime["id"], ready["id"]],
    ))
    tags_api.catalog_video_tags_update(CatalogVideoTagsRequest(
        workspace_path="ready/channel/anime-film.mp4",
        tag_ids=[anime["id"], film["id"], ready["id"]],
    ))
    tags_api.catalog_video_tags_update(CatalogVideoTagsRequest(
        workspace_path="ready/channel/film.mp4",
        tag_ids=[film["id"], ready["id"]],
    ))

    profile_any = _profile_id("Anime Profile")
    api.local_storage_profile_tag_rules_update(
        profile_any,
        LocalStorageProfileTagRulesRequest(
            include_tag_ids=[anime["id"]],
            exclude_tag_ids=[film["id"]],
            tag_match_mode="any",
        ),
    )
    synced_any = api.local_storage_profile_tag_sync_run(profile_any)
    assert synced_any["summary"]["matched"] == 1
    assert {item["workspace_path"] for item in synced_any["items"]} == {"ready/channel/anime.mp4"}

    profile_all = _profile_id("Anime Film Profile")
    api.local_storage_profile_tag_rules_update(
        profile_all,
        LocalStorageProfileTagRulesRequest(
            include_tag_ids=[anime["id"], film["id"]],
            exclude_tag_ids=[],
            tag_match_mode="all",
        ),
    )
    synced_all = api.local_storage_profile_tag_sync_run(profile_all)
    assert synced_all["summary"]["matched"] == 1
    assert {item["workspace_path"] for item in synced_all["items"]} == {"ready/channel/anime-film.mp4"}


def test_storage_profile_youtube_enqueue_requires_status_ready_tag(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        LocalStorageProfileItemCreateRequest,
        LocalStorageProfileYouTubeLinkRequest,
        LocalStorageProfileYouTubePublishRequest,
    )

    root = _workspace(tmp_path)
    relative = "edits/channel/draft.mp4"
    _video(root, relative)
    profile_id = _profile_id()
    account_id = _youtube_account(channel_title="Draft Channel")
    api.local_storage_profile_youtube_link(
        profile_id,
        LocalStorageProfileYouTubeLinkRequest(account_id=account_id),
    )
    item = api.local_storage_profile_item_add(
        profile_id,
        LocalStorageProfileItemCreateRequest(workspace_path=relative),
    )["item"]

    data = api.local_storage_profile_youtube_enqueue(
        profile_id,
        LocalStorageProfileYouTubePublishRequest(item_ids=[item["id"]]),
    )

    assert data["summary"]["created"] == 0
    assert data["summary"]["errors"] == 1
    assert data["jobs"] == []
    assert "тегом" in data["skipped_items"][0]["reason"]


def test_storage_profiles_ui_is_registered():
    root = Path(__file__).resolve().parents[1]
    html = (root / "shortsfarm" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (root / "shortsfarm" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "shortsfarm" / "web" / "static" / "style.css").read_text(encoding="utf-8")

    assert 'data-v="storage-profiles"' in html
    assert 'data-v="tags"' in html
    assert 'data-v="integrations"' in html
    assert 'data-v="publish"' not in html
    assert 'id="v-publish"' not in html
    assert 'id="v-integrations"' in html
    assert 'id="settings-youtube-oauth"' not in html
    assert 'data-settings-tab="youtube-oauth"' not in html
    assert "workspace-youtube-account" not in html
    assert "workspace-youtube-enqueue-btn" not in html
    assert "workspace-youtube-upload-btn" not in html
    assert "storage-profiles-grid" in html
    assert 'id="v-tags"' in html
    assert 'id="tags-manager"' in html
    assert 'id="v-storage-profile"' in html
    tags_html = html.split('<div id="v-tags"', 1)[1].split('<div id="v-storage-profiles"', 1)[0]
    hub_html = html.split('<div id="v-storage-profiles"', 1)[1].split('<div id="v-storage-profile"', 1)[0]
    detail_html = html.split('<div id="v-storage-profile"', 1)[1].split('<div id="v-integrations"', 1)[0]
    assert "Настройки профилей" in hub_html
    assert "Менеджер тегов" in tags_html
    assert "Создать тег" in tags_html
    assert "Менеджер тегов" not in hub_html
    assert "createGlobalCatalogTag" not in hub_html
    assert "storage-profile-detail" not in hub_html
    assert "storage-profile-detail" in detail_html
    assert "Все профили" in detail_html
    assert "Интеграции" in html
    assert "integrations-oauth-profiles" in html
    assert "integrations-accounts-list" in html
    assert "integration-oauth-modal" in html
    assert "integration-oauth-json" in html
    assert "integration-oauth-client-id" in html
    assert "integration-oauth-client-secret" in html
    assert "OAuth Client JSON" in html
    assert "Теперь нажмите" in html
    assert "ui-text-modal" in html
    assert "storage-profile-pick-modal" in html
    assert "storage-profile-card create-card" in js
    assert "openStorageProfile(profileId" in js
    assert "openStorageProfilesHub" in js
    assert "storage-channel-compact" in js
    assert "storage-profile-tabs" in js
    assert "storage-profile-actionbar" in js
    assert "storage-profile-drawer" in js
    assert "storageProfileMainContent(profile)" in js
    assert "renderStorageProfileDrawer(profile)" in js
    assert "openStorageProfileVideoPicker" in js
    assert "openGlobalTagsView" in js
    assert "loadTagsView" in js
    assert "renderGlobalTagsManager" in js
    assert "searchParams.set('profile'" in js
    assert "/api/catalog/videos/search" in js
    assert "/api/catalog/videos/random" in js
    assert "/api/tags" in js
    assert "/tag-rules" in js
    assert "/tag-sync/run" in js
    assert "/youtube/link" in js
    assert "/youtube/enqueue" in js
    assert "/youtube/sync" in js
    assert "/youtube/sync-branding" in js
    assert "/youtube/videos" in js
    assert "/publish-jobs" in js
    assert "/publish-settings" in js
    assert "Привязать YouTube" in js
    assert "Отвязать" in js
    assert "Обновить оформление с YouTube" in js
    assert "Автоматически брать оформление из YouTube" in js
    assert "Вернуть имя из YouTube" in js
    assert "Вернуть описание из YouTube" in js
    assert "Вернуть фото YouTube" in js
    assert "Вернуть шапку из YouTube" in js
    assert "linked_with_sync_error" in js
    assert "storage-avatar-fallback" in js
    assert "onerror=\"this.style.display='none'\"" in js
    assert "effective_banner_url" in js
    assert "setStorageProfileBrandingOverride" in js
    assert "Публикация YouTube" in js
    assert "Настройки публикации профиля" in js
    assert "Теги профиля" in js
    assert "Случайные видео" in js
    assert "Менеджер тегов" in js
    assert "Добавить теги в видео" in js
    assert "Поиск тегов" in js
    assert "tags-create-color" in js
    assert "updateCatalogTagColor" in js
    assert "scope=all" in js
    assert "workspace-catalog-tags-panel" in js
    assert "workspace-filter-tag-select" in html
    assert "workspace-search-input" in html
    assert "workspace-filter-active-tags" in html
    assert "addWorkspaceFilterTag" in js
    assert "workspaceFilterIncludeTagIds" in js
    assert "workspaceFilterExcludeTagIds" in js
    assert "bulkAddCatalogTagToWorkspaceItems" in js
    assert "bulkRemoveCatalogTagFromWorkspaceItems" in js
    assert "createStorageCatalogTag" not in js
    assert "assignTagToSelectedVideos" in js
    assert "Автоимпорт готовых видео" not in js
    assert "Синхронизировать YouTube" in js
    assert "Видео на YouTube" in js
    assert "только на YouTube" in js
    assert "enqueueStorageProfileSelection" in js
    assert "loadIntegrationsView" in js
    assert "renderIntegrationsAccountsPanel" in js
    assert "prompt(" not in js
    assert "settings-oauth" not in js
    assert "data-tag-color-id" in js
    assert "openTextActionModal" in js
    assert "openStorageProfilePickModal" in js
    create_integration_body = js.split("function createIntegrationOAuthProfile", 1)[1].split("function editIntegrationOAuthProfile", 1)[0]
    assert "prompt(" not in create_integration_body
    assert "/api/publish/youtube/oauth-profiles/import-client-json" in create_integration_body
    assert "/api/publish/youtube/oauth-profiles" in create_integration_body
    window_exports = js.split("Object.assign(window,", 1)[1].split("});", 1)[0]
    assert "loadIntegrationsView" in window_exports
    assert "closeTextActionModal" in window_exports
    assert "confirmTextActionModal" in window_exports
    assert "closeStorageProfilePickModal" in window_exports
    assert "confirmStorageProfilePickModal" in window_exports
    assert "createIntegrationOAuthProfile" in window_exports
    assert "closeIntegrationOAuthModal" in window_exports
    assert "saveIntegrationOAuthProfile" in window_exports
    assert "editIntegrationOAuthProfile" in window_exports
    assert "setIntegrationDefaultOAuthProfile" in window_exports
    assert "deleteIntegrationOAuthProfile" in window_exports
    assert "disconnectYouTubeAccount" in window_exports
    assert "openStorageProfile" in window_exports
    assert "addWorkspaceItemToStorageProfile" in js
    assert "storage-youtube-controls" in css
    assert "storage-avatar-wrap" in css
    assert "storage-avatar-fallback" in css
    assert "storage-profiles-hub" in css
    assert "storage-profile-route-head" in css
    assert "storage-profile-drawer" in css
    assert "storage-profile-main-grid" in css
    assert "storage-video-grid-compact" in css
    assert "storage-profile-actionbar" in css
    render_detail_body = js.split("function renderStorageProfileDetail()", 1)[1].split("async function saveStorageProfile", 1)[0]
    assert "${storageProfilePublishSettingsPanel()}" not in render_detail_body
    assert "${storageProfileTagRulesPanel(profile)}" not in render_detail_body
    assert "${storageProfileServiceLinks(profile)}" not in render_detail_body
    assert "${renderStorageCandidatePicker()}" not in render_detail_body
    assert "storage-tag-panel" in css
    assert "tags-manager" in css
    assert "tags-manager-grid" in css
    assert "storage-tags-manager" not in css
    assert "storage-tag-manager-grid" not in css
    assert "tag-color-input" in css
    assert "workspace-filter-panel" in css
    assert "filter-query-chip" in css
    assert "workspace-catalog-tags-panel" in css
    assert "workspace-tags-panel" in css
    assert "storage-search-panel" in css
    assert "storage-profile-publish-panel" in css
    assert "storage-auto-panel" not in css
    assert "storage-youtube-grid" in css
    assert "storage-video-grid" in css
