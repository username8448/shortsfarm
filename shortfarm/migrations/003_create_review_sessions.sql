-- 003: Create review_sessions table
CREATE TABLE IF NOT EXISTS review_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER NOT NULL,
    session_file TEXT    NOT NULL UNIQUE,
    status       TEXT    NOT NULL DEFAULT 'open',
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    imported_at  TEXT,
    error        TEXT,
    FOREIGN KEY(video_id) REFERENCES videos(id)
);

CREATE INDEX IF NOT EXISTS idx_review_sessions_video_id ON review_sessions(video_id);
