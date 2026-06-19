-- 021: Workspace video preparation metadata
ALTER TABLE clip_workspace_metadata ADD COLUMN target_aspect TEXT DEFAULT 'original';
ALTER TABLE clip_workspace_metadata ADD COLUMN prepared_path TEXT;
ALTER TABLE clip_workspace_metadata ADD COLUMN prepared_at TEXT;
ALTER TABLE clip_workspace_metadata ADD COLUMN prepare_status TEXT DEFAULT 'none';
ALTER TABLE clip_workspace_metadata ADD COLUMN prepare_error TEXT;

ALTER TABLE clips ADD COLUMN source_clip_id INTEGER;
ALTER TABLE clips ADD COLUMN source_aspect TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_source_clip_aspect
    ON clips(source_clip_id, source_aspect)
    WHERE source_clip_id IS NOT NULL;
