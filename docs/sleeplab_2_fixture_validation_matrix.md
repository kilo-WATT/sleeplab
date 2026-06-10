# SleepLab 2.0 Fixture Validation Matrix (Phase 2)

Status: **Phase 2 audit — fixture-backed vs injected-only coverage.** This
document inventories every committed conformance fixture and records, per
fixture, exactly what is validated today by a *real fixture* versus what is only
exercised by an *injected normalized `ImportRun`* (or a tmp-written manifest, or
synthetic DB rows). It is the honest baseline for moving import-level
conformance from "comparators implemented with injected tests" to "committed
safe fixtures assert `expected.import` behavior where loader/fixture evidence
honestly supports it."

It is conservative by construction:

- If a check is only exercised by an injected run / tmp manifest / synthetic DB
  row, it is listed as **injected-only**, not fixture-backed.
- If a check is fixture-backed, the exact fixture **and** test are named.
- No private values are exposed. Anonymized real-card figures are quoted only
  where they are already committed in the repo (the fixture's `README.md` /
  `oscar_reference/`), and only as high-level/safe summaries.

Cross-references: `docs/sleeplab_2_import_level_conformance_plan.md` (the
`validate_import` / `expected.import` contract),
`docs/sleeplab_2_alpha_7_checklist.md` §3 (import-level conformance depth), and
`importer/conformance.py` (the comparators).

## 1. Committed fixtures

Exactly **two** conformance fixtures are committed:

| # | Fixture path | Kind | Redistribution | Manufacturer / family | Manifest | Source files | OSCAR reference |
|---|---|---|---|---|---|---|---|
| 1 | `fixtures/conformance/synthetic-resmed-minimal/` | synthetic | repository | ResMed / AirSense 10 (structural) | yes (standard schema) | yes (STR.edf, Identification.tgt, DATALOG/20260601 CSL+EVE+PLD) | no (`oscar_version`/`oscar_export_hash` = null) |
| 2 | `tests/conformance/fixtures/resmed_airsense10_001/` | anonymized (real card) | permitted (serial replaced, timestamps shifted, reviewed) | ResMed / AirSense 10 AutoSet | yes (**non-standard** schema; see §2.2) | yes (Identification.tgt, STR.edf 40-night summary, DATALOG 3 detailed nights BRP/PLD/EVE/CSL/SAD + .crc) | yes (`oscar_reference/summary.csv` + `sessions.csv`, anonymized, committed) |

No other fixtures exist under `fixtures/conformance/` or
`tests/conformance/fixtures/`. No OSCAR source, OSCAR archive, raw card binaries,
real serials, or PHI are committed — the only "oscar" tracked files are the two
anonymized reference CSVs in fixture #2.

## 2. Per-fixture detail

### 2.1 `synthetic-resmed-minimal` (synthetic, repository)

- **Fixture id:** `synthetic-resmed-minimal`
- **Kind / redistribution:** synthetic / `repository`; `anonymization.reviewed:
  true`. No patient or real device data.
- **Manifest:** standard schema (`fixture_id`, `schema_version`, `fixture_kind`,
  `redistribution`, `anonymization`, `source_hash`, `reference`, `expected`).
  Passes `validate_manifest_metadata`.
- **Committed source files:** `STR.edf`, `Identification.tgt`,
  `DATALOG/20260601/` (`*_CSL.edf`, `*_EVE.edf`, `*_PLD.edf`). **No BRP**
  (`waveform_files: 0`), **no SAD/oximetry**.
- **OSCAR reference:** none.
- **Tests using it:** `tests/test_conformance.py` —
  `test_synthetic_resmed_fixture_matches_manifest` (planning) plus the absence /
  diagnostics / coverage tests, and it is the base fixture copied into `tmp_path`
  by `_write_import_manifest(...)` for the injected-run `validate_import` tests.

| Capability | Status | Backing |
|---|---|---|
| detection | **fixture-backed** | `validate_fixture` (`matched`, `device_count`, `executable`, `source_file_count=5`) |
| identity | **fixture-backed** (partial) | `validate_fixture` (`manufacturer=ResMed`, `family=AirSense 10`) |
| capabilities | **fixture-backed** | `validate_fixture` (`identity`/`sessions`/`settings`/`source_manifest`) |
| source manifest (file count) | **fixture-backed** | `validate_fixture` (`source_file_count`) |
| coverage summary | **fixture-backed** | `validate_fixture` (`therapy_days`, `estimated_session_blocks`, `event_files`, `waveform_files=0`, `settings_files`, `oximetry_files`) |
| planning diagnostics | **fixture-backed** | `test_manifest_expected_diagnostics_passes_when_warning_code_present` (removes STR in a tmp copy → `resmed_missing_str`) |
| waveform absence diagnostics | **fixture-backed** (planning) | `test_waveform_coverage_absence_is_detected` (harness *catches* a false `waveform_files` claim) |
| import warnings / session blocks / therapy aggregates / settings / events / oscar_reference / identity hashes | **not committed** | no `expected.import` block; `validate_import(fixture)` passes-and-skips |

- **Cannot validate yet:** any `expected.import` comparator from the *committed*
  manifest (no `expected.import`); event-window waveforms (no BRP); OSCAR
  reference (none); per-setting values, real therapy aggregates (would need a
  parse, and the synthetic EDFs are hand-built — not confirmed `cpap-py`-decodable).
- **Privacy:** none (synthetic).
- **Recommended next action:** candidate for the first committed, **parser-free**
  `expected.import` additions that are backward-compatible (skip cleanly without
  `cpap-parser`/`cpap-py`): e.g. `warnings.absent` (STR is present, so
  `resmed_missing_str` should be absent). Anything needing a real parse
  (`session_blocks.intervals`, `settings.values`, `events`,
  `therapy_aggregates`) must first confirm the synthetic EDFs decode through
  `ResMedNativeLoader.import_data_with_directory` — **unverified**, so deferred.

### 2.2 `resmed_airsense10_001` (anonymized real card, permitted)

- **Fixture id (manifest):** `resmed_airsense10_fixture_001`.
- **Kind / redistribution:** anonymized real AirSense 10 card; `redistribution:
  permitted`. Serial replaced (`SN-FIXTURE-AirSense10-001`); every EDF timestamp
  shifted (`timestamp_shift_days: -508`). `README.md` documents provenance and
  forbids overwriting the data files with a raw card.
- **Manifest:** **non-standard schema** — it carries provenance/anonymization
  metadata and `expected_serial`, but **no `expected` block**, so it is *not*
  consumed by `validate_fixture` / `validate_manifest_metadata` / `validate_import`
  today. It is read directly by the conformance tests for the serial,
  anonymization shift, etc.
- **Committed source files:** `Identification.tgt`, `STR.edf` (40-night summary
  history), `DATALOG/` for **3 detailed nights** (`20260506`, `20260517`,
  `20260528`) each with BRP/PLD/EVE/CSL/SAD `.edf` + `.crc`.
- **OSCAR reference:** `oscar_reference/summary.csv` (per-day rollup, the
  ground-truth the tests assert against) and `oscar_reference/sessions.csv`. Both
  committed and anonymized. **No `export_hash` is committed** — the CSV is read
  at runtime for values, not yet integrity-pinned.
- **Tests using it:**
  - `tests/test_resmed_import_regressions.py::test_airsense10_fixture_channel_inventory_matches_classification`
    — **pure-Python** (`edf_parser.read_header`, no `cpap-py`), runs in the
    normal suite.
  - `tests/conformance/test_resmed_airsense10.py`:
    `test_fixture_serial_parsed_from_identity_tgt` (runs wherever `cpap-parser`
    is installed — pure-Python `.tgt` fallback);
    `test_fixture_ahi_matches_oscar_summary`,
    `test_fixture_ghost_nights_flagged_not_deleted`,
    `test_fixture_computed_usage_matches_oscar_for_detailed_nights`
    (**`cpap-py`-gated**; `importorskip` → skip with a visible reason when the
    EDF backend is absent).

| Capability | Status | Backing |
|---|---|---|
| identity (serial) | **fixture-backed** (parser, no `cpap-py`) | `test_fixture_serial_parsed_from_identity_tgt` (scrubbed serial; never `"Unknown"`) |
| signal channel inventory | **fixture-backed** (pure-Python, normal suite) | `test_airsense10_fixture_channel_inventory_matches_classification` (13 channels decoded; `>=5 Hz → waveform` classification, no misclassification) |
| events (AHI parity) | **fixture-backed** (`cpap-py`-gated) | `test_fixture_ahi_matches_oscar_summary` vs `oscar_reference/summary.csv`, detailed nights, abs=0.05 |
| therapy aggregates (computed usage) | **fixture-backed** (`cpap-py`-gated) | `test_fixture_computed_usage_matches_oscar_for_detailed_nights` vs OSCAR total time, abs=0.1 h |
| summary-only / ghost nights | **fixture-backed** (`cpap-py`-gated) | `test_fixture_ghost_nights_flagged_not_deleted` (kept + `has_detailed_data is False`) |
| OSCAR reference (export hash) | **not yet** | reference CSV committed but no `export_hash` pinned; not run through `validate_import` |
| `expected.import` (warnings / session_blocks / settings / events / therapy_aggregates / identity_hashes) | **not committed** | manifest has no `expected.import`; non-standard schema |
| settings values | **cannot — loader gap** | ResMed loader maps no `SettingsSnapshot` (see §3) |
| DB identity hashes | **not via this fixture** | exercised only by synthetic DB-row tests |

- **Cannot validate yet:** anything through `validate_import` (no `expected.import`,
  non-standard manifest); OSCAR export-hash integrity (no committed hash);
  per-setting values (loader gap); persisted DB identity hashes for this card.
- **Privacy concerns / unknowns:** anonymized real card. Do **not** expose real
  values; do **not** overwrite the data files. Safe, already-committed summary
  facts (from `README.md`/`oscar_reference`): 40 summary nights, 3 detailed
  DATALOG nights, per-day AHI range `0.000`…`2.408`, serial pseudonym
  `SN-FIXTURE-AirSense10-001`. The `manifest.json` `nights_included: 5` vs 3
  on-disk detailed nights is a *recorded, intentionally-unfixed* discrepancy (the
  on-disk DATALOG is authoritative).
- **Recommended next action:** the safest honest upgrade is to **pin the OSCAR
  reference `export_hash`** for the already-committed `oscar_reference/summary.csv`
  and exercise it through `validate_import`'s parser-free hash check — moving the
  `oscar_reference` comparator from injected-only to committed-fixture-backed
  without any PHI, parse, or DB. This touches the *manifest* (metadata), never the
  anonymized data files, and the fixture's privacy/redistribution status is clear.

## 3. The settings-value loader gap (why `settings.values` stays injected-only)

`grep SettingsSnapshot importer/loaders/` matches **only**
`importer/loaders/models.py` (the dataclass definition). No loader —
`resmed_native.py` included — constructs a `SettingsSnapshot`. So although the
`settings.values` comparator (missing-≠-off semantics, float tolerance, snapshot
selection) is fully implemented and tested, it can only ever be exercised by an
**injected** snapshot-bearing run. Against a real card it would find an empty
snapshot list and fail/skip, never fabricate a pass. Wiring real
`SettingsSnapshot` values is a **loader** change, out of scope for this milestone
(and a stop-and-ask item if it touches production import behavior).

## 4. Coverage summary: fixture-backed vs injected-only

**Fixture-backed today** (a committed fixture + a named test):

- Planning-level: detection, identity, capabilities, file-count coverage,
  planning diagnostics, waveform-absence detection (synthetic, `validate_fixture`).
- Parser-backed real-card: serial identity, signal channel inventory
  (pure-Python), AHI parity, computed-usage parity, ghost-night flagging
  (airsense10; the last three `cpap-py`-gated against the committed OSCAR CSV).

**Injected-only today** (no committed fixture drives them — exercised by an
injected `ImportRun`, a tmp-written manifest, or synthetic DB rows):

- `validate_import.warnings` (`codes`/`absent`)
- `validate_import.session_blocks` (`block_count` **and** `intervals`)
- `validate_import.therapy_aggregates` (`usage`/`wall_clock`/`gap`/`block_count`)
- `validate_import.settings` (`snapshot_count`/`present`/`values`)
- `validate_import.events` (`count`/`types`/ordered `events`)
- `validate_import.oscar_reference.export_hash` (tests write a tmp CSV, not a
  committed fixture)
- `validate_import.identity_hashes` (synthetic upserted DB rows, DB-gated)

**Blocked / deferred (unchanged):** OSCAR numeric parity (`oscar_reference.parity`),
weighted/time-based summaries, settings-value loader mapping, Lowenstein
persistence, ResMed `cpap-parser` production cutover, full-night /
compressed-segment waveform storage, device-time-correction implementation.

## 5. Recommended Phase 2 order (conservative)

1. **Pin the airsense10 OSCAR `export_hash`** and add a parser-free
   `validate_import` test → first committed-fixture-backed `oscar_reference`
   coverage. Lowest risk: no parse, no DB, no PHI, manifest-only.
2. **Add a parser-free `warnings.absent` `expected.import`** to the synthetic
   fixture (skips cleanly without the parser) — only if it does not overclaim.
3. **Defer** parse-dependent committed expectations (`session_blocks.intervals`,
   `settings.values`, `events`, `therapy_aggregates`) until either the synthetic
   EDFs are confirmed `cpap-py`-decodable or the airsense10 fixture gains a
   standard `expected.import` block, and (for `settings.values`) until the loader
   maps `SettingsSnapshot`s.
