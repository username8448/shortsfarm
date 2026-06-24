import {useEffect, useMemo, useState} from 'react';
import {
  studioApi,
  type MediaItem,
  type MediaSection,
  type ReactionItem,
  type RenderJob,
} from '../api';
import {MediaPicker} from './MediaPicker';
import {ReactionPicker} from './ReactionPicker';
import {RemotionPreview} from './RemotionPreview';
import {RenderPanel} from './RenderPanel';
import {StudioLayout} from './StudioLayout';
import {TemplateControls} from './TemplateControls';
import {
  createDefaultRecipe,
  resolveDraftRecipe,
  type Recipe,
} from './recipe';

export const StudioPage = () => {
  const [sections, setSections] = useState<MediaSection[]>([]);
  const [reactions, setReactions] = useState<ReactionItem[]>([]);
  const [recipe, setRecipe] = useState<Recipe>(createDefaultRecipe);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const mediaItems = sections.flatMap((section) => section.items);
  const mainItem = mediaItems.find(
    (item) => item.workspace_path === recipe.media.main.workspace_path,
  );
  const reaction = reactions.find(
    (item) => item.id === recipe.media.reaction.asset_id,
  );
  const resolvedRecipe = useMemo(() => {
    if (!mainItem?.url || !mainItem.duration_sec || !reaction?.url) return null;
    return resolveDraftRecipe(
      recipe,
      mainItem.url,
      mainItem.duration_sec,
      reaction.url,
      reaction.duration_sec,
    );
  }, [recipe, mainItem, reaction]);

  useEffect(() => {
    const load = async () => {
      try {
        const [media, reactionData, templates] = await Promise.all([
          studioApi.mediaItems(),
          studioApi.reactions(),
          studioApi.templates(),
        ]);
        setSections(media.sections);
        setReactions(reactionData.items);
        const id = Number(new URLSearchParams(window.location.search).get('project'));
        if (id > 0) {
          const project = await studioApi.project(id);
          setProjectId(project.item.id);
          setRecipe(project.item.recipe_json);
        } else if (templates.items[0]) {
          setRecipe(templates.items[0].recipe_defaults);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
  }, []);

  useEffect(() => {
    if (!job || !['queued', 'rendering'].includes(job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await studioApi.renderJob(job.id);
        setJob(data.job);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        window.clearInterval(timer);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  const selectMedia = (item: MediaItem) => {
    setRecipe((current) => ({
      ...current,
      media: {...current.media, main: {workspace_path: item.workspace_path}},
    }));
  };

  const selectReaction = (id: number | null) => {
    setRecipe((current) => ({
      ...current,
      media: {...current.media, reaction: {asset_id: id}},
    }));
  };

  const save = async () => {
    if (!resolvedRecipe) throw new Error('Выберите доступные main video и reaction.');
    const response = projectId
      ? await studioApi.updateProject(projectId, recipe)
      : await studioApi.createProject(recipe);
    setProjectId(response.item.id);
    const url = new URL(window.location.href);
    url.searchParams.set('project', String(response.item.id));
    window.history.replaceState({}, '', url);
    return response.item.id;
  };

  const handleSave = async () => {
    setBusy(true);
    setError('');
    try {
      await save();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleRender = async () => {
    setBusy(true);
    setError('');
    try {
      const id = await save();
      const response = await studioApi.render(id);
      setJob(response.job);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="studio-shell">
      <header>
        <div>
          <h1>ShortsFarm Studio</h1>
          <p>Remotion · reaction_top_25</p>
        </div>
        <a href="/">Legacy UI</a>
      </header>
      {error ? <div className="global-error">{error}</div> : null}
      <StudioLayout
        media={<MediaPicker sections={sections} selected={recipe.media.main.workspace_path} onSelect={selectMedia} />}
        preview={<RemotionPreview recipe={resolvedRecipe} />}
        controls={(
          <>
            <h2>Controls</h2>
            <ReactionPicker reactions={reactions} value={recipe.media.reaction.asset_id} onChange={selectReaction} />
            <TemplateControls recipe={recipe} onChange={setRecipe} />
            <RenderPanel
              projectId={projectId}
              job={job}
              busy={busy}
              onSave={handleSave}
              onRender={handleRender}
            />
          </>
        )}
      />
    </div>
  );
};
