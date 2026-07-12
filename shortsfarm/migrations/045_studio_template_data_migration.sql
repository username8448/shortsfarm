-- 045: Data migration bridge from legacy edit_templates to Studio templates.
-- Complex/idempotent data migration is implemented in shortsfarm.migrations.
CREATE TABLE IF NOT EXISTS migration_reports (
    migration_key TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
