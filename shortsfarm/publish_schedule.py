from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_moscow_tz():
    try:
        return ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        # Windows may not have the IANA tz database installed.
        # Moscow is fixed UTC+3 for our scheduling use case.
        return timezone(timedelta(hours=3))


MOSCOW_TZ = _load_moscow_tz()
SCHEDULE_MODES = {"none", "same", "interval", "individual"}
OVERDUE_GRACE = timedelta(minutes=15)
MIN_PUBLISH_LEAD = timedelta(minutes=30)


def parse_schedule_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Некорректная дата расписания: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW_TZ)
    return parsed.astimezone(timezone.utc)


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_schedule_spec(spec: dict[str, Any] | Any) -> dict[str, Any]:
    raw = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec or {})
    mode = str(raw.get("mode") or "none").strip().lower()
    if mode not in SCHEDULE_MODES:
        raise ValueError("Режим расписания должен быть none, same, interval или individual.")
    interval = raw.get("interval_minutes")
    if interval is not None:
        interval = int(interval)
        if interval <= 0:
            raise ValueError("Интервал должен быть больше нуля.")
    item_times = {
        int(key): str(value)
        for key, value in dict(raw.get("item_times") or {}).items()
        if str(value or "").strip()
    }
    return {
        "mode": mode,
        "start_at": str(raw.get("start_at") or "").strip() or None,
        "interval_minutes": interval,
        "item_times": item_times,
    }


def expand_schedule(
    job_ids: list[int],
    spec: dict[str, Any] | Any,
) -> dict[int, str | None]:
    normalized = normalize_schedule_spec(spec)
    mode = normalized["mode"]
    if mode == "none":
        return {job_id: None for job_id in job_ids}

    if mode in {"same", "interval"}:
        start = parse_schedule_datetime(normalized["start_at"])
        if start is None:
            raise ValueError("Для расписания требуется время начала.")
        interval = int(normalized["interval_minutes"] or 0)
        if mode == "interval" and interval <= 0:
            raise ValueError("Для режима interval требуется интервал.")
        return {
            job_id: utc_iso(start + timedelta(minutes=interval * index))
            for index, job_id in enumerate(job_ids)
        }

    missing = [job_id for job_id in job_ids if job_id not in normalized["item_times"]]
    if missing:
        raise ValueError(f"Не задано индивидуальное время для jobs: {', '.join(map(str, missing))}")
    return {
        job_id: utc_iso(parse_schedule_datetime(normalized["item_times"][job_id]))
        for job_id in job_ids
    }


def validate_schedule_pair(upload_at: str | None, publish_at: str | None) -> None:
    upload = parse_schedule_datetime(upload_at)
    publish = parse_schedule_datetime(publish_at)
    if upload is not None and publish is not None and publish - upload < MIN_PUBLISH_LEAD:
        raise ValueError("Публикация должна быть минимум на 30 минут позже начала загрузки.")


def schedule_state(
    upload_at: str | None,
    overdue_approved_at: str | None,
    *,
    now: datetime | None = None,
) -> str:
    upload = parse_schedule_datetime(upload_at)
    if upload is None:
        return "untimed"
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current < upload:
        return "waiting"
    if current <= upload + OVERDUE_GRACE:
        return "due"
    if overdue_approved_at:
        return "approved"
    return "overdue"


def seconds_until(value: str | None, *, now: datetime | None = None) -> int | None:
    target = parse_schedule_datetime(value)
    if target is None:
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return int((target - current).total_seconds())


def schedule_spec_json(spec: dict[str, Any] | Any) -> str:
    normalized = normalize_schedule_spec(spec)
    return json.dumps(normalized["item_times"], ensure_ascii=False, sort_keys=True)
