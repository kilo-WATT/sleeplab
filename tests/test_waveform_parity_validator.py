from datetime import UTC, datetime, timedelta

from importer.waveform_chunks import WaveformPoint
from scripts.waveform_parity_validator import compare_points, continuous_prefix, render_report

START = datetime(2026, 1, 2, tzinfo=UTC)


def _points(values, *, offset_ms=0):
    return [
        WaveformPoint(START + timedelta(milliseconds=offset_ms + index * 40), value)
        for index, value in enumerate(values)
    ]


def test_compare_points_accepts_rounding_and_timestamp_tolerance():
    comparison = compare_points(
        _points([1.2345, None, 2.0]),
        _points([1.23449, None, 2.00001], offset_ms=0.1),
        timestamp_tolerance_ms=0.5,
        value_tolerance=0.0001,
    )
    assert comparison.passed
    assert comparison.row_count == comparison.chunk_count == 3
    assert comparison.max_value_difference < 0.0001
    assert comparison.mean_difference < 0.0001


def test_compare_points_reports_count_time_null_and_value_mismatches():
    comparison = compare_points(
        _points([1.0, None, 3.0]),
        _points([1.2, 2.0], offset_ms=2),
        timestamp_tolerance_ms=0.5,
        value_tolerance=0.01,
    )
    assert not comparison.passed
    assert comparison.row_count == 3
    assert comparison.chunk_count == 2
    assert comparison.timestamp_mismatches == 2
    assert comparison.null_mismatches == 1
    assert comparison.value_mismatches == 1


def test_continuous_prefix_stops_before_event_window_gap():
    points = _points([1.0, 2.0, 3.0])
    points.append(WaveformPoint(START + timedelta(seconds=10), 4.0))
    assert continuous_prefix(points) == points[:3]


def test_render_report_is_sanitized_and_contains_stat_differences():
    comparison = compare_points(
        _points([1.0, 2.0]),
        _points([1.0, 2.0]),
        timestamp_tolerance_ms=0,
        value_tolerance=0,
    )
    report = render_report([{"signal_name": "flow_rate", "comparison": comparison}], window_seconds=300)
    assert "PASS" in report
    assert "Min diff" in report
    assert "2026-01-02" not in report
    assert "1.0" not in report
    assert "session_id" not in report
