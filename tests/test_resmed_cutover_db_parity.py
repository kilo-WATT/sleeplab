"""ResMed legacy-vs-cpap-parser DB parity harness (first version).

Two tiers:

* **Classification unit tests** — pure, DB-free, parser-free. They prove the
  ``cutover_parity.classify_parity`` verdict logic for every category. These run
  in the normal suite.
* **The gated DB harness** (``test_db_parity_harness``) — imports the committed
  AirSense 10 fixture through *both* the legacy ``import_sessions`` path and the
  cpap-parser ``persist_import_run`` path into the **same rolled-back test
  transaction under separate machine ids**, snapshots the parity tables for each,
  and classifies the differences. It needs a test database (the ``db`` fixture
  skips without ``TEST_DATABASE_URL``); the parser half additionally needs the
  ``cpap-py`` backend (skipped cleanly when absent — then the report is
  legacy-only). It asserts the harness *runs and classifies*, not that parity
  holds — the documented differences (oximetry, settings, granularity, …) are
  expected to show up, and the test proves the harness *sees* them rather than
  hiding them.

Safety: everything runs inside the ``db`` fixture's transaction, which is rolled
back on teardown — no production database is touched and no row survives the test.
The legacy path's internal ``commit()``/``rollback()`` are swallowed by a thin
proxy so its writes stay inside that transaction. Snapshots are aggregate-only
(counts + category labels) — no serials, timestamps, paths, or ids.
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg2.extras
import pytest

from importer.loaders.planning import _source_role
from tests import cutover_parity as cp

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO_ROOT / "tests" / "conformance" / "fixtures" / "resmed_airsense10_001"
_CPAP_PARSER_ADAPTER = "resmed-cpap-parser-v1"
_LEGACY_ADAPTER = "resmed-native-v2"


# ---------------------------------------------------------------------------
# Classification unit tests (no DB, no parser) — run in the normal suite.
# ---------------------------------------------------------------------------


def test_classify_equal_and_expected_and_unexpected():
    # session_metrics is intentionally NOT in KNOWN_DIFFERENCES, so a difference
    # there is the "unexpected" case; sessions IS known; session_spo2 here is equal.
    assert "session_metrics" not in cp.KNOWN_DIFFERENCES
    legacy = {
        "sessions": {"row_count": 3},
        "session_spo2": {"row_count": 0},
        "session_metrics": {"row_count": 100},
    }
    parser = {
        "sessions": {"row_count": 40},  # differs; sessions IS a known difference
        "session_spo2": {"row_count": 0},  # equal
        "session_metrics": {"row_count": 105},  # differs; NOT a known difference
    }
    report = cp.classify_parity(
        legacy, parser, tables=("sessions", "session_spo2", "session_metrics")
    )
    assert report["sessions"]["category"] == cp.EXPECTED_DIFFERENCE
    assert report["sessions"]["reason"]  # carries the audit reason
    assert report["session_spo2"]["category"] == cp.EQUAL
    assert report["session_metrics"]["category"] == cp.UNEXPECTED_DIFFERENCE


def test_classify_missing_sides():
    legacy = {"sessions": {"row_count": 1}}
    parser = {"session_spo2": {"row_count": 0}}
    report = cp.classify_parity(legacy, parser, tables=("sessions", "session_spo2"))
    assert report["sessions"]["category"] == cp.MISSING_IN_PARSER
    assert report["session_spo2"]["category"] == cp.MISSING_IN_LEGACY


def test_classify_skips_when_a_side_is_none():
    legacy = {"sessions": {"row_count": 1}}
    report = cp.classify_parity(legacy, None, tables=("sessions",))
    assert report["sessions"]["category"] == cp.SKIPPED


def test_classify_marks_query_errors_not_implemented():
    legacy = {"nightly_therapy_aggregates": {"_error": "UndefinedTable: nope"}}
    parser = {"nightly_therapy_aggregates": {"row_count": 3}}
    report = cp.classify_parity(legacy, parser, tables=("nightly_therapy_aggregates",))
    assert report["nightly_therapy_aggregates"]["category"] == cp.NOT_IMPLEMENTED


def test_classify_every_verdict_category_is_valid_and_formattable():
    legacy = {"sessions": {"row_count": 3}, "session_spo2": {"row_count": 9}}
    parser = {"sessions": {"row_count": 40}, "session_spo2": {"row_count": 0}}
    report = cp.classify_parity(legacy, parser, tables=("sessions", "session_spo2"))
    assert cp.categories_present(report) <= cp.VALID_CATEGORIES
    text = cp.format_report(report)
    assert "DB parity report" in text
    assert "session_spo2" in text


def test_known_differences_cover_the_audited_drops():
    # The audit's P0/P1 persisted-row drops must be pre-classified as expected.
    for table in ("session_spo2", "settings_snapshots", "import_source_files", "sessions"):
        assert table in cp.KNOWN_DIFFERENCES


# ---------------------------------------------------------------------------
# Gated end-to-end DB harness.
# ---------------------------------------------------------------------------


class _NoCommitConn:
    """Proxy that swallows ``commit()``/``rollback()`` and delegates the rest.

    The legacy importer commits per block; inside the parity harness we want its
    writes to stay in the caller's rolled-back test transaction, so we hand it a
    connection whose commit/rollback are no-ops. Everything else (``cursor`` …)
    forwards to the real psycopg2 connection.
    """

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def commit(self):  # noqa: D401 — swallow
        pass

    def rollback(self):  # noqa: D401 — swallow
        pass

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)


def _new_machine_and_run(raw_conn, *, user_id, serial, adapter_id):
    """Reconcile a machine and open an ``import_runs`` row; return ``(machine, run)``."""
    import importer.db as importer_db

    machine_id = importer_db.reconcile_machine(
        raw_conn,
        user_id=user_id,
        adapter_id=adapter_id,
        manufacturer="ResMed",
        serial_number=serial,
    )
    with raw_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, started_at
            ) VALUES (%s, %s, %s, 'directory', %s, 'running', 'partial', NOW())
            RETURNING id::text
            """,
            (user_id, machine_id, adapter_id, f"parity-{uuid.uuid4().hex}"),
        )
        run_id = cur.fetchone()[0]
    return machine_id, run_id


def _register_source_manifest(raw_conn, *, run_id: str, fixture_root: Path) -> int:
    """Mirror ``create_import_run`` source-manifest persistence inside the harness."""
    rows = []
    for path in sorted(
        (path for path in fixture_root.rglob("*") if path.is_file()),
        key=lambda item: item.as_posix(),
    ):
        rows.append(
            (
                run_id,
                path.relative_to(fixture_root).as_posix(),
                path.stat().st_size,
                f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
                _source_role(path),
            )
        )
    with raw_conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO import_source_files (
                import_run_id, relative_path, size_bytes, content_hash, parser_role
            ) VALUES %s
            """,
            rows,
        )
    return len(rows)


def _finalize_source_manifest(raw_conn, *, run_id: str) -> None:
    """Mirror the source-disposition part of ``finish_import_run``."""
    with raw_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE import_source_files
            SET disposition = 'skipped'
            WHERE import_run_id = %s AND disposition = 'unknown'
            """,
            (run_id,),
        )


def _run_legacy(real_conn, *, fixture_root: Path, user_id: str, machine_id: str, run_id: str):
    """Drive the legacy ``import_sessions`` path into the test transaction.

    The legacy scripts use bare ``from db import …`` imports (they run with
    ``importer/`` as the working dir), so we add ``importer/`` to ``sys.path`` for
    the duration of the import. Writes go through a commit-swallowing proxy.
    """
    importer_dir = str(_REPO_ROOT / "importer")
    added = importer_dir not in sys.path
    if added:
        sys.path.insert(0, importer_dir)
    try:
        import import_sessions as legacy  # noqa: E402 — path-scoped legacy import
        from resmed_str import parse_resmed_str  # noqa: E402

        proxy = _NoCommitConn(real_conn)
        _tz_name, tz = legacy._machine_tz_for_user(proxy, user_id)
        str_path = fixture_root / "STR.edf"
        str_days = parse_resmed_str(str_path, tz) if str_path.exists() else {}

        datalog = fixture_root / "DATALOG"
        folder_by_date = {
            f.name: f
            for f in datalog.iterdir()
            if f.is_dir() and f.name.isdigit() and len(f.name) == 8
        }
        for str_date in str_days:
            folder_by_date.setdefault(str_date.strftime("%Y%m%d"), datalog / str_date.strftime("%Y%m%d"))

        for key in sorted(folder_by_date):
            folder = folder_by_date[key]
            folder_date = date(int(key[:4]), int(key[4:6]), int(key[6:8]))
            legacy.import_folder(
                folder,
                folder_date,
                proxy,
                user_id,
                import_run_id=run_id,
                machine_id=machine_id,
                adapter_id=_LEGACY_ADAPTER,
                source_root=fixture_root,
                str_day=str_days.get(folder_date),
            )
    finally:
        if added and importer_dir in sys.path:
            sys.path.remove(importer_dir)


def _run_parser(real_conn, *, fixture_root: Path, user_id: str, machine_id: str, run_id: str):
    """Drive the cpap-parser path (loader -> persist_import_run) into the test txn."""
    from importer.loaders.models import ImportOptions
    from importer.loaders.persist import persist_import_run
    from importer.loaders.resmed_native import ResMedNativeLoader

    loader = ResMedNativeLoader()
    detected = next(
        (d for d in loader.detect(fixture_root) if d.adapter_id == _CPAP_PARSER_ADAPTER), None
    )
    assert detected is not None, "cpap-parser loader did not detect the fixture"
    run, directory = loader.import_data_with_directory(detected, ImportOptions())
    persist_import_run(
        run, user_id, real_conn, import_run_id=run_id, machine_id=machine_id, raw_directory=directory
    )


def test_db_parity_harness(db, test_user):
    """Run both import paths on the AirSense 10 fixture and classify DB differences.

    Gated by the ``db`` fixture (needs ``TEST_DATABASE_URL``). The parser half is
    additionally gated on ``cpap-py``; absent it, the report is legacy-only and the
    parser-dependent tables classify as ``skipped`` — never a crash.
    """
    raw = db.connection().connection.driver_connection
    user_id = test_user["id"]

    # --- Legacy path (host-runnable; pure-Python EDF parser) -------------------
    legacy_snap = None
    ml, rl = _new_machine_and_run(raw, user_id=user_id, serial="PARITY-LEGACY", adapter_id=_LEGACY_ADAPTER)
    manifest_count = _register_source_manifest(raw, run_id=rl, fixture_root=_FIXTURE)
    with raw.cursor() as cur:
        cur.execute("SAVEPOINT legacy_sp")
    try:
        _run_legacy(raw, fixture_root=_FIXTURE, user_id=user_id, machine_id=ml, run_id=rl)
        _finalize_source_manifest(raw, run_id=rl)
        legacy_snap = cp.snapshot_parity_tables(raw, machine_id=ml, import_run_id=rl)
    except Exception as exc:  # noqa: BLE001 — record, never crash the harness
        with raw.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT legacy_sp")
        pytest.fail(f"legacy import path raised inside the parity harness: {exc!r}")

    # --- cpap-parser path (cpap-py-gated) -------------------------------------
    parser_snap = None
    try:
        import cpap_py  # noqa: F401

        has_parser = True
    except Exception:
        has_parser = False

    if has_parser:
        mp, rp = _new_machine_and_run(raw, user_id=user_id, serial="PARITY-PARSER", adapter_id=_CPAP_PARSER_ADAPTER)
        assert _register_source_manifest(raw, run_id=rp, fixture_root=_FIXTURE) == manifest_count
        with raw.cursor() as cur:
            cur.execute("SAVEPOINT parser_sp")
        try:
            _run_parser(raw, fixture_root=_FIXTURE, user_id=user_id, machine_id=mp, run_id=rp)
            _finalize_source_manifest(raw, run_id=rp)
            parser_snap = cp.snapshot_parity_tables(raw, machine_id=mp, import_run_id=rp)
        except Exception as exc:  # noqa: BLE001
            with raw.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT parser_sp")
            pytest.fail(f"cpap-parser path raised inside the parity harness: {exc!r}")

    report = cp.classify_parity(legacy_snap, parser_snap)
    print("\n" + cp.format_report(report))

    # The harness ran and produced a snapshot for every table on the legacy side.
    assert legacy_snap is not None
    assert set(report) == set(cp.PARITY_TABLES)
    assert cp.categories_present(report) <= cp.VALID_CATEGORIES

    # Legacy actually wrote rows (proves the harness exercised the real path).
    assert legacy_snap["sessions"]["row_count"] >= 1

    if not has_parser:
        # Without the backend everything is a clean skip, never a crash.
        assert all(v["category"] == cp.SKIPPED for v in report.values())
        pytest.skip("cpap-py backend unavailable — parser side skipped; legacy snapshot captured")

    # Both sides ran. Pin the genuinely-observable findings on this fixture so the
    # harness is a real guard, then require that every divergence is documented.
    assert parser_snap is not None

    # (a) therapy_mode now persists on the parser side. The snapshot comparison
    # includes keys/values and flattened-column coverage so matching row counts
    # cannot falsely imply full settings parity.
    assert legacy_snap["settings_snapshots"]["row_count"] > 0
    assert parser_snap["settings_snapshots"]["row_count"] > 0
    assert parser_snap["settings_snapshots"]["therapy_mode_values"] == ["APAP"]
    assert parser_snap["settings_snapshots"]["session_therapy_mode_count"] > 0
    assert parser_snap["settings_snapshots"]["session_mask_type_count"] == 0
    assert parser_snap["settings_snapshots"]["session_humidity_level_count"] == 0
    assert parser_snap["settings_snapshots"]["session_temperature_c_count"] == 0
    assert parser_snap["settings_snapshots"]["setting_keys"] == ["therapy_mode"]
    assert report["settings_snapshots"]["category"] == cp.EXPECTED_DIFFERENCE

    # (b) Both paths start with the same uploaded-root source manifest. Legacy
    # resolves and links real relative paths; parser synthetic ids are not
    # persisted as fake UUID links.
    legacy_sources = legacy_snap["import_source_files"]
    parser_sources = parser_snap["import_source_files"]
    assert legacy_sources["row_count"] == parser_sources["row_count"] == manifest_count
    assert legacy_sources["roles"] == parser_sources["roles"]
    assert legacy_sources["used_count"] > 0
    assert parser_sources["used_count"] == 0
    assert legacy_sources["skipped_count"] == manifest_count - legacy_sources["used_count"]
    assert parser_sources["skipped_count"] == manifest_count
    assert legacy_sources["unknown_count"] == parser_sources["unknown_count"] == 0
    assert legacy_sources["linked_blocks"] > 0
    assert legacy_sources["linked_events"] > 0
    assert legacy_sources["linked_channels"] > 0
    assert legacy_sources["linked_settings"] > 0
    assert parser_sources["linked_blocks"] == 0
    assert parser_sources["linked_events"] == 0
    assert parser_sources["linked_channels"] == 0
    assert parser_sources["linked_settings"] == 0
    assert report["import_source_files"]["category"] == cp.EXPECTED_DIFFERENCE

    # (c) This fixture cannot exercise oximetry samples: all SAD SpO2 values are
    # missing sentinels (pinned parser-free in test_resmed_import_regressions).
    # Keep the observed 0/0 result explicit without treating it as support parity.
    assert legacy_snap["session_spo2"]["row_count"] == 0
    assert parser_snap["session_spo2"]["row_count"] == 0
    assert legacy_snap["sessions"]["has_spo2_count"] == 0
    assert parser_snap["sessions"]["has_spo2_count"] == 0
    assert report["session_spo2"]["category"] == cp.EQUAL

    # (d) Granularity split is visible: legacy has per-PLD-block session rows
    # (max_block_index >= 1); the cpap-parser path is one row per night (== 0).
    assert legacy_snap["sessions"]["max_block_index"] >= 1
    assert parser_snap["sessions"]["max_block_index"] == 0
    assert report["sessions"]["category"] == cp.EXPECTED_DIFFERENCE

    # (e) Genuine parity also exists — the harness is not all-noise. Low-rate
    # metrics, scored events, and the nightly view agree exactly on this fixture.
    assert report["session_metrics"]["category"] == cp.EQUAL
    assert report["session_events"]["category"] == cp.EQUAL
    assert report["nightly_therapy_aggregates"]["category"] == cp.EQUAL

    # (f) First-version guarantee: nothing surfaces as an undocumented divergence.
    unexpected = {t: v for t, v in report.items() if v["category"] == cp.UNEXPECTED_DIFFERENCE}
    assert not unexpected, f"undocumented DB divergence(s): {unexpected}"
