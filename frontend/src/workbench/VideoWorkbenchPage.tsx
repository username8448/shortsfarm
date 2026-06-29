import {useEffect, useMemo, useState} from 'react';
import {mediaApi, type WorkspaceVideoSection} from '../api';
import {folderSectionLabel, workspacePathLabel} from '../studio/labels';
import {UniversalVideoWorkbench} from './UniversalVideoWorkbench';

export const VideoWorkbenchPage = ({initialPath = ''}: {initialPath?: string}) => {
  const [sections, setSections] = useState<WorkspaceVideoSection[]>([]);
  const [inputPath, setInputPath] = useState(initialPath);
  const [activePath, setActivePath] = useState(initialPath);
  const [error, setError] = useState('');

  const flatItems = useMemo(
    () => sections.flatMap((section) => section.items.map((item) => ({
      ...item,
      section: section.key,
    }))),
    [sections],
  );

  useEffect(() => {
    const load = async () => {
      try {
        const data = await mediaApi.videos();
        setSections(data.sections);
        if (!activePath) {
          const first = data.sections.flatMap((section) => section.items)[0];
          if (first) {
            setInputPath(first.path);
            setActivePath(first.path);
          }
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void load();
  }, []);

  const open = () => {
    const next = inputPath.trim();
    if (!next) {
      setError('Укажите workspace path к видео.');
      return;
    }
    setError('');
    setActivePath(next);
  };

  return (
    <div className="workbench-page">
      <section className="ts-card apply-panel">
        <div className="ts-card-head">
          <div><h2>Video Workbench</h2><p>Единый viewer для workspace video и ручной разметки таймингов.</p></div>
        </div>
        {error ? <div className="ts-alert error">{error}</div> : null}
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
          <button className="primary" onClick={open}>Открыть player</button>
          <button onClick={() => void mediaApi.videos().then((data) => setSections(data.sections))}>Обновить список</button>
        </div>
      </section>

      {activePath ? (
        <UniversalVideoWorkbench
          workspacePath={activePath}
          title="Video Workbench"
          mode="marking"
          onUseAsSource={(path) => setInputPath(path)}
        />
      ) : (
        <section className="ts-card">
          <div className="preview-empty">Выберите видео из workspace или введите путь вручную.</div>
        </section>
      )}
    </div>
  );
};
