from __future__ import annotations

import os
import sqlite3
from pathlib import Path


APP_NAME = "shortsfarm"
YOUTUBE_CLIENT_ID_SETTING = "youtube_client_id"
YOUTUBE_CLIENT_SECRET_SETTING = "youtube_client_secret"
YOUTUBE_REDIRECT_URI_SETTING = "youtube_redirect_uri"
DEFAULT_YOUTUBE_REDIRECT_URI = "http://127.0.0.1:8000/api/publish/youtube/oauth/callback"

def data_dir() -> Path:
    env_value = os.environ.get("SHORTSFARM_HOME")
    if env_value:
        return Path(env_value).expanduser().resolve()

    return Path("shortsfarm-data").resolve()


def input_dir() -> Path:
    return data_dir() / "input"


def output_dir() -> Path:
    return data_dir() / "output"


def logs_dir() -> Path:
    return data_dir() / "logs"


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def _setting_from_db(key: str) -> str | None:
    path = db_path()
    if not path.exists():
        return None
    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(str(path))
        row = con.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value = row[0]
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass


def youtube_client_id() -> str | None:
    return _setting_from_db(YOUTUBE_CLIENT_ID_SETTING) or os.environ.get("YOUTUBE_CLIENT_ID") or None


def youtube_client_secret() -> str | None:
    return _setting_from_db(YOUTUBE_CLIENT_SECRET_SETTING) or os.environ.get("YOUTUBE_CLIENT_SECRET") or None


def youtube_redirect_uri() -> str:
    return (
        _setting_from_db(YOUTUBE_REDIRECT_URI_SETTING)
        or os.environ.get("YOUTUBE_REDIRECT_URI")
        or DEFAULT_YOUTUBE_REDIRECT_URI
    )


def ensure_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    input_dir().mkdir(parents=True, exist_ok=True)
    output_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
