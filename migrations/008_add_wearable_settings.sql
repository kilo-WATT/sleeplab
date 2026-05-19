-- migrations/008_add_wearable_settings.sql
-- NOTE: Rename this file to the next available number after all pending PRs
-- (#27-#29, #37) merge and you inspect migrations/ for the highest existing number.
BEGIN;
ALTER TABLE user_import_settings
    ADD COLUMN IF NOT EXISTS wearable_provider  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_base_url  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_api_key   TEXT;
COMMIT;
