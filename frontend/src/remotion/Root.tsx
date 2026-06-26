import React from 'react';
import {Composition, type CalculateMetadataFunction} from 'remotion';
import {ReactionLayoutTemplate} from './templates/ReactionLayoutTemplate';
import {ReactionTop25} from './templates/ReactionTop25';
import {createDefaultRecipe, type ResolvedRecipe} from '../studio/recipe';

const defaults = createDefaultRecipe();
const defaultProps: ResolvedRecipe = {
  ...defaults,
  media: {
    main: {...defaults.media.main, url: '', duration_sec: 1},
    reaction: {asset_id: null},
  },
  duration_in_frames: 30,
};

const calculateMetadata: CalculateMetadataFunction<ResolvedRecipe> = ({props}) => ({
  durationInFrames: Math.max(1, props.duration_in_frames),
  fps: props.canvas.fps,
  width: props.canvas.width,
  height: props.canvas.height,
  props,
});

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="ReactionLayoutTemplate"
      component={ReactionLayoutTemplate}
      durationInFrames={30}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={calculateMetadata}
    />
    <Composition
      id="ReactionTop25"
      component={ReactionTop25}
      durationInFrames={30}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={calculateMetadata}
    />
  </>
);
