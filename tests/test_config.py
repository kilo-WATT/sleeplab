from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestGetConfig:
    """Test suite for get config."""

    def test_defaults_to_utc(self, monkeypatch):
        """Test defaults to utc."""
        monkeypatch.delenv("DISPLAY_TZ", raising=False)
        monkeypatch.delenv("MACHINE_TZ", raising=False)
        resp = client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_tz"] == "UTC"
        assert data["machine_tz"] == "UTC"
        assert data["resmed_import_backend"] == "legacy"
        assert isinstance(data["cpap_parser_available"], bool)
        assert data["resmed_import_ready"] is True
        assert data["datalog_import_backend"] == "legacy"
        assert data["datalog_import_available"] is True
        assert data["cpap_parser_oximetry_supported"] is False
        assert data["cpap_parser_source_provenance"] == "manifest-level-partial"

    def test_reflects_env_vars(self, monkeypatch):
        """Test reflects env vars."""
        monkeypatch.setenv("DISPLAY_TZ", "America/New_York")
        monkeypatch.setenv("MACHINE_TZ", "America/Chicago")
        resp = client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_tz"] == "America/New_York"
        assert data["machine_tz"] == "America/Chicago"

    def test_reports_selected_resmed_backend(self, monkeypatch):
        monkeypatch.setenv("SLEEPLAB_USE_CPAP_PARSER", "1")

        resp = client.get("/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["resmed_import_backend"] == "cpap-parser"
        assert data["datalog_import_available"] is False
        assert data["resmed_import_ready"] is data["cpap_parser_available"]

    def test_no_auth_required(self):
        """Test no auth required."""
        resp = client.get("/config")
        assert resp.status_code == 200

    def test_response_shape(self):
        """Test response shape."""
        resp = client.get("/config")
        data = resp.json()
        assert set(data.keys()) == {
            "display_tz",
            "machine_tz",
            "resmed_import_backend",
            "cpap_parser_available",
            "resmed_import_ready",
            "datalog_import_backend",
            "datalog_import_available",
            "cpap_parser_oximetry_supported",
            "cpap_parser_source_provenance",
        }
        assert isinstance(data["display_tz"], str)
        assert isinstance(data["machine_tz"], str)
        assert data["resmed_import_backend"] in {"legacy", "cpap-parser"}
        assert isinstance(data["cpap_parser_available"], bool)
