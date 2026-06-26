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
  error: string | null;
  media_url?: string | null;
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
  render_status?: RenderJob['status'];
  render_error?: string | null;
  media_url?: string | null;
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
  status: 'draft' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  total_items: number;
  done_items: number;
  failed_items: number;
  error: string | null;
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
  templates: () => request<{items: AutomationTemplate[]}>('/api/studio/templates'),
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
  render: (id: number) => request<{job: RenderJob}>(`/api/studio/projects/${id}/render`, {
    method: 'POST',
    body: '{}',
  }),
  renderJob: (id: number) => request<{job: RenderJob}>(`/api/studio/render-jobs/${id}`),
  applyTemplate: (
    id: number,
    body: Record<string, unknown>,
  ) => request<{batch: RenderBatch; jobs: RenderJob[]}>(`/api/studio/templates/${id}/apply`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  renderBatches: () => request<{items: RenderBatch[]}>('/api/studio/render-batches'),
  renderBatch: (id: number) => request<{batch: RenderBatch}>(`/api/studio/render-batches/${id}`),
  startBatch: (id: number) => request<{batch: RenderBatch}>(`/api/studio/render-batches/${id}/start`, {
    method: 'POST',
    body: '{}',
  }),
  cancelBatch: (id: number) => request<{batch: RenderBatch; cancelled: number}>(`/api/studio/render-batches/${id}/cancel`, {
    method: 'POST',
    body: '{}',
  }),
  pipelines: () => request<{items: RemotionPipeline[]}>('/api/studio/pipelines'),
  createPipeline: (body: Record<string, unknown>) => request<{item: RemotionPipeline}>('/api/studio/pipelines', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  runPipeline: (id: number) => request<{batch: RenderBatch; jobs: RenderJob[]}>(`/api/studio/pipelines/${id}/run`, {
    method: 'POST',
    body: '{}',
  }),
};
