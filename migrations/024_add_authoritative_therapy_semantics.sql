BEGIN;

ALTER TABLE session_blocks
    ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'legacy_inferred',
    ADD COLUMN IF NOT EXISTS therapy_duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS source_reported_duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS recording_duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE session_blocks b
SET
    source_kind = CASE
        WHEN s.provenance_status = 'native_resmed_partial' THEN 'recording_span'
        ELSE 'legacy_inferred'
    END,
    recording_duration_seconds = ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime)))::int,
    therapy_duration_seconds = ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime)))::int
FROM sessions s
WHERE s.id = b.session_id
  AND b.source_kind = 'legacy_inferred'
  AND b.recording_duration_seconds IS NULL;

UPDATE session_blocks
SET therapy_duration_seconds = ROUND(EXTRACT(EPOCH FROM (end_datetime - start_datetime)))::int
WHERE therapy_duration_seconds IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_session_blocks_source_kind'
    ) THEN
        ALTER TABLE session_blocks
            ADD CONSTRAINT ck_session_blocks_source_kind
            CHECK (source_kind IN (
                'resmed_str_mask_interval',
                'recording_span',
                'summary_reported',
                'legacy_inferred'
            ));
    END IF;
END $$;

ALTER TABLE settings_snapshots
    ADD COLUMN IF NOT EXISTS parser_id TEXT,
    ADD COLUMN IF NOT EXISTS parser_version TEXT,
    ADD COLUMN IF NOT EXISTS diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_settings_snapshots_machine_effective_adapter
    ON settings_snapshots (machine_id, effective_at, adapter_id);

ALTER TABLE import_runs
    ADD COLUMN IF NOT EXISTS imported_settings_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS summary_only_day_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS capability_status JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE OR REPLACE VIEW nightly_therapy_aggregates AS
WITH raw_blocks AS (
    SELECT
        s.user_id,
        s.machine_id,
        s.folder_date AS machine_local_date,
        b.id AS block_id,
        b.session_id,
        b.start_datetime,
        b.end_datetime,
        b.source_kind,
        b.therapy_duration_seconds,
        b.source_reported_duration_seconds,
        b.confidence,
        b.validation_status
    FROM session_blocks b
    JOIN sessions s ON s.id = b.session_id

    UNION ALL

    -- Preserve compatibility for legacy and non-ResMed importers that have
    -- not yet started persisting explicit session blocks.
    SELECT
        s.user_id,
        s.machine_id,
        s.folder_date AS machine_local_date,
        NULL::UUID AS block_id,
        s.id AS session_id,
        s.start_datetime,
        s.start_datetime + s.duration_seconds * INTERVAL '1 second' AS end_datetime,
        'recording_span'::VARCHAR(32) AS source_kind,
        s.duration_seconds AS therapy_duration_seconds,
        NULL::INTEGER AS source_reported_duration_seconds,
        'probable'::VARCHAR(16) AS confidence,
        'unvalidated'::VARCHAR(24) AS validation_status
    FROM sessions s
    WHERE NOT EXISTS (
        SELECT 1
        FROM session_blocks b
        WHERE b.session_id = s.id
    )
),
block_candidates AS (
    SELECT
        rb.*,
        BOOL_OR(rb.source_kind = 'resmed_str_mask_interval') OVER (
            PARTITION BY rb.user_id, rb.machine_id, rb.machine_local_date
        ) AS has_source_intervals
    FROM raw_blocks rb
),
selected_blocks AS (
    SELECT *,
           COALESCE(
               therapy_duration_seconds,
               ROUND(EXTRACT(EPOCH FROM (end_datetime - start_datetime)))::int
           ) AS selected_duration_seconds
    FROM block_candidates
    WHERE (has_source_intervals AND source_kind = 'resmed_str_mask_interval')
       OR (NOT has_source_intervals AND source_kind <> 'summary_reported')
)
SELECT
    user_id,
    machine_id,
    machine_local_date,
    MIN(start_datetime) AS start_datetime,
    MAX(end_datetime) AS end_datetime,
    SUM(selected_duration_seconds)::int AS usage_seconds,
    ROUND(EXTRACT(EPOCH FROM (MAX(end_datetime) - MIN(start_datetime))))::int AS wall_clock_seconds,
    GREATEST(
        0,
        ROUND(EXTRACT(EPOCH FROM (MAX(end_datetime) - MIN(start_datetime))))::int
            - SUM(selected_duration_seconds)::int
    ) AS gap_seconds,
    COUNT(*)::int AS block_count,
    CASE
        WHEN BOOL_OR(source_kind = 'resmed_str_mask_interval') THEN 'resmed_str_mask_intervals'
        ELSE 'recording_spans'
    END AS usage_source,
    MAX(source_reported_duration_seconds) AS summary_reported_usage_seconds,
    CASE
        WHEN BOOL_AND(validation_status = 'validated') THEN 'validated'
        WHEN BOOL_OR(validation_status IN ('validated', 'partial')) THEN 'partial'
        WHEN BOOL_OR(validation_status = 'failed') THEN 'failed'
        ELSE 'unvalidated'
    END AS validation_status
FROM selected_blocks
GROUP BY user_id, machine_id, machine_local_date;

COMMIT;
