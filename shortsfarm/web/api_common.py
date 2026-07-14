from __future__ import annotations

from fastapi import HTTPException

from .. import db
from ..config import ensure_dirs


def init_api() -> None:
    ensure_dirs()
    db.init_db()


def fail(exc: Exception, status_code: int = 400) -> HTTPException:
    message = str(exc) or exc.__class__.__name__
    return HTTPException(status_code=status_code, detail={"message": message})
