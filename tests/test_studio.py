"""Tests for the Remotion Studio vertical slice."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

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
    assert sections[-1]["label"] == "Edited results"
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
    template = next(item for item in items if item["key"] == "reaction_top_25")
    definition = template["definition"]

    assert definition["slots"]["main"]["duration_policy"] == (
        "defines_output_duration"
    )
    assert definition["slots"]["reaction"]["playback"] == "loop"
    assert definition["parameters"]["reaction_height"]["default"] == 480
    assert definition["rules"]["output_duration"] == "main.duration"

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


def test_remotion_render_global_and_project_locks(tmp_path, monkeypatch):
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
    with pytest.raises(sqlite3.IntegrityError):
        db.create_remotion_render_job(second_project)

    assert db.mark_remotion_render_job_failed(first_job, "done")
    assert db.create_remotion_render_job(first_project) > first_job


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
    job_id = db.create_remotion_render_job(project_id)
    _temp, final = renderer.build_remotion_output_paths(
        "cuts/show/segment.mp4",
        project_id,
        job_id,
    )
    db.update_remotion_render_job_output(job_id, str(final))
    monkeypatch.setattr(renderer, "_required_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(renderer, "_required_remotion_dependencies", lambda: None)
    monkeypatch.setattr(renderer, "_required_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 10.0)

    def fake_run(command, **kwargs):
        payload = json.loads(kwargs["input"])
        Path(payload["outputPath"]).write_bytes(b"valid mp4")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)

    renderer.run_remotion_render_job(job_id, "http://127.0.0.1:8000")

    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "done"
    assert final.read_bytes() == b"valid mp4"
    assert not final.with_name(f"render_job_{job_id}.tmp.mp4").exists()


def test_remotion_worker_records_missing_node_without_crashing(tmp_path, monkeypatch):
    from shortsfarm import db
    import shortsfarm.remotion_renderer as renderer

    project_id, _main, _asset = _project(tmp_path, monkeypatch)
    job_id = db.create_remotion_render_job(project_id)
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

    assert db.fail_interrupted_remotion_render_jobs() == 1
    job = db.get_remotion_render_job(job_id)
    assert job["status"] == "failed"
    assert "перезапуском backend" in job["error"]


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
