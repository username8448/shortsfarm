CREATE TABLE IF NOT EXISTS shorts_pipeline_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    status                TEXT    NOT NULL DEFAULT 'queued'
                          CHECK(status IN ('queued', 'splitting', 'rendering', 'syncing_profile', 'done', 'failed', 'cancelled')),
    source_mode           TEXT    NOT NULL
                          CHECK(source_mode IN ('external_file', 'workspace')),
    source_path           TEXT,
    source_paths_json     TEXT    NOT NULL DEFAULT '[]',
    imported_source_path  TEXT,
    split_seconds         INTEGER NOT NULL DEFAULT 60,
    skip_json             TEXT    NOT NULL DEFAULT '[]',
    overwrite             INTEGER NOT NULL DEFAULT 0,
    studio_template_id    INTEGER,
    template_key          TEXT,
    reaction_strategy     TEXT    NOT NULL DEFAULT 'fixed_asset',
    reaction_asset_id     INTEGER,
    reaction_pool_id      INTEGER,
    parameter_values_json TEXT    NOT NULL DEFAULT '{}',
    renderer_engine       TEXT    NOT NULL DEFAULT 'ffmpeg_fast',
    render_profile        TEXT    NOT NULL DEFAULT 'low_540p',
    duration_limit_sec    REAL,
    start_offset_sec      REAL    NOT NULL DEFAULT 0,
    full_length           INTEGER NOT NULL DEFAULT 0,
    tag_ids_json          TEXT    NOT NULL DEFAULT '[]',
    channel_tag_id        INTEGER,
    remotion_batch_id     INTEGER,
    summary_json          TEXT    NOT NULL DEFAULT '{}',
    error                 TEXT,
    created_at            TEXT    NOT NULL,
    started_at            TEXT,
    finished_at           TEXT,
    updated_at            TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_shorts_pipeline_runs_one_active
    ON shorts_pipeline_runs((1))
    WHERE status IN ('queued', 'splitting', 'rendering', 'syncing_profile');

CREATE INDEX IF NOT EXISTS idx_shorts_pipeline_runs_status
    ON shorts_pipeline_runs(status, id);

CREATE INDEX IF NOT EXISTS idx_shorts_pipeline_runs_batch
    ON shorts_pipeline_runs(remotion_batch_id);

CREATE TABLE IF NOT EXISTS shorts_pipeline_run_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                INTEGER NOT NULL REFERENCES shorts_pipeline_runs(id) ON DELETE CASCADE,
    source_workspace_path TEXT,
    segment_workspace_path TEXT,
    render_job_id         INTEGER,
    output_workspace_path TEXT,
    status                TEXT    NOT NULL DEFAULT 'queued',
    error                 TEXT,
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shorts_pipeline_run_items_run
    ON shorts_pipeline_run_items(run_id, id);

CREATE INDEX IF NOT EXISTS idx_shorts_pipeline_run_items_render_job
    ON shorts_pipeline_run_items(render_job_id);
