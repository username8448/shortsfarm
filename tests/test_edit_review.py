"""Tests for local review of rendered edit jobs."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse


def _done_edit_job(
    *,
    name: str = "review",
    write_file: bool = True,
    media_path: Path | None = None,
) -> tuple[int, Path]:
    from shortsfarm import db
    from shortsfarm.config import output_dir

    path = media_path or (output_dir() / "edited" / "reaction_top_25" / f"{name}.mp4")
    if write_file:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake edited video")
    job_id = db.create_edit_job(
        workspace_item_key="segment:1",
        output_path=str(path),
    )
    db.mark_edit_job_done(job_id, str(path))
    return job_id, path.resolve()


def test_new_edit_job_review_status_is_pending():
    from shortsfarm import db

    job_id = db.create_edit_job(workspace_item_key="segment:10")
    job = db.get_edit_job(job_id)

    assert job["review_status"] == "pending"
    assert job["reviewed_at"] is None
    assert job["review_note"] is None


def test_approve_and_reject_done_edit_jobs_save_review_data():
    from shortsfarm import db

    approved_id, _ = _done_edit_job(name="approved")
    rejected_id, _ = _done_edit_job(name="rejected")

    assert db.set_edit_job_review_status(approved_id, "approved", "looks good")
    approved = db.get_edit_job(approved_id)
    assert approved["review_status"] == "approved"
    assert approved["reviewed_at"] is not None
    assert approved["review_note"] == "looks good"

    assert db.set_edit_job_review_status(rejected_id, "rejected", "wrong reaction")
    rejected = db.get_edit_job(rejected_id)
    assert rejected["review_status"] == "rejected"
    assert rejected["reviewed_at"] is not None
    assert rejected["review_note"] == "wrong reaction"

    assert db.set_edit_job_review_status(rejected_id, "pending")
    reset = db.get_edit_job(rejected_id)
    assert reset["review_status"] == "pending"
    assert reset["reviewed_at"] is None
    assert reset["review_note"] is None


def test_approve_queued_edit_job_is_forbidden():
    from shortsfarm import db

    job_id = db.create_edit_job(workspace_item_key="segment:11")

    with pytest.raises(ValueError, match="status=done"):
        db.set_edit_job_review_status(job_id, "approved")

    assert db.get_edit_job(job_id)["review_status"] == "pending"


def test_media_endpoint_returns_file_response_for_done_job():
    from shortsfarm.web import api

    job_id, media_path = _done_edit_job(name="media")

    response = api.editing_job_media(job_id)

    assert isinstance(response, FileResponse)
    assert Path(response.path) == media_path
    assert response.media_type == "video/mp4"


def test_media_endpoint_rejects_path_outside_edited(tmp_path):
    from shortsfarm.web import api

    outside = tmp_path / "outside.mp4"
    job_id, _ = _done_edit_job(media_path=outside)

    with pytest.raises(HTTPException) as exc:
        api.editing_job_media(job_id)

    assert exc.value.status_code == 403
    assert "внутри" in exc.value.detail["message"]


def test_media_endpoint_rejects_missing_file():
    from shortsfarm.web import api

    job_id, _ = _done_edit_job(name="missing", write_file=False)

    with pytest.raises(HTTPException) as exc:
        api.editing_job_media(job_id)

    assert exc.value.status_code == 404
    assert "не найден" in exc.value.detail["message"]


def test_media_endpoint_rejects_job_not_done():
    from shortsfarm import db
    from shortsfarm.web import api

    job_id = db.create_edit_job(workspace_item_key="segment:12")

    with pytest.raises(HTTPException) as exc:
        api.editing_job_media(job_id)

    assert exc.value.status_code == 400
    assert "ещё не готов" in exc.value.detail["message"]


def test_open_endpoint_launches_mpv_without_waiting(monkeypatch):
    from shortsfarm.web import api

    job_id, media_path = _done_edit_job(name="mpv")
    popen = MagicMock()
    monkeypatch.setattr(api, "require_mpv", lambda: "/usr/bin/mpv")
    monkeypatch.setattr(api.subprocess, "Popen", popen)

    result = api.editing_job_open(job_id)

    assert result == {"status": "opened", "job_id": job_id, "player": "mpv"}
    popen.assert_called_once_with(
        ["/usr/bin/mpv", str(media_path)],
        stdout=api.subprocess.DEVNULL,
        stderr=api.subprocess.DEVNULL,
        start_new_session=True,
    )


def test_review_api_and_job_list_include_review_fields():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobReviewRequest

    job_id, _ = _done_edit_job(name="api-review")

    approved = api.editing_job_approve(
        job_id,
        EditJobReviewRequest(note="approved manually"),
    )
    assert approved["status"] == "ok"
    assert approved["job"]["review_status"] == "approved"
    assert approved["job"]["reviewed_at"] is not None
    assert approved["job"]["review_note"] == "approved manually"

    listed = api.editing_jobs(review_status="approved")
    assert [item["id"] for item in listed["items"]] == [job_id]
    assert listed["items"][0]["review_status"] == "approved"

    rejected = api.editing_job_reject(
        job_id,
        EditJobReviewRequest(note="change reaction"),
    )
    assert rejected["job"]["review_status"] == "rejected"
    assert rejected["job"]["review_note"] == "change reaction"

    reset = api.editing_job_reset_review(job_id)
    assert reset["job"]["review_status"] == "pending"
    assert reset["job"]["reviewed_at"] is None
    assert reset["job"]["review_note"] is None
