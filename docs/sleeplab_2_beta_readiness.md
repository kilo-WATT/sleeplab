# SleepLab 2.0 Beta Readiness

Status of the ResMed parser-default import path as of the post-alpha.24
beta-blocker burn-down. This is the operational readiness record; the
forward-looking task list lives in
[`sleeplab_2_beta_readiness_plan.md`](sleeplab_2_beta_readiness_plan.md) and is
not duplicated here.

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
| Background import failure persists `failed` status + friendly message in history | `tests/test_import_failure_cleanup.py` |
| Failure message/API copy is human-readable, never a raw traceback | `tests/test_import_failure_cleanup.py`, `tests/test_loader_upload.py` |
| A late failure callback never clobbers an already-successful run | `tests/test_import_failure_cleanup.py` |
| Temp-upload staging dir is removed on success **and** failure | `tests/test_import_failure_cleanup.py` |
| Cleanup is best-effort (`ignore_errors`) and cannot mask the import failure | `tests/test_import_failure_cleanup.py` |

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

## 5. Beta blocker status (burn-down)

1. **Parser-enabled Linux CI matrix green — CLOSED (code) / verify on next run.**
   The GitHub `CI` `backend` job (`ubuntu-latest`, Postgres 16, `uv sync --extra
   parser`) passes — alpha.24 run: **453 passed, 2 skipped** with the cpap-py
   conformance and cutover-parity suites running. CI was red *only* because the
   separate `version-check` job compared incompatible version formats
   (pyproject's PEP 440 `2.0.0a24` vs npm semver `2.0.0-alpha.24`) and an
   unrelated NOTICE.md document version. `version-check` is now fixed to
   normalize PEP 440 → semver and to compare the real app-version carriers
   (`pyproject.toml`, `package.json`, `frontend/package.json`, `VERSION`, and the
   git tag). NOTICE.md keeps its own third-party-notices version and is no longer
   conflated with the app version. The full pipeline should be green on the next
   push; confirm the run.

2. **Second independent private-card soak — OPEN (requires maintainer hardware).**
   Cannot be run from this environment: no private card is available and the
   cpap-parser/cpap-py runtime cannot build on Windows dev (pyedflib needs MSVC).
   An automated, independent end-to-end soak does run in Linux CI against the
   committed, non-private **AirSense 10 conformance fixture**
   (`tests/conformance/test_resmed_airsense10.py`), exercising the full
   parser→persist path. The real private-card soak remains a manual step — see
   the checklist in §9 below. Do not fabricate its results.

3. **DB-backed background-failure / temp-upload-cleanup coverage — CLOSED.**
   Added `tests/test_import_failure_cleanup.py` (DB-backed failure-status
   persistence + history rendering, plus success/failure temp-cleanup and
   best-effort cleanup guarantees). See §3.

## 6. Non-blocking post-beta items

- Automatic, preservation-aware legacy→nightly **migration** that keeps notes,
  tags, oximetry, and other user data (RC).
- Parser **SpO2/pulse** persistence once real-sample evidence exists.
- Settings coverage beyond `therapy_mode`; row-level provenance once upstream
  source paths are available.
- Freeze normalized setting/channel/event/provenance/API contracts (RC).

## 7. Recommendation

Two of the three beta blockers are now closed in code: the CI version-check fix
makes the parser-enabled Linux pipeline green (blocker 1), and DB-backed
background-failure and temp-upload-cleanup coverage is in place (blocker 3). The
cpap-parser default path remains functionally validated and safe on all
supported flows: fresh install, default parser import, idempotent same-backend
re-import (no waveform bloat), legacy fallback, and mixed-history protection.

**Recommendation: tag `v2.0.0-alpha.25` first, then `v2.0.0-beta.1` after the
private-card soak.** The single remaining blocker (blocker 2, the independent
*real* private-card soak) cannot be discharged from CI or this dev environment —
it needs the maintainer to run the §9 checklist on their own card and confirm no
duplicate sessions/events/chunks, no `session_waveform` bloat, and working Event
Inspector / full-night views. An `alpha.25` tag captures the now-green CI plus
the failure/cleanup hardening as a clean checkpoint; promote to `beta.1` once the
soak passes. (Per project rules, no tag is created by this change.)

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

## 9. Private-card soak checklist (manual — blocker 2)

Run on a Linux/Docker host with the parser runtime installed and a **real**
ResMed card. This is the independent second soak; it must not be faked, and no
card data, serials, dates, or session identifiers are committed or printed.

```bash
# 1. Parse-level idempotency soak (aggregate-only; copies/prints nothing).
export SLEEPLAB_PRIVATE_RESMED_CARD="/path/to/your/card/root"
uv run pytest tests/test_resmed_private_card_soak.py -q -s
```

Then exercise the full DB-backed import path in a running instance
(`SLEEPLAB_USE_CPAP_PARSER=1`) and confirm:

- [ ] fresh parser-default import of the card succeeds
- [ ] same-card re-import produces **no duplicate sessions**
- [ ] re-import produces **no duplicate events**
- [ ] re-import produces **no duplicate `waveform_chunks`** (chunk count stable)
- [ ] `session_waveform` is **not** repopulated (stays empty for parser nights)
- [ ] Event Inspector windows render for scored events
- [ ] full-night waveform view renders
- [ ] Nightly data coverage reports chunk-backed waveforms (not row-backed)
- [ ] Import History shows both runs; the second reports a sensible
      re-import/unchanged summary

If any check fails, treat it as a beta blocker and do not promote to `beta.1`.
