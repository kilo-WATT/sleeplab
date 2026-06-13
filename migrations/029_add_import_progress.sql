-- Durable, coarse-grained progress for long parser-backed imports.
ALTER TABLE import_runs
    ADD COLUMN IF NOT EXISTS current_stage TEXT,
    ADD COLUMN IF NOT EXISTS current_message TEXT,
    ADD COLUMN IF NOT EXISTS files_processed INTEGER,
    ADD COLUMN IF NOT EXISTS files_total INTEGER,
    ADD COLUMN IF NOT EXISTS sessions_processed INTEGER,
    ADD COLUMN IF NOT EXISTS sessions_total INTEGER;

ALTER TABLE import_runs
    DROP CONSTRAINT IF EXISTS ck_import_runs_progress_counts;

ALTER TABLE import_runs
    ADD CONSTRAINT ck_import_runs_progress_counts CHECK (
        COALESCE(files_processed, 0) >= 0
        AND COALESCE(files_total, 0) >= 0
        AND COALESCE(sessions_processed, 0) >= 0
        AND COALESCE(sessions_total, 0) >= 0
    );
