ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS leak_kind TEXT,
    ADD COLUMN IF NOT EXISTS leak_unit TEXT;

UPDATE sessions
SET leak_kind = 'unintentional',
    leak_unit = 'L/s'
WHERE manufacturer = 'ResMed'
  AND avg_leak IS NOT NULL
  AND (leak_kind IS NULL OR leak_unit IS NULL);
