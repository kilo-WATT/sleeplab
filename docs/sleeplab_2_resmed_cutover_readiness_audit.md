# SleepLab 2.0 — ResMed `cpap-parser` Cutover Readiness Audit

Status: **Audit only. No routing/schema/persistence/dependency change.** This
document inventories what stands between today's state and routing production
ResMed imports through the `cpap-parser` loader by default. It is grounded in the
current code, not aspiration, and cross-references the existing retirement-evidence
list in `docs/sleeplab_2_loader_and_conformance_plan.md` ("What evidence is
required before retiring native ResMed").

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
  `SLEEPLAB_USE_CPAP_PARSER` (`execution.py:31`), **default off**.
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
| 3 | **Oximetry (SpO2)** | parses `SA2`→`replace_session_spo2`; `has_spo2 = data is not None` (`import_sessions.py:287,303,415`) | `has_spo2=False` hardcoded; no `session_spo2` writes (`persist.py:326`, docstring §"Remaining gaps") | New path drops oximetry entirely | **P0** | persist docstring + code |
| 4 | **Settings snapshots** | `replace_resmed_str_day`→`upsert_settings_snapshot` writes full STR-derived `settings_snapshots` | persists loader-provided snapshots and flattens `therapy_mode` onto `sessions`; currently only `therapy_mode` is available | Therapy-mode persistence is closed, but full settings parity remains partial because cpap-parser exposes no mask/humidifier/temperature/pressure/EPR/ramp settings | **P1** | parser-backed DB parity harness; gap audit §11 |
| 5 | **STR mask-interval blocks** | `replace_resmed_str_day` emits `resmed_str_mask_interval` blocks from STR.edf (`data_architecture.md:61-64`) | blocks come from cpap-parser file-sessions but are **labeled** `source_kind="resmed_str_mask_interval"` (`persist.py:208`); no real STR intervals | Block provenance is mislabeled and the STR mask-on/off intervals are absent | **P1** | `persist.py:208` vs architecture doc |
| 6 | **Session granularity** | one `sessions` row **per PLD recording** (`block_index` loop, `import_sessions.py:292-329`); "PLD files remain durable source sessions" (`data_architecture.md:61`) | one `sessions` row **per night** (`persist.py:154`, one per `daily_summary`), `block_index=0` | Table shape + meaning of "a session" changes; diverges from documented model | **P1** | code + architecture doc |
| 7 | **Source-file provenance** | maps every file → `import_source_files`, links blocks/events/channels/derived (`import_sessions.py:331-404`) | `run.source_files=[]`; block `source_file_ids=[]`; event/channel `source_file_id=None` (`persist.py:207,221,386`) | No persisted source manifest/lineage on the new path | **P1** | persist code + comments |
| 8 | **Signal-channel source** | from raw EDF header `replace_signal_channels(header=…)` (`import_sessions.py:388`) | from `ImportRun.signals` metadata (`persist.py:343`) | Different source; channel set/units/rates parity unproven | **P1** | both code paths |
| 9 | **Event-type vocabulary** | raw labels; AHI counts via `_AHI_EVENT_TYPES` | same raw labels + loader-derived `Large Leak` rows; **not** OSCAR enum | Persisted `session_events.event_type` vocabulary is SleepLab-normalized, not OSCAR parity; `Large Leak` becomes an event row | **P1** | gap audit §12; `persist.py:66,214` |
| 10 | **`leak_unit` label** | `'L/s'` (`import_sessions.py:302`) | `'L/min'` (`persist.py:304`) | Direct metadata mismatch between paths (plan criterion: "leak unit/kind … match") | **P2** | both code paths |
| 11 | **`session_id` / dedupe** | block-scoped id; `source_session_key` legacy | `cpapparser_{date}`; `source_session_key=resmed:{key}:{date}` (`persist.py:460`, `resmed_native.py:315`) | Cross-path re-import will **not** dedupe; mixed historical data after a flip | **P1** | code |
| 12 | **TZ/DST + cross-midnight** | localizes EDF instants with `machine_tz` | same localization (`persist.py:158`) | Not yet conformance-proven on the new path (plan criterion) | **P2** | no targeted test |
| 13 | **`cpap-py` dependency** | not required | hard runtime requirement; absent from `pyproject`/`uv.lock`/CI | Production default would require a git-sourced native dep in the locked/CI closure | **P1** | gap audit §9; `execution.py:129` |
| 14 | **`/datalog/*` endpoint** | legacy subprocess only | not wired to the flag (`upload.py:398`) | A second import entry point stays legacy-only; full cutover must route or retire it | **P1** | `upload.py` |
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
2. **Decide the session-granularity model** (P1 #6, #11). Per-night (new) vs
   per-PLD (legacy/architecture). Product/data decision — **stop-and-ask**. Blocks
   #5, #7, #11 framing and any migration plan for already-imported data.
3. **Wire `therapy_mode` → `settings_snapshots`** in the bridge (P0 #4). — **DONE.**
   `persist_import_run` now uses `upsert_settings_snapshot`, preserves normalized
   settings/source names with conservative confidence, and flattens real values
   onto `sessions`. Missing/`"Unknown"` values are not persisted or fabricated.
4. **Map oximetry (SA2 SpO2/pulse)** into the new path (P0 #3). `cpap-py` exposes
   `spo2`/`pulse` on `timeseries`; write `session_spo2` + `has_spo2`. **Stop-and-ask**.
5. **Persist source-file provenance** (P1 #7). Requires the loader to expose a
   source manifest (`run.source_files`) and the bridge to populate
   `import_source_files` + link blocks/events/channels. Larger; **stop-and-ask**.
6. **Reconcile parity polish** (P2 #8,#10,#12): `leak_unit`, signal-channel
   set/units/rates, TZ/DST + cross-midnight — each as a parity assertion in the
   harness; fix the cheap mismatches (e.g. `leak_unit`).
7. **Decide `cpap-py` production dependency posture** (P1 #13). Add to a
   prod-locked path or keep `requirements.txt`-only with a documented install
   gate; ensure CI can build it before any default flip. **Stop-and-ask**.
8. **Route or retire `/datalog/*`** under the same flag (P1 #14). **Stop-and-ask**
   (routing).
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
| `sessions` | expected_difference | 43 rows, max_block_index 3 (per-PLD-block) | 40 rows, max_block_index 0 (per-night) |
| `session_blocks` | expected_difference | 72 | 7 |
| `settings_snapshots` | **expected_difference (partial parity)** | **40 rows; 14 keys; `therapy_mode=apap`** | **40 rows; `therapy_mode` only; `therapy_mode=APAP`** |
| `session_events` | **equal** | 11 (CA/Hypopnea/Large Leak/Obstructive) | 11 (same types) |
| `session_metrics` | **equal** | 34 710 | 34 710 |
| `nightly_therapy_aggregates` | **equal** | 40 | 40 |
| `signal_channels` | expected_difference | 54 rows; units incl. `L/s` | 33 rows; units incl. `L/min` |
| `derived_values` | expected_difference | 90 rows, event-count+stat keys | 520 rows, usage-semantics keys |
| `session_waveform` | expected_difference | 81 485 | 81 410 (~0.1%) |
| `session_spo2` | equal (not exercised) | 0 | 0 |
| `import_source_files` | equal (not exercised) | 0 | 0 |

Honest reads from the first run:
- **Strong parity already exists** where it matters most: low-rate `session_metrics`
  (exact), scored `session_events` (count + type set), the nightly view, and
  `total_ahi_events` (9 = 9).
- The settings row-count drop is closed (**40 → 40**) and parser sessions now
  populate `therapy_mode` (**40/40**). Full parity is still partial: legacy has
  14 setting keys and non-null mask/humidity/temperature values; parser has only
  `therapy_mode`, with those unavailable fields left `NULL`.
- The **granularity split is visible** (per-PLD-block vs per-night; 43/72 vs 40/7).
- **Oximetry (#3) and source-file provenance (#7) are NOT exercised by this
  fixture** — both sides are 0 (`session_spo2`: this card's SAD carries no usable
  SpO2; `import_source_files`: the in-transaction harness registers no upload files).
  The code gap is still real; it just needs a fixture/flow that exercises it.
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

## 7. Cross-references

- `docs/sleeplab_2_loader_and_conformance_plan.md` — go/no-go + retirement evidence.
- `docs/sleeplab_2_resmed_normalized_output_gap_audit.md` §9–§12 — normalized
  output, parser dependency, settings (`therapy_mode`), and event-type counts.
- `docs/sleeplab_2_fixture_validation_matrix.md` §4.1 — semantic coverage ladder.
- `docs/sleeplab_2_data_architecture.md` — sessions/blocks model intent.
