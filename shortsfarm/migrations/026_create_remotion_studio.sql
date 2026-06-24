-- 026: Remotion Studio projects and render jobs
CREATE TABLE IF NOT EXISTS studio_projects (
    id                  INTEGER PRIMARY KEY,
    workspace_item_key  TEXT,
    main_workspace_path TEXT NOT NULL,
    template_key        TEXT NOT NULL,
    reaction_asset_id   INTEGER REFERENCES reaction_assets(id) ON DELETE SET NULL,
    recipe_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_studio_projects_main_path
    ON studio_projects(main_workspace_path);
CREATE INDEX IF NOT EXISTS idx_studio_projects_template_key
    ON studio_projects(template_key);
CREATE INDEX IF NOT EXISTS idx_studio_projects_reaction
    ON studio_projects(reaction_asset_id);

CREATE TABLE IF NOT EXISTS remotion_render_jobs (
    id                INTEGER PRIMARY KEY,
    studio_project_id INTEGER NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK(status IN ('queued', 'rendering', 'done', 'failed')),
    output_path       TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_project
    ON remotion_render_jobs(studio_project_id);
CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_status
    ON remotion_render_jobs(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_remotion_render_jobs_one_active_global
    ON remotion_render_jobs((1))
    WHERE status IN ('queued', 'rendering');

CREATE UNIQUE INDEX IF NOT EXISTS idx_remotion_render_jobs_one_active_project
    ON remotion_render_jobs(studio_project_id)
    WHERE status IN ('queued', 'rendering');
