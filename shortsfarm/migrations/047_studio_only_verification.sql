-- 047: Verify Studio-only cutover without dropping legacy tables.
CREATE TABLE IF NOT EXISTS migration_reports (
    migration_key TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
