ALTER TABLE local_storage_profile_service_links
    ADD COLUMN last_sync_at TEXT;

ALTER TABLE local_storage_profile_service_links
    ADD COLUMN last_sync_error TEXT;

ALTER TABLE local_storage_profile_service_links
    ADD COLUMN synced_video_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS local_storage_profile_external_videos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id        INTEGER NOT NULL REFERENCES local_storage_profiles(id) ON DELETE CASCADE,
    platform          TEXT    NOT NULL DEFAULT 'youtube',
    external_video_id TEXT    NOT NULL,
    external_url      TEXT,
    title             TEXT,
    description       TEXT,
    tags              TEXT,
    category_id       TEXT,
    privacy_status    TEXT,
    publish_at        TEXT,
    published_at      TEXT,
    duration          TEXT,
    thumbnail_url     TEXT,
    profile_item_id   INTEGER REFERENCES local_storage_profile_items(id) ON DELETE SET NULL,
    publish_job_id    INTEGER REFERENCES publish_jobs(id) ON DELETE SET NULL,
    raw_json          TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(profile_id, platform, external_video_id)
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_external_videos_profile
    ON local_storage_profile_external_videos(profile_id, platform, last_seen_at);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_external_videos_video
    ON local_storage_profile_external_videos(platform, external_video_id);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_external_videos_job
    ON local_storage_profile_external_videos(publish_job_id);
