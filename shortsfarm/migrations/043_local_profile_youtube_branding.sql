-- 043: YouTube branding mirror for local storage profiles
ALTER TABLE local_storage_profiles ADD COLUMN avatar_url TEXT;
ALTER TABLE local_storage_profiles ADD COLUMN youtube_branding_sync_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE local_storage_profiles ADD COLUMN name_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_storage_profiles ADD COLUMN handle_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_storage_profiles ADD COLUMN description_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_storage_profiles ADD COLUMN avatar_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_storage_profiles ADD COLUMN banner_override INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_storage_profiles ADD COLUMN youtube_branding_synced_at TEXT;
ALTER TABLE local_storage_profiles ADD COLUMN youtube_branding_sync_error TEXT;

CREATE INDEX IF NOT EXISTS idx_local_storage_profiles_youtube_branding
    ON local_storage_profiles(youtube_branding_sync_enabled);
