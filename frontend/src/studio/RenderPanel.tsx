import type {RenderJob} from '../api';

export const RenderPanel = ({
  projectId,
  job,
  busy,
  onSave,
  onRender,
}: {
  projectId: number | null;
  job: RenderJob | null;
  busy: boolean;
  onSave: () => void;
  onRender: () => void;
}) => (
  <section className="render-panel">
    <div className="actions">
      <button onClick={onSave} disabled={busy}>Сохранить проект</button>
      <button className="primary" onClick={onRender} disabled={busy}>
        Рендер
      </button>
    </div>
    <div className="status">
      Project: {projectId ? `#${projectId}` : 'не сохранён'}
      {job ? <> · Render #{job.id}: <strong>{job.status}</strong></> : null}
    </div>
    {job?.error ? <div className="error">{job.error}</div> : null}
    {job?.status === 'done' && job.media_url ? (
      <a className="result-link" href={job.media_url} target="_blank" rel="noreferrer">
        Открыть готовое видео
      </a>
    ) : null}
  </section>
);
