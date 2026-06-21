#!/usr/bin/env python3
"""Read-only readiness check for upgrading a SleepLab database to the 2.0 line.

Inspects the target database and prints a sanitized summary plus a single
recommendation token. It never writes to the database and never prints PHI,
device serials, raw filenames, or per-night details unless ``--verbose`` is
passed (which only adds the conflicting migration *names*, still not PHI).

Usage:
    DATABASE_URL=postgresql://... python scripts/check_2x_upgrade_readiness.py

Recommendations:
    SAFE_TO_ATTEMPT_IN_PLACE
    SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS
    BLOCKED_CONFLICTING_1_4_MIGRATIONS
    MANUAL_REVIEW_REQUIRED

Exit codes: 0 when a recommendation is produced (including BLOCKED), 2 when the
database is unreachable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

# Allow ``python scripts/check_2x_upgrade_readiness.py`` to import the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.upgrade_guard import (  # noqa: E402
    classify_upgrade_state,
    conflicting_migrations,
    foreign_migrations,
    local_migration_filenames,
)


def _table_exists(cur, name: str) -> bool:
    """Return whether a regular table named ``name`` exists in the public schema."""
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _count(cur, name: str) -> int | None:
    """Return ``COUNT(*)`` for ``name``, or None when the table is absent."""
    if not _table_exists(cur, name):
        return None
    cur.execute(f"SELECT COUNT(*) FROM {name}")  # noqa: S608 — name is a literal allowlist
    return int(cur.fetchone()[0])


def _applied_migrations(cur) -> set[str]:
    """Return the filenames recorded in ``schema_migrations`` (empty if absent)."""
    if not _table_exists(cur, "schema_migrations"):
        return set()
    cur.execute("SELECT filename FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _exists(count: int | None) -> bool:
    """Return whether a count indicates at least one row."""
    return bool(count)


def _yesno(value: bool) -> str:
    """Render a boolean as ``yes``/``no``."""
    return "yes" if value else "no"


def collect_and_report(conn, *, verbose: bool) -> str:
    """Inspect the database and print the readiness report.

    Args:
        conn: An open psycopg2 connection.
        verbose: When True, also list the conflicting migration filenames.

    Returns:
        The recommendation token.
    """
    with conn.cursor() as cur:
        applied = _applied_migrations(cur)
        conflicts = conflicting_migrations(applied)
        local = local_migration_filenames()
        foreign = foreign_migrations(applied, local)

        sessions_count = _count(cur, "sessions")
        events_count = _count(cur, "session_events")
        row_waveform_count = _count(cur, "session_waveform")
        chunk_waveform_count = _count(cur, "waveform_chunks")
        import_runs_count = _count(cur, "import_runs")

    has_row_waveforms = _exists(row_waveform_count)
    has_chunk_waveforms = _exists(chunk_waveform_count)

    recommendation = classify_upgrade_state(
        applied,
        has_chunk_waveforms=has_chunk_waveforms,
        has_row_waveforms=has_row_waveforms,
        local=local,
    )

    def line(label: str, value: object) -> None:
        print(f"{label:<42}{value}")

    print("SleepLab 2.0 upgrade readiness")
    print("=" * 60)
    line("database reachable:", "yes")
    line("schema_migrations entries:", len(applied) if applied else 0)
    line("conflicting upstream 1.4 migrations:", _yesno(bool(conflicts)))
    if verbose and conflicts:
        for name in sorted(conflicts):
            line("  conflict:", name)
    if verbose and foreign:
        line("unrecognized migrations:", len(foreign))
    line("sessions table count:", "absent" if sessions_count is None else sessions_count)
    line("events table count:", "absent" if events_count is None else events_count)
    line(
        "session_waveform row count:",
        "absent" if row_waveform_count is None else row_waveform_count,
    )
    line(
        "waveform_chunks row count:",
        "absent" if chunk_waveform_count is None else chunk_waveform_count,
    )
    line(
        "import_runs count:",
        "absent" if import_runs_count is None else import_runs_count,
    )
    line("old row-backed waveform data:", _yesno(has_row_waveforms))
    line("new chunk-backed waveform data:", _yesno(has_chunk_waveforms))
    print("-" * 60)
    line("RECOMMENDATION:", recommendation)
    return recommendation


def main() -> int:
    """Entry point: connect read-only, print the report, and return an exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also list conflicting migration filenames (not PHI)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "DATABASE_URL must be set (credentials are intentionally not accepted "
            "as CLI arguments)",
            file=sys.stderr,
        )
        return 2
    database_url = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(database_url)
    except psycopg2.Error as exc:
        print("database reachable:                       no", file=sys.stderr)
        print(f"connection error: {exc.__class__.__name__}", file=sys.stderr)
        return 2

    try:
        # Keep the whole inspection read-only even if a query is interrupted.
        conn.set_session(readonly=True, autocommit=True)
        collect_and_report(conn, verbose=args.verbose)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
