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
| oximetry payload suitability | **fixture-backed negative evidence** (pure-Python) | six SAD files have Pulse/SpO2 channel headers, but every SpO2 sample is missing (`-1`); `test_airsense10_fixture_sad_files_contain_no_usable_oximetry` proves this card cannot validate `session_spo2` persistence |
| events (AHI parity) | **fixture-backed** (`cpap-py`-gated) | `test_fixture_ahi_matches_oscar_summary` vs `oscar_reference/summary.csv`, detailed nights, abs=0.05 |
| therapy aggregates (computed usage) | **fixture-backed** (`cpap-py`-gated) | `test_fixture_computed_usage_matches_oscar_for_detailed_nights` vs OSCAR total time, abs=0.1 h |
| summary-only / ghost nights | **fixture-backed** (`cpap-py`-gated) | `test_fixture_ghost_nights_flagged_not_deleted` (kept + `has_detailed_data is False`) |
| OSCAR reference (export hash) | **fixture-backed (Phase 2)** | manifest pins `expected.import.oscar_reference.export_hash` for committed `oscar_reference/summary.csv` **and** (via `oscar_reference.files`) for `oscar_reference/sessions.csv`; both verified parser-free by `validate_import` in `test_validate_import_oscar_reference_hash_pinned_on_committed_airsense10_fixture` and `test_validate_import_oscar_reference_sessions_csv_hash_pinned_on_committed_airsense10_fixture` |
| `expected.import` **warnings / session_blocks.block_count / therapy_aggregates / events.count + events.types** | **fixture-backed (Phase 2, value-level, `cpap-py`-gated)** | manifest pins these authored-from-the-real-run values; `validate_import(run=...)` verifies them against the normalized `ImportRun` in `test_fixture_semantic_expected_import_matches_normalized_run` (parsed in Linux/Docker; skips where `cpap-py` absent). `events.types` are **SleepLab-normalized** per-type counts (raw cpap-parser labels + loader `Large Leak`), **not** OSCAR parity — see gap audit §12. First committed *value-level* import coverage on a real card |
| `expected.import` **settings.snapshot_count / present / values.therapy_mode** | **fixture-backed (Phase 2, value-level, `cpap-py`-gated)** | loader now maps `pressure_mode` → `therapy_mode` (only); manifest pins `snapshot_count: 1`/`present: true`/`values: {therapy_mode: "APAP"}` for the 3 detailed nights, verified by `test_fixture_semantic_expected_import_matches_normalized_run` + `test_fixture_settings_snapshot_maps_only_therapy_mode` (gap audit §11) |
| settings values (other fields) | **cannot — not in parser schema** | min/max/set pressure, EPR, ramp, humidifier, mask_type are absent from cpap-parser; only `therapy_mode` exists (see §3) |
| `expected.import` identity_hashes | **not committed** | DB-gated, synthetic only |
| DB identity hashes | **not via this fixture** | exercised only by synthetic DB-row tests |
| DB source-file provenance | **fixture-backed (`cpap-py` + DB gated)** | parity harness seeds the production-style 53-file manifest on both paths; legacy finalizes 25 used / 28 skipped and links block/event/channel/settings rows, parser finalizes 0 used / 53 skipped and links none |

- **Now fixture-backed (value-level):** `warnings`, `session_blocks.block_count`,
  `therapy_aggregates` (usage/wall-clock/gap seconds), `events.count` (gap audit
  §9.2), **`events.types`** (SleepLab-normalized per-type counts — gap audit §12),
  and **`settings.therapy_mode`** (gap audit §11) — all authored from and verified
  against the real normalized `ImportRun`.
- **Cannot validate yet:** `settings.values` *beyond* `therapy_mode` (the other
  fields are not in the cpap-parser schema); raw→OSCAR event-type **parity** (the
  pinned `events.types` are SleepLab-normalized, not OSCAR); exact
  `session_blocks.intervals` / ordered timestamped `events` / event `duration_seconds`
  (anonymization-calendar split + event-type vocabulary — deferred, no timestamps
  authored); persisted DB identity hashes for this card; oximetry sample
  persistence (`session_spo2`/`has_spo2`) because all committed SAD samples are
  missing sentinels.
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
- **Parser-backed semantic coverage — LANDED (Phase 2).** The first parser-gated
  semantic `expected.import` values for this card
  (`warnings`/`session_blocks.block_count`/`therapy_aggregates`/`events.count`) are
  now committed and value-verified. The path that got there:
  - **(1) `cpap-py` EDF backend — resolved in Linux/Docker.** On Windows the
    `cpap-parser[resmed]` install fails because `pyedflib` has no wheel and needs
    MSVC Build Tools (gap audit §9.1). The fixture was therefore parsed in a
    `python:3.12-slim` container (manylinux `pyedflib` wheel — no compiler), where
    `cpap_parser`+`cpap_py` import and `ResMedNativeLoader` yields a normalized
    `ImportRun`. **No dependency/lock/tracked files were changed** — the install
    stayed inside the ephemeral container (gap audit §9.2).
  - **(2) Fixture `source_directory` — FIXED (prior phase).** The manifest pins
    `"source_directory": "."` so `_acquire_import_run` resolves the source to the
    committed fixture root. Verified parser-free by
    `test_validate_import_airsense10_source_directory_points_at_committed_root`.
  The authored values were read from the real run and are checked by
  `test_fixture_semantic_expected_import_matches_normalized_run` (`cpap-py`-gated:
  runs in Linux/Docker, skips cleanly on Windows/CI without fabricating a pass). The
  gated contract also still holds via
  `test_validate_import_airsense10_semantic_block_gated_until_parser_backend`. Full
  detail (incl. the exact values and what stays blocked) in the gap audit §9.2.

## 3. The settings-value loader gap (mostly resolved: `therapy_mode` only)

Originally no loader constructed a `SettingsSnapshot`, so `settings.values` was
injected-only. **Now resolved for the one setting cpap-parser exposes:**
`ResMedNativeLoader._session_settings` maps the daily summary's `pressure_mode`
to `therapy_mode` and emits a one-key `SettingsSnapshot` per session (absent when
the mode is `""`/`"Unknown"` — never fabricated). The AirSense 10 fixture's
`settings.therapy_mode` (`"APAP"`) is now committed-fixture-backed (gap audit §11).

What stays blocked, and why:

- **Every other `SettingsSnapshot` field** — `minimum_pressure_cm_h2o`,
  `maximum_pressure_cm_h2o`, `set_pressure_cm_h2o`, `epr_level`/`epr_enabled`,
  `ramp_*`, `humidifier_level`, `mask_type` — is **absent from the cpap-parser
  schema** (`MachineInfo`/`CPAPSessionSummary`/`CPAPSession` carry no such fields;
  `pressure_50`/`pressure_95` are *measured* percentiles, not settings). Mapping
  them needs upstream parser/schema work, not a SleepLab loader change.
- **Persistence is implemented for `therapy_mode`.** The loader snapshot is written
  through `upsert_settings_snapshot` and projected to `sessions.therapy_mode`.
  Missing/`"Unknown"` values are omitted; unsupported fields remain `NULL`.
- The `settings.values` comparator (missing-≠-off semantics, float tolerance,
  snapshot selection) remains exercised by injected runs for the *unmapped* fields.

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
- **`validate_import.warnings` (`codes`/`absent`), `session_blocks.block_count`,
  `therapy_aggregates` (usage/wall-clock/gap seconds), `events.count` + `events.types`**
  — now committed-fixture-backed on the AirSense 10 fixture, authored from and
  verified against the real normalized `ImportRun` (`cpap-py`-gated, parsed in
  Linux/Docker; see §2.2 and gap audit §9.2, §12). `events.types` are
  SleepLab-normalized per-type counts, not OSCAR parity. First *value-level*
  committed import coverage.
- **`validate_import.settings` (`snapshot_count`/`present`/`values.therapy_mode`)** —
  now committed-fixture-backed on the AirSense 10 fixture: the loader maps
  `pressure_mode` → `therapy_mode` (only), pinned as `"APAP"` (`cpap-py`-gated; see
  §2.2 and gap audit §11).

**Injected-only today** (no committed fixture drives them — exercised by an
injected `ImportRun`, a tmp-written manifest, or synthetic DB rows):

- `validate_import.session_blocks.intervals` (timestamped — deferred; only
  `block_count` is committed-fixture-backed, above)
- `validate_import.events` ordered timestamped `events` / `duration_seconds`, and
  raw→OSCAR event-type **parity** (both `count` and per-type `types` are now
  committed-fixture-backed as SleepLab-normalized counts, above)
- `validate_import.settings.values` for fields **other than** `therapy_mode`
  (min/max/set pressure, EPR, ramp, humidifier, mask_type — absent from the
  cpap-parser schema)
- `validate_import.oscar_reference.parity` (numeric parity — still deferred,
  always a skip; the export-**hash** half is now fixture-backed, above)
- `validate_import.identity_hashes` (synthetic upserted DB rows, DB-gated)

**Blocked / deferred:** OSCAR numeric parity (`oscar_reference.parity`),
weighted/time-based summaries, settings-value loader mapping **beyond
`therapy_mode`** (the rest are absent from the cpap-parser schema), full settings
parity beyond the persisted therapy mode, oximetry until a usable safe fixture
exists, parser source-file linkage until real source paths survive normalization,
Lowenstein persistence, ResMed `cpap-parser` production cutover, full-night /
compressed-segment waveform storage, device-time-correction implementation.

### 4.1 AirSense 10 semantic coverage ladder (at a glance)

Where each `expected.import` rung stands on the committed AirSense 10 fixture, so
the next safe step is obvious. "Done" = committed + value-verified against the real
normalized run (`cpap-py`-gated); "deferred/blocked" rows say why.

| Rung | Status | Note |
|---|---|---|
| `oscar_reference` export-hash (summary + sessions csv) | ✅ done | parser-free hash pins |
| `warnings.codes` / `warnings.absent` | ✅ done | §9.2 |
| `session_blocks.block_count` | ✅ done | §9.2 |
| `session_blocks.intervals` (timestamps) | ⛔ deferred | anonymization-calendar split; no timestamps authored |
| `therapy_aggregates` (usage/wall-clock/gap seconds) | ✅ done | §9.2 |
| `events.count` | ✅ done | §9.2 |
| `events.types` (per-type counts) | ✅ done | §12 — **SleepLab-normalized, not OSCAR parity** |
| `events.types` raw→OSCAR **parity** | ⛔ deferred | needs raw→OSCAR enum mapping (stop-and-ask: changes event-type normalization) |
| `events` ordered list / start times / `duration_seconds` | ⛔ deferred | timestamps + duration shape not authored |
| `settings.present` / `snapshot_count` / `values.therapy_mode` | ✅ done | §11 |
| `settings.values` other fields (pressure/EPR/ramp/humidifier/mask) | ⛔ blocked | absent from cpap-parser schema |
| `settings` **persistence** (`therapy_mode`) | ✅ done | snapshot row + `sessions.therapy_mode`; unsupported fields stay `NULL` |
| `identity_hashes` | ⛔ deferred | DB-gated; synthetic rows only |
| `oscar_reference` numeric **parity** | ⛔ deferred | designed (plan §13), not implemented |

Smallest likely-safe next rungs: none remain that are purely additive and
non-stop-and-ask on this fixture — the remaining rungs each need either a deferred
timestamp/vocabulary decision, a schema-absent field, or a stop-and-ask
(persistence / raw→OSCAR normalization / numeric parity). New *fixtures* (a second
ResMed model, oximetry, or a safe Lowenstein sample) are the next breadth step.

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
