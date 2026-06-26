import type React from 'react';
import type {ResolvedRecipe} from '../../studio/recipe';
import {ReactionLayoutTemplate} from './ReactionLayoutTemplate';

export const ReactionTop25: React.FC<ResolvedRecipe> = (recipe) => (
  <ReactionLayoutTemplate {...recipe} />
);
