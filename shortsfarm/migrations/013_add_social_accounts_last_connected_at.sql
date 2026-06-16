-- 013: Add last_connected_at to social_accounts
ALTER TABLE social_accounts ADD COLUMN last_connected_at TEXT;
