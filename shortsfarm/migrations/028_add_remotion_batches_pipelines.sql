-- 028: Remotion Apply Template batches and pipeline foundation
PRAGMA foreign_keys = OFF;

DROP INDEX IF EXISTS idx_remotion_render_jobs_one_active_global;
DROP INDEX IF EXISTS idx_remotion_render_jobs_one_active_project;

CREATE TABLE IF NOT EXISTS remotion_render_jobs_new (
    id                INTEGER PRIMARY KEY,
    studio_project_id INTEGER NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK(status IN ('queued', 'rendering', 'done', 'failed', 'cancelled')),
    output_path       TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT
);

INSERT OR IGNORE INTO remotion_render_jobs_new
    (id, studio_project_id, status, output_path, error, created_at, started_at, finished_at)
SELECT id, studio_project_id, status, output_path, error, created_at, started_at, finished_at
FROM remotion_render_jobs;

DROP TABLE remotion_render_jobs;
ALTER TABLE remotion_render_jobs_new RENAME TO remotion_render_jobs;

PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_project
    ON remotion_render_jobs(studio_project_id);
CREATE INDEX IF NOT EXISTS idx_remotion_render_jobs_status
    ON remotion_render_jobs(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_remotion_render_jobs_one_active_global
    ON remotion_render_jobs((1))
    WHERE status = 'rendering';

CREATE UNIQUE INDEX IF NOT EXISTS idx_remotion_render_jobs_one_active_project
    ON remotion_render_jobs(studio_project_id)
    WHERE status IN ('queued', 'rendering');

CREATE TABLE IF NOT EXISTS remotion_render_batches (
    id                    INTEGER PRIMARY KEY,
    studio_template_id    INTEGER REFERENCES studio_templates(id) ON DELETE SET NULL,
    template_key          TEXT NOT NULL,
    name                  TEXT NOT NULL,
    source_mode           TEXT NOT NULL
                          CHECK(source_mode IN ('selected', 'folder', 'folder_recursive', 'pipeline')),
    source_path           TEXT,
    reaction_strategy     TEXT NOT NULL DEFAULT 'fixed_asset'
                          CHECK(reaction_strategy IN ('fixed_asset', 'pool_first', 'pool_weighted')),
    reaction_asset_id     INTEGER REFERENCES reaction_assets(id) ON DELETE SET NULL,
    reaction_pool_id      INTEGER REFERENCES reaction_pools(id) ON DELETE SET NULL,
    parameter_values_json TEXT NOT NULL DEFAULT '{}',
    status                TEXT NOT NULL DEFAULT 'queued'
                          CHECK(status IN ('draft', 'queued', 'running', 'done', 'failed', 'cancelled')),
    total_items           INTEGER NOT NULL DEFAULT 0,
    done_items            INTEGER NOT NULL DEFAULT 0,
    failed_items          INTEGER NOT NULL DEFAULT 0,
    error                 TEXT,
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    finished_at           TEXT,
    updated_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_remotion_render_batches_status
    ON remotion_render_batches(status);
CREATE INDEX IF NOT EXISTS idx_remotion_render_batches_template
    ON remotion_render_batches(studio_template_id);

CREATE TABLE IF NOT EXISTS remotion_render_batch_items (
    id                  INTEGER PRIMARY KEY,
    batch_id            INTEGER NOT NULL REFERENCES remotion_render_batches(id) ON DELETE CASCADE,
    studio_project_id   INTEGER NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
    render_job_id       INTEGER NOT NULL REFERENCES remotion_render_jobs(id) ON DELETE CASCADE,
    main_workspace_path TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'rendering', 'done', 'failed', 'cancelled')),
    error               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_remotion_render_batch_items_job
    ON remotion_render_batch_items(render_job_id);
CREATE INDEX IF NOT EXISTS idx_remotion_render_batch_items_batch
    ON remotion_render_batch_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_remotion_render_batch_items_status
    ON remotion_render_batch_items(status);

CREATE TABLE IF NOT EXISTS remotion_pipelines (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL,
    studio_template_id    INTEGER REFERENCES studio_templates(id) ON DELETE SET NULL,
    source_mode           TEXT NOT NULL
                          CHECK(source_mode IN ('selected', 'folder', 'folder_recursive')),
    source_path           TEXT,
    source_paths_json     TEXT NOT NULL DEFAULT '[]',
    recursive             INTEGER NOT NULL DEFAULT 0,
    reaction_strategy     TEXT NOT NULL DEFAULT 'fixed_asset'
                          CHECK(reaction_strategy IN ('fixed_asset', 'pool_first', 'pool_weighted')),
    reaction_asset_id     INTEGER REFERENCES reaction_assets(id) ON DELETE SET NULL,
    reaction_pool_id      INTEGER REFERENCES reaction_pools(id) ON DELETE SET NULL,
    parameter_values_json TEXT NOT NULL DEFAULT '{}',
    output_policy_json    TEXT NOT NULL DEFAULT '{}',
    enabled               INTEGER NOT NULL DEFAULT 1,
    last_batch_id         INTEGER REFERENCES remotion_render_batches(id) ON DELETE SET NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_remotion_pipelines_enabled
    ON remotion_pipelines(enabled);
CREATE INDEX IF NOT EXISTS idx_remotion_pipelines_template
    ON remotion_pipelines(studio_template_id);
