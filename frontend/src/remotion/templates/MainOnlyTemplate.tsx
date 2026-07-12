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

export const MainOnlyTemplate: React.FC<ResolvedRecipe> = (recipe) => {
  const startFrame = Math.max(
    0,
    Math.round((recipe.trim?.start_sec || 0) * recipe.canvas.fps),
  );

  return (
    <AbsoluteFill style={{backgroundColor: recipe.layout.background_color, overflow: 'hidden'}}>
      <Html5Video
        src={recipe.media.main.url}
        startFrom={startFrame}
        volume={recipe.audio.main_volume}
        style={{width: '100%', height: '100%', objectFit: recipe.layout.main_fit}}
      />
      {recipe.overlays.top_text ? (
        <div style={{...overlayStyle, top: 38}}>{recipe.overlays.top_text}</div>
      ) : null}
      {recipe.overlays.bottom_text ? (
        <div style={{...overlayStyle, bottom: 38}}>{recipe.overlays.bottom_text}</div>
      ) : null}
    </AbsoluteFill>
  );
};
