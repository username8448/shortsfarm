from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .. import db
from ..config import ensure_dirs


def init_api() -> None:
    ensure_dirs()
    db.init_db()


def fail(exc: Exception, status_code: int = 400) -> HTTPException:
    message = str(exc) or exc.__class__.__name__
    return HTTPException(status_code=status_code, detail={"message": message})


def normalize_setting_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return default
