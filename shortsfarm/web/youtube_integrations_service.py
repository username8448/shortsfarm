from __future__ import annotations

import json
import os
import secrets
from typing import Any

from fastapi.responses import HTMLResponse

from .. import db
from ..config import (
    DEFAULT_YOUTUBE_REDIRECT_URI,
    YOUTUBE_CLIENT_ID_SETTING,
    YOUTUBE_CLIENT_SECRET_SETTING,
    YOUTUBE_REDIRECT_URI_SETTING,
    youtube_client_secret,
)
from ..publish_youtube import (
    fetch_youtube_channel_metadata_items,
    save_youtube_channel_metadata,
    sync_youtube_account_metadata,
)
from ..youtube_oauth import YOUTUBE_SCOPES
from .api_common import normalize_setting_text
from .storage_profile_youtube_service import (
    touch_linked_storage_profiles_youtube_branding,
)
from .api_common import row_value as _row
from .youtube_integration_payloads import youtube_account_dict


def choose_redirect_uri(candidates: list[str] | None) -> str:
    if not candidates:
        return DEFAULT_YOUTUBE_REDIRECT_URI
    for item in candidates:
        uri = normalize_setting_text(item)
        if uri and uri.startswith(("http://", "https://")):
            return uri
    return DEFAULT_YOUTUBE_REDIRECT_URI


def next_imported_profile_name() -> str:
    existing_names = {str(row["name"]) for row in db.list_youtube_oauth_profiles()}
    if "Imported YouTube OAuth" not in existing_names:
        return "Imported YouTube OAuth"
    index = 2
    while True:
        candidate = f"Imported YouTube OAuth {index}"
        if candidate not in existing_names:
            return candidate
        index += 1


def extract_oauth_client_json(payload: dict[str, Any]) -> tuple[str, str, str]:
    section = payload.get("web")
    if not isinstance(section, dict):
        section = payload.get("installed")
    if not isinstance(section, dict):
        raise ValueError("Не найден блок web или installed в OAuth Client JSON.")

    client_id = normalize_setting_text(section.get("client_id"))
    client_secret = normalize_setting_text(section.get("client_secret"))
    redirect_uris = section.get("redirect_uris")
    redirect_uri = choose_redirect_uri(redirect_uris if isinstance(redirect_uris, list) else None)
    if not client_id or not client_secret:
        raise ValueError("OAuth Client JSON должен содержать client_id и client_secret.")
    return client_id, client_secret, redirect_uri


def youtube_client_config_from_profile(profile: Any) -> dict[str, Any]:
    client_id = normalize_setting_text(_row(profile, "client_id"))
    client_secret = normalize_setting_text(_row(profile, "client_secret"))
    redirect_uri = (
        normalize_setting_text(_row(profile, "redirect_uri"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    if not client_id or not client_secret:
        raise RuntimeError("У выбранного OAuth Profile не заполнены client_id/client_secret.")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def youtube_flow_from_profile(flow_cls: Any, profile: Any) -> Any:
    redirect_uri = (
        normalize_setting_text(_row(profile, "redirect_uri"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    return flow_cls.from_client_config(
        youtube_client_config_from_profile(profile),
        scopes=YOUTUBE_SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False,
    )


def select_youtube_oauth_profile(profile_id: int | None = None) -> Any:
    if profile_id is not None:
        profile = db.get_youtube_oauth_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("YouTube OAuth Profile не найден.")
        if _row(profile, "status", "active") != "active":
            raise ValueError("YouTube OAuth Profile не активен.")
        return profile

    default_profile = db.get_default_youtube_oauth_profile()
    if default_profile is not None and _row(default_profile, "status", "active") == "active":
        return default_profile

    active_profiles = [
        row for row in db.list_youtube_oauth_profiles()
        if _row(row, "status", "active") == "active"
    ]
    if not active_profiles:
        raise RuntimeError("Сначала создайте YouTube OAuth Profile в настройках.")
    if len(active_profiles) == 1:
        return active_profiles[0]
    raise RuntimeError("Выберите OAuth Profile для подключения YouTube-канала или назначьте профиль по умолчанию.")


def fetch_google_account_email(credentials: Any) -> str | None:
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None

    try:
        oauth2 = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        payload = oauth2.userinfo().get().execute()
        return normalize_setting_text(payload.get("email"))
    except Exception:
        return None


def youtube_settings_status() -> dict[str, Any]:
    stored_client_id = normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_ID_SETTING))
    stored_client_secret = normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_SECRET_SETTING))
    stored_redirect_uri = normalize_setting_text(db.get_setting(YOUTUBE_REDIRECT_URI_SETTING))
    env_client_id = bool(normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_ID")))
    env_client_secret = bool(normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_SECRET")))
    env_redirect_uri = bool(normalize_setting_text(os.environ.get("YOUTUBE_REDIRECT_URI")))

    client_id = stored_client_id or normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_ID")) or ""
    redirect_uri = (
        stored_redirect_uri
        or normalize_setting_text(os.environ.get("YOUTUBE_REDIRECT_URI"))
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )
    client_secret_set = bool(
        stored_client_secret
        or normalize_setting_text(os.environ.get("YOUTUBE_CLIENT_SECRET"))
    )
    configured = bool(client_id and client_secret_set)
    return {
        "configured": configured,
        "client_id": client_id,
        "client_secret_set": client_secret_set,
        "redirect_uri": redirect_uri,
        "env_fallback": {
            "client_id": env_client_id and not stored_client_id,
            "client_secret": env_client_secret and not stored_client_secret,
            "redirect_uri": env_redirect_uri and not stored_redirect_uri,
        },
    }


def save_youtube_settings(
    *,
    client_id: str | None,
    client_secret: str | None,
    redirect_uri: str | None,
) -> dict[str, Any]:
    existing_client_id = normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_ID_SETTING))
    existing_client_secret = normalize_setting_text(db.get_setting(YOUTUBE_CLIENT_SECRET_SETTING))

    resolved_client_id = normalize_setting_text(client_id) or existing_client_id
    resolved_client_secret = normalize_setting_text(client_secret) or existing_client_secret
    resolved_redirect_uri = normalize_setting_text(redirect_uri) or DEFAULT_YOUTUBE_REDIRECT_URI

    if not resolved_client_id:
        raise ValueError("Укажите YouTube client_id.")
    if not resolved_client_secret and not normalize_setting_text(youtube_client_secret()):
        raise ValueError("Укажите YouTube client_secret.")

    db.set_setting(YOUTUBE_CLIENT_ID_SETTING, resolved_client_id, is_secret=False)
    if normalize_setting_text(client_secret):
        db.set_setting(YOUTUBE_CLIENT_SECRET_SETTING, normalize_setting_text(client_secret), is_secret=True)
    elif existing_client_secret:
        db.set_setting(YOUTUBE_CLIENT_SECRET_SETTING, existing_client_secret, is_secret=True)
    db.set_setting(YOUTUBE_REDIRECT_URI_SETTING, resolved_redirect_uri, is_secret=False)
    return youtube_settings_status()


def clear_youtube_settings() -> dict[str, Any]:
    db.delete_setting(YOUTUBE_CLIENT_ID_SETTING)
    db.delete_setting(YOUTUBE_CLIENT_SECRET_SETTING)
    db.delete_setting(YOUTUBE_REDIRECT_URI_SETTING)
    return {"status": "ok", "settings": youtube_settings_status()}


def oauth_page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    color = "#86efac" if ok else "#fca5a5"
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    payload = json.dumps(
        {
            "type": "shortsfarm-youtube-oauth-complete" if ok else "shortsfarm-youtube-oauth-error",
            "ok": ok,
            "message": message,
        },
        ensure_ascii=False,
    )
    callback_script = f"""
  <script>
    (function () {{
      const payload = {payload};
      try {{
        localStorage.setItem('shortsfarm.youtube.oauth.event', JSON.stringify(payload));
        localStorage.setItem('shortsfarm.youtube.oauth.updated', String(Date.now()));
      }} catch (err) {{}}
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, window.location.origin);
        }}
      }} catch (err) {{}}
    }})();
  </script>"""
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#111827; color:#f8fafc; font-family:-apple-system,Segoe UI,system-ui,sans-serif; }}
    main {{ width:min(560px, calc(100vw - 32px)); padding:28px; border:1px solid #3f4b5c; border-radius:16px; background:#1f2937; box-shadow:0 18px 44px rgba(0,0,0,.32); }}
    h1 {{ margin:0 0 10px; font-size:22px; color:{color}; }}
    p {{ margin:0; color:#cbd5e1; line-height:1.6; }}
    a {{ color:#bfdbfe; }}
  </style>
</head>
<body><main><h1>{safe_title}</h1><p>{safe_message}</p></main>{callback_script}</body>
</html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


def create_youtube_connect_start(oauth_profile_id: int | None = None) -> dict[str, Any]:
    profile = select_youtube_oauth_profile(oauth_profile_id)
    state = secrets.token_urlsafe(32)
    db.create_oauth_state("youtube", state, oauth_profile_id=int(profile["id"]))
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise RuntimeError(
            "Google OAuth зависимости не установлены. Выполните: pip install -e ."
        ) from exc

    flow = youtube_flow_from_profile(Flow, profile)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
        state=state,
    )
    return {
        "auth_url": auth_url,
        "oauth_profile_id": int(profile["id"]),
        "profile_name": _row(profile, "name", "") or "",
    }


def youtube_oauth_callback_response(
    *,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    try:
        if error:
            if state:
                try:
                    db.consume_oauth_state("youtube", state)
                except Exception:
                    pass
            return oauth_page(
                "Ошибка YouTube OAuth",
                f"Google вернул ошибку: {error}. Можно закрыть эту вкладку и попробовать подключение ещё раз.",
                ok=False,
            )
        if not code:
            return oauth_page("Ошибка YouTube OAuth", "Callback не содержит code.", ok=False)
        if not state:
            return oauth_page("Ошибка YouTube OAuth", "Callback не содержит state.", ok=False)
        state_row = db.consume_oauth_state("youtube", state)
        if state_row is None:
            return oauth_page(
                "Ошибка YouTube OAuth",
                "OAuth state не найден или уже был использован. Запустите подключение заново.",
                ok=False,
            )

        oauth_profile_id = _row(state_row, "oauth_profile_id")
        profile = select_youtube_oauth_profile(int(oauth_profile_id) if oauth_profile_id is not None else None)
        try:
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google OAuth зависимости не установлены. Выполните: pip install -e ."
            ) from exc

        flow = youtube_flow_from_profile(Flow, profile)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        account_email = fetch_google_account_email(credentials)

        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        items = fetch_youtube_channel_metadata_items(youtube, mine=True)
        if not items:
            message = "YouTube канал для этого аккаунта не найден."
            return oauth_page("Ошибка YouTube OAuth", message, ok=False)

        expires_at = credentials.expiry.isoformat() if credentials.expiry else None
        scopes = " ".join(credentials.scopes or YOUTUBE_SCOPES)
        saved_ids: list[int] = []

        for channel in items:
            snippet = channel.get("snippet") or {}
            channel_id = str(channel.get("id") or "").strip()
            if not channel_id:
                continue
            channel_title = str(snippet.get("title") or "").strip() or "YouTube канал"
            # TODO: encrypt tokens before production use.
            account_id = db.save_social_account(
                platform="youtube",
                display_name=channel_title,
                channel_id=channel_id,
                channel_title=channel_title,
                access_token=credentials.token,
                refresh_token=credentials.refresh_token,
                token_expires_at=expires_at,
                scopes=scopes,
                oauth_profile_id=int(profile["id"]),
                account_email=account_email,
                last_connected_at=db.now_utc(),
                status="active",
                error=None,
                preserve_display_name=True,
            )
            try:
                save_youtube_channel_metadata(account_id, channel)
            except Exception as sync_exc:
                db.set_social_account_metadata_sync_error(
                    account_id,
                    str(sync_exc) or sync_exc.__class__.__name__,
                )
            saved_ids.append(account_id)

        if not saved_ids:
            message = "YouTube канал для этого аккаунта не найден."
            return oauth_page("Ошибка YouTube OAuth", message, ok=False)
        channel_word = "канал" if len(saved_ids) == 1 else "каналов"
        return oauth_page(
            "YouTube аккаунт подключён",
            f"Импортировано YouTube {channel_word}: {len(saved_ids)}. Можно закрыть эту вкладку и вернуться в ShortsFarm → Интеграции.",
            ok=True,
        )
    except Exception as exc:
        return oauth_page("Ошибка YouTube OAuth", str(exc) or exc.__class__.__name__, ok=False)


def sync_all_youtube_account_metadata() -> dict[str, Any]:
    summary = {"ok": 0, "failed": 0, "skipped": 0}
    items: list[dict[str, Any]] = []
    for account in db.list_social_accounts(platform="youtube"):
        account_id = int(account["id"])
        if str(account["status"] or "") != "active":
            summary["skipped"] += 1
            items.append({
                "account_id": account_id,
                "status": "skipped",
                "reason": "Аккаунт не активен.",
            })
            continue
        try:
            updated = sync_youtube_account_metadata(account)
            branding_profiles = touch_linked_storage_profiles_youtube_branding(
                account_id,
                account=updated,
                error=None,
            )
            summary["ok"] += 1
            items.append({
                "account_id": account_id,
                "status": "ok",
                "account": youtube_account_dict(updated, include_profile_links=True),
                "branding_profiles": branding_profiles,
            })
        except Exception as sync_exc:
            message = str(sync_exc) or sync_exc.__class__.__name__
            db.set_social_account_metadata_sync_error(account_id, message)
            branding_profiles = touch_linked_storage_profiles_youtube_branding(
                account_id,
                account=account,
                error=message,
            )
            updated = db.get_social_account(account_id)
            summary["failed"] += 1
            items.append({
                "account_id": account_id,
                "status": "failed",
                "error": message,
                "branding_profiles": branding_profiles,
                "account": youtube_account_dict(updated, include_profile_links=True) if updated else None,
            })
    return {"status": "ok", "summary": summary, "items": items}


def sync_one_youtube_account_metadata(account_id: int) -> dict[str, Any]:
    account = db.get_social_account(account_id)
    if account is None or str(account["platform"] or "") != "youtube":
        raise FileNotFoundError("YouTube аккаунт не найден.")
    try:
        updated = sync_youtube_account_metadata(account)
        branding_profiles = touch_linked_storage_profiles_youtube_branding(
            account_id,
            account=updated,
            error=None,
        )
        return {
            "status": "ok",
            "account": youtube_account_dict(updated, include_profile_links=True),
            "branding_profiles": branding_profiles,
        }
    except Exception as sync_exc:
        message = str(sync_exc) or sync_exc.__class__.__name__
        db.set_social_account_metadata_sync_error(account_id, message)
        branding_profiles = touch_linked_storage_profiles_youtube_branding(
            account_id,
            account=account,
            error=message,
        )
        updated = db.get_social_account(account_id)
        return {
            "status": "failed",
            "error": message,
            "branding_profiles": branding_profiles,
            "account": youtube_account_dict(updated, include_profile_links=True) if updated else None,
        }
