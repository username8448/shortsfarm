-- 017: Add next_attempt_at to publish_jobs
ALTER TABLE publish_jobs ADD COLUMN next_attempt_at TEXT;

CREATE INDEX IF NOT EXISTS idx_publish_jobs_next_attempt
    ON publish_jobs(status, next_attempt_at, id);
