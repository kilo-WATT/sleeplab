# Upgrading a SleepLab 1.x database to 2.0

SleepLab 2.0 is **preservation-first**: an in-place upgrade keeps your existing
sessions, events, summary metrics, notes/tags, devices, and old waveform data.
You should not need to wipe the database, and you should never do so before you
have a backup — some history lives only in the database and cannot be rebuilt
from the SD card.

This guide covers the supported upgrade paths, what is preserved, what stays
legacy-backed, when a reimport helps, the **upstream 1.4 → 2.0 bridge** that lets
Josh's 1.4.x users upgrade in place, and the upstream states that remain
**blocked on purpose**.

---

## 1. Back up first (always)

Before upgrading, take a full logical backup. This is the single most important
step and the only reliable way to recover database-only history.

```bash
# Replace with your real connection string / container.
pg_dump "$DATABASE_URL" > sleeplab-backup-$(date +%Y%m%d).sql

# Docker Compose example (service name "db", database "cpap"):
docker compose exec -T db pg_dump -U postgres cpap > sleeplab-backup-$(date +%Y%m%d).sql
```

> **Do not** run `docker compose down -v`, `docker volume rm`, or any "reset the
> database volume" step before you have this backup. That permanently deletes
> sessions, events, metrics, and waveforms that may exist **only** in the
> database and are not on your SD card.

---

## 2. Check readiness (recommended)

A read-only diagnostic reports your database state and a single recommendation.
It never writes to the database and never prints PHI, serials, or per-night
detail.

```bash
DATABASE_URL="$DATABASE_URL" python scripts/check_2x_upgrade_readiness.py
```

It prints, among other fields, a `RECOMMENDATION:` line that is one of:

| Recommendation | Meaning |
| --- | --- |
| `SAFE_TO_ATTEMPT_IN_PLACE` | History is recognized; start 2.0 normally. |
| `SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS` | Safe in-place, but your waveforms are still legacy row-backed; reimport from the SD card to enrich them (see §6). |
| `BRIDGEABLE_UPSTREAM_1_4_TO_2_0` | A clean upstream 1.4 database. The 1.4 → 2.0 bridge will reconcile it automatically on the next start; see §7. |
| `BRIDGED_UPSTREAM_1_4_TO_2_0` | An upstream 1.4 database that has already been bridged and is now on the 2.0 line; see §7. |
| `BLOCKED_UNSUPPORTED_1_4_STATE` | A partial or mixed upstream 1.4 history the bridge cannot safely handle; blocked before any write; see §7–§8. |
| `MANUAL_REVIEW_REQUIRED` | An unrecognized migration history was found; ask before upgrading. |

---

## 3. Safest preservation path

1. Take the `pg_dump` backup (§1).
2. Run the readiness check (§2).
3. If the recommendation is `SAFE_TO_ATTEMPT_IN_PLACE`,
   `SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS`, or
   `BRIDGEABLE_UPSTREAM_1_4_TO_2_0`, start SleepLab 2.0 against the **same**
   database. Migrations apply automatically and additively; an upstream 1.4
   database is bridged first (see §7).
4. Verify your old nights still load, then (optionally) reimport from the SD card
   to enrich waveforms (§6).

Keep the backup until you have confirmed everything you care about is present.
A fresh database + reimport is an *optional* convenience, never the only
preservation path — the in-place upgrade (including the 1.4 bridge) keeps your
database-only history.

---

## 4. In-place upgrade path (known-safe databases)

For fresh installs, existing 2.0 beta databases, and known-safe 1.3.x-style
databases, no special steps are required — just start 2.0:

```bash
docker compose up -d        # or your usual start command
```

On startup the migration runner:

- creates `schema_migrations` if missing and adopts an existing pre-tracking
  schema by marking the baseline migrations as already applied;
- ensures a durable `schema_compatibility` table that records any 1.x → 2.x
  bridging applied to the database;
- runs an **upgrade safety guard** *before* applying any 2.0 migration. A clean
  upstream 1.4 database is reconciled by the **bridge** (see §7); a partial/mixed
  1.4 state is blocked before any write;
- applies migrations `022`–`032` additively (new tables and columns; backfill of
  legacy sessions into the new CPAP data model). No existing session, event,
  metric, note, tag, or waveform row is deleted.

---

## 5. What old data is preserved

| Data | Status on in-place upgrade |
| --- | --- |
| Sessions | **Preserved & migrated forward** — kept as-is and backfilled with `machine_id`, `source_session_key`, and a `provenance_status` of `legacy_backfilled` (or `legacy_invalid_duration` for negative durations). |
| Events (`session_events`) | **Preserved & migrated forward** — kept; tagged with `adapter_id = 'legacy-session-v1'` and a `source_event_key`. *(Migration 020 removes exact duplicate events, keeping one copy of each.)* |
| Summary metrics (`session_metrics`) | **Preserved as-is.** |
| Session notes / tags | **Preserved as-is.** |
| Machines / devices | **Migrated forward** — a `cpap_machines` row is synthesized per distinct legacy serial and linked to sessions. |
| Old waveforms (`session_waveform`) | **Legacy-readable only** — kept untouched and still served (see §6); not converted to `waveform_chunks`. |
| Import settings / history (`import_runs`, import settings) | **Preserved as-is** — older `import_runs` rows render with newer fields reported as `null`. |

Nothing in the upgrade path drops, truncates, vacuums, or rewrites your old
`session_waveform` data.

---

## 6. Waveforms: legacy-backed vs chunk-backed, and when reimport helps

SleepLab 2.0 stores newly parsed waveforms in `waveform_chunks` (compressed,
full-night capable). Databases imported by 1.x have row-backed `session_waveform`
data instead.

- **Old row-backed data keeps working.** The Event Inspector reads
  `waveform_chunks` when present and **falls back to `session_waveform`**
  otherwise, so legacy nights still show event waveforms. Coverage labels report
  the source explicitly as `chunks`, `rows`, or `none`.
- **New imports use chunks.** Any import you run under 2.0 writes
  `waveform_chunks`.
- **The two coexist safely.** A session can hold both legacy rows and new chunks;
  they are stored and read independently.
- **Reimport adds chunk-backed data, for nights still on the card.** Reimporting
  from the SD card writes `waveform_chunks` (and full-night flow) for those nights.

### Known limitation: reimport does not merge into a legacy/bridged night

A parser reimport resolves its device as a **new** `cpap_machines` row
(`resmed-native-v2:serial:…`), which is distinct from the synthetic
`legacy-session-v1` machine the 1.x backfill (and the 1.4 bridge) records. Because
sessions dedupe on `(machine_id, source_session_key)`, a reimported night lands on
a **new parser session under that new machine** rather than enriching the existing
`legacy_backfilled` session in place. Both are preserved — nothing is deleted or
destructively duplicated, the parser write is idempotent on its own machine, and
legacy row-backed and parser chunk-backed waveforms coexist — but the night may
appear under **two machines** (legacy + parser) until a future merge step
reconciles them. If you want a single clean parser-owned history for a device,
reimport the nights that are still on the card; the legacy sessions remain
available for the older nights that are not. This behavior is locked in by
`tests/test_bridge_reimport_enrichment.py`.

### Why reimport alone is not enough

Reimport can only recover what is **still on the SD card**. Nights that were
imported long ago and have since rolled off the card exist **only in the
database**. A "fresh database + reimport" upgrade would silently lose that
history. That is why the in-place, preserve-the-database path is the default and
why the backup in §1 matters.

---

## 7. The upstream 1.4 → 2.0 bridge

The upstream 1.4.x line (`joshuamyers-dev/main`) and the SleepLab 2.0 line share
an identical migration baseline through `021_add_session_manufacturer`, then
**reuse the same migration numbers for different changes**:

- upstream 1.4: `022_add_adherence_settings`, `023_add_adherence_enabled`
  (these only add nullable `adherence_*` columns to `user_import_settings`)
- SleepLab 2.0: `022_add_session_leak_semantics` … `032_add_import_result_summary`
  (leak semantics, the CPAP data foundation, `waveform_chunks`, and more)

Migrations are tracked by filename, so the two 022/023 entries collide *by name*.
But the actual schema changes are **orthogonal**: upstream 1.4 only touches
adherence columns on `user_import_settings`, while 2.0 only adds new tables and
columns elsewhere. SleepLab 2.0's own adherence analytics use a fixed policy and
never read those upstream columns, so they are simply preserved.

### How the bridge works

When SleepLab 2.0 starts against a **clean upstream 1.4 database** — both
adherence migrations recorded, the matching adherence columns actually present,
and nothing else divergent — it:

1. **Verifies the live schema** matches the expected upstream 1.4 shape (baseline
   tables present, `user_import_settings` adherence columns present). If it does
   not, it stops before writing (see below).
2. **Records a bridge marker** in the durable `schema_compatibility` table,
   documenting the upstream migrations it recognized and the adherence columns it
   preserved. It does **not** fake any 2.0 migration it has not run.
3. **Continues the normal 2.0 migration path**, applying `022`–`032` additively on
   top. Your upstream 1.4 adherence settings/toggles, sessions, events, metrics,
   notes/tags, machines, import history, and old `session_waveform` rows are all
   preserved.

The readiness script reports `BRIDGEABLE_UPSTREAM_1_4_TO_2_0` before the bridge
runs and `BRIDGED_UPSTREAM_1_4_TO_2_0` afterward. The bridge is idempotent — once
recorded, later starts skip it and simply continue.

After bridging, your waveforms are still legacy row-backed (`session_waveform`).
They remain fully readable; reimport from the SD card (see §6) optionally enriches
them with `waveform_chunks`.

### Blocked: unsupported 1.4 states

The bridge only handles a *clean* upstream 1.4 database. It refuses, **before any
write**, when the history is:

- **partial** — only one of the two adherence migrations recorded; or
- **mixed** — the adherence migrations plus 2.0-divergent or unrecognized
  migrations, with no recorded bridge; or
- **schema-mismatched** — the adherence migrations are recorded but the expected
  adherence columns are not actually present.

These surface as `BLOCKED_UNSUPPORTED_1_4_STATE`, and startup stops with a clear
message naming what was found. **No data is modified.**

---

## 8. Recovering if startup is blocked

If 2.0 stops at startup with a `BLOCKED_UNSUPPORTED_1_4_STATE` message:

- **Nothing was changed** — the guard and the bridge both run before any migration
  is applied, so your database is exactly as it was.
- Make sure you have the `pg_dump` backup from §1.
- Run the readiness check for a summary you can share:

  ```bash
  DATABASE_URL="$DATABASE_URL" python scripts/check_2x_upgrade_readiness.py --verbose
  ```

- Do **not** hand-edit `schema_migrations` or `schema_compatibility` to bypass the
  guard.
- Stay on your current SleepLab version and open an issue with the readiness
  output so a supported migration can be planned. Re-pointing 2.0 at a **fresh**
  database will start cleanly, but **do not delete your old volume** — keep it
  (and the backup) so the historical data can be migrated later.

---

## 9. Quick reference

```bash
# 1. Back up (do this first, always)
pg_dump "$DATABASE_URL" > sleeplab-backup.sql

# 2. Check readiness (read-only, no PHI)
DATABASE_URL="$DATABASE_URL" python scripts/check_2x_upgrade_readiness.py

# 3. Start 2.0 in place if SAFE_*; migrations apply additively
docker compose up -d
```

Never reset or delete the database volume before the backup completes.
