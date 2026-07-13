import {useEffect, useMemo, useState} from 'react';
import {studioApi, type ReactionItem, type ReactionPool, type ReactionPoolItem} from '../api';

type ReactionPoolsPageProps = {
  reactions: ReactionItem[];
  onChanged: () => Promise<void>;
};

export const ReactionPoolsPage = ({reactions, onChanged}: ReactionPoolsPageProps) => {
  const [pools, setPools] = useState<ReactionPool[]>([]);
  const [items, setItems] = useState<ReactionPoolItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [assetId, setAssetId] = useState<number | null>(null);
  const [weight, setWeight] = useState(1);
  const [form, setForm] = useState({name: '', description: '', enabled: true});
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const loadPools = async () => {
    const data = await studioApi.reactionPoolsForManagement();
    setPools(data.items || []);
  };

  const loadItems = async (poolId: number) => {
    const data = await studioApi.reactionPoolItems(poolId);
    setItems(data.items || []);
  };

  useEffect(() => {
    void loadPools().catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
  }, []);

  const selected = pools.find((item) => item.id === selectedId) || null;
  const activeReactions = useMemo(
    () => reactions.filter((item) => item.enabled !== false && item.available !== false),
    [reactions],
  );

  const reset = () => {
    setSelectedId(null);
    setItems([]);
    setAssetId(null);
    setWeight(1);
    setForm({name: '', description: '', enabled: true});
    setError('');
    setMessage('');
  };

  const select = async (pool: ReactionPool) => {
    setSelectedId(pool.id);
    setForm({
      name: pool.name || '',
      description: pool.description || '',
      enabled: pool.enabled !== false,
    });
    setError('');
    setMessage('');
    await loadItems(pool.id);
  };

  const save = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        enabled: form.enabled,
      };
      if (!body.name) throw new Error('Название пула обязательно.');
      const data = selectedId
        ? await studioApi.updateReactionPool(selectedId, body)
        : await studioApi.createReactionPool(body);
      await loadPools();
      await onChanged();
      await select(data.item);
      setMessage(selectedId ? 'Пул сохранён.' : 'Пул создан.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const upsertItem = async () => {
    if (!selectedId) {
      setError('Сначала выберите или создайте пул.');
      return;
    }
    if (!assetId) {
      setError('Выберите reaction asset.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const data = await studioApi.upsertReactionPoolItem(selectedId, assetId, Math.max(1, Number(weight) || 1));
      setItems(data.items || []);
      await loadPools();
      await onChanged();
      setMessage('Элемент пула обновлён.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const removeItem = async (reactionAssetId: number) => {
    if (!selectedId) return;
    setBusy(true);
    setError('');
    try {
      const data = await studioApi.removeReactionPoolItem(selectedId, reactionAssetId);
      setItems(data.items || []);
      await loadPools();
      await onChanged();
      setMessage('Элемент удалён из пула.');
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
          <div><h2>Пулы реакций</h2><p>Правила выбора reaction assets для Studio render.</p></div>
          <button onClick={reset}>Создать пул</button>
        </div>
        <div className="ts-table-scroll">
          <table>
            <thead><tr><th>#</th><th>Название</th><th>Элементы</th><th>Статус</th></tr></thead>
            <tbody>
              {pools.map((pool) => (
                <tr key={pool.id} className={pool.id === selectedId ? 'active' : ''} onClick={() => void select(pool)}>
                  <td className="mono">#{pool.id}</td>
                  <td><b>{pool.name}</b><span className="mono dim">{pool.description || ''}</span></td>
                  <td className="mono">{pool.item_count || pool.items?.length || 0}</td>
                  <td><span className={`ts-badge ${pool.enabled === false ? 'error' : ''}`}>{pool.enabled === false ? 'disabled' : 'active'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="ts-card">
        <div className="ts-card-head"><h2>{selected ? `Пул #${selected.id}` : 'Новый пул'}</h2></div>
        {error ? <div className="ts-alert error">{error}</div> : null}
        {message ? <div className="ts-alert success">{message}</div> : null}
        <label><span>Название</span><input value={form.name} onChange={(event) => setForm({...form, name: event.target.value})} /></label>
        <label><span>Описание</span><textarea rows={3} value={form.description} onChange={(event) => setForm({...form, description: event.target.value})} /></label>
        <label className="check-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({...form, enabled: event.target.checked})} /> Активен</label>
        <div className="ts-row-actions">
          <button className="primary" disabled={busy} onClick={() => void save()}>Сохранить пул</button>
        </div>
      </section>

      <section className="ts-card">
        <div className="ts-card-head"><h2>Элементы пула</h2></div>
        <div className="info-row">
          <label>
            <span>Reaction asset</span>
            <select value={assetId ?? ''} onChange={(event) => setAssetId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Выберите файл реакции</option>
              {activeReactions.map((item) => (
                <option key={item.id} value={item.id}>{item.name}{item.file_exists === false ? ' · файл отсутствует' : ''}</option>
              ))}
            </select>
          </label>
          <label><span>Вес выбора</span><input type="number" min={1} value={weight} onChange={(event) => setWeight(Number(event.target.value) || 1)} /></label>
        </div>
        <button disabled={busy || !selectedId} onClick={() => void upsertItem()}>Добавить / обновить</button>
        <div className="ts-table-scroll">
          <table>
            <thead><tr><th>Реакция</th><th>Вес</th><th>Файл</th><th /></tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.reaction_asset_id}>
                  <td><b>{item.asset_name}</b><span className="mono dim">{item.tags || ''}</span></td>
                  <td className="mono">{item.weight}</td>
                  <td><span className={`ts-badge ${item.file_exists ? '' : 'error'}`}>{item.file_exists ? 'ok' : 'missing'}</span></td>
                  <td><button disabled={busy} onClick={() => void removeItem(item.reaction_asset_id)}>Удалить</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};
