# SleepLab 2.0 Beta Readiness

Status of the ResMed parser-default import path as of the alpha.24 beta-hardening
pass. This is the operational readiness record; the forward-looking task list
lives in [`sleeplab_2_beta_readiness_plan.md`](sleeplab_2_beta_readiness_plan.md)
and is not duplicated here.

This milestone is **hardening, not feature expansion**. No SpO2, Lowenstein,
Philips/DreamStation, Apple Health, AI, or dashboard work is included. The goal
is to prove the current ResMed cpap-parser default path is safe for beta.

## 1. Supported default path

- ResMed imports go through the **cpap-parser** backend by default
  (`SLEEPLAB_USE_CPAP_PARSER` defaults to `1`). Unchanged from alpha.22/alpha.23.
- Upload the full SD-card / archive **root** through `/upload/source/*`
  (start → batch → inspect → finish). Detection always runs through the
  structural loader registry; only execution differs by backend.
- `GET /config` reports the active backend, parser availability/readiness,
  DATALOG posture, parser SpO2 status, and source-provenance level.
- Parser imports write full-night high-rate signals to **`waveform_chunks`**
  (compressed) and low-rate signals to `session_metrics`. `session_waveform` is
  only a fallback when no chunks can be produced, and is cleared once chunks
  exist — parser imports never bloat it.
- A parser-selected import fails clearly (HTTP 503 at finish; the run is never
  created) when the parser runtime is absent, with instructions to install it or
  select the legacy fallback.

## 2. Legacy / native fallback

- Set `SLEEPLAB_USE_CPAP_PARSER=0` and restart to route ResMed imports through
  the legacy/native `importer/import_sessions.py` subprocess. This is the
  rollback path and the parity oracle; it is **retained, not deprecated**.
- The legacy DATALOG-only flows (`/upload/datalog/*`, local DATALOG settings,
  `/import/trigger-local`, `/import/trigger/all`, `/import/webhook/*`) return
  HTTP 409 while the parser backend is selected and operate normally under the
  fallback. `/config` reports `datalog_import_backend: "legacy"`.
- UI/history labels describe the fallback as legacy/native and never present it
  as the recommended path. `importer_mode` on each run records which backend ran.

## 3. Validated flows (this pass)

Validated against a throwaway Postgres 16 with migrations applied from an empty
database. Parser-runtime tests (cpap-py) are exercised by the Linux CI matrix.

| Area | Evidence |
| --- | --- |
| Fresh DB starts; all migrations apply | full suite run on an empty PG, migrations auto-applied |
| Default = cpap-parser; legacy on flag | `tests/test_config.py` |
| Mixed parser/native history blocked **both directions** | `tests/test_beta_hardening.py`, `tests/test_loader_upload.py` |
| Same-backend re-import is **not** blocked | `tests/test_beta_hardening.py` |
| Re-import idempotent: no duplicate sessions | `tests/test_resmed_import_regressions.py::test_upsert_session_reimport_is_idempotent` |
| Re-import: no duplicate waveform chunks; `session_waveform` stays empty | `tests/test_waveform_chunks.py::test_parser_import_persistence_prefers_chunks_and_api_reads_windows` |
| Exact-snapshot re-import is a clear no-op (`status: unchanged`) | `reusable_import_run` + `tests/test_sessions.py` |
| Delete then re-import is allowed (no stale block) | `tests/test_sessions.py::test_reset_clears_import_history_so_reimport_is_not_blocked` |
| Chunk-backed Event Inspector window + full-night waveform read | `tests/test_waveform_chunks.py` |
| Native (legacy) sessions still display alongside parser sessions | shared `sessions`/aggregate path; `session_waveform` fallback covered |
| Older import-history rows render safely (null new fields) | `tests/test_beta_hardening.py` |
| Legacy fallback DATALOG/webhook/trigger behavior | `tests/test_local_import.py` (pinned to fallback) |

## 4. Known limitations (expected, not blockers)

- **SpO2**: schema- and legacy-importer-ready, but the parser path writes no
  `session_spo2` and reports `cpap_parser_oximetry_supported: false`. Import
  oximetry separately via `/upload/oximeter`.
- **Settings**: the parser exposes only `therapy_mode`; other settings stay
  `NULL` rather than being fabricated.
- **Per-block therapy time**: parser blocks are honest `recording_span`s, not
  STR mask intervals; per-block therapy duration is left `NULL`.
- **Provenance**: parser-consumed categories without stable upstream paths are
  marked consumed without a row-level source-file link (`manifest-level-partial`).
- **Switching an existing machine between backends** is intentionally blocked,
  not migrated; it remains explicit RC work.

## 5. Beta blockers (remaining before v2.0.0-beta.1)

- Confirm the parser-enabled **Linux CI** matrix (`uv sync --extra parser`) is
  green, including the conformance and cutover-parity suites that cannot build
  on Windows dev machines (pyedflib needs MSVC there).
- Complete a second independent **private-card soak** with the aggregate-only
  harness (`tests/test_resmed_private_card_soak.py`).
- Database-backed route coverage for **background failure status** and
  **temporary-upload cleanup** on the Linux/Postgres/parser matrix.

## 6. Non-blocking post-beta items

- Automatic, preservation-aware legacy→nightly **migration** that keeps notes,
  tags, oximetry, and other user data (RC).
- Parser **SpO2/pulse** persistence once real-sample evidence exists.
- Settings coverage beyond `therapy_mode`; row-level provenance once upstream
  source paths are available.
- Freeze normalized setting/channel/event/provenance/API contracts (RC).

## 7. Recommendation

The cpap-parser default path is functionally validated and safe for beta on the
supported flows: fresh install, default parser import, same-backend re-import
(idempotent, no waveform bloat), legacy fallback, and mixed-history protection.
Existing native- and parser-imported sessions both continue to display, and
older import-history rows render safely.

This work is a reasonable candidate to tag as **v2.0.0-alpha.24** after review.
Do **not** advance to **v2.0.0-beta.1** until the three section-5 blockers clear —
chiefly a green parser-enabled Linux CI run and the second private-card soak.

## 8. Testing notes

```bash
# Backend (Linux / CI parity)
uv sync --extra parser --group dev
uv run ruff check importer/ tests/
TEST_DATABASE_URL=postgresql://.../throwaway uv run pytest -v --tb=short

# Parser-backed subset
uv run pytest tests/conformance/test_resmed_airsense10.py tests/test_resmed_cutover_db_parity.py -q
```

- DB tests skip cleanly without `TEST_DATABASE_URL`; parser tests skip cleanly
  without the cpap-py runtime.
- On **Windows** dev machines a few `tests/test_local_import.py` cases fail
  because `/data` does not resolve under a drive anchor (`Path("/data")` vs a
  resolved `C:\data\...`). These pass on Linux/Docker, where `/data` is the real
  volume mount; they are an environment artifact, not a product defect.
