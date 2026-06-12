"""Regression coverage for compressed full-night waveform storage."""

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from importer.loaders.persist import _write_waveform_chunks
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


def test_parser_waveform_persistence_is_idempotent_and_api_reads_windows(
    db, test_user, client, auth_headers
):
    session_id, run_id = _seed_parser_session(db, test_user["id"])
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

    first_count = _write_waveform_chunks(
        raw_conn,
        session_db_id=session_id,
        import_run_id=run_id,
        detailed=detailed,
        machine_tz=UTC,
        parser_version="0.1",
    )
    second_count = _write_waveform_chunks(
        raw_conn,
        session_db_id=session_id,
        import_run_id=run_id,
        detailed=detailed,
        machine_tz=UTC,
        parser_version="0.1",
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
    assert first_count == second_count == 2
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
