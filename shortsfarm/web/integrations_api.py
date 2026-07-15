from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .. import db
from ..config import DEFAULT_YOUTUBE_REDIRECT_URI
from .api_common import fail as _fail
from .api_common import init_api as _init
from .api_common import normalize_setting_text
from .schemas import (
    YouTubeAccountUpdateRequest,
    YouTubeClientJsonImportRequest,
    YouTubeConnectStartRequest,
    YouTubeOAuthProfileCreateRequest,
    YouTubeOAuthProfileImportRequest,
    YouTubeOAuthProfileUpdateRequest,
    YouTubeSettingsRequest,
)
from .youtube_integration_payloads import (
    youtube_account_dict,
    youtube_oauth_profile_dict,
)
from .youtube_integrations_service import (
    clear_youtube_settings,
    create_youtube_connect_start,
    extract_oauth_client_json,
    next_imported_profile_name,
    oauth_page,
    save_youtube_settings,
    sync_all_youtube_account_metadata,
    sync_one_youtube_account_metadata,
    youtube_oauth_callback_response,
    youtube_settings_status,
)

router = APIRouter()


@router.get("/settings/youtube")
def youtube_settings() -> dict[str, Any]:
    try:
        _init()
        return youtube_settings_status()
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube")
def youtube_settings_save(req: YouTubeSettingsRequest) -> dict[str, Any]:
    try:
        _init()
        return save_youtube_settings(
            client_id=req.client_id,
            client_secret=req.client_secret,
            redirect_uri=req.redirect_uri,
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube/import-client-json")
def youtube_settings_import_client_json(req: YouTubeClientJsonImportRequest) -> dict[str, Any]:
    try:
        _init()
        text = normalize_setting_text(req.json_text)
        if not text:
            raise ValueError("Вставьте OAuth Client JSON.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("OAuth Client JSON должен быть JSON-объектом.")
        client_id, client_secret, redirect_uri = extract_oauth_client_json(payload)

        return save_youtube_settings(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except json.JSONDecodeError as exc:
        raise _fail(ValueError(f"Не удалось разобрать JSON: {exc.msg}"))
    except Exception as exc:
        raise _fail(exc)


@router.post("/settings/youtube/clear")
def youtube_settings_clear() -> dict[str, Any]:
    try:
        _init()
        return clear_youtube_settings()
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/oauth-profiles")
def youtube_oauth_profiles() -> dict[str, Any]:
    try:
        _init()
        rows = db.list_youtube_oauth_profiles()
        return {"profiles": [youtube_oauth_profile_dict(row) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles")
def youtube_oauth_profiles_create(req: YouTubeOAuthProfileCreateRequest) -> dict[str, Any]:
    try:
        _init()
        name = normalize_setting_text(req.name)
        client_id = normalize_setting_text(req.client_id)
        client_secret = normalize_setting_text(req.client_secret)
        redirect_uri = normalize_setting_text(req.redirect_uri) or DEFAULT_YOUTUBE_REDIRECT_URI
        if not name:
            raise ValueError("Укажите название OAuth Profile.")
        if not client_id or not client_secret:
            raise ValueError("Укажите client_id и client_secret.")
        profile_id = db.create_youtube_oauth_profile(
            name=name,
            mode="custom",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            status="active",
            is_default=req.is_default,
            notes=req.notes,
        )
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": youtube_oauth_profile_dict(profile)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles/import-client-json")
def youtube_oauth_profiles_import(req: YouTubeOAuthProfileImportRequest) -> dict[str, Any]:
    try:
        _init()
        text = normalize_setting_text(req.json_text)
        if not text:
            raise ValueError("Вставьте OAuth Client JSON.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("OAuth Client JSON должен быть JSON-объектом.")

        client_id, client_secret, redirect_uri = extract_oauth_client_json(payload)
        profile_id = db.create_youtube_oauth_profile(
            name=normalize_setting_text(req.name) or next_imported_profile_name(),
            mode="custom",
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            status="active",
            is_default=req.is_default or not db.list_youtube_oauth_profiles(),
            notes=req.notes,
        )
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": youtube_oauth_profile_dict(profile)}
    except json.JSONDecodeError as exc:
        raise _fail(ValueError(f"Не удалось разобрать JSON: {exc.msg}"))
    except Exception as exc:
        raise _fail(exc)


@router.patch("/publish/youtube/oauth-profiles/{profile_id}")
def youtube_oauth_profiles_update(profile_id: int, req: YouTubeOAuthProfileUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        ok = db.update_youtube_oauth_profile(
            profile_id,
            name=req.name,
            client_id=req.client_id,
            client_secret=req.client_secret,
            redirect_uri=req.redirect_uri,
            status=req.status,
            notes=req.notes,
        )
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": youtube_oauth_profile_dict(profile)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/publish/youtube/oauth-profiles/{profile_id}")
def youtube_oauth_profiles_delete(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.delete_youtube_oauth_profile(profile_id)
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/oauth-profiles/{profile_id}/set-default")
def youtube_oauth_profiles_set_default(profile_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.set_default_youtube_oauth_profile(profile_id)
        if not ok:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        profile = db.get_youtube_oauth_profile(profile_id)
        return {"profile": youtube_oauth_profile_dict(profile)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/accounts")
def youtube_accounts() -> dict[str, Any]:
    try:
        _init()
        rows = db.list_social_accounts(platform="youtube")
        return {"accounts": [youtube_account_dict(row, include_profile_links=True) for row in rows]}
    except Exception as exc:
        raise _fail(exc)


@router.patch("/publish/youtube/accounts/{account_id}")
def youtube_account_update(account_id: int, req: YouTubeAccountUpdateRequest) -> dict[str, Any]:
    try:
        _init()
        account = db.get_social_account(account_id)
        if account is None or str(account["platform"] or "") != "youtube":
            raise FileNotFoundError("YouTube аккаунт не найден.")
        alias = req.local_alias if req.local_alias is not None else req.display_name
        if alias is None:
            raise ValueError("Укажите локальное название аккаунта.")
        if len(str(alias).strip()) > 160:
            raise ValueError("Локальное название слишком длинное.")
        db.update_social_account_alias(account_id, str(alias).strip() or None)
        updated = db.get_social_account(account_id)
        assert updated is not None
        return {"account": youtube_account_dict(updated, include_profile_links=True)}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/accounts/sync-metadata")
def youtube_accounts_sync_metadata() -> dict[str, Any]:
    try:
        _init()
        return sync_all_youtube_account_metadata()
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/accounts/{account_id}/sync-metadata")
def youtube_account_sync_metadata(account_id: int) -> dict[str, Any]:
    try:
        _init()
        return sync_one_youtube_account_metadata(account_id)
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/publish/youtube/connect/start")
def youtube_connect_start(req: YouTubeConnectStartRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        return create_youtube_connect_start(req.oauth_profile_id if req else None)
    except Exception as exc:
        raise _fail(exc)


@router.get("/publish/youtube/oauth/callback")
def youtube_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    try:
        _init()
        return youtube_oauth_callback_response(code=code, state=state, error=error)
    except Exception as exc:
        return oauth_page("Ошибка YouTube OAuth", str(exc) or exc.__class__.__name__, ok=False)


@router.post("/publish/youtube/accounts/{account_id}/disconnect")
def youtube_disconnect(account_id: int) -> dict[str, Any]:
    try:
        _init()
        ok = db.disconnect_social_account(account_id, platform="youtube")
        if not ok:
            raise FileNotFoundError("YouTube аккаунт не найден.")
        return {"status": "ok"}
    except FileNotFoundError as exc:
        raise _fail(exc, status_code=404)
    except Exception as exc:
        raise _fail(exc)
