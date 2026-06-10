"""SleepLab 2.0 ``ImportRun`` -> database persistence bridge.

This module is the missing link between the vendor-neutral loader contract and
the live database. :class:`~importer.loaders.resmed_native.ResMedNativeLoader`
(and any future :meth:`LoaderAdapter.import_data`) returns an in-memory
:class:`~importer.loaders.models.ImportRun` with *no* database coupling so it can
be diffed by conformance tests. :func:`persist_import_run` takes one of those
runs and writes it through the existing ``importer.db`` helpers, reusing exactly
the same upsert/replace functions the legacy native subprocess uses.

It is called by the loader-registry execution path in
:mod:`importer.loaders.execution` (gated behind ``SLEEPLAB_USE_CPAP_PARSER=1``),
never by the default subprocess importer.

Documented mapping gaps (``ImportRun`` -> ``sessions``/sample tables)
--------------------------------------------------------------------
The loader deliberately produces *less* than the full schema can hold; rather
than silently dropping or inventing data we record the gaps here:

* **Timestamps are naive machine-local.** The loader leaves session/block/event
  instants without a timezone (``timezone_basis == "machine_local"``) because
  localization is a normalization-layer concern. The ``TIMESTAMPTZ`` columns
  need an offset, so this bridge localizes every instant with the user's
  configured ``machine_tz`` (``user_import_settings``), matching the legacy
  importer's behavior.
* **Per-sample signals are not carried.** :class:`WaveformSegment` exposes only
  ``sample_count``/``sample_rate_hz`` metadata, not the underlying arrays, so the
  ``session_metrics`` / ``session_waveform`` / ``session_spo2`` sample tables are
  *not* populated from an ``ImportRun``. Channel *metadata* (``signal_channels``)
  is persisted; sample bridging is left for a future loader revision.
* **Summary statistics the loader does not compute** (``avg_pressure``,
  ``p95_pressure``, ``avg_leak``, the per-channel averages, oximetry) are written
  as ``NULL``/``0`` rather than fabricated. AHI, event counts and the three usage
  semantics *are* carried through.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import DerivedValue, ImportRun, Session

logger = logging.getLogger(__name__)

#: Event types that count toward the apnea/hypopnea indices, mirroring the
#: legacy importer's ``derive_summary``.
_AHI_EVENT_TYPES = frozenset({"Central Apnea", "Obstructive Apnea", "Hypopnea", "Apnea"})

#: ResMed cpap-parser execution adapter id (matches the loader's ``adapter_id``).
_ADAPTER_ID = "resmed-cpap-parser-v1"


class PersistenceError(RuntimeError):
    """Raised when an ``ImportRun`` cannot be written to the database."""


def persist_import_run(
    run: ImportRun,
    user_id: str,
    db_conn: Any,
    *,
    import_run_id: str,
    machine_id: str,
) -> dict[str, int]:
    """Persist an :class:`ImportRun` to the database.

    Args:
        run: The in-memory import result produced by a loader's
            :meth:`import_data`.
        user_id: Owning user UUID (string).
        db_conn: An open ``psycopg2`` connection (e.g. ``importer.db.get_conn()``).
        import_run_id: The durable ``import_runs`` row id created by
            ``api.import_runs.create_import_run`` for this upload.
        machine_id: The ``cpap_machines`` row id created alongside the run.

    Returns:
        A summary dict with counts of what was written, suitable for logging and
        for :func:`importer.db.finish_import_run`:
        ``{"sessions", "blocks", "events", "channels", "derived_values",
        "summary_only_days"}``.

    The function commits nothing itself — the caller owns the transaction so a
    failure can be rolled back and the run marked failed. Re-running is safe:
    every write goes through an idempotent upsert/replace keyed on stable source
    keys.

    Note:
        The brief specifies ``persist_import_run(run, user_id, db_conn)``. The
        durable ``import_run_id``/``machine_id`` are required because
        ``derived_values`` and ``sessions`` carry NOT-NULL/foreign-key linkage to
        the run and machine rows already created upstream by ``create_import_run``;
        they are passed as keyword-only arguments to keep that explicit.
    """
    # Local imports: psycopg2-backed helpers are only needed when actually
    # persisting, keeping ``import importer.loaders`` cheap for detection-only
    # and test contexts.
    from importer.db import (
        replace_derived_values,
        replace_session_events,
        upsert_session,
        upsert_session_block,
    )

    machine_tz_name, machine_tz = _resolve_machine_tz(db_conn, user_id)

    counts = {
        "sessions": 0,
        "blocks": 0,
        "events": 0,
        "channels": 0,
        "derived_values": 0,
        "summary_only_days": 0,
    }

    serial = run.machine.serial_number
    manufacturer = run.machine.manufacturer or "ResMed"

    for session in run.sessions:
        derived = {value.key: value for value in session.derived_values}
        has_detailed = bool(_derived_scalar(derived, "has_detailed_data", default=False))
        folder_date = _parse_local_date(session.machine_local_date)
        start_dt = _localize(session.start_time, machine_tz)
        duration_seconds = _session_duration_seconds(session, derived)

        session_data = _session_row(
            session=session,
            user_id=user_id,
            machine_id=machine_id,
            import_run_id=import_run_id,
            folder_date=folder_date,
            start_dt=start_dt,
            duration_seconds=duration_seconds,
            derived=derived,
            serial=serial,
            manufacturer=manufacturer,
            machine_tz_name=machine_tz_name,
            has_detailed=has_detailed,
        )
        session_db_id = upsert_session(db_conn, session_data)
        counts["sessions"] += 1
        if not has_detailed:
            counts["summary_only_days"] += 1

        # -- Blocks -------------------------------------------------------
        for block in session.blocks:
            duration_seconds = int((block.end_time - block.start_time).total_seconds())
            # Invalid PLD recordings can yield a non-positive interval (e.g. an
            # end before the start). The ``ck_session_blocks_interval`` check
            # constraint rejects those, so skip and surface the offending block
            # rather than aborting the whole import.
            if block.end_time <= block.start_time:
                logger.warning(
                    "Skipping session block %s with non-positive duration (%ds): "
                    "end %s <= start %s",
                    block.source_block_key,
                    duration_seconds,
                    block.end_time,
                    block.start_time,
                )
                continue
            upsert_session_block(
                db_conn,
                session_db_id=str(session_db_id),
                import_run_id=import_run_id,
                source_block_key=block.source_block_key,
                start_datetime=_localize(block.start_time, machine_tz),
                end_datetime=_localize(block.end_time, machine_tz),
                # Source-file ids are a UUID[] column; the parser path does not
                # yet map block files to persisted import_source_files rows, so
                # we pass an empty array rather than non-UUID strings.
                source_file_ids=[],
                source_kind="resmed_str_mask_interval",
                therapy_duration_seconds=duration_seconds,
            )
            counts["blocks"] += 1

        # -- Events -------------------------------------------------------
        event_tuples = _event_tuples(session, base=session.start_time)
        replace_session_events(
            db_conn,
            session_db_id,
            event_tuples,
            start_dt,
            import_run_id=import_run_id,
            source_file_id_value=None,
            adapter_id=_ADAPTER_ID,
        )
        counts["events"] += len(event_tuples)

        # -- Signal channel metadata -------------------------------------
        counts["channels"] += _replace_signal_channel_metadata(
            db_conn,
            session_db_id=str(session_db_id),
            import_run_id=import_run_id,
            signals=session.signals,
        )

        # -- Derived values ----------------------------------------------
        summary = {key: _jsonable(value.value) for key, value in derived.items()}
        if summary:
            counts["derived_values"] += replace_derived_values(
                db_conn,
                user_id=user_id,
                machine_id=machine_id,
                session_db_id=str(session_db_id),
                import_run_id=import_run_id,
                adapter_id=_ADAPTER_ID,
                summary=summary,
            )

    return counts


# -- Row mapping -----------------------------------------------------------


def _session_row(
    *,
    session: Session,
    user_id: str,
    machine_id: str,
    import_run_id: str,
    folder_date: date,
    start_dt: datetime,
    duration_seconds: int,
    derived: dict[str, DerivedValue],
    serial: str | None,
    manufacturer: str,
    machine_tz_name: str,
    has_detailed: bool,
) -> dict[str, Any]:
    """Build the flat ``upsert_session`` dict from a normalized Session.

    Averages/percentiles the loader does not compute are left ``None`` (gap);
    event counts and AHI are derived from the normalized events/derived values.
    """
    event_counts = _event_counts(session)
    ahi = _derived_scalar(derived, "ahi", default=None)
    return {
        "session_id": _session_id(session, folder_date),
        "folder_date": folder_date,
        "block_index": 0,
        "start_datetime": start_dt,
        "pld_start_datetime": start_dt,
        "duration_seconds": duration_seconds,
        "device_serial": serial,
        "manufacturer": manufacturer,
        "leak_kind": "unintentional",
        "leak_unit": "L/min",
        "ahi": ahi,
        "central_apnea_count": event_counts["Central Apnea"],
        "obstructive_apnea_count": event_counts["Obstructive Apnea"],
        "hypopnea_count": event_counts["Hypopnea"],
        "apnea_count": event_counts["Apnea"],
        "arousal_count": event_counts["Arousal"],
        "total_ahi_events": sum(event_counts[t] for t in _AHI_EVENT_TYPES),
        # Per-channel summary statistics are not computed by the loader (gap).
        "avg_pressure": None,
        "p95_pressure": None,
        "avg_leak": None,
        "avg_resp_rate": None,
        "avg_tidal_vol": None,
        "avg_min_vent": None,
        "avg_snore": None,
        "avg_flow_lim": None,
        # Oximetry is not mapped by the loader yet (gap).
        "has_spo2": False,
        "therapy_mode": None,
        "mask_type": None,
        "humidity_level": None,
        "temperature_c": None,
        "machine_tz": machine_tz_name,
        "user_id": user_id,
        "machine_id": machine_id,
        "import_run_id": import_run_id,
        "source_session_key": session.source_session_key,
        "provenance_status": (
            "native_resmed_cpap_parser" if has_detailed else "native_resmed_cpap_parser_summary_only"
        ),
        "adapter_id": _ADAPTER_ID,
    }


def _replace_signal_channel_metadata(
    db_conn: Any,
    *,
    session_db_id: str,
    import_run_id: str,
    signals: list,
) -> int:
    """Persist channel metadata directly.

    ``importer.db.replace_signal_channels`` consumes a raw EDF header object, not
    the loader's :class:`SignalChannel` list, so we cannot reuse it. We insert the
    normalized metadata the loader already produced. Samples are not carried by
    the ``ImportRun`` (gap, see module docstring).
    """
    import psycopg2.extras

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM signal_channels WHERE session_id = %s", (session_db_id,))
    if not signals:
        return 0

    # A session aggregates several recording intervals (one ``cpap_session`` per
    # EDF file), each emitting the *same* channels (e.g. flow/pressure/leak). The
    # ``signal_channels`` table holds one metadata row per channel per session
    # (unique on ``session_id, normalized_name, source_name``), so collapse the
    # repeats to one row per ``(channel_key, source_label)`` rather than letting
    # in-batch duplicates trip the ON CONFLICT. Prefer the highest sample rate so
    # a waveform-rate interval wins the channel_kind classification.
    by_channel: dict[tuple[str, str], Any] = {}
    for signal in signals:
        key = (signal.channel_key, signal.source_label)
        existing = by_channel.get(key)
        if existing is None or (signal.sample_rate_hz or 0) > (existing.sample_rate_hz or 0):
            by_channel[key] = signal

    rows = []
    for signal in by_channel.values():
        sample_rate = signal.sample_rate_hz
        channel_kind = "waveform" if sample_rate and sample_rate >= 5 else "low_rate"
        rows.append(
            (
                session_db_id,
                import_run_id,
                None,  # source_file_id: parser path does not map persisted files
                signal.channel_key,
                signal.source_label,
                signal.unit,
                sample_rate,
                channel_kind,
                signal.value_kind,
                signal.leak_kind,
                _ADAPTER_ID,
                "strong",
                "partial",
            )
        )
    with db_conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO signal_channels (
                session_id, import_run_id, source_file_id, normalized_name,
                source_name, unit, sample_rate_hz, channel_kind, value_kind,
                leak_kind, adapter_id, confidence, validation_status
            ) VALUES %s
            ON CONFLICT (session_id, normalized_name, source_name) DO UPDATE SET
                import_run_id = EXCLUDED.import_run_id,
                unit = EXCLUDED.unit,
                sample_rate_hz = EXCLUDED.sample_rate_hz,
                channel_kind = EXCLUDED.channel_kind,
                value_kind = EXCLUDED.value_kind,
                leak_kind = EXCLUDED.leak_kind,
                confidence = EXCLUDED.confidence,
                validation_status = EXCLUDED.validation_status,
                updated_at = NOW()
            """,
            rows,
        )
    return len(rows)


# -- Small helpers ---------------------------------------------------------


def _event_counts(session: Session) -> dict[str, int]:
    counts = {"Central Apnea": 0, "Obstructive Apnea": 0, "Hypopnea": 0, "Apnea": 0, "Arousal": 0}
    for event in session.events:
        if event.event_type in counts:
            counts[event.event_type] += 1
    return counts


def _event_tuples(session: Session, *, base: datetime) -> list[tuple[float, float | None, str]]:
    """Promote absolute-time normalized events to ``(onset, duration, type)``.

    ``replace_session_events`` expects onsets relative to a ``csl_start``; the
    loader carries absolute ``start_time`` instants, so we subtract the session
    start. ``base`` is the naive loader start (events share that frame).
    """
    tuples: list[tuple[float, float | None, str]] = []
    for event in session.events:
        onset = (event.start_time - base).total_seconds()
        tuples.append((onset, event.duration_seconds, event.event_type))
    return tuples


def _session_duration_seconds(session: Session, derived: dict[str, DerivedValue]) -> int:
    """Therapy seconds: prefer computed usage hours, fall back to the span."""
    computed = _derived_scalar(derived, "computed_usage_hours", default=None)
    if isinstance(computed, (int, float)) and computed > 0:
        return int(round(computed * 3600))
    span = (session.end_time - session.start_time).total_seconds()
    return max(int(round(span)), 0)


def _session_id(session: Session, folder_date: date) -> str:
    """A compact, stable per-machine ``session_id`` text value."""
    return f"cpapparser_{folder_date:%Y%m%d}"


def _derived_scalar(derived: dict[str, DerivedValue], key: str, *, default: Any) -> Any:
    value = derived.get(key)
    return value.value if value is not None else default


def _jsonable(value: Any) -> Any:
    """Coerce a normalized scalar to something ``json.dumps`` accepts."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_local_date(machine_local_date: str) -> date:
    return date.fromisoformat(machine_local_date)


def _localize(value: datetime, machine_tz: ZoneInfo) -> datetime:
    """Attach ``machine_tz`` to a naive loader instant (idempotent)."""
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=machine_tz)


def _resolve_machine_tz(db_conn: Any, user_id: str) -> tuple[str, ZoneInfo]:
    """Look up the user's configured machine timezone, defaulting to UTC."""
    name: str | None = None
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT machine_tz FROM user_import_settings WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if row and row[0]:
            name = row[0]
    except Exception:
        name = None
    try:
        zone = ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        zone = ZoneInfo("UTC")
    return zone.key, zone
