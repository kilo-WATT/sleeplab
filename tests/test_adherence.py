from datetime import date, timedelta

from fastapi.testclient import TestClient

from api.adherence import AdherencePolicy, NightlyUsage, calculate_adherence, evaluation_start
from api.auth import get_current_user
from api.database import get_db
from api.main import app

END_DATE = date(2026, 6, 18)
POLICY = AdherencePolicy()
START_DATE = evaluation_start(END_DATE, POLICY)


def _usage(report_date: date, seconds: int = 14400) -> NightlyUsage:
    return NightlyUsage(report_date=report_date, usage_seconds=seconds)


def _days(start: date, count: int, seconds: int = 14400) -> list[NightlyUsage]:
    return [_usage(start + timedelta(days=offset), seconds) for offset in range(count)]


def test_empty_period_counts_every_day_as_missing():
    result = calculate_adherence([], end_date=END_DATE)

    assert result.summary.total_evaluation_days == 90
    assert result.summary.days_with_therapy_data == 0
    assert result.summary.compliant_nights == 0
    assert result.summary.missing_nights == 90
    assert result.summary.noncompliant_nights_with_data == 0
    assert result.summary.compliance_percent == 0.0
    assert len(result.daily) == 90
    assert all(day.status == "missing" for day in result.daily)
    assert len(result.rolling_windows) == 61
    assert not result.current_window.qualifies
    assert not result.best_window.qualifies


def test_all_compliant_nights_qualify_every_window():
    result = calculate_adherence(_days(START_DATE, 90), end_date=END_DATE)

    assert result.summary.compliant_nights == 90
    assert result.summary.compliance_percent == 100.0
    assert result.current_window.compliant_nights == 30
    assert result.current_window.qualifies
    assert result.best_window.start_date == END_DATE - timedelta(days=29)
    assert all(window.qualifies for window in result.rolling_windows)


def test_missing_days_count_against_denominator():
    result = calculate_adherence(_days(START_DATE, 10), end_date=END_DATE)

    assert result.summary.days_with_therapy_data == 10
    assert result.summary.compliant_nights == 10
    assert result.summary.missing_nights == 80
    assert result.summary.compliance_percent == 11.1


def test_exactly_twenty_one_compliant_nights_in_current_window_qualifies():
    current_start = END_DATE - timedelta(days=29)
    result = calculate_adherence(_days(current_start, 21), end_date=END_DATE)

    assert result.current_window.compliant_nights == 21
    assert result.current_window.compliance_percent == 70.0
    assert result.current_window.qualifies


def test_twenty_compliant_nights_in_current_window_does_not_qualify():
    current_start = END_DATE - timedelta(days=29)
    result = calculate_adherence(_days(current_start, 20), end_date=END_DATE)

    assert result.current_window.compliant_nights == 20
    assert result.current_window.compliance_percent == 66.7
    assert not result.current_window.qualifies


def test_best_window_is_found_within_evaluation_period():
    best_start = START_DATE + timedelta(days=5)
    usage = _days(best_start, 25)
    result = calculate_adherence(usage, end_date=END_DATE)

    assert result.best_window.start_date == best_start
    assert result.best_window.end_date == best_start + timedelta(days=29)
    assert result.best_window.compliant_nights == 25
    assert result.best_window.qualifies
    assert result.current_window.compliant_nights == 0
    assert not result.current_window.qualifies


def test_current_window_is_always_the_thirty_days_ending_on_evaluation_end():
    current_start = END_DATE - timedelta(days=29)
    result = calculate_adherence(_days(current_start, 30), end_date=END_DATE)

    assert result.current_window.start_date == current_start
    assert result.current_window.end_date == END_DATE
    assert result.current_window.total_days == 30


def test_current_and_longest_streaks_are_calendar_consecutive():
    usage = [
        *_days(START_DATE + timedelta(days=10), 5),
        *_days(END_DATE - timedelta(days=2), 3),
    ]
    result = calculate_adherence(usage, end_date=END_DATE)

    assert result.streaks.longest_compliant_nights == 5
    assert result.streaks.current_compliant_nights == 3


def test_usage_below_four_hours_is_noncompliant_data_not_missing():
    result = calculate_adherence([_usage(END_DATE, 14399), _usage(END_DATE - timedelta(days=1), 0)], end_date=END_DATE)

    assert result.summary.days_with_therapy_data == 2
    assert result.summary.noncompliant_nights_with_data == 2
    assert result.summary.missing_nights == 88
    assert result.daily[-1].status == "noncompliant"
    assert result.daily[-1].usage_seconds == 14399


def test_evaluation_boundaries_are_inclusive_and_outside_data_is_ignored():
    usage = [
        _usage(START_DATE - timedelta(days=1)),
        _usage(START_DATE),
        _usage(END_DATE),
        _usage(END_DATE + timedelta(days=1)),
    ]
    result = calculate_adherence(usage, end_date=END_DATE)

    assert result.summary.start_date == START_DATE
    assert result.summary.end_date == END_DATE
    assert result.summary.days_with_therapy_data == 2
    assert result.daily[0].status == "compliant"
    assert result.daily[-1].status == "compliant"


def test_duplicate_or_multi_machine_rows_on_one_date_are_summed():
    result = calculate_adherence(
        [_usage(END_DATE, 7200), _usage(END_DATE, 7200)],
        end_date=END_DATE,
    )

    assert result.summary.days_with_therapy_data == 1
    assert result.summary.compliant_nights == 1
    assert result.daily[-1].usage_seconds == 14400
    assert result.daily[-1].status == "compliant"


class _FakeResult:
    def __init__(self, *, scalar_value=None, rows=None):
        self.scalar_value = scalar_value
        self.rows = rows or []

    def scalar(self):
        return self.scalar_value

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _AdherenceDb:
    def __init__(self, *, latest_date: date | None, rows: list[dict]):
        self.latest_date = latest_date
        self.rows = rows

    def execute(self, statement, _params):
        if "MAX(machine_local_date)" in str(statement):
            return _FakeResult(scalar_value=self.latest_date)
        return _FakeResult(rows=self.rows)


def _api_client(db: _AdherenceDb) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: {"id": "00000000-0000-0000-0000-000000000001"}
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestAdherenceApi:
    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_uses_latest_therapy_date_and_normalized_usage(self):
        latest = date(2026, 6, 18)
        db = _AdherenceDb(
            latest_date=latest,
            rows=[
                {"report_date": latest - timedelta(days=1), "usage_seconds": 3600},
                {"report_date": latest, "usage_seconds": 14400},
            ],
        )

        with _api_client(db) as client:
            response = client.get("/stats/adherence")

        assert response.status_code == 200
        payload = response.json()
        assert payload["policy"] == {
            "qualifying_usage_seconds": 14400,
            "required_percent": 70.0,
            "window_days": 30,
            "evaluation_days": 90,
        }
        assert payload["summary"]["end_date"] == latest.isoformat()
        assert payload["summary"]["days_with_therapy_data"] == 2
        assert payload["summary"]["compliant_nights"] == 1
        assert payload["summary"]["noncompliant_nights_with_data"] == 1
        assert payload["daily"][-1]["status"] == "compliant"

    def test_explicit_end_date_controls_period_boundary(self):
        with _api_client(_AdherenceDb(latest_date=None, rows=[])) as client:
            response = client.get("/stats/adherence?end_date=2026-05-31")

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["end_date"] == "2026-05-31"
        assert payload["summary"]["start_date"] == "2026-03-03"
        assert payload["summary"]["missing_nights"] == 90

    def test_no_data_defaults_evaluation_end_to_today(self):
        with _api_client(_AdherenceDb(latest_date=None, rows=[])) as client:
            response = client.get("/stats/adherence")

        assert response.status_code == 200
        assert response.json()["summary"]["end_date"] == date.today().isoformat()

    def test_invalid_end_date_is_rejected(self):
        with _api_client(_AdherenceDb(latest_date=None, rows=[])) as client:
            response = client.get("/stats/adherence?end_date=not-a-date")

        assert response.status_code == 422
