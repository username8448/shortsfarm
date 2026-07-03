CREATE TABLE IF NOT EXISTS local_storage_profile_publish_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id      INTEGER NOT NULL REFERENCES local_storage_profiles(id) ON DELETE CASCADE,
    profile_item_id INTEGER NOT NULL REFERENCES local_storage_profile_items(id) ON DELETE CASCADE,
    publish_job_id  INTEGER NOT NULL REFERENCES publish_jobs(id) ON DELETE CASCADE,
    platform        TEXT    NOT NULL DEFAULT 'youtube',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE(profile_item_id, publish_job_id)
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_publish_jobs_profile
    ON local_storage_profile_publish_jobs(profile_id, platform, publish_job_id);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_publish_jobs_item
    ON local_storage_profile_publish_jobs(profile_item_id, platform);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_publish_jobs_job
    ON local_storage_profile_publish_jobs(publish_job_id);
