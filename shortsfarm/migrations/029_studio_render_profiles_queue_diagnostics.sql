-- 029: Studio render profiles, diagnostics, retry/recovery metadata

ALTER TABLE remotion_render_jobs ADD COLUMN renderer_engine TEXT NOT NULL DEFAULT 'ffmpeg_fast';
ALTER TABLE remotion_render_jobs ADD COLUMN render_profile TEXT NOT NULL DEFAULT 'low_540p';
ALTER TABLE remotion_render_jobs ADD COLUMN duration_limit_sec REAL;
ALTER TABLE remotion_render_jobs ADD COLUMN start_offset_sec REAL NOT NULL DEFAULT 0;
ALTER TABLE remotion_render_jobs ADD COLUMN full_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE remotion_render_jobs ADD COLUMN worker_pid INTEGER;
ALTER TABLE remotion_render_jobs ADD COLUMN worker_started_at TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN last_heartbeat_at TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN stdout_tail TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN stderr_tail TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN returncode INTEGER;
ALTER TABLE remotion_render_jobs ADD COLUMN elapsed_sec REAL;

ALTER TABLE remotion_render_batches ADD COLUMN renderer_engine TEXT NOT NULL DEFAULT 'ffmpeg_fast';
ALTER TABLE remotion_render_batches ADD COLUMN render_profile TEXT NOT NULL DEFAULT 'low_540p';
ALTER TABLE remotion_render_batches ADD COLUMN duration_limit_sec REAL;
ALTER TABLE remotion_render_batches ADD COLUMN start_offset_sec REAL NOT NULL DEFAULT 0;
ALTER TABLE remotion_render_batches ADD COLUMN full_length INTEGER NOT NULL DEFAULT 0;

ALTER TABLE remotion_pipelines ADD COLUMN renderer_engine TEXT NOT NULL DEFAULT 'ffmpeg_fast';
ALTER TABLE remotion_pipelines ADD COLUMN render_profile TEXT NOT NULL DEFAULT 'low_540p';
ALTER TABLE remotion_pipelines ADD COLUMN duration_limit_sec REAL;
ALTER TABLE remotion_pipelines ADD COLUMN start_offset_sec REAL NOT NULL DEFAULT 0;
ALTER TABLE remotion_pipelines ADD COLUMN full_length INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_worker_pid
    ON remotion_render_jobs(worker_pid);
CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_heartbeat
    ON remotion_render_jobs(last_heartbeat_at);
