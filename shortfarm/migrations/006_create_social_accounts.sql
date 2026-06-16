-- 006: Create social_accounts table for publishing integrations
CREATE TABLE IF NOT EXISTS social_accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT    NOT NULL,
    display_name     TEXT,
    channel_id       TEXT,
    channel_title    TEXT,
    access_token     TEXT,
    refresh_token    TEXT,
    token_expires_at TEXT,
    scopes           TEXT,
    status           TEXT    NOT NULL DEFAULT 'active',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_social_accounts_platform ON social_accounts(platform);
CREATE INDEX IF NOT EXISTS idx_social_accounts_status   ON social_accounts(status);
