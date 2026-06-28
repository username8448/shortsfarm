-- 030: Studio render live progress fields

ALTER TABLE remotion_render_jobs ADD COLUMN progress_percent REAL NOT NULL DEFAULT 0;
ALTER TABLE remotion_render_jobs ADD COLUMN progress_stage TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN progress_message TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN current_frame INTEGER;
ALTER TABLE remotion_render_jobs ADD COLUMN total_frames INTEGER;
ALTER TABLE remotion_render_jobs ADD COLUMN out_time_sec REAL;
ALTER TABLE remotion_render_jobs ADD COLUMN speed TEXT;
ALTER TABLE remotion_render_jobs ADD COLUMN eta_sec REAL;
ALTER TABLE remotion_render_jobs ADD COLUMN output_size_bytes INTEGER;
ALTER TABLE remotion_render_jobs ADD COLUMN completed_message TEXT;
