"""Regression conformance tests for the five ResMed bug fixes.

These pin the behavior of the pinned ``cpap-parser`` fork
(``fix/resmed-four-bugs``, awaiting upstream MR !12) for the ResMed AirSense 10
path. Each test maps to one fix described in
``docs/sleeplab_2_loader_and_conformance_plan.md`` and consumed by
``importer/loaders/resmed_native.py``:

1. ``test_waveform_timestamps_not_epoch`` — waveform sample timestamps are
   absolute (session-based), not Unix-epoch relative.
2. ``test_usage_duration_fields_present`` — usage is split into three distinct
   semantics: ``summary_reported_usage`` / ``computed_usage`` / ``recording_span``.
3. ``test_serial_number_none_on_absence`` — a missing serial is ``None``, never
   the literal string ``"Unknown"``.
4. ``test_ghost_sessions_flagged_not_deleted`` — STR-only days are kept and
   flagged ``has_detailed_data is False`` rather than dropped.
5. ``test_brp_pld_no_double_counting`` — BRP and PLD therapy time is counted
   once, not summed.

No real SD-card data is required. The tests drive the fixed code paths directly
with small synthetic inputs (fake EDF signal objects and hand-built schema
models), so they run anywhere the dependency is installed.
"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

# Skip the whole module (with a visible reason) if the pinned dependency is not
# installed. The directory conftest also ignores collection in that case.
pytest.importorskip("cpap_parser", reason="cpap-parser not installed; see requirements.txt pin")

from cpap_parser.adapters.resmed import ResMedAdapter  # noqa: E402
from cpap_parser.schema import CPAPSession, CPAPSessionSummary  # noqa: E402

# Plausible-range bounds for therapy timestamps, as Unix epoch seconds.
_YEAR_2000 = datetime(2000, 1, 1).timestamp()
_YEAR_2100 = datetime(2100, 1, 1).timestamp()


def _fake_edf(label: str, data: list[float], gain: float = 1.0, offset: float = 0.0):
    """Build a minimal stand-in for a parsed EDF with one signal channel.

    ``ResMedAdapter._decode_signal`` only touches ``signals[*].label/.data/
    .gain/.offset``, so a ``SimpleNamespace`` is enough to exercise the signal
    decoding and timestamp-generation paths without ``cpap-py`` or real files.
    """
    signal = SimpleNamespace(label=label, data=data, gain=gain, offset=offset)
    return SimpleNamespace(signals=[signal])


def _str_record(day: date, mask_duration_minutes: float):
    """Build a fake STR.edf record accepted by ``_map_summaries``."""
    return SimpleNamespace(
        date=day,
        mode=1,  # CPAP
        mask_duration=mask_duration_minutes,
        ahi=1.0,
        ai=0.5,
        hi=0.5,
        cai=0.0,
        oai=0.0,
        leak_50=0.1,
        leak_95=0.2,
        mp_50=10.0,
        mp_95=11.0,
    )


def test_waveform_timestamps_not_epoch():
    """Fix #1: sample timestamps are absolute, not relative to Unix epoch.

    The bug produced timestamps of ``i / sample_rate`` (so the first sample sat
    at 1970-01-01). The fix adds ``session_start.timestamp()``, anchoring every
    sample to the real session time.
    """
    adapter = ResMedAdapter()
    session_start = datetime(2026, 6, 1, 22, 0, 0)

    high_rate = adapter._parse_brp_signals(
        _fake_edf("Flow", [float(i) for i in range(100)]), 25.0, session_start
    )
    assert high_rate.timestamps, "expected decoded high-rate timestamps"
    assert min(high_rate.timestamps) >= _YEAR_2000, "first sample is near the Unix epoch"
    assert max(high_rate.timestamps) <= _YEAR_2100
    # The first sample should sit at (not far from) the real session start.
    assert min(high_rate.timestamps) >= session_start.timestamp() - 1

    low_rate = adapter._parse_pld_signals(
        _fake_edf("Leak", [0.1] * 50), 0.5, session_start
    )
    assert low_rate.timestamps_low, "expected decoded low-rate timestamps"
    assert min(low_rate.timestamps_low) >= _YEAR_2000
    assert max(low_rate.timestamps_low) <= _YEAR_2100


def test_usage_duration_fields_present():
    """Fix #2: usage is three distinct numeric fields for a day with data."""
    adapter = ResMedAdapter()
    summaries = adapter._map_summaries([_str_record(date(2026, 6, 1), 480.0)])
    session = CPAPSession(
        start_time=datetime(2026, 6, 1, 22, 0),
        end_time=datetime(2026, 6, 2, 6, 0),
        duration_minutes=480.0,
        file_type="BRP",
    )
    adapter._annotate_summaries(summaries, [session])
    summary = summaries[0]

    for field_name in ("summary_reported_usage", "computed_usage", "recording_span"):
        value = getattr(summary, field_name)
        assert value is not None, f"{field_name} should be populated for a day with data"
        assert isinstance(value, (int, float)), f"{field_name} should be numeric"

    # All three describe ~8h here but via different methods, and are kept apart.
    assert summary.summary_reported_usage == pytest.approx(8.0)
    assert summary.computed_usage == pytest.approx(8.0)
    assert summary.recording_span == pytest.approx(8.0)


def test_serial_number_none_on_absence(tmp_path):
    """Fix #3: an absent serial is ``None``, never the literal ``"Unknown"``."""
    # Identity record present but carrying no serial number.
    (tmp_path / "Identification.json").write_text("{}", encoding="utf-8")

    machine = ResMedAdapter()._load_machine_info(tmp_path)

    assert machine.serial_number is None
    assert machine.serial_number != "Unknown"


def test_serial_number_parsed_when_present(tmp_path):
    """Guard against a false-positive ``None``: a real serial is extracted.

    Without this, ``test_serial_number_none_on_absence`` could pass simply
    because the fallback parser never returns anything.
    """
    (tmp_path / "Identification.tgt").write_text("#SRN ABC123XYZ\n", encoding="utf-8")

    machine = ResMedAdapter()._load_machine_info(tmp_path)

    assert machine.serial_number == "ABC123XYZ"


def test_ghost_sessions_flagged_not_deleted():
    """Fix #4: STR-only (ghost) days survive and are flagged, not deleted."""
    adapter = ResMedAdapter()
    summaries = [
        CPAPSessionSummary(date=date(2026, 6, 1)),  # has detailed EDF data
        CPAPSessionSummary(date=date(2026, 6, 2)),  # STR history only (ghost)
    ]
    detailed_session = CPAPSession(
        start_time=datetime(2026, 6, 1, 22, 0),
        end_time=datetime(2026, 6, 2, 6, 0),
        duration_minutes=480.0,
        file_type="BRP",
    )

    adapter._annotate_summaries(summaries, [detailed_session])

    # Both days remain present — historical summaries are never dropped.
    assert len(summaries) == 2
    assert {s.date for s in summaries} == {date(2026, 6, 1), date(2026, 6, 2)}

    detailed = next(s for s in summaries if s.date == date(2026, 6, 1))
    ghost = next(s for s in summaries if s.date == date(2026, 6, 2))
    assert detailed.has_detailed_data is True
    assert ghost.has_detailed_data is False


def test_brp_pld_no_double_counting():
    """Fix #5: BRP and PLD therapy time is counted once, not summed.

    In this fork the BRP/PLD double-count manifests as *duration*, not event
    counts: when BRP and PLD files have slightly different timestamp prefixes
    they become two ``CPAPSession`` objects covering the same therapy period.
    ``_annotate_summaries`` de-duplicates by counting BRP-only (falling back to
    PLD), so ``computed_usage`` is true mask-on time. (Respiratory *events* come
    only from EVE files, so there is no separate PLD event tally to inflate.)
    """
    adapter = ResMedAdapter()
    summary = CPAPSessionSummary(date=date(2026, 6, 1))
    start = datetime(2026, 6, 1, 22, 0)
    end = datetime(2026, 6, 2, 6, 0)  # 8 hours
    brp = CPAPSession(start_time=start, end_time=end, duration_minutes=480.0, file_type="BRP")
    pld = CPAPSession(start_time=start, end_time=end, duration_minutes=480.0, file_type="PLD")

    adapter._annotate_summaries([summary], [brp, pld])

    # 8h counted once — not 16h (BRP + PLD summed).
    assert summary.computed_usage == pytest.approx(8.0)
    assert summary.computed_usage != pytest.approx(16.0)
