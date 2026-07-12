-- 046: Mark Template Studio as the only runtime template system.
-- The cutover timestamp is written by the Python migration handler.
CREATE TABLE IF NOT EXISTS migration_reports (
    migration_key TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
