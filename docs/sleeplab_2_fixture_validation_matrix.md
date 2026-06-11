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
`docs/sleeplab_2_alpha_7_checklist.md` §3 (import-level conformance depth),
`docs/sleeplab_2_resmed_normalized_output_gap_audit.md` (the loader-output
bridge: what `ResMedNativeLoader` emits per `expected.import` block and the
per-block blocker to fixture-backed *values*), and `importer/conformance.py`
(the comparators).

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
  ground-truth the tests assert against) and `oscar_reference/sessions.csv`
  (per-session detail). Both committed and anonymized. **Both are now
  integrity-pinned** — the manifest pins a sha256 `export_hash` for `summary.csv`
  and, via the `oscar_reference.files` list, for `sessions.csv`; the CSVs are
  still read at runtime for values, but silent drift is now caught.
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
| OSCAR reference (export hash) | **fixture-backed (Phase 2)** | manifest pins `expected.import.oscar_reference.export_hash` for committed `oscar_reference/summary.csv` **and** (via `oscar_reference.files`) for `oscar_reference/sessions.csv`; both verified parser-free by `validate_import` in `test_validate_import_oscar_reference_hash_pinned_on_committed_airsense10_fixture` and `test_validate_import_oscar_reference_sessions_csv_hash_pinned_on_committed_airsense10_fixture` |
| `expected.import` (warnings / session_blocks / settings / events / therapy_aggregates / identity_hashes) | **not committed** | manifest has no `expected.import`; non-standard schema |
| settings values | **cannot — loader gap** | ResMed loader maps no `SettingsSnapshot` (see §3) |
| DB identity hashes | **not via this fixture** | exercised only by synthetic DB-row tests |

- **Cannot validate yet:** the parse-dependent `expected.import` comparators for
  this card (no `session_blocks`/`settings`/`events`/`therapy_aggregates` block,
  non-standard manifest); per-setting values (loader gap); persisted DB identity
  hashes for this card. *(OSCAR export-hash integrity is now pinned — see below.)*
- **Privacy concerns / unknowns:** anonymized real card. Do **not** expose real
  values; do **not** overwrite the data files. Safe, already-committed summary
  facts (from `README.md`/`oscar_reference`): 40 summary nights, 3 detailed
  DATALOG nights, per-day AHI range `0.000`…`2.408`, serial pseudonym
  `SN-FIXTURE-AirSense10-001`. The `manifest.json` `nights_included: 5` vs 3
  on-disk detailed nights is a *recorded, intentionally-unfixed* discrepancy (the
  on-disk DATALOG is authoritative).
- **Recommended next action — DONE (Phase 2):** the OSCAR reference `export_hash`
  is now pinned in the manifest for **both** committed anonymized exports —
  `oscar_reference/summary.csv` (top-level `export_hash`) and
  `oscar_reference/sessions.csv` (an `oscar_reference.files` entry) — and exercised
  through `validate_import`'s parser-free hash check, moving the `oscar_reference`
  comparator from injected-only to committed-fixture-backed with no PHI, parse, or
  DB. This touched the *manifest* (metadata) plus a backward-compatible comparator
  generalization (support a `files` list), never the anonymized data files. Next
  candidates remain gated: a standard `expected.import` block (parse-dependent) and
  `settings.values` (loader gap).
- **Parser-backed semantic coverage — setup path, one blocker remaining.**
  Authoring the first parser-gated semantic `expected.import` values
  (`warnings`/`session_blocks.block_count`/`therapy_aggregates`/`events.count`)
  for this card needs a normalized `ImportRun`. Two setup gaps were identified; one
  is now closed:
  - **(1) `cpap-py` EDF backend — still absent (the remaining blocker).**
    `cpap_parser` imports but `cpap_py` does not, so `_import_parser_available()`
    is `False` and auto-parse skips with `"cpap-parser/cpap-py not installed"`.
    `cpap_py` is the ResMed EDF reader pulled by the `cpap-parser[resmed]` extra
    (pinned git fork in root `requirements.txt`; a production dep via the
    Dockerfile, deliberately absent from `pyproject.toml`/`uv.lock` and CI's
    `uv sync --group dev`). Dependency files were **not** changed (adding it would
    rebuild git-sourced native deps in CI and activate the gated tests — not
    low-risk); see the gap audit §9 for the operator-authorized install path.
  - **(2) Fixture `source_directory` — FIXED this phase.** The manifest now pins
    `"source_directory": "."` so `_acquire_import_run` resolves the source to the
    committed fixture root (the card's `DATALOG/`/`STR.edf` live there, not under a
    `source/` subdir). Verified parser-free by
    `test_validate_import_airsense10_source_directory_points_at_committed_root`
    (`tests/test_conformance.py`) and by the detection half of
    `test_fixture_normalized_import_run_acquired_via_loader`
    (`tests/conformance/test_resmed_airsense10.py`, the run half `cpap-py`-gated).
  No semantic values were added. The gated contract holds in both environments via
  `test_validate_import_airsense10_semantic_block_gated_until_parser_backend`.
  Full detail in `docs/sleeplab_2_resmed_normalized_output_gap_audit.md` §8–§9.

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
- **`validate_import.oscar_reference.export_hash`** — now committed-fixture-backed
  on the AirSense 10 fixture for **both** the per-day `summary.csv` and the
  per-session `sessions.csv` (parser-free hash checks; see §2.2).

**Injected-only today** (no committed fixture drives them — exercised by an
injected `ImportRun`, a tmp-written manifest, or synthetic DB rows):

- `validate_import.warnings` (`codes`/`absent`)
- `validate_import.session_blocks` (`block_count` **and** `intervals`)
- `validate_import.therapy_aggregates` (`usage`/`wall_clock`/`gap`/`block_count`)
- `validate_import.settings` (`snapshot_count`/`present`/`values`)
- `validate_import.events` (`count`/`types`/ordered `events`)
- `validate_import.oscar_reference.parity` (numeric parity — still deferred,
  always a skip; the export-**hash** half is now fixture-backed, above)
- `validate_import.identity_hashes` (synthetic upserted DB rows, DB-gated)

**Blocked / deferred (unchanged):** OSCAR numeric parity (`oscar_reference.parity`),
weighted/time-based summaries, settings-value loader mapping, Lowenstein
persistence, ResMed `cpap-parser` production cutover, full-night /
compressed-segment waveform storage, device-time-correction implementation.

## 5. Recommended Phase 2 order (conservative)

1. **DONE — Pin the airsense10 OSCAR `export_hash`** for `summary.csv` and add a
   parser-free `validate_import` test → first committed-fixture-backed
   `oscar_reference` coverage. Lowest risk: no parse, no DB, no PHI, manifest-only.
   Plus a read-only `summarize_import_blocks` reporting helper.
2. **DONE — Pin the twin `sessions.csv` `export_hash`** via the manifest's
   `oscar_reference.files` list (a small backward-compatible comparator
   generalization), with committed-fixture + mismatch/missing-file tests. Same
   lowest-risk profile: a sha256 over an already-committed redistributable export,
   no parse/DB/PHI, no production behavior change.
3. **DEFERRED (deliberately) — `warnings.absent` on the synthetic fixture.** A
   parser-free `warnings.absent` would only assert wiring against an injected empty
   run (no loader actually runs in this environment — `cpap-py`/`cpap-parser` are
   not installed, so it cannot be parse-verified), *and* it would flip the
   deliberately-encoded "synthetic fixture stays import-block-free" invariant
   asserted by three tests (`test_validate_import_passes_and_skips_when_import_block_absent`,
   `test_expected_import_block_is_optional_and_absent_today`,
   `test_summarize_import_blocks_empty_without_import_block`). Marginal evidentiary
   value at the cost of the backward-compat witness — not taken.
4. **Defer** parse-dependent committed expectations (`session_blocks.intervals`,
   `settings.values`, `events`, `therapy_aggregates`) until either the synthetic
   EDFs are confirmed `cpap-py`-decodable or the airsense10 fixture gains a
   standard `expected.import` block, and (for `settings.values`) until the loader
   maps `SettingsSnapshot`s. None can be honestly value-verified while no parser is
   installed. The per-block loader-emission status and the exact blocker for each
   parse-dependent value are audited in
   `docs/sleeplab_2_resmed_normalized_output_gap_audit.md`: `session_blocks`
   (count) / `therapy_aggregates` / `events` (count) are *loader-ready* (gated
   only on a run + an authored manifest block), whereas `settings.values` is
   blocked by the loader gap (no `SettingsSnapshot` is constructed) and the
   timestamped `intervals`/`events` lists by the anonymization-calendar split.
   For the AirSense 10 card specifically, the `source_directory` setup gap is now
   **closed** (`"source_directory": "."` pinned), so obtaining a run is gated only
   by the absent `cpap-py` backend — see §2.2 and the gap audit §8–§9.

## 6. Inspecting fixture-backed status (reporting)

Two read-only aids surface this matrix's distinctions without reading test source:

- `summarize_import_blocks(fixture_dir, result)` labels each requested
  `expected.import` block **passed / skipped / failed**.
- The conformance CLI gained an opt-in `--import` flag
  (`python -m importer.conformance <fixture> --import`) that runs `validate_import`
  parser-free and prints the per-block status alongside the planning result. It
  degrades gracefully on the non-standard AirSense 10 fixture (reports
  "planning validation unavailable: …" instead of crashing) and still emits the
  import section. Neither changes production behavior.

For how contributors can supply *new* safe evidence without posting raw CPAP
data, see `docs/sleeplab_2_validation_inputs.md`.
