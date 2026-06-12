from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import DailyStat, OverviewDailyStat, OverviewStats, SummaryStats

router = APIRouter()


def _float_or_none(value):
    return float(value) if value is not None else None


@router.get("/summary", response_model=SummaryStats)
def get_summary(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Global stats for the dashboard header and charts.

    For multi-block nights, picks the longest block as the primary AHI for that night.
    """
    # Date range and compliance
    range_row = (
        db.execute(
            text("""
        SELECT MIN(machine_local_date) AS first_date, MAX(machine_local_date) AS last_date
        FROM nightly_therapy_aggregates
        WHERE user_id = CAST(:uid AS uuid)
    """),
            {"uid": current_user["id"]},
        )
        .mappings()
        .first()
    )

    if not range_row or not range_row["first_date"]:
        return SummaryStats(
            total_nights=0,
            nights_with_data=0,
            compliance_pct=0.0,
            avg_ahi=None,
            avg_pressure=None,
            ahi_trend=[],
            event_breakdown={},
        )

    # Total calendar nights in range
    total_nights_row = (
        db.execute(
            text("""
        SELECT (MAX(machine_local_date) - MIN(machine_local_date) + 1) AS total_nights,
               COUNT(DISTINCT machine_local_date) FILTER (WHERE usage_seconds > 0) AS nights_with_data,
               COUNT(DISTINCT machine_local_date) FILTER (WHERE usage_seconds >= 14400) AS compliant_nights
        FROM nightly_therapy_aggregates
        WHERE user_id = CAST(:uid AS uuid)
    """),
            {"uid": current_user["id"]},
        )
        .mappings()
        .first()
    )

    total_nights = int(total_nights_row["total_nights"])
    nights_with_data = int(total_nights_row["nights_with_data"])
    compliant_nights = int(total_nights_row["compliant_nights"])
    compliance_pct = round(compliant_nights / total_nights * 100, 1) if total_nights > 0 else 0.0

    # Per-night primary block: longest duration per folder_date
    primary_blocks = (
        db.execute(
            text("""
        SELECT
            (array_agg(s.id::text ORDER BY s.duration_seconds DESC))[1] AS id,
            s.folder_date,
            CASE WHEN nta.usage_seconds > 0
                 THEN SUM(s.total_ahi_events) / (nta.usage_seconds / 3600.0)
                 ELSE NULL END AS ahi,
            nta.usage_seconds AS duration_seconds,
            AVG(s.avg_pressure) AS avg_pressure
        FROM sessions s
        JOIN nightly_therapy_aggregates nta
          ON nta.user_id = s.user_id
         AND nta.machine_id IS NOT DISTINCT FROM s.machine_id
         AND nta.machine_local_date = s.folder_date
        WHERE s.user_id = CAST(:uid AS uuid)
        GROUP BY s.folder_date, s.machine_id, nta.usage_seconds
    """),
            {"uid": current_user["id"]},
        )
        .mappings()
        .all()
    )

    ahi_values = [float(r["ahi"]) for r in primary_blocks if r["ahi"] is not None]
    press_values = [float(r["avg_pressure"]) for r in primary_blocks if r["avg_pressure"] is not None]

    avg_ahi = round(sum(ahi_values) / len(ahi_values), 2) if ahi_values else None
    avg_pressure = round(sum(press_values) / len(press_values), 2) if press_values else None

    # AHI trend: all nights (most recent 90)
    ahi_trend_rows = (
        db.execute(
            text("""
        SELECT
            (array_agg(s.id::text ORDER BY s.duration_seconds DESC))[1] AS id,
            s.folder_date,
            CASE WHEN nta.usage_seconds > 0
                 THEN SUM(s.total_ahi_events) / (nta.usage_seconds / 3600.0)
                 ELSE NULL END AS ahi,
            nta.usage_seconds AS duration_seconds
        FROM sessions s
        JOIN nightly_therapy_aggregates nta
          ON nta.user_id = s.user_id
         AND nta.machine_id IS NOT DISTINCT FROM s.machine_id
         AND nta.machine_local_date = s.folder_date
        WHERE s.user_id = CAST(:uid AS uuid)
        GROUP BY s.folder_date, s.machine_id, nta.usage_seconds
        ORDER BY s.folder_date DESC
        LIMIT 90
    """),
            {"uid": current_user["id"]},
        )
        .mappings()
        .all()
    )

    ahi_trend = [
        DailyStat(
            folder_date=r["folder_date"],
            ahi=float(r["ahi"]) if r["ahi"] is not None else None,
            duration_hours=round(float(r["duration_seconds"]) / 3600, 2),
            session_id=r["id"],
        )
        for r in reversed(ahi_trend_rows)
    ]

    # Event breakdown totals (across all sessions)
    evt_row = (
        db.execute(
            text("""
        SELECT
            SUM(central_apnea_count)     AS central,
            SUM(obstructive_apnea_count) AS obstructive,
            SUM(hypopnea_count)          AS hypopnea,
            SUM(apnea_count)             AS apnea,
            SUM(arousal_count)           AS arousal
        FROM sessions
        WHERE user_id = CAST(:uid AS uuid)
    """),
            {"uid": current_user["id"]},
        )
        .mappings()
        .first()
    )

    event_breakdown = {
        "central_apnea": int(evt_row["central"] or 0),
        "obstructive_apnea": int(evt_row["obstructive"] or 0),
        "hypopnea": int(evt_row["hypopnea"] or 0),
        "apnea": int(evt_row["apnea"] or 0),
        "arousal": int(evt_row["arousal"] or 0),
    }

    return SummaryStats(
        total_nights=total_nights,
        nights_with_data=nights_with_data,
        compliance_pct=compliance_pct,
        avg_ahi=avg_ahi,
        avg_pressure=avg_pressure,
        ahi_trend=ahi_trend,
        event_breakdown=event_breakdown,
    )


@router.get("/overview", response_model=OverviewStats)
def get_overview(
    days: int = Query(180, ge=7, le=3650),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Long-range nightly trend data for the overview page.

    Multi-block nights are grouped into one row. Event counts are normalized to
    hourly indexes so they can be compared across nights of different lengths.
    """
    rows = (
        db.execute(
            text("""
        WITH night AS (
            SELECT
                s.folder_date,
                (array_agg(s.id::text ORDER BY s.duration_seconds DESC))[1] AS session_id,
                nta.start_datetime,
                nta.end_datetime,
                nta.usage_seconds AS duration_seconds,
                SUM(s.total_ahi_events) AS total_ahi_events,
                SUM(s.central_apnea_count) AS central_apnea_count,
                SUM(s.obstructive_apnea_count) AS obstructive_apnea_count,
                SUM(s.hypopnea_count) AS hypopnea_count,
                SUM(s.apnea_count) AS apnea_count,
                SUM(COALESCE(s.arousal_count, 0)) AS arousal_count,
                AVG(s.avg_pressure) AS avg_pressure,
                MAX(s.p95_pressure) AS p95_pressure,
                AVG(s.avg_leak) AS avg_leak,
                (array_agg(s.leak_unit ORDER BY s.duration_seconds DESC))[1] AS leak_unit,
                AVG(s.avg_flow_lim) AS avg_flow_lim,
                AVG(s.avg_tidal_vol) AS avg_tidal_vol,
                AVG(s.avg_min_vent) AS avg_min_vent,
                AVG(s.avg_resp_rate) AS avg_resp_rate,
                AVG(s.avg_spo2) AS avg_spo2,
                MIN(s.min_spo2) AS min_spo2
            FROM sessions s
            JOIN nightly_therapy_aggregates nta
              ON nta.user_id = s.user_id
             AND nta.machine_id IS NOT DISTINCT FROM s.machine_id
             AND nta.machine_local_date = s.folder_date
            WHERE s.user_id = CAST(:uid AS uuid)
              AND s.folder_date >= CURRENT_DATE - (:days * INTERVAL '1 day')
            GROUP BY s.folder_date, s.machine_id, nta.start_datetime,
                     nta.end_datetime, nta.usage_seconds
        ),
        metric AS (
            SELECT
                s.folder_date,
                ROUND((SUM(COALESCE(se.duration_seconds, 0)) / 60.0)::numeric, 1) AS large_leak_minutes
            FROM sessions s
            JOIN session_events se ON se.session_id = s.id
            WHERE s.user_id = CAST(:uid AS uuid)
              AND s.folder_date >= CURRENT_DATE - (:days * INTERVAL '1 day')
              AND se.event_type = 'Large Leak'
            GROUP BY s.folder_date
        ),
        spo2 AS (
            SELECT
                s.folder_date,
                AVG(ss.spo2) AS avg_spo2,
                MIN(ss.spo2) AS min_spo2,
                AVG(ss.pulse) AS avg_pulse
            FROM sessions s
            JOIN session_spo2 ss ON ss.session_id = s.id
            WHERE s.user_id = CAST(:uid AS uuid)
              AND s.folder_date >= CURRENT_DATE - (:days * INTERVAL '1 day')
            GROUP BY s.folder_date
        ),
        equipment AS (
            SELECT
                n.folder_date,
                MAX(n.folder_date - eq.start_date) AS equipment_age_days
            FROM night n
            LEFT JOIN LATERAL (
                SELECT DISTINCT ON (equipment_type)
                    equipment_type,
                    start_date
                FROM user_equipment
                WHERE user_id = CAST(:uid AS uuid)
                  AND start_date <= n.folder_date
                ORDER BY equipment_type, start_date DESC
            ) eq ON TRUE
            GROUP BY n.folder_date
        )
        SELECT
            n.folder_date,
            n.session_id,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.total_ahi_events / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS ahi,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.central_apnea_count / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS central_apnea_index,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.obstructive_apnea_count / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS obstructive_apnea_index,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.hypopnea_count / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS hypopnea_index,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.apnea_count / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS apnea_index,
            CASE WHEN n.duration_seconds > 0
                 THEN ROUND((n.arousal_count / (n.duration_seconds / 3600.0))::numeric, 2)
                 ELSE NULL END AS arousal_index,
            ROUND((n.duration_seconds / 3600.0)::numeric, 2) AS usage_hours,
            ROUND((EXTRACT(HOUR FROM n.start_datetime) + EXTRACT(MINUTE FROM n.start_datetime) / 60.0)::numeric, 2) AS session_start_hour,
            ROUND((EXTRACT(HOUR FROM n.end_datetime) + EXTRACT(MINUTE FROM n.end_datetime) / 60.0)::numeric, 2) AS session_end_hour,
            ROUND(n.avg_pressure::numeric, 2) AS avg_pressure,
            ROUND(n.p95_pressure::numeric, 2) AS p95_pressure,
            ROUND(n.avg_leak::numeric, 2) AS avg_leak,
            n.leak_unit,
            m.large_leak_minutes,
            ROUND(n.avg_flow_lim::numeric, 4) AS avg_flow_lim,
            ROUND(n.avg_tidal_vol::numeric, 2) AS avg_tidal_vol,
            ROUND(n.avg_min_vent::numeric, 2) AS avg_min_vent,
            ROUND(n.avg_resp_rate::numeric, 2) AS avg_resp_rate,
            COALESCE(sp.min_spo2, n.min_spo2) AS min_spo2,
            ROUND(COALESCE(sp.avg_spo2, n.avg_spo2)::numeric, 1) AS avg_spo2,
            ROUND(sp.avg_pulse::numeric, 1) AS avg_pulse,
            e.equipment_age_days
        FROM night n
        LEFT JOIN metric m ON m.folder_date = n.folder_date
        LEFT JOIN spo2 sp ON sp.folder_date = n.folder_date
        LEFT JOIN equipment e ON e.folder_date = n.folder_date
        ORDER BY n.folder_date
    """),
            {"uid": current_user["id"], "days": days},
        )
        .mappings()
        .all()
    )

    return OverviewStats(
        nights=[
            OverviewDailyStat(
                folder_date=row["folder_date"],
                session_id=row["session_id"],
                ahi=_float_or_none(row["ahi"]),
                central_apnea_index=_float_or_none(row["central_apnea_index"]),
                obstructive_apnea_index=_float_or_none(row["obstructive_apnea_index"]),
                hypopnea_index=_float_or_none(row["hypopnea_index"]),
                apnea_index=_float_or_none(row["apnea_index"]),
                arousal_index=_float_or_none(row["arousal_index"]),
                usage_hours=_float_or_none(row["usage_hours"]) or 0.0,
                session_start_hour=_float_or_none(row["session_start_hour"]),
                session_end_hour=_float_or_none(row["session_end_hour"]),
                avg_pressure=_float_or_none(row["avg_pressure"]),
                p95_pressure=_float_or_none(row["p95_pressure"]),
                avg_leak=_float_or_none(row["avg_leak"]),
                leak_unit=row["leak_unit"],
                large_leak_minutes=_float_or_none(row["large_leak_minutes"]),
                avg_flow_lim=_float_or_none(row["avg_flow_lim"]),
                avg_tidal_vol=_float_or_none(row["avg_tidal_vol"]),
                avg_min_vent=_float_or_none(row["avg_min_vent"]),
                avg_resp_rate=_float_or_none(row["avg_resp_rate"]),
                min_spo2=_float_or_none(row["min_spo2"]),
                avg_spo2=_float_or_none(row["avg_spo2"]),
                avg_pulse=_float_or_none(row["avg_pulse"]),
                equipment_age_days=row["equipment_age_days"],
            )
            for row in rows
        ]
    )
