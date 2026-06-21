"""Tests for the 1.x -> 2.x upgrade safety guard.

The classification and block decisions are pure functions, so most of this suite
runs without a database. Two storage-layer checks (legacy waveform survival and
row/chunk coexistence) require Postgres and skip cleanly without it, matching the
rest of the DB-backed suite.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy import text

from api.upgrade_guard import (
    BLOCKED_CONFLICTING_1_4_MIGRATIONS,
    MANUAL_REVIEW_REQUIRED,
    SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS,
    SAFE_TO_ATTEMPT_IN_PLACE,
    UPSTREAM_1_4_CONFLICT_MIGRATIONS,
    classify_upgrade_state,
    evaluate_startup,
    local_migration_filenames,
)

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _shared_baseline() -> set[str]:
    """The 001-021 migrations shared identically by upstream 1.4 and the 2.0 line.

    A known-safe 1.3.x-style database has only these recorded (plus the schema
    bootstrap), with none of the divergent 022+ migrations from either line.
    """
    return {
        path.name
        for path in _MIGRATIONS_DIR.glob("*.sql")
        if path.name[:3].isdigit() and int(path.name[:3]) <= 21
    } | {"schema.sql"}


def _full_2_0_history() -> set[str]:
    """Every migration this 2.0 checkout ships, as a fully-migrated 2.0 DB sees it."""
    return local_migration_filenames() | {"schema.sql"}


def _upstream_1_4_history() -> set[str]:
    """Shared baseline plus the upstream 1.4 adherence migrations (the conflict)."""
    return _shared_baseline() | set(UPSTREAM_1_4_CONFLICT_MIGRATIONS)


# -- Startup block decision -------------------------------------------------


def test_fresh_database_is_not_blocked():
    """A fresh install (nothing recorded yet) must pass through."""
    assert evaluate_startup(set()).blocked is False


def test_valid_2_0_database_is_not_blocked():
    """A fully-migrated 2.0 database must not be blocked."""
    assert evaluate_startup(_full_2_0_history()).blocked is False


def test_known_safe_legacy_database_is_not_blocked():
    """A known-safe 1.3.x-style database (shared baseline only) is not blocked."""
    assert evaluate_startup(_shared_baseline()).blocked is False


def test_conflicting_upstream_1_4_database_is_blocked():
    """An upstream 1.4 database (collision migrations recorded) is blocked."""
    decision = evaluate_startup(_upstream_1_4_history())
    assert decision.blocked is True
    assert sorted(UPSTREAM_1_4_CONFLICT_MIGRATIONS) == decision.conflicts


def test_block_message_is_clear_and_actionable():
    """The block message names the conflict, warns against data loss, and guides recovery."""
    message = evaluate_startup(_upstream_1_4_history()).message
    assert "blocked" in message.lower()
    assert "022_add_adherence_settings.sql" in message
    assert "023_add_adherence_enabled.sql" in message
    assert "pg_dump" in message
    assert "check_2x_upgrade_readiness.py" in message
    # No data has been touched, and the user is warned off destructive resets.
    assert "No data has been modified" in message
    assert "back" in message.lower()


def test_single_conflict_entry_still_blocks():
    """Even one upstream 1.4 conflict migration is enough to block."""
    applied = _shared_baseline() | {"022_add_adherence_settings.sql"}
    assert evaluate_startup(applied).blocked is True


# -- Readiness classification -----------------------------------------------


def test_classify_fresh_database_safe_in_place():
    assert (
        classify_upgrade_state(set(), has_chunk_waveforms=False, has_row_waveforms=False)
        == SAFE_TO_ATTEMPT_IN_PLACE
    )


def test_classify_2_0_with_chunks_safe_in_place():
    assert (
        classify_upgrade_state(
            _full_2_0_history(), has_chunk_waveforms=True, has_row_waveforms=False
        )
        == SAFE_TO_ATTEMPT_IN_PLACE
    )


def test_classify_legacy_rows_only_recommends_reimport():
    """Legacy row-backed waveforms without chunks -> reimport recommended."""
    assert (
        classify_upgrade_state(
            _shared_baseline(), has_chunk_waveforms=False, has_row_waveforms=True
        )
        == SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS
    )


def test_classify_conflict_is_blocked():
    assert (
        classify_upgrade_state(
            _upstream_1_4_history(), has_chunk_waveforms=False, has_row_waveforms=True
        )
        == BLOCKED_CONFLICTING_1_4_MIGRATIONS
    )


def test_classify_unknown_migration_requires_manual_review():
    """An unrecognized migration filename (not ours, not a known conflict) -> manual review."""
    applied = _shared_baseline() | {"099_some_unknown_fork_migration.sql"}
    assert (
        classify_upgrade_state(
            applied, has_chunk_waveforms=False, has_row_waveforms=False
        )
        == MANUAL_REVIEW_REQUIRED
    )


def test_conflict_takes_precedence_over_unknown():
    """A conflict outranks an unknown migration in the classification."""
    applied = _upstream_1_4_history() | {"099_some_unknown_fork_migration.sql"}
    assert (
        classify_upgrade_state(
            applied, has_chunk_waveforms=False, has_row_waveforms=False
        )
        == BLOCKED_CONFLICTING_1_4_MIGRATIONS
    )


# -- Static migration safety (no DB) ----------------------------------------


def test_no_upgrade_migration_destroys_legacy_session_waveform():
    """Upgrade migrations must never drop/truncate/delete/vacuum session_waveform.

    Preservation-first: old row-backed waveform data is kept as-is. This is a
    static check over the migration SQL so it runs without a database.

    Migrations ``001``-``005`` are the foundational baseline (``004`` recreates
    the whole schema from scratch). The migration runner explicitly adopts that
    baseline as already-applied on any existing database and never re-runs it, so
    it is not part of the 1.x -> 2.x upgrade delta and is excluded here. Every
    migration that *does* run on a populated database (``006`` onward) must
    preserve ``session_waveform``.
    """
    destructive = re.compile(
        r"\b(drop\s+table|truncate|delete\s+from|vacuum)\b[^;]*session_waveform",
        re.IGNORECASE | re.DOTALL,
    )
    offenders = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if not (path.name[:3].isdigit() and int(path.name[:3]) >= 6):
            continue
        if destructive.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == [], f"destructive session_waveform statements in: {offenders}"


# -- Storage-layer coexistence (requires Postgres) --------------------------


def _seed_session(db, user_id: str, session_key: str) -> str:
    """Insert one session and return its internal UUID id."""
    return db.execute(
        text("""
            INSERT INTO sessions (
                session_id, folder_date, start_datetime, pld_start_datetime,
                duration_seconds, device_serial, manufacturer, user_id,
                provenance_status
            ) VALUES (
                :session_id, DATE '2026-06-01', NOW(), NOW(), 3600,
                'GUARD-COEXIST', 'ResMed', CAST(:uid AS uuid), 'legacy_backfilled'
            )
            RETURNING id::text
        """),
        {"session_id": session_key, "uid": user_id},
    ).scalar_one()


def test_legacy_and_chunk_waveforms_coexist(db, test_user):
    """A session may carry both legacy row-backed and new chunk-backed waveforms.

    This exercises the storage invariant the Event Inspector fallback relies on:
    old ``session_waveform`` rows and new ``waveform_chunks`` rows for the same
    session can be stored and read independently without conflict.
    """
    session_id = _seed_session(db, test_user["id"], f"coexist-{uuid.uuid4().hex[:8]}")

    db.execute(
        text("""
            INSERT INTO session_waveform (session_id, ts, flow, pressure)
            VALUES (CAST(:sid AS uuid), NOW(), 12.3456, 9.87)
        """),
        {"sid": session_id},
    )
    db.execute(
        text("""
            INSERT INTO waveform_chunks (
                session_id, signal_name, unit, sample_rate_hz,
                start_time, end_time, chunk_index, sample_count,
                encoding, payload, uncompressed_bytes, compressed_bytes,
                adapter_id
            ) VALUES (
                CAST(:sid AS uuid), 'flow_rate', 'L/min', 25.0,
                NOW(), NOW() + INTERVAL '1 second', 0, 25,
                'float32-le-zlib-v1', :payload, 100, 40,
                'resmed-native-v2'
            )
        """),
        {"sid": session_id, "payload": b"\x00\x01\x02\x03"},
    )
    db.commit()

    row_count = db.execute(
        text("SELECT COUNT(*) FROM session_waveform WHERE session_id = CAST(:sid AS uuid)"),
        {"sid": session_id},
    ).scalar_one()
    chunk_count = db.execute(
        text("SELECT COUNT(*) FROM waveform_chunks WHERE session_id = CAST(:sid AS uuid)"),
        {"sid": session_id},
    ).scalar_one()

    assert row_count == 1
    assert chunk_count == 1
