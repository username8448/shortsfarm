from __future__ import annotations

from typing import Any

from .. import db
from ..config import DEFAULT_YOUTUBE_REDIRECT_URI
from .api_common import normalize_setting_text
from .social_account_payloads import social_account_base_dict
from .storage_profile_payloads import storage_profile_dict
from .api_common import row_value as _row


def youtube_oauth_profile_dict(row: Any) -> dict[str, Any]:
    mode = _row(row, "mode", "custom") or "custom"
    client_id = _row(row, "client_id", "") or ""
    redirect_uri = _row(row, "redirect_uri", DEFAULT_YOUTUBE_REDIRECT_URI) or DEFAULT_YOUTUBE_REDIRECT_URI
    return {
        "id": int(row["id"]),
        "name": _row(row, "name", "") or "",
        "mode": mode,
        "client_id": client_id,
        "client_secret_set": bool(normalize_setting_text(_row(row, "client_secret"))),
        "redirect_uri": redirect_uri,
        "status": _row(row, "status", "active") or "active",
        "is_default": bool(_row(row, "is_default", 0)),
        "notes": _row(row, "notes"),
        "created_at": _row(row, "created_at"),
        "updated_at": _row(row, "updated_at"),
    }


def youtube_account_dict(
    row: Any,
    *,
    include_profile_links: bool = False,
) -> dict[str, Any]:
    data = social_account_base_dict(row)
    if include_profile_links:
        data["linked_storage_profiles"] = [
            storage_profile_dict(profile)
            for profile in db.list_local_storage_profiles_for_service_account(
                platform=_row(row, "platform", "youtube") or "youtube",
                external_account_id=int(row["id"]),
            )
        ]
    return data
