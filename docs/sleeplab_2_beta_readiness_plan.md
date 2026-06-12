# SleepLab 2.0 Beta Readiness Plan

## 1. What works now

The ResMed `cpap-parser` path is the SleepLab 2.0 target. The old native
importer remains available as a fallback and comparison reference.

The parser path stores one nightly session with child recording blocks,
preserves the full device-scored event list, replaces generated child data on
same-card re-import, and records each import attempt separately. Re-importing
the same card does not add duplicate sessions, blocks, settings, events,
metrics, waveform rows, signal channels, derived values, or nightly summaries.

Nightly therapy usage uses the best available value in this order:

1. true mask or therapy intervals;
2. source-reported therapy duration;
3. computed usage;
4. recording span only as a fallback.

`SLEEPLAB_USE_CPAP_PARSER=1` selects the parser path for the root-folder
`/source` upload flow. `/config` reports the selected ResMed backend and whether
the `cpap_parser` and `cpap_py` modules are installed. The public default remains
the legacy path.

## 2. What intentionally changed in 2.0

- A canonical session is one machine-local therapy night, not one row per EDF
  recording.
- Recording fragments belong in `session_blocks`.
- The full device-scored event list is retained. Counts can be slightly higher
  than the legacy importer, which clipped events to recording windows.
- The parser currently persists only `therapy_mode` from settings. Pressure
  limits, EPR, ramp, humidifier, temperature, and mask type remain unavailable
  because the parser schema does not expose them.
- Full-night high-rate waveform storage is deferred. Beta keeps event-window
  waveform storage.

## 3. What users may need to delete and re-import

Users who imported ResMed data through the legacy path should clear their
existing SleepLab session data before switching to the parser path. Automatic
cross-path deletion is intentionally not implemented because legacy sessions
may own notes, tags, oximetry, or other user data that must not be silently
discarded.

The supported alpha/beta reset workflow is:

1. Back up the database.
2. Use **Delete all session data** in the application (`DELETE /sessions/all`).
3. Set `SLEEPLAB_USE_CPAP_PARSER=1` only in an environment where the parser
   runtime is installed.
4. Restart SleepLab and confirm `/config` reports
   `resmed_import_backend: "cpap-parser"` and `cpap_parser_available: true`.
5. Re-import from the original SD card through the root-folder source flow.

Deleting sessions cascades to session blocks, events, metrics, waveform rows,
signal channels, derived values, session-linked settings, notes, tags, and
oximetry. Machine and import-history records remain as an audit trail. This is a
destructive workflow, so the backup step is required.

## 4. What remains before beta

- Package and test the pinned `cpap-parser[resmed]`/`cpap-py` runtime in clean
  installs and normal CI.
- Add route-level database coverage for parser success, parser failure, durable
  run status, and temporary-upload cleanup.
- Decide whether `/datalog/*` is retired, parser-routed, or explicitly
  legacy-only.
- Implement a preservation-aware legacy-to-nightly migration, or keep the
  documented clear-and-reimport requirement for beta.
- Run a second independent ResMed card soak.
- Validate real SpO2/pulse data. The committed fixture contains only missing
  SpO2 samples.
- Make parser-consumed source-file dispositions understandable even where
  upstream `cpap-parser` cannot provide real source paths.

## 5. What remains before RC

- Freeze the normalized setting, channel, event, and API contracts.
- Test upgrade, backup, restore, rollback, cancellation, concurrency, and crash
  recovery on realistic self-hosted installations.
- Preserve notes, tags, oximetry, and other user-owned data through any automatic
  legacy migration.
- Validate reports, Therapy Score, adherence, trends, and AI inputs against the
  final normalized model.
- Publish an exact capability-based supported-device matrix.

## 6. What is explicitly out of scope for beta

- Lowenstein persistence; it remains a separate validation track.
- Broad unsupported-machine claims.
- Full-night waveform row storage or a new waveform BLOB/segment schema.
- Fabricating settings not exposed by `cpap-parser`.
- Claiming oximetry support before a fixture with real SpO2 data passes.
- Complete row-level source links before upstream parser support exists.
