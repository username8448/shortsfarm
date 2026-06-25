import type {AutomationTemplate} from './template';

export const TemplatesPage = ({
  templates,
  onOpen,
  onDuplicate,
  onTest,
}: {
  templates: AutomationTemplate[];
  onOpen: (item: AutomationTemplate) => void;
  onDuplicate: (item: AutomationTemplate) => void;
  onTest: (item: AutomationTemplate) => void;
}) => (
  <section className="ts-card templates-list">
    <div className="ts-card-head">
      <div>
        <h2>Automation Templates</h2>
        <p>Версионируемые схемы для пакетного применения к видео и каналам.</p>
      </div>
      <span className="ts-count">{templates.length}</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Key / Name</th>
          <th>Engine</th>
          <th>Version</th>
          <th>Status</th>
          <th>Updated</th>
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
            <td><span className={`ts-badge ${item.status}`}>{item.status}</span></td>
            <td>{new Date(item.updated_at || item.created_at).toLocaleString('ru-RU')}</td>
            <td>
              <div className="ts-row-actions">
                <button onClick={() => onOpen(item)}>Open</button>
                <button onClick={() => onDuplicate(item)}>Duplicate</button>
                <button className="primary" onClick={() => onTest(item)}>Test</button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </section>
);
