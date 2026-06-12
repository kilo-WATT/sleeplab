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
* **Per-sample signals are not carried by the ImportRun.**
  :class:`WaveformSegment` exposes only ``sample_count``/``sample_rate_hz``
  metadata, not the underlying arrays. To still populate the sample tables
  *without* widening the vendor-neutral model, the execution layer passes the
  raw ``CPAPDirectory`` (``raw_directory=``) from the same parse; this bridge
  then writes ``session_metrics`` (full-resolution low-rate) and
  ``session_waveform`` (event-windowed high-rate) from ``CPAPSession.timeseries``.
  Channel *metadata* (``signal_channels``) is persisted from the ImportRun.
* **Summary statistics** (``avg_pressure``, ``p95_pressure``, ``avg_leak`` and the
  per-channel averages) are computed by the loader from the decoded timeseries
  (``ResMedNativeLoader._signal_metrics``), carried as DerivedValues, and mapped
  onto the ``sessions`` columns here. ``avg_pressure``/``p95_pressure`` use the
  device's set pressure (``Press.2s``), the same channel the old path used. AHI,
  event counts and the three usage semantics are carried through as before.
* **Settings snapshots** are persisted from ``Session.settings``. Each
  ``SettingsSnapshot`` is written to ``settings_snapshots`` via the shared
  ``db.upsert_settings_snapshot`` (idempotent), and its normalized settings are
  flattened onto the ``sessions`` columns (``therapy_mode`` …), mirroring the
  legacy STR projection. Today the loader emits only ``therapy_mode`` (from
  cpap-parser ``pressure_mode``); fields the parser does not expose stay ``NULL``
  and nothing is fabricated. Confidence/validation are conservative
  (``probable``/``partial``) — a single device-reported field, not yet
  cross-validated.

Remaining gaps (genuinely unavailable from cpap-py, written ``NULL``):

* Oximetry (``session_spo2``, ``has_spo2``) is not mapped yet.
* Settings beyond ``therapy_mode`` (mask_type, humidifier, pressure/EPR/ramp) are
  absent from the cpap-parser schema, so they remain ``NULL``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import DerivedValue, ImportRun, Session
from .resmed_native import _ANNOTATION_FILE_TYPES, ResMedNativeLoader

#: Event-focused waveform window, matching ``db.replace_session_waveform``: full
#: 25 Hz flow/pressure is far too large to store whole-night, so only merged
#: windows around scored events are persisted (the Event Inspector's needs).
_WAVEFORM_BEFORE_SECONDS = 120
_WAVEFORM_AFTER_SECONDS = 180

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
    raw_directory: Any = None,
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
        raw_directory: Optional raw ``cpap_parser.schema.CPAPDirectory`` from the
            same parse that produced ``run`` (see
            :meth:`ResMedNativeLoader.import_data_with_directory`). When supplied,
            the per-sample tables ``session_metrics`` (low-rate) and
            ``session_waveform`` (high-rate, event-windowed) are populated from
            ``CPAPSession.timeseries`` — data the vendor-neutral
            :class:`WaveformSegment` does not carry. When ``None`` those tables
            are left empty (detection-only/legacy callers).

    Returns:
        A summary dict with counts of what was written, suitable for logging and
        for :func:`importer.db.finish_import_run`:
        ``{"sessions", "blocks", "events", "channels", "settings", "derived_values",
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
        source_file_id,
        upsert_session,
        upsert_session_block,
        upsert_settings_snapshot,
    )

    machine_tz_name, machine_tz = _resolve_machine_tz(db_conn, user_id)

    counts = {
        "sessions": 0,
        "blocks": 0,
        "events": 0,
        "channels": 0,
        "settings": 0,
        "derived_values": 0,
        "summary_only_days": 0,
        "metric_rows": 0,
        "waveform_rows": 0,
    }

    # Index the raw parser's detailed file-sessions by night date so each
    # normalized Session can pull back its decoded sample arrays. Keyed exactly
    # like the loader keys its summaries (night date), so the join is a date
    # lookup. ``None`` when no raw directory was supplied.
    detailed_by_night = _index_detailed_sessions(raw_directory)

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
        # The cpap-parser path builds one block per detailed file-session from
        # ``CPAPSession.start_time -> end_time`` — i.e. a *recording span*, the
        # wall-clock extent of an EDF recording, NOT a mask-on/off therapy
        # interval. cpap-parser does not expose the granular STR mask intervals
        # the legacy path reads, so these blocks must be labeled honestly as
        # ``recording_span`` and must never masquerade as
        # ``resmed_str_mask_interval`` (which the nightly aggregate treats as the
        # authoritative therapy source). We record the span in
        # ``recording_duration_seconds`` and deliberately leave
        # ``therapy_duration_seconds`` NULL: per-block therapy time is not
        # available from the parser, so we do not invent it. See
        # docs/sleeplab_2_resmed_cutover_remaining_work.md ("session_blocks").
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
                source_kind="recording_span",
                recording_duration_seconds=duration_seconds,
                diagnostics=[
                    {
                        "code": "recording_span_not_mask_interval",
                        "message": (
                            "cpap-parser file-session recording span; not a "
                            "mask-on/off therapy interval. Per-block therapy "
                            "duration is unavailable from the parser."
                        ),
                    }
                ],
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

        # -- Settings snapshots ------------------------------------------
        # Persist the loader-provided SettingsSnapshots (today: therapy_mode only,
        # from cpap-parser's pressure_mode). Nothing is fabricated — only what the
        # normalized session actually carries is written, with the snapshot's own
        # (conservative) confidence. Idempotent: the helper's
        # ON CONFLICT (machine_id, effective_at, adapter_id) makes a re-import
        # update-in-place rather than duplicate.
        counts["settings"] += _write_settings_snapshots(
            db_conn,
            session=session,
            user_id=user_id,
            machine_id=machine_id,
            import_run_id=import_run_id,
            session_db_id=str(session_db_id),
            machine_tz=machine_tz,
            adapter_version=run.adapter_version,
            resolve_source_file_id=source_file_id,
            upsert=upsert_settings_snapshot,
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

        # -- Per-sample tables (session_metrics / session_waveform) -------
        # Populated only when the raw CPAPDirectory is available; the decoded
        # sample arrays live on CPAPSession.timeseries, not on the ImportRun.
        if detailed_by_night is not None:
            detailed = detailed_by_night.get(folder_date, [])
            counts["metric_rows"] += _write_session_metrics(
                db_conn, str(session_db_id), detailed, machine_tz
            )
            # The night's normalized events carry absolute (naive, machine-local)
            # onsets; the waveform writer rebases them onto each file-session so a
            # late-night event lands in the block that actually recorded it (not
            # the block cpap-py happened to park it on).
            night_events = [
                (event.start_time, float(event.duration_seconds or 0.0))
                for event in session.events
            ]
            counts["waveform_rows"] += _write_session_waveform(
                db_conn, str(session_db_id), detailed, machine_tz, night_events
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
    _settings = _session_settings_map(session)
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
        # Per-channel summary statistics are derived by the loader from the
        # decoded timeseries (see ResMedNativeLoader._signal_metrics) and carried
        # here as DerivedValues. ``avg_pressure``/``p95_pressure`` come from the
        # device set pressure (``Press.2s``), matching the old path.
        "avg_pressure": _derived_scalar(derived, "avg_pressure", default=None),
        "p95_pressure": _derived_scalar(derived, "p95_pressure", default=None),
        "avg_leak": _derived_scalar(derived, "avg_leak", default=None),
        "avg_resp_rate": _derived_scalar(derived, "avg_resp_rate", default=None),
        "avg_tidal_vol": _derived_scalar(derived, "avg_tidal_vol", default=None),
        "avg_min_vent": _derived_scalar(derived, "avg_min_vent", default=None),
        "avg_snore": _derived_scalar(derived, "avg_snore", default=None),
        "avg_flow_lim": _derived_scalar(derived, "avg_flow_lim", default=None),
        # Oximetry is not mapped by the loader yet (gap).
        "has_spo2": False,
        # Flattened settings columns mirror the legacy projection
        # (db._project_settings_to_sessions): only values the loader actually
        # carries are set; everything the parser does not expose stays None
        # (never fabricated). Today that is therapy_mode (from pressure_mode).
        "therapy_mode": _settings.get("therapy_mode"),
        "mask_type": _settings.get("mask_type"),
        "humidity_level": _settings.get("humidifier_level"),
        "temperature_c": _settings.get("tube_temperature_c"),
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


# -- Settings ---------------------------------------------------------------


def _session_settings_map(session: Session) -> dict[str, Any]:
    """Merge a session's ``SettingsSnapshot`` settings into one dict (later wins).

    Used to flatten the loader's settings onto the ``sessions`` columns, mirroring
    the legacy ``db._project_settings_to_sessions`` projection. Only keys the loader
    actually emits appear here — nothing is invented.
    """
    merged: dict[str, Any] = {}
    for snapshot in getattr(session, "settings", []) or []:
        merged.update(_normalized_settings(getattr(snapshot, "settings", {}) or {}))
    return merged


def _normalized_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Keep only real loader-provided settings, never missing placeholders."""
    return {
        key: value
        for key, value in settings.items()
        if value is not None
        and not (isinstance(value, str) and value.strip().casefold() == "unknown")
    }


def _write_settings_snapshots(
    db_conn: Any,
    *,
    session: Session,
    user_id: str,
    machine_id: str,
    import_run_id: str,
    session_db_id: str,
    machine_tz: ZoneInfo,
    adapter_version: str,
    resolve_source_file_id: Any,
    upsert: Any,
) -> int:
    """Persist the loader-provided ``SettingsSnapshot``s into ``settings_snapshots``.

    Writes one row per snapshot that actually carries settings, through the shared
    ``db.upsert_settings_snapshot`` helper (idempotent on
    ``(machine_id, effective_at, adapter_id)``). Conservative by construction:

    * a snapshot with no real settings is **skipped** (no empty/placeholder row);
    * ``vendor_settings`` is ``{}`` (cpap-parser exposes no raw vendor blob);
    * exact loader references that match persisted manifest paths are linked.
      Synthetic parser ids remain unlinked rather than being guessed;
    * the snapshot's own ``confidence`` (e.g. ``probable``) is preserved and the
      row is marked ``validation_status='partial'`` — a single device-reported
      field, not yet cross-validated, so nothing is overclaimed.
    """
    written = 0
    for snapshot in getattr(session, "settings", []) or []:
        normalized = _normalized_settings(dict(getattr(snapshot, "settings", {}) or {}))
        if not normalized:
            continue
        source_names = {
            key: value
            for key, value in dict(getattr(snapshot, "source_names", {}) or {}).items()
            if key in normalized
        }
        source_file_ids = _resolve_source_file_ids(
            db_conn,
            import_run_id=import_run_id,
            source_refs=getattr(snapshot, "source_file_ids", ()) or (),
            resolve=resolve_source_file_id,
        )
        written += upsert(
            db_conn,
            user_id=user_id,
            machine_id=machine_id,
            session_id=session_db_id,
            import_run_id=import_run_id,
            effective_at=_localize(snapshot.effective_at, machine_tz),
            normalized_settings=normalized,
            vendor_settings={},
            source_names=source_names,
            source_file_ids=source_file_ids,
            adapter_id=_ADAPTER_ID,
            parser_id="sleeplab.resmed_cpap_parser",
            parser_version=str(adapter_version),
            diagnostics=[],
            confidence=str(getattr(snapshot, "confidence", "probable")),
            validation_status="partial",
        )
    return written


# -- Small helpers ---------------------------------------------------------


def _resolve_source_file_ids(
    db_conn: Any,
    *,
    import_run_id: str,
    source_refs: tuple[str, ...],
    resolve: Any,
) -> list[str]:
    """Resolve exact manifest paths without inventing links for synthetic ids."""
    resolved: list[str] = []
    for source_ref in source_refs:
        source_id = resolve(db_conn, import_run_id, source_ref)
        if source_id and source_id not in resolved:
            resolved.append(source_id)
    return resolved


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
    """Therapy seconds for ``sessions.duration_seconds``.

    Order of preference, most-to-least authoritative for *therapy* time:

    1. ``computed_usage_hours`` — summed EDF therapy time, present on detailed
       (DATALOG) nights.
    2. ``summary_reported_usage_hours`` — STR.edf reported usage. This is the
       *only* therapy number available for summary-only / ghost nights (no
       DATALOG), and it is the same authoritative source the legacy path derives
       its STR mask intervals from. Without this fallback those 37 summary-only
       nights persisted ``duration_seconds = 0`` (start == end == midnight), so
       the nightly aggregate — which reads ``sessions.duration_seconds`` for
       block-less nights — dropped their entire therapy history. We never invent
       a value: a night with no reported usage still falls through to the span.
    3. The wall-clock span as a last resort.

    Note: detailed nights also persist ``recording_span`` blocks, so the nightly
    aggregate sums those blocks (recording spans) for them rather than this
    column. Carrying the authoritative therapy total here keeps
    ``sessions.duration_seconds`` truthful regardless, and is what recovers the
    summary-only nights through the view's block-less session fallback.
    """
    computed = _derived_scalar(derived, "computed_usage_hours", default=None)
    if isinstance(computed, (int, float)) and computed > 0:
        return int(round(computed * 3600))
    reported = _derived_scalar(derived, "summary_reported_usage_hours", default=None)
    if isinstance(reported, (int, float)) and reported > 0:
        return int(round(reported * 3600))
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


# -- Per-sample table bridging (session_metrics / session_waveform) --------


def _index_detailed_sessions(raw_directory: Any) -> dict[date, list] | None:
    """Group a raw ``CPAPDirectory``'s detailed file-sessions by night date.

    Returns ``None`` when no directory is supplied so the caller can cheaply skip
    the per-sample writes. Annotation-only files (EVE/CSL/AEV) carry no signal
    samples and are excluded, mirroring the loader's own grouping so the night
    keys line up with each Session's ``machine_local_date``.
    """
    if raw_directory is None:
        return None
    by_night: dict[date, list] = {}
    for cpap_session in raw_directory.sessions:
        if cpap_session.file_type in _ANNOTATION_FILE_TYPES:
            continue
        night = ResMedNativeLoader._night_date(cpap_session.start_time)
        by_night.setdefault(night, []).append(cpap_session)
    return by_night


def _low_rate_epoch_seconds(timeseries) -> float:
    """Seconds between consecutive low-rate (PLD) samples.

    cpap-py stores ``timestamps_low`` as epoch seconds spaced ``1 / rate`` apart;
    the spacing is the epoch. ResMed PLD is 2 s; fall back to that if the track
    is too short to measure.
    """
    low = timeseries.timestamps_low
    if len(low) >= 2 and low[1] > low[0]:
        return low[1] - low[0]
    return 2.0


def _write_session_metrics(
    db_conn: Any, session_db_id: str, detailed: list, machine_tz: ZoneInfo
) -> int:
    """Replace ``session_metrics`` (low-rate PLD samples) for one night.

    Mirrors ``importer.db.replace_session_metrics`` but sources its columns from
    cpap-py's decoded low-rate track instead of a raw EDF channel dict. The night
    may aggregate several file-sessions, so rows from every detailed session are
    written under the single night ``session_id`` after a one-time delete.

    cpap-py field -> ``session_metrics`` column
    -------------------------------------------
    * ``mask_pressure``       -> ``mask_pressure`` (``MaskPress.2s``)
    * ``set_pressure``        -> ``pressure``      (``Press.2s``)
    * ``epr_pressure``        -> ``epr_pressure``  (``EprPress.2s``)
    * ``leak``                -> ``leak``          (``Leak.2s``)
    * ``respiratory_rate``    -> ``resp_rate``     (``RespRate.2s``)
    * ``tidal_volume``        -> ``tidal_vol``     (``TidVol.2s``)
    * ``minute_ventilation``  -> ``min_vent``      (``MinVent.2s``)
    * ``snore``               -> ``snore``         (``Snore.2s``)
    * ``flow_limitation``     -> ``flow_lim``      (``FlowLim.2s``)
    """
    import psycopg2.extras

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM session_metrics WHERE session_id = %s", (session_db_id,))

    rows: list[tuple] = []
    for cpap_session in detailed:
        timeseries = cpap_session.timeseries
        if timeseries is None:
            continue
        # Tracks in ``session_metrics`` column order. ``set_pressure`` (Press.2s)
        # is the therapy pressure column; ``mask_pressure`` (MaskPress.2s) and
        # ``epr_pressure`` (EprPress.2s) are distinct measured/EPR pressures.
        tracks = (
            timeseries.mask_pressure,
            timeseries.set_pressure,
            timeseries.epr_pressure,
            timeseries.leak,
            timeseries.respiratory_rate,
            timeseries.tidal_volume,
            timeseries.minute_ventilation,
            timeseries.snore,
            timeseries.flow_limitation,
        )
        sample_count = max((len(track) for track in tracks), default=0)
        if sample_count == 0:
            continue
        epoch = _low_rate_epoch_seconds(timeseries)
        start = _localize(cpap_session.start_time, machine_tz)
        for index in range(sample_count):
            ts = start + timedelta(seconds=index * epoch)
            rows.append((session_db_id, ts, *(_sample(track, index) for track in tracks)))

    if not rows:
        return 0
    sql = """
    INSERT INTO session_metrics
        (session_id, ts, mask_pressure, pressure, epr_pressure, leak, resp_rate,
         tidal_vol, min_vent, snore, flow_lim)
    VALUES %s
    """
    with db_conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)
    return len(rows)


def _write_session_waveform(
    db_conn: Any,
    session_db_id: str,
    detailed: list,
    machine_tz: ZoneInfo,
    night_events: list[tuple[datetime, float]],
) -> int:
    """Replace ``session_waveform`` (high-rate BRP samples) for one night.

    Mirrors ``importer.db.replace_session_waveform``: only merged windows around
    scored events are stored (full-night 25 Hz is far too large).

    ``night_events`` are the night's events as ``(absolute_start, duration_s)``
    tuples in the loader's naive machine-local frame. Each file-session rebases
    them onto its own start (``event - session_start``) before windowing, exactly
    like the old path's ``_events_relative_to_waveform``. This matters because
    cpap-py parks an EVE file's events on the *first* BRP/PLD group regardless of
    when they occurred, so an event must be matched to the block that actually
    recorded its samples — not the block it is attached to.

    cpap-py field -> ``session_waveform`` column
    --------------------------------------------
    * ``flow_rate`` (``Flow.40ms``)  -> ``flow``
    * ``pressure``  (``Press.40ms``) -> ``pressure``
    """
    import psycopg2.extras

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM session_waveform WHERE session_id = %s", (session_db_id,))

    rows: list[tuple] = []
    for cpap_session in detailed:
        timeseries = cpap_session.timeseries
        if timeseries is None:
            continue
        flow = timeseries.flow_rate
        pressure = timeseries.pressure
        sample_count = max(len(flow), len(pressure))
        rate = cpap_session.sample_rate
        if sample_count == 0 or rate <= 0:
            continue
        epoch = 1.0 / rate
        # Rebase the night's events onto this block; only events whose absolute
        # time lands within (or near) this block's span survive the window clip.
        relative_onsets = [
            ((event_start - cpap_session.start_time).total_seconds(), duration)
            for event_start, duration in night_events
        ]
        windows = _merge_event_windows(relative_onsets, sample_count * epoch)
        if not windows:
            # No scored events land in this block -> nothing to window, like the
            # old path which stores no waveform for a block without events.
            continue
        start = _localize(cpap_session.start_time, machine_tz)
        for window_start, window_end in windows:
            first = max(0, int(window_start / epoch))
            last = min(sample_count, int(window_end / epoch) + 1)
            for index in range(first, last):
                ts = start + timedelta(seconds=index * epoch)
                rows.append(
                    (
                        session_db_id,
                        ts,
                        _sample(flow, index),
                        _sample(pressure, index, ndigits=2),
                    )
                )

    if not rows:
        return 0
    sql = "INSERT INTO session_waveform (session_id, ts, flow, pressure) VALUES %s"
    with db_conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=5000)
    return len(rows)


def _merge_event_windows(
    relative_onsets: list[tuple[float, float]], total_seconds: float
) -> list[tuple[float, float]]:
    """Merge ±window intervals around each event onset (seconds from start).

    ``relative_onsets`` are ``(onset_seconds, duration_seconds)`` already rebased
    to the block start. Mirrors ``db._merge_waveform_windows`` with the same
    before/after padding, clipped to the recorded span — onsets outside the span
    clip away to nothing.
    """
    windows: list[tuple[float, float]] = []
    for onset, duration in relative_onsets:
        start = max(0.0, onset - _WAVEFORM_BEFORE_SECONDS)
        end = min(total_seconds, onset + (duration or 0.0) + _WAVEFORM_AFTER_SECONDS)
        if end > start:
            windows.append((start, end))
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


def _sample(track: list, index: int, ndigits: int = 4) -> float | None:
    """Return ``round(track[index], ndigits)`` or ``None`` when out of range."""
    if index < len(track):
        return round(track[index], ndigits)
    return None
