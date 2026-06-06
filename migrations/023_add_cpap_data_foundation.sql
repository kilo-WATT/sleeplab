BEGIN;

CREATE TABLE cpap_machines (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    manufacturer          TEXT,
    family                TEXT,
    model                 TEXT,
    product_code          TEXT,
    serial_number         TEXT,
    firmware_version      TEXT,
    data_format_version   TEXT,
    adapter_id            TEXT NOT NULL,
    adapter_version       TEXT,
    identity_key          TEXT NOT NULL,
    identity_confidence   TEXT NOT NULL DEFAULT 'none',
    support_status        TEXT NOT NULL DEFAULT 'unknown',
    validation_status     TEXT NOT NULL DEFAULT 'unvalidated',
    source_identity       JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_cpap_machines_identity_confidence
        CHECK (identity_confidence IN ('none', 'weak', 'probable', 'strong', 'exact')),
    CONSTRAINT ck_cpap_machines_support_status
        CHECK (support_status IN ('supported', 'validated', 'experimental', 'detected_only', 'unsupported', 'unknown')),
    CONSTRAINT ck_cpap_machines_validation_status
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed')),
    CONSTRAINT uq_cpap_machines_identity_key UNIQUE (user_id, identity_key)
);
CREATE INDEX idx_cpap_machines_user_id ON cpap_machines (user_id);
CREATE UNIQUE INDEX uq_cpap_machines_adapter_serial
    ON cpap_machines (user_id, adapter_id, serial_number)
    WHERE serial_number IS NOT NULL AND BTRIM(serial_number) <> '';

CREATE TABLE import_runs (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    machine_id               UUID REFERENCES cpap_machines(id) ON DELETE SET NULL,
    adapter_id               TEXT NOT NULL,
    adapter_version          TEXT,
    source_type              TEXT NOT NULL,
    source_fingerprint       TEXT NOT NULL,
    import_fingerprint       TEXT,
    source_label             TEXT,
    status                   TEXT NOT NULL DEFAULT 'pending',
    validation_status        TEXT NOT NULL DEFAULT 'unvalidated',
    identity_confidence      TEXT,
    detected_manufacturer    TEXT,
    detected_family          TEXT,
    detected_capabilities    JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    skipped_files            JSONB NOT NULL DEFAULT '[]'::jsonb,
    imported_session_count   INTEGER NOT NULL DEFAULT 0,
    imported_block_count     INTEGER NOT NULL DEFAULT 0,
    imported_event_count     INTEGER NOT NULL DEFAULT 0,
    imported_channel_count   INTEGER NOT NULL DEFAULT 0,
    started_at               TIMESTAMPTZ,
    completed_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_import_runs_status
        CHECK (status IN ('pending', 'running', 'success', 'partial', 'failed', 'cancelled')),
    CONSTRAINT ck_import_runs_validation_status
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed'))
);
CREATE INDEX idx_import_runs_user_created ON import_runs (user_id, created_at DESC);
CREATE INDEX idx_import_runs_machine_created ON import_runs (machine_id, created_at DESC);
CREATE INDEX idx_import_runs_source_fingerprint ON import_runs (user_id, source_fingerprint);

CREATE TABLE import_source_files (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_run_id      UUID NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    relative_path      TEXT NOT NULL,
    size_bytes         BIGINT NOT NULL,
    content_hash       TEXT,
    parser_role        TEXT NOT NULL DEFAULT 'unknown',
    disposition        TEXT NOT NULL DEFAULT 'unknown',
    parser_component   TEXT,
    warning_state      JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_state        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_import_source_files_disposition
        CHECK (disposition IN ('used', 'skipped', 'unknown', 'failed')),
    CONSTRAINT uq_import_source_files_path UNIQUE (import_run_id, relative_path)
);
CREATE INDEX idx_import_source_files_run_role ON import_source_files (import_run_id, parser_role);

ALTER TABLE sessions
    ADD COLUMN machine_id UUID REFERENCES cpap_machines(id) ON DELETE SET NULL,
    ADD COLUMN import_run_id UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    ADD COLUMN source_session_key TEXT,
    ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'legacy_unknown';

INSERT INTO cpap_machines (
    user_id,
    manufacturer,
    serial_number,
    adapter_id,
    identity_key,
    identity_confidence,
    support_status,
    validation_status,
    source_identity
)
SELECT DISTINCT ON (
    s.user_id,
    CASE
        WHEN NULLIF(BTRIM(s.device_serial), '') IS NOT NULL
            THEN 'legacy-session-v1:serial:' || LOWER(BTRIM(s.device_serial))
        ELSE 'legacy-session-v1:unknown'
    END
)
    s.user_id,
    NULLIF(BTRIM(s.manufacturer), ''),
    NULLIF(BTRIM(s.device_serial), ''),
    'legacy-session-v1',
    CASE
        WHEN NULLIF(BTRIM(s.device_serial), '') IS NOT NULL
            THEN 'legacy-session-v1:serial:' || LOWER(BTRIM(s.device_serial))
        ELSE 'legacy-session-v1:unknown'
    END,
    CASE WHEN NULLIF(BTRIM(s.device_serial), '') IS NOT NULL THEN 'probable' ELSE 'none' END,
    'unknown',
    'unvalidated',
    jsonb_build_object('backfill', 'migration-023')
FROM sessions s
ORDER BY
    s.user_id,
    CASE
        WHEN NULLIF(BTRIM(s.device_serial), '') IS NOT NULL
            THEN 'legacy-session-v1:serial:' || LOWER(BTRIM(s.device_serial))
        ELSE 'legacy-session-v1:unknown'
    END,
    s.updated_at DESC
ON CONFLICT (user_id, identity_key) DO NOTHING;

UPDATE sessions s
SET
    machine_id = m.id,
    source_session_key = s.session_id,
    provenance_status = CASE
        WHEN s.duration_seconds < 0 THEN 'legacy_invalid_duration'
        ELSE 'legacy_backfilled'
    END
FROM cpap_machines m
WHERE m.user_id = s.user_id
  AND m.adapter_id = 'legacy-session-v1'
  AND m.identity_key = CASE
      WHEN NULLIF(BTRIM(s.device_serial), '') IS NOT NULL
          THEN 'legacy-session-v1:serial:' || LOWER(BTRIM(s.device_serial))
      ELSE 'legacy-session-v1:unknown'
  END;

CREATE UNIQUE INDEX uq_sessions_machine_source_key
    ON sessions (machine_id, source_session_key)
    WHERE machine_id IS NOT NULL AND source_session_key IS NOT NULL;
DROP INDEX IF EXISTS uq_sessions_user_session_id;
CREATE INDEX idx_sessions_machine_id ON sessions (machine_id);
CREATE INDEX idx_sessions_import_run_id ON sessions (import_run_id);

CREATE TABLE session_blocks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    import_run_id      UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    source_block_key   TEXT NOT NULL,
    block_kind         TEXT NOT NULL DEFAULT 'therapy',
    start_datetime     TIMESTAMPTZ NOT NULL,
    end_datetime       TIMESTAMPTZ NOT NULL,
    source_file_ids    UUID[] NOT NULL DEFAULT '{}',
    confidence         TEXT NOT NULL DEFAULT 'none',
    validation_status  TEXT NOT NULL DEFAULT 'unvalidated',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_session_blocks_interval CHECK (end_datetime >= start_datetime),
    CONSTRAINT ck_session_blocks_confidence
        CHECK (confidence IN ('none', 'weak', 'probable', 'strong', 'exact')),
    CONSTRAINT ck_session_blocks_validation
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed')),
    CONSTRAINT uq_session_blocks_source_key UNIQUE (session_id, source_block_key)
);
CREATE INDEX idx_session_blocks_session_start ON session_blocks (session_id, start_datetime);

INSERT INTO session_blocks (
    session_id,
    source_block_key,
    start_datetime,
    end_datetime,
    confidence,
    validation_status
)
SELECT
    id,
    COALESCE(source_session_key, session_id) || ':legacy-block:' || block_index,
    start_datetime,
    start_datetime + (GREATEST(duration_seconds, 0) * INTERVAL '1 second'),
    CASE WHEN duration_seconds < 0 THEN 'none' ELSE 'probable' END,
    CASE WHEN duration_seconds < 0 THEN 'failed' ELSE 'unvalidated' END
FROM sessions
ON CONFLICT (session_id, source_block_key) DO NOTHING;

CREATE TABLE settings_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    machine_id          UUID NOT NULL REFERENCES cpap_machines(id) ON DELETE CASCADE,
    session_id          UUID REFERENCES sessions(id) ON DELETE CASCADE,
    import_run_id       UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    effective_at        TIMESTAMPTZ NOT NULL,
    normalized_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    vendor_settings     JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_names        JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_file_ids     UUID[] NOT NULL DEFAULT '{}',
    adapter_id          TEXT NOT NULL,
    confidence          TEXT NOT NULL DEFAULT 'none',
    validation_status   TEXT NOT NULL DEFAULT 'unvalidated',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_settings_snapshots_confidence
        CHECK (confidence IN ('none', 'weak', 'probable', 'strong', 'exact')),
    CONSTRAINT ck_settings_snapshots_validation
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed'))
);
CREATE INDEX idx_settings_snapshots_machine_effective
    ON settings_snapshots (machine_id, effective_at DESC);

CREATE TABLE signal_channels (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    import_run_id       UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    source_file_id      UUID REFERENCES import_source_files(id) ON DELETE SET NULL,
    normalized_name     TEXT NOT NULL,
    source_name         TEXT NOT NULL,
    unit                TEXT,
    sample_rate_hz      DOUBLE PRECISION,
    channel_kind        TEXT NOT NULL,
    value_kind          TEXT NOT NULL DEFAULT 'sample',
    leak_kind           TEXT,
    scale_metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    adapter_id          TEXT NOT NULL,
    confidence          TEXT NOT NULL DEFAULT 'none',
    validation_status   TEXT NOT NULL DEFAULT 'unvalidated',
    is_derived          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_signal_channels_kind
        CHECK (channel_kind IN ('summary', 'low_rate', 'high_rate', 'waveform', 'derived', 'experimental')),
    CONSTRAINT ck_signal_channels_confidence
        CHECK (confidence IN ('none', 'weak', 'probable', 'strong', 'exact')),
    CONSTRAINT ck_signal_channels_validation
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed')),
    CONSTRAINT uq_signal_channels_source_name
        UNIQUE (session_id, normalized_name, source_name)
);
CREATE INDEX idx_signal_channels_session ON signal_channels (session_id);

ALTER TABLE session_events
    ADD COLUMN source_event_key TEXT,
    ADD COLUMN source_event_type TEXT,
    ADD COLUMN import_run_id UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    ADD COLUMN source_file_id UUID REFERENCES import_source_files(id) ON DELETE SET NULL,
    ADD COLUMN adapter_id TEXT,
    ADD COLUMN confidence TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'unvalidated';

UPDATE session_events
SET
    source_event_key = 'legacy:' || id,
    source_event_type = event_type,
    adapter_id = 'legacy-session-v1'
WHERE source_event_key IS NULL;

ALTER TABLE session_events
    ALTER COLUMN source_event_key SET NOT NULL,
    ALTER COLUMN source_event_type SET NOT NULL;

CREATE UNIQUE INDEX uq_session_events_source_key
    ON session_events (session_id, source_event_key);

CREATE TABLE derived_values (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    machine_id         UUID REFERENCES cpap_machines(id) ON DELETE SET NULL,
    session_id         UUID REFERENCES sessions(id) ON DELETE CASCADE,
    import_run_id      UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    key                TEXT NOT NULL,
    value              JSONB,
    unit               TEXT,
    method             TEXT NOT NULL,
    method_version     TEXT,
    input_refs         JSONB NOT NULL DEFAULT '[]'::jsonb,
    adapter_id         TEXT,
    validation_status  TEXT NOT NULL DEFAULT 'unvalidated',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_derived_values_validation
        CHECK (validation_status IN ('unvalidated', 'partial', 'validated', 'failed')),
    CONSTRAINT uq_derived_values_session_key_method
        UNIQUE (session_id, key, method)
);
CREATE INDEX idx_derived_values_session ON derived_values (session_id);
CREATE INDEX idx_derived_values_machine_key ON derived_values (machine_id, key);

COMMIT;
