from __future__ import annotations


def test_youtube_config_prefers_db_settings(monkeypatch):
    from shortsfarm import db
    import shortsfarm.config as cfg

    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env-client")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("YOUTUBE_REDIRECT_URI", "http://env.example/callback")

    db.set_setting(cfg.YOUTUBE_CLIENT_ID_SETTING, "db-client")
    db.set_setting(cfg.YOUTUBE_CLIENT_SECRET_SETTING, "db-secret", is_secret=True)
    db.set_setting(cfg.YOUTUBE_REDIRECT_URI_SETTING, "http://db.example/callback")

    assert cfg.youtube_client_id() == "db-client"
    assert cfg.youtube_client_secret() == "db-secret"
    assert cfg.youtube_redirect_uri() == "http://db.example/callback"


def test_youtube_config_env_fallback_when_db_settings_absent(monkeypatch):
    import shortsfarm.config as cfg

    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env-client")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("YOUTUBE_REDIRECT_URI", "http://env.example/callback")

    assert cfg.youtube_client_id() == "env-client"
    assert cfg.youtube_client_secret() == "env-secret"
    assert cfg.youtube_redirect_uri() == "http://env.example/callback"
