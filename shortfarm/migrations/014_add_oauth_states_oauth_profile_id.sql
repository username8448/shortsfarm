-- 014: Track oauth_profile_id in oauth_states
ALTER TABLE oauth_states ADD COLUMN oauth_profile_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_oauth_states_oauth_profile_id
    ON oauth_states(oauth_profile_id);
