-- 004: Create marks table
CREATE TABLE IF NOT EXISTS marks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL,
    session_id INTEGER,
    in_sec     REAL    NOT NULL,
    out_sec    REAL    NOT NULL,
    rating     INTEGER,
    label      TEXT,
    source     TEXT    NOT NULL DEFAULT 'mpv',
    created_at TEXT    NOT NULL,
    FOREIGN KEY(video_id)   REFERENCES videos(id),
    FOREIGN KEY(session_id) REFERENCES review_sessions(id),
    CHECK(out_sec > in_sec),
    CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5))
);

CREATE INDEX IF NOT EXISTS idx_marks_video_id ON marks(video_id);
