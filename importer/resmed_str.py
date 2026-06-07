"""Production ResMed STR.edf settings, summary, and mask-interval parsing."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from edf_parser import EDFHeader, parse_edf_channels

PARSER_ID = "sleeplab.resmed_str"
PARSER_VERSION = "1"

MODE_VALUES = {
    0: "cpap",
    1: "apap",
    2: "bilevel_t",
    3: "bilevel_s",
    4: "bilevel_st",
    5: "bilevel_st",
    6: "vauto",
    7: "asv",
    8: "asv_auto",
    9: "ivaps",
    10: "pac",
    11: "apap_for_her",
}
MASK_VALUES = {0: "pillows", 1: "full_face", 2: "nasal", 3: "unknown"}
RAMP_VALUES = {0: "off", 1: "fixed", 2: "auto"}
EPR_VALUES = {0: "off", 1: "ramp_only", 2: "full_time"}
CLIMATE_VALUES = {0: "auto", 1: "manual"}
TEMPERATURE_VALUES = {0: "off", 1: "on", 2: "auto"}


@dataclass(frozen=True)
class ResMedMaskInterval:
    index: int
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


@dataclass
class ResMedSTRDay:
    machine_local_date: date
    intervals: list[ResMedMaskInterval]
    normalized_settings: dict[str, Any]
    vendor_settings: dict[str, Any]
    source_names: dict[str, str]
    summary_usage_seconds: int | None
    on_duration_seconds: int | None
    patient_hours: int | None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def usage_seconds(self) -> int:
        return sum(interval.duration_seconds for interval in self.intervals)

    @property
    def wall_clock_seconds(self) -> int:
        if not self.intervals:
            return 0
        return int((self.intervals[-1].end - self.intervals[0].start).total_seconds())


def parse_resmed_str(path: str | Path, machine_tz: ZoneInfo) -> dict[date, ResMedSTRDay]:
    """Parse usable daily records from a ResMed STR.edf file."""
    header, scaled, raw = parse_edf_channels(str(path))
    labels = {signal.label: signal for signal in header.signals}
    required = {"MaskOn", "MaskOff", "MaskEvents"}
    missing = sorted(required - labels.keys())
    if missing:
        raise ValueError(f"STR.edf is missing required signals: {', '.join(missing)}")

    days: dict[date, ResMedSTRDay] = {}
    for record_index in range(header.num_records):
        record_date = _record_date(header, raw, record_index)
        intervals = _mask_intervals(header, raw, record_index, record_date, machine_tz)
        if not intervals:
            continue
        normalized, vendor, source_names, diagnostics = _settings_for_record(
            header, scaled, raw, record_index
        )
        summary_usage = _minutes_value(header, scaled, record_index, "Duration")
        on_duration = _minutes_value(header, scaled, record_index, "OnDuration")
        day = ResMedSTRDay(
            machine_local_date=record_date,
            intervals=intervals,
            normalized_settings=normalized,
            vendor_settings=vendor,
            source_names=source_names,
            summary_usage_seconds=summary_usage,
            on_duration_seconds=on_duration,
            patient_hours=_int_value(header, scaled, record_index, "PatientHours"),
            diagnostics=diagnostics,
        )
        if summary_usage is not None and abs(day.usage_seconds - summary_usage) > 60:
            diagnostics.append(
                {
                    "code": "resmed_duration_disagreement",
                    "severity": "warning",
                    "message": (
                        f"STR mask intervals total {day.usage_seconds}s but Duration reports "
                        f"{summary_usage}s."
                    ),
                    "affects": ["duration"],
                }
            )
        if on_duration is not None and abs(day.wall_clock_seconds - on_duration) > 60:
            diagnostics.append(
                {
                    "code": "resmed_wall_span_disagreement",
                    "severity": "warning",
                    "message": (
                        f"STR mask span is {day.wall_clock_seconds}s but OnDuration reports "
                        f"{on_duration}s."
                    ),
                    "affects": ["duration"],
                }
            )
        days[record_date] = day
    return days


def _record_date(header: EDFHeader, raw: dict[str, list[int]], record_index: int) -> date:
    values = raw.get("Date")
    if values and record_index < len(values) and values[record_index] >= 0:
        return date(1970, 1, 1) + timedelta(days=values[record_index])
    return header.start_datetime.date() + timedelta(days=record_index)


def _mask_intervals(
    header: EDFHeader,
    raw: dict[str, list[int]],
    record_index: int,
    record_date: date,
    machine_tz: ZoneInfo,
) -> list[ResMedMaskInterval]:
    mask_signal = next(signal for signal in header.signals if signal.label == "MaskOn")
    count = mask_signal.num_samples_per_record
    start_offset = record_index * count
    noon = datetime.combine(record_date, time(12), tzinfo=machine_tz)
    intervals: list[ResMedMaskInterval] = []
    for index, (on_minute, off_minute) in enumerate(
        zip(
            raw["MaskOn"][start_offset : start_offset + count],
            raw["MaskOff"][start_offset : start_offset + count],
            strict=True,
        )
    ):
        if on_minute < 0 and off_minute < 0:
            continue
        if on_minute < 0:
            on_minute = 0
        if off_minute < 0:
            off_minute = 24 * 60
        if not (0 <= on_minute <= off_minute <= 24 * 60) or on_minute == off_minute:
            continue
        intervals.append(
            ResMedMaskInterval(
                index=index,
                start=noon + timedelta(minutes=on_minute),
                end=noon + timedelta(minutes=off_minute),
            )
        )
    return intervals


def _settings_for_record(
    header: EDFHeader,
    scaled: dict[str, list[float]],
    raw: dict[str, list[int]],
    record_index: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    normalized: dict[str, Any] = {}
    vendor: dict[str, Any] = {}
    source_names: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []

    def scalar(label: str) -> float | None:
        value = _value(header, scaled, record_index, label)
        raw_value = _raw_value(header, raw, record_index, label)
        if value is None or raw_value == -1:
            return None
        vendor[label] = {"raw": raw_value, "value": value, "unit": _unit(header, label)}
        return value

    def set_number(key: str, *labels: str) -> None:
        for label in labels:
            value = scalar(label)
            if value is not None:
                normalized[key] = value
                source_names[key] = label
                return

    def set_enum(key: str, label: str, values: dict[int, str]) -> None:
        value = scalar(label)
        if value is None:
            return
        code = int(value)
        if code in values:
            normalized[key] = values[code]
            source_names[key] = label
        else:
            diagnostics.append(_unknown_setting(label, code, values))

    set_enum("therapy_mode", "Mode", MODE_VALUES)
    mode = normalized.get("therapy_mode")
    if mode == "cpap":
        set_number("fixed_pressure_cmh2o", "S.C.Press")
        set_number("ramp_start_pressure_cmh2o", "S.C.StartPress")
    elif mode in {"apap", "apap_for_her"}:
        set_number("minimum_pressure_cmh2o", "S.AS.MinPress", "S.A.MinPress", "S.AFH.MinPress")
        set_number("maximum_pressure_cmh2o", "S.AS.MaxPress", "S.A.MaxPress", "S.AFH.MaxPress")
        set_number("ramp_start_pressure_cmh2o", "S.AS.StartPress", "S.A.StartPress", "S.AFH.StartPress")
    set_number("epap_cmh2o", "S.BL.EPAP", "S.S.EPAP", "EPAP")
    set_number("ipap_cmh2o", "S.BL.IPAP", "S.S.IPAP", "IPAP")
    set_number("pressure_support_cmh2o", "S.VA.PS", "PS")
    set_number("minimum_epap_cmh2o", "S.VA.MinEPAP", "Min EPAP")
    set_number("maximum_epap_cmh2o", "S.VA.MaxEPAP", "Max EPAP")
    set_number("minimum_ipap_cmh2o", "Min IPAP")
    set_number("maximum_ipap_cmh2o", "S.VA.MaxIPAP", "Max IPAP")
    set_number("minimum_pressure_support_cmh2o", "Min PS")
    set_number("maximum_pressure_support_cmh2o", "Max PS")

    set_enum("ramp_mode", "S.RampEnable", RAMP_VALUES)
    set_number("ramp_time_minutes", "S.RampTime")
    set_number("epr_level_cmh2o", "S.EPR.Level", "EPR Level")
    epr_enabled = scalar("S.EPR.EPREnable")
    epr_clinical = scalar("S.EPR.ClinEnable")
    epr_type = scalar("S.EPR.EPRType")
    if epr_enabled is not None or epr_clinical is not None or epr_type is not None:
        if not epr_enabled or not epr_clinical:
            normalized["epr_mode"] = "off"
        elif epr_type is not None and int(epr_type) in EPR_VALUES:
            normalized["epr_mode"] = EPR_VALUES[int(epr_type)]
        else:
            diagnostics.append(_unknown_setting("S.EPR.EPRType", epr_type, EPR_VALUES))
        source_names["epr_mode"] = "S.EPR.EPREnable/S.EPR.ClinEnable/S.EPR.EPRType"

    set_enum("mask_type", "S.Mask", MASK_VALUES)
    set_enum("climate_control_mode", "S.ClimateControl", CLIMATE_VALUES)
    set_number("humidifier_level", "S.HumLevel")
    set_number("tube_temperature_c", "S.Temp")
    humidifier_enabled = scalar("S.HumEnable")
    if humidifier_enabled is not None:
        normalized["humidifier_enabled"] = bool(humidifier_enabled)
        source_names["humidifier_enabled"] = "S.HumEnable"
    set_enum("heated_tube_mode", "S.TempEnable", TEMPERATURE_VALUES)

    known_labels = set(source_names.values()) | {
        part for value in source_names.values() for part in value.split("/")
    }
    for signal in header.signals:
        if not signal.label.startswith("S.") or signal.label in known_labels:
            continue
        value = scalar(signal.label)
        if value is not None:
            diagnostics.append(
                {
                    "code": "resmed_unknown_setting_field",
                    "severity": "info",
                    "message": f"Preserved unnormalized STR setting {signal.label}.",
                    "source_value": str(value),
                    "affects": ["settings"],
                }
            )
    vendor["_parser"] = {"id": PARSER_ID, "version": PARSER_VERSION}
    return normalized, vendor, source_names, diagnostics


def _value(
    header: EDFHeader, channels: dict[str, list[float]], record_index: int, label: str
) -> float | None:
    signal = next((item for item in header.signals if item.label == label), None)
    if signal is None:
        return None
    index = record_index * signal.num_samples_per_record
    values = channels.get(label, [])
    return values[index] if index < len(values) else None


def _raw_value(
    header: EDFHeader, channels: dict[str, list[int]], record_index: int, label: str
) -> int | None:
    signal = next((item for item in header.signals if item.label == label), None)
    if signal is None:
        return None
    index = record_index * signal.num_samples_per_record
    values = channels.get(label, [])
    return values[index] if index < len(values) else None


def _unit(header: EDFHeader, label: str) -> str | None:
    signal = next((item for item in header.signals if item.label == label), None)
    return signal.dim.replace("Â", "") or None if signal else None


def _minutes_value(
    header: EDFHeader, channels: dict[str, list[float]], record_index: int, label: str
) -> int | None:
    value = _value(header, channels, record_index, label)
    return None if value is None or value < 0 else round(value * 60)


def _int_value(
    header: EDFHeader, channels: dict[str, list[float]], record_index: int, label: str
) -> int | None:
    value = _value(header, channels, record_index, label)
    return None if value is None or value < 0 else round(value)


def _unknown_setting(label: str, value: Any, known: dict[int, str]) -> dict[str, Any]:
    return {
        "code": "resmed_unknown_setting_value",
        "severity": "warning",
        "message": f"Unknown ResMed value {value!r} for {label}; it was preserved but not normalized.",
        "source_value": str(value),
        "expected_values": [str(code) for code in known],
        "affects": ["settings"],
    }
