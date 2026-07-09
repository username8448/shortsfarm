-- 040: Soft-delete metadata for source videos.

ALTER TABLE videos ADD COLUMN deleted_at TEXT;
ALTER TABLE videos ADD COLUMN source_file_deleted_at TEXT;

CREATE INDEX IF NOT EXISTS idx_videos_deleted_at ON videos(deleted_at);
