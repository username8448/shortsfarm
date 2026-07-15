"""Shared pytest fixtures for ShortsFarm tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Temp data directory - isolates every test from the real project DB
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set SHORTSFARM_HOME to a fresh tmp directory for each test."""
    home = tmp_path / "shortsfarm-data"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SHORTSFARM_HOME", str(home))
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REDIRECT_URI", raising=False)

    # Re-import config so cached Path objects are refreshed
    import importlib
    import shortsfarm.config as cfg
    importlib.reload(cfg)

    from shortsfarm import db
    db.init_db()

    yield home

    from shortsfarm.remotion_renderer import wait_for_studio_render_queue
    assert wait_for_studio_render_queue(timeout_sec=10.0), (
        "studio-render-queue did not stop before temporary test DB teardown"
    )


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# A dummy video file on disk
# ---------------------------------------------------------------------------

@pytest.fixture
def dummy_video(tmp_path: Path) -> Path:
    f = tmp_path / "test_video.mp4"
    f.write_bytes(b"\x00" * 256)
    return f


# ---------------------------------------------------------------------------
# A video already registered in the DB
# ---------------------------------------------------------------------------

@pytest.fixture
def video_in_db(dummy_video: Path) -> int:
    from shortsfarm import db
    return db.add_video(
        source_path  = dummy_video,
        title        = dummy_video.stem,
        duration_sec = 120.0,
    )


# ---------------------------------------------------------------------------
# A video+session+mark already in the DB
# ---------------------------------------------------------------------------

@pytest.fixture
def mark_in_db(video_in_db: int, tmp_path: Path) -> dict:
    from shortsfarm import db
    session_id = db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    mark_id    = db.insert_mark(
        video_id   = video_in_db,
        session_id = session_id,
        in_sec     = 10.0,
        out_sec    = 70.0,
        rating     = None,
        label      = None,
    )
    clip_id = db.insert_clip(video_id=video_in_db, mark_id=mark_id, cut_mode="exact")
    return {"video_id": video_in_db, "session_id": session_id,
            "mark_id": mark_id, "clip_id": clip_id}
