# SleepLab 2.0 Alpha 6 Working Checklist

Milestone: `v2.0.0-alpha.6` (annotated tag on `develop/2.0`, per `AGENTS.md` —
**not** a new branch).

This checklist operationalizes the "Next milestone" / "Immediate
implementation order" sections of the three SleepLab 2.0 docs:

- `docs/sleeplab_2_data_architecture.md` — "Next milestone" (validate ResMed
  waveform/full-night storage and BRP/SA2 channel inventory, sample rates,
  retention, query performance) and "Known alpha limitations" (BRP samples are
  event-window-focused, not full-night).
- `docs/sleeplab_2_loader_and_conformance_plan.md` — "Conformance testing
  strategy" (waveforms, session boundaries, settings, duplicate/incremental
  imports) and "Recommended implementation sequence".
- `docs/sleeplab_2_release_roadmap.md` — "Immediate implementation order"
  items 1–2, and "Required ResMed depth" / "Required conformance and fixtures".

Alpha 6 sits **before** Lowenstein read-only conformance and **before** any
ResMed cutover. It does not begin beta.

## Current baseline (grounding)

- High-rate BRP samples are persisted to `session_waveform` (migration
  `013_add_session_waveform.sql`) as **event-windowed** `flow`/`pressure` only
  — merged windows around scored events, explicitly *not* full-night
  (`importer/loaders/persist.py`, `importer/db.py:replace_session_waveform`).
- Low-rate PLD metrics are persisted full-resolution to `session_metrics`.
- Channel **metadata** is persisted to `signal_channels` (migration
  `023_add_cpap_data_foundation.sql`), classified `waveform` when
  `sample_rate_hz >= 5`, else `low_rate`.
- BRP is parsed by `importer/edf_parser.py:parse_brp` (Flow.40ms / Press.40ms,
  ~25 Hz). SA2/SAD oximetry is parsed (`parse_sa2`) but **not mapped** into the
  loader/persist path yet (`persist.py`: `has_spo2: False`, "Oximetry is not
  mapped by the loader yet").
- Conformance harness (`importer/conformance.py`) validates manifest metadata +
  detection/identity/capabilities/coverage against a checked-in `manifest.json`.
  Coverage currently covers `therapy_days`, `estimated_session_blocks`,
  `event_files`, `waveform_files`, `settings_files` only.
- Fixtures: `fixtures/conformance/synthetic-resmed-minimal/` (CLI harness) and
  `tests/conformance/fixtures/resmed_airsense10_001/` (anonymized AirSense 10,
  OSCAR summary/sessions reference).

## Alpha 6 must-do

Scope = the five focus areas in the kickoff brief, mapped to docs.

### 1. ResMed BRP/SA2 channel inventory
- [ ] Enumerate every channel ResMed BRP and SA2/SAD files actually carry on
      the AirSense 10 fixture; record source label, unit, sample rate, value
      kind, and leak semantics. (`signal_channels` already models these.)
- [x] **(done)** SA2/SAD oximetry channel *metadata* is inventoried:
      `SpO2.1s` → `spo2` (unit `%`), `Pulse.1s` → `pulse` (unit `bpm`),
      classified `low_rate` at 1 Hz, units sourced from the EDF `dim` field
      (commit `05261ea`, `importer/db.py:_normalized_signal` +
      `replace_signal_channels`). Oximetry **sample** persistence through the
      cpap-parser loader path remains a separate open gap (see §4 /
      `persist.py` `has_spo2: False`).
- [ ] Verify the `sample_rate_hz >= 5` → `waveform` classification in
      `persist.py` matches the real BRP/SA2 rates; capture any channel that
      misclassifies.

### 2. Full-night waveform storage validation
- [ ] Measure stored vs. recorded coverage: today only event-windowed BRP is
      stored. Decide and document the Alpha 6 target (still event-windowed, or
      a validated full-night option behind a flag) — do not silently change the
      default.
- [ ] Validate segment start/end, sample counts, and missing spans against the
      decoded EDF for at least one detailed night (`WaveformSegment` semantics
      from the loader plan).
- [ ] Confirm flow + pressure at minimum (loader plan "Waveforms" section).

### 3. Downsampling, query performance, retention
- [ ] Estimate full-night 25 Hz row counts and storage size per night/per card
      (data-architecture "Next milestone": retention + query performance).
- [ ] Benchmark `session_waveform` read queries used by the Event Inspector /
      charts; record the current index (`idx_session_waveform_session_id_ts`)
      behavior under a multi-night load.
- [ ] Draft (do not yet implement) downsampling + retention options; capture
      trade-offs. Implementation of a new storage scheme is *later alpha / beta*.

### 4. Absence diagnostics for missing waveform/settings data

These five concerns are deliberately **distinct** tiers — absence at one tier
does not imply absence at another, and each needs its own diagnostic:

- **Waveform metadata inventory** — channel names/units/rates/classification in
  `signal_channels`. (BRP flow/pressure + SA2 SpO2/pulse: *done*, §1.)
- **Event-window waveform storage** — high-rate `flow`/`pressure` samples around
  scored events in `session_waveform`. (Current default; partial.)
- **Full-night waveform storage** — whole-night high-rate samples. (Not
  implemented; see §2 — do not implement yet.)
- **Oximetry sample persistence** — per-sample SpO2/pulse rows. Native path
  writes `session_spo2`; the cpap-parser loader path does **not** yet
  (`persist.py` `has_spo2: False`). (Open gap.)
- **Absence diagnostics** — recording *why* any of the above is absent.

Items:
- [ ] When a waveform or setting is absent, record *why* (summary-only night,
      malformed PLD header, capability unavailable) rather than only
      "unavailable" — roadmap "Required ResMed depth".
- [ ] Surface absence through structured warnings / capability state, not a
      plausible zero (loader-plan invariant: "absence is `None`/empty, never a
      plausible zero or `Unknown`").
- [x] **(in progress)** Regression coverage pins the existing absence
      diagnostics: the conformance harness *detects* missing waveform coverage
      (`tests/test_conformance.py`), and the native loader emits the structured
      `resmed_summary_only_day` warning for a night with no detailed DATALOG
      waveform/metric source — kept and flagged, not silently empty
      (`tests/test_resmed_import_regressions.py`).
- [x] **(done)** The native loader distinguishes a detailed night that is
      *missing only its BRP waveform* (PLD/session data present, BRP
      absent/malformed) by emitting `resmed_waveform_absent`
      (severity `warning`, `affects=("waveforms",)`, `relative_path="DATALOG"`).
      It flags waveform availability without questioning the session's
      existence, keeps the run from being forced partial (gap, not parse
      failure), and fabricates no high-rate samples. Tested in
      `tests/test_resmed_import_regressions.py`. The four ResMed waveform tiers
      are now diagnosable: summary-only night, detailed-without-BRP, detailed
      event-window waveform, and (future) full-night storage.
- [x] **(done)** Absence diagnostics are now *persisted and visible*, not only
      in-memory. `_build_session` flushes each night's `session.warnings` to the
      run-level list, so the cpap-parser execution path serializes them
      (`execution._warning_dict` -> `finish_import_run`) into
      `import_runs.warnings` (JSONB, migration 023) and they surface through the
      existing import-history API (`api/routers/imports.py:list_import_runs`).
      Structured fields (code/severity/affects/relative_path) are retained — not
      collapsed to a string — and `finish_import_run` dedupes identical entries
      so repeated ghost/waveform-absent nights collapse to one. Tested in
      `tests/test_resmed_import_regressions.py`.
- [x] **(decided — Option B)** Waveform absence is **not** represented in
      `signal_channels`. That table is a *presence* inventory: both writers
      (`persist._write_signal_channels`, `db.replace_signal_channels`) emit a row
      only for a channel that actually carries data, each stamping a fixed
      `validation_status='partial'`. A missing BRP waveform is therefore the
      *absence of the `flow_rate`/`pressure` rows*, not a downgraded row —
      marking it on `signal_channels` would require fabricating a channel with no
      samples, violating the loader-plan invariant "absence is `None`/empty,
      never a plausible zero" and the "do not fabricate channels" rule. Waveform
      absence is correctly owned by import-run diagnostics
      (`resmed_waveform_absent`, persisted to `import_runs.warnings`) and
      capability coverage (`Capabilities.waveforms` /
      `import_runs.capability_status`). Pinned by
      `test_missing_brp_waveform_does_not_fabricate_signal_channels`
      (`tests/test_resmed_import_regressions.py`). No schema change.
      (Persistence here is the opt-in cpap-parser execution route behind
      `SLEEPLAB_USE_CPAP_PARSER`; the legacy native subprocess persists its own
      `run_stats["warnings"]` separately.)

### 5. Conformance manifest expansion
Extend the manifest/`importer.conformance` contract (roadmap item 2) to cover:
- [x] **(started)** expected diagnostics: optional
      `expected.diagnostics.warning_codes` asserts that specific structured
      warning codes are surfaced by the plan (e.g. `resmed_missing_str`).
      `validate_fixture` collects codes from inspection- and device-level
      warnings (`_plan_diagnostic_codes`) and fails when an expected code is
      absent. Backward-compatible: fixtures without the block are unaffected.
      Tested (backward-compat, present, absent) in `tests/test_conformance.py`.
      *Scope note:* only detection/planning diagnostics are observable here;
      import-time codes (`resmed_summary_only_day`/`resmed_waveform_absent`)
      need the cpap-parser execution path and are asserted in
      `tests/test_resmed_import_regressions.py` instead — extending the manifest
      to cover those is a later subtask once an import-run conformance path
      exists.
- [ ] **(import-level)** persisted settings snapshots (mode, pressures, EPR,
      ramp, humidification, mask) — compared, with "missing ≠ off";
- [ ] **(import-level)** interval boundaries (session-block start/end,
      mask-on/off);
- [ ] **(import-level)** usage / wall-clock span / gap (the nightly aggregate
      semantics);
- [ ] **(import-level)** duplicate-import stable hashes (persisted UUID sets
      unchanged on re-import);
- [ ] **(import-level)** incremental nights (adding a newer night leaves
      existing identities unchanged);
- [ ] OSCAR references (version/commit + export hashes) as first-class manifest
      fields, required before a capability may claim `validated`.

**Observability boundary.** The current `importer.conformance` harness is
*planning-only*: it runs `create_import_plan`, whose `CoverageSummary` is
derived from file inventory and directory structure, and it never decodes
EDF payloads or runs nightly aggregation. So the items marked **(import-level)**
above (settings *values*, interval *boundaries*, usage/span/gap, duplicate and
incremental persisted-identity hashes) **cannot** be observed by the present
checker and must not be faked with file-count proxies. They require the
import-level conformance path described in §6. What *is* observable today:
detection/identity, capabilities, file-count coverage (therapy_days,
session-block/waveform/event/oximetry/settings file counts, first/last date),
and detection/planning diagnostics. Pinned by
`test_conformance_coverage_cannot_observe_therapy_aggregates`
(`tests/test_conformance.py`).

Keep manifest additions **backward compatible**: existing synthetic and
AirSense 10 fixtures must continue to pass while new optional fields are
introduced.

## Later alpha (after Alpha 6, still pre-beta)

Per roadmap "Immediate implementation order" 3–9 and loader-plan sequence:

- [ ] Obtain a second independent anonymized ResMed fixture (immutable hash).
- [ ] First Lowenstein **read-only** normalized fixture comparison (no writes).
- [ ] Pin/package `cpap-parser` to an immutable tested revision, separated from
      adapter selection and normalization.
- [ ] Lowenstein read-only conformance adapter (detect/peek/normalize to JSON).
- [ ] PRS1 / DreamStation fixture-backed detection + identity (import may stay
      experimental).
- [ ] Import cancellation / progress + worker heartbeat recovery.
- [ ] Source-file drill-down and settings-change presentation in the UI.

## Beta-only (do NOT start in Alpha 6)

Per roadmap "Beta: validation, compatibility, and hardening":

- [ ] Implement and ship full-night waveform storage size / retention / backup
      validation as a hardening deliverable (beta exit gate 4).
- [ ] Native + parser-backed ResMed shadow comparison soak and the retirement
      decision.
- [ ] Backfill existing 1.x / early-alpha sessions into machine/import
      provenance.
- [ ] Concurrency protection, crash recovery hardening beyond stale-run
      recovery.
- [ ] Privacy review of filenames, serials, manifests, logs, exports, sharing.
- [ ] Freeze adapter contract / channel names / settings keys / API shapes.
- [ ] Publish the supported-device matrix with exact capability/validation.

## Explicit no-go (this milestone)

These are hard constraints for Alpha 6 work:

- [ ] **Do not enable Lowenstein persistence.** Read-only conformance only,
      and only later in alpha after its fixture gate passes.
- [ ] **Do not route ResMed through `cpap-parser` in production.** Native ResMed
      stays the production path and regression oracle until conformance gates
      pass and a cutover decision is made.
- [ ] **Do not create a new alpha branch.** Work stays on `develop/2.0`;
      milestones are annotated tags. No `codex/…` or `claude/…` branches.
- [ ] **Do not create tool-specific folders** (`codex/`, `claude/`,
      `codex-notes/`, `claude-notes/`). Docs live in `docs/`; agent
      coordination notes in `dev-notes/`.
- [ ] **Do not begin beta work** (see beta-only list above).
- [ ] **Do not delete summary-only ("ghost") nights** or store literal
      `"Unknown"` identity; absence stays explicit.
- [ ] **Do not destructively rewrite** the import path; prefer small,
      conformance-testable increments.

## First implementation change (landed this milestone)

Per the kickoff brief ("make the first implementation change small … a
checklist/doc update plus one narrow test or diagnostic improvement"):

1. This checklist (`docs/sleeplab_2_alpha_6_checklist.md`).
2. A narrow regression test in `tests/test_conformance.py` proving the
   conformance harness genuinely **detects** a waveform-coverage discrepancy
   (absence diagnostics, must-do item 4 / manifest item 5) rather than passing
   vacuously. No production import routing, schema, or UI changes.

## Alpha 6 exit (working definition)

Alpha 6 is "done enough" to move to the next item when:

1. BRP/SA2 channel inventory for the AirSense 10 fixture is documented with
   units/rates/value-kinds and any misclassification recorded.
2. Waveform storage scope (event-window vs. full-night) is explicitly decided
   and documented, with measured size/query implications.
3. Absence of waveform/settings data is recorded with a reason and asserted by
   tests.
4. The conformance manifest contract is extended (backward compatibly) for
   settings, interval boundaries, usage/span/gaps, duplicate hashes,
   incremental nights, and OSCAR references, with existing fixtures still green.
