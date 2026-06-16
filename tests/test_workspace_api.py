from __future__ import annotations


def _make_segment(video_in_db: int, tmp_path):
    from shortsfarm import db
    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    path = tmp_path / "segment-001.mp4"
    path.write_bytes(b"segment")
    return db.insert_segment(video_in_db, job_id, 1, 0.0, 15.0, path)


def test_workspace_api_lists_segments_even_without_clips(video_in_db, tmp_path):
    from shortsfarm.web import api
    segment_id = _make_segment(video_in_db, tmp_path)

    data = api.workspace_clips()

    ids = {item["id"] for item in data["items"]}
    assert f"segment:{segment_id}" in ids
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
