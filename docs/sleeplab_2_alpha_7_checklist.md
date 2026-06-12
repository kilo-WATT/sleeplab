# SleepLab 2.0 Alpha 7 Working Checklist

Milestone target: `v2.0.0-alpha.7` (a future annotated tag on `develop/2.0`,
per `AGENTS.md` — **not** a new branch, and **not yet created**: do not tag
`v2.0.0-alpha.7` until explicitly told). Latest existing milestone is
`v2.0.0-alpha.6`.

This checklist operationalizes the recommendations in
`docs/sleeplab_2_crimson_structure_review.md` (the **OSCAR 2.0 Database
Architecture Review**), which re-grounded Alpha 7 planning in the actual
extracted OSCAR 2.0 SQLite source (`OSCAR-code-master.tar.gz`) rather than the
earlier inferred "crimson-structure" placeholder. It continues directly from the
deferred / "alpha.7 depth" items at the end of
`docs/sleeplab_2_alpha_6_checklist.md`.

Alpha 7 is **planning + conformance depth only**. It sits **after** Alpha 6's
event-window waveform decision and **before** any Lowenstein persistence, ResMed
parser cutover, full-night waveform storage, or broad UI/API change. It does not
begin beta.

## Current baseline (grounding)

- OSCAR 2.0 is now a single-file SQLite database (`oscar.db`); schema code
  reports `CURRENT_SCHEMA_VERSION = 17` while `Notes/Database/DATABASE_SCHEMA.md`
  documents v16. v17 adds `device_time_corrections`. (Review §1, §10.)
- OSCAR stores waveform/event data as **compressed BLOBs** — `event_lists`
  (one metadata row per EventList) + `event_data` (one compressed payload row per
  EventList), qCompress + CRC16 — **not** one row per sample. (Review §7.)
- SleepLab persists high-rate BRP flow/pressure as **event-windowed** rows in
  `session_waveform`; full-night storage is deferred (Alpha 6 decision). Estimate
  pinned in `importer/waveform_estimate.py` /
  `tests/test_waveform_estimate.py` (≈90k rows/h, ≈720k/8 h-night,
  ≈21.6 M/30-night card).
- SleepLab's `WaveformSegment` (`importer/loaders/models.py`) already carries
  segment metadata + a nullable `storage_ref` — i.e., it is segment-ready for a
  future OSCAR-like compressed-segment design.
- Import-level conformance (`validate_import`, `importer/conformance.py`) today
  checks: warnings, session-block **count + interval boundaries**, therapy
  aggregates (usage/span/gap), settings **count/presence + per-setting values**
  (missing-vs-off), **event count/type/timestamp/duration parity**, identity
  hashes (DB-gated), and the OSCAR reference **export hash**. The value/boundary/
  event comparators are exercised by injecting a normalized `ImportRun`; the
  ResMed loader does not yet map `SettingsSnapshot` *values*, so against a real
  card settings `values` still needs loader work. Still deferred: OSCAR **numeric
  parity** (designed in the conformance plan §13, not implemented) and
  weighted/time-based summaries.

## Alpha 7 must-do

### 1. OSCAR 2.0 DB architecture mapping review
- [x] **(done)** Inspect the extracted OSCAR 2.0 source and rewrite the review as
      `docs/sleeplab_2_crimson_structure_review.md` →
      "SleepLab 2.0 Alpha 7 OSCAR Database Architecture Review", grounded in
      file-specific findings (schema docs, `database_schema.{h,cpp}`, the
      per-entity repositories, `device_time_correction_repository.{h,cpp}`). No
      OSCAR source committed.
- [x] **(done)** Verify the kickoff observations against the archive: `oscar.db`
      SQLite; machines/sessions/slices/settings/channel-values/daily-summaries/
      event-lists/event-data tables; `event_lists`/`event_data` compressed-BLOB
      model; docs v16 vs code v17; v17 `device_time_corrections`. All confirmed.
- [x] **(done)** Produce the OSCAR→SleepLab concept mapping for
      `cpap_machines`, `import_runs`, `import_source_files`, `sessions`,
      `session_blocks`, `settings_snapshots`, `signal_channels`,
      `session_events`, `session_metrics`, `session_waveform`/event-window,
      `derived_values`, `nightly_therapy_aggregates`, and conformance fixtures.
      (Review §3.)

### 2. Alpha 7 checklist
- [x] **(done)** This document, derived from the review.

### 3. Import-level conformance depth on existing ResMed fixtures
Extend `validate_import` / the `expected.import` manifest block (backward
compatibly; existing synthetic + AirSense 10 fixtures stay green; absence stays
skipped, never faked). No routing or schema change. In priority order:
- [x] **(done)** **Session-block interval boundaries.** `expected.import.
      session_blocks.<date>.intervals` now compares each block's start/end against
      manifest ISO timestamps within a 1s tolerance, upgrading the
      block-**count**-only check. Actual blocks are sorted canonically by
      `(start_time, end_time, source_block_key)`; expected intervals are compared
      in listed (chronological) order; count mismatch, malformed shape, invalid
      timestamp, and naive-vs-tz-aware boundaries are clear failures (no timezone
      conversion invented). Backward compatible — `block_count`-only and
      no-`expected.import` fixtures are unchanged. Mask-on/off boundaries remain
      deferred. (Alpha 6 §5 deferred item; OSCAR `session_slices` is the reference
      shape.) Implemented in `importer/conformance.py`; covered in
      `tests/test_conformance.py`.
- [x] **(done — comparator; loader mapping still pending)** **Settings values /
      missing-vs-off semantics.** `expected.import.settings.<date>.values` compares
      per-setting normalized values against the selected `SettingsSnapshot`:
      strings/bools exact, numbers within `1e-6`, and `null` asserts *missing*
      (absent or `None`) — never satisfied by a fabricated `0`/`false`/`off`, and
      `0`/`false` never count as missing. Multiple snapshots resolve to the latest
      `effective_at` at/before the session start, else fail with an
      ambiguous-snapshot message. Implemented in `importer/conformance.py`, covered
      in `tests/test_conformance.py` via injected snapshot-bearing runs. **Still
      gated:** the ResMed loader does not yet map `SettingsSnapshot` values, so
      against a real card only presence/count applies — wiring real values is the
      remaining loader step (not done here; no loader change in this milestone).
- [x] **(done — against the normalized run; OSCAR-reference sourcing still
      future)** **Event count / type / timestamp parity.**
      `expected.import.events.<date>` compares total `count`, per-type `types`
      tallies, and an ordered `events` list (type exact, start ±1s, optional
      `duration_seconds` ±1s with `null`=`None`) against the normalized
      `Session.events`, sorted canonically by `(start_time, event_type,
      duration_seconds, source_event_key)`. Missing date, count/type/length
      mismatch, malformed event, invalid timestamp, and naive-vs-tz-aware starts
      are clear failures. Implemented in `importer/conformance.py`, covered in
      `tests/test_conformance.py` via injected event-bearing runs. The comparator
      is event-type-vocabulary-agnostic; aligning the normalized vocabulary to
      OSCAR's enum (`0=Obstructive,1=Unclassified,2=Hypopnea,3=RERA,4=Clear
      Airway,5=User-flagged`) and the v10 `central→unclassified` correction, and
      sourcing the expected values directly from an OSCAR export, remain future
      work (see OSCAR numeric parity below).
- [~] **(design recorded; implementation deferred)** **OSCAR reference
      comparison** beyond the export hash: numeric/row parity against OSCAR
      `session_summaries` / `daily_summaries` values (per-night), gated on
      availability of a reference export. The design is now written up in
      `docs/sleeplab_2_import_level_conformance_plan.md` §13 (optional nested
      `oscar_reference.parity` block reusing the existing aggregate/settings/
      block/event comparator shapes; tolerances; explicit skip-vs-fail rules;
      provenance metadata recording OSCAR version + code schema v17 + commit/
      archive hash + export hash; no OSCAR source/export committed unless safely
      anonymized). **Not implemented** — `oscar_reference.parity` still skips with
      a clear reason until a redistributable reference export and a normalized run
      are both available. **Phase 2 update:** the export-**hash** half is now
      *committed-fixture-backed* — the anonymized AirSense 10 fixture
      (`tests/conformance/fixtures/resmed_airsense10_001/manifest.json`) pins
      `expected.import.oscar_reference.export_hash` for its committed
      `oscar_reference/summary.csv` **and**, via the `oscar_reference.files` list, a
      sha256 for the twin `oscar_reference/sessions.csv`, both verified parser-free
      by `validate_import` (`tests/test_conformance.py`). The comparator was
      generalized backward-compatibly to verify a list of reference-file pins.
      Numeric `parity` is unchanged (still skips).
- [ ] **Weighted / time-based summaries.** Where ResMed PLD data already exists,
      exercise time-weighted summary conformance — OSCAR's
      `session_channel_values` (value → count + `time_ms`) is the precedent for
      why simple averages are insufficient.

### 4. Reframe future waveform storage (documentation only)
- [x] **(done — documentation reframe)** Reframed planning language from
      "full-night row-per-sample storage" to a **future compressed waveform
      segment/BLOB design investigation** modeled on OSCAR's
      `event_lists`/`event_data` (metadata index row + compressed payload +
      integrity checksum), explicitly noting Postgres-native concerns
      (`BYTEA`/large-object/TOAST trade-offs, backup size, streaming/range reads,
      retention/downsampling tiers, multi-tenant isolation). Landed in
      `docs/sleeplab_2_data_architecture.md` → "Waveform storage scope" (the
      future-direction + investigation bullets) and "Next milestone"; the ~21.6 M
      row figure is reframed as the row-per-sample *worst-case upper bound* a
      compressed design avoids, and `WaveformSegment.storage_ref` is noted as
      already segment-ready. No schema, migration, or persistence change.
      (Review §7, §9.)
- [x] **(decided — unchanged)** Keep **event-window** waveform storage as Alpha 7
      production behavior; full-night storage stays deferred and stop-and-ask.
      (Review §8; Alpha 6 §2 decision.)

### 5. Device-time-correction design note (documentation only)
- [x] **(done — design note only, no migration/schema/code)** Captured the shape
      implied by OSCAR's v17 `device_time_corrections` as a dedicated design note,
      `docs/sleeplab_2_device_time_correction_design.md`: a per-machine,
      date-ranged, **typed** (`timezone|travel|dst|reset|offset|drift`),
      **reversible** (`applied_at`/`undone_at`) correction record using either a
      constant `offset_ms` or a linear drift model (`corrected = c0_ms + c1·t`),
      layered **non-destructively** over raw device timestamps, related to
      SleepLab's current `timezone_basis` string on `Session`/`Capabilities`. The
      note records the problem (wrong clock/travel/DST/reset/drift), the OSCAR v17
      reference, SleepLab requirements (machine-scoped, import-run provenance,
      source timestamps retained, corrected times derived, audited/reversible, UI
      explains corrected-vs-source), explicit Alpha 7 non-goals, and future
      stop-and-ask implementation gates. (Review §10.)

### 6. Lowenstein read-only conformance (prep only)
- [ ] Keep the first Lowenstein **read-only** normalized fixture comparison
      deferred until a safe anonymized or synthetic fixture exists. No persistence.

## Explicit no-go (this milestone)

Hard constraints for Alpha 7 work:
- [ ] **Do not create `v2.0.0-alpha.7`** (or any tag) until explicitly told.
- [ ] **Do not create a new branch.** Work stays on `develop/2.0`; milestones are
      annotated tags. No `codex/…` or `claude/…` branches.
- [ ] **Do not create tool-specific folders** (`codex/`, `claude/`, etc.). Docs
      live in `docs/`; coordination notes in `dev-notes/`.
- [ ] **Do not commit the OSCAR archive or any extracted third-party source**,
      schema files, or ER diagrams. Inspect outside the repo only.
- [ ] **Do not create DB migrations**, change production import behavior, or
      change import routing.
- [ ] **Do not enable Lowenstein persistence.**
- [ ] **Do not route ResMed through `cpap-parser` in production.**
- [ ] **Do not implement full-night waveform storage** (or an OSCAR-like
      segment/BLOB store) — design investigation only.
- [ ] **Do not begin broad UI/API rewrites or beta work.**

Each of the above remains an explicit **stop-and-ask** item.

## Alpha 7 exit (working definition)

Alpha 7 is "done enough" to move on when:
1. The OSCAR 2.0 DB architecture mapping review is finalized with file-specific
   findings and the full concept mapping. **(Met — §1.)**
2. This Alpha 7 checklist exists, derived from the review. **(Met — §2.)**
3. At least the first import-level conformance depth item (session-block interval
   boundaries) is implemented against the existing ResMed fixture, backward
   compatibly, with deferred sub-checks skipping cleanly. **(Met — §3:
   interval boundaries, settings values, and event count/type/timestamp/duration
   parity are implemented and tested; deferred sub-keys skip cleanly.)**
4. Future waveform storage is reframed as a compressed segment/BLOB design
   investigation, with event-window storage kept as the production default.
   **(Met — §4: the prose reframe landed in `docs/sleeplab_2_data_architecture.md`
   ("Waveform storage scope" + "Next milestone"); event-window storage stays the
   production default.)**
5. A device-time-correction design note is recorded (no migration). **(Met — §5:
   `docs/sleeplab_2_device_time_correction_design.md`.)**
6. Lowenstein read-only conformance remains explicitly deferred behind a safe
   fixture. **(On track — still deferred by design; §6.)**

Items 1–5 are met by the planning, documentation, and import-level conformance
depth landed in this milestone (interval boundaries, settings values, and event
parity comparators, plus the OSCAR-numeric-parity, device-time-correction, and
waveform segment/BLOB design notes). The open items are OSCAR **numeric parity**
implementation (designed but not built), weighted/time-based summaries, and
Lowenstein read-only conformance — none of which requires a migration, routing
change, or new tag.

## Phase 2 status (fixture-backed validation)

Phase 2 moves import-level conformance from "comparators implemented with
injected normalized `ImportRun` tests" toward "committed safe fixtures assert
`expected.import` behavior where loader/fixture evidence honestly supports it."
It is **started, not complete**, and broadens no production behavior.

**Readiness audit result:** Alpha 7 is **coherent and milestone-ready** from a
docs/conformance standpoint. All audited docs correctly keep event-window
waveform storage as the production default, frame future waveform work as a
compressed segment/BLOB design investigation, and keep Lowenstein persistence,
the ResMed `cpap-parser` cutover, full-night/compressed waveform storage, and
device-time-correction implementation blocked/deferred. The implemented
`validate_import` comparators match the conformance plan. No OSCAR source,
archive, raw card, real serial, or PHI is committed. Tests pass.

**`v2.0.0-alpha.7` was NOT tagged in this session.** The milestone conditions
are met (clean tree, passing tests, latest tag still `v2.0.0-alpha.6`), but
cutting and pushing a public annotated tag is an outward-facing, hard-to-reverse
action, and the tag remains an explicit *stop-and-ask* item (this checklist's
"Explicit no-go", `AGENTS.md`). Tagging is deferred until explicitly authorized.

**Fixture inventory:** `docs/sleeplab_2_fixture_validation_matrix.md` records,
per committed fixture, exactly what is fixture-backed vs injected-only.

**Fixture-backed coverage that exists:**

- Planning-level on the synthetic fixture (`validate_fixture`): detection,
  identity, capabilities, file-count coverage, planning diagnostics, and
  waveform-absence detection.
- Real-card (anonymized AirSense 10): serial identity and signal channel
  inventory (pure-Python, normal suite); AHI / computed-usage / ghost-night
  parity vs the committed OSCAR export (`cpap-py`-gated).
- **This phase:** `expected.import.oscar_reference.export_hash` is now
  committed-fixture-backed on the AirSense 10 fixture for **both** committed
  anonymized exports — the per-day `summary.csv` and (via the
  `oscar_reference.files` list) the per-session `sessions.csv` — verified
  parser-free by `validate_import`. This is the only committed `expected.import`
  coverage so far. A `warnings.absent` pin on the synthetic fixture was
  *deliberately not taken*: it would only assert wiring against an injected empty
  run (no parser is installed to value-verify it) while flipping the
  import-block-free invariant three tests rely on — see the matrix §5.

**Still injected-only** (no committed fixture drives them): the `warnings`,
`session_blocks` (count + intervals), `therapy_aggregates`, `settings`
(count/present/values), `events`, and `identity_hashes` comparators — exercised
by injected runs, `tmp_path` manifests, or synthetic DB rows.

**Tooling:** `summarize_import_blocks(fixture_dir, result)` labels each requested
`expected.import` block passed/skipped/failed so a green-and-checked block reads
distinctly from a green-but-gated one. The conformance CLI also gained an opt-in
`--import` flag (`python -m importer.conformance <fixture> --import`) that prints
that per-block status parser-free and degrades gracefully (no traceback) on the
non-standard AirSense 10 fixture. Both are read-only; no production behavior.
Contributor guidance for supplying new safe evidence lives in
`docs/sleeplab_2_validation_inputs.md`.

**Remaining blocked/deferred (unchanged):** OSCAR numeric parity,
weighted/time-based summaries, the `settings.values` loader mapping (no loader
constructs a `SettingsSnapshot` yet — comparator stays injected-only), Lowenstein
persistence, ResMed parser cutover, full-night/compressed-segment waveform
storage, and device-time-correction implementation.

**ResMed normalized-output gap audit (Phase 2 bridge):**
`docs/sleeplab_2_resmed_normalized_output_gap_audit.md` inventories exactly what
`ResMedNativeLoader` emits per `expected.import` block and pins, contract-area by
contract-area, why the AirSense 10 fixture can pin OSCAR reference *hashes* but
not yet assert real *values*. Summary: `warnings.codes`,
`session_blocks.block_count`, `therapy_aggregates`, and `events.count` are
**loader-ready** — gated only on obtaining a normalized run (`cpap-parser` **and**
the `cpap-py` backend; the backend is **absent in CI/most envs**, so the
auto-parse path skips) **and** on authoring those blocks in the manifest.
`settings.values` is additionally **loader-blocked** (no `SettingsSnapshot` is
ever built; only `present: false` is honest today). Timestamped
`session_blocks.intervals` / ordered `events` are deferred behind the fixture's
anonymization-calendar split (EDF shifted −508d; DATALOG dirs/OSCAR refs on the
original calendar) and the raw-string→OSCAR event-type vocabulary. **Next safe
task:** add fixture-backed `warnings`/`block_count`/`therapy_aggregates`/
`events.count` assertions in an environment where `cpap-py` is installed (no
loader, routing, schema, or persistence change). The current loader output shape
is pinned parser-free by the `test_build_session_emits_*` tests in
`tests/test_resmed_import_regressions.py`.

**Parser-backed setup path (this phase — one blocker closed, one remaining; no
semantic values added):** the two setup blockers recorded in gap audit §8 were
worked. **(2) `source_directory` — FIXED:** the AirSense 10 manifest now pins
`"source_directory": "."`, so `_acquire_import_run` resolves the source to the
committed fixture root (its `DATALOG`/`STR.edf` live there, not under `source/`).
Verified parser-free by
`test_validate_import_airsense10_source_directory_points_at_committed_root` and the
detection half of `test_fixture_normalized_import_run_acquired_via_loader`.
**(1) `cpap-py` backend — still absent (remaining blocker):** `cpap_parser`
imports but `cpap_py` does not, so auto-parse skips with
`"cpap-parser/cpap-py not installed"`. Audited its source (gap audit §9):
`cpap_py` is the ResMed EDF reader from the `cpap-parser[resmed]` extra (pinned git
fork in root `requirements.txt`; a production dep via the Dockerfile, but
deliberately absent from `pyproject.toml`/`uv.lock` and CI's
`uv sync --group dev`). **Dependency files were not changed** — adding the extra
would rebuild git-sourced native deps in CI and activate the gated tests, which is
not low-risk; the operator-authorized install path is documented instead. Committed
coverage therefore stays the parser-free `oscar_reference` hash pins; the gated
contract holds in both environments via
`test_validate_import_airsense10_semantic_block_gated_until_parser_backend`, and a
new `cpap-py`-gated `test_fixture_normalized_import_run_acquired_via_loader` proves
the normalized-run acquisition path (skips cleanly until the backend is installed).
No values were fabricated while the backend is unavailable. **Local install
attempted (this phase) and blocked by a host toolchain gap:** the authorized
validation-only `uv pip install "cpap-parser[resmed] @ git+…@6e015c4c…"` failed
because its transitive `pyedflib` dependency has no prebuilt wheel for this Python
3.12 / Windows host and its `c_edf` C extension cannot compile without Microsoft
C++ Build Tools (MSVC v14+). `cpap_py` remains absent; **no dependency/lock/tracked
files were changed**; nothing was committed from the install (gap audit §9.1).
**Next safe task:** run the install on an environment where `pyedflib` resolves to
a prebuilt wheel (a Linux/CI runner — manylinux — or macOS, or a Windows host with
MSVC Build Tools), confirm `test_fixture_normalized_import_run_acquired_via_loader`
passes, then author `warnings`/`block_count`/`therapy_aggregates`/`events.count`
from the verified normalized run.

**Parser-backed semantic coverage — LANDED (later phase, in Linux/Docker):** the
"next safe task" above is now done. The fixture was parsed in a `python:3.12-slim`
container (manylinux `pyedflib` wheel — no MSVC build needed; `cpap_parser`+`cpap_py`
import cleanly), `ResMedNativeLoader` produced a normalized `ImportRun`, and the
first **value-level** semantic `expected.import` blocks were authored *from that
run* and committed: `warnings.codes`/`warnings.absent`,
`session_blocks.block_count`, `therapy_aggregates` (usage/wall-clock/gap seconds),
and `events.count` for the 3 detailed nights. They are verified by
`tests/conformance/test_resmed_airsense10.py::test_fixture_semantic_expected_import_matches_normalized_run`
(`cpap-py`-gated: runs in Linux/Docker, skips cleanly on Windows/CI — never
fabricates a pass). **No dependency/lock/tracked files were changed** (the install
stayed inside the ephemeral container); the manifest carries an
`import_expected_provenance` note. Exact values and the full method are in gap audit
§9.2. **Still blocked (unchanged):** `settings.values` (no `SettingsSnapshot` is
built — settings empty on all 40 sessions); timestamped `session_blocks.intervals`
and ordered `events` (anonymization-calendar split + event-type vocabulary — no
timestamps authored). **Next safe task:** either map `SettingsSnapshot` in the
loader (a loader change — stop-and-ask if it touches production) to unblock
`settings.present`/`values`, or settle the event-type vocabulary + calendar rebase
before authoring timestamped intervals/events.

**ResMed `SettingsSnapshot` mapping — LANDED (`therapy_mode` only):** the "next
safe task" above is partly done. An audit of cpap-parser found it exposes exactly
one normalized therapy *setting* — the daily summary's `pressure_mode` — and **no**
min/max/set pressure, EPR, ramp, humidifier, or mask-type fields (those are absent
from the schema; `pressure_50/95` are measured percentiles, not settings).
`ResMedNativeLoader._session_settings` now maps `pressure_mode` → a one-key
`SettingsSnapshot.therapy_mode` per session (absent when the mode is `""`/`"Unknown"`
— never fabricated; `effective_at` = session `start_time`; `confidence` = `PROBABLE`).
On the fixture `therapy_mode` is a stable `"APAP"`, so the manifest now pins
`settings.<date>` = `snapshot_count: 1`/`present: true`/`values: {therapy_mode: "APAP"}`
for the 3 detailed nights, verified by `test_fixture_semantic_expected_import_matches_normalized_run`
+ `test_fixture_settings_snapshot_maps_only_therapy_mode` (`cpap-py`-gated) and
parser-free unit tests in `tests/test_resmed_import_regressions.py`. **Persistence
now landed for `therapy_mode`** — the cpap-parser bridge writes the normalized
snapshot and populates `sessions.therapy_mode`; unsupported fields remain `NULL`
and missing/`"Unknown"` values are not fabricated (gap audit §11). **Still blocked:**
all other settings fields (not in the parser schema); full settings parity;
timestamped intervals/events.

**Event TYPE counts — LANDED (SleepLab-normalized, not OSCAR parity):** the
`events` coverage is extended from total `count` to per-night `types`. The
parser-backed run's `Session.events` type tallies (raw cpap-parser labels —
`Central Apnea`/`Obstructive Apnea`/`Hypopnea` — plus the loader-derived
`Large Leak`) are stable and reconcile with the existing `count`, so the manifest
now pins `events.<date>.types` for the 3 detailed nights, verified by
`test_fixture_event_type_counts_match_normalized_run` (`cpap-py`-gated) and a
parser-free guard `test_validate_import_airsense10_committed_event_types_are_checked`.
These are **SleepLab-normalized** type counts, **not** OSCAR event-type parity —
the raw→OSCAR enum mapping (and the v10 `central→unclassified` correction) stays
deferred and is a stop-and-ask item (it changes event-type normalization used by
import/persistence). No loader/routing/schema/persistence change. **Next safe
task:** either tackle the raw→OSCAR event-type vocabulary parity (stop-and-ask), or
decide whether to wire the `therapy_mode` snapshot through `persist_import_run` (a
persistence change — stop-and-ask). Exact event/block timestamps and durations
stay deferred.

**ResMed cutover DB parity harness — LANDED (test-only, no routing change):** the
first legacy-vs-cpap-parser database parity oracle is built
(`tests/cutover_parity.py` + `tests/test_resmed_cutover_db_parity.py`). It imports
the committed AirSense 10 fixture through both the legacy `import_sessions` path and
the cpap-parser `persist_import_run` path into one rolled-back test transaction
(separate `machine_id`s), snapshots redacted aggregates of the key tables, and
classifies each as equal / expected_difference / unexpected_difference / skipped /
not_implemented. DB-free classification unit tests run in the normal suite; the
end-to-end harness gates on `TEST_DATABASE_URL` + `cpap-py` (validated in
Linux/Docker with a Postgres service). First run: `session_metrics`/`session_events`/
`nightly_therapy_aggregates` are at **parity**. The settings row-count drop is now
closed (**40 → 40**) with parser `therapy_mode="APAP"` persisted on all sessions,
while broader settings remain an `expected_difference` (legacy 14 keys vs parser
`therapy_mode` only); the **session-granularity split** is measured, not assumed
(cutover audit §5a). No routing/default/schema/dependency change. Remaining cutover work includes
oximetry, source-file provenance, session granularity, and settings beyond the
single field exposed by cpap-parser.

**ResMed oximetry/provenance audit — LANDED:** the committed
AirSense 10 fixture cannot prove oximetry persistence. Its six SAD files advertise
Pulse/SpO2 channels, but every SpO2 value is the `-1` missing sentinel, so legacy
`parse_sa2` returns `None`; both DB paths produce 0 `session_spo2` rows and 0
`has_spo2` sessions. A parser-free regression test pins that negative evidence.
The fallback provenance audit now seeds the parity harness with the same 53-file
manifest production creates before execution. Legacy finalizes 25 used / 28
skipped and links block/event/channel/settings rows. The parser save path now
resolves the loader's exact `STR.edf` settings reference, finalizing 1 used / 52
skipped with one settings link. Block/event/channel ids remain synthetic because
cpap-parser exposes no real source path, so those links remain upstream-blocked.
No routing, schema, default, or dependency change.

**ResMed cutover remaining-work matrix — LANDED (docs only):**
`docs/sleeplab_2_resmed_cutover_remaining_work.md` separates parser-read
evidence, database-save evidence, and production-route readiness. It assigns
each remaining item to SleepLab, upstream parser work, test-data acquisition, or
a product decision. The first next task is the session-shape decision; default
routing remains off until dedupe, dependency/runtime, route integration,
diagnostics, and soak gates are closed.
