-- 010: Create youtube_oauth_profiles table
CREATE TABLE IF NOT EXISTS youtube_oauth_profiles (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    mode          TEXT    NOT NULL DEFAULT 'custom',
    client_id     TEXT    NOT NULL,
    client_secret TEXT,
    redirect_uri  TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active',
    is_default    INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_youtube_oauth_profiles_status
    ON youtube_oauth_profiles(status);
