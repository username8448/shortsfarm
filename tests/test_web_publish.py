from __future__ import annotations

import sys
import types
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import HTTPException


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
        from shortfarm.youtube_oauth import YOUTUBE_SCOPES

        class Credentials:
            token = "access-token"
            refresh_token = "refresh-token"
            scopes = YOUTUBE_SCOPES
            expiry = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)

        return Credentials()


def _install_fake_google_modules(monkeypatch, *, email="owner@example.com", channel_id="channel-1", channel_title="Channel One"):
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

    def build(service_name, version, credentials=None, cache_discovery=False):
        class ChannelsResource:
            def list(self, **kwargs):
                class Request:
                    def execute(self):
                        return {
                            "items": [
                                {
                                    "id": channel_id,
                                    "snippet": {"title": channel_title},
                                }
                            ]
                        }

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
    from shortfarm import db
    from shortfarm.web import api

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

    data = api.youtube_accounts()

    assert len(data["accounts"]) == 1
    account = data["accounts"][0]
    assert account["channel_id"] == "channel-1"
    assert account["status"] == "active"
    assert account["oauth_profile_id"] == profile_id
    assert account["profile_name"] == "Profile One"
    assert "access_token" not in account
    assert "refresh_token" not in account


def test_youtube_settings_save_and_get_hide_secret():
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeSettingsRequest

    data = api.youtube_settings_save(
        YouTubeSettingsRequest(
            client_id="ui-client-id",
            client_secret="ui-client-secret",
            redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "ui-client-id"
    assert data["client_secret_set"] is True

    fetched = api.youtube_settings()
    assert fetched["configured"] is True
    assert fetched["client_id"] == "ui-client-id"
    assert fetched["client_secret_set"] is True
    assert "client_secret" not in fetched


def test_import_client_json_web_format_works():
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeClientJsonImportRequest

    data = api.youtube_settings_import_client_json(
        YouTubeClientJsonImportRequest(
            json_text='{"web":{"client_id":"web-client","client_secret":"web-secret","redirect_uris":["http://127.0.0.1:8000/api/publish/youtube/oauth/callback"]}}'
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "web-client"
    assert data["client_secret_set"] is True
    assert data["redirect_uri"] == "http://127.0.0.1:8000/api/publish/youtube/oauth/callback"


def test_import_client_json_installed_format_works():
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeClientJsonImportRequest

    data = api.youtube_settings_import_client_json(
        YouTubeClientJsonImportRequest(
            json_text='{"installed":{"client_id":"installed-client","client_secret":"installed-secret","redirect_uris":["http://localhost/callback"]}}'
        )
    )

    assert data["configured"] is True
    assert data["client_id"] == "installed-client"
    assert data["client_secret_set"] is True
    assert data["redirect_uri"] == "http://localhost/callback"


def test_youtube_connect_start_without_settings_returns_russian_error(monkeypatch):
    from shortfarm.web import api

    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_REDIRECT_URI", raising=False)

    try:
        api.youtube_connect_start()
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == {
            "message": "Сначала создайте YouTube OAuth Profile в настройках."
        }
    else:
        raise AssertionError("Expected HTTPException")


def test_youtube_connect_start_works_with_settings_saved_via_ui(monkeypatch):
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeSettingsRequest
    from shortfarm.youtube_oauth import YOUTUBE_SCOPES

    api.youtube_settings_save(
        YouTubeSettingsRequest(
            client_id="ui-client-id",
            client_secret="ui-client-secret",
            redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        )
    )
    _install_fake_google_modules(monkeypatch)

    data = api.youtube_connect_start()

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
    from shortfarm import db
    from shortfarm.web import api
    from shortfarm.youtube_oauth import YOUTUBE_SCOPES

    db.create_youtube_oauth_profile(
        name="Web Profile",
        mode="custom",
        client_id="web-client",
        client_secret="web-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch)

    data = api.youtube_connect_start()

    assert data["auth_url"].startswith("https://accounts.google.com/")
    assert _FakeFlow.last_from_client_config_kwargs == {"autogenerate_code_verifier": False}
    assert _FakeFlow.last_scopes == YOUTUBE_SCOPES
    assert _auth_url_scopes(data["auth_url"]) == set(YOUTUBE_SCOPES)
    assert "code_challenge" not in data["auth_url"]
    assert "code_challenge_method" not in data["auth_url"]


def test_oauth_profiles_api_hides_client_secret():
    from shortfarm import db
    from shortfarm.web import api

    db.create_youtube_oauth_profile(
        name="Profile One",
        mode="custom",
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )

    data = api.youtube_oauth_profiles()

    assert len(data["profiles"]) == 1
    profile = data["profiles"][0]
    assert profile["name"] == "Profile One"
    assert profile["client_secret_set"] is True
    assert "client_secret" not in profile


def test_oauth_profiles_import_web_format_works():
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeOAuthProfileImportRequest

    data = api.youtube_oauth_profiles_import(
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
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeOAuthProfileImportRequest

    data = api.youtube_oauth_profiles_import(
        YouTubeOAuthProfileImportRequest(
            json_text='{"installed":{"client_id":"installed-client","client_secret":"installed-secret","redirect_uris":["http://localhost/callback"]}}'
        )
    )

    profile = data["profile"]
    assert profile["client_id"] == "installed-client"
    assert profile["client_secret_set"] is True
    assert profile["redirect_uri"] == "http://localhost/callback"


def test_youtube_connect_start_uses_selected_profile(monkeypatch):
    from shortfarm import db
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeConnectStartRequest

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

    data = api.youtube_connect_start(YouTubeConnectStartRequest(oauth_profile_id=second_id))

    assert data["oauth_profile_id"] == second_id
    assert data["profile_name"] == "Profile Two"
    assert _FakeFlow.last_client_config["web"]["client_id"] == "client-2"
    assert _FakeFlow.last_redirect_uri == "http://localhost/profile-two"


def test_youtube_connect_start_uses_default_profile_if_missing_id(monkeypatch):
    from shortfarm import db
    from shortfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Default Profile",
        mode="custom",
        client_id="client-default",
        client_secret="secret-default",
        redirect_uri="http://localhost/default",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch)

    data = api.youtube_connect_start()

    assert data["oauth_profile_id"] == profile_id
    assert _FakeFlow.last_client_config["web"]["client_id"] == "client-default"


def test_youtube_callback_saves_social_account_with_oauth_profile_id(monkeypatch):
    from shortfarm import db
    from shortfarm.web import api
    from shortfarm.web.schemas import YouTubeConnectStartRequest
    from shortfarm.youtube_oauth import YOUTUBE_SCOPES

    profile_id = db.create_youtube_oauth_profile(
        name="Callback Profile",
        mode="custom",
        client_id="client-callback",
        client_secret="secret-callback",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch, email="channel@example.com", channel_id="channel-42", channel_title="Channel 42")

    start_data = api.youtube_connect_start(YouTubeConnectStartRequest(oauth_profile_id=profile_id))
    assert "code_challenge" not in start_data["auth_url"]
    assert "code_challenge_method" not in start_data["auth_url"]
    state = start_data["auth_url"].split("state=", 1)[1]
    response = api.youtube_oauth_callback(code="oauth-code", state=state)
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


def test_youtube_callback_without_state_returns_russian_html_error():
    from shortfarm.web import api

    response = api.youtube_oauth_callback(code="dummy", state=None)
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "Callback не содержит state" in body
    assert "Traceback" not in body


def test_youtube_callback_invalid_state_returns_russian_html_error():
    from shortfarm.web import api

    response = api.youtube_oauth_callback(code="dummy", state="invalid")
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert "OAuth state не найден или уже был использован" in body
    assert "Traceback" not in body


def test_youtube_callback_error_allows_second_auth_attempt(monkeypatch):
    from shortfarm import db
    from shortfarm.web import api

    profile_id = db.create_youtube_oauth_profile(
        name="Retry Profile",
        mode="custom",
        client_id="retry-client",
        client_secret="retry-secret",
        redirect_uri="http://127.0.0.1:8000/api/publish/youtube/oauth/callback",
        is_default=True,
    )
    _install_fake_google_modules(monkeypatch, email="retry@example.com", channel_id="retry-channel", channel_title="Retry Channel")

    first_start = api.youtube_connect_start()
    first_state = first_start["auth_url"].split("state=", 1)[1]
    first_response = api.youtube_oauth_callback(error="access_denied", state=first_state)
    first_body = first_response.body.decode("utf-8")

    assert first_response.status_code == 400
    assert "попробовать подключение ещё раз" in first_body
    assert db.consume_oauth_state("youtube", first_state) is None

    second_start = api.youtube_connect_start()
    second_state = second_start["auth_url"].split("state=", 1)[1]
    second_response = api.youtube_oauth_callback(code="oauth-code", state=second_state)

    assert second_start["oauth_profile_id"] == profile_id
    assert second_state != first_state
    assert second_response.status_code == 200
    accounts = db.list_social_accounts(platform="youtube")
    assert len(accounts) == 1
    assert accounts[0]["channel_id"] == "retry-channel"
