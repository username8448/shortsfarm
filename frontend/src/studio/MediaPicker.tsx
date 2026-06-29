import type {MediaItem, MediaSection} from '../api';
import {openWebPlayer} from '../workbench/openWebPlayer';
import {folderSectionLabel, workspacePathLabel} from './labels';

export const MediaPicker = ({
  sections,
  selected,
  onSelect,
  allowedSections,
}: {
  sections: MediaSection[];
  selected: string;
  onSelect: (item: MediaItem) => void;
  allowedSections?: string[];
}) => (
  <>
    <h2>Медиа</h2>
    {sections.map((section) => (
      <section
        className={`media-section ${section.kind === 'edited' ? 'edited-results' : ''}`}
        key={section.key}
      >
        <h3>
          {section.label || folderSectionLabel(section.key)}
          {section.kind === 'edited' ? <span className="edited-badge">результат</span> : null}
        </h3>
        {section.items.length ? section.items.map((item) => (
          <div className="media-item-row" key={item.workspace_path}>
            <button
              className={`media-item ${selected === item.workspace_path ? 'selected' : ''}`}
              disabled={Boolean(
                allowedSections && !allowedSections.includes(section.key),
              )}
              onClick={() => onSelect(item)}
            >
              <span>{item.name}</span>
              <small title={item.workspace_path}>{workspacePathLabel(item.workspace_path)}</small>
              {allowedSections && !allowedSections.includes(section.key)
                ? <em>Не разрешено схемой слота</em>
                : null}
            </button>
            <button
              className="media-watch-button"
              type="button"
              onClick={() => openWebPlayer(item.workspace_path)}
            >
              Смотреть
            </button>
          </div>
        )) : <div className="empty-note">Видео не найдены</div>}
      </section>
    ))}
  </>
);
