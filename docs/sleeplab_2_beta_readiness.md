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

1. **Parser-enabled Linux CI matrix green — CLOSED.**
   The GitHub `CI` `backend` job (`ubuntu-latest`, Postgres 16, `uv sync --extra
   parser`) passes — alpha.24 run: **453 passed, 2 skipped** with the cpap-py
   conformance and cutover-parity suites running. CI was red *only* because the
   separate `version-check` job compared incompatible version formats
   (pyproject's PEP 440 `2.0.0a24` vs npm semver `2.0.0-alpha.24`) and an
   unrelated NOTICE.md document version. `version-check` is now fixed to
   normalize PEP 440 → semver and to compare the real app-version carriers
   (`pyproject.toml`, `package.json`, `frontend/package.json`, `VERSION`, and the
   git tag). NOTICE.md keeps its own third-party-notices version and is no longer
   conflated with the app version. The post-alpha.25 pipeline is green.

2. **Second independent private-card soak — CLOSED.**
   The maintainer ran `scripts/private_card_soak.ps1` against a real private
   ResMed card on `develop/2.0` at `a4b3daf`. The parser-default fresh import
   succeeded with safe aggregate totals of **51 sessions**, **432 events**, and
   **7,396 `waveform_chunks`**; `session_waveform` remained **0**. Exact-snapshot
   re-import was an unchanged/no-op and all four counts stayed stable. Import
   History, Nightly chunk coverage, Event Inspector, and full-night waveform
   checks passed. `resmed_summary_only_day` was an expected non-fatal warning.
   No private filenames, serials, dates, card contents, or report data are
   recorded here.

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

All beta blockers are closed. Parser-enabled Linux CI and version consistency
checks are green, DB-backed failure/cleanup coverage is in place, and the second
independent real-card soak passed with stable idempotent storage and working
chunk-backed API/UI data paths.

**Recommendation: release `v2.0.0-beta.1`.** The cpap-parser default path is
validated across fresh install, parser import, exact-snapshot re-import (no
session/event/chunk duplication or row-waveform bloat), Import History, Nightly
coverage, Event Inspector, full-night waveform, legacy fallback, and
mixed-history protection.

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

## 9. Private-card soak checklist (completed — blocker 2 closed)

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

- [x] fresh parser-default import of the card succeeds
- [x] same-card re-import produces **no duplicate sessions**
- [x] re-import produces **no duplicate events**
- [x] re-import produces **no duplicate `waveform_chunks`** (chunk count stable)
- [x] `session_waveform` is **not** repopulated (stays empty for parser nights)
- [x] Event Inspector windows render for scored events
- [x] full-night waveform view renders
- [x] Nightly data coverage reports chunk-backed waveforms (not row-backed)
- [x] Import History shows the completed run; exact-snapshot re-import reports
      a sensible re-import/unchanged summary

If any check fails, treat it as a beta blocker and do not promote to `beta.1`.

## 10. Automated private-card soak runner

The manual checklist above is now automated by
`scripts/private_card_soak.ps1`. Run it on Windows with Docker and a **real**
ResMed card (or a private local copy). It uses the same `/upload/source/*` path
as the UI, waits for completion, captures aggregate DB counts, exercises the
chunk-backed coverage, Event Inspector, and full-night APIs, then re-imports the
same snapshot. Its JSON report contains no card filenames, serials, dates, or
session identifiers. Card, copy, and report paths inside the repository are
refused.

Use a disposable SleepLab database/user. `-ResetSoakData` is explicit and
destructive: it clears sessions, import history, and machines for the logged-in
user so the first import is genuinely fresh. Without it, existing data is kept;
an already-imported snapshot produces INCONCLUSIVE instead of a false pass.

```powershell
# Prompts securely for the SleepLab login; report stays outside the repository.
.\scripts\private_card_soak.ps1 `
  -CardPath 'E:\' `
  -CopyTo 'C:\tmp\sleeplab-private-card-soak\card' `
  -ResetSoakData `
  -OutputPath 'C:\tmp\sleeplab-private-card-soak\report.json'

# Use an existing private copy without copying it again.
.\scripts\private_card_soak.ps1 `
  -CardPath 'D:\private\resmed-card-copy' `
  -SkipCopy `
  -OutputPath 'D:\private\sleeplab-soak-report.json'
```

The runner automates import status/provenance/history, aggregate session/event/
chunk/legacy-waveform counts, same-card stability, nightly chunk coverage, and
usable Event Inspector/full-night waveform responses. A maintainer may still
visually inspect the rendered screens. Any unavailable automation is a manual
item and exit code 2 (INCONCLUSIVE), never a false pass.

Blocker 2 closed when this runner reported PASS on the second independent real
private card at `a4b3daf`. Only the safe aggregates in §5 are retained in the
repository; the private source and local report remain outside it.
