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
      are both available.
- [ ] **Weighted / time-based summaries.** Where ResMed PLD data already exists,
      exercise time-weighted summary conformance — OSCAR's
      `session_channel_values` (value → count + `time_ms`) is the precedent for
      why simple averages are insufficient.

### 4. Reframe future waveform storage (documentation only)
- [ ] Reframe planning language from "full-night row-per-sample storage" to a
      **future compressed waveform segment/BLOB design investigation** modeled on
      OSCAR's `event_lists`/`event_data` (metadata index row + compressed payload
      + integrity checksum), explicitly noting Postgres-native concerns
      (`BYTEA`/large-object/TOAST trade-offs, backup size, streaming/range reads,
      retention/downsampling tiers, multi-tenant isolation). (Review §7, §9.)
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
   **(Partly met — the event-window production decision is recorded (§4); the
   prose reframe of full-night → compressed-segment investigation is still open.)**
5. A device-time-correction design note is recorded (no migration). **(Met — §5:
   `docs/sleeplab_2_device_time_correction_design.md`.)**
6. Lowenstein read-only conformance remains explicitly deferred behind a safe
   fixture. **(On track — still deferred by design; §6.)**

Items 1–3 and 5 are met by the planning, documentation, and import-level
conformance depth landed in this milestone (interval boundaries, settings values,
and event parity comparators, plus the OSCAR-numeric-parity and device-time-
correction design notes). The open items are the waveform-storage prose reframe
(§4, item 4), OSCAR **numeric parity** implementation (designed but not built),
weighted/time-based summaries, and Lowenstein read-only conformance — none of
which requires a migration, routing change, or new tag.
