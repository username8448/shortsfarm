from __future__ import annotations

import pytest
from fastapi import HTTPException
from pathlib import Path


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
