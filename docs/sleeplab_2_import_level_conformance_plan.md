# SleepLab 2.0 Import-Level Conformance Plan

Status: **Steps 1–2 (parse-observable), the OSCAR reference-hash check, and
Step 4 (DB identity hashes) implemented; OSCAR numeric parity still
design-only.** The `validate_import` entry point, `ImportConformanceResult`, the
parse-observable comparators for `warnings`, `session_blocks`,
`therapy_aggregates`, and `settings` (count/presence), the `oscar_reference`
export-hash verification, and the DB-gated `identity_hashes` checks (persisted
session/block key-set hashes + counts, with duplicate/incremental stability
proven by DB-gated tests) are built and tested. Remaining: OSCAR **numeric
parity** (Step 3b) and settings-value / interval-boundary comparison, which need
loader/parser support.

This document expands the design note in
`docs/sleeplab_2_alpha_6_checklist.md` §6 ("Import-level conformance path") into
a concrete API, manifest, and gating design. It is the next major Alpha 6 item.
No DB-backed conformance runner is built yet; the scaffold defines the contract
so each subsequent step stays small and reviewable.

It complements — does **not** replace — the planning-only harness in
`importer/conformance.py` (`validate_fixture`). That harness stays exactly as it
is: it runs `create_import_plan` and compares file-derived inspection results
against a checked-in `manifest.json`. The import-level path is a *second*,
separately-gated entry point.

## 1. Why a second entry point is needed

The current harness is **planning/inspection-only**. `validate_fixture` runs
`importer.loaders.create_import_plan`, whose `CoverageSummary` is derived from
file inventory and directory structure (`first_date`, `last_date`,
`therapy_days`, `estimated_session_blocks`, `waveform_files`, `event_files`,
`oximetry_files`, `settings_files`). It never decodes an EDF payload and never
writes a database row.

That boundary is pinned by
`tests/test_conformance.py::test_conformance_coverage_cannot_observe_therapy_aggregates`,
which asserts that parsed/aggregate semantics (usage, wall-clock span, gap,
mask-on intervals, therapy mode) are **not** observable fields.

So the following checklist §5 items marked **(import-level)** cannot be honestly
validated today, and must not be faked with file-count proxies:

- persisted **settings** *values* (mode, pressures, EPR, ramp, humidification,
  mask) with "missing ≠ off";
- **interval boundaries** (session-block start/end, mask-on/off);
- **therapy aggregates** (usage / wall-clock span / gap per machine-local date);
- **duplicate-import** stable identity hashes (persisted UUID sets unchanged on
  re-import);
- **incremental nights** (a newer night leaves existing identities unchanged);
- **OSCAR parity** as a first-class, hash-pinned reference.

These require a real parse (for values/boundaries/aggregates) and a real persist
(for identity hashes). That is the import-level conformance path.

## 2. Entry point

### Name

```python
validate_import(fixture_dir, *, conn=None) -> ImportConformanceResult
```

`validate_import` is the chosen name (the brief's example). It mirrors the
existing `validate_fixture` so the two read as siblings: same module
(`importer/conformance.py`), same `Path`-or-str fixture argument, same
"compare normalized output against the manifest" shape, same dataclass result
style (`ConformanceResult` → `ImportConformanceResult`).

It is **separate** from `validate_fixture` on purpose:

- `validate_fixture` stays dependency-free (no `cpap-parser`, no Postgres) so the
  default CI suite is always green and fast.
- `validate_import` is allowed to require heavier, optional dependencies, and
  degrades to *skips*, never hard failures, when they are absent (§9).

### What it does, in order

1. Read `manifest.json`; if there is no `expected.import` block, return a result
   with `skipped=("no expected.import block",)` and `passed=True` — fully
   backward compatible (the same pattern as the optional `expected.diagnostics`
   block in `validate_fixture`).
2. Acquire the normalized `ImportRun`. An explicitly injected `run=` is used
   as-is (this is how tests exercise the comparison logic with no parser/DB);
   otherwise the loader's `import_data_with_directory(detected, options)` is run
   on the fixture source. Parsing needs `cpap-parser` / `cpap-py`; when absent
   (or a parse raises) the parse-dependent sub-checks skip with the acquisition
   reason rather than crashing.
3. Compare the **pre-persistence** `ImportRun` against the parse-only blocks
   (`settings`, `blocks`, `aggregates`, `warnings`). No database needed.
4. If `conn` is provided, persist via `persist_import_run` into a throwaway
   transaction, then compare **post-persistence** database state
   (`identity_hashes`, and the duplicate/incremental idempotency checks). Roll
   the transaction back at the end — `validate_import` is read-only with respect
   to durable state.
5. If an `oscar_reference` block is present, compare normalized output against
   the checked-in OSCAR export and assert the reference's version/hash matches
   the manifest.

### Result shape

```python
@dataclass(frozen=True)
class ImportConformanceResult:
    fixture_id: str
    passed: bool
    failures: tuple[str, ...]   # real discrepancies — a non-empty tuple fails
    skipped: tuple[str, ...]    # gated-out checks, each with a human reason
```

A *skip* is never a *failure*. A run with `failures == ()` is `passed=True` even
if every check was skipped (e.g. no `cpap-parser` installed). The CLI/test layer
surfaces `skipped` reasons so a green-but-skipped run is visibly distinct from a
green-and-checked one.

## 3. What it compares (three-to-four layers)

Per the loader-and-conformance plan's "Conformance compares three layers", with
a fourth optional cross-check:

| Layer | Source | Needs |
|---|---|---|
| Normalized **pre-persistence** | `ImportRun` from `import_data_with_directory` | `cpap-parser` |
| Persisted **post-import** DB state | rows written by `persist_import_run` | `cpap-parser` + Postgres |
| Optional **OSCAR reference** | checked-in `oscar_reference/*.csv` + manifest hash | reference files only |
| Optional **cpap-parser raw** | `CPAPDirectory` before normalization | `cpap-parser` |

The fourth (raw `cpap-parser` output) is where SleepLab's normalization is
distinguished from the upstream parser — useful for catching a regression where
the loader, not the parser, drops or mislabels a value. It is optional and only
populated where a manifest cares.

## 4. Manifest: the `expected.import` block

A single new **optional** top-level block under `expected`, mirroring
`expected.diagnostics`. Fixtures without it are unaffected (backward compatible).

```jsonc
"expected": {
  "import": {
    "settings": {
      "2026-06-01": {
        "therapy_mode": "apap",
        "minimum_pressure_cm_h2o": 4.0,
        "maximum_pressure_cm_h2o": 15.0,
        "ramp_mode": "auto",
        "mask_type": "nasal",
        "epr_level": null            // present-and-null = "missing", NOT off/0
      }
    },
    "session_blocks": {
      "2026-06-01": {
        "block_count": 2,
        "intervals": [
          {"start": "2026-06-01T22:00:00", "end": "2026-06-01T23:00:00"},
          {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"}
        ]
      }
    },
    "therapy_aggregates": {
      "2026-06-01": {
        "usage_seconds": 8100,
        "wall_clock_seconds": 9000,
        "gap_seconds": 900,
        "block_count": 2
      }
    },
    "warnings": {
      "codes": ["resmed_summary_only_day", "resmed_waveform_absent"]
    },
    "identity_hashes": {
      "algorithm": "sha256",
      "machine": "…",               // hash of stable identity tuple, see §11
      "sessions": "…",              // hash of sorted source_session_key set
      "blocks": "…",
      "incremental_night": {
        "add_date": "2026-06-02",
        "unchanged": ["machine", "sessions", "blocks"]
      }
    },
    "oscar_reference": {
      "oscar_version": "1.5.1",
      "oscar_commit": "…",
      "export_hash": "sha256:…",
      "summary_csv": "oscar_reference/summary.csv"
    }
  }
}
```

Every sub-block is independently optional. A manifest may assert only
`therapy_aggregates`, only `settings`, etc. Each present sub-block runs only the
checks it can (gating in §4–§8). Naming follows the brief's
`expected.import.{settings,session_blocks,therapy_aggregates,warnings,identity_hashes,oscar_reference}`.

#### Implemented vs deferred sub-keys (current state)

The shape above is the full target. What `validate_import` actually checks today
is a subset; everything else is surfaced as a *skip*, never a silent pass:

| Sub-block | Checked now | Deferred (→ skip) |
|---|---|---|
| `warnings` | `codes` (present), `absent` (forbidden) | any other key |
| `session_blocks` | `block_count` per date | `intervals` / boundary start-end |
| `therapy_aggregates` | `usage_seconds`, `wall_clock_seconds`, `gap_seconds` (= wall-clock − usage), `block_count` | any non-observable field, or a field whose source `DerivedValue` is absent |
| `settings` | `snapshot_count`, `present` | per-setting *value* keys (loader maps no settings snapshots yet) |
| `identity_hashes` | `sessions`/`blocks` key-set hashes, `session_count`/`block_count` (needs `conn` + `machine_id`) | `machine`, `incremental_night` sub-keys |
| `oscar_reference` | `export_hash` of the reference file | numeric `parity` vs OSCAR rows (Step 3b) |

A requested date absent from the normalized run is a **failure** (not a skip):
the run claims data the manifest expects and none was produced.

### Tolerances

Boundaries (`session_blocks.intervals`) compare to within **one source sample
interval** unless the format reports coarser resolution, matching the loader
plan's "Session boundaries" default tolerance. `therapy_aggregates` seconds are
exact (they are integer second counts the native path already produces — see the
`nightly_therapy_aggregates` assertion in
`tests/test_resmed_import_regressions.py`, which expects `(8100, 9000, 900, 2)`).
Settings values compare exactly; **missing is asserted as `null`/absent, never a
fabricated `0`/`off`**.

## 5. Which checks run without Postgres

Parse-only, no database — these compare the **pre-persistence** `ImportRun`
(implemented in Step 2 unless noted):

- `settings` — **[implemented: count/presence]** `snapshot_count` and `present`
  from `Session.settings`. Per-setting *value* comparison (with missing-≠-off) is
  deferred because the ResMed cpap-parser loader maps no `SettingsSnapshot`s yet;
  a value key is skipped, not faked.
- `session_blocks` — **[implemented: block_count]** summed `len(Session.blocks)`
  per machine-local date. `intervals` start/end comparison
  (`SessionBlock.start_time`/`end_time`, with the one-sample tolerance below) is
  deferred and skipped.
- `therapy_aggregates` — **[implemented]** `usage_seconds`
  (`computed_usage_hours`×3600), `wall_clock_seconds` (`recording_span_hours`×
  3600), `gap_seconds` (= wall-clock − usage), and `block_count`, all derived
  from the normalized `Session` (the loader's usage `DerivedValue`s and block
  list) rather than from `nightly_therapy_aggregates`. A field whose source
  derived value is absent is skipped, never defaulted to zero.
- `warnings` — **[implemented]** `codes` (each must be surfaced) and `absent`
  (each must not be). Codes are read from run-level **and** session-level
  `ImportWarning`s, so the *import-time* codes `resmed_summary_only_day` /
  `resmed_waveform_absent` become observable here — they are invisible to the
  planning-only harness, which is why `validate_fixture` sees only
  *detection/planning* diagnostics.
- `oscar_reference` — **[deferred, Step 3]** comparison against checked-in CSVs
  and the manifest hash assertion. No DB; needs only the reference files (and a
  normalized side).

These comparators are exercised today by **injecting a normalized `ImportRun`**
(`validate_import(fixture, run=...)`), so the comparison logic is unit-tested
with no parser and no Postgres. The auto-parse acquisition path additionally
needs `cpap-parser`/`cpap-py` (§6); both forms are Postgres-free.

## 6. Which checks require cpap-py / cpap-parser

**All of `validate_import`'s payload checks** require a parse, so all of
§5 and §7 depend on `cpap-parser` + its `cpap-py` EDF backend being installed.
The dependency is gated exactly like the existing conformance suite:

```python
pytest.importorskip("cpap_parser", reason="cpap-parser not installed; see requirements.txt pin")
pytest.importorskip("cpap_py", reason="cpap-py EDF backend not installed")
```

(mirroring `tests/conformance/test_resmed_airsense10.py` and
`tests/conformance/conftest.py`). When absent, `validate_import` records a skip
reason and returns `passed=True` with the parse-dependent checks listed in
`skipped`. The manifest-metadata-only checks (`validate_manifest_metadata`) and
`validate_fixture` remain fully runnable without the parser, unchanged.

## 7. Which checks require Postgres

The **post-persistence** checks, because they read rows written by
`persist_import_run`:

- `identity_hashes.{machine,sessions,blocks}` — hash the persisted stable
  identity sets after one import.
- `identity_hashes` **duplicate** check — import twice, assert the hashes are
  unchanged (no new machine/session/block rows; stable `source_session_key`s).
  This reuses the idempotency already proven by
  `tests/test_resmed_import_regressions.py::test_upsert_session_reimport_is_idempotent`
  and `…::test_resmed_str_persistence_is_duplicate_safe_and_incremental`.
- `identity_hashes.incremental_night` — import night A, snapshot hashes, import
  night A+B, assert A's identities are unchanged and only B's are new.

Postgres is gated through the existing `db` / `test_user` fixtures
(`tests/conftest.py`), which `pytest.skip` when no `TEST_DATABASE_URL` is
configured. `validate_import(conn=None)` simply skips all DB checks; the
`conn`-bearing form is only exercised by a `db`-gated test. The transaction is
always rolled back — conformance never commits durable rows.

## 8. Which checks require private / restricted fixtures

**None are required.** The design is explicitly satisfiable with committed
synthetic fixtures (§9 reporting makes the gaps visible rather than failing).

- Restricted fixtures (a real anonymized AirSense card) give *stronger* evidence
  for `oscar_reference` parity and realistic multi-night `identity_hashes`, but
  they are **optional** and live as manifest-only entries retrieved by hash in an
  authorized job (per the data-architecture "Conformance fixtures" section).
- The committed `tests/conformance/fixtures/resmed_airsense10_001/` anonymized
  fixture (serial replaced, timestamps shifted) already carries an
  `oscar_reference/` and is redistributable; it can back the `oscar_reference`
  and aggregate checks without any private data.

No PHI, real serials, or raw cards are ever committed — see §11.

## 9. Which checks use synthetic committed fixtures, and how skips report

The smallest first target is the **synthetic** fixture
`fixtures/conformance/synthetic-resmed-minimal/`. It has one therapy day, an
`STR.edf`, one PLD, and event files — enough to assert a minimal
`therapy_aggregates`/`session_blocks`/`settings` block once the parse runs.

Skip reporting rules (so a gated check never masquerades as a pass **or** a
fail):

- No `expected.import` block → `skipped=("no expected.import block",)`,
  `passed=True`.
- `cpap-parser`/`cpap-py` absent → each parse-dependent sub-check appended to
  `skipped` with the dependency reason; `passed` reflects only real failures
  (none, if nothing could be checked).
- `conn is None` → DB sub-checks appended to `skipped` with
  `"no database connection"`.
- `oscar_reference` present but reference file missing/hash-mismatched → this is
  a **failure**, not a skip (a declared reference that cannot be verified is a
  real problem).

The CLI prints `{fixture_id, passed, failures, skipped}` so a reviewer sees
`passed: true, skipped: [...]` distinctly from `passed: true, skipped: []`.

## 10. Duplicate and incremental import checks

Both are DB-gated (`conn` required) and reuse the existing idempotency machinery:

**Duplicate:** persist the same `ImportRun` twice in one rolled-back
transaction. Assert:

- machine/session/block row counts are identical after the second persist;
- the set of persisted `source_session_key`s (and their derived identity hashes)
  is byte-for-byte unchanged;
- summary values are *updated in place*, not duplicated (the dedup key is the
  partial unique index `uq_sessions_machine_source_key` on
  `(machine_id, source_session_key)`).

This is the persisted-UUID-stability property the data-architecture doc calls
"Duplicate verification compares stable persisted UUID sets".

**Incremental:** persist night A, snapshot the identity hashes; then persist a
superset (A + a newer night B). Assert A's machine/session/block identities are
unchanged and only B's rows are added. The manifest's
`identity_hashes.incremental_night.add_date` names B; `unchanged` lists which
identity sets must match the pre-B snapshot.

For synthetic fixtures, "night B" can be a second committed synthetic day or a
test-constructed `ImportRun` — no private/expanded card is needed.

## 11. How expected hashes avoid exposing PHI or serials

The manifest stores **hashes of stable keys**, never the keys themselves, and
the keys are already pseudonymized before they reach a hash:

- **Serials** are never hashed raw. Identity hashing uses the *fixture
  pseudonym* (`MachineIdentity.serial_number` after anonymization — e.g.
  `SN-FIXTURE-AirSense10-001`), so the hash is reproducible from the committed
  fixture and reveals nothing about a real device.
- **Session/block identity** hashes are computed over the normalized
  `source_session_key`s (`resmed:{machine_key}:{date}`) — derived, non-PHI
  strings — sorted and joined, then `sha256`. A hash is one-way; even the
  pseudonymous inputs are not recoverable from the manifest.
- **Timestamps** in a restricted fixture are already shifted by the anonymizer
  (`anonymization.timestamp_shift_days`), so any date that enters a hash is on
  the shifted calendar, not a real therapy date.
- The manifest records `identity_hashes.algorithm` so a hash scheme change is
  explicit and versioned.
- `validate_manifest_metadata` already enforces `anonymization.reviewed: true`
  and a redistribution policy; the import-level block adds no new PHI surface —
  it only adds *hashes of already-anonymized derived keys*.

Net: a committed manifest contains stable hashes + pseudonymous expected values,
and committing it leaks no serial, no patient identifier, and no real date.

## 12. Smallest first implementation step

The first code step (now **landed** — step 1 below) was the dependency-safe
scaffold: add `ImportConformanceResult` and `validate_import`, with **no** parse
and **no** DB access. It returns `passed=True` and records clear `skipped`
reasons for every requested sub-block, so it is inert for existing fixtures and
cannot crash where `cpap-parser`/Postgres are absent. Tests pin: the entry point
is importable; an absent `expected.import` block passes-and-skips; a present
block skips with clear reasons; an unknown sub-block is surfaced; and
`validate_fixture` is unchanged. Step 2 (now landed) added the parse-observable
comparators, exercised by injecting a normalized `ImportRun`; the next step
(step 3) adds the OSCAR-reference comparison.

## Implementation sequence

Each step is a small PR. **Steps 0–2, the Step 3 OSCAR hash check, and the
DB-gated Steps 4–5 are landed.** Remaining: Step 3b (OSCAR numeric parity),
loader-dependent settings-value / interval-boundary comparison, and the Step 6
CLI wiring.

0. **[DONE] Design.** This document; checklist §6 points here. (The original
   boundary test asserting `validate_import` did not exist was replaced when
   step 1 landed.)
1. **[DONE] `ImportConformanceResult` + `validate_import` scaffold.** Returns
   `passed=True` with everything in `skipped` when no `expected.import` block /
   no parser / no `conn`. Parse-free, DB-free. Implemented in
   `importer/conformance.py`; the result type is `(fixture_id, passed, failures,
   skipped)`. Recognized sub-blocks are gated and skipped with a clear reason
   (`cpap-parser/cpap-py not installed`, `no database connection`, or
   `not implemented yet`); an unrecognized sub-block is surfaced as a skip too.
   Covered by `tests/test_conformance.py` (importable, absent-block pass/skip,
   present-block clear skips, unknown-block visibility, and `validate_fixture`
   backward-compat).
2. **[DONE] Parse-observable checks.** `warnings` (codes/absent),
   `session_blocks` (block_count), `therapy_aggregates`
   (usage/wall-clock/gap/block_count), and `settings` (snapshot_count/present)
   against the normalized `ImportRun`. Implemented as pure comparators dispatched
   from `_IMPORT_BLOCK_COMPARATORS`; each returns `(failures, skips)`. Unit-tested
   in `tests/test_conformance.py` by **injecting** an `ImportRun` (`run=`), so the
   logic runs with no parser and no DB; deferred sub-keys (interval boundaries,
   settings values) and unobservable/absent fields are skipped, not faked, and a
   missing requested date is a real failure. The auto-parse acquisition path is
   `cpap-parser`/`cpap-py`-gated.
3. **[DONE: hash; parity deferred] OSCAR reference check.** `oscar_reference`
   export-hash verification is implemented (`_compare_oscar_reference`): a
   declared reference file whose sha256 mismatches (or is missing) is a failure.
   Numeric CSV parity vs the parsed run (Step 3b) is still deferred and skipped.
4. **[DONE] Identity-hash checks (DB).** `identity_hashes` for a persisted
   machine via the read-only `persisted_identity_snapshot(conn, machine_id)`
   primitive (`sessions`/`blocks` key-set hashes + counts), gated on `conn` +
   `machine_id`; `db`/`test_user` tests roll back.
5. **[DONE] Duplicate + incremental checks (DB).** DB-gated tests in
   `tests/test_conformance.py` assert duplicate-import snapshot stability and
   incremental-night non-mutation (first night's id/keys unchanged, only the new
   night added), reusing the idempotency persistence pattern.
6. **Wire into the conformance CLI** (`python -m importer.conformance --import …`
   or a sibling subcommand) and document in the data-architecture "Conformance
   fixtures" section.

Acceptance: an adapter may claim a capability `validated` only when its
fixture's `expected.import` checks pass *and* its `oscar_reference` is present
and hash-verified — tying this path to the loader plan's "Acceptance gates" and
the roadmap's "before a capability may claim `validated`".

## Cross-references

- `docs/sleeplab_2_alpha_6_checklist.md` §5 (manifest expansion, import-level
  items) and §6 (the design note this expands).
- `docs/sleeplab_2_loader_and_conformance_plan.md` — "Conformance testing
  strategy" (layers, session boundaries, settings, duplicate imports, acceptance
  gates) and the `ImportRun`/`Session`/`SettingsSnapshot` contract.
- `docs/sleeplab_2_data_architecture.md` — "Conformance fixtures" (manifest
  fields, restricted-fixture handling) and "Next milestone".
- `importer/conformance.py` — the planning-only `validate_fixture` this sits
  beside.
- `importer/loaders/resmed_native.py` (`import_data_with_directory`) and
  `importer/loaders/persist.py` (`persist_import_run`) — the parse and persist
  entry points `validate_import` drives.
- `tests/test_resmed_import_regressions.py` — the idempotency/aggregate tests
  whose patterns the DB-gated checks reuse.
