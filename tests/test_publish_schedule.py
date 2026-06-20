from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _make_account() -> int:
    from shortsfarm import db

    profile_id = db.create_youtube_oauth_profile(
        name="Schedule profile",
        mode="custom",
        client_id="client",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    return db.save_social_account(
        platform="youtube",
        display_name="Schedule account",
        channel_id="channel-schedule",
        channel_title="Schedule channel",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.upload",
        oauth_profile_id=profile_id,
        status="active",
    )


def _make_jobs(tmp_path: Path, count: int = 2) -> list[int]:
    from shortsfarm import db

    account_id = _make_account()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    video_id = db.add_video(source, "source", 60.0)
    job_ids: list[int] = []
    for index in range(count):
        mark_id = db.insert_mark(video_id, None, float(index), float(index + 10))
        clip_id = db.insert_clip(video_id, mark_id)
        output = tmp_path / f"clip-{index}.mp4"
        output.write_bytes(b"clip")
        db.set_clip_done(clip_id, str(output))
        job_ids.append(
            db.create_publish_job(
                account_id=account_id,
                clip_id=clip_id,
                title=f"Job {index}",
                description="",
                tags="[]",
                category_id="22",
                privacy_status="private",
                publish_mode="private",
                publish_at=None,
                made_for_kids=False,
            )
        )
    return job_ids


def _iso(delta_minutes: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)
    ).isoformat(timespec="seconds")


def test_schedule_modes_and_moscow_conversion():
    from shortsfarm.publish_schedule import expand_schedule

    jobs = [10, 20]
    same = expand_schedule(jobs, {"mode": "same", "start_at": "2026-06-20T12:00"})
    interval = expand_schedule(
        jobs,
        {"mode": "interval", "start_at": "2026-06-20T12:00", "interval_minutes": 30},
    )
    individual = expand_schedule(
        jobs,
        {
            "mode": "individual",
            "item_times": {
                10: "2026-06-20T12:00",
                20: "2026-06-20T14:00",
            },
        },
    )
    none = expand_schedule(jobs, {"mode": "none"})

    assert same[10] == "2026-06-20T09:00:00+00:00"
    assert same[20] == same[10]
    assert interval[20] == "2026-06-20T09:30:00+00:00"
    assert individual[20] == "2026-06-20T11:00:00+00:00"
    assert none == {10: None, 20: None}


def test_schedule_requires_30_minute_publish_lead():
    from shortsfarm.publish_schedule import validate_schedule_pair

    with pytest.raises(ValueError, match="30 минут"):
        validate_schedule_pair(
            "2026-06-20T09:00:00+00:00",
            "2026-06-20T09:29:00+00:00",
        )


def test_create_schedule_group_assigns_jobs(tmp_path):
    from shortsfarm import db

    jobs = _make_jobs(tmp_path)
    group_id = db.save_publish_schedule_group(
        name="Morning",
        job_ids=jobs,
        upload_spec={"mode": "interval", "start_at": _iso(60), "interval_minutes": 20},
        publish_spec={"mode": "interval", "start_at": _iso(120), "interval_minutes": 20},
    )

    rows = db.list_publish_schedule_group_jobs(group_id)
    assert [int(row["id"]) for row in rows] == jobs
    assert [int(row["schedule_position"]) for row in rows] == [1, 2]
    assert all(row["upload_at"] for row in rows)
    assert all(row["publish_mode"] == "schedule" for row in rows)
    assert all(row["privacy_status"] == "private" for row in rows)


def test_update_group_changes_only_queued_jobs(tmp_path):
    from shortsfarm import db

    jobs = _make_jobs(tmp_path)
    group_id = db.save_publish_schedule_group(
        name="Original",
        job_ids=jobs,
        upload_spec={"mode": "same", "start_at": _iso(60)},
        publish_spec={"mode": "none"},
    )
    original_done_upload = db.get_publish_job(jobs[1])["upload_at"]
    db.mark_publish_done(jobs[1], "yt-done", "https://youtu.be/yt-done")

    db.save_publish_schedule_group(
        group_id=group_id,
        name="Shifted",
        job_ids=[jobs[0]],
        upload_spec={"mode": "same", "start_at": _iso(180)},
        publish_spec={"mode": "none"},
    )

    queued = db.get_publish_job(jobs[0])
    done = db.get_publish_job(jobs[1])
    assert queued["upload_at"] != original_done_upload
    assert done["upload_at"] == original_done_upload
    assert int(done["schedule_group_id"]) == group_id


def test_remove_group_clears_schedule_without_deleting_jobs(tmp_path):
    from shortsfarm import db

    jobs = _make_jobs(tmp_path)
    group_id = db.save_publish_schedule_group(
        name="Remove",
        job_ids=jobs,
        upload_spec={"mode": "same", "start_at": _iso(60)},
        publish_spec={"mode": "same", "start_at": _iso(120)},
    )

    assert db.remove_publish_schedule_group(group_id) is True
    job = db.get_publish_job(jobs[0])
    assert job is not None
    assert job["schedule_group_id"] is None
    assert job["upload_at"] is None
    assert job["publish_at"] is None
    assert job["publish_mode"] == "private"


def test_scheduled_worker_ignores_untimed_future_and_overdue(tmp_path):
    from shortsfarm import db

    jobs = _make_jobs(tmp_path, count=3)
    db.save_publish_schedule_group(
        name="Future",
        job_ids=[jobs[1]],
        upload_spec={"mode": "same", "start_at": _iso(60)},
        publish_spec={"mode": "none"},
    )
    overdue_group = db.save_publish_schedule_group(
        name="Overdue",
        job_ids=[jobs[2]],
        upload_spec={"mode": "same", "start_at": _iso(-20)},
        publish_spec={"mode": "none"},
    )

    assert db.claim_next_scheduled_publish_job() is None
    assert db.approve_overdue_publish_schedule_group(overdue_group) == 1
    claimed = db.claim_next_scheduled_publish_job()
    assert int(claimed["id"]) == jobs[2]
    assert db.get_publish_job(jobs[0])["status"] == "queued"


def test_due_scheduled_job_is_claimed(tmp_path):
    from shortsfarm import db

    job_id = _make_jobs(tmp_path, count=1)[0]
    db.save_publish_schedule_group(
        name="Due",
        job_ids=[job_id],
        upload_spec={"mode": "same", "start_at": _iso(-5)},
        publish_spec={"mode": "none"},
    )

    claimed = db.claim_next_scheduled_publish_job()
    assert int(claimed["id"]) == job_id
    assert claimed["status"] == "uploading"


def test_manual_claim_requires_force_for_future_job(tmp_path):
    from shortsfarm import db

    job_id = _make_jobs(tmp_path, count=1)[0]
    db.save_publish_schedule_group(
        name="Future",
        job_ids=[job_id],
        upload_spec={"mode": "same", "start_at": _iso(60)},
        publish_spec={"mode": "none"},
    )

    with pytest.raises(ValueError, match="ещё не наступило"):
        db.claim_publish_job(job_id)
    claimed = db.claim_publish_job(job_id, force=True)
    assert int(claimed["id"]) == job_id


def test_schedule_api_returns_schedule_fields(tmp_path):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import PublishScheduleGroupRequest, PublishScheduleSpecRequest

    job_id = _make_jobs(tmp_path, count=1)[0]
    created = api.publish_schedule_group_create(
        PublishScheduleGroupRequest(
            name="API group",
            job_ids=[job_id],
            upload=PublishScheduleSpecRequest(mode="same", start_at=_iso(60)),
            publish=PublishScheduleSpecRequest(mode="same", start_at=_iso(120)),
        )
    )

    assert created["group"]["name"] == "API group"
    job = api.publish_jobs()["jobs"][0]
    assert job["schedule_group_id"] == created["group"]["id"]
    assert job["schedule_state"] == "waiting"
    assert job["seconds_until_upload"] > 0
    assert job["can_run"] is False
    assert job["can_force_run"] is True
    assert db.get_publish_job(job_id)["publish_mode"] == "schedule"
