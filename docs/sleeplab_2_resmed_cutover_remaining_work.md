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
- Persisted event rows and low-rate `session_metrics` match on the current
  fixture. Nightly aggregate row counts match, but total usage does not.
- Parser file-session blocks are now labeled `source_kind='recording_span'`
  instead of being mislabeled as `resmed_str_mask_interval`. The parity harness
  reports each path's `usage_source`, so a recording-span total is no longer
  silently compared against authoritative mask intervals.
- Summary-only (STR-history) nights now carry their STR-reported therapy usage
  instead of persisting as zero-usage nights. On the fixture this recovers
  ~837,780s of therapy across 37 nights (`zero_usage_nights` 37 → 0) and moves
  the parser nightly usage total from 89,820s to 927,600s, within ~2.2% of
  legacy's 907,380s. The residual is the three detailed nights, which still
  contribute recording spans rather than per-mask therapy (see below).
- The parser path now also carries each detailed night's STR-reported therapy
  usage on its recording-span blocks (`source_reported_duration_seconds`, the
  same field legacy sets), so `nightly_therapy_aggregates.summary_reported_usage_seconds`
  surfaces the authoritative device-reported therapy total *next to* the
  recording-span `usage_seconds`. Consumers and the parity harness can now read
  the "candy" (authoritative therapy) and the "wrapper" (recording span) side by
  side on a single view, without any schema or view change.
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
| Session row shape / granularity | **Decided:** SleepLab 2.0 uses one night-level `sessions` row plus child `session_blocks`. Legacy still writes 43 block-scoped rows; parser writes 40 night-scoped rows. | The canonical meaning of a session is now stable. Legacy compatibility and cross-path migration still need implementation. | must fix before cutover | SleepLab | DB parity: legacy `max_block_index=3`; parser `max_block_index=0`. The classifier now accepts the row-count difference only when block, usage, and event totals match. | Make the legacy-to-parser transition preserve one owning night and move recording fragments into stable blocks. |
| `session_blocks` shape | **Mislabeling fixed.** Legacy writes 72 STR/PLD-derived blocks; parser writes 7 file-session blocks, now correctly `source_kind='recording_span'` with `recording_duration_seconds` set and `therapy_duration_seconds` left NULL (parser exposes no per-block therapy). Block *count* (72 vs 7) still reflects the granularity split. | Blocks now explicitly own mask-on periods and recording fragments, so their totals and semantics must reconcile even though session row counts need not. | reduced blocker | SleepLab | DB parity: parser `source_kinds=['recording_span']` (was mislabeled `resmed_str_mask_interval`); legacy `['recording_span','resmed_str_mask_interval']`; parser block recording seconds 89,820, therapy seconds 0; legacy therapy seconds 907,380. | Reconcile block *counts* under the night-level model; per-mask therapy intervals need upstream cpap-parser support. |
| Persisted event rows | Current fixture has equal persisted event totals and type sets: 11 rows on each path. **However, a private real multi-night soak showed the two paths' persisted event *totals* (and AHI-event totals) can diverge by a small amount on real data** — the single fixture's 11=11 equality is not sufficient evidence of event parity. Type sets still match. | Events are central to AHI review and the event inspector. | must fix before cutover | SleepLab | DB parity marks `session_events` equal on the fixture but `unexpected_difference` on a multi-night real card. | Investigate event count/attribution divergence (night-boundary assignment, large-leak derivation, de-dup) on multi-night data; do not rely on single-fixture equality. |
| Event type counts | Counts for Central Apnea, Obstructive Apnea, Hypopnea, and loader-derived Large Leak are fixture-backed. They are SleepLab-normalized, not OSCAR enum parity. | Stable counts prove useful parser behavior, but vocabulary claims must remain precise. | acceptable known difference for early cutover | SleepLab | `expected.import.events.types` and parser-backed conformance tests pin the three detailed nights. | Retain the current vocabulary for cutover unless a separate product decision authorizes normalization changes. |
| Event timestamps, ordering, and durations | Totals are tested; exact timestamp/order/duration parity is not. | Events can appear on the wrong block or waveform window even when totals match. | must fix before cutover | SleepLab | Current harness compares row count and type set, not individual timestamps or durations. | Add privacy-safe tolerance checks for timestamp, order, duration, and block ownership on detailed nights. |
| Low-rate `session_metrics` | Exact parity on the current fixture: 34,710 rows on each path. | This is strong evidence that detailed low-rate therapy signals survive persistence. | acceptable known difference for early cutover | SleepLab | DB parity classifies `session_metrics` as equal and treats any future difference as unexpected. | Keep as a hard regression gate and repeat on a second fixture. |
| Event-window waveform rows | Parser writes 81,410 rows versus legacy 81,485, a roughly 0.1% difference. | Small boundary differences can change the event-inspector window even when full signal content is present. | acceptable known difference for early cutover | SleepLab | DB parity records row counts only and documents event-window rebasing as the likely cause. | Compare selected event-window boundaries and document an accepted tolerance before cutover. |
| `derived_values` | Legacy writes 90 rows; parser writes 520 because vocabulary and ownership differ. | Reports and scores must consume equivalent night-level semantics, not merely non-empty rows. | must fix before cutover | SleepLab | DB parity classifies the difference as expected; nightly aggregate row counts still match. | Define the authoritative night-level derived-value vocabulary and compare values, not just counts. |
| Signal channels and leak unit | Parser and legacy differ in channel inventory, units, and source metadata. Legacy includes `L/s`; parser includes `L/min`. | A wrong unit or semantic label can corrupt charts, thresholds, and downstream calculations. | must fix before cutover | SleepLab | DB parity: 54 legacy channels versus 33 parser channels; distinct unit sets differ. | Audit normalized channel mapping and reconcile leak unit/kind and required channel metadata. |
| `nightly_therapy_aggregates` | **Summary-only usage recovered; detailed-night recording-span overcount remains and is data-dependent.** Both paths produce one row per night. Summary-only nights now contribute STR-reported therapy via the session-duration fallback (`zero_usage_nights` → 0). Detailed nights still contribute recording spans (`usage_source='recording_spans'`) rather than per-mask therapy. The view also now surfaces the authoritative `summary_reported_usage_seconds` on the parser path for detailed nights. | This view feeds duration-sensitive features; matching row counts do not protect against missing therapy usage. | must fix before cutover | SleepLab | On the 3-detailed-of-40-night fixture the overcount is small (parser `usage_seconds` 927,600 vs legacy 907,380, ~2.2%), **but this understates the gap**: the overcount equals Σ(recording span − therapy) over *detailed* nights, so on a typical card where most or all nights are detailed it is materially larger (tens of percent). A private real-card soak confirmed this scaling. `summary_reported_usage_seconds` now provides the authoritative reference for cross-checking. | Close the detailed-night overcount: either upstream per-mask intervals, or a product/schema decision letting the view prefer authoritative per-night therapy (`summary_reported`/`computed`) over recording spans. Both are out of scope for a no-schema/no-view-change fix. |
| Duplicate import / same-path re-import safety | Shared upserts and replacement writers are designed to be idempotent; synthetic tests cover stable session/block identities and settings re-import. The full real-card parser path is not yet run twice in the parity harness. | A retry or repeated card upload must not duplicate sessions, events, blocks, settings, or samples. | must fix before cutover | SleepLab | Synthetic DB tests prove duplicate/incremental stability; persistence helpers delete/replace samples and upsert normalized rows. | Extend the parser-backed DB harness to import the same real fixture twice and then with an expanded fixture/card view. |
| Cross-path dedupe after a flag change | **Not safe today.** Legacy and parser use different `source_session_key` and session-id shapes. | Enabling the flag for a user with legacy history can create parallel rows for the same therapy night. | must fix before cutover | SleepLab | Code audit: legacy keys are block-scoped; parser keys are `resmed:{machine}:{date}` and `cpapparser_{date}`. | Define a migration/matching strategy and add a legacy-then-parser re-import test before any default flip. |
| `cpap-py` dependency/runtime posture | Parser-backed ResMed works in Linux/Docker, but the dependency is git-sourced/native and absent from `pyproject.toml`, `uv.lock`, and normal CI. Windows installation needs a compiler when no wheel is available. | The default route cannot depend on an optional runtime that clean installs and CI do not reliably provide. | must fix before cutover | SleepLab | Linux Docker parser tests pass; host tests skip cleanly because `cpap-py` is absent. | Decide supported platforms, immutable packaging, CI coverage, failure behavior, and rollback posture. |
| `/source/{id}/finish` flag behavior | The endpoint honors `SLEEPLAB_USE_CPAP_PARSER`; default remains off. Detection/planning are shared and execution branches after the durable run is created. | This is the actual switch point, so status, failures, cleanup, and import-run completion must remain truthful. | must fix before cutover | SleepLab | Code audit confirms flag-on parser routing and flag-off legacy routing. Current parity tests call persistence directly, not the full background endpoint. | Add route-level integration coverage for success, missing parser backend, parser failure, durable run status, and cleanup. |
| `/datalog/*` behavior | Legacy-only and does not honor the parser flag. | A partial cutover would give different import behavior depending on which upload flow the user chose. | needs explicit product decision | product decision | `finish_datalog_upload` always schedules `_run_import`; only `/source/{id}/finish` branches. | Decide whether to route, retire, or explicitly keep `/datalog/*` legacy-only, then test and document that choice. |
| Second ResMed card and private soak | Only one anonymized AirSense 10 card is in the repeatable parser-backed evidence set. **A private, local-only dual-path soak against a real multi-night card has now been run** (aggregate-only, nothing committed); it confirmed the recording-span/summary-only fixes hold at scale (e.g. exact low-rate `session_metrics` parity over hundreds of thousands of rows) and surfaced the two findings above (data-dependent usage overcount; small event-total divergence). No committed two-path soak *report* exists, and no second committed fixture exists. | One device/card cannot cover firmware, split sessions, clock behavior, real SpO2, or unknown layouts. | must fix before cutover | test data needed | Fixture matrix requires at least two independent ResMed fixtures; current committed parity figures come from one card. | Obtain a second safe fixture with immutable hashes; keep the private soak local-only. |
| User-visible import history and diagnostics | Durable runs and source manifests exist, but parser provenance is partial and route-level failure/status behavior is not fully exercised. | The UI and API must not claim files were unused, data was complete, or import succeeded when evidence is partial. | must fix before cutover | SleepLab | Manifest parity is honest in tests; parser execution has failure handling, but row-level source linkage is mostly absent. | Audit import-history wording and status transitions against parser success, partial provenance, and failure cases. |
| Lowenstein and other non-ResMed work | Separate validation track. It is not evidence for or a prerequisite of switching the ResMed backend. | Mixing vendor milestones obscures which risks belong to the ResMed cutover. | post-cutover polish | SleepLab | Roadmap keeps Lowenstein read-only/persistence behind its own fixture gates. | Track Lowenstein independently; do not use its status to raise or lower the ResMed go/no-go bar. |
| Full-night/compressed waveform storage | Explicitly deferred. Current production behavior stores event-window waveforms. | A future storage redesign is valuable, but it is not required to choose between the two current ResMed execution paths. | post-cutover polish | SleepLab | Both paths are compared using current `session_waveform` event-window rows; architecture docs defer compressed full-night storage. | Keep event-window storage for cutover; handle compressed segments as a separate design and migration project. |

## Immediate next tasks

1. Reconcile legacy and parser block/usage/event totals under the decided
   night-level session model.
2. Define and test the legacy-to-night-level migration and cross-path dedupe
   strategy.
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

Summary-only usage is now recovered and block labeling is honest, so the
remaining usage gap is narrow and well understood: the three detailed nights
report recording spans (89,820s) instead of their STR therapy (~69,600s), a
+20,220s overcount that keeps the parser total at 927,600s vs legacy 907,380s.

Closing it is a **product/schema decision**, not a bug fix, and is intentionally
out of scope for a no-schema/no-view-change change:

- Option A (upstream): have cpap-parser expose per-mask STR intervals so the
  parser path can persist `resmed_str_mask_interval` blocks like legacy.
- Option B (view/product): let `nightly_therapy_aggregates` prefer authoritative
  per-night therapy (`computed_usage`/STR-reported) over recording spans when a
  night has only recording-span blocks. This changes the view and needs its own
  migration + tests.

Until one is chosen, the parser's detailed-night usage remains a recording-span
proxy, honestly labeled `usage_source='recording_spans'`. After that, implement
cross-path dedupe and migration around the night-level ownership rules.
