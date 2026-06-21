"""DB-backed tests for the upstream 1.4 -> 2.0 bridge runtime.

These build a *real* throwaway upstream-1.4 database (its own engine and database,
dropped afterward) and exercise the bridge end to end: detection, schema
verification, the bridge marker, data preservation, continuation onto the 2.0
migration path, and row/chunk waveform coexistence. They skip cleanly when no
Postgres test database is configured or when the test role cannot create
databases, matching the rest of the DB-backed suite.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from api.upgrade_bridge import (
    BridgeError,
    bridge_recorded,
    ensure_compatibility_table,
    perform_bridge,
    verify_upstream_1_4_schema,
)
from api.upgrade_guard import UPSTREAM_1_4_ADHERENCE_COLUMNS

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_REPO_ROOT, "migrations")
_SCHEMA_SQL = os.path.join(_REPO_ROOT, "schema.sql")


def _test_db_url() -> str | None:
    """Mirror conftest's safety gate for locating a Postgres test database."""
    url = os.environ.get("DATABASE_URL", "")
    if "test" in url.lower() or "TEST_DATABASE_URL" in os.environ:
        return os.environ.get("TEST_DATABASE_URL", url)
    return None


def _migration_files(max_number: int) -> list[str]:
    """Return absolute paths of migrations numbered <= ``max_number``, in order."""
    names = sorted(
        n for n in os.listdir(_MIGRATIONS_DIR)
        if n.endswith(".sql") and n[:3].isdigit() and int(n[:3]) <= max_number
    )
    return [os.path.join(_MIGRATIONS_DIR, n) for n in names]


def _migration_files_between(low: int, high: int) -> list[tuple[str, str]]:
    """Return ``(filename, abspath)`` for migrations numbered in ``[low, high]``."""
    names = sorted(
        n for n in os.listdir(_MIGRATIONS_DIR)
        if n.endswith(".sql") and n[:3].isdigit() and low <= int(n[:3]) <= high
    )
    return [(n, os.path.join(_MIGRATIONS_DIR, n)) for n in names]


@pytest.fixture
def upstream_1_4_engine():
    """Yield an engine to a throwaway database shaped like a clean upstream 1.4 DB.

    Builds schema.sql + migrations 001-021, adds the upstream 1.4 adherence
    columns, records the upstream schema_migrations history (including the
    colliding 022/023 adherence entries), and seeds a little user data. Drops the
    database on teardown.
    """
    url = _test_db_url()
    if url is None:
        pytest.skip("No test database URL configured")

    base = make_url(url)
    admin = base.set(database="postgres")
    throwaway_name = f"{(base.database or 'sleeplab')}_bridge_{uuid.uuid4().hex[:10]}"

    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{throwaway_name}"'))
    except Exception as exc:  # noqa: BLE001 — environment may forbid CREATE DATABASE
        admin_engine.dispose()
        pytest.skip(f"Cannot create throwaway database: {exc}")

    throwaway_url = base.set(database=throwaway_name)
    engine = create_engine(throwaway_url)
    try:
        _build_upstream_1_4(engine)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": throwaway_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{throwaway_name}"'))
        admin_engine.dispose()


def _build_upstream_1_4(engine) -> None:
    """Construct a clean upstream-1.4 schema + history + seed data in ``engine``."""
    with engine.begin() as conn:
        conn.execute(
            text("""
            CREATE TABLE schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """)
        )
        # Apply the shared baseline: schema.sql + 001-021.
        baseline = [_SCHEMA_SQL] + _migration_files(21)
        for path in baseline:
            with open(path, encoding="utf-8") as handle:
                conn.exec_driver_sql(handle.read())
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": os.path.basename(path)},
            )

        # Upstream 1.4's 022/023 adherence columns on user_import_settings.
        adherence_cols = ", ".join(
            f"ADD COLUMN IF NOT EXISTS {col} "
            + ("BOOLEAN DEFAULT TRUE" if col == "adherence_enabled" else "NUMERIC")
            for col in sorted(UPSTREAM_1_4_ADHERENCE_COLUMNS)
        )
        conn.exec_driver_sql(f"ALTER TABLE user_import_settings {adherence_cols}")
        for name in ("022_add_adherence_settings.sql", "023_add_adherence_enabled.sql"):
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": name},
            )

        # Seed a user, an import-settings row with adherence values, a session,
        # and a couple of legacy row-backed waveform samples.
        uid = conn.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
            {"e": f"bridge-{uuid.uuid4().hex[:8]}@test.local"},
        ).scalar_one()
        conn.execute(
            text("""
                INSERT INTO user_import_settings (user_id, adherence_enabled, adherence_threshold_hours)
                VALUES (CAST(:u AS uuid), TRUE, 4)
            """),
            {"u": uid},
        )
        sid = conn.execute(
            text("""
                INSERT INTO sessions (
                    session_id, user_id, folder_date, start_datetime, pld_start_datetime,
                    duration_seconds, device_serial
                ) VALUES (
                    '20260101_000000', CAST(:u AS uuid), DATE '2026-01-01', NOW(), NOW(), 3600, 'BRIDGE-SN'
                ) RETURNING id
            """),
            {"u": uid},
        ).scalar_one()
        for i in range(3):
            conn.execute(
                text("""
                    INSERT INTO session_waveform (session_id, ts, flow, pressure)
                    VALUES (CAST(:s AS uuid), NOW() + (:i || ' seconds')::interval, :f, :p)
                """),
                {"s": sid, "i": i, "f": 10.0 + i, "p": 8.0 + i},
            )


def _apply_2_0_migrations(engine) -> None:
    """Apply the divergent 2.0 migrations 022-032 the way run_migrations would."""
    with engine.begin() as conn:
        for filename, path in _migration_files_between(22, 999):
            already = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE filename = :f"), {"f": filename}
            ).first()
            if already:
                continue
            with open(path, encoding="utf-8") as handle:
                conn.exec_driver_sql(handle.read())
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"), {"f": filename}
            )


def test_clean_1_4_schema_verifies_ok(upstream_1_4_engine):
    """A clean upstream 1.4 schema reports no verification problems."""
    with upstream_1_4_engine.connect() as conn:
        assert verify_upstream_1_4_schema(conn) == []


def test_bridge_runs_records_marker_and_is_idempotent(upstream_1_4_engine):
    """perform_bridge records a durable marker once and is a no-op afterward."""
    with upstream_1_4_engine.connect() as conn:
        ensure_compatibility_table(conn)
        assert bridge_recorded(conn) is False
        assert perform_bridge(conn) is True
        assert bridge_recorded(conn) is True
        # Idempotent: a second call records nothing new.
        assert perform_bridge(conn) is False

        detail = conn.execute(
            text("SELECT detail FROM schema_compatibility WHERE kind = 'upstream_1_4_to_2_0'")
        ).scalar_one()
        assert "022_add_adherence_settings.sql" in detail["recognized_migrations"]
        assert "adherence_enabled" in detail["preserved_adherence_columns"]


def test_bridge_preserves_adherence_and_waveforms_then_continues(upstream_1_4_engine):
    """The bridge preserves 1.4 data and the DB continues onto the 2.0 path."""
    with upstream_1_4_engine.connect() as conn:
        ensure_compatibility_table(conn)
        assert perform_bridge(conn) is True

    # Continue the normal 2.0 migration path on top of the bridged database.
    _apply_2_0_migrations(upstream_1_4_engine)

    with upstream_1_4_engine.connect() as conn:
        # Adherence settings preserved.
        adherence_enabled = conn.execute(
            text("SELECT adherence_enabled FROM user_import_settings LIMIT 1")
        ).scalar_one()
        assert adherence_enabled is True

        # Old row-backed waveforms preserved (none deleted).
        row_waveforms = conn.execute(text("SELECT COUNT(*) FROM session_waveform")).scalar_one()
        assert row_waveforms == 3

        # Sessions/events foundations preserved & migrated forward.
        assert conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
        provenance = conn.execute(
            text("SELECT provenance_status FROM sessions LIMIT 1")
        ).scalar_one()
        assert provenance == "legacy_backfilled"

        # 2.0 chunk storage now exists and coexists with legacy rows.
        sid = conn.execute(text("SELECT id::text FROM sessions LIMIT 1")).scalar_one()
        conn.execute(
            text("""
                INSERT INTO waveform_chunks (
                    session_id, signal_name, unit, sample_rate_hz, start_time, end_time,
                    chunk_index, sample_count, encoding, payload, uncompressed_bytes,
                    compressed_bytes, adapter_id
                ) VALUES (
                    CAST(:s AS uuid), 'flow_rate', 'L/min', 25.0, NOW(), NOW() + INTERVAL '1 second',
                    0, 25, 'float32-le-zlib-v1', :payload, 100, 40, 'resmed-native-v2'
                )
            """),
            {"s": sid, "payload": b"\x00\x01\x02\x03"},
        )
        conn.commit()
        chunks = conn.execute(
            text("SELECT COUNT(*) FROM waveform_chunks WHERE session_id = CAST(:s AS uuid)"),
            {"s": sid},
        ).scalar_one()
        rows = conn.execute(
            text("SELECT COUNT(*) FROM session_waveform WHERE session_id = CAST(:s AS uuid)"),
            {"s": sid},
        ).scalar_one()
        assert chunks == 1
        assert rows == 3


def test_bridge_blocks_when_adherence_columns_missing(upstream_1_4_engine):
    """If the recorded 1.4 history lacks the actual adherence columns, refuse."""
    with upstream_1_4_engine.connect() as conn:
        # Simulate schema drift: drop the adherence columns the history claims.
        for col in sorted(UPSTREAM_1_4_ADHERENCE_COLUMNS):
            conn.exec_driver_sql(f"ALTER TABLE user_import_settings DROP COLUMN {col}")
        conn.commit()

        problems = verify_upstream_1_4_schema(conn)
        assert any("adherence" in p for p in problems)

        with pytest.raises(BridgeError) as excinfo:
            perform_bridge(conn)
        message = str(excinfo.value)
        assert "blocked" in message.lower()
        assert "No data has been modified" in message
        # Nothing was recorded.
        assert bridge_recorded(conn) is False
