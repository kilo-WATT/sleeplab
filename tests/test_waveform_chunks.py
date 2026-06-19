"""Regression coverage for compressed full-night waveform storage."""

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from importer.loaders import persist as waveform_persist
from importer.loaders.persist import _replace_high_rate_waveforms, _write_waveform_chunks
from importer.waveform_chunks import (
    ENCODING,
    build_chunks,
    decode_samples,
    decode_window,
    downsample_extrema,
    encode_samples,
)


def test_chunk_encode_decode_and_compression_round_trip():
    samples = [0.0] * 500 + [None, -1.25, 2.5]

    payload, raw_size = encode_samples(samples)
    decoded = decode_samples(payload, len(samples))

    assert raw_size == len(samples) * 4
    assert len(payload) < raw_size
    assert decoded[-3:] == [None, -1.25, 2.5]
    assert decoded[:500] == [0.0] * 500


def test_decode_rejects_corrupt_or_mismatched_payloads():
    payload, _ = encode_samples([1.0, 2.0])

    with pytest.raises(ValueError, match="size mismatch"):
        decode_samples(payload, 3)
    with pytest.raises(ValueError, match="invalid compressed"):
        decode_samples(b"not-zlib", 2)


def test_time_window_read_decodes_only_requested_samples():
    start = datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
    chunks = build_chunks(
        signal_name="flow_rate",
        unit="L/s",
        sample_rate_hz=2,
        start_time=start,
        samples=list(range(20)),
        chunk_seconds=4,
    )
    rows = [
        {
            "sample_rate_hz": chunk.sample_rate_hz,
            "start_time": chunk.start_time,
            "sample_count": chunk.sample_count,
            "payload": chunk.payload,
        }
        for chunk in chunks
        if chunk.end_time >= start + timedelta(seconds=3)
        and chunk.start_time <= start + timedelta(seconds=5)
    ]

    points = decode_window(
        rows,
        start_time=start + timedelta(seconds=3),
        end_time=start + timedelta(seconds=5),
    )

    assert [point.value for point in points] == [6.0, 7.0, 8.0, 9.0, 10.0]
    assert points[0].timestamp == start + timedelta(seconds=3)
    assert points[-1].timestamp == start + timedelta(seconds=5)


def test_decode_window_marks_recording_gaps_instead_of_connecting_sessions():
    start = datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
    first = build_chunks(
        signal_name="flow_rate",
        unit="L/s",
        sample_rate_hz=1,
        start_time=start,
        samples=[1.0, 2.0],
    )[0]
    second = build_chunks(
        signal_name="flow_rate",
        unit="L/s",
        sample_rate_hz=1,
        start_time=start + timedelta(minutes=10),
        samples=[3.0, 4.0],
    )[0]
    rows = [
        {
            "sample_rate_hz": chunk.sample_rate_hz,
            "start_time": chunk.start_time,
            "sample_count": chunk.sample_count,
            "payload": chunk.payload,
        }
        for chunk in (first, second)
    ]

    points = decode_window(rows)

    assert [point.value for point in points] == [1.0, 2.0, None, 3.0, 4.0]


def test_downsample_extrema_respects_limit_and_preserves_local_spikes():
    start = datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
    samples = [0.0] * 50 + [-9.0] + [0.0] * 48 + [12.0] + [0.0] * 100
    payload, _ = encode_samples(samples)
    points = decode_window(
        [{
            "sample_rate_hz": 1,
            "start_time": start,
            "sample_count": len(samples),
            "payload": payload,
        }]
    )

    reduced = downsample_extrema(points, 20)

    assert len(reduced) <= 20
    assert min(point.value for point in reduced if point.value is not None) == -9.0
    assert max(point.value for point in reduced if point.value is not None) == 12.0


def test_waveform_migration_is_repeat_safe_and_constrained():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "028_add_waveform_chunks.sql"
    ).read_text()

    assert "CREATE TABLE IF NOT EXISTS waveform_chunks" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_waveform_chunks_session_signal_time" in sql
    assert "UNIQUE (session_id, signal_name, chunk_index)" in sql
    assert "CHECK (sample_rate_hz > 0)" in sql
    assert ENCODING in sql


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        connection = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, params):
                connection.statements.append((statement, params))

        return _Cursor()


def test_parser_persistence_clears_legacy_rows_only_after_chunks_exist(monkeypatch):
    connection = _RecordingConnection()
    monkeypatch.setattr(waveform_persist, "_write_waveform_chunks", lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        waveform_persist,
        "_write_session_waveform",
        lambda *args, **kwargs: pytest.fail("legacy rows must not be written"),
    )

    counts = _replace_high_rate_waveforms(
        connection,
        session_db_id="session-1",
        import_run_id="run-1",
        detailed=[],
        machine_tz=UTC,
        parser_version="test",
        night_events=[],
    )

    assert counts == (0, 2)
    assert connection.statements == [
        ("DELETE FROM session_waveform WHERE session_id = %s", ("session-1",))
    ]


def test_parser_persistence_keeps_row_fallback_when_chunks_are_unavailable(monkeypatch):
    connection = _RecordingConnection()
    monkeypatch.setattr(waveform_persist, "_write_waveform_chunks", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        waveform_persist, "_write_session_waveform", lambda *args, **kwargs: 37
    )

    counts = _replace_high_rate_waveforms(
        connection,
        session_db_id="session-1",
        import_run_id="run-1",
        detailed=[],
        machine_tz=UTC,
        parser_version="test",
        night_events=[],
    )

    assert counts == (37, 0)
    assert connection.statements == []


def _seed_parser_session(db, user_id: str) -> tuple[str, str]:
    machine_id = db.execute(
        text("""
            INSERT INTO cpap_machines (
                user_id, manufacturer, adapter_id, identity_key,
                identity_confidence, support_status, validation_status
            ) VALUES (
                CAST(:uid AS uuid), 'ResMed', 'resmed-cpap-parser-v1', :identity_key,
                'strong', 'experimental', 'partial'
            )
            RETURNING id::text
        """),
        {"uid": user_id, "identity_key": f"waveform-test-{uuid.uuid4()}"},
    ).scalar_one()
    run_id = db.execute(
        text("""
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status
            ) VALUES (
                CAST(:uid AS uuid), CAST(:mid AS uuid), 'resmed-cpap-parser-v1',
                'directory', :fingerprint, 'running', 'partial'
            )
            RETURNING id::text
        """),
        {
            "uid": user_id,
            "mid": machine_id,
            "fingerprint": f"waveform-{uuid.uuid4()}",
        },
    ).scalar_one()
    session_id = str(uuid.uuid4())
    start = datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
    db.execute(
        text("""
            INSERT INTO sessions (
                id, session_id, folder_date, start_datetime, pld_start_datetime,
                duration_seconds, has_spo2, user_id, machine_id, import_run_id,
                source_session_key, provenance_status, machine_tz
            ) VALUES (
                CAST(:sid AS uuid), :sid, :folder_date, :start, :start,
                8 * 3600, FALSE, CAST(:uid AS uuid), CAST(:mid AS uuid),
                CAST(:run_id AS uuid), :source_key, 'native_resmed_cpap_parser', 'UTC'
            )
        """),
        {
            "sid": session_id,
            "folder_date": date(2026, 6, 11),
            "start": start,
            "uid": user_id,
            "mid": machine_id,
            "run_id": run_id,
            "source_key": f"waveform-session-{uuid.uuid4()}",
        },
    )
    db.flush()
    return session_id, run_id


def _seed_event(db, session_id: str, *, offset_seconds: int = 4) -> int:
    return db.execute(
        text("""
            INSERT INTO session_events (
                session_id, event_type, onset_seconds, duration_seconds,
                event_datetime, source_event_key, source_event_type
            ) VALUES (
                CAST(:sid AS uuid), 'Obstructive Apnea', :offset, 10,
                :event_time, :source_key, 'Obstructive Apnea'
            )
            RETURNING id
        """),
        {
            "sid": session_id,
            "offset": offset_seconds,
            "event_time": datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
            + timedelta(seconds=offset_seconds),
            "source_key": f"event-{uuid.uuid4()}",
        },
    ).scalar_one()


def _write_test_chunks(db, session_id: str, run_id: str) -> None:
    detailed = [
        SimpleNamespace(
            start_time=datetime(2026, 6, 11, 22, 0),
            file_type="BRP+PLD",
            sample_rate=2,
            timeseries=SimpleNamespace(
                flow_rate=[index / 2 for index in range(17)],
                pressure=[8.0 + index / 2 for index in range(17)],
            ),
        )
    ]
    _write_waveform_chunks(
        db.connection().connection.driver_connection,
        session_db_id=session_id,
        import_run_id=run_id,
        detailed=detailed,
        machine_tz=UTC,
        parser_version="test",
    )
    db.flush()


def _event_window(client, auth_headers, session_id: str, event_id: int):
    return client.get(
        f"/sessions/{session_id}/events/{event_id}/window",
        params={"before_seconds": 10, "after_seconds": 10},
        headers=auth_headers,
    )


def test_event_inspector_prefers_chunk_backed_waveform_window(
    db, test_user, client, auth_headers
):
    session_id, run_id = _seed_parser_session(db, test_user["id"])
    event_id = _seed_event(db, session_id)
    _write_test_chunks(db, session_id, run_id)
    db.execute(
        text("""
            INSERT INTO session_waveform (session_id, ts, flow, pressure)
            VALUES (CAST(:sid AS uuid), :ts, 999, 999)
        """),
        {"sid": session_id, "ts": datetime(2026, 6, 11, 22, 0, 4, tzinfo=UTC)},
    )
    db.commit()

    response = _event_window(client, auth_headers, session_id, event_id)

    assert response.status_code == 200
    waveform = response.json()["waveform"]
    assert set(waveform) == {"timestamps", "flow", "pressure"}
    assert len(waveform["timestamps"]) == len(waveform["flow"]) == len(waveform["pressure"]) == 17
    assert waveform["flow"][8] == 4.0
    assert waveform["pressure"][8] == 12.0
    assert 999 not in waveform["flow"]


def test_event_inspector_falls_back_to_session_waveform_when_chunks_are_missing(
    db, test_user, client, auth_headers
):
    session_id, _ = _seed_parser_session(db, test_user["id"])
    event_id = _seed_event(db, session_id)
    db.execute(
        text("""
            INSERT INTO session_waveform (session_id, ts, flow, pressure)
            VALUES
                (CAST(:sid AS uuid), :first_ts, 1.25, 9.5),
                (CAST(:sid AS uuid), :second_ts, NULL, 10.0)
        """),
        {
            "sid": session_id,
            "first_ts": datetime(2026, 6, 11, 22, 0, 3, tzinfo=UTC),
            "second_ts": datetime(2026, 6, 11, 22, 0, 4, tzinfo=UTC),
        },
    )
    db.commit()

    response = _event_window(client, auth_headers, session_id, event_id)

    assert response.status_code == 200
    assert response.json()["waveform"] == {
        "timestamps": ["2026-06-11T22:00:03+00:00", "2026-06-11T22:00:04+00:00"],
        "flow": [1.25, None],
        "pressure": [9.5, 10.0],
    }


def test_event_inspector_row_and_chunk_waveform_shapes_are_compatible(
    db, test_user, client, auth_headers
):
    chunk_session_id, run_id = _seed_parser_session(db, test_user["id"])
    chunk_event_id = _seed_event(db, chunk_session_id)
    _write_test_chunks(db, chunk_session_id, run_id)

    row_session_id, _ = _seed_parser_session(db, test_user["id"])
    row_event_id = _seed_event(db, row_session_id)
    for index in range(17):
        db.execute(
            text("""
                INSERT INTO session_waveform (session_id, ts, flow, pressure)
                VALUES (CAST(:sid AS uuid), :ts, :flow, :pressure)
            """),
            {
                "sid": row_session_id,
                "ts": datetime(2026, 6, 11, 22, 0, tzinfo=UTC)
                + timedelta(seconds=index / 2),
                "flow": index / 2,
                "pressure": 8.0 + index / 2,
            },
        )
    db.commit()

    chunk_waveform = _event_window(
        client, auth_headers, chunk_session_id, chunk_event_id
    ).json()["waveform"]
    row_waveform = _event_window(
        client, auth_headers, row_session_id, row_event_id
    ).json()["waveform"]

    assert chunk_waveform == row_waveform


def test_event_inspector_missing_waveform_data_remains_an_empty_response(
    db, test_user, client, auth_headers
):
    session_id, _ = _seed_parser_session(db, test_user["id"])
    event_id = _seed_event(db, session_id)
    db.commit()

    response = _event_window(client, auth_headers, session_id, event_id)

    assert response.status_code == 200
    assert response.json()["waveform"] == {
        "timestamps": [],
        "flow": [],
        "pressure": [],
    }


def test_parser_import_persistence_prefers_chunks_and_api_reads_windows(
    db, test_user, client, auth_headers
):
    session_id, run_id = _seed_parser_session(db, test_user["id"])
    event_id = _seed_event(db, session_id)
    raw_conn = db.connection().connection.driver_connection
    start = datetime(2026, 6, 11, 22, 0)
    detailed = [
        SimpleNamespace(
            start_time=start,
            file_type="BRP+PLD",
            sample_rate=25,
            timeseries=SimpleNamespace(
                flow_rate=[float(index % 20) / 10 for index in range(25 * 12)],
                pressure=[8.0] * (25 * 12),
            ),
        )
    ]

    db.execute(
        text("""
            INSERT INTO session_waveform (session_id, ts, flow, pressure)
            VALUES (CAST(:sid AS uuid), :ts, 999, 999)
        """),
        {"sid": session_id, "ts": datetime(2026, 6, 11, 22, 0, tzinfo=UTC)},
    )
    first_rows, first_count = _replace_high_rate_waveforms(
        raw_conn,
        session_db_id=session_id,
        import_run_id=run_id,
        detailed=detailed,
        machine_tz=UTC,
        parser_version="0.1",
        night_events=[(start + timedelta(seconds=4), 10.0)],
    )
    second_rows, second_count = _replace_high_rate_waveforms(
        raw_conn,
        session_db_id=session_id,
        import_run_id=run_id,
        detailed=detailed,
        machine_tz=UTC,
        parser_version="0.1",
        night_events=[(start + timedelta(seconds=4), 10.0)],
    )
    db.flush()

    stored = db.execute(
        text("""
            SELECT signal_name, COUNT(*)::int, SUM(sample_count)::int,
                   MIN(unit), MIN(encoding)
            FROM waveform_chunks
            WHERE session_id = CAST(:sid AS uuid)
            GROUP BY signal_name
            ORDER BY signal_name
        """),
        {"sid": session_id},
    ).all()
    legacy_row_count = db.execute(
        text("SELECT COUNT(*) FROM session_waveform WHERE session_id = CAST(:sid AS uuid)"),
        {"sid": session_id},
    ).scalar_one()
    assert first_rows == second_rows == 0
    assert first_count == second_count == 2
    assert legacy_row_count == 0

    event_response = _event_window(client, auth_headers, session_id, event_id)
    assert event_response.status_code == 200
    assert event_response.json()["waveform"]["flow"][:3] == pytest.approx([0.0, 0.1, 0.2])
    assert stored == [
        ("flow_rate", 1, 300, "L/s", ENCODING),
        ("pressure", 1, 300, "cmH2O", ENCODING),
    ]

    detail = client.get(f"/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["data_availability"]["full_night_flow_available"] is True

    response = client.get(
        f"/sessions/{session_id}/waveforms/flow_rate",
        params={
            "start_time": "2026-06-11T22:00:02Z",
            "end_time": "2026-06-11T22:00:04Z",
            "max_points": 100,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["signal_name"] == "flow_rate"
    assert body["unit"] == "L/s"
    assert body["sample_rate_hz"] == 25
    assert body["sample_count"] == 51
    assert body["returned_sample_count"] == 51
    assert body["timestamps"][0].startswith("2026-06-11T22:00:02")
    assert body["timestamps"][-1].startswith("2026-06-11T22:00:04")

    downsampled = client.get(
        f"/sessions/{session_id}/waveforms/flow_rate",
        params={"max_points": 100},
        headers=auth_headers,
    )
    assert downsampled.status_code == 200
    downsampled_body = downsampled.json()
    assert downsampled_body["sample_count"] == 300
    assert downsampled_body["returned_sample_count"] <= 100

    invalid_window = client.get(
        f"/sessions/{session_id}/waveforms/flow_rate",
        params={
            "start_time": "2026-06-11T22:00:04Z",
            "end_time": "2026-06-11T22:00:02Z",
        },
        headers=auth_headers,
    )
    assert invalid_window.status_code == 400

    unavailable = client.get(
        f"/sessions/{session_id}/waveforms/spo2",
        headers=auth_headers,
    )
    assert unavailable.status_code == 404
    assert "not available" in unavailable.json()["detail"]
