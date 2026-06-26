import {useMemo} from 'react';
import {Player} from '@remotion/player';
import {ReactionLayoutTemplate} from '../remotion/templates/ReactionLayoutTemplate';
import type {ResolvedRecipe} from './recipe';

export const RemotionPreview = ({recipe}: {recipe: ResolvedRecipe | null}) => {
  const inputProps = useMemo(() => recipe, [recipe]);
  if (!inputProps) {
    return <div className="preview-empty">Выберите основное тестовое видео</div>;
  }
  return (
    <div className="preview-frame">
      <Player
        component={ReactionLayoutTemplate}
        inputProps={inputProps}
        durationInFrames={inputProps.duration_in_frames}
        compositionWidth={inputProps.canvas.width}
        compositionHeight={inputProps.canvas.height}
        fps={inputProps.canvas.fps}
        controls
        loop
        style={{width: '100%', aspectRatio: '9 / 16'}}
      />
      {!inputProps.media.reaction.url
        ? <div className="slot-warning">Реакция не выбрана · предпросмотр только основного видео</div>
        : null}
    </div>
  );
};
