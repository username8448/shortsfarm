import type {Recipe, ResolvedRecipe} from './studio/recipe';

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

export type StudioProject = {
  id: number;
  workspace_item_key: string | null;
  main_workspace_path: string;
  template_key: string;
  reaction_asset_id: number | null;
  recipe_json: Recipe;
  resolved_recipe_json: ResolvedRecipe;
  created_at: string;
  updated_at: string | null;
};

export type RenderJob = {
  id: number;
  studio_project_id: number;
  status: 'queued' | 'rendering' | 'done' | 'failed';
  output_path: string | null;
  error: string | null;
  media_url?: string | null;
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
  reactions: () => request<{items: ReactionItem[]}>('/api/studio/reactions'),
  templates: () => request<{items: Array<{key: string; name: string; recipe_defaults: Recipe}>}>('/api/studio/templates'),
  project: (id: number) => request<{item: StudioProject}>(`/api/studio/projects/${id}`),
  createProject: (recipe: Recipe) => request<{item: StudioProject}>('/api/studio/projects', {
    method: 'POST',
    body: JSON.stringify({recipe_json: recipe}),
  }),
  updateProject: (id: number, recipe: Recipe) => request<{item: StudioProject}>(`/api/studio/projects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({recipe_json: recipe}),
  }),
  render: (id: number) => request<{job: RenderJob}>(`/api/studio/projects/${id}/render`, {
    method: 'POST',
    body: '{}',
  }),
  renderJob: (id: number) => request<{job: RenderJob}>(`/api/studio/render-jobs/${id}`),
};
