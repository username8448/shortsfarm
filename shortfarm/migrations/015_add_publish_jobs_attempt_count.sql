-- 015: Add attempt_count to publish_jobs
ALTER TABLE publish_jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
