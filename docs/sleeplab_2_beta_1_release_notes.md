# SleepLab 2.0 beta.1 release notes

**Milestone:** `v2.0.0-beta.1` · **Branch:** `develop/2.0`

SleepLab 2.0 reworks how ResMed SD-card data is imported, stored, and reviewed.
This is the first beta of that line. It is a **hardening milestone, not a
feature expansion** — the goal is to prove the new ResMed import path is safe and
trustworthy for wider testing. The operational readiness record behind this
release lives in [`sleeplab_2_beta_readiness.md`](sleeplab_2_beta_readiness.md);
the forward plan is in
[`sleeplab_2_beta_readiness_plan.md`](sleeplab_2_beta_readiness_plan.md).

## What's new in 2.0 beta.1

### cpap-parser is the default ResMed import path

ResMed imports now run through the **cpap-parser** backend by default
(`SLEEPLAB_USE_CPAP_PARSER=1`). Upload the full SD-card / archive **root** (the
folder containing `STR.edf` and `DATALOG`) through the import screen; SleepLab
detects the source structure, plans the import, and then executes it. `GET /config`
reports the active backend, parser availability/readiness, DATALOG posture,
parser SpO2 status, and source-provenance level.

The older native importer (`importer/import_sessions.py`) is **retained as an
explicit legacy/native fallback**, not deprecated. Set
`SLEEPLAB_USE_CPAP_PARSER=0` and restart to route ResMed imports through it — for
example to keep importing a machine whose existing history was created by that
backend. SleepLab never rewrites existing sessions or mixes the two backends for
one machine; mixed parser/native history is blocked in both directions with a
clear message rather than silently merged.

### Chunk-backed waveform storage

Parser imports write full-night high-rate signals to compressed
**`waveform_chunks`** and low-rate signals to `session_metrics`. The legacy
single-blob `session_waveform` is only a fallback used when no chunks can be
produced, and is cleared once chunks exist — parser imports never bloat it.

### Event Inspector and full-night waveforms from chunks

Both the Event Inspector event-window view and the full-night waveform view read
from `waveform_chunks`. Native (legacy) sessions still display alongside parser
sessions through the shared session/aggregate path.

### Import progress, history, and result summaries

The import UI reports **honest stages** (scan, parser selection, session/event
writes, waveform chunks, finalizing) instead of estimating a percentage it can't
know. The completion card and **Import History** show which importer ran
(`importer_mode`), sessions added or already present, events, waveform chunks,
warnings, and errors when those values were recorded. Background import failures
persist a `failed` status with a human-readable message (never a raw traceback),
and a late failure callback can't clobber an already-successful run. Older
import-history rows render safely with the new fields null.

### Idempotent re-import

Re-importing the same card snapshot is an idempotent no-op: an exact-snapshot
re-import reports `unchanged` with no duplicate sessions, events, or waveform
chunks, and `session_waveform` stays empty for parser nights. A changed snapshot
imports only new work where possible. Delete-then-re-import is allowed (no stale
mixed-history block left behind).

### Private-card soak (safe aggregate summary)

A second independent soak ran `scripts/private_card_soak.ps1` against a **real**
private ResMed card on `develop/2.0` at `a4b3daf`. The fresh parser-default
import succeeded with safe aggregate totals of **51 sessions**, **432 events**,
and **7,396 `waveform_chunks`**, with `session_waveform` at **0**. An
exact-snapshot re-import was an unchanged/no-op and all four counts stayed
stable. Import History, nightly chunk coverage, Event Inspector, and full-night
waveform checks passed. No private filenames, serials, dates, card contents, or
report data are recorded — only these aggregates. See
[`sleeplab_2_beta_readiness.md`](sleeplab_2_beta_readiness.md) §5/§9 for detail.

## Known limitations

These are expected for beta.1 and are **not** blockers:

- **SpO2 / pulse:** the parser path writes no `session_spo2` and reports
  `cpap_parser_oximetry_supported: false`. The schema and legacy importer can
  store oximetry; import it separately via `/upload/oximeter`.
- **Settings coverage:** the parser exposes only `therapy_mode`. Other settings
  stay `NULL` rather than being fabricated.
- **Per-block therapy time:** parser blocks are honest `recording_span`s, not STR
  mask intervals; per-block therapy duration is left `NULL`.
- **Provenance:** parser-consumed categories without stable upstream paths are
  marked consumed without a row-level source-file link
  (`manifest-level-partial`).
- **Backend switching for an existing machine** is intentionally blocked, not
  migrated. Automatic preservation-aware legacy→nightly migration is RC work.
- **DATALOG-only flows** (`/upload/datalog/*`, local DATALOG settings,
  `/import/trigger-local`, `/import/trigger/all`, `/import/webhook/*`) are
  legacy-only and return HTTP 409 while the parser backend is selected. Use the
  root `/upload/source/*` path under the default backend.
- **Single validated family:** ResMed is the validated path. Other manufacturers
  are not in scope for this beta (see non-goals below).

## Upgrade and import cautions

- **Back up your database before upgrading.** Migrations apply automatically at
  API startup; on the self-hosted stack they run when the `app` container boots.
- **A parser-selected import fails clearly** (HTTP 503 at finish; no run is
  created) when the parser runtime is absent. The release Docker image bundles
  the parser; local `uv` users install it with `uv sync --extra parser --group dev`.
- **Don't mix backends on one machine.** If you have existing native history and
  want to keep extending it, stay on `SLEEPLAB_USE_CPAP_PARSER=0`. Switching an
  existing machine to the parser is not auto-migrated yet.
- **Keep card data private.** Don't attach real card files, serial numbers,
  dates, or session exports to bug reports — only the kind of safe aggregates
  shown above.

## Not in this release (non-goals)

beta.1 deliberately excludes large feature work: no SpO2 persistence, no
Lowenstein, no Philips/DreamStation, no Apple Health / wearable expansion, and no
major dashboard rewrite. See
[`sleeplab_2_release_roadmap.md`](sleeplab_2_release_roadmap.md) for the full
beta/RC/stable plan and [`sleeplab_2_beta_2_plan.md`](sleeplab_2_beta_2_plan.md)
for the next small beta scope.

## Reporting feedback

Open an issue at <https://github.com/kilo-WATT/sleeplab/issues>. Helpful details:
your platform, the importer backend (`cpap-parser` or legacy), what `GET /config`
reports, and the relevant `docker compose logs app` excerpt. **Redact private
details first.**
