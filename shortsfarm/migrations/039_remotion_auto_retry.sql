-- 039: Remotion render auto-retry metadata

ALTER TABLE remotion_render_jobs ADD COLUMN auto_retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE remotion_render_jobs ADD COLUMN max_auto_retries INTEGER NOT NULL DEFAULT 2;

CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_auto_retry
    ON remotion_render_jobs(status, auto_retry_count, max_auto_retries, id);
