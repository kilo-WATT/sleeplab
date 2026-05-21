-- migrations/012_add_wearable_settings.sql
BEGIN;
ALTER TABLE user_import_settings
    ADD COLUMN IF NOT EXISTS wearable_provider  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_base_url  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_api_key   TEXT;
COMMIT;
