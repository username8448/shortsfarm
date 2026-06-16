"""Tests for mpv_session.py - mpv subprocess is mocked."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock


def _write_jsonl(path: Path, events: list[dict]) -> None:
    lines = [json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mock_mpv_run(session_file: Path, events: list[dict]):
    """Return a fake subprocess.run that writes *events* to session_file."""
    def _run(cmd, **kwargs):
        _write_jsonl(session_file, events)
        return MagicMock(returncode=0)
    return _run


# ---------------------------------------------------------------------------
# _parse_marks
# ---------------------------------------------------------------------------

def test_parse_marks_basic():
    from shortfarm.mpv_session import _parse_marks
    events = [
        {"event": "mark",       "in": 10.0, "out": 70.0},
        {"event": "quick_clip", "in": 80.0, "out": 140.0},
    ]
    marks = _parse_marks(events)
    assert len(marks) == 2
    assert marks[0]["in_sec"] == pytest.approx(10.0)


def test_parse_marks_undo():
    from shortfarm.mpv_session import _parse_marks
    events = [
        {"event": "mark", "in": 0.0, "out": 60.0},
        {"event": "mark", "in": 60.0, "out": 120.0},
        {"event": "undo"},
    ]
    marks = _parse_marks(events)
    assert len(marks) == 1
    assert marks[0]["in_sec"] == pytest.approx(0.0)


def test_parse_marks_undo_empty():
    from shortfarm.mpv_session import _parse_marks
    # undo on empty list should not crash
    marks = _parse_marks([{"event": "undo"}])
    assert marks == []


def test_parse_marks_invalid_range():
    from shortfarm.mpv_session import _parse_marks
    # out <= in should be skipped
    events = [{"event": "mark", "in": 100.0, "out": 50.0}]
    assert _parse_marks(events) == []


# ---------------------------------------------------------------------------
# _import_session  (no real mpv, file pre-written)
# ---------------------------------------------------------------------------

def _setup_session(video_in_db, tmp_path, events):
    from shortfarm import db
    session_file = tmp_path / "sess.jsonl"
    session_id   = db.create_review_session(video_in_db, str(session_file))
    db.update_video_review_status(video_in_db, "reviewing")
    _write_jsonl(session_file, events)
    db.close_review_session(session_id)
    return session_id, session_file


def test_import_done(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [
        {"event": "mark", "in": 10.0, "out": 70.0},
        {"event": "done"},
    ]
    sid, sf = _setup_session(video_in_db, tmp_path, events)
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.get_video(video_in_db)["review_status"] == "reviewed"
    assert db.count_marks(video_in_db) == 1
    assert db.count_clips(video_in_db) == 1
    assert db.get_review_session(sid)["status"] == "imported"


def test_import_skip(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    sid, sf = _setup_session(video_in_db, tmp_path, [{"event": "skip"}])
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.get_video(video_in_db)["review_status"] == "skipped"


def test_import_quit_returns_to_inbox(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [
        {"event": "mark", "in": 5.0, "out": 65.0},
        {"event": "quit"},
    ]
    sid, sf = _setup_session(video_in_db, tmp_path, events)
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.get_video(video_in_db)["review_status"] == "inbox"
    # marks are preserved
    assert db.count_marks(video_in_db) == 1
    assert "quit" in db.get_review_session(sid)["error"]


def test_import_no_final_event(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [{"event": "mark", "in": 0.0, "out": 60.0}]
    sid, sf = _setup_session(video_in_db, tmp_path, events)
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.get_video(video_in_db)["review_status"] == "inbox"
    assert "without done/skip/quit" in db.get_review_session(sid)["error"]


def test_import_missing_file(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    session_file = tmp_path / "nonexistent.jsonl"
    sid = db.create_review_session(video_in_db, str(session_file))
    db.update_video_review_status(video_in_db, "reviewing")

    _import_session(session_id=sid, video_id=video_in_db, session_file=session_file)

    assert db.get_review_session(sid)["status"] == "failed"
    assert db.get_video(video_in_db)["review_status"] == "inbox"


def test_import_bad_json_lines_skipped(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    session_file = tmp_path / "sess.jsonl"
    session_file.write_text(
        '{"event":"mark","in":0.0,"out":60.0}\n'
        'INVALID JSON LINE\n'
        '{"event":"done"}\n',
        encoding="utf-8",
    )
    sid = db.create_review_session(video_in_db, str(session_file))
    db.update_video_review_status(video_in_db, "reviewing")
    db.close_review_session(sid)

    _import_session(session_id=sid, video_id=video_in_db, session_file=session_file)

    # Good events processed, bad line skipped
    assert db.count_marks(video_in_db) == 1
    row = db.get_review_session(sid)
    assert row["status"] == "imported"
    assert row["error"] is not None   # warning recorded


def test_import_non_object_json_lines_skipped(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    session_file = tmp_path / "sess.jsonl"
    session_file.write_text(
        '{"event":"mark","in":0.0,"out":60.0}\n'
        '["not", "an", "event"]\n'
        '{"event":"done"}\n',
        encoding="utf-8",
    )
    sid = db.create_review_session(video_in_db, str(session_file))
    db.update_video_review_status(video_in_db, "reviewing")
    db.close_review_session(sid)

    _import_session(session_id=sid, video_id=video_in_db, session_file=session_file)

    assert db.count_marks(video_in_db) == 1
    row = db.get_review_session(sid)
    assert row["status"] == "imported"
    assert "expected JSON object event" in row["error"]


def test_import_invalid_rating_and_label_do_not_break_import(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [
        {"event": "mark", "in": 0.0, "out": 60.0, "rating": 99, "label": {"x": 1}},
        {"event": "done"},
    ]
    sid, sf = _setup_session(video_in_db, tmp_path, events)

    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    marks = db.list_marks(video_in_db)
    assert len(marks) == 1
    assert marks[0]["rating"] is None
    assert marks[0]["label"] is None
    assert db.get_review_session(sid)["status"] == "imported"


def test_import_undo_affects_clips(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [
        {"event": "mark", "in": 0.0,  "out": 60.0},
        {"event": "mark", "in": 60.0, "out": 120.0},
        {"event": "undo"},
        {"event": "done"},
    ]
    sid, sf = _setup_session(video_in_db, tmp_path, events)
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.count_marks(video_in_db) == 1
    assert db.count_clips(video_in_db) == 1


def test_import_is_idempotent_after_success(video_in_db, tmp_path):
    from shortfarm import db
    from shortfarm.mpv_session import _import_session

    events = [
        {"event": "mark", "in": 10.0, "out": 70.0},
        {"event": "done"},
    ]
    sid, sf = _setup_session(video_in_db, tmp_path, events)

    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)
    _import_session(session_id=sid, video_id=video_in_db, session_file=sf)

    assert db.count_marks(video_in_db) == 1
    assert db.count_clips(video_in_db) == 1
    assert db.get_review_session(sid)["status"] == "imported"


def test_launch_review_missing_mpv_records_failed_session(
    video_in_db,
    monkeypatch,
):
    from shortfarm import db
    from shortfarm.mpv_session import launch_review

    db.update_video_review_status(video_in_db, "reviewing")
    monkeypatch.setattr("shortfarm.mpv_session.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="mpv failed to start"):
        launch_review(video_in_db)

    sessions = db.list_review_sessions(video_in_db)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "failed"
    assert db.get_video(video_in_db)["review_status"] == "inbox"
