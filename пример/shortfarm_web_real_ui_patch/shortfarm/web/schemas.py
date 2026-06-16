from __future__ import annotations

from pydantic import BaseModel, Field


class SplitRequest(BaseModel):
    kind: str = "file"  # file | folder
    path: str
    seconds: int = Field(default=60, gt=0)
    skip: list[str] = Field(default_factory=list)
    dry_run: bool = False
    overwrite: bool = False


class RenderRequest(BaseModel):
    limit: int = Field(default=10, gt=0)


class RetryFailedRequest(BaseModel):
    clip_id: int | None = None
