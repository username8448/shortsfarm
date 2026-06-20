-- 022: Scheduled YouTube uploads and publish schedule groups
CREATE TABLE IF NOT EXISTS publish_schedule_groups (
    id                         INTEGER PRIMARY KEY,
    name                       TEXT    NOT NULL,
    upload_mode                TEXT    NOT NULL DEFAULT 'none',
    upload_start_at            TEXT,
    upload_interval_minutes    INTEGER,
    upload_item_times          TEXT,
    publish_mode               TEXT    NOT NULL DEFAULT 'none',
    publish_start_at           TEXT,
    publish_interval_minutes   INTEGER,
    publish_item_times         TEXT,
    created_at                 TEXT    NOT NULL,
    updated_at                 TEXT    NOT NULL
);

ALTER TABLE publish_jobs ADD COLUMN schedule_group_id INTEGER;
ALTER TABLE publish_jobs ADD COLUMN schedule_position INTEGER;
ALTER TABLE publish_jobs ADD COLUMN upload_at TEXT;
ALTER TABLE publish_jobs ADD COLUMN overdue_approved_at TEXT;

CREATE INDEX IF NOT EXISTS idx_publish_jobs_upload_at
    ON publish_jobs(status, upload_at);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_schedule_group
    ON publish_jobs(schedule_group_id, schedule_position);
