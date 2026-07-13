import {useEffect, useMemo, useState} from 'react';
import {studioApi, type ReactionItem} from '../api';

const fileNameFromPath = (value: string): string => {
  const name = value.split(/[\\/]/).filter(Boolean).pop() || '';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(0, dot) : name;
};

const shortPath = (value?: string | null): string => {
  const text = String(value || '');
  if (text.length <= 72) return text || '—';
  return `…${text.slice(-69)}`;
};

type ReactionsPageProps = {
  onChanged: () => Promise<void>;
};

export const ReactionsPage = ({onChanged}: ReactionsPageProps) => {
  const [items, setItems] = useState<ReactionItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: '',
    file_path: '',
    tags: '',
    mood: '',
    language: '',
    enabled: true,
  });
  const [importForm, setImportForm] = useState({
    folder_path: '',
    tags: 'reaction',
    mood: '',
    language: 'ru',
    recursive: true,
  });

  const load = async () => {
    const data = await studioApi.reactionAssetsForManagement();
    setItems(data.items || []);
  };

  useEffect(() => {
    void load().catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
  }, []);

  const selected = items.find((item) => item.id === selectedId) || null;
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) => [
      item.name,
      item.file_path,
      item.tags,
      item.mood,
      item.language,
    ].some((field) => String(field || '').toLowerCase().includes(needle)));
  }, [items, query]);

  const select = (item: ReactionItem) => {
    setSelectedId(item.id);
    setForm({
      name: item.name || '',
      file_path: item.file_path || '',
      tags: item.tags || '',
      mood: item.mood || '',
      language: item.language || '',
      enabled: item.enabled !== false,
    });
    setError('');
    setMessage('');
  };

  const reset = () => {
    setSelectedId(null);
    setForm({name: '', file_path: '', tags: '', mood: '', language: '', enabled: true});
    setError('');
    setMessage('');
  };

  const pickFile = async () => {
    try {
      const data = await studioApi.pickLocalPath('file', 'Выберите reaction-видео');
      if (!data.selected || !data.path) return;
      setForm((current) => ({
        ...current,
        file_path: data.path,
        name: current.name.trim() ? current.name : fileNameFromPath(data.path),
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Локальный выбор файла недоступен. Укажите путь вручную.');
    }
  };

  const pickFolder = async () => {
    try {
      const data = await studioApi.pickLocalPath('directory', 'Выберите папку с reaction-видео');
      if (!data.selected || !data.path) return;
      setImportForm((current) => ({...current, folder_path: data.path}));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Локальный выбор папки недоступен. Укажите путь вручную.');
    }
  };

  const save = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const body = {
        name: form.name.trim(),
        file_path: form.file_path.trim(),
        tags: form.tags.trim() || null,
        mood: form.mood.trim() || null,
        language: form.language.trim() || null,
        enabled: form.enabled,
      };
      if (!body.name) throw new Error('Название реакции обязательно.');
      if (!body.file_path) throw new Error('Путь к файлу реакции обязателен.');
      const data = selectedId
        ? await studioApi.updateReactionAsset(selectedId, body)
        : await studioApi.createReactionAsset(body);
      await load();
      await onChanged();
      select(data.item);
      setMessage(selectedId ? 'Реакция сохранена.' : 'Реакция добавлена.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    if (!selectedId) return;
    setBusy(true);
    setError('');
    try {
      const data = await studioApi.disableReactionAsset(selectedId);
      await load();
      await onChanged();
      select(data.item);
      setMessage('Реакция отключена.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const importFolder = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const data = await studioApi.importReactionFolder({
        folder_path: importForm.folder_path.trim(),
        recursive: importForm.recursive,
        tags: importForm.tags.trim() || null,
        mood: importForm.mood.trim() || null,
        language: importForm.language.trim() || null,
      });
      await load();
      await onChanged();
      setMessage(`Импортировано: ${data.created} · пропущено: ${data.skipped} · ошибок: ${data.errors}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="builder-grid">
      <section className="ts-card">
        <div className="ts-card-head">
          <div><h2>Реакции</h2><p>Reaction assets для шаблонов, тестов и Apply Template.</p></div>
          <button onClick={reset}>Добавить реакцию</button>
        </div>
        <label>
          <span>Поиск</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Название, тег, настроение, путь…" />
        </label>
        <div className="ts-table-scroll">
          <table>
            <thead><tr><th>#</th><th>Название / файл</th><th>Теги</th><th>Состояние</th></tr></thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.id} className={item.id === selectedId ? 'active' : ''} onClick={() => select(item)}>
                  <td className="mono">#{item.id}</td>
                  <td>
                    <b>{item.name}</b>
                    <span className="mono dim" title={item.file_path}>{shortPath(item.file_path)}</span>
                  </td>
                  <td>
                    <span className="mono dim">{item.tags || '—'}</span>
                    <span className="mono dim">{[item.mood, item.language].filter(Boolean).join(' · ')}</span>
                  </td>
                  <td>
                    <span className={`ts-badge ${item.enabled === false ? 'error' : ''}`}>{item.enabled === false ? 'disabled' : 'active'}</span>
                    <span className={`ts-badge ${item.file_exists === false ? 'error' : ''}`}>{item.file_exists === false ? 'missing' : 'ok'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="ts-card">
        <div className="ts-card-head"><h2>{selected ? `Reaction #${selected.id}` : 'Новая реакция'}</h2></div>
        {error ? <div className="ts-alert error">{error}</div> : null}
        {message ? <div className="ts-alert success">{message}</div> : null}
        <label><span>Название</span><input value={form.name} onChange={(event) => setForm({...form, name: event.target.value})} /></label>
        <label>
          <span>Путь к файлу</span>
          <div className="path-pick-row">
            <input value={form.file_path} onChange={(event) => setForm({...form, file_path: event.target.value})} placeholder="/path/to/reaction.mp4" />
            <button type="button" onClick={() => void pickFile()}>Выбрать…</button>
          </div>
        </label>
        <div className="info-row">
          <label><span>Теги</span><input value={form.tags} onChange={(event) => setForm({...form, tags: event.target.value})} /></label>
          <label><span>Настроение</span><input value={form.mood} onChange={(event) => setForm({...form, mood: event.target.value})} /></label>
        </div>
        <label><span>Язык</span><input value={form.language} onChange={(event) => setForm({...form, language: event.target.value})} placeholder="ru" /></label>
        <label className="check-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({...form, enabled: event.target.checked})} /> Активна</label>
        <div className="ts-row-actions">
          <button className="primary" disabled={busy} onClick={() => void save()}>Сохранить</button>
          {selectedId ? <button disabled={busy} onClick={() => void disable()}>Отключить</button> : null}
        </div>
      </section>

      <section className="ts-card">
        <div className="ts-card-head"><h2>Импорт папки</h2></div>
        <label>
          <span>Папка</span>
          <div className="path-pick-row">
            <input value={importForm.folder_path} onChange={(event) => setImportForm({...importForm, folder_path: event.target.value})} placeholder="/path/to/reactions" />
            <button type="button" onClick={() => void pickFolder()}>Выбрать папку…</button>
          </div>
        </label>
        <div className="info-row">
          <label><span>Теги</span><input value={importForm.tags} onChange={(event) => setImportForm({...importForm, tags: event.target.value})} /></label>
          <label><span>Настроение</span><input value={importForm.mood} onChange={(event) => setImportForm({...importForm, mood: event.target.value})} /></label>
        </div>
        <label><span>Язык</span><input value={importForm.language} onChange={(event) => setImportForm({...importForm, language: event.target.value})} /></label>
        <label className="check-row"><input type="checkbox" checked={importForm.recursive} onChange={(event) => setImportForm({...importForm, recursive: event.target.checked})} /> Включая вложенные папки</label>
        <button disabled={busy} onClick={() => void importFolder()}>Импортировать папку</button>
      </section>
    </div>
  );
};
