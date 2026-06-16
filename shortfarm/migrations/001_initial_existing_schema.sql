-- 001: Registration of existing tables (videos, jobs, segments)
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    duration_sec REAL,
    status      TEXT    NOT NULL DEFAULT 'added',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL,
    type            TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    mode            TEXT    NOT NULL,
    segment_seconds INTEGER NOT NULL,
    error           TEXT,
    created_at      TEXT    NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      INTEGER NOT NULL,
    job_id        INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    start_sec     REAL    NOT NULL,
    end_sec       REAL    NOT NULL,
    path          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
    FOREIGN KEY(job_id)   REFERENCES jobs(id)   ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_video_id     ON jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_segments_video_id ON segments(video_id);
CREATE INDEX IF NOT EXISTS idx_segments_job_id   ON segments(job_id);
