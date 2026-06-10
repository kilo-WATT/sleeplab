# SleepLab 2.0 Crimson-Structure Review

Status: Alpha 7 planning review only. No production code, import routing, schema,
Lowenstein persistence, ResMed cutover, full-night waveform storage, or tag work is
included here.

## Scope and source availability

This review compares the current SleepLab 2.0 normalized import/conformance
architecture against the available OSCAR/crimson-structure architectural direction.
The local `crimson-structure` repository or uploaded files were not available in
this environment, and public repository lookup for `open-cpap/crimson-structure`
did not resolve. Treat this document as an Alpha 7 planning baseline grounded in
SleepLab's existing OSCAR/cpap-parser architecture notes and current code. Once the
actual crimson-structure material is available locally, this review should be
updated with file-specific observations.

The SleepLab-side sources reviewed were:

- `docs/sleeplab_2_data_architecture.md`
- `docs/sleeplab_2_loader_and_conformance_plan.md`
- `docs/sleeplab_2_release_roadmap.md`
- `docs/sleeplab_2_alpha_6_checklist.md`
- `docs/sleeplab_2_import_level_conformance_plan.md`
- `importer/loaders/models.py`
- `importer/conformance.py`
- `importer/db.py`
- `importer/loaders/resmed_native.py`
- `importer/loaders/persist.py`
- `importer/waveform_estimate.py`
- referenced regression/conformance test areas

## 1. What crimson-structure is trying to document or represent

Based on the name and the existing SleepLab notes about OSCAR's loader lifecycle,
`crimson-structure` appears to be an architecture/reference description of CPAP
card structures and OSCAR-style import concepts rather than a complete SleepLab
runtime dependency. It should be read as evidence about how mature OSCAR-style
importers separate these concerns:

1. Detecting a card or source root by structure.
2. Peeking machine identity before parsing full therapy data.
3. Reporting per-device capabilities and unsupported/untested data.
4. Opening/importing only after a machine and source have been selected.
5. Mapping vendor-specific values into a normalized channel/session/event model.
6. Recording unexpected data rather than silently guessing.

That is the same conceptual shape already captured in SleepLab's loader plan: a
registry, structural detectors, identity peeking, capabilities, import execution,
normalized sessions, settings, channels, events, waveforms, derived values, and
warnings.

## 2. Dependency/schema/package or architecture reference?

Treat crimson-structure as an OSCAR architecture reference unless the actual local
material proves otherwise.

It should not be adopted as:

- a direct database schema for SleepLab;
- a required Python package;
- a replacement for SleepLab's loader contract;
- a reason to bypass SleepLab conformance gates;
- a reason to enable Lowenstein, parser-backed ResMed, or full-night waveform
  persistence without fixture evidence.

The correct Alpha 7 posture is to mine it for concepts, naming, lifecycle gaps,
and fixture expectations, then translate the useful parts into SleepLab's
web/database-native model.

## 3. Concept mapping to SleepLab normalized architecture

### `cpap_machines`

OSCAR/crimson-style architecture treats the machine as the durable identity for
imports. SleepLab aligns with this by storing manufacturer, family, model, product
code, serial, firmware, adapter identity/version, source identity, support state,
validation state, and first/last seen timestamps separately from consumable
equipment.

Mapping:

- OSCAR loader name + serial -> SleepLab `adapter_id` + normalized serial.
- OSCAR machine profile -> SleepLab `cpap_machines` row.
- Unknown or missing serial -> SleepLab unresolved/source-fingerprint-scoped
  machine identity, not the literal string `Unknown`.

Alpha 7 implication: keep machine identity as the anchor for every conformance and
import decision. Do not move CPAP machine identity back into equipment records.

### `import_runs`

OSCAR's import context aggregates source handling, duplicate checks, warnings, and
commit behavior. SleepLab maps this into durable `import_runs` plus
`import_source_files`.

Mapping:

- OSCAR import context -> SleepLab import run.
- Unexpected-data log -> structured `import_runs.warnings`.
- Source path scanning -> source manifest with relative path, role, hash,
  disposition, parser component, and diagnostics.
- Reviewed import plan -> content-addressed source fingerprint.

Alpha 7 implication: crimson-structure should reinforce import-run provenance and
diagnostics, not replace it.

### `sessions`

OSCAR emits therapy/session objects from vendor loaders. SleepLab maps these to
machine-scoped sessions with `machine_id`, `import_run_id`, `source_session_key`,
`machine_local_date`, provenance, start/end, duration, and compatibility summary
columns.

Mapping:

- OSCAR session -> SleepLab `sessions` row.
- Vendor source session key -> SleepLab `source_session_key`.
- Profile-local storage -> SleepLab machine-scoped uniqueness.

Alpha 7 implication: session uniqueness must remain machine-scoped. Avoid any
change that falls back to `(user_id, session_id)` as the final identity boundary.

### `session_blocks`

OSCAR loaders preserve source-defined recording or therapy intervals. SleepLab
maps these to `session_blocks`, including ResMed STR mask-on/mask-off intervals.

Mapping:

- OSCAR source-defined intervals -> SleepLab `session_blocks`.
- ResMed STR therapy intervals -> `resmed_str_mask_interval` blocks.
- Legacy single-span sessions -> one conservative legacy block.

Alpha 7 implication: interval-boundary conformance should become a priority before
claiming broader parser parity.

### `settings_snapshots`

OSCAR has machine and session setting channels. SleepLab maps settings into
versioned normalized/vendor-specific snapshots with source names, source file IDs,
adapter identity, parser identity, confidence, validation, and diagnostics.

Mapping:

- OSCAR setting channels -> SleepLab normalized settings keys plus vendor JSON.
- Unknown setting values -> diagnostics and vendor JSON, not guessed normalized
  values.
- Effective settings for a night -> `settings_snapshots`.

Alpha 7 implication: settings value conformance remains a meaningful gap. Presence
checks exist; value-level comparisons should follow once loader support is deep
enough.

### `signal_channels`

OSCAR has a shared channel registry and vendor loaders register or map channel
semantics. SleepLab maps this to `signal_channels` rows with normalized/source
name, units, sample rate, channel kind, value kind, leak semantics, adapter,
source file, confidence, and validation.

Mapping:

- OSCAR channel taxonomy -> SleepLab normalized channel names.
- Vendor labels such as `Flow.40ms`, `Press.40ms`, `Leak.2s`, `SpO2.1s` ->
  `signal_channels.source_label` plus normalized channel key.
- Missing channel -> no fabricated row; absence belongs in diagnostics/capability
  state.

Alpha 7 implication: reconcile remaining unit/name divergences between the native
path and cpap-parser path before any parser-backed ResMed cutover.

### `session_events`

OSCAR stores scored events with source-derived event types/timestamps. SleepLab
maps these to `session_events` with source event key/type, adapter identity,
source-file provenance, confidence, validation, and replace-on-import behavior.

Mapping:

- OSCAR event -> SleepLab `session_events` row.
- Vendor event code -> normalized event type plus retained source type.
- Re-import replacement -> stable source event keys.

Alpha 7 implication: event count/type/timestamp conformance against OSCAR reference
exports should stay high priority.

### Waveform/event-window storage

OSCAR can work with detailed high-rate signals. SleepLab intentionally differs:
Alpha 6 decided to persist high-rate ResMed BRP flow/pressure only in event
windows, while low-rate PLD metrics are persisted full-resolution and channel
metadata is persisted for all decoded channels.

Mapping:

- OSCAR full signal availability -> SleepLab channel metadata plus selectively
  persisted samples.
- High-rate BRP flow/pressure -> event-window `session_waveform` rows.
- Low-rate PLD values -> full-resolution `session_metrics`.
- Whole-night BRP storage -> deferred design decision, not Alpha 7 default.

Alpha 7 implication: crimson-structure may describe full detailed signals, but
SleepLab should keep event-window storage until a deliberate retention,
downsampling, query-performance, and UI/API design is approved.

### `derived_values`

OSCAR produces calculated summaries and statistics. SleepLab maps these into
`derived_values` with method/version, unit, input references, machine/session/import
ownership, adapter, and validation.

Mapping:

- OSCAR summary values -> SleepLab derived values with provenance.
- AHI, pressures, leak, usage/span/gap, therapy score inputs -> derived values or
  nightly aggregate inputs.
- Disagreements -> diagnostics/conformance failures, not silent overwrites.

Alpha 7 implication: expand parity checks for source summary values and computed
nightly aggregates separately, because ResMed already has multiple valid usage
semantics.

### Nightly therapy aggregates

OSCAR-style reports tend to reason by therapy day/night. SleepLab maps this into
`nightly_therapy_aggregates`: source session, therapy blocks, machine-local date,
usage duration, wall-clock span, gap duration, and summary-reported usage.

Mapping:

- OSCAR therapy day -> SleepLab machine-local aggregate.
- Mask-on/mask-off intervals -> usage seconds.
- First-on/last-off -> wall-clock span.
- Difference -> gap seconds.

Alpha 7 implication: keep aggregate semantics as the authoritative path for UI,
reports, adherence, AI duration inputs, and conformance.

### Conformance fixtures

OSCAR/crimson-style reference material is useful only if SleepLab converts it into
reproducible fixtures and expected outputs. SleepLab already has planning-level and
import-level conformance paths, with import-level checks partially implemented.

Mapping:

- OSCAR reference exports -> manifest `expected.import.oscar_reference`.
- Fixture metadata/anonymization -> manifest privacy/provenance fields.
- Parser-normalized output -> import-level parse-observable checks.
- Persisted idempotency -> DB-gated identity hash checks.

Alpha 7 implication: real progress means fixture-backed checks, not just broader
claims of manufacturer support.

## 4. Where SleepLab already aligns with OSCAR/crimson-structure

SleepLab already aligns in the most important architectural areas:

1. Loader lifecycle is separated into detect, peek identity, capabilities, and
   import execution.
2. Detection is structural and evidence-based, not manufacturer-name guessing.
3. Machine identity is durable and separate from consumable equipment.
4. Source provenance is modeled explicitly through import runs and source files.
5. Sessions are machine-scoped and source-keyed.
6. Explicit therapy blocks are first-class.
7. Settings are versioned snapshots, not only flattened nightly columns.
8. Signals retain normalized names, source labels, units, rates, and leak semantics.
9. Events retain source identity and are replaceable on re-import.
10. Derived values carry method/provenance instead of being anonymous summaries.
11. Unknown or absent data is represented through diagnostics/capability state,
    not plausible zeros or fake values.
12. Alpha/beta support claims are tied to fixtures and conformance.

## 5. Where SleepLab has gaps

The biggest gaps are not conceptual; they are validation and depth gaps:

1. The actual crimson-structure material still needs to be inspected locally and
   cited file-by-file.
2. OSCAR numeric parity is still mostly design-only; the reference-hash check is
   present, but row/value parity is not complete.
3. Settings snapshot value comparison is incomplete.
4. Session block interval-boundary comparison is incomplete.
5. Event timestamp/type/count parity needs more fixture coverage.
6. ResMed native path and cpap-parser path still have known unit/name divergences
   for some channels.
7. Oximetry sample persistence is uneven: native path writes `session_spo2`, while
   the cpap-parser persistence path still reports the gap.
8. Lowenstein read-only normalized conformance has not yet become the first
   non-ResMed vertical slice.
9. Full-night waveform storage is intentionally deferred, but the product still
   needs a later design for storage, retention, downsampling, query performance,
   backup impact, and UI/API behavior.
10. Multi-machine edge cases need continued fixture-backed testing beyond the
    structural detector layer.

## 6. Where SleepLab intentionally differs because it is web/database-native

SleepLab should not copy OSCAR's architecture literally. The useful distinction is
that OSCAR is primarily a desktop profile application, while SleepLab is a
self-hosted web/database application.

Intentional differences:

1. Durable import history matters more in SleepLab; every import should be
   explainable after the fact.
2. Source manifests and content fingerprints are first-class because imports can
   come from uploads, archives, local paths, or future services.
3. Database identity must be idempotent and transactional; source-key replacement
   is safer than UI-profile mutation.
4. Privacy handling must be stricter because filenames, serials, logs, and
   uploaded manifests may be exposed through web UI, backups, or support bundles.
5. Full-night waveform storage has larger operational consequences in Postgres
   than in a local desktop profile, so event-window storage is a reasonable Alpha
   limitation.
6. Capability status must be visible to the user because unsupported cards may be
   detected but intentionally blocked.
7. Conformance must distinguish planning-only checks, parse-observable checks,
   persisted DB checks, and OSCAR reference parity.

## 7. Suggested timing: Alpha 7, later alpha, beta, post-2.0

### Alpha 7

Alpha 7 should focus on review, conformance depth, and parser/backend readiness:

1. Inspect actual crimson-structure material once available and update this review
   with file-specific notes.
2. Convert any useful crimson/OSCAR concepts into explicit SleepLab conformance
   expectations, not production behavior changes.
3. Implement import-level conformance improvements that do not require routing or
   schema changes: settings value comparison where data already exists, block
   interval comparison, and event count/type/timestamp comparison.
4. Add or prepare a read-only Lowenstein normalized fixture comparison, without
   persistence.
5. Reconcile ResMed channel naming/unit discrepancies in the parser mapping plan
   before any production cutover.
6. Strengthen OSCAR reference export parity checks.

### Later alpha

Later alpha work should include:

1. A second independent anonymized ResMed fixture.
2. Lowenstein parser-backed read-only conformance passing consistently.
3. Lowenstein persistence only after fixture gates pass and after explicit approval.
4. PRS1/DreamStation fixture-backed detection and identity.
5. Import cancellation/progress and worker-heartbeat recovery.
6. Source-file drill-down and settings-change presentation.

### Beta

Beta should focus on hardening and compatibility:

1. Validate all advertised supported families with real anonymized fixtures and
   OSCAR reference exports.
2. Run native ResMed and parser-backed ResMed in shadow comparison before deciding
   whether to retire native parsing.
3. Test upgrades/backfills from 1.x and early-alpha data.
4. Freeze adapter contracts, normalized channel names, settings keys, APIs, and
   migration policy.
5. Validate full-card performance on ordinary self-hosted hardware.

### Post-2.0

Post-2.0 work should include:

1. Broad long-tail device coverage.
2. Automated fixture donation/anonymization tooling.
3. Full raw-card archival and remote reprocessing services.
4. Advanced user-configurable OSCAR-style chart preferences.
5. Population/cohort analytics and richer AI analysis.

## 8. Recommended Alpha 7 priorities

Recommended order:

1. Get the actual crimson-structure material into the local workspace or document
   its exact path.
2. Update this review with concrete references to crimson files and concepts.
3. Add Alpha 7 checklist items derived from this review.
4. Deepen import-level conformance in this order:
   - session block interval boundaries;
   - settings values/presence semantics;
   - event count/type/timestamp parity;
   - OSCAR numeric/reference parity;
   - DB identity hash expansion only where it remains read-only/test-gated.
5. Prepare the first Lowenstein read-only normalized conformance fixture.
6. Keep Lowenstein persistence, ResMed parser cutover, full-night waveform storage,
   and broad UI/API changes explicitly blocked until later approval.

## 9. Bottom line

Crimson-structure should be treated as a reference map for OSCAR-like import
architecture, not as a drop-in SleepLab schema or dependency. SleepLab's current
2.0 architecture is already pointed in the right direction: durable machine
identity, audited import runs, normalized sessions/blocks/settings/signals/events,
explicit derived values, capability states, absence diagnostics, and fixture-gated
support claims.

Alpha 7 should therefore avoid broad implementation and instead turn the
crimson/OSCAR reference into sharper conformance coverage and a better task order
for the first non-ResMed vertical slice.
