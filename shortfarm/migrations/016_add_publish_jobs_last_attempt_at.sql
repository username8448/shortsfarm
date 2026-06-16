-- 016: Add last_attempt_at to publish_jobs
ALTER TABLE publish_jobs ADD COLUMN last_attempt_at TEXT;
