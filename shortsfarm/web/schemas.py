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


class WorkspaceRootRequest(BaseModel):
    workspace_root: str


class LocalDialogPickRequest(BaseModel):
    kind: str = "file"  # file | directory
    title: str | None = None


class FileFolderCreateRequest(BaseModel):
    parent_path: str = ""
    name: str
    kind: str = "custom"


class FileRenameRequest(BaseModel):
    path: str
    new_name: str


class FileMoveRequest(BaseModel):
    source_path: str
    target_folder: str


class FileImportSourceRequest(BaseModel):
    source_path: str
    target_folder: str = "sources"
    mode: str = "copy"


class FileRegisterSourceRequest(BaseModel):
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
    remove_from_profiles: bool = False


class VideoBulkDeleteRequest(BaseModel):
    video_ids: list[int] = Field(default_factory=list)
    delete_source_files: bool = False
    delete_child_clips: bool = False
    remove_from_profiles: bool = False


class VideoChildClipsDeleteRequest(BaseModel):
    remove_from_profiles: bool = False


class VideoRelinkSourceRequest(BaseModel):
    source_path: str


class DatabaseResetRequest(BaseModel):
    confirmation: str = ""
    create_backup: bool = True


class WorkspacePrepareRequest(BaseModel):
    target_aspect: str = "original"


class WorkspaceBulkPrepareRequest(BaseModel):
    item_keys: list[str] = Field(default_factory=list)
    target_aspect: str = "original"


class WorkspaceYouTubeEnqueueRequest(BaseModel):
    item_keys: list[str] = Field(default_factory=list)
    account_id: int
    publish_mode: str = "public"
    category_id: str = "22"
    made_for_kids: bool = False


class TagCreateRequest(BaseModel):
    name: str
    slug: str | None = None
    kind: str = "user"
    color: str | None = None
    description: str | None = None


class TagUpdateRequest(BaseModel):
    name: str | None = None
    slug: str | None = None
    color: str | None = None
    description: str | None = None
    enabled: bool | None = None


class CatalogVideoTagsRequest(BaseModel):
    workspace_path: str
    tag_ids: list[int] = Field(default_factory=list)
    mode: str = "replace"


class LocalStorageProfileTagRulesRequest(BaseModel):
    include_tag_ids: list[int] = Field(default_factory=list)
    exclude_tag_ids: list[int] = Field(default_factory=list)
    tag_match_mode: str = "any"


class LocalStorageProfileCreateRequest(BaseModel):
    name: str
    handle: str | None = None
    description: str | None = None
    avatar_initials: str | None = None
    avatar_color: str | None = None
    banner_color: str | None = None
    auto_import_enabled: bool = False
    auto_import_sections: list[str] = Field(default_factory=lambda: ["edits", "ready", "published"])
    auto_import_prefix: str | None = None
    tag_match_mode: str = "any"


class LocalStorageProfileUpdateRequest(BaseModel):
    name: str | None = None
    handle: str | None = None
    description: str | None = None
    avatar_initials: str | None = None
    avatar_color: str | None = None
    banner_color: str | None = None
    auto_import_enabled: bool | None = None
    auto_import_sections: list[str] | None = None
    auto_import_prefix: str | None = None
    tag_match_mode: str | None = None
    enabled: bool | None = None


class LocalStorageProfileItemCreateRequest(BaseModel):
    workspace_path: str
    title: str | None = None
    description: str | None = None
    tags: str | None = None
    status: str = "draft"


class LocalStorageProfileYouTubeLinkRequest(BaseModel):
    account_id: int


class LocalStorageProfileYouTubePublishRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)
    account_id: int | None = None
    publish_mode: str | None = None
    category_id: str | None = None
    made_for_kids: bool | None = None
    title_template: str | None = None
    description_template: str | None = None
    tags_template: str | None = None


class LocalStorageProfilePublishSettingsRequest(BaseModel):
    publish_mode: str | None = None
    category_id: str | None = None
    made_for_kids: bool | None = None
    title_template: str | None = None
    description_template: str | None = None
    tags_template: str | None = None
    default_action: str | None = None


class LocalStorageProfileAutoImportRunRequest(BaseModel):
    force: bool = False


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


class YouTubeAccountUpdateRequest(BaseModel):
    display_name: str | None = None
    local_alias: str | None = None


class YouTubeUploadRequest(BaseModel):
    account_id: int
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: str = "22"
    publish_mode: str = "public"
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
    default_studio_template_id: int | None = None
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
    default_studio_template_id: int | None = None
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
    studio_template_id: int | None = None
    reaction_asset_id: int | None = None
    parameter_values: dict = Field(default_factory=dict)
    renderer_engine: str = "ffmpeg_fast"
    render_profile: str = "low_540p"
    duration_limit_sec: float | None = None
    start_offset_sec: float = 0
    full_length: bool = False
    force_new: bool = False


class EditJobRenderRequest(BaseModel):
    force: bool = False


class EditWorkerRunOnceRequest(BaseModel):
    limit: int = Field(default=1, gt=0)


class EditJobsBulkRenderRequest(BaseModel):
    job_ids: list[int] = Field(default_factory=list)
    force: bool = False


class EditJobReviewRequest(BaseModel):
    note: str | None = None
