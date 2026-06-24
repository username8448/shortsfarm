export type FitMode = 'cover' | 'contain';

export type Recipe = {
  version: 1;
  template: {key: 'reaction_top_25'; renderer: 'remotion'};
  canvas: {width: 1080; height: 1920; fps: 30};
  media: {
    main: {workspace_path: string};
    reaction: {asset_id: number | null};
  };
  layout: {
    reaction_height: number;
    main_fit: FitMode;
    reaction_fit: FitMode;
    background_color: string;
  };
  audio: {
    main_volume: number;
    reaction_volume: number;
    mute_reaction: boolean;
  };
  overlays: {top_text: string; bottom_text: string};
};

export type ResolvedRecipe = Recipe & {
  media: {
    main: {workspace_path: string; url: string; duration_sec: number};
    reaction: {asset_id: number; url: string; duration_sec?: number | null};
  };
  duration_in_frames: number;
};

export const createDefaultRecipe = (): Recipe => ({
  version: 1,
  template: {key: 'reaction_top_25', renderer: 'remotion'},
  canvas: {width: 1080, height: 1920, fps: 30},
  media: {
    main: {workspace_path: ''},
    reaction: {asset_id: null},
  },
  layout: {
    reaction_height: 480,
    main_fit: 'cover',
    reaction_fit: 'cover',
    background_color: '#000000',
  },
  audio: {
    main_volume: 1,
    reaction_volume: 0,
    mute_reaction: true,
  },
  overlays: {top_text: '', bottom_text: ''},
});

export const resolveDraftRecipe = (
  recipe: Recipe,
  mainUrl: string,
  mainDuration: number,
  reactionUrl: string,
  reactionDuration?: number | null,
): ResolvedRecipe => ({
  ...recipe,
  media: {
    main: {
      ...recipe.media.main,
      url: mainUrl,
      duration_sec: mainDuration,
    },
    reaction: {
      asset_id: recipe.media.reaction.asset_id as number,
      url: reactionUrl,
      duration_sec: reactionDuration,
    },
  },
  duration_in_frames: Math.max(1, Math.round(mainDuration * recipe.canvas.fps)),
});
