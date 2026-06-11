# SleepLab 2.0 Release Roadmap

## Release principle

SleepLab 2.0 is a data-platform release, not only a new import screen. The
alpha should be as functionally complete as practical, but breadth must not be
confused with validated support. A manufacturer is supported only after real
fixtures pass conformance checks against source data and OSCAR reference
output.

The alpha exit criterion is:

> The complete loader lifecycle, normalized storage model, diagnostics, and
> review UI work end to end for ResMed and at least one non-ResMed family.

Additional manufacturers may be visible during alpha as detection-only or
experimental adapters. Their capability and validation state must be shown
honestly.

## What remains valid from PR #143

The architectural direction in
[PR #143](https://github.com/joshuamyers-dev/sleeplab/pull/143) remains valid.
The branch has already implemented part of its P0 foundation:

- root-folder upload instead of requiring `DATALOG`;
- registered structural detectors for ResMed, PRS1, Fisher & Paykel,
  Lowenstein, legacy BMC, and BMC G3X;
- canonical-root selection, detection evidence, confidence, identity peeking,
  capabilities, ambiguity handling, and source fingerprints;
- a normalized Python contract for machines, sessions, blocks, settings,
  channels, events, waveforms, derived values, source files, and warnings;
- deterministic review-before-import planning;
- native ResMed execution behind the adapter boundary;
- ResMed AirSense 10 identity extraction;
- explicit leak kind/unit metadata and normalized large-leak spans;
- a working GUI vertical slice through inspection, import, session review,
  charts, and event waveform inspection.

PR #143's remaining machine, import-run, settings, provenance, conformance, and
multi-vendor work is still required. PR #114 should be mined for parser mapping,
Docker packaging, provenance, and tests, but its machine-as-equipment model and
parallel non-ResMed import path should not be adopted as the final architecture.

## Alpha: architecture and deep vertical slices

Alpha is allowed to change schema and APIs. It should include all foundational
concepts needed by 2.0 so beta is hardening rather than another redesign.

### Required data foundation

- Persist a first-class `cpap_machines` record with manufacturer, family,
  model, product code, serial, firmware, adapter identity/version, and
  validation state.
- Scope source session identity to a machine. Remove dependence on
  `(user_id, session_id)` as the final uniqueness model.
- Persist `import_runs` and source-file manifests with fingerprint, adapter,
  files found/used/skipped, warnings, unknown data, validation status, start,
  completion, and failure details.
- Persist explicit `session_blocks` for mask-on/off therapy intervals and gaps.
- Persist versioned `settings_snapshots` rather than flattening a small set of
  settings onto the nightly session.
- Add signal metadata: normalized channel, source label, unit, sample rate,
  leak semantics, and validation state.
- Add event provenance and stable source identity so re-imports replace rather
  than duplicate events.
- Add derived-value provenance for AHI, percentiles, large leak, therapy score,
  reports, and AI inputs.
- Keep consumable equipment separate from CPAP machine identity.

### Required importer behavior

- Complete `Detect -> PeekInfo -> Capabilities -> Import` through one registry.
- Support roots, selected subdirectories, extracted archives, ambiguous roots,
  multiple machines, duplicates, incremental imports, and unknown layouts.
- Never silently import with a competing or low-confidence detector.
- Pin `cpap-parser` to an immutable tested revision and package it separately
  from adapter selection and SleepLab normalization.
- Persist diagnostics for unknown firmware, source labels, settings, events,
  and partial/corrupt files.
- Make import retryable and transactional at machine/session granularity.

### Required ResMed depth

- Parse `Identification.tgt` and `Identification.json`.
- Parse the selected `STR.edf` settings and summary fields needed for mode,
  pressures, EPR, ramp, humidification, mask, mask-on/off intervals,
  summary-only days, and timing repair.
- Preserve explicit session blocks and trustworthy wall-clock boundaries.
- Normalize settings, events, low-rate signals, leak, oximetry, and full-night
  flow/pressure waveform availability.
- Record why a waveform or setting is absent instead of showing only
  "unavailable."
- Compare native and `cpap-parser` ResMed output without writing parser-backed
  ResMed data to production until conformance gates pass.

### Required non-ResMed proof

- Deliver one full non-ResMed vertical slice in alpha. Lowenstein Prisma is the
  preferred first parser-backed target because its parser implementation and
  available sample evidence are currently strongest.
- Deliver PRS1/DreamStation detection and identity plus a conformance fixture.
  Production persistence may remain experimental until native-card session and
  waveform behavior is validated.
- Keep Fisher & Paykel and BMC detection visible, but block import when no
  conformance fixture proves the advertised capability.
- Show experimental/partial status in both the inspection result and imported
  session provenance.

### Required conformance and fixtures

- Add a fixture manifest with anonymization record, immutable hashes, device
  family, expected capabilities, and redistribution status.
- Add a CLI/test harness that compares normalized SleepLab output with
  `cpap-parser` and OSCAR reference exports.
- Cover identity, settings, session/block boundaries, event counts/timestamps,
  leak kind/unit, pressure statistics, timezone/DST, waveform channels/sample
  rates, duplicate imports, and unknown data.
- Require at least two independent ResMed fixtures and one Lowenstein fixture
  before alpha exit.
- Obtain at least one PRS1/DreamStation fixture during alpha even if its adapter
  remains experimental.

### Required user experience

- Show import history and diagnostics in the web app.
- Show machine identity and validation status on session/equipment views.
- Show settings history and changes by night.
- Explain partial capability, summary-only data, missing waveform data, and
  unknown signals in plain language.
- Keep root-folder selection and review-before-import.
- Keep session, event, waveform, leak-span, oximetry, notes, tags, trends,
  reports, and AI features working on normalized data.
- Add progress/cancellation behavior for large cards and remove avoidable
  event-inspector and chart performance bottlenecks.

### Alpha exit gate

Alpha is complete when all of the following are true:

1. Schema concepts above are persisted and used by production import.
2. ResMed root import includes identity, selected STR settings, blocks,
   events, signals, and waveform provenance.
3. One non-ResMed family imports end to end through the same contract.
4. Real anonymized fixtures produce stored conformance reports.
5. Duplicate/incremental import and timezone/DST tests pass.
6. The GUI exposes machine, settings, import warnings, and validation status.
7. Unsupported cards fail safely without misleading partial data.

## Beta: validation, compatibility, and hardening

Beta should not introduce another core storage redesign.

- Validate ResMed, Lowenstein, PRS1/DreamStation, and Fisher & Paykel with real
  fixtures. Treat BMC/BMC G3X as experimental until fixtures pass.
- Run native and parser-backed ResMed in shadow comparison long enough to make
  an evidence-based retirement decision.
- Backfill existing 1.x/early-alpha sessions into machine/import provenance
  records without losing notes, tags, oximetry, or AI history.
- Validate full-night waveform storage size, query performance, downsampling,
  retention, and backup behavior.
- Add import cancellation, crash recovery, concurrency protection, and useful
  progress estimates.
- Validate reports, therapy score, adherence, trends, and AI cache
  invalidation against corrected normalized inputs.
- Complete privacy review for uploaded filenames, serials, manifests, logs,
  exports, and sharing.
- Test clean install, upgrade, restore, Docker, reverse proxy, and common
  self-hosted deployment paths.
- Freeze the adapter contract, normalized channel names, settings keys, API
  shapes, and migration policy before the release candidate.
- Publish the supported-device matrix with exact capability and validation
  levels, not manufacturer-wide promises.

### Beta exit gate

1. No known import can corrupt or silently misclassify existing data.
2. Every advertised supported family has at least one real fixture and OSCAR
   comparison; major families should have two.
3. Upgrade and rollback procedures are tested on a copy of a 1.x database.
4. Full-card imports remain usable on ordinary self-hosted hardware.
5. Reports and scores disclose unavailable or unvalidated inputs.
6. Adapter/API/schema contracts are frozen for 2.0.

## SleepLab 2.0 stable

The stable release should contain:

- the loader registry and versioned normalized contract;
- durable machine identity and machine-scoped sessions;
- import history, source provenance, diagnostics, and idempotency;
- session blocks and settings history;
- normalized events, signal semantics, waveform availability, and derived
  values;
- complete validated ResMed support;
- validated production support for at least Lowenstein and PRS1/DreamStation;
- Fisher & Paykel production support if fixture gates pass, otherwise an
  explicitly experimental adapter;
- preserved oximetry, notes, tags, equipment, reports, trends, event inspector,
  AI summaries, APIs, and web sharing foundations;
- documented upgrades, backups, exports, supported devices, limitations, and
  privacy behavior.

Native ResMed parsing may remain in 2.0 if parser parity is not proven. Removing
working native parsing is not a release requirement; trustworthy normalized
output is.

## Post-2.0 work

These are valuable but should not block a trustworthy 2.0:

- broad long-tail manufacturer coverage beyond validated fixtures;
- automatic cloud sync integrations and richer NAS/card watchers;
- clinician portals, public share links, and collaborative annotations;
- mobile/PWA packaging and offline-first use;
- advanced clock-drift correction UI;
- arbitrary channel/plugin authoring by users;
- long-term raw-card archival and remote reprocessing services;
- population comparisons, cohort analytics, and advanced AI interpretation;
- polished OSCAR layout customization and every OSCAR graph preference;
- automated fixture donation/anonymization tooling.

## Immediate implementation order

The alpha branch now persists STR settings snapshots and source-defined
therapy blocks, derives machine-local nightly usage/span/gaps, and routes
score, adherence, reports, trends, and AI duration inputs through that
aggregate. One restricted AirSense 10 card and synthetic fixtures prove the
implemented path, but do not justify a full ResMed validation claim.

**Phase 2 fixture-backed validation status (not RC/beta readiness):** the only
*committed-fixture-backed* `expected.import` coverage today is the OSCAR
reference `export_hash` — a parser-free sha256 integrity pin on the AirSense 10
fixture's committed, anonymized `summary.csv` and `sessions.csv`. Every other
import-level comparator (`warnings`, `session_blocks`, `therapy_aggregates`,
`settings`, `events`, `identity_hashes`) remains **injected-only**, and OSCAR
**numeric parity**, the `settings.values` loader mapping, and Lowenstein stay
blocked/deferred. This is conformance depth, not validated multi-family support;
it does not move any device to **validated** or imply beta. See
`docs/sleeplab_2_fixture_validation_matrix.md` for the per-fixture breakdown and
`docs/sleeplab_2_validation_inputs.md` for how new safe evidence can be
contributed.

1. Validate ResMed BRP/SA2 channel inventory and full-night waveform storage,
   downsampling, query performance, retention, and absence diagnostics.
2. Extend conformance manifests with persisted settings, interval boundaries,
   usage/span/gaps, duplicate hashes, incremental nights, and OSCAR references.
3. Obtain a second independent anonymized ResMed fixture.
4. Add the first Lowenstein read-only normalized fixture comparison.
5. Pin/package `cpap-parser` and implement Lowenstein read-only conformance.
6. Enable Lowenstein persistence only after its fixture gate passes.
7. Add PRS1/DreamStation fixture-backed import work.
8. Add import cancellation/progress and worker heartbeat recovery.
9. Expand source-file drill-down and settings-change presentation.
10. Begin beta only after the alpha exit gate is met.
