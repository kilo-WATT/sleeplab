"""
PostgreSQL connection and upsert helpers for the CPAP importer.
"""

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

_raw_dsn = os.environ.get("DATABASE_URL", "dbname=cpap")
DB_DSN = _raw_dsn.replace("postgresql+psycopg2://", "postgresql://", 1)


def get_conn() -> Any:
    """Establish a connection to the PostgreSQL database.

    Returns:
        A psycopg2 connection object initialized with DB_DSN.
    """
    return psycopg2.connect(DB_DSN)


def reconcile_machine(
    conn: Any,
    *,
    user_id: str,
    adapter_id: str,
    manufacturer: str | None,
    serial_number: str | None,
) -> str:
    """Find or create a deterministic machine record for direct/legacy imports."""

    serial = serial_number.strip() if serial_number and serial_number.strip() else None
    identity_key = (
        f"{adapter_id}:serial:{serial.casefold()}"
        if serial
        else f"{adapter_id}:unresolved-user:{user_id}"
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cpap_machines (
                user_id, manufacturer, serial_number, adapter_id, identity_key,
                identity_confidence, support_status, validation_status,
                source_identity, last_seen_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'experimental', 'partial',
                '{"source":"native-import-fallback"}'::jsonb, NOW(), NOW()
            )
            ON CONFLICT (user_id, identity_key) DO UPDATE SET
                manufacturer = COALESCE(EXCLUDED.manufacturer, cpap_machines.manufacturer),
                serial_number = COALESCE(EXCLUDED.serial_number, cpap_machines.serial_number),
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING id
            """,
            (user_id, manufacturer, serial, adapter_id, identity_key, "probable" if serial else "none"),
        )
        return str(cur.fetchone()[0])


def finish_import_run(
    conn: Any,
    import_run_id: str,
    *,
    status: str,
    imported_sessions: int,
    imported_blocks: int,
    imported_events: int,
    imported_channels: int,
    errors: list[str],
    imported_settings: int = 0,
    summary_only_days: int = 0,
    warnings: list[dict] | None = None,
    capability_status: dict | None = None,
) -> None:
    """Finalize a durable import run after native importer execution."""

    warnings = _dedupe_diagnostics(warnings or [])
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE import_source_files
            SET disposition = 'skipped',
                warning_state = warning_state || '[{"code":"not_consumed","message":"File was inspected but not consumed by the execution adapter."}]'::jsonb
            WHERE import_run_id = %s AND disposition = 'unknown'
            """,
            (import_run_id,),
        )
        cur.execute(
            """
            UPDATE import_runs
            SET status = %s,
                imported_session_count = %s,
                imported_block_count = %s,
                imported_event_count = %s,
                imported_channel_count = %s,
                imported_settings_count = %s,
                summary_only_day_count = %s,
                warnings = %s::jsonb,
                capability_status = %s::jsonb,
                errors = %s::jsonb,
                skipped_files = (
                    SELECT COALESCE(
                        jsonb_agg(jsonb_build_object(
                            'path', relative_path,
                            'role', parser_role,
                            'reason', 'not_consumed'
                        ) ORDER BY relative_path),
                        '[]'::jsonb
                    )
                    FROM import_source_files
                    WHERE import_run_id = %s AND disposition = 'skipped'
                ),
                completed_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                imported_sessions,
                imported_blocks,
                imported_events,
                imported_channels,
                imported_settings,
                summary_only_days,
                json.dumps(warnings),
                json.dumps(capability_status or {}),
                json.dumps([{"code": "native_import_error", "message": error} for error in errors]),
                import_run_id,
                import_run_id,
            ),
        )
    conn.commit()


def _dedupe_diagnostics(diagnostics: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        key = json.dumps(
            {
                "code": diagnostic.get("code"),
                "message": diagnostic.get("message"),
                "source_value": diagnostic.get("source_value"),
                "relative_path": diagnostic.get("relative_path"),
            },
            sort_keys=True,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(diagnostic)
    return deduped


def source_file_id(conn: Any, import_run_id: str | None, relative_path: str | None) -> str | None:
    """Resolve one persisted manifest entry and mark it used."""

    if not import_run_id or not relative_path:
        return None
    normalized = relative_path.replace("\\", "/")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE import_source_files
            SET disposition = 'used'
            WHERE import_run_id = %s AND relative_path = %s
            RETURNING id
            """,
            (import_run_id, normalized),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def session_exists(conn: Any, user_id: str, session_id: str) -> bool:
    """Check if a session already exists in the database.

    Args:
        conn: The psycopg2 database connection.
        user_id: The UUID string of the user.
        session_id: The unique session identifier string.

    Returns:
        True if the session exists, False otherwise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM sessions WHERE user_id = %s AND session_id = %s",
            (user_id, session_id),
        )
        return cur.fetchone() is not None


def get_session_db_id(conn: Any, user_id: str, session_id: str) -> str | None:
    """Retrieve the primary database ID of a session.

    Args:
        conn: The psycopg2 database connection.
        user_id: The UUID string of the user.
        session_id: The unique session identifier string.

    Returns:
        The UUID string of the session from the database, or None if not found.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM sessions WHERE user_id = %s AND session_id = %s",
            (user_id, session_id),
        )
        row = cur.fetchone()
        return row[0] if row else None


def upsert_session(conn: Any, data: dict) -> int:
    """Insert a new session or update an existing one.

    Args:
        conn: The psycopg2 database connection.
        data: A dictionary containing all required column values for the
            sessions table.

    Returns:
        The database ID of the inserted or updated session.
    """
    data.setdefault("manufacturer", None)
    data.setdefault("leak_kind", None)
    data.setdefault("leak_unit", None)
    if not data.get("machine_id"):
        data["machine_id"] = reconcile_machine(
            conn,
            user_id=data["user_id"],
            adapter_id=data.get("adapter_id") or "legacy-session-v1",
            manufacturer=data.get("manufacturer"),
            serial_number=data.get("device_serial"),
        )
    data.setdefault("import_run_id", None)
    data.setdefault("source_session_key", data["session_id"])
    data.setdefault("provenance_status", "legacy_unknown")
    sql = """
    INSERT INTO sessions (
        session_id, folder_date, block_index, start_datetime, pld_start_datetime,
        duration_seconds, device_serial, ahi,
        central_apnea_count, obstructive_apnea_count, hypopnea_count,
        apnea_count, arousal_count, total_ahi_events,
        avg_pressure, p95_pressure, avg_leak, avg_resp_rate, avg_tidal_vol,
        avg_min_vent, avg_snore, avg_flow_lim, has_spo2,
        therapy_mode, mask_type, humidity_level, temperature_c,
        machine_tz, manufacturer, leak_kind, leak_unit, user_id,
        machine_id, import_run_id, source_session_key, provenance_status, updated_at
    ) VALUES (
        %(session_id)s, %(folder_date)s, %(block_index)s, %(start_datetime)s, %(pld_start_datetime)s,
        %(duration_seconds)s, %(device_serial)s, %(ahi)s,
        %(central_apnea_count)s, %(obstructive_apnea_count)s, %(hypopnea_count)s,
        %(apnea_count)s, %(arousal_count)s, %(total_ahi_events)s,
        %(avg_pressure)s, %(p95_pressure)s, %(avg_leak)s, %(avg_resp_rate)s, %(avg_tidal_vol)s,
        %(avg_min_vent)s, %(avg_snore)s, %(avg_flow_lim)s, %(has_spo2)s,
        %(therapy_mode)s, %(mask_type)s, %(humidity_level)s, %(temperature_c)s,
        %(machine_tz)s, %(manufacturer)s, %(leak_kind)s, %(leak_unit)s, %(user_id)s,
        %(machine_id)s, %(import_run_id)s, %(source_session_key)s, %(provenance_status)s, NOW()
    )
    ON CONFLICT (machine_id, source_session_key)
        WHERE machine_id IS NOT NULL AND source_session_key IS NOT NULL
    DO UPDATE SET
        folder_date             = EXCLUDED.folder_date,
        block_index             = EXCLUDED.block_index,
        start_datetime          = EXCLUDED.start_datetime,
        pld_start_datetime      = EXCLUDED.pld_start_datetime,
        duration_seconds        = EXCLUDED.duration_seconds,
        device_serial           = EXCLUDED.device_serial,
        ahi                     = EXCLUDED.ahi,
        central_apnea_count     = EXCLUDED.central_apnea_count,
        obstructive_apnea_count = EXCLUDED.obstructive_apnea_count,
        hypopnea_count          = EXCLUDED.hypopnea_count,
        apnea_count             = EXCLUDED.apnea_count,
        arousal_count           = EXCLUDED.arousal_count,
        total_ahi_events        = EXCLUDED.total_ahi_events,
        avg_pressure            = EXCLUDED.avg_pressure,
        p95_pressure            = EXCLUDED.p95_pressure,
        avg_leak                = EXCLUDED.avg_leak,
        avg_resp_rate           = EXCLUDED.avg_resp_rate,
        avg_tidal_vol           = EXCLUDED.avg_tidal_vol,
        avg_min_vent            = EXCLUDED.avg_min_vent,
        avg_snore               = EXCLUDED.avg_snore,
        avg_flow_lim            = EXCLUDED.avg_flow_lim,
        has_spo2                = EXCLUDED.has_spo2,
        therapy_mode            = EXCLUDED.therapy_mode,
        mask_type               = EXCLUDED.mask_type,
        humidity_level          = EXCLUDED.humidity_level,
        temperature_c           = EXCLUDED.temperature_c,
        machine_tz              = EXCLUDED.machine_tz,
        manufacturer            = COALESCE(NULLIF(EXCLUDED.manufacturer, ''), NULLIF(sessions.manufacturer, '')),
        leak_kind               = COALESCE(EXCLUDED.leak_kind, sessions.leak_kind),
        leak_unit               = COALESCE(EXCLUDED.leak_unit, sessions.leak_unit),
        import_run_id           = COALESCE(EXCLUDED.import_run_id, sessions.import_run_id),
        provenance_status       = EXCLUDED.provenance_status,
        -- user_id intentionally excluded: re-import must not change ownership
        updated_at              = NOW()
    RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, data)
        return cur.fetchone()[0]


def replace_session_events(
    conn,
    session_db_id: int,
    events: list,
    csl_start: datetime,
    *,
    import_run_id: str | None = None,
    source_file_id_value: str | None = None,
    adapter_id: str = "legacy-session-v1",
):
    """Delete existing events for this session and insert the new list."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM session_events WHERE session_id = %s", (session_db_id,))

    if not events:
        return

    rows = []
    for index, (onset, duration, event_type) in enumerate(_dedupe_events(events)):
        event_dt = csl_start + timedelta(seconds=onset)
        source_event_key = (
            f"{source_file_id_value or 'generated'}:{event_type}:{round(float(onset), 3)}:"
            f"{round(float(duration or 0), 3)}:{index}"
        )
        rows.append(
            (
                session_db_id,
                event_type,
                onset,
                duration if duration is not None else None,
                event_dt,
                source_event_key,
                event_type,
                import_run_id,
                source_file_id_value,
                adapter_id,
                "strong" if source_file_id_value else "probable",
                "partial",
            )
        )

    sql = """
    INSERT INTO session_events (
        session_id, event_type, onset_seconds, duration_seconds, event_datetime,
        source_event_key, source_event_type, import_run_id, source_file_id,
        adapter_id, confidence, validation_status
    )
    VALUES %s
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)


def upsert_session_block(
    conn: Any,
    *,
    session_db_id: str,
    import_run_id: str | None,
    source_block_key: str,
    start_datetime: datetime,
    end_datetime: datetime,
    source_file_ids: list[str],
    source_kind: str = "recording_span",
    therapy_duration_seconds: int | None = None,
    source_reported_duration_seconds: int | None = None,
    recording_duration_seconds: int | None = None,
    diagnostics: list[dict] | None = None,
    confidence: str = "strong",
    validation_status: str = "partial",
) -> None:
    """Persist one explicit therapy block."""

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO session_blocks (
                session_id, import_run_id, source_block_key, block_kind,
                start_datetime, end_datetime, source_file_ids, confidence,
                validation_status, source_kind, source_reported_duration_seconds,
                recording_duration_seconds, therapy_duration_seconds, diagnostics, updated_at
            ) VALUES (
                %s, %s, %s, 'therapy', %s, %s, %s::uuid[], %s, %s, %s, %s, %s,
                %s, %s::jsonb, NOW()
            )
            ON CONFLICT (session_id, source_block_key) DO UPDATE SET
                import_run_id = EXCLUDED.import_run_id,
                start_datetime = EXCLUDED.start_datetime,
                end_datetime = EXCLUDED.end_datetime,
                source_file_ids = EXCLUDED.source_file_ids,
                confidence = EXCLUDED.confidence,
                validation_status = EXCLUDED.validation_status,
                source_kind = EXCLUDED.source_kind,
                source_reported_duration_seconds = EXCLUDED.source_reported_duration_seconds,
                recording_duration_seconds = EXCLUDED.recording_duration_seconds,
                therapy_duration_seconds = EXCLUDED.therapy_duration_seconds,
                diagnostics = EXCLUDED.diagnostics,
                updated_at = NOW()
            """,
            (
                session_db_id,
                import_run_id,
                source_block_key,
                start_datetime,
                end_datetime,
                source_file_ids,
                confidence,
                validation_status,
                source_kind,
                source_reported_duration_seconds,
                recording_duration_seconds,
                therapy_duration_seconds,
                json.dumps(diagnostics or []),
            ),
        )


def replace_resmed_str_day(
    conn: Any,
    *,
    user_id: str,
    machine_id: str,
    import_run_id: str,
    adapter_id: str,
    folder_date,
    str_day: Any,
    source_file_id_value: str | None,
) -> tuple[int, int, bool]:
    """Replace STR-derived blocks/settings for one machine-local therapy day."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, start_datetime,
                   start_datetime + duration_seconds * INTERVAL '1 second' AS end_datetime
            FROM sessions
            WHERE user_id = %s AND machine_id = %s AND folder_date = %s
            ORDER BY start_datetime
            """,
            (user_id, machine_id, folder_date),
        )
        sessions = cur.fetchall()

    summary_only = not sessions
    if summary_only:
        session_id = _upsert_summary_session(
            conn,
            user_id=user_id,
            machine_id=machine_id,
            import_run_id=import_run_id,
            adapter_id=adapter_id,
            folder_date=folder_date,
            str_day=str_day,
        )
        sessions = [(session_id, str_day.intervals[0].start, str_day.intervals[-1].end)]

    source_ids = [source_file_id_value] if source_file_id_value else []
    duration_diagnostics = [
        item for item in str_day.diagnostics if "duration" in item.get("affects", [])
    ]
    block_validation = (
        "partial"
        if any(item.get("severity") in {"warning", "error"} for item in duration_diagnostics)
        else "validated"
    )
    current_keys: list[str] = []
    for interval in str_day.intervals:
        session_id = _closest_session(interval.start, interval.end, sessions)
        source_key = f"str:{folder_date.isoformat()}:{interval.index}"
        current_keys.append(source_key)
        upsert_session_block(
            conn,
            session_db_id=session_id,
            import_run_id=import_run_id,
            source_block_key=source_key,
            start_datetime=interval.start,
            end_datetime=interval.end,
            source_file_ids=source_ids,
            source_kind="resmed_str_mask_interval",
            therapy_duration_seconds=interval.duration_seconds,
            source_reported_duration_seconds=str_day.summary_usage_seconds,
            diagnostics=duration_diagnostics,
            confidence="exact",
            validation_status=block_validation,
        )
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM session_blocks b
            USING sessions s
            WHERE b.session_id = s.id
              AND s.user_id = %s
              AND s.machine_id = %s
              AND s.folder_date = %s
              AND b.source_kind = 'resmed_str_mask_interval'
              AND NOT (b.source_block_key = ANY(%s))
            """,
            (user_id, machine_id, folder_date, current_keys),
        )

    representative_session_id = _closest_session(
        str_day.intervals[0].start, str_day.intervals[-1].end, sessions
    )
    effective_at = datetime.combine(
        folder_date,
        time(12),
        tzinfo=str_day.intervals[0].start.tzinfo,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM settings_snapshots
            WHERE machine_id = %s AND adapter_id = %s
              AND effective_at >= %s AND effective_at < %s
              AND effective_at <> %s
            """,
            (
                machine_id,
                adapter_id,
                effective_at,
                effective_at + timedelta(days=1),
                effective_at,
            ),
        )
    settings_count = upsert_settings_snapshot(
        conn,
        user_id=user_id,
        machine_id=machine_id,
        session_id=representative_session_id,
        import_run_id=import_run_id,
        effective_at=effective_at,
        normalized_settings=str_day.normalized_settings,
        vendor_settings=str_day.vendor_settings,
        source_names=str_day.source_names,
        source_file_ids=source_ids,
        adapter_id=adapter_id,
        parser_id="sleeplab.resmed_str",
        parser_version="1",
        diagnostics=str_day.diagnostics,
    )
    _project_settings_to_sessions(
        conn, user_id, machine_id, folder_date, str_day.normalized_settings
    )
    return len(str_day.intervals), settings_count, summary_only


def upsert_settings_snapshot(
    conn: Any,
    *,
    user_id: str,
    machine_id: str,
    session_id: str | None,
    import_run_id: str,
    effective_at: datetime,
    normalized_settings: dict,
    vendor_settings: dict,
    source_names: dict,
    source_file_ids: list[str],
    adapter_id: str,
    parser_id: str,
    parser_version: str,
    diagnostics: list[dict],
    confidence: str = "strong",
    validation_status: str | None = None,
) -> int:
    # ``confidence``/``validation_status`` default to the legacy STR behavior
    # (``'strong'`` + diagnostics-derived) so existing callers are unchanged; the
    # cpap-parser persistence bridge passes its own conservative values for a
    # single-field, not-yet-cross-validated mapping (e.g. ``probable``/``partial``).
    resolved_validation = validation_status or (
        "partial"
        if any(item.get("severity") in {"warning", "error"} for item in diagnostics)
        else "validated"
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO settings_snapshots (
                user_id, machine_id, session_id, import_run_id, effective_at,
                normalized_settings, vendor_settings, source_names, source_file_ids,
                adapter_id, confidence, validation_status, parser_id, parser_version,
                diagnostics, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::uuid[],
                %s, %s, %s, %s, %s, %s::jsonb, NOW()
            )
            ON CONFLICT (machine_id, effective_at, adapter_id) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                import_run_id = EXCLUDED.import_run_id,
                normalized_settings = EXCLUDED.normalized_settings,
                vendor_settings = EXCLUDED.vendor_settings,
                source_names = EXCLUDED.source_names,
                source_file_ids = EXCLUDED.source_file_ids,
                confidence = EXCLUDED.confidence,
                validation_status = EXCLUDED.validation_status,
                parser_id = EXCLUDED.parser_id,
                parser_version = EXCLUDED.parser_version,
                diagnostics = EXCLUDED.diagnostics,
                updated_at = NOW()
            """,
            (
                user_id,
                machine_id,
                session_id,
                import_run_id,
                effective_at,
                json.dumps(normalized_settings),
                json.dumps(vendor_settings),
                json.dumps(source_names),
                source_file_ids,
                adapter_id,
                confidence,
                resolved_validation,
                parser_id,
                parser_version,
                json.dumps(diagnostics),
            ),
        )
    return 1


def _closest_session(start: datetime, end: datetime, sessions: list[tuple]) -> str:
    def distance(item: tuple) -> tuple[float, float]:
        _, session_start, session_end = item
        overlap = max(0.0, (min(end, session_end) - max(start, session_start)).total_seconds())
        return (-overlap, abs((session_start - start).total_seconds()))

    return str(min(sessions, key=distance)[0])


def _upsert_summary_session(
    conn: Any,
    *,
    user_id: str,
    machine_id: str,
    import_run_id: str,
    adapter_id: str,
    folder_date,
    str_day: Any,
) -> str:
    start = str_day.intervals[0].start
    data = {
        "session_id": f"str_{folder_date:%Y%m%d}",
        "folder_date": folder_date,
        "block_index": 0,
        "start_datetime": start,
        "pld_start_datetime": start,
        "duration_seconds": str_day.usage_seconds,
        "device_serial": None,
        "manufacturer": "ResMed",
        "leak_kind": "unintentional",
        "leak_unit": "L/s",
        "ahi": None,
        "central_apnea_count": 0,
        "obstructive_apnea_count": 0,
        "hypopnea_count": 0,
        "apnea_count": 0,
        "arousal_count": None,
        "total_ahi_events": 0,
        "avg_pressure": None,
        "p95_pressure": None,
        "avg_leak": None,
        "avg_resp_rate": None,
        "avg_tidal_vol": None,
        "avg_min_vent": None,
        "avg_snore": None,
        "avg_flow_lim": None,
        "has_spo2": False,
        "therapy_mode": str_day.normalized_settings.get("therapy_mode"),
        "mask_type": str_day.normalized_settings.get("mask_type"),
        "humidity_level": str_day.normalized_settings.get("humidifier_level"),
        "temperature_c": str_day.normalized_settings.get("tube_temperature_c"),
        "machine_tz": start.tzinfo.key if hasattr(start.tzinfo, "key") else "UTC",
        "user_id": user_id,
        "machine_id": machine_id,
        "import_run_id": import_run_id,
        "source_session_key": f"str:{folder_date.isoformat()}:summary",
        "provenance_status": "native_resmed_summary_only",
        "adapter_id": adapter_id,
    }
    return str(upsert_session(conn, data))


def _project_settings_to_sessions(
    conn: Any, user_id: str, machine_id: str, folder_date, settings: dict
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sessions
            SET therapy_mode = COALESCE(%s, therapy_mode),
                mask_type = COALESCE(%s, mask_type),
                humidity_level = COALESCE(%s, humidity_level),
                temperature_c = COALESCE(%s, temperature_c),
                updated_at = NOW()
            WHERE user_id = %s AND machine_id = %s AND folder_date = %s
            """,
            (
                settings.get("therapy_mode"),
                settings.get("mask_type"),
                settings.get("humidifier_level"),
                settings.get("tube_temperature_c"),
                user_id,
                machine_id,
                folder_date,
            ),
        )


def replace_signal_channels(
    conn: Any,
    *,
    session_db_id: str,
    import_run_id: str | None,
    source_file_id_value: str | None,
    adapter_id: str,
    header: Any,
) -> int:
    """Replace channel metadata discovered in a parsed EDF header."""

    with conn.cursor() as cur:
        cur.execute("DELETE FROM signal_channels WHERE session_id = %s", (session_db_id,))
    rows = []
    duration = float(header.duration_per_record or 0)
    for signal in header.signals:
        if signal.label == "Crc16":
            continue
        sample_rate = signal.num_samples_per_record / duration if duration > 0 else None
        normalized_name, channel_kind, leak_kind = _normalized_signal(signal.label, sample_rate)
        rows.append(
            (
                session_db_id,
                import_run_id,
                source_file_id_value,
                normalized_name,
                signal.label,
                signal.dim or None,
                sample_rate,
                channel_kind,
                "sample",
                leak_kind,
                adapter_id,
                "strong",
                "partial",
            )
        )
    if rows:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO signal_channels (
                    session_id, import_run_id, source_file_id, normalized_name,
                    source_name, unit, sample_rate_hz, channel_kind, value_kind,
                    leak_kind, adapter_id, confidence, validation_status
                ) VALUES %s
                """,
                rows,
            )
    return len(rows)


def replace_derived_values(
    conn: Any,
    *,
    user_id: str,
    machine_id: str,
    session_db_id: str,
    import_run_id: str | None,
    adapter_id: str,
    summary: dict,
) -> int:
    """Persist provenance for native importer summary calculations."""

    units = {
        "ahi": "events/hour",
        "avg_pressure": "cmH2O",
        "p95_pressure": "cmH2O",
        "avg_leak": "L/s",
    }
    rows = [
        (
            user_id,
            machine_id,
            session_db_id,
            import_run_id,
            key,
            json.dumps(value),
            units.get(key),
            "sleeplab.native_resmed.summary",
            "1",
            json.dumps(["session_events", "session_metrics"]),
            adapter_id,
            "partial",
        )
        for key, value in summary.items()
    ]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM derived_values WHERE session_id = %s", (session_db_id,))
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO derived_values (
                user_id, machine_id, session_id, import_run_id, key, value, unit,
                method, method_version, input_refs, adapter_id, validation_status
            ) VALUES %s
            """,
            rows,
        )
    return len(rows)


def _normalized_signal(source_name: str, sample_rate: float | None) -> tuple[str, str, str | None]:
    names = {
        "MaskPress.2s": "mask_pressure",
        "Press.2s": "pressure",
        "EprPress.2s": "epr_pressure",
        "Leak.2s": "leak",
        "RespRate.2s": "respiratory_rate",
        "TidVol.2s": "tidal_volume",
        "MinVent.2s": "minute_ventilation",
        "Snore.2s": "snore",
        "FlowLim.2s": "flow_limitation",
        "Flow.40ms": "flow",
        "Press.40ms": "pressure",
        # SA2/SAD oximetry (1 Hz). Units come from the EDF dim field: SpO2 in
        # "%", Pulse in "bpm". Classification stays low-rate via the sample-rate
        # rule below.
        "SpO2.1s": "spo2",
        "Pulse.1s": "pulse",
    }
    normalized = names.get(source_name, f"unknown:{source_name}")
    kind = "waveform" if sample_rate and sample_rate >= 5 else "low_rate"
    return normalized, kind, "unintentional" if normalized == "leak" else None


def replace_session_metrics(conn, session_db_id: int, header, channels: dict, start_datetime: datetime | None = None):
    """Delete existing metrics for this session and bulk-insert all PLD time-series rows."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM session_metrics WHERE session_id = %s", (session_db_id,))

    labels = [
        "MaskPress.2s",
        "Press.2s",
        "EprPress.2s",
        "Leak.2s",
        "RespRate.2s",
        "TidVol.2s",
        "MinVent.2s",
        "Snore.2s",
        "FlowLim.2s",
    ]

    # Determine samples per record from first non-Crc16 signal
    data_signals = [s for s in header.signals if s.label != "Crc16"]
    spr = data_signals[0].num_samples_per_record  # 30 samples per 60s record = 2s epochs
    dur = header.duration_per_record  # 60.0 seconds
    epoch = dur / spr  # 2.0 seconds
    pld_start = start_datetime or header.start_datetime

    rows = []
    for rec in range(header.num_records):
        for si in range(spr):
            abs_idx = rec * spr + si
            ts = pld_start + timedelta(seconds=rec * dur + si * epoch)
            row = [session_db_id, ts]
            for label in labels:
                vals = channels.get(label)
                row.append(round(vals[abs_idx], 4) if vals and abs_idx < len(vals) else None)
            rows.append(tuple(row))

    sql = """
    INSERT INTO session_metrics
        (session_id, ts, mask_pressure, pressure, epr_pressure, leak, resp_rate,
         tidal_vol, min_vent, snore, flow_lim)
    VALUES %s
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)


def replace_session_spo2(conn, session_db_id: int, header, spo2_data: tuple, start_datetime: datetime | None = None):
    """Delete existing SpO2 rows and insert new ones."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM session_spo2 WHERE session_id = %s", (session_db_id,))

    pulse_vals, spo2_vals = spo2_data
    spo2_sig = header.signals[1]  # SpO2.1s at 1 Hz
    spr = spo2_sig.num_samples_per_record  # 1 sample/second per record
    dur = header.duration_per_record
    pld_start = start_datetime or header.start_datetime

    rows = []
    for rec in range(header.num_records):
        for si in range(spr):
            abs_idx = rec * spr + si
            ts = pld_start + timedelta(seconds=rec * dur + si)
            p = pulse_vals[abs_idx] if abs_idx < len(pulse_vals) else None
            s = spo2_vals[abs_idx] if abs_idx < len(spo2_vals) else None
            rows.append((session_db_id, ts, s if s != -1 else None, p if p != -1 else None))

    sql = "INSERT INTO session_spo2 (session_id, ts, spo2, pulse) VALUES %s"
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)


def replace_session_waveform(
    conn,
    session_db_id: int,
    header,
    channels: dict,
    events: list | None = None,
    before_seconds: int = 120,
    after_seconds: int = 180,
    start_datetime=None,
    csl_start_datetime=None,
    delete_existing: bool = True,
):
    """
    Delete existing BRP waveform rows and bulk-insert event-focused samples.

    Full-night BRP is large: a typical 7-hour night is ~630k rows at 25 Hz.
    The Event Inspector only needs windows around scored events, so by default
    we store merged event windows rather than the entire night.
    """
    if delete_existing:
        clear_session_waveform(conn, session_db_id)

    flow_vals = channels.get("Flow.40ms")
    pressure_vals = channels.get("Press.40ms")
    if not flow_vals and not pressure_vals:
        return

    data_signals = [s for s in header.signals if s.label != "Crc16"]
    if not data_signals:
        return

    spr = data_signals[0].num_samples_per_record
    dur = header.duration_per_record
    if spr <= 0 or dur <= 0:
        return

    epoch = dur / spr
    start = start_datetime or header.start_datetime
    total_samples = spr * header.num_records
    windows = _merge_waveform_windows(
        _events_relative_to_waveform(events or [], csl_start_datetime, start),
        before_seconds,
        after_seconds,
    )
    if not windows:
        return

    rows = []
    for start_idx, end_idx in windows:
        start_idx = max(0, int(start_idx / epoch))
        end_idx = min(total_samples, int(end_idx / epoch) + 1)
        for idx in range(start_idx, end_idx):
            ts = start + timedelta(seconds=idx * epoch)
            flow = flow_vals[idx] if flow_vals and idx < len(flow_vals) else None
            pressure = pressure_vals[idx] if pressure_vals and idx < len(pressure_vals) else None
            rows.append(
                (
                    session_db_id,
                    ts,
                    round(flow, 4) if flow is not None else None,
                    round(pressure, 2) if pressure is not None else None,
                )
            )

    if not rows:
        return

    sql = "INSERT INTO session_waveform (session_id, ts, flow, pressure) VALUES %s"
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)


def clear_session_waveform(conn, session_db_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM session_waveform WHERE session_id = %s", (session_db_id,))


def _dedupe_events(events: list) -> list:
    deduped = []
    seen = set()
    for onset, duration, event_type in events:
        key = (event_type, round(float(onset), 1), None if duration is None else round(float(duration), 1))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((onset, duration, event_type))
    return deduped


def _events_relative_to_waveform(events: list, csl_start: datetime | None, waveform_start: datetime) -> list:
    if csl_start is None:
        return events

    relative_events = []
    for onset, duration, event_type in events:
        event_dt = csl_start + timedelta(seconds=onset)
        relative_onset = (event_dt - waveform_start).total_seconds()
        relative_events.append((relative_onset, duration, event_type))
    return relative_events


def _merge_waveform_windows(events: list, before_seconds: int, after_seconds: int) -> list[tuple[float, float]]:
    windows = []
    for onset, duration, _event_type in events:
        duration = duration or 0
        windows.append((max(0, onset - before_seconds), onset + duration + after_seconds))
    if not windows:
        return []

    windows.sort()
    merged = [windows[0]]
    for start, end in windows[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged
