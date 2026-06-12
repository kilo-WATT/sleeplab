-- Leak summary statistics previously stored only the mean (avg_leak). The
-- nightly session view conflated this with pressure (it displayed p95_pressure
-- under the leak card), so add a dedicated leak 95th-percentile column. The
-- cpap-parser path populates it from the night's concatenated Leak.2s samples
-- in the same units as avg_leak; the legacy path leaves it NULL (not computed).
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS p95_leak NUMERIC(8,4);
