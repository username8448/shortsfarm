-- 048: Repair previously applied Studio template migration links.
-- The Python migration handler performs all data repair and writes a report.
CREATE TABLE IF NOT EXISTS migration_reports (
    migration_key TEXT PRIMARY KEY,
    report_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
