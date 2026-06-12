# SleepLab 2.0 Beta Readiness Plan

## 1. What works now

The `cpap-parser` ResMed path is the SleepLab 2.0 target. Root-folder `/source`
imports select it with `SLEEPLAB_USE_CPAP_PARSER=1`. Same-card parser re-imports
replace normalized data without duplication while retaining a durable
`import_runs` record for every attempt.

Docker installs the parser runtime. Local `uv` users install the same pin with:

```bash
uv sync --extra parser --group dev
```

Linux CI installs that extra, verifies `cpap_parser` and `cpap_py`, and runs the
parser-backed suite. Tests skip clearly on hosts without the optional runtime.
`/config` reports backend selection, parser availability/readiness, DATALOG
posture, parser SpO2 status, and source-provenance level. A parser-selected
import fails before creating an import run when the runtime is absent.

## 2. What intentionally changed in 2.0

- A canonical session is one machine-local therapy night.
- Recording fragments belong in `session_blocks`.
- The full device-scored event list is retained.
- Nightly usage chooses mask intervals, source-reported therapy, computed usage,
  then recording span.
- Parser settings contain only values exposed upstream, currently
  `therapy_mode`.
- Beta keeps event-window waveform storage.

## 3. Delete and re-import policy

Beta uses an explicit reset policy for backend switching. `/source` detects an
existing ResMed history from the opposite backend and returns HTTP 409 before
creating a new import run. It does not silently duplicate nights or delete
notes, tags, oximetry, and other user-owned data.

To switch:

1. Back up the database.
2. Use **Delete all session data** (`DELETE /sessions/all`).
3. Select one backend and restart SleepLab.
4. Confirm `/config` reports the expected backend and readiness.
5. Re-import the full card through `/source`.

Automatic preservation-aware migration remains RC work.

## 4. What remains before beta.1

- Run a second independent private-card soak with the aggregate-only harness.
- Obtain real SpO2/pulse evidence and implement parser persistence before
  claiming parser oximetry support.
- Run the new parser-enabled GitHub CI workflow and keep it green.
- Complete database-backed route coverage for background failure status and
  temporary-upload cleanup in the Linux/Postgres/parser matrix.

## 5. What remains before RC

- Preserve notes, tags, oximetry, and other user data through automatic
  legacy-to-nightly migration.
- Freeze normalized setting, channel, event, provenance, and API contracts.
- Test upgrade, backup/restore, rollback, cancellation, concurrency, and crash
  recovery.
- Validate reports, Therapy Score, adherence, trends, and AI inputs.
- Publish an exact capability-based supported-device matrix.

## 6. Out of scope for beta

- Lowenstein persistence.
- Unsupported-machine claims.
- Full-night waveform/BLOB storage.
- Fabricated settings or source-file links.
- Parser SpO2 support without real evidence.
- Complete row-level provenance before upstream source paths exist.

## 7. Parser-backed tests

```bash
uv sync --extra parser --group dev
uv run pytest tests/conformance/test_resmed_airsense10.py -q
uv run pytest tests/test_resmed_cutover_db_parity.py -q
```

The DB suite also needs `TEST_DATABASE_URL` pointing to a throwaway Postgres
database. Never use a production database.

## 8. Private-card soak

The soak reads a private card in place, parses it twice, and prints only
aggregate counts. It copies no files and writes no report:

```powershell
$env:SLEEPLAB_PRIVATE_RESMED_CARD = "D:\private\card"
uv run pytest tests/test_resmed_private_card_soak.py -q -s
```

## 9. DATALOG posture

`/datalog/*`, local DATALOG settings, scheduler triggers, and uploader webhooks
remain legacy-only for beta. They return HTTP 409 while
`SLEEPLAB_USE_CPAP_PARSER=1`; use `/source`. `/config` reports
`datalog_import_backend: "legacy"` and current availability.

## 10. SpO2 status

The schema and legacy importer can store SpO2/pulse. The parser path has no
validated real-sample fixture and writes no `session_spo2` rows. `/config`
reports `cpap_parser_oximetry_supported: false`. This is schema-ready,
parser-evidence-blocked behavior, not a support claim.

## 11. Source provenance status

The upload manifest remains exact. Exact references such as `STR.edf` link
normally. Parser-consumed categories without stable upstream paths are marked
used with `consumed_without_row_link`; row-level links remain empty instead of
using synthetic paths. `/config` reports `manifest-level-partial`.
