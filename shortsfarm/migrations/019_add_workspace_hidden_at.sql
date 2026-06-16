-- 019: Soft-hide workspace items and remember confirmed missing files
ALTER TABLE clip_workspace_metadata ADD COLUMN hidden_at TEXT;
ALTER TABLE clip_workspace_metadata ADD COLUMN missing_confirmed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_clip_workspace_metadata_hidden
    ON clip_workspace_metadata(hidden_at);
