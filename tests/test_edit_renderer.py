"""Tests for the Stage 4 FFmpeg edit-job renderer."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/editing/worker/run-once",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    })


def _make_edit_job(
    tmp_path: Path,
    *,
    name: str = "job",
    template_key: str = "reaction_top_25",
    renderer: str = "ffmpeg",
    main_exists: bool = True,
    reaction_exists: bool = True,
    output_path: Path | None = None,
) -> tuple[int, Path, Path, Path]:
    from shortsfarm import db
    from shortsfarm.config import output_dir

    main_path = tmp_path / f"{name}-main.mp4"
    reaction_path = tmp_path / f"{name}-reaction.mp4"
    if main_exists:
        main_path.write_bytes(b"main")
    if reaction_exists:
        reaction_path.write_bytes(b"reaction")
    resolved_output = output_path or (
        output_dir() / "edited" / "reaction_top_25" / f"{name}.mp4"
    )
    recipe = {
        "version": 1,
        "template": {
            "id": 1,
            "key": template_key,
            "renderer": renderer,
            "recipe": {},
        },
        "workspace": {
            "item_key": "segment:1",
            "main_input_path": str(main_path),
        },
        "reaction": {
            "asset_id": 1,
            "file_path": str(reaction_path),
        },
        "output": {"path": str(resolved_output)},
    }
    job_id = db.create_edit_job(
        workspace_item_key="segment:1",
        input_path=str(main_path),
        output_path=str(resolved_output),
        renderer=renderer,
        recipe_json=recipe,
    )
    return job_id, main_path, reaction_path, resolved_output


def _mock_successful_ffmpeg(monkeypatch, captured: list[list[str]] | None = None):
    import shortsfarm.edit_renderer as renderer

    monkeypatch.setattr(renderer, "require_binary", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 12.5)

    def fake_run(cmd, **kwargs):
        if captured is not None:
            captured.append(cmd)
        Path(cmd[-1]).write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)


def test_render_edit_job_uses_recipe_and_builds_expected_ffmpeg_command(
    tmp_path,
    monkeypatch,
):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job

    job_id, main_path, reaction_path, output_path = _make_edit_job(
        tmp_path,
        name="success",
    )
    captured: list[list[str]] = []
    _mock_successful_ffmpeg(monkeypatch, captured)

    rendered = render_edit_job(job_id)

    assert rendered["status"] == "done"
    assert rendered["edited_path"] == str(output_path.resolve())
    assert output_path.read_bytes() == b"rendered"
    command = captured[0]
    assert command[command.index("-i") + 1] == str(main_path.resolve())
    assert command[command.index("-stream_loop") + 1] == "-1"
    assert str(reaction_path.resolve()) in command
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "scale=1080:480" in filter_complex
    assert "scale=1080:1440" in filter_complex
    assert "vstack=inputs=2" in filter_complex
    assert command[command.index("-map") + 1] == "[v]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "0:a:0?"
    assert command[command.index("-t") + 1] == "12.500000"
    assert db.get_edit_job(job_id)["error"] is None


@pytest.mark.parametrize(
    ("main_exists", "reaction_exists", "message"),
    [
        (False, True, "Main input file"),
        (True, False, "Reaction input file"),
    ],
)
def test_missing_input_marks_job_failed(
    tmp_path,
    main_exists,
    reaction_exists,
    message,
):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job

    job_id, _main, _reaction, _output = _make_edit_job(
        tmp_path,
        name=f"missing-{main_exists}-{reaction_exists}",
        main_exists=main_exists,
        reaction_exists=reaction_exists,
    )

    with pytest.raises(FileNotFoundError, match=message):
        render_edit_job(job_id)

    failed = db.get_edit_job(job_id)
    assert failed["status"] == "failed"
    assert message in failed["error"]


def test_invalid_recipe_json_marks_job_failed(tmp_path):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job

    job_id, _main, _reaction, _output = _make_edit_job(tmp_path, name="bad-json")
    with db.connect() as con:
        con.execute(
            "UPDATE edit_jobs SET recipe_json=? WHERE id=?",
            ("{broken", job_id),
        )

    with pytest.raises(ValueError, match="невалидный JSON"):
        render_edit_job(job_id)

    assert db.get_edit_job(job_id)["status"] == "failed"


@pytest.mark.parametrize(
    ("template_key", "renderer", "message"),
    [
        ("another_template", "ffmpeg", "Unsupported edit template"),
        ("reaction_top_25", "revideo", "renderer=ffmpeg"),
    ],
)
def test_unsupported_template_or_renderer_marks_job_failed(
    tmp_path,
    template_key,
    renderer,
    message,
):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job

    job_id, _main, _reaction, _output = _make_edit_job(
        tmp_path,
        name=f"unsupported-{template_key}-{renderer}",
        template_key=template_key,
        renderer=renderer,
    )

    with pytest.raises(ValueError, match=message):
        render_edit_job(job_id)

    failed = db.get_edit_job(job_id)
    assert failed["status"] == "failed"
    assert message in failed["error"]


def test_output_path_outside_edited_directory_is_rejected(tmp_path):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job

    outside = tmp_path / "outside.mp4"
    job_id, _main, _reaction, _output = _make_edit_job(
        tmp_path,
        name="outside",
        output_path=outside,
    )

    with pytest.raises(ValueError, match="output/edited|внутри"):
        render_edit_job(job_id)

    assert db.get_edit_job(job_id)["status"] == "failed"
    assert not outside.exists()


def test_workspace_edits_output_is_rendered(tmp_path, monkeypatch):
    from shortsfarm.edit_renderer import render_edit_job
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "workspace")
    output_path = root / "edits" / "show" / "episode" / "segment_001" / "edit_job_1.mp4"
    job_id, _main, _reaction, _output = _make_edit_job(
        tmp_path,
        name="workspace-edits",
        output_path=output_path,
    )
    _mock_successful_ffmpeg(monkeypatch)

    rendered = render_edit_job(job_id)

    assert rendered["status"] == "done"
    assert rendered["edited_path"] == str(output_path.resolve())
    assert output_path.read_bytes() == b"rendered"


def test_other_workspace_folder_is_not_allowed_for_render(tmp_path):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "workspace")
    outside_edits = root / "ready" / "escape.mp4"
    job_id, _main, _reaction, _output = _make_edit_job(
        tmp_path,
        name="workspace-ready",
        output_path=outside_edits,
    )

    with pytest.raises(ValueError, match="внутри"):
        render_edit_job(job_id)

    assert db.get_edit_job(job_id)["status"] == "failed"
    assert not outside_edits.exists()


def test_ffmpeg_failure_marks_job_failed_and_saves_stderr(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.edit_renderer import render_edit_job
    import shortsfarm.edit_renderer as renderer

    job_id, _main, _reaction, output_path = _make_edit_job(
        tmp_path,
        name="ffmpeg-failure",
    )
    monkeypatch.setattr(renderer, "require_binary", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(renderer, "probe_duration", lambda path: 8.0)
    monkeypatch.setattr(
        renderer.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="line one\nencoder exploded",
        ),
    )

    with pytest.raises(RuntimeError, match="encoder exploded"):
        render_edit_job(job_id)

    failed = db.get_edit_job(job_id)
    assert failed["status"] == "failed"
    assert "encoder exploded" in failed["error"]
    assert not output_path.exists()


def test_done_job_is_returned_without_rerender_unless_forced(tmp_path, monkeypatch):
    from shortsfarm.edit_renderer import render_edit_job

    job_id, _main, _reaction, output_path = _make_edit_job(
        tmp_path,
        name="done-force",
    )
    _mock_successful_ffmpeg(monkeypatch)
    first = render_edit_job(job_id)
    output_path.write_bytes(b"keep")

    second = render_edit_job(job_id)
    assert second["status"] == "done"
    assert output_path.read_bytes() == b"keep"

    forced = render_edit_job(job_id, force=True)
    assert forced["status"] == "done"
    assert output_path.read_bytes() == b"rendered"
    assert first["id"] == forced["id"]


def test_run_edit_queue_once_respects_limit(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.edit_renderer import run_edit_queue_once

    first_id, *_ = _make_edit_job(tmp_path, name="queue-first")
    second_id, *_ = _make_edit_job(tmp_path, name="queue-second")
    _mock_successful_ffmpeg(monkeypatch)

    handled = run_edit_queue_once(limit=1)

    assert [row["id"] for row in handled] == [first_id]
    assert db.get_edit_job(first_id)["status"] == "done"
    assert db.get_edit_job(second_id)["status"] == "queued"


def test_queue_continues_after_failed_job(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.edit_renderer import run_edit_queue_once

    failed_id, *_ = _make_edit_job(
        tmp_path,
        name="queue-missing",
        main_exists=False,
    )
    done_id, *_ = _make_edit_job(tmp_path, name="queue-good")
    _mock_successful_ffmpeg(monkeypatch)

    handled = run_edit_queue_once(limit=2)

    assert [row["id"] for row in handled] == [failed_id, done_id]
    assert [row["status"] for row in handled] == ["failed", "done"]
    assert db.get_edit_job(done_id)["status"] == "done"


def test_edit_render_api_endpoints(tmp_path, monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        EditJobRenderRequest,
        EditJobsBulkRenderRequest,
        EditWorkerRunOnceRequest,
    )

    direct_id, *_ = _make_edit_job(tmp_path, name="api-direct")
    worker_id, *_ = _make_edit_job(tmp_path, name="api-worker")
    bulk_one_id, *_ = _make_edit_job(tmp_path, name="api-bulk-one")
    bulk_two_id, *_ = _make_edit_job(tmp_path, name="api-bulk-two")
    _mock_successful_ffmpeg(monkeypatch)

    with pytest.raises(Exception) as direct_exc:
        api.editing_job_render(direct_id, EditJobRenderRequest())
    assert "Legacy edit job доступен только для просмотра" in str(direct_exc.value)

    started: list[str] = []
    monkeypatch.setattr(
        api,
        "start_studio_render_queue",
        lambda base_url: started.append(base_url) or {"running": False},
    )
    worker = api.editing_worker_run_once(
        EditWorkerRunOnceRequest(limit=1),
        _request(),
    )
    assert worker["legacy_skipped"] >= 1
    assert worker["processed"] == 0
    assert started == ["http://127.0.0.1:8000"]

    bulk = api.editing_jobs_bulk_render(
        EditJobsBulkRenderRequest(job_ids=[bulk_one_id, bulk_two_id])
    )
    assert bulk["status"] == "ok"
    assert bulk["summary"] == {"processed": 0, "skipped": 0, "errors": 2}
    assert [result["status"] for result in bulk["results"]] == ["error", "error"]
    assert all(
        "Legacy edit job доступен только для просмотра" in result["error"]
        for result in bulk["results"]
    )


def test_edit_worker_cli_prints_summary(runner):
    from shortsfarm.cli import app

    result = runner.invoke(app, ["edit-worker", "--limit", "2"])

    assert result.exit_code == 1
    assert "Legacy edit worker отключён" in result.output
