# SleepLab 2.0 Alpha Data Architecture

## Scope

SleepLab 2.0 treats a CPAP import as an auditable transformation from a source
card into machine-scoped therapy data. The legacy `sessions` table remains the
read path for existing charts, reports, notes, tags, oximetry, and AI features,
but it is no longer the owner of machine identity or provenance.

Migrations `023_add_cpap_data_foundation.sql` and
`024_add_authoritative_therapy_semantics.sql` are additive and backfill
existing data. They do not delete sessions, events, metrics, waveforms,
oximetry, notes, tags, equipment, or AI cache rows.

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

For native ResMed imports, PLD files remain durable source sessions and
recording-span blocks. STR mask-on/mask-off intervals are stored separately as
`resmed_str_mask_interval` blocks. Re-imports upsert stable source keys and
remove only stale STR intervals for the same machine-local date.

`nightly_therapy_aggregates` derives a machine/night read model:

- **source session**: one durable source recording, currently keyed from PLD;
- **therapy block**: a source-defined mask-on/mask-off interval;
- **machine-local date**: the therapy date emitted in the machine timezone;
- **usage duration**: sum of validated STR intervals, falling back to
  recording spans only when explicit intervals are unavailable;
- **wall-clock span**: first therapy-on to last therapy-off;
- **gap duration**: wall-clock span minus usage;
- **summary-reported usage**: STR `Duration`, retained separately.

Session headers, Therapy Score, adherence, dashboard/overview, calendar,
trends, reports, AI summaries, and PDF exports use authoritative nightly
usage. Legacy sessions without a machine or explicit blocks remain visible
through a conservative recording-span fallback.

### Settings

`settings_snapshots` stores versioned normalized and vendor-specific JSON,
source names/files, adapter and parser identity/version, effective timestamp,
confidence, validation, and diagnostics.

Native ResMed reads settings and mask intervals from `STR.edf`. CSL is an EDF
annotation/event source, not a settings source. Confident mappings include
therapy mode, pressure ranges, bilevel fields when present, EPR, ramp,
humidification/climate, heated-tube temperature, and mask type. Unknown fields
and enum codes remain in vendor JSON and produce diagnostics; they are never
guessed. Selected values are projected to legacy session columns only for
compatibility.

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

Therapy score, reports, trends, adherence, and AI duration inputs now use
authoritative nightly usage. Other clinical values still use existing summary
columns while their provenance is retained in `derived_values`.

## ResMed AirSense 10 audit

A restricted real AirSense 10 card was audited locally on June 6, 2026. No
private card files are committed. June 5, 2026 established:

| Value | Meaning | Result |
| --- | --- | ---: |
| STR mask intervals | Computed therapy usage | 21,840 s (6h04m) |
| STR first on to last off | Wall-clock span | 22,740 s (6h19m) |
| Derived gaps | Span minus usage | 900 s (15m) |
| PLD headers | Recording coverage | 21,660 s (6h01m) |
| STR `Duration` | Summary-reported usage | 21,840 s |
| STR `OnDuration` | Summary-reported on-span | 22,740 s |

The previous 5h57m header excluded short PLD recordings, while the previous
Therapy Score used another duration path. Consumers now share the aggregate
instead of being patched independently.

### ResMed BRP/SA2 channel inventory (Alpha 6)

The channels below are the full BRP/PLD/SAD inventory carried by the committed,
anonymized AirSense 10 fixture (`tests/conformance/fixtures/resmed_airsense10_001/`).
This is **decoded evidence**, not assumption: the fixture is read with the
pure-Python `importer/edf_parser.py:read_header` (no `cpap-py`), and every
channel is run through `importer/db.py:_normalized_signal` at its real sample
rate. Pinned by `test_airsense10_fixture_channel_inventory_matches_classification`
(`tests/test_resmed_import_regressions.py`), which runs in the normal suite.

`unit` is the EDF `dim` field as the fixture carries it. `kind` follows the
single rule `sample_rate_hz >= 5 → waveform`, else `low_rate`. `value_kind` is
`sample` for all of these. `Crc16` is a per-record checksum, not a signal, and is
skipped (never inventoried). CSL/EVE are annotation files and carry no signal
channels.

| Source label | Normalized | Unit | Rate (Hz) | Kind | Leak kind | Support |
| --- | --- | --- | ---: | --- | --- | --- |
| `Flow.40ms` | `flow` | L/s | 25 | waveform | — | supported (event-window storage) |
| `Press.40ms` | `pressure` | cmH2O | 25 | waveform | — | supported (event-window storage) |
| `MaskPress.2s` | `mask_pressure` | cmH2O | 0.5 | low_rate | — | supported (full-resolution) |
| `Press.2s` | `pressure` | cmH2O | 0.5 | low_rate | — | supported (full-resolution) |
| `EprPress.2s` | `epr_pressure` | cmH2O | 0.5 | low_rate | — | supported (full-resolution) |
| `Leak.2s` | `leak` | L/s | 0.5 | low_rate | unintentional | supported (full-resolution) |
| `RespRate.2s` | `respiratory_rate` | bpm | 0.5 | low_rate | — | supported (full-resolution) |
| `TidVol.2s` | `tidal_volume` | L | 0.5 | low_rate | — | supported (full-resolution) |
| `MinVent.2s` | `minute_ventilation` | L/min | 0.5 | low_rate | — | supported (full-resolution) |
| `Snore.2s` | `snore` | (none) | 0.5 | low_rate | — | supported (full-resolution) |
| `FlowLim.2s` | `flow_limitation` | (none) | 0.5 | low_rate | — | supported (full-resolution) |
| `SpO2.1s` | `spo2` | % | 1 | low_rate | — | metadata inventoried; native path persists `session_spo2`, cpap-parser path sample persistence deferred (`persist.has_spo2: False`) |
| `Pulse.1s` | `pulse` | bpm | 1 | low_rate | — | metadata inventoried; sample persistence as above |

Notes and known divergences (documented, not silent):

- **Storage tiers** (see "Waveform storage scope" below): the 25 Hz BRP waveform
  channels are persisted to `session_waveform` only within event windows; the
  0.5 Hz PLD channels are persisted full-resolution to `session_metrics`; channel
  metadata for all of the above is persisted to `signal_channels`.
- **No misclassification** was found on the fixture: only the two 25 Hz BRP
  channels classify as `waveform`; everything else is `low_rate`. The
  `>= 5 Hz → waveform` rule is applied identically by the native path
  (`db._normalized_signal`) and the cpap-parser path
  (`persist._replace_signal_channel_metadata`), both pinned by tests.
- **Unit-label divergence (follow-up, not a bug):** the native path stores the
  EDF `dim` verbatim (so `flow`/`leak` are `L/s`), while the cpap-parser loader's
  `resmed_native._HIGH_RATE_CHANNELS`/`_LOW_RATE_CHANNELS` currently label
  `flow`/`leak` as `L/min`. The cpap-parser path is not the production route this
  milestone; reconciling these unit labels is tracked for the parser-parity work,
  not relied upon by production.
- **Deeper parser-path verification** (decoding via `cpap-py` and the
  cpap-parser normalized output) lives in the `cpap-py`-gated
  `tests/conformance/` suite and skips cleanly when that backend is absent.

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

Runs record settings counts, source-defined block counts, summary-only days,
capability validation, missing optional sources, unknown STR fields, duration
disagreements, and timestamp uncertainty. Material per-recording failures make
the run `partial`. A run still marked `running` after two hours is finalized
as failed when import history is inspected, preventing permanent stale runs
after a worker crash.

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

For a local restricted AirSense card, keep the card outside the repository,
point `--source-root` at its root and `--datalog` at `DATALOG`, and use a
dedicated development database/import run. Record only anonymized expected
counts and hashes. Duplicate verification compares stable persisted UUID sets;
incremental verification adds an authorized or synthetic newer night and
checks that existing identities remain unchanged.

## Waveform storage scope (Alpha 6 decision)

Alpha 6 deliberately keeps **event-window waveform storage** as the production
default and does **not** implement full-night waveform storage. This is an
intentional, documented limitation — not a hidden bug or a silent gap.

- **Current production storage:** high-rate BRP `flow`/`pressure` samples are
  persisted to `session_waveform` only within merged windows around scored
  events (120 s before / 180 s after, clipped to the recorded span;
  `db.replace_session_waveform`, `persist._write_session_waveform`). Storage
  therefore scales with event count, not night length. Low-rate PLD metrics are
  stored full-resolution in `session_metrics`; channel metadata in
  `signal_channels`.
- **Full-night waveform storage is not implemented in Alpha 6.** No schema,
  loader, or persistence change toward whole-night high-rate storage is made
  this milestone.
- **Alpha 6 adds measurement support** to ground the eventual decision: the
  row-count estimate is codified in tested, pure helpers
  (`importer/waveform_estimate.py`; `tests/test_waveform_estimate.py`) — ~90k
  rows/hour, ~720k for an 8 h night, ~21.6 M for a 30-night card (one machine,
  flow+pressure only), with the event-window scheme bounded above by that figure.
  That estimate assumes **one SQL row per sample** (the current
  `session_waveform` shape); it is the *worst-case upper bound* a future
  compressed design would avoid, not a target storage layout.
- **Future direction is a compressed segment/BLOB design investigation, not a
  full-night row-per-sample table.** Earlier planning framed the next step as
  "whole-night high-rate storage" implicitly extending the per-timestamp
  `session_waveform` table. The OSCAR 2.0 review re-grounds that: OSCAR does
  **not** store one row per sample — it uses `event_lists` (one metadata/index
  row per segment) plus `event_data` (one compressed payload row per segment,
  qCompress + CRC16 integrity checksum). Any future SleepLab high-rate storage
  beyond event windows should therefore be investigated as a **compressed
  waveform segment/BLOB model** (a segment metadata/index row + a compressed
  payload + an integrity checksum), modeled on that shape — never a naïve
  row-per-sample full-night table. SleepLab's `WaveformSegment`
  (`importer/loaders/models.py`) already carries segment metadata + a nullable
  `storage_ref`, i.e. it is segment-ready for exactly this design.
- **The investigation is a written design first, and remains stop-and-ask.** It
  must weigh Postgres-native storage concerns before any schema or migration:
  `BYTEA` vs large-object vs TOAST trade-offs (inline vs out-of-line, the ~2 kB
  TOAST threshold, compression), backup size, streaming / byte-range reads for
  partial-segment access, retention and downsampling tiers (e.g. raw → decimated
  rollups with bounded retention), and multi-tenant isolation. No full-night /
  compressed-segment storage, schema, loader, or persistence change is made now;
  **event-window storage stays the production default.** (See
  `docs/sleeplab_2_crimson_structure_review.md` §7–§9.)
- Absence of high-rate samples beyond event windows is surfaced through
  import-run diagnostics (`resmed_waveform_absent`) and capability state, never a
  fabricated zero.

## Known alpha limitations

- STR parsing has evidence from one AirSense 10 card and synthetic fixtures;
  this is not full ResMed-family validation.
- Some STR fields remain vendor-only and diagnostic because their meaning is
  not confidently established.
- Three malformed PLD headers on the restricted card report `num_records=-1`;
  valid data imports and the run is correctly marked partial.
- BRP waveform samples remain event-window-focused rather than full-night — an
  intentional Alpha 6 decision (see "Waveform storage scope" above), not a bug.
- Non-ResMed adapters are detection/planning only.
- Import cancellation and proactive worker heartbeats are not implemented;
  stale-run recovery prevents permanent `running` state.
- Legacy local-path and SleepHQ imports reconcile machines but do not yet
  create full `import_runs` manifests.
- AI cache invalidation remains timestamp/input-fingerprint based rather than
  a general dependency graph over derived values.
- The source manifest records filenames; deployments must treat import
  diagnostics as private health data.
- Device clocks are not corrected: sessions carry only a `timezone_basis` label,
  not an offset/DST/drift correction, so a wrong device clock, travel, DST, or
  clock drift can mis-date a night. A future non-destructive, machine-scoped,
  reversible correction model (inspired by OSCAR v17 `device_time_corrections`)
  is sketched in `docs/sleeplab_2_device_time_correction_design.md` — design only,
  no migration in Alpha 7.

## Next milestone

Investigate a future **compressed waveform segment/BLOB design** (modeled on
OSCAR's `event_lists`/`event_data`, see "Waveform storage scope" above) rather
than a row-per-sample full-night table, alongside BRP/SA2 channel inventory,
sample rates, retention, and query performance. This investigation is a written
design first and stays stop-and-ask; the current ResMed path deliberately stores
event-window waveform samples and that remains the production default. Lowenstein
read-only conformance through the same normalized model follows.
