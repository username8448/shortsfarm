-- 007: OAuth state protection and social account error field
CREATE TABLE IF NOT EXISTS oauth_states (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    state       TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_provider ON oauth_states(provider);

ALTER TABLE social_accounts ADD COLUMN error TEXT;
