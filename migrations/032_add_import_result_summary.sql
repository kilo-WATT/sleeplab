-- User-facing import provenance and result summary fields.
ALTER TABLE import_runs
    ADD COLUMN IF NOT EXISTS importer_mode TEXT,
    ADD COLUMN IF NOT EXISTS sessions_added_count INTEGER,
    ADD COLUMN IF NOT EXISTS sessions_updated_count INTEGER,
    ADD COLUMN IF NOT EXISTS sessions_skipped_count INTEGER,
    ADD COLUMN IF NOT EXISTS waveform_chunk_count INTEGER;

ALTER TABLE import_runs
    DROP CONSTRAINT IF EXISTS ck_import_runs_importer_mode,
    DROP CONSTRAINT IF EXISTS ck_import_runs_result_counts;

ALTER TABLE import_runs
    ADD CONSTRAINT ck_import_runs_importer_mode CHECK (
        importer_mode IS NULL OR importer_mode IN ('cpap-parser', 'legacy')
    ),
    ADD CONSTRAINT ck_import_runs_result_counts CHECK (
        COALESCE(sessions_added_count, 0) >= 0
        AND COALESCE(sessions_updated_count, 0) >= 0
        AND COALESCE(sessions_skipped_count, 0) >= 0
        AND COALESCE(waveform_chunk_count, 0) >= 0
    );
