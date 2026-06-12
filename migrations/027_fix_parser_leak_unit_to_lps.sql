-- The cpap-parser ResMed path persisted leak summary/sample values that are
-- numerically the raw Leak.2s channel (liters/second, identical to the legacy
-- importer) but tagged them leak_unit = 'L/min'. That mislabel made every leak
-- figure (card, p95, night chart, therapy score, PDF, AI summary, trends) read
-- ~60x too low, because the L/min-normalizing consumers treated an already-L/min
-- value as needing no scaling.
--
-- The stored numbers are correct (L/s); only the unit label was wrong. Relabel
-- existing parser sessions to the truthful unit so display normalizes them
-- (x60) like legacy L/s nights. No value rewrite is needed — sessions.avg_leak,
-- sessions.p95_leak and session_metrics.leak all already hold L/s magnitudes.
UPDATE sessions
SET leak_unit = 'L/s'
WHERE leak_unit = 'L/min';
