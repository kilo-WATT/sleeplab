from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class OximeterSample:
    timestamp: datetime
    spo2: int | None
    pulse: int | None


@dataclass(frozen=True)
class OximeterRecording:
    started_at: datetime
    ended_at: datetime
    sample_interval_seconds: float
    samples: list[OximeterSample]


@dataclass(frozen=True)
class _ViatomRecord:
    spo2: int
    pulse: int
    invalid: int
    motion: int
    vibration: int


class OximeterParseError(ValueError):
    pass


_TIMESTAMP_PATTERNS = (
    re.compile(r"(20[0-5]\d[01]\d[0-3]\d[0-2]\d[0-5]\d[0-5]\d)"),
    re.compile(r"(20[0-5]\d-[01]\d-[0-3]\d [0-2]\d:[0-5]\d:[0-5]\d)"),
)


def parse_viatom_binary(data: bytes, filename: str, timezone_name: str = "UTC") -> OximeterRecording:
    if len(data) < 2:
        raise OximeterParseError("File is too short for a Viatom/Wellue recording")

    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:
        raise OximeterParseError(f"Unknown timezone: {timezone_name}") from exc

    signature = int.from_bytes(data[:2], "little")
    if signature in {0x0003, 0x0005}:
        return _parse_legacy_recording(data, filename, tz, signature)
    if signature == 0x0301:
        return _parse_o2ring_s_recording(data, filename, tz)
    raise OximeterParseError(f"Unsupported Viatom/Wellue signature: 0x{signature:04x}")


def _parse_legacy_recording(data: bytes, filename: str, tz: ZoneInfo, signature: int) -> OximeterRecording:
    header_size = 40
    record_size = 5
    if len(data) < header_size:
        raise OximeterParseError("File is too short for a legacy Viatom/Wellue header")

    header = data[:header_size]
    header_started_at = _parse_header_timestamp(header, tz)
    filename_started_at = _parse_filename_timestamp(filename, tz)
    started_at = filename_started_at or header_started_at

    duration_seconds = int.from_bytes(header[13:15], "little")
    body = data[header_size:]
    if len(body) % record_size != 0:
        raise OximeterParseError("Legacy Viatom/Wellue data length is not aligned to 5-byte records")

    records = [
        _ViatomRecord(
            spo2=body[offset],
            pulse=body[offset + 1],
            invalid=body[offset + 2],
            motion=body[offset + 3],
            vibration=body[offset + 4],
        )
        for offset in range(0, len(body), record_size)
    ]
    if not records:
        raise OximeterParseError("Recording contains no oximeter samples")

    if duration_seconds <= 0:
        raise OximeterParseError("Recording duration is invalid")

    sample_interval = duration_seconds / len(records)
    if sample_interval == 2 and signature in {0x0003, 0x0005}:
        deduplicated = _deduplicate_pairs(records)
        if deduplicated is not None:
            records = deduplicated
            sample_interval = duration_seconds / len(records)

    return _recording_from_records(started_at, sample_interval, records)


def _parse_o2ring_s_recording(data: bytes, filename: str, tz: ZoneInfo) -> OximeterRecording:
    if len(data) < 46:
        raise OximeterParseError("File is too short for an O2Ring S recording")

    started_at = _parse_filename_timestamp(filename, tz)
    if started_at is None:
        raise OximeterParseError("O2Ring S recordings require a timestamp in the filename")

    record_count = int.from_bytes(data[-36:-34], "little")
    if record_count <= 0:
        raise OximeterParseError("O2Ring S record count is invalid")

    sample_block = data[10 : 10 + record_count * 3]
    if len(sample_block) < record_count * 3:
        raise OximeterParseError("O2Ring S sample block is truncated")

    records = [
        _ViatomRecord(
            spo2=sample_block[offset],
            pulse=sample_block[offset + 1],
            invalid=0xFF if sample_block[offset] == 0xFF or sample_block[offset + 1] == 0xFF else 0,
            motion=sample_block[offset + 2],
            vibration=0,
        )
        for offset in range(0, len(sample_block), 3)
    ]
    return _recording_from_records(started_at, 1.0, records)


def _parse_header_timestamp(header: bytes, tz: ZoneInfo) -> datetime:
    year = int.from_bytes(header[2:4], "little")
    month = header[4]
    day = header[5]
    hour = header[6]
    minute = header[7]
    second = header[8]

    try:
        started_at = datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise OximeterParseError("Header timestamp is invalid") from exc

    if year < 2015 or year > 2059:
        raise OximeterParseError("Header timestamp year is outside the supported Viatom/Wellue range")
    return started_at.replace(tzinfo=tz)


def _parse_filename_timestamp(filename: str, tz: ZoneInfo) -> datetime | None:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "_" in base:
        base = base.rsplit("_", 1)[-1]
    for pattern in _TIMESTAMP_PATTERNS:
        match = pattern.search(base)
        if not match:
            continue
        raw = match.group(1)
        fmt = "%Y%m%d%H%M%S" if raw.isdigit() else "%Y-%m-%d %H:%M:%S"
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            return None
    return None


def _deduplicate_pairs(records: list[_ViatomRecord]) -> list[_ViatomRecord] | None:
    if len(records) % 2 != 0:
        return None
    deduplicated: list[_ViatomRecord] = []
    for index in range(0, len(records), 2):
        left = records[index]
        right = records[index + 1]
        if left != right:
            return None
        deduplicated.append(left)
    return deduplicated


def _recording_from_records(
    started_at: datetime,
    sample_interval_seconds: float,
    records: list[_ViatomRecord],
) -> OximeterRecording:
    samples = [
        OximeterSample(
            timestamp=started_at + timedelta(seconds=index * sample_interval_seconds),
            spo2=None if record.invalid == 0xFF or record.spo2 == 0xFF else record.spo2,
            pulse=None if record.invalid == 0xFF or record.pulse == 0xFF else record.pulse,
        )
        for index, record in enumerate(records)
    ]
    return OximeterRecording(
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=len(records) * sample_interval_seconds),
        sample_interval_seconds=sample_interval_seconds,
        samples=samples,
    )


def build_legacy_viatom_fixture(
    *,
    signature: int = 0x0005,
    started_at: datetime,
    duration_seconds: int,
    records: list[tuple[int, int, int, int, int]],
) -> bytes:
    header = bytearray(40)
    header[0:2] = signature.to_bytes(2, "little")
    header[2:4] = started_at.year.to_bytes(2, "little")
    header[4] = started_at.month
    header[5] = started_at.day
    header[6] = started_at.hour
    header[7] = started_at.minute
    header[8] = started_at.second
    filesize = 40 + len(records) * 5
    header[9:12] = filesize.to_bytes(3, "little")
    header[13:15] = duration_seconds.to_bytes(2, "little")
    body = bytearray()
    for spo2, pulse, invalid, motion, vibration in records:
        body.extend(struct.pack("BBBBB", spo2, pulse, invalid, motion, vibration))
    return bytes(header + body)


def build_o2ring_s_fixture(
    *,
    records: list[tuple[int, int, int]],
) -> bytes:
    body = bytearray()
    body.extend((0x01, 0x03))
    body.extend(bytes(8))
    for spo2, pulse, motion in records:
        body.extend(struct.pack("BBB", spo2, pulse, motion))
    trailer = bytearray(36)
    trailer[0:2] = len(records).to_bytes(2, "little")
    body.extend(trailer)
    return bytes(body)
