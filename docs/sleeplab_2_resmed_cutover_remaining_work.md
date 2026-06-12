# SleepLab 2.0 ResMed Cutover Remaining Work

## SleepLab 2.0 target: cpap-parser is the ResMed import path

**The cpap-parser ResMed path is the SleepLab 2.0 target architecture.** We are no
longer trying to clone the legacy importer's database rows. The legacy native
importer is retained only as a *fallback/rollback* and as a *safety parity oracle*
— exact old-vs-new row parity is **not** the goal.

Because this is 2.0, **breaking old importer assumptions is acceptable during
alpha**:

- The session model is now one night-level `sessions` row plus child
  `session_blocks` (not the legacy per-recording-block rows).
- Device-scored events are preserved in full (Option A), not clipped to PLD
  recording windows.
- The parser exposes therapy primarily as device-reported totals, not per-mask
  intervals; the nightly view selects the best available therapy accordingly.
- **Users may need to delete and re-import their data from their SD cards during
  the 2.0 alpha.** That is an accepted alpha cost.

A "remaining gap" below is therefore only a **blocker** if it risks data loss,
duplicate imports, bad usage totals, missing core events, broken import history,
crashes, or database inconsistency. Differences that are merely "not identical to
legacy" are accepted 2.0 model differences once documented.

Status: **cpap-parser is the 2.0 ResMed target; the runtime default has not been
flipped yet** — parser packaging is implemented, but the second-card,
real-SpO2, and parser-enabled CI/DB evidence gates remain. To run SleepLab 2.0 on cpap-parser today, set
`SLEEPLAB_USE_CPAP_PARSER=1` (the `compose.advanced.yaml`/`.env.example` document
this).

This document is the short operational view of what remains before SleepLab can
make `cpap-parser` the *default* ResMed route. It separates three questions that
are easy to blur together:

1. **Can the parser read the card correctly?**
2. **Does SleepLab save the normalized result correctly?**
3. **Is the production route safe to operate and reverse?**

The committed AirSense 10 fixture gives useful evidence for all three questions,
but it is one card and it does not exercise usable oximetry. The legacy importer
remains the runtime default and parity oracle until the before-default gates below
are met.

## What has improved

- `therapy_mode` is fixture-backed, persists to `settings_snapshots`, and is
  projected to `sessions.therapy_mode`.
- The exact `STR.edf` settings source now links to the uploaded source manifest.
- Event totals and SleepLab-normalized event-type totals are fixture-backed.
- Persisted event rows and low-rate `session_metrics` match on the current
  fixture. Nightly aggregate row counts and, as of migration 025, total usage now
  reconcile too.
- Parser file-session blocks are now labeled `source_kind='recording_span'`
  instead of being mislabeled as `resmed_str_mask_interval`. The parity harness
  reports each path's `usage_source`, so a recording-span total is no longer
  silently compared against authoritative mask intervals.
- Summary-only (STR-history) nights now carry their STR-reported therapy usage
  instead of persisting as zero-usage nights. On the fixture this recovers
  ~837,780s of therapy across 37 nights (`zero_usage_nights` 37 → 0).
- **Therapy usage now follows a best-available priority (migration 025):** the
  `nightly_therapy_aggregates` view selects per-night usage as mask intervals →
  source-reported therapy → computed usage → recording span, and labels the chosen
  tier in `usage_source`. The parser's detailed nights now prefer their
  device-reported STR therapy (69,600s on the fixture) over the recording span
  (89,820s), so the parser nightly total lands exactly on legacy's 907,380s (was
  927,600s). Legacy is unaffected (its nights resolve to tier 1, mask intervals).
  Nothing is fabricated: recording spans are still preserved as context in
  `recording_duration_seconds`, only never used as usage when a better number
  exists. Validated against Postgres with synthetic per-tier coverage.
- The parser path also carries each detailed night's STR-reported therapy on its
  recording-span blocks (`source_reported_duration_seconds`), which both feeds the
  new tier-2 selection and is surfaced as
  `nightly_therapy_aggregates.summary_reported_usage_seconds` for cross-checking.
- The database parity harness reports expected differences instead of hiding
  them, and now treats the night-level session/block row-count differences as
  accepted 2.0 model differences gated on usage totals reconciling. It measures
  both paths from the same 53-file source manifest.

These are real reductions in risk. Runtime packaging and CI configuration are
implemented, mixed histories are rejected by an explicit reset policy, and the
DATALOG posture is enforced in code. The remaining evidence questions are the
second card, real oximetry, and a green parser-enabled CI/DB matrix.

## Remaining-work matrix

| Area | Current status | Why it matters | Category | Owner | Evidence from current tests/parity harness | Recommended next task |
|---|---|---|---|---|---|---|
| Settings snapshots / `therapy_mode` | **Reduced blocker.** Parser writes 40 snapshots and fills `sessions.therapy_mode`; legacy also writes 40 snapshots. | Proves the normalized setting survives database persistence, not just parsing. | acceptable known difference for early cutover | SleepLab | Parser fixture pins `therapy_mode=APAP`; DB parity reports 40 rows on both paths. | Keep the persistence regression and parity assertion as a cutover gate. |
| Remaining settings fields | Parser exposes only `therapy_mode`; legacy has 14 keys including pressure limits, EPR, ramp, humidity, tube temperature, and mask type. | A cutover would otherwise silently lose settings history users already receive. | needs cpap-parser upstream change | cpap-parser/open-cpap | DB parity: parser setting keys are only `therapy_mode`; unsupported session columns remain `NULL`. | Add the missing settings to the upstream normalized schema, or explicitly accept and disclose the loss before cutover. |
| Oximetry / SpO2 | **Unproven.** Parser save path writes no `session_spo2` rows and keeps `has_spo2=False`; current SAD files contain only `-1` missing values. | A 0-versus-0 result does not prove that real SpO2 and pulse samples survive import. | needs better test CPAP card data | test data needed | Parser-free SAD audit proves all six fixture files lack usable SpO2; DB parity is 0 rows on both paths. | Obtain a safe card with real SpO2/pulse samples, then implement and compare persistence. |
| Source manifest registration and disposition | Both paths register the same manifest. Exact references such as `STR.edf` link normally. Parser-consumed roles without stable upstream paths are marked used with `consumed_without_row_link`; CRC/other files remain skipped. | Import history now distinguishes skipped files from consumed-but-unlinkable files without inventing row links. | closed for manifest posture; upstream row links remain | SleepLab/upstream | Production finalizer and parity harness share the same role-marking helper; linked blocks/events/channels remain zero. | Keep diagnostics truthful and request stable source paths upstream. |
| Block/event/channel source links | Parser links none because its normalized references are synthetic; settings alone links `STR.edf`. | Without real links, row-level provenance and source drill-down are incomplete. | needs cpap-parser upstream change | cpap-parser/open-cpap | DB parity: parser linked blocks/events/channels = 0; linked settings = 1. | Draft an upstream issue requesting stable real source paths or source-file references on parsed sessions, events, and signals. |
| Session row shape / granularity | **Decided + accepted as a 2.0 model difference.** SleepLab 2.0 uses one night-level `sessions` row plus child `session_blocks`. Legacy writes 43 block-scoped rows; parser writes 40 night-scoped rows, and the two paths' `session_blocks` counts differ by model (legacy STR+PLD blocks vs parser file-session recording-span blocks). | The canonical meaning of a session is stable. We are not cloning the legacy row shape; cross-path migration still needs implementation. | accepted 2.0 model difference (cross-path migration still to do) | SleepLab | DB parity: legacy `max_block_index=3`; parser `max_block_index=0`. The classifier now accepts both the session-row and the block-row count differences as 2.0 model differences, gated on the genuine blockers: nightly **usage totals must reconcile** (they now do, via migration 025) and any event-count difference must be the accepted device-scored-event policy (parser ≥ legacy, matching types). | Implement the legacy-to-night-level migration and cross-path dedupe (the remaining blocker here is dedupe/migration safety, not the row-count difference itself). |
| `session_blocks` shape | **Mislabeling fixed.** Legacy writes 72 STR/PLD-derived blocks; parser writes 7 file-session blocks, now correctly `source_kind='recording_span'` with `recording_duration_seconds` set and `therapy_duration_seconds` left NULL (parser exposes no per-block therapy). Block *count* (72 vs 7) still reflects the granularity split. | Blocks now explicitly own mask-on periods and recording fragments, so their totals and semantics must reconcile even though session row counts need not. | reduced blocker | SleepLab | DB parity: parser `source_kinds=['recording_span']` (was mislabeled `resmed_str_mask_interval`); legacy `['recording_span','resmed_str_mask_interval']`; parser block recording seconds 89,820, therapy seconds 0; legacy therapy seconds 907,380. | Reconcile block *counts* under the night-level model; per-mask therapy intervals need upstream cpap-parser support. |
| Persisted event rows | **DECIDED — accepted known difference (not a blocker).** SleepLab 2.0 adopts **Option A: preserve the full device-scored event list.** Background: both EVE parsers read an identical raw scored-event list (legacy EDF parser and cpap-py agree type-for-type) and large-leak derivation matches; the legacy path then **clips device-scored events to each PLD recording window** (`events_for_block`), dropping events in inter-block gaps / at boundaries, while the cpap-parser path keeps the full list. The new path is **not overcounting** — it preserves real device-scored events the old path dropped. Legacy recording-window clipping is treated as legacy behavior, **not** the target. AHI/event totals may be **slightly higher** than legacy SleepLab on affected nights. | Events drive AHI review and the event inspector. Preserving the device's own scoring aligns with the machine/OSCAR-style interpretation. | accepted known difference (decided) | SleepLab | DB parity marks `session_events` equal on the fixture and a policy-aware `expected_difference` on real cards; the harness accepts it **only** while consistent with the policy (matching type sets, parser totals/AHI ≥ legacy) and keeps a type-set mismatch, a net-negative, or unexplained shape as `unexpected`. `type_counts` / `ahi_event_count` / `zero_duration_count` localize the difference. | Keep the breakdown fields as the event regression oracle and ensure no duplicate events are introduced. Do not claim exact OSCAR parity without an OSCAR reference; where OSCAR reference files are available, check event parity against them. |
| Event type counts | Counts for Central Apnea, Obstructive Apnea, Hypopnea, and loader-derived Large Leak are fixture-backed. They are SleepLab-normalized, not OSCAR enum parity. | Stable counts prove useful parser behavior, but vocabulary claims must remain precise. | acceptable known difference for early cutover | SleepLab | `expected.import.events.types` and parser-backed conformance tests pin the three detailed nights. | Retain the current vocabulary for cutover unless a separate product decision authorizes normalization changes. |
| Event timestamps, ordering, and durations | Totals are tested; exact timestamp/order/duration parity is not. | Events can appear on the wrong block or waveform window even when totals match. | must fix before cutover | SleepLab | Current harness compares row count and type set, not individual timestamps or durations. | Add privacy-safe tolerance checks for timestamp, order, duration, and block ownership on detailed nights. |
| Low-rate `session_metrics` | Exact parity on the current fixture: 34,710 rows on each path. | This is strong evidence that detailed low-rate therapy signals survive persistence. | acceptable known difference for early cutover | SleepLab | DB parity classifies `session_metrics` as equal and treats any future difference as unexpected. | Keep as a hard regression gate and repeat on a second fixture. |
| Event-window waveform rows | Parser writes 81,410 rows versus legacy 81,485, a roughly 0.1% difference. | Small boundary differences can change the event-inspector window even when full signal content is present. | acceptable known difference for early cutover | SleepLab | DB parity records row counts only and documents event-window rebasing as the likely cause. | Compare selected event-window boundaries and document an accepted tolerance before cutover. |
| `derived_values` | Legacy writes 90 rows; parser writes 520 because vocabulary and ownership differ. | Reports and scores must consume equivalent night-level semantics, not merely non-empty rows. | must fix before cutover | SleepLab | DB parity classifies the difference as expected; nightly aggregate row counts still match. | Define the authoritative night-level derived-value vocabulary and compare values, not just counts. |
| Signal channels and leak unit | Parser and legacy differ in channel inventory, units, and source metadata. Legacy includes `L/s`; parser includes `L/min`. | A wrong unit or semantic label can corrupt charts, thresholds, and downstream calculations. | must fix before cutover | SleepLab | DB parity: 54 legacy channels versus 33 parser channels; distinct unit sets differ. | Audit normalized channel mapping and reconcile leak unit/kind and required channel metadata. |
| `nightly_therapy_aggregates` | **CLOSED for usage totals (migration 025).** The 2.0 authoritative-therapy view now selects per-night usage by priority — (1) true mask/therapy intervals, (2) source-reported therapy, (3) computed usage, (4) recording span — so detailed parser nights prefer their device-reported STR therapy over the recording span. Summary-only nights still contribute their STR/computed usage via the block-less session fallback. `usage_source` names the tier that won. | This view feeds duration-sensitive features; usage must reflect the best available therapy, never a recording span when a better number exists. | accepted 2.0 difference (usage total reconciles) | SleepLab | On the 3-detailed-of-40-night fixture the parser total now lands exactly on legacy's 907,380s (was 927,600s; the +20,220s recording-span overcount on the 3 detailed nights is gone). `usage_source` is `resmed_str_mask_intervals` (legacy) vs `source_reported_therapy`/`computed_usage` (parser); the totals match. Validated against Postgres with synthetic tier coverage and the parity harness. | Keep the usage-total reconciliation as the parity gate (a divergent total is still a real blocker). Per-mask interval *labels* still need upstream cpap-parser support, but that no longer affects the usage number. |
| Duplicate import / same-path re-import safety | **Closed for same-card re-import.** Parser sessions upsert by machine/night key; generated blocks and parser settings are cleared and rewritten; events, channels, metrics, waveform rows, and derived values use replacement writers. | A retry or repeated card upload must not duplicate sessions, events, blocks, settings, or samples. | closed for beta | SleepLab | The parser-backed AirSense 10/Postgres harness imports the same card twice as two durable attempts and proves stable aggregate state for sessions, blocks, settings, events, SpO2 rows, channels, derived values, metrics, waveform rows, and nightly aggregates. `import_runs` intentionally increases from one to two, with one source manifest per attempt. | Keep the double-import test as a release gate; add an expanded-card incremental test when a second safe fixture exists. |
| Cross-path dedupe after a flag change | **Explicit reset policy enforced.** Legacy and parser keys still differ, so `/source` rejects an opposite-backend ResMed history before creating a run. | Prevents silent parallel rows while preserving user-owned data from automatic deletion. | closed beta policy; migration remains RC work | SleepLab | Database-backed mixed-history route test. | Build preservation-aware migration before RC. |
| `cpap-py` dependency/runtime posture | The immutable parser revision is in the optional `parser` extra and lockfile, Docker installs it, and Linux CI installs and verifies both modules. Windows may still need native build tools when no wheel is available. | Clean installs and CI now have an explicit supported parser path. | implementation closed; CI evidence pending | SleepLab | `pyproject.toml`, `uv.lock`, Dockerfile, CI runtime check, missing-runtime route test. | Keep the parser-enabled CI job green and document platform limitations. |
| `/source/{id}/finish` flag behavior | The endpoint honors `SLEEPLAB_USE_CPAP_PARSER`; default remains off. Detection/planning are shared and execution branches after the durable run is created. `/config` reports the selected backend and parser runtime availability. | This is the actual switch point, so status, failures, cleanup, and import-run completion must remain truthful. | reduced blocker | SleepLab | Route tests prove flag-on schedules the parser task and flag-off schedules legacy. Config tests prove the selected backend is visible. | Add database-backed route coverage for success, missing backend, parser failure, durable run status, and cleanup. |
| `/datalog/*` behavior | Explicitly legacy-only for beta and disabled with HTTP 409 while parser mode is selected. Local triggers and webhooks follow the same rule. | Prevents accidental mixed-path imports while retaining a rollback path. | closed beta policy | SleepLab | Route/config/local-trigger tests. | Revisit only with a full-card server-side parser flow. |
| Second ResMed card and private soak | Only one anonymized AirSense 10 card is in the repeatable parser-backed evidence set. **A private, local-only dual-path soak against a real multi-night card has now been run** (aggregate-only, nothing committed); it confirmed the recording-span/summary-only fixes hold at scale (e.g. exact low-rate `session_metrics` parity over hundreds of thousands of rows) and surfaced the two findings above (data-dependent usage overcount; a small event-total divergence, **now decided** as an accepted device-scored-event policy — Option A, preserve the full device-scored list — not a parser overcount). No committed two-path soak *report* exists, and no second committed fixture exists. | One device/card cannot cover firmware, split sessions, clock behavior, real SpO2, or unknown layouts. | must fix before cutover | test data needed | Fixture matrix requires at least two independent ResMed fixtures; current committed parity figures come from one card. | Obtain a second safe fixture with immutable hashes; keep the private soak local-only. |
| User-visible import history and diagnostics | Durable runs and source manifests exist, but parser provenance is partial and route-level failure/status behavior is not fully exercised. | The UI and API must not claim files were unused, data was complete, or import succeeded when evidence is partial. | must fix before cutover | SleepLab | Manifest parity is honest in tests; parser execution has failure handling, but row-level source linkage is mostly absent. | Audit import-history wording and status transitions against parser success, partial provenance, and failure cases. |
| Lowenstein and other non-ResMed work | Separate validation track. It is not evidence for or a prerequisite of switching the ResMed backend. | Mixing vendor milestones obscures which risks belong to the ResMed cutover. | post-cutover polish | SleepLab | Roadmap keeps Lowenstein read-only/persistence behind its own fixture gates. | Track Lowenstein independently; do not use its status to raise or lower the ResMed go/no-go bar. |
| Full-night/compressed waveform storage | Explicitly deferred. Current production behavior stores event-window waveforms. | A future storage redesign is valuable, but it is not required to choose between the two current ResMed execution paths. | post-cutover polish | SleepLab | Both paths are compared using current `session_waveform` event-window rows; architecture docs defer compressed full-night storage. | Keep event-window storage for cutover; handle compressed segments as a separate design and migration project. |

## Immediate next tasks

1. ~~Reconcile legacy and parser usage totals under the night-level session
   model.~~ **Done (migration 025):** the 2.0 authoritative-therapy view selects
   the best available therapy per night, so usage totals reconcile and the session
   row-shape difference is an accepted 2.0 model difference. (Event totals are
   **decided**: SleepLab 2.0 preserves the full device-scored list, so parser ≥
   legacy is the accepted policy — just guard against duplicates.)
2. Define and test the legacy-to-night-level migration and cross-path dedupe
   strategy (**the remaining hard blocker** for flipping the default safely).
3. Decide the supported `cpap-py` runtime, packaging, and CI posture.
4. Add a private-card, local-only dual-path soak report if suitable data is
   available. Do not commit the card or identifying output.
5. Seek or create safe test card data containing real SpO2 and pulse values.
6. Draft an upstream `cpap-parser` issue requesting stable real source-path or
   source-file references.

## Before-default gate

cpap-parser is already the 2.0 target; this gate is specifically about flipping
the *runtime default*. Do not flip the default until all of the following are
true:

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
- The device-scored-event policy (Option A: SleepLab 2.0 preserves the full
  device-scored list; AHI/event totals may be marginally higher than legacy on
  affected nights) is disclosed to users, and the parity harness confirms no
  duplicate events are introduced.
- At least one additional ResMed card and a local-only soak have been reviewed.
- Native ResMed remains available as the rollback path until parser-backed
  imports have operated safely for an agreed soak period.

## Recommended next task

The therapy-usage gap is **closed**. SleepLab 2.0 took **Option B (view/product)**:
migration `025_prefer_authoritative_therapy_usage.sql` rewrites
`nightly_therapy_aggregates` to select per-night usage by priority — true mask
intervals → source-reported therapy → computed usage → recording span — and names
the winning tier in `usage_source`. On the fixture the parser detailed nights now
use their device-reported STR therapy (69,600s) instead of the recording span
(89,820s), so the parser total lands exactly on legacy's 907,380s. This is **not**
a no-view-change fix; it intentionally changes the view, and the legacy path is
unaffected because legacy nights still resolve to tier 1 (mask intervals).

Option A (upstream per-mask STR intervals so the parser can persist
`resmed_str_mask_interval` blocks) is still desirable for *block-level* labeling,
but it no longer affects the usage number.

The next real blocker is **cross-path dedupe and the legacy-to-night-level
migration**: enabling the flag for a user with legacy history must not create
parallel rows for the same therapy night. Implement and test that around the
night-level ownership rules before flipping the runtime default. During 2.0 alpha,
the accepted interim is that **users may delete and re-import their card data**.
