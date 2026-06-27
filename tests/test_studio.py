"""Tests for the Remotion Studio vertical slice."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from starlette.requests import Request


def _request(
    path: str = "/api/studio",
    *,
    range_header: str | None = None,
) -> Request:
    headers = []
    if range_header:
        headers.append((b"range", range_header.encode("ascii")))
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    })


def _workspace(tmp_path: Path) -> Path:
    from shortsfarm.workspace_fs import set_workspace_root

    return set_workspace_root(tmp_path / "workspace")


def _reaction(tmp_path: Path) -> tuple[int, Path]:
    from shortsfarm import db

    path = tmp_path / "reaction.mp4"
    path.write_bytes(b"reaction")
    asset_id = db.create_reaction_asset(
        name="Reaction",
        file_path=str(path),
        duration_sec=2.0,
    )
    return asset_id, path


def _recipe(main_path: str, asset_id: int) -> dict:
    return {
        "version": 1,
        "template": {"key": "reaction_top_25", "renderer": "remotion"},
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "media": {
            "main": {"workspace_path": main_path},
            "reaction": {"asset_id": asset_id},
        },
        "layout": {
            "reaction_height": 480,
            "main_fit": "cover",
            "reaction_fit": "contain",
            "background_color": "#112233",
        },
        "audio": {
            "main_volume": 1,
            "reaction_volume": 0.25,
            "mute_reaction": False,
        },
        "overlays": {"top_text": "Top", "bottom_text": "Bottom"},
    }


def _project(tmp_path: Path, monkeypatch) -> tuple[int, Path, int]:
    from shortsfarm import db
    from shortsfarm.studio import normalize_studio_recipe

    root = _workspace(tmp_path)
    main = root / "cuts" / "show" / "segment.mp4"
    main.parent.mkdir(parents=True)
    main.write_bytes(b"main")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 10.0)
    recipe = normalize_studio_recipe(_recipe("cuts/show/segment.mp4", asset_id))
    project_id = db.create_studio_project(
        main_workspace_path="cuts/show/segment.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=asset_id,
        recipe_json=recipe,
    )
    return project_id, main, asset_id


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/video.mp4",
        "../video.mp4",
        "sources/../video.mp4",
        ".shortsfarm/metadata/video.mp4",
    ],
)
def test_studio_media_rejects_unsafe_paths(tmp_path, path):
    from shortsfarm.studio import resolve_studio_media_path

    _workspace(tmp_path)
    with pytest.raises((ValueError, PermissionError)):
        resolve_studio_media_path(path)


def test_studio_media_rejects_symlink(tmp_path):
    from shortsfarm.studio import resolve_studio_media_path

    root = _workspace(tmp_path)
    target = root / "sources" / "target.mp4"
    target.write_bytes(b"target")
    link = root / "sources" / "link.mp4"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable.")

    with pytest.raises(PermissionError, match="Symlinks"):
        resolve_studio_media_path("sources/link.mp4")


def test_studio_media_serves_file_and_manual_range(tmp_path):
    from shortsfarm.web.studio_api import studio_media

    root = _workspace(tmp_path)
    video = root / "sources" / "video.mp4"
    video.write_bytes(b"0123456789")

    full = studio_media("sources/video.mp4", _request())
    partial = studio_media(
        "sources/video.mp4",
        _request(range_header="bytes=2-5"),
    )

    assert isinstance(full, FileResponse)
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["access-control-allow-origin"] == "*"
    assert isinstance(partial, StreamingResponse)
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 2-5/10"

    async def consume() -> bytes:
        chunks = [chunk async for chunk in partial.body_iterator]
        return b"".join(chunks)

    assert asyncio.run(consume()) == b"2345"


def test_reaction_media_serves_valid_asset_and_rejects_missing(tmp_path):
    from shortsfarm.web.studio_api import studio_reaction_media

    asset_id, path = _reaction(tmp_path)
    response = studio_reaction_media(asset_id, _request())

    assert isinstance(response, FileResponse)
    assert Path(response.path) == path.resolve()
    with pytest.raises(HTTPException) as exc:
        studio_reaction_media(999999, _request())
    assert exc.value.status_code == 404


def test_media_picker_sections_mark_edited_results(tmp_path, monkeypatch):
    from shortsfarm.studio import list_studio_media_items

    root = _workspace(tmp_path)
    for folder in ("sources", "cuts", "prepared", "edits"):
        (root / folder / f"{folder}.mp4").write_bytes(folder.encode())
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 1.0)

    sections = list_studio_media_items()

    assert [item["key"] for item in sections] == [
        "sources", "cuts", "prepared", "edits",
    ]
    assert sections[-1]["label"] == "Результаты монтажа"
    assert sections[-1]["kind"] == "edited"
    assert sections[-1]["items"][0]["kind"] == "edited"


def test_studio_project_create_and_update(tmp_path, monkeypatch):
    from shortsfarm.web.studio_api import (
        StudioProjectRequest,
        studio_project_create,
        studio_project_update,
    )

    root = _workspace(tmp_path)
    main = root / "cuts" / "main.mp4"
    main.write_bytes(b"main")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 12.0)
    request = _request()

    created = studio_project_create(
        StudioProjectRequest(
            recipe_json=_recipe("cuts/main.mp4", asset_id),
        ),
        request,
    )["item"]
    updated_recipe = created["recipe_json"]
    updated_recipe["layout"]["reaction_height"] = 600
    updated = studio_project_update(
        created["id"],
        StudioProjectRequest(recipe_json=updated_recipe),
        request,
    )["item"]

    assert created["template_key"] == "reaction_top_25"
    assert created["resolved_recipe_json"]["media"]["main"]["url"].startswith(
        "http://127.0.0.1:8000/api/studio/media"
    )
    assert updated["recipe_json"]["layout"]["reaction_height"] == 600


def test_template_definition_lists_slots_parameters_rules_and_versions():
    from shortsfarm.web.studio_api import (
        StudioTemplateRequest,
        studio_template_create_version,
        studio_template_duplicate,
        studio_templates,
    )

    items = studio_templates()["items"]
    assert {
        "reaction_top_25",
        "reaction_top_33",
        "reaction_top_50",
        "reaction_bottom_25",
        "reaction_pip_corner",
    } <= {item["key"] for item in items}
    template = next(item for item in items if item["key"] == "reaction_top_25")
    definition = template["definition"]

    assert definition["slots"]["main"]["duration_policy"] == (
        "defines_output_duration"
    )
    assert definition["slots"]["reaction"]["playback"] == "loop"
    assert definition["parameters"]["reaction_height"]["default"] == 480
    assert definition["parameters"]["reaction_position"]["default"] == "top"
    assert definition["rules"]["output_duration"] == "main.duration"
    assert definition["rules"]["renderer_adapter"] == "reaction_layout"
    assert definition["rules"]["composition_id"] == "ReactionLayoutTemplate"

    duplicate = studio_template_duplicate(template["id"])["item"]
    assert duplicate["status"] == "draft"
    assert duplicate["key"].startswith("reaction_top_25_copy")

    new_version = studio_template_create_version(
        template["id"],
        StudioTemplateRequest(
            name=template["name"],
            status="draft",
            definition=definition,
        ),
    )["item"]
    assert new_version["key"] == "reaction_top_25"
    assert new_version["version"] == template["version"] + 1


def test_studio_project_allows_main_only_test_context(tmp_path, monkeypatch):
    from shortsfarm.web.studio_api import (
        StudioProjectRequest,
        studio_project_create,
    )

    root = _workspace(tmp_path)
    main = root / "prepared" / "main.mp4"
    main.write_bytes(b"main")
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 8.0)
    recipe = _recipe("prepared/main.mp4", 1)
    recipe["media"]["reaction"]["asset_id"] = None

    created = studio_project_create(
        StudioProjectRequest(recipe_json=recipe),
        _request(),
    )["item"]

    assert created["recipe_json"]["media"]["reaction"]["asset_id"] is None
    assert "url" not in created["resolved_recipe_json"]["media"]["reaction"]


def test_resolved_recipe_uses_render_profile_and_duration_trim(tmp_path, monkeypatch):
    from shortsfarm.studio import resolved_studio_recipe

    root = _workspace(tmp_path)
    (root / "sources" / "long.mp4").write_bytes(b"long")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 300.0)

    resolved = resolved_studio_recipe(
        _recipe("sources/long.mp4", asset_id),
        render_profile="low_540p",
        duration_limit_sec=30,
        start_offset_sec=10,
    )

    assert resolved["canvas"] == {"width": 540, "height": 960, "fps": 24}
    assert resolved["trim"] == {
        "start_sec": 10.0,
        "duration_sec": 30.0,
        "end_sec": 40.0,
        "source_duration_sec": 300.0,
        "full_length": False,
    }
    assert resolved["duration_in_frames"] == 720


def test_render_rejects_test_context_without_required_reaction(
    tmp_path,
    monkeypatch,
):
    from shortsfarm import db
    from shortsfarm.web.studio_api import studio_project_render

    root = _workspace(tmp_path)
    main = root / "sources" / "main.mp4"
    main.write_bytes(b"main")
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 8.0)
    recipe = _recipe("sources/main.mp4", 1)
    recipe["media"]["reaction"]["asset_id"] = None
    project_id = db.create_studio_project(
        main_workspace_path="sources/main.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=None,
        recipe_json=recipe,
    )

    with pytest.raises(HTTPException) as exc:
        studio_project_render(project_id, _request())

    assert exc.value.status_code == 400
    assert "reaction" in exc.value.detail["message"].lower()


def test_remotion_queue_allows_many_queued_but_one_rendering(tmp_path, monkeypatch):
    from shortsfarm import db

    first_project, _main, asset_id = _project(tmp_path, monkeypatch)
    second_project = db.create_studio_project(
        main_workspace_path="cuts/show/segment.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=asset_id,
        recipe_json=_recipe("cuts/show/segment.mp4", asset_id),
    )

    first_job = db.create_remotion_render_job(first_project)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_remotion_render_job(first_project)
    second_job = db.create_remotion_render_job(second_project)

    claimed = db.claim_next_remotion_render_job()
    assert claimed["id"] == first_job
    assert db.claim_next_remotion_render_job() is None
    assert db.mark_remotion_render_job_done(first_job, "done.mp4")
    claimed = db.claim_next_remotion_render_job()
    assert claimed["id"] == second_job


def test_remotion_output_paths_are_managed_and_use_temp(tmp_path, monkeypatch):
    from shortsfarm.studio import build_remotion_output_paths

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    temp, final = build_remotion_output_paths(
        "cuts/show/segment.mp4",
        project_id,
        9,
    )

    assert temp.name == "render_job_9.tmp.mp4"
    assert final.name == "render_job_9.mp4"
    assert "edits/show/segment/remotion_project_" in temp.as_posix()


def test_remotion_worker_atomically_finishes_valid_output(tmp_path, monkeypatch):
    from shortsfarm import db
    import shortsfarm.remotion_renderer as renderer

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id, renderer_engine="ffmpeg_fast")
    _temp, final = renderer.build_remotion_output_paths(
        "cuts/show/segment.mp4",
        project_id,
        job_id,
    )
    db.update_remotion_render_job_output(job_id, str(final))
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 10.0)

    def fake_ffmpeg(job_id, normalized_recipe, resolved_recipe, temp_path):
        Path(temp_path).write_bytes(b"valid mp4")
        return renderer.ProcessResult(
            returncode=0,
            stdout_tail="ok",
            stderr_tail="",
            elapsed_sec=1.25,
        )

    monkeypatch.setattr(renderer, "_run_ffmpeg_fast", fake_ffmpeg)

    renderer.run_remotion_render_job(job_id, "http://127.0.0.1:8000")

    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "done"
    assert job["renderer_engine"] == "ffmpeg_fast"
    assert job["stdout_tail"] == "ok"
    assert job["elapsed_sec"] == 1.25
    assert final.read_bytes() == b"valid mp4"
    assert not final.with_name(f"render_job_{job_id}.tmp.mp4").exists()


def test_remotion_worker_records_missing_node_without_crashing(tmp_path, monkeypatch):
    from shortsfarm import db
    import shortsfarm.remotion_renderer as renderer

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id, renderer_engine="remotion")
    monkeypatch.setattr(
        renderer,
        "_required_node",
        lambda: (_ for _ in ()).throw(RuntimeError("Node.js не найден.")),
    )

    renderer.run_remotion_render_job(job_id, "http://127.0.0.1:8000")

    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "failed"
    assert "Node.js не найден" in job["error"]


def test_interrupted_remotion_jobs_are_recovered(tmp_path, monkeypatch):
    from shortsfarm import db

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id)
    assert db.claim_remotion_render_job(job_id) is not None

    assert db.fail_interrupted_remotion_render_jobs() == 1
    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "failed"
    assert "перезапуском backend" in job["error"]


def test_render_queue_status_and_recover_stale_job(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import studio_api as api

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id)
    assert db.claim_remotion_render_job(job_id) is not None

    status = api.studio_render_queue_status()["queue"]
    assert status["status"] == "stale"
    assert status["current_job_id"] == job_id

    recovered = api.studio_render_queue_recover()
    assert recovered["recovered"] == 1
    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "failed"
    assert "Render queue recovery" in job["error"]


def test_apply_template_selected_creates_batch_projects_and_jobs(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import studio_api as api

    root = _workspace(tmp_path)
    first = root / "sources" / "one.mp4"
    second = root / "cuts" / "two.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 10.0)
    template = next(
        item for item in api.studio_templates()["items"]
        if item["key"] == "reaction_top_25"
    )

    response = api.studio_template_apply(
        template["id"],
        api.StudioApplyRequest(
            name="Selected batch",
            source_mode="selected",
            source_paths=["sources/one.mp4", "cuts/two.mp4"],
            reaction_strategy="fixed_asset",
            reaction_asset_id=asset_id,
            parameter_values={"reaction_height": 600},
            start=False,
        ),
        _request(),
    )
    payload = json.loads(response.body)

    batch = payload["batch"]
    assert batch["name"] == "Selected batch"
    assert batch["renderer_engine"] == "ffmpeg_fast"
    assert batch["render_profile"] == "low_540p"
    assert batch["duration_limit_sec"] == 45
    assert batch["total_items"] == 2
    assert len(batch["items"]) == 2
    assert len(payload["jobs"]) == 2
    for item in batch["items"]:
        job = db.get_remotion_render_job(item["render_job_id"])
        project = db.get_studio_project(item["studio_project_id"])
        assert job["status"] == "queued"
        assert job["renderer_engine"] == "ffmpeg_fast"
        assert job["render_profile"] == "low_540p"
        assert job["duration_limit_sec"] == 45
        assert "/edits/" in job["output_path"]
        assert f"/{template['key']}/" in job["output_path"]
        assert json.loads(project["recipe_json"])["layout"]["reaction_height"] == 600


def test_apply_template_uses_selected_non_default_template(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import studio_api as api

    root = _workspace(tmp_path)
    main = root / "sources" / "one.mp4"
    main.write_bytes(b"one")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 10.0)
    template = next(
        item for item in api.studio_templates()["items"]
        if item["key"] == "reaction_bottom_25"
    )

    response = api.studio_template_apply(
        template["id"],
        api.StudioApplyRequest(
            name="Bottom batch",
            source_mode="selected",
            source_paths=["sources/one.mp4"],
            reaction_strategy="fixed_asset",
            reaction_asset_id=asset_id,
            start=False,
        ),
        _request(),
    )
    payload = json.loads(response.body)
    item = payload["batch"]["items"][0]
    project = db.get_studio_project(item["studio_project_id"])
    job = db.get_remotion_render_job(item["render_job_id"])
    recipe = json.loads(project["recipe_json"])

    assert payload["batch"]["template_key"] == "reaction_bottom_25"
    assert project["template_key"] == "reaction_bottom_25"
    assert recipe["template"]["key"] == "reaction_bottom_25"
    assert recipe["layout"]["reaction_position"] == "bottom"
    assert "/reaction_bottom_25/" in job["output_path"]


def test_render_job_and_batch_retry_failed(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import studio_api as api

    project_id, _main, asset_id = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id)
    db.mark_remotion_render_job_failed(
        job_id,
        "boom",
        stdout_tail="out",
        stderr_tail="err",
        returncode=9,
        elapsed_sec=2.0,
    )
    monkeypatch.setattr(
        api,
        "start_remotion_render_queue",
        lambda base_url: {"started": True, "reason": "started"},
    )

    retried = api.studio_render_job_retry(job_id, _request())
    job = db.get_remotion_render_job(job_id)

    assert retried["retried"] is True
    assert job["status"] == "queued"
    assert job["error"] is None
    assert job["stdout_tail"] is None

    batch_id = db.create_remotion_render_batch(
        studio_template_id=None,
        template_key="reaction_top_25",
        name="Retry batch",
        source_mode="selected",
    )
    second_main = _main.parent / "second.mp4"
    second_main.write_bytes(b"second")
    second_project = db.create_studio_project(
        main_workspace_path="cuts/show/second.mp4",
        template_key="reaction_top_25",
        reaction_asset_id=asset_id,
        recipe_json=_recipe("cuts/show/second.mp4", asset_id),
    )
    second_job = db.create_remotion_render_job(second_project)
    db.create_remotion_render_batch_item(
        batch_id=batch_id,
        studio_project_id=second_project,
        render_job_id=second_job,
        main_workspace_path="cuts/show/second.mp4",
    )
    db.mark_remotion_render_job_failed(second_job, "failed")

    response = api.studio_render_batch_retry_failed(batch_id, _request())
    assert response["retried"] == 1
    assert db.get_remotion_render_job(second_job)["status"] == "queued"


def test_apply_template_rejects_template_without_remotion_adapter(tmp_path):
    from shortsfarm import db
    from shortsfarm.studio_templates import default_reaction_top_25_definition
    from shortsfarm.web import studio_api as api

    _workspace(tmp_path)
    definition = default_reaction_top_25_definition()
    definition["key"] = "ffmpeg_only_template"
    definition["name"] = "FFmpeg only"
    definition["engine"] = "ffmpeg"
    definition["rules"]["renderer"] = "ffmpeg"
    definition["rules"].pop("renderer_adapter", None)
    definition["rules"].pop("composition_id", None)
    template_id = db.create_studio_template(
        template_key=definition["key"],
        name=definition["name"],
        engine="ffmpeg",
        version=1,
        status="active",
        definition_json=definition,
    )

    with pytest.raises(HTTPException) as exc:
        api.studio_template_apply(
            template_id,
            api.StudioApplyRequest(
                source_mode="selected",
                source_paths=["sources/missing.mp4"],
                reaction_asset_id=1,
                start=False,
            ),
            _request(),
        )

    assert exc.value.status_code == 400
    assert "Remotion renderer adapter" in exc.value.detail["message"]


def test_apply_template_folder_recursive_and_pipeline_run(tmp_path, monkeypatch):
    from shortsfarm.web import studio_api as api

    root = _workspace(tmp_path)
    nested = root / "sources" / "show" / "nested"
    nested.mkdir(parents=True)
    (root / "sources" / "show" / "root.mp4").write_bytes(b"root")
    (nested / "child.mp4").write_bytes(b"child")
    asset_id, _ = _reaction(tmp_path)
    monkeypatch.setattr("shortsfarm.studio.probe_duration", lambda path: 10.0)
    monkeypatch.setattr(api, "start_remotion_render_queue", lambda base_url: None)
    template = api.studio_templates()["items"][0]

    pipeline = api.studio_pipeline_create(
        api.StudioPipelineRequest(
            name="Folder pipeline",
            studio_template_id=template["id"],
            source_mode="folder_recursive",
            source_path="sources/show",
            recursive=True,
            reaction_strategy="fixed_asset",
            reaction_asset_id=asset_id,
            parameter_values={"top_text": "Hello"},
        )
    )["item"]
    response = api.studio_pipeline_run(pipeline["id"], _request())
    payload = json.loads(response.body)

    assert payload["batch"]["source_mode"] == "folder_recursive"
    assert payload["batch"]["total_items"] == 2
    assert {
        item["main_workspace_path"]
        for item in payload["batch"]["items"]
    } == {
        "sources/show/root.mp4",
        "sources/show/nested/child.mp4",
    }


def test_studio_route_shows_build_instructions_without_dist(tmp_path, monkeypatch):
    import shortsfarm.web.app as web_app

    monkeypatch.setattr(web_app, "STUDIO_DIST", tmp_path / "missing-dist")
    app = web_app.create_app()
    route = next(
        item for item in app.routes
        if getattr(item, "path", None) == "/studio"
    )

    response = route.endpoint()

    assert isinstance(response, HTMLResponse)
    assert response.status_code == 503
    assert b"npm --prefix frontend run build" in response.body


def test_main_panel_embeds_template_studio_without_legacy_word():
    template = (
        Path(__file__).resolve().parents[1]
        / "shortsfarm"
        / "web"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert 'data-v="studio"' in template
    assert 'id="v-studio"' in template
    assert 'id="studio-root"' in template
    assert "Legacy" not in template


def test_studio_frontend_uses_preview_registry_and_embedded_batch_open():
    root = Path(__file__).resolve().parents[1]
    preview = (
        root
        / "frontend"
        / "src"
        / "studio"
        / "RemotionPreview.tsx"
    ).read_text(encoding="utf-8")
    registry = (
        root
        / "frontend"
        / "src"
        / "studio"
        / "previewRegistry.ts"
    ).read_text(encoding="utf-8")
    apply_panel = (
        root
        / "frontend"
        / "src"
        / "studio"
        / "ApplyTemplatePanel.tsx"
    ).read_text(encoding="utf-8")
    legacy_js = (
        root
        / "shortsfarm"
        / "web"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "previewComponentForComposition" in preview
    assert "component={ReactionLayoutTemplate}" not in preview
    assert "ReactionTop25: ReactionLayoutTemplate" in registry
    assert "/studio?batch=" not in apply_panel
    assert "onOpenBatch(batch.id)" in apply_panel
    assert "activateInitialViewFromQuery" in legacy_js
    assert "params.has('batch')" in legacy_js
    assert "nav('studio'" in legacy_js
