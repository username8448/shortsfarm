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
    from shortsfarm.workspace_fs import get_workspace_root, set_workspace_root

    job_id = db.create_job(video_in_db, "fast", 60)
    db.mark_job_done(job_id)
    root = get_workspace_root() or set_workspace_root(tmp_path / "workspace")
    path = root / "cuts" / f"{name}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _template(key: str = "main_only_template", *, reaction: bool = False) -> int:
    from shortsfarm import db
    from shortsfarm.studio_templates import (
        default_reaction_top_25_definition,
        default_studio_template_definitions,
    )

    if reaction:
        definition = default_reaction_top_25_definition()
    else:
        definition = next(
            item for item in default_studio_template_definitions()
            if item["key"] == "main_only"
        )
    template_key = key.replace("-", "_")
    definition["key"] = template_key
    definition["name"] = f"Template {key}"
    return db.create_studio_template(
        template_key=template_key,
        name=f"Template {key}",
        engine="remotion",
        version=1,
        status="active",
        definition_json=definition,
    )


def _profile(
    *,
    template_id: int | None,
    reaction_pool_id: int | None = None,
) -> int:
    from shortsfarm import db

    return db.create_channel_profile(
        name="Funny RU",
        default_studio_template_id=template_id,
        reaction_pool_id=reaction_pool_id,
    )


def _recipe_for_job(job) -> dict:
    from shortsfarm import db

    project = db.get_studio_project(int(job["studio_project_id"]))
    assert project is not None
    return json.loads(str(project["recipe_json"]))


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
    assert job["recipe_json"] is None
    assert job["studio_template_id"] == template_id
    assert job["studio_project_id"] is not None
    assert job["remotion_render_job_id"] is not None
    assert str(job["output_path"]).endswith(
        f"edits/ready/main_only_template/render_job_{job['remotion_render_job_id']}.mp4"
    )
    recipe = _recipe_for_job(job)
    assert recipe["template"]["studio_template_id"] == template_id
    assert recipe["template"]["definition_schema_version"] == 2
    assert recipe["template"]["adapter"] == "main_only"
    assert recipe["parameters"]["main_fit"] == "cover"
    assert recipe["media"]["main"]["workspace_path"] == "cuts/ready.mp4"
    assert recipe["media"]["reaction"]["asset_id"] is None


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
    reaction_path = tmp_path / "reaction.mp4"
    reaction_path.write_bytes(b"reaction")
    reaction_id = db.create_reaction_asset(
        name="Reaction",
        file_path=str(reaction_path),
    )
    profile_id = db.create_channel_profile(
        name="Studio profile",
        default_studio_template_id=int(template["id"]),
    )

    data = api.editing_jobs_plan(
        EditJobsPlanRequest(
                item_keys=[f"segment:{segment_id}"],
                channel_profile_id=profile_id,
                reaction_asset_id=reaction_id,
                parameter_values={"reaction_height": 360},
                renderer_engine="remotion",
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
    assert job["recipe_json"] is None
    recipe = _recipe_for_job(job)
    assert recipe["template"]["studio_template_id"] == template["id"]
    assert recipe["layout"]["reaction_height"] == 360
    assert recipe["parameters"]["reaction_height"] == 360


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
        root / "edits" / "managed-ready" / "main_only_template"
        / f"render_job_{job['remotion_render_job_id']}.mp4"
    )
    assert job["recipe_json"] is None
    recipe = _recipe_for_job(job)
    assert recipe["media"]["main"]["workspace_path"].startswith("cuts/")
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
    assert "default Studio template" in no_template["results"][0]["reason"]


def test_plan_selects_available_reaction_from_pool(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="pool-main",
    )
    template_id = _template("pool-template", reaction=True)
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
    recipe = _recipe_for_job(job)
    assert recipe["media"]["reaction"]["asset_id"] == available_id


def test_plan_pool_with_only_missing_reaction_is_skipped(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import EditJobsPlanRequest

    _segment_id, item_key, _ = _make_segment(
        video_in_db,
        tmp_path,
        name="missing-pool-main",
    )
    template_id = _template("missing-pool-template", reaction=True)
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
    template_id = _template("explicit-template", reaction=True)
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

    first_job = db.get_edit_job(first_id)
    assert first_job is not None
    db.mark_remotion_render_job_done(
        int(first_job["remotion_render_job_id"]),
        "/tmp/edited.mp4",
    )
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

    current = db.get_edit_job(job_id)
    assert current is not None
    db.mark_remotion_render_job_failed(
        int(current["remotion_render_job_id"]),
        "boom",
    )
    failed_retried = api.editing_job_retry(job_id)["item"]
    assert failed_retried["status"] == "queued"
