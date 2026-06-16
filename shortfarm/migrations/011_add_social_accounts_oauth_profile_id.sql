-- 011: Link social_accounts to youtube_oauth_profiles
ALTER TABLE social_accounts ADD COLUMN oauth_profile_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_social_accounts_oauth_profile_id
    ON social_accounts(oauth_profile_id);
