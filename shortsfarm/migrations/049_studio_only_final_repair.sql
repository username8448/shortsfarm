-- 049: Final Studio-only repair pass without deleting legacy tables.
-- The Python migration handler performs data repair and post-repair verification.
CREATE TABLE IF NOT EXISTS migration_reports (
    migration_key TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
