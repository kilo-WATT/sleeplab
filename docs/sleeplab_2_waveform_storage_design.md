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

- **cpap-parser path** (`importer/loaders/persist.py`) writes **both**:
  `_write_session_waveform` (event windows, same windowing as the native path)
  **and** `_write_waveform_chunks` (full-night compressed chunks). This path is
  **not** the production ResMed route in this milestone.

### Read patterns

- **Event Inspector** (`GET /sessions/{id}/events/...`, `api/routers/sessions.py`)
  reads `session_waveform` directly: a `(session_id, ts)`-range scan over the
  event window, downsampled in SQL via `ROW_NUMBER() % :ds`. Low-rate context is
  read from `session_metrics` the same way.

- **Full-night waveform API** (`GET /sessions/{id}/waveforms` and
  `/waveforms/{signal_name}`) reads `waveform_chunks`: it selects the chunk rows
  overlapping the requested window, decodes them
  (`importer/waveform_chunks.py:decode_window`), and reduces them for display
  with extrema-preserving downsampling (`downsample_extrema`).

- **Breath / metrics** (`GET /sessions/{id}/metrics`, `/breath`) read
  `session_metrics`.

So today the two high-rate stores serve **different read shapes**:
`session_waveform` backs the Event Inspector's direct SQL window scan;
`waveform_chunks` backs the full-night decode-and-downsample API.

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

**`session_waveform` should become one of the following**, in order of
preference as readers migrate:

1. a **derived, rebuildable event-window cache** populated from the canonical
   chunks (fast `(session_id, ts)`-range reads for the Event Inspector), then
2. a **temporary compatibility table** retained only while native-path sessions
   and API readers still depend on it, then
3. **retired** once the native importer writes chunks and all readers decode from
   chunks.

**This is a recommendation, not a statement of current state.** As of this
document, `waveform_chunks` is populated **only** by the non-production
cpap-parser path; the native ResMed production path still writes
`session_waveform` exclusively. Making chunks canonical therefore requires
real importer and API work that is **not** done here and is gated by the phases
and validation below.

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
  database** (see §9).

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

- **Event Inspector decodes windows from chunks.** `decode_window` already
  accepts `start_time`/`end_time`, so the Event Inspector could read its
  ±120/180 s windows by decoding the overlapping chunks instead of scanning
  `session_waveform`.

- **`session_waveform` becomes a cache or is retired.** Once the native path
  writes chunks and the Event Inspector reads from them, `session_waveform` is
  either a rebuildable cache (kept for read latency) or removed entirely —
  reclaiming 870 MB of table + 537 MB of indexes.

- **Full-resolution chunks as source of truth.** Chunks retain lossless
  full-night signal; nothing is discarded at ingest.

- **Downsampled display rollups.** Zoomed-out charts should read decimated tiers
  derived from chunks (e.g. min/max-per-bucket rollups), never the raw stream —
  rebuildable, never a destructive edit to the source of truth.

- **Retention settings for old detailed data.** A retention/decimation policy
  (e.g. keep raw chunks for N days, then keep only rollups) bounds long-term
  growth. Policy choice is an open question (§8).

## 8. Open questions

- **Does `session_waveform` duplicate `waveform_chunks` for all parser
  sessions?** For parser-path sessions it appears to be a strict event-window
  *subset* of the full-night chunks. This must be confirmed empirically (do all
  parser sessions with `session_waveform` rows also have covering chunks, with
  matching values within the windows?).

- **What exact API queries depend on `session_waveform`?** The Event Inspector
  window query is the known reader; a full audit (including any reports, exports,
  tests, or ad-hoc tools) is required before retiring the table.

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

## 9. Required validation before any code

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

## 10. Proposed next decision

**Do not start Phase 1 or Phase 2 yet.** First land a small,
non-schema-changing **diagnostic / reporting PR or runbook** that gathers, from a
real alpha DB, the artifacts in §9: the `(session_id, ts)` uniqueness check, the
`EXPLAIN (ANALYZE, BUFFERS)` plans for the Event-Inspector / metrics / chunk
queries, the `pg_stat_user_indexes` usage review, and the before/after size
estimates.

With that evidence in hand, make the explicit, reviewed choice:

- **Phase 1 (shrink in place)** if the priority is reclaiming space quickly with
  minimal behavior change and the rewrite lock window is acceptable; or
- **Phase 2 (chunks canonical)** if the priority is solving the row-per-sample
  scaling problem structurally and the native-importer + API reader work is in
  scope.

Either way, the migration is a separate, stop-and-ask change backed by the §9
validation — never inferred from local index counters alone.
