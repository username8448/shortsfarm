"""Tests for planning edit jobs without running a renderer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_parse_workspace_item_key_validates_format():
    from shortsfarm.edit_planner import parse_workspace_item_key

    assert parse_workspace_item_key("segment:12") == ("segment", 12)
    assert parse_workspace_item_key("clip:3") == ("clip", 3)
    for value in ("video:1", "segment:0", "clip:nope", "segment"):
        with pytest.raises(ValueError):
            parse_workspace_item_key(value)


def _make_segment(
    video_in_db: int,
    tmp_path: Path,
    *,
    name: str,
    status: str = "ready",
    exists: bool = True,
) -> tuple[int, str, Path]:
    from shortsfarm import db

    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    path = tmp_path / f"{name}.mp4"
    if exists:
        path.write_bytes(b"segment")
    segment_id = db.insert_segment(
        video_in_db,
        job_id,
        1,
        0.0,
        20.0,
        path,
    )
    db.update_workspace_item("segment", segment_id, workspace_status=status)
    return segment_id, f"segment:{segment_id}", path


def _template(key: str = "reaction_top_25") -> int:
    from shortsfarm import db

    return db.create_edit_template(
        key=key,
        name=f"Template {key}",
        renderer="ffmpeg",
        recipe_json={
            "version": 1,
            "canvas": {"width": 1080, "height": 1920},
            "slots": {"main": {}, "reaction": {}},
        },
    )


def _profile(
    *,
    template_id: int | None,
    reaction_pool_id: int | None = None,
) -> int:
    from shortsfarm import db

    return db.create_channel_profile(
        name="Funny RU",
        default_template_id=template_id,
        reaction_pool_id=reaction_pool_id,
    )


def test_plan_endpoint_creates_materialized_job_for_ready_segment(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    segment_id, item_key, source = _make_segment(
        video_in_db,
        tmp_path,
        name="ready",
    )
    template_id = _template()
    profile_id = _profile(template_id=template_id)

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[item_key],
            channel_profile_id=profile_id,
        )
    )

    assert data["summary"] == {"created": 1, "existing": 0, "skipped": 0, "errors": 0}
    result = data["results"][0]
    assert result["status"] == "created"
    job = db.get_edit_job(result["job"]["id"])
    assert job is not None
    assert job["status"] == "queued"
    assert job["input_path"] == str(source.resolve())
    assert (
        f"output/edited/reaction_top_25/segment_{segment_id}"
        f"__profile_{profile_id}__job_{job['id']}.mp4"
    ) in str(job["output_path"])
    recipe = json.loads(job["recipe_json"])
    assert recipe["template"]["id"] == template_id
    assert recipe["template"]["recipe"]["canvas"]["width"] == 1080
    assert recipe["workspace"]["item_key"] == item_key
    assert recipe["workspace"]["main_input_path"] == str(source.resolve())
    assert recipe["channel_profile"]["id"] == profile_id
    assert recipe["reaction"]["asset_id"] is None
    assert recipe["output"]["path"] == job["output_path"]


def test_plan_with_studio_template_creates_remotion_links(tmp_path):
    from shortsfarm import db
    from shortsfarm.studio_templates import ensure_default_studio_templates
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "workspace")
    source = root / "sources" / "parent.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    video_id = db.add_video(source, source.stem, 60.0)
    segment_path = root / "cuts" / "parent" / "segment.mp4"
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    segment_path.write_bytes(b"segment")
    split_job_id = db.create_job(video_id, "fast", 60)
    db.mark_job_done(split_job_id)
    segment_id = db.insert_segment(video_id, split_job_id, 1, 0.0, 10.0, segment_path)
    db.update_workspace_item("segment", segment_id, workspace_status="ready")
    template = ensure_default_studio_templates()[0]
    profile_id = db.create_channel_profile(
        name="Studio profile",
        default_studio_template_id=int(template["id"]),
    )

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[f"segment:{segment_id}"],
            channel_profile_id=profile_id,
            parameter_values={"reaction_height": 360},
        )
    )

    assert data["summary"]["created"] == 1
    job = db.get_edit_job(data["results"][0]["job"]["id"])
    assert job is not None
    assert job["studio_template_id"] == template["id"]
    assert job["studio_project_id"] is not None
    assert job["remotion_render_job_id"] is not None
    assert job["renderer"] == "remotion"
    render_job = db.get_remotion_render_job(int(job["remotion_render_job_id"]))
    assert render_job is not None
    assert render_job["status"] == "queued"
    assert str(render_job["output_path"]).startswith(str(root / "edits"))
    recipe = json.loads(str(job["recipe_json"]))
    assert recipe["template"]["studio_template_id"] == template["id"]
    assert recipe["layout"]["reaction_height"] == 360


def test_plan_uses_workspace_edits_for_managed_source(tmp_path):
    from shortsfarm import db
    from shortsfarm.edit_planner import plan_edit_job_for_workspace_item
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "workspace")
    source = (
        root / "sources" / "Автор" / "Подкаст" / "Выпуск 001" / "original.mp4"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    video_id = db.add_video(source, source.stem, 120.0)
    segment_id, item_key, _ = _make_segment(
        video_id,
        tmp_path,
        name="managed-ready",
    )
    template_id = _template()
    profile_id = _profile(template_id=template_id)

    result = plan_edit_job_for_workspace_item(item_key, profile_id)

    job = db.get_edit_job(result["job"]["id"])
    assert job is not None
    assert Path(job["output_path"]) == (
        root / "edits" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / f"segment_{segment_id:03d}" / f"edit_job_{job['id']}.mp4"
    )
    recipe = json.loads(job["recipe_json"])
    assert recipe["workspace"]["source_path"] == str(source)
    assert recipe["output"]["path"] == job["output_path"]
    assert not Path(job["output_path"]).parent.exists()


def test_plan_skips_draft_missing_and_profile_without_template(video_in_db, tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _draft_id, draft_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="draft",
        status="draft",
    )
    _missing_id, missing_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="missing",
        exists=False,
    )
    template_id = _template("valid")
    profile_id = _profile(template_id=template_id)

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[draft_key, missing_key],
            channel_profile_id=profile_id,
        )
    )
    reasons = {result["item_key"]: result["reason"] for result in data["results"]}
    assert data["summary"]["skipped"] == 2
    assert "ready" in reasons[draft_key]
    assert "Видео отсутствует" in reasons[missing_key]

    no_template_profile = _profile(template_id=None)
    no_template = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[draft_key],
            channel_profile_id=no_template_profile,
        )
    )
    assert no_template["summary"]["skipped"] == 1
    assert "default template" in no_template["results"][0]["reason"]


def test_plan_selects_available_reaction_from_pool(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="pool-main",
    )
    template_id = _template("pool-template")
    pool_id = db.create_reaction_pool(name="Funny pool")
    missing_id = db.create_reaction_asset(
        name="Missing",
        file_path=str(tmp_path / "missing-reaction.mp4"),
    )
    available_path = tmp_path / "available-reaction.mp4"
    available_path.write_bytes(b"reaction")
    available_id = db.create_reaction_asset(
        name="Available",
        file_path=str(available_path),
    )
    db.add_reaction_to_pool(pool_id, missing_id, weight=100)
    db.add_reaction_to_pool(pool_id, available_id, weight=1)
    profile_id = _profile(template_id=template_id, reaction_pool_id=pool_id)

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(item_keys=[item_key], channel_profile_id=profile_id)
    )

    job = data["results"][0]["job"]
    assert data["summary"]["created"] == 1
    assert job["reaction_asset_id"] == available_id
    recipe = json.loads(job["recipe_json"])
    assert recipe["reaction"]["file_path"] == str(available_path)


def test_plan_pool_with_only_missing_reaction_is_skipped(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="missing-pool-main",
    )
    template_id = _template("missing-pool-template")
    pool_id = db.create_reaction_pool(name="Missing pool")
    asset_id = db.create_reaction_asset(
        name="Gone",
        file_path=str(tmp_path / "gone.mp4"),
    )
    db.add_reaction_to_pool(pool_id, asset_id)
    profile_id = _profile(template_id=template_id, reaction_pool_id=pool_id)

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(item_keys=[item_key], channel_profile_id=profile_id)
    )

    assert data["summary"]["skipped"] == 1
    assert "нет доступных reaction files" in data["results"][0]["reason"]


def test_plan_explicit_reaction_asset_overrides_pool(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="explicit-main",
    )
    template_id = _template("explicit-template")
    reaction_path = tmp_path / "explicit.mp4"
    reaction_path.write_bytes(b"reaction")
    reaction_id = db.create_reaction_asset(
        name="Explicit",
        file_path=str(reaction_path),
    )
    profile_id = _profile(template_id=template_id)

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[item_key],
            channel_profile_id=profile_id,
            reaction_asset_id=reaction_id,
        )
    )

    assert data["results"][0]["job"]["reaction_asset_id"] == reaction_id


def test_duplicate_and_force_new_rules(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="duplicate-main",
    )
    template_id = _template("duplicate-template")
    profile_id = _profile(template_id=template_id)
    request = EditJobsPlanRequest(
        item_keys=[item_key],
        channel_profile_id=profile_id,
    )

    first = api.editing_jobs_plan(request)
    queued_existing = api.editing_jobs_plan(request)
    first_id = first["results"][0]["job"]["id"]
    assert queued_existing["results"][0]["status"] == "existing"
    assert queued_existing["results"][0]["job"]["id"] == first_id
    assert len(db.list_edit_jobs()) == 1

    db.mark_edit_job_done(first_id, "/tmp/edited.mp4")
    done_existing = api.editing_jobs_plan(request)
    assert done_existing["results"][0]["status"] == "existing"
    assert done_existing["results"][0]["job"]["id"] == first_id

    forced = api.editing_jobs_plan(
        EditJobsPlanRequest(
            item_keys=[item_key],
            channel_profile_id=profile_id,
            force_new=True,
        )
    )
    assert forced["results"][0]["status"] == "created"
    assert forced["results"][0]["job"]["id"] != first_id
    assert len(db.list_edit_jobs()) == 2


def test_edit_jobs_api_details_cancel_and_retry(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="lifecycle-main",
    )
    template_id = _template("lifecycle-template")
    profile_id = _profile(template_id=template_id)
    planned = api.editing_jobs_plan(
        EditJobsPlanRequest(item_keys=[item_key], channel_profile_id=profile_id)
    )
    job_id = planned["results"][0]["job"]["id"]

    listed = api.editing_jobs()["items"]
    assert listed[0]["id"] == job_id
    assert listed[0]["channel_profile_name"] == "Funny RU"
    assert listed[0]["template_name"] == "Template lifecycle-template"

    cancelled = api.editing_job_cancel(job_id)["item"]
    assert cancelled["status"] == "cancelled"
    retried = api.editing_job_retry(job_id)["item"]
    assert retried["status"] == "queued"
    assert retried["error"] is None
    assert retried["started_at"] is None
    assert retried["finished_at"] is None

    db.mark_edit_job_failed(job_id, "boom")
    failed_retried = api.editing_job_retry(job_id)["item"]
    assert failed_retried["status"] == "queued"
