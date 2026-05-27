import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text

from api.oximeter import build_legacy_viatom_fixture


def _seed_session(db, user_id: str, start: datetime | None = None) -> str:
    session_id = str(uuid.uuid4())
    start = start or datetime(2025, 1, 15, 22, 0, 0, tzinfo=UTC)
    db.execute(
        text("""
            INSERT INTO sessions (
                id, session_id, folder_date, start_datetime, pld_start_datetime,
                duration_seconds, device_serial, has_spo2, user_id
            ) VALUES (
                CAST(:sid AS uuid), :sid, :fd, :start, :start,
                28800, 'SN12345', FALSE, CAST(:uid AS uuid)
            )
        """),
        {
            "sid": session_id,
            "fd": date(2025, 1, 15),
            "start": start,
            "uid": user_id,
        },
    )
    db.commit()
    return session_id


def _fixture(started_at: datetime | None = None) -> bytes:
    return build_legacy_viatom_fixture(
        signature=0x0005,
        started_at=started_at or datetime(2025, 1, 15, 22, 0, 0),
        duration_seconds=8,
        records=[
            (97, 61, 0, 0, 0),
            (96, 62, 0, 0, 0),
        ],
    )


class TestOximeterUpload:
    def test_unauthenticated(self, client: TestClient):
        resp = client.post(
            "/upload/oximeter",
            files=[("files", ("20250115220000", _fixture(), "application/octet-stream"))],
        )

        assert resp.status_code == 401

    def test_matched_upload_imports_spo2_and_updates_summary(self, client: TestClient, auth_headers, test_user, db):
        sid = _seed_session(db, test_user["id"])

        resp = client.post(
            "/upload/oximeter",
            headers=auth_headers,
            files=[("files", ("20250115220000", _fixture(), "application/octet-stream"))],
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 1
        assert data["results"][0]["session_id"] == sid
        assert data["results"][0]["sample_count"] == 2

        rows = db.execute(
            text("SELECT spo2, pulse FROM session_spo2 WHERE session_id = CAST(:sid AS uuid) ORDER BY ts"),
            {"sid": sid},
        ).mappings().all()
        assert [row["spo2"] for row in rows] == [97, 96]
        assert [row["pulse"] for row in rows] == [61, 62]

        session = db.execute(
            text("SELECT has_spo2, avg_spo2, min_spo2 FROM sessions WHERE id = CAST(:sid AS uuid)"),
            {"sid": sid},
        ).mappings().one()
        assert session["has_spo2"] is True
        assert float(session["avg_spo2"]) == 96.5
        assert int(session["min_spo2"]) == 96

    def test_unmatched_upload_does_not_create_rows(self, client: TestClient, auth_headers, test_user, db):
        _seed_session(db, test_user["id"], datetime(2025, 1, 15, 22, 0, 0, tzinfo=UTC))
        payload = _fixture(datetime(2025, 1, 17, 22, 0, 0))

        resp = client.post(
            "/upload/oximeter",
            headers=auth_headers,
            files=[("files", ("20250117220000", payload, "application/octet-stream"))],
        )

        assert resp.status_code == 200
        assert resp.json()["unmatched"] == 1
        assert db.execute(text("SELECT COUNT(*) FROM session_spo2")).scalar_one() == 0

    def test_existing_spo2_skips_by_default_and_overwrites_when_requested(
        self,
        client: TestClient,
        auth_headers,
        test_user,
        db,
    ):
        sid = _seed_session(db, test_user["id"])

        first = client.post(
            "/upload/oximeter",
            headers=auth_headers,
            files=[("files", ("20250115220000", _fixture(), "application/octet-stream"))],
        )
        assert first.status_code == 200
        assert first.json()["imported"] == 1

        skipped = client.post(
            "/upload/oximeter",
            headers=auth_headers,
            files=[("files", ("20250115220000", _fixture(), "application/octet-stream"))],
        )
        assert skipped.status_code == 200
        assert skipped.json()["skipped"] == 1
        assert db.execute(text("SELECT COUNT(*) FROM session_spo2 WHERE session_id = CAST(:sid AS uuid)"), {"sid": sid}).scalar_one() == 2

        replacement = build_legacy_viatom_fixture(
            signature=0x0005,
            started_at=datetime(2025, 1, 15, 22, 0, 0),
            duration_seconds=4,
            records=[(95, 64, 0, 0, 0)],
        )
        overwritten = client.post(
            "/upload/oximeter",
            headers=auth_headers,
            data={"overwrite": "true"},
            files=[("files", ("20250115220000", replacement, "application/octet-stream"))],
        )

        assert overwritten.status_code == 200
        assert overwritten.json()["imported"] == 1
        rows = db.execute(
            text("SELECT spo2, pulse FROM session_spo2 WHERE session_id = CAST(:sid AS uuid) ORDER BY ts"),
            {"sid": sid},
        ).mappings().all()
        assert [(row["spo2"], row["pulse"]) for row in rows] == [(95, 64)]
