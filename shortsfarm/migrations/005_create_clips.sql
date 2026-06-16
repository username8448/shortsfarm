-- 005: Create clips table
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL,
    mark_id     INTEGER,
    status      TEXT    NOT NULL DEFAULT 'queued',
    cut_mode    TEXT    NOT NULL DEFAULT 'exact',
    output_path TEXT,
    temp_path   TEXT,
    error       TEXT,
    created_at  TEXT    NOT NULL,
    started_at  TEXT,
    rendered_at TEXT,
    FOREIGN KEY(video_id) REFERENCES videos(id),
    FOREIGN KEY(mark_id)  REFERENCES marks(id)
);

CREATE INDEX IF NOT EXISTS idx_clips_video_id ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_status   ON clips(status);
