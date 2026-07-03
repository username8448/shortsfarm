ALTER TABLE local_storage_profiles
    ADD COLUMN auto_import_enabled INTEGER NOT NULL DEFAULT 0;

ALTER TABLE local_storage_profiles
    ADD COLUMN auto_import_sections TEXT NOT NULL DEFAULT '["edits","ready","published"]';

ALTER TABLE local_storage_profiles
    ADD COLUMN auto_import_prefix TEXT;

ALTER TABLE local_storage_profiles
    ADD COLUMN auto_import_last_scan_at TEXT;

CREATE INDEX IF NOT EXISTS idx_local_storage_profiles_auto_import
    ON local_storage_profiles(auto_import_enabled);
