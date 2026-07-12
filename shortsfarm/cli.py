from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import typer

from . import db
from .config import data_dir, db_path, ensure_dirs, input_dir, logs_dir, output_dir
from .ffmpeg_tools import require_binary
from .services import (
    FileSplitResult,
    add_video,
    fast_split_video,
    get_or_add_video,
    open_video_for_review,
    reset_video_review,
    split_all_input_videos,
    split_existing_video,
    split_file,
    split_first_input_video,
    split_video_file,
    split_video_folder,
)


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="shortsfarm",
    help="Local CLI tool for simple video splitting and MPV-assisted review.",
    no_args_is_help=True,
)

debug_app = typer.Typer(
    name="debug",
    help="Internal/advanced commands kept for compatibility and debugging.",
    no_args_is_help=True,
)
youtube_app = typer.Typer(
    name="youtube",
    help="Advanced YouTube OAuth/profile helpers.",
    no_args_is_help=True,
)
app.add_typer(debug_app, name="debug")
app.add_typer(youtube_app, name="youtube")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def die(message: str) -> None:
    typer.secho(f"ERROR: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    sec = total % 60
    return f"{hours:02d}:{minutes:02d}:{sec:02d}" if hours else f"{minutes:02d}:{sec:02d}"


def format_seconds(seconds: float) -> str:
    if float(seconds).is_integer():
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def format_interval(start: float, end: float) -> str:
    return f"{format_duration(start)}-{format_duration(end)}"


def _is_int_token(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def _extract_flag(args: list[str], *names: str) -> tuple[bool, list[str]]:
    found = False
    rest: list[str] = []
    for item in args:
        if item in names:
            found = True
        else:
            rest.append(item)
    return found, rest


def _resolve_existing_video_path(path: Path) -> tuple[Path, int]:
    resolved = path.expanduser().resolve()
    row = db.get_video_by_source_path(resolved)
    if row is None:
        raise ValueError(
            f"File is not in ShortsFarm yet: {resolved}\n"
            "Use 'shortsfarm split <file>' or 'shortsfarm review <file>' first."
        )
    return resolved, int(row["id"])


def _latest_output_dir(video_id: int | None = None) -> str:
    latest = db.latest_segment_path(video_id)
    if latest is None:
        return "-"
    return str(Path(latest).parent)


# ---------------------------------------------------------------------------
# Output helpers for internal listings
# ---------------------------------------------------------------------------

def _print_videos() -> None:
    rows = db.list_videos()
    if not rows:
        typer.echo("No videos.")
        return
    typer.echo(f"{'ID':>5}  {'DURATION':>10}  {'REVIEW':>10}  TITLE")
    typer.echo("-" * 70)
    for row in rows:
        typer.echo(
            f"{row['id']:>5}  "
            f"{format_duration(row['duration_sec']):>10}  "
            f"{(row['review_status'] or '-'):>10}  "
            f"{row['title']}"
        )


def _print_inbox() -> None:
    rows = db.list_videos_with_counts()
    if not rows:
        typer.echo("No videos.")
        return

    typer.echo(f"{'ID':>5}  {'REVIEW':>10}  {'MARKS':>6}  {'CLIPS':>6}  {'DURATION':>10}  TITLE")
    typer.echo("-" * 90)
    for row in rows:
        typer.echo(
            f"{row['id']:>5}  "
            f"{(row['review_status'] or 'inbox'):>10}  "
            f"{row['mark_count']:>6}  "
            f"{row['clip_count']:>6}  "
            f"{format_duration(row['duration_sec']):>10}  "
            f"{row['title']}"
        )


def _print_jobs(limit: int = 50) -> None:
    rows = db.list_jobs(limit)
    if not rows:
        typer.echo("No jobs.")
        return
    typer.echo(f"{'ID':>5}  {'VIDEO':>5}  {'STATUS':>10}  {'MODE':>8}  TITLE")
    typer.echo("-" * 80)
    for row in rows:
        video_id = row["video_id"] if row["video_id"] is not None else "-"
        mode = row["mode"] or "-"
        title = row["video_title"] or row["type"]
        typer.echo(
            f"{row['id']:>5}  {video_id:>5}  "
            f"{row['status']:>10}  {mode:>8}  {title}"
        )


def _print_segments(video_id: int, job_id: int | None = None) -> None:
    rows = db.list_segments(video_id, job_id)
    if not rows:
        typer.echo("No segments.")
        return
    typer.echo(f"{'IDX':>5}  {'START':>8}  {'END':>8}  PATH")
    typer.echo("-" * 100)
    for row in rows:
        typer.echo(
            f"{row['segment_index']:>5}  "
            f"{row['start_sec']:>8.0f}  "
            f"{row['end_sec']:>8.0f}  "
            f"{row['path']}"
        )


def _print_youtube_profiles() -> None:
    rows = db.list_youtube_oauth_profiles()
    if not rows:
        typer.echo("No YouTube OAuth Profiles.")
        return
    typer.echo(f"{'ID':>5}  {'DEFAULT':>7}  {'STATUS':>10}  {'MODE':>8}  NAME")
    typer.echo("-" * 80)
    for row in rows:
        typer.echo(
            f"{row['id']:>5}  "
            f"{('yes' if row['is_default'] else '-'):>7}  "
            f"{(row['status'] or 'active'):>10}  "
            f"{(row['mode'] or 'custom'):>8}  "
            f"{row['name']}"
        )


def _print_social_accounts() -> None:
    rows = db.list_social_accounts(platform="youtube")
    if not rows:
        typer.echo("No YouTube accounts.")
        return
    typer.echo(f"{'ID':>5}  {'PROFILE':>18}  {'STATUS':>12}  CHANNEL")
    typer.echo("-" * 100)
    for row in rows:
        channel = row["channel_title"] or row["display_name"] or "-"
        profile_name = row["profile_name"] or "-"
        typer.echo(
            f"{row['id']:>5}  "
            f"{profile_name[:18]:>18}  "
            f"{(row['status'] or 'active'):>12}  "
            f"{channel}"
        )


def _print_marks(video_id: int) -> None:
    rows = db.list_marks(video_id)
    if not rows:
        typer.echo("No marks.")
        return
    typer.echo(f"{'ID':>6}  {'IN':>10}  {'OUT':>10}  {'DUR':>8}  {'RATING':>6}  LABEL")
    typer.echo("-" * 70)
    for row in rows:
        dur = row["out_sec"] - row["in_sec"]
        typer.echo(
            f"{row['id']:>6}  "
            f"{row['in_sec']:>10.2f}  "
            f"{row['out_sec']:>10.2f}  "
            f"{dur:>8.2f}  "
            f"{str(row['rating']) if row['rating'] else '-':>6}  "
            f"{row['label'] or ''}"
        )


def _print_clips(
    status: str | None = None,
    video_id: int | None = None,
    limit: int = 500,
) -> None:
    rows = db.list_clips(status=status, video_id=video_id, limit=limit)
    if not rows:
        typer.echo("No clips.")
        return
    typer.echo(f"{'ID':>6}  {'VIDEO':>5}  {'STATUS':>10}  {'CUT':>5}  OUTPUT")
    typer.echo("-" * 90)
    for row in rows:
        out = row["output_path"] or "-"
        typer.echo(
            f"{row['id']:>6}  {row['video_id']:>5}  "
            f"{row['status']:>10}  {row['cut_mode']:>5}  {out}"
        )


def _print_split_result(result: FileSplitResult) -> None:
    if result.dry_run:
        typer.echo("Dry run. Nothing was written.")
    else:
        typer.echo("Done.")
        typer.echo(f"  video_id: {result.video_id}")
        typer.echo(f"  job_id:   {result.job_id}")
    typer.echo(f"  file:     {result.source_path}")
    typer.echo(f"  duration: {format_duration(result.duration_sec)}")
    typer.echo(f"  segments: {len(result.segment_ranges)}")
    typer.echo(f"  output:   {result.output_dir}")
    if result.segment_ranges:
        typer.echo("  plan:")
        for index, (start, end) in enumerate(result.segment_ranges, start=1):
            typer.echo(
                f"    {index:04d}: {format_interval(start, end)} "
                f"({format_seconds(end - start)} sec)"
            )


def _print_old_split_result(video_id: int, job_id: int, files: list[Path]) -> None:
    typer.echo("Done.")
    typer.echo(f"  video_id: {video_id}")
    typer.echo(f"  job_id:   {job_id}")
    typer.echo(f"  segments: {len(files)}")
    if files:
        typer.echo(f"  output:   {files[0].parent}")


# ---------------------------------------------------------------------------
# Init / doctor
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Create local data folders and run database migrations."""
    ensure_dirs()
    db.init_db()
    typer.echo("Initialized ShortsFarm data directory:")
    typer.echo(f"  data:   {data_dir()}")
    typer.echo(f"  input:  {input_dir()}")
    typer.echo(f"  output: {output_dir()}")
    typer.echo(f"  logs:   {logs_dir()}")
    typer.echo(f"  db:     {db_path()}")


@app.command()
def doctor() -> None:
    """Check FFmpeg, mpv, Lua script and local data directory."""
    try:
        ensure_dirs()
        db.init_db()

        from .mpv_session import LUA_SCRIPT, require_mpv

        ffmpeg = require_binary("ffmpeg")
        ffprobe = require_binary("ffprobe")
        mpv = require_mpv()

        lua_ok = LUA_SCRIPT.exists()

        typer.echo("OK" if lua_ok else "WARN")
        typer.echo(f"  ffmpeg:  {ffmpeg}")
        typer.echo(f"  ffprobe: {ffprobe}")
        typer.echo(f"  mpv:     {mpv}")
        typer.echo(f"  lua:     {LUA_SCRIPT}  {'OK' if lua_ok else 'MISSING'}")
        typer.echo(f"  data:    {data_dir()}")
        typer.echo(f"  db:      {db_path()}")

        if not lua_ok:
            typer.secho(
                f"WARNING: Lua script not found at {LUA_SCRIPT}",
                fg=typer.colors.YELLOW,
                err=True,
            )

    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Videos and old compatible listings
# ---------------------------------------------------------------------------

@app.command("add", hidden=True)
def add_cmd(
    path: Path = typer.Argument(..., help="Path to video file"),
) -> None:
    """Compatibility alias: add a video file to the database."""
    try:
        db.init_db()
        video_id = add_video(path)
        typer.echo(f"Added video: {video_id}")
    except Exception as exc:
        die(str(exc))


@app.command("videos", hidden=True)
def videos_cmd() -> None:
    """Compatibility alias: list all videos in the database."""
    try:
        db.init_db()
        _print_videos()
    except Exception as exc:
        die(str(exc))


@app.command("inbox", hidden=True)
def inbox_cmd() -> None:
    """Compatibility alias: show videos with review counters."""
    try:
        ensure_dirs()
        db.init_db()
        _print_inbox()
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# User split workflow
# ---------------------------------------------------------------------------

@app.command("split")
def split_cmd(
    target: str = typer.Argument(..., help="Video file path"),
    seconds: int = typer.Option(60, "--seconds", "-s", help="Segment length in seconds"),
    skip: list[str] = typer.Option(
        [],
        "--skip",
        "-k",
        help="Skip range: start-00:07:30, 00:20-00:25, or skip(...)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show planned segments"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace output folder if it exists"),
    mode: str = typer.Option(
        "fast",
        "--mode",
        "-m",
        help="Old ID fallback mode: fast or exact",
        hidden=True,
    ),
) -> None:
    """Split a video file into fast stream-copy chunks."""
    try:
        db.init_db()
        target_path = Path(target).expanduser()

        if _is_int_token(target) and not target_path.exists():
            job_id, files = split_existing_video(int(target), seconds, mode, overwrite)
            _print_old_split_result(int(target), job_id, files)
            return

        result = split_video_file(
            Path(target),
            segment_seconds = seconds,
            skip_specs      = skip,
            dry_run         = dry_run,
            overwrite       = overwrite,
        )
        _print_split_result(result)
    except Exception as exc:
        die(str(exc))


@app.command("split-folder")
def split_folder_cmd(
    folder: Path = typer.Argument(..., help="Folder with video files"),
    seconds: int = typer.Option(60, "--seconds", "-s", help="Segment length in seconds"),
    skip: list[str] = typer.Option(
        [],
        "--skip",
        "-k",
        help="Skip range; may be repeated or wrapped as skip(...)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only show planned segments"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace output folder if it exists"),
) -> None:
    """Split every video in a folder, continuing after per-file errors."""
    try:
        db.init_db()
        items = split_video_folder(
            folder,
            segment_seconds = seconds,
            skip_specs      = skip,
            dry_run         = dry_run,
            overwrite       = overwrite,
        )
        if not items:
            typer.echo("No video files found.")
            return

        ok = 0
        failed = 0
        for item in items:
            typer.echo("")
            typer.echo(f"File: {item.source_path}")
            if item.error:
                failed += 1
                typer.secho(f"  ERROR: {item.error}", fg=typer.colors.RED)
                continue
            ok += 1
            assert item.result is not None
            typer.echo(f"  segments: {len(item.result.segment_ranges)}")
            typer.echo(f"  output:   {item.result.output_dir}")
            if item.result.dry_run:
                typer.echo("  dry-run:  yes")

        typer.echo("")
        typer.echo("Summary:")
        typer.echo(f"  processed: {ok}")
        typer.echo(f"  failed:    {failed}")
    except Exception as exc:
        die(str(exc))


@app.command("split-file", hidden=True)
def split_file_cmd(
    path: Path = typer.Argument(..., help="Path to video file"),
    seconds: int = typer.Option(60, "--seconds", "-s"),
    mode: str = typer.Option("fast", "--mode", "-m"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Compatibility alias: add a video and immediately split it."""
    try:
        db.init_db()
        video_id, job_id, files = split_file(path, seconds, mode, overwrite)
        _print_old_split_result(video_id, job_id, files)
    except Exception as exc:
        die(str(exc))


@app.command("fast-split", hidden=True)
def fast_split_cmd(
    video_id: int = typer.Argument(..., help="Video ID from 'shortsfarm videos'"),
    seconds: int = typer.Option(60, "--seconds", "-s", help="Segment length in seconds"),
    skip: list[str] = typer.Option(
        [],
        "--skip",
        "-k",
        help="Skip range, for example: skip(start-00:07:30, 01:37:00-end)",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace existing output folder"),
) -> None:
    """Advanced alias: fast split an already-added video by ID."""
    try:
        db.init_db()
        job_id, files = fast_split_video(
            video_id        = video_id,
            segment_seconds = seconds,
            skip_specs      = skip,
            overwrite       = overwrite,
        )
        _print_old_split_result(video_id, job_id, files)
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Old split-folder aliases
# ---------------------------------------------------------------------------

@app.command("jobs", hidden=True)
def jobs_cmd(
    limit: int = typer.Option(50, "--limit", "-n", help="Number of jobs to show"),
) -> None:
    """Compatibility alias: list recent split jobs."""
    try:
        db.init_db()
        _print_jobs(limit)
    except Exception as exc:
        die(str(exc))


@app.command("segments", hidden=True)
def segments_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
    job_id: int | None = typer.Option(None, "--job-id", "-j", help="Specific job ID"),
) -> None:
    """Compatibility alias: list generated segments for a video."""
    try:
        db.init_db()
        _print_segments(video_id, job_id)
    except Exception as exc:
        die(str(exc))


@app.command("cut", hidden=True)
def cut_cmd(
    seconds: int = typer.Option(60, "--seconds", "-s"),
    mode: str = typer.Option("fast", "--mode", "-m"),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite"),
) -> None:
    """Compatibility alias: cut the first pending video from the input folder."""
    try:
        db.init_db()
        video_id, job_id, files = split_first_input_video(seconds, mode, overwrite)
        _print_old_split_result(video_id, job_id, files)
    except Exception as exc:
        die(str(exc))


@app.command("cut-all", hidden=True)
def cut_all_cmd(
    seconds: int = typer.Option(60, "--seconds", "-s"),
    mode: str = typer.Option("fast", "--mode", "-m"),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite"),
) -> None:
    """Compatibility alias: cut all pending videos from the input folder."""
    try:
        db.init_db()
        results = split_all_input_videos(seconds, mode, overwrite)
        typer.echo("Done.")
        typer.echo(f"  processed: {len(results)}")
        for path, video_id, job_id, files in results:
            typer.echo("")
            typer.echo(f"  file:     {path.name}")
            _print_old_split_result(video_id, job_id, files)
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------

def _do_launch_review(video_id: int) -> None:
    """Common logic: launch mpv and print result."""
    from .mpv_session import launch_review

    session_id, session_file = launch_review(video_id)
    video = db.get_video(video_id)
    typer.echo(f"  video_id:   {video_id}")
    typer.echo(f"  session_id: {session_id}")
    typer.echo(f"  status:     {video['review_status'] if video else '?'}")
    typer.echo(f"  marks:      {db.count_marks(video_id)}")
    typer.echo(f"  clips:      {db.count_clips(video_id)}")
    typer.echo(f"  session:    {session_file}")


def _review_next() -> None:
    video = db.claim_inbox_video()
    if video is None:
        typer.echo("No inbox videos available")
        return
    typer.echo(f"Reviewing: {video['title']}")
    _do_launch_review(int(video["id"]))


def _review_video_id(video_id: int, force: bool = False) -> None:
    open_video_for_review(video_id, force=force)
    video = db.get_video(video_id)
    typer.echo(f"Reviewing: {video['title'] if video else video_id}")
    _do_launch_review(video_id)


def _review_video_path(path: Path, force: bool = False) -> None:
    video_id = get_or_add_video(path)
    _review_video_id(video_id, force=force)


def _review_reset(video_id: int) -> None:
    abandoned = reset_video_review(video_id)
    typer.echo(f"Reset video {video_id} -> inbox  ({abandoned} session(s) abandoned)")


@app.command(
    "review",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def review_cmd(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Video file path"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-review completed/skipped videos"),
    video_id: int | None = typer.Option(None, "--id", help="Advanced: review by video ID"),
) -> None:
    """Open a video file in mpv for manual review."""
    try:
        db.init_db()
        args = list(ctx.args)
        extra_force, args = _extract_flag(args, "--force", "-f")
        force = force or extra_force

        if video_id is not None:
            if target is not None or args:
                die("Use either 'shortsfarm review --id <id>' or 'shortsfarm review <file>'.")
            _review_video_id(video_id, force=force)
            return

        if target is None:
            die("Use: shortsfarm review <video-file>")

        if target == "inbox":
            if args:
                die("Use: shortsfarm review inbox")
            _review_next()
            return

        if target == "open-id":
            if not args or not _is_int_token(args[0]):
                die("Use: shortsfarm review open-id <video_id>")
            _review_video_id(int(args[0]), force=force)
            return

        if target == "open":
            if not args:
                die("Use: shortsfarm review open <video-file>")
            _review_video_path(Path(args[0]), force=force)
            return

        if target == "reset":
            args = [arg for arg in args if arg != "--abandon-open-session"]
            if not args or not _is_int_token(args[0]):
                die("Use: shortsfarm review reset <video_id>")
            _review_reset(int(args[0]))
            return

        if args:
            die(f"Unexpected review arguments: {' '.join(args)}")
        _review_video_path(Path(target), force=force)

    except typer.Exit:
        raise
    except Exception as exc:
        die(str(exc))


@app.command("review-next")
def review_next_cmd() -> None:
    """Atomically pick the next inbox video and open it in mpv."""
    try:
        db.init_db()
        _review_next()
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Status and queue
# ---------------------------------------------------------------------------

@app.command("status")
def status_cmd(
    file: Path | None = typer.Argument(None, help="Optional video file"),
    details: bool = typer.Option(False, "--details", help="Show detailed lists"),
) -> None:
    """Show project summary or the status of one video file."""
    try:
        db.init_db()

        if file is not None:
            path, video_id = _resolve_existing_video_path(file)
            video = db.get_video(video_id)
            assert video is not None
            typer.echo("Video:")
            typer.echo(f"  path:          {path}")
            typer.echo(f"  video_id:      {video_id}")
            typer.echo(f"  duration:      {format_duration(video['duration_sec'])}")
            typer.echo(f"  review_status: {video['review_status']}")
            typer.echo(f"  marks:         {db.count_marks(video_id)}")
            typer.echo(f"  clips:         {db.count_clips(video_id)}")
            typer.echo(f"  segments:      {db.count_segments(video_id)}")
            typer.echo(f"  latest output: {_latest_output_dir(video_id)}")
            if details:
                typer.echo("")
                _print_clips(video_id=video_id, limit=100)
            return

        video_counts = db.count_videos_by_review_status()
        clip_counts = db.count_clips_by_status()
        job_counts = db.count_jobs_by_status()
        latest_output = _latest_output_dir()

        typer.echo("Videos:")
        typer.echo(f"  total:     {db.count_videos()}")
        for status in ["inbox", "reviewing", "reviewed", "skipped", "failed"]:
            typer.echo(f"  {status:<10} {video_counts.get(status, 0)}")

        typer.echo("")
        typer.echo("Queue:")
        typer.echo(f"  queued clips: {clip_counts.get('queued', 0)}")
        typer.echo(f"  rendering:    {clip_counts.get('rendering', 0)}")
        typer.echo(f"  done:         {clip_counts.get('done', 0)}")
        typer.echo(f"  failed:       {clip_counts.get('failed', 0)}")
        if job_counts:
            typer.echo(f"  split jobs:   {sum(job_counts.values())}")

        typer.echo("")
        typer.echo("Output:")
        typer.echo(f"  segments:      {db.count_segments()}")
        typer.echo(f"  latest output: {latest_output}")

        errors = db.list_recent_errors(limit=5)
        if errors:
            typer.echo("")
            typer.echo("Recent errors:")
            for row in errors:
                typer.echo(
                    f"  {row['kind']} {row['id']} "
                    f"(video {row['video_id']}): {row['error']}"
                )

        if details:
            typer.echo("")
            _print_inbox()

    except Exception as exc:
        die(str(exc))


@app.command("queue")
def queue_cmd(
    failed: bool = typer.Option(False, "--failed", help="Show failed clips only"),
    done: bool = typer.Option(False, "--done", help="Show done clips only"),
    video: Path | None = typer.Option(None, "--video", help="Filter by video file"),
) -> None:
    """Show clip render queue and generated split segments."""
    try:
        db.init_db()
        if failed and done:
            die("Use only one of --failed or --done.")

        status = "failed" if failed else "done" if done else None
        video_id: int | None = None
        if video is not None:
            _, video_id = _resolve_existing_video_path(video)

        clip_counts = db.count_clips_by_status(video_id)
        typer.echo("Clips:")
        typer.echo(f"  queued:    {clip_counts.get('queued', 0)}")
        typer.echo(f"  rendering: {clip_counts.get('rendering', 0)}")
        typer.echo(f"  done:      {clip_counts.get('done', 0)}")
        typer.echo(f"  failed:    {clip_counts.get('failed', 0)}")

        typer.echo("")
        _print_clips(status=status, video_id=video_id, limit=100)

        if not failed:
            segments = db.list_recent_segments(video_id=video_id, limit=20)
            typer.echo("")
            typer.echo(f"Segments: {db.count_segments(video_id)}")
            if segments:
                typer.echo(f"{'ID':>6}  {'VIDEO':>5}  {'START':>8}  {'END':>8}  PATH")
                typer.echo("-" * 100)
                for row in segments:
                    typer.echo(
                        f"{row['id']:>6}  {row['video_id']:>5}  "
                        f"{row['start_sec']:>8.0f}  {row['end_sec']:>8.0f}  "
                        f"{row['path']}"
                    )

    except typer.Exit:
        raise
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Marks / skip / clips compatibility commands
# ---------------------------------------------------------------------------

@app.command("marks", hidden=True)
def marks_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
) -> None:
    """Compatibility alias: list marks for a video."""
    try:
        db.init_db()
        _print_marks(video_id)
    except Exception as exc:
        die(str(exc))


@app.command("skip", hidden=True)
def skip_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
) -> None:
    """Compatibility alias: mark a video as skipped."""
    try:
        db.init_db()
        video = db.get_video(video_id)
        if video is None:
            die(f"Video {video_id} not found")
        db.update_video_review_status(video_id, "skipped")
        typer.echo(f"Video {video_id} marked as skipped.")
    except typer.Exit:
        raise
    except Exception as exc:
        die(str(exc))


@app.command("clips", hidden=True)
def clips_cmd(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    video_id: int | None = typer.Option(None, "--video-id", "-v", help="Filter by video ID"),
) -> None:
    """Compatibility alias: list clips."""
    try:
        db.init_db()
        _print_clips(status=status, video_id=video_id)
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Render / retry / clean
# ---------------------------------------------------------------------------

@app.command("render")
def render_cmd(
    limit: int = typer.Option(10, "--limit", "-n", help="Max clips to render"),
) -> None:
    """Render queued clips created by the mpv review workflow."""
    try:
        db.init_db()
        from .render import render_queued

        queued = db.list_clips(status="queued", limit=1)
        if not queued:
            typer.echo("No queued clips.")
            return
        typer.echo(f"Rendering up to {limit} clip(s)...")
        results = render_queued(limit=limit)
        typer.echo(f"Done.  Rendered: {len(results)}")
        for cid, path in results:
            typer.echo(f"  clip {cid}: {path}")
    except Exception as exc:
        die(str(exc))


@app.command("render-all", hidden=True)
def render_all_cmd() -> None:
    """Compatibility alias: render all queued clips."""
    try:
        db.init_db()
        from .render import render_all_queued

        queued = db.list_clips(status="queued", limit=1)
        if not queued:
            typer.echo("No queued clips.")
            return
        typer.echo("Rendering all queued clips...")
        results = render_all_queued()
        typer.echo(f"Done.  Rendered: {len(results)}")
        for cid, path in results:
            typer.echo(f"  clip {cid}: {path}")
    except Exception as exc:
        die(str(exc))


@app.command("retry-failed")
def retry_failed_cmd(
    clip_id: int | None = typer.Option(None, "--clip-id", "-c", help="Specific clip ID"),
) -> None:
    """Reset failed clips back to queued without touching finished outputs."""
    try:
        db.init_db()
        from .render import retry_failed_clips

        failed_before = db.list_clips(status="failed", limit=10_000)
        reset_ids, skipped_ids = retry_failed_clips(clip_id=clip_id)
        typer.echo(f"Failed before:    {len(failed_before) if clip_id is None else int(bool(failed_before))}")
        typer.echo(f"Reset to queued:  {len(reset_ids)}  ({reset_ids or 'none'})")
        if skipped_ids:
            typer.echo(f"Skipped (output exists): {len(skipped_ids)}  ({skipped_ids})")
    except Exception as exc:
        die(str(exc))


@app.command("clean")
def clean_cmd() -> None:
    """Safely remove known temp files from failed/rendering clip renders."""
    try:
        db.init_db()
        clips = db.list_clips(status="failed", limit=10_000) + db.list_clips(
            status="rendering",
            limit=10_000,
        )
        output_root = output_dir().resolve()
        removed = 0
        skipped = 0
        for clip in clips:
            if not clip["temp_path"]:
                continue
            temp = Path(str(clip["temp_path"])).expanduser().resolve()
            safe_temp_name = temp.name.endswith(".tmp.mp4") or ".tmp" in temp.name
            if not safe_temp_name or not temp.is_relative_to(output_root):
                skipped += 1
                continue
            if temp.exists() and temp.is_file():
                temp.unlink()
                removed += 1
        typer.echo("Clean complete.")
        typer.echo(f"  temp files removed: {removed}")
        typer.echo(f"  temp paths skipped: {skipped}")
        typer.echo("  final output files: untouched")
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# YouTube publishing
# ---------------------------------------------------------------------------

@youtube_app.command("profiles")
def youtube_profiles_cmd() -> None:
    """List YouTube OAuth Profiles."""
    try:
        db.init_db()
        _print_youtube_profiles()
    except Exception as exc:
        die(str(exc))


@youtube_app.command("accounts")
def youtube_accounts_cmd() -> None:
    """List connected YouTube channel accounts."""
    try:
        db.init_db()
        _print_social_accounts()
    except Exception as exc:
        die(str(exc))


@youtube_app.command("connect")
def youtube_connect_cmd(
    profile_id: int | None = typer.Option(None, "--profile-id", help="OAuth Profile ID"),
    open_browser: bool = typer.Option(
        False,
        "--open-browser",
        help="Open the Google OAuth URL in your browser",
    ),
) -> None:
    """Start YouTube OAuth for a chosen profile and print the auth URL."""
    try:
        import webbrowser

        from fastapi import HTTPException

        from .web import api as web_api
        from .web.schemas import YouTubeConnectStartRequest

        db.init_db()
        try:
            payload = web_api.youtube_connect_start(
                YouTubeConnectStartRequest(oauth_profile_id=profile_id)
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                die(str(detail.get("message") or exc))
            die(str(detail or exc))

        typer.echo(f"profile: {payload['profile_name']} ({payload['oauth_profile_id']})")
        typer.echo(payload["auth_url"])
        if open_browser:
            webbrowser.open(payload["auth_url"])
    except Exception as exc:
        die(str(exc))


@app.command("publish-youtube")
def publish_youtube_cmd(
    clip_id: int = typer.Option(..., "--clip-id", help="Rendered clip ID"),
    account_id: int = typer.Option(..., "--account-id", help="YouTube social account ID"),
    title: str = typer.Option(..., "--title", help="YouTube video title"),
    description: str = typer.Option("", "--description", help="YouTube video description"),
    tag: list[str] | None = typer.Option(None, "--tag", help="YouTube tag; can be repeated"),
    category_id: str = typer.Option("22", "--category-id", help="YouTube category ID"),
    mode: str = typer.Option(
        "private",
        "--mode",
        help="Publish mode: private, unlisted, public, schedule",
    ),
    publish_at: str | None = typer.Option(None, "--publish-at", help="ISO datetime for schedule mode"),
    made_for_kids: bool = typer.Option(
        False,
        "--made-for-kids/--not-made-for-kids",
        help="YouTube made for kids flag",
    ),
) -> None:
    """Upload a rendered clip to YouTube using a connected account."""
    try:
        db.init_db()
        from .publish_youtube import parse_tags, upload_clip_to_youtube, validate_publish_options

        validated = validate_publish_options(
            title=title,
            publish_mode=mode,
            publish_at=publish_at,
            category_id=category_id,
        )
        tags = parse_tags(tag or [])
        job_id = db.create_publish_job(
            account_id=account_id,
            clip_id=clip_id,
            title=title.strip(),
            description=description,
            tags=json.dumps(tags, ensure_ascii=False),
            category_id=category_id,
            privacy_status=str(validated["privacy_status"]),
            publish_mode=mode,
            publish_at=validated["publish_at"],
            made_for_kids=made_for_kids,
            platform="youtube",
        )
        job = upload_clip_to_youtube(job_id)
        typer.echo("YouTube upload complete.")
        typer.echo(f"  publish_job: {job['id']}")
        typer.echo(f"  status:      {job['status']}")
        typer.echo(f"  video_id:    {job['youtube_video_id'] or '-'}")
        typer.echo(f"  url:         {job['youtube_url'] or '-'}")
    except Exception as exc:
        die(str(exc))


@app.command("publish-worker")
def publish_worker_cmd(
    once: bool = typer.Option(False, "--once", help="Process available jobs once and exit"),
    poll_interval: int = typer.Option(60, "--poll-interval", help="Seconds to wait when queue is empty"),
    limit: int = typer.Option(3, "--limit", help="Maximum jobs per cycle", min=1),
) -> None:
    """Run the YouTube publish worker."""
    try:
        from .publish_youtube import run_publish_worker

        db.init_db()
        handled = run_publish_worker(
            once=once,
            poll_interval=poll_interval,
            limit=limit,
        )
        if once:
            typer.echo(f"Processed publish jobs: {handled}")
    except KeyboardInterrupt:
        typer.echo("Publish worker stopped.")
    except Exception as exc:
        die(str(exc))


@app.command("edit-worker")
def edit_worker_cmd(
    limit: int = typer.Option(1, "--limit", help="Maximum edit jobs to render", min=1),
) -> None:
    """Legacy edit worker is disabled after Studio-only cutover."""
    try:
        db.init_db()
        raise RuntimeError(
            "Legacy edit worker отключён. "
            "Новые задачи рендерятся через Studio render queue."
        )
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

def _web_pid_path() -> Path:
    return data_dir() / "web.pid"


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_web_pid() -> Path:
    path = _web_pid_path()
    existing_pid: int | None = None
    if path.exists():
        try:
            existing_pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            existing_pid = None
    if existing_pid and _is_process_alive(existing_pid):
        raise RuntimeError(f"ShortsFarm web уже запущен: PID {existing_pid}")
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return path


def _remove_web_pid(path: Path | None = None) -> None:
    target = path or _web_pid_path()
    try:
        if target.exists():
            target.unlink()
    except OSError:
        pass


@app.command("web")
def web_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Host for local web UI"),
    port: int = typer.Option(8000, "--port", "-p", help="Port for local web UI"),
    open_browser: bool = typer.Option(
        False,
        "--open-browser",
        help="Open browser automatically",
    ),
) -> None:
    """Start the local FastAPI web interface."""
    try:
        ensure_dirs()
        db.init_db()
        pid_path = _write_web_pid()

        import webbrowser
        import uvicorn

        url = f"http://{host}:{port}"
        typer.echo(f"Open {url}")
        if open_browser:
            webbrowser.open(url)

        uvicorn.run(
            "shortsfarm.web.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=False,
        )
    except Exception as exc:
        die(str(exc))
    finally:
        try:
            _remove_web_pid(pid_path)
        except UnboundLocalError:
            pass


@app.command("stop")
def stop_cmd() -> None:
    """Stop the local ShortsFarm web server started by 'shortsfarm web'."""
    try:
        ensure_dirs()
        pid_path = _web_pid_path()
        if not pid_path.exists():
            typer.echo("ShortsFarm web is not running.")
            return

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            _remove_web_pid(pid_path)
            typer.echo("Removed stale ShortsFarm web pid file.")
            return

        if not _is_process_alive(pid):
            _remove_web_pid(pid_path)
            typer.echo(f"ShortsFarm web is not running. Removed stale pid file for PID {pid}.")
            return

        os.kill(pid, signal.SIGTERM)
        _remove_web_pid(pid_path)
        typer.echo(f"Stopped ShortsFarm web server PID {pid}.")
    except Exception as exc:
        die(str(exc))


# ---------------------------------------------------------------------------
# Debug group
# ---------------------------------------------------------------------------

@debug_app.command("add")
def debug_add_cmd(path: Path = typer.Argument(..., help="Path to video file")) -> None:
    add_cmd(path)


@debug_app.command("inbox")
def debug_inbox_cmd() -> None:
    inbox_cmd()


@debug_app.command("jobs")
def debug_jobs_cmd(
    limit: int = typer.Option(50, "--limit", "-n", help="Number of jobs to show"),
) -> None:
    jobs_cmd(limit)


@debug_app.command("clips")
def debug_clips_cmd(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    video_id: int | None = typer.Option(None, "--video-id", "-v", help="Filter by video ID"),
) -> None:
    clips_cmd(status, video_id)


@debug_app.command("marks")
def debug_marks_cmd(video_id: int = typer.Argument(..., help="Video ID")) -> None:
    marks_cmd(video_id)


@debug_app.command("segments")
def debug_segments_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
    job_id: int | None = typer.Option(None, "--job-id", "-j", help="Specific job ID"),
) -> None:
    segments_cmd(video_id, job_id)


@debug_app.command("review-id")
def debug_review_id_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-review completed/skipped videos"),
) -> None:
    try:
        db.init_db()
        _review_video_id(video_id, force=force)
    except Exception as exc:
        die(str(exc))


@debug_app.command("split-id")
def debug_split_id_cmd(
    video_id: int = typer.Argument(..., help="Video ID"),
    seconds: int = typer.Option(60, "--seconds", "-s", help="Segment length in seconds"),
    mode: str = typer.Option("fast", "--mode", "-m", help="Split mode: fast or exact"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace output folder"),
) -> None:
    try:
        db.init_db()
        job_id, files = split_existing_video(video_id, seconds, mode, overwrite)
        _print_old_split_result(video_id, job_id, files)
    except Exception as exc:
        die(str(exc))
