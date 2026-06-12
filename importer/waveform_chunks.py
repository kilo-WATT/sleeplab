"""Encoding and read helpers for fixed-rate waveform chunks."""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

ENCODING = "float32-le-zlib-v1"
DEFAULT_CHUNK_SECONDS = 300


@dataclass(frozen=True)
class EncodedWaveformChunk:
    """One independently compressed fixed-rate waveform interval."""

    signal_name: str
    unit: str
    sample_rate_hz: float
    start_time: datetime
    end_time: datetime
    chunk_index: int
    sample_count: int
    payload: bytes
    uncompressed_bytes: int


@dataclass(frozen=True)
class WaveformPoint:
    """One decoded waveform sample."""

    timestamp: datetime
    value: float | None


def encode_samples(samples: Sequence[float | None]) -> tuple[bytes, int]:
    """Encode nullable samples as little-endian float32 and compress with zlib."""
    values = [math.nan if value is None else float(value) for value in samples]
    raw = struct.pack(f"<{len(values)}f", *values)
    return zlib.compress(raw, level=6), len(raw)


def decode_samples(payload: bytes | memoryview, sample_count: int) -> list[float | None]:
    """Decompress and decode a float32 chunk, validating its declared size."""
    if sample_count < 0:
        raise ValueError("sample_count must be non-negative")
    try:
        raw = zlib.decompress(bytes(payload))
    except zlib.error as exc:
        raise ValueError("invalid compressed waveform payload") from exc
    expected = sample_count * 4
    if len(raw) != expected:
        raise ValueError(
            f"waveform payload size mismatch: expected {expected} bytes, got {len(raw)}"
        )
    values = struct.unpack(f"<{sample_count}f", raw) if sample_count else ()
    return [None if math.isnan(value) else float(value) for value in values]


def build_chunks(
    *,
    signal_name: str,
    unit: str,
    sample_rate_hz: float,
    start_time: datetime,
    samples: Sequence[float | None],
    first_chunk_index: int = 0,
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
) -> list[EncodedWaveformChunk]:
    """Split a fixed-rate signal into independently decodable time chunks."""
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    samples_per_chunk = max(1, int(round(sample_rate_hz * chunk_seconds)))
    chunks: list[EncodedWaveformChunk] = []
    for offset in range(0, len(samples), samples_per_chunk):
        chunk_samples = samples[offset : offset + samples_per_chunk]
        payload, raw_size = encode_samples(chunk_samples)
        chunk_start = start_time + timedelta(seconds=offset / sample_rate_hz)
        chunk_end = chunk_start + timedelta(
            seconds=(len(chunk_samples) - 1) / sample_rate_hz
        )
        chunks.append(
            EncodedWaveformChunk(
                signal_name=signal_name,
                unit=unit,
                sample_rate_hz=sample_rate_hz,
                start_time=chunk_start,
                end_time=chunk_end,
                chunk_index=first_chunk_index + len(chunks),
                sample_count=len(chunk_samples),
                payload=payload,
                uncompressed_bytes=raw_size,
            )
        )
    return chunks


def decode_window(
    rows: Iterable[Any],
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[WaveformPoint]:
    """Decode database chunk rows and retain samples inside an optional window."""
    points: list[WaveformPoint] = []
    for row in rows:
        rate = float(_field(row, "sample_rate_hz"))
        chunk_start = _field(row, "start_time")
        values = decode_samples(_field(row, "payload"), int(_field(row, "sample_count")))
        if points:
            expected_step = timedelta(seconds=1 / rate)
            if chunk_start - points[-1].timestamp > expected_step * 1.5:
                points.append(
                    WaveformPoint(
                        timestamp=points[-1].timestamp + expected_step,
                        value=None,
                    )
                )
        for index, value in enumerate(values):
            timestamp = chunk_start + timedelta(seconds=index / rate)
            if start_time is not None and timestamp < start_time:
                continue
            if end_time is not None and timestamp > end_time:
                break
            points.append(WaveformPoint(timestamp=timestamp, value=value))
    return points


def downsample_extrema(
    points: Sequence[WaveformPoint], max_points: int
) -> list[WaveformPoint]:
    """Reduce a long waveform while preserving each bucket's local extrema."""
    if max_points <= 0:
        raise ValueError("max_points must be positive")
    if len(points) <= max_points:
        return list(points)

    gap_points = [point for point in points if point.value is None]
    data_points = [point for point in points if point.value is not None]
    data_limit = max(1, max_points - len(gap_points))
    if len(data_points) <= data_limit:
        return sorted([*data_points, *gap_points], key=lambda point: point.timestamp)

    bucket_count = max(1, data_limit // 2)
    bucket_size = len(data_points) / bucket_count
    reduced: list[WaveformPoint] = []
    for bucket_index in range(bucket_count):
        first = int(bucket_index * bucket_size)
        last = min(len(data_points), int((bucket_index + 1) * bucket_size))
        bucket = data_points[first:last]
        low = min(bucket, key=lambda point: point.value)
        high = max(bucket, key=lambda point: point.value)
        reduced.extend(sorted((low, high), key=lambda point: point.timestamp))
    return sorted([*reduced[:data_limit], *gap_points], key=lambda point: point.timestamp)[
        :max_points
    ]


def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row[name]
    try:
        return row[name]
    except (KeyError, TypeError):
        return getattr(row, name)
