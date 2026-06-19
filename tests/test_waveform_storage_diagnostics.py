from scripts.waveform_storage_diagnostics import (
    EVENT_WAVEFORM_SQL,
    METRICS_DOWNSAMPLE_SQL,
    METRICS_WINDOW_SQL,
    WAVEFORM_CHUNKS_SQL,
    _explain,
    recommendation,
    render_report,
    sanitize_plan,
)


def _data(*, duplicates=0, waveform_rows=100, chunk_rows=10, row_only=0):
    table = {"row_count": 0, "heap_bytes": 0, "index_bytes": 0, "total_bytes": 0}
    return {
        "duplicates": {"duplicate_groups": duplicates, "duplicate_rows": duplicates * 2},
        "tables": {
            "session_waveform": {**table, "row_count": waveform_rows},
            "session_metrics": {**table, "row_count": 50},
            "waveform_chunks": {**table, "row_count": chunk_rows, "payload_bytes": 12, "compressed_bytes": 10},
        },
        "indexes": [
            {
                "table_name": "session_waveform",
                "index_name": "safe_index",
                "size_bytes": 1,
                "idx_scan": 2,
                "idx_tup_read": 3,
                "idx_tup_fetch": 4,
            }
        ],
        "coverage": {"both_sessions": 1, "waveform_only_sessions": row_only, "chunk_only_sessions": 0},
        "plans": {"Example": "Index Scan using safe_index"},
    }


def test_sanitize_plan_removes_session_id_and_timestamp():
    plan = sanitize_plan(
        [
            "Filter: session_id = '123e4567-e89b-12d3-a456-426614174000'::uuid",
            "Index Cond: ts >= '2026-01-02 03:04:05+00'::timestamp with time zone",
        ]
    )
    assert "123e4567" not in plan
    assert "2026-01-02" not in plan
    assert "<session-id>" in plan
    assert "<timestamp>" in plan


def test_explain_extracts_real_dict_cursor_rows():
    class Cursor:
        def execute(self, sql, params):
            assert sql.startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)")
            assert params == {"value": 1}

        def fetchall(self):
            return [{"QUERY PLAN": "Index Scan using safe_index"}]

    assert _explain(Cursor(), " SELECT 1", {"value": 1}) == "Index Scan using safe_index"


def test_plan_sql_tracks_api_query_shapes():
    assert "ROW_NUMBER() OVER" in EVENT_WAVEFORM_SQL
    assert "machine_id IS NOT DISTINCT FROM" in EVENT_WAVEFORM_SQL
    assert "ROW_NUMBER() OVER" in METRICS_DOWNSAMPLE_SQL
    assert "MIN(ts)" in METRICS_WINDOW_SQL
    assert "wc.end_time >=" in WAVEFORM_CHUNKS_SQL
    assert "wc.start_time <=" in WAVEFORM_CHUNKS_SQL


def test_recommendation_blocks_phase_one_on_duplicates():
    verdict, reasons = recommendation(_data(duplicates=1))
    assert verdict.startswith("Phase 2")
    assert "Duplicate" in reasons[0]


def test_recommendation_blocks_phase_two_without_chunks():
    verdict, _ = recommendation(_data(chunk_rows=0))
    assert verdict.startswith("Phase 1")


def test_report_is_aggregate_and_contains_required_sections():
    report = render_report(_data(row_only=2))
    assert "## Duplicate check" in report
    assert "## Table storage" in report
    assert "## Index usage" in report
    assert "## Query plans" in report
    assert "Phase 1" in report
    assert "safe_index" in report
