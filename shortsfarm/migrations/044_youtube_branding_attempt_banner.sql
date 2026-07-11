-- 044: Repairable YouTube branding attempts and best-effort channel banner metadata
ALTER TABLE local_storage_profiles ADD COLUMN youtube_branding_attempted_at TEXT;
ALTER TABLE local_storage_profiles ADD COLUMN banner_url TEXT;

ALTER TABLE social_accounts ADD COLUMN channel_banner_url TEXT;
ALTER TABLE social_accounts ADD COLUMN channel_branding_json TEXT;
