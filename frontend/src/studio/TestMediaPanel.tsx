import type {
  MediaItem,
  MediaSection,
  ReactionItem,
  ReactionPool,
} from '../api';
import {MediaPicker} from './MediaPicker';

export const TestMediaPanel = ({
  sections,
  selectedMain,
  allowedSections,
  reactions,
  pools,
  reactionAssetId,
  reactionPoolId,
  onMain,
  onReaction,
  onPool,
  onOpenReactions,
}: {
  sections: MediaSection[];
  selectedMain: string;
  allowedSections: string[];
  reactions: ReactionItem[];
  pools: ReactionPool[];
  reactionAssetId: number | null;
  reactionPoolId: number | null;
  onMain: (item: MediaItem) => void;
  onReaction: (id: number | null) => void;
  onPool: (id: number | null) => void;
  onOpenReactions: () => void;
}) => (
  <section className="ts-card test-media-card">
    <div className="ts-card-head">
      <div><h2>Test Media</h2><p>Sample context не входит в template definition.</p></div>
    </div>
    <div className="test-selects">
      <label>
        <span>Reaction pool · optional</span>
        <select value={reactionPoolId ?? ''} onChange={(event) => onPool(event.target.value ? Number(event.target.value) : null)}>
          <option value="">Без pool</option>
          {pools.map((pool) => <option value={pool.id} key={pool.id}>{pool.name} · {pool.items.length}</option>)}
        </select>
      </label>
      <label>
        <span>Reaction sample</span>
        <select value={reactionAssetId ?? ''} onChange={(event) => onReaction(event.target.value ? Number(event.target.value) : null)}>
          <option value="">Не выбран</option>
          {reactions.filter((item) => item.available).map((item) => (
            <option value={item.id} key={item.id}>{item.name}</option>
          ))}
        </select>
      </label>
    </div>
    {!reactions.some((item) => item.available) ? (
      <div className="reaction-empty">
        <strong>Reaction-файлы не добавлены</strong>
        <p>Добавьте reaction в разделе Монтаж → Реакции.</p>
        <button onClick={onOpenReactions}>Открыть раздел Монтаж</button>
      </div>
    ) : null}
    <div className="sample-media-list">
      <MediaPicker
        sections={sections}
        selected={selectedMain}
        allowedSections={allowedSections}
        onSelect={onMain}
      />
    </div>
  </section>
);
