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
    from shortsfarm.web import api
    from shortsfarm.web.schemas import CatalogVideoTagsRequest, TagCreateRequest

    root = _workspace(tmp_path)
    _video(root, "ready/anime/clip-one.mp4")
    _video(root, "edits/cinema/clip-two.mp4")
    _video(root, "sources/raw.mp4")

    anime = api.tag_create(TagCreateRequest(name="аниме", color="#ff77aa"))["tag"]
    ready = next(tag for tag in api.tags_list()["items"] if tag["slug"] == "status-ready")
    api.catalog_video_tags_update(
        CatalogVideoTagsRequest(
            workspace_path="ready/anime/clip-one.mp4",
            tag_ids=[anime["id"], ready["id"]],
        )
    )

    tags = api.catalog_video_tags("ready/anime/clip-one.mp4")["tags"]
    assert {tag["slug"] for tag in tags} >= {"аниме", "status-ready"}
    raw_tags = api.catalog_video_tags_update(
        CatalogVideoTagsRequest(
            workspace_path="sources/raw.mp4",
            tag_ids=[anime["id"]],
        )
    )["tags"]
    assert {tag["slug"] for tag in raw_tags} == {"аниме"}

    search = api.catalog_videos_search(q="аниме")["items"]
    assert [item["workspace_path"] for item in search] == ["ready/anime/clip-one.mp4"]
    assert search[0]["is_publish_ready"] is True
    all_scope_search = api.catalog_videos_search(q="raw", scope="all")["items"]
    assert [item["workspace_path"] for item in all_scope_search] == ["sources/raw.mp4"]

    random_items = api.catalog_videos_random(limit=20)["items"]
    paths = {item["workspace_path"] for item in random_items}
    assert "ready/anime/clip-one.mp4" in paths
    assert "edits/cinema/clip-two.mp4" in paths
    assert "sources/raw.mp4" not in paths
    all_random_paths = {item["workspace_path"] for item in api.catalog_videos_random(scope="all", limit=20)["items"]}
    assert "sources/raw.mp4" in all_random_paths


def test_profile_tag_sync_any_all_and_exclude(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        CatalogVideoTagsRequest,
        LocalStorageProfileTagRulesRequest,
        TagCreateRequest,
    )

    root = _workspace(tmp_path)
    _video(root, "ready/channel/anime.mp4")
    _video(root, "ready/channel/anime-film.mp4")
    _video(root, "ready/channel/film.mp4")

    anime = api.tag_create(TagCreateRequest(name="аниме"))["tag"]
    film = api.tag_create(TagCreateRequest(name="кино"))["tag"]
    ready = next(tag for tag in api.tags_list()["items"] if tag["slug"] == "status-ready")
    api.catalog_video_tags_update(CatalogVideoTagsRequest(
        workspace_path="ready/channel/anime.mp4",
        tag_ids=[anime["id"], ready["id"]],
    ))
    api.catalog_video_tags_update(CatalogVideoTagsRequest(
        workspace_path="ready/channel/anime-film.mp4",
        tag_ids=[anime["id"], film["id"], ready["id"]],
    ))
    api.catalog_video_tags_update(CatalogVideoTagsRequest(
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
    assert "storage-profiles-grid" in html
    assert 'id="v-storage-profile"' in html
    hub_html = html.split('<div id="v-storage-profiles"', 1)[1].split('<div id="v-storage-profile"', 1)[0]
    detail_html = html.split('<div id="v-storage-profile"', 1)[1].split('<div id="v-publish"', 1)[0]
    assert "Настройки профилей" in hub_html
    assert "storage-profile-detail" not in hub_html
    assert "storage-profile-detail" in detail_html
    assert "Все профили" in detail_html
    assert "storage-profile-card create-card" in js
    assert "openStorageProfile(profileId" in js
    assert "openStorageProfilesHub" in js
    assert "searchParams.set('profile'" in js
    assert "/api/catalog/videos/search" in js
    assert "/api/catalog/videos/random" in js
    assert "/api/tags" in js
    assert "/tag-rules" in js
    assert "/tag-sync/run" in js
    assert "/youtube/link" in js
    assert "/youtube/enqueue" in js
    assert "/youtube/sync" in js
    assert "/youtube/videos" in js
    assert "/publish-jobs" in js
    assert "Привязать YouTube" in js
    assert "Отвязать" in js
    assert "Публикация YouTube" in js
    assert "Теги профиля" in js
    assert "Случайные видео" in js
    assert "Менеджер тегов" in js
    assert "Добавить теги в видео" in js
    assert "Поиск тегов" in js
    assert "storage-tag-create-color" in js
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
    assert "assignTagToSelectedVideos" in js
    assert "Автоимпорт готовых видео" not in js
    assert "Синхронизировать YouTube" in js
    assert "Видео на YouTube" in js
    assert "только на YouTube" in js
    assert "enqueueStorageProfileSelection" in js
    assert "addWorkspaceItemToStorageProfile" in js
    assert "storage-youtube-controls" in css
    assert "storage-profiles-hub" in css
    assert "storage-profile-route-head" in css
    assert "storage-tag-panel" in css
    assert "storage-tags-manager" in css
    assert "storage-tag-manager-grid" in css
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
