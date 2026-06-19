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


def test_import_result_summary_migration_is_backward_compatible():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "032_add_import_result_summary.sql"
    ).read_text()

    assert "ADD COLUMN IF NOT EXISTS importer_mode" in sql
    assert "ADD COLUMN IF NOT EXISTS sessions_added_count" in sql
    assert "ADD COLUMN IF NOT EXISTS sessions_skipped_count" in sql
    assert "ADD COLUMN IF NOT EXISTS waveform_chunk_count" in sql
    assert "importer_mode IS NULL" in sql
