# SleepLab 2.0 — ResMed `cpap-parser` Cutover Readiness Audit

**SleepLab 2.0 target:** the `cpap-parser` ResMed path **is** the 2.0 target
architecture. The legacy native importer is a fallback/rollback and a parity
oracle, not the goal — **exact old-vs-new row parity is no longer required**, and
**breaking old importer assumptions (including users having to delete/re-import
card data during alpha) is acceptable.** This audit therefore measures *safety to
flip the runtime default*, not fidelity to legacy rows.

Status: **cpap-parser is the 2.0 target; runtime default not yet flipped.** The
therapy-usage reconciliation that used to block this is now resolved (migration
`025_prefer_authoritative_therapy_usage.sql` — best-available therapy priority).
The remaining gates are cross-path dedupe/migration safety, the `cpap-py`
parser-enabled CI evidence, real SpO2 evidence, and the second-card soak. The
dependency/runtime and `/datalog/*` policies are now implemented. This
document inventories those, grounded in the current code, and cross-references the
retirement-evidence list in `docs/sleeplab_2_loader_and_conformance_plan.md`.

For the concise owner/category/next-task view, see
`docs/sleeplab_2_resmed_cutover_remaining_work.md`. Recommendation: cpap-parser is
the target and is enabled with `SLEEPLAB_USE_CPAP_PARSER=1`; do not flip the
*default* until the dedupe/runtime/routing/soak gates are met.

## Beta-hardening update

Same-card cpap-parser re-import is now closed as a duplicate-safety blocker. The
AirSense 10/Postgres harness imports the same fixture twice under two durable
`import_runs` and proves stable sessions, blocks, settings, events, signal
metadata, derived values, metrics, waveform rows, and nightly aggregates.
Parser-owned blocks and settings are authoritative replacements, so corrected
input can remove stale children instead of only upserting new rows.

The runtime default remains unchanged. Route tests now prove flag-on selects the
parser task and flag-off selects legacy, while `/config` reports both the
selected backend and parser runtime availability. Cross-path legacy-to-parser
dedupe is still not automatic because deleting legacy sessions could silently
discard notes, tags, oximetry, or other user-owned data. The supported alpha/beta
transition is an explicit backup, clear, and SD-card re-import workflow documented
in `docs/sleeplab_2_beta_readiness_plan.md`.

## 1. Scope and method

Compared, path-by-path:

- **Legacy production path** — `importer/import_sessions.py` (direct EDF →
  Postgres via `importer/db.py`), spawned as a subprocess by
  `api/routers/upload.py`.
- **cpap-parser path** — `importer/loaders/resmed_native.py` →
  `importer/loaders/execution.py::run_cpap_parser_import` →
  `importer/loaders/persist.py::persist_import_run`.
- **Fixture/conformance evidence** — `tests/conformance/.../resmed_airsense10_001`,
  `importer/conformance.py`, `tests/conformance/test_resmed_airsense10.py`.

The comparison is at the **persisted-row** level (what lands in the database),
because that is what a production cutover changes — normalized `ImportRun`
conformance (already strong) is necessary but not sufficient.

## 2. Current routing state (no change proposed here)

- A feature flag already exists: `use_cpap_parser()` reads
  `SLEEPLAB_USE_CPAP_PARSER` (`execution.py`), **default off** at runtime even
  though cpap-parser is the 2.0 target — the default stays off only until the
  `cpap-py` runtime gate is met. `compose.override.yaml` and `.env.example`
  document enabling it (`SLEEPLAB_USE_CPAP_PARSER=1`) for 2.0 dev/alpha.
- `POST /source/{id}/finish` (`upload.py:461`) honors the flag — flag on →
  `run_cpap_parser_import` (in-process loader → `persist_import_run`); flag off →
  legacy subprocess. Detection/planning are shared (`resmed-native-v2`) regardless.
- `POST /datalog/{id}/finish` (`upload.py:373`) is **legacy-only** — it always
  calls `_run_import` (the subprocess) with **no** parser branch. A full cutover
  must also address this entry point.
- The cpap-parser path **hard-requires** `cpap-parser` + its `cpap-py` backend;
  absent, `run_cpap_parser_import` raises a clear `ImportError` (`execution.py:129`).
  `cpap-py` is git-sourced/native and is **not** in `pyproject.toml`/`uv.lock`/CI
  (only `requirements.txt` + Dockerfile) — see the gap audit §9.

## 3. Cutover blocker matrix

Severity: **P0** = correctness/data-loss vs legacy or untested production write;
**P1** = semantic divergence needing a decision + conformance; **P2** =
parity/polish. "Evidence" = what proves it today (or its absence).

| # | Area | Legacy (`import_sessions.py`) | cpap-parser path (`persist.py`) | Gap | Sev | Evidence today |
|---|---|---|---|---|---|---|
| 1 | **Persistence tests** | exercised indirectly by import | synthetic DB tests cover settings snapshot/session projection and idempotency; parser-backed parity covers the real fixture | Core bridge persistence is exercised, though broader execution/routing tests remain useful | **P2** | `test_conformance.py`; parser-backed parity harness |
| 2 | **Legacy↔parser row diff** | n/a (oracle) | implemented | Automated redacted aggregate diff exists and classifies documented vs unexpected differences | **Done** | `tests/cutover_parity.py`; `tests/test_resmed_cutover_db_parity.py` |
| 3 | **Oximetry (SpO2)** | parses SA2/SAD; writes `session_spo2` and sets `has_spo2` only when SpO2 samples are not all missing (`-1`) | `has_spo2=False` hardcoded; no `session_spo2` writes | New path has an oximetry save-path gap, but the committed card cannot prove it: all six SAD files contain only missing SpO2 sentinels, so both paths correctly produce 0 rows | **P0 (data-blocked evidence)** | parser-free SAD payload test + DB parity (0/0) |
| 4 | **Settings snapshots** | `replace_resmed_str_day`→`upsert_settings_snapshot` writes full STR-derived `settings_snapshots` | persists loader-provided snapshots and flattens `therapy_mode` onto `sessions`; currently only `therapy_mode` is available | Therapy-mode persistence is closed, but full settings parity remains partial because cpap-parser exposes no mask/humidifier/temperature/pressure/EPR/ramp settings | **P1** | parser-backed DB parity harness; gap audit §11 |
| 5 | **STR mask-interval blocks** | `replace_resmed_str_day` emits `resmed_str_mask_interval` blocks from STR.edf | cpap-parser file-sessions are correctly labeled `source_kind="recording_span"`; no real per-mask STR intervals are exposed | Mislabeling is fixed. Per-mask interval detail remains unavailable, but nightly usage prefers source-reported therapy over recording spans | **P1 upstream gap** | persistence code + parser-backed parity harness |
| 6 | **Session granularity** | one `sessions` row **per PLD recording** (`block_index` loop, `import_sessions.py:292-329`) | one `sessions` row **per night** (`persist.py:154`, one per `daily_summary`), `block_index=0` | Canonical model is decided: night-level session plus child blocks. Legacy compatibility, block reconciliation, and cross-path migration remain | **P1** | data architecture decision + conditional parity guard |
| 7 | **Source-file provenance** | uses the upload-created manifest, resolves real relative paths, marks files used, and links blocks/events/channels/settings | receives the same manifest and now resolves the loader's exact `STR.edf` settings reference; block/event/channel ids remain synthetic because raw parser sessions expose no source path | Settings provenance is preserved, but parser-side session/file identity and row linkage remain the gap | **P1** | parser-backed DB parity: same 53 rows, legacy 25 used vs parser 1 |
| 8 | **Signal-channel source** | from raw EDF header `replace_signal_channels(header=…)` (`import_sessions.py:388`) | from `ImportRun.signals` metadata (`persist.py:343`) | Different source; channel set/units/rates parity unproven | **P1** | both code paths |
| 9 | **Event-type vocabulary** | raw labels; AHI counts via `_AHI_EVENT_TYPES` | same raw labels + loader-derived `Large Leak` rows; **not** OSCAR enum | Persisted `session_events.event_type` vocabulary is SleepLab-normalized, not OSCAR parity; `Large Leak` becomes an event row | **P1** | gap audit §12; `persist.py:66,214` |
| 10 | **`leak_unit` label** | `'L/s'` (`import_sessions.py:302`) | `'L/min'` (`persist.py:304`) | Direct metadata mismatch between paths (plan criterion: "leak unit/kind … match") | **P2** | both code paths |
| 11 | **`session_id` / dedupe** | block-scoped id; `source_session_key` legacy | `cpapparser_{date}`; `source_session_key=resmed:{key}:{date}` | Keys do not dedupe, so beta enforces delete-and-reimport and rejects mixed histories before run creation | **Closed beta policy / RC migration remains** | DB-backed route test |
| 12 | **TZ/DST + cross-midnight** | localizes EDF instants with `machine_tz` | same localization (`persist.py:158`) | Not yet conformance-proven on the new path (plan criterion) | **P2** | no targeted test |
| 13 | **`cpap-py` dependency** | not required | pinned optional extra, locked, installed in Docker/Linux CI | Runtime packaging is explicit; green CI evidence remains | **Closed implementation / evidence pending** | pyproject, lockfile, CI |
| 14 | **`/datalog/*` endpoint** | legacy subprocess only | explicitly disabled in parser mode | Prevents mixed-path imports during beta | **Closed beta policy** | route/config tests |
| 15 | **Second fixture / soak** | n/a | only one AirSense 10 fixture | Retirement evidence needs ≥2 independent fixtures + a real-import soak diff | **P1** | fixture matrix; plan "evidence" list |

## 4. What is already cutover-ready (not blockers)

To keep the matrix honest, these are **done** and de-risk the cutover:

- **Detection/planning are shared and unchanged** (`resmed-native-v2`); the flag
  only swaps execution (`execution.py` docstring). No detection regression risk.
- **Normalized `ImportRun` conformance** on the AirSense 10 fixture: `warnings`,
  `session_blocks.block_count`, `therapy_aggregates`, `events.count`+`types`,
  `settings.therapy_mode`, and `oscar_reference` hashes are fixture-backed
  (`cpap-py`-gated) — gap audit §9.2/§11/§12.
- **OSCAR parity** for AHI, computed-usage, and ghost-night flagging vs the
  committed OSCAR export (`test_resmed_airsense10.py`).
- **Four ResMed bug fixes** are regression-tested; **event-windowed waveform** +
  **`session_metrics`** persistence are implemented in the bridge.
- **`identity_hashes`** DB-gated tests prove duplicate/incremental key-set
  stability *within* the new path (synthetic rows).

## 5. Recommended task order

Each step is small and safe on its own; persistence/routing/dependency steps are
**stop-and-ask** per the workflow rules. Ordered so the *oracle* lands first.

1. **Build the legacy↔parser DB parity harness** (test-only; P0 #1,#2). — **DONE,
   see §5a.** Imports the AirSense 10 fixture through both paths into a throwaway
   rolled-back test transaction and classifies per-table differences. The oracle now
   exists; the remaining steps are measured against it. No routing change.
2. **Adopt night-level sessions plus `session_blocks`** (P1 #6, #11). — **DECIDED.**
   The remaining implementation work is to reconcile block/usage/event totals,
   migrate or match legacy block-scoped rows, and prevent cross-path duplicates.
3. **Wire `therapy_mode` → `settings_snapshots`** in the bridge (P0 #4). — **DONE.**
   `persist_import_run` now uses `upsert_settings_snapshot`, preserves normalized
   settings/source names with conservative confidence, and flattens real values
   onto `sessions`. Missing/`"Unknown"` values are not persisted or fabricated.
4. **Acquire usable oximetry evidence** before implementing (P0 #3). The current
   six SAD files contain channel metadata but every SpO2 sample is `-1`; both
   paths therefore write 0 rows. A safely redistributable fixture with real
   SpO2/pulse samples is required to prove `session_spo2` + `has_spo2`.
5. **Map source-file identity into normalized/parser rows** (P1 #7). The upload
   flow already creates `import_source_files`; do not duplicate it. The save path
   now resolves exact manifest references, which safely preserves the loader's
   `STR.edf` settings link. cpap-parser's merged sessions still expose no real
   source path and the loader emits synthetic block/event/channel ids. Fix those
   upstream/at the loader boundary before linking more UUIDs.
6. **Reconcile parity polish** (P2 #8,#10,#12): `leak_unit`, signal-channel
   set/units/rates, TZ/DST + cross-midnight — each as a parity assertion in the
   harness; fix the cheap mismatches (e.g. `leak_unit`).
7. **Keep parser packaging green** (P1 #13). Docker, the optional locked
   `parser` extra, and Linux CI now share the immutable revision.
8. **Keep `/datalog/*` legacy-only for beta** (P1 #14). It is disabled in parser
   mode; use `/source`.
9. **Acquire a second independent anonymized ResMed fixture** (P1 #15) for
   retirement evidence; blocked on a safe contributed/anonymized card.
10. **Soak**: run both paths in parallel on real imports, diff, no parser-backed
    writes (plan criterion) before flipping the default.
11. **Flip `SLEEPLAB_USE_CPAP_PARSER` default** — only after 1–10. Explicit
    **stop-and-ask**; this is the cutover.

## 5a. DB parity harness — BUILT (step 1 done)

Step 1 (the legacy↔parser DB oracle) is implemented and validated end-to-end. It
turns the matrix from assertions into measured pass/fail without any routing,
schema, persistence, or dependency change.

**Where it lives.**
- `tests/cutover_parity.py` — pure, DB-free, unit-tested core: the parity-table
  scope, the redacted aggregate `snapshot_parity_tables(conn, …)`, the
  `KNOWN_DIFFERENCES` map (from §3), and `classify_parity(legacy, parser)` →
  per-table verdict (`equal` / `expected_difference` / `unexpected_difference` /
  `missing_in_legacy` / `missing_in_parser` / `skipped` / `not_implemented`).
- `tests/test_resmed_cutover_db_parity.py` — DB-free classification unit tests
  (run in the normal suite) **plus** the gated end-to-end harness
  `test_db_parity_harness`.

**What it compares.** Aggregate, redacted snapshots (counts, distinct counts, and
category-label sets — never serials, raw timestamps, paths, or ids) of: `sessions`,
`session_blocks`, `settings_snapshots`, `session_events`, `session_spo2`,
`signal_channels`, `derived_values`, `session_metrics` (row count), `session_waveform`
(row count only — no blobs), `import_source_files`, and the
`nightly_therapy_aggregates` view.

**How it runs / what gates it.** Both paths import the committed AirSense 10 fixture
into the **same rolled-back test transaction under separate `machine_id`s** — no
production DB, nothing survives the test. The legacy path runs on any host (its EDF
parser is pure-Python) via a commit-swallowing connection proxy; the cpap-parser
path runs `persist_import_run`. Gating: the `db` fixture skips without
`TEST_DATABASE_URL`; the parser half additionally needs `cpap-py` (absent → the
report is legacy-only and parser-dependent tables read `skipped`, never a crash).
To run the full harness: a Postgres `TEST_DATABASE_URL` **and** `cpap-py` —
i.e. Linux/Docker (Postgres service + `pip install -r api/requirements.txt -r
requirements.txt`), exactly the pattern used for the parser-backed conformance tests.

**First-run results (AirSense 10 fixture, both paths).** The harness verdicts:

| Table | Verdict | Legacy | Parser |
|---|---|---|---|
| `sessions` | **unexpected_difference (guard failed)** | 43 rows, max_block_index 3 (per-PLD-block) | 40 rows, max_block_index 0 (per-night); not accepted until block/usage/event totals match |
| `session_blocks` | expected_difference | 72; `source_kinds=[recording_span, resmed_str_mask_interval]`; therapy 907,380s | 7; `source_kinds=[recording_span]` (no longer mislabeled); recording 89,820s, therapy NULL |
| `settings_snapshots` | **expected_difference (partial parity)** | **40 rows; 14 keys; `therapy_mode=apap`** | **40 rows; `therapy_mode` only; `therapy_mode=APAP`** |
| `session_events` | **equal** on fixture; **expected_difference (accepted device-scored policy, Option A)** on real cards | 11 (CA/Hypopnea/Large Leak/Obstructive); clips events to PLD recording window (legacy behavior) | 11 (same types); retains full device-scored EVE list — SleepLab 2.0 target (parser ≥ legacy on real cards) |
| `session_metrics` | **equal** | 34 710 | 34 710 |
| `nightly_therapy_aggregates` | **expected_difference (usage)** | 40 rows; 907,380s; `usage_source=resmed_str_mask_intervals`; 0 zero-usage nights | 40 rows; **927,600s** (was 89,820); `usage_source=recording_spans`; 0 zero-usage nights (was 37) |
| `signal_channels` | expected_difference | 54 rows; units incl. `L/s` | 33 rows; units incl. `L/min` |
| `derived_values` | expected_difference | 90 rows, event-count+stat keys | 520 rows, usage-semantics keys |
| `session_waveform` | expected_difference | 81 485 | 81 410 (~0.1%) |
| `session_spo2` | equal (**not usable evidence**) | 0; all SAD SpO2 samples missing | 0; no oximetry persistence |
| `import_source_files` | **expected_difference (partial linkage)** | exact used/skipped paths plus row links | parser-consumed roles marked used with diagnostics; exact `STR.edf` settings link; other row links unavailable |

Honest reads from the first run:
- **Strong parity exists** for low-rate `session_metrics` (exact), scored
  `session_events` (count + type set), and `total_ahi_events` (9 = 9).
  The nightly view has matching row counts but not matching usage totals.
- **Event-window policy — DECIDED (Option A: preserve the full device-scored
  list).** On the fixture, event totals match exactly; a private multi-night soak
  showed a small divergence. Root cause: **both EVE parsers read an identical raw
  scored-event list** (legacy EDF parser and cpap-py agree type-for-type) and
  large-leak derivation matches, but the legacy path clips device-scored events to
  each PLD recording window (`events_for_block`) while the parser keeps the full
  device-scored list — so the parser persists the few events legacy drops in
  inter-block gaps / at boundaries (parser ≥ legacy). This is **not** a parser
  overcount, parse error, type-mapping issue, or large-leak difference. **Decision:
  SleepLab 2.0 keeps the full device-scored list** (aligning with the
  machine/OSCAR-style interpretation that a device-scored event should be
  preserved); legacy recording-window clipping is treated as legacy behavior, not
  the target. AHI/event totals may be marginally higher than legacy on affected
  nights — an **accepted known difference**, not a blocker, provided no duplicate
  events are introduced. The harness carries `session_events.type_counts` /
  `ahi_event_count` / `zero_duration_count` and a **policy-aware** classifier that
  accepts the difference only while it matches the policy (equal type sets, parser
  totals/AHI ≥ legacy) and keeps a type-set mismatch, a net-negative, or
  duplication as `unexpected`. No exact OSCAR parity is claimed here without an
  OSCAR reference; where OSCAR reference files exist, event parity should be
  checked against them.
- The settings row-count drop is closed (**40 → 40**) and parser sessions now
  populate `therapy_mode` (**40/40**). Full parity is still partial: legacy has
  14 setting keys and non-null mask/humidity/temperature values; parser has only
  `therapy_mode`, with those unavailable fields left `NULL`.
- The **granularity split is visible** (per-PLD-block vs per-night; 43/72 vs
  40/7). The target model is now explicitly per-night. The parity classifier
  accepts the session row-count difference only when block and total nightly usage
  totals match and any event-count difference is the accepted device-scored-event
  policy (parser ≥ legacy, matching types); otherwise it reports an unexpected
  difference. On the current fixture it remains unexpected: blocks are 72 vs 7 and
  total nightly usage is 907,380 vs 927,600 seconds, although event rows match
  11 vs 11.
- **Therapy usage is now counted honestly, with one residual.** Parser
  file-session blocks are labeled `source_kind='recording_span'` (previously
  mislabeled `resmed_str_mask_interval`, which made recording spans masquerade as
  the view's authoritative therapy source). Summary-only nights recover their
  STR-reported therapy through the session-duration fallback, so
  `zero_usage_nights` drops 37 → 0 and the parser nightly total rises from
  89,820s to 927,600s — within ~2.2% of legacy's 907,380s. The remaining +20,220s
  is the three detailed nights: they still report recording spans (89,820s)
  rather than ~69,600s of STR therapy, because the view sums `session_blocks`
  when present and cpap-parser exposes no per-mask intervals. Closing it requires
  upstream mask intervals or a view/product decision (out of scope here, no
  schema/view change). The harness now reports `usage_source` so a recording-span
  total is never silently compared against authoritative mask intervals. The
  parser path also now carries each detailed night's STR-reported therapy on its
  recording-span blocks, so the view's `summary_reported_usage_seconds` surfaces
  the authoritative therapy total (69,600s on the fixture) alongside the
  recording-span `usage_seconds` — the authoritative reference for cross-checking.
- **The detailed-night overcount is data-dependent and the fixture understates
  it.** The fixture has only 3 detailed nights of 40, so the recording-span
  overcount looks like ~2.2%. The overcount is Σ(recording span − therapy) over
  *detailed* nights, so on a card where most or all nights are detailed it is
  materially larger (tens of percent). A private, local-only real-card soak
  confirmed this and is the reason the parity harness must read `usage_source`
  and `summary_reported_usage_seconds`, not just `total_usage_seconds`.
- **Oximetry is not provable with this fixture.** It contains six SAD files and
  valid Pulse/SpO2 headers, but every SpO2 sample is the `-1` missing sentinel.
  `parse_sa2` returns `None`, so legacy correctly writes 0 rows and keeps
  `has_spo2=False`; parser also writes 0. Do not infer oximetry parity from 0=0.
- **Source provenance is now exercised honestly.** The harness mirrors production
  manifest creation: both paths receive the same 53 rows/roles. Legacy marks 25
  files used and links source UUIDs (25 block, 3 event, 6 channel, 1 settings
  source files), with 28 unconsumed files finalized as skipped. Parser now
  resolves the exact `STR.edf` reference already carried by settings snapshots,
  marking parser-consumed source roles as used with
  `consumed_without_row_link`, while preserving the exact `STR.edf` settings
  link. Synthetic block/event/channel ids still cannot be mapped safely to real
  relative paths.
- `derived_values` and `session_waveform` differ as a *consequence* of granularity /
  event-window rebasing — now classified as documented `expected_difference`s, so
  the harness fails only on a genuinely **new, undocumented** divergence.

## 6. Bottom line

The cpap-parser path is **normalized-output-validated and now DB-diffable** (the
parity oracle exists, §5a). The first measured run shows real strengths
(`session_metrics`/`session_events`/nightly-aggregate parity) and pins the live
gaps: **full settings parity remains partial** despite closing the 40 → 0 row-count
drop; the **session-granularity split** needs a product decision; oximetry/source-file gaps
are real in code but await a fixture/flow that exercises them. The remaining gating
items — dropped persisted data, the granularity model, and the operational
dependency/routing/soak gates — are now each a measurable row against the harness,
not a guess. This phase changes only the existing cpap-parser persistence bridge;
no production routing, schema, default, or dependency changed.

The remaining work is now organized by owner and cutover category in
`docs/sleeplab_2_resmed_cutover_remaining_work.md`. The leading blocker is the
implementation of the decided night-level model: block semantics and totals,
derived-value ownership, legacy migration, and cross-path dedupe must now be
made consistent with that decision.

## 7. Cross-references

- `docs/sleeplab_2_loader_and_conformance_plan.md` — go/no-go + retirement evidence.
- `docs/sleeplab_2_resmed_normalized_output_gap_audit.md` §9–§12 — normalized
  output, parser dependency, settings (`therapy_mode`), and event-type counts.
- `docs/sleeplab_2_fixture_validation_matrix.md` §4.1 — semantic coverage ladder.
- `docs/sleeplab_2_data_architecture.md` — sessions/blocks model intent.
