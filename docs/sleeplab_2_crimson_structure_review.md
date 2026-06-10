# SleepLab 2.0 Alpha 7 OSCAR Database Architecture Review

Status: Alpha 7 planning review only. No production code, import routing, schema,
migration, Lowenstein persistence, ResMed parser cutover, full-night waveform
storage, or tag work is included or authorized here. This document is
documentation-direction only.

> **Supersedes the earlier "crimson-structure" framing.** The prior version of
> this file was a placeholder written when no OSCAR/crimson source was available
> locally; it reasoned from SleepLab's own notes and explicitly flagged that it
> should be re-grounded once real material arrived. That material is now
> available: the uploaded **`OSCAR-code-master.tar.gz`** is the actual OSCAR 2.0
> source tree, including a complete SQLite database layer. This review replaces
> the speculative crimson-structure mapping with file-specific observations from
> that archive. "Crimson-structure" should **no longer** be treated as the
> primary Alpha 7 reference — the OSCAR 2.0 on-disk database model is the better,
> concrete reference and is used as the baseline throughout this document.

## 0. Source and method

The archive was extracted **outside the repository** to a temporary, untracked
location and inspected read-only. **No OSCAR source, schema files, ER diagrams,
or extracted third-party code are committed** — only this SleepLab planning
document. The OSCAR areas inspected were:

- `Notes/Database/DATABASE_SCHEMA.md` (narrative schema, declares "Schema
  Version 16")
- `Notes/Database/SCHEMA_DESIGN_PHILOSOPHY.md` (database-only storage rationale,
  per-version highlights)
- `Notes/Database/DATA_DICTIONARY.md`, `DATABASE_SCHEMA_REFERENCE.md`,
  `DATABASE_INDEXES_AND_FOREIGN_KEYS.md`, `OSCAR_Data Directory Contents.md`
- `oscar/database/` C++ repository layer: `database_schema.{h,cpp}`,
  `migration_manager.{h,cpp}`, `database_manager.cpp`, and the per-entity
  repositories (`machine_`, `session_`, `session_slices_`, `session_settings_`,
  `session_channels_`, `session_channel_values_`, `session_summaries_`,
  `respiratory_events_`, `event_list_`, `event_data_`, `daily_summary_`,
  `channel_`, `device_time_correction_repository.{h,cpp}`)

SleepLab-side sources reviewed: `docs/sleeplab_2_data_architecture.md`,
`docs/sleeplab_2_loader_and_conformance_plan.md`,
`docs/sleeplab_2_release_roadmap.md`, `docs/sleeplab_2_alpha_6_checklist.md`,
`docs/sleeplab_2_import_level_conformance_plan.md`, `importer/loaders/models.py`,
`importer/conformance.py`, `importer/db.py`, `importer/loaders/resmed_native.py`,
`importer/loaders/persist.py`, `importer/waveform_estimate.py`,
`tests/test_conformance.py`, `tests/test_resmed_import_regressions.py`,
`tests/test_waveform_estimate.py`.

## 1. What the OSCAR 2.0 archive shows about OSCAR's current architecture

OSCAR 2.0 has moved from a desktop application that stored data as a profile
folder of binary `.000`/`.001` files plus XML into a **single-file SQLite
database** (`oscar.db`, opened in `oscar/main.cpp` / `profileselector.cpp`).
This is the central finding and it directly validates SleepLab's database-native
direction.

Confirmed facts:

1. **`oscar.db` SQLite database.** Confirmed — the application reads/writes
   `GetAppData() + "/oscar.db"` and a single file holds all user data.

2. **Everything is in the database.** Machines, sessions, mask-on/off slices,
   per-session settings, per-channel summary statistics, value/time histograms,
   daily summaries, scored respiratory events, and **waveform/event sample data**
   are all DB rows. Per `SCHEMA_DESIGN_PHILOSOPHY.md`, the v8 "database-only
   mode" deliberately eliminated the legacy `.001` files.

3. **`event_lists` + `event_data` store signals as compressed BLOBs, not
   one-row-per-sample.** `event_lists` holds **one metadata row per EventList**
   (channel, type, first/last time, sample count, rate, gain, offset, min/max,
   dimension, `data_size`, `compressed_size`). `event_data` holds **one row per
   EventList** whose `data_blob`/`data_compressed` (and parallel `time_*` /
   `data2_*` columns) are the entire sample array stored as a single
   `qCompress`'d BLOB. Compression is ~40–60% (qCompress level 9), applied only
   when it saves >10%, with a CRC16 `checksum`. EventList `event_type`:
   `0 = Waveform`, `1 = Event`. **This is the most important design lesson for
   SleepLab's future waveform work** (§7).

4. **Documented schema version lags the code.** `DATABASE_SCHEMA.md` declares
   "Schema Version 16," but the code constant is
   `CURRENT_SCHEMA_VERSION = 17` (`database_schema.h`), with
   `MIN_RESTORE_SCHEMA_VERSION = 12`. The v16→v17 migration
   (`migrateV16ToV17`) is the live edge. Observation verified exactly as the
   kickoff brief predicted — the narrative doc trails the code by one version.

5. **Schema v17 adds `device_time_corrections`.** Confirmed. The v17 migration
   creates a per-machine, date-ranged, **reversible**, typed time-correction
   table. This is highly relevant to SleepLab's future timezone/DST/travel/clock
   handling and is treated as a dedicated topic in §10.

6. **OSCAR validates the direction but should not be copied literally.**
   OSCAR is a single-user desktop SQLite app; SleepLab is a multi-user,
   self-hosted **web/Postgres** application with durable import provenance and
   stricter privacy needs. The concepts map; the storage decisions do not all
   transfer (§6).

Additional architecture notes worth recording:

- **Profile-centric with denormalized `profile_id`.** All data hangs off
  `profiles`; v12 pushed `profile_id` down into `session_settings`,
  `session_channels`, `session_summaries`, `event_lists`, and
  `respiratory_events` purely for query performance. OSCAR's `profile` is the
  rough analogue of a SleepLab user/tenant, **not** a machine.
- **Two summary tiers.** `session_summaries` (per session) and `daily_summaries`
  (per profile-day) are both **pre-computed cache tables** with a
  `sessions_hash` for invalidation — the same "derived/aggregate values are
  first-class, provenance-stamped" instinct SleepLab encodes in
  `derived_values` / `nightly_therapy_aggregates`.
- **`session_channel_values`** (v7 bug-fix table) stores the value→(count,
  time_ms) histogram per channel so **weighted/time-based averages** are correct.
  This is a direct, concrete precedent for SleepLab's weighted-summary
  conformance work.
- **`daily_summaries` dropped its per-machine dimension in v16.** OSCAR
  concluded a daily rollup is inherently a *profile-day* that already aggregates
  across all contributing machines, and the `machine_id` key fragment was a
  never-correctly-used design mistake. SleepLab should read this as a caution
  (§5/§6), since SleepLab's aggregates are deliberately **machine-scoped**.
- **No import-run / source-file audit tables.** OSCAR has machines and
  `last_imported`/`data_version` columns but **no durable per-import audit
  trail** of source files, hashes, warnings, and dispositions. This is a place
  SleepLab is intentionally *richer*, not behind (§5/§6).
- **File-to-DB consolidation is a running theme.** v8 (waveforms), v9
  (`json_value`), v11/v13 (reports → `report_tree`), v14 (`app_preferences`,
  `graph_layouts`) all moved data out of side files into the DB. The trajectory
  is "one transactional database is the source of truth," which is exactly
  SleepLab's posture.

## 2. Is "crimson-structure" still the primary reference?

**No.** The actual OSCAR 2.0 database model is now the better Alpha 7 reference.
"Crimson-structure" was a name for an *inferred* OSCAR-style import architecture
used while no concrete source was in hand. Now that the real OSCAR 2.0 tree —
with a documented, versioned SQLite schema and a per-entity C++ repository layer
— is available, planning should be grounded in the concrete model, not the
inferred one. This document is therefore reframed as the **OSCAR 2.0 Database
Architecture Review** and the OSCAR DB schema is the baseline for the mapping in
§3.

OSCAR 2.0's DB model should still be **mined, not adopted**. It must not become:
a literal SleepLab schema, a required dependency, a replacement for SleepLab's
loader contract or conformance gates, or a reason to enable Lowenstein,
parser-backed ResMed, or full-night waveform persistence without fixture
evidence.

## 3. OSCAR 2.0 → SleepLab concept mapping

| SleepLab (Postgres) | OSCAR 2.0 (SQLite) | Notes |
|---|---|---|
| `cpap_machines` | `machines` | Aligned; SleepLab identity is richer (adapter id/version, validation/support state). |
| `import_runs` | *(none — implicit)* | **SleepLab is richer.** OSCAR has no durable import audit. |
| `import_source_files` | *(none — implicit)* | **SleepLab is richer.** OSCAR keeps no per-file manifest/hash/disposition. |
| `sessions` | `sessions` | Aligned; both machine-scoped + source session key (`UNIQUE(machine_id, session_id)`). |
| `session_blocks` | `session_slices` | Aligned; OSCAR `status` 0/1 = mask-off/mask-on. |
| `settings_snapshots` | `session_settings` (+`json_value`) | Aligned in intent; OSCAR is `(channel_id → REAL value)` + JSON. |
| `signal_channels` | `channels` / `session_channels` (meta) | OSCAR splits per-profile channel config (`channels`) from per-session channel stats (`session_channels`). |
| `session_events` | `respiratory_events` | Aligned; OSCAR `event_type` int enum, has `desaturation`/`severity`. |
| `session_metrics` (low-rate) | `session_channels` + `session_channel_values` | OSCAR stores stats + value/time histogram, not always raw low-rate rows. |
| `session_waveform` / event-window | `event_lists` + `event_data` (BLOB) | **Key divergence in storage form** — OSCAR = compressed BLOB per EventList; SleepLab = row-per-timestamp, event-windowed. |
| `derived_values` | `session_summaries` | OSCAR's per-session cache; SleepLab's carry method/version/provenance. |
| `nightly_therapy_aggregates` | `daily_summaries` | Aligned; **OSCAR dropped per-machine dimension (v16); SleepLab keeps machine scope** (§5/§6). |
| conformance fixtures | *(none)* | SleepLab-specific; no OSCAR equivalent. |

Detail notes per target:

- **`cpap_machines` ↔ `machines`.** Both treat the machine as durable import
  identity (`loader_name`, `serial_number`, `model_number`, `series`,
  `last_imported`, `data_version`, free-form `properties`). SleepLab keeps the
  machine the anchor for every conformance/import decision and adds adapter
  identity/version, support state, validation state, and source fingerprint for
  unresolved serials. **Keep machine identity as the anchor; do not fold it back
  into equipment records, and do not adopt OSCAR's profile as the identity
  boundary.**

- **`import_runs` / `import_source_files` ↔ (no OSCAR equivalent).** OSCAR
  records only coarse machine-level `last_imported`/`data_version`. SleepLab's
  durable, content-addressed import-run + source-file manifest (with
  per-file role, hash, disposition, parser component, diagnostics, and JSONB
  `warnings`) is a **deliberate SleepLab strength** for a web app where every
  import must be explainable after the fact. Alpha 7 should reinforce this, not
  trade it away.

- **`sessions` ↔ `sessions`.** Both machine-scoped and source-keyed. OSCAR's
  `summary_only`, `no_settings`, `events_loaded` flags map onto SleepLab's
  summary-only / absence diagnostics. **Keep session uniqueness machine-scoped;
  never fall back to `(user_id, session_id)`.**

- **`session_blocks` ↔ `session_slices`.** OSCAR stores mask-on/off intervals
  directly; SleepLab stores `resmed_str_mask_interval` blocks plus legacy
  single-span. **Block interval-boundary conformance is now a concrete Alpha 7
  target** (OSCAR gives us a reference shape for start/end/status).

- **`settings_snapshots` ↔ `session_settings`.** OSCAR is `channel_id → REAL`
  with a `json_value` escape hatch for complex types. SleepLab keeps normalized
  keys + vendor JSON and refuses to guess unknown values. **Settings *value*
  comparison (with "missing ≠ off") remains the principal settings gap** —
  presence checks exist; value checks await deeper loader support.

- **`signal_channels` ↔ `channels` / `session_channels`.** OSCAR separates
  per-profile channel *config* (`channels`: color, label, thresholds,
  visibility) from per-session channel *statistics* (`session_channels`: count,
  sum, avg, **wavg**, min/max, median, p90/p95, phys_min/max, cph/sph,
  first/last time, gain). SleepLab's `signal_channels` is a presence/metadata
  inventory (normalized key, source label, unit, rate, kind, leak semantics);
  per-session stats live in `session_metrics`/`derived_values`. The split is
  compatible. **Reconcile remaining native-vs-cpap-parser unit/name divergences
  before any parser cutover.**

- **`session_events` ↔ `respiratory_events`.** OSCAR retains source-derived
  type/timestamps and adds `desaturation`/`severity`/`channel_id`. SleepLab
  keeps source event key/type, replace-on-import, and provenance. **Event
  count/type/timestamp parity against an OSCAR reference export stays high
  priority** — OSCAR's enum (`0=Obstructive, 1=Unclassified, 2=Hypopnea,
  3=RERA, 4=Clear Airway, 5=User-flagged`) is a usable mapping target, including
  the v10 `central → unclassified` semantic correction.

- **`session_metrics` ↔ `session_channels` + `session_channel_values`.**
  OSCAR's `session_channel_values` (value → count + `time_ms`) is exactly the
  data needed for **weighted/time-based** averages, and OSCAR added it as a
  bug-fix because simple averages were wrong. SleepLab should treat this as a
  precedent for **time-weighted summary conformance** where ResMed PLD data
  already exists.

- **`session_waveform` / event-window ↔ `event_lists` + `event_data`.** Same
  intent (store the signal), different storage form. OSCAR = **one compressed
  BLOB per EventList**; SleepLab = **one row per timestamp**, event-windowed.
  See §7–§9.

- **`derived_values` ↔ `session_summaries`.** OSCAR's per-session summary is a
  pre-computed cache with `sessions_hash` invalidation. SleepLab's
  `derived_values` carry method/version, unit, input refs, and validation.
  **Expand parity checks for source summary values vs. computed aggregates
  separately**, because ResMed already has multiple valid usage semantics.

- **`nightly_therapy_aggregates` ↔ `daily_summaries`.** Same purpose
  (therapy-day rollup for UI/reports/adherence). OSCAR's v16 removal of the
  per-machine dimension is a notable divergence: OSCAR's day is profile-scoped;
  SleepLab's aggregate is deliberately **machine-local-date** scoped. **Keep
  aggregate semantics as SleepLab's authoritative path for UI, reports,
  adherence, and AI duration inputs.**

- **conformance fixtures ↔ (none).** OSCAR has no fixture/conformance harness.
  SleepLab's planning-/import-level conformance with checked-in anonymized
  fixtures and OSCAR reference exports is SleepLab-specific and is where real
  Alpha 7 progress is measured.

## 4. Where SleepLab already aligns with OSCAR 2.0

1. Database-native, single source of truth (OSCAR `oscar.db`; SleepLab Postgres).
2. Machine is durable import identity, separate from per-user config.
3. Sessions are machine-scoped and source-session-keyed.
4. Explicit mask-on/off intervals are first-class (`session_slices` ↔
   `session_blocks`).
5. Settings are per-session snapshots with a JSON escape hatch for complex types.
6. Signals retain normalized identity, units, rates, gain, and per-channel stats.
7. Events retain source type/time and are replaceable on re-import.
8. Two-tier pre-computed summaries (session + day) with cache invalidation /
   provenance.
9. Weighted/time-based statistics are recognized as requiring value/time
   histograms, not just simple averages.
10. Trend toward consolidating side-files into one transactional store.

## 5. Where SleepLab has gaps

1. **OSCAR numeric parity is still mostly design-only.** The reference-hash
   check exists; row/value parity against OSCAR's `session_summaries` /
   `daily_summaries` / `respiratory_events` is not yet implemented.
2. **Settings *value* comparison incomplete** (presence/count only today).
3. **Session-block interval-boundary comparison incomplete** (block-count only).
4. **Event timestamp/type/count parity needs more fixture coverage.**
5. **Time-weighted summary conformance** is not yet exercised, although OSCAR's
   `session_channel_values` shows exactly the data shape required.
6. **Native vs. cpap-parser channel unit/name divergences** remain for some
   channels.
7. **Oximetry sample persistence is uneven** — native path writes
   `session_spo2`; the cpap-parser persistence path still reports the gap
   (`persist.py` `has_spo2: False`).
8. **Lowenstein read-only normalized conformance** has not yet become the first
   non-ResMed vertical slice (still blocked on a safe anonymized/synthetic
   fixture).
9. **No compressed waveform-segment storage design exists yet** — SleepLab has
   only event-window row storage; OSCAR's `event_lists`/`event_data` BLOB model
   is the reference to study *before* any full-night persistence (§7–§9).
10. **No device-time-correction model** — OSCAR's v17 `device_time_corrections`
    has no SleepLab analogue yet (§10).

## 6. Where SleepLab intentionally differs (web/Postgres-native)

1. **Durable import history matters more.** Every import is auditable via
   `import_runs` / `import_source_files`; OSCAR keeps only coarse machine
   `last_imported`. SleepLab keeps the richer audit on purpose.
2. **Content-addressed source provenance is first-class** because imports arrive
   from uploads, archives, local paths, or future services.
3. **Identity must be idempotent and transactional at the row level.**
   Source-key replacement is safer than OSCAR's in-place profile mutation.
4. **Stricter privacy.** Filenames, serials, logs, and uploaded manifests can be
   exposed through a web UI, backups, or support bundles, so SleepLab anonymizes
   and hashes where OSCAR (single-user desktop) need not.
5. **Storage form differs by necessity.** A single `qCompress` BLOB per
   EventList is ideal for a local SQLite file; in multi-tenant Postgres, blob
   bloat, backup size, and query/streaming patterns push toward a *segment*
   model with explicit retention — not row-per-sample, and not naïvely one giant
   BLOB either (§7–§9).
6. **Machine-scoped aggregates are kept on purpose.** OSCAR collapsed
   `daily_summaries` to profile-day (v16); SleepLab keeps machine-local-date
   scope because multi-machine attribution matters for a self-hosted multi-user
   product.
7. **Capability/validation status is user-visible.** Unsupported cards may be
   detected but intentionally blocked, and conformance distinguishes
   planning-only, parse-observable, persisted-DB, and OSCAR-reference tiers.

## 7. What OSCAR's `event_lists` / `event_data` implies for SleepLab waveforms

OSCAR's BLOB model is the concrete reference SleepLab has been missing. Its
lessons:

1. **Metadata/data split.** `event_lists` (queryable metadata: channel, type,
   first/last time, count, rate, gain, offset, min/max, dimension, sizes) is
   separate from `event_data` (the opaque compressed payload). Waveform
   *metadata* can be queried without inflating the payload — SleepLab's
   `signal_channels` + a future segment-index table mirror this split.
2. **Segment, not sample, is the storage unit.** One row per EventList, not one
   row per sample. SleepLab's per-timestamp `session_waveform` is the opposite
   extreme and is precisely why full-night row storage is ~1–2 orders of
   magnitude larger (≈90k rows/h, ≈720k/8 h-night, ≈21.6 M/30-night card —
   `importer/waveform_estimate.py`).
3. **Compression is the enabler.** qCompress level 9 (~40–60%), applied only
   when it saves >10%, plus a CRC16 integrity checksum. Any future SleepLab
   segment store should carry an analogous compression + integrity scheme.
4. **SleepLab's contract is already segment-ready.** `WaveformSegment`
   (`importer/loaders/models.py`) already carries `channel_key`, `start_time`,
   `sample_rate_hz`, `sample_count`, `unit`, `source_file_id`, and a nullable
   `storage_ref` — i.e., the metadata an `event_lists`-style index needs and a
   handle to an out-of-row payload. The data model can describe compressed
   segments **before** any storage change is built.

The implication is **not** "store full-night samples." It is: *if/when* SleepLab
ever persists more than event windows, it should look like OSCAR's
metadata-row + compressed-segment-BLOB model, **not** like more
row-per-timestamp data.

## 8. Why SleepLab should keep Alpha 6's event-window decision for now

Alpha 6 deliberately decided: **persist high-rate ResMed BRP flow/pressure only
in event windows; full-night high-rate storage is deferred** behind a
deliberate retention / downsampling / query-performance / UI-API decision
(`docs/sleeplab_2_data_architecture.md` → "Waveform storage scope (Alpha 6
decision)"; row-count estimator pinned in `importer/waveform_estimate.py` /
`tests/test_waveform_estimate.py`). Nothing in the OSCAR archive overturns this:

- OSCAR's efficiency comes from **compression + the segment storage form**, not
  from storing samples row-per-timestamp. SleepLab's current full-night cost
  estimate assumes row-per-timestamp, so OSCAR does **not** make naïve full-night
  rows cheap.
- Adopting an OSCAR-like segment/BLOB store is a real storage-layer design
  change (schema, write path, query path, retention, backup impact, UI/API) and
  a **stop-and-ask** item. It is not an Alpha 7 default.
- Event-window storage remains correct and bounded (scales with event count, not
  night length) for Alpha 7 production.

**Keep event-window waveform storage as the Alpha 7 production behavior.**

## 9. Should SleepLab study an OSCAR-like compressed segment/BLOB model first?

**Yes — as a design investigation, not an implementation.** Before SleepLab ever
implements full-night waveform persistence, it should produce a written design
note studying an OSCAR-like **compressed waveform segment/BLOB** model:

- a metadata index row per segment (channel, start, rate, sample_count, gain,
  min/max, sizes) analogous to `event_lists`, reusing `WaveformSegment`;
- an out-of-row compressed payload analogous to `event_data`, with a
  compression method + integrity checksum;
- Postgres-native specifics OSCAR's SQLite model does not face: blob storage
  (`BYTEA`/large-object/TOAST) trade-offs, backup-size impact, streaming/range
  reads for the Event Inspector, retention/downsampling tiers, and multi-tenant
  isolation.

This investigation is itself an Alpha-7-or-later **document**, and full-night
waveform persistence stays blocked until it (and explicit approval) exist.

## 10. What OSCAR's `device_time_corrections` implies for SleepLab

Schema v17 adds `device_time_corrections` — a per-machine, date-ranged,
reversible, typed time-correction model:

- **Keyed to the machine**, `date_from`/`date_to` (open-ended allowed).
- **Typed**: `type IN ('timezone','travel','dst','reset','offset','drift')`.
- **Two correction forms**: a constant `offset_ms` (when slope `c1 == 0`), or a
  **linear drift model** `corrected = c0_ms + c1 · t` (when `c1 != 0`) for clock
  drift.
- **Reversible/auditable**: `applied_at` + `undone_at` (empty = active), with
  `markUndone` / `findActive` / `upsertTyped` in the repository — corrections are
  layered records, never destructive edits to timestamps.

Implications for SleepLab (web/Postgres, multi-machine, travel/DST-prone users):

1. SleepLab today carries only a `timezone_basis` string on `Session` and
   `Capabilities` (`importer/loaders/models.py`) — enough to *label* a basis,
   not to *correct* drift/offset/DST. OSCAR shows the richer target shape.
2. The right SleepLab analogue would be a **non-destructive, machine-scoped,
   date-ranged, typed, reversible correction record** layered over raw device
   timestamps — never mutating stored sample/event times in place. This fits
   SleepLab's audit-everything posture well.
3. The linear `c0 + c1·t` drift model and the discrete-offset/timezone/DST/reset
   distinction are a good vocabulary for a future SleepLab design.

**Action for Alpha 7: a design note only.** Capturing this is a documentation
task; **no migration, table, or production code** is created now. Time-correction
implementation is later-alpha/beta and stop-and-ask.

## 11. Change classification (Alpha 7 / later alpha / beta / post-2.0)

### Alpha 7 (docs, conformance depth, no production/schema change)
1. This OSCAR 2.0 DB architecture mapping review (done here).
2. An Alpha 7 checklist derived from it
   (`docs/sleeplab_2_alpha_7_checklist.md`).
3. Import-level conformance depth on existing ResMed fixtures that needs **no**
   routing/schema change: session-block interval boundaries, settings
   value/missing-vs-off semantics, event count/type/timestamp parity, OSCAR
   reference comparison, and weighted/time-based summaries where data exists.
4. Reframe future waveform storage from "full-night rows" to a "future
   compressed segment/BLOB design investigation."
5. A future design note for device time corrections (no migration).
6. Keep the first Lowenstein read-only conformance prep tied to a safe
   anonymized/synthetic fixture.

### Later alpha
1. Second independent anonymized ResMed fixture.
2. Lowenstein parser-backed read-only conformance passing consistently.
3. Lowenstein persistence only after fixture gates pass **and** explicit
   approval.
4. PRS1/DreamStation fixture-backed detection and identity.
5. Compressed waveform segment/BLOB **design note** (§9), still pre-implementation.
6. Device-time-correction **design note** maturing toward a proposal (§10).
7. Import cancellation/progress and worker-heartbeat recovery.

### Beta
1. Validate advertised families against real anonymized fixtures + OSCAR
   reference exports.
2. Native vs. parser-backed ResMed shadow comparison and the cutover decision.
3. Full-night waveform storage size/retention/backup validation **if** the
   segment design is approved.
4. Device-time-correction implementation **if** approved.
5. Freeze adapter contracts, channel names, settings keys, APIs, migration
   policy.

### Post-2.0
Broad long-tail device coverage; fixture donation/anonymization tooling; raw-card
archival + remote reprocessing; OSCAR-style user chart preferences;
population/cohort analytics and richer AI analysis.

## 12. Recommended Alpha 7 priorities

1. **Finalize this OSCAR 2.0 DB architecture mapping review.** (This document.)
2. **Add an Alpha 7 checklist** derived from it
   (`docs/sleeplab_2_alpha_7_checklist.md`).
3. **Deepen import-level conformance around existing ResMed fixtures**, in order:
   1. session-block interval boundaries (one-sample tolerance);
   2. settings values / missing-vs-off semantics;
   3. event count / type / timestamp parity;
   4. OSCAR reference comparison (extend beyond the export hash);
   5. weighted / time-based summaries where ResMed PLD data already exists.
4. **Reframe future waveform storage** from "full-night rows" to a "future
   compressed segment/BLOB design investigation" (§9) — keep event-window
   storage as the Alpha 7 production behavior (§8).
5. **Add a future design note for device time corrections** (§10) — **no
   migration**.
6. **Keep Lowenstein read-only conformance deferred** until a safe anonymized or
   synthetic fixture exists.
7. **Keep blocked until explicitly approved**: Lowenstein persistence, ResMed
   parser production cutover, full-night waveform storage, broad UI/API rewrites,
   and any DB migration.

## 13. Bottom line

The uploaded `OSCAR-code-master.tar.gz` confirms OSCAR 2.0 is now a single-file
SQLite, database-native CPAP application — which **validates SleepLab 2.0's
normalized-Postgres direction**. SleepLab already aligns on the architectural
fundamentals (durable machine identity, machine-scoped sessions, explicit
intervals, snapshot settings, channel stats, replaceable events, two-tier
provenance-stamped summaries) and is **intentionally richer** where it matters
for a web app (import-run/source-file audit, content-addressed provenance,
idempotent row identity, stricter privacy).

The two storage lessons worth carrying forward are concrete: OSCAR's
`event_lists`/`event_data` **compressed-segment** model is the right thing to
*study* before any full-night waveform persistence (while keeping Alpha 6's
event-window decision for production now), and OSCAR's v17
`device_time_corrections` is the right *vocabulary* for a future, non-destructive
timezone/DST/drift design. Both are **documentation/design** items for Alpha 7,
not implementation. Alpha 7 should otherwise turn this concrete OSCAR reference
into sharper import-level conformance coverage on the existing ResMed fixtures.
