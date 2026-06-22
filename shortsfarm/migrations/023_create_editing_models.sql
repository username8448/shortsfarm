-- 023: Base data model for template-driven Shorts editing
CREATE TABLE IF NOT EXISTS reaction_assets (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    file_path    TEXT    NOT NULL,
    duration_sec REAL,
    tags         TEXT,
    mood         TEXT,
    language     TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_assets_file_path
    ON reaction_assets(file_path);
CREATE INDEX IF NOT EXISTS idx_reaction_assets_enabled
    ON reaction_assets(enabled);

CREATE TABLE IF NOT EXISTS reaction_pools (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_pools_name
    ON reaction_pools(name);

CREATE TABLE IF NOT EXISTS reaction_pool_items (
    id                INTEGER PRIMARY KEY,
    pool_id           INTEGER NOT NULL REFERENCES reaction_pools(id) ON DELETE CASCADE,
    reaction_asset_id INTEGER NOT NULL REFERENCES reaction_assets(id) ON DELETE CASCADE,
    weight            INTEGER NOT NULL DEFAULT 1,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_pool_items_unique
    ON reaction_pool_items(pool_id, reaction_asset_id);
CREATE INDEX IF NOT EXISTS idx_reaction_pool_items_pool_id
    ON reaction_pool_items(pool_id);
CREATE INDEX IF NOT EXISTS idx_reaction_pool_items_asset_id
    ON reaction_pool_items(reaction_asset_id);

CREATE TABLE IF NOT EXISTS edit_templates (
    id          INTEGER PRIMARY KEY,
    key         TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    renderer    TEXT    NOT NULL DEFAULT 'ffmpeg',
    recipe_json TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_edit_templates_key
    ON edit_templates(key);
CREATE INDEX IF NOT EXISTS idx_edit_templates_enabled
    ON edit_templates(enabled);
CREATE INDEX IF NOT EXISTS idx_edit_templates_renderer
    ON edit_templates(renderer);

CREATE TABLE IF NOT EXISTS channel_profiles (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    youtube_account_id  INTEGER REFERENCES social_accounts(id) ON DELETE SET NULL,
    default_template_id INTEGER REFERENCES edit_templates(id) ON DELETE SET NULL,
    reaction_pool_id    INTEGER REFERENCES reaction_pools(id) ON DELETE SET NULL,
    title_template      TEXT,
    description_template TEXT,
    tags_template       TEXT,
    default_privacy     TEXT,
    default_category_id TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_channel_profiles_youtube_account
    ON channel_profiles(youtube_account_id);
CREATE INDEX IF NOT EXISTS idx_channel_profiles_default_template
    ON channel_profiles(default_template_id);
CREATE INDEX IF NOT EXISTS idx_channel_profiles_reaction_pool
    ON channel_profiles(reaction_pool_id);
CREATE INDEX IF NOT EXISTS idx_channel_profiles_enabled
    ON channel_profiles(enabled);

CREATE TABLE IF NOT EXISTS edit_jobs (
    id                 INTEGER PRIMARY KEY,
    workspace_item_key TEXT NOT NULL,
    channel_profile_id INTEGER REFERENCES channel_profiles(id) ON DELETE SET NULL,
    template_id        INTEGER REFERENCES edit_templates(id) ON DELETE SET NULL,
    reaction_asset_id  INTEGER REFERENCES reaction_assets(id) ON DELETE SET NULL,
    input_path         TEXT,
    output_path        TEXT,
    edited_path        TEXT,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK(status IN ('queued', 'rendering', 'done', 'failed', 'cancelled')),
    renderer           TEXT NOT NULL DEFAULT 'ffmpeg',
    recipe_json        TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    finished_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_edit_jobs_status
    ON edit_jobs(status);
CREATE INDEX IF NOT EXISTS idx_edit_jobs_workspace_item
    ON edit_jobs(workspace_item_key);
CREATE INDEX IF NOT EXISTS idx_edit_jobs_channel_profile
    ON edit_jobs(channel_profile_id);
CREATE INDEX IF NOT EXISTS idx_edit_jobs_template
    ON edit_jobs(template_id);
CREATE INDEX IF NOT EXISTS idx_edit_jobs_reaction_asset
    ON edit_jobs(reaction_asset_id);
