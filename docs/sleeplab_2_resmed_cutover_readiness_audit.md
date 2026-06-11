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
| 1 | **Persistence tests** | exercised indirectly by import | `persist_import_run` / `run_cpap_parser_import` have **no tests** (no test references either symbol) | The bridge that writes production rows is unverified end-to-end | **P0** | grep of `tests/` finds neither symbol; only normalized-`ImportRun` `validate_import` + synthetic DB `identity_hashes` exist |
| 2 | **Legacy↔parser row diff** | n/a (oracle) | none | No automated diff of the two paths' DB rows on one fixture (plan item 10 "Parallel ResMed conformance" unbuilt) | **P0** | no parity harness in repo |
| 3 | **Oximetry (SpO2)** | parses `SA2`→`replace_session_spo2`; `has_spo2 = data is not None` (`import_sessions.py:287,303,415`) | `has_spo2=False` hardcoded; no `session_spo2` writes (`persist.py:326`, docstring §"Remaining gaps") | New path drops oximetry entirely | **P0** | persist docstring + code |
| 4 | **Settings snapshots** | `replace_resmed_str_day`→`upsert_settings_snapshot` writes `settings_snapshots` (`db.py:441,547`) | writes **none**; `therapy_mode`/`mask_type`/`humidity_level` hardcoded `None` (`persist.py:327`) — and **ignores** the `therapy_mode` `SettingsSnapshot` the loader now produces | New path persists no settings; a ready-to-wire field is dropped at the bridge | **P0** | `db.py` vs `persist.py`; gap audit §11 |
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

1. **Build the legacy↔parser DB parity harness** (test-only; P0 #1,#2). Import the
   AirSense 10 fixture through both paths into a throwaway test DB (Docker:
   Postgres + `cpap-py`) and diff `sessions`/`session_blocks`/`session_events`/
   `signal_channels`/`derived_values`/`session_metrics`/`session_waveform`. This
   creates the missing oracle and makes every later gap measurable. No routing
   change.
2. **Decide the session-granularity model** (P1 #6, #11). Per-night (new) vs
   per-PLD (legacy/architecture). Product/data decision — **stop-and-ask**. Blocks
   #5, #7, #11 framing and any migration plan for already-imported data.
3. **Wire `therapy_mode` → `settings_snapshots`** in the bridge (P0 #4). The loader
   already emits the `SettingsSnapshot`; persist it via `upsert_settings_snapshot`.
   **Stop-and-ask** (persistence change) + a parity assertion in the #1 harness.
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

## 6. Bottom line

The cpap-parser path is **normalized-output-validated but persistence-unproven**.
The gating items are not the loader — they are (a) the absent legacy↔parser
**DB-level oracle**, (b) **dropped persisted data** vs legacy (oximetry,
settings, source-file lineage), and (c) an undecided **session-granularity model**
plus the operational dependency/routing/soak gates. Recommended first move is the
parity harness (step 1): test-only, no routing change, and it turns every other
row in this matrix into a measured pass/fail.

## 7. Cross-references

- `docs/sleeplab_2_loader_and_conformance_plan.md` — go/no-go + retirement evidence.
- `docs/sleeplab_2_resmed_normalized_output_gap_audit.md` §9–§12 — normalized
  output, parser dependency, settings (`therapy_mode`), and event-type counts.
- `docs/sleeplab_2_fixture_validation_matrix.md` §4.1 — semantic coverage ladder.
- `docs/sleeplab_2_data_architecture.md` — sessions/blocks model intent.
