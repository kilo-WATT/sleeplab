"""Compare row-backed and chunk-backed waveform windows without changing data."""

from __future__ import annotations

import argparse
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from importer.waveform_chunks import WaveformPoint, decode_window

SIGNALS = {
    "flow_rate": {"row_column": "flow", "value_tolerance": 0.0001},
    "pressure": {"row_column": "pressure", "value_tolerance": 0.0051},
}


@dataclass(frozen=True)
class Comparison:
    row_count: int
    chunk_count: int
    aligned_count: int
    timestamp_mismatches: int
    null_mismatches: int
    value_mismatches: int
    max_timestamp_delta_ms: float | None
    max_value_difference: float | None
    mean_value_difference: float | None
    min_difference: float | None
    max_difference: float | None
    mean_difference: float | None

    @property
    def passed(self) -> bool:
        return (
            self.row_count == self.chunk_count
            and self.timestamp_mismatches == 0
            and self.null_mismatches == 0
            and self.value_mismatches == 0
        )


def _stats(values: Sequence[float | None]) -> tuple[float, float, float] | None:
    present = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return (min(present), max(present), fmean(present)) if present else None


def continuous_prefix(points: Sequence[WaveformPoint]) -> list[WaveformPoint]:
    """Keep the first row-backed run so full-night chunks do not fill event gaps."""
    if len(points) < 3:
        return list(points)
    steps = [
        (current.timestamp - previous.timestamp).total_seconds()
        for previous, current in zip(points, points[1:], strict=False)
        if current.timestamp > previous.timestamp
    ]
    if not steps:
        return list(points)
    expected_step = min(steps)
    for index, (previous, current) in enumerate(zip(points, points[1:], strict=False), start=1):
        if (current.timestamp - previous.timestamp).total_seconds() > expected_step * 1.5:
            return list(points[:index])
    return list(points)


def compare_points(
    row_points: Sequence[WaveformPoint],
    chunk_points: Sequence[WaveformPoint],
    *,
    timestamp_tolerance_ms: float,
    value_tolerance: float,
) -> Comparison:
    """Compare ordered samples and summary statistics within explicit tolerances."""
    aligned_count = min(len(row_points), len(chunk_points))
    timestamp_deltas: list[float] = []
    value_differences: list[float] = []
    timestamp_mismatches = null_mismatches = value_mismatches = 0
    for row, chunk in zip(row_points, chunk_points, strict=False):
        timestamp_delta = abs((row.timestamp - chunk.timestamp).total_seconds() * 1000)
        timestamp_deltas.append(timestamp_delta)
        timestamp_mismatches += timestamp_delta > timestamp_tolerance_ms
        if (row.value is None) != (chunk.value is None):
            null_mismatches += 1
        elif row.value is not None and chunk.value is not None:
            difference = abs(float(row.value) - float(chunk.value))
            value_differences.append(difference)
            value_mismatches += difference > value_tolerance

    row_stats = _stats([point.value for point in row_points])
    chunk_stats = _stats([point.value for point in chunk_points])
    stat_differences = (
        tuple(abs(row - chunk) for row, chunk in zip(row_stats, chunk_stats, strict=True))
        if row_stats is not None and chunk_stats is not None
        else (None, None, None)
    )
    return Comparison(
        row_count=len(row_points),
        chunk_count=len(chunk_points),
        aligned_count=aligned_count,
        timestamp_mismatches=timestamp_mismatches,
        null_mismatches=null_mismatches,
        value_mismatches=value_mismatches,
        max_timestamp_delta_ms=max(timestamp_deltas, default=None),
        max_value_difference=max(value_differences, default=None),
        mean_value_difference=fmean(value_differences) if value_differences else None,
        min_difference=stat_differences[0],
        max_difference=stat_differences[1],
        mean_difference=stat_differences[2],
    )


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


def render_report(results: Sequence[dict[str, Any]], *, window_seconds: int) -> str:
    """Render only anonymous counts and differences; never emit source waveform data."""
    passed = sum(result["comparison"].passed for result in results)
    lines = [
        "# Waveform parity validation",
        "",
        "> Read-only, sanitized output. Session identifiers, timestamps, waveform values, device details, and source metadata are omitted.",
        "",
        "## Summary",
        "",
        f"Compared **{len(results):,}** anonymous signal window(s) of up to **{window_seconds:,} seconds**: "
        f"**{passed:,} passed**, **{len(results) - passed:,} failed**.",
        "",
        "| Window | Signal | Result | Rows | Chunks | Aligned | Time mismatches | Value mismatches | Null mismatches | Max time delta (ms) | Max value diff | Mean value diff | Min diff | Max diff | Mean diff |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, result in enumerate(results, start=1):
        comparison: Comparison = result["comparison"]
        lines.append(
            f"| {index} | `{result['signal_name']}` | {'PASS' if comparison.passed else 'FAIL'} | "
            f"{comparison.row_count:,} | {comparison.chunk_count:,} | {comparison.aligned_count:,} | "
            f"{comparison.timestamp_mismatches:,} | {comparison.value_mismatches:,} | "
            f"{comparison.null_mismatches:,} | {_number(comparison.max_timestamp_delta_ms)} | "
            f"{_number(comparison.max_value_difference)} | {_number(comparison.mean_value_difference)} | "
            f"{_number(comparison.min_difference)} | {_number(comparison.max_difference)} | "
            f"{_number(comparison.mean_difference)} |"
        )
    lines += [
        "",
        "Min/max/mean columns are absolute differences between store-level statistics, not waveform values.",
        "A window passes only when sample counts match and every aligned timestamp, null state, and value is within tolerance.",
        "This report is diagnostic evidence only and does not authorize schema, importer-routing, or Event Inspector changes.",
        "",
    ]
    return "\n".join(lines)


def collect(
    conn,
    *,
    session_limit: int,
    window_seconds: int,
    timestamp_tolerance_ms: float,
    value_tolerance: float | None,
    statement_timeout_ms: int,
) -> list[dict[str, Any]]:
    """Read representative matching windows from both stores in a read-only transaction."""
    results: list[dict[str, Any]] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (statement_timeout_ms,))
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(
            """
            SELECT sw.session_id, MIN(sw.ts) AS start_time
            FROM session_waveform sw
            WHERE EXISTS (SELECT 1 FROM waveform_chunks wc WHERE wc.session_id = sw.session_id)
            GROUP BY sw.session_id
            ORDER BY COUNT(*) DESC, sw.session_id
            LIMIT %s
            """,
            (session_limit,),
        )
        sessions = list(cur.fetchall())
        for session in sessions:
            start_time: datetime = session["start_time"]
            end_time = start_time + timedelta(seconds=window_seconds)
            for signal_name, settings in SIGNALS.items():
                row_column = settings["row_column"]
                cur.execute(
                    f"""
                    SELECT ts, {row_column} AS value
                    FROM session_waveform
                    WHERE session_id = %s AND ts >= %s AND ts <= %s
                    ORDER BY ts
                    """,  # column is selected only from the fixed SIGNALS mapping
                    (session["session_id"], start_time, end_time),
                )
                row_points = continuous_prefix(
                    [
                        WaveformPoint(row["ts"], None if row["value"] is None else float(row["value"]))
                        for row in cur.fetchall()
                    ]
                )
                if not row_points:
                    continue
                comparison_end = row_points[-1].timestamp
                cur.execute(
                    """
                    SELECT sample_rate_hz, start_time, sample_count, payload
                    FROM waveform_chunks
                    WHERE session_id = %s AND signal_name = %s
                      AND end_time >= %s AND start_time <= %s
                    ORDER BY start_time, chunk_index
                    """,
                    (session["session_id"], signal_name, start_time, comparison_end),
                )
                chunk_rows = list(cur.fetchall())
                chunk_points = decode_window(chunk_rows, start_time=start_time, end_time=comparison_end)
                results.append(
                    {
                        "signal_name": signal_name,
                        "comparison": compare_points(
                            row_points,
                            chunk_points,
                            timestamp_tolerance_ms=timestamp_tolerance_ms,
                            value_tolerance=(
                                value_tolerance if value_tolerance is not None else settings["value_tolerance"]
                            ),
                        ),
                    }
                )
    conn.rollback()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--window-seconds", type=int, default=300)
    parser.add_argument("--timestamp-tolerance-ms", type=float, default=0.5)
    parser.add_argument("--value-tolerance", type=float)
    parser.add_argument("--statement-timeout-ms", type=int, default=120_000)
    args = parser.parse_args()
    for name in ("sessions", "window_seconds", "statement_timeout_ms"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.timestamp_tolerance_ms < 0 or (args.value_tolerance is not None and args.value_tolerance < 0):
        parser.error("tolerances must be non-negative")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set (credentials are intentionally not accepted as CLI arguments)")
    database_url = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    with psycopg2.connect(database_url) as conn:
        results = collect(
            conn,
            session_limit=args.sessions,
            window_seconds=args.window_seconds,
            timestamp_tolerance_ms=args.timestamp_tolerance_ms,
            value_tolerance=args.value_tolerance,
            statement_timeout_ms=args.statement_timeout_ms,
        )
    args.output.write_text(render_report(results, window_seconds=args.window_seconds), encoding="utf-8")
    print(f"Wrote sanitized read-only report to {args.output}")
    return 0 if results and all(result["comparison"].passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
