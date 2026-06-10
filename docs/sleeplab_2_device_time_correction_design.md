# SleepLab 2.0 Device Time Correction — Future Design Note

Status: **design note only.** No migration, table, column, or production code is
created by this document. It captures the *shape* of a future, non-destructive
device-time-correction model — inspired by OSCAR 2.0's v17
`device_time_corrections` — so that when the work is scheduled (later
alpha/beta), it starts from an agreed design rather than an ad-hoc patch.

This expands Alpha 7 checklist §5 and
`docs/sleeplab_2_crimson_structure_review.md` §10. It is explicitly **gated** —
implementing any of it is a stop-and-ask item (see §5).

## 1. Problem

CPAP/therapy device clocks are not authoritative, yet SleepLab keys nights to a
machine-local date. Several real-world effects can therefore shift session/event
boundaries and the night a record lands on:

- **Wrong device clock.** A machine set to the wrong time (never set, battery
  reset, factory default) emits timestamps offset from real local time.
- **Travel / timezone change.** A user crossing time zones produces nights whose
  machine-local date no longer matches their actual local calendar.
- **DST transitions.** Spring-forward / fall-back can duplicate or skip an hour,
  distorting wall-clock spans and the date a session is attributed to.
- **Device resets.** A clock reset mid-history creates a discontinuity — older
  data on one offset, newer data on another.
- **Clock drift.** Some devices drift seconds-to-minutes over weeks; left
  uncorrected this smears event/session boundaries against an oximeter or a
  reference export.

SleepLab today carries only a `timezone_basis` **string** on `Session` and
`Capabilities` (`importer/loaders/models.py`). That is enough to *label* the
basis of a timestamp; it cannot *correct* an offset, a DST step, or drift.

## 2. OSCAR v17 reference (`device_time_corrections`)

OSCAR 2.0 schema v17 adds a per-machine, date-ranged, typed, reversible
correction model (confirmed against the extracted source —
`device_time_correction_repository.{h,cpp}`; review §10):

- **Machine-scoped, date-ranged.** Keyed to a machine with `date_from`/`date_to`
  (open-ended ranges allowed).
- **Typed.** `type IN ('timezone','travel','dst','reset','offset','drift')` —
  the cause is recorded, not just the effect.
- **Two correction forms.** A constant `offset_ms` (slope `c1 == 0`), or a
  **linear drift model** `corrected = c0_ms + c1 · t` (slope `c1 != 0`) for clock
  drift.
- **Reversible / auditable.** `applied_at` + `undone_at` (empty = active), with
  repository operations `markUndone` / `findActive` / `upsertTyped`. Corrections
  are **layered records**, never destructive edits to stored timestamps.

## 3. SleepLab requirements (target shape)

A future SleepLab analogue should be a **non-destructive, machine-scoped,
date-ranged, typed, reversible correction record** layered over raw device
timestamps. Concretely:

- **Machine-scoped.** Keyed to `cpap_machines.id`, consistent with the existing
  machine + `source_session_key` durable-identity boundary.
- **Import-run provenance.** Each correction records the `import_run_id` (and
  adapter/parser identity) that introduced or last modified it, matching
  SleepLab's audit-everything posture.
- **Original source timestamps retained.** Raw device times stay stored,
  untouched; the correction is applied as a *derived view*, never an in-place
  rewrite of sample/event/session rows.
- **Derived corrected timestamps.** A corrected time is computed on read from the
  active correction(s) for the machine and date range — constant `offset_ms` or
  the linear `c0 + c1·t` drift model.
- **Typed.** Reuse OSCAR's vocabulary (`timezone|travel|dst|reset|offset|drift`)
  so the cause is queryable and explainable.
- **Audited and reversible.** `applied_at` / `undone_at` history; a correction
  can be undone without losing the record that it ever existed.
- **Relates to `timezone_basis`.** The existing `timezone_basis` string remains
  the *label* of a timestamp's basis; a correction record is the *transform*
  layered on top. The two are complementary, not redundant.
- **UI explains corrected vs source time.** Any surfaced time that has been
  corrected must be distinguishable from the raw device time, with the correction
  type/reason available.

## 4. Explicit non-goals for Alpha 7

This milestone produces **only** this design note. For Alpha 7 there is:

- **No migration** and **no schema change** (no table, no column).
- **No automatic correction** — nothing infers or applies offsets/drift.
- **No retroactive rewrite** of existing session/event/sample timestamps.
- **No production behavior change** and **no import-routing change**.

## 5. Future implementation gates (stop-and-ask)

Before any of this is built, all of the following must be satisfied, and the work
itself remains a **stop-and-ask** item:

- **Fixture evidence.** A safe anonymized or synthetic fixture that actually
  exhibits the effect (wrong clock / travel / DST / reset / drift), so behavior
  is conformance-testable rather than speculative.
- **DST / travel tests.** Explicit tests for spring-forward/fall-back and
  timezone-crossing nights, including the date a corrected night is attributed to.
- **Duplicate-import stability.** Applying corrections must not break the
  duplicate/incremental identity-hash stability already proven for imports
  (`tests/test_conformance.py`, `tests/test_resmed_import_regressions.py`): a
  re-import must not churn persisted identities, and a correction must be a
  layered record, not a new identity.
- **Clear UI.** Corrected-vs-source time is visibly distinguished and explained.
- **Privacy review.** Corrections derive from real therapy dates/clocks; any
  fixture or exported artifact must follow the existing anonymization/
  redistribution rules (no PHI, real serials, or real dates committed).

## Cross-references

- `docs/sleeplab_2_crimson_structure_review.md` §10 — the OSCAR v17
  `device_time_corrections` findings this note expands.
- `docs/sleeplab_2_alpha_7_checklist.md` §5 — the checklist item this satisfies.
- `docs/sleeplab_2_data_architecture.md` — "Machine-scoped sessions and blocks"
  (machine-local date, `timezone_basis`) and "Known alpha limitations".
- `importer/loaders/models.py` — the current `timezone_basis` string on
  `Session` / `Capabilities` this would layer a transform over.
