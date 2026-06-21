# Upgrading a SleepLab 1.x database to 2.0

SleepLab 2.0 is **preservation-first**: an in-place upgrade keeps your existing
sessions, events, summary metrics, notes/tags, devices, and old waveform data.
You should not need to wipe the database, and you should never do so before you
have a backup — some history lives only in the database and cannot be rebuilt
from the SD card.

This guide covers the supported upgrade paths, what is preserved, what stays
legacy-backed, when a reimport helps, and the one upstream history that is
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
| `BLOCKED_CONFLICTING_1_4_MIGRATIONS` | An upstream 1.4 history was detected that collides with 2.0; see §7. |
| `MANUAL_REVIEW_REQUIRED` | An unrecognized migration history was found; ask before upgrading. |

---

## 3. Safest preservation path

1. Take the `pg_dump` backup (§1).
2. Run the readiness check (§2).
3. If the recommendation is `SAFE_TO_ATTEMPT_IN_PLACE` or
   `SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS`, start SleepLab 2.0 against the
   **same** database. Migrations apply automatically and additively.
4. Verify your old nights still load, then (optionally) reimport from the SD card
   to enrich waveforms (§6).

Keep the backup until you have confirmed everything you care about is present.

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
- runs an **upgrade safety guard** *before* applying any 2.0 migration (see §7);
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
- **Reimport enriches, but only for nights still on the card.** Reimporting from
  the SD card adds chunk-backed waveforms (and full-night flow) for those nights.

### Why reimport alone is not enough

Reimport can only recover what is **still on the SD card**. Nights that were
imported long ago and have since rolled off the card exist **only in the
database**. A "fresh database + reimport" upgrade would silently lose that
history. That is why the in-place, preserve-the-database path is the default and
why the backup in §1 matters.

---

## 7. Blocked: conflicting upstream 1.4 history

The upstream 1.4.x line (`joshuamyers-dev/main`) and the SleepLab 2.0 line share
an identical migration baseline through `021_add_session_manufacturer`, then
**reuse the same migration numbers for different changes**:

- upstream 1.4: `022_add_adherence_settings`, `023_add_adherence_enabled`
- SleepLab 2.0: `022_add_session_leak_semantics` … `032_add_import_result_summary`

Because migrations are tracked by filename, a database that already recorded the
upstream 022/023 files would otherwise have 2.0's own 022/023+ layered on top of
a schema the runner never modeled — an unrecoverable mixup.

To prevent this, **SleepLab 2.0 refuses to start** when it detects those upstream
1.4 migrations and stops *before changing anything*. You will see a message
naming the conflicting migrations and pointing here. **No data is modified.**

This is the `BLOCKED_CONFLICTING_1_4_MIGRATIONS` state. Direct upgrade from
upstream 1.4 is **not supported** in this release. If you are in this state:

1. Take a backup (§1) if you have not already.
2. Do **not** attempt to hand-edit `schema_migrations` to bypass the guard.
3. Open an issue with the readiness output (run with `--verbose` to include the
   conflicting migration names) so a supported migration can be planned.

---

## 8. Recovering if startup is blocked

If 2.0 stops at startup with the conflict message:

- **Nothing was changed** — the guard runs before any migration is applied, so
  your database is exactly as it was.
- Make sure you have the `pg_dump` backup from §1.
- Run the readiness check for a summary you can share:

  ```bash
  DATABASE_URL="$DATABASE_URL" python scripts/check_2x_upgrade_readiness.py --verbose
  ```

- Stay on your current SleepLab version until a supported path is available.
  Re-pointing 2.0 at a **fresh** database will start cleanly, but **do not delete
  your old volume** — keep it (and the backup) so the historical data can be
  migrated later.

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
