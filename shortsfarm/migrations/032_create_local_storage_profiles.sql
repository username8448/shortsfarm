CREATE TABLE IF NOT EXISTS local_storage_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    handle          TEXT NOT NULL UNIQUE,
    description     TEXT,
    avatar_initials TEXT,
    avatar_color    TEXT NOT NULL DEFAULT '#3b82f6',
    banner_color    TEXT NOT NULL DEFAULT '#111827',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profiles_enabled
    ON local_storage_profiles(enabled);

CREATE TABLE IF NOT EXISTS local_storage_profile_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id     INTEGER NOT NULL REFERENCES local_storage_profiles(id) ON DELETE CASCADE,
    workspace_path TEXT NOT NULL,
    title          TEXT,
    description    TEXT,
    tags           TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',
    added_at       TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(profile_id, workspace_path)
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_items_profile
    ON local_storage_profile_items(profile_id);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_items_path
    ON local_storage_profile_items(workspace_path);

CREATE TABLE IF NOT EXISTS local_storage_profile_service_links (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id          INTEGER NOT NULL REFERENCES local_storage_profiles(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL,
    external_account_id INTEGER,
    display_name        TEXT,
    status              TEXT NOT NULL DEFAULT 'not_connected',
    settings_json       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_service_links_profile
    ON local_storage_profile_service_links(profile_id);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_service_links_platform
    ON local_storage_profile_service_links(platform);
