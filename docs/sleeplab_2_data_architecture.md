# SleepLab 2.0 Alpha Data Architecture

## Scope

SleepLab 2.0 treats a CPAP import as an auditable transformation from a source
card into machine-scoped therapy data. The legacy `sessions` table remains the
read path for existing charts, reports, notes, tags, oximetry, and AI features,
but it is no longer the owner of machine identity or provenance.

Migration `023_add_cpap_data_foundation.sql` is additive and backfills existing
data. It does not delete sessions, events, metrics, waveforms, oximetry, notes,
tags, equipment, or AI cache rows.

## Durable models

### CPAP machines

`cpap_machines` stores durable identity separately from consumable equipment:

- manufacturer, family, model, product code, serial, firmware, and data format;
- adapter ID/version and source identity fields;
- identity confidence;
- support state: `supported`, `validated`, `experimental`, `detected_only`,
  `unsupported`, or `unknown`;
- validation state: `unvalidated`, `partial`, `validated`, or `failed`;
- first/last seen timestamps.

Machines with a serial reconcile by user, adapter, and normalized serial.
Machines without a serial use a source-fingerprint-scoped unresolved identity.
They are not silently merged across unrelated cards.

Legacy sessions are assigned to deterministic `legacy-session-v1` machines.
Missing identity remains unknown rather than being guessed.

### Import runs and source manifests

`import_runs` records the reviewed source fingerprint, adapter, source type,
machine, status, validation, capabilities, diagnostics, timestamps, and
imported object counts.

`import_source_files` stores every staged relative path, byte size, SHA-256
hash, parser role, disposition, parser component, and structured warning/error
state. Files not consumed by the execution adapter are explicitly marked
`skipped` when the run finishes.

The source fingerprint protects the review-before-import boundary. Execution
is rejected if staged content changes after inspection.

### Machine-scoped sessions and blocks

Sessions now carry `machine_id`, `import_run_id`, `source_session_key`, and
`provenance_status`. Machine plus source session key is the durable uniqueness
boundary. The old user plus session ID unique index is removed because two
machines can legitimately emit the same local source key.

`session_blocks` stores explicit therapy intervals. Existing sessions receive
one legacy block. Negative legacy durations are retained as zero-length blocks
with failed validation and `legacy_invalid_duration` provenance.

### Settings

`settings_snapshots` supports versioned normalized and vendor-specific JSON,
source names, source files, adapter identity, confidence, and validation.

The table and query API are live. Native ResMed STR/CSL extraction is not yet
connected, so the UI/API correctly return no settings rather than inventing
values from flattened session columns.

### Signals and events

`signal_channels` records normalized/source names, units, sample rates, channel
kind, value kind, leak semantics, adapter, source file, confidence, and
validation. Native ResMed currently records parsed PLD channel metadata.
Full BRP/SA2 channel inventory remains follow-up work.

`session_events` now retains source event keys/types, import and source-file
provenance, adapter identity, confidence, and validation. Stable source keys
and replace-on-import behavior prevent duplicate events.

### Derived values

`derived_values` records value, unit, method/version, input references,
machine/session/import ownership, adapter, and validation. Native ResMed
summary values are tagged as produced by `sleeplab.native_resmed.summary`.

Therapy score, reports, trends, and AI inputs still read legacy summary
columns. Moving each consumer to explicit derived inputs is a later milestone.

## Import lifecycle

The uploaded-root flow is:

1. Select a root/card once.
2. Stage its relative files.
3. Detect all adapters and review evidence/capabilities.
4. Build a content-addressed plan.
5. Recompute and verify the fingerprint at execution.
6. Reconcile the machine and persist the run/source manifest.
7. Execute the approved adapter.
8. Attach sessions, blocks, channels, events, and derived values.
9. Finalize counts, errors, and skipped files.
10. Show the run in import history.

Only `resmed-native-v2` executes in production in this milestone. Other
families may be detected, but remain blocked and do not create import runs.

## Support claims

- `validated`: fixture/conformance evidence exists for the advertised
  capability.
- `supported`: production policy permits the capability after validation and
  operational review.
- `experimental`: parsing or persistence exists but evidence is incomplete.
- `detected_only`: structure/identity may be recognized; import is blocked.
- `unsupported`: intentionally rejected.
- `unknown`: identity or policy cannot yet be resolved.

Support is capability-specific. Detecting a manufacturer is not a claim that
sessions, settings, events, or waveforms are supported.

## Conformance fixtures

Each fixture lives under `fixtures/conformance/<fixture-id>/` with a
`manifest.json`. The manifest records:

- fixture kind and redistribution policy;
- anonymization method/review;
- immutable source/reference hashes where available;
- parser and OSCAR reference versions;
- expected detector, identity, capability, coverage, warning, and execution
  behavior.

Run:

```bash
python -m importer.conformance fixtures/conformance/synthetic-resmed-minimal
```

Synthetic structural fixtures may be committed. Real card data must not be
committed without documented consent and anonymization review. Restricted
fixtures should keep only the manifest in Git and be retrieved by immutable
hash in an authorized job.

## Known alpha limitations

- ResMed STR settings and summary-only days are not normalized yet.
- Native ResMed session grouping still creates one session row per PLD block;
  the explicit block model now exists, but nightly aggregation is unchanged.
- BRP waveform samples remain event-window-focused rather than full-night.
- Non-ResMed adapters are detection/planning only.
- Import cancellation and crash recovery are not implemented.
- Legacy local-path and SleepHQ imports reconcile machines but do not yet
  create full `import_runs` manifests.
- Existing score/report/AI consumers have not migrated to `derived_values`.
- The source manifest records filenames; deployments must treat import
  diagnostics as private health data.

## Next milestone

Parse ResMed `STR.edf`/CSL settings and source-defined mask-on/off intervals
into `settings_snapshots` and `session_blocks`, then add BRP/SA2 channel
metadata and fixture-backed duplicate import tests against persisted rows.
