import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from api.therapy_score import compute_therapy_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importer"))

import db as importer_db
import import_sessions
from resmed_str import ResMedMaskInterval, ResMedSTRDay


@dataclass
class FakeSignal:
    label: str
    num_samples_per_record: int


@dataclass
class FakeHeader:
    start_datetime: datetime
    num_records: int = 1
    duration_per_record: float = 60.0
    device_serial: str = "SN123"

    @property
    def signals(self):
        return [FakeSignal("Flow.40ms", 60), FakeSignal("Press.40ms", 60)]


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConn:
    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _session_data(**overrides):
    data = {
        "session_id": "20260601_235949",
        "folder_date": date(2026, 6, 1),
        "block_index": 0,
        "start_datetime": datetime(2026, 6, 1, 23, 59, 49, tzinfo=UTC),
        "pld_start_datetime": datetime(2026, 6, 1, 23, 59, 49, tzinfo=UTC),
        "duration_seconds": 600,
        "device_serial": "SN123",
        "manufacturer": "ResMed",
        "ahi": 0.0,
        "central_apnea_count": 0,
        "obstructive_apnea_count": 0,
        "hypopnea_count": 0,
        "apnea_count": 0,
        "arousal_count": 0,
        "total_ahi_events": 0,
        "avg_pressure": None,
        "p95_pressure": None,
        "avg_leak": 0.170366,
        "avg_resp_rate": None,
        "avg_tidal_vol": None,
        "avg_min_vent": None,
        "avg_snore": None,
        "avg_flow_lim": None,
        "has_spo2": False,
        "therapy_mode": None,
        "mask_type": None,
        "humidity_level": None,
        "temperature_c": None,
        "machine_tz": "UTC",
        "user_id": "user-1",
    }
    data.update(overrides)
    return data


def _str_day(day: date, intervals: list[tuple[datetime, datetime]], max_pressure: float = 14.0):
    return ResMedSTRDay(
        machine_local_date=day,
        intervals=[
            ResMedMaskInterval(index=index, start=start, end=end)
            for index, (start, end) in enumerate(intervals)
        ],
        normalized_settings={
            "therapy_mode": "apap",
            "minimum_pressure_cm_h2o": 4.0,
            "maximum_pressure_cm_h2o": max_pressure,
            "ramp_mode": "auto",
            "mask_type": "nasal",
        },
        vendor_settings={"Mode": {"raw": 1}, "Max Press": {"raw": max_pressure}},
        source_names={"therapy_mode": "Mode", "maximum_pressure_cm_h2o": "Max Press"},
        summary_usage_seconds=sum(int((end - start).total_seconds()) for start, end in intervals),
        on_duration_seconds=int((intervals[-1][1] - intervals[0][0]).total_seconds()),
        patient_hours=None,
    )


def test_upsert_session_persists_manufacturer_without_null_clobber():
    conn = FakeConn(rows=[("machine-1",), (123,)])

    assert importer_db.upsert_session(conn, _session_data()) == 123

    sql, params = conn.cursor_obj.statements[1]
    assert "manufacturer" in sql
    assert "%(manufacturer)s" in sql
    assert "manufacturer            = COALESCE(NULLIF(EXCLUDED.manufacturer, ''), NULLIF(sessions.manufacturer, ''))" in sql
    assert params["manufacturer"] == "ResMed"


def test_upsert_session_defaults_unknown_manufacturer_to_null():
    conn = FakeConn(rows=[("machine-1",), (123,)])
    data = _session_data()
    data.pop("manufacturer")

    assert importer_db.upsert_session(conn, data) == 123

    _sql, params = conn.cursor_obj.statements[1]
    assert params["manufacturer"] is None


def test_resmed_str_persistence_is_duplicate_safe_and_incremental(db, test_user):
    raw_conn = db.connection().connection.driver_connection
    machine_id = importer_db.reconcile_machine(
        raw_conn,
        user_id=test_user["id"],
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number="TEST-STR-IDEMPOTENCY",
    )
    with raw_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, started_at
            ) VALUES (%s, %s, 'resmed-native-v2', 'directory', %s, 'running', 'partial', NOW())
            RETURNING id::text
            """,
            (test_user["id"], machine_id, f"fixture-{test_user['id']}"),
        )
        import_run_id = cur.fetchone()[0]

    tz = ZoneInfo("America/New_York")
    first_date = date(2026, 6, 1)
    first_intervals = [
        (
            datetime(2026, 6, 1, 22, 0, tzinfo=tz),
            datetime(2026, 6, 1, 23, 0, tzinfo=tz),
        ),
        (
            datetime(2026, 6, 1, 23, 15, tzinfo=tz),
            datetime(2026, 6, 2, 0, 15, tzinfo=tz),
        ),
    ]
    session_id = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=test_user["id"],
            machine_id=machine_id,
            import_run_id=import_run_id,
            source_session_key="test:2026-06-01",
            session_id="test_20260601",
            folder_date=first_date,
            start_datetime=first_intervals[0][0],
            pld_start_datetime=first_intervals[0][0],
            duration_seconds=8100,
            machine_tz=tz.key,
        ),
    )
    assert session_id

    original = _str_day(first_date, first_intervals)
    for _ in range(2):
        importer_db.replace_resmed_str_day(
            raw_conn,
            user_id=test_user["id"],
            machine_id=machine_id,
            import_run_id=import_run_id,
            adapter_id="resmed-native-v2",
            folder_date=first_date,
            str_day=original,
            source_file_id_value=None,
        )

    corrected_intervals = [
        first_intervals[0],
        (first_intervals[1][0], datetime(2026, 6, 2, 0, 30, tzinfo=tz)),
    ]
    importer_db.replace_resmed_str_day(
        raw_conn,
        user_id=test_user["id"],
        machine_id=machine_id,
        import_run_id=import_run_id,
        adapter_id="resmed-native-v2",
        folder_date=first_date,
        str_day=_str_day(first_date, corrected_intervals, max_pressure=15.0),
        source_file_id_value=None,
    )

    second_date = date(2026, 6, 2)
    second_intervals = [
        (
            datetime(2026, 6, 2, 21, 0, tzinfo=tz),
            datetime(2026, 6, 2, 22, 0, tzinfo=tz),
        )
    ]
    importer_db.replace_resmed_str_day(
        raw_conn,
        user_id=test_user["id"],
        machine_id=machine_id,
        import_run_id=import_run_id,
        adapter_id="resmed-native-v2",
        folder_date=second_date,
        str_day=_str_day(second_date, second_intervals, max_pressure=15.0),
        source_file_id_value=None,
    )

    with raw_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM session_blocks b
            JOIN sessions s ON s.id = b.session_id
            WHERE s.machine_id = %s AND b.source_kind = 'resmed_str_mask_interval'
            """,
            (machine_id,),
        )
        assert cur.fetchone()[0] == 3
        cur.execute(
            "SELECT COUNT(*) FROM settings_snapshots WHERE machine_id = %s",
            (machine_id,),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            """
            SELECT usage_seconds, wall_clock_seconds, gap_seconds, block_count
            FROM nightly_therapy_aggregates
            WHERE machine_id = %s AND machine_local_date = %s
            """,
            (machine_id, first_date),
        )
        assert cur.fetchone() == (8100, 9000, 900, 2)
        cur.execute(
            """
            SELECT normalized_settings->>'maximum_pressure_cm_h2o'
            FROM settings_snapshots
            WHERE machine_id = %s AND effective_at::date = %s
            """,
            (machine_id, first_date),
        )
        assert cur.fetchone()[0] == "15.0"


def test_upsert_session_reimport_is_idempotent(db, test_user):
    """Re-importing the same night must update the row in place, not duplicate it.

    The dedup key is the partial unique index uq_sessions_machine_source_key on
    (machine_id, source_session_key). This locks in that re-running an import for
    the same SD card produces no new session rows and overwrites stale summary
    values with the freshly parsed ones.
    """
    raw_conn = db.connection().connection.driver_connection
    machine_id = importer_db.reconcile_machine(
        raw_conn,
        user_id=test_user["id"],
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number="TEST-REIMPORT-IDEMPOTENCY",
    )
    with raw_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, started_at
            ) VALUES (%s, %s, 'resmed-native-v2', 'directory', %s, 'running', 'partial', NOW())
            RETURNING id::text
            """,
            (test_user["id"], machine_id, f"reimport-{test_user['id']}"),
        )
        import_run_id = cur.fetchone()[0]

    base = _session_data(
        user_id=test_user["id"],
        machine_id=machine_id,
        import_run_id=import_run_id,
        source_session_key="resmed:2026-06-01:0",
        session_id="20260601_235949",
        ahi=4.2,
        duration_seconds=8100,
    )

    first_id = importer_db.upsert_session(raw_conn, dict(base))

    # Second import of the same night with corrected summary values.
    second_id = importer_db.upsert_session(
        raw_conn,
        dict(base, ahi=5.7, duration_seconds=8400, total_ahi_events=13),
    )

    assert first_id == second_id, "re-import must reuse the existing session row"

    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND source_session_key = %s",
            (machine_id, "resmed:2026-06-01:0"),
        )
        assert cur.fetchone()[0] == 1, "re-import must not create a duplicate session"
        cur.execute(
            "SELECT ahi, duration_seconds, total_ahi_events FROM sessions WHERE id = %s",
            (first_id,),
        )
        ahi, duration_seconds, total_ahi_events = cur.fetchone()
        assert float(ahi) == 5.7
        assert duration_seconds == 8400
        assert total_ahi_events == 13


def test_dedupe_events_preserves_zero_duration_arousal():
    events = [
        (10.0, 0.0, "Arousal"),
        (10.0, 0.0, "Arousal"),
        (20.0, 10.0, "Central Apnea"),
        (20.0, 10.0, "Central Apnea"),
    ]

    assert import_sessions.dedupe_events(events) == [
        (10.0, 0.0, "Arousal"),
        (20.0, 10.0, "Central Apnea"),
    ]


def test_derive_large_leak_events_uses_end_time_and_duration():
    csl_start = datetime(2026, 6, 1, 23, 50, tzinfo=UTC)
    block_start = csl_start + timedelta(minutes=10)

    events = import_sessions.derive_large_leak_events(
        [0.1, 0.4, 0.5, 0.2, 0.4, 0.6],
        csl_start,
        block_start,
    )

    assert events == [
        (606.0, 4.0, "Large Leak"),
        (612.0, 4.0, "Large Leak"),
    ]


def test_derive_large_leak_events_ignores_values_below_threshold():
    start = datetime(2026, 6, 1, 23, 50, tzinfo=UTC)

    assert import_sessions.derive_large_leak_events(
        [0.0, 0.1, 0.39],
        start,
        start,
    ) == []


def test_replace_session_events_dedupes_before_insert_and_keeps_zero_duration(monkeypatch):
    inserted = []

    def fake_execute_values(_cur, _sql, rows, **_kwargs):
        inserted.extend(rows)

    monkeypatch.setattr(importer_db.psycopg2.extras, "execute_values", fake_execute_values)

    importer_db.replace_session_events(
        FakeConn(),
        123,
        [(10.0, 0.0, "Arousal"), (10.0, 0.0, "Arousal")],
        datetime(2026, 6, 2, 4, 0, tzinfo=UTC),
    )

    assert len(inserted) == 1
    assert inserted[0][3] == 0.0


def test_replace_session_waveform_uses_absolute_event_time(monkeypatch):
    inserted = []

    def fake_execute_values(_cur, _sql, rows, **_kwargs):
        inserted.extend(rows)

    monkeypatch.setattr(importer_db.psycopg2.extras, "execute_values", fake_execute_values)

    csl_start = datetime(2026, 6, 2, 4, 20, tzinfo=UTC)
    waveform_start = csl_start + timedelta(seconds=20)
    importer_db.replace_session_waveform(
        FakeConn(),
        123,
        FakeHeader(waveform_start),
        {"Flow.40ms": [float(i) for i in range(60)], "Press.40ms": [10.0 for _ in range(60)]},
        [(25.0, 10.0, "Central Apnea")],
        before_seconds=2,
        after_seconds=2,
        start_datetime=waveform_start,
        csl_start_datetime=csl_start,
    )

    assert inserted
    assert inserted[0][1] == waveform_start + timedelta(seconds=3)
    assert inserted[-1][1] == waveform_start + timedelta(seconds=17)


def test_import_folder_replaces_events_on_repeat_import(monkeypatch, tmp_path):
    folder = tmp_path / "20260601"
    folder.mkdir()
    for suffix in ("CSL", "EVE", "PLD", "BRP"):
        (folder / f"20260601_235949_{suffix}.edf").touch()

    event_calls = []
    waveform_calls = []

    monkeypatch.setattr(import_sessions, "_machine_tz_for_user", lambda _conn, _uid: ("UTC", UTC))
    monkeypatch.setattr(import_sessions, "parse_pld", lambda _path: (FakeHeader(datetime(2026, 6, 1, 23, 59, 49), num_records=10), {}))
    monkeypatch.setattr(
        import_sessions,
        "parse_eve",
        lambda _path: (
            FakeHeader(datetime(2026, 6, 1, 23, 59, 49)),
            [(120.0, 0.0, "Arousal"), (120.0, 0.0, "Arousal"), (180.0, 10.0, "Central Apnea")],
        ),
    )
    monkeypatch.setattr(import_sessions, "read_header", lambda _path: FakeHeader(datetime(2026, 6, 1, 23, 59, 49)))
    monkeypatch.setattr(import_sessions, "parse_brp", lambda _path: (FakeHeader(datetime(2026, 6, 1, 23, 59, 49)), {}))
    monkeypatch.setattr(import_sessions, "upsert_session", lambda _conn, _data: 123)
    monkeypatch.setattr(import_sessions, "replace_session_metrics", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_spo2", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_events", lambda _conn, _sid, events, _start: event_calls.append(events))
    monkeypatch.setattr(import_sessions, "replace_waveforms_for_block", lambda *_args: waveform_calls.append(True))

    conn = FakeConn()
    assert import_sessions.import_folder(folder, date(2026, 6, 1), conn, "user-1") == 1
    assert import_sessions.import_folder(folder, date(2026, 6, 1), conn, "user-1") == 1

    assert [len(events) for events in event_calls] == [2, 2]
    assert event_calls[0] == event_calls[1]
    assert len(waveform_calls) == 2


def test_resmed_import_folder_sets_manufacturer_and_scores_leak(monkeypatch, tmp_path):
    folder = tmp_path / "20260601"
    folder.mkdir()
    for suffix in ("CSL", "PLD"):
        (folder / f"20260601_235949_{suffix}.edf").touch()

    upserts = []

    def fake_upsert(_conn, data):
        upserts.append(dict(data))
        return 123

    monkeypatch.setattr(import_sessions, "_machine_tz_for_user", lambda _conn, _uid: ("UTC", UTC))
    monkeypatch.setattr(
        import_sessions,
        "parse_pld",
        lambda _path: (
            FakeHeader(datetime(2026, 6, 1, 23, 59, 49), num_records=10),
            {"Leak.2s": [0.170366], "Press.2s": [10.0]},
        ),
    )
    monkeypatch.setattr(import_sessions, "read_header", lambda _path: FakeHeader(datetime(2026, 6, 1, 23, 59, 49)))
    monkeypatch.setattr(import_sessions, "upsert_session", fake_upsert)
    monkeypatch.setattr(import_sessions, "replace_session_metrics", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_spo2", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_events", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_waveforms_for_block", lambda *_args: None)

    assert import_sessions.import_folder(folder, date(2026, 6, 1), FakeConn(), "user-1") == 1

    assert upserts[0]["manufacturer"] == "ResMed"
    assert upserts[0]["leak_kind"] == "unintentional"
    assert upserts[0]["leak_unit"] == "L/s"
    assert upserts[0]["avg_leak"] == 0.1704
    score = compute_therapy_score({**upserts[0], "parser_validated": True})
    assert score.components.leak is not None
    assert score.components.leak.value == 10.22
    assert score.components.leak.unit == "L/min"


def test_import_folder_assigns_repeated_eve_events_to_only_matching_split_block(monkeypatch, tmp_path):
    folder = tmp_path / "20260601"
    folder.mkdir()
    (folder / "20260601_000000_CSL.edf").touch()
    (folder / "20260601_000000_EVE.edf").touch()
    for stamp in ("000000", "010000", "020000"):
        (folder / f"20260601_{stamp}_PLD.edf").touch()

    starts = {
        "20260601_000000": datetime(2026, 6, 1, 0, 0, tzinfo=None),
        "20260601_010000": datetime(2026, 6, 1, 1, 0, tzinfo=None),
        "20260601_020000": datetime(2026, 6, 1, 2, 0, tzinfo=None),
    }
    inserted_by_session = {}

    def header_for_path(path):
        stem = Path(path).stem[:15]
        return FakeHeader(starts[stem], num_records=60)

    def fake_upsert(_conn, data):
        return data["session_id"]

    def fake_replace_events(_conn, session_id, events, _start):
        inserted_by_session[session_id] = list(events)

    monkeypatch.setattr(import_sessions, "_machine_tz_for_user", lambda _conn, _uid: ("UTC", UTC))
    monkeypatch.setattr(import_sessions, "parse_pld", lambda path: (header_for_path(path), {}))
    monkeypatch.setattr(import_sessions, "read_header", header_for_path)
    monkeypatch.setattr(
        import_sessions,
        "parse_eve",
        lambda _path: (
            FakeHeader(datetime(2026, 6, 1, 0, 0)),
            [
                (90 * 60.0, 10.0, "Central Apnea"),
                (150 * 60.0, 0.0, "Arousal"),
            ],
        ),
    )
    monkeypatch.setattr(import_sessions, "upsert_session", fake_upsert)
    monkeypatch.setattr(import_sessions, "replace_session_metrics", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_spo2", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_waveforms_for_block", lambda *_args: None)
    monkeypatch.setattr(import_sessions, "replace_session_events", fake_replace_events)

    assert import_sessions.import_folder(folder, date(2026, 6, 1), FakeConn(), "user-1") == 3

    assert inserted_by_session["20260601_000000"] == []
    assert inserted_by_session["20260601_010000"] == [(90 * 60.0, 10.0, "Central Apnea")]
    assert inserted_by_session["20260601_020000"] == [(150 * 60.0, 0.0, "Arousal")]
