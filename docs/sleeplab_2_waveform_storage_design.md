# SleepLab 2.0 Waveform Storage Scalability — Design

> **Status: design only.** This document analyzes SleepLab's high-rate waveform
> storage and proposes a phased migration path. Nothing here is implemented. No
> migration, schema change, index change, importer change, or API change is made
> by this document. The work it describes is **stop-and-ask**: each phase is a
> separate, reviewed decision, consistent with the Alpha-6 storage governance in
> `docs/sleeplab_2_data_architecture.md` ("Waveform storage scope").

## 1. Current storage model

SleepLab persists time-series therapy data in three tables, written by two
import paths and read by the session/Event-Inspector API.

### Tables

- **`session_waveform`** (migration `013_add_session_waveform.sql`) — high-rate
  BRP `flow`/`pressure`, stored **one SQL row per timestamp**. Schema:
  `id BIGSERIAL PRIMARY KEY, session_id UUID, ts TIMESTAMPTZ, flow NUMERIC(7,4),
  pressure NUMERIC(6,2)`, with `idx_session_waveform_session_id_ts` on
  `(session_id, ts)`. Storage is **event-windowed only** — merged windows of
  120 s before / 180 s after each scored event, clipped to the recorded span —
  so its size scales with event count, not night length.

- **`session_metrics`** — low-rate (0.5 Hz PLD) channels (`mask_pressure`,
  `pressure`, `epr_pressure`, `leak`, `resp_rate`, `tidal_vol`, `min_vent`,
  `snore`, `flow_lim`), stored full-resolution, one row per 2 s timestamp.
  Indexed by `idx_session_metrics_session_id` (migration 004) and
  `idx_session_metrics_session_id_ts` (migration `014_add_event_inspector_indexes.sql`).

- **`waveform_chunks`** (migration `028_add_waveform_chunks.sql`) — full-night
  high-rate signals stored as **compressed segments**: each row is one
  independently decodable interval (`DEFAULT_CHUNK_SECONDS = 300`) whose
  `payload BYTEA` is little-endian float32 compressed with zlib
  (`encoding = 'float32-le-zlib-v1'`). Rows carry signal/unit/sample-rate,
  `start_time`/`end_time`, `chunk_index`, `sample_count`, `uncompressed_bytes`,
  `compressed_bytes`, full import/source provenance, and a
  `UNIQUE (session_id, signal_name, chunk_index)` constraint. This is the
  OSCAR-style `event_lists` (metadata/index row) + `event_data` (compressed
  payload) shape named as the target in the data-architecture doc.

### Import paths

- **Native ResMed path** (`importer/db.py:replace_session_waveform`, called from
  `importer/import_sessions.py`) writes **`session_waveform` only**, event-windowed.
  Per Alpha-6 governance this is the **production path and regression oracle**.

- **cpap-parser path** (`importer/loaders/persist.py`) prefers full-night
  compressed `waveform_chunks`. A successful chunk write suppresses and clears
  duplicate `session_waveform` rows; event-window rows are written only when no
  chunks can be produced.

### Read patterns

- **Event Inspector** (`GET /sessions/{id}/events/...`, `api/routers/sessions.py`)
  decodes overlapping `waveform_chunks` first. When no relevant chunks exist it
  falls back to a `(session_id, ts)` range scan of `session_waveform`,
  downsampled in SQL via `ROW_NUMBER() % :ds`. Low-rate context is read from
  `session_metrics` the same way.

- **Full-night waveform API** (`GET /sessions/{id}/waveforms` and
  `/waveforms/{signal_name}`) reads `waveform_chunks`: it selects the chunk rows
  overlapping the requested window, decodes them
  (`importer/waveform_chunks.py:decode_window`), and reduces them for display
  with extrema-preserving downsampling (`downsample_extrema`).

- **Breath / metrics** (`GET /sessions/{id}/metrics`, `/breath`) read
  `session_metrics`.

So today `waveform_chunks` is the preferred high-rate store for cpap-parser
imports and both waveform APIs. `session_waveform` remains the native importer's
write target and the Event Inspector's compatibility fallback.

## 2. Evidence from the local alpha DB

Measured on the local alpha `cpap` database (46 sessions). Major tables show
**0 dead tuples**, so this is genuine data, not vacuum bloat.

| Metric | Value |
| --- | ---: |
| `cpap` database size | 1241 MB |
| Postgres data folder | 2.4 GB |
| Sessions | 46 |
| `session_waveform` rows | 2,296,648 |
| `session_metrics` rows | 501,450 |
| `waveform_chunks` rows | 6,750 |
| `session_waveform` total size | 870 MB |
| `session_waveform` table data | 333 MB |
| `session_waveform` indexes | 537 MB |
| &nbsp;&nbsp;↳ `idx_session_waveform_session_id_ts` | 393 MB (heavily used) |
| &nbsp;&nbsp;↳ `session_waveform_pkey` | 144 MB (0 `idx_scan` locally) |
| `waveform_chunks` total size | 125 MB |

Every `session_waveform` row has both `flow` and `pressure` populated.

### The decisive comparison

`session_waveform` stores **event windows only** and uses **870 MB**.
`waveform_chunks` stores **full nights** — strictly more signal — in **125 MB**.

Per stored sample, `session_waveform` costs ~190 bytes (≈152 B heap + ≈245 B
index, spread over two values per row), versus ~2–3 bytes/sample in the
compressed chunks. The compressed-segment layout is roughly **two orders of
magnitude denser** while retaining full resolution. Indexes alone on
`session_waveform` (537 MB) exceed the entire `waveform_chunks` table (125 MB) by
more than 4×.

## 3. Problem statement — why row-per-sample SQL is expensive

BRP `flow`/`pressure` arrive at 25 Hz (1500 samples per 60 s record). Storing one
SQL row per timestamp incurs fixed per-row overhead that dwarfs the 8 bytes of
actual signal:

- **Row count explosion.** A single 8 h night at 25 Hz is ~720k timestamps;
  `importer/waveform_estimate.py` codifies ~90k rows/hour and ~21.6 M rows for a
  30-night card (flow+pressure, one machine). The local DB already holds
  2.3 M rows for just 46 sessions — and that is the *event-windowed* subset, not
  full-night.

- **Large btree indexes.** A `(uuid, timestamptz)` btree over millions of rows is
  inherently large: `idx_session_waveform_session_id_ts` is **393 MB**. Index
  size grows with row count, and here indexes (537 MB) are larger than the heap
  (333 MB).

- **`numeric` overhead.** `NUMERIC(7,4)` / `NUMERIC(6,2)` are variable-length,
  arbitrary-precision types — heavier to store and slower to compare than
  fixed-width floats, for values that are display-bound to 4 dp / 2 dp.

- **Surrogate bigint PK overhead.** `id BIGSERIAL` adds 8 bytes/row to the heap
  and a **144 MB** btree that, locally, has **0 `idx_scan`** — it is referenced
  by no foreign key and appears in no read query.

- **Scaling.** All of the above is per-row and therefore linear in samples. At
  hundreds-to-thousands of nights across multiple tenants, a row-per-sample
  high-rate table becomes the dominant, index-heavy cost in the database.

## 4. Canonical recommendation

**`waveform_chunks` should become the long-term canonical high-rate waveform
store.** It already is the lossless, full-night, compressed-segment design the
architecture doc designates as the target; it is ~70× denser per sample, has a
trivially small index footprint at 6,750 rows, is unit-tested
(`tests/test_waveform_chunks.py`), and already backs the `/waveforms` API.

**`session_waveform` is now legacy/fallback storage.** For cpap-parser-backed
imports, successfully encoded chunks suppress the event-window row write and
remove stale rows for that session. If no chunks can be produced, the importer
still writes event-window rows so incomplete inputs retain Event Inspector
coverage. The native/legacy importer continues to write rows because it does
not yet write chunks.

Longer term, `session_waveform` can become one of the following, in order of
preference as remaining writers migrate:

1. a **derived, rebuildable event-window cache** populated from the canonical
   chunks (fast `(session_id, ts)`-range reads for the Event Inspector), then
2. a **temporary compatibility table** retained only while native-path sessions
   and API readers still depend on it, then
3. **retired** once the native importer writes chunks and all readers decode from
   chunks.

`waveform_chunks` is populated by the cpap-parser path; the native ResMed path
still writes `session_waveform` exclusively. Chunk-first Event Inspector reads
and cpap-parser write suppression are implemented, but completing the canonical
cutover still requires migrating that remaining native writer.

## 5. Why not drop indexes now

- **`idx_session_waveform_session_id_ts` is large but heavily used.** It is the
  index the Event Inspector range scan depends on. Dropping or altering it
  without a validated replacement would regress the primary high-rate read path.

- **`session_waveform_pkey` appears unused locally but is tied to PK design.**
  Its 0 `idx_scan` makes it *look* free to drop, but it backs the table's PRIMARY
  KEY (`id`). Removing it is a primary-key change (Phase 1), not an index drop —
  it must be replaced by an alternative key/uniqueness story, not simply deleted.

- **`idx_session_metrics_session_id` may look redundant but is used.** It can
  appear superseded by `idx_session_metrics_session_id_ts`, but a leading-column
  composite index does not always serve the same plans (e.g. equality-only
  lookups, planner cost choices). It must not be assumed redundant without
  `EXPLAIN` evidence.

- **Index/schema changes require real query validation.** Local `idx_scan`
  counters reflect only the queries this developer ran against this dataset.
  Production plans depend on data distribution and statistics. **No index is
  added, dropped, or altered without `EXPLAIN (ANALYZE, BUFFERS)` on a realistic
  database** (see §10).

## 6. Phase 1 option — shrink `session_waveform` in place

A low-risk, behavior-preserving option that keeps both the table and its read
shape but reduces its footprint. Presented as a **future, optional** migration,
not a committed change.

- **Check uniqueness of `(session_id, ts)`.** Required before any composite-key
  change. Overlapping recording blocks rebased onto a shared start could, in
  principle, collide on `ts`. If duplicates exist, a UNIQUE/PRIMARY KEY on
  `(session_id, ts)` is not yet safe and a non-unique composite index must be
  used instead.

- **Replace the surrogate `id` PK with a composite key/index.** If
  `(session_id, ts)` is unique, make it the PRIMARY KEY: this drops the 144 MB
  `session_waveform_pkey`, removes 8 bytes/row from the heap, and makes the PK
  index serve the Event Inspector query directly. If not unique, at minimum drop
  the surrogate-key dead weight in favor of the existing composite index.

- **Convert `numeric` columns to `real`/`float4`.** `flow` (±~3 L/s, 4 dp) and
  `pressure` (4–25 cmH₂O, 2 dp) fit comfortably within float32's ~7 significant
  digits and match the float32 encoding already used by `waveform_chunks`. Saves
  ~8 bytes/row on the heap and speeds comparisons. `double precision` offers no
  size win over `numeric` here.

- **Evaluate whether separate indexes can be removed.** Once `(session_id, ts)`
  is the PK, the standalone `idx_session_waveform_session_id_ts` may be
  redundant — but only remove it after `EXPLAIN` confirms the PK serves the same
  plans.

- **Require `EXPLAIN` plans before implementation.** No type or key change ships
  without before/after plans for the real read queries.

- **Risks.** Type/PK changes **rewrite the whole table** under an
  `ACCESS EXCLUSIVE` lock — long on a 333 MB table, blocking concurrent reads
  and writes for the duration. Migration time, lock window, rollback, and any
  ORM/`INSERT … VALUES` assumptions about the `id` column must be planned for.
  Phase 1 reduces cost but does **not** solve the fundamental row-per-sample
  scaling problem — it only postpones it.

## 7. Phase 2 option — make chunks canonical

The structural fix. Larger, design-first, stop-and-ask. Eliminates the
row-per-sample high-rate table rather than shrinking it.

- **Native ResMed importer writes `waveform_chunks`.** The native path already
  decodes the full-resolution `Flow.40ms` / `Press.40ms` arrays
  (`channels["Flow.40ms"]`, `channels["Press.40ms"]`); it would build and persist
  chunks the same way `persist._write_waveform_chunks` does, giving production
  sessions a canonical full-night store. This must preserve current native
  behavior and remain the regression oracle (Alpha-6 constraint).

- **Event Inspector decodes windows from chunks.** This reader cutover is now
  implemented with a `session_waveform` fallback; see §8.

- **`session_waveform` remains fallback storage or is retired.** The Event
  Inspector already reads chunks first and cpap-parser imports no longer write
  duplicate rows when chunks are produced. Once the native path writes chunks,
  `session_waveform` is
  either a rebuildable cache (kept for read latency) or removed entirely —
  reclaiming 870 MB of table + 537 MB of indexes.

- **Full-resolution chunks as source of truth.** Chunks retain lossless
  full-night signal; nothing is discarded at ingest.

- **Downsampled display rollups.** Zoomed-out charts should read decimated tiers
  derived from chunks (e.g. min/max-per-bucket rollups), never the raw stream —
  rebuildable, never a destructive edit to the source of truth.

- **Retention settings for old detailed data.** A retention/decimation policy
  (e.g. keep raw chunks for N days, then keep only rollups) bounds long-term
  growth. Policy choice is an open question (§9).

## 8. Event Inspector preferred read path

The Event Inspector now reads overlapping `flow_rate` and `pressure` data from
`waveform_chunks` first, decodes the requested event window, and merges both
signals into the existing columnar `timestamps` / `flow` / `pressure` response.
This preserves the frontend API contract and its current empty-window behavior.

If no relevant chunks overlap the requested window, the reader falls back to
the existing `session_waveform` query. The fallback is intentionally retained
for legacy and partially populated nights; `session_waveform` is not deleted.
Chunk rows win when both stores contain the window,
so the row table is now a compatibility fallback for this reader rather than its
preferred source.

## 9. Open questions

- **Does `session_waveform` duplicate `waveform_chunks` for every future input?**
  No for cpap-parser-backed imports: a successful chunk write clears/suppresses
  row storage. The fallback remains necessary for native imports, legacy data,
  and parser inputs with no chunkable high-rate signals.

- **What non-Event-Inspector consumers still depend on `session_waveform`?** The
  runtime Event Inspector reader has moved to chunks-first behavior, but reports,
  diagnostics, tests, and ad-hoc tooling must still be audited before retiring
  the table.

- **Can the native ResMed import write chunks without losing current behavior?**
  The native path must remain the production oracle; adding chunk writes must be
  additive and must not perturb existing `session_waveform`/metrics output until
  readers are migrated.

- **What retention/downsampling policy should SleepLab expose?** Raw-retention
  window, rollup tiers/resolutions, and whether retention is global, per-tenant,
  or user-configurable are unresolved.

- **Should waveform chunks move outside Postgres later, or remain in-database
  compressed chunks?** In-Postgres `BYTEA`/TOAST keeps backups and access
  simple; object storage could lower DB size and backup cost at the price of
  operational complexity, byte-range read design, and consistency. Out of scope
  here; flagged for a later decision.

## 10. Required validation before any schema migration

No migration is written until these are gathered from a **realistic** database
(ideally a copy of the alpha DB), not assumed from local counters:

1. **Duplicate check for `(session_id, ts)`** (gates any composite key):
   ```sql
   SELECT session_id, ts, COUNT(*)
   FROM session_waveform
   GROUP BY session_id, ts
   HAVING COUNT(*) > 1
   LIMIT 20;
   ```
2. **`EXPLAIN (ANALYZE, BUFFERS)` for the Event Inspector waveform query** — the
   `(session_id, ts)`-range scan with `ROW_NUMBER() % :ds` downsampling — to
   confirm which index serves it and the cost.
3. **`EXPLAIN (ANALYZE, BUFFERS)` for the graph/window queries** — the
   `session_metrics` window query (`/metrics`, `/breath`) and the
   `waveform_chunks` overlap query (`/waveforms/{signal_name}`).
4. **Index usage review** — `pg_stat_user_indexes` (`idx_scan`,
   `idx_tup_read`/`fetch`) and `pg_relation_size` for every index on
   `session_waveform` and `session_metrics`, to confirm what is truly unused
   versus merely idle in one local session.
5. **Before/after size estimate** — projected heap/index sizes after PK change +
   `numeric`→`real`, and after chunk-canonical retirement, so the reclaimed space
   is quantified before committing.
6. **Migration rollback plan** — for any rewrite: lock window, expected
   duration, how to restore the prior shape, and confirmation that importer
   `INSERT` paths and API readers tolerate the new schema.

## 11. Proposed next decision

**Do not start Phase 1 or Phase 2 yet.** First land a small,
non-schema-changing **diagnostic / reporting PR or runbook** that gathers, from a
real alpha DB, the artifacts in §10: the `(session_id, ts)` uniqueness check, the
`EXPLAIN (ANALYZE, BUFFERS)` plans for the Event-Inspector / metrics / chunk
queries, the `pg_stat_user_indexes` usage review, and the before/after size
estimates.

With that evidence in hand, make the explicit, reviewed choice:

- **Phase 1 (shrink in place)** if the priority is reclaiming space quickly with
  minimal behavior change and the rewrite lock window is acceptable; or
- **Phase 2 (chunks canonical)** if the priority is solving the row-per-sample
  scaling problem structurally and the native-importer + API reader work is in
  scope.

Either way, the migration is a separate, stop-and-ask change backed by the §10
validation — never inferred from local index counters alone.

## 12. Running the diagnostics

`scripts/waveform_storage_diagnostics.py` gathers the section 10 evidence without
changing the database. Run it only against a local development database or a
copy of the alpha database, preferably while no import is active:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg2://user:password@localhost:5432/cpap_copy'
uv run python scripts/waveform_storage_diagnostics.py --output waveform-storage-report.md
```

The command starts a read-only transaction, applies a two-minute statement
timeout, runs aggregate size/duplicate/index queries and representative
`EXPLAIN (ANALYZE, BUFFERS)` reads, then rolls back. Override the timeout with
`--statement-timeout-ms` if a large copied database needs longer. The output
path should remain outside source control when it contains evidence from a real
database.

The generated Markdown excludes session IDs, exact timestamps, signal values,
device identifiers, provenance, source paths, and serial numbers. Query-plan
literals are sanitized as a second precaution. It reports aggregate session
coverage across the two waveform stores because Phase 2 is not cutover-ready
while any `session_waveform` session lacks chunks. Its recommendation is
deliberately conservative and does not authorize a migration.

### Validating row/chunk waveform parity

Before a chunk-canonical reader cutover, compare representative windows from
sessions populated in both stores:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg2://user:password@localhost:5432/cpap_copy'
uv run python scripts/waveform_parity_validator.py --sessions 5 --window-seconds 300 --output waveform-parity-report.md
```

The validator uses a read-only transaction and compares the row-backed `flow`
and `pressure` windows with decoded chunk-backed `flow_rate` and `pressure`
windows. It checks ordered timestamp alignment, sample and null counts,
individual value tolerances, and min/max/mean parity. Defaults reflect the row
store's existing rounding (0.0001 for flow and 0.0051 for pressure), plus a
0.5 ms timestamp tolerance. `--value-tolerance` overrides both signal defaults;
`--timestamp-tolerance-ms` controls timestamp matching.

The command exits zero only when at least one window was compared and every
comparison passed. Its Markdown contains anonymous window numbers, counts,
pass/fail results, and aggregate differences only. It never includes session
IDs, exact timestamps, waveform values, patient/device details, provenance, or
source paths. Keep reports from real databases outside source control.
