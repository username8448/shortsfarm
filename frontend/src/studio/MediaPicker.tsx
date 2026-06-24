import type {MediaItem, MediaSection} from '../api';

export const MediaPicker = ({
  sections,
  selected,
  onSelect,
}: {
  sections: MediaSection[];
  selected: string;
  onSelect: (item: MediaItem) => void;
}) => (
  <>
    <h2>Media</h2>
    {sections.map((section) => (
      <section
        className={`media-section ${section.kind === 'edited' ? 'edited-results' : ''}`}
        key={section.key}
      >
        <h3>
          {section.label}
          {section.kind === 'edited' ? <span className="edited-badge">result</span> : null}
        </h3>
        {section.items.length ? section.items.map((item) => (
          <button
            className={`media-item ${selected === item.workspace_path ? 'selected' : ''}`}
            key={item.workspace_path}
            onClick={() => onSelect(item)}
          >
            <span>{item.name}</span>
            <small>{item.workspace_path}</small>
          </button>
        )) : <div className="empty-note">Видео не найдены</div>}
      </section>
    ))}
  </>
);
