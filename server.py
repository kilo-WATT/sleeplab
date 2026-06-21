from pathlib import Path

from sqlalchemy import text

from api.database import engine
from api.main import app  # noqa: F401 — imported for uvicorn
from api.upgrade_bridge import bridge_recorded, ensure_compatibility_table, perform_bridge
from api.upgrade_guard import ACTION_BRIDGE, evaluate_startup


def run_migrations() -> None:
    root = Path(__file__).parent
    schema_file = root / "schema.sql"
    migrations_dir = root / "migrations"
    sql_files = [schema_file] + sorted(migrations_dir.glob("*.sql"))
    baseline_migration_prefixes = {f"{index:03d}" for index in range(1, 6)}

    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        )
        conn.commit()

        applied_count = conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one()
        has_existing_schema = conn.execute(text("SELECT to_regclass('public.users')")).scalar_one() is not None

        if applied_count == 0 and has_existing_schema:
            for path in sql_files:
                if path.name == schema_file.name or path.name[:3] in baseline_migration_prefixes:
                    conn.execute(
                        text("INSERT INTO schema_migrations (filename) VALUES (:f) ON CONFLICT DO NOTHING"),
                        {"f": path.name},
                    )
            conn.commit()

        # Durable, inspectable record of any 1.x -> 2.x compatibility bridging.
        ensure_compatibility_table(conn)

        # Preservation-first guard. A clean upstream 1.4 database is reconciled by
        # the bridge and carried forward; a partial/mixed 1.4 state is stopped
        # before any migration write. Fresh installs, valid 2.0 betas, known-safe
        # 1.3.x databases, and already-bridged databases pass through untouched.
        applied_filenames = {
            row[0]
            for row in conn.execute(text("SELECT filename FROM schema_migrations")).all()
        }
        decision = evaluate_startup(
            applied_filenames, bridge_recorded=bridge_recorded(conn)
        )
        if decision.blocked:
            raise RuntimeError(decision.message)
        if decision.action == ACTION_BRIDGE:
            # Verifies the live schema and records the bridge marker before the
            # normal apply loop. Raises a clear error (no write) if unsafe.
            print("[migrations] bridging upstream 1.4 database to SleepLab 2.0")
            perform_bridge(conn)
            print("[migrations] upstream 1.4 -> 2.0 bridge recorded")

        for path in sql_files:
            filename = path.name
            already_applied = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE filename = :f"),
                {"f": filename},
            ).fetchone()

            if already_applied:
                continue

            print(f"[migrations] applying {filename}")
            conn.exec_driver_sql(path.read_text())
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"),
                {"f": filename},
            )
            conn.commit()
            print(f"[migrations] applied {filename}")


run_migrations()
