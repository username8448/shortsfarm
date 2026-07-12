import type {Recipe, StudioRenderer} from './recipe';

export type TemplateStatus = 'draft' | 'active' | 'archived';
export type TemplateEngine = 'remotion' | 'ffmpeg' | 'ffmpeg_fast';

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
  schema_version?: 2;
  version?: 1;
  key: string;
  name: string;
  engine?: TemplateEngine;
  adapter?: string;
  supported_renderers?: StudioRenderer[];
  default_renderer?: StudioRenderer;
  canvas: {width: number; height: number; fps: number};
  slots: Record<string, SlotDefinition>;
  parameters: Record<string, ParameterDefinition>;
  rules: Record<string, string>;
};

export type TemplateRendererAdapter = {
  key: string;
  compositionId: string;
  displayName: string;
  supportedRenderers: StudioRenderer[];
};

export const TEMPLATE_RENDERER_REGISTRY: Record<string, TemplateRendererAdapter> = {
  reaction_layout: {
    key: 'reaction_layout',
    compositionId: 'ReactionLayoutTemplate',
    displayName: 'Reaction Layout Template',
    supportedRenderers: ['ffmpeg_fast', 'remotion'],
  },
  main_only: {
    key: 'main_only',
    compositionId: 'MainOnlyTemplate',
    displayName: 'Main Only Template',
    supportedRenderers: ['ffmpeg_fast', 'remotion'],
  },
};

export const rendererAdapterKey = (definition: TemplateDefinition): string =>
  String(definition.adapter || definition.rules.renderer_adapter || 'reaction_layout');

export const rendererAdapter = (
  definition: TemplateDefinition,
): TemplateRendererAdapter | null =>
  TEMPLATE_RENDERER_REGISTRY[rendererAdapterKey(definition)] || null;

export const hasRendererAdapter = (definition: TemplateDefinition): boolean =>
  Boolean(rendererAdapter(definition));

export type AutomationTemplate = {
  id: number;
  key: string;
  template_key: string;
  name: string;
  engine: TemplateEngine;
  version: number;
  status: TemplateStatus;
  deleted_at?: string | null;
  definition: TemplateDefinition;
  created_at: string;
  updated_at: string | null;
};

export const recipeFromTemplate = (
  template: AutomationTemplate,
  current?: Recipe,
): Recipe => {
  const defaults = template.definition.parameters;
  const adapter = rendererAdapter(template.definition);
  const renderer = template.definition.default_renderer || 'ffmpeg_fast';
  const value = (key: string, fallback: string | number | boolean) =>
    defaults[key]?.default ?? fallback;
  const parameters: Record<string, string | number | boolean> = {};
  Object.keys(defaults).forEach((key) => {
    const parameter = defaults[key];
    if (parameter) parameters[key] = parameter.default;
  });
  return {
    version: 1,
    template: {
      key: template.key,
      renderer,
      studio_template_id: template.id,
      template_version: template.version,
      definition_schema_version: template.definition.schema_version || 2,
      adapter: adapter?.key || rendererAdapterKey(template.definition),
      renderer_adapter: adapter?.key || rendererAdapterKey(template.definition),
      composition_id: String(
        template.definition.rules.composition_id || adapter?.compositionId || 'ReactionLayoutTemplate',
      ),
    },
    parameters,
    canvas: {
      width: template.definition.canvas.width,
      height: template.definition.canvas.height,
      fps: template.definition.canvas.fps,
    },
    media: current?.media ?? {
      main: {workspace_path: ''},
      reaction: {asset_id: null},
    },
    layout: {
      reaction_position: String(value('reaction_position', 'top')) as Recipe['layout']['reaction_position'],
      reaction_height: Number(value('reaction_height', 480)),
      pip_position: String(value('pip_position', 'top_right')) as Recipe['layout']['pip_position'],
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
    reaction_position: recipe.layout.reaction_position,
    reaction_height: recipe.layout.reaction_height,
    pip_position: recipe.layout.pip_position,
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
  if (key === 'reaction_position') {
    return {
      ...recipe,
      layout: {
        ...recipe.layout,
        reaction_position: String(value) as Recipe['layout']['reaction_position'],
      },
    };
  }
  if (key === 'pip_position') {
    return {
      ...recipe,
      layout: {
        ...recipe.layout,
        pip_position: String(value) as Recipe['layout']['pip_position'],
      },
    };
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
