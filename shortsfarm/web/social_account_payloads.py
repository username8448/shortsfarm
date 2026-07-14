from __future__ import annotations

import json
from typing import Any

from .tag_catalog import row_value as _row


def social_account_base_dict(row: Any) -> dict[str, Any]:
    oauth_profile_id = _row(row, "oauth_profile_id")

    def _json_field(key: str) -> Any:
        value = _row(row, key)
        if not value:
            return {} if key.endswith("_json") else None
        try:
            return json.loads(str(value))
        except Exception:
            return {}

    local_alias = _row(row, "display_name", "") or ""
    official_title = _row(row, "channel_title", "") or ""
    return {
        "id": int(row["id"]),
        "platform": row["platform"],
        "oauth_profile_id": oauth_profile_id,
        "profile_name": _row(row, "profile_name", "") or "",
        "oauth_profile": (
            {
                "id": int(oauth_profile_id),
                "name": _row(row, "profile_name", "") or f"OAuth Profile #{int(oauth_profile_id)}",
            }
            if oauth_profile_id is not None
            else None
        ),
        "display_name": local_alias,
        "local_alias": local_alias,
        "account_email": _row(row, "account_email", "") or "",
        "channel_id": _row(row, "channel_id", "") or "",
        "channel_title": official_title,
        "official_channel_title": official_title,
        "channel_description": _row(row, "channel_description", "") or "",
        "channel_custom_url": _row(row, "channel_custom_url", "") or "",
        "channel_handle": _row(row, "channel_handle", "") or "",
        "channel_country": _row(row, "channel_country", "") or "",
        "channel_published_at": _row(row, "channel_published_at"),
        "channel_avatar_url": _row(row, "channel_avatar_url", "") or "",
        "channel_thumbnails": _json_field("channel_thumbnails_json"),
        "channel_banner_url": _row(row, "channel_banner_url", "") or "",
        "channel_branding": _json_field("channel_branding_json"),
        "subscriber_count": _row(row, "subscriber_count"),
        "view_count": _row(row, "view_count"),
        "video_count": _row(row, "video_count"),
        "hidden_subscriber_count": bool(_row(row, "hidden_subscriber_count", 0) or 0),
        "uploads_playlist_id": _row(row, "uploads_playlist_id", "") or "",
        "channel_status": _json_field("channel_status_json"),
        "metadata_synced_at": _row(row, "metadata_synced_at"),
        "metadata_sync_error": _row(row, "metadata_sync_error"),
        "scopes": _row(row, "scopes", "") or "",
        "status": _row(row, "status", "active") or "active",
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
        "last_connected_at": _row(row, "last_connected_at"),
        "error": _row(row, "error"),
    }
