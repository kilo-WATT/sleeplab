import uuid

from fastapi.testclient import TestClient


class TestListEquipment:
    """Test suite for list equipment."""

    def test_empty(self, client: TestClient, auth_headers):
        """Test empty."""
        resp = client.get("/equipment/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_after_create(self, client: TestClient, auth_headers):
        """Test after create."""
        client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "cushion",
                "start_date": "2025-06-01",
                "brand": "ResMed",
                "model": "P10",
            },
        )
        resp = client.get("/equipment/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(e["brand"] == "ResMed" for e in data)

    def test_unauthenticated(self, client: TestClient):
        """Test unauthenticated."""
        resp = client.get("/equipment/")
        assert resp.status_code == 401


class TestCreateEquipment:
    """Test suite for create equipment."""

    def test_create_cushion(self, client: TestClient, auth_headers):
        """Test create cushion."""
        resp = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "cushion",
                "start_date": "2025-06-01",
                "mask_category": "Nasal Pillows",
                "brand": "ResMed",
                "model": "P10",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["equipment_type"] == "cushion"
        assert data["brand"] == "ResMed"
        assert data["id"] is not None
        assert data["days_in_use"] is not None

    def test_create_tubing(self, client: TestClient, auth_headers):
        """Test create tubing."""
        resp = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "tubing",
                "start_date": "2025-06-15",
                "replacement_days": 90,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["equipment_type"] == "tubing"
        assert data["replacement_days"] == 90

    def test_invalid_type(self, client: TestClient, auth_headers):
        """Test invalid type."""
        resp = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "invalid_type",
                "start_date": "2025-06-01",
            },
        )
        assert resp.status_code == 422

    def test_unauthenticated(self, client: TestClient):
        """Test unauthenticated."""
        resp = client.post(
            "/equipment/",
            json={
                "equipment_type": "cushion",
                "start_date": "2025-06-01",
            },
        )
        assert resp.status_code == 401


class TestUpdateEquipment:
    """Test suite for update equipment."""

    def test_update_start_date(self, client: TestClient, auth_headers):
        """Test update start date."""
        create = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "filter",
                "start_date": "2025-01-01",
            },
        )
        eq_id = create.json()["id"]
        resp = client.put(
            f"/equipment/{eq_id}",
            headers=auth_headers,
            json={
                "start_date": "2025-03-01",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["start_date"] == "2025-03-01"

    def test_nonexistent(self, client: TestClient, auth_headers):
        """Test nonexistent."""
        fake_id = str(uuid.uuid4())
        resp = client.put(
            f"/equipment/{fake_id}",
            headers=auth_headers,
            json={
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 404

    def test_unauthenticated(self, client: TestClient):
        """Test unauthenticated."""
        resp = client.put(
            "/equipment/some-id",
            json={
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 401


class TestDeleteEquipment:
    """Test suite for delete equipment."""

    def test_delete_existing(self, client: TestClient, auth_headers):
        """Test delete existing."""
        create = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "headgear",
                "start_date": "2025-01-01",
            },
        )
        eq_id = create.json()["id"]
        resp = client.delete(f"/equipment/{eq_id}", headers=auth_headers)
        assert resp.status_code == 204

    def test_nonexistent(self, client: TestClient, auth_headers):
        """Test nonexistent."""
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/equipment/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    def test_unauthenticated(self, client: TestClient):
        """Test unauthenticated."""
        resp = client.delete("/equipment/some-id")
        assert resp.status_code == 401


class TestDefaultEquipment:
    """Test suite for the default/'in use' equipment selector."""

    def _create_cushion(self, client, auth_headers, *, model, start_date):
        resp = client.post(
            "/equipment/",
            headers=auth_headers,
            json={"equipment_type": "cushion", "start_date": start_date, "brand": "ResMed", "model": model},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def test_new_equipment_is_not_default(self, client: TestClient, auth_headers):
        eq_id = self._create_cushion(client, auth_headers, model="P30i", start_date="2025-01-01")
        item = next(e for e in client.get("/equipment/", headers=auth_headers).json() if e["id"] == eq_id)
        assert item["is_default"] is False

    def test_set_default_marks_one_and_clears_siblings(self, client: TestClient, auth_headers):
        older = self._create_cushion(client, auth_headers, model="P30i", start_date="2025-01-01")
        newer = self._create_cushion(client, auth_headers, model="N30i", start_date="2025-03-01")

        first = client.put(f"/equipment/{older}/default", headers=auth_headers)
        assert first.status_code == 200
        assert first.json()["is_default"] is True

        # Switching the default must clear the previous one (unique per type).
        second = client.put(f"/equipment/{newer}/default", headers=auth_headers)
        assert second.status_code == 200

        by_id = {e["id"]: e for e in client.get("/equipment/", headers=auth_headers).json()}
        assert by_id[newer]["is_default"] is True
        assert by_id[older]["is_default"] is False

    def test_inferred_prefers_default_over_more_recent(self, client: TestClient, auth_headers):
        """A chosen default wins over a newer-by-start-date item of the same type."""
        older = self._create_cushion(client, auth_headers, model="P30i", start_date="2025-01-01")
        self._create_cushion(client, auth_headers, model="N30i", start_date="2025-03-01")

        # Without a default, inference returns the most recent (N30i).
        baseline = client.get("/equipment/inferred?ref_date=2025-06-01", headers=auth_headers).json()
        assert baseline["cushion"]["model"] == "N30i"

        # After choosing the older one as default, inference returns it instead.
        client.put(f"/equipment/{older}/default", headers=auth_headers)
        chosen = client.get("/equipment/inferred?ref_date=2025-06-01", headers=auth_headers).json()
        assert chosen["cushion"]["model"] == "P30i"
        assert chosen["cushion"]["is_default"] is True

    def test_set_default_nonexistent(self, client: TestClient, auth_headers):
        resp = client.put(f"/equipment/{uuid.uuid4()}/default", headers=auth_headers)
        assert resp.status_code == 404

    def test_set_default_unauthenticated(self, client: TestClient):
        resp = client.put("/equipment/some-id/default")
        assert resp.status_code == 401


class TestInferredEquipment:
    """Test suite for inferred equipment."""

    def test_empty(self, client: TestClient, auth_headers):
        """Test empty."""
        resp = client.get("/equipment/inferred", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        for eq_type in ("cushion", "headgear", "tubing", "humidifier_chamber", "filter"):
            assert data.get(eq_type) is None

    def test_with_equipment(self, client: TestClient, auth_headers):
        """Test with equipment."""
        post_resp = client.post(
            "/equipment/",
            headers=auth_headers,
            json={
                "equipment_type": "cushion",
                "start_date": "2025-01-01",
                "brand": "ResMed",
            },
        )
        assert post_resp.status_code == 201, f"create failed: {post_resp.text}"
        resp = client.get("/equipment/inferred?ref_date=2025-06-01", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("cushion") is not None
        assert data["cushion"]["brand"] == "ResMed"
        assert data.get("headgear") is None

    def test_unauthenticated(self, client: TestClient):
        """Test unauthenticated."""
        resp = client.get("/equipment/inferred")
        assert resp.status_code == 401
