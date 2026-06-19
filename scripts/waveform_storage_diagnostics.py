"""Generate a read-only waveform storage decision report for PostgreSQL."""

from __future__ import annotations

import argparse
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

TABLES = ("session_waveform", "session_metrics", "waveform_chunks")

EVENT_WAVEFORM_SQL = """
WITH target AS (
    SELECT folder_date, machine_id FROM sessions
    WHERE id = %(session_id)s AND user_id = %(user_id)s
), numbered AS (
    SELECT sw.ts, sw.flow, sw.pressure, ROW_NUMBER() OVER (ORDER BY sw.ts) AS rn
    FROM session_waveform sw
    JOIN sessions s ON sw.session_id = s.id
    JOIN target t ON t.folder_date = s.folder_date AND t.machine_id IS NOT DISTINCT FROM s.machine_id
    WHERE s.user_id = %(user_id)s AND sw.ts >= %(start_time)s AND sw.ts <= %(end_time)s
)
SELECT ts, flow, pressure FROM numbered WHERE (rn - 1) %% %(downsample)s = 0 ORDER BY ts
"""

METRICS_WINDOW_SQL = """
SELECT ts, mask_pressure, pressure, epr_pressure, leak, resp_rate,
       tidal_vol, min_vent, snore, flow_lim
FROM session_metrics
WHERE session_id = %(session_id)s
  AND ts >= (SELECT MIN(ts) + (%(offset_min)s * INTERVAL '1 minute')
             FROM session_metrics WHERE session_id = %(session_id)s)
  AND ts < (SELECT MIN(ts) + ((%(offset_min)s + %(window_min)s) * INTERVAL '1 minute')
            FROM session_metrics WHERE session_id = %(session_id)s)
ORDER BY ts
"""

METRICS_DOWNSAMPLE_SQL = """
WITH target AS (
    SELECT folder_date, machine_id FROM sessions
    WHERE id = %(session_id)s AND user_id = %(user_id)s
), numbered AS (
    SELECT sm.ts, sm.mask_pressure, sm.pressure, sm.epr_pressure, sm.leak,
           sm.resp_rate, sm.tidal_vol, sm.min_vent, sm.snore, sm.flow_lim,
           ROW_NUMBER() OVER (ORDER BY sm.ts) AS rn
    FROM session_metrics sm
    JOIN sessions s ON sm.session_id = s.id
    JOIN target t ON t.folder_date = s.folder_date AND t.machine_id IS NOT DISTINCT FROM s.machine_id
    WHERE s.user_id = %(user_id)s
)
SELECT ts, mask_pressure, pressure, epr_pressure, leak, resp_rate,
       tidal_vol, min_vent, snore, flow_lim
FROM numbered WHERE (rn - 1) %% %(downsample)s = 0 ORDER BY ts
"""

WAVEFORM_CHUNKS_SQL = """
WITH target AS (
    SELECT folder_date, machine_id FROM sessions
    WHERE id = %(session_id)s AND user_id = %(user_id)s
)
SELECT signal_name, unit, sample_rate_hz, start_time, end_time, sample_count,
       encoding, payload
FROM waveform_chunks wc
JOIN sessions s ON s.id = wc.session_id
JOIN target t ON t.folder_date = s.folder_date AND t.machine_id IS NOT DISTINCT FROM s.machine_id
WHERE s.user_id = %(user_id)s AND wc.signal_name = %(signal_name)s
  AND wc.end_time >= %(start_time)s AND wc.start_time <= %(end_time)s
ORDER BY wc.start_time, wc.chunk_index
"""

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_TIMESTAMP = re.compile(r"'\d{4}-\d\d-\d\d(?:[ T][^']+)?'")


def _size(value: int | None) -> str:
    if value is None:
        return "n/a"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return "n/a"


def sanitize_plan(lines: list[str]) -> str:
    """Remove identifiers and exact therapy timestamps from printable plans."""
    plan = "\n".join(lines)
    return _TIMESTAMP.sub("'<timestamp>'", _UUID.sub("<session-id>", plan))


def recommendation(data: dict[str, Any]) -> tuple[str, list[str]]:
    duplicate_groups = data["duplicates"]["duplicate_groups"]
    waveform_rows = data["tables"]["session_waveform"]["row_count"]
    chunk_rows = data["tables"]["waveform_chunks"]["row_count"]
    coverage = data["coverage"]
    reasons: list[str] = []
    if duplicate_groups:
        reasons.append("Duplicate `(session_id, ts)` groups make a composite-key Phase 1 unsafe without remediation.")
    else:
        reasons.append(
            "No duplicate `(session_id, ts)` groups were found, so Phase 1's uniqueness prerequisite passes."
        )
    if not chunk_rows:
        reasons.append("No waveform chunks are populated; Phase 2 cannot safely become canonical yet.")
        return "Phase 1 shrink-in-place is the safer next investigation.", reasons
    if coverage["waveform_only_sessions"]:
        reasons.append(
            f"{coverage['waveform_only_sessions']:,} session(s) have row waveforms but no chunks, "
            "so Phase 2 needs backfill/cutover work first."
        )
        return "Phase 1 shrink-in-place is the safer near-term option; Phase 2 is not cutover-ready.", reasons
    if duplicate_groups:
        reasons.append("Chunk coverage avoids the duplicate-key blocker affecting Phase 1.")
        return "Phase 2 chunk-canonical migration looks safer, subject to reader/importer validation.", reasons
    if waveform_rows and coverage["both_sessions"]:
        reasons.append("All sessions with row waveforms also have chunks, making staged reader validation feasible.")
        return (
            "Phase 2 chunk-canonical migration looks structurally safer; validate value parity before cutover.",
            reasons,
        )
    return "Evidence is insufficient for either migration; gather a representative populated copy first.", reasons


def render_report(data: dict[str, Any]) -> str:
    verdict, reasons = recommendation(data)
    lines = [
        "# Waveform storage diagnostics",
        "",
        "> Read-only diagnostic output. No patient, device, source-path, session-ID, or exact timestamp data is included.",
        "",
        "## Decision summary",
        "",
        f"**{verdict}**",
        "",
        *[f"- {reason}" for reason in reasons],
        "",
        "This is evidence for a reviewed decision, not authorization to change schema or routing.",
        "",
        "## Duplicate check",
        "",
        f"Duplicate `(session_id, ts)` groups: **{data['duplicates']['duplicate_groups']:,}** "
        f"({data['duplicates']['duplicate_rows']:,} rows in those groups).",
        "",
        "## Table storage",
        "",
        "| Table | Rows | Heap | Indexes | Total | Payload | Compressed metadata |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in TABLES:
        row = data["tables"][name]
        lines.append(
            f"| `{name}` | {row['row_count']:,} | {_size(row['heap_bytes'])} | {_size(row['index_bytes'])} | "
            f"{_size(row['total_bytes'])} | {_size(row.get('payload_bytes'))} | {_size(row.get('compressed_bytes'))} |"
        )
    coverage = data["coverage"]
    lines += [
        "",
        "Chunk coverage (aggregate session counts): "
        f"both stores **{coverage['both_sessions']:,}**, row-only **{coverage['waveform_only_sessions']:,}**, "
        f"chunk-only **{coverage['chunk_only_sessions']:,}**.",
        "",
        "## Index usage",
        "",
        "Counters are cumulative since PostgreSQL statistics were last reset; zero does not prove an index is unnecessary.",
        "",
        "| Table | Index | Size | Scans | Tuples read | Tuples fetched |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in data["indexes"]:
        lines.append(
            f"| `{row['table_name']}` | `{row['index_name']}` | {_size(row['size_bytes'])} | "
            f"{row['idx_scan']:,} | {row['idx_tup_read']:,} | {row['idx_tup_fetch']:,} |"
        )
    lines += ["", "## Query plans", ""]
    for title, plan in data["plans"].items():
        lines += [f"### {title}", "", "```text", plan or "No representative data available.", "```", ""]
    lines += [
        "## Interpretation cautions",
        "",
        "- Plans reflect this database's data distribution, cache state, statistics, and PostgreSQL settings.",
        "- Re-run after `ANALYZE` on a copied realistic database if estimates are stale; this tool never runs it for you.",
        "- Payload bytes use `pg_column_size(payload)`; table total includes TOAST and indexes.",
        "- Validate waveform value/window parity separately before any Phase 2 reader cutover.",
        "",
    ]
    return "\n".join(lines)


def _fetchone(cur, sql: str, params=None) -> dict[str, Any]:
    cur.execute(sql, params)
    return dict(cur.fetchone())


def _explain(cur, sql: str, params: dict[str, Any]) -> str:
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql, params)
    return sanitize_plan([row["QUERY PLAN"] for row in cur.fetchall()])


def collect(conn, statement_timeout_ms: int) -> dict[str, Any]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (statement_timeout_ms,))
        cur.execute("SET LOCAL lock_timeout = '5s'")
        duplicates = _fetchone(
            cur,
            """
            SELECT COUNT(*)::bigint AS duplicate_groups, COALESCE(SUM(n), 0)::bigint AS duplicate_rows
            FROM (SELECT COUNT(*) AS n FROM session_waveform GROUP BY session_id, ts HAVING COUNT(*) > 1) d
        """,
        )
        tables: dict[str, Any] = {}
        for table in TABLES:
            row = _fetchone(
                cur,
                """
                SELECT pg_relation_size(c.oid)::bigint AS heap_bytes,
                       pg_indexes_size(c.oid)::bigint AS index_bytes,
                       pg_total_relation_size(c.oid)::bigint AS total_bytes
                FROM pg_class c WHERE c.oid = %s::regclass
            """,
                (table,),
            )
            cur.execute(f"SELECT COUNT(*)::bigint AS row_count FROM {table}")  # fixed constants only
            row.update(cur.fetchone())
            row.update({"payload_bytes": None, "compressed_bytes": None})
            if table == "waveform_chunks":
                row.update(
                    _fetchone(
                        cur,
                        """
                    SELECT COALESCE(SUM(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                           COALESCE(SUM(compressed_bytes), 0)::bigint AS compressed_bytes FROM waveform_chunks
                """,
                    )
                )
            tables[table] = row
        cur.execute(
            """
            SELECT relname AS table_name, indexrelname AS index_name,
                   pg_relation_size(indexrelid)::bigint AS size_bytes, idx_scan, idx_tup_read, idx_tup_fetch
            FROM pg_stat_user_indexes WHERE relname = ANY(%s) ORDER BY relname, indexrelname
        """,
            (list(TABLES),),
        )
        indexes = [dict(row) for row in cur.fetchall()]
        coverage = _fetchone(
            cur,
            """
            WITH sw AS (SELECT DISTINCT session_id FROM session_waveform),
                 wc AS (SELECT DISTINCT session_id FROM waveform_chunks)
            SELECT COUNT(*) FILTER (WHERE sw.session_id IS NOT NULL AND wc.session_id IS NOT NULL)::bigint AS both_sessions,
                   COUNT(*) FILTER (WHERE sw.session_id IS NOT NULL AND wc.session_id IS NULL)::bigint AS waveform_only_sessions,
                   COUNT(*) FILTER (WHERE sw.session_id IS NULL AND wc.session_id IS NOT NULL)::bigint AS chunk_only_sessions
            FROM sw FULL JOIN wc USING (session_id)
        """,
        )
        plans: dict[str, str] = {}
        cur.execute("""
            SELECT sw.session_id, s.user_id, MIN(sw.ts) start_time, MAX(sw.ts) end_time
            FROM session_waveform sw JOIN sessions s ON s.id = sw.session_id
            GROUP BY sw.session_id, s.user_id ORDER BY COUNT(*) DESC LIMIT 1
        """)
        sample = cur.fetchone()
        plans["Event Inspector waveform window"] = ""
        if sample:
            start = sample["start_time"]
            plans["Event Inspector waveform window"] = _explain(
                cur,
                EVENT_WAVEFORM_SQL,
                {
                    "session_id": sample["session_id"],
                    "user_id": sample["user_id"],
                    "start_time": start,
                    "end_time": min(sample["end_time"], start + timedelta(minutes=5)),
                    "downsample": 5,
                },
            )
        cur.execute("""
            SELECT sm.session_id, s.user_id, MIN(sm.ts) start_time, MAX(sm.ts) end_time
            FROM session_metrics sm JOIN sessions s ON s.id = sm.session_id
            GROUP BY sm.session_id, s.user_id ORDER BY COUNT(*) DESC LIMIT 1
        """)
        sample = cur.fetchone()
        plans["Session metrics downsampled night"] = ""
        plans["Session breath metrics window"] = ""
        if sample:
            plans["Session metrics downsampled night"] = _explain(
                cur,
                METRICS_DOWNSAMPLE_SQL,
                {
                    "session_id": sample["session_id"],
                    "user_id": sample["user_id"],
                    "downsample": 15,
                },
            )
            plans["Session breath metrics window"] = _explain(
                cur,
                METRICS_WINDOW_SQL,
                {
                    "session_id": sample["session_id"],
                    "offset_min": 0,
                    "window_min": 10,
                },
            )
        cur.execute("""
            SELECT wc.session_id, s.user_id, wc.signal_name,
                   MIN(wc.start_time) start_time, MAX(wc.end_time) end_time
            FROM waveform_chunks wc JOIN sessions s ON s.id = wc.session_id
            GROUP BY wc.session_id, s.user_id, wc.signal_name ORDER BY SUM(wc.sample_count) DESC LIMIT 1
        """)
        sample = cur.fetchone()
        plans["Waveform chunk overlap window"] = ""
        if sample:
            start = sample["start_time"]
            plans["Waveform chunk overlap window"] = _explain(
                cur,
                WAVEFORM_CHUNKS_SQL,
                {
                    "session_id": sample["session_id"],
                    "user_id": sample["user_id"],
                    "signal_name": sample["signal_name"],
                    "start_time": start,
                    "end_time": min(sample["end_time"], start + timedelta(minutes=10)),
                },
            )
    conn.rollback()
    return {"duplicates": duplicates, "tables": tables, "indexes": indexes, "coverage": coverage, "plans": plans}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--statement-timeout-ms", type=int, default=120_000)
    args = parser.parse_args()
    if args.statement_timeout_ms < 1:
        parser.error("--statement-timeout-ms must be positive")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set (credentials are intentionally not accepted as CLI arguments)")
    database_url = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    with psycopg2.connect(database_url) as conn:
        data = collect(conn, args.statement_timeout_ms)
    args.output.write_text(render_report(data), encoding="utf-8")
    print(f"Wrote sanitized read-only report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
