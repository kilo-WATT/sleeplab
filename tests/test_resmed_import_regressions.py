import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from api.therapy_score import compute_therapy_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importer"))

import db as importer_db
import import_sessions
from edf_parser import parse_sa2, read_header
from resmed_str import ResMedMaskInterval, ResMedSTRDay

#: Committed, anonymized AirSense 10 fixture (serial replaced, timestamps shifted).
_AIRSENSE10_DATALOG = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "conformance"
    / "fixtures"
    / "resmed_airsense10_001"
    / "DATALOG"
)


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


def test_upsert_session_persists_p95_leak_and_defaults_it_when_absent():
    """The leak 95th percentile is its own column, defaulted (not required).

    Regression for the nightly leak card showing the pressure percentile: leak
    must carry a dedicated ``p95_leak`` column. Callers that predate it (legacy
    importer, STR-only days) omit the key, so ``upsert_session`` must default it
    to NULL rather than KeyError, while still threading a supplied value through.
    """
    conn = FakeConn(rows=[("machine-1",), (123,)])
    data = _session_data(p95_leak=2.4)
    assert importer_db.upsert_session(conn, data) == 123

    sql, params = conn.cursor_obj.statements[1]
    # The column and its bind parameter are present and distinct from pressure.
    assert "p95_leak" in sql
    assert "%(p95_leak)s" in sql
    assert "p95_leak                = EXCLUDED.p95_leak" in sql
    assert params["p95_leak"] == 2.4

    # A caller that never sets p95_leak (legacy path) still upserts cleanly.
    legacy_conn = FakeConn(rows=[("machine-1",), (124,)])
    legacy_data = _session_data()
    legacy_data.pop("p95_leak", None)
    assert importer_db.upsert_session(legacy_conn, legacy_data) == 124
    _legacy_sql, legacy_params = legacy_conn.cursor_obj.statements[1]
    assert legacy_params["p95_leak"] is None


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


def test_parser_child_cleanup_removes_stale_blocks_and_settings():
    """Corrected parser output replaces generated child sets, including removals."""
    from importer.loaders import persist

    conn = FakeConn()

    persist._clear_parser_session_children(conn, "session-1")

    assert conn.cursor_obj.statements == [
        ("DELETE FROM session_blocks WHERE session_id = %s", ("session-1",)),
        (
            "DELETE FROM settings_snapshots WHERE session_id = %s AND adapter_id = %s",
            ("session-1", "resmed-cpap-parser-v1"),
        ),
    ]


def test_finish_import_run_marks_parser_consumed_roles_without_fake_links():
    conn = FakeConn()

    importer_db.finish_import_run(
        conn,
        "run-1",
        status="success",
        imported_sessions=1,
        imported_blocks=1,
        imported_events=1,
        imported_channels=1,
        imported_settings=1,
        summary_only_days=0,
        warnings=[],
        errors=[],
        consumed_unlinked_roles=("summary", "events", "waveform"),
    )

    consumed_sql, consumed_params = conn.cursor_obj.statements[0]
    assert "SET disposition = 'used'" in consumed_sql
    assert "consumed_without_row_link" in consumed_params[0]
    assert consumed_params[1] == "run-1"
    assert consumed_params[2] == ["summary", "events", "waveform"]
    skipped_sql, _skipped_params = conn.cursor_obj.statements[1]
    assert "disposition = 'unknown'" in skipped_sql


def test_replace_derived_values_accepts_empty_corrected_summary(monkeypatch):
    """A re-import with no derived values clears stale rows without bulk inserting."""
    calls = []
    monkeypatch.setattr(
        importer_db.psycopg2.extras,
        "execute_values",
        lambda *_args, **_kwargs: calls.append(True),
    )
    conn = FakeConn()

    count = importer_db.replace_derived_values(
        conn,
        user_id="user-1",
        machine_id="machine-1",
        session_db_id="session-1",
        import_run_id="run-1",
        adapter_id="resmed-cpap-parser-v1",
        summary={},
    )

    assert count == 0
    assert calls == []
    assert conn.cursor_obj.statements == [
        ("DELETE FROM derived_values WHERE session_id = %s", ("session-1",))
    ]


def test_normalized_signal_inventory_classifies_brp_and_pld_channels():
    """Alpha 6 item 1: pin the native ResMed BRP/PLD channel inventory.

    ``_normalized_signal`` is the single place that maps a source EDF label to a
    normalized channel name, classifies it as ``waveform`` vs ``low_rate`` by
    sample rate, and tags leak semantics. This locks the inventory the
    ``signal_channels`` table is populated from so a parser/label change can't
    silently reclassify a channel.
    """
    # High-rate BRP waveform channels (Flow.40ms / Press.40ms ~ 25 Hz).
    assert importer_db._normalized_signal("Flow.40ms", 25.0) == ("flow", "waveform", None)
    assert importer_db._normalized_signal("Press.40ms", 25.0) == ("pressure", "waveform", None)

    # Low-rate PLD channels (.2s ~ 0.5 Hz). Leak carries unintentional semantics.
    assert importer_db._normalized_signal("Leak.2s", 0.5) == ("leak", "low_rate", "unintentional")
    assert importer_db._normalized_signal("Press.2s", 0.5) == ("pressure", "low_rate", None)
    assert importer_db._normalized_signal("RespRate.2s", 0.5) == ("respiratory_rate", "low_rate", None)
    assert importer_db._normalized_signal("MaskPress.2s", 0.5) == ("mask_pressure", "low_rate", None)


def test_normalized_signal_classification_boundary_is_5hz():
    """The waveform/low-rate split is sample_rate >= 5 Hz; absence stays low-rate.

    Guards the exact threshold used by ``replace_signal_channels`` so a channel
    sitting on the boundary (or with an unknown rate) is classified
    deterministically rather than by accident.
    """
    assert importer_db._normalized_signal("Press.2s", 5.0)[1] == "waveform"
    assert importer_db._normalized_signal("Press.2s", 4.999)[1] == "low_rate"
    # An unknown/absent sample rate must not be treated as high-rate.
    assert importer_db._normalized_signal("Press.2s", None)[1] == "low_rate"


def test_normalized_signal_maps_sa2_oximetry_channels():
    """Alpha 6 item 1: SA2/SAD oximetry channels are inventoried, not unknown.

    Real ResMed SA2/SAD files (verified against the anonymized AirSense 10
    fixture) carry ``SpO2.1s`` (unit ``%``) and ``Pulse.1s`` (unit ``bpm``) at
    1 Hz. They normalize to ``spo2``/``pulse`` and stay ``low_rate`` at that
    rate. Oximetry channels carry no leak semantics.
    """
    assert importer_db._normalized_signal("SpO2.1s", 1.0) == ("spo2", "low_rate", None)
    assert importer_db._normalized_signal("Pulse.1s", 1.0) == ("pulse", "low_rate", None)

    # Classification still follows the sample-rate rule, not the channel name:
    # an oximetry label arriving at high rate would be a waveform.
    assert importer_db._normalized_signal("SpO2.1s", 25.0)[1] == "waveform"


def test_airsense10_fixture_sad_files_contain_no_usable_oximetry():
    """The committed SAD files prove metadata presence, not SpO2 sample behavior.

    All six files advertise Pulse/SpO2 channels, but every SpO2 sample is the
    ResMed missing sentinel (``-1``). ``parse_sa2`` therefore returns ``None``,
    which is why the legacy importer writes no ``session_spo2`` rows and leaves
    ``sessions.has_spo2`` false. A future oximetry parity test needs a safely
    redistributable fixture with real, non-missing samples.
    """
    import pytest

    paths = sorted(_AIRSENSE10_DATALOG.glob("**/*_SAD.edf"))
    if not paths:
        pytest.skip("AirSense 10 fixture SAD EDF files not present")

    assert len(paths) == 6
    for path in paths:
        header, oximetry = parse_sa2(str(path))
        assert [signal.label for signal in header.signals[:2]] == ["Pulse.1s", "SpO2.1s"]
        assert oximetry is None, f"{path.name} unexpectedly contains usable SpO2 samples"


def test_normalized_signal_unknown_channel_is_flagged_not_silently_mapped():
    """Alpha 6 absence diagnostics: unknown channels surface, never get aliased.

    The loader-plan invariant is "never silently map an unknown channel to a
    known one". A label outside the known inventory must come back with the
    explicit ``unknown:`` sentinel (preserving the raw source label) rather than
    being folded into a known normalized name, so a missing mapping is
    diagnosable from the persisted channel row instead of lost.
    """
    assert importer_db._normalized_signal("Movement.2s", 0.5) == ("unknown:Movement.2s", "low_rate", None)

    novel = importer_db._normalized_signal("Totally.Novel", 12.0)
    assert novel == ("unknown:Totally.Novel", "waveform", None)
    assert novel[0].startswith("unknown:")


#: Expected BRP/PLD/SAD channel inventory for the committed AirSense 10 fixture,
#: keyed by EDF source label -> (normalized_name, channel_kind, leak_kind, dim).
#: Sourced by decoding the fixture with the pure-Python ``edf_parser`` (no
#: cpap-py), so this pins what the native path actually ingests. ``Crc16`` is a
#: per-record checksum, not a signal, and is intentionally absent (skipped by
#: ``replace_signal_channels``).
_AIRSENSE10_EXPECTED_CHANNELS = {
    # BRP: high-rate waveform at 25 Hz.
    "Flow.40ms": ("flow", "waveform", None, "L/s"),
    "Press.40ms": ("pressure", "waveform", None, "cmH2O"),
    # PLD: low-rate metrics at 0.5 Hz (2 s epoch).
    "MaskPress.2s": ("mask_pressure", "low_rate", None, "cmH2O"),
    "Press.2s": ("pressure", "low_rate", None, "cmH2O"),
    "EprPress.2s": ("epr_pressure", "low_rate", None, "cmH2O"),
    "Leak.2s": ("leak", "low_rate", "unintentional", "L/s"),
    "RespRate.2s": ("respiratory_rate", "low_rate", None, "bpm"),
    "TidVol.2s": ("tidal_volume", "low_rate", None, "L"),
    "MinVent.2s": ("minute_ventilation", "low_rate", None, "L/min"),
    "Snore.2s": ("snore", "low_rate", None, ""),
    "FlowLim.2s": ("flow_limitation", "low_rate", None, ""),
    # SAD/SA2: oximetry at 1 Hz (low-rate).
    "SpO2.1s": ("spo2", "low_rate", None, "%"),
    "Pulse.1s": ("pulse", "low_rate", None, "bpm"),
}


def _fixture_channels_by_suffix(suffix: str) -> dict[str, tuple[str, float | None]]:
    """Decode the first fixture EDF of ``suffix`` (BRP/PLD/SAD) via the native reader.

    Returns ``{label: (dim, sample_rate_hz)}`` for non-``Crc16`` signals. Pure
    Python — no cpap-py — so it runs in the normal suite. Skips the test if the
    committed fixture is absent (e.g. a sparse checkout).
    """
    import pytest

    matches = sorted(_AIRSENSE10_DATALOG.glob(f"**/*_{suffix}.edf"))
    if not matches:
        pytest.skip(f"AirSense 10 fixture {suffix} EDF not present")
    header = read_header(str(matches[0]))
    duration = header.duration_per_record or 0.0
    channels: dict[str, tuple[str, float | None]] = {}
    for signal in header.signals:
        if signal.label == "Crc16":
            continue
        rate = signal.num_samples_per_record / duration if duration > 0 else None
        channels[signal.label] = (signal.dim, rate)
    return channels


def test_airsense10_fixture_channel_inventory_matches_classification():
    """Alpha 6 §1: enumerate the fixture's real BRP/SA2 channels and pin classification.

    Decodes the committed (anonymized) AirSense 10 fixture with the pure-Python
    ``edf_parser`` — no cpap-py, so this is real decoded evidence that runs in the
    normal suite — and asserts every BRP/PLD/SAD channel maps through
    ``db._normalized_signal`` to the expected normalized name, channel kind
    (waveform vs low_rate), and leak semantics at its *real* sample rate. This
    closes §1's "enumerate every channel the fixture carries" and "verify the
    >=5 Hz classification matches the real rates; capture any misclassification".
    """
    observed: dict[str, tuple[str, float | None]] = {}
    for suffix in ("BRP", "PLD", "SAD"):
        observed.update(_fixture_channels_by_suffix(suffix))

    # Every decoded channel is a known, correctly-classified channel — no
    # ``unknown:`` sentinel, i.e. no misclassification on the real fixture.
    for label, (dim, rate) in observed.items():
        assert label in _AIRSENSE10_EXPECTED_CHANNELS, f"unexpected fixture channel {label!r}"
        exp_name, exp_kind, exp_leak, exp_dim = _AIRSENSE10_EXPECTED_CHANNELS[label]
        normalized, kind, leak = importer_db._normalized_signal(label, rate)
        assert normalized == exp_name, f"{label}: normalized {normalized!r} != {exp_name!r}"
        assert kind == exp_kind, f"{label}: kind {kind!r} != {exp_kind!r} (rate={rate})"
        assert leak == exp_leak, f"{label}: leak {leak!r} != {exp_leak!r}"
        assert not normalized.startswith("unknown:"), f"{label} misclassified as unknown"
        assert dim == exp_dim, f"{label}: dim {dim!r} != {exp_dim!r}"

    # The full expected inventory is present (nothing silently dropped).
    assert set(observed) == set(_AIRSENSE10_EXPECTED_CHANNELS), (
        f"fixture channels {sorted(observed)} != expected {sorted(_AIRSENSE10_EXPECTED_CHANNELS)}"
    )

    # The >=5 Hz waveform rule holds on the real rates: only BRP is waveform.
    waveform = {label for label, (_dim, rate) in observed.items() if rate and rate >= 5}
    assert waveform == {"Flow.40ms", "Press.40ms"}


def test_persist_signal_channel_metadata_classifies_by_5hz_rule(monkeypatch):
    """Alpha 6 §1: the cpap-parser persist path classifies by the same >=5 Hz rule.

    ``persist._replace_signal_channel_metadata`` is the second classification site
    (the cpap-parser execution path). Pin that a high-rate BRP channel persists as
    ``waveform`` and a low-rate PLD channel as ``low_rate``, so the rule cannot
    drift away from ``db._normalized_signal``.
    """
    import psycopg2.extras

    from importer.loaders import persist
    from importer.loaders.models import SignalChannel

    inserted = []

    def fake_execute_values(_cur, _sql, rows, **_kwargs):
        inserted.extend(rows)

    monkeypatch.setattr(psycopg2.extras, "execute_values", fake_execute_values)

    signals = [
        SignalChannel(
            channel_key="flow_rate", source_label="Flow.40ms", unit="L/s",
            sample_rate_hz=25.0, value_kind="sample", leak_kind=None, source_file_ids=(),
        ),
        SignalChannel(
            channel_key="set_pressure", source_label="Press.2s", unit="cmH2O",
            sample_rate_hz=0.5, value_kind="sample", leak_kind=None, source_file_ids=(),
        ),
    ]

    persist._replace_signal_channel_metadata(
        FakeConn(), session_db_id="sess-1", import_run_id=None, signals=signals
    )

    # Row layout index 3 = normalized_name, index 6 = sample_rate, index 7 = channel_kind.
    kind_by_name = {row[3]: row[7] for row in inserted}
    assert kind_by_name["flow_rate"] == "waveform"
    assert kind_by_name["set_pressure"] == "low_rate"


def test_replace_signal_channels_persists_sa2_units_and_low_rate(monkeypatch):
    """End-to-end: SA2 oximetry rows carry correct units and low-rate kind.

    ``_normalized_signal`` sets names/kinds; the unit comes from the EDF ``dim``
    field via ``replace_signal_channels``. This pins that SpO2/Pulse persist with
    units ``%``/``bpm`` at ``low_rate``, and that the ``Crc16`` checksum channel
    is skipped (not inventoried).
    """
    from types import SimpleNamespace

    inserted = []

    def fake_execute_values(_cur, _sql, rows, **_kwargs):
        inserted.extend(rows)

    monkeypatch.setattr(importer_db.psycopg2.extras, "execute_values", fake_execute_values)

    header = SimpleNamespace(
        duration_per_record=60.0,
        signals=[
            SimpleNamespace(label="Pulse.1s", dim="bpm", num_samples_per_record=60),
            SimpleNamespace(label="SpO2.1s", dim="%", num_samples_per_record=60),
            SimpleNamespace(label="Crc16", dim="", num_samples_per_record=1),
        ],
    )

    count = importer_db.replace_signal_channels(
        FakeConn(),
        session_db_id="sess-1",
        import_run_id=None,
        source_file_id_value=None,
        adapter_id="resmed-native-v2",
        header=header,
    )

    # Crc16 is skipped; only the two oximetry channels are inventoried.
    assert count == 2
    # Row layout: (session_id, import_run_id, source_file_id, normalized_name,
    #              source_name, unit, sample_rate_hz, channel_kind, value_kind,
    #              leak_kind, adapter_id, confidence, validation_status)
    by_name = {row[3]: row for row in inserted}
    assert set(by_name) == {"pulse", "spo2"}

    pulse = by_name["pulse"]
    assert pulse[4] == "Pulse.1s"
    assert pulse[5] == "bpm"
    assert pulse[6] == 1.0
    assert pulse[7] == "low_rate"

    spo2 = by_name["spo2"]
    assert spo2[4] == "SpO2.1s"
    assert spo2[5] == "%"
    assert spo2[6] == 1.0
    assert spo2[7] == "low_rate"


def test_summary_only_night_emits_absence_diagnostic_warning():
    """Alpha 6 absence diagnostics: a night with no detailed DATALOG records WHY.

    A summary-only ("ghost") night has STR history but no BRP/PLD source, so no
    waveform or metric samples are persisted for it. The native loader must emit
    a structured ``resmed_summary_only_day`` warning explaining the absence (kept
    and flagged, not deleted) instead of letting the night silently appear empty.
    """
    from types import SimpleNamespace

    from importer.loaders.resmed_native import ResMedNativeLoader

    summary = SimpleNamespace(
        date=date(2026, 6, 2),
        has_detailed_data=False,
        summary_reported_usage=7.5,
        computed_usage=None,
        recording_span=None,
        ahi=3.1,
    )

    session = ResMedNativeLoader()._build_session(
        summary,
        detailed=[],
        machine_key="SN-TEST",
        include_waveforms=False,
        run_warnings=[],
    )

    warning = next(w for w in session.warnings if w.code == "resmed_summary_only_day")
    assert warning.severity == "info"
    assert warning.relative_path == "STR.edf"
    assert "summary_only_days" in warning.affects
    assert "sessions" in warning.affects
    # Absence is empty, not a fabricated waveform.
    assert session.waveforms == []


def test_detailed_night_does_not_emit_summary_only_absence_warning():
    """The absence diagnostic is specific to absence.

    A night that ships detailed DATALOG data must NOT be flagged summary-only,
    so the warning stays a meaningful signal rather than firing on every night.
    """
    from types import SimpleNamespace

    from importer.loaders.resmed_native import ResMedNativeLoader

    summary = SimpleNamespace(
        date=date(2026, 6, 1),
        has_detailed_data=True,
        summary_reported_usage=8.0,
        computed_usage=8.0,
        recording_span=8.0,
        ahi=1.0,
    )
    detailed = [
        SimpleNamespace(
            start_time=datetime(2026, 6, 1, 22, 0),
            end_time=datetime(2026, 6, 2, 6, 0),
            file_type="BRP",
            events=[],
            timeseries=None,
        )
    ]

    session = ResMedNativeLoader()._build_session(
        summary,
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=False,
        run_warnings=[],
    )

    assert "resmed_summary_only_day" not in {w.code for w in session.warnings}


def _detailed_session(*, flow_rate, set_pressure, **overrides):
    """Build a fake cpap-py file-session for ``_build_session`` waveform tests.

    ``flow_rate`` is the high-rate BRP channel; ``set_pressure`` is a low-rate
    PLD channel. ``leak`` is left empty so large-leak derivation is skipped.
    """
    from types import SimpleNamespace

    timeseries = SimpleNamespace(
        flow_rate=flow_rate,
        pressure=[],
        mask_pressure=[],
        set_pressure=set_pressure,
        epr_pressure=[],
        leak=[],
        tidal_volume=[],
        minute_ventilation=[],
        respiratory_rate=[],
        snore=[],
        flow_limitation=[],
        timestamps_low=[0.0, 2.0],
    )
    return SimpleNamespace(
        start_time=datetime(2026, 6, 1, 22, 0),
        end_time=datetime(2026, 6, 2, 6, 0),
        file_type="PLD",
        events=[],
        sample_rate=25.0,
        timeseries=timeseries,
        **overrides,
    )


def _waveform_summary():
    from types import SimpleNamespace

    return SimpleNamespace(
        date=date(2026, 6, 1),
        has_detailed_data=True,
        summary_reported_usage=8.0,
        computed_usage=8.0,
        recording_span=8.0,
        ahi=1.0,
    )


def test_detailed_night_without_brp_waveform_emits_waveform_absent_diagnostic():
    """Alpha 6: a detailed night with PLD/session data but no BRP waveform.

    The night is real (has detailed DATALOG/PLD data) but ships no high-rate
    BRP samples. The loader must flag ``resmed_waveform_absent`` — affecting
    waveform availability, not the session's existence — and must NOT fabricate
    high-rate samples to make the night look complete.
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    detailed = [_detailed_session(flow_rate=[], set_pressure=[10.0, 11.0])]

    session = ResMedNativeLoader()._build_session(
        _waveform_summary(),
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=[],
    )

    warning = next(w for w in session.warnings if w.code == "resmed_waveform_absent")
    assert warning.severity == "warning"  # gap, not parse failure -> not forced partial
    assert warning.affects == ("waveforms",)
    assert warning.relative_path == "DATALOG"
    # Session still exists; this is a summary/low-rate night, not a ghost night.
    assert "resmed_summary_only_day" not in {w.code for w in session.warnings}
    # No fabricated high-rate samples: no flow_rate/pressure segment was created.
    assert not ResMedNativeLoader._has_high_rate_waveform(session.waveforms)


def test_detailed_night_with_brp_waveform_has_no_absence_diagnostic():
    """A detailed night that ships BRP high-rate samples is not flagged absent."""
    from importer.loaders.resmed_native import ResMedNativeLoader

    detailed = [_detailed_session(flow_rate=[1.0, 2.0, 3.0], set_pressure=[10.0, 11.0])]

    session = ResMedNativeLoader()._build_session(
        _waveform_summary(),
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=[],
    )

    assert "resmed_waveform_absent" not in {w.code for w in session.warnings}
    assert ResMedNativeLoader._has_high_rate_waveform(session.waveforms)


def test_missing_brp_waveform_does_not_fabricate_signal_channels():
    """Alpha 6 decision: waveform absence is row-absence, not a fake channel.

    ``signal_channels`` is a *presence* table — both writers
    (``persist._write_signal_channels`` and ``db.replace_signal_channels``) only
    emit a row for a channel that actually carries data, and stamp a fixed
    ``validation_status='partial'``. A detailed night missing its BRP waveform
    therefore must NOT gain a fabricated high-rate ``flow_rate``/``pressure``
    channel just to mark it absent; the low-rate PLD channels that *are* present
    still appear, and the absence is recorded at the run level via
    ``resmed_waveform_absent`` (and capability coverage), not by inventing a
    channel row. This pins Option B from the Alpha 6 checklist (§4).
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    session = ResMedNativeLoader()._build_session(
        _waveform_summary(),
        detailed=[_detailed_session(flow_rate=[], set_pressure=[10.0, 11.0])],
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=[],
    )

    channel_keys = {signal.channel_key for signal in session.signals}
    # Present low-rate PLD channel is inventoried.
    assert "set_pressure" in channel_keys
    # Missing high-rate BRP channels are NOT fabricated as signal_channels rows.
    assert "flow_rate" not in channel_keys
    assert "pressure" not in channel_keys
    # Absence is surfaced as a run-level diagnostic instead.
    assert "resmed_waveform_absent" in {w.code for w in session.warnings}


def test_build_session_flushes_absence_warnings_to_run_level():
    """Alpha 6: session absence warnings reach the run-level list for persistence.

    ``_build_session`` carries diagnostics on ``session.warnings``, but only the
    *run-level* ``run.warnings`` is serialized into ``import_runs.warnings`` (via
    ``execution._warning_dict`` -> ``finish_import_run``) and shown in import
    history. This pins that both ``resmed_summary_only_day`` and
    ``resmed_waveform_absent`` are flushed into the shared ``run_warnings`` list
    so they survive past the in-memory ``ImportRun``.
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    loader = ResMedNativeLoader()
    run_warnings = []

    # Ghost night: no detailed DATALOG -> summary-only diagnostic.
    ghost = SimpleNamespace(
        date=date(2026, 6, 2),
        has_detailed_data=False,
        summary_reported_usage=7.5,
        computed_usage=None,
        recording_span=None,
        ahi=3.1,
    )
    loader._build_session(
        ghost, detailed=[], machine_key="SN-TEST", include_waveforms=True, run_warnings=run_warnings
    )

    # Detailed night with PLD data but no BRP waveform -> waveform-absent.
    loader._build_session(
        _waveform_summary(),
        detailed=[_detailed_session(flow_rate=[], set_pressure=[10.0, 11.0])],
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=run_warnings,
    )

    codes = {w.code for w in run_warnings}
    assert "resmed_summary_only_day" in codes
    assert "resmed_waveform_absent" in codes
    # Proof 4: non-error warnings do not force the import to fail/partial.
    # (``import_data`` escalates status only when a warning has severity "error".)
    assert not any(w.severity == "error" for w in run_warnings)


def test_run_warnings_persist_as_structured_diagnostics():
    """Alpha 6: persisted diagnostics keep code/severity/affects/path, not a string.

    Drives the exact serialization the cpap-parser execution path uses —
    ``execution._warning_dict`` then ``db._dedupe_diagnostics`` — and asserts the
    structured fields survive (proof 3) and that repeated identical night
    warnings collapse to a single entry rather than flooding import history.
    """
    from importer.db import _dedupe_diagnostics
    from importer.loaders.execution import _warning_dict
    from importer.loaders.resmed_native import ResMedNativeLoader

    loader = ResMedNativeLoader()
    run_warnings = []
    # Two ghost nights produce the *same* summary-only diagnostic; one detailed
    # night without BRP produces the waveform-absent diagnostic.
    for day in (date(2026, 6, 2), date(2026, 6, 3)):
        loader._build_session(
            SimpleNamespace(
                date=day,
                has_detailed_data=False,
                summary_reported_usage=7.5,
                computed_usage=None,
                recording_span=None,
                ahi=3.1,
            ),
            detailed=[],
            machine_key="SN-TEST",
            include_waveforms=True,
            run_warnings=run_warnings,
        )
    loader._build_session(
        _waveform_summary(),
        detailed=[_detailed_session(flow_rate=[], set_pressure=[10.0, 11.0])],
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=run_warnings,
    )

    persisted = _dedupe_diagnostics([_warning_dict(w) for w in run_warnings])

    by_code = {d["code"]: d for d in persisted}
    # Duplicate ghost-night warnings collapsed to one; waveform-absent retained.
    assert sorted(by_code) == ["resmed_summary_only_day", "resmed_waveform_absent"]
    assert len(persisted) == 2

    waveform = by_code["resmed_waveform_absent"]
    # Structured fields, not a flattened string.
    assert waveform["severity"] == "warning"
    assert waveform["affects"] == ["waveforms"]
    assert waveform["relative_path"] == "DATALOG"
    assert isinstance(waveform["message"], str) and waveform["message"]

    summary_only = by_code["resmed_summary_only_day"]
    assert summary_only["severity"] == "info"
    assert summary_only["affects"] == ["sessions", "summary_only_days"]
    assert summary_only["relative_path"] == "STR.edf"


# ---------------------------------------------------------------------------
# ResMed normalized-output gap documentation (Phase 2).
#
# These pin the *current shape* of ``ResMedNativeLoader``'s normalized output so
# the gap audit (``docs/sleeplab_2_resmed_normalized_output_gap_audit.md``) stays
# honest: which ``expected.import`` blocks the loader can already feed, and which
# it cannot. They drive ``_build_session`` directly with the same parser-free
# fakes the absence-diagnostic tests use, so they run with no ``cpap-py`` backend.
# ---------------------------------------------------------------------------


def _summary_with_mode(pressure_mode):
    """A summary fake carrying a ``pressure_mode`` (the only setting cpap-parser exposes)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        date=date(2026, 6, 1),
        has_detailed_data=True,
        summary_reported_usage=8.0,
        computed_usage=8.0,
        recording_span=8.0,
        ahi=1.0,
        pressure_mode=pressure_mode,
    )


def test_build_session_maps_therapy_mode_settings_snapshot():
    """Loader now maps ``pressure_mode`` → one ``SettingsSnapshot.therapy_mode``.

    cpap-parser exposes exactly one normalized therapy *setting* — the daily
    summary's ``pressure_mode`` label — so ``_build_session`` emits a single
    ``SettingsSnapshot`` carrying *only* ``therapy_mode`` (no fabricated min/max/
    set pressure, EPR, ramp, humidifier, or mask_type — those are absent from the
    parser schema). Source provenance is preserved and ``effective_at`` reuses the
    session's own ``start_time`` anchor.
    """
    from importer.loaders.models import Confidence
    from importer.loaders.resmed_native import ResMedNativeLoader

    detailed = [_detailed_session(flow_rate=[1.0, 2.0], set_pressure=[10.0, 11.0])]

    session = ResMedNativeLoader()._build_session(
        _summary_with_mode("APAP"),
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=[],
    )

    assert len(session.settings) == 1
    snapshot = session.settings[0]
    # Only therapy_mode is mapped — nothing fabricated for absent fields.
    assert snapshot.settings == {"therapy_mode": "APAP"}
    assert snapshot.source_names == {"therapy_mode": "pressure_mode"}
    assert snapshot.source_file_ids == ("STR.edf",)
    assert snapshot.confidence == Confidence.PROBABLE
    # effective_at reuses the existing session anchor, not an invented date.
    assert snapshot.effective_at == session.start_time


def test_build_session_omits_settings_when_mode_unknown_or_absent():
    """Missing/unknown therapy mode stays absent — never a fabricated placeholder.

    ``pressure_mode`` is ``""`` when STR.edf has no mode and the literal
    ``"Unknown"`` for an unmapped mode code; a summary fake may also omit the
    attribute entirely. In every "no real setting" case ``Session.settings`` must
    stay empty — never coerced to ``"Unknown"``/``""``/a fake snapshot — so the
    conformance missing-≠-off semantics hold against a real card.
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    loader = ResMedNativeLoader()
    detailed = [_detailed_session(flow_rate=[1.0, 2.0], set_pressure=[10.0, 11.0])]

    for summary in (_summary_with_mode("Unknown"), _summary_with_mode(""), _waveform_summary()):
        session = loader._build_session(
            summary,
            detailed=detailed,
            machine_key="SN-TEST",
            include_waveforms=True,
            run_warnings=[],
        )
        assert session.settings == [], summary


def test_persist_session_row_flattens_only_present_settings():
    """Persist bridge flattens loader settings onto the ``sessions`` columns.

    Parser-free: drives ``persist._session_row`` directly. A session carrying a
    ``therapy_mode`` snapshot must set ``sessions.therapy_mode``; every settings
    column the loader does not expose (mask_type / humidity_level / temperature_c)
    stays ``None`` — never fabricated. A session with no settings keeps them all
    ``None``.
    """
    from importer.loaders import persist
    from importer.loaders.models import Confidence, Session, SettingsSnapshot

    start = datetime(2026, 6, 1, 22, 0)

    def _session(settings):
        return Session(
            source_session_key="resmed:SET:2026-06-01",
            machine_key="SET",
            start_time=start,
            end_time=start + timedelta(hours=8),
            machine_local_date="2026-06-01",
            timezone_basis="machine_local",
            settings=list(settings),
        )

    snapshot = SettingsSnapshot(
        effective_at=start,
        settings={"therapy_mode": "APAP"},
        source_names={"therapy_mode": "pressure_mode"},
        source_file_ids=("STR.edf",),
        confidence=Confidence.PROBABLE,
    )

    def _row(session):
        return persist._session_row(
            session=session,
            user_id="u",
            machine_id="m",
            import_run_id="r",
            folder_date=date(2026, 6, 1),
            start_dt=start,
            duration_seconds=28800,
            derived={},
            serial="SER",
            manufacturer="ResMed",
            machine_tz_name="UTC",
            has_detailed=True,
        )

    with_settings = _row(_session([snapshot]))
    assert with_settings["therapy_mode"] == "APAP"
    # Absent fields stay None — no fabricated mask/humidity/temperature.
    assert with_settings["mask_type"] is None
    assert with_settings["humidity_level"] is None
    assert with_settings["temperature_c"] is None

    without = _row(_session([]))
    assert without["therapy_mode"] is None
    assert without["mask_type"] is None

    # The merge helper exposes exactly the loader's keys, nothing invented.
    assert persist._session_settings_map(_session([snapshot])) == {"therapy_mode": "APAP"}
    assert persist._session_settings_map(_session([])) == {}

    placeholder = SettingsSnapshot(
        effective_at=start,
        settings={"therapy_mode": "Unknown", "mask_type": None},
        source_names={"therapy_mode": "pressure_mode"},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )
    placeholder_row = _row(_session([placeholder]))
    assert placeholder_row["therapy_mode"] is None
    assert persist._session_settings_map(_session([placeholder])) == {}


def test_persist_session_row_labels_parser_leak_as_lps():
    """The cpap-parser bridge must persist leak_unit='L/s', matching the raw values.

    Regression: it previously stored 'L/min' while the values were raw Leak.2s
    (L/s) magnitude, which made every leak figure read ~60x too low. cpap-py leak
    is numerically identical to the legacy 'L/s' channel, so the row must carry the
    truthful 'L/s' unit (same as importer.import_sessions) for display to scale it.
    """
    from importer.loaders import persist
    from importer.loaders.models import Session

    start = datetime(2026, 6, 1, 22, 0)
    session = Session(
        source_session_key="resmed:LEAK:2026-06-01",
        machine_key="LEAK",
        start_time=start,
        end_time=start + timedelta(hours=8),
        machine_local_date="2026-06-01",
        timezone_basis="machine_local",
    )
    row = persist._session_row(
        session=session,
        user_id="u",
        machine_id="m",
        import_run_id="r",
        folder_date=date(2026, 6, 1),
        start_dt=start,
        duration_seconds=28800,
        derived={},
        serial="SER",
        manufacturer="ResMed",
        machine_tz_name="UTC",
        has_detailed=True,
    )
    assert row["leak_unit"] == "L/s"
    assert row["leak_kind"] == "unintentional"


def test_build_session_emits_therapy_aggregate_derived_values():
    """The ``therapy_aggregates`` comparator's source values ARE emitted.

    ``_observable_aggregates`` reads ``computed_usage_hours`` (→ usage_seconds),
    ``recording_span_hours`` (→ wall_clock_seconds), and the block count. This
    documents that a detailed night already carries those derived values, so a
    fixture-backed ``therapy_aggregates`` block is gated only on obtaining a run
    (parser + manifest block), not on a loader change.
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    detailed = [_detailed_session(flow_rate=[1.0, 2.0], set_pressure=[10.0, 11.0])]

    session = ResMedNativeLoader()._build_session(
        _waveform_summary(),  # computed_usage=8.0, recording_span=8.0
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=True,
        run_warnings=[],
    )

    derived = {dv.key: dv.value for dv in session.derived_values}
    assert derived["computed_usage_hours"] == 8.0
    assert derived["recording_span_hours"] == 8.0
    # gap_seconds in the comparator = wall_clock - usage; both are present here.
    assert "summary_reported_usage_hours" in derived


def test_build_session_emits_blocks_and_events_for_detailed_night():
    """A detailed night emits ``Session.blocks`` and ``Session.events``.

    Documents that ``session_blocks`` (count) and ``events`` (count/types) have a
    real loader source — block start/end from each file-session, events from
    ``CPAPSession.events`` — so those blocks are gated on a run + manifest block,
    not a loader change. (The ordered ``intervals``/``events`` lists additionally
    embed timestamps; see the audit for why those are deferred.)
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    event = SimpleNamespace(
        event_type="Obstructive Apnea",
        timestamp_sec=120.0,
        duration_sec=12.0,
    )
    # Built inline (not via ``_detailed_session``, which hardcodes ``events=[]``)
    # so a scored event is present for ``_session_events`` to map.
    detailed = [
        SimpleNamespace(
            start_time=datetime(2026, 6, 1, 22, 0),
            end_time=datetime(2026, 6, 2, 6, 0),
            file_type="BRP",
            events=[event],
            sample_rate=25.0,
            timeseries=None,
        )
    ]

    session = ResMedNativeLoader()._build_session(
        _waveform_summary(),
        detailed=detailed,
        machine_key="SN-TEST",
        include_waveforms=False,  # exclude derived large-leak events for a clean count
        run_warnings=[],
    )

    assert len(session.blocks) == 1
    assert session.blocks[0].start_time == datetime(2026, 6, 1, 22, 0)
    # The scored event survives with its raw cpap-parser type string (not yet
    # mapped to the OSCAR enum — a documented vocabulary gap in the audit).
    assert [e.event_type for e in session.events] == ["Obstructive Apnea"]
    assert session.events[0].start_time == datetime(2026, 6, 1, 22, 2)  # start + 120s


def test_signal_metrics_derives_leak_p95_distinct_from_pressure_p95():
    """The loader emits a leak 95th percentile from the leak channel, not pressure.

    Regression for the nightly leak card displaying ``p95_pressure``. ``_signal_metrics``
    must derive ``p95_leak`` from the concatenated ``timeseries.leak`` samples, tagged with
    the raw Leak.2s unit (``L/s`` — cpap-py's leak is numerically identical to the legacy
    channel), and keep it independent from the set-pressure percentile so the API/UI can
    label the leak stat honestly and scale it correctly.
    """
    from importer.loaders.resmed_native import ResMedNativeLoader

    # Leak 95th percentile (0.04 L/s) differs from both its mean and the pressure p95
    # (10.0); a regression that aliased one onto the other would be caught. 0.04 L/s is
    # the OSCAR-style 2.4 L/min p95 *before* display scaling — stored verbatim, not x60.
    leak = [0.0] * 19 + [0.04]
    timeseries = SimpleNamespace(
        set_pressure=[10.0] * 20,
        leak=leak,
        respiratory_rate=[],
        tidal_volume=[],
        minute_ventilation=[],
        snore=[],
        flow_limitation=[],
    )
    detailed = [SimpleNamespace(timeseries=timeseries)]

    derived = {dv.key: (dv.value, dv.unit) for dv in ResMedNativeLoader._signal_metrics(detailed)}

    # Raw L/s magnitude is stored verbatim (display layer multiplies by 60 -> 2.4 L/min).
    assert derived["p95_leak"] == (0.04, "L/s")
    assert derived["avg_leak"][1] == "L/s"
    assert derived["p95_pressure"] == (10.0, "cmH2O")
    # The leak percentile is not the pressure percentile.
    assert derived["p95_leak"][0] != derived["p95_pressure"][0]


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
