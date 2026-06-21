"""Runtime for the upstream 1.4 -> SleepLab 2.0 bridge.

:mod:`api.upgrade_guard` decides *whether* a database is a clean upstream 1.4
state that may be bridged. This module performs the actual, database-touching
work when that decision is ``ACTION_BRIDGE``:

1. Ensure a durable, inspectable ``schema_compatibility`` marker table exists.
2. Verify the live schema really matches the expected upstream 1.4 shape
   (``user_import_settings`` adherence columns present, baseline tables present).
   If it does not, raise a clear error *before* writing anything.
3. Record a bridge marker documenting what was recognized and preserved.

The bridge deliberately does **not** alter sessions/events/metrics/waveforms or
the adherence columns: the two histories are orthogonal, so reconciliation is
purely a matter of recording that the upstream 1.4 adherence migrations are
accounted for and then letting the normal 2.0 apply loop add migrations
``022``-``032`` on top. It never fakes a migration it did not run.

The functions accept any SQLAlchemy connection/session exposing ``execute`` and
``commit`` (the runner passes a ``Connection``; tests pass an engine connection).
"""

from __future__ import annotations

import json

from sqlalchemy import text

from api.upgrade_guard import UPSTREAM_1_4_ADHERENCE_COLUMNS, UPSTREAM_1_4_CONFLICT_MIGRATIONS

# Marker kind recorded in ``schema_compatibility`` for a completed bridge.
BRIDGE_KIND = "upstream_1_4_to_2_0"
BRIDGE_VERSION = 1

# Baseline tables an upstream 1.4 database must have before we will bridge it.
_REQUIRED_BASELINE_TABLES = (
    "users",
    "user_import_settings",
    "sessions",
    "session_events",
    "session_metrics",
    "session_waveform",
)


class BridgeError(RuntimeError):
    """Raised when a bridge cannot be performed safely. Message is human-readable."""


def ensure_compatibility_table(conn) -> None:
    """Create the durable ``schema_compatibility`` marker table if absent.

    The table is intentionally simple and append-only: each row records one
    compatibility/bridge event that was applied to this database.
    """
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS schema_compatibility (
            id          BIGSERIAL PRIMARY KEY,
            kind        TEXT NOT NULL,
            source_line TEXT,
            detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    )
    conn.commit()


def _table_exists(conn, name: str) -> bool:
    """Return whether a regular table named ``name`` exists in the public schema."""
    return conn.execute(
        text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
    ).scalar() is not None


def bridge_recorded(conn) -> bool:
    """Return whether a completed upstream 1.4 -> 2.0 bridge is recorded.

    Safe to call before the marker table exists (returns False).
    """
    if not _table_exists(conn, "schema_compatibility"):
        return False
    return bool(
        conn.execute(
            text("SELECT 1 FROM schema_compatibility WHERE kind = :k LIMIT 1"),
            {"k": BRIDGE_KIND},
        ).first()
    )


def _existing_columns(conn, table: str) -> set[str]:
    """Return the column names present on ``table`` in the public schema."""
    rows = conn.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table},
    ).all()
    return {row[0] for row in rows}


def verify_upstream_1_4_schema(conn) -> list[str]:
    """Return a list of problems that make this database unsafe to bridge.

    An empty list means the live schema matches the expected upstream 1.4 shape:
    the baseline tables exist and ``user_import_settings`` carries the adherence
    columns the upstream 022/023 migrations add. Any returned problem is a
    human-readable reason the bridge must not proceed.
    """
    problems: list[str] = []
    for table in _REQUIRED_BASELINE_TABLES:
        if not _table_exists(conn, table):
            problems.append(f"expected baseline table '{table}' is missing")

    if _table_exists(conn, "user_import_settings"):
        present = _existing_columns(conn, "user_import_settings")
        missing = sorted(UPSTREAM_1_4_ADHERENCE_COLUMNS - present)
        if missing:
            problems.append(
                "user_import_settings is missing upstream 1.4 adherence column(s): "
                + ", ".join(missing)
            )
    return problems


def perform_bridge(conn) -> bool:
    """Perform the upstream 1.4 -> 2.0 bridge, recording a durable marker.

    Verifies the live schema first and raises :class:`BridgeError` with a clear
    message if it does not match the expected upstream 1.4 shape — *before* any
    write. Idempotent: if a bridge is already recorded, returns False without
    changing anything.

    Args:
        conn: A SQLAlchemy connection/session with ``execute`` and ``commit``.

    Returns:
        True when a new bridge marker was recorded, False if one already existed.

    Raises:
        BridgeError: If the schema cannot be safely bridged.
    """
    ensure_compatibility_table(conn)
    if bridge_recorded(conn):
        return False

    problems = verify_upstream_1_4_schema(conn)
    if problems:
        listed = "\n".join(f"  - {p}" for p in problems)
        raise BridgeError(
            "SleepLab 2.0 upgrade blocked: the upstream 1.4 -> 2.0 bridge could "
            "not verify this database's schema and stopped before writing.\n\n"
            f"{listed}\n\n"
            "No data has been modified. Back up first "
            '(pg_dump "$DATABASE_URL" > sleeplab-backup.sql), run '
            "python scripts/check_2x_upgrade_readiness.py, and see "
            "docs/sleeplab_2_upgrade_from_1x.md for recovery options."
        )

    detail = {
        "bridge_version": BRIDGE_VERSION,
        "recognized_migrations": sorted(UPSTREAM_1_4_CONFLICT_MIGRATIONS),
        "preserved_adherence_columns": sorted(UPSTREAM_1_4_ADHERENCE_COLUMNS),
        "note": (
            "Upstream 1.4 adherence migrations recognized and preserved; SleepLab "
            "2.0 migrations 022-032 are applied additively on top. No sessions, "
            "events, metrics, or waveforms were altered."
        ),
    }
    conn.execute(
        text("""
            INSERT INTO schema_compatibility (kind, source_line, detail)
            VALUES (:kind, :source_line, CAST(:detail AS jsonb))
        """),
        {"kind": BRIDGE_KIND, "source_line": "upstream-1.4", "detail": json.dumps(detail)},
    )
    conn.commit()
    return True
