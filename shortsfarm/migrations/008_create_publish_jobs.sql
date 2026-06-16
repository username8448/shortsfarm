-- 008: Create publish_jobs table for YouTube uploads
CREATE TABLE IF NOT EXISTS publish_jobs (
    id                INTEGER PRIMARY KEY,
    platform          TEXT    NOT NULL DEFAULT 'youtube',
    account_id        INTEGER NOT NULL,
    clip_id           INTEGER NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'queued',
    title             TEXT    NOT NULL,
    description       TEXT,
    tags              TEXT,
    category_id       TEXT    NOT NULL DEFAULT '22',
    privacy_status    TEXT    NOT NULL,
    publish_mode      TEXT    NOT NULL,
    publish_at        TEXT,
    made_for_kids     INTEGER NOT NULL DEFAULT 0,
    youtube_video_id  TEXT,
    youtube_url       TEXT,
    error             TEXT,
    created_at        TEXT    NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    updated_at        TEXT,
    UNIQUE(platform, account_id, clip_id)
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_platform ON publish_jobs(platform);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_status   ON publish_jobs(status);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_clip_id  ON publish_jobs(clip_id);
