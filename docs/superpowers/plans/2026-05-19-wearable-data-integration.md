# Wearable Data Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch HR, SpO₂, and sleep stage data in real-time from self-hosted wearable APIs (open-wearables, mirobody) and overlay it on the session detail page and dashboard trend chart.

**Architecture:** Sync `httpx`-based adapters in `api/wearable/` are called at request time by two new endpoints (`GET /wearable/data`, `GET /wearable/summary`). No wearable data is stored in the SleepLab database. Frontend fetches wearable data non-blocking and overlays it on existing CPAP charts.

**Tech Stack:** Python (FastAPI, httpx, SQLAlchemy), TypeScript (React, Recharts), pytest, podman compose for integration testing.

---

> **⚠️ Pending PR conflicts:** This branch starts from `main` before PRs #25–#37 merge.
> When each merges, rebase this branch and resolve conflicts. Key conflicts:
> - `SessionDetail.tsx` — PR #25 adds `SpO2Chart`; our version supersedes it (ours is a superset)
> - `import_settings.py` — PRs #26–#29, #33, #37 all add fields; append our three fields cleanly
> - `Settings.tsx` — multiple PRs add cards; ours goes at the end
> - `client.ts` — multiple PRs add fields; append ours
> - `api/main.py` — PRs #29, #36 modify it; just add our router include
> - **Migration number:** Current last migration is `007`. Rename `008_add_wearable_settings.sql` to the next available number after all pending PRs merge. Check `ls migrations/` before opening the PR.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `api/wearable/__init__.py` | Package marker |
| Create | `api/wearable/base.py` | `WearableAdapter` ABC, `Sample`, `StageSample`, `WearablePayload` dataclasses |
| Create | `api/wearable/open_wearables.py` | `OpenWearablesAdapter` — fetches from open-wearables HTTP API |
| Create | `api/wearable/mirobody.py` | `MirobodyAdapter` — fetches from mirobody HTTP API |
| Create | `api/wearable/registry.py` | `ADAPTERS` dict + `get_adapter()` factory |
| Create | `api/routers/wearable.py` | `GET /wearable/data` and `GET /wearable/summary` endpoints |
| Modify | `api/main.py` | Register wearable router |
| Modify | `api/routers/import_settings.py` | Add `wearable_provider`, `wearable_base_url`, `wearable_api_key` to models and endpoints |
| Create | `migrations/008_add_wearable_settings.sql` | Add three wearable columns to `user_import_settings` |
| Create | `tests/test_wearable.py` | All backend wearable tests |
| Modify | `frontend/src/api/client.ts` | Add `WearableData`, `WearableDailySummary` types + two API methods + `SpO2Response` |
| Modify | `frontend/src/pages/Settings.tsx` | "Wearable Data" settings card |
| Create | `frontend/src/components/SpO2Chart.tsx` | CPAP + wearable HR/SpO₂ chart with color-differentiated overlay |
| Create | `frontend/src/components/WearableSleepStageChart.tsx` | Sleep stage hypnogram (step chart) |
| Modify | `frontend/src/pages/SessionDetail.tsx` | Fetch SpO₂ + wearable data non-blocking; render two new charts |
| Create | `frontend/src/components/WearableSleepSummaryChart.tsx` | Stacked bar chart: hours per sleep stage per night |
| Modify | `frontend/src/pages/Dashboard.tsx` | Fetch wearable summary; render `WearableSleepSummaryChart` |

---

## Task 1: Database Migration

**Files:**
- Create: `migrations/008_add_wearable_settings.sql`

- [ ] **Step 1: Write the migration**

```sql
-- migrations/008_add_wearable_settings.sql
-- NOTE: Rename this file to the next available number after all pending PRs
-- (#27-#29, #37) merge and you inspect migrations/ for the highest existing number.
BEGIN;
ALTER TABLE user_import_settings
    ADD COLUMN IF NOT EXISTS wearable_provider  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_base_url  TEXT,
    ADD COLUMN IF NOT EXISTS wearable_api_key   TEXT;
COMMIT;
```

- [ ] **Step 2: Commit**

```bash
git add migrations/008_add_wearable_settings.sql
git commit -m "feat(wearable): add wearable_provider/base_url/api_key columns to user_import_settings"
```

---

## Task 2: Adapter Base Module

**Files:**
- Create: `api/wearable/__init__.py`
- Create: `api/wearable/base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wearable.py
from api.wearable.base import WearablePayload, Sample, StageSample

def test_wearable_payload_is_empty_when_default():
    assert WearablePayload().is_empty()

def test_wearable_payload_not_empty_with_hr():
    p = WearablePayload(hr=[Sample(timestamp="2025-01-01T02:00:00Z", value=62.0)])
    assert not p.is_empty()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/camden/ZedProjects/sleeplab
uv run pytest tests/test_wearable.py -v
```
Expected: `ModuleNotFoundError: No module named 'api.wearable'`

- [ ] **Step 3: Create the package and base module**

```python
# api/wearable/__init__.py
```

```python
# api/wearable/base.py
"""Abstract base for wearable data source adapters.

All adapters in this package return data on demand at request time.
No data is stored in the SleepLab database.

Docstring style: Google.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Sample:
    """A single timestamped scalar measurement.

    Attributes:
        timestamp: ISO-8601 UTC string, e.g. ``"2025-01-15T02:34:00Z"``.
        value: Numeric value — bpm for HR, percent for SpO₂.
    """

    timestamp: str
    value: float


@dataclass
class StageSample:
    """A single timestamped sleep stage epoch.

    Attributes:
        timestamp: ISO-8601 UTC string marking the start of the epoch.
        stage: Normalised stage code: ``1`` awake, ``2`` light, ``3`` deep, ``4`` REM.
    """

    timestamp: str
    stage: int


@dataclass
class WearablePayload:
    """Normalised wearable data for a single night.

    Attributes:
        hr: Heart rate samples (bpm).
        spo2: Blood oxygen saturation samples (%).
        stages: Sleep stage epochs in chronological order.
    """

    hr: list[Sample] = field(default_factory=list)
    spo2: list[Sample] = field(default_factory=list)
    stages: list[StageSample] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return True when all three series are empty."""
        return not self.hr and not self.spo2 and not self.stages


class WearableAdapter(ABC):
    """Abstract base for wearable data source adapters.

    Subclass this, implement ``fetch``, then register in
    ``api/wearable/registry.py`` under a unique provider name string.

    Example::

        class MyAdapter(WearableAdapter):
            def __init__(self, base_url: str, api_key: str) -> None:
                self._base_url = base_url
                self._api_key = api_key

            def fetch(self, user_id: str, target_date: date) -> WearablePayload:
                ...

        # registry.py
        ADAPTERS["my-provider"] = MyAdapter
    """

    @abstractmethod
    def fetch(self, user_id: str, target_date: date) -> WearablePayload:
        """Fetch wearable data for one night.

        Args:
            user_id: SleepLab user UUID, used to scope to the correct account.
            target_date: Local calendar date of the sleep session. Adapters
                should query a window that captures midnight-spanning sessions.

        Returns:
            Normalised ``WearablePayload``. Return empty ``WearablePayload()``
            when no data exists — never raise for missing data.

        Raises:
            httpx.HTTPStatusError: On 401/403 from the upstream API so callers
                can surface credential errors to the user.
        """
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/wearable/__init__.py api/wearable/base.py tests/test_wearable.py
git commit -m "feat(wearable): add WearableAdapter ABC and payload dataclasses"
```

---

## Task 3: OpenWearables Adapter

**Files:**
- Create: `api/wearable/open_wearables.py`
- Modify: `tests/test_wearable.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wearable.py`:

```python
from unittest.mock import MagicMock, patch
from datetime import date as date_type
from api.wearable.open_wearables import OpenWearablesAdapter, _STAGE_MAP


def test_stage_map_covers_all_expected_labels():
    expected = {"awake", "light", "nrem1", "nrem2", "deep", "nrem3", "nrem4", "rem"}
    assert expected == set(_STAGE_MAP.keys())


def test_stage_map_normalises_correctly():
    assert _STAGE_MAP["awake"] == 1
    assert _STAGE_MAP["light"] == 2
    assert _STAGE_MAP["nrem1"] == 2
    assert _STAGE_MAP["nrem2"] == 2
    assert _STAGE_MAP["deep"] == 3
    assert _STAGE_MAP["nrem3"] == 3
    assert _STAGE_MAP["nrem4"] == 3
    assert _STAGE_MAP["rem"] == 4


def _make_ok_response(json_data: dict):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = json_data
    return r


def _make_error_response(status_code: int):
    import httpx
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=MagicMock()
    )
    return r


def test_open_wearables_fetch_returns_normalised_payload():
    adapter = OpenWearablesAdapter(base_url="http://wearables.test", api_key="key")
    hr_resp = _make_ok_response({"samples": [{"ts": "2025-01-15T02:00:00Z", "bpm": 58.0}]})
    spo2_resp = _make_ok_response({"samples": [{"ts": "2025-01-15T02:00:00Z", "spo2_pct": 97.0}]})
    stages_resp = _make_ok_response({"epochs": [{"ts": "2025-01-15T02:00:00Z", "stage": "rem"}]})

    with patch("api.wearable.open_wearables.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [hr_resp, spo2_resp, stages_resp]
        mock_client_cls.return_value = mock_client

        payload = adapter.fetch("user-123", date_type(2025, 1, 15))

    assert len(payload.hr) == 1
    assert payload.hr[0].value == 58.0
    assert len(payload.spo2) == 1
    assert payload.spo2[0].value == 97.0
    assert len(payload.stages) == 1
    assert payload.stages[0].stage == 4  # rem


def test_open_wearables_connect_error_returns_empty():
    import httpx
    adapter = OpenWearablesAdapter(base_url="http://wearables.test", api_key="key")

    with patch("api.wearable.open_wearables.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client_cls.return_value = mock_client

        payload = adapter.fetch("user-123", date_type(2025, 1, 15))

    assert payload.is_empty()


def test_open_wearables_401_raises():
    import httpx
    adapter = OpenWearablesAdapter(base_url="http://wearables.test", api_key="bad-key")
    auth_err_resp = _make_error_response(401)
    ok_resp = _make_ok_response({"samples": []})

    with patch("api.wearable.open_wearables.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [auth_err_resp, ok_resp, ok_resp]
        mock_client_cls.return_value = mock_client

        import pytest
        with pytest.raises(httpx.HTTPStatusError):
            adapter.fetch("user-123", date_type(2025, 1, 15))
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: new tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the adapter**

```python
# api/wearable/open_wearables.py
"""open-wearables adapter.

Fetches HR, SpO₂, and sleep stages from a self-hosted open-wearables instance
(https://github.com/openwearables). This module is the canonical worked example
for building new adapters — follow its pattern exactly.
"""

from datetime import date, timedelta

import httpx

from .base import Sample, StageSample, WearableAdapter, WearablePayload

_STAGE_MAP: dict[str, int] = {
    "awake": 1,
    "light": 2,
    "nrem1": 2,
    "nrem2": 2,
    "deep": 3,
    "nrem3": 3,
    "nrem4": 3,
    "rem": 4,
}

_TIMEOUT = 5.0


class OpenWearablesAdapter(WearableAdapter):
    """Adapter for a self-hosted open-wearables instance.

    Args:
        base_url: Root URL, e.g. ``"https://wearables.home.example.com"``.
        api_key: Bearer token issued by the open-wearables instance.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def fetch(self, user_id: str, target_date: date) -> WearablePayload:
        """Fetch HR, SpO₂, and sleep stages for one night.

        Args:
            user_id: SleepLab user UUID forwarded as ``subject`` query param.
            target_date: Local calendar date of the sleep session.

        Returns:
            Normalised ``WearablePayload``. Unknown stage labels default to
            ``1`` (awake). Empty lists for any series with no data.

        Raises:
            httpx.HTTPStatusError: Propagated on 401/403.
        """
        params = {
            "subject": user_id,
            "from": target_date.isoformat(),
            "to": (target_date + timedelta(days=1)).isoformat(),
        }

        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=_TIMEOUT,
        ) as client:
            try:
                hr_resp = client.get("/v1/heart-rate", params=params)
                spo2_resp = client.get("/v1/spo2", params=params)
                stages_resp = client.get("/v1/sleep-stages", params=params)
            except httpx.ConnectError:
                return WearablePayload()

            for resp in (hr_resp, spo2_resp, stages_resp):
                if resp.status_code in (401, 403):
                    resp.raise_for_status()

        hr = [
            Sample(timestamp=s["ts"], value=s["bpm"])
            for s in (hr_resp.json().get("samples", []) if hr_resp.status_code == 200 else [])
        ]
        spo2 = [
            Sample(timestamp=s["ts"], value=s["spo2_pct"])
            for s in (spo2_resp.json().get("samples", []) if spo2_resp.status_code == 200 else [])
        ]
        stages = [
            StageSample(
                timestamp=s["ts"],
                stage=_STAGE_MAP.get(s["stage"].lower(), 1),
            )
            for s in (stages_resp.json().get("epochs", []) if stages_resp.status_code == 200 else [])
        ]

        return WearablePayload(hr=hr, spo2=spo2, stages=stages)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: all tests so far PASS.

- [ ] **Step 5: Commit**

```bash
git add api/wearable/open_wearables.py tests/test_wearable.py
git commit -m "feat(wearable): add OpenWearablesAdapter with stage normalisation"
```

---

## Task 4: Mirobody Adapter

**Files:**
- Create: `api/wearable/mirobody.py`
- Modify: `tests/test_wearable.py`

> **Note:** Mirobody endpoint paths are based on its standard REST API. Verify against your Mirobody instance's API docs if the paths differ (`/api/v1/biometrics/heart-rate`, `/api/v1/biometrics/spo2`, `/api/v1/sleep/stages`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wearable.py`:

```python
from api.wearable.mirobody import MirobodyAdapter, _MIROBODY_STAGE_MAP


def test_mirobody_stage_map_normalises_correctly():
    assert _MIROBODY_STAGE_MAP["wake"] == 1
    assert _MIROBODY_STAGE_MAP["light"] == 2
    assert _MIROBODY_STAGE_MAP["deep"] == 3
    assert _MIROBODY_STAGE_MAP["rem"] == 4


def test_mirobody_connect_error_returns_empty():
    import httpx
    adapter = MirobodyAdapter(base_url="http://mirobody.test", api_key="key")

    with patch("api.wearable.mirobody.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client_cls.return_value = mock_client

        payload = adapter.fetch("user-456", date_type(2025, 1, 15))

    assert payload.is_empty()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_wearable.py::test_mirobody_stage_map_normalises_correctly -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the adapter**

```python
# api/wearable/mirobody.py
"""mirobody adapter.

Fetches HR, SpO₂, and sleep stages from a self-hosted Mirobody instance
(https://mirobody.com). Follows the same pattern as ``open_wearables.py``.
"""

from datetime import date, timedelta

import httpx

from .base import Sample, StageSample, WearableAdapter, WearablePayload

_MIROBODY_STAGE_MAP: dict[str, int] = {
    "wake": 1,
    "awake": 1,
    "light": 2,
    "nrem": 2,
    "deep": 3,
    "slow_wave": 3,
    "rem": 4,
}

_TIMEOUT = 5.0


class MirobodyAdapter(WearableAdapter):
    """Adapter for a self-hosted Mirobody instance.

    Args:
        base_url: Root URL, e.g. ``"https://mirobody.home.example.com"``.
        api_key: API key from your Mirobody instance settings.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}

    def fetch(self, user_id: str, target_date: date) -> WearablePayload:
        """Fetch HR, SpO₂, and sleep stages for one night.

        Args:
            user_id: SleepLab user UUID forwarded as ``user_id`` query param.
            target_date: Local calendar date of the sleep session.

        Returns:
            Normalised ``WearablePayload``. Unknown stage labels default to
            ``1`` (awake). Empty lists for any series with no data.

        Raises:
            httpx.HTTPStatusError: Propagated on 401/403.
        """
        params = {
            "user_id": user_id,
            "date_from": target_date.isoformat(),
            "date_to": (target_date + timedelta(days=1)).isoformat(),
        }

        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=_TIMEOUT,
        ) as client:
            try:
                hr_resp = client.get("/api/v1/biometrics/heart-rate", params=params)
                spo2_resp = client.get("/api/v1/biometrics/spo2", params=params)
                stages_resp = client.get("/api/v1/sleep/stages", params=params)
            except httpx.ConnectError:
                return WearablePayload()

            for resp in (hr_resp, spo2_resp, stages_resp):
                if resp.status_code in (401, 403):
                    resp.raise_for_status()

        hr = [
            Sample(timestamp=s["timestamp"], value=s["value"])
            for s in (hr_resp.json().get("data", []) if hr_resp.status_code == 200 else [])
        ]
        spo2 = [
            Sample(timestamp=s["timestamp"], value=s["value"])
            for s in (spo2_resp.json().get("data", []) if spo2_resp.status_code == 200 else [])
        ]
        stages = [
            StageSample(
                timestamp=s["timestamp"],
                stage=_MIROBODY_STAGE_MAP.get(s["stage"].lower(), 1),
            )
            for s in (stages_resp.json().get("data", []) if stages_resp.status_code == 200 else [])
        ]

        return WearablePayload(hr=hr, spo2=spo2, stages=stages)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/wearable/mirobody.py tests/test_wearable.py
git commit -m "feat(wearable): add MirobodyAdapter"
```

---

## Task 5: Registry

**Files:**
- Create: `api/wearable/registry.py`
- Modify: `tests/test_wearable.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wearable.py`:

```python
from api.wearable.registry import get_adapter
from api.wearable.open_wearables import OpenWearablesAdapter
from api.wearable.mirobody import MirobodyAdapter
import pytest


def test_registry_returns_open_wearables():
    adapter = get_adapter("open-wearables", "http://host", "key")
    assert isinstance(adapter, OpenWearablesAdapter)


def test_registry_returns_mirobody():
    adapter = get_adapter("mirobody", "http://host", "key")
    assert isinstance(adapter, MirobodyAdapter)


def test_registry_raises_on_unknown_provider():
    with pytest.raises(ValueError, match="Unknown wearable provider"):
        get_adapter("nonexistent", "http://host", "key")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_wearable.py::test_registry_returns_open_wearables -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the registry**

```python
# api/wearable/registry.py
"""Provider registry.

To add a new wearable provider:
1. Create ``api/wearable/<provider>.py`` subclassing ``WearableAdapter``.
2. Import and register it here.
3. Add the provider name to the selector in ``frontend/src/pages/Settings.tsx``.
"""

from .mirobody import MirobodyAdapter
from .open_wearables import OpenWearablesAdapter
from .base import WearableAdapter

ADAPTERS: dict[str, type[WearableAdapter]] = {
    "open-wearables": OpenWearablesAdapter,
    "mirobody": MirobodyAdapter,
}


def get_adapter(provider: str, base_url: str, api_key: str) -> WearableAdapter:
    """Instantiate the adapter for the given provider name.

    Args:
        provider: Key matching an entry in ``ADAPTERS``.
        base_url: Root URL of the self-hosted wearable server.
        api_key: Bearer token / API key for the wearable server.

    Returns:
        A ``WearableAdapter`` instance ready to call ``fetch()``.

    Raises:
        ValueError: If ``provider`` is not registered.
    """
    cls = ADAPTERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown wearable provider: {provider!r}. "
            f"Registered: {list(ADAPTERS)}"
        )
    return cls(base_url=base_url, api_key=api_key)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/wearable/registry.py tests/test_wearable.py
git commit -m "feat(wearable): add provider registry with get_adapter()"
```

---

## Task 6: Wearable Router + Register in main.py

**Files:**
- Create: `api/routers/wearable.py`
- Modify: `api/main.py`
- Modify: `tests/test_wearable.py`

- [ ] **Step 1: Write the failing endpoint tests**

Add to `tests/test_wearable.py`:

```python
# ── endpoint tests ────────────────────────────────────────────────────────────
# These use the standard client + auth_headers fixtures from conftest.py.
# They require TEST_DATABASE_URL to be set (see conftest.py).

from unittest.mock import patch as _patch
import httpx


def test_wearable_data_no_provider_returns_empty(client, auth_headers):
    resp = client.get("/wearable/data", params={"date": "2025-01-15"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["hr"] == []
    assert body["spo2"] == []
    assert body["stages"] == []


def test_wearable_data_unauthenticated(client):
    resp = client.get("/wearable/data", params={"date": "2025-01-15"})
    assert resp.status_code == 401


def test_wearable_summary_no_provider_returns_empty(client, auth_headers):
    resp = client.get(
        "/wearable/summary",
        params={"date_from": "2025-01-01", "date_to": "2025-01-03"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_wearable_data_connect_error_returns_empty(client, auth_headers, db):
    from sqlalchemy import text
    # Insert wearable settings for the test user so provider is configured.
    # We need the user_id — read it from the auth token via /auth/me.
    me = client.get("/auth/me", headers=auth_headers).json()
    uid = me["user_id"]
    db.execute(
        text("""
            INSERT INTO user_import_settings
                (user_id, wearable_provider, wearable_base_url, wearable_api_key)
            VALUES (CAST(:uid AS uuid), 'open-wearables', 'http://no-such-host.test', 'key')
            ON CONFLICT (user_id) DO UPDATE
                SET wearable_provider  = EXCLUDED.wearable_provider,
                    wearable_base_url  = EXCLUDED.wearable_base_url,
                    wearable_api_key   = EXCLUDED.wearable_api_key
        """),
        {"uid": uid},
    )
    db.commit()

    with _patch("api.wearable.open_wearables.httpx.Client") as mock_cls:
        mock_c = MagicMock()
        mock_c.__enter__ = MagicMock(return_value=mock_c)
        mock_c.__exit__ = MagicMock(return_value=False)
        mock_c.get.side_effect = httpx.ConnectError("refused")
        mock_cls.return_value = mock_c

        resp = client.get("/wearable/data", params={"date": "2025-01-15"}, headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["hr"] == []


def test_wearable_data_401_from_api_returns_502(client, auth_headers, db):
    from sqlalchemy import text
    me = client.get("/auth/me", headers=auth_headers).json()
    uid = me["user_id"]
    db.execute(
        text("""
            INSERT INTO user_import_settings
                (user_id, wearable_provider, wearable_base_url, wearable_api_key)
            VALUES (CAST(:uid AS uuid), 'open-wearables', 'http://host.test', 'bad-key')
            ON CONFLICT (user_id) DO UPDATE
                SET wearable_provider  = EXCLUDED.wearable_provider,
                    wearable_base_url  = EXCLUDED.wearable_base_url,
                    wearable_api_key   = EXCLUDED.wearable_api_key
        """),
        {"uid": uid},
    )
    db.commit()

    with _patch("api.wearable.open_wearables.httpx.Client") as mock_cls:
        mock_c = MagicMock()
        mock_c.__enter__ = MagicMock(return_value=mock_c)
        mock_c.__exit__ = MagicMock(return_value=False)
        auth_err = MagicMock()
        auth_err.status_code = 401
        auth_err.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock()
        )
        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {"samples": []}
        mock_c.get.side_effect = [auth_err, ok, ok]
        mock_cls.return_value = mock_c

        resp = client.get("/wearable/data", params={"date": "2025-01-15"}, headers=auth_headers)

    assert resp.status_code == 502
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_wearable.py::test_wearable_data_no_provider_returns_empty -v
```
Expected: FAIL with `404 Not Found` (route not registered yet).

- [ ] **Step 3: Implement the router**

```python
# api/routers/wearable.py
"""Wearable data endpoints.

GET /wearable/data?date=YYYY-MM-DD       — raw samples for one night
GET /wearable/summary?date_from=&date_to= — daily aggregates for a date range
"""

import logging
from datetime import date, timedelta
from statistics import mean
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..wearable.base import StageSample, WearablePayload
from ..wearable.registry import get_adapter

router = APIRouter()
logger = logging.getLogger(__name__)


class SampleOut(BaseModel):
    timestamp: str
    value: float


class StageSampleOut(BaseModel):
    timestamp: str
    stage: int


class WearableDataResponse(BaseModel):
    hr: list[SampleOut]
    spo2: list[SampleOut]
    stages: list[StageSampleOut]


class WearableDailySummary(BaseModel):
    date: str
    avg_hr: Optional[float]
    avg_spo2: Optional[float]
    awake_h: float
    light_h: float
    deep_h: float
    rem_h: float


def _get_adapter_for_user(user_id: str, db: Session):
    """Return a configured adapter or None if the user has no wearable set up."""
    row = db.execute(
        text("""
            SELECT wearable_provider, wearable_base_url, wearable_api_key
            FROM user_import_settings
            WHERE user_id = CAST(:uid AS uuid)
        """),
        {"uid": user_id},
    ).mappings().first()

    if not row or not row["wearable_provider"]:
        return None

    try:
        return get_adapter(
            row["wearable_provider"],
            row["wearable_base_url"] or "",
            row["wearable_api_key"] or "",
        )
    except ValueError:
        return None


def _payload_to_response(payload: WearablePayload) -> WearableDataResponse:
    return WearableDataResponse(
        hr=[SampleOut(timestamp=s.timestamp, value=s.value) for s in payload.hr],
        spo2=[SampleOut(timestamp=s.timestamp, value=s.value) for s in payload.spo2],
        stages=[StageSampleOut(timestamp=s.timestamp, stage=s.stage) for s in payload.stages],
    )


def _stages_to_hours(stages: list[StageSample], next_ts_iso: Optional[str] = None) -> dict:
    """Convert a list of StageSample epochs to hours per stage.

    Each epoch runs from its timestamp to the next epoch's timestamp (or
    ``next_ts_iso`` for the final epoch, defaulting to 30 minutes).
    """
    hours: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    from datetime import datetime, timezone

    for i, sample in enumerate(stages):
        start = datetime.fromisoformat(sample.timestamp.replace("Z", "+00:00"))
        if i + 1 < len(stages):
            end_str = stages[i + 1].timestamp
        elif next_ts_iso:
            end_str = next_ts_iso
        else:
            end_str = None

        if end_str:
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        else:
            end = start + timedelta(minutes=30)

        duration_h = max(0.0, (end - start).total_seconds() / 3600)
        hours[sample.stage] = hours.get(sample.stage, 0.0) + duration_h

    return hours


@router.get("/data", response_model=WearableDataResponse)
def get_wearable_data(
    date: str = Query(..., description="YYYY-MM-DD"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    adapter = _get_adapter_for_user(current_user["id"], db)
    if adapter is None:
        return WearableDataResponse(hr=[], spo2=[], stages=[])

    try:
        target = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    try:
        payload = adapter.fetch(current_user["id"], target)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Wearable API returned {exc.response.status_code}. Check your credentials in Settings.",
        )

    return _payload_to_response(payload)


@router.get("/summary", response_model=list[WearableDailySummary])
def get_wearable_summary(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    adapter = _get_adapter_for_user(current_user["id"], db)
    if adapter is None:
        return []

    try:
        start = date_type.fromisoformat(date_from)
        end = date_type.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from and date_to must be YYYY-MM-DD")

    results: list[WearableDailySummary] = []
    current = start
    while current <= end:
        try:
            payload = adapter.fetch(current_user["id"], current)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Wearable API returned {exc.response.status_code}. Check your credentials in Settings.",
            )

        if not payload.is_empty():
            hr_vals = [s.value for s in payload.hr]
            spo2_vals = [s.value for s in payload.spo2]
            stage_hours = _stages_to_hours(payload.stages)
            results.append(WearableDailySummary(
                date=current.isoformat(),
                avg_hr=round(mean(hr_vals), 1) if hr_vals else None,
                avg_spo2=round(mean(spo2_vals), 1) if spo2_vals else None,
                awake_h=round(stage_hours[1], 2),
                light_h=round(stage_hours[2], 2),
                deep_h=round(stage_hours[3], 2),
                rem_h=round(stage_hours[4], 2),
            ))

        current += timedelta(days=1)

    return results
```

Add `from datetime import date as date_type` at the top of `api/routers/wearable.py` (the `date` param shadows the stdlib name).

The full import block for `api/routers/wearable.py`:

```python
import logging
from datetime import date as date_type, timedelta
from statistics import mean
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..wearable.base import StageSample, WearablePayload
from ..wearable.registry import get_adapter
```

- [ ] **Step 4: Register the router in `api/main.py`**

Add after the existing `include_router` lines:

```python
from .routers import wearable as wearable_router
# ...
app.include_router(wearable_router.router, prefix="/wearable", tags=["wearable"])
```

Full updated import line in `api/main.py` (add to the existing import block):

```python
from .routers import auth as auth_router
from .routers import sessions, stats, upload, ai_summary, llm, import_settings as import_settings_router
from .routers import wearable as wearable_router
```

And the include line:

```python
app.include_router(wearable_router.router, prefix="/wearable", tags=["wearable"])
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_wearable.py -v
```
Expected: all tests PASS (DB-dependent tests skip without `TEST_DATABASE_URL`).

- [ ] **Step 6: Lint check**

```bash
uv run ruff check api/routers/wearable.py api/wearable/
```
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add api/routers/wearable.py api/main.py tests/test_wearable.py
git commit -m "feat(wearable): add /wearable/data and /wearable/summary endpoints"
```

---

## Task 7: Import Settings Extensions

**Files:**
- Modify: `api/routers/import_settings.py`
- Modify: `tests/test_wearable.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_wearable.py`:

```python
def test_wearable_settings_round_trip(client, auth_headers):
    resp = client.put(
        "/import/settings",
        json={
            "wearable_provider": "open-wearables",
            "wearable_base_url": "https://wearables.home.example.com",
            "wearable_api_key": "my-secret-key",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["wearable_provider"] == "open-wearables"
    assert body["wearable_base_url"] == "https://wearables.home.example.com"
    assert body["wearable_api_key"] is None  # always masked


def test_wearable_api_key_not_exposed_in_get(client, auth_headers):
    client.put(
        "/import/settings",
        json={"wearable_api_key": "super-secret"},
        headers=auth_headers,
    )
    resp = client.get("/import/settings", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["wearable_api_key"] is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_wearable.py::test_wearable_settings_round_trip -v
```
Expected: FAIL with `422 Unprocessable Entity` (field not in model yet).

- [ ] **Step 3: Extend ImportSettingsResponse**

In `api/routers/import_settings.py`, add three fields to `ImportSettingsResponse`:

```python
class ImportSettingsResponse(BaseModel):
    sleephq_client_id: Optional[str] = None
    sleephq_client_secret: Optional[str] = None
    sleephq_team_id: Optional[int] = None
    sleephq_machine_id: Optional[int] = None
    auto_import_sleephq: bool = False
    lookback_days: int = 30
    local_datalog_path: Optional[str] = None
    local_import_frequency: str = "daily"
    last_local_import_at: Optional[str] = None
    last_local_import_status: Optional[str] = None
    wearable_provider: Optional[str] = None
    wearable_base_url: Optional[str] = None
    wearable_api_key: Optional[str] = None  # always None in responses
```

- [ ] **Step 4: Extend ImportSettingsUpdate**

```python
class ImportSettingsUpdate(BaseModel):
    sleephq_client_id: Optional[str] = None
    sleephq_client_secret: Optional[str] = None
    sleephq_team_id: Optional[int] = None
    sleephq_machine_id: Optional[int] = None
    auto_import_sleephq: Optional[bool] = None
    lookback_days: Optional[int] = None
    local_datalog_path: Optional[str] = None
    local_import_frequency: Optional[str] = None
    wearable_provider: Optional[str] = None
    wearable_base_url: Optional[str] = None
    wearable_api_key: Optional[str] = None
```

- [ ] **Step 5: Update `get_import_settings` to read the new columns**

In `get_import_settings`, extend the `return ImportSettingsResponse(...)` call:

```python
    return ImportSettingsResponse(
        sleephq_client_id=row["sleephq_client_id"],
        sleephq_client_secret=None,
        sleephq_team_id=row["sleephq_team_id"],
        sleephq_machine_id=row["sleephq_machine_id"],
        auto_import_sleephq=row["auto_import_sleephq"],
        lookback_days=row["lookback_days"],
        local_datalog_path=row["local_datalog_path"],
        local_import_frequency=row["local_import_frequency"] or "daily",
        last_local_import_at=last_at.isoformat() if last_at else None,
        last_local_import_status=row["last_local_import_status"],
        wearable_provider=row["wearable_provider"],
        wearable_base_url=row["wearable_base_url"],
        wearable_api_key=None,  # never expose
    )
```

- [ ] **Step 6: Update `save_import_settings` INSERT branch**

In the `if existing is None:` branch, expand the INSERT to include the new columns. Replace the existing INSERT with:

```python
        db.execute(
            text("""
                INSERT INTO user_import_settings
                    (user_id, sleephq_client_id, sleephq_client_secret,
                     sleephq_team_id, sleephq_machine_id,
                     auto_import_sleephq, lookback_days,
                     local_datalog_path, local_import_frequency,
                     wearable_provider, wearable_base_url, wearable_api_key,
                     updated_at)
                VALUES
                    (CAST(:uid AS uuid), :client_id, :client_secret,
                     :team_id, :machine_id,
                     :auto_import, :lookback,
                     :local_path, :local_freq,
                     :w_provider, :w_base_url, :w_api_key,
                     NOW())
            """),
            {
                "uid": current_user["id"],
                "client_id": body.sleephq_client_id,
                "client_secret": body.sleephq_client_secret,
                "team_id": body.sleephq_team_id,
                "machine_id": body.sleephq_machine_id,
                "auto_import": body.auto_import_sleephq if body.auto_import_sleephq is not None else False,
                "lookback": body.lookback_days if body.lookback_days is not None else 30,
                "local_path": body.local_datalog_path or None,
                "local_freq": body.local_import_frequency or "daily",
                "w_provider": body.wearable_provider,
                "w_base_url": body.wearable_base_url,
                "w_api_key": body.wearable_api_key,
            },
        )
```

- [ ] **Step 7: Update `save_import_settings` UPDATE branch**

In the `else:` (update) branch, add three new conditional clauses after the existing `local_import_frequency` block:

```python
        if body.wearable_provider is not None:
            set_clauses.append("wearable_provider = :w_provider")
            fields["w_provider"] = body.wearable_provider

        if body.wearable_base_url is not None:
            set_clauses.append("wearable_base_url = :w_base_url")
            fields["w_base_url"] = body.wearable_base_url

        if body.wearable_api_key is not None:
            set_clauses.append("wearable_api_key = :w_api_key")
            fields["w_api_key"] = body.wearable_api_key
```

- [ ] **Step 8: Run all tests**

```bash
uv run pytest tests/ -v --tb=short
```
Expected: all tests PASS (DB tests skip without `TEST_DATABASE_URL`).

- [ ] **Step 9: Commit**

```bash
git add api/routers/import_settings.py tests/test_wearable.py
git commit -m "feat(wearable): add wearable provider/credentials to import settings"
```

---

## Task 8: Frontend Types and API Client

**Files:**
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: Add types and API methods**

In `frontend/src/api/client.ts`, add after the `ImportSettings` interface:

```typescript
export interface SpO2Response {
  timestamps: string[]
  spo2: (number | null)[]
  pulse: (number | null)[]
}

export interface WearableData {
  hr: { timestamp: string; value: number }[]
  spo2: { timestamp: string; value: number }[]
  stages: { timestamp: string; stage: number }[]
}

export interface WearableDailySummary {
  date: string
  avg_hr: number | null
  avg_spo2: number | null
  awake_h: number
  light_h: number
  deep_h: number
  rem_h: number
}
```

Also add three fields to the existing `ImportSettings` interface:

```typescript
export interface ImportSettings {
  // ...existing fields...
  wearable_provider: string | null
  wearable_base_url: string | null
  wearable_api_key: string | null
}
```

In the `api` object, add after `triggerLocalImport`:

```typescript
  getSessionSpo2: (id: string) => get<SpO2Response>(`/sessions/${id}/spo2`),
  getWearableData: (date: string) => get<WearableData>('/wearable/data', { date }),
  getWearableSummary: (dateFrom: string, dateTo: string) =>
    get<WearableDailySummary[]>('/wearable/summary', { date_from: dateFrom, date_to: dateTo }),
```

- [ ] **Step 2: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/api/client.ts
git commit -m "feat(wearable): add WearableData/WearableDailySummary types and API methods to client.ts"
```

---

## Task 9: Wearable Settings Card

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Add state and load/save logic**

At the top of `SettingsPage`, add these state variables after the existing local DATALOG state block:

```typescript
  // Wearable settings
  const [wearableProvider, setWearableProvider] = useState('')
  const [wearableBaseUrl, setWearableBaseUrl] = useState('')
  const [wearableApiKey, setWearableApiKey] = useState('')
  const [wearableMessage, setWearableMessage] = useState<string | null>(null)
  const [wearableError, setWearableError] = useState<string | null>(null)
  const [isWearableSubmitting, setIsWearableSubmitting] = useState(false)
```

In the existing `api.getImportSettings()` `useEffect`, add after `setLocalFrequency(...)`:

```typescript
      setWearableProvider(settings.wearable_provider ?? '')
      setWearableBaseUrl(settings.wearable_base_url ?? '')
      // wearable_api_key is always null from server — leave blank
```

Add the submit handler before `return (`:

```typescript
  async function handleWearableSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setWearableError(null)
    setWearableMessage(null)
    setIsWearableSubmitting(true)
    try {
      await api.saveImportSettings({
        wearable_provider: wearableProvider || null,
        wearable_base_url: wearableBaseUrl || null,
        wearable_api_key: wearableApiKey || null,
      })
      setWearableMessage('Settings saved.')
      setWearableApiKey('')
    } catch (err) {
      setWearableError(err instanceof Error ? err.message : 'Could not save settings')
    } finally {
      setIsWearableSubmitting(false)
    }
  }
```

- [ ] **Step 2: Add the settings card to the JSX**

Add this card after the Local DATALOG Import card and before the Danger Zone card:

```tsx
      <Card className="bg-[radial-gradient(circle_at_top_left,_rgba(255,255,255,0.45),_transparent_38%),var(--surface-strong)]">
        <CardHeader>
          <CardTitle className="text-2xl">Wearable Data</CardTitle>
          <CardDescription>
            Overlay heart rate, SpO₂, and sleep stages from a self-hosted wearable API onto your session charts. Supported providers: open-wearables, mirobody.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-5" onSubmit={handleWearableSubmit}>
            <div className="space-y-3">
              <Label htmlFor="wearableProvider">Provider</Label>
              <select
                id="wearableProvider"
                value={wearableProvider}
                onChange={(event) => setWearableProvider(event.target.value)}
                className="flex h-9 w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
              >
                <option value="">None</option>
                <option value="open-wearables">open-wearables</option>
                <option value="mirobody">mirobody</option>
              </select>
            </div>

            <div className="space-y-3">
              <Label htmlFor="wearableBaseUrl">Base URL</Label>
              <Input
                id="wearableBaseUrl"
                value={wearableBaseUrl}
                onChange={(event) => setWearableBaseUrl(event.target.value)}
                autoComplete="off"
                placeholder="https://wearables.home.example.com"
              />
            </div>

            <div className="space-y-3">
              <Label htmlFor="wearableApiKey">API key</Label>
              <Input
                id="wearableApiKey"
                type="password"
                value={wearableApiKey}
                onChange={(event) => setWearableApiKey(event.target.value)}
                autoComplete="new-password"
                placeholder="Leave blank to keep existing key"
              />
            </div>

            {wearableMessage ? <p className="text-sm font-medium text-[var(--olive-deep)]">{wearableMessage}</p> : null}
            {wearableError ? <p className="text-sm text-[var(--danger-text)]">{wearableError}</p> : null}

            <Button type="submit" disabled={isWearableSubmitting}>
              {isWearableSubmitting ? 'Saving...' : 'Save wearable settings'}
            </Button>
          </form>
        </CardContent>
      </Card>
```

- [ ] **Step 3: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/pages/Settings.tsx
git commit -m "feat(wearable): add Wearable Data settings card"
```

---

## Task 10: SpO2Chart Component (CPAP + Wearable Overlay)

**Files:**
- Create: `frontend/src/components/SpO2Chart.tsx`

> **Conflict note:** PR #25 also creates `SpO2Chart.tsx`. When it merges, keep our version — it is a strict superset (same CPAP chart plus optional wearable overlay). Review the colors and styling from #25 and align if they differ.

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/SpO2Chart.tsx
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import type { SpO2Response, WearableData } from '../api/client'

interface Props {
  spo2: SpO2Response
  wearable?: WearableData | null
}

function formatTick(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function SpO2Chart({ spo2, wearable }: Props) {
  const hasWearable =
    wearable && (wearable.hr.length > 0 || wearable.spo2.length > 0)

  // Build a unified time-indexed dataset for Recharts.
  // Each entry keyed by timestamp string.
  const byTs: Record<string, {
    ts: string
    cpapSpo2?: number | null
    cpapPulse?: number | null
    wearableSpo2?: number
    wearableHr?: number
  }> = {}

  spo2.timestamps.forEach((ts, i) => {
    byTs[ts] = { ts, cpapSpo2: spo2.spo2[i], cpapPulse: spo2.pulse[i] }
  })

  if (wearable) {
    wearable.spo2.forEach(({ timestamp, value }) => {
      byTs[timestamp] = { ...byTs[timestamp], ts: timestamp, wearableSpo2: value }
    })
    wearable.hr.forEach(({ timestamp, value }) => {
      byTs[timestamp] = { ...byTs[timestamp], ts: timestamp, wearableHr: value }
    })
  }

  const data = Object.values(byTs).sort((a, b) => a.ts.localeCompare(b.ts))

  const tickInterval = Math.max(1, Math.floor(data.length / 8))

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle>Oximetry</CardTitle>
          {hasWearable && (
            <div className="flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-4 rounded-sm bg-[#6366f1]" />
                CPAP
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-4 rounded-sm bg-[#f59e0b]" />
                Wearable
              </span>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-1 pb-4">
        {/* SpO₂ panel */}
        <p className="text-xs font-semibold text-[var(--muted-foreground)]">SpO₂ (%)</p>
        <ResponsiveContainer width="100%" height={150}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.3} />
            <XAxis
              dataKey="ts"
              tickFormatter={formatTick}
              interval={tickInterval}
              tick={{ fill: '#7d695d', fontSize: 10 }}
            />
            <YAxis domain={[80, 100]} tick={{ fill: '#7d695d', fontSize: 10 }} />
            <Tooltip
              contentStyle={{
                background: 'rgba(255,251,245,0.96)',
                border: '1px solid rgba(125,105,93,0.2)',
                borderRadius: 12,
                color: '#3c2b22',
                fontSize: 12,
              }}
              labelFormatter={formatTick}
            />
            <ReferenceLine y={90} stroke="#ef4444" strokeDasharray="4 4" strokeOpacity={0.7} />
            <Line
              type="monotone"
              dataKey="cpapSpo2"
              stroke="#6366f1"
              dot={false}
              strokeWidth={1.5}
              connectNulls
              name="CPAP SpO₂"
            />
            {hasWearable && (
              <Line
                type="monotone"
                dataKey="wearableSpo2"
                stroke="#f59e0b"
                dot={false}
                strokeWidth={1.5}
                connectNulls
                name="Wearable SpO₂"
              />
            )}
          </LineChart>
        </ResponsiveContainer>

        {/* Pulse / HR panel */}
        <p className="text-xs font-semibold text-[var(--muted-foreground)] pt-2">
          {hasWearable ? 'Pulse / HR (bpm)' : 'Pulse (bpm)'}
        </p>
        <ResponsiveContainer width="100%" height={150}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.3} />
            <XAxis
              dataKey="ts"
              tickFormatter={formatTick}
              interval={tickInterval}
              tick={{ fill: '#7d695d', fontSize: 10 }}
            />
            <YAxis domain={['auto', 'auto']} tick={{ fill: '#7d695d', fontSize: 10 }} />
            <Tooltip
              contentStyle={{
                background: 'rgba(255,251,245,0.96)',
                border: '1px solid rgba(125,105,93,0.2)',
                borderRadius: 12,
                color: '#3c2b22',
                fontSize: 12,
              }}
              labelFormatter={formatTick}
            />
            <Line
              type="monotone"
              dataKey="cpapPulse"
              stroke="#818cf8"
              dot={false}
              strokeWidth={1.5}
              connectNulls
              name="CPAP Pulse"
            />
            {hasWearable && (
              <Line
                type="monotone"
                dataKey="wearableHr"
                stroke="#10b981"
                dot={false}
                strokeWidth={1.5}
                connectNulls
                name="Wearable HR"
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/components/SpO2Chart.tsx
git commit -m "feat(wearable): add SpO2Chart with CPAP + wearable overlay and color legend"
```

---

## Task 11: WearableSleepStageChart

**Files:**
- Create: `frontend/src/components/WearableSleepStageChart.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/WearableSleepStageChart.tsx
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import type { WearableData } from '../api/client'

interface Props {
  stages: WearableData['stages']
}

const STAGE_LABELS: Record<number, string> = {
  1: 'Awake',
  2: 'Light',
  3: 'Deep',
  4: 'REM',
}

// Invert so REM (4) appears at the top of the chart and Awake (1) at the bottom.
// Recharts renders higher Y values higher, so we flip.
function invertStage(stage: number): number {
  return 5 - stage
}

function formatTick(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function WearableSleepStageChart({ stages }: Props) {
  if (stages.length === 0) return null

  const data = stages.map(({ timestamp, stage }) => ({
    ts: timestamp,
    stage: invertStage(stage),
    originalStage: stage,
  }))

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>Sleep Stages</CardTitle>
      </CardHeader>
      <CardContent className="pb-4">
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.3} />
            <XAxis
              dataKey="ts"
              tickFormatter={formatTick}
              interval={Math.max(1, Math.floor(data.length / 8))}
              tick={{ fill: '#7d695d', fontSize: 10 }}
            />
            <YAxis
              domain={[1, 4]}
              ticks={[1, 2, 3, 4]}
              tickFormatter={(v) => STAGE_LABELS[5 - v] ?? ''}
              tick={{ fill: '#7d695d', fontSize: 10 }}
              width={40}
            />
            <Tooltip
              contentStyle={{
                background: 'rgba(255,251,245,0.96)',
                border: '1px solid rgba(125,105,93,0.2)',
                borderRadius: 12,
                color: '#3c2b22',
                fontSize: 12,
              }}
              labelFormatter={formatTick}
              formatter={(_, __, props) => [
                STAGE_LABELS[props.payload.originalStage] ?? 'Unknown',
                'Stage',
              ]}
            />
            <Line
              type="stepAfter"
              dataKey="stage"
              stroke="#8b5cf6"
              dot={false}
              strokeWidth={2}
              name="Stage"
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/components/WearableSleepStageChart.tsx
git commit -m "feat(wearable): add WearableSleepStageChart hypnogram component"
```

---

## Task 12: Session Detail Integration

**Files:**
- Modify: `frontend/src/pages/SessionDetail.tsx`

- [ ] **Step 1: Add imports and state**

Add to the import block at the top of `SessionDetail.tsx`:

```typescript
import type { SpO2Response, WearableData } from '../api/client'
import SpO2Chart from '../components/SpO2Chart'
import WearableSleepStageChart from '../components/WearableSleepStageChart'
```

Add state variables after the existing `const [prevNext, ...]` line:

```typescript
  const [spo2, setSpo2] = useState<SpO2Response | null>(null)
  const [wearableData, setWearableData] = useState<WearableData | null>(null)
```

- [ ] **Step 2: Fetch SpO₂ and wearable data non-blocking**

Add a second `useEffect` after the existing prevNext `useEffect`:

```typescript
  useEffect(() => {
    if (!session) return
    if (!session.has_spo2) return
    api.getSessionSpo2(sessionId).then(setSpo2).catch(() => {})
  }, [session, sessionId])

  useEffect(() => {
    if (!session) return
    api.getWearableData(session.folder_date).then((data) => {
      if (!data.hr.length && !data.spo2.length && !data.stages.length) return
      setWearableData(data)
    }).catch(() => {})
  }, [session, sessionId])
```

- [ ] **Step 3: Render the charts**

Find where `<MetricsChart metrics={metrics} />` is rendered in the JSX. Add the oximetry and sleep stage cards after it:

```tsx
        <MetricsChart metrics={metrics} />

        {spo2 && (
          <SpO2Chart spo2={spo2} wearable={wearableData} />
        )}

        {wearableData && wearableData.stages.length > 0 && (
          <WearableSleepStageChart stages={wearableData.stages} />
        )}
```

- [ ] **Step 4: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/pages/SessionDetail.tsx
git commit -m "feat(wearable): render SpO2Chart with wearable overlay and sleep stage hypnogram on session detail"
```

---

## Task 13: Dashboard Sleep Stage Summary Chart

**Files:**
- Create: `frontend/src/components/WearableSleepSummaryChart.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Create the stacked bar chart component**

```tsx
// frontend/src/components/WearableSleepSummaryChart.tsx
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import type { WearableDailySummary } from '../api/client'

interface Props {
  data: WearableDailySummary[]
}

const STAGE_COLORS = {
  awake_h: '#f87171',   // red-400
  light_h: '#60a5fa',  // blue-400
  deep_h: '#34d399',   // emerald-400
  rem_h: '#a78bfa',    // violet-400
}

const STAGE_LABELS = {
  awake_h: 'Awake',
  light_h: 'Light',
  deep_h: 'Deep',
  rem_h: 'REM',
}

export default function WearableSleepSummaryChart({ data }: Props) {
  if (data.length === 0) return null

  const chartData = data.map((d) => ({
    date: d.date.slice(5),  // MM-DD
    awake_h: d.awake_h,
    light_h: d.light_h,
    deep_h: d.deep_h,
    rem_h: d.rem_h,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sleep Stage Breakdown</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#d8dcdd" />
            <XAxis dataKey="date" tick={{ fill: '#7d695d', fontSize: 11 }} interval={6} />
            <YAxis
              tick={{ fill: '#7d695d', fontSize: 11 }}
              label={{ value: 'hours', angle: -90, position: 'insideLeft', fill: '#7d695d', fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{
                background: 'rgba(255,251,245,0.96)',
                border: '1px solid rgba(125,105,93,0.2)',
                borderRadius: 18,
                color: '#3c2b22',
              }}
              formatter={(val: number, name: string) => [
                `${val.toFixed(1)}h`,
                STAGE_LABELS[name as keyof typeof STAGE_LABELS] ?? name,
              ]}
            />
            <Legend
              formatter={(value) => STAGE_LABELS[value as keyof typeof STAGE_LABELS] ?? value}
              wrapperStyle={{ fontSize: 12, color: '#7d695d' }}
            />
            {(Object.keys(STAGE_COLORS) as Array<keyof typeof STAGE_COLORS>).map((key) => (
              <Bar key={key} dataKey={key} stackId="stages" fill={STAGE_COLORS[key]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Fetch wearable summary in Dashboard and render the chart**

In `Dashboard.tsx`, add the import:

```typescript
import type { WearableDailySummary } from '../api/client'
import WearableSleepSummaryChart from '../components/WearableSleepSummaryChart'
```

Add state after `const [error, ...]`:

```typescript
  const [wearableSummary, setWearableSummary] = useState<WearableDailySummary[]>([])
```

After `setSummary(nextSummary)` in `loadDashboard`, add a non-blocking wearable fetch that derives its date range from the summary's AHI trend (so it inherits any future range changes automatically):

```typescript
        setSummary(nextSummary)
        setSessions(nextSessions)
        // Fetch wearable summary using the same date range as the AHI trend.
        if (nextSummary.ahi_trend.length > 0) {
          const dateFrom = nextSummary.ahi_trend[0].folder_date
          const dateTo = nextSummary.ahi_trend[nextSummary.ahi_trend.length - 1].folder_date
          api.getWearableSummary(dateFrom, dateTo)
            .then(setWearableSummary)
            .catch(() => {})
        }
```

In the JSX, add after `<AHITrendChart trend={summary.ahi_trend} />`:

```tsx
        <AHITrendChart trend={summary.ahi_trend} />
        <WearableSleepSummaryChart data={wearableSummary} />
```

- [ ] **Step 3: Type-check**

```bash
cd /home/camden/ZedProjects/sleeplab/frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 4: Run vitest**

```bash
npx vitest run
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/camden/ZedProjects/sleeplab
git add frontend/src/components/WearableSleepSummaryChart.tsx frontend/src/pages/Dashboard.tsx
git commit -m "feat(wearable): add sleep stage summary stacked bar chart to dashboard"
```

---

## Task 14: Final Verification

- [ ] **Step 1: Run full backend test suite**

```bash
cd /home/camden/ZedProjects/sleeplab
uv run ruff check tests/ api/wearable/ api/routers/wearable.py
uv run pytest -v --tb=short
```
Expected: all tests pass; ruff clean.

- [ ] **Step 2: Run with live DB (if available)**

```bash
TEST_DATABASE_URL="postgresql+psycopg2://cpap:cpap@localhost:5432/cpap" uv run pytest tests/test_wearable.py -v
```
Expected: all DB tests pass (not skipped).

- [ ] **Step 3: Full frontend check**

```bash
cd frontend
npx tsc --noEmit
npx vitest run
```
Expected: no errors, all tests pass.

- [ ] **Step 4: Open draft PR**

```bash
cd /home/camden/ZedProjects/sleeplab
git push -u origin feat/wearable-data-integration
gh pr create \
  --repo joshuamyers-dev/sleeplab \
  --head "camden-bock:feat/wearable-data-integration" \
  --base main \
  --draft \
  --title "feat(#12): wearable data overlay — HR, SpO₂, and sleep stages" \
  --body "$(cat <<'EOF'
## Summary

- Adds `api/wearable/` — `WearableAdapter` ABC, `OpenWearablesAdapter`, `MirobodyAdapter`, provider registry
- `GET /wearable/data?date=` — real-time fetch of HR, SpO₂, sleep stages for a session date
- `GET /wearable/summary?date_from=&date_to=` — daily aggregates for dashboard trend chart
- Wearable credentials (provider, base URL, API key) stored in `user_import_settings` and configurable in Settings
- Session detail: wearable HR/SpO₂ overlaid on existing CPAP oximetry chart with color legend; sleep stage hypnogram below
- Dashboard: stacked bar chart of sleep stage hours per night, date range inherited from AHI trend

## Blocking PR conflicts

This branch was started from `main` before the following PRs merged. Rebase after each and resolve:

| PR | Conflict files |
|----|----------------|
| #25 — SpO2/pulse chart | `SpO2Chart.tsx` — ours supersedes (keep ours) |
| #26 — SleepHQ secret masking | `import_settings.py` — append our wearable clauses |
| #27 — SleepHQ Machine ID | `import_settings.py`, `models.py`, `Settings.tsx` |
| #28 — Equipment catalog | `import_settings.py`, `models.py`, `Settings.tsx`, migration numbering |
| #29 — Equipment CRUD API/UI | Same as #28 plus `main.py` |
| #33 — opt-in SleepHQ | `import_settings.py`, `Settings.tsx` |
| #36 — timezone | `main.py`, `Settings.tsx`, `client.ts`, `SessionDetail.tsx` |
| #37 — local DATALOG import | `import_settings.py`, `Settings.tsx`, `client.ts`, migration number |

**Before merging:** rename `008_add_wearable_settings.sql` to the next available number after all pending PRs merge.

## Test plan

- [ ] `uv run pytest tests/test_wearable.py -v` — all pass
- [ ] `uv run ruff check tests/ api/wearable/ api/routers/wearable.py` — clean
- [ ] `cd frontend && npx tsc --noEmit` — clean
- [ ] `cd frontend && npx vitest run` — all pass
- [ ] Manual: configure open-wearables or mirobody in Settings, view session, wearable overlay appears on oximetry chart
- [ ] Manual: sleep stage hypnogram appears below oximetry card when stages are returned
- [ ] Manual: dashboard shows sleep stage stacked bar chart alongside AHI trend
- [ ] Manual: no provider configured → no charts, no errors

🤖 Generated with [Claude Code](https://claude.ai/claude-code)
EOF
)"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Migration ✓, adapters ✓, registry ✓, `/wearable/data` ✓, `/wearable/summary` ✓, import settings extensions ✓, Settings card ✓, `SpO2Chart` with overlay ✓, `WearableSleepStageChart` ✓, `WearableSleepSummaryChart` ✓, Dashboard integration ✓
- [x] **No placeholders:** All code is complete. Mirobody endpoint paths noted as verifiable.
- [x] **Type consistency:** `WearableData`, `WearableDailySummary`, `SpO2Response` defined once in Task 8 and referenced identically in Tasks 10–13. `get_adapter()` signature matches registry implementation.
- [x] **Migration caveat:** Renaming instruction present in Task 1 and PR description.
- [x] **`date` name shadowing:** Noted in Task 6 — import `date as date_type` in router.
