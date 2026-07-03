CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL DEFAULT 'user'
                CHECK(kind IN ('user', 'system', 'status', 'channel')),
    color       TEXT    NOT NULL DEFAULT '#64748b',
    description TEXT,
    system_key  TEXT,
    locked      INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_system_key
    ON tags(system_key)
    WHERE system_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tags_kind_enabled
    ON tags(kind, enabled);

CREATE TABLE IF NOT EXISTS workspace_tag_links (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_path TEXT,
    item_type      TEXT,
    item_id        INTEGER,
    tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source         TEXT    NOT NULL DEFAULT 'manual',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    CHECK(workspace_path IS NOT NULL OR (item_type IS NOT NULL AND item_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_tag_links_path_tag
    ON workspace_tag_links(workspace_path, tag_id)
    WHERE workspace_path IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_tag_links_item_tag
    ON workspace_tag_links(item_type, item_id, tag_id)
    WHERE item_type IS NOT NULL AND item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_tag_links_tag
    ON workspace_tag_links(tag_id);

CREATE TABLE IF NOT EXISTS local_storage_profile_tag_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES local_storage_profiles(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    mode       TEXT    NOT NULL DEFAULT 'include'
               CHECK(mode IN ('include', 'exclude')),
    locked     INTEGER NOT NULL DEFAULT 0,
    source     TEXT    NOT NULL DEFAULT 'manual',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE(profile_id, tag_id, mode)
);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_tag_rules_profile
    ON local_storage_profile_tag_rules(profile_id, mode);

CREATE INDEX IF NOT EXISTS idx_local_storage_profile_tag_rules_tag
    ON local_storage_profile_tag_rules(tag_id);

ALTER TABLE local_storage_profiles
    ADD COLUMN tag_match_mode TEXT NOT NULL DEFAULT 'any'
    CHECK(tag_match_mode IN ('any', 'all'));

INSERT INTO tags (name, slug, kind, color, description, system_key, locked, enabled, created_at, updated_at)
VALUES
    ('Черновик', 'status-draft', 'status', '#64748b', 'Системный статус: черновик', 'status:draft', 1, 1, datetime('now'), datetime('now')),
    ('Готово', 'status-ready', 'status', '#22c55e', 'Системный статус: готово к публикации', 'status:ready', 1, 1, datetime('now'), datetime('now')),
    ('В очереди', 'status-queued', 'status', '#38bdf8', 'Системный статус: в очереди', 'status:queued', 1, 1, datetime('now'), datetime('now')),
    ('Загружено', 'status-uploaded', 'status', '#a78bfa', 'Системный статус: загружено', 'status:uploaded', 1, 1, datetime('now'), datetime('now')),
    ('Ошибка', 'status-failed', 'status', '#ef4444', 'Системный статус: ошибка', 'status:failed', 1, 1, datetime('now'), datetime('now'))
ON CONFLICT(slug) DO UPDATE SET
    name=excluded.name,
    slug=excluded.slug,
    kind=excluded.kind,
    color=excluded.color,
    description=excluded.description,
    locked=excluded.locked,
    enabled=excluded.enabled,
    updated_at=excluded.updated_at;

INSERT OR IGNORE INTO workspace_tag_links (item_type, item_id, tag_id, source, created_at, updated_at)
SELECT wm.item_type, wm.item_id, t.id, 'workspace_status', datetime('now'), datetime('now')
FROM clip_workspace_metadata wm
JOIN tags t ON t.system_key = 'status:' || wm.workspace_status
WHERE wm.workspace_status IS NOT NULL;

INSERT OR IGNORE INTO workspace_tag_links (workspace_path, tag_id, source, created_at, updated_at)
SELECT lspi.workspace_path, t.id, 'profile_item_status', datetime('now'), datetime('now')
FROM local_storage_profile_items lspi
JOIN tags t ON t.system_key = 'status:' || lspi.status
WHERE lspi.workspace_path IS NOT NULL;
