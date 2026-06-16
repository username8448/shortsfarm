"""Tests for render.py - ffmpeg calls are mocked."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call


def _make_mark_and_clip(video_id: int) -> tuple[int, int]:
    from shortfarm import db
    mid = db.insert_mark(video_id, None, 5.0, 65.0)
    cid = db.insert_clip(video_id, mid, cut_mode="exact")
    return mid, cid


# ---------------------------------------------------------------------------
# render_clip success path
# ---------------------------------------------------------------------------

def test_render_clip_success(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.render import render_clip

    mid, cid = _make_mark_and_clip(video_in_db)

    fake = MagicMock()
    fake.returncode = 0

    with patch("shortfarm.render.require_binary", return_value="ffmpeg"), \
         patch("shortfarm.render.subprocess.run", return_value=fake), \
         patch("shortfarm.render.shutil.move") as mock_move:
        output = render_clip(cid)

    # Clip should be done
    row = db.get_clip(cid)
    assert row["status"] == "done"
    assert row["output_path"] is not None


# ---------------------------------------------------------------------------
# render_clip ffmpeg failure
# ---------------------------------------------------------------------------

def test_render_clip_ffmpeg_fails(video_in_db):
    from shortfarm import db
    from shortfarm.render import render_clip

    mid, cid = _make_mark_and_clip(video_in_db)

    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "ffmpeg error line\n" * 5

    with patch("shortfarm.render.require_binary", return_value="ffmpeg"), \
         patch("shortfarm.render.subprocess.run", return_value=fake), \
         pytest.raises(RuntimeError, match="ffmpeg failed"):
        render_clip(cid)

    assert db.get_clip(cid)["status"] == "failed"


def test_render_clip_missing_source_marks_failed(video_in_db):
    from shortfarm import db
    from shortfarm.render import render_clip

    video = db.get_video(video_in_db)
    Path(str(video["source_path"])).unlink()
    mid, cid = _make_mark_and_clip(video_in_db)

    with pytest.raises(FileNotFoundError, match="Source file gone"):
        render_clip(cid)

    row = db.get_clip(cid)
    assert row["status"] == "failed"
    assert "Source file gone" in row["error"]


# ---------------------------------------------------------------------------
# render_clip wrong status
# ---------------------------------------------------------------------------

def test_render_clip_wrong_status(video_in_db):
    from shortfarm import db
    from shortfarm.render import render_clip

    mid, cid = _make_mark_and_clip(video_in_db)
    db.set_clip_done(cid, "/fake/path.mp4")

    with pytest.raises(ValueError, match="'done'"):
        render_clip(cid)


# ---------------------------------------------------------------------------
# retry_failed_clips
# ---------------------------------------------------------------------------

def test_retry_failed_clips(video_in_db):
    from shortfarm import db
    from shortfarm.render import retry_failed_clips

    mid, cid = _make_mark_and_clip(video_in_db)
    db.set_clip_failed(cid, "timeout")

    reset_ids, skipped_ids = retry_failed_clips()
    assert cid in reset_ids
    assert skipped_ids == []
    assert db.get_clip(cid)["status"] == "queued"


def test_retry_failed_skips_existing_output(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.render import retry_failed_clips

    mid, cid = _make_mark_and_clip(video_in_db)
    existing = tmp_path / "out.mp4"
    existing.write_bytes(b"\x00")

    # Manually set output_path to an existing file, then fail
    db.set_clip_rendering(cid, str(tmp_path / "tmp.mp4"))
    with db.connect() as con:
        con.execute(
            "UPDATE clips SET status='failed', output_path=?, error='x' WHERE id=?",
            (str(existing), cid),
        )

    reset_ids, skipped_ids = retry_failed_clips()
    assert cid in skipped_ids
    assert db.get_clip(cid)["status"] == "failed"


# ---------------------------------------------------------------------------
# render_queued / render_all_queued
# ---------------------------------------------------------------------------

def test_render_queued_batch(video_in_db):
    from shortfarm import db
    from shortfarm.render import render_queued

    for i in range(3):
        mid = db.insert_mark(video_in_db, None, float(i * 70), float(i * 70 + 60))
        db.insert_clip(video_in_db, mid)

    fake = MagicMock()
    fake.returncode = 0

    with patch("shortfarm.render.require_binary", return_value="ffmpeg"), \
         patch("shortfarm.render.subprocess.run", return_value=fake), \
         patch("shortfarm.render.shutil.move"):
        results = render_queued(limit=2)

    assert len(results) == 2


def test_render_all_marks_preflight_failure_failed(video_in_db):
    from shortfarm import db
    from shortfarm.render import render_all_queued

    video = db.get_video(video_in_db)
    Path(str(video["source_path"])).unlink()
    mid, cid = _make_mark_and_clip(video_in_db)

    assert render_all_queued() == []
    assert db.get_clip(cid)["status"] == "failed"
