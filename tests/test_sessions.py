import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text


def _seed_session(db, user_id: str, folder_date: date | None = None):
    """Test  seed session."""
    if folder_date is None:
        folder_date = date.today()
    session_id = str(uuid.uuid4())
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
            "fd": folder_date,
            "start": datetime(2025, 1, 15, 22, 0, 0, tzinfo=UTC),
            "uid": user_id,
        },
    )
    db.commit()
    return session_id


class TestListSessions:
    """Test suite for list sessions."""

    def test_list_authenticated(self, client: TestClient, auth_headers, test_user, db):
        """Test list authenticated."""
        _seed_session(db, test_user["id"])
        resp = client.get("/sessions/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_list_unauthenticated(self, client: TestClient):
        """Test list unauthenticated."""
        resp = client.get("/sessions/")
        assert resp.status_code == 401


class TestGetSession:
    """Test suite for get session."""

    def test_get_detail(self, client: TestClient, auth_headers, test_user, db):
        """Test get detail."""
        sid = _seed_session(db, test_user["id"])
        resp = client.get(f"/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sid
        assert data["session_id"] == sid
        assert data["duration_seconds"] == 28800
        assert data.get("therapy_mode") is None
        assert data.get("mask_type") is None
        assert data.get("humidity_level") is None
        assert data.get("temperature_c") is None
        assert data.get("machine_tz") is None

    def test_get_nonexistent(self, client: TestClient, auth_headers):
        """Test get nonexistent."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = client.get(f"/sessions/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404
