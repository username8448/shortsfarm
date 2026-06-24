"""Tests for services.py."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

def test_safe_filename_basic():
    from shortsfarm.services import safe_filename
    assert safe_filename("hello world") == "hello_world"


def test_safe_filename_forbidden_chars():
    from shortsfarm.services import safe_filename
    assert "/" not in safe_filename("video: part/1")


def test_safe_filename_empty():
    from shortsfarm.services import safe_filename
    assert safe_filename("") == "video"
    assert safe_filename("...") == "video"


def test_safe_filename_truncates():
    from shortsfarm.services import safe_filename
    assert len(safe_filename("a" * 200)) == 100


# ---------------------------------------------------------------------------
# add_video
# ---------------------------------------------------------------------------

def test_add_video_success(dummy_video):
    from shortsfarm.services import add_video
    with patch("shortsfarm.services.probe_duration", return_value=90.0):
        vid = add_video(dummy_video)
    assert isinstance(vid, int)


def test_add_video_not_found(tmp_data_dir):
    from shortsfarm.services import add_video
    with pytest.raises(FileNotFoundError):
        add_video(Path("/no/such/file.mp4"))


def test_add_video_directory(tmp_path):
    from shortsfarm.services import add_video
    with pytest.raises(ValueError):
        add_video(tmp_path)          # tmp_path is a directory, not a file


# ---------------------------------------------------------------------------
# list_input_videos
# ---------------------------------------------------------------------------

def test_list_input_videos(tmp_data_dir):
    from shortsfarm.services import list_input_videos
    from shortsfarm.config import input_dir
    inp = input_dir()
    (inp / "clip.mp4").write_bytes(b"\x00")
    (inp / "clip.mkv").write_bytes(b"\x00")
    (inp / "readme.txt").write_bytes(b"x")
    names = {p.name for p in list_input_videos()}
    assert "clip.mp4" in names
    assert "clip.mkv" in names
    assert "readme.txt" not in names


# ---------------------------------------------------------------------------
# fast split skip parsing
# ---------------------------------------------------------------------------

def test_parse_skip_ranges_wrapper():
    from shortsfarm.services import parse_skip_ranges
    assert parse_skip_ranges(
        ["skip(start-00:07:30, 01:37:00-end)"],
        7200.0,
    ) == [(0.0, 450.0), (5820.0, 7200.0)]


def test_parse_skip_ranges_multiple_specs_and_mmss():
    from shortsfarm.services import parse_skip_ranges
    assert parse_skip_ranges(
        ["20:00-25:00", "00:30:00-00:31:00"],
        4000.0,
    ) == [(1200.0, 1500.0), (1800.0, 1860.0)]


def test_parse_timecode_accepts_seconds_mmss_and_hhmmss():
    from shortsfarm.services import parse_timecode
    assert parse_timecode("75", 1000.0) == 75.0
    assert parse_timecode("01:15", 1000.0) == 75.0
    assert parse_timecode("01:01:15", 10000.0) == 3675.0


def test_parse_skip_ranges_clamps_and_rejects_empty_after_clamp():
    from shortsfarm.services import parse_skip_ranges
    with pytest.raises(ValueError, match="after clamping"):
        parse_skip_ranges(["02:00-end"], 120.0)


def test_parse_skip_ranges_merges_overlaps():
    from shortsfarm.services import parse_skip_ranges
    assert parse_skip_ranges(
        ["00:10-00:30", "00:20-00:50"],
        100.0,
    ) == [(10.0, 50.0)]


def test_build_keep_intervals_no_skip():
    from shortsfarm.services import build_keep_intervals
    assert build_keep_intervals(120.0, []) == [(0.0, 120.0)]


def test_build_keep_intervals_skip_start_end_and_middle():
    from shortsfarm.services import build_keep_intervals
    assert build_keep_intervals(
        100.0,
        [(0.0, 10.0), (40.0, 50.0), (90.0, 100.0)],
    ) == [(10.0, 40.0), (50.0, 90.0)]


def test_build_keep_intervals_merges_skips():
    from shortsfarm.services import build_keep_intervals
    keep = build_keep_intervals(
        100.0,
        [(0.0, 10.0), (20.0, 30.0), (25.0, 50.0), (90.0, 100.0)],
    )
    assert keep == [(10.0, 20.0), (50.0, 90.0)]


def test_build_segment_ranges_keeps_short_tail():
    from shortsfarm.services import build_segment_ranges
    assert build_segment_ranges([(30.0, 125.0)], 60) == [
        (30.0, 90.0),
        (90.0, 125.0),
    ]


def test_split_video_file_dry_run_does_not_add_video(dummy_video):
    from shortsfarm import db
    from shortsfarm.services import split_video_file

    with patch("shortsfarm.services.probe_duration", return_value=121.0), \
         patch("shortsfarm.services.fast_cut_range") as cut:
        result = split_video_file(dummy_video, dry_run=True)

    assert result.dry_run is True
    assert result.video_id is None
    assert result.job_id is None
    assert result.segment_ranges == [(0.0, 60.0), (60.0, 120.0), (120.0, 121.0)]
    cut.assert_not_called()
    assert db.count_videos() == 0
    assert db.count_segments() == 0


def test_split_video_file_raises_when_skip_covers_everything(dummy_video):
    from shortsfarm.services import split_video_file

    with patch("shortsfarm.services.probe_duration", return_value=60.0):
        with pytest.raises(RuntimeError, match="cover the whole video"):
            split_video_file(dummy_video, skip_specs=["start-end"], dry_run=True)


def test_split_managed_source_uses_workspace_cuts_tree(tmp_path):
    from shortsfarm import db
    from shortsfarm.services import split_video_file
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "managed")
    source = root / "sources" / "Автор" / "Подкаст" / "Выпуск 001" / "original.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")

    def fake_cut(input_path, output_path, start_sec, end_sec):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"segment")
        return output_path

    with patch("shortsfarm.services.probe_duration", return_value=61.0), \
         patch("shortsfarm.services.fast_cut_range", side_effect=fake_cut):
        result = split_video_file(
            source,
            run_timestamp="run-001",
        )

    expected = (
        root / "cuts" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "original" / "run-001"
    )
    assert result.output_dir == expected
    assert [path.parent for path in result.files] == [expected, expected]
    assert [Path(row["path"]).parent for row in db.list_segments(result.video_id)] == [
        expected,
        expected,
    ]


def test_split_managed_source_dry_run_plans_without_creating_output(tmp_path):
    from shortsfarm.services import split_video_file
    from shortsfarm.workspace_fs import set_workspace_root

    root = set_workspace_root(tmp_path / "managed-dry")
    source = root / "sources" / "episode.mp4"
    source.write_bytes(b"source")

    with patch("shortsfarm.services.probe_duration", return_value=30.0):
        result = split_video_file(
            source,
            dry_run=True,
            run_timestamp="dry-run",
        )

    expected = root / "cuts" / "episode" / "original" / "dry-run"
    assert result.output_dir == expected
    assert not expected.exists()


def test_split_external_source_keeps_legacy_output_path(tmp_path):
    from shortsfarm.config import output_dir
    from shortsfarm.services import split_video_file
    from shortsfarm.workspace_fs import set_workspace_root

    set_workspace_root(tmp_path / "managed-external")
    source = tmp_path / "external.mp4"
    source.write_bytes(b"source")

    with patch("shortsfarm.services.probe_duration", return_value=30.0):
        result = split_video_file(
            source,
            dry_run=True,
            run_timestamp="legacy",
        )

    assert result.output_dir == output_dir() / "split" / "external" / "legacy"


# ---------------------------------------------------------------------------
# open_video_for_review
# ---------------------------------------------------------------------------

def test_open_review_inbox(video_in_db):
    from shortsfarm import db
    from shortsfarm.services import open_video_for_review
    open_video_for_review(video_in_db, force=False)
    assert db.get_video(video_in_db)["review_status"] == "reviewing"


def test_open_review_already_reviewing(video_in_db):
    from shortsfarm import db
    from shortsfarm.services import open_video_for_review
    db.update_video_review_status(video_in_db, "reviewing")
    with pytest.raises(ValueError, match="already being reviewed"):
        open_video_for_review(video_in_db)


def test_open_review_done_no_force(video_in_db):
    from shortsfarm import db
    from shortsfarm.services import open_video_for_review
    db.update_video_review_status(video_in_db, "reviewed")
    with pytest.raises(ValueError, match="--force"):
        open_video_for_review(video_in_db, force=False)


def test_open_review_done_with_force(video_in_db):
    from shortsfarm import db
    from shortsfarm.services import open_video_for_review
    db.update_video_review_status(video_in_db, "reviewed")
    open_video_for_review(video_in_db, force=True)
    assert db.get_video(video_in_db)["review_status"] == "reviewing"


# ---------------------------------------------------------------------------
# reset_video_review
# ---------------------------------------------------------------------------

def test_reset_video_review(video_in_db, tmp_path):
    from shortsfarm import db
    from shortsfarm.services import reset_video_review
    db.update_video_review_status(video_in_db, "reviewing")
    db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))
    abandoned = reset_video_review(video_in_db)
    assert abandoned == 1
    assert db.get_video(video_in_db)["review_status"] == "inbox"
