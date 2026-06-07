import struct
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importer"))

from resmed_str import parse_resmed_str


def _field(value: object, width: int) -> bytes:
    return str(value).encode("latin-1").ljust(width)


def _write_str(path: Path, records: list[dict[str, list[int] | int]]) -> None:
    signals = [
        ("Date", "", 0, 24836, 0, 24836, 1),
        ("MaskOn", "min.", 0, 1440, 0, 1440, 4),
        ("MaskOff", "min.", 0, 1440, 0, 1440, 4),
        ("MaskEvents", "", 0, 255, 0, 255, 1),
        ("Duration", "min.", 0, 1440, 0, 1440, 1),
        ("OnDuration", "min.", 0, 1440, 0, 1440, 1),
        ("PatientHours", "hrs", 0, 65534, 0, 65534, 1),
        ("Mode", "", 0, 16, 0, 16, 1),
        ("S.RampEnable", "", 0, 16, 0, 16, 1),
        ("S.RampTime", "min.", 5, 45, 5, 45, 1),
        ("S.AS.StartPress", "cmH2O", 4, 20, 200, 1000, 1),
        ("S.AS.MaxPress", "cmH2O", 4, 20, 200, 1000, 1),
        ("S.AS.MinPress", "cmH2O", 4, 20, 200, 1000, 1),
        ("S.EPR.ClinEnable", "", 0, 16, 0, 16, 1),
        ("S.EPR.EPREnable", "", 0, 16, 0, 16, 1),
        ("S.EPR.Level", "cmH2O", 1, 3, 50, 150, 1),
        ("S.EPR.EPRType", "", 0, 16, 0, 16, 1),
        ("S.Mask", "", 0, 16, 0, 16, 1),
        ("S.ClimateControl", "", 0, 16, 0, 16, 1),
        ("S.HumEnable", "", 0, 16, 0, 16, 1),
        ("S.HumLevel", "", 1, 8, 1, 8, 1),
        ("S.TempEnable", "", 0, 16, 0, 16, 1),
        ("S.Temp", "\N{DEGREE SIGN}C", 16, 30, 160, 300, 1),
        ("S.Unknown", "", 0, 16, 0, 16, 1),
        ("Crc16", "", -32768, 32767, -32768, 32767, 1),
    ]
    ns = len(signals)
    header_bytes = 256 + ns * 256
    main = b"".join(
        [
            _field("0", 8),
            _field("", 80),
            _field("Startdate 07-MAR-2026 X X X SRN=SYNTHETIC", 80),
            _field("07.03.26", 8),
            _field("12.00.00", 8),
            _field(header_bytes, 8),
            _field("", 44),
            _field(len(records), 8),
            _field(86400, 8),
            _field(ns, 4),
        ]
    )

    def signal_field(index: int, width: int) -> bytes:
        return b"".join(_field(signal[index], width) for signal in signals)

    signal_header = b"".join(
        [
            signal_field(0, 16),
            b"".join(_field("", 80) for _ in signals),
            signal_field(1, 8),
            signal_field(2, 8),
            signal_field(3, 8),
            signal_field(4, 8),
            signal_field(5, 8),
            b"".join(_field("", 80) for _ in signals),
            signal_field(6, 8),
            b"".join(_field("", 32) for _ in signals),
        ]
    )
    payload = bytearray()
    for record in records:
        for label, *_metadata, samples_per_record in signals:
            value = record.get(label, -1)
            values = value if isinstance(value, list) else [value] * samples_per_record
            payload.extend(struct.pack(f"<{samples_per_record}h", *values))
    path.write_bytes(main + signal_header + payload)


def test_parse_resmed_str_normalizes_settings_and_split_blocks(tmp_path: Path):
    raw_date = (date(2026, 3, 7) - date(1970, 1, 1)).days
    path = tmp_path / "STR.edf"
    _write_str(
        path,
        [
            {
                "Date": raw_date,
                "MaskOn": [690, 1080, -1, -1],
                "MaskOff": [900, 1140, -1, -1],
                "MaskEvents": 4,
                "Duration": 270,
                "OnDuration": 450,
                "PatientHours": 120,
                "Mode": 1,
                "S.RampEnable": 2,
                "S.RampTime": 20,
                "S.AS.StartPress": 200,
                "S.AS.MaxPress": 700,
                "S.AS.MinPress": 250,
                "S.EPR.ClinEnable": 1,
                "S.EPR.EPREnable": 1,
                "S.EPR.Level": 100,
                "S.EPR.EPRType": 2,
                "S.Mask": 0,
                "S.ClimateControl": 1,
                "S.HumEnable": 1,
                "S.HumLevel": 4,
                "S.TempEnable": 2,
                "S.Temp": 270,
                "S.Unknown": 9,
                "Crc16": 0,
            }
        ],
    )

    result = parse_resmed_str(path, ZoneInfo("America/New_York"))
    day = result[date(2026, 3, 7)]

    assert day.usage_seconds == 270 * 60
    assert day.wall_clock_seconds == 450 * 60
    assert day.summary_usage_seconds == 270 * 60
    assert len(day.intervals) == 2
    assert day.intervals[0].start.isoformat() == "2026-03-07T23:30:00-05:00"
    assert day.intervals[1].end.isoformat() == "2026-03-08T07:00:00-04:00"
    assert day.normalized_settings == {
        "therapy_mode": "apap",
        "minimum_pressure_cmh2o": 5.0,
        "maximum_pressure_cmh2o": 14.0,
        "ramp_start_pressure_cmh2o": 4.0,
        "ramp_mode": "auto",
        "ramp_time_minutes": 20.0,
        "epr_level_cmh2o": 2.0,
        "epr_mode": "full_time",
        "mask_type": "pillows",
        "climate_control_mode": "manual",
        "humidifier_level": 4.0,
        "tube_temperature_c": 27.0,
        "humidifier_enabled": True,
        "heated_tube_mode": "auto",
    }
    assert any(item["code"] == "resmed_unknown_setting_field" for item in day.diagnostics)


def test_parse_resmed_str_preserves_unknown_enum_without_guessing(tmp_path: Path):
    raw_date = (date(2026, 6, 1) - date(1970, 1, 1)).days
    path = tmp_path / "STR.edf"
    _write_str(
        path,
        [
            {
                "Date": raw_date,
                "MaskOn": [600, -1, -1, -1],
                "MaskOff": [660, -1, -1, -1],
                "MaskEvents": 2,
                "Duration": 60,
                "OnDuration": 60,
                "Mode": 15,
                "Crc16": 0,
            }
        ],
    )

    day = parse_resmed_str(path, ZoneInfo("UTC"))[date(2026, 6, 1)]

    assert "therapy_mode" not in day.normalized_settings
    assert day.vendor_settings["Mode"]["raw"] == 15
    assert any(item["code"] == "resmed_unknown_setting_value" for item in day.diagnostics)
