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

export const ReactionTop25: React.FC<ResolvedRecipe> = (recipe) => {
  const {reaction_height, main_fit, reaction_fit, background_color} = recipe.layout;
  const hasReaction = Boolean(recipe.media.reaction.url);
  return (
    <AbsoluteFill style={{backgroundColor: background_color, overflow: 'hidden'}}>
      {hasReaction ? <div style={{position: 'absolute', inset: `0 0 auto 0`, height: reaction_height}}>
        <Html5Video
          src={recipe.media.reaction.url!}
          loop
          muted={recipe.audio.mute_reaction}
          volume={recipe.audio.reaction_volume}
          style={{width: '100%', height: '100%', objectFit: reaction_fit}}
        />
      </div> : null}
      <div style={{position: 'absolute', inset: `${hasReaction ? reaction_height : 0}px 0 0 0`}}>
        <Html5Video
          src={recipe.media.main.url}
          volume={recipe.audio.main_volume}
          style={{width: '100%', height: '100%', objectFit: main_fit}}
        />
      </div>
      {recipe.overlays.top_text ? (
        <div style={{...overlayStyle, top: 38}}>{recipe.overlays.top_text}</div>
      ) : null}
      {recipe.overlays.bottom_text ? (
        <div style={{...overlayStyle, bottom: 38}}>{recipe.overlays.bottom_text}</div>
      ) : null}
    </AbsoluteFill>
  );
};
