from __future__ import annotations

import sys
import types
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import HTTPException

from shortsfarm.web import integrations_api


class _FakeFlow:
    last_client_config = None
    last_scopes = None
    last_redirect_uri = None
    last_from_client_config_kwargs = None
    from_client_config_calls = []
    last_authorization_kwargs = None

    def __init__(self, client_config, scopes, redirect_uri, **kwargs):
        self.client_config = client_config
        self.scopes = scopes
        self.redirect_uri = redirect_uri
        self.autogenerate_code_verifier = kwargs.get("autogenerate_code_verifier", True)
        self.code_verifier = kwargs.get("code_verifier")

    @classmethod
    def from_client_config(cls, client_config, scopes, redirect_uri, **kwargs):
        cls.last_client_config = client_config
        cls.last_scopes = scopes
        cls.last_redirect_uri = redirect_uri
        cls.last_from_client_config_kwargs = dict(kwargs)
        cls.from_client_config_calls.append(dict(kwargs))
        return cls(client_config, scopes, redirect_uri, **kwargs)

    def authorization_url(self, **kwargs):
        type(self).last_authorization_kwargs = kwargs
        state = kwargs.get("state")
        url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(
            {
                "scope": " ".join(self.scopes),
                "state": state,
            }
        )
        if self.autogenerate_code_verifier:
            url += "&code_challenge=fake-code-challenge&code_challenge_method=S256"
        return (url, state)

    def fetch_token(self, *, code, code_verifier=None):
        if self.autogenerate_code_verifier and not (self.code_verifier or code_verifier):
            raise ValueError("(invalid_grant) Missing code verifier.")
        if "email" in self.scopes or "profile" in self.scopes:
            raise ValueError(
                "Scope has changed from \"email openid profile\" to "
                "\"https://www.googleapis.com/auth/userinfo.email "
                "https://www.googleapis.com/auth/userinfo.profile\""
            )
        self.fetch_code = code

    @property
    def credentials(self):
        from datetime import datetime, timezone
        from shortsfarm.youtube_oauth import YOUTUBE_SCOPES

        class Credentials:
            token = "access-token"
            refresh_token = "refresh-token"
            scopes = YOUTUBE_SCOPES
            expiry = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)

        return Credentials()


def _install_fake_google_modules(
    monkeypatch,
    *,
    email="owner@example.com",
    channel_id="channel-1",
    channel_title="Channel One",
    channels=None,
    channels_error: Exception | None = None,
):
    _FakeFlow.last_client_config = None
    _FakeFlow.last_scopes = None
    _FakeFlow.last_redirect_uri = None
    _FakeFlow.last_from_client_config_kwargs = None
    _FakeFlow.from_client_config_calls = []
    _FakeFlow.last_authorization_kwargs = None

    fake_pkg = types.ModuleType("google_auth_oauthlib")
    fake_flow_mod = types.ModuleType("google_auth_oauthlib.flow")
    fake_flow_mod.Flow = _FakeFlow
    fake_pkg.flow = fake_flow_mod
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", fake_pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_flow_mod)

    fake_googleapi = types.ModuleType("googleapiclient")
    fake_discovery = types.ModuleType("googleapiclient.discovery")

    if channels is None:
        channels = [
            {
                "id": channel_id,
                "snippet": {"title": channel_title},
            }
        ]

    def build(service_name, version, credentials=None, cache_discovery=False):
        class ChannelsResource:
            def list(self, **kwargs):
                class Request:
                    def execute(self):
                        if channels_error is not None:
                            raise channels_error
                        requested_id = kwargs.get("id")
                        items = channels
                        if requested_id:
                            items = [item for item in channels if item.get("id") == requested_id]
                        return {"items": items}

                return Request()

        class UserInfoResource:
            def get(self):
                class Request:
                    def execute(self):
                        return {"email": email}

                return Request()

        class OAuth2Resource:
            def userinfo(self):
                return UserInfoResource()

        class YouTubeService:
            def channels(self):
                return ChannelsResource()

        if service_name == "youtube":
            return YouTubeService()
        if service_name == "oauth2":
            return OAuth2Resource()
        raise AssertionError(f"Unexpected Google API service: {service_name}")

    fake_discovery.build = build
    fake_googleapi.discovery = fake_discovery
    monkeypatch.setitem(sys.modules, "googleapiclient", fake_googleapi)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_discovery)


def _auth_url_scopes(auth_url: str) -> set[str]:
    values = parse_qs(urlparse(auth_url).query).get("scope", [])
    assert len(values) == 1
    return set(values[0].split())


def test_youtube_accounts_response_hides_tokens():
    from shortsfarm import db
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    db.save_social_account(
        platform="youtube",
        display_name="Account",
        channel_id="channel-1",
        channel_title="Channel",
        access_token="access-secret",
        refresh_token="refresh-secret",
        token_expires_at=None,
        scopes="scope-a scope-b",
        oauth_profile_id=profile_id,
        status="active",
    )

    data = integrations_api.youtube_accounts()

    assert len(data["accounts"]) == 1
    account = data["accounts"][0]
    assert account["channel_id"] == "channel-1"
    assert account["status"] == "active"
    assert account["oauth_profile_id"] == profile_id
    assert account["profile_name"] == "Profile One"
    assert "access_token" not in account
    assert "refresh_token" not in account


def test_youtube_settings_save_and_get_hide_secret():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeSettingsRequest

    data = integrations_api.youtube_settings_save(
        YouTubeSettingsRequest(
            client_id="ui-client-id",
            client_secret="ui-client-secret",
            redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "ui-client-id"
    assert data["client_secret_set"] is True

    fetched = integrations_api.youtube_settings()
    assert fetched["configured"] is True
    assert fetched["client_id"] == "ui-client-id"
    assert fetched["client_secret_set"] is True
    assert "client_secret" not in fetched


def test_import_client_json_web_format_works():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeClientJsonImportRequest

    data = integrations_api.youtube_settings_import_client_json(
        YouTubeClientJsonImportRequest(
            json_text='{"web":{"client_id":"web-client","client_secret":"web-secret","redirect_uris":["http://127.0.0.1:8000/api/publish/youtube/oauth/callback"]}}'
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "web-client"
    assert data["client_secret_set"] is True
    assert data["redirect_uri"] == "http://127.0.0.1:8000/api/publish/youtube/oauth/callback"


def test_import_client_json_installed_format_works():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeClientJsonImportRequest

    data = integrations_api.youtube_settings_import_client_json(
        YouTubeClientJsonImportRequest(
            json_text='{"installed":{"client_id":"installed-client","client_secret":"installed-secret","redirect_uris":["http://localhost/callback"]}}'
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "installed-client"
    assert data["client_secret_set"] is True
    assert data["redirect_uri"] == "http://localhost/callback"


def test_youtube_connect_start_without_settings_returns_russian_error(monkeypatch):
    from shortsfarm.web import api

    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REDIRECT_URI", raising=False)

    try:
        integrations_api.youtube_connect_start()
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == {
            "message": "Сначала создайте YouTube OAuth Profile в настройках."
        }
    else:
        raise AssertionError("Expected HTTPException")


def test_youtube_connect_start_works_with_settings_saved_via_ui(monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeSettingsRequest
    from shortsfarm.youtube_oauth import YOUTUBE_SCOPES

    integrations_api.youtube_settings_save(
        YouTubeSettingsRequest(
            client_id="ui-client-id",
            client_secret="ui-client-secret",
            redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        )
    )
    _install_fake_google_modules(monkeypatch)

    data = integrations_api.youtube_connect_start()

    assert data["auth_url"].startswith("https://accounts.google.com/")
    assert _FakeFlow.last_client_config["web"]["client_id"] == "ui-client-id"
    assert _FakeFlow.last_client_config["web"]["client_secret"] == "ui-client-secret"
    assert _FakeFlow.last_redirect_uri == "http://127.0.0.1:8000/api/publish/youtube/oauth/callback"
    assert _FakeFlow.last_scopes == YOUTUBE_SCOPES
    assert _FakeFlow.last_from_client_config_kwargs["autogenerate_code_verifier"] is False
    assert _FakeFlow.last_authorization_kwargs["prompt"] == "consent select_account"
    assert _FakeFlow.last_authorization_kwargs["access_type"] == "offline"
    assert _auth_url_scopes(data["auth_url"]) == set(YOUTUBE_SCOPES)
    assert "https://www.googleapis.com/auth/userinfo.email" in _auth_url_scopes(data["auth_url"])
    assert "https://www.googleapis.com/auth/userinfo.profile" in _auth_url_scopes(data["auth_url"])
    assert "email" not in _auth_url_scopes(data["auth_url"])
    assert "profile" not in _auth_url_scopes(data["auth_url"])
    assert "code_challenge" not in data["auth_url"]
    assert "code_challenge_method" not in data["auth_url"]


def test_youtube_connect_start_web_oauth_does_not_enable_pkce(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.youtube_oauth import YOUTUBE_SCOPES

    db.create_youtube_oauth_profile(
        name="Web Profile",
        mode="custom",
        client_id="web-client",
        client_secret="web-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch)

    data = integrations_api.youtube_connect_start()

    assert data["auth_url"].startswith("https://accounts.google.com/")
    assert _FakeFlow.last_from_client_config_kwargs == {"autogenerate_code_verifier": False}
    assert _FakeFlow.last_scopes == YOUTUBE_SCOPES
    assert _auth_url_scopes(data["auth_url"]) == set(YOUTUBE_SCOPES)
    assert "code_challenge" not in data["auth_url"]
    assert "code_challenge_method" not in data["auth_url"]


def test_oauth_profiles_api_hides_client_secret():
    from shortsfarm import db
    from shortsfarm.web import api

    db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )

    data = integrations_api.youtube_oauth_profiles()

    assert len(data["profiles"]) == 1
    profile = data["profiles"][0]
    assert profile["name"] == "Profile One"
    assert profile["client_secret_set"] is True
    assert "client_secret" not in profile


def test_oauth_profiles_import_web_format_works():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeOAuthProfileImportRequest

    data = integrations_api.youtube_oauth_profiles_import(
        YouTubeOAuthProfileImportRequest(
            json_text='{"web":{"client_id":"web-client","client_secret":"web-secret","redirect_uris":["http://127.0.0.1:8000/api/publish/youtube/oauth/callback"]}}',
            name="Imported Web Profile",
            is_default=True,
        )
    )

    profile = data["profile"]
    assert profile["name"] == "Imported Web Profile"
    assert profile["client_id"] == "web-client"
    assert profile["client_secret_set"] is True
    assert profile["is_default"] is True


def test_oauth_profiles_import_installed_format_works():
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeOAuthProfileImportRequest

    data = integrations_api.youtube_oauth_profiles_import(
        YouTubeOAuthProfileImportRequest(
            json_text='{"installed":{"client_id":"installed-client","client_secret":"installed-secret","redirect_uris":["http://localhost/callback"]}}'
        )
    )

    profile = data["profile"]
    assert profile["client_id"] == "installed-client"
    assert profile["client_secret_set"] is True
    assert profile["redirect_uri"] == "http://localhost/callback"


def test_youtube_connect_start_uses_selected_profile(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeConnectStartRequest

    db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    second_id = db.create_youtube_oauth_profile(
        name="Profile Two",
        mode="custom",
        client_id="client-2",
        client_secret="secret-2",
        redirect_uri="http://localhost/profile-two",
    )
    _install_fake_google_modules(monkeypatch)

    data = integrations_api.youtube_connect_start(YouTubeConnectStartRequest(oauth_profile_id=second_id))

    assert data["oauth_profile_id"] == second_id
    assert data["profile_name"] == "Profile Two"
    assert _FakeFlow.last_client_config["web"]["client_id"] == "client-2"
    assert _FakeFlow.last_redirect_uri == "http://localhost/profile-two"


def test_youtube_connect_start_uses_default_profile_if_missing_id(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Default Profile",
        mode="custom",
        client_id="client-default",
        client_secret="secret-default",
        redirect_uri="http://localhost/default",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch)

    data = integrations_api.youtube_connect_start()

    assert data["oauth_profile_id"] == profile_id
    assert _FakeFlow.last_client_config["web"]["client_id"] == "client-default"


def test_youtube_callback_saves_social_account_with_oauth_profile_id(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api
    from shortsfarm.web.schemas import YouTubeConnectStartRequest
    from shortsfarm.youtube_oauth import YOUTUBE_SCOPES

    profile_id = db.create_youtube_oauth_profile(
        name="Callback Profile",
        mode="custom",
        client_id="client-callback",
        client_secret="secret-callback",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch, email="channel@example.com", channel_id="channel-42", channel_title="Channel 42")

    start_data = integrations_api.youtube_connect_start(YouTubeConnectStartRequest(oauth_profile_id=profile_id))
    assert "code_challenge" not in start_data["auth_url"]
    assert "code_challenge_method" not in start_data["auth_url"]
    state = start_data["auth_url"].split("state=", 1)[1]
    response = integrations_api.youtube_oauth_callback(code="oauth-code", state=state)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Missing code verifier" not in body
    assert "Scope has changed" not in body
    assert [call["autogenerate_code_verifier"] for call in _FakeFlow.from_client_config_calls] == [False, False]
    assert _FakeFlow.last_scopes == YOUTUBE_SCOPES
    accounts = db.list_social_accounts(platform="youtube")
    assert len(accounts) == 1
    account = accounts[0]
    assert account["oauth_profile_id"] == profile_id
    assert account["profile_name"] == "Callback Profile"
    assert account["account_email"] == "channel@example.com"
    assert account["channel_id"] == "channel-42"
    assert account["last_connected_at"] is not None
    saved_scopes = set(str(account["scopes"]).split())
    assert saved_scopes == set(YOUTUBE_SCOPES)
    assert "email" not in saved_scopes
    assert "profile" not in saved_scopes


def test_youtube_callback_saves_channel_metadata_after_oauth(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Metadata Profile",
        mode="custom",
        client_id="metadata-client",
        client_secret="metadata-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(
        monkeypatch,
        email="meta@example.com",
        channels=[
            {
                "id": "meta-channel",
                "snippet": {
                    "title": "Official Meta Channel",
                    "description": "Channel description",
                    "customUrl": "@meta",
                    "country": "US",
                    "publishedAt": "2020-01-02T03:04:05Z",
                    "thumbnails": {"high": {"url": "https://img.example/high.jpg"}},
                },
                "statistics": {
                    "subscriberCount": "1234",
                    "viewCount": "98765",
                    "videoCount": "42",
                    "hiddenSubscriberCount": False,
                },
                "contentDetails": {"relatedPlaylists": {"uploads": "UU-meta"}},
                "status": {"privacyStatus": "public", "madeForKids": False},
                "brandingSettings": {"image": {"bannerExternalUrl": "https://img.example/banner.jpg"}},
            }
        ],
    )

    start = integrations_api.youtube_connect_start()
    state = start["auth_url"].split("state=", 1)[1]
    response = integrations_api.youtube_oauth_callback(code="oauth-code", state=state)

    assert response.status_code == 200
    account = db.list_social_accounts(platform="youtube")[0]
    assert account["oauth_profile_id"] == profile_id
    assert account["channel_title"] == "Official Meta Channel"
    assert account["display_name"] == "Official Meta Channel"
    assert account["channel_description"] == "Channel description"
    assert account["channel_handle"] == "@meta"
    assert account["channel_country"] == "US"
    assert account["channel_avatar_url"] == "https://img.example/high.jpg"
    assert account["channel_banner_url"] == "https://img.example/banner.jpg"
    assert account["subscriber_count"] == 1234
    assert account["view_count"] == 98765
    assert account["video_count"] == 42
    assert account["uploads_playlist_id"] == "UU-meta"
    assert account["metadata_synced_at"] is not None
    assert account["metadata_sync_error"] is None

    data = integrations_api.youtube_accounts()["accounts"][0]
    assert data["local_alias"] == "Official Meta Channel"
    assert data["official_channel_title"] == "Official Meta Channel"
    assert data["channel_avatar_url"] == "https://img.example/high.jpg"
    assert data["channel_banner_url"] == "https://img.example/banner.jpg"
    assert data["subscriber_count"] == 1234
    assert "access_token" not in data
    assert "refresh_token" not in data


def test_youtube_callback_imports_all_channels(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    db.create_youtube_oauth_profile(
        name="Multi Profile",
        mode="custom",
        client_id="multi-client",
        client_secret="multi-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(
        monkeypatch,
        channels=[
            {"id": "channel-a", "snippet": {"title": "Channel A"}},
            {"id": "channel-b", "snippet": {"title": "Channel B"}},
        ],
    )

    state = integrations_api.youtube_connect_start()["auth_url"].split("state=", 1)[1]
    response = integrations_api.youtube_oauth_callback(code="oauth-code", state=state)
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Импортировано YouTube каналов: 2" in body
    accounts = sorted(db.list_social_accounts(platform="youtube"), key=lambda row: row["channel_id"])
    assert [row["channel_id"] for row in accounts] == ["channel-a", "channel-b"]


def test_youtube_callback_without_channels_does_not_create_account(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    db.create_youtube_oauth_profile(
        name="Empty Profile",
        mode="custom",
        client_id="empty-client",
        client_secret="empty-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch, channels=[])

    state = integrations_api.youtube_connect_start()["auth_url"].split("state=", 1)[1]
    response = integrations_api.youtube_oauth_callback(code="oauth-code", state=state)
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "YouTube канал для этого аккаунта не найден" in body
    assert db.list_social_accounts(platform="youtube") == []


def test_youtube_reconnect_preserves_local_alias(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    db.create_youtube_oauth_profile(
        name="Reconnect Profile",
        mode="custom",
        client_id="reconnect-client",
        client_secret="reconnect-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    db.save_social_account(
        platform="youtube",
        display_name="Local Alias",
        channel_id="same-channel",
        channel_title="Old Official",
        access_token="old-access",
        refresh_token="old-refresh",
        token_expires_at=None,
        scopes="scope",
        status="active",
    )
    _install_fake_google_modules(
        monkeypatch,
        channels=[{"id": "same-channel", "snippet": {"title": "New Official"}}],
    )

    state = integrations_api.youtube_connect_start()["auth_url"].split("state=", 1)[1]
    response = integrations_api.youtube_oauth_callback(code="oauth-code", state=state)

    assert response.status_code == 200
    account = db.list_social_accounts(platform="youtube")[0]
    assert account["display_name"] == "Local Alias"
    assert account["channel_title"] == "New Official"
    assert account["access_token"] == "access-token"


def test_youtube_account_manual_sync_updates_metadata(monkeypatch):
    from shortsfarm import db, publish_youtube
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Manual Sync Profile",
        mode="custom",
        client_id="manual-client",
        client_secret="manual-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    account_id = db.save_social_account(
        platform="youtube",
        display_name="Manual Alias",
        channel_id="manual-channel",
        channel_title="Old Title",
        access_token="access",
        refresh_token="refresh",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.readonly",
        oauth_profile_id=profile_id,
        status="active",
    )

    class Channels:
        def list(self, **kwargs):
            class Request:
                def execute(self):
                    return {
                        "items": [
                            {
                                "id": "manual-channel",
                                "snippet": {
                                    "title": "Manual Official",
                                    "customUrl": "@manual",
                                    "thumbnails": {"default": {"url": "https://img.example/manual.jpg"}},
                                },
                                "statistics": {"subscriberCount": "7", "viewCount": "8", "videoCount": "9"},
                                "contentDetails": {"relatedPlaylists": {"uploads": "UU-manual"}},
                                "status": {"privacyStatus": "public"},
                            }
                        ]
                    }

            return Request()

    class YouTube:
        def channels(self):
            return Channels()

    monkeypatch.setattr(publish_youtube, "build_youtube_client", lambda account: YouTube())

    result = integrations_api.youtube_account_sync_metadata(account_id)

    assert result["status"] == "ok"
    assert result["account"]["local_alias"] == "Manual Alias"
    assert result["account"]["channel_title"] == "Manual Official"
    assert result["account"]["channel_handle"] == "@manual"
    assert result["account"]["subscriber_count"] == 7
    assert result["account"]["metadata_sync_error"] is None


def test_youtube_account_manual_sync_error_preserves_tokens(monkeypatch):
    from shortsfarm import db, publish_youtube
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Manual Error Profile",
        mode="custom",
        client_id="error-client",
        client_secret="error-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    account_id = db.save_social_account(
        platform="youtube",
        display_name="Error Alias",
        channel_id="error-channel",
        channel_title="Error Channel",
        access_token="access-secret",
        refresh_token="refresh-secret",
        token_expires_at=None,
        scopes="https://www.googleapis.com/auth/youtube.readonly",
        oauth_profile_id=profile_id,
        status="active",
    )

    def fail_build(account):
        raise RuntimeError("YouTube API недоступен")

    monkeypatch.setattr(publish_youtube, "build_youtube_client", fail_build)

    result = integrations_api.youtube_account_sync_metadata(account_id)
    account = db.get_social_account(account_id)

    assert result["status"] == "failed"
    assert "YouTube API недоступен" in result["error"]
    assert account["access_token"] == "access-secret"
    assert account["refresh_token"] == "refresh-secret"
    assert account["metadata_sync_error"] == "YouTube API недоступен"


def test_youtube_callback_without_state_returns_russian_html_error():
    from shortsfarm.web import api

    response = integrations_api.youtube_oauth_callback(code="dummy", state=None)
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "Callback не содержит state" in body
    assert "Traceback" not in body


def test_youtube_callback_invalid_state_returns_russian_html_error():
    from shortsfarm.web import api

    response = integrations_api.youtube_oauth_callback(code="dummy", state="invalid")
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "OAuth state не найден или уже был использован" in body
    assert "Traceback" not in body


def test_youtube_callback_error_allows_second_auth_attempt(monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Retry Profile",
        mode="custom",
        client_id="retry-client",
        client_secret="retry-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch, email="retry@example.com", channel_id="retry-channel", channel_title="Retry Channel")

    first_start = integrations_api.youtube_connect_start()
    first_state = first_start["auth_url"].split("state=", 1)[1]
    first_response = integrations_api.youtube_oauth_callback(error="access_denied", state=first_state)
    first_body = first_response.body.decode("utf-8")

    assert first_response.status_code == 400
    assert "попробовать подключение ещё раз" in first_body
    assert db.consume_oauth_state("youtube", first_state) is None

    second_start = integrations_api.youtube_connect_start()
    second_state = second_start["auth_url"].split("state=", 1)[1]
    second_response = integrations_api.youtube_oauth_callback(code="oauth-code", state=second_state)

    assert second_start["oauth_profile_id"] == profile_id
    assert second_state != first_state
    assert second_response.status_code == 200
    accounts = db.list_social_accounts(platform="youtube")
    assert len(accounts) == 1
    assert accounts[0]["channel_id"] == "retry-channel"
