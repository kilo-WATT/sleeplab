"""API tests for the GUI-facing SleepLab 2.0 source inspection flow."""

import shutil
from io import BytesIO

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import text

from api.routers import upload
from api.routers.upload import (
    UPLOAD_SESSIONS,
    StartSourceUploadRequest,
    discard_source_upload,
    finish_source_import,
    inspect_uploaded_source,
    start_source_upload,
    upload_source_batch,
)


def _file(path: str, content: bytes = b"fixture") -> UploadFile:
    return UploadFile(filename=path, file=BytesIO(content))


def _start(user_id: str, root_name: str = "CPAP-SD") -> str:
    return start_source_upload(
        StartSourceUploadRequest(root_name=root_name),
        current_user={"id": user_id},
    )["upload_id"]


def _cleanup(upload_id: str) -> None:
    session = UPLOAD_SESSIONS.pop(upload_id, None)
    if session:
        shutil.rmtree(session.temp_root, ignore_errors=True)


def test_source_inspection_detects_resmed_from_card_root():
    upload_id = _start("resmed-user", "RESMED-SD")
    try:
        upload_source_batch(
            upload_id,
            [
                _file("STR.edf", b""),
                _file("Identification.json", b'{"FlowGenerator": {"SerialNumber": "TEST-ONLY"}}'),
                _file("DATALOG/20260601/20260601_220000_PLD.edf", b""),
            ],
            current_user={"id": "resmed-user"},
        )

        result = inspect_uploaded_source(upload_id, current_user={"id": "resmed-user"})

        assert result["source_root"] == "RESMED-SD"
        assert result["inspection"]["matched"] is True
        assert result["inspection"]["devices"][0]["adapter_id"] == "resmed-native-v2"
        assert result["source_manifest"]["file_count"] == 3
        assert result["executable"] is True
    finally:
        _cleanup(upload_id)


def test_source_inspection_detects_prs1_and_blocks_unconnected_importer():
    upload_id = _start("prs1-user", "PHILIPS-SD")
    try:
        upload_source_batch(
            upload_id,
            [
                _file(
                    "P-Series/P012345/PROP.TXT",
                    b"SerialNumber=TEST-PRS1\nModelNumber=560P\nSoftwareVersion=1.2\n",
                ),
                _file("P-Series/P012345/p0/000000001.000", b"fixture"),
            ],
            current_user={"id": "prs1-user"},
        )

        result = inspect_uploaded_source(upload_id, current_user={"id": "prs1-user"})

        assert result["inspection"]["matched"] is True
        assert result["inspection"]["devices"][0]["adapter_id"] == "philips-prs1-v2"
        assert result["inspection"]["devices"][0]["identity"]["model_number"] == "560P"
        assert result["executable"] is False
        with pytest.raises(HTTPException, match="does not implement execution"):
            finish_source_import(
                upload_id,
                BackgroundTasks(),
                current_user={"id": "prs1-user"},
            )
    finally:
        _cleanup(upload_id)


def test_source_import_requires_reinspection_after_staged_files_change():
    upload_id = _start("changed-user", "RESMED-SD")
    try:
        upload_source_batch(
            upload_id,
            [
                _file("STR.edf", b"summary"),
                _file("DATALOG/20260601/20260601_220000_PLD.edf", b"session"),
            ],
            current_user={"id": "changed-user"},
        )
        inspect_uploaded_source(upload_id, current_user={"id": "changed-user"})
        upload_source_batch(
            upload_id,
            [_file("DATALOG/20260601/20260601_220000_EVE.edf", b"events")],
            current_user={"id": "changed-user"},
        )

        with pytest.raises(HTTPException, match="changed after inspection"):
            finish_source_import(
                upload_id,
                BackgroundTasks(),
                current_user={"id": "changed-user"},
            )
    finally:
        _cleanup(upload_id)


def test_source_upload_rejects_parent_traversal():
    upload_id = _start("traversal-user")
    try:
        with pytest.raises(HTTPException, match="Invalid file path"):
            upload_source_batch(
                upload_id,
                [_file("../outside.txt")],
                current_user={"id": "traversal-user"},
            )
    finally:
        _cleanup(upload_id)


def test_source_upload_can_be_discarded_after_inspection():
    upload_id = _start("discard-user")

    result = discard_source_upload(upload_id, current_user={"id": "discard-user"})

    assert result == {"status": "discarded"}
    assert upload_id not in UPLOAD_SESSIONS


@pytest.mark.parametrize(
    ("flag_value", "expected_task"),
    [
        ("1", upload._run_cpap_parser_import),
        ("0", upload._run_import),
    ],
)
def test_source_finish_routes_by_cpap_parser_flag(monkeypatch, flag_value, expected_task):
    upload_id = _start("route-user", "RESMED-SD")
    temp_root = UPLOAD_SESSIONS[upload_id].temp_root
    try:
        upload_source_batch(
            upload_id,
            [
                _file("STR.edf", b"summary"),
                _file("DATALOG/20260601/20260601_220000_PLD.edf", b"session"),
            ],
            current_user={"id": "route-user"},
        )
        inspect_uploaded_source(upload_id, current_user={"id": "route-user"})
        monkeypatch.setenv("SLEEPLAB_USE_CPAP_PARSER", flag_value)
        monkeypatch.setattr(upload, "cpap_parser_runtime_available", lambda: True)
        monkeypatch.setattr(upload, "resmed_backend_conflict", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(
            upload,
            "create_import_run",
            lambda *_args, **_kwargs: ("run-id", "machine-id"),
        )
        tasks = BackgroundTasks()

        result = finish_source_import(
            upload_id,
            tasks,
            current_user={"id": "route-user"},
            db=object(),
        )

        assert result["import_run_id"] == "run-id"
        assert len(tasks.tasks) == 1
        assert tasks.tasks[0].func is expected_task
    finally:
        UPLOAD_SESSIONS.pop(upload_id, None)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_source_finish_rejects_missing_parser_runtime_before_creating_run(monkeypatch):
    upload_id = _start("missing-runtime-user", "RESMED-SD")
    try:
        upload_source_batch(
            upload_id,
            [
                _file("STR.edf", b"summary"),
                _file("DATALOG/20260601/20260601_220000_PLD.edf", b"session"),
            ],
            current_user={"id": "missing-runtime-user"},
        )
        inspect_uploaded_source(upload_id, current_user={"id": "missing-runtime-user"})
        monkeypatch.setenv("SLEEPLAB_USE_CPAP_PARSER", "1")
        monkeypatch.setattr(upload, "cpap_parser_runtime_available", lambda: False)
        monkeypatch.setattr(
            upload,
            "create_import_run",
            lambda *_args, **_kwargs: pytest.fail("missing runtime must fail before run creation"),
        )

        with pytest.raises(HTTPException) as exc_info:
            finish_source_import(
                upload_id,
                BackgroundTasks(),
                current_user={"id": "missing-runtime-user"},
                db=object(),
            )

        assert exc_info.value.status_code == 503
        assert "uv sync --extra parser" in exc_info.value.detail
        assert upload_id in UPLOAD_SESSIONS
    finally:
        _cleanup(upload_id)


def test_datalog_finish_is_disabled_in_parser_mode(monkeypatch):
    upload_id = _start("datalog-user", "DATALOG")
    try:
        UPLOAD_SESSIONS[upload_id].file_count = 1
        monkeypatch.setenv("SLEEPLAB_USE_CPAP_PARSER", "1")

        with pytest.raises(HTTPException) as exc_info:
            upload.finish_datalog_upload(
                upload_id,
                BackgroundTasks(),
                current_user={"id": "datalog-user"},
            )

        assert exc_info.value.status_code == 409
        assert "legacy-only" in exc_info.value.detail
    finally:
        _cleanup(upload_id)


def test_source_finish_rejects_mixed_legacy_history(db, test_user, monkeypatch):
    user_id = test_user["id"]
    db.execute(
        text("""
            INSERT INTO sessions (
                session_id, folder_date, start_datetime, pld_start_datetime, duration_seconds,
                device_serial, manufacturer, user_id, provenance_status
            ) VALUES (
                'legacy-beta-policy', DATE '2026-06-01', NOW(), NOW(), 3600,
                'RESET-POLICY-TEST', 'ResMed', CAST(:uid AS uuid), 'native_resmed_partial'
            )
        """),
        {"uid": user_id},
    )
    db.commit()

    upload_id = _start(user_id, "RESMED-SD")
    temp_root = UPLOAD_SESSIONS[upload_id].temp_root
    try:
        upload_source_batch(
            upload_id,
            [
                _file("STR.edf", b"summary"),
                _file(
                    "Identification.json",
                    b'{"FlowGenerator":{"SerialNumber":"RESET-POLICY-TEST"}}',
                ),
                _file("DATALOG/20260601/20260601_220000_PLD.edf", b"session"),
            ],
            current_user={"id": user_id},
        )
        inspect_uploaded_source(upload_id, current_user={"id": user_id})
        monkeypatch.setenv("SLEEPLAB_USE_CPAP_PARSER", "1")
        monkeypatch.setattr(upload, "cpap_parser_runtime_available", lambda: True)
        monkeypatch.setattr(
            upload,
            "create_import_run",
            lambda *_args, **_kwargs: pytest.fail("mixed history must fail before run creation"),
        )

        with pytest.raises(HTTPException) as exc_info:
            finish_source_import(
                upload_id,
                BackgroundTasks(),
                current_user={"id": user_id},
                db=db,
            )

        assert exc_info.value.status_code == 409
        assert "delete existing imported session data" in exc_info.value.detail
    finally:
        UPLOAD_SESSIONS.pop(upload_id, None)
        shutil.rmtree(temp_root, ignore_errors=True)
