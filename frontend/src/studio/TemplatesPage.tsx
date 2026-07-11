import type {AutomationTemplate} from './template';
import {statusLabel} from './labels';

export const TemplatesPage = ({
  templates,
  onOpen,
  onDuplicate,
  onTest,
  onDelete,
  onRestore,
}: {
  templates: AutomationTemplate[];
  onOpen: (item: AutomationTemplate) => void;
  onDuplicate: (item: AutomationTemplate) => void;
  onTest: (item: AutomationTemplate) => void;
  onDelete: (item: AutomationTemplate) => void;
  onRestore: (item: AutomationTemplate) => void;
}) => (
  <section className="ts-card templates-list">
    <div className="ts-card-head">
      <div>
        <h2>Автоматизированные шаблоны</h2>
        <p>Версионируемые схемы для пакетного применения к видео и каналам.</p>
      </div>
      <span className="ts-count">{templates.length}</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Ключ / название</th>
          <th>Движок</th>
          <th>Версия</th>
          <th>Статус</th>
          <th>Обновлён</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {templates.map((item) => (
          <tr key={item.id}>
            <td>
              <strong>{item.name}</strong>
              <small>{item.key}</small>
            </td>
            <td><span className="ts-badge engine">{item.engine}</span></td>
            <td>v{item.version}</td>
            <td>
              <span className={`ts-badge ${item.status}`}>{statusLabel(item.status)}</span>
              {item.deleted_at ? <span className="ts-badge archived">Скрыт</span> : null}
            </td>
            <td>{new Date(item.updated_at || item.created_at).toLocaleString('ru-RU')}</td>
            <td>
              <div className="ts-row-actions">
                <button onClick={() => onOpen(item)}>Открыть</button>
                <button onClick={() => onDuplicate(item)}>Дублировать</button>
                <button className="primary" onClick={() => onTest(item)}>Тест</button>
                {item.deleted_at ? (
                  <button onClick={() => onRestore(item)}>Восстановить</button>
                ) : (
                  <button onClick={() => onDelete(item)}>Удалить</button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </section>
);
