-- 042: Store synchronized YouTube channel metadata on social accounts
ALTER TABLE social_accounts ADD COLUMN channel_description TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_custom_url TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_handle TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_country TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_published_at TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_avatar_url TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_thumbnails_json TEXT;
ALTER TABLE social_accounts ADD COLUMN subscriber_count INTEGER;
ALTER TABLE social_accounts ADD COLUMN view_count INTEGER;
ALTER TABLE social_accounts ADD COLUMN video_count INTEGER;
ALTER TABLE social_accounts ADD COLUMN hidden_subscriber_count INTEGER;
ALTER TABLE social_accounts ADD COLUMN uploads_playlist_id TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_status_json TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_metadata_json TEXT;
ALTER TABLE social_accounts ADD COLUMN metadata_synced_at TEXT;
ALTER TABLE social_accounts ADD COLUMN metadata_sync_error TEXT;

CREATE INDEX IF NOT EXISTS idx_social_accounts_channel_id
    ON social_accounts(platform, channel_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_metadata_synced_at
    ON social_accounts(metadata_synced_at);
