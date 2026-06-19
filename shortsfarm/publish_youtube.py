from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .youtube_oauth import YOUTUBE_SCOPES

PUBLISH_MODES = {"private", "unlisted", "public", "schedule"}
TRANSIENT_UPLOAD_STATUSES = {500, 502, 503, 504}
CHUNK_MAX_RETRIES = 5
AUTO_RETRY_LIMIT = 2
UPLOAD_CHUNKSIZE = 50 * 1024 * 1024
WORKER_PAUSE_SECONDS = 10.0
YOUTUBE_METADATA_WRITE_SCOPES = {
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtubepartner",
}


def parse_tags(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]

    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("publish_at должен быть ISO datetime, например 2026-06-15T18:00:00Z") from exc


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = _parse_iso_datetime(value)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def validate_publish_options(
    *,
    title: str,
    publish_mode: str = "private",
    publish_at: str | None = None,
    category_id: str = "22",
) -> dict[str, str | None]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("title обязателен для публикации в YouTube")

    if publish_mode not in PUBLISH_MODES:
        raise ValueError("publish_mode должен быть private, unlisted, public или schedule")

    if not category_id.strip():
        raise ValueError("category_id обязателен")

    if publish_mode == "schedule":
        if not publish_at:
            raise ValueError("publish_at обязателен для schedule")
        _parse_iso_datetime(publish_at)
        return {"privacy_status": "private", "publish_at": publish_at}

    return {
        "privacy_status": publish_mode,
        "publish_at": None,
    }


def _require_row(row: Any | None, message: str) -> Any:
    if row is None:
        raise ValueError(message)
    return row


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _account_profile(account: Any) -> Any:
    profile_id = account["oauth_profile_id"]
    if profile_id is not None:
        profile = db.get_youtube_oauth_profile(int(profile_id))
        if profile is None:
            raise RuntimeError("OAuth Profile для YouTube аккаунта не найден. Подключите канал заново.")
        if str(profile["status"] or "active") != "active":
            raise RuntimeError("OAuth Profile для YouTube аккаунта не активен.")
        return profile

    default_profile = db.get_default_youtube_oauth_profile()
    if default_profile is not None and str(default_profile["status"] or "active") == "active":
        return default_profile

    active_profiles = [
        row for row in db.list_youtube_oauth_profiles()
        if str(row["status"] or "active") == "active"
    ]
    if len(active_profiles) == 1:
        return active_profiles[0]
    if not active_profiles:
        raise RuntimeError("Не найден YouTube OAuth Profile. Откройте настройки и подключите профиль.")
    raise RuntimeError("Для этого аккаунта не определён OAuth Profile. Подключите канал заново.")


def refresh_youtube_credentials_if_needed(account: Any) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("Google OAuth зависимости не установлены. Выполните: pip install -e .") from exc

    profile = _account_profile(account)
    client_id = str(profile["client_id"] or "").strip()
    client_secret = str(profile["client_secret"] or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("У OAuth Profile не заполнены client_id/client_secret.")

    credentials = Credentials(
        token=account["access_token"],
        refresh_token=account["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=str(account["scopes"] or "").split() or YOUTUBE_SCOPES,
    )
    credentials.expiry = _parse_expiry(account["token_expires_at"])

    if not credentials.valid:
        if not account["refresh_token"]:
            raise RuntimeError("YouTube refresh_token отсутствует. Подключите аккаунт заново.")
        credentials.refresh(Request())
        expires_at = credentials.expiry.isoformat() if credentials.expiry else None
        # TODO: encrypt tokens before production use.
        db.save_social_account(
            platform="youtube",
            display_name=account["display_name"],
            channel_id=account["channel_id"],
            channel_title=account["channel_title"],
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_expires_at=expires_at,
            scopes=" ".join(credentials.scopes or YOUTUBE_SCOPES),
            oauth_profile_id=int(profile["id"]),
            account_email=account["account_email"],
            last_connected_at=account["last_connected_at"],
            status=account["status"],
            error=None,
        )

    return credentials


def build_youtube_client(account: Any) -> Any:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("google-api-python-client не установлен. Выполните: pip install -e .") from exc

    credentials = refresh_youtube_credentials_if_needed(account)
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _media_file_upload(path: Path) -> Any:
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError("google-api-python-client не установлен. Выполните: pip install -e .") from exc

    return MediaFileUpload(
        str(path),
        mimetype="video/*",
        chunksize=UPLOAD_CHUNKSIZE,
        resumable=True,
    )


def validate_publish_job(job: Any) -> dict[str, Any]:
    account = _require_row(db.get_social_account(int(job["account_id"])), "YouTube аккаунт не найден.")
    clip = _require_row(db.get_clip(int(job["clip_id"])), "Клип не найден.")
    profile = _account_profile(account)

    if account["platform"] != "youtube":
        raise ValueError("Аккаунт не является YouTube аккаунтом.")
    if account["status"] != "active":
        raise ValueError("YouTube аккаунт не активен.")
    if clip["status"] != "done":
        raise ValueError("Можно загружать только готовые clips.status='done'.")
    if not clip["output_path"]:
        raise ValueError("У клипа нет output_path.")

    output_path = Path(str(clip["output_path"])).expanduser().resolve()
    if not output_path.exists() or not output_path.is_file():
        raise FileNotFoundError(f"Файл клипа не найден: {output_path}")

    validate_publish_options(
        title=str(job["title"] or ""),
        publish_mode=str(job["publish_mode"] or "private"),
        publish_at=job["publish_at"],
        category_id=str(job["category_id"] or "22"),
    )
    return {
        "account": account,
        "profile": profile,
        "clip": clip,
        "output_path": output_path,
    }


def build_youtube_video_body(job: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "privacyStatus": job["privacy_status"],
        "selfDeclaredMadeForKids": bool(job["made_for_kids"]),
    }
    if job["publish_mode"] == "schedule":
        status["publishAt"] = job["publish_at"]

    return {
        "snippet": {
            "title": job["title"],
            "description": job["description"] or "",
            "tags": parse_tags(job["tags"]),
            "categoryId": job["category_id"] or "22",
        },
        "status": status,
    }


def update_youtube_video_metadata(
    job_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: str | list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
    made_for_kids: bool | None = None,
) -> Any:
    job = _require_row(db.get_publish_job(job_id), f"Publish job {job_id} не найден.")
    if str(job["status"] or "") != "done":
        raise ValueError("Обновить данные на YouTube можно только для загруженного видео.")

    youtube_video_id = str(job["youtube_video_id"] or "").strip()
    if not youtube_video_id:
        raise ValueError("У publish job отсутствует youtube_video_id.")

    clean_title = str(title if title is not None else job["title"] or "").strip()
    clean_description = str(
        description if description is not None else job["description"] or ""
    )
    clean_tags = parse_tags(tags if tags is not None else job["tags"])
    clean_category_id = str(category_id if category_id is not None else job["category_id"] or "22").strip()
    clean_privacy = str(
        privacy_status if privacy_status is not None else job["privacy_status"] or "private"
    ).strip()
    clean_made_for_kids = (
        bool(made_for_kids)
        if made_for_kids is not None
        else bool(job["made_for_kids"])
    )

    if not clean_title:
        raise ValueError("Название видео не может быть пустым.")
    if not clean_category_id:
        raise ValueError("category_id обязателен.")
    if clean_privacy not in {"private", "unlisted", "public"}:
        raise ValueError("privacy_status должен быть private, unlisted или public.")

    account = _require_row(
        db.get_social_account(int(job["account_id"])),
        "YouTube аккаунт не найден.",
    )
    if str(account["platform"] or "") != "youtube":
        raise ValueError("Аккаунт publish job не является YouTube аккаунтом.")
    if str(account["status"] or "") != "active":
        raise ValueError("YouTube аккаунт не активен.")
    account_scopes = set(str(account["scopes"] or "").split())
    if not account_scopes.intersection(YOUTUBE_METADATA_WRITE_SCOPES):
        raise RuntimeError(
            "YouTube аккаунт подключён без права изменения metadata. "
            "Переподключите канал через Google OAuth."
        )

    try:
        youtube = build_youtube_client(account)
        current_response = youtube.videos().list(
            part="snippet,status",
            id=youtube_video_id,
        ).execute()
        current_items = current_response.get("items") or []
        if not current_items:
            raise FileNotFoundError("Видео не найдено в YouTube.")
        current = current_items[0]
        current_snippet = current.get("snippet") or {}
        current_status = current.get("status") or {}
        snippet = {
            key: current_snippet[key]
            for key in ("defaultLanguage",)
            if key in current_snippet
        }
        snippet.update({
            "title": clean_title,
            "description": clean_description,
            "tags": clean_tags,
            "categoryId": clean_category_id,
        })
        status = {
            key: current_status[key]
            for key in (
                "embeddable",
                "license",
                "publicStatsViewable",
                "publishAt",
                "containsSyntheticMedia",
            )
            if key in current_status
        }
        status.update({
            "privacyStatus": clean_privacy,
            "selfDeclaredMadeForKids": clean_made_for_kids,
        })
        body = {
            "id": youtube_video_id,
            "snippet": snippet,
            "status": status,
        }
        youtube.videos().update(part="snippet,status", body=body).execute()
        db.update_publish_job_metadata(
            job_id,
            title=clean_title,
            description=clean_description,
            tags=json.dumps(clean_tags, ensure_ascii=False),
            category_id=clean_category_id,
            privacy_status=clean_privacy,
            made_for_kids=clean_made_for_kids,
            error=None,
        )
        return db.get_publish_job(job_id)
    except Exception as exc:
        db.set_publish_job_error(job_id, str(exc) or exc.__class__.__name__)
        raise


def _google_error_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "resp", None)
    if response is None:
        return None
    for attr in ("status", "status_code"):
        value = getattr(response, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_retryable_upload_error(exc: Exception) -> bool:
    return _google_error_status(exc) in TRANSIENT_UPLOAD_STATUSES


def _next_attempt_at(attempt_count: int) -> str:
    delay_seconds = 60 * (2 ** max(attempt_count - 1, 0))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()


def _run_resumable_request(request: Any) -> dict[str, Any]:
    response = None
    attempts = 0
    while response is None:
        try:
            _status, response = request.next_chunk()
        except Exception as exc:
            if not _is_retryable_upload_error(exc) or attempts >= CHUNK_MAX_RETRIES:
                raise
            _sleep(2 ** attempts)
            attempts += 1
    return response


def _mark_failed_job(job: Any, exc: Exception) -> None:
    attempt_count = int(job["attempt_count"] or 0)
    retryable = _is_retryable_upload_error(exc) and attempt_count <= AUTO_RETRY_LIMIT
    db.mark_publish_failed(
        int(job["id"]),
        str(exc) or exc.__class__.__name__,
        retryable=retryable,
        next_attempt_at=_next_attempt_at(attempt_count) if retryable else None,
    )


def _upload_claimed_job(job: Any) -> Any:
    job_id = int(job["id"])
    try:
        validated = validate_publish_job(job)
        account = validated["account"]
        output_path = validated["output_path"]
        youtube = build_youtube_client(account)
        request = youtube.videos().insert(
            part="snippet,status",
            body=build_youtube_video_body(job),
            media_body=_media_file_upload(output_path),
        )
        response = _run_resumable_request(request)

        youtube_video_id = str(response.get("id") or "")
        if not youtube_video_id:
            raise RuntimeError("YouTube upload завершился без video id.")

        youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
        db.mark_publish_done(job_id, youtube_video_id, youtube_url)
        return db.get_publish_job(job_id)
    except Exception as exc:
        _mark_failed_job(job, exc)
        raise


def upload_clip_to_youtube(job_id: int) -> Any:
    job = _require_row(db.get_publish_job(job_id), f"Publish job {job_id} not found")
    if job["status"] == "done":
        return job

    claimed = db.claim_publish_job(job_id)
    if claimed is None:
        raise RuntimeError(f"Publish job {job_id} нельзя запустить из статуса {job['status']}")
    return _upload_claimed_job(claimed)


def run_publish_job_now(job_id: int) -> Any:
    return upload_clip_to_youtube(job_id)


def run_publish_queue_once(limit: int = 3, pause_seconds: float = WORKER_PAUSE_SECONDS) -> list[Any]:
    processed: list[Any] = []
    for index in range(limit):
        claimed = db.claim_next_publish_job()
        if claimed is None:
            break
        try:
            processed.append(_upload_claimed_job(claimed))
        except Exception:
            processed.append(db.get_publish_job(int(claimed["id"])))
        if index + 1 < limit:
            _sleep(pause_seconds)
    return processed


def run_publish_worker(
    *,
    once: bool = False,
    poll_interval: int = 60,
    limit: int = 3,
    pause_seconds: float = WORKER_PAUSE_SECONDS,
) -> int:
    handled = 0
    while True:
        jobs = run_publish_queue_once(limit=limit, pause_seconds=pause_seconds)
        handled += len(jobs)
        if once:
            return handled
        if not jobs:
            _sleep(float(poll_interval))
