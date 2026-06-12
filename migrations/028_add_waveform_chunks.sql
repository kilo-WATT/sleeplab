BEGIN;

CREATE TABLE IF NOT EXISTS waveform_chunks (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    import_run_id       UUID REFERENCES import_runs(id) ON DELETE SET NULL,
    source_file_id      UUID REFERENCES import_source_files(id) ON DELETE SET NULL,
    signal_name         TEXT NOT NULL,
    unit                TEXT NOT NULL,
    sample_rate_hz      DOUBLE PRECISION NOT NULL,
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ NOT NULL,
    chunk_index         INTEGER NOT NULL,
    sample_count        INTEGER NOT NULL,
    encoding            TEXT NOT NULL,
    payload             BYTEA NOT NULL,
    uncompressed_bytes  INTEGER NOT NULL,
    compressed_bytes    INTEGER NOT NULL,
    source_ref          TEXT,
    adapter_id          TEXT NOT NULL,
    parser_id           TEXT,
    parser_version      TEXT,
    provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_waveform_chunks_rate CHECK (sample_rate_hz > 0),
    CONSTRAINT ck_waveform_chunks_interval CHECK (end_time >= start_time),
    CONSTRAINT ck_waveform_chunks_index CHECK (chunk_index >= 0),
    CONSTRAINT ck_waveform_chunks_sample_count CHECK (sample_count > 0),
    CONSTRAINT ck_waveform_chunks_sizes
        CHECK (uncompressed_bytes > 0 AND compressed_bytes > 0),
    CONSTRAINT ck_waveform_chunks_encoding
        CHECK (encoding IN ('float32-le-zlib-v1')),
    CONSTRAINT uq_waveform_chunks_session_signal_index
        UNIQUE (session_id, signal_name, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_waveform_chunks_session_signal_time
    ON waveform_chunks (session_id, signal_name, start_time, end_time);

CREATE INDEX IF NOT EXISTS idx_waveform_chunks_import_run
    ON waveform_chunks (import_run_id);

COMMIT;
