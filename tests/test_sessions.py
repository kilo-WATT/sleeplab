import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text

from importer.import_sessions import derive_machine_settings


def _seed_session(db, user_id: str, folder_date: date | None = None):
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
    def test_list_authenticated(self, client: TestClient, auth_headers, test_user, db):
        _seed_session(db, test_user["id"])
        resp = client.get("/sessions/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_list_unauthenticated(self, client: TestClient):
        resp = client.get("/sessions/")
        assert resp.status_code == 401


class TestGetSession:
    def test_get_detail(self, client: TestClient, auth_headers, test_user, db):
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

    def test_settings_history_detects_changes(self, client: TestClient, auth_headers, test_user, db):
        first_sid = _seed_session(db, test_user["id"], date(2025, 1, 15))
        second_sid = _seed_session(db, test_user["id"], date(2025, 1, 16))
        db.execute(
            text("""
                UPDATE sessions
                SET therapy_mode = 'apap',
                    pressure_min = 7,
                    pressure_max = 12,
                    epr_setting = '2',
                    ramp_setting = 'Auto',
                    humidity_level = 3
                WHERE id = CAST(:sid AS uuid)
            """),
            {"sid": first_sid},
        )
        db.execute(
            text("""
                UPDATE sessions
                SET therapy_mode = 'apap',
                    pressure_min = 8,
                    pressure_max = 13,
                    epr_setting = '3',
                    ramp_setting = 'Auto',
                    humidity_level = 3
                WHERE id = CAST(:sid AS uuid)
            """),
            {"sid": second_sid},
        )
        db.commit()

        resp = client.get("/sessions/settings/history?days=1000", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["history"]) == 2
        assert len(data["changes"]) == 1
        changed = {field["field"] for field in data["changes"][0]["changed_fields"]}
        assert {"pressure_min", "pressure_max", "epr_setting"} <= changed

    def test_derive_machine_settings_from_pld_channels(self):
        settings = derive_machine_settings({
            "Press.2s": [7.0, 7.2, 9.5, 10.0],
            "EprPress.2s": [5.0, 5.2, 7.5, 8.0],
        })
        assert settings["therapy_mode"] == "apap"
        assert settings["pressure_min"] is None
        assert settings["pressure_max"] is None
        assert settings["epr_setting"] == "2"

    def test_derive_fixed_pressure_for_cpap_channels(self):
        settings = derive_machine_settings({
            "Press.2s": [8.0, 8.1, 8.0, 8.0],
            "EprPress.2s": [6.0, 6.1, 6.0, 6.0],
        })
        assert settings["therapy_mode"] == "cpap"
        assert settings["pressure_min"] == 8.0
        assert settings["pressure_max"] == 8.0
        assert settings["epr_setting"] == "2"

    def test_get_nonexistent(self, client: TestClient, auth_headers):
        fake_id = "00000000-0000-0000-0000-000000000000"
        resp = client.get(f"/sessions/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404
