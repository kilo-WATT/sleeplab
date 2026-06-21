from pathlib import Path

from sqlalchemy import text

from api.database import engine
from api.main import app  # noqa: F401 — imported for uvicorn
from api.upgrade_guard import evaluate_startup


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

        # Preservation-first guard: stop before applying any 2.0 migration if the
        # database already recorded upstream 1.4 migrations whose numbers collide
        # with the 2.0 history. Blocking here keeps an unrecoverable schema mixup
        # from ever being written. Fresh installs, valid 2.0 betas, and known-safe
        # 1.3.x databases pass through untouched.
        applied_filenames = {
            row[0]
            for row in conn.execute(text("SELECT filename FROM schema_migrations")).all()
        }
        decision = evaluate_startup(applied_filenames)
        if decision.blocked:
            raise RuntimeError(decision.message)

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
