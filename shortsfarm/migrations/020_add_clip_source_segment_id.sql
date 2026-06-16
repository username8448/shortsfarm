-- 020: Link publishable service clips back to source workspace segments
ALTER TABLE clips ADD COLUMN source_segment_id INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_source_segment_id
    ON clips(source_segment_id)
    WHERE source_segment_id IS NOT NULL;
