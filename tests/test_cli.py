"""CLI tests via Typer's CliRunner (no subprocess)."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# init / doctor
# ---------------------------------------------------------------------------

def test_init(runner):
    from shortfarm.cli import app
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Initialized" in result.output


def test_doctor_missing_ffmpeg(runner, tmp_data_dir):
    from shortfarm.cli import app
    with patch("shortfarm.ffmpeg_tools.shutil.which", return_value=None):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code != 0 or "ERROR" in (result.output + result.stderr)


# ---------------------------------------------------------------------------
# videos / add
# ---------------------------------------------------------------------------

def test_videos_empty(runner):
    from shortfarm.cli import app
    result = runner.invoke(app, ["videos"])
    assert result.exit_code == 0
    assert "No videos" in result.output


def test_add_and_list(runner, dummy_video):
    from shortfarm.cli import app
    with patch("shortfarm.services.probe_duration", return_value=60.0):
        r = runner.invoke(app, ["add", str(dummy_video)])
    assert r.exit_code == 0
    assert "Added video" in r.output

    r2 = runner.invoke(app, ["videos"])
    assert dummy_video.stem in r2.output


def test_add_missing_file(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["add", "/no/such/video.mp4"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------

def test_inbox_empty(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["inbox"])
    assert result.exit_code == 0


def test_inbox_shows_video(runner, tmp_data_dir, dummy_video):
    from shortfarm.cli import app
    from shortfarm import db

    db.add_video(dummy_video, dummy_video.stem, 60.0)

    result = runner.invoke(app, ["inbox"])
    assert result.exit_code == 0
    assert dummy_video.stem in result.output
    assert "inbox" in result.output


# ---------------------------------------------------------------------------
# marks / skip
# ---------------------------------------------------------------------------

def test_marks_empty(runner, video_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["marks", str(video_in_db)])
    assert result.exit_code == 0
    assert "No marks" in result.output


def test_marks_with_data(runner, mark_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["marks", str(mark_in_db["video_id"])])
    assert result.exit_code == 0
    assert "10.00" in result.output


def test_skip_cmd(runner, video_in_db):
    from shortfarm.cli import app
    from shortfarm import db
    result = runner.invoke(app, ["skip", str(video_in_db)])
    assert result.exit_code == 0
    assert db.get_video(video_in_db)["review_status"] == "skipped"


# ---------------------------------------------------------------------------
# review sub-commands
# ---------------------------------------------------------------------------

def test_review_inbox_no_videos(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["review", "inbox"])
    assert result.exit_code == 0
    assert "No inbox" in result.output


def test_review_open_id_already_reviewing(runner, video_in_db):
    from shortfarm.cli import app
    from shortfarm import db
    db.update_video_review_status(video_in_db, "reviewing")
    result = runner.invoke(app, ["review", "open-id", str(video_in_db)])
    assert result.exit_code != 0


def test_review_reset(runner, video_in_db, tmp_path):
    from shortfarm.cli import app
    from shortfarm import db
    db.update_video_review_status(video_in_db, "reviewing")
    db.create_review_session(video_in_db, str(tmp_path / "s.jsonl"))

    result = runner.invoke(app, ["review", "reset", str(video_in_db)])
    assert result.exit_code == 0
    assert db.get_video(video_in_db)["review_status"] == "inbox"


# ---------------------------------------------------------------------------
# clips
# ---------------------------------------------------------------------------

def test_clips_empty(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["clips"])
    assert result.exit_code == 0
    assert "No clips" in result.output


def test_clips_list(runner, mark_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["clips", "--status", "queued"])
    assert result.exit_code == 0
    assert "queued" in result.output


# ---------------------------------------------------------------------------
# render / retry-failed
# ---------------------------------------------------------------------------

def test_render_no_queued(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["render"])
    assert result.exit_code == 0
    assert "No queued" in result.output


def test_render_all_no_queued(runner, tmp_data_dir):
    from shortfarm.cli import app
    result = runner.invoke(app, ["render-all"])
    assert result.exit_code == 0
    assert "No queued" in result.output


def test_fast_split_with_skip(runner, video_in_db):
    from shortfarm.cli import app
    from shortfarm import db

    def fake_cut(input_path, output_path, start_sec, end_sec):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return output_path

    with patch("shortfarm.services.fast_cut_range", side_effect=fake_cut):
        result = runner.invoke(
            app,
            [
                "fast-split",
                str(video_in_db),
                "--seconds",
                "60",
                "--skip",
                "start-00:30",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "segments: 2" in result.output
    rows = db.list_segments(video_in_db)
    assert [(row["start_sec"], row["end_sec"]) for row in rows] == [
        (30.0, 90.0),
        (90.0, 120.0),
    ]


# ---------------------------------------------------------------------------
# new user-facing CLI UX
# ---------------------------------------------------------------------------

def test_split_file_default_creates_segments(runner, tmp_data_dir, dummy_video):
    from shortfarm.cli import app
    from shortfarm import db

    def fake_cut(input_path, output_path, start_sec, end_sec):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return output_path

    with patch("shortfarm.services.probe_duration", return_value=125.0), \
         patch("shortfarm.services.fast_cut_range", side_effect=fake_cut) as cut:
        result = runner.invoke(app, ["split", str(dummy_video)])

    assert result.exit_code == 0, result.output
    assert "segments: 3" in result.output
    assert str(tmp_data_dir / "output" / "split" / dummy_video.stem) in result.output
    assert cut.call_count == 3

    rows = db.list_videos()
    assert len(rows) == 1
    segments = db.list_segments(int(rows[0]["id"]))
    assert [(row["start_sec"], row["end_sec"]) for row in segments] == [
        (0.0, 60.0),
        (60.0, 120.0),
        (120.0, 125.0),
    ]


def test_split_file_with_skip_ranges(runner, dummy_video):
    from shortfarm.cli import app
    from shortfarm import db

    def fake_cut(input_path, output_path, start_sec, end_sec):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return output_path

    with patch("shortfarm.services.probe_duration", return_value=180.0), \
         patch("shortfarm.services.fast_cut_range", side_effect=fake_cut):
        result = runner.invoke(
            app,
            [
                "split",
                str(dummy_video),
                "--skip",
                "start-00:30",
                "--skip",
                "02:00-end",
            ],
        )

    assert result.exit_code == 0, result.output
    video_id = int(db.list_videos()[0]["id"])
    segments = db.list_segments(video_id)
    assert [(row["start_sec"], row["end_sec"]) for row in segments] == [
        (30.0, 90.0),
        (90.0, 120.0),
    ]


def test_split_file_dry_run_does_not_touch_db_or_ffmpeg(runner, dummy_video):
    from shortfarm.cli import app
    from shortfarm import db

    with patch("shortfarm.services.probe_duration", return_value=125.0), \
         patch("shortfarm.services.fast_cut_range") as cut:
        result = runner.invoke(app, ["split", str(dummy_video), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "segments: 3" in result.output
    cut.assert_not_called()
    assert db.count_videos() == 0
    assert db.count_segments() == 0


def test_split_folder_continues_after_one_file_error(runner, tmp_path):
    from shortfarm.cli import app

    folder = tmp_path / "videos"
    folder.mkdir()
    good = folder / "good.mp4"
    bad = folder / "bad.mp4"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    def fake_probe(path):
        if Path(path).name == "bad.mp4":
            raise RuntimeError("probe failed")
        return 61.0

    def fake_cut(input_path, output_path, start_sec, end_sec):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return output_path

    with patch("shortfarm.services.probe_duration", side_effect=fake_probe), \
         patch("shortfarm.services.fast_cut_range", side_effect=fake_cut):
        result = runner.invoke(app, ["split-folder", str(folder)])

    assert result.exit_code == 0, result.output
    assert "processed: 1" in result.output
    assert "failed:    1" in result.output
    assert "probe failed" in result.output


def test_review_file_auto_adds_and_launches(runner, dummy_video):
    from shortfarm.cli import app
    from shortfarm import db

    with patch("shortfarm.services.probe_duration", return_value=60.0), \
         patch("shortfarm.cli._do_launch_review") as launch:
        result = runner.invoke(app, ["review", str(dummy_video)])

    assert result.exit_code == 0, result.output
    launch.assert_called_once()
    video_id = int(db.list_videos()[0]["id"])
    assert launch.call_args.args == (video_id,)
    assert db.get_video(video_id)["review_status"] == "reviewing"


def test_review_next_user_alias(runner, video_in_db):
    from shortfarm.cli import app
    from shortfarm import db

    with patch("shortfarm.cli._do_launch_review") as launch:
        result = runner.invoke(app, ["review-next"])

    assert result.exit_code == 0, result.output
    launch.assert_called_once_with(video_in_db)
    assert db.get_video(video_in_db)["review_status"] == "reviewing"


def test_status_summary(runner, mark_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "Videos:" in result.output
    assert "Queue:" in result.output
    assert "queued clips" in result.output


def test_status_for_file(runner, dummy_video, video_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["status", str(dummy_video)])
    assert result.exit_code == 0, result.output
    assert "review_status" in result.output
    assert "segments:" in result.output


def test_queue_user_command(runner, mark_in_db):
    from shortfarm.cli import app
    result = runner.invoke(app, ["queue"])
    assert result.exit_code == 0, result.output
    assert "Clips:" in result.output
    assert "queued" in result.output


def test_debug_aliases(runner, dummy_video, video_in_db):
    from shortfarm.cli import app

    with patch("shortfarm.services.probe_duration", return_value=60.0):
        result = runner.invoke(app, ["debug", "add", str(dummy_video)])
    assert result.exit_code == 0, result.output

    for args in (
        ["debug", "inbox"],
        ["debug", "jobs"],
        ["debug", "clips"],
        ["debug", "marks", str(video_in_db)],
        ["debug", "segments", str(video_in_db)],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_debug_review_id(runner, video_in_db):
    from shortfarm.cli import app

    with patch("shortfarm.cli._do_launch_review") as launch:
        result = runner.invoke(app, ["debug", "review-id", str(video_in_db)])

    assert result.exit_code == 0, result.output
    launch.assert_called_once_with(video_in_db)


def test_debug_split_id(runner, video_in_db):
    from shortfarm.cli import app

    def fake_split(input_path, output_dir, output_pattern, segment_seconds, mode):
        output_dir.mkdir(parents=True, exist_ok=True)
        first = output_dir / "part_0001.mp4"
        first.write_bytes(b"x")
        return [first]

    with patch("shortfarm.services.split_video", side_effect=fake_split):
        result = runner.invoke(app, ["debug", "split-id", str(video_in_db)])

    assert result.exit_code == 0, result.output
    assert "segments: 1" in result.output


def test_render_calls_ffmpeg(runner, mark_in_db, tmp_path):
    """Render should call ffmpeg and mark clip as done."""
    from shortfarm.cli import app
    from shortfarm import db

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""

    with patch("shortfarm.render.require_binary", return_value="ffmpeg"), \
         patch("shortfarm.render.subprocess.run", return_value=fake_result), \
         patch("shortfarm.render.shutil.move") as mock_move:

        result = runner.invoke(app, ["render", "--limit", "5"])

    assert result.exit_code == 0
    # the clip should now be in a terminal state
    from shortfarm import db
    clip = db.get_clip(mark_in_db["clip_id"])
    assert clip["status"] in ("done", "failed")


def test_retry_failed(runner, mark_in_db):
    from shortfarm.cli import app
    from shortfarm import db
    db.set_clip_failed(mark_in_db["clip_id"], "some error")

    result = runner.invoke(app, ["retry-failed"])
    assert result.exit_code == 0
    assert db.get_clip(mark_in_db["clip_id"])["status"] == "queued"


def test_clean_removes_only_known_temp_files(runner, mark_in_db):
    from shortfarm.cli import app
    from shortfarm import db
    from shortfarm.config import output_dir

    temp = output_dir() / "clips" / "clip_000001.tmp.mp4"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(b"temp")

    with db.connect() as con:
        con.execute(
            "UPDATE clips SET status='failed', temp_path=? WHERE id=?",
            (str(temp), mark_in_db["clip_id"]),
        )

    result = runner.invoke(app, ["clean"])

    assert result.exit_code == 0, result.output
    assert "temp files removed: 1" in result.output
    assert not temp.exists()


def test_youtube_profiles_cmd(runner):
    from shortfarm.cli import app
    from shortfarm import db

    db.create_youtube_oauth_profile(
        name="CLI Profile",
        mode="custom",
        client_id="cli-client",
        client_secret="cli-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )

    result = runner.invoke(app, ["youtube", "profiles"])

    assert result.exit_code == 0, result.output
    assert "CLI Profile" in result.output


def test_youtube_accounts_cmd(runner):
    from shortfarm.cli import app
    from shortfarm import db

    profile_id = db.create_youtube_oauth_profile(
        name="CLI Profile",
        mode="custom",
        client_id="cli-client",
        client_secret="cli-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    db.save_social_account(
        platform="youtube",
        display_name="CLI Account",
        channel_id="cli-channel",
        channel_title="CLI Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=profile_id,
        status="active",
    )

    result = runner.invoke(app, ["youtube", "accounts"])

    assert result.exit_code == 0, result.output
    assert "CLI Channel" in result.output


def test_youtube_connect_cmd(runner):
    from shortfarm.cli import app

    with patch("shortfarm.web.api.youtube_connect_start", return_value={
        "auth_url": "https://accounts.google.com/o/oauth2/auth?state=test",
        "oauth_profile_id": 1,
        "profile_name": "CLI Profile",
    }):
        result = runner.invoke(app, ["youtube", "connect", "--profile-id", "1"])

    assert result.exit_code == 0, result.output
    assert "CLI Profile" in result.output
    assert "https://accounts.google.com/" in result.output


def test_stop_no_pid_file(runner):
    from shortfarm.cli import app

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0, result.output
    assert "not running" in result.output


def test_stop_sends_sigterm_and_removes_pid_file(runner, tmp_data_dir):
    from shortfarm.cli import app

    pid_path = tmp_data_dir / "web.pid"
    pid_path.write_text("12345\n", encoding="utf-8")

    with patch("shortfarm.cli._is_process_alive", return_value=True), patch("shortfarm.cli.os.kill") as kill:
        result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0, result.output
    kill.assert_called_once()
    assert kill.call_args.args[0] == 12345
    assert "Stopped ShortFarm web server PID 12345" in result.output
    assert not pid_path.exists()


def test_publish_worker_once(runner, tmp_path):
    from shortfarm.cli import app
    from shortfarm import db

    profile_id = db.create_youtube_oauth_profile(
        name="Worker Profile",
        mode="custom",
        client_id="worker-client",
        client_secret="worker-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    account_id = db.save_social_account(
        platform="youtube",
        display_name="Worker Account",
        channel_id="worker-channel",
        channel_title="Worker Channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=profile_id,
        status="active",
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    video_id = db.add_video(source, "worker-video", 60.0)
    mark_id = db.insert_mark(video_id, None, 0.0, 10.0)
    clip_id = db.insert_clip(video_id, mark_id)
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"clip")
    db.set_clip_done(clip_id, str(output))
    job_id = db.create_publish_job(
        account_id=account_id,
        clip_id=clip_id,
        title="Worker Upload",
        description="Desc",
        tags='["one"]',
        category_id="22",
        privacy_status="private",
        publish_mode="private",
        publish_at=None,
        made_for_kids=False,
    )

    with patch("shortfarm.publish_youtube.run_publish_worker", return_value=1) as worker:
        result = runner.invoke(app, ["publish-worker", "--once", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "Processed publish jobs: 1" in result.output
    worker.assert_called_once()
