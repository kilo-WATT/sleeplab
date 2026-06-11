# SleepLab 2.0 — ResMed Normalized-Output Gap Audit (Phase 2)

Status: **Phase 2 bridge audit.** This document inventories exactly what
`ResMedNativeLoader` (`importer/loaders/resmed_native.py`) emits into the
vendor-neutral `ImportRun` model, and uses that to explain *why* the committed
AirSense 10 fixture can today pin OSCAR reference-file **hashes** but cannot yet
assert real fixture-backed **values** for `session_blocks.intervals`,
`therapy_aggregates`, `settings.values`, and `events` through `validate_import`.

It is the honest bridge between the two existing Phase 2 docs:

- `docs/sleeplab_2_fixture_validation_matrix.md` — per-fixture fixture-backed vs
  injected-only inventory.
- `docs/sleeplab_2_import_level_conformance_plan.md` — the `validate_import` /
  `expected.import` contract and comparators.

Nothing here changes production import routing, persistence, schema, waveform
storage, or the ResMed `cpap-parser` cutover. It is analysis + small
documenting tests only.

## 1. Purpose

The `validate_import` comparators for `warnings`, `session_blocks` (count +
intervals), `therapy_aggregates`, `settings` (count/present/values), and `events`
are **implemented and unit-tested** — but only by *injecting* a normalized
`ImportRun` (`validate_import(fixture, run=...)`). The committed AirSense 10
fixture cannot yet drive them. This audit pins down, contract-area by
contract-area, the precise reason: for most blocks it is a **missing
`expected.import` block + an unavailable parser**, and for `settings.values` it
is additionally a **hard loader gap** (no `SettingsSnapshot` is ever
constructed). It then records the smallest safe next steps.

## 2. Current baseline

### 2.1 What is fixture-backed today

Verified against the committed fixtures and tests:

- **OSCAR reference export hashes (parser-free).** The AirSense 10 manifest
  (`tests/conformance/fixtures/resmed_airsense10_001/manifest.json`) pins
  `expected.import.oscar_reference.export_hash` for `oscar_reference/summary.csv`
  and, via the `oscar_reference.files` list, for `oscar_reference/sessions.csv`.
  `validate_import` verifies both with a sha256 over the committed file — no
  parse, no DB, no PHI. This is the **only** committed `expected.import` coverage.
- **Parser-backed real-card facts via *separate* tests** (not via
  `validate_import`): serial identity and the 13-channel signal inventory
  (pure-Python `edf_parser.read_header`, normal suite); AHI parity,
  computed-usage parity, and ghost-night flagging (`cpap-py`-gated) in
  `tests/conformance/test_resmed_airsense10.py`. These assert against
  `oscar_reference/summary.csv` directly, *not* against a normalized `ImportRun`
  comparator.

### 2.2 What is injected-only today

Every parse-observable `validate_import` comparator: `warnings`,
`session_blocks` (`block_count` + `intervals`), `therapy_aggregates`,
`settings` (`snapshot_count`/`present`/`values`), `events`
(`count`/`types`/ordered list), plus the DB-gated `identity_hashes`. Each is
exercised by an injected `ImportRun`, a `tmp_path` manifest, or synthetic DB
rows — no committed fixture drives them.

### 2.3 What is parser-gated today

`validate_import`'s auto-parse acquisition path (`_acquire_import_run`) needs
**both** `cpap-parser` and its `cpap-py` EDF backend. `_import_parser_available()`
requires both `find_spec`s. **Verified in this environment:** `cpap_parser` is
importable but `cpap_py` is **not**, so the EDF payload cannot be decoded and the
auto-parse path returns `(None, "cpap-parser/cpap-py not installed")` — every
parse-dependent block skips. *Do not assume the backend is installed; it varies
by environment.*

## 3. ResMed normalized-output table

For each vendor-neutral contract area on `Session` / `ImportRun`, what
`ResMedNativeLoader` emits today and what blocks fixture-backed assertion.

| Normalized contract area | `expected.import` block | Loader emits today? | Source in cpap-parser / loader | Current test coverage | Fixture-backed readiness | Blocker | Recommended next action |
|---|---|---|---|---|---|---|---|
| `ImportRun.warnings` | `warnings` | **Yes** | `_merge_identity` → `resmed_serial_absent`; `_build_session` → `resmed_summary_only_day`, `resmed_waveform_absent`, flushed to run via `run_warnings.extend` | Injected-only (`validate_import`); codes also asserted parser-free by `_build_session` regression tests | **High** (codes parser-free, no PHI) | No `expected.import.warnings` block in the AirSense 10 manifest; auto-parse needs `cpap-py` | Author a `warnings.codes`/`absent` block once a run is obtainable; codes are the safest semantic pin |
| `Session.blocks` | `session_blocks.block_count` / `.intervals` | **Yes** | `_session_blocks` from `directory.sessions` (non-annotation file-sessions): `start_time`/`end_time`/`file_type` | Injected-only (`validate_import`); span sanity indirectly via computed-usage parity test | `block_count` **medium**; `intervals` **low** | No manifest block; `cpap-py` absent; `intervals` embed (shifted) real timestamps; **timestamp-shift calendar mismatch** (EDF shifted −508d, DATALOG dirs/OSCAR refs on original calendar) | Start with `block_count` per detailed night; defer `intervals` until calendar-rebase + timestamp-safety are settled |
| `Session.settings` | `settings.snapshot_count` / `.present` / `.values` | **No** | *Nothing.* `_build_session` never constructs a `SettingsSnapshot`; `Session.settings` stays `[]` | Comparator injected-only; **loader emits nothing** | `present`/`snapshot_count` would assert *false*/0 (true today); `values` **not ready** | **Loader gap** — no loader builds `SettingsSnapshot`; STR.edf mode/pressure/EPR/ramp are not mapped. `persist.py` confirms it: `therapy_mode`/`mask_type`/`humidity_level` are hardcoded `None` | Map `SettingsSnapshot` from STR settings in a *future* loader change (stop-and-ask if it touches production); until then only `present:false` is honest |
| `Session.signals` | *(no comparator)* | **Yes** (when `include_waveforms`) | `_session_waveforms` → `_HIGH_RATE_CHANNELS` (BRP) + `_LOW_RATE_CHANNELS` (PLD) `SignalChannel`s | Fixture-backed via `test_airsense10_fixture_channel_inventory_matches_classification` (pure-Python, **not** `validate_import`) | n/a (no import-level comparator) | No `signals` comparator in `validate_import`; channel inventory already covered elsewhere | None needed; channel inventory is already fixture-backed by its own test |
| `Session.events` | `events.count` / `.types` / `.events` | **Yes** (scored events always; large-leak only when `include_waveforms`) | `_session_events` from `CPAPSession.events` (`event_type`, `timestamp_sec`→absolute, `duration_sec`); `_large_leak_events` derived from leak signal | Injected-only (`validate_import`); event *counts* indirectly via AHI parity test | `count`/`types` **medium**; ordered `events` **low** | No manifest block; `cpap-py` absent; event **type vocabulary is raw cpap-parser strings** (`"Obstructive Apnea"`, …), not yet mapped to the OSCAR enum; ordered list embeds real timestamps | Start with `count` (and per-type `types`) on detailed nights; defer ordered `events`/timestamps until vocabulary + calendar are settled |
| `Session.waveforms` | *(no comparator)* | **Yes** (when `include_waveforms`) | `_session_waveforms` → `WaveformSegment` metadata (`sample_count`/`sample_rate_hz`, no arrays) | Indirect: `resmed_waveform_absent` warning tested parser-free | n/a (no import-level comparator) | No waveform comparator; full-night/segment storage is deferred + stop-and-ask | None this milestone (waveform storage out of scope) |
| `Session.derived_values` | `therapy_aggregates.*` | **Yes** | `_summary_derived_values`: `summary_reported_usage_hours`, `computed_usage_hours`, `recording_span_hours`, `has_detailed_data`, `ahi` (always); `_signal_metrics` (avg/p95 etc., `include_waveforms` only) | Injected-only (`validate_import`); `computed_usage` parity fixture-backed via separate test | **Medium** | No `therapy_aggregates` manifest block; `cpap-py` absent. `usage_seconds`←`computed_usage_hours`, `wall_clock_seconds`←`recording_span_hours`, `gap_seconds`=span−usage are all derived and observable | Author `therapy_aggregates` per detailed night once a run is obtainable; values are non-timestamped seconds (lower privacy surface) |
| `Session.source_file_ids` / `ImportRun.source_files` / provenance | *(feeds `identity_hashes`)* | **Partial / No** | `ImportRun.source_files = []` ("cpap-parser does not expose a source manifest yet"); `Session.source_file_ids` left `[]`; block `source_file_ids` are synthetic `"{date}:{file_type}:{index}"` strings; `source_session_key`/`source_block_key` ARE emitted | Injected/synthetic-DB only | identity-hash inputs **present** (keys), source-file provenance **absent** | No persisted source-file manifest from the parser; `identity_hashes` also needs Postgres | Leave provenance manifest for a later parser/loader step; `identity_hashes` stays DB-gated |

## 4. The central question, answered

> Why can the fixture pin OSCAR reference hashes but not assert
> `session_blocks.intervals` / `therapy_aggregates` / `settings.values` /
> `events`?

Three layered blockers, in order of how fundamental they are:

1. **Hashes need no run; values do.** `oscar_reference.export_hash` is a sha256
   over a committed CSV — `validate_import` runs it with no `ImportRun`, no parse,
   no DB (`_compare_oscar_reference`). The value comparators all require a
   normalized `ImportRun`. With no injected `run=`, the run must be parsed from
   the card.

2. **The parser/backend is gated and the manifest has no value blocks.**
   - `_acquire_import_run` needs `cpap-parser` **and** `cpap-py`. The EDF backend
     is **absent here** (verified), so the run is `None` and every parse-dependent
     block skips with the acquisition reason. This is environment-dependent, not a
     code defect.
   - The AirSense 10 manifest's `expected.import` contains **only**
     `oscar_reference`. There is no `session_blocks`, `therapy_aggregates`,
     `settings`, or `events` block, so even with a parser present `validate_import`
     would have nothing to compare for them.
   - A real value block would also have to handle the fixture's **anonymization
     calendar split**: `scrub_sdcard.py` shifted EDF timestamps by −508 days but
     left `DATALOG/<YYYYMMDD>` dir names and the OSCAR reference CSVs on the
     original calendar (see `test_resmed_airsense10.py::_summaries_by_oscar_date`).
     Authored interval/event timestamps must account for that.

3. **`settings.values` has an additional hard loader gap.** Even with a parser
   and an authored block, `settings.values` could never pass against this card:
   **no loader constructs a `SettingsSnapshot`.** `grep SettingsSnapshot
   importer/` matches only the dataclass definition in `models.py` and the
   comparator/tests — `ResMedNativeLoader._build_session` never populates
   `Session.settings`, and `persist.py` hardcodes `therapy_mode`/`mask_type`/
   `humidity_level`/`temperature_c` to `None`. The comparator correctly *fails*
   (not fabricates a pass) against an empty snapshot list — but that means the
   only honest committed assertion today is `present: false`. Wiring real settings
   is a loader change, deliberately out of scope (stop-and-ask if it would touch
   production import behavior).

So: `oscar_reference` is *manifest + file* only; the value blocks need *a run*
(parser/backend + an authored block + calendar handling), and `settings.values`
additionally needs *the loader to emit `SettingsSnapshot`*.

## 5. Explicit decisions

- **Do not cut over production ResMed routing** to `cpap-parser`. Out of scope;
  stop-and-ask.
- **Do not change persistence** (`persist.py`) or the database schema.
- **Do not add private fixture data** or new anonymized/real fixtures.
- **Do not claim full validation from injected tests.** Injected runs validate
  comparator *logic*, not the loader's real output; the matrix keeps them labeled
  injected-only.
- **Do not add exact real timestamps/settings** to a manifest unless they are
  already safe and proven. The AirSense 10 timestamps are shifted but still
  real-derived; prefer non-timestamped pins (counts, seconds, warning codes,
  `present:false`) first.

## 6. Recommended next implementation order (conservative)

1. **Make parser-backed fixture tests runnable/gated cleanly.** Keep the
   `importorskip("cpap_parser")` / `importorskip("cpap_py")` gating; ensure the
   `validate_import` auto-parse path is exercised by a `cpap-py`-gated test where
   the backend exists, so a green-but-skipped run is visibly distinct.
2. **Add fixture-backed `warnings` / `session_blocks.block_count` /
   `therapy_aggregates` assertions** *only* where the loader already emits them
   and parser deps are available — these are non-timestamped and lowest-risk
   (warning codes; block counts per detailed night; usage/span/gap seconds).
3. **Map `SettingsSnapshot`** only after confirming `cpap-parser` exposes the
   needed STR settings safely; until then assert `settings.present: false`,
   which is honest. This is the deepest gap and a loader change.
4. **Add fixture-backed `events.count`/`types`** before the ordered `events`
   list; defer timestamped `intervals`/`events` until the normalized event-type
   vocabulary (raw strings → OSCAR enum) and the anonymization-calendar rebase
   are both settled.
5. **Leave persistence, production routing, waveform storage, Lowenstein, and
   OSCAR numeric parity** for later stop-and-ask tasks.

## 7. Cross-references

- `importer/loaders/resmed_native.py` — `_build_session`, `_session_blocks`,
  `_session_events`, `_summary_derived_values`, `_session_waveforms`.
- `importer/conformance.py` — `validate_import`, `_acquire_import_run`,
  `_import_parser_available`, the `_compare_*` comparators.
- `importer/loaders/persist.py` — confirms the settings/oximetry persistence gaps
  (`therapy_mode`/`mask_type` hardcoded `None`).
- `docs/sleeplab_2_fixture_validation_matrix.md` §2.2, §3 — the AirSense 10
  fixture row and the settings-value loader gap.
- `docs/sleeplab_2_import_level_conformance_plan.md` §4–§9 — the comparator
  contract and gating these blockers map onto.
- `tests/test_resmed_import_regressions.py` — the parser-free `_build_session`
  idiom the documenting tests below reuse.

## 8. Parser-backed setup gap (verified this phase)

An attempt to author the first parser-gated **semantic** `expected.import`
values for the committed AirSense 10 fixture confirmed the path is *ready in
concept* but blocked by two concrete, recorded setup gaps. Neither is a code
defect; both are environment/fixture wiring that must be closed before any
semantic `warnings`/`session_blocks.block_count`/`therapy_aggregates`/
`events.count` value can be honestly authored. No semantic values were added.

1. **`cpap-py` EDF backend absent.** In the environment used here `cpap_parser`
   **is** importable but `cpap_py` is **not** (`importlib.util.find_spec`), so
   `_import_parser_available()` is `False`. The three `cpap-py`-gated tests in
   `tests/conformance/test_resmed_airsense10.py` (AHI / computed-usage /
   ghost-night parity) `importorskip` and skip; the pure-Python serial/channel
   tests still pass. `validate_import`'s auto-parse acquisition therefore returns
   `(None, "cpap-parser/cpap-py not installed")` and every parse-dependent block
   skips. Decoding `STR.edf`/`DATALOG` to build a normalized `ImportRun` needs the
   backend, so the EDF payload cannot be decoded here.

2. **Fixture layout vs `source_directory` default.** Even with the backend
   installed, `_acquire_import_run` parses `root / manifest.get("source_directory",
   "source")`. The AirSense 10 fixture is **non-standard**: `DATALOG/` and
   `STR.edf` live at the fixture **root**, and the manifest carries **no**
   `source_directory` key. So the default `source/` path is empty —
   `ResMedNativeLoader().detect(<root>/source)` returns `[]` (auto-parse would skip
   with `"no ResMed device detected in fixture source"`), whereas
   `detect(<root>)` *does* find the device. Closing this is a one-line manifest
   addition (`"source_directory": "."`), but it is only worth taking together with
   (1) and an authored block — adding it alone would not enable any check.

**What this means.** Current committed fixture-backed coverage for this card
remains the parser-free `oscar_reference` hash pins (§2.1). The loader-ready
semantic candidates stay **injected-only** until a `cpap-py`-equipped dev/CI
environment exists *and* the fixture exposes its source via `source_directory`.
The honest gated contract is pinned parser-free by
`tests/test_conformance.py::test_validate_import_airsense10_semantic_block_gated_until_parser_backend`:
a semantic block injected into a copy of the committed manifest **skips** with one
of the acquisition reasons above (never a fabricated pass) while the
`oscar_reference` hash still verifies.

**Next action (unchanged, now precise):** in a `cpap-py`-equipped environment,
(a) add `"source_directory": "."` to the AirSense 10 manifest (or pass an injected
run), (b) obtain the normalized `ImportRun`, (c) read the safe aggregate facts
(warning codes, per-night block counts, usage/span/gap seconds, event counts), and
(d) author those `expected.import` blocks from the verified run. Do **not**
fabricate any value while the backend is unavailable.
