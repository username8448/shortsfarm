CREATE UNIQUE INDEX IF NOT EXISTS idx_local_storage_profile_service_links_unique_profile_platform
    ON local_storage_profile_service_links(profile_id, platform);
