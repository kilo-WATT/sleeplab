"""API tests for the GUI-facing SleepLab 2.0 source inspection flow."""

import shutil
from io import BytesIO

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

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
        assert result["matched"] is True
        assert result["devices"][0]["adapter_id"] == "resmed-native-v2"
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

        assert result["matched"] is True
        assert result["devices"][0]["adapter_id"] == "philips-prs1-v2"
        assert result["devices"][0]["identity"]["model_number"] == "560P"
        with pytest.raises(HTTPException, match="full importer is not connected"):
            finish_source_import(
                upload_id,
                BackgroundTasks(),
                current_user={"id": "prs1-user"},
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
