from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(path: str = "/api/shorts-pipeline/runs") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    })


def _workspace(tmp_path: Path) -> Path:
    from shortsfarm.workspace_fs import set_workspace_root

    return set_workspace_root(tmp_path / "workspace")


def _default_template_id() -> int:
    from shortsfarm import db
    from shortsfarm.studio_templates import ensure_default_studio_templates

    ensure_default_studio_templates()
    row = db.get_latest_studio_template_by_key("reaction_top_25")
    assert row is not None
    return int(row["id"])


def _request_body(template_id: int, **overrides):
    from shortsfarm.web.pipeline_api import ShortsPipelineRequest

    body = {
        "source_mode": "workspace",
        "source_paths": ["sources/main.mp4"],
        "split_seconds": 30,
        "studio_template_id": template_id,
        "reaction_strategy": "fixed_asset",
        "reaction_asset_id": 1,
    }
    body.update(overrides)
    return ShortsPipelineRequest(**body)


def test_shorts_pipeline_plan_validates_and_counts_segments(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    (root / "sources" / "main.mp4").write_bytes(b"video")
    reaction_path = tmp_path / "reaction.mp4"
    reaction_path.write_bytes(b"reaction")
    asset_id = db.create_reaction_asset(
        name="Pipeline Reaction",
        file_path=str(reaction_path),
    )
    tag_id = db.create_tag(name="anime", color="#ff00aa")

    def fake_split(path, **kwargs):
        assert kwargs["dry_run"] is True
        return SimpleNamespace(
            duration_sec=61.0,
            output_dir=root / "cuts" / "main" / "original" / "preview",
            segment_ranges=[(0.0, 30.0), (30.0, 60.0), (60.0, 61.0)],
        )

    monkeypatch.setattr(pipeline_api, "split_video_file", fake_split)
    monkeypatch.setattr(pipeline_api, "choose_reaction_asset", lambda **kwargs: 1)

    data = pipeline_api.shorts_pipeline_plan(
        _request_body(_default_template_id(), tag_ids=[tag_id])
    )

    assert data["valid"] is True
    assert data["plan"]["source_count"] == 1
    assert data["plan"]["segments_count"] == 3
    assert data["plan"]["tag_ids"] == [tag_id]
    assert data["plan"]["will_sync_profiles"] is False


def test_shorts_pipeline_run_creates_batch_items_and_blocks_duplicate(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    (root / "sources" / "main.mp4").write_bytes(b"video")
    reaction_path = tmp_path / "pipeline-reaction.mp4"
    reaction_path.write_bytes(b"reaction")
    asset_id = db.create_reaction_asset(
        name="Pipeline Reaction",
        file_path=str(reaction_path),
    )

    def fake_split(path, **kwargs):
        segment = root / "cuts" / "main" / "original" / "pipeline_1" / "seg_0001.mp4"
        segment.parent.mkdir(parents=True, exist_ok=True)
        segment.write_bytes(b"segment")
        return SimpleNamespace(
            video_id=None,
            job_id=None,
            files=[segment],
            duration_sec=30.0,
            output_dir=segment.parent,
            segment_ranges=[(0.0, 30.0)],
        )

    def fake_apply_batch(template_id, req, *, request, source_mode_override=None):
        assert req.source_paths == ["cuts/main/original/pipeline_1/seg_0001.mp4"]
        assert req.reaction_strategy == "fixed_asset"
        assert req.reaction_asset_id == asset_id
        project_id = db.create_studio_project(
            main_workspace_path=req.source_paths[0],
            template_key="reaction_top_25",
            reaction_asset_id=req.reaction_asset_id,
            recipe_json={
                "media": {"reaction": {"asset_id": req.reaction_asset_id}},
            },
            studio_template_id=template_id,
        )
        job_id = db.create_remotion_render_job(project_id)
        batch_id = db.create_remotion_render_batch(
            studio_template_id=template_id,
            template_key="reaction_top_25",
            name="Fake batch",
            source_mode="selected",
        )
        db.create_remotion_render_batch_item(
            batch_id=batch_id,
            studio_project_id=project_id,
            render_job_id=job_id,
            main_workspace_path=req.source_paths[0],
        )
        return {
            "batch": {
                "id": batch_id,
                "items": [{
                    "main_workspace_path": req.source_paths[0],
                    "render_job_id": job_id,
                    "render_status": "queued",
                    "output_workspace_path": None,
                }],
            },
            "jobs": [],
            "queue": {"started": True},
        }

    monkeypatch.setattr(pipeline_api, "split_video_file", fake_split)
    monkeypatch.setattr(pipeline_api, "choose_reaction_asset", lambda **kwargs: asset_id)
    monkeypatch.setattr(pipeline_api, "_create_apply_batch", fake_apply_batch)

    response = pipeline_api.shorts_pipeline_run_create(
        _request_body(_default_template_id(), reaction_asset_id=asset_id),
        _request(),
    )
    data = json.loads(response.body)

    assert data["run"]["status"] == "rendering"
    assert data["run"]["remotion_batch_id"] > 0
    assert data["run"]["items"][0]["segment_workspace_path"] == "cuts/main/original/pipeline_1/seg_0001.mp4"
    job = db.get_remotion_render_job(int(data["run"]["items"][0]["render_job_id"]))
    project = db.get_studio_project(int(job["studio_project_id"]))
    assert project["reaction_asset_id"] == asset_id
    assert json.loads(project["recipe_json"])["media"]["reaction"]["asset_id"] == asset_id
    assert db.list_publish_jobs() == []

    with pytest.raises(HTTPException) as exc:
        pipeline_api.shorts_pipeline_run_create(_request_body(_default_template_id()), _request())
    assert exc.value.status_code == 409


def test_shorts_pipeline_rendering_run_restarts_idle_render_queue(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    _workspace(tmp_path)
    reaction_path = tmp_path / "reaction.mp4"
    reaction_path.write_bytes(b"reaction")
    asset_id = db.create_reaction_asset(
        name="Queued Reaction",
        file_path=str(reaction_path),
    )
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=asset_id,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Queued batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/seg.mp4",
        render_job_id=job_id,
        status="queued",
    )
    calls: list[str] = []

    def fake_ensure(base_url: str):
        calls.append(base_url)
        return {
            "queue": {"status": "idle", "queued_count": 1, "rendering_count": 0},
            "recovered": None,
            "started": {"started": True, "reason": "started"},
        }

    monkeypatch.setattr(pipeline_api, "ensure_remotion_render_queue_running", fake_ensure)

    run = pipeline_api.shorts_pipeline_run_get(run_id, _request(f"/api/shorts-pipeline/runs/{run_id}"))["run"]

    assert calls == ["http://127.0.0.1:8000"]
    assert run["status"] == "rendering"
    assert run["summary"]["render_queue"]["started"]["started"] is True


def test_shorts_pipeline_health_reports_stopped_queue_with_preflight(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    _workspace(tmp_path)
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Health batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    monkeypatch.setattr(
        pipeline_api,
        "_pipeline_preflight",
        lambda: {"ok": True, "checks": [], "blocking": []},
    )

    health = pipeline_api.shorts_pipeline_health(_request("/api/shorts-pipeline/health"))

    assert health["active"] is True
    assert health["run"]["id"] == run_id
    assert health["queue"]["queued_count"] == 1
    assert any("worker не работает" in note["message"] for note in health["notes"])


def test_shorts_pipeline_continue_action_starts_idle_queue(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    _workspace(tmp_path)
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Continue batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    calls: list[str] = []

    def fake_ensure(base_url: str):
        calls.append(base_url)
        return {
            "queue": {"status": "idle", "queued_count": 1, "rendering_count": 0},
            "recovered": None,
            "started": {"started": True, "reason": "started"},
        }

    monkeypatch.setattr(pipeline_api, "ensure_remotion_render_queue_running", fake_ensure)

    data = pipeline_api.shorts_pipeline_run_continue(
        run_id,
        _request(f"/api/shorts-pipeline/runs/{run_id}/continue"),
    )
    updated = db.get_shorts_pipeline_run(run_id)
    summary = json.loads(updated["summary_json"])

    assert calls == ["http://127.0.0.1:8000"]
    assert data["continued"] is True
    assert data["run"]["status"] == "rendering"
    assert summary["manual_continue"]["queue"]["started"]["started"] is True


def test_shorts_pipeline_failed_batch_auto_retries_and_restarts_queue(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    _workspace(tmp_path)
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Failed batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    db.mark_remotion_render_job_failed(job_id, "temporary chromium crash")
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/seg.mp4",
        render_job_id=job_id,
        status="failed",
        error="temporary chromium crash",
    )
    calls: list[str] = []

    def fake_ensure(base_url: str):
        calls.append(base_url)
        return {
            "queue": {"status": "idle", "queued_count": 1, "rendering_count": 0},
            "recovered": None,
            "started": {"started": True, "reason": "started"},
        }

    monkeypatch.setattr(pipeline_api, "ensure_remotion_render_queue_running", fake_ensure)

    run = pipeline_api.shorts_pipeline_run_get(run_id, _request(f"/api/shorts-pipeline/runs/{run_id}"))["run"]
    job = db.get_remotion_render_job(job_id)
    batch = db.get_remotion_render_batch(batch_id)

    assert calls == ["http://127.0.0.1:8000"]
    assert run["status"] == "rendering"
    assert run["summary"]["auto_retry"]["retried"] == 1
    assert job["status"] == "queued"
    assert job["auto_retry_count"] == 1
    assert batch["status"] == "queued"


def test_shorts_pipeline_retry_failed_action_requeues_failed_jobs(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    _workspace(tmp_path)
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Manual retry batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    db.mark_remotion_render_job_failed(job_id, "temporary failure")
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    monkeypatch.setattr(
        pipeline_api,
        "ensure_remotion_render_queue_running",
        lambda base_url: {
            "queue": {"status": "idle", "queued_count": 1, "rendering_count": 0},
            "recovered": None,
            "started": {"started": True},
        },
    )

    data = pipeline_api.shorts_pipeline_run_retry_failed(
        run_id,
        _request(f"/api/shorts-pipeline/runs/{run_id}/retry-failed"),
    )

    assert data["retried"] == 1
    assert db.get_remotion_render_job(job_id)["status"] == "queued"
    assert db.get_remotion_render_job(job_id)["auto_retry_count"] == 0


def test_shorts_pipeline_failed_batch_finishes_after_retry_limit_and_keeps_done_outputs(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    channel_tag_id = db.create_tag(
        name="channel-finished-with-errors",
        kind="channel",
        color="#f59e0b",
        locked=True,
        system_key="youtube:finished-with-errors",
    )
    profile_id = db.create_local_storage_profile(name="Finished With Errors")
    db.replace_local_storage_profile_tag_rules(
        profile_id,
        include_tag_ids=[channel_tag_id],
        exclude_tag_ids=[],
        tag_match_mode="any",
    )
    output = root / "edits" / "main" / "ok.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"ok")

    done_project_id = db.create_studio_project(
        main_workspace_path="cuts/main/ok.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    done_job_id = db.create_remotion_render_job(done_project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Partial batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=done_project_id,
        render_job_id=done_job_id,
        main_workspace_path="cuts/main/ok.mp4",
    )
    db.mark_remotion_render_job_done(done_job_id, str(output))

    failed_project_id = db.create_studio_project(
        main_workspace_path="cuts/main/bad.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    failed_job_id = db.create_remotion_render_job(failed_project_id, max_auto_retries=0)
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=failed_project_id,
        render_job_id=failed_job_id,
        main_workspace_path="cuts/main/bad.mp4",
    )
    db.mark_remotion_render_job_failed(failed_job_id, "render failed permanently")

    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
        tag_ids_json=[],
        channel_tag_id=channel_tag_id,
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/ok.mp4",
        render_job_id=done_job_id,
        status="queued",
    )
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/bad.mp4",
        render_job_id=failed_job_id,
        status="queued",
    )

    run = pipeline_api.shorts_pipeline_run_get(run_id)["run"]
    active = db.get_active_shorts_pipeline_run()
    profile_items = db.list_local_storage_profile_items(profile_id)
    tags = db.list_workspace_tag_links(workspace_path="edits/main/ok.mp4")

    assert run["status"] == "done"
    assert "завершён с ошибками" in run["error"]
    assert run["summary"]["rendered"] == 1
    assert run["summary"]["failed"] == 1
    assert active is None
    assert [item["workspace_path"] for item in profile_items] == ["edits/main/ok.mp4"]
    assert {tag["slug"] for tag in tags} >= {"channel-finished-with-errors", "status-ready"}
    statuses = {item["segment_workspace_path"]: item["status"] for item in run["items"]}
    assert statuses == {
        "cuts/main/ok.mp4": "done",
        "cuts/main/bad.mp4": "failed",
    }


def test_shorts_pipeline_finish_with_errors_cancels_queued_and_syncs_done_outputs(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    channel_tag_id = db.create_tag(
        name="channel-manual-finish",
        kind="channel",
        color="#f59e0b",
        locked=True,
        system_key="youtube:manual-finish",
    )
    profile_id = db.create_local_storage_profile(name="Manual Finish")
    db.replace_local_storage_profile_tag_rules(
        profile_id,
        include_tag_ids=[channel_tag_id],
        exclude_tag_ids=[],
        tag_match_mode="any",
    )
    output = root / "edits" / "main" / "manual-ok.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"ok")
    done_project_id = db.create_studio_project(
        main_workspace_path="cuts/main/ok.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    done_job_id = db.create_remotion_render_job(done_project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Manual finish batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=done_project_id,
        render_job_id=done_job_id,
        main_workspace_path="cuts/main/ok.mp4",
    )
    db.mark_remotion_render_job_done(done_job_id, str(output))
    queued_project_id = db.create_studio_project(
        main_workspace_path="cuts/main/queued.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    queued_job_id = db.create_remotion_render_job(queued_project_id)
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=queued_project_id,
        render_job_id=queued_job_id,
        main_workspace_path="cuts/main/queued.mp4",
    )
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
        channel_tag_id=channel_tag_id,
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/ok.mp4",
        render_job_id=done_job_id,
        status="queued",
    )
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/queued.mp4",
        render_job_id=queued_job_id,
        status="queued",
    )

    data = pipeline_api.shorts_pipeline_run_finish_with_errors(run_id)
    run = data["run"]

    assert data["finished"] is True
    assert run["status"] == "done"
    assert run["summary"]["rendered"] == 1
    assert run["summary"]["cancelled"] == 1
    assert db.get_remotion_render_job(queued_job_id)["status"] == "cancelled"
    assert [item["workspace_path"] for item in db.list_local_storage_profile_items(profile_id)] == [
        "edits/main/manual-ok.mp4"
    ]


def _done_batch(root: Path, output_relative: str) -> tuple[int, int]:
    from shortsfarm import db

    seg = root / "cuts" / "main" / "seg.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_bytes(b"segment")
    output = root / output_relative
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"output")
    project_id = db.create_studio_project(
        main_workspace_path="cuts/main/seg.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json={"template": "test"},
    )
    job_id = db.create_remotion_render_job(project_id)
    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Batch",
        source_mode="selected",
    )
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=project_id,
        render_job_id=job_id,
        main_workspace_path="cuts/main/seg.mp4",
    )
    db.mark_remotion_render_job_done(job_id, str(output))
    return batch_id, job_id


def test_shorts_pipeline_done_render_tags_outputs_and_syncs_channel_profile(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    channel_tag_id = db.create_tag(
        name="channel-hello-world",
        kind="channel",
        color="#f59e0b",
        locked=True,
        system_key="youtube:test-channel",
    )
    profile_id = db.create_local_storage_profile(name="Hello World")
    db.replace_local_storage_profile_tag_rules(
        profile_id,
        include_tag_ids=[channel_tag_id],
        exclude_tag_ids=[],
        tag_match_mode="any",
    )
    batch_id, job_id = _done_batch(root, "edits/main/final.mp4")
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
        tag_ids_json=[],
        channel_tag_id=channel_tag_id,
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/seg.mp4",
        render_job_id=job_id,
        status="queued",
    )

    run = pipeline_api.shorts_pipeline_run_get(run_id)["run"]
    tags = db.list_workspace_tag_links(workspace_path="edits/main/final.mp4")
    profile_items = db.list_local_storage_profile_items(profile_id)

    assert run["status"] == "done"
    assert {tag["slug"] for tag in tags} >= {"channel-hello-world", "status-ready"}
    assert [item["workspace_path"] for item in profile_items] == ["edits/main/final.mp4"]


def test_shorts_pipeline_without_channel_tag_does_not_sync_profile(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import pipeline_api

    root = _workspace(tmp_path)
    user_tag_id = db.create_tag(name="podcast", color="#22c55e")
    profile_id = db.create_local_storage_profile(name="Podcast")
    db.replace_local_storage_profile_tag_rules(
        profile_id,
        include_tag_ids=[user_tag_id],
        exclude_tag_ids=[],
        tag_match_mode="any",
    )
    batch_id, job_id = _done_batch(root, "edits/main/no-channel.mp4")
    run_id = db.create_shorts_pipeline_run(
        source_mode="workspace",
        source_paths_json=["sources/main.mp4"],
        studio_template_id=None,
        template_key="reaction_top_25",
        tag_ids_json=[user_tag_id],
        channel_tag_id=None,
    )
    db.update_shorts_pipeline_run(run_id, status="rendering", remotion_batch_id=batch_id)
    db.create_shorts_pipeline_run_item(
        run_id=run_id,
        source_workspace_path="sources/main.mp4",
        segment_workspace_path="cuts/main/seg.mp4",
        render_job_id=job_id,
        status="queued",
    )

    run = pipeline_api.shorts_pipeline_run_get(run_id)["run"]

    assert run["status"] == "done"
    assert db.list_local_storage_profile_items(profile_id) == []


def test_shorts_pipeline_ui_is_registered():
    root = Path(__file__).resolve().parents[1]
    html = (root / "shortsfarm" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    js = (root / "shortsfarm" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "shortsfarm" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    tabler_css = root / "shortsfarm" / "web" / "static" / "vendor" / "tabler-icons" / "tabler-icons.min.css"

    assert "cdn.jsdelivr.net" not in html
    assert "/static/vendor/tabler-icons/tabler-icons.min.css" in html
    assert tabler_css.is_file()
    assert (tabler_css.parent / "tabler-icons.min.css.map").is_file()
    assert (tabler_css.parent / "fonts" / "tabler-icons.woff2").is_file()
    assert 'data-v="pipeline"' in html
    assert 'id="v-pipeline"' in html
    pipeline_end_marker = '{% include "views/files.html" %}'
    pipeline_html = html.split('<div id="v-pipeline"', 1)[1].split(pipeline_end_marker, 1)[0]
    assert "Конвейер" in pipeline_html
    assert "Настройка цикла" in pipeline_html
    assert "Таймер" not in pipeline_html
    assert "YouTube" not in pipeline_html
    queue_end_marker = '{% include "views/tags.html" %}'
    queue_html = html.split('<div id="v-queue"', 1)[1].split(queue_end_marker, 1)[0]
    assert "Конвейер · полный цикл" in queue_html
    assert "Единая очередь · источники и задачи" in queue_html
    assert "техническая история нарезки" in queue_html
    assert 'id="pipeline-health"' in html
    assert 'id="queue-pipeline-health"' in html
    assert 'data-v="files"' in html
    nav_html = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'data-v="queue"' in nav_html
    assert 'data-v="videos"' not in nav_html
    assert 'data-v="split"' not in nav_html
    assert 'data-v="clips"' not in nav_html
    assert 'id="v-videos"' not in html
    assert 'id="v-clips"' not in html
    assert 'id="v-split"' in html
    assert 'id="queue-overview"' in queue_html
    assert 'data-queue-filter="source"' in queue_html
    assert 'data-queue-filter="split"' in queue_html
    assert 'data-queue-filter="prepare"' in queue_html
    assert 'data-queue-filter="render"' in queue_html
    assert 'data-queue-filter="review"' in queue_html
    assert 'data-queue-filter="publish"' in queue_html
    assert 'data-queue-filter="missing"' in queue_html
    assert 'data-queue-filter="deleted"' in queue_html
    assert 'data-queue-view="table"' in queue_html
    assert 'data-queue-view="grid"' in queue_html
    assert 'id="queue-sources-bulk-toolbar"' in queue_html
    assert "Единая очередь" in queue_html
    assert "Указать новый путь…" in js
    assert "Восстановить" in js
    assert "/api/queue/items" in js
    assert "/api/videos/{videoId}/restore" not in js
    assert "/restore" in js
    assert "/relink-source" in js
    assert "currentView === 'videos'" not in js
    assert "nav('videos'" not in js
    assert 'id="queue-clips-section" hidden' in queue_html
    assert 'id="queue-clips-section"' in queue_html
    assert 'id="workspace-parent-filter-line"' in queue_html
    assert 'id="clips-table"' in queue_html
    assert "Нарезки и клипы" in queue_html
    assert "Показать все нарезки/клипы" in queue_html
    assert "← Назад к очереди" in queue_html
    assert "queueSubView = 'overview'" in js
    assert "setQueueSubView('overview')" in js
    assert "setQueueSubView('clips')" in js
    assert "showQueueOverview" in js
    assert "workspaceParentVideoFilter" in js
    assert "openQueueClipsForJob" in js
    assert "showAllQueueClips" in js
    assert "workspaceItemsForParentFilter" in js
    assert "openManagedFileInStudio" not in js
    assert "Открыть в Нарезке" not in js
    assert 'data-v="tags"' in html
    assert 'data-v="editing"' not in html
    assert 'data-v="studio"' in html
    assert "/api/shorts-pipeline/plan" in js
    assert "/api/shorts-pipeline/runs" in js
    assert "/api/shorts-pipeline/health" in js
    assert "Продолжить очередь" in js
    assert "Починить зависший запуск" in js
    assert "Завершить с ошибками" in js
    assert "Запустить цикл" in js
    assert "renderQueuePipelineRuns" in js
    assert "Studio render" in js
    assert 'id="videos-bulk-toolbar"' not in html
    assert 'id="queue-sources-bulk-toolbar"' in html
    assert "/api/videos/bulk-delete" in js
    assert "deleteSelectedVideos" in js
    assert "video-delete-child-clips" in js
    assert "video-delete-profile-items" in js
    assert "Удалить видео из локальных профилей" in js
    assert "Удалить нарезки и клипы вместе с видео" in js
    assert "Клипы останутся видимыми и будут привязаны к удалённому исходнику" in js
    assert "remove_from_profiles" in js
    assert "/clips/delete" in js
    assert "Удалить все клипы этого исходника" in js
    assert "Исходник удалён" in js
    assert 'data-settings-tab="database"' in html
    assert 'id="settings-database"' in html
    assert "/api/settings/database/reset" in js
    assert "УДАЛИТЬ БАЗУ" in html
    assert 'id="app-topbar"' in html
    assert 'id="global-inspector"' in html
    assert 'id="global-action-bar"' in html
    assert "toggleSidebarCollapsed" in js
    assert "toggleDensity" in js
    assert "openInspector" in js
    assert "openWorkspaceInspector" not in js
    assert "workspaceInspectorBody" not in js
    assert "function selectWorkspaceItem" in js
    assert "closeInspector();" in js
    assert "renderActionBar" in js
    assert "setVideoViewMode" in js
    assert "setClipViewMode" in js
    assert "media-grid" in css
    assert "global-inspector" in css
    assert "global-action-bar" in css
    assert "sidebar-collapsed" in css
    assert "density-compact" in css
    assert "settings-dashboard" in css
    assert "#v-queue .box-head" not in css
    assert "#v-tags .box-head" not in css
    assert "#v-integrations .box-head" not in css
    assert "#v-editing .box-head" not in css
    assert "pipeline-layout" in css
    assert "pipeline-step" in css
    assert "pipeline-health-card" in css
    assert "workspace-detail-enqueue-youtube" not in html
    assert "workspace-detail-upload-youtube" not in html
