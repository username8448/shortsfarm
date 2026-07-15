from __future__ import annotations

from typing import Any

from ..publish_schedule import schedule_state, seconds_until
from .api_common import row_value as _row


def publish_job_dict(row: Any) -> dict[str, Any]:
    source_segment_id = _row(row, "clip_source_segment_id")
    source_clip_id = _row(row, "clip_source_clip_id")
    workspace_item_key = (
        f"segment:{source_segment_id}"
        if source_segment_id is not None
        else f"clip:{source_clip_id}" if source_clip_id is not None else f"clip:{int(row['clip_id'])}"
    )
    current_schedule_state = schedule_state(
        _row(row, "upload_at"),
        _row(row, "overdue_approved_at"),
    )
    return {
        "id": int(row["id"]),
        "platform": _row(row, "platform", "youtube"),
        "account_id": int(row["account_id"]),
        "clip_id": int(row["clip_id"]),
        "status": _row(row, "status", "queued"),
        "title": _row(row, "title", ""),
        "description": _row(row, "description"),
        "tags": _row(row, "tags"),
        "category_id": _row(row, "category_id", "22"),
        "privacy_status": _row(row, "privacy_status", "public"),
        "publish_mode": _row(row, "publish_mode", "public"),
        "publish_at": _row(row, "publish_at"),
        "upload_at": _row(row, "upload_at"),
        "schedule_group_id": _row(row, "schedule_group_id"),
        "schedule_group_name": _row(row, "schedule_group_name", "") or "",
        "schedule_position": _row(row, "schedule_position"),
        "overdue_approved_at": _row(row, "overdue_approved_at"),
        "schedule_state": current_schedule_state,
        "is_overdue": current_schedule_state == "overdue",
        "seconds_until_upload": seconds_until(_row(row, "upload_at")),
        "seconds_until_publish": seconds_until(_row(row, "publish_at")),
        "made_for_kids": bool(_row(row, "made_for_kids", 0)),
        "youtube_video_id": _row(row, "youtube_video_id"),
        "youtube_url": _row(row, "youtube_url"),
        "error": _row(row, "error"),
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "started_at"),
        "finished_at": _row(row, "finished_at"),
        "updated_at": _row(row, "updated_at"),
        "attempt_count": int(_row(row, "attempt_count", 0) or 0),
        "last_attempt_at": _row(row, "last_attempt_at"),
        "next_attempt_at": _row(row, "next_attempt_at"),
        "oauth_profile_id": _row(row, "oauth_profile_id"),
        "profile_name": _row(row, "profile_name", "") or "",
        "account_display_name": _row(row, "account_display_name", "") or "",
        "account_email": _row(row, "account_email", "") or "",
        "channel_id": _row(row, "channel_id", "") or "",
        "channel_title": _row(row, "channel_title", "") or "",
        "clip_video_id": _row(row, "clip_video_id"),
        "clip_status": _row(row, "clip_status", "") or "",
        "clip_output_path": _row(row, "clip_output_path", "") or "",
        "clip_cut_mode": _row(row, "clip_cut_mode", "") or "",
        "clip_source_segment_id": source_segment_id,
        "clip_source_clip_id": source_clip_id,
        "clip_source_aspect": _row(row, "clip_source_aspect", "") or "",
        "workspace_item_key": workspace_item_key,
        "video_title": _row(row, "video_title", "") or "",
        "video_source_path": _row(row, "video_source_path", "") or "",
        "can_retry": _row(row, "status") in {"failed", "cancelled"},
        "can_run": (
            _row(row, "status") in {"queued", "failed"}
            and current_schedule_state not in {"waiting", "overdue"}
        ),
        "can_force_run": _row(row, "status") in {"queued", "failed"},
        "can_cancel": _row(row, "status") in {"queued", "failed"},
    }
