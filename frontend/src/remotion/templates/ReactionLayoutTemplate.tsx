import React from 'react';
import {AbsoluteFill, Html5Video} from 'remotion';
import type {ResolvedRecipe} from '../../studio/recipe';

const overlayStyle: React.CSSProperties = {
  position: 'absolute',
  left: 54,
  right: 54,
  zIndex: 4,
  color: '#fff',
  fontFamily: 'Inter, Arial, sans-serif',
  fontSize: 64,
  fontWeight: 800,
  lineHeight: 1.08,
  textAlign: 'center',
  textShadow: '0 3px 12px rgba(0,0,0,.9)',
  background: 'rgba(0,0,0,.42)',
  borderRadius: 24,
  padding: '16px 24px',
};

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

const pipInsets = (
  position: string,
  margin: number,
): React.CSSProperties => {
  const vertical = position.startsWith('bottom') ? {bottom: margin} : {top: margin};
  const horizontal = position.endsWith('left') ? {left: margin} : {right: margin};
  return {...vertical, ...horizontal};
};

export const ReactionLayoutTemplate: React.FC<ResolvedRecipe> = (recipe) => {
  const {
    reaction_position = 'top',
    reaction_height,
    pip_position = 'top_right',
    main_fit,
    reaction_fit,
    background_color,
  } = recipe.layout;
  const hasReaction = Boolean(recipe.media.reaction.url) && reaction_position !== 'none';
  const height = clamp(Number(reaction_height || 480), 240, Math.min(960, recipe.canvas.height));
  const pipHeight = clamp(height, 240, 620);
  const pipWidth = Math.round(pipHeight * 9 / 16);
  const pipMargin = 42;
  const mainInset =
    hasReaction && reaction_position === 'top'
      ? `${height}px 0 0 0`
      : hasReaction && reaction_position === 'bottom'
        ? `0 0 ${height}px 0`
        : '0';

  return (
    <AbsoluteFill style={{backgroundColor: background_color, overflow: 'hidden'}}>
      <div style={{position: 'absolute', inset: mainInset}}>
        <Html5Video
          src={recipe.media.main.url}
          volume={recipe.audio.main_volume}
          style={{width: '100%', height: '100%', objectFit: main_fit}}
        />
      </div>

      {hasReaction && reaction_position === 'top' ? (
        <div style={{position: 'absolute', inset: '0 0 auto 0', height}}>
          <Html5Video
            src={recipe.media.reaction.url!}
            loop
            muted={recipe.audio.mute_reaction}
            volume={recipe.audio.reaction_volume}
            style={{width: '100%', height: '100%', objectFit: reaction_fit}}
          />
        </div>
      ) : null}

      {hasReaction && reaction_position === 'bottom' ? (
        <div style={{position: 'absolute', inset: 'auto 0 0 0', height}}>
          <Html5Video
            src={recipe.media.reaction.url!}
            loop
            muted={recipe.audio.mute_reaction}
            volume={recipe.audio.reaction_volume}
            style={{width: '100%', height: '100%', objectFit: reaction_fit}}
          />
        </div>
      ) : null}

      {hasReaction && reaction_position === 'pip' ? (
        <div
          style={{
            position: 'absolute',
            ...pipInsets(pip_position, pipMargin),
            width: pipWidth,
            height: pipHeight,
            zIndex: 3,
            overflow: 'hidden',
            borderRadius: 28,
            boxShadow: '0 18px 56px rgba(0,0,0,.55)',
            border: '3px solid rgba(255,255,255,.72)',
            background: '#000',
          }}
        >
          <Html5Video
            src={recipe.media.reaction.url!}
            loop
            muted={recipe.audio.mute_reaction}
            volume={recipe.audio.reaction_volume}
            style={{width: '100%', height: '100%', objectFit: reaction_fit}}
          />
        </div>
      ) : null}

      {recipe.overlays.top_text ? (
        <div style={{...overlayStyle, top: 38}}>{recipe.overlays.top_text}</div>
      ) : null}
      {recipe.overlays.bottom_text ? (
        <div style={{...overlayStyle, bottom: 38}}>{recipe.overlays.bottom_text}</div>
      ) : null}
    </AbsoluteFill>
  );
};
