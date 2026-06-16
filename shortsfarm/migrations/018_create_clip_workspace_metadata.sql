-- 018: Workspace metadata for segment/clip preparation UI
CREATE TABLE IF NOT EXISTS clip_workspace_metadata (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type        TEXT    NOT NULL,
    item_id          INTEGER NOT NULL,
    workspace_status TEXT    NOT NULL DEFAULT 'draft',
    title            TEXT,
    description      TEXT,
    tags             TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    UNIQUE(item_type, item_id),
    CHECK(item_type IN ('segment', 'clip')),
    CHECK(workspace_status IN ('draft', 'ready', 'queued', 'uploaded', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_clip_workspace_metadata_item
    ON clip_workspace_metadata(item_type, item_id);

CREATE INDEX IF NOT EXISTS idx_clip_workspace_metadata_status
    ON clip_workspace_metadata(workspace_status);
