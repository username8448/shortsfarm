import {useEffect, useMemo, useState} from 'react';
import {mediaApi, type WorkspaceVideoSection} from '../api';
import {folderSectionLabel, workspacePathLabel} from '../studio/labels';
import {UniversalVideoWorkbench} from './UniversalVideoWorkbench';

export const VideoPlayerPage = ({initialPath = ''}: {initialPath?: string}) => {
  const [sections, setSections] = useState<WorkspaceVideoSection[]>([]);
  const [inputPath, setInputPath] = useState(initialPath);
  const [activePath, setActivePath] = useState(initialPath);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const flatItems = useMemo(
    () => sections.flatMap((section) => section.items.map((item) => ({
      ...item,
      section: section.key,
    }))),
    [sections],
  );

  const loadVideos = async () => {
    const data = await mediaApi.videos();
    setSections(data.sections);
    if (!activePath) {
      const first = data.sections.flatMap((section) => section.items)[0];
      if (first) {
        setInputPath(first.path);
      }
    }
  };

  useEffect(() => {
    void loadVideos().catch((caught) => {
      setError(caught instanceof Error ? caught.message : String(caught));
    });
  }, []);

  const open = () => {
    const next = inputPath.trim();
    if (!next) {
      setError('Укажите workspace path к видео.');
      return;
    }
    setError('');
    setActivePath(next);
    const url = new URL(window.location.href);
    url.pathname = '/player';
    url.searchParams.set('path', next);
    window.history.replaceState(null, '', url.toString());
  };

  const copyPath = async () => {
    if (!activePath) return;
    await navigator.clipboard?.writeText(activePath);
    setMessage('Workspace path скопирован.');
    window.setTimeout(() => setMessage(''), 1800);
  };

  return (
    <main className="player-page">
      <section className="player-hero">
        <div>
          <p className="player-kicker">ShortsFarm</p>
          <h1>Video Player</h1>
          <p>Глобальный web player для видео внутри workspace: sources, cuts, prepared, edits, ready и published.</p>
        </div>
        <div className="ts-row-actions">
          <a className="button-like" href="/">Назад в основную панель</a>
          <button disabled={!activePath} onClick={() => void copyPath()}>Копировать путь</button>
        </div>
      </section>

      <section className="ts-card apply-panel">
        <div className="ts-card-head">
          <div><h2>Выбор видео</h2><p>Можно открыть `/player?path=sources/example.mp4` напрямую.</p></div>
          <button onClick={() => void loadVideos().catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))}>
            Обновить список
          </button>
        </div>
        {error ? <div className="ts-alert error">{error}</div> : null}
        {message ? <div className="ts-alert success">{message}</div> : null}
        <label>
          <span>Workspace path</span>
          <input
            value={inputPath}
            onChange={(event) => setInputPath(event.target.value)}
            placeholder="sources/example.mp4"
          />
        </label>
        <label>
          <span>Выбрать из workspace</span>
          <select value={inputPath} onChange={(event) => setInputPath(event.target.value)}>
            <option value="">—</option>
            {flatItems.map((item) => (
              <option value={item.path} key={item.path}>
                {folderSectionLabel(item.section)} · {workspacePathLabel(item.path)}
              </option>
            ))}
          </select>
        </label>
        <div className="ts-row-actions">
          <button className="primary" onClick={open}>Открыть</button>
        </div>
      </section>

      {activePath ? (
        <UniversalVideoWorkbench
          workspacePath={activePath}
          title={workspacePathLabel(activePath)}
          mode="marking"
          onUseAsSource={(path) => {
            setInputPath(path);
            setActivePath(path);
          }}
        />
      ) : (
        <section className="ts-card">
          <div className="preview-empty">Выберите видео из workspace или введите путь вручную.</div>
        </section>
      )}
    </main>
  );
};
