import type {ComponentType} from 'react';
import type {ResolvedRecipe} from './recipe';
import {ReactionLayoutTemplate} from '../remotion/templates/ReactionLayoutTemplate';
import {MainOnlyTemplate} from '../remotion/templates/MainOnlyTemplate';

export const PREVIEW_COMPONENT_REGISTRY: Record<string, ComponentType<ResolvedRecipe>> = {
  ReactionLayoutTemplate,
  ReactionTop25: ReactionLayoutTemplate,
  MainOnlyTemplate,
};

export const previewComponentForComposition = (
  compositionId: string,
): ComponentType<ResolvedRecipe> | null =>
  PREVIEW_COMPONENT_REGISTRY[compositionId] || null;
