-- 027: Versioned automation template definitions for Template Studio
CREATE TABLE IF NOT EXISTS studio_templates (
    id              INTEGER PRIMARY KEY,
    template_key    TEXT NOT NULL,
    name            TEXT NOT NULL,
    engine          TEXT NOT NULL CHECK(engine IN ('remotion', 'ffmpeg')),
    version         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft', 'active', 'archived')),
    definition_json TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_templates_key_version
    ON studio_templates(template_key, version);
CREATE INDEX IF NOT EXISTS idx_studio_templates_status
    ON studio_templates(status);
CREATE INDEX IF NOT EXISTS idx_studio_templates_engine
    ON studio_templates(engine);

ALTER TABLE studio_projects ADD COLUMN studio_template_id INTEGER
    REFERENCES studio_templates(id) ON DELETE SET NULL;
ALTER TABLE studio_projects ADD COLUMN reaction_pool_id INTEGER
    REFERENCES reaction_pools(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_studio_projects_template_definition
    ON studio_projects(studio_template_id);
CREATE INDEX IF NOT EXISTS idx_studio_projects_reaction_pool
    ON studio_projects(reaction_pool_id);
