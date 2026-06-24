import {useMemo} from 'react';
import {Player} from '@remotion/player';
import {ReactionTop25} from '../remotion/templates/ReactionTop25';
import type {ResolvedRecipe} from './recipe';

export const RemotionPreview = ({recipe}: {recipe: ResolvedRecipe | null}) => {
  const inputProps = useMemo(() => recipe, [recipe]);
  if (!inputProps) {
    return <div className="preview-empty">Выберите main video и reaction</div>;
  }
  return (
    <Player
      component={ReactionTop25}
      inputProps={inputProps}
      durationInFrames={inputProps.duration_in_frames}
      compositionWidth={inputProps.canvas.width}
      compositionHeight={inputProps.canvas.height}
      fps={inputProps.canvas.fps}
      controls
      loop
      style={{width: '100%', maxHeight: 'calc(100vh - 150px)', aspectRatio: '9 / 16'}}
    />
  );
};
