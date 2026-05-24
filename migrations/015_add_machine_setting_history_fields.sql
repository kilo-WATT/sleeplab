-- Machine setting history fields imported per session.
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS pressure_min NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS pressure_max NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS epr_setting TEXT,
    ADD COLUMN IF NOT EXISTS ramp_setting TEXT;

