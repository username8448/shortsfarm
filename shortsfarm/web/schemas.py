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


class OpenMpvRequest(BaseModel):
    path: str


class WorkspaceItemUpdateRequest(BaseModel):
    workspace_status: str | None = None
    title: str | None = None
    description: str | None = None
    tags: str | None = None


class WorkspaceBulkStatusRequest(BaseModel):
    items: list[str] = Field(default_factory=list)
    workspace_status: str


class YouTubeSettingsRequest(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str | None = None


class YouTubeClientJsonImportRequest(BaseModel):
    json_text: str


class YouTubeOAuthProfileCreateRequest(BaseModel):
    name: str
    client_id: str
    client_secret: str
    redirect_uri: str | None = None
    notes: str | None = None
    is_default: bool = False


class YouTubeOAuthProfileUpdateRequest(BaseModel):
    name: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    notes: str | None = None
    status: str | None = None


class YouTubeOAuthProfileImportRequest(BaseModel):
    json_text: str
    name: str | None = None
    notes: str | None = None
    is_default: bool = False


class YouTubeConnectStartRequest(BaseModel):
    oauth_profile_id: int | None = None


class YouTubeUploadRequest(BaseModel):
    account_id: int
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: str = "22"
    publish_mode: str = "private"
    publish_at: str | None = None
    made_for_kids: bool = False


class PublishJobResponse(BaseModel):
    id: int
    platform: str
    account_id: int
    clip_id: int
    status: str
    title: str
    description: str | None = None
    tags: str | None = None
    category_id: str
    privacy_status: str
    publish_mode: str
    publish_at: str | None = None
    made_for_kids: bool
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    attempt_count: int = 0
    last_attempt_at: str | None = None
    next_attempt_at: str | None = None
    oauth_profile_id: int | None = None
    profile_name: str = ""
    account_display_name: str = ""
    account_email: str = ""
    channel_id: str = ""
    channel_title: str = ""
    clip_video_id: int | None = None
    clip_status: str = ""
    clip_output_path: str = ""
    clip_cut_mode: str = ""
    video_title: str = ""
    video_source_path: str = ""


class PublishJobRetryRequest(BaseModel):
    pass


class PublishWorkerRunOnceRequest(BaseModel):
    limit: int = Field(default=3, gt=0)
