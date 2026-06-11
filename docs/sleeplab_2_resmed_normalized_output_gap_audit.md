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
| `Session.settings` | `settings.snapshot_count` / `.present` / `.values` | **Partial (updated — see §11)** | `_build_session` → `_session_settings` now emits one snapshot with `therapy_mode` (← `pressure_mode`) when a real mode exists; nothing else (parser exposes no other setting) | `therapy_mode` fixture-backed (`cpap-py`-gated) + parser-free unit tests; `values` for the rest still injected-only | `therapy_mode` **ready & landed**; min/max/set pressure/EPR/ramp/humidifier/mask_type **not in cpap-parser schema** | **Done for `therapy_mode` (§11).** Remaining fields need upstream parser/schema work; `persist.py` still hardcodes `therapy_mode`/`mask_type`/`humidity_level` `None` so mapping is conformance-only |
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

3. **`settings.values` had an additional hard loader gap — now partially closed
   (§11).** At the time of this audit no loader constructed a `SettingsSnapshot`,
   so `settings.values` could not pass against this card. §11 closes this for the
   one setting cpap-parser exposes: `_build_session` → `_session_settings` now maps
   `pressure_mode` → `therapy_mode` (and only that). The remaining fields
   (min/max/set pressure, EPR, ramp, humidifier, mask_type) are **absent from the
   cpap-parser schema** and stay unmapped, and `persist.py` still hardcodes
   `therapy_mode`/`mask_type`/`humidity_level`/`temperature_c` to `None` — so the
   mapping is **conformance-only** and persistence is unchanged (stop-and-ask if
   wiring it into production would touch import behavior).

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

2. **Fixture layout vs `source_directory` default — NOW CLOSED.** Even with the
   backend installed, `_acquire_import_run` parses `root /
   manifest.get("source_directory", "source")`. The AirSense 10 fixture is
   **non-standard**: `DATALOG/` and `STR.edf` live at the fixture **root**. The
   manifest previously carried no `source_directory` key, so the default `source/`
   path was empty — `ResMedNativeLoader().detect(<root>/source)` returned `[]`
   (auto-parse skipped with `"no ResMed device detected in fixture source"`),
   whereas `detect(<root>)` finds the device. **Fixed this phase:** the manifest now
   pins `"source_directory": "."`, so the source resolves to the committed fixture
   root. Verified parser-free by
   `tests/test_conformance.py::test_validate_import_airsense10_source_directory_points_at_committed_root`
   (structural `detect` finds exactly one ResMed device at the resolved root) and
   by the detection half of
   `tests/conformance/test_resmed_airsense10.py::test_fixture_normalized_import_run_acquired_via_loader`.

**What this means.** With blocker (2) closed, the **only** remaining gate on a
normalized run for this card is blocker (1) — the `cpap-py` EDF backend. Current
committed fixture-backed coverage stays the parser-free `oscar_reference` hash
pins (§2.1); the loader-ready semantic candidates stay **injected-only** until a
`cpap-py`-equipped dev/CI environment exists. The honest gated contract is pinned
in both environments by
`tests/test_conformance.py::test_validate_import_airsense10_semantic_block_gated_until_parser_backend`:
backend-absent → a semantic block injected into a copy of the committed manifest
**skips** with `"cpap-parser/cpap-py not installed"` (never a fabricated pass);
backend-present → the run is now acquirable, so the block is *actually compared*,
no longer acquisition-gated. The `oscar_reference` hash still verifies throughout.

**Next action (now down to one blocker):** in a `cpap-py`-equipped environment
(see §9 for the install path), (a) run
`test_fixture_normalized_import_run_acquired_via_loader` to confirm the normalized
`ImportRun` is produced, (b) read the safe aggregate facts (warning codes,
per-night block counts, usage/span/gap seconds, event counts) from that run, and
(c) author those `expected.import` blocks from the verified run. Do **not**
fabricate any value while the backend is unavailable.

## 9. `cpap-py` dependency: source and install path

Audited this phase to answer "why is `cpap_parser` importable but `cpap_py` not?"

- **What `cpap_py` is.** A Python package — the **ResMed EDF reader** — pulled in
  by the `[resmed]` *extra* of `cpap-parser`. It hard-depends on `pyedflib` +
  `numpy`/`pandas` (see the comment in root `requirements.txt`). It is **not** the
  Rust extension module: importing `cpap_parser` prints "Rust extension module not
  available; Lowenstein/Yuwell adapter disabled", which is a *separate* optional
  native backend for other vendors and unrelated to the ResMed EDF path.
- **Distribution vs import name.** Installed via the extra `cpap-parser[resmed]`;
  the import name is `cpap_py` (distribution `cpap-py`).
- **Where it comes from.** The pinned ResMed fork
  `git+https://github.com/kilo-WATT/cpap-parser.git@6e015c4c…` (root
  `requirements.txt`, "awaiting upstream MR !12").
- **Is it in the lock file?** **No.** `uv.lock` contains no `cpap-parser` /
  `cpap-py` / `pyedflib` / `pandas` entries (the only `numpy` rows are matplotlib's
  transitive dep). So `cpap_py` is *absent by design* from the uv environment — not
  "present but failing to build". The base `cpap_parser` currently in `.venv` was
  installed out-of-band **without** the `[resmed]` extra, which is exactly why
  `cpap_parser` imports but `cpap_py` does not.
- **Production vs test treatment.** The project already treats
  `cpap-parser[resmed]` as a **production** dependency for the import path: the
  `Dockerfile` installs root `requirements.txt` (lines 27–30, "the importer's
  git-sourced deps … that the API base set omits but the upload/import path
  needs"). But **CI/test** installs with `uv sync --group dev` (`.github/workflows/
  ci.yml`), which uses `pyproject.toml`/`uv.lock` **only** and deliberately
  excludes the parser. The two are intentionally split: heavy, git-sourced,
  native-building deps stay out of the locked test closure.
- **Decision — dependency files unchanged this phase.** Adding
  `cpap-parser[resmed]` to `pyproject.toml`/`uv.lock` would change CI's dependency
  closure: `uv sync --group dev` would then build `pyedflib`/`pandas` from a git
  source on every CI run and would *activate* the three currently-skipped
  `cpap-py`-gated tests (which must then pass). That is git-sourced, platform-
  sensitive, and CI-destabilizing — not "low-risk" — so per the task guardrails it
  was **not** done, and a direct install was likewise not performed (git-sourced
  external code requires explicit operator authorization).
- **Recommended install path (operator-authorized).** To enable parser-backed
  fixture validation in a dev/CI environment, install the same pinned spec already
  in `requirements.txt`, e.g. `uv pip install -r requirements.txt` (or, minimally,
  the `cpap-parser[resmed] @ git+…@6e015c4c…` line). This brings `cpap_py` +
  `pyedflib` + `numpy`/`pandas`. After that, the `cpap-py`-gated tests in
  `tests/conformance/test_resmed_airsense10.py` — including
  `test_fixture_normalized_import_run_acquired_via_loader` — will run instead of
  skip. Only then author semantic `expected.import` values, sourced from the
  verified normalized run.

### 9.1 Local install attempt (this phase) — blocked by missing C toolchain

The operator-authorized, **validation-only** local install was attempted on this
Windows dev host:

```
uv pip install "cpap-parser[resmed] @ git+https://github.com/kilo-WATT/cpap-parser.git@6e015c4c95317683f68027099d7e998e36131eb2"
```

**Result: failed to build a transitive native dependency.** Resolution proceeded
correctly through the chain `cpap-parser[resmed]` → `cpap-py` (v1.0.0) →
`pyedflib` (v0.1.42), but **`pyedflib` has no prebuilt wheel for this Python 3.12 /
Windows environment and must compile its `c_edf` C extension from source**. That
build aborts with:

> `error: Microsoft Visual C++ 14.0 or greater is required. Get it with "Microsoft C++ Build Tools".`

So the blocker is a **host toolchain gap**, not a repository or pin problem:
`cpap_py` remains un-importable, **no dependency/lock/tracked files were changed**
(`pyproject.toml`/`uv.lock`/`requirements.txt`/`Dockerfile` untouched), nothing was
committed from the install, and the `.venv` is a gitignored local artifact. Tasks
depending on a normalized run (semantic `expected.import` authoring) therefore stay
blocked on this host.

**Concrete prerequisite for the next attempt** — use an environment where
`pyedflib` resolves to a **prebuilt wheel** so no C compiler is needed: a Linux
host / CI runner (manylinux wheels) or macOS, **or** a Windows host with the
**Microsoft C++ Build Tools (MSVC v14+)** installed so the source build can
proceed. A Linux/CI runner is the lowest-friction path (it is also where the
Dockerfile already installs `requirements.txt` successfully). The separate concern
about adding the parser to `pyproject.toml`/`uv.lock` (which would rebuild these
native deps in every CI run and activate the gated tests) is **unchanged** — that
remains a deliberate, declined change; this note only records the local-host build
failure.

### 9.2 Resolved in a Linux container — first parser-backed semantic coverage landed

Following §9.1, the parser was installed and the fixture validated **in a Linux
container** (`python:3.12-slim`), where `pyedflib` ships a manylinux wheel so no C
compiler is needed:

```
docker run -d -v <repo>:/src:ro python:3.12-slim sleep infinity
# in container: apt-get install -y git
#               pip install -r /src/api/requirements.txt -r /src/requirements.txt pytest
```

`cpap_parser` **and** `cpap_py` then import cleanly (`pyedflib 0.1.42`, `numpy
2.4.6`, `pandas 3.0.3`), and `ResMedNativeLoader.import_data_with_directory` on the
committed AirSense 10 fixture produces a normalized `ImportRun`. **No
dependency/lock/tracked files were changed** — the install lived only inside the
ephemeral container; `pyproject.toml`/`uv.lock`/`requirements.txt`/`Dockerfile`
remain untouched.

**Safe aggregate facts read from the normalized run** (default `ImportOptions`, the
exact options `_acquire_import_run` uses; anonymized −508d-shifted machine-local
dates, no serial/PHI printed):

| Fact | Value |
|---|---|
| sessions | 40 (37 summary-only "ghost", 3 detailed) |
| run warning codes | `resmed_summary_only_day` (only); `resmed_serial_absent` **absent** (serial present) |
| detailed dates | `2024-12-14`, `2024-12-25`, `2025-01-05` |
| `session_blocks.block_count` | 4 / 1 / 2 |
| `therapy_aggregates` usage_seconds | 26100 / 22920 / 20400 |
| `therapy_aggregates` wall_clock_seconds | 31767 / 22920 / 20401 |
| `therapy_aggregates` gap_seconds | 5667 / 0 / 1 |
| `events.count` | 2 / 4 / 5 |
| `Session.settings` | **empty on every session** (no `SettingsSnapshot` built — loader gap holds) |
| signals/waveforms | present only with `include_waveforms=True` (not pinned) |

**What became fixture-backed (committed).** These exact values are now pinned as
semantic `expected.import` blocks in the AirSense 10 manifest —
`warnings.codes`/`warnings.absent`, `session_blocks.block_count`,
`therapy_aggregates` (usage/wall-clock/gap seconds), and `events.count` — and are
verified by `validate_import(run=...)` against the real parsed run in
`tests/conformance/test_resmed_airsense10.py::test_fixture_semantic_expected_import_matches_normalized_run`
(`cpap-py`-gated: it runs where the backend is present and skips cleanly, never
fabricating a pass, where it is absent — e.g. Windows/CI). The manifest records this
provenance in its top-level `import_expected_provenance` field. This is the **first
committed, value-level, fixture-backed import-level coverage** on a real card (the
prior `oscar_reference` coverage was hash-only).

**What remains blocked.** *(Superseded for settings by §11.)* `settings.values`
was blocked at the time of §9.2 — the loader constructed no `SettingsSnapshot`. §11
**partially resolves** this: the one therapy setting cpap-parser actually exposes
(`pressure_mode` → `therapy_mode`) is now mapped and fixture-backed; every other
settings field stays blocked because the parser schema has none. Exact
`session_blocks.intervals` and ordered `events` with timestamps remain **deferred**:
they embed shifted real timestamps and depend on the anonymization-calendar split
plus the raw→OSCAR event-type vocabulary, none of which were settled here — so no
timestamps were authored. OSCAR numeric parity, weighted/time-based summaries,
persistence, production routing, and the `cpap-parser` cutover are all untouched.

## 11. ResMed settings mapping — `therapy_mode` only (resolved this phase)

The §3 / §9.2 "no loader builds a `SettingsSnapshot`" gap is now **partially
closed**, conservatively. An audit of what cpap-parser actually exposes (parsed
AirSense 10 fixture in a Linux container) found that the normalized schema carries
**exactly one** therapy *setting*: `CPAPSessionSummary.pressure_mode` — the STR.edf
mode code rendered as a label (`PRESSURE_MODE_NAMES`: `"CPAP"`/`"APAP"`/`"BiLevel …"`/
`"ASV"`, or `"Unknown"`/`""` when unset). The other `pressure_*` fields
(`pressure_50`/`pressure_95`) are **measured mask-pressure percentiles, not configured
settings**, and there are **no** min/max/set-pressure, EPR, ramp, humidifier, tube/
climate, or mask-type fields anywhere in `MachineInfo` / `CPAPSessionSummary` /
`CPAPSession`.

**What was implemented** (`ResMedNativeLoader._session_settings`, wired into
`_build_session`):

- One `SettingsSnapshot` per session carrying **only** `therapy_mode = pressure_mode`.
- A missing/unknown mode (`""` or the literal `"Unknown"`) is left **absent** — no
  snapshot, never coerced to a placeholder/`0`/`false`/`"off"` — so the conformance
  missing-≠-off semantics hold.
- `effective_at` reuses the session's existing `start_time` anchor (detailed-night
  first start, or the summary calendar-day anchor for a ghost day) — no invented
  global date. `source_names = {"therapy_mode": "pressure_mode"}`,
  `source_file_ids = ("STR.edf",)`, `confidence = PROBABLE` (conservative: a single
  device-reported field, not yet cross-validated).

**On the committed AirSense 10 fixture:** `pressure_mode` is a stable `"APAP"` across
all 40 summaries (AutoSet device), so all 40 sessions get a one-key snapshot. The
manifest now pins `expected.import.settings.<date>` = `snapshot_count: 1`,
`present: true`, `values: {therapy_mode: "APAP"}` for the 3 detailed nights, verified
against the real run by `test_fixture_semantic_expected_import_matches_normalized_run`
and `test_fixture_settings_snapshot_maps_only_therapy_mode`
(`cpap-py`-gated). Parser-free unit coverage of the helper lives in
`tests/test_resmed_import_regressions.py`
(`test_build_session_maps_therapy_mode_settings_snapshot`,
`test_build_session_omits_settings_when_mode_unknown_or_absent`).

**Still blocked / unchanged.**

- **All other settings fields** (`minimum_pressure_cm_h2o`/`maximum_pressure_cm_h2o`/
  `set_pressure_cm_h2o`/`epr_level`/`epr_enabled`/`ramp_*`/`humidifier_level`/
  `mask_type`): cpap-parser does **not** expose them, so they cannot be mapped
  without parser/schema work upstream. Not fabricated.
- **Persistence is unchanged.** `persist.py` still hardcodes
  `therapy_mode`/`mask_type`/`humidity_level` to `None`; this mapping is
  **normalized-run / conformance-only** for now. Wiring the snapshot through to the
  `sessions` columns is a separate persistence change (stop-and-ask if it touches
  production import behavior).
- Support status is **not** upgraded to "validated" beyond this single,
  conservatively-confident field.
