import type {ReactionItem} from '../api';

export const ReactionPicker = ({
  reactions,
  value,
  onChange,
}: {
  reactions: ReactionItem[];
  value: number | null;
  onChange: (id: number | null) => void;
}) => (
  <label className="control">
    <span>Reaction-видео</span>
    <select
      value={value ?? ''}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
    >
      <option value="">Выберите reaction</option>
      {reactions.map((item) => (
        <option key={item.id} value={item.id} disabled={!item.available}>
          {item.name}{item.available ? '' : ' · недоступно'}
        </option>
      ))}
    </select>
  </label>
);
