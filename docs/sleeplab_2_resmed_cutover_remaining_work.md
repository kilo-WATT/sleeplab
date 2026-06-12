# SleepLab 2.0 ResMed Cutover Remaining Work

Status: **planning only; ResMed is not ready for a default-route cutover.**

This document is the short operational view of what remains before SleepLab can
route ResMed imports through `cpap-parser` by default. It separates three
questions that are easy to blur together:

1. **Can the parser read the card correctly?**
2. **Does SleepLab save the normalized result correctly?**
3. **Is the production route safe to operate and reverse?**

The committed AirSense 10 fixture gives useful evidence for all three questions,
but it is one card and it does not exercise usable oximetry. The legacy importer
must remain the production default and regression oracle until the before-cutover
gates below are met.

## What has improved

- `therapy_mode` is fixture-backed, persists to `settings_snapshots`, and is
  projected to `sessions.therapy_mode`.
- The exact `STR.edf` settings source now links to the uploaded source manifest.
- Event totals and SleepLab-normalized event-type totals are fixture-backed.
- Persisted event rows, low-rate `session_metrics`, and
  `nightly_therapy_aggregates` match on the current fixture.
- The database parity harness reports expected differences instead of hiding
  them. It measures both paths from the same 53-file source manifest.

These are real reductions in risk. They do not close the remaining cutover
questions.

## Remaining-work matrix

| Area | Current status | Why it matters | Category | Owner | Evidence from current tests/parity harness | Recommended next task |
|---|---|---|---|---|---|---|
| Settings snapshots / `therapy_mode` | **Reduced blocker.** Parser writes 40 snapshots and fills `sessions.therapy_mode`; legacy also writes 40 snapshots. | Proves the normalized setting survives database persistence, not just parsing. | acceptable known difference for early cutover | SleepLab | Parser fixture pins `therapy_mode=APAP`; DB parity reports 40 rows on both paths. | Keep the persistence regression and parity assertion as a cutover gate. |
| Remaining settings fields | Parser exposes only `therapy_mode`; legacy has 14 keys including pressure limits, EPR, ramp, humidity, tube temperature, and mask type. | A cutover would otherwise silently lose settings history users already receive. | needs cpap-parser upstream change | cpap-parser/open-cpap | DB parity: parser setting keys are only `therapy_mode`; unsupported session columns remain `NULL`. | Add the missing settings to the upstream normalized schema, or explicitly accept and disclose the loss before cutover. |
| Oximetry / SpO2 | **Unproven.** Parser save path writes no `session_spo2` rows and keeps `has_spo2=False`; current SAD files contain only `-1` missing values. | A 0-versus-0 result does not prove that real SpO2 and pulse samples survive import. | needs better test CPAP card data | test data needed | Parser-free SAD audit proves all six fixture files lack usable SpO2; DB parity is 0 rows on both paths. | Obtain a safe card with real SpO2/pulse samples, then implement and compare persistence. |
| Source manifest registration and disposition | Both paths register the same 53 files. Parser marks `STR.edf` used through settings provenance, but the other 52 rows finalize as skipped because they lack resolvable links, even though the parser consumed card data from this set. | Import history must distinguish truly skipped files from consumed-but-unlinked files; it must not turn missing provenance into a false usage claim. | must fix before cutover | SleepLab | Legacy: 25 used / 28 skipped. Parser: 1 used / 52 skipped. The harness proves registration and linkage counts, not that all 52 files were genuinely ignored. | Define truthful disposition/diagnostic behavior for parser-consumed files without fabricating row links. |
| Block/event/channel source links | Parser links none because its normalized references are synthetic; settings alone links `STR.edf`. | Without real links, row-level provenance and source drill-down are incomplete. | needs cpap-parser upstream change | cpap-parser/open-cpap | DB parity: parser linked blocks/events/channels = 0; linked settings = 1. | Draft an upstream issue requesting stable real source paths or source-file references on parsed sessions, events, and signals. |
| Session row shape / granularity | Legacy writes 43 block-scoped session rows; parser writes 40 night-scoped rows. | This changes what a `sessions` row means and affects identity, dedupe, derived values, and UI/API assumptions. | needs explicit product decision | product decision | DB parity: legacy `max_block_index=3`; parser `max_block_index=0`. | Decide whether to preserve legacy per-recording rows or adopt one nightly session with explicit child blocks. |
| `session_blocks` shape | Legacy writes 72 STR/PLD-derived blocks; parser writes 7 file-session blocks and currently labels them as STR mask intervals. | Block boundaries drive therapy duration, gaps, provenance, and event ownership. The current label overstates what the parser produced. | needs explicit product decision | product decision | DB parity reports 72 versus 7; code shows parser blocks are not real STR mask intervals. | Resolve the session model first, then correct block semantics and `source_kind` without inventing STR intervals. |
| Persisted event rows | Current fixture has equal persisted event totals and type sets: 11 rows on each path. | Events are central to AHI review and the event inspector. | acceptable known difference for early cutover | SleepLab | DB parity marks `session_events` equal. | Keep equality in the cutover harness and expand it with another card. |
| Event type counts | Counts for Central Apnea, Obstructive Apnea, Hypopnea, and loader-derived Large Leak are fixture-backed. They are SleepLab-normalized, not OSCAR enum parity. | Stable counts prove useful parser behavior, but vocabulary claims must remain precise. | acceptable known difference for early cutover | SleepLab | `expected.import.events.types` and parser-backed conformance tests pin the three detailed nights. | Retain the current vocabulary for cutover unless a separate product decision authorizes normalization changes. |
| Event timestamps, ordering, and durations | Totals are tested; exact timestamp/order/duration parity is not. | Events can appear on the wrong block or waveform window even when totals match. | must fix before cutover | SleepLab | Current harness compares row count and type set, not individual timestamps or durations. | Add privacy-safe tolerance checks for timestamp, order, duration, and block ownership on detailed nights. |
| Low-rate `session_metrics` | Exact parity on the current fixture: 34,710 rows on each path. | This is strong evidence that detailed low-rate therapy signals survive persistence. | acceptable known difference for early cutover | SleepLab | DB parity classifies `session_metrics` as equal and treats any future difference as unexpected. | Keep as a hard regression gate and repeat on a second fixture. |
| Event-window waveform rows | Parser writes 81,410 rows versus legacy 81,485, a roughly 0.1% difference. | Small boundary differences can change the event-inspector window even when full signal content is present. | acceptable known difference for early cutover | SleepLab | DB parity records row counts only and documents event-window rebasing as the likely cause. | Compare selected event-window boundaries and document an accepted tolerance before cutover. |
| `derived_values` | Legacy writes 90 rows; parser writes 520 because vocabulary and session granularity differ. | Reports and scores must consume equivalent semantics, not merely non-empty rows. | needs explicit product decision | product decision | DB parity classifies the difference as expected; nightly aggregate row counts still match. | Decide the session model, then define the authoritative derived-value vocabulary and compare values, not just counts. |
| Signal channels and leak unit | Parser and legacy differ in channel inventory, units, and source metadata. Legacy includes `L/s`; parser includes `L/min`. | A wrong unit or semantic label can corrupt charts, thresholds, and downstream calculations. | must fix before cutover | SleepLab | DB parity: 54 legacy channels versus 33 parser channels; distinct unit sets differ. | Audit normalized channel mapping and reconcile leak unit/kind and required channel metadata. |
| `nightly_therapy_aggregates` | Equal on the current fixture: 40 rows on each path. | This view feeds duration-sensitive features and shows the nightly model can produce matching coverage. | acceptable known difference for early cutover | SleepLab | DB parity classifies the view as equal. | Keep as a hard regression gate and compare aggregate values on soak runs. |
| Duplicate import / same-path re-import safety | Shared upserts and replacement writers are designed to be idempotent; synthetic tests cover stable session/block identities and settings re-import. The full real-card parser path is not yet run twice in the parity harness. | A retry or repeated card upload must not duplicate sessions, events, blocks, settings, or samples. | must fix before cutover | SleepLab | Synthetic DB tests prove duplicate/incremental stability; persistence helpers delete/replace samples and upsert normalized rows. | Extend the parser-backed DB harness to import the same real fixture twice and then with an expanded fixture/card view. |
| Cross-path dedupe after a flag change | **Not safe today.** Legacy and parser use different `source_session_key` and session-id shapes. | Enabling the flag for a user with legacy history can create parallel rows for the same therapy night. | must fix before cutover | SleepLab | Code audit: legacy keys are block-scoped; parser keys are `resmed:{machine}:{date}` and `cpapparser_{date}`. | Define a migration/matching strategy and add a legacy-then-parser re-import test before any default flip. |
| `cpap-py` dependency/runtime posture | Parser-backed ResMed works in Linux/Docker, but the dependency is git-sourced/native and absent from `pyproject.toml`, `uv.lock`, and normal CI. Windows installation needs a compiler when no wheel is available. | The default route cannot depend on an optional runtime that clean installs and CI do not reliably provide. | must fix before cutover | SleepLab | Linux Docker parser tests pass; host tests skip cleanly because `cpap-py` is absent. | Decide supported platforms, immutable packaging, CI coverage, failure behavior, and rollback posture. |
| `/source/{id}/finish` flag behavior | The endpoint honors `SLEEPLAB_USE_CPAP_PARSER`; default remains off. Detection/planning are shared and execution branches after the durable run is created. | This is the actual switch point, so status, failures, cleanup, and import-run completion must remain truthful. | must fix before cutover | SleepLab | Code audit confirms flag-on parser routing and flag-off legacy routing. Current parity tests call persistence directly, not the full background endpoint. | Add route-level integration coverage for success, missing parser backend, parser failure, durable run status, and cleanup. |
| `/datalog/*` behavior | Legacy-only and does not honor the parser flag. | A partial cutover would give different import behavior depending on which upload flow the user chose. | needs explicit product decision | product decision | `finish_datalog_upload` always schedules `_run_import`; only `/source/{id}/finish` branches. | Decide whether to route, retire, or explicitly keep `/datalog/*` legacy-only, then test and document that choice. |
| Second ResMed card and private soak | Only one anonymized AirSense 10 card is in the repeatable parser-backed evidence set. No committed two-path soak report exists. | One device/card cannot cover firmware, split sessions, clock behavior, real SpO2, or unknown layouts. | must fix before cutover | test data needed | Fixture matrix requires at least two independent ResMed fixtures; current parity figures come from one card. | Run a local-only/private dual-path soak if available and obtain a second safe fixture with immutable hashes. |
| User-visible import history and diagnostics | Durable runs and source manifests exist, but parser provenance is partial and route-level failure/status behavior is not fully exercised. | The UI and API must not claim files were unused, data was complete, or import succeeded when evidence is partial. | must fix before cutover | SleepLab | Manifest parity is honest in tests; parser execution has failure handling, but row-level source linkage is mostly absent. | Audit import-history wording and status transitions against parser success, partial provenance, and failure cases. |
| Lowenstein and other non-ResMed work | Separate validation track. It is not evidence for or a prerequisite of switching the ResMed backend. | Mixing vendor milestones obscures which risks belong to the ResMed cutover. | post-cutover polish | SleepLab | Roadmap keeps Lowenstein read-only/persistence behind its own fixture gates. | Track Lowenstein independently; do not use its status to raise or lower the ResMed go/no-go bar. |
| Full-night/compressed waveform storage | Explicitly deferred. Current production behavior stores event-window waveforms. | A future storage redesign is valuable, but it is not required to choose between the two current ResMed execution paths. | post-cutover polish | SleepLab | Both paths are compared using current `session_waveform` event-window rows; architecture docs defer compressed full-night storage. | Keep event-window storage for cutover; handle compressed segments as a separate design and migration project. |

## Immediate next tasks

1. Decide the session shape and granularity strategy.
2. Decide whether the parser path must preserve legacy session rows or use
   night-level sessions with authoritative `session_blocks`.
3. Decide the supported `cpap-py` runtime, packaging, and CI posture.
4. Add a private-card, local-only dual-path soak report if suitable data is
   available. Do not commit the card or identifying output.
5. Seek or create safe test card data containing real SpO2 and pulse values.
6. Draft an upstream `cpap-parser` issue requesting stable real source-path or
   source-file references.

## Before-cutover gate

Do not flip the default until all of the following are true:

- The database comparison harness is green enough that every remaining
  difference is explicitly accepted, tested, and documented.
- Duplicate import, incremental import, and legacy-to-parser re-import behavior
  are tested on realistic data.
- The `cpap-py` dependency and supported runtime path are settled and exercised
  by CI or an equivalent release gate.
- `/source/{id}/finish` status, cleanup, diagnostics, and failure behavior are
  integration-tested.
- `/datalog/*` has an explicit route/retirement decision.
- User-visible import history does not overstate completeness or file usage.
- There is no known loss of data the legacy importer preserves, unless that loss
  has an explicit product acceptance and a user-visible limitation.
- At least one additional ResMed card and a local-only soak have been reviewed.
- Native ResMed remains available as the rollback path until parser-backed
  imports have operated safely for an agreed soak period.

## Recommended next task

Start with the **session shape decision**. It determines block semantics,
cross-path dedupe, derived-value ownership, source provenance expectations, and
what the parity harness should consider equivalent. Implementing around that
decision before it is made would create churn in nearly every remaining row.
