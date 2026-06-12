-- SleepLab 2.0: nightly therapy usage should reflect the best available therapy
-- signal, never a recording span when a better number exists.
--
-- Migration 024 selected per-night usage by a single rule: if a night had any
-- ``resmed_str_mask_interval`` block it summed those (authoritative therapy),
-- otherwise it summed whatever non-summary blocks existed -- which, for the
-- cpap-parser path's detailed nights, are ``recording_span`` blocks. That meant a
-- detailed parser night reported its wall-clock recording span (e.g. 89,820s on
-- the AirSense 10 fixture) even though the device's own STR-reported therapy time
-- (~69,600s) was already persisted next to it in
-- ``source_reported_duration_seconds``. The span is a usage *proxy*; the reported
-- therapy is the real number.
--
-- This migration replaces the view with an explicit therapy-usage priority,
-- evaluated per machine-local night:
--
--   1. true mask/therapy intervals      -- SUM(therapy) over resmed_str_mask_interval blocks
--   2. source-reported therapy          -- MAX(source_reported_duration_seconds) (per-night STR usage)
--   3. computed parser/therapy usage     -- SUM(therapy_duration_seconds) over remaining real blocks
--   4. recording span (last resort)      -- SUM of wall-clock recording extents
--
-- ``usage_source`` now names which tier won, so consumers and the parity harness
-- can tell authoritative therapy from a recording-span proxy at a glance.
--
-- This is intentionally NOT a behavior change for the legacy ResMed path: legacy
-- nights carry ``resmed_str_mask_interval`` blocks, so tier 1 still wins and the
-- legacy total is unchanged. Block-less nights (legacy/non-ResMed importers, and
-- the parser's summary-only nights) fall through the session-row UNION exactly as
-- before; their ``duration_seconds`` lands in tier 3 (computed) instead of tier 4
-- (recording span), which is the same number but a more honest label. The visible
-- effect is on the cpap-parser path's *detailed* nights, which now prefer the
-- device-reported therapy time over the recording span.
--
-- No schema/column changes: the view's output columns are byte-for-byte the same
-- (user_id, machine_id, machine_local_date, start_datetime, end_datetime,
-- usage_seconds, wall_clock_seconds, gap_seconds, block_count, usage_source,
-- summary_reported_usage_seconds, validation_status). Only ``usage_seconds`` and
-- ``usage_source`` change value, and only where a better therapy number exists.

BEGIN;

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
    -- not yet started persisting explicit session blocks. The session's
    -- duration_seconds already holds the most authoritative therapy time the
    -- importer could derive (computed EDF usage, or STR-reported usage for
    -- summary-only nights), so it is carried as therapy_duration_seconds and
    -- resolves in the "computed" tier below.
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
night_blocks AS (
    SELECT
        rb.*,
        ROUND(EXTRACT(EPOCH FROM (rb.end_datetime - rb.start_datetime)))::int AS span_seconds
    FROM raw_blocks rb
),
night_usage AS (
    SELECT
        user_id,
        machine_id,
        machine_local_date,
        MIN(start_datetime) AS start_datetime,
        MAX(end_datetime) AS end_datetime,
        COUNT(*)::int AS block_count,
        -- Tier 1: authoritative per-mask therapy intervals (legacy STR blocks).
        SUM(therapy_duration_seconds)
            FILTER (WHERE source_kind = 'resmed_str_mask_interval') AS mask_interval_seconds,
        -- Tier 2: device-reported therapy carried on the night's blocks. It is a
        -- per-night value repeated on each block, so MAX collapses it.
        MAX(source_reported_duration_seconds) AS source_reported_seconds,
        -- Tier 3: computed therapy on any remaining real blocks (and the
        -- block-less session fallback's duration_seconds).
        SUM(therapy_duration_seconds)
            FILTER (WHERE source_kind NOT IN ('resmed_str_mask_interval', 'summary_reported'))
            AS computed_therapy_seconds,
        -- Tier 4: recording-span proxy -- wall-clock extent of recording blocks.
        SUM(COALESCE(therapy_duration_seconds, span_seconds))
            FILTER (WHERE source_kind = 'recording_span') AS recording_span_seconds,
        ROUND(EXTRACT(EPOCH FROM (MAX(end_datetime) - MIN(start_datetime))))::int AS wall_clock_seconds,
        MAX(source_reported_duration_seconds) AS summary_reported_usage_seconds,
        CASE
            WHEN BOOL_AND(validation_status = 'validated') THEN 'validated'
            WHEN BOOL_OR(validation_status IN ('validated', 'partial')) THEN 'partial'
            WHEN BOOL_OR(validation_status = 'failed') THEN 'failed'
            ELSE 'unvalidated'
        END AS validation_status
    FROM night_blocks
    GROUP BY user_id, machine_id, machine_local_date
)
SELECT
    user_id,
    machine_id,
    machine_local_date,
    start_datetime,
    end_datetime,
    COALESCE(
        mask_interval_seconds,
        source_reported_seconds,
        computed_therapy_seconds,
        recording_span_seconds,
        0
    )::int AS usage_seconds,
    wall_clock_seconds,
    GREATEST(
        0,
        wall_clock_seconds - COALESCE(
            mask_interval_seconds,
            source_reported_seconds,
            computed_therapy_seconds,
            recording_span_seconds,
            0
        )::int
    ) AS gap_seconds,
    block_count,
    CASE
        WHEN mask_interval_seconds IS NOT NULL THEN 'resmed_str_mask_intervals'
        WHEN source_reported_seconds IS NOT NULL THEN 'source_reported_therapy'
        WHEN computed_therapy_seconds IS NOT NULL THEN 'computed_usage'
        ELSE 'recording_spans'
    END AS usage_source,
    summary_reported_usage_seconds,
    validation_status
FROM night_usage;

COMMIT;
