from __future__ import annotations

from pathlib import Path

import pytest


def _make_profile(name: str = "Profile", *, is_default: bool = True) -> int:
    from shortsfarm import db

    return db.create_youtube_oauth_profile(
        name=name,
        mode="custom",
        client_id=f"{name}-client",
        client_secret=f"{name}-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=is_default,
    )


def _make_done_clip(tmp_path: Path, *, output_exists: bool = True) -> tuple[int, Path]:
    from shortsfarm import db

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    video_id = db.add_video(source, "source", 60.0)
    mark_id = db.insert_mark(video_id, None, 0.0, 10.0)
    clip_id = db.insert_clip(video_id, mark_id)
    output = tmp_path / "clip.mp4"
    if output_exists:
        output.write_bytes(b"clip")
    db.set_clip_done(clip_id, str(output))
    return clip_id, output


def _make_queued_clip(tmp_path: Path) -> int:
    from shortsfarm import db

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    video_id = db.add_video(source, "source", 60.0)
    mark_id = db.insert_mark(video_id, None, 0.0, 10.0)
    return db.insert_clip(video_id, mark_id)


def _make_account(*, status: str = "active", oauth_profile_id: int | None = None) -> int:
    from shortsfarm import db

    profile_id = oauth_profile_id if oauth_profile_id is not None else _make_profile()
    return db.save_social_account(
        platform="youtube",
        display_name="Account",
        channel_id="channel-1",
        channel_title="Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=profile_id,
        status=status,
    )


def _make_job(
    *,
    account_id: int,
    clip_id: int,
    publish_mode: str = "private",
    publish_at: str | None = None,
    title: str = "Test",
) -> int:
    from shortsfarm import db
    from shortsfarm.publish_youtube import validate_publish_options

    validated = validate_publish_options(
        title=title,
        publish_mode=publish_mode,
        publish_at=publish_at,
        category_id="22",
    )
    return db.create_publish_job(
        account_id=account_id,
        clip_id=clip_id,
        title=title,
        description="Description",
        tags='["one","two"]',
        category_id="22",
        privacy_status=str(validated["privacy_status"]),
        publish_mode=publish_mode,
        publish_at=validated["publish_at"],
        made_for_kids=False,
    )


def _patch_success_upload(monkeypatch: pytest.MonkeyPatch, capture: dict) -> None:
    from shortsfarm import publish_youtube

    class Request:
        def next_chunk(self):
            return None, {"id": "yt123"}

    class Videos:
        def insert(self, *, part, body, media_body):
            capture["part"] = part
            capture["body"] = body
            capture["media_body"] = media_body
            return Request()

    class YouTube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())
    monkeypatch.setattr(publish_youtube, "_media_file_upload", lambda path: f"media:{path}")


def test_upload_rejects_clip_not_done(tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    account_id = _make_account()
    clip_id = _make_queued_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(ValueError, match="clips.status='done'"):
        upload_clip_to_youtube(job_id)

    assert db.get_publish_job(job_id)["status"] == "failed"


def test_upload_rejects_empty_output_path(tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    account_id = _make_account()
    clip_id = _make_queued_clip(tmp_path)
    with db.connect() as con:
        con.execute("UPDATE clips SET status='done', output_path=NULL WHERE id=?", (clip_id,))
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(ValueError, match="output_path"):
        upload_clip_to_youtube(job_id)

    assert db.get_publish_job(job_id)["status"] == "failed"


def test_upload_rejects_missing_output_file(tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path, output_exists=False)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(FileNotFoundError):
        upload_clip_to_youtube(job_id)

    assert db.get_publish_job(job_id)["status"] == "failed"


def test_upload_rejects_inactive_account(tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    account_id = _make_account(status="disconnected")
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(ValueError, match="не активен"):
        upload_clip_to_youtube(job_id)

    assert db.get_publish_job(job_id)["status"] == "failed"


def test_create_publish_job_does_not_duplicate_clip_account(tmp_path):
    from shortsfarm import db

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    first = _make_job(account_id=account_id, clip_id=clip_id)
    second = _make_job(account_id=account_id, clip_id=clip_id)

    assert second == first
    assert len(db.list_publish_jobs()) == 1


def test_create_publish_job_requeues_failed_job(tmp_path):
    from shortsfarm import db

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id, title="Old title")
    db.mark_publish_failed(job_id, "old error", retryable=False)

    second_id = _make_job(account_id=account_id, clip_id=clip_id, title="New title")
    job = db.get_publish_job(second_id)

    assert second_id == job_id
    assert job["status"] == "queued"
    assert job["title"] == "New title"
    assert job["error"] is None


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("private", "private"),
        ("unlisted", "unlisted"),
        ("public", "public"),
    ],
)
def test_upload_privacy_modes(monkeypatch, tmp_path, mode, expected):
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    capture: dict = {}
    _patch_success_upload(monkeypatch, capture)
    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id, publish_mode=mode)

    job = upload_clip_to_youtube(job_id)

    assert capture["body"]["status"]["privacyStatus"] == expected
    assert "publishAt" not in capture["body"]["status"]
    assert job["youtube_video_id"] == "yt123"
    assert job["youtube_url"] == "https://www.youtube.com/watch?v=yt123"


def test_schedule_requires_publish_at():
    from shortsfarm.publish_youtube import validate_publish_options

    with pytest.raises(ValueError, match="publish_at обязателен"):
        validate_publish_options(title="Test", publish_mode="schedule", publish_at=None)


def test_schedule_sets_private_and_publish_at(monkeypatch, tmp_path):
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    capture: dict = {}
    _patch_success_upload(monkeypatch, capture)
    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(
        account_id=account_id,
        clip_id=clip_id,
        publish_mode="schedule",
        publish_at="2026-06-15T18:00:00Z",
    )

    upload_clip_to_youtube(job_id)

    assert capture["body"]["status"]["privacyStatus"] == "private"
    assert capture["body"]["status"]["publishAt"] == "2026-06-15T18:00:00Z"


def test_upload_success_saves_youtube_fields(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    capture: dict = {}
    _patch_success_upload(monkeypatch, capture)
    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    upload_clip_to_youtube(job_id)
    job = db.get_publish_job(job_id)

    assert job["status"] == "done"
    assert job["youtube_video_id"] == "yt123"
    assert job["youtube_url"] == "https://www.youtube.com/watch?v=yt123"


def test_upload_failure_marks_job_failed(monkeypatch, tmp_path):
    from shortsfarm import db, publish_youtube
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    class Request:
        def next_chunk(self):
            raise RuntimeError("upload boom")

    class Videos:
        def insert(self, *, part, body, media_body):
            return Request()

    class YouTube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())
    monkeypatch.setattr(publish_youtube, "_media_file_upload", lambda path: f"media:{path}")
    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(RuntimeError, match="upload boom"):
        upload_clip_to_youtube(job_id)

    job = db.get_publish_job(job_id)
    assert job["status"] == "failed"
    assert "upload boom" in job["error"]


def test_transient_upload_failure_sets_next_attempt_at(monkeypatch, tmp_path):
    from shortsfarm import db, publish_youtube
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    class RetryableError(Exception):
        def __init__(self):
            self.resp = type("Resp", (), {"status": 503})()
            super().__init__("server unavailable")

    class Request:
        def next_chunk(self):
            raise RetryableError()

    class Videos:
        def insert(self, *, part, body, media_body):
            return Request()

    class YouTube:
        def videos(self):
            return Videos()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())
    monkeypatch.setattr(publish_youtube, "_media_file_upload", lambda path: f"media:{path}")
    monkeypatch.setattr(publish_youtube, "_sleep", lambda seconds: None)

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    with pytest.raises(RetryableError):
        upload_clip_to_youtube(job_id)

    job = db.get_publish_job(job_id)
    assert job["status"] == "failed"
    assert int(job["attempt_count"]) == 1
    assert job["next_attempt_at"] is not None


def test_enqueue_clip_creates_queued_job(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeUploadRequest

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)

    result = api.youtube_enqueue_clip(
        clip_id,
        YouTubeUploadRequest(
            account_id=account_id,
            title="Queued upload",
            description="Desc",
            tags=["a", "b"],
            category_id="22",
            publish_mode="private",
            publish_at=None,
            made_for_kids=False,
        ),
    )

    assert result["job"]["status"] == "queued"
    assert result["job"]["channel_title"] == "Channel"
    assert result["job"]["profile_name"] == "Profile"


def test_publish_jobs_api_returns_context(tmp_path):
    from shortsfarm.web import api

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    _make_job(account_id=account_id, clip_id=clip_id)

    result = api.publish_jobs()

    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["channel_title"] == "Channel"
    assert job["profile_name"] == "Profile"
    assert job["video_title"] == "source"


def test_retry_failed_job_requeues_job(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)
    db.mark_publish_failed(job_id, "old error", retryable=False)

    result = api.publish_job_retry(job_id, None)

    assert result["job"]["status"] == "queued"
    assert db.get_publish_job(job_id)["status"] == "queued"


def test_cancel_publish_job_marks_cancelled(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    result = api.publish_job_cancel(job_id)

    assert result["job"]["status"] == "cancelled"
    assert db.get_publish_job(job_id)["status"] == "cancelled"


def test_publish_job_run_calls_upload(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    def fake_run(job_id_arg: int):
        db.mark_publish_done(job_id_arg, "yt-run", "https://www.youtube.com/watch?v=yt-run")
        return db.get_publish_job(job_id_arg)

    monkeypatch.setattr(api, "run_publish_job_now", fake_run)

    result = api.publish_job_run(job_id)

    assert result["job"]["status"] == "done"
    assert result["youtube_url"] == "https://www.youtube.com/watch?v=yt-run"


def test_worker_once_processes_queued_job(monkeypatch, tmp_path):
    from shortsfarm import db, publish_youtube

    account_id = _make_account()
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    def fake_upload(job):
        db.mark_publish_done(int(job["id"]), "yt-worker", "https://www.youtube.com/watch?v=yt-worker")
        return db.get_publish_job(int(job["id"]))

    monkeypatch.setattr(publish_youtube, "_upload_claimed_job", fake_upload)
    monkeypatch.setattr(publish_youtube, "_sleep", lambda seconds: None)

    jobs = publish_youtube.run_publish_queue_once(limit=1, pause_seconds=0)

    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["status"] == "done"


def test_old_account_without_profile_can_use_default_profile(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm.publish_youtube import upload_clip_to_youtube

    _make_profile()
    capture: dict = {}
    _patch_success_upload(monkeypatch, capture)
    account_id = db.save_social_account(
        platform="youtube",
        display_name="Legacy Account",
        channel_id="legacy-channel",
        channel_title="Legacy Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=None,
        status="active",
    )
    clip_id, _output = _make_done_clip(tmp_path)
    job_id = _make_job(account_id=account_id, clip_id=clip_id)

    job = upload_clip_to_youtube(job_id)

    assert job["status"] == "done"
    assert capture["body"]["snippet"]["title"] == "Test"
