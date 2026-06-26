import {useEffect, useMemo, useState} from 'react';
import {
  studioApi,
  type MediaItem,
  type MediaSection,
  type ApplySourceFolder,
  type ReactionItem,
  type ReactionPool,
  type RenderJob,
  type RenderBatch,
  type RemotionPipeline,
} from '../api';
import {ApplyTemplatePanel} from './ApplyTemplatePanel';
import {RemotionPreview} from './RemotionPreview';
import {RenderPanel} from './RenderPanel';
import {statusLabel} from './labels';
import {ParametersPanel} from './ParametersPanel';
import {RulesPanel} from './RulesPanel';
import {SlotsPanel} from './SlotsPanel';
import {TemplatesPage} from './TemplatesPage';
import {TestMediaPanel} from './TestMediaPanel';
import {
  createDefaultRecipe,
  resolveDraftRecipe,
  type Recipe,
} from './recipe';
import {
  rendererAdapter,
  recipeFromTemplate,
  type AutomationTemplate,
  type TemplateDefinition,
  type TemplateStatus,
} from './template';

type StudioMode = 'templates' | 'builder' | 'test' | 'apply';

const cloneDefinition = (value: TemplateDefinition): TemplateDefinition =>
  JSON.parse(JSON.stringify(value)) as TemplateDefinition;

export const StudioPage = ({embedded = false}: {embedded?: boolean}) => {
  const [mode, setMode] = useState<StudioMode>('templates');
  const [templates, setTemplates] = useState<AutomationTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<AutomationTemplate | null>(null);
  const [definition, setDefinition] = useState<TemplateDefinition | null>(null);
  const [sections, setSections] = useState<MediaSection[]>([]);
  const [folders, setFolders] = useState<ApplySourceFolder[]>([]);
  const [reactions, setReactions] = useState<ReactionItem[]>([]);
  const [pools, setPools] = useState<ReactionPool[]>([]);
  const [batches, setBatches] = useState<RenderBatch[]>([]);
  const [pipelines, setPipelines] = useState<RemotionPipeline[]>([]);
  const [activeBatch, setActiveBatch] = useState<RenderBatch | null>(null);
  const [recipe, setRecipe] = useState<Recipe>(createDefaultRecipe);
  const [reactionPoolId, setReactionPoolId] = useState<number | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const mediaItems = sections.flatMap((section) => section.items);
  const mainItem = mediaItems.find(
    (item) => item.workspace_path === recipe.media.main.workspace_path,
  );
  const reaction = reactions.find(
    (item) => item.id === recipe.media.reaction.asset_id,
  );
  const resolvedRecipe = useMemo(() => {
    if (!mainItem?.url || !mainItem.duration_sec) return null;
    return resolveDraftRecipe(
      recipe,
      mainItem.url,
      mainItem.duration_sec,
      reaction?.url,
      reaction?.duration_sec,
    );
  }, [recipe, mainItem, reaction]);

  const refreshTemplates = async () => {
    const data = await studioApi.templates();
    setTemplates(data.items);
    return data.items;
  };

  const refreshBatches = async () => {
    const data = await studioApi.renderBatches();
    setBatches(data.items);
    if (activeBatch) {
      const latest = data.items.find((item) => item.id === activeBatch.id);
      if (latest) setActiveBatch(latest);
    }
  };

  const refreshPipelines = async () => {
    const data = await studioApi.pipelines();
    setPipelines(data.items);
  };

  useEffect(() => {
    const load = async () => {
      try {
        const [media, applySources, reactionData, poolData, templateData, batchData, pipelineData] = await Promise.all([
          studioApi.mediaItems(),
          studioApi.applySources(),
          studioApi.reactions(),
          studioApi.reactionPools(),
          studioApi.templates(),
          studioApi.renderBatches(),
          studioApi.pipelines(),
        ]);
        setSections(media.sections);
        setFolders(applySources.folders);
        setReactions(reactionData.items);
        setPools(poolData.items);
        setTemplates(templateData.items);
        setBatches(batchData.items);
        setPipelines(pipelineData.items);

        const projectIdFromUrl = Number(
          new URLSearchParams(window.location.search).get('project'),
        );
        const batchIdFromUrl = Number(
          new URLSearchParams(window.location.search).get('batch'),
        );
        if (projectIdFromUrl > 0) {
          const project = await studioApi.project(projectIdFromUrl);
          const template = templateData.items.find(
            (item) => item.id === project.item.studio_template_id,
          ) || templateData.items[0];
          if (template) {
            setSelectedTemplate(template);
            setDefinition(cloneDefinition(template.definition));
          }
          setProjectId(project.item.id);
          setReactionPoolId(project.item.reaction_pool_id);
          setRecipe(project.item.recipe_json);
          setMode('test');
        } else if (batchIdFromUrl > 0) {
          const batch = await studioApi.renderBatch(batchIdFromUrl);
          setActiveBatch(batch.batch);
          const template = templateData.items.find(
            (item) => item.id === batch.batch.studio_template_id,
          ) || templateData.items.find(
            (item) => item.key === batch.batch.template_key,
          ) || templateData.items[0];
          if (template) {
            setSelectedTemplate(template);
            setDefinition(cloneDefinition(template.definition));
            setRecipe(recipeFromTemplate(template));
          }
          setMode('apply');
        } else if (templateData.items[0]) {
          const template = templateData.items[0];
          setSelectedTemplate(template);
          setDefinition(cloneDefinition(template.definition));
          setRecipe(recipeFromTemplate(template));
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
  }, []);

  useEffect(() => {
    const hasActiveBatch = batches.some((item) => ['queued', 'running'].includes(item.status));
    if (!hasActiveBatch) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [batches]);

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

  const openTemplate = (
    template: AutomationTemplate,
    targetMode: StudioMode = 'builder',
  ) => {
    setSelectedTemplate(template);
    setDefinition(cloneDefinition(template.definition));
    setRecipe((current) => recipeFromTemplate(template, current));
    setMode(targetMode);
    setMessage('');
    setError('');
  };

  const duplicateTemplate = async (template: AutomationTemplate) => {
    setBusy(true);
    setError('');
    try {
      const result = await studioApi.duplicateTemplate(template.id);
      await refreshTemplates();
      openTemplate(result.item);
      setMessage(`Создан черновик шаблона ${result.item.key}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const saveTemplate = async (newVersion = false) => {
    if (!selectedTemplate || !definition) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = newVersion
        ? await studioApi.createTemplateVersion(
          selectedTemplate.id,
          definition.name,
          'draft',
          definition,
        )
        : await studioApi.updateTemplate(
          selectedTemplate.id,
          definition.name,
          selectedTemplate.status,
          definition,
        );
      await refreshTemplates();
      setSelectedTemplate(result.item);
      setDefinition(cloneDefinition(result.item.definition));
      setMessage(
        newVersion
          ? `Сохранена новая версия v${result.item.version}.`
          : `Шаблон v${result.item.version} сохранён.`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const updateTemplateStatus = (status: TemplateStatus) => {
    if (!selectedTemplate) return;
    setSelectedTemplate({...selectedTemplate, status});
  };

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

  const selectPool = (id: number | null) => {
    setReactionPoolId(id);
    if (id) {
      const pool = pools.find((item) => item.id === id);
      selectReaction(pool?.items[0]?.asset_id ?? null);
    }
  };

  const saveTestProject = async () => {
    if (!selectedTemplate) throw new Error('Выберите шаблон автоматизации.');
    if (!mainItem) throw new Error('Выберите основное тестовое видео.');
    const response = projectId
      ? await studioApi.updateProject(
        projectId,
        recipe,
        selectedTemplate.id,
        reactionPoolId,
      )
      : await studioApi.createProject(
        recipe,
        selectedTemplate.id,
        reactionPoolId,
      );
    setProjectId(response.item.id);
    const url = new URL(window.location.href);
    url.searchParams.set('project', String(response.item.id));
    window.history.replaceState({}, '', url);
    return response.item.id;
  };

  const handleSaveProject = async () => {
    setBusy(true);
    setError('');
    try {
      await saveTestProject();
      setMessage('Тестовый контекст сохранён.');
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
      const id = await saveTestProject();
      const response = await studioApi.render(id);
      setJob(response.job);
      setMessage('Тестовый рендер добавлен в очередь.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const openReactions = () => {
    const host = window as typeof window & {
      nav?: (id: string, element: Element | null) => void;
    };
    host.nav?.('editing', document.querySelector('[data-v="editing"]'));
  };

  const openMainPanelUrl = useMemo(() => {
    const url = new URL('/', window.location.origin);
    for (const [key, value] of new URLSearchParams(window.location.search)) {
      url.searchParams.set(key, value);
    }
    return url.pathname + url.search;
  }, []);

  const handleBatchCreated = (batch: RenderBatch) => {
    setActiveBatch(batch);
    setBatches((current) => [batch, ...current.filter((item) => item.id !== batch.id)]);
    const url = new URL(window.location.href);
    url.searchParams.set('batch', String(batch.id));
    url.searchParams.delete('project');
    window.history.replaceState({}, '', url);
  };

  const mainAllowedSections = (
    definition?.slots.main?.allowed_sections || ['sources', 'cuts', 'prepared']
  );
  const missingMain = Boolean(definition?.slots.main?.required) && !mainItem;
  const missingReaction = Boolean(definition?.slots.reaction?.required) && !reaction?.url;
  const renderDisabled = missingMain || missingReaction;
  const disabledReason = [
    missingMain ? 'Основное тестовое видео не выбрано.' : '',
    missingReaction ? 'Реакция не выбрана.' : '',
  ].filter(Boolean).join(' ');

  const testMedia = definition ? (
    <TestMediaPanel
      sections={sections}
      selectedMain={recipe.media.main.workspace_path}
      allowedSections={mainAllowedSections}
      reactions={reactions}
      pools={pools}
      reactionAssetId={recipe.media.reaction.asset_id}
      reactionPoolId={reactionPoolId}
      onMain={selectMedia}
      onReaction={selectReaction}
      onPool={selectPool}
      onOpenReactions={openReactions}
    />
  ) : null;

  return (
    <div className={`template-studio ${embedded ? 'embedded' : 'standalone'}`}>
      <div className="ts-topbar">
        <div>
          <h1>Template Studio</h1>
          <p>Конструктор автоматизированных шаблонов · тестовые медиа отделены от определения шаблона</p>
        </div>
        {!embedded ? <a href={openMainPanelUrl}>Открыть в основной панели</a> : null}
      </div>
      <nav className="ts-tabs">
        <button className={mode === 'templates' ? 'active' : ''} onClick={() => setMode('templates')}>Шаблоны</button>
        <button className={mode === 'builder' ? 'active' : ''} disabled={!selectedTemplate} onClick={() => setMode('builder')}>Конструктор шаблона</button>
        <button className={mode === 'test' ? 'active' : ''} disabled={!selectedTemplate} onClick={() => setMode('test')}>Тестовый рендер</button>
        <button className={mode === 'apply' ? 'active' : ''} disabled={!selectedTemplate} onClick={() => setMode('apply')}>Apply Template</button>
      </nav>
      {error ? <div className="ts-alert error">{error}</div> : null}
      {message ? <div className="ts-alert success">{message}</div> : null}

      {mode === 'templates' ? (
        <TemplatesPage
          templates={templates}
          onOpen={(item) => openTemplate(item, 'builder')}
          onDuplicate={duplicateTemplate}
          onTest={(item) => openTemplate(item, 'test')}
        />
      ) : null}

      {mode === 'builder' && selectedTemplate && definition ? (
        <div className="builder-grid">
          <div className="builder-left">
            <section className="ts-card template-info">
              <div className="ts-card-head"><h2>Информация о шаблоне</h2></div>
              <div className="adapter-note">
                Этот template использует Remotion renderer adapter:
                {' '}
                <b>{rendererAdapter(definition)?.displayName || definition.rules.renderer_adapter || 'не найден'}</b>.
                {' '}
                Можно менять параметры, defaults и правила. Для полностью нового
                визуального renderer нужно добавить adapter.
              </div>
              <label><span>Ключ шаблона</span><input value={selectedTemplate.key} disabled /></label>
              <label><span>Название</span><input value={definition.name} onChange={(event) => setDefinition({...definition, name: event.target.value})} /></label>
              <div className="info-row">
                <label><span>Движок</span><input value={selectedTemplate.engine} disabled /></label>
                <label><span>Версия</span><input value={`v${selectedTemplate.version}`} disabled /></label>
              </div>
              <label>
                <span>Статус</span>
                <select value={selectedTemplate.status} onChange={(event) => updateTemplateStatus(event.target.value as TemplateStatus)}>
                  <option value="draft">{statusLabel('draft')}</option>
                  <option value="active">{statusLabel('active')}</option>
                  <option value="archived">{statusLabel('archived')}</option>
                </select>
              </label>
              <div className="ts-row-actions">
                <button className="primary" disabled={busy} onClick={() => void saveTemplate(false)}>Сохранить шаблон</button>
                <button disabled={busy} onClick={() => void saveTemplate(true)}>Сохранить новую версию</button>
              </div>
            </section>
            <SlotsPanel definition={definition} onChange={setDefinition} />
            <RulesPanel definition={definition} />
          </div>
          <div className="builder-center">
            <section className="ts-card preview-card">
              <div className="ts-card-head"><h2>Предпросмотр</h2><span className="ts-badge">9:16</span></div>
              <RemotionPreview recipe={resolvedRecipe} />
            </section>
            {testMedia}
          </div>
          <div className="builder-right">
            <ParametersPanel
              definition={definition}
              recipe={recipe}
              onDefinitionChange={setDefinition}
              onRecipeChange={setRecipe}
            />
            <section className="ts-card">
              <div className="ts-card-head"><h2>Тестовый рендер</h2></div>
              <RenderPanel
                projectId={projectId}
                job={job}
                busy={busy}
                renderDisabled={renderDisabled}
                disabledReason={disabledReason}
                onSave={handleSaveProject}
                onRender={handleRender}
              />
            </section>
          </div>
        </div>
      ) : null}

      {mode === 'test' && selectedTemplate && definition ? (
        <div className="test-render-grid">
          <div>{testMedia}</div>
          <section className="ts-card preview-card">
            <div className="ts-card-head">
              <div><h2>Тестовый предпросмотр</h2><p>{selectedTemplate.key} · v{selectedTemplate.version}</p></div>
              <span className="ts-badge engine">{selectedTemplate.engine}</span>
            </div>
            <RemotionPreview recipe={resolvedRecipe} />
          </section>
          <div>
            <ParametersPanel
              definition={definition}
              recipe={recipe}
              onDefinitionChange={setDefinition}
              onRecipeChange={setRecipe}
            />
            <section className="ts-card">
              <div className="ts-card-head"><h2>Тестовый рендер</h2></div>
              <RenderPanel
                projectId={projectId}
                job={job}
                busy={busy}
                renderDisabled={renderDisabled}
                disabledReason={disabledReason}
                onSave={handleSaveProject}
                onRender={handleRender}
              />
            </section>
          </div>
        </div>
      ) : null}

      {mode === 'apply' && selectedTemplate && definition ? (
        <ApplyTemplatePanel
          template={selectedTemplate}
          sections={sections}
          folders={folders}
          reactions={reactions}
          pools={pools}
          recipe={recipe}
          onRecipeChange={setRecipe}
          batches={activeBatch ? [activeBatch, ...batches.filter((item) => item.id !== activeBatch.id)] : batches}
          pipelines={pipelines}
          onBatchCreated={handleBatchCreated}
          onRefreshBatches={refreshBatches}
          onRefreshPipelines={refreshPipelines}
        />
      ) : null}
    </div>
  );
};
