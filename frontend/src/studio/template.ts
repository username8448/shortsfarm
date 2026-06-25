import type {Recipe} from './recipe';

export type TemplateStatus = 'draft' | 'active' | 'archived';
export type TemplateEngine = 'remotion' | 'ffmpeg';

export type SlotDefinition = {
  type: 'video';
  required: boolean;
  allowed_sections?: string[];
  duration_policy?: string;
  source?: string;
  playback?: string;
};

export type ParameterDefinition = {
  group?: 'layout' | 'audio' | 'text' | 'automation';
  type: 'number' | 'select' | 'boolean' | 'text' | 'color';
  default: string | number | boolean;
  min?: number;
  max?: number;
  max_length?: number;
  values?: string[];
};

export type TemplateDefinition = {
  version: 1;
  key: string;
  name: string;
  engine: TemplateEngine;
  canvas: {width: number; height: number; fps: number};
  slots: Record<string, SlotDefinition>;
  parameters: Record<string, ParameterDefinition>;
  rules: Record<string, string>;
};

export type AutomationTemplate = {
  id: number;
  key: string;
  template_key: string;
  name: string;
  engine: TemplateEngine;
  version: number;
  status: TemplateStatus;
  definition: TemplateDefinition;
  created_at: string;
  updated_at: string | null;
};

export const recipeFromTemplate = (
  template: AutomationTemplate,
  current?: Recipe,
): Recipe => {
  const defaults = template.definition.parameters;
  const value = (key: string, fallback: string | number | boolean) =>
    defaults[key]?.default ?? fallback;
  return {
    version: 1,
    template: {key: 'reaction_top_25', renderer: 'remotion'},
    canvas: {
      width: 1080,
      height: 1920,
      fps: 30,
    },
    media: current?.media ?? {
      main: {workspace_path: ''},
      reaction: {asset_id: null},
    },
    layout: {
      reaction_height: Number(value('reaction_height', 480)),
      main_fit: String(value('main_fit', 'cover')) as 'cover' | 'contain',
      reaction_fit: String(value('reaction_fit', 'cover')) as 'cover' | 'contain',
      background_color: String(value('background_color', '#000000')),
    },
    audio: {
      main_volume: Number(value('main_volume', 1)),
      reaction_volume: Number(value('reaction_volume', 0)),
      mute_reaction: Boolean(value('mute_reaction', true)),
    },
    overlays: {
      top_text: String(value('top_text', '')),
      bottom_text: String(value('bottom_text', '')),
    },
  };
};

export const parameterValue = (
  recipe: Recipe,
  key: string,
): string | number | boolean => {
  const values: Record<string, string | number | boolean> = {
    reaction_height: recipe.layout.reaction_height,
    main_fit: recipe.layout.main_fit,
    reaction_fit: recipe.layout.reaction_fit,
    background_color: recipe.layout.background_color,
    main_volume: recipe.audio.main_volume,
    reaction_volume: recipe.audio.reaction_volume,
    mute_reaction: recipe.audio.mute_reaction,
    top_text: recipe.overlays.top_text,
    bottom_text: recipe.overlays.bottom_text,
  };
  return values[key] ?? '';
};

export const setRecipeParameter = (
  recipe: Recipe,
  key: string,
  value: string | number | boolean,
): Recipe => {
  if (key === 'reaction_height') {
    return {...recipe, layout: {...recipe.layout, reaction_height: Number(value)}};
  }
  if (key === 'main_fit' || key === 'reaction_fit') {
    return {
      ...recipe,
      layout: {...recipe.layout, [key]: String(value) as 'cover' | 'contain'},
    };
  }
  if (key === 'background_color') {
    return {...recipe, layout: {...recipe.layout, background_color: String(value)}};
  }
  if (key === 'main_volume' || key === 'reaction_volume') {
    return {...recipe, audio: {...recipe.audio, [key]: Number(value)}};
  }
  if (key === 'mute_reaction') {
    return {...recipe, audio: {...recipe.audio, mute_reaction: Boolean(value)}};
  }
  if (key === 'top_text' || key === 'bottom_text') {
    return {...recipe, overlays: {...recipe.overlays, [key]: String(value)}};
  }
  return recipe;
};
