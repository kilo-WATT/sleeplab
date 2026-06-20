"""Beta-blocker coverage: background import failure status + temp-upload cleanup.

Two safety properties the beta depends on:

* When a background import (cpap-parser default or legacy/native fallback) fails,
  the durable ``import_runs`` row is marked ``failed`` with a clear, human-readable
  message that surfaces in Import History — never a raw traceback as the primary
  message — and a successful run is never clobbered by a late failure.
* The temporary upload staging directory is removed whether the import succeeds
  or fails, and cleanup is best-effort (``ignore_errors=True``) so a cleanup
  problem can never mask or suppress the real import failure.

The DB-backed tests require Postgres (``TEST_DATABASE_URL``) and skip cleanly
without it. The cleanup tests are filesystem-only and always run.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from api.routers import upload

# -- shared helpers ---------------------------------------------------------


class _SharedSession:
    """Adapt the test session so ``_fail_durable_import_run`` writes in-band.

    ``_fail_durable_import_run`` opens its own ``SessionLocal()`` and commits.
    Pointing it at the test session keeps its writes inside the test transaction
    (rolled back at teardown): ``commit`` becomes ``flush`` so same-transaction
    reads see the row, and ``close`` is a no-op so the shared connection lives on.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self):
        self._real.flush()

    def rollback(self):
        # The happy path never calls this; keep it from tearing down the shared
        # transaction if the UPDATE itself ever raised.
        pass

    def close(self):
        pass


def _seed_running_run(db, user_id: str, *, status: str = "running") -> str:
    """Insert a machine + an in-flight ``import_runs`` row, return the run id."""

    machine_id = db.execute(
        text("""
            INSERT INTO cpap_machines (
                user_id, manufacturer, adapter_id, identity_key,
                identity_confidence, support_status, validation_status
            ) VALUES (
                CAST(:uid AS uuid), 'ResMed', 'resmed-cpap-parser-v1', :identity_key,
                'strong', 'experimental', 'partial'
            )
            RETURNING id::text
        """),
        {"uid": user_id, "identity_key": f"failtest-{uuid.uuid4()}"},
    ).scalar_one()
    run_id = db.execute(
        text("""
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, importer_mode, current_stage,
                current_message, started_at
            ) VALUES (
                CAST(:uid AS uuid), CAST(:mid AS uuid), 'resmed-cpap-parser-v1',
                'uploaded_root', :fingerprint, :status, 'partial', 'cpap-parser',
                'writing_database', 'Writing sessions to the database.', NOW()
            )
            RETURNING id::text
        """),
        {
            "uid": user_id,
            "mid": machine_id,
            "fingerprint": f"failtest-{uuid.uuid4()}",
            "status": status,
        },
    ).scalar_one()
    db.flush()
    return run_id


# -- DB-backed: background failure status -----------------------------------


def test_background_failure_persists_failed_status_and_friendly_message(
    db, test_user, monkeypatch
):
    run_id = _seed_running_run(db, test_user["id"])
    monkeypatch.setattr(upload, "SessionLocal", lambda: _SharedSession(db))

    raw = "RuntimeError: decoder segfault at row 42\nTraceback (most recent call last): ..."
    upload._fail_durable_import_run(run_id, raw)

    row = db.execute(
        text("""
            SELECT status, current_stage, current_message, errors, completed_at
            FROM import_runs WHERE id = CAST(:id AS uuid)
        """),
        {"id": run_id},
    ).mappings().one()

    assert row["status"] == "failed"
    assert row["current_stage"] == "failed"
    assert row["completed_at"] is not None
    # Primary user-facing message is the friendly copy, never a raw traceback.
    assert row["current_message"] == (
        "SleepLab could not finish this import. The technical detail is saved in "
        "Import History for troubleshooting."
    )
    assert "Traceback" not in row["current_message"]

    # The structured error carries the code + friendly message; the raw detail is
    # retained only in the secondary `detail` field for troubleshooting.
    last_error = row["errors"][-1]
    assert last_error["code"] == "unexpected_import_failure"
    assert last_error["message"] == row["current_message"]
    assert "Traceback" not in last_error["message"]
    assert "segfault" in last_error["detail"]


def test_background_failure_appears_in_import_history_without_raw_message(
    db, test_user, client, auth_headers, monkeypatch
):
    run_id = _seed_running_run(db, test_user["id"])
    monkeypatch.setattr(upload, "SessionLocal", lambda: _SharedSession(db))

    upload._fail_durable_import_run(
        run_id, "boom: unhandled exception\nTraceback (most recent call last): ..."
    )

    resp = client.get("/imports/runs", headers=auth_headers)
    assert resp.status_code == 200
    runs = {row["id"]: row for row in resp.json()}
    assert run_id in runs
    row = runs[run_id]
    assert row["status"] == "failed"
    assert row["current_message"].startswith("SleepLab could not finish this import")
    assert "Traceback" not in row["current_message"]
    assert any(e["code"] == "unexpected_import_failure" for e in row["errors"])


def test_fail_durable_does_not_clobber_a_successful_run(db, test_user, monkeypatch):
    """A late failure callback must not overwrite an already-successful run."""

    run_id = _seed_running_run(db, test_user["id"], status="success")
    monkeypatch.setattr(upload, "SessionLocal", lambda: _SharedSession(db))

    upload._fail_durable_import_run(run_id, "stale late error after success")

    status = db.execute(
        text("SELECT status FROM import_runs WHERE id = CAST(:id AS uuid)"),
        {"id": run_id},
    ).scalar_one()
    assert status == "success"


# -- temp-upload cleanup (filesystem) ---------------------------------------


def _staged_dir(tmp_path: Path) -> Path:
    root = tmp_path / "cpap-upload-stage"
    (root / "DATALOG" / "20260601").mkdir(parents=True)
    (root / "STR.edf").write_bytes(b"summary")
    return root


def test_parser_import_cleans_temp_dir_on_success(tmp_path, monkeypatch):
    staged = _staged_dir(tmp_path)
    monkeypatch.setattr(
        upload, "run_cpap_parser_import", lambda **_kwargs: {"sessions": 1}
    )
    monkeypatch.setattr(upload, "_mark_import_finished", lambda *_a, **_k: None)

    upload._run_cpap_parser_import(
        str(staged), "user-1", "run-1", "machine-1", cleanup_dir=str(staged)
    )

    assert not staged.exists()


def test_parser_import_cleans_temp_dir_on_failure(tmp_path, monkeypatch):
    staged = _staged_dir(tmp_path)
    calls = []
    monkeypatch.setattr(
        upload,
        "run_cpap_parser_import",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("decoder panic")),
    )
    monkeypatch.setattr(upload, "_mark_import_finished", lambda *_a, **_k: None)
    monkeypatch.setattr(
        upload, "_fail_durable_import_run", lambda run_id, msg: calls.append((run_id, msg))
    )

    upload._run_cpap_parser_import(
        str(staged), "user-1", "run-1", "machine-1", cleanup_dir=str(staged)
    )

    assert not staged.exists()  # cleaned up even though the import failed
    assert calls == [("run-1", "decoder panic")]  # failure was recorded


def test_cleanup_is_best_effort_and_cannot_mask_import_failure(tmp_path, monkeypatch):
    staged = _staged_dir(tmp_path)
    fail_calls = []
    rmtree_kwargs = {}
    monkeypatch.setattr(
        upload,
        "run_cpap_parser_import",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("decoder panic")),
    )
    monkeypatch.setattr(upload, "_mark_import_finished", lambda *_a, **_k: None)
    monkeypatch.setattr(
        upload, "_fail_durable_import_run", lambda run_id, msg: fail_calls.append(msg)
    )

    def _record_rmtree(path, *args, **kwargs):
        rmtree_kwargs.update(kwargs)

    monkeypatch.setattr(upload.shutil, "rmtree", _record_rmtree)

    # Must not raise — the failure is already recorded, and cleanup is swallowed.
    upload._run_cpap_parser_import(
        str(staged), "user-1", "run-1", "machine-1", cleanup_dir=str(staged)
    )

    assert fail_calls == ["decoder panic"]
    assert rmtree_kwargs.get("ignore_errors") is True


def test_legacy_subprocess_import_cleans_temp_dir_on_success(tmp_path, monkeypatch):
    staged = _staged_dir(tmp_path)
    monkeypatch.setattr(
        upload.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(upload, "_mark_import_finished", lambda *_a, **_k: None)

    upload._run_import(str(staged), "user-1", None, cleanup_dir=str(staged))

    assert not staged.exists()


def test_legacy_subprocess_import_cleans_temp_dir_on_failure(tmp_path, monkeypatch):
    staged = _staged_dir(tmp_path)
    fail_calls = []
    monkeypatch.setattr(
        upload.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    monkeypatch.setattr(upload, "_mark_import_finished", lambda *_a, **_k: None)
    monkeypatch.setattr(
        upload, "_fail_durable_import_run", lambda run_id, msg: fail_calls.append((run_id, msg))
    )

    upload._run_import(
        str(staged), "user-1", None, cleanup_dir=str(staged), import_run_id="run-9"
    )

    assert not staged.exists()
    assert len(fail_calls) == 1
    assert fail_calls[0][0] == "run-9"
