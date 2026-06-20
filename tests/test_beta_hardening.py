"""Beta-hardening coverage for the ResMed parser-default import path.

These tests lock in the safety guarantees the 2.0 beta depends on:

* the mixed parser/native history block fires in **both** directions and never
  blocks a same-backend re-import (``resmed_backend_conflict``);
* older/legacy ``import_runs`` rows — written before the alpha.23/alpha.24
  provenance and result-summary columns existed — still render through the
  Import History endpoints with the new fields reported as ``null`` rather than
  erroring.

All tests require a Postgres test database (``TEST_DATABASE_URL``) and skip
cleanly without one, matching the rest of the DB-backed suite.
"""

from types import SimpleNamespace

from sqlalchemy import text

from api.import_runs import resmed_backend_conflict

# Provenance prefixes the import flow writes, mirrored from api.import_runs.
_NATIVE_PROVENANCE = "native_resmed_partial"
_PARSER_PROVENANCE = "native_resmed_cpap_parser"


def _resmed_plan(serial: str, adapter_id: str = "resmed-native-v2") -> SimpleNamespace:
    """Build the minimal plan shape ``resmed_backend_conflict`` reads.

    The function only touches ``plan.inspection["devices"][0]["adapter_id"]`` and
    its ``identity.serial_number``, so a lightweight stand-in avoids staging and
    inspecting a full SD-card fixture for a pure routing-policy assertion.
    """

    return SimpleNamespace(
        inspection={
            "devices": [{"adapter_id": adapter_id, "identity": {"serial_number": serial}}]
        }
    )


def _seed_session(db, user_id: str, *, serial: str, provenance: str) -> None:
    """Insert one ResMed session with a given provenance for conflict checks."""

    db.execute(
        text("""
            INSERT INTO sessions (
                session_id, folder_date, start_datetime, pld_start_datetime,
                duration_seconds, device_serial, manufacturer, user_id,
                provenance_status
            ) VALUES (
                :session_id, DATE '2026-06-01', NOW(), NOW(), 3600,
                :serial, 'ResMed', CAST(:uid AS uuid), :provenance
            )
        """),
        {
            "session_id": f"seed-{provenance}-{serial}",
            "serial": serial,
            "uid": user_id,
            "provenance": provenance,
        },
    )
    db.commit()


# -- Mixed parser/native history block (both directions) --------------------


def test_no_conflict_when_machine_has_no_history(db, test_user):
    """A first-ever import of either backend is never a mixed-history conflict."""

    plan = _resmed_plan("BETA-FRESH")
    assert resmed_backend_conflict(db, user_id=test_user["id"], plan=plan, parser_selected=True) is False
    assert resmed_backend_conflict(db, user_id=test_user["id"], plan=plan, parser_selected=False) is False


def test_parser_selected_blocks_existing_native_history(db, test_user):
    """Parser import is blocked when the machine already has native sessions."""

    _seed_session(db, test_user["id"], serial="BETA-NATIVE", provenance=_NATIVE_PROVENANCE)
    plan = _resmed_plan("BETA-NATIVE")
    assert (
        resmed_backend_conflict(db, user_id=test_user["id"], plan=plan, parser_selected=True)
        is True
    )


def test_native_fallback_blocks_existing_parser_history(db, test_user):
    """Reverse direction: native fallback is blocked when parser history exists.

    This is the guard that keeps ``SLEEPLAB_USE_CPAP_PARSER=0`` from layering
    legacy/native sessions on top of a machine already imported by cpap-parser.
    """

    _seed_session(db, test_user["id"], serial="BETA-PARSER", provenance=_PARSER_PROVENANCE)
    plan = _resmed_plan("BETA-PARSER")
    assert (
        resmed_backend_conflict(db, user_id=test_user["id"], plan=plan, parser_selected=False)
        is True
    )


def test_same_backend_reimport_is_not_a_conflict(db, test_user):
    """A re-import on the same backend must never trip the mixed-history block."""

    _seed_session(db, test_user["id"], serial="BETA-PARSER-RE", provenance=_PARSER_PROVENANCE)
    parser_plan = _resmed_plan("BETA-PARSER-RE")
    assert (
        resmed_backend_conflict(db, user_id=test_user["id"], plan=parser_plan, parser_selected=True)
        is False
    )

    _seed_session(db, test_user["id"], serial="BETA-NATIVE-RE", provenance=_NATIVE_PROVENANCE)
    native_plan = _resmed_plan("BETA-NATIVE-RE")
    assert (
        resmed_backend_conflict(db, user_id=test_user["id"], plan=native_plan, parser_selected=False)
        is False
    )


# -- Older import-history rendering -----------------------------------------


def _insert_legacy_run(db, user_id: str) -> str:
    """Insert a pre-alpha.23 style run: no provenance/result-summary columns set.

    Only the columns that existed in migration 023 are populated; the alpha.23/24
    additions (``importer_mode``, ``waveform_chunk_count``, ``capability_status``,
    ``sessions_*_count``, staged-progress fields, …) are left at their defaults /
    ``NULL`` to emulate a row written by an older SleepLab build.
    """

    return db.execute(
        text("""
            INSERT INTO import_runs (
                user_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, imported_session_count, started_at,
                completed_at
            ) VALUES (
                CAST(:uid AS uuid), 'resmed-native-v2', 'directory',
                :fingerprint, 'success', 'partial', 3, NOW(), NOW()
            )
            RETURNING id::text
        """),
        {"uid": user_id, "fingerprint": f"legacy-history-{user_id}"},
    ).scalar_one()


def test_import_history_list_renders_legacy_run_safely(client, auth_headers, db, test_user):
    """``GET /imports/runs`` renders a legacy row with new fields reported null."""

    run_id = _insert_legacy_run(db, test_user["id"])
    db.commit()

    resp = client.get("/imports/runs", headers=auth_headers)
    assert resp.status_code == 200
    runs = {row["id"]: row for row in resp.json()}
    assert run_id in runs
    row = runs[run_id]
    # Pre-existing fields still populate.
    assert row["status"] == "success"
    assert row["imported_session_count"] == 3
    # Newer nullable provenance / result-summary fields degrade to null, not an
    # error (these have no column default).
    assert row["importer_mode"] is None
    assert row["waveform_chunk_count"] is None
    assert row["sessions_added_count"] is None
    assert row["current_stage"] is None
    # NOT NULL columns added by later migrations carry their server default.
    assert row["capability_status"] == {}
    assert row["summary_only_day_count"] == 0


def test_import_history_detail_renders_legacy_run_safely(client, auth_headers, db, test_user):
    """``GET /imports/runs/{id}`` renders a legacy row (no manifest) without error."""

    run_id = _insert_legacy_run(db, test_user["id"])
    db.commit()

    resp = client.get(f"/imports/runs/{run_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert body["status"] == "success"
    # A legacy run has no persisted source-file manifest; the endpoint returns an
    # empty list rather than failing.
    assert body["source_files"] == []
