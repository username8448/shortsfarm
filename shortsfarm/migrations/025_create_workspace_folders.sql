-- 025: Folder metadata for managed filesystem workspaces
CREATE TABLE IF NOT EXISTS workspace_folders (
    id             INTEGER PRIMARY KEY,
    workspace_root TEXT NOT NULL,
    relative_path  TEXT NOT NULL,
    display_name   TEXT,
    kind           TEXT NOT NULL DEFAULT 'custom'
                   CHECK(kind IN (
                       'custom', 'collection', 'project',
                       'source_group', 'podcast', 'episode'
                   )),
    description    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    UNIQUE(workspace_root, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_workspace_folders_root
    ON workspace_folders(workspace_root);
CREATE INDEX IF NOT EXISTS idx_workspace_folders_kind
    ON workspace_folders(workspace_root, kind);
