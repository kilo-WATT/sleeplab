"""Regression coverage for durable import progress metadata."""

from pathlib import Path


def test_import_progress_migration_is_repeat_safe_and_constrained():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "029_add_import_progress.sql"
    ).read_text()

    assert "ADD COLUMN IF NOT EXISTS current_stage" in sql
    assert "ADD COLUMN IF NOT EXISTS current_message" in sql
    assert "ADD COLUMN IF NOT EXISTS sessions_processed" in sql
    assert "ck_import_runs_progress_counts" in sql
