"""Tests for db.py helper functions."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# videos
# ---------------------------------------------------------------------------

def test_add_video_returns_id(dummy_video):
    from shortsfarm import db
    vid = db.add_video(dummy_video, "test", 120.0)
    assert isinstance(vid, int) and vid > 0


def test_add_video_idempotent(dummy_video):
    from shortsfarm import db
    id1 = db.add_video(dummy_video, "test", 120.0)
    id2 = db.add_video(dummy_video, "test", 120.0)
    assert id1 == id2


def test_get_video(video_in_db):
    from shortsfarm import db
    row = db.get_video(video_in_db)
    assert row is not None
    assert row["review_status"] == "inbox"


def test_update_review_status(video_in_db):
    from shortsfarm import db
    db.update_video_review_status(video_in_db, "reviewed")
    assert db.get_video(video_in_db)["review_status"] == "reviewed"


def test_claim_inbox_video(video_in_db):
    from shortsfarm import db
    claimed = db.claim_inbox_video()
    assert claimed is not None
    assert int(claimed["id"]) == video_in_db
    assert claimed["review_status"] == "reviewing"


def test_claim_inbox_video_empty(tmp_data_dir):
    from shortsfarm import db
    assert db.claim_inbox_video() is None


def test_claim_inbox_video_no_double_claim(video_in_db):
    from shortsfarm import db
    db.claim_inbox_video()
    second = db.claim_inbox_video()
    assert second is None          # already claimed


# ---------------------------------------------------------------------------
# review_sessions
# ---------------------------------------------------------------------------

def test_create_review_session(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    row = db.get_review_session(sid)
    assert row["status"] == "open"


def test_close_review_session(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    db.close_review_session(sid)
    assert db.get_review_session(sid)["status"] == "closed"


def test_fail_review_session(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    db.fail_review_session(sid, "boom")
    row = db.get_review_session(sid)
    assert row["status"] == "failed"
    assert "boom" in row["error"]


def test_import_review_session(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    db.import_review_session(sid, warning="minor warn")
    row = db.get_review_session(sid)
    assert row["status"] == "imported"
    assert row["imported_at"] is not None


def test_abandon_open_sessions(video_in_db, tmp_path):
    from shortsfarm import db
    sid1 = db.create_review_session(video_in_db, str(tmp_path / "a.jsonl"))
    sid2 = db.create_review_session(video_in_db, str(tmp_path / "b.jsonl"))
    count = db.abandon_open_sessions(video_in_db)
    assert count == 2
    for sid in (sid1, sid2):
        assert db.get_review_session(sid)["status"] == "abandoned"


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------

def test_insert_mark(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    mid = db.insert_mark(video_in_db, sid, 10.0, 70.0)
    rows = db.list_marks(video_in_db)
    assert len(rows) == 1
    assert rows[0]["in_sec"] == pytest.approx(10.0)
    assert rows[0]["id"] == mid


def test_count_marks(video_in_db, tmp_path):
    from shortsfarm import db
    sid = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    db.insert_mark(video_in_db, sid, 0.0,  30.0)
    db.insert_mark(video_in_db, sid, 30.0, 60.0)
    assert db.count_marks(video_in_db) == 2


# ---------------------------------------------------------------------------
# clips
# ---------------------------------------------------------------------------

def test_insert_and_list_clip(video_in_db, tmp_path):
    from shortsfarm import db
    mid = db.insert_mark(video_in_db, None, 0.0, 60.0)
    cid = db.insert_clip(video_in_db, mid)
    clips = db.list_clips(status="queued")
    assert any(int(c["id"]) == cid for c in clips)


def test_clip_lifecycle(video_in_db, tmp_path):
    from shortsfarm import db
    mid = db.insert_mark(video_in_db, None, 0.0, 60.0)
    cid = db.insert_clip(video_in_db, mid)

    db.set_clip_rendering(cid, "/tmp/temp.mp4")
    assert db.get_clip(cid)["status"] == "rendering"

    db.set_clip_done(cid, "/tmp/out.mp4")
    row = db.get_clip(cid)
    assert row["status"] == "done"
    assert row["output_path"] == "/tmp/out.mp4"


def test_reset_clip_to_queued(video_in_db):
    from shortsfarm import db
    mid = db.insert_mark(video_in_db, None, 0.0, 60.0)
    cid = db.insert_clip(video_in_db, mid)
    db.set_clip_failed(cid, "oops")
    db.reset_clip_to_queued(cid)
    row = db.get_clip(cid)
    assert row["status"] == "queued"
    assert row["error"] is None


def test_workspace_lists_segments_without_clips(video_in_db, tmp_path):
    from shortsfarm import db
    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    segment_path = tmp_path / "segment-001.mp4"
    segment_path.write_bytes(b"segment")
    segment_id = db.insert_segment(video_in_db, job_id, 1, 0.0, 12.5, segment_path)

    items = db.list_workspace_items()

    item = next(row for row in items if row["id"] == f"segment:{segment_id}")
    assert item["item_type"] == "segment"
    assert item["workspace_status"] == "draft"
    assert item["path"] == str(segment_path)
    assert item["duration_sec"] == pytest.approx(12.5)


def test_workspace_updates_metadata_and_filters(video_in_db, tmp_path):
    from shortsfarm import db
    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    segment_id = db.insert_segment(video_in_db, job_id, 1, 0.0, 20.0, tmp_path / "seg.mp4")

    assert db.update_workspace_item(
        "segment",
        segment_id,
        workspace_status="ready",
        title="Short title",
        description="Local description",
        tags="one, two",
    )

    item = db.get_workspace_item("segment", segment_id)
    assert item is not None
    assert item["workspace_status"] == "ready"
    assert item["title"] == "Short title"
    assert item["description"] == "Local description"
    assert item["tags"] == "one, two"
    assert [row["id"] for row in db.list_workspace_items(status="ready")] == [f"segment:{segment_id}"]


def test_workspace_derives_clip_status_from_render_state(video_in_db, tmp_path):
    from shortsfarm import db
    mid = db.insert_mark(video_in_db, None, 0.0, 10.0)
    clip_id = db.insert_clip(video_in_db, mid)
    db.set_clip_done(clip_id, str(tmp_path / "clip.mp4"))

    item = db.get_workspace_item("clip", clip_id)

    assert item is not None
    assert item["item_type"] == "clip"
    assert item["workspace_status"] == "ready"
    assert item["render_status"] == "done"


# ---------------------------------------------------------------------------
# social_accounts / OAuth state
# ---------------------------------------------------------------------------

def test_save_social_account_keeps_existing_refresh_token_when_missing():
    from shortsfarm import db

    account_id = db.save_social_account(
        platform="youtube",
        display_name="Old",
        channel_id="channel-1",
        channel_title="Old Channel",
        access_token="access-old",
        refresh_token="old",
        token_expires_at=None,
        scopes="scope-a",
        status="active",
    )

    updated_id = db.save_social_account(
        platform="youtube",
        display_name="New",
        channel_id="channel-1",
        channel_title="New Channel",
        access_token="access-new",
        refresh_token=None,
        token_expires_at=None,
        scopes="scope-b",
        status="active",
    )

    rows = db.list_social_accounts(platform="youtube")
    assert updated_id == account_id
    assert len(rows) == 1
    assert rows[0]["display_name"] == "New"
    assert rows[0]["access_token"] == "access-new"
    assert rows[0]["refresh_token"] == "old"


def test_save_social_account_replaces_refresh_token_when_present():
    from shortsfarm import db

    db.save_social_account(
        platform="youtube",
        display_name="Old",
        channel_id="channel-1",
        channel_title="Old Channel",
        access_token="access-old",
        refresh_token="old",
        token_expires_at=None,
        scopes="scope-a",
        status="active",
    )

    db.save_social_account(
        platform="youtube",
        display_name="New",
        channel_id="channel-1",
        channel_title="New Channel",
        access_token="access-new",
        refresh_token="new",
        token_expires_at=None,
        scopes="scope-b",
        status="active",
    )

    rows = db.list_social_accounts(platform="youtube")
    assert len(rows) == 1
    assert rows[0]["refresh_token"] == "new"


def test_oauth_state_is_single_use():
    from shortsfarm import db

    db.create_oauth_state("youtube", "state-1", oauth_profile_id=7)

    consumed = db.consume_oauth_state("youtube", "state-1")
    assert consumed is not None
    assert consumed["oauth_profile_id"] == 7
    assert db.consume_oauth_state("youtube", "state-1") is None
    assert db.consume_oauth_state("youtube", "missing") is None


def test_app_settings_save_read_mask_and_delete():
    from shortsfarm import db

    db.set_setting("youtube_client_id", "client-id")
    db.set_setting("youtube_client_secret", "secret-value", is_secret=True)

    assert db.get_setting("youtube_client_id") == "client-id"
    assert db.get_setting("youtube_client_secret") == "secret-value"
    assert db.get_setting("missing", default="fallback") == "fallback"

    settings = {item["key"]: item for item in db.list_settings(mask_secrets=True)}
    assert settings["youtube_client_id"]["value"] == "client-id"
    assert settings["youtube_client_secret"]["value"] == "********"
    assert settings["youtube_client_secret"]["is_secret"] is True

    assert db.delete_setting("youtube_client_secret") is True
    assert db.get_setting("youtube_client_secret") is None


def test_create_multiple_youtube_oauth_profiles_and_set_default():
    from shortsfarm import db

    profile_one = db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    profile_two = db.create_youtube_oauth_profile(
        name="Profile Two",
        mode="custom",
        client_id="client-2",
        client_secret="secret-2",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
    )

    assert db.get_default_youtube_oauth_profile()["id"] == profile_one
    rows = db.list_youtube_oauth_profiles()
    assert [int(row["id"]) for row in rows] == [profile_one, profile_two]

    assert db.set_default_youtube_oauth_profile(profile_two) is True
    assert db.get_default_youtube_oauth_profile()["id"] == profile_two


def test_bootstrap_legacy_youtube_oauth_profile_from_settings():
    from shortsfarm import db

    db.set_setting("youtube_client_id", "legacy-client")
    db.set_setting("youtube_client_secret", "legacy-secret", is_secret=True)
    db.set_setting("youtube_redirect_uri", "http://127.0.0.1:8000/api/publish/youtube/oauth/callback")

    row = db.bootstrap_legacy_youtube_oauth_profile()

    assert row is not None
    assert row["mode"] == "custom"
    assert row["is_default"] == 1
    assert row["name"] == "Legacy YouTube OAuth"
    assert len(db.list_youtube_oauth_profiles()) == 1
    assert db.bootstrap_legacy_youtube_oauth_profile()["id"] == row["id"]


def test_delete_profile_with_active_channels_is_blocked():
    from shortsfarm import db

    profile_id = db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    db.save_social_account(
        platform="youtube",
        display_name="Channel",
        channel_id="channel-1",
        channel_title="Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="scope-a",
        oauth_profile_id=profile_id,
        status="active",
    )

    with pytest.raises(ValueError, match="активными YouTube-каналами"):
        db.delete_youtube_oauth_profile(profile_id)


def test_claim_next_publish_job_sets_uploading_atomically(tmp_path):
    from shortsfarm import db

    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    video_id = db.add_video(source, "video", 60.0)
    clip_id = db.insert_clip(video_id, None)
    account_id = db.save_social_account(
        platform="youtube",
        display_name="Channel",
        channel_id="channel-1",
        channel_title="Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="scope-a",
        status="active",
    )
    job_id = db.create_publish_job(
        account_id=account_id,
        clip_id=clip_id,
        title="Upload",
        description="Desc",
        tags='["tag"]',
        category_id="22",
        privacy_status="private",
        publish_mode="private",
        publish_at=None,
        made_for_kids=False,
    )

    claimed = db.claim_next_publish_job()

    assert claimed is not None
    assert int(claimed["id"]) == job_id
    assert claimed["status"] == "uploading"
    assert int(claimed["attempt_count"]) == 1
    assert db.claim_next_publish_job() is None
    assert db.get_publish_job(job_id)["status"] == "uploading"
