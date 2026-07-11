-- 041: Bridge legacy Editing templates/jobs to Template Studio.

ALTER TABLE edit_templates ADD COLUMN studio_template_id INTEGER
    REFERENCES studio_templates(id) ON DELETE SET NULL;

ALTER TABLE channel_profiles ADD COLUMN default_studio_template_id INTEGER
    REFERENCES studio_templates(id) ON DELETE SET NULL;

ALTER TABLE edit_jobs ADD COLUMN studio_template_id INTEGER
    REFERENCES studio_templates(id) ON DELETE SET NULL;

ALTER TABLE edit_jobs ADD COLUMN studio_project_id INTEGER
    REFERENCES studio_projects(id) ON DELETE SET NULL;

ALTER TABLE edit_jobs ADD COLUMN remotion_render_job_id INTEGER
    REFERENCES remotion_render_jobs(id) ON DELETE SET NULL;

ALTER TABLE studio_templates ADD COLUMN deleted_at TEXT;

CREATE INDEX IF NOT EXISTS idx_edit_templates_studio_template
    ON edit_templates(studio_template_id);

CREATE INDEX IF NOT EXISTS idx_channel_profiles_default_studio_template
    ON channel_profiles(default_studio_template_id);

CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_template
    ON edit_jobs(studio_template_id);

CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_project
    ON edit_jobs(studio_project_id);

CREATE INDEX IF NOT EXISTS idx_edit_jobs_remotion_render_job
    ON edit_jobs(remotion_render_job_id);

CREATE INDEX IF NOT EXISTS idx_studio_templates_deleted_at
    ON studio_templates(deleted_at);

CREATE INDEX IF NOT EXISTS idx_edit_jobs_studio_duplicate
    ON edit_jobs(workspace_item_key, channel_profile_id, studio_template_id, status);
