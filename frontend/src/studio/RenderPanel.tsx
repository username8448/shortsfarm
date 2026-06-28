import type {RenderJob} from '../api';
import {statusLabel} from './labels';

const progressValue = (job: RenderJob): number => {
  if (['done', 'failed', 'cancelled'].includes(job.status)) return 100;
  return Math.max(0, Math.min(99, Number(job.progress_percent || 0)));
};

const formatDuration = (seconds?: number | null): string => {
  if (seconds === null || seconds === undefined) return '—';
  const total = Math.max(0, Math.round(Number(seconds)));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
};

export const RenderPanel = ({
  projectId,
  job,
  busy,
  onSave,
  onRender,
  renderDisabled,
  disabledReason,
}: {
  projectId: number | null;
  job: RenderJob | null;
  busy: boolean;
  onSave: () => void;
  onRender: () => void;
  renderDisabled?: boolean;
  disabledReason?: string;
}) => (
  <section className="render-panel">
    <div className="actions">
      <button onClick={onSave} disabled={busy}>Сохранить проект</button>
      <button className="primary" onClick={onRender} disabled={busy || renderDisabled}>
        Рендер
      </button>
    </div>
    <div className="status">
      Проект: {projectId ? `#${projectId}` : 'не сохранён'}
      {job ? <> · Рендер #{job.id}: <strong>{statusLabel(job.status)}</strong></> : null}
    </div>
    {job ? (
      <div className="single-render-progress">
        <progress className="render-progress" value={progressValue(job)} max={100} />
        <div className="render-progress-meta">
          <span>{Math.round(progressValue(job))}%</span>
          {job.progress_message ? <span>{job.progress_message}</span> : null}
          {job.eta_sec ? <span>Осталось примерно: {formatDuration(job.eta_sec)}</span> : null}
        </div>
        {job.output_path ? <small>Путь: {job.output_path}</small> : null}
      </div>
    ) : null}
    {job?.error ? <div className="error">{job.error}</div> : null}
    {renderDisabled && disabledReason
      ? <div className="render-disabled">{disabledReason}</div>
      : null}
    {job?.status === 'done' && job.media_url ? (
      <a className="result-link" href={job.media_url} target="_blank" rel="noreferrer">
        Открыть готовое видео
      </a>
    ) : null}
  </section>
);
