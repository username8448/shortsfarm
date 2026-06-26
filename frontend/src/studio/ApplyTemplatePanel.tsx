import {useMemo, useState} from 'react';
import type {
  ApplySourceFolder,
  MediaItem,
  MediaSection,
  ReactionItem,
  ReactionPool,
  RemotionPipeline,
  RenderBatch,
} from '../api';
import {studioApi} from '../api';
import {
  fitLabel,
  folderSectionLabel,
  parameterLabel,
  statusLabel,
  workspacePathLabel,
} from './labels';
import {
  hasRendererAdapter,
  parameterValue,
  rendererAdapter,
  setRecipeParameter,
  type AutomationTemplate,
  type ParameterDefinition,
} from './template';
import type {Recipe} from './recipe';

type SourceMode = 'selected' | 'folder' | 'folder_recursive';
type ReactionStrategy = 'fixed_asset' | 'pool_first' | 'pool_weighted';

const itemSection = (path: string) => path.split('/')[0] || '';

const ParameterControl = ({
  name,
  definition,
  recipe,
  onChange,
}: {
  name: string;
  definition: ParameterDefinition;
  recipe: Recipe;
  onChange: (recipe: Recipe) => void;
}) => {
  const value = parameterValue(recipe, name);
  const setValue = (next: string | number | boolean) => {
    onChange(setRecipeParameter(recipe, name, next));
  };
  if (definition.type === 'boolean') {
    return (
      <label className="apply-check">
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => setValue(event.target.checked)} />
        <span>{parameterLabel(name)}</span>
      </label>
    );
  }
  if (definition.type === 'select') {
    return (
      <label>
        <span>{parameterLabel(name)}</span>
        <select value={String(value)} onChange={(event) => setValue(event.target.value)}>
          {(definition.values || []).map((option) => (
            <option key={option} value={option}>{fitLabel(option)}</option>
          ))}
        </select>
      </label>
    );
  }
  if (definition.type === 'color') {
    return (
      <label>
        <span>{parameterLabel(name)}</span>
        <input type="color" value={String(value)} onChange={(event) => setValue(event.target.value)} />
      </label>
    );
  }
  return (
    <label>
      <span>{parameterLabel(name)}</span>
      <input
        type={definition.type === 'number' ? 'number' : 'text'}
        min={definition.min}
        max={definition.max}
        maxLength={definition.max_length}
        step={definition.type === 'number' && Number(definition.max) <= 1 ? 0.05 : 1}
        value={String(value)}
        onChange={(event) => setValue(
          definition.type === 'number' ? Number(event.target.value) : event.target.value,
        )}
      />
    </label>
  );
};

export const ApplyTemplatePanel = ({
  template,
  sections,
  folders,
  reactions,
  pools,
  recipe,
  onRecipeChange,
  batches,
  pipelines,
  onBatchCreated,
  onRefreshBatches,
  onRefreshPipelines,
}: {
  template: AutomationTemplate;
  sections: MediaSection[];
  folders: ApplySourceFolder[];
  reactions: ReactionItem[];
  pools: ReactionPool[];
  recipe: Recipe;
  onRecipeChange: (recipe: Recipe) => void;
  batches: RenderBatch[];
  pipelines: RemotionPipeline[];
  onBatchCreated: (batch: RenderBatch) => void;
  onRefreshBatches: () => Promise<void>;
  onRefreshPipelines: () => Promise<void>;
}) => {
  const [sourceMode, setSourceMode] = useState<SourceMode>('selected');
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [folderPath, setFolderPath] = useState(folders[0]?.path || 'sources');
  const [reactionStrategy, setReactionStrategy] = useState<ReactionStrategy>('fixed_asset');
  const [reactionAssetId, setReactionAssetId] = useState<number | null>(
    recipe.media.reaction.asset_id,
  );
  const [reactionPoolId, setReactionPoolId] = useState<number | null>(pools[0]?.id ?? null);
  const [batchName, setBatchName] = useState(`${template.name} batch`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const allowedSections = template.definition.slots.main?.allowed_sections || ['sources', 'cuts', 'prepared'];
  const mediaItems = sections
    .flatMap((section) => section.items)
    .filter((item) => allowedSections.includes(itemSection(item.workspace_path)));

  const previewItems = useMemo(() => {
    if (sourceMode === 'selected') {
      return mediaItems.filter((item) => selectedPaths.has(item.workspace_path));
    }
    const prefix = folderPath.replace(/\/+$/, '');
    return mediaItems.filter((item) => {
      if (sourceMode === 'folder_recursive') {
        return item.workspace_path === prefix || item.workspace_path.startsWith(`${prefix}/`);
      }
      const parent = item.workspace_path.split('/').slice(0, -1).join('/');
      return parent === prefix;
    });
  }, [folderPath, mediaItems, selectedPaths, sourceMode]);

  const togglePath = (path: string) => {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const adapter = rendererAdapter(template.definition);
  const supported = hasRendererAdapter(template.definition);

  const parameterValues = () => Object.fromEntries(
    Object.keys(template.definition.parameters).map((key) => [
      key,
      parameterValue(recipe, key),
    ]),
  );

  const requestBody = () => ({
    name: batchName,
    source_mode: sourceMode,
    source_paths: Array.from(selectedPaths),
    source_path: folderPath,
    recursive: sourceMode === 'folder_recursive',
    reaction_strategy: reactionStrategy,
    reaction_asset_id: reactionAssetId,
    reaction_pool_id: reactionPoolId,
    parameter_values: parameterValues(),
    start: true,
  });

  const createBatch = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await studioApi.applyTemplate(template.id, requestBody());
      onBatchCreated(result.batch);
      await onRefreshBatches();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const savePipeline = async () => {
    setBusy(true);
    setError('');
    try {
      await studioApi.createPipeline({
        ...requestBody(),
        name: batchName || `${template.name} pipeline`,
        studio_template_id: template.id,
        enabled: true,
      });
      await onRefreshPipelines();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const runPipeline = async (id: number) => {
    setBusy(true);
    setError('');
    try {
      const result = await studioApi.runPipeline(id);
      onBatchCreated(result.batch);
      await onRefreshBatches();
      await onRefreshPipelines();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const startBatch = async (id: number) => {
    await studioApi.startBatch(id);
    await onRefreshBatches();
  };

  const cancelBatch = async (id: number) => {
    await studioApi.cancelBatch(id);
    await onRefreshBatches();
  };

  return (
    <div className="apply-grid">
      <section className="ts-card apply-panel">
        <div className="ts-card-head">
          <div><h2>Apply Template</h2><p>{template.name} · {template.key} · v{template.version}</p></div>
        </div>
        <div className="adapter-note">
          Этот template использует существующий Remotion renderer adapter:
          {' '}
          <b>{adapter?.displayName || template.definition.rules.renderer_adapter || 'не найден'}</b>.
          {' '}
          Можно менять параметры, defaults и правила. Для полностью нового
          визуального renderer нужно добавить adapter.
        </div>
        {!supported ? (
          <div className="ts-alert error">Этот template пока не имеет Remotion renderer adapter.</div>
        ) : null}
        {error ? <div className="ts-alert error">{error}</div> : null}
        <label>
          <span>Название batch</span>
          <input value={batchName} onChange={(event) => setBatchName(event.target.value)} />
        </label>
        <label>
          <span>Источник</span>
          <select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as SourceMode)}>
            <option value="selected">Выбранные видео</option>
            <option value="folder">Папка без вложенных</option>
            <option value="folder_recursive">Папка рекурсивно</option>
          </select>
        </label>
        {sourceMode === 'selected' ? (
          <div className="apply-media-list">
            {mediaItems.map((item) => (
              <label className="apply-media-row" key={item.workspace_path}>
                <input
                  type="checkbox"
                  checked={selectedPaths.has(item.workspace_path)}
                  onChange={() => togglePath(item.workspace_path)}
                />
                <span>{item.name}</span>
                <small>{workspacePathLabel(item.workspace_path)}</small>
              </label>
            ))}
          </div>
        ) : (
          <label>
            <span>Папка</span>
            <select value={folderPath} onChange={(event) => setFolderPath(event.target.value)}>
              {folders
                .filter((folder) => allowedSections.includes(itemSection(folder.path)))
                .map((folder) => (
                  <option value={folder.path} key={folder.path}>
                    {workspacePathLabel(folder.path)}
                  </option>
                ))}
            </select>
          </label>
        )}
      </section>

      <section className="ts-card apply-panel">
        <div className="ts-card-head"><h2>Reaction и параметры</h2></div>
        <label>
          <span>Reaction strategy</span>
          <select value={reactionStrategy} onChange={(event) => setReactionStrategy(event.target.value as ReactionStrategy)}>
            <option value="fixed_asset">Фиксированный reaction asset</option>
            <option value="pool_first">Первый доступный из pool</option>
            <option value="pool_weighted">Случайный weighted из pool</option>
          </select>
        </label>
        {reactionStrategy === 'fixed_asset' ? (
          <label>
            <span>Reaction asset</span>
            <select value={reactionAssetId ?? ''} onChange={(event) => setReactionAssetId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Не выбран</option>
              {reactions.filter((item) => item.available).map((item) => (
                <option value={item.id} key={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            <span>Reaction pool</span>
            <select value={reactionPoolId ?? ''} onChange={(event) => setReactionPoolId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Не выбран</option>
              {pools.map((pool) => (
                <option value={pool.id} key={pool.id}>{pool.name} · {pool.items.length}</option>
              ))}
            </select>
          </label>
        )}
        <div className="apply-parameters">
          {Object.entries(template.definition.parameters).map(([name, definition]) => (
            <ParameterControl
              key={name}
              name={name}
              definition={definition}
              recipe={recipe}
              onChange={onRecipeChange}
            />
          ))}
        </div>
      </section>

      <section className="ts-card apply-panel">
        <div className="ts-card-head">
          <div><h2>Batch preview</h2><p>Будет создано render jobs: {previewItems.length}</p></div>
        </div>
        <div className="apply-preview">
          <div>Template: <b>{template.key}</b></div>
          <div>Источник: <b>{sourceMode === 'selected' ? 'выбранные видео' : workspacePathLabel(folderPath)}</b></div>
          <div>Reaction: <b>{reactionStrategy === 'fixed_asset' ? 'asset' : 'pool'}</b></div>
          <div>Output: <b>workspace_root/edits/&lt;source path&gt;/{template.key}/render_job_*.mp4</b></div>
        </div>
        <div className="apply-media-list compact">
          {previewItems.slice(0, 30).map((item) => (
            <div className="apply-media-row" key={item.workspace_path}>
              <span>{item.name}</span>
              <small>{workspacePathLabel(item.workspace_path)}</small>
            </div>
          ))}
          {previewItems.length > 30 ? <div className="empty-note">И ещё {previewItems.length - 30} файлов…</div> : null}
          {!previewItems.length ? <div className="empty-note">Видео не выбраны.</div> : null}
        </div>
        <div className="ts-row-actions">
          <button className="primary" disabled={busy || !supported || !previewItems.length} onClick={() => void createBatch()}>
            Create render batch
          </button>
          <button disabled={busy || !supported || !previewItems.length} onClick={() => void savePipeline()}>
            Сохранить pipeline
          </button>
        </div>
      </section>

      <section className="ts-card apply-panel apply-wide">
        <div className="ts-card-head">
          <div><h2>Batch progress</h2><p>Очередь допускает много queued jobs, но рендерит по одному.</p></div>
          <button onClick={() => void onRefreshBatches()}>Обновить</button>
        </div>
        <div className="batch-list">
          {batches.slice(0, 8).map((batch) => (
            <article className="batch-card" key={batch.id}>
              <div>
                <strong>#{batch.id} {batch.name}</strong>
                <small>{batch.template_key} · {statusLabel(batch.status)} · {batch.done_items}/{batch.total_items} готово · ошибок {batch.failed_items}</small>
              </div>
              <progress value={batch.done_items + batch.failed_items} max={Math.max(1, batch.total_items)} />
              <div className="ts-row-actions">
                {batch.status === 'queued' ? <button onClick={() => void startBatch(batch.id)}>Старт</button> : null}
                {['queued', 'running'].includes(batch.status) ? <button onClick={() => void cancelBatch(batch.id)}>Отменить queued</button> : null}
                <a href={`/studio?batch=${batch.id}`}>Открыть</a>
              </div>
            </article>
          ))}
          {!batches.length ? <div className="empty-note">Batch пока нет.</div> : null}
        </div>
      </section>

      <section className="ts-card apply-panel apply-wide">
        <div className="ts-card-head">
          <div><h2>Automated Pipelines</h2><p>Пока без watcher/cron: сохранение и ручной запуск.</p></div>
          <button onClick={() => void onRefreshPipelines()}>Обновить</button>
        </div>
        <div className="batch-list">
          {pipelines.map((pipeline) => (
            <article className="batch-card" key={pipeline.id}>
              <div>
                <strong>#{pipeline.id} {pipeline.name}</strong>
                <small>{pipeline.source_mode} · {pipeline.source_path || `${pipeline.source_paths.length} selected`} · {pipeline.enabled ? 'enabled' : 'disabled'}</small>
              </div>
              <div className="ts-row-actions">
                <button disabled={busy || !pipeline.enabled} onClick={() => void runPipeline(pipeline.id)}>
                  Запустить pipeline
                </button>
              </div>
            </article>
          ))}
          {!pipelines.length ? <div className="empty-note">Pipeline пока нет.</div> : null}
        </div>
      </section>
    </div>
  );
};
