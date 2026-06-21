"""Tests for the 1.x -> 2.x upgrade guard policy (pure, no database).

These cover the bridge-aware startup decision and the readiness classification.
The decisions are pure functions of the recorded migration filenames plus a few
boolean facts, so the whole module runs without Postgres. DB-backed runtime
behavior of the bridge itself lives in ``tests/test_upgrade_bridge.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.upgrade_guard import (
    ACTION_BRIDGE,
    ACTION_NONE,
    BLOCKED_UNSUPPORTED_1_4_STATE,
    BRIDGEABLE_UPSTREAM_1_4_TO_2_0,
    BRIDGED_UPSTREAM_1_4_TO_2_0,
    MANUAL_REVIEW_REQUIRED,
    SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS,
    SAFE_TO_ATTEMPT_IN_PLACE,
    UPSTREAM_1_4_CONFLICT_MIGRATIONS,
    classify_upgrade_state,
    divergent_2_0_migrations,
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
    """Shared baseline plus the upstream 1.4 adherence migrations (clean 1.4 DB)."""
    return _shared_baseline() | set(UPSTREAM_1_4_CONFLICT_MIGRATIONS)


def _bridged_history() -> set[str]:
    """A bridged database: upstream 1.4 entries plus the applied 2.0 migrations."""
    return _full_2_0_history() | set(UPSTREAM_1_4_CONFLICT_MIGRATIONS)


# -- Startup decision: non-1.4 databases ------------------------------------


def test_fresh_database_is_not_blocked():
    """A fresh install (nothing recorded yet) must pass through with no action."""
    decision = evaluate_startup(set())
    assert decision.blocked is False
    assert decision.action == ACTION_NONE


def test_valid_2_0_database_is_not_blocked():
    """A fully-migrated 2.0 database must not be blocked and needs no bridge."""
    decision = evaluate_startup(_full_2_0_history())
    assert decision.blocked is False
    assert decision.action == ACTION_NONE


def test_known_safe_legacy_database_is_not_blocked():
    """A known-safe 1.3.x-style database (shared baseline only) is not blocked."""
    decision = evaluate_startup(_shared_baseline())
    assert decision.blocked is False
    assert decision.action == ACTION_NONE


# -- Startup decision: upstream 1.4 ------------------------------------------


def test_clean_upstream_1_4_is_bridgeable_not_blocked():
    """A clean upstream 1.4 database is bridged, not blocked."""
    decision = evaluate_startup(_upstream_1_4_history())
    assert decision.blocked is False
    assert decision.action == ACTION_BRIDGE
    assert decision.state == BRIDGEABLE_UPSTREAM_1_4_TO_2_0
    assert sorted(UPSTREAM_1_4_CONFLICT_MIGRATIONS) == decision.conflicts


def test_already_bridged_upstream_1_4_is_not_blocked_and_needs_no_action():
    """Once bridged, the database proceeds normally with no further bridge step."""
    decision = evaluate_startup(_bridged_history(), bridge_recorded=True)
    assert decision.blocked is False
    assert decision.action == ACTION_NONE
    assert decision.state == BRIDGED_UPSTREAM_1_4_TO_2_0


def test_partial_upstream_1_4_is_blocked():
    """Only one of the two adherence migrations recorded -> unsafe, blocked."""
    applied = _shared_baseline() | {"022_add_adherence_settings.sql"}
    decision = evaluate_startup(applied)
    assert decision.blocked is True
    assert decision.state == BLOCKED_UNSUPPORTED_1_4_STATE


def test_mixed_upstream_1_4_without_bridge_is_blocked():
    """Upstream 1.4 entries plus 2.0-divergent migrations, with no recorded bridge."""
    one_divergent = sorted(divergent_2_0_migrations())[0]
    applied = _upstream_1_4_history() | {one_divergent}
    decision = evaluate_startup(applied, bridge_recorded=False)
    assert decision.blocked is True
    assert decision.state == BLOCKED_UNSUPPORTED_1_4_STATE


def test_block_message_is_clear_and_actionable():
    """The unsupported-state message guides backup, readiness, and recovery."""
    applied = _shared_baseline() | {"022_add_adherence_settings.sql"}
    message = evaluate_startup(applied).message
    assert "blocked" in message.lower()
    assert "022_add_adherence_settings.sql" in message
    assert "pg_dump" in message
    assert "check_2x_upgrade_readiness.py" in message
    assert "No data has been modified" in message
    assert "back" in message.lower()


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


def test_classify_clean_upstream_1_4_is_bridgeable():
    assert (
        classify_upgrade_state(
            _upstream_1_4_history(), has_chunk_waveforms=False, has_row_waveforms=True
        )
        == BRIDGEABLE_UPSTREAM_1_4_TO_2_0
    )


def test_classify_bridged_upstream_1_4():
    assert (
        classify_upgrade_state(
            _bridged_history(),
            has_chunk_waveforms=False,
            has_row_waveforms=True,
            bridge_recorded=True,
        )
        == BRIDGED_UPSTREAM_1_4_TO_2_0
    )


def test_classify_partial_1_4_is_blocked_unsupported():
    applied = _shared_baseline() | {"023_add_adherence_enabled.sql"}
    assert (
        classify_upgrade_state(
            applied, has_chunk_waveforms=False, has_row_waveforms=False
        )
        == BLOCKED_UNSUPPORTED_1_4_STATE
    )


def test_classify_mixed_1_4_without_bridge_is_blocked_unsupported():
    one_divergent = sorted(divergent_2_0_migrations())[0]
    applied = _upstream_1_4_history() | {one_divergent}
    assert (
        classify_upgrade_state(
            applied, has_chunk_waveforms=False, has_row_waveforms=False
        )
        == BLOCKED_UNSUPPORTED_1_4_STATE
    )


def test_classify_unknown_migration_requires_manual_review():
    """An unrecognized filename (not ours, not a known 1.4 migration) -> manual review."""
    applied = _shared_baseline() | {"099_some_unknown_fork_migration.sql"}
    assert (
        classify_upgrade_state(
            applied, has_chunk_waveforms=False, has_row_waveforms=False
        )
        == MANUAL_REVIEW_REQUIRED
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
