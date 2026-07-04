from __future__ import annotations

import pytest
from fastapi import HTTPException
from pathlib import Path
from types import SimpleNamespace


def _make_segment(video_in_db: int, tmp_path, *, exists: bool = True, under_output: bool = False):
    from shortsfarm import db
    from shortsfarm.config import output_dir
    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    folder = output_dir() / "split" / "test" if under_output else tmp_path
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"segment-{job_id}.mp4"
    if exists:
        path.write_bytes(b"segment")
    return db.insert_segment(video_in_db, job_id, 1, 0.0, 15.0, path)


def _make_youtube_account() -> int:
    from shortsfarm import db
    profile_id = db.create_youtube_oauth_profile(
        name="Profile",
        mode="custom",
        client_id="client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
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
        status="active",
    )


def _make_clip(video_in_db: int, tmp_path, *, exists: bool = True):
    from shortsfarm import db
    mark_id = db.insert_mark(video_in_db, None, 3.0, 23.0)
    clip_id = db.insert_clip(video_in_db, mark_id)
    path = tmp_path / f"clip-{clip_id}.mp4"
    if exists:
        path.write_bytes(b"clip")
    db.set_clip_done(clip_id, str(path))
    return clip_id


def _patch_prepare_ffmpeg(monkeypatch):
    def fake_run(cmd, text, stdout, stderr):
        Path(cmd[-1]).write_bytes(b"prepared")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shortsfarm.prepare_video.require_binary", lambda name: "ffmpeg")
    monkeypatch.setattr("shortsfarm.prepare_video.subprocess.run", fake_run)


def test_workspace_api_lists_segments_even_without_clips(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clips()

    item = next(row for row in data["items"] if row["id"] == f"segment:{segment_id}")
    assert item["file_exists"] is True
    assert item["folder_exists"] is True
    assert item["missing"] is False
    assert data["counts"]["draft"] == 1


def test_workspace_api_patch_updates_local_metadata(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest
    segment_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clip_update(
        f"segment:{segment_id}",
        WorkspaceItemUpdateRequest(
            workspace_status="ready",
            title="Local title",
            description="Local description",
            tags="shorts, test",
        ),
    )

    item = data["item"]
    assert item["workspace_status"] == "ready"
    assert item["title"] == "Local title"
    assert item["description"] == "Local description"
    assert item["tags"] == "shorts, test"

    reloaded = api.workspace_clips()
    persisted = next(row for row in reloaded["items"] if row["id"] == f"segment:{segment_id}")
    assert persisted["title"] == "Local title"
    assert persisted["description"] == "Local description"
    assert persisted["tags"] == "shorts, test"


def test_workspace_api_includes_catalog_tags_for_workspace_video(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import CatalogVideoTagsRequest, TagCreateRequest, WorkspaceItemUpdateRequest
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "workspace")
    path = root / "cuts/raw/segment.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"segment")
    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    segment_id = db.insert_segment(video_in_db, job_id, 1, 0.0, 15.0, path)
    tag = api.tag_create(TagCreateRequest(name="аниме"))["tag"]
    api.catalog_video_tags_update(
        CatalogVideoTagsRequest(
            workspace_path="cuts/raw/segment.mp4",
            tag_ids=[tag["id"]],
        )
    )

    item = next(row for row in api.workspace_clips()["items"] if row["id"] == f"segment:{segment_id}")
    assert item["workspace_path"] == "cuts/raw/segment.mp4"
    assert {tag["slug"] for tag in item["catalog_tags"]} >= {"аниме", "status-draft"}

    updated = api.workspace_clip_update(
        f"segment:{segment_id}",
        WorkspaceItemUpdateRequest(workspace_status="ready"),
    )["item"]
    assert {tag["slug"] for tag in updated["catalog_tags"]} >= {"аниме", "status-ready"}


def test_workspace_api_patch_saves_target_aspect(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest
    segment_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clip_update(
        f"segment:{segment_id}",
        WorkspaceItemUpdateRequest(target_aspect="9x16"),
    )

    assert data["item"]["target_aspect"] == "9x16"


def test_workspace_api_status_is_reversible(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest
    segment_id = _make_segment(video_in_db, tmp_path)
    key = f"segment:{segment_id}"

    ready = api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(workspace_status="ready"),
    )["item"]
    draft = api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(workspace_status="draft"),
    )["item"]
    failed = api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(workspace_status="failed"),
    )["item"]

    assert ready["workspace_status"] == "ready"
    assert draft["workspace_status"] == "draft"
    assert failed["workspace_status"] == "failed"


def test_workspace_api_bulk_status(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceBulkStatusRequest
    first_id = _make_segment(video_in_db, tmp_path)
    second_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clips_bulk_status(
        WorkspaceBulkStatusRequest(
            items=[f"segment:{first_id}", f"segment:{second_id}"],
            workspace_status="ready",
        )
    )

    assert data["updated"] == 2
    ready_ids = {item["id"] for item in data["items"] if item["workspace_status"] == "ready"}
    assert {f"segment:{first_id}", f"segment:{second_id}"} <= ready_ids

    draft_data = api.workspace_clips_bulk_status(
        WorkspaceBulkStatusRequest(
            items=[f"segment:{first_id}", f"segment:{second_id}"],
            workspace_status="draft",
        )
    )
    draft_ids = {item["id"] for item in draft_data["items"] if item["workspace_status"] == "draft"}
    assert {f"segment:{first_id}", f"segment:{second_id}"} <= draft_ids


def test_workspace_api_marks_missing_file(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path, exists=False)

    data = api.workspace_clips()

    item = next(row for row in data["items"] if row["id"] == f"segment:{segment_id}")
    assert item["file_exists"] is False
    assert item["folder_exists"] is True
    assert item["missing"] is True
    assert "Файл не найден" in item["path_error"]
    assert data["counts"]["missing"] == 1


def test_workspace_cleanup_missing_hides_missing_items(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path, exists=False)

    data = api.workspace_clips_cleanup_missing()

    assert data["summary"]["missing"] == 1
    assert data["summary"]["hidden"] == 1
    assert f"segment:{segment_id}" not in {item["id"] for item in data["items"]}
    assert f"segment:{segment_id}" not in {item["id"] for item in api.workspace_clips()["items"]}


def test_workspace_delete_existing_file_removes_file_and_hides_item(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path, exists=True, under_output=True)
    item = db.get_workspace_item("segment", segment_id)
    path = item["path"]

    data = api.workspace_clip_delete(f"segment:{segment_id}")

    assert data["result"]["file_deleted"] is True
    assert data["result"]["hidden"] is True
    assert not Path(path).exists()
    assert f"segment:{segment_id}" not in {row["id"] for row in data["items"]}


def test_workspace_delete_missing_item_does_not_fail(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path, exists=False)

    data = api.workspace_clip_delete(f"segment:{segment_id}")

    assert data["result"]["already_missing"] is True
    assert data["result"]["hidden"] is True


def test_workspace_bulk_delete_summary(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceBulkDeleteRequest
    existing_id = _make_segment(video_in_db, tmp_path, exists=True, under_output=True)
    missing_id = _make_segment(video_in_db, tmp_path, exists=False, under_output=True)

    data = api.workspace_clips_bulk_delete(
        WorkspaceBulkDeleteRequest(items=[f"segment:{existing_id}", f"segment:{missing_id}"])
    )

    assert data["summary"]["deleted_files"] == 1
    assert data["summary"]["already_missing"] == 1
    assert data["summary"]["errors"] == 0


def test_workspace_delete_file_outside_output_is_forbidden(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path, exists=True, under_output=False)

    with pytest.raises(HTTPException) as exc:
        api.workspace_clip_delete(f"segment:{segment_id}")

    assert exc.value.status_code == 403


def test_workspace_prepare_segment_9x16_creates_prepared_path(monkeypatch, video_in_db, tmp_path):
    from shortsfarm.config import output_dir
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    _patch_prepare_ffmpeg(monkeypatch)
    segment_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clip_prepare(
        f"segment:{segment_id}",
        WorkspacePrepareRequest(target_aspect="9x16"),
    )

    item = data["item"]
    prepared_path = Path(item["prepared_path"])
    assert item["target_aspect"] == "9x16"
    assert item["prepare_status"] == "done"
    assert prepared_path.exists()
    assert output_dir() / "prepared" / "9x16" in prepared_path.parents


def test_workspace_prepare_clip_16x9_creates_prepared_path(monkeypatch, video_in_db, tmp_path):
    from shortsfarm.config import output_dir
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    _patch_prepare_ffmpeg(monkeypatch)
    clip_id = _make_clip(video_in_db, tmp_path)

    data = api.workspace_clip_prepare(
        f"clip:{clip_id}",
        WorkspacePrepareRequest(target_aspect="16x9"),
    )

    item = data["item"]
    prepared_path = Path(item["prepared_path"])
    assert item["target_aspect"] == "16x9"
    assert item["prepare_status"] == "done"
    assert prepared_path.exists()
    assert output_dir() / "prepared" / "16x9" in prepared_path.parents


def test_workspace_prepare_managed_segment_uses_prepared_tree(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "managed-prepare")
    source = (
        root / "sources" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original.mp4"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    video_id = db.add_video(source, "original", 120.0)
    job_id = db.create_job(video_id, "fast", 60)
    db.mark_job_done(job_id)
    segment_path = (
        root / "cuts" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "original" / "run-001" / "segment_0001.mp4"
    )
    segment_path.parent.mkdir(parents=True)
    segment_path.write_bytes(b"segment")
    segment_id = db.insert_segment(
        video_id,
        job_id,
        1,
        0.0,
        15.0,
        segment_path,
    )
    _patch_prepare_ffmpeg(monkeypatch)

    data = api.workspace_clip_prepare(
        f"segment:{segment_id}",
        WorkspacePrepareRequest(target_aspect="9x16"),
    )

    prepared = Path(data["item"]["prepared_path"])
    expected_folder = (
        root / "prepared" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "9x16"
    )
    assert prepared.parent == expected_folder
    assert prepared.exists()


def test_workspace_prepare_managed_clip_uses_source_lineage(monkeypatch, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "managed-clip-prepare")
    source = root / "sources" / "Channel" / "Episode" / "original.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    video_id = db.add_video(source, "original", 90.0)
    mark_id = db.insert_mark(video_id, None, 5.0, 25.0)
    clip_id = db.insert_clip(video_id, mark_id)
    clip_path = tmp_path / "rendered-clip.mp4"
    clip_path.write_bytes(b"clip")
    db.set_clip_done(clip_id, str(clip_path))
    _patch_prepare_ffmpeg(monkeypatch)

    data = api.workspace_clip_prepare(
        f"clip:{clip_id}",
        WorkspacePrepareRequest(target_aspect="16x9"),
    )

    prepared = Path(data["item"]["prepared_path"])
    assert prepared.parent == (
        root / "prepared" / "Channel" / "Episode"
        / "original" / "16x9"
    )
    assert prepared.exists()


def test_workspace_prepare_repeated_uses_same_path(monkeypatch, video_in_db, tmp_path):
    from shortsfarm.config import output_dir
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    _patch_prepare_ffmpeg(monkeypatch)
    segment_id = _make_segment(video_in_db, tmp_path)
    key = f"segment:{segment_id}"

    first = api.workspace_clip_prepare(key, WorkspacePrepareRequest(target_aspect="9x16"))["item"]
    second = api.workspace_clip_prepare(key, WorkspacePrepareRequest(target_aspect="9x16"))["item"]

    assert second["prepared_path"] == first["prepared_path"]
    prepared_files = list((output_dir() / "prepared" / "9x16").glob("*.mp4"))
    assert prepared_files == [Path(first["prepared_path"])]


def test_workspace_prepare_missing_item_is_rejected(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    segment_id = _make_segment(video_in_db, tmp_path, exists=False)

    with pytest.raises(HTTPException) as exc:
        api.workspace_clip_prepare(
            f"segment:{segment_id}",
            WorkspacePrepareRequest(target_aspect="9x16"),
        )

    assert exc.value.status_code == 404


def test_workspace_prepare_invalid_target_aspect(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspacePrepareRequest
    segment_id = _make_segment(video_in_db, tmp_path)

    with pytest.raises(HTTPException) as exc:
        api.workspace_clip_prepare(
            f"segment:{segment_id}",
            WorkspacePrepareRequest(target_aspect="1x1"),
        )

    assert exc.value.status_code == 400


def test_workspace_youtube_enqueue_segment_creates_publish_clip(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    account_id = _make_youtube_account()
    segment_id = _make_segment(video_in_db, tmp_path)
    api.workspace_clip_update(
        f"segment:{segment_id}",
        WorkspaceItemUpdateRequest(
            workspace_status="ready",
            title="Workspace title",
            description="Workspace description",
            tags="one, two",
        ),
    )

    data = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(
            item_keys=[f"segment:{segment_id}"],
            account_id=account_id,
            publish_mode="private",
            category_id="22",
            made_for_kids=False,
        )
    )

    assert data["created"] == 1
    assert data["skipped"] == 0
    item = data["items"][0]
    assert item["item_key"] == f"segment:{segment_id}"
    assert item["status"] == "queued"
    clip = db.get_clip(item["clip_id"])
    assert int(clip["source_segment_id"]) == segment_id
    assert clip["status"] == "done"
    job = db.get_publish_job(item["job_id"])
    assert job["status"] == "queued"
    assert job["title"] == "Workspace title"
    assert job["description"] == "Workspace description"
    assert '"one"' in job["tags"]
    assert db.get_workspace_item("segment", segment_id)["workspace_status"] == "queued"
    assert f"clip:{item['clip_id']}" not in {row["id"] for row in db.list_workspace_items()}


def test_workspace_youtube_enqueue_uses_prepared_segment_path(monkeypatch, video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    _patch_prepare_ffmpeg(monkeypatch)
    account_id = _make_youtube_account()
    segment_id = _make_segment(video_in_db, tmp_path)
    key = f"segment:{segment_id}"
    api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(workspace_status="ready", target_aspect="9x16"),
    )

    data = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[key], account_id=account_id)
    )

    assert data["prepared"] == 1
    item = data["items"][0]
    clip = db.get_clip(item["clip_id"])
    workspace_item = db.get_workspace_item("segment", segment_id)
    assert workspace_item["prepare_status"] == "done"
    assert Path(workspace_item["prepared_path"]).exists()
    assert clip["output_path"] == workspace_item["prepared_path"]
    assert "/prepared/9x16/" in clip["output_path"]
    assert clip["source_aspect"] == "9x16"


def test_workspace_youtube_enqueue_prepared_clip_uses_service_variant(monkeypatch, video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    _patch_prepare_ffmpeg(monkeypatch)
    account_id = _make_youtube_account()
    clip_id = _make_clip(video_in_db, tmp_path)
    key = f"clip:{clip_id}"
    api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(workspace_status="ready", target_aspect="16x9"),
    )

    data = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[key], account_id=account_id)
    )

    assert data["prepared"] == 1
    item = data["items"][0]
    service_clip = db.get_clip(item["clip_id"])
    workspace_item = db.get_workspace_item("clip", clip_id)
    assert int(item["clip_id"]) != clip_id
    assert int(service_clip["source_clip_id"]) == clip_id
    assert service_clip["source_aspect"] == "16x9"
    assert service_clip["output_path"] == workspace_item["prepared_path"]
    assert workspace_item["publish_job_id"] == item["job_id"]
    assert workspace_item["publish_job_status"] == "queued"
    assert f"clip:{item['clip_id']}" not in {row["id"] for row in db.list_workspace_items()}


def test_workspace_youtube_enqueue_updates_existing_queued_job_metadata(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    account_id = _make_youtube_account()
    segment_id = _make_segment(video_in_db, tmp_path)
    key = f"segment:{segment_id}"

    api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(
            workspace_status="ready",
            title="Old title",
            description="Old description",
        ),
    )
    first = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[key], account_id=account_id)
    )["items"][0]

    api.workspace_clip_update(
        key,
        WorkspaceItemUpdateRequest(
            title="New title",
            description="New description",
            tags="fresh, tags",
        ),
    )
    second = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[key], account_id=account_id)
    )["items"][0]

    assert second["job_id"] == first["job_id"]
    job = db.get_publish_job(second["job_id"])
    assert job["title"] == "New title"
    assert job["description"] == "New description"
    assert '"fresh"' in job["tags"]


def test_workspace_youtube_enqueue_skips_missing_and_not_ready(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    account_id = _make_youtube_account()
    draft_id = _make_segment(video_in_db, tmp_path, exists=True)
    missing_id = _make_segment(video_in_db, tmp_path, exists=False)
    api.workspace_clip_update(
        f"segment:{missing_id}",
        WorkspaceItemUpdateRequest(workspace_status="ready"),
    )

    data = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(
            item_keys=[f"segment:{draft_id}", f"segment:{missing_id}"],
            account_id=account_id,
        )
    )

    assert data["created"] == 0
    assert data["skipped"] == 2
    reasons = {item["item_key"]: item["reason"] for item in data["skipped_items"]}
    assert "Готово" in reasons[f"segment:{draft_id}"]
    assert "Файл отсутствует" in reasons[f"segment:{missing_id}"]


def test_publish_done_and_failed_update_workspace_segment_status(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    account_id = _make_youtube_account()
    first_id = _make_segment(video_in_db, tmp_path)
    second_id = _make_segment(video_in_db, tmp_path)
    for segment_id in (first_id, second_id):
        api.workspace_clip_update(
            f"segment:{segment_id}",
            WorkspaceItemUpdateRequest(workspace_status="ready"),
        )

    first = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[f"segment:{first_id}"], account_id=account_id)
    )["items"][0]
    second = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[f"segment:{second_id}"], account_id=account_id)
    )["items"][0]

    db.mark_publish_done(first["job_id"], "yt-1", "https://www.youtube.com/watch?v=yt-1")
    db.mark_publish_failed(second["job_id"], "upload failed", retryable=False)

    assert db.get_workspace_item("segment", first_id)["workspace_status"] == "uploaded"
    assert db.get_workspace_item("segment", second_id)["workspace_status"] == "failed"


def test_publish_cancel_and_retry_update_workspace_segment_status(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceItemUpdateRequest, WorkspaceYouTubeEnqueueRequest
    account_id = _make_youtube_account()
    segment_id = _make_segment(video_in_db, tmp_path)
    api.workspace_clip_update(
        f"segment:{segment_id}",
        WorkspaceItemUpdateRequest(workspace_status="ready"),
    )
    item = api.workspace_clips_youtube_enqueue(
        WorkspaceYouTubeEnqueueRequest(item_keys=[f"segment:{segment_id}"], account_id=account_id)
    )["items"][0]

    api.publish_job_cancel(item["job_id"])
    assert db.get_workspace_item("segment", segment_id)["workspace_status"] == "draft"

    api.publish_job_retry(item["job_id"], None)
    workspace_item = db.get_workspace_item("segment", segment_id)
    assert workspace_item["workspace_status"] == "queued"
    assert workspace_item["publish_job_id"] == item["job_id"]
    assert workspace_item["publish_job_status"] == "queued"
