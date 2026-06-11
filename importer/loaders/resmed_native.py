"""Reference ResMed loader backed by the pinned ``cpap-parser`` fork.

This is the first full :meth:`LoaderAdapter.import_data` implementation in the
SleepLab 2.0 loader registry and is intended to be read as the *reference* for
the multi-manufacturer work that follows. It deliberately favours explicit,
boring mapping code over cleverness so a reviewer adding a second manufacturer
can copy the shape.

Responsibilities are split exactly the way the loader-and-conformance plan
prescribes:

* **Detection / identity / capabilities** are delegated to the existing
  :class:`~importer.loaders.detectors.resmed.ResMedStructuralAdapter`. There is
  no reason to reimplement structural detection here, and keeping one detector
  avoids two adapters disagreeing about what a ResMed card looks like.
* **Import** parses the card with ``cpap_parser`` and maps its *normalized*
  output into the vendor-neutral 2.0 contract (:class:`ImportRun`).

What this loader consumes from the fixed ``cpap-parser`` (fix/resmed-four-bugs,
awaiting upstream MR !12). Each item maps to one of the five regression tests in
``tests/conformance/test_resmed_airsense10.py``:

1. **Usage is three distinct numbers, not one.** ``CPAPSessionSummary`` exposes
   ``summary_reported_usage`` (STR.edf as reported), ``computed_usage`` (summed
   EDF session durations) and ``recording_span`` (wall-clock span). We emit all
   three as separate :class:`DerivedValue`\\s rather than collapsing them into a
   single "usage" field, because they have genuinely different meanings on
   fragmented nights.
2. **Serial may legitimately be absent.** ``MachineInfo.serial_number`` is now
   ``None`` when no identity record yields a serial — never the literal string
   ``"Unknown"``. We propagate ``None`` and never substitute a placeholder.
3. **Waveform timestamps are absolute.** ``TimeSeriesData.timestamps`` are real
   UTC epoch seconds, so we trust the parser's session ``start_time`` for each
   :class:`WaveformSegment` and never recompute an origin from ``i / rate``.
4. **Ghost days are flagged, not deleted.** ``has_detailed_data is False`` marks
   STR-history-only days. We still import them (as summary-only sessions) and
   record the flag, instead of silently dropping summary history.
5. **BRP/PLD are not double-counted.** ``computed_usage`` already de-duplicates
   BRP vs PLD therapy time upstream, so we use it directly for therapy duration
   instead of summing per-file session durations ourselves.

Persistence is intentionally *out of scope*: :meth:`import_data` returns an
in-memory :class:`ImportRun` so conformance tests can diff normalized output
without a database. Timezone localization is also out of scope here — it is a
normalization-layer concern per the plan — so timestamps are left as the
parser's machine-local instants and ``timezone_basis`` is reported as
``"machine_local"``.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from .base import LoaderAdapter
from .detectors.resmed import ResMedStructuralAdapter
from .models import (
    Capabilities,
    Confidence,
    DerivedValue,
    DetectedDevice,
    Event,
    ImportOptions,
    ImportRun,
    ImportStatus,
    ImportWarning,
    MachineIdentity,
    NormalizedScalar,
    Session,
    SessionBlock,
    SettingsSnapshot,
    SignalChannel,
    ValidationStatus,
    WaveformSegment,
)

#: ``CPAPSession.file_type`` codes that are annotation-only and must not count
#: toward therapy duration or be treated as recording blocks.
_ANNOTATION_FILE_TYPES = frozenset({"EVE", "CSL", "AEV"})

#: High-rate (BRP) waveform channels and their units, in emit order.
_HIGH_RATE_CHANNELS: tuple[tuple[str, str], ...] = (
    ("flow_rate", "L/min"),
    ("pressure", "cmH2O"),
)

#: Low-rate (PLD) signal channels and their units, in emit order.
_LOW_RATE_CHANNELS: tuple[tuple[str, str], ...] = (
    ("mask_pressure", "cmH2O"),
    ("set_pressure", "cmH2O"),  # Press.2s — commanded therapy pressure
    ("epr_pressure", "cmH2O"),  # EprPress.2s — EPR-adjusted exhale pressure
    ("leak", "L/min"),
    ("tidal_volume", "mL"),
    ("minute_ventilation", "L/min"),
    ("respiratory_rate", "1/min"),
    ("snore", "index"),
    ("flow_limitation", "index"),
)


class ResMedNativeLoader(LoaderAdapter):
    """Full ResMed loader: structural detection + ``cpap-parser`` import.

    Detection, identity peeking and capability reporting are delegated to
    :class:`ResMedStructuralAdapter`; only :meth:`import_data` is original to
    this class. The adapter id is distinct from the structural detector's
    (``resmed-native-v2``) so both may be registered side by side without the
    registry rejecting a duplicate id.
    """

    adapter_id = "resmed-cpap-parser-v1"
    adapter_version = "0.1"
    # Lower priority value sorts first; keep the structural detector ahead of
    # this importer so existing detection/planning behavior is unchanged.
    priority = 20

    def __init__(self, detector: ResMedStructuralAdapter | None = None) -> None:
        self._detector = detector or ResMedStructuralAdapter()

    # -- Detection / identity / capabilities (delegated) -------------------

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        # Re-stamp the structural detector's candidates with this adapter id so
        # the registry routes import_data back here.
        candidates = self._detector.detect(source_root)
        return [self._restamp(candidate) for candidate in candidates]

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        return self._detector.peek_info(detected)

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        return self._detector.capabilities(detected)

    def _restamp(self, candidate: DetectedDevice) -> DetectedDevice:
        if candidate.adapter_id == self.adapter_id:
            return candidate
        return DetectedDevice(
            adapter_id=self.adapter_id,
            source_root=candidate.source_root,
            device_path=candidate.device_path,
            manufacturer_hint=candidate.manufacturer_hint,
            family_hint=candidate.family_hint,
            confidence=candidate.confidence,
            evidence=candidate.evidence,
            device_key_hint=candidate.device_key_hint,
            competing_adapter_ids=candidate.competing_adapter_ids,
            requires_user_choice=candidate.requires_user_choice,
            warnings=candidate.warnings,
        )

    # -- Import ------------------------------------------------------------

    def import_data(self, detected: DetectedDevice, options: ImportOptions) -> ImportRun:
        """Parse the detected ResMed card and map it to a normalized ImportRun.

        Requires the pinned ``cpap-parser`` (and its ``cpap-py`` backend) to be
        installed. We import it lazily so this module — and the registry that
        imports it — load fine in environments where the parser is absent.
        """
        run, _directory = self.import_data_with_directory(detected, options)
        return run

    def import_data_with_directory(
        self, detected: DetectedDevice, options: ImportOptions
    ):
        """Parse the card *once* and return ``(ImportRun, CPAPDirectory)``.

        :meth:`import_data` returns only the vendor-neutral :class:`ImportRun`
        because that is all conformance tests need. The persistence layer,
        however, needs the *raw* ``CPAPDirectory`` as well: the per-sample arrays
        (flow/pressure/leak/…) live on ``CPAPSession.timeseries`` but are
        deliberately *not* carried by :class:`WaveformSegment` (which holds only
        ``sample_count``/``sample_rate``). Rather than re-parse the card or widen
        the vendor-neutral model, this method hands the already-parsed directory
        back so ``persist_import_run`` can populate ``session_metrics`` /
        ``session_waveform`` directly. The two outputs come from a single parse.
        """
        # Lazy import: keep the dependency optional at module-load time.
        from cpap_parser.adapters.resmed import ResMedAdapter
        from cpap_parser.schema import CPAPDirectory

        started_at = datetime.now()
        source_root = detected.source_root
        identity = self.peek_info(detected)
        capabilities = self.capabilities(detected)

        adapter = ResMedAdapter()
        directory: CPAPDirectory = adapter.extract_and_map(
            source_root, include_timeseries=options.include_waveforms
        )

        warnings: list[ImportWarning] = []
        machine = self._merge_identity(identity, directory, warnings)
        machine_key = machine.serial_number or f"unresolved:{detected.device_key_hint or source_root.name}"

        # Group detailed file-sessions by night date so they can be attached to
        # the matching daily summary. ``_night_date`` mirrors the parser: a
        # session that starts before noon belongs to the previous night.
        detailed_by_night: dict[object, list] = {}
        for cpap_session in directory.sessions:
            if cpap_session.file_type in _ANNOTATION_FILE_TYPES:
                continue
            night = self._night_date(cpap_session.start_time)
            detailed_by_night.setdefault(night, []).append(cpap_session)

        sessions: list[Session] = []
        for summary in directory.daily_summaries:
            sessions.append(
                self._build_session(
                    summary=summary,
                    detailed=detailed_by_night.get(summary.date, []),
                    machine_key=machine_key,
                    include_waveforms=options.include_waveforms,
                    run_warnings=warnings,
                )
            )

        status = ImportStatus.COMPLETE
        if any(w.severity == "error" for w in warnings):
            status = ImportStatus.FAILED if not options.allow_partial else ImportStatus.PARTIAL

        run = ImportRun(
            run_id=str(uuid.uuid4()),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            source_fingerprint="",  # set by the planning layer, not the parser
            started_at=started_at,
            completed_at=datetime.now(),
            status=status,
            detected_device=detected,
            machine=machine,
            capabilities=capabilities,
            source_files=[],  # cpap-parser does not expose a source manifest yet
            sessions=sessions,
            warnings=warnings,
        )
        return run, directory

    # -- Mapping helpers ---------------------------------------------------

    @staticmethod
    def _merge_identity(
        identity: MachineIdentity,
        directory,  # cpap_parser.schema.CPAPDirectory (lazy import, see import_data)
        warnings: list[ImportWarning],
    ) -> MachineIdentity:
        """Prefer the structural peek serial; fall back to the parser's.

        Both sources now represent an absent serial as ``None`` (never
        ``"Unknown"``). We record a warning when neither yields a serial so the
        machine is reconciled rather than silently merged.
        """
        parser_serial = directory.machine.serial_number  # None when absent
        serial = identity.serial_number or parser_serial
        if serial is None:
            warnings.append(
                ImportWarning(
                    code="resmed_serial_absent",
                    severity="warning",
                    message="No serial number was found; machine needs reconciliation.",
                    affects=("identity",),
                )
            )
        return MachineIdentity(
            manufacturer=identity.manufacturer or "ResMed",
            family=identity.family or (directory.machine.series or None),
            model=identity.model or (directory.machine.model or None),
            model_number=identity.model_number or (directory.machine.product_code or None),
            serial_number=serial,
            firmware_version=identity.firmware_version,
            data_format_version=identity.data_format_version,
            loader_identity=identity.loader_identity,
            identity_confidence=identity.identity_confidence,
            source_fields=identity.source_fields,
            warnings=identity.warnings,
        )

    def _build_session(
        self,
        summary,
        detailed: list,
        machine_key: str,
        include_waveforms: bool,
        run_warnings: list[ImportWarning],
    ) -> Session:
        """Map one daily summary (+ any detailed file-sessions) to a Session."""
        has_detailed_data = bool(summary.has_detailed_data)
        local_date = summary.date.isoformat()
        warnings: list[ImportWarning] = []

        # Session boundaries: prefer real detailed-session instants; otherwise
        # fall back to the summary's calendar day (ghost / summary-only day).
        if detailed:
            start_time = min(s.start_time for s in detailed)
            end_time = max(s.end_time for s in detailed)
        else:
            start_time = datetime.combine(summary.date, datetime.min.time())
            end_time = start_time
            if not has_detailed_data:
                warnings.append(
                    ImportWarning(
                        code="resmed_summary_only_day",
                        severity="info",
                        message=(
                            "STR history day without detailed DATALOG data; "
                            "imported as summary-only, not deleted."
                        ),
                        relative_path="STR.edf",
                        affects=("sessions", "summary_only_days"),
                    )
                )

        session = Session(
            source_session_key=f"resmed:{machine_key}:{local_date}",
            machine_key=machine_key,
            start_time=start_time,
            end_time=end_time,
            machine_local_date=local_date,
            timezone_basis="machine_local",
            warnings=warnings,
        )
        session.settings = self._session_settings(summary, start_time)
        session.derived_values = self._summary_derived_values(summary, has_detailed_data)
        # Per-signal summary statistics (avg/p95 pressure, avg leak, resp rate,
        # …) are derived here, in the loader, so they are conformance-testable and
        # so the persistence layer can map them straight onto the ``sessions``
        # summary columns. They require decoded timeseries, which only exist when
        # waveforms were requested.
        if include_waveforms:
            session.derived_values.extend(self._signal_metrics(detailed))
        session.blocks = self._session_blocks(detailed, machine_key, local_date)
        session.events = self._session_events(detailed)
        # Large-leak bands are derived from the low-rate leak signal (which only
        # exists when waveforms were requested), matching the old path so OSCAR's
        # leak overlay is reproduced. They never count toward AHI.
        if include_waveforms:
            session.events.extend(self._large_leak_events(detailed))
        if include_waveforms:
            session.signals, session.waveforms = self._session_waveforms(detailed, machine_key, local_date)
            # Distinguish a detailed night that simply lacks high-rate BRP
            # waveform samples from one that has them. Low-rate/session data may
            # still be present and the session is still valid; we record *why*
            # the waveform view is empty rather than letting an absent or
            # malformed BRP file look like a complete night. Severity stays
            # "warning" (not "error") so the run is not forced partial — absence
            # of high-rate samples is a coverage gap, not a parse failure.
            if detailed and not self._has_high_rate_waveform(session.waveforms):
                session.warnings.append(
                    ImportWarning(
                        code="resmed_waveform_absent",
                        severity="warning",
                        message=(
                            "Detailed night has session/PLD data but no BRP "
                            "high-rate waveform samples; the waveform view is "
                            "empty (no samples were fabricated)."
                        ),
                        relative_path="DATALOG",
                        affects=("waveforms",),
                    )
                )
        # Surface this night's diagnostics at the run level so they are persisted
        # into ``import_runs.warnings`` (via ``execution._warning_dict`` ->
        # ``finish_import_run``) and shown in import history — not only carried on
        # the in-memory ``session.warnings``. ``finish_import_run`` dedupes
        # identical entries, so repeated summary-only/waveform-absent nights
        # collapse to one diagnostic. Severity is unchanged here, so a non-error
        # warning still does not force the run partial.
        run_warnings.extend(session.warnings)
        return session

    @staticmethod
    def _has_high_rate_waveform(waveforms: list[WaveformSegment]) -> bool:
        """True if any persisted segment is a high-rate BRP channel.

        High-rate channel keys (``flow_rate``/``pressure``) are unique to BRP;
        low-rate PLD channels use distinct keys (``mask_pressure`` etc.), so a
        low-rate-only night reports no high-rate waveform here.
        """
        high_rate_keys = {channel for channel, _unit in _HIGH_RATE_CHANNELS}
        return any(segment.channel_key in high_rate_keys for segment in waveforms)

    @staticmethod
    def _session_settings(summary, effective_at: datetime) -> list[SettingsSnapshot]:
        """Map the device-reported therapy mode into a minimal ``SettingsSnapshot``.

        cpap-parser exposes exactly **one** normalized therapy *setting* on the
        daily summary: ``CPAPSessionSummary.pressure_mode`` — the STR.edf mode code
        rendered as a label (``"CPAP"``/``"APAP"``/``"BiLevel …"``/``"ASV"``). The
        rest of the ``SettingsSnapshot`` vocabulary (min/max/set pressure, EPR,
        ramp, humidifier, mask type) is **not present** in the cpap-parser schema —
        ``pressure_50``/``pressure_95`` are *measured* mask-pressure percentiles,
        not configured settings — so only ``therapy_mode`` is mapped here. See
        ``docs/sleeplab_2_resmed_normalized_output_gap_audit.md`` §10.

        A missing or unrecognised mode is left **absent** (no snapshot at all),
        never coerced to a placeholder: ``pressure_mode`` is ``""`` when unset and
        the literal ``"Unknown"`` for an STR mode code the parser does not map, and
        both mean "no real setting" — consistent with the conformance
        missing-≠-off semantics. When no real setting is present, ``Session.settings``
        stays empty.

        ``effective_at`` reuses the session's own ``start_time`` (the detailed
        night's first session start, or the summary calendar-day anchor for a
        summary-only/ghost day) — a defensible existing anchor, never an invented
        global effective date. ``confidence`` is deliberately conservative
        (``PROBABLE``): the value is device-reported but is an early, single-field
        mapping not yet cross-validated against an independent reference.
        """
        mode = getattr(summary, "pressure_mode", None)
        settings: dict[str, NormalizedScalar] = {}
        source_names: dict[str, str] = {}
        # Only a real, recognised mode is mapped; "" / "Unknown" stay absent.
        if mode and mode != "Unknown":
            settings["therapy_mode"] = mode
            source_names["therapy_mode"] = "pressure_mode"
        if not settings:
            return []
        return [
            SettingsSnapshot(
                effective_at=effective_at,
                settings=settings,
                source_names=source_names,
                source_file_ids=("STR.edf",),
                confidence=Confidence.PROBABLE,
            )
        ]

    @staticmethod
    def _summary_derived_values(summary, has_detailed_data: bool) -> list[DerivedValue]:
        """Emit the three usage semantics + key indices as DerivedValues.

        The three usage values are kept distinct on purpose (fix #2): collapsing
        them loses the therapy-time vs wall-clock-span distinction that matters
        on fragmented nights.
        """
        values: list[DerivedValue] = [
            DerivedValue(
                key="summary_reported_usage_hours",
                value=summary.summary_reported_usage,
                unit="h",
                method="resmed_str_reported",
                input_refs=("STR.edf",),
                validation=ValidationStatus.PARTIAL,
            ),
            DerivedValue(
                key="computed_usage_hours",
                # Already BRP/PLD de-duplicated upstream (fix #5): this is summed
                # EDF therapy time, not BRP+PLD double-counted.
                value=summary.computed_usage,
                unit="h",
                method="resmed_edf_session_sum",
                input_refs=("DATALOG",),
                validation=ValidationStatus.PARTIAL,
            ),
            DerivedValue(
                key="recording_span_hours",
                value=summary.recording_span,
                unit="h",
                method="resmed_wall_clock_span",
                input_refs=("DATALOG",),
                validation=ValidationStatus.PARTIAL,
            ),
            DerivedValue(
                key="has_detailed_data",
                # Ghost days (fix #4) are flagged here, never dropped.
                value=has_detailed_data,
                unit=None,
                method="resmed_detailed_data_present",
                input_refs=("STR.edf", "DATALOG"),
                validation=ValidationStatus.VALIDATED,
            ),
            DerivedValue(
                key="ahi",
                value=summary.ahi,
                unit="1/h",
                method="resmed_str_reported",
                input_refs=("STR.edf",),
                validation=ValidationStatus.PARTIAL,
            ),
        ]
        return values

    @staticmethod
    def _signal_metrics(detailed: list) -> list[DerivedValue]:
        """Compute the old importer's per-night summary statistics.

        This reproduces ``importer.import_sessions.derive_summary`` for the
        cpap-parser path. The night may span several detailed file-sessions
        (one ``CPAPSession`` per EDF), so we concatenate each channel's samples
        across them before aggregating — matching the old path's behavior of
        averaging the night's low-rate signal as a whole.

        cpap-py signal label -> ``sessions`` summary column mapping
        ----------------------------------------------------------
        * ``timeseries.set_pressure`` (``Press.2s``) -> ``avg_pressure`` /
          ``p95_pressure``. This is the device's *set* therapy pressure, exactly
          the channel the old path used. (The patched ``PLD_SIGNAL_MAP`` now
          decodes ``Press.2s`` into ``set_pressure``; before that it was dropped
          and we fell back to ``mask_pressure``.)
        * ``timeseries.leak`` (``Leak.2s``)               -> ``avg_leak``
        * ``timeseries.respiratory_rate`` (``RespRate.2s``) -> ``avg_resp_rate``
        * ``timeseries.tidal_volume`` (``TidVol.2s``)     -> ``avg_tidal_vol``
        * ``timeseries.minute_ventilation`` (``MinVent.2s``) -> ``avg_min_vent``
        * ``timeseries.snore`` (``Snore.2s``)             -> ``avg_snore``
        * ``timeseries.flow_limitation`` (``FlowLim.2s``) -> ``avg_flow_lim``

        Filtering matches ``derive_summary`` exactly: pressure / resp-rate /
        tidal-volume / minute-ventilation drop non-positive samples; leak / snore
        / flow-limitation are averaged as-is.
        """
        channels: dict[str, list[float]] = {
            "set_pressure": [],
            "leak": [],
            "respiratory_rate": [],
            "tidal_volume": [],
            "minute_ventilation": [],
            "snore": [],
            "flow_limitation": [],
        }
        for cpap_session in detailed:
            timeseries = cpap_session.timeseries
            if timeseries is None:
                continue
            for key in channels:
                channels[key].extend(getattr(timeseries, key, []))

        def safe_mean(values: list[float]) -> float | None:
            return round(statistics.mean(values), 4) if values else None

        def percentile(values: list[float], pct: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            return round(ordered[int(pct * len(ordered))], 2)

        positive_pressure = [v for v in channels["set_pressure"] if v > 0]
        resp_rate = [v for v in channels["respiratory_rate"] if v > 0]
        tidal_volume = [v for v in channels["tidal_volume"] if v > 0]
        minute_ventilation = [v for v in channels["minute_ventilation"] if v > 0]

        metrics: list[tuple[str, NormalizedScalar, str | None]] = [
            ("avg_pressure", safe_mean(positive_pressure), "cmH2O"),
            ("p95_pressure", percentile(positive_pressure, 0.95), "cmH2O"),
            ("avg_leak", safe_mean(channels["leak"]), "L/min"),
            ("avg_resp_rate", safe_mean(resp_rate), "1/min"),
            ("avg_tidal_vol", safe_mean(tidal_volume), "mL"),
            ("avg_min_vent", safe_mean(minute_ventilation), "L/min"),
            ("avg_snore", safe_mean(channels["snore"]), "index"),
            ("avg_flow_lim", safe_mean(channels["flow_limitation"]), "index"),
        ]
        return [
            DerivedValue(
                key=key,
                value=value,
                unit=unit,
                method="resmed_pld_signal_summary",
                input_refs=("DATALOG",),
                validation=ValidationStatus.PARTIAL,
            )
            for key, value, unit in metrics
        ]

    def _session_blocks(self, detailed: list, machine_key: str, local_date: str) -> list[SessionBlock]:
        blocks: list[SessionBlock] = []
        for index, cpap_session in enumerate(detailed):
            blocks.append(
                SessionBlock(
                    source_block_key=f"resmed:{machine_key}:{local_date}:{index}",
                    start_time=cpap_session.start_time,
                    end_time=cpap_session.end_time,
                    block_kind=cpap_session.file_type or "recording",
                    source_file_ids=(f"{local_date}:{cpap_session.file_type}:{index}",),
                )
            )
        return blocks

    @staticmethod
    def _session_events(detailed: list) -> list[Event]:
        events: list[Event] = []
        for index, cpap_session in enumerate(detailed):
            for ev_index, cpap_event in enumerate(cpap_session.events):
                # ``timestamp_sec`` is an onset relative to the session start;
                # promote it to an absolute instant off the (correct) start.
                events.append(
                    Event(
                        source_event_key=f"{index}:{ev_index}",
                        event_type=cpap_event.event_type,
                        source_type=cpap_event.event_type,
                        start_time=cpap_session.start_time + timedelta(seconds=cpap_event.timestamp_sec),
                        duration_seconds=cpap_event.duration_sec,
                        source_file_id=f"{cpap_session.file_type}:{index}",
                        confidence=Confidence.STRONG,
                    )
                )
        return events

    @staticmethod
    def _large_leak_events(detailed: list, threshold: float = 0.4) -> list[Event]:
        """Derive ``Large Leak`` duration events from the low-rate leak signal.

        Mirrors ``importer.import_sessions.derive_large_leak_events`` so the
        cpap-parser path overlays the same large-leak bands the old path — and
        OSCAR — show. cpap-py's ``timeseries.leak`` values are numerically
        identical to the old ``Leak.2s`` channel, so the same ``0.4`` threshold
        applies directly.

        Like the old path, each event is anchored at the *end* of an
        over-threshold span (``start_time`` = span end) with ``duration_seconds``
        spanning back to where the leak first crossed the threshold. ``Large
        Leak`` is not an AHI event, so it never affects event counts or AHI.
        """
        events: list[Event] = []
        for index, cpap_session in enumerate(detailed):
            timeseries = cpap_session.timeseries
            if timeseries is None or not timeseries.leak:
                continue
            leak = timeseries.leak
            sample_seconds = ResMedNativeLoader._low_rate_epoch_seconds(timeseries)
            file_id = f"{cpap_session.file_type}:{index}"

            def emit(span_start: int, span_end: int) -> None:
                events.append(
                    Event(
                        source_event_key=f"largeleak:{index}:{span_start}:{span_end}",
                        event_type="Large Leak",
                        source_type="Large Leak",
                        # Absolute end of the span (old path anchors onset here).
                        start_time=cpap_session.start_time
                        + timedelta(seconds=span_end * sample_seconds),
                        duration_seconds=(span_end - span_start) * sample_seconds,
                        source_file_id=file_id,
                        # Derived from a signal threshold, not a device-scored event.
                        confidence=Confidence.PROBABLE,
                    )
                )

            span_start: int | None = None
            for sample_index, value in enumerate(leak):
                if value >= threshold and span_start is None:
                    span_start = sample_index
                elif value < threshold and span_start is not None:
                    emit(span_start, sample_index)
                    span_start = None
            if span_start is not None:
                emit(span_start, len(leak))
        return events

    @staticmethod
    def _low_rate_epoch_seconds(timeseries) -> float:
        """Seconds between consecutive low-rate (PLD) samples (ResMed PLD = 2 s)."""
        low = timeseries.timestamps_low
        if len(low) >= 2 and low[1] > low[0]:
            return low[1] - low[0]
        return 2.0

    def _session_waveforms(
        self, detailed: list, machine_key: str, local_date: str
    ) -> tuple[list[SignalChannel], list[WaveformSegment]]:
        signals: list[SignalChannel] = []
        segments: list[WaveformSegment] = []
        for index, cpap_session in enumerate(detailed):
            timeseries = cpap_session.timeseries
            if timeseries is None:
                continue
            file_id = f"{local_date}:{cpap_session.file_type}:{index}"
            # Waveform timestamps from the parser are absolute UTC epoch seconds
            # (fix #3), so the session start_time is a trustworthy segment origin
            # and we never reconstruct it from a sample index.
            for channel_key, unit in _HIGH_RATE_CHANNELS:
                samples = getattr(timeseries, channel_key, [])
                if samples:
                    signals.append(self._signal(channel_key, unit, cpap_session.sample_rate, file_id))
                    segments.append(
                        self._segment(channel_key, cpap_session.start_time, cpap_session.sample_rate, len(samples), unit, file_id)
                    )
            low_rate = self._low_rate_sample_rate(timeseries)
            for channel_key, unit in _LOW_RATE_CHANNELS:
                samples = getattr(timeseries, channel_key, [])
                if samples:
                    signals.append(self._signal(channel_key, unit, low_rate, file_id))
                    segments.append(
                        self._segment(channel_key, cpap_session.start_time, low_rate, len(samples), unit, file_id)
                    )
        return signals, segments

    @staticmethod
    def _low_rate_sample_rate(timeseries) -> float:
        """Derive PLD sample rate from its own timestamp spacing, if present."""
        low = timeseries.timestamps_low
        if len(low) >= 2 and low[1] != low[0]:
            return 1.0 / (low[1] - low[0])
        return 0.0

    @staticmethod
    def _signal(channel_key: str, unit: str, sample_rate: float, file_id: str) -> SignalChannel:
        leak_kind = "unintentional" if channel_key == "leak" else None
        return SignalChannel(
            channel_key=channel_key,
            source_label=channel_key,
            unit=unit,
            sample_rate_hz=sample_rate or None,
            value_kind="sample",
            leak_kind=leak_kind,
            source_file_ids=(file_id,),
        )

    @staticmethod
    def _segment(
        channel_key: str,
        start_time: datetime,
        sample_rate: float,
        sample_count: int,
        unit: str,
        file_id: str,
    ) -> WaveformSegment:
        return WaveformSegment(
            channel_key=channel_key,
            start_time=start_time,
            sample_rate_hz=sample_rate,
            sample_count=sample_count,
            unit=unit,
            source_file_id=file_id,
        )

    @staticmethod
    def _night_date(dt: datetime):
        """Return the night date for a session start (mirrors the parser).

        Sessions starting before noon belong to the previous calendar night, so
        fragmented overnight sessions land on the preceding day's summary.
        """
        day = dt.date() if hasattr(dt, "date") else dt
        if isinstance(day, date) and dt.hour < 12:
            return day - timedelta(days=1)
        return day
