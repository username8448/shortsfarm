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
    target_aspect: str | None = None


class WorkspaceBulkStatusRequest(BaseModel):
    items: list[str] = Field(default_factory=list)
    workspace_status: str


class WorkspaceBulkDeleteRequest(BaseModel):
    items: list[str] = Field(default_factory=list)


class WorkspacePrepareRequest(BaseModel):
    target_aspect: str = "original"


class WorkspaceBulkPrepareRequest(BaseModel):
    item_keys: list[str] = Field(default_factory=list)
    target_aspect: str = "original"


class WorkspaceYouTubeEnqueueRequest(BaseModel):
    item_keys: list[str] = Field(default_factory=list)
    account_id: int
    publish_mode: str = "private"
    category_id: str = "22"
    made_for_kids: bool = False


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


class PublishJobRunRequest(BaseModel):
    force: bool = False


class YouTubeMetadataUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: str | list[str] | None = None
    category_id: str | None = None
    privacy_status: str | None = None
    made_for_kids: bool | None = None


class PublishJobsBulkRequest(BaseModel):
    job_ids: list[int] = Field(default_factory=list)
    force: bool = False


class PublishScheduleSpecRequest(BaseModel):
    mode: str = "none"
    start_at: str | None = None
    interval_minutes: int | None = Field(default=None, gt=0)
    item_times: dict[int, str] = Field(default_factory=dict)


class PublishScheduleGroupRequest(BaseModel):
    name: str
    job_ids: list[int] = Field(default_factory=list)
    upload: PublishScheduleSpecRequest = Field(default_factory=PublishScheduleSpecRequest)
    publish: PublishScheduleSpecRequest = Field(default_factory=PublishScheduleSpecRequest)


class PublishWorkerRunOnceRequest(BaseModel):
    limit: int = Field(default=3, gt=0)


class ReactionAssetCreateRequest(BaseModel):
    name: str
    file_path: str
    duration_sec: float | None = None
    tags: str | None = None
    mood: str | None = None
    language: str | None = None
    enabled: bool = True


class ReactionAssetUpdateRequest(BaseModel):
    name: str | None = None
    file_path: str | None = None
    duration_sec: float | None = None
    tags: str | None = None
    mood: str | None = None
    language: str | None = None
    enabled: bool | None = None


class ReactionFolderImportRequest(BaseModel):
    folder_path: str
    recursive: bool = True
    tags: str | None = None
    mood: str | None = None
    language: str | None = None


class ReactionPoolCreateRequest(BaseModel):
    name: str
    description: str | None = None
    enabled: bool = True


class ReactionPoolUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


class ReactionPoolItemRequest(BaseModel):
    reaction_asset_id: int
    weight: int = Field(default=1, gt=0)


class EditTemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    renderer: str | None = None
    recipe_json: dict | str | None = None
    enabled: bool | None = None


class ChannelProfileCreateRequest(BaseModel):
    name: str
    youtube_account_id: int | None = None
    default_template_id: int | None = None
    reaction_pool_id: int | None = None
    title_template: str | None = None
    description_template: str | None = None
    tags_template: str | None = None
    default_privacy: str | None = None
    default_category_id: str | None = None
    enabled: bool = True


class ChannelProfileUpdateRequest(BaseModel):
    name: str | None = None
    youtube_account_id: int | None = None
    default_template_id: int | None = None
    reaction_pool_id: int | None = None
    title_template: str | None = None
    description_template: str | None = None
    tags_template: str | None = None
    default_privacy: str | None = None
    default_category_id: str | None = None
    enabled: bool | None = None


class EditJobsPlanRequest(BaseModel):
    item_keys: list[str] = Field(default_factory=list)
    channel_profile_id: int
    template_id: int | None = None
    reaction_asset_id: int | None = None
    force_new: bool = False
