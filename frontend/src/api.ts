import type {Recipe, ResolvedRecipe} from './studio/recipe';
import type {
  AutomationTemplate,
  TemplateDefinition,
  TemplateStatus,
} from './studio/template';

export type MediaItem = {
  name: string;
  workspace_path: string;
  kind: 'source' | 'cut' | 'prepared' | 'edited';
  size: number;
  modified_at: string;
  duration_sec: number | null;
  url: string;
};

export type MediaSection = {
  key: string;
  label: string;
  kind: MediaItem['kind'];
  items: MediaItem[];
};

export type ReactionItem = {
  id: number;
  name: string;
  duration_sec: number | null;
  tags?: string | null;
  available: boolean;
  unavailable_reason?: string;
  url?: string;
};

export type ReactionPool = {
  id: number;
  name: string;
  description?: string | null;
  items: Array<{asset_id: number; name: string; weight: number}>;
};

export type StudioProject = {
  id: number;
  workspace_item_key: string | null;
  main_workspace_path: string;
  template_key: string;
  reaction_asset_id: number | null;
  reaction_pool_id: number | null;
  studio_template_id: number | null;
  recipe_json: Recipe;
  resolved_recipe_json: ResolvedRecipe;
  created_at: string;
  updated_at: string | null;
};

export type RenderJob = {
  id: number;
  studio_project_id: number;
  status: 'queued' | 'rendering' | 'done' | 'failed' | 'cancelled';
  output_path: string | null;
  output_workspace_path?: string | null;
  error: string | null;
  renderer_engine: 'ffmpeg_fast' | 'remotion';
  render_profile: string;
  duration_limit_sec: number | null;
  start_offset_sec: number;
  full_length: boolean;
  worker_pid?: number | null;
  progress_percent: number;
  progress_stage?: string | null;
  progress_message?: string | null;
  current_frame?: number | null;
  total_frames?: number | null;
  out_time_sec?: number | null;
  speed?: string | null;
  eta_sec?: number | null;
  output_size_bytes?: number | null;
  completed_message?: string | null;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
  returncode?: number | null;
  elapsed_sec?: number | null;
  media_url?: string | null;
};

export type CompletedRenderJob = RenderJob & {
  template_key: string;
  main_workspace_path: string;
  studio_template_id: number | null;
};

export type RenderQueueStatus = {
  status: 'idle' | 'running' | 'stale';
  current_job_id: number | null;
  worker_pid: number | null;
  alive: boolean;
  queued_count: number;
  rendering_count: number;
  failed_count: number;
  last_error: string | null;
};

export type RenderQueueStart = {
  started: boolean;
  reason: string;
  current_job_id?: number | null;
};

export type RenderProfile = {
  key: string;
  label: string;
  width: number;
  height: number;
  fps: number;
  crf: number;
  preset: string;
  max_duration_sec: number;
  timeout_sec: number;
};

export type MediaMetadata = {
  path: string;
  filename: string;
  size_bytes: number;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  has_audio: boolean;
  container: string | null;
};

export type WorkspaceVideoItem = {
  path: string;
  filename: string;
  size_bytes: number;
};

export type WorkspaceVideoSection = {
  key: string;
  items: WorkspaceVideoItem[];
};

export type VideoSegment = {
  id: number;
  source_path: string;
  label: string | null;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  status: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type ApplySourceFolder = {
  path: string;
  name: string;
};

export type RenderBatchItem = {
  id: number;
  batch_id: number;
  studio_project_id: number;
  render_job_id: number;
  main_workspace_path: string;
  status: RenderJob['status'];
  error: string | null;
  output_path?: string | null;
  output_workspace_path?: string | null;
  render_status?: RenderJob['status'];
  render_error?: string | null;
  renderer_engine?: RenderJob['renderer_engine'];
  render_profile?: string;
  duration_limit_sec?: number | null;
  start_offset_sec?: number;
  full_length?: boolean;
  progress_percent?: number;
  progress_stage?: string | null;
  progress_message?: string | null;
  current_frame?: number | null;
  total_frames?: number | null;
  out_time_sec?: number | null;
  speed?: string | null;
  eta_sec?: number | null;
  output_size_bytes?: number | null;
  completed_message?: string | null;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
  returncode?: number | null;
  elapsed_sec?: number | null;
  media_url?: string | null;
};

export type RenderBatchProgress = {
  percent: number;
  queued: number;
  rendering: number;
  done: number;
  failed: number;
  cancelled?: number;
  total: number;
  current_job_id: number | null;
  message: string;
};

export type RenderBatch = {
  id: number;
  studio_template_id: number | null;
  template_key: string;
  name: string;
  source_mode: 'selected' | 'folder' | 'folder_recursive' | 'pipeline';
  source_path: string | null;
  reaction_strategy: 'fixed_asset' | 'pool_first' | 'pool_weighted';
  reaction_asset_id: number | null;
  reaction_pool_id: number | null;
  parameter_values: Record<string, unknown>;
  renderer_engine: RenderJob['renderer_engine'];
  render_profile: string;
  duration_limit_sec: number | null;
  start_offset_sec: number;
  full_length: boolean;
  status: 'draft' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  total_items: number;
  done_items: number;
  failed_items: number;
  error: string | null;
  progress?: RenderBatchProgress;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  items?: RenderBatchItem[];
};

export type RemotionPipeline = {
  id: number;
  name: string;
  studio_template_id: number;
  source_mode: 'selected' | 'folder' | 'folder_recursive';
  source_path: string | null;
  source_paths: string[];
  recursive: boolean;
  reaction_strategy: 'fixed_asset' | 'pool_first' | 'pool_weighted';
  reaction_asset_id: number | null;
  reaction_pool_id: number | null;
  parameter_values: Record<string, unknown>;
  renderer_engine: RenderJob['renderer_engine'];
  render_profile: string;
  duration_limit_sec: number | null;
  start_offset_sec: number;
  full_length: boolean;
  output_policy: Record<string, unknown>;
  enabled: boolean;
  last_batch_id: number | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body?.detail?.message || body?.message || message;
    } catch {
      // Keep the HTTP fallback.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const studioApi = {
  mediaItems: () => request<{sections: MediaSection[]}>('/api/studio/media-items'),
  applySources: () => request<{sections: MediaSection[]; folders: ApplySourceFolder[]}>('/api/studio/apply/sources'),
  reactions: () => request<{items: ReactionItem[]}>('/api/studio/reactions'),
  reactionPools: () => request<{items: ReactionPool[]}>('/api/studio/reaction-pools'),
  renderProfiles: () => request<{
    default_engine: RenderJob['renderer_engine'];
    default_profile: string;
    engines: string[];
    profiles: RenderProfile[];
  }>('/api/studio/render-profiles'),
  templates: (includeDeleted = false) => request<{items: AutomationTemplate[]}>(`/api/studio/templates${includeDeleted ? '?include_deleted=true' : ''}`),
  template: (id: number) => request<{item: AutomationTemplate}>(`/api/studio/templates/${id}`),
  updateTemplate: (
    id: number,
    name: string,
    status: TemplateStatus,
    definition: TemplateDefinition,
  ) => request<{item: AutomationTemplate}>(`/api/studio/templates/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({name, status, definition}),
  }),
  duplicateTemplate: (id: number) => request<{item: AutomationTemplate}>(`/api/studio/templates/${id}/duplicate`, {
    method: 'POST',
    body: '{}',
  }),
  createTemplateVersion: (
    id: number,
    name: string,
    status: TemplateStatus,
    definition: TemplateDefinition,
  ) => request<{item: AutomationTemplate}>(`/api/studio/templates/${id}/versions`, {
    method: 'POST',
    body: JSON.stringify({name, status, definition}),
  }),
  deleteTemplate: (id: number) => request<{status: string; action: string; item: AutomationTemplate | null}>(`/api/studio/templates/${id}`, {
    method: 'DELETE',
  }),
  restoreTemplate: (id: number) => request<{status: string; item: AutomationTemplate}>(`/api/studio/templates/${id}/restore`, {
    method: 'POST',
    body: '{}',
  }),
  project: (id: number) => request<{item: StudioProject}>(`/api/studio/projects/${id}`),
  createProject: (
    recipe: Recipe,
    templateId: number,
    reactionPoolId: number | null,
  ) => request<{item: StudioProject}>('/api/studio/projects', {
    method: 'POST',
    body: JSON.stringify({
      recipe_json: recipe,
      studio_template_id: templateId,
      reaction_pool_id: reactionPoolId,
    }),
  }),
  updateProject: (
    id: number,
    recipe: Recipe,
    templateId: number,
    reactionPoolId: number | null,
  ) => request<{item: StudioProject}>(`/api/studio/projects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({
      recipe_json: recipe,
      studio_template_id: templateId,
      reaction_pool_id: reactionPoolId,
    }),
  }),
  render: (id: number) => request<{job: RenderJob; queue?: RenderQueueStart}>(`/api/studio/projects/${id}/render`, {
    method: 'POST',
    body: '{}',
  }),
  renderJob: (id: number) => request<{job: RenderJob}>(`/api/studio/render-jobs/${id}`),
  completedRenderJobs: (limit = 5) => request<{items: CompletedRenderJob[]}>(`/api/studio/render-jobs/completed?limit=${limit}`),
  retryRenderJob: (id: number) => request<{job: RenderJob; retried: boolean; queue?: RenderQueueStart}>(`/api/studio/render-jobs/${id}/retry`, {
    method: 'POST',
    body: '{}',
  }),
  applyTemplate: (
    id: number,
    body: Record<string, unknown>,
  ) => request<{batch: RenderBatch; jobs: RenderJob[]; queue?: RenderQueueStart | null}>(`/api/studio/templates/${id}/apply`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  renderBatches: () => request<{items: RenderBatch[]}>('/api/studio/render-batches'),
  renderBatch: (id: number) => request<{batch: RenderBatch}>(`/api/studio/render-batches/${id}`),
  startBatch: (id: number) => request<{batch: RenderBatch; queue: RenderQueueStart}>(`/api/studio/render-batches/${id}/start`, {
    method: 'POST',
    body: '{}',
  }),
  retryFailedBatch: (id: number) => request<{batch: RenderBatch; retried: number; queue?: RenderQueueStart | null}>(`/api/studio/render-batches/${id}/retry-failed`, {
    method: 'POST',
    body: '{}',
  }),
  cancelBatch: (id: number) => request<{batch: RenderBatch; cancelled: number}>(`/api/studio/render-batches/${id}/cancel`, {
    method: 'POST',
    body: '{}',
  }),
  queueStatus: () => request<{queue: RenderQueueStatus}>('/api/studio/render-queue/status'),
  recoverQueue: () => request<{recovered: number; reason: string; queue: RenderQueueStatus}>('/api/studio/render-queue/recover', {
    method: 'POST',
    body: '{}',
  }),
  pipelines: () => request<{items: RemotionPipeline[]}>('/api/studio/pipelines'),
  createPipeline: (body: Record<string, unknown>) => request<{item: RemotionPipeline}>('/api/studio/pipelines', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  runPipeline: (id: number) => request<{batch: RenderBatch; jobs: RenderJob[]; queue?: RenderQueueStart | null}>(`/api/studio/pipelines/${id}/run`, {
    method: 'POST',
    body: '{}',
  }),
};

export const mediaApi = {
  videoUrl: (path: string) => `/api/media/video?path=${encodeURIComponent(path)}`,
  metadata: (path: string) => request<MediaMetadata>(`/api/media/metadata?path=${encodeURIComponent(path)}`),
  videos: () => request<{sections: WorkspaceVideoSection[]}>('/api/media/videos'),
  segments: (path: string) => request<{items: VideoSegment[]}>(`/api/media/segments?path=${encodeURIComponent(path)}`),
  createSegment: (body: {
    source_path: string;
    label?: string | null;
    start_sec: number;
    end_sec: number;
    notes?: string | null;
  }) => request<{item: VideoSegment}>('/api/media/segments', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateSegment: (
    id: number,
    body: Partial<Pick<VideoSegment, 'label' | 'start_sec' | 'end_sec' | 'status' | 'notes'>>,
  ) => request<{item: VideoSegment}>(`/api/media/segments/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  deleteSegment: (id: number) => request<{deleted: boolean}>(`/api/media/segments/${id}`, {
    method: 'DELETE',
  }),
};
