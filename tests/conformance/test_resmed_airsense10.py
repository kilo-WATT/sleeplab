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

import csv
import json
from datetime import date, datetime
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Fixture-backed conformance against the anonymized AirSense 10 SD card.
#
# Unlike the synthetic tests above, these drive the parser against the real
# (anonymized) fixture in ``fixtures/resmed_airsense10_001/`` and assert against
# OSCAR's own export of the same card (``oscar_reference/summary.csv``). See
# ``fixtures/README.md`` for provenance and the ground-truth values.
#
# Two tiers:
#   * Identity (serial) is parsed via the pure-Python ``.tgt`` fallback, so it
#     runs wherever ``cpap-parser`` is installed.
#   * The OSCAR numeric comparisons need ``ResMedAdapter.extract_and_map``, which
#     decodes EDF via the ``cpap-py`` backend; they ``importorskip("cpap_py")``
#     and skip with a visible reason when that backend is absent.
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "resmed_airsense10_001"


def _manifest() -> dict:
    return json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _oscar_summary_by_date() -> dict[date, dict[str, str]]:
    """Map each OSCAR ``summary.csv`` row to its calendar date."""
    rows: dict[date, dict[str, str]] = {}
    with (_FIXTURE_DIR / "oscar_reference" / "summary.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[date.fromisoformat(row["Date"])] = row
    return rows


def _detailed_night_dates() -> set[date]:
    """Night dates that ship detailed DATALOG therapy data (BRP/PLD present).

    ResMed names each ``DATALOG/<YYYYMMDD>`` directory by the night date, so the
    directory name is the night the summary is keyed on. We treat the on-disk
    DATALOG layout — not ``manifest.json`` — as authoritative for which nights
    have detailed data (see the "Known discrepancy" note in fixtures/README.md).
    """
    nights: set[date] = set()
    for day_dir in (_FIXTURE_DIR / "DATALOG").iterdir():
        if not day_dir.is_dir():
            continue
        has_therapy = any(
            "BRP" in f.name or "PLD" in f.name for f in day_dir.iterdir()
        )
        if has_therapy:
            nights.add(date(int(day_dir.name[0:4]), int(day_dir.name[4:6]), int(day_dir.name[6:8])))
    return nights


def _oscar_total_time_hours(value: str) -> float:
    """Parse an OSCAR ``HH:MM:SS`` total-time string into hours."""
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours + minutes / 60.0 + seconds / 3600.0


def _parsed_directory():
    """Parse the fixture card, skipping if the ``cpap-py`` EDF backend is absent."""
    pytest.importorskip(
        "cpap_py",
        reason="cpap-py EDF backend not installed; STR.edf/DATALOG cannot be decoded",
    )
    return ResMedAdapter().extract_and_map(_FIXTURE_DIR, include_timeseries=False)


def test_fixture_serial_parsed_from_identity_tgt():
    """Fix #3 on real data: the scrubbed serial is extracted, never ``"Unknown"``.

    Exercises the pure-Python ``.tgt`` identity fallback against the fixture's
    real (anonymized) ``Identification.tgt``, so it runs without ``cpap-py``.
    """
    expected_serial = _manifest()["expected_serial"]

    machine = ResMedAdapter()._load_machine_info(_FIXTURE_DIR)

    assert machine.serial_number == expected_serial
    assert machine.serial_number is not None
    assert machine.serial_number != "Unknown"


def test_fixture_ahi_matches_oscar_summary():
    """Per-night AHI from STR.edf matches OSCAR's export of the same card."""
    directory = _parsed_directory()
    oscar = _oscar_summary_by_date()

    parsed = {s.date: s for s in directory.daily_summaries}
    common = sorted(parsed.keys() & oscar.keys())
    # The parser and OSCAR both derive nightly summaries from STR.edf, so they
    # should agree on (nearly) all 40 calendar nights.
    assert len(common) >= len(oscar) - 1, (
        f"only {len(common)} of {len(oscar)} OSCAR nights matched parsed summaries"
    )

    mismatches = []
    for day in common:
        expected_ahi = float(oscar[day]["AHI"])
        actual_ahi = parsed[day].ahi
        if actual_ahi != pytest.approx(expected_ahi, abs=0.05):
            mismatches.append(f"{day}: parser AHI={actual_ahi!r} vs OSCAR={expected_ahi!r}")
    assert not mismatches, "AHI disagreements with OSCAR:\n" + "\n".join(mismatches)


def test_fixture_ghost_nights_flagged_not_deleted():
    """Fix #4 on real data: STR-only nights survive and are flagged.

    The card carries 40 nights of STR summary history but detailed DATALOG data
    for only a few; the summary-only nights must be kept and flagged
    ``has_detailed_data is False`` rather than dropped.
    """
    directory = _parsed_directory()
    oscar_dates = set(_oscar_summary_by_date())
    detailed_nights = _detailed_night_dates()

    parsed_dates = {s.date for s in directory.daily_summaries}
    # No STR night is dropped: every OSCAR night is still present.
    assert oscar_dates <= parsed_dates, (
        f"missing nights dropped by parser: {sorted(oscar_dates - parsed_dates)}"
    )

    by_date = {s.date: s for s in directory.daily_summaries}
    # Nights with on-disk DATALOG therapy data are flagged detailed.
    for night in detailed_nights:
        assert by_date[night].has_detailed_data is True, f"{night} should have detailed data"
    # And the STR-only majority are flagged ghost (kept, not deleted).
    ghosts = [d for d in oscar_dates - detailed_nights if by_date[d].has_detailed_data is False]
    assert ghosts, "expected STR-only ghost nights flagged has_detailed_data is False"


def test_fixture_computed_usage_matches_oscar_for_detailed_nights():
    """Fix #5 on real data: detailed-night therapy time matches OSCAR (counted once).

    For nights with DATALOG data, ``computed_usage`` (BRP/PLD de-duplicated) should
    track OSCAR's total mask time, not double it.
    """
    directory = _parsed_directory()
    oscar = _oscar_summary_by_date()
    detailed_nights = _detailed_night_dates()
    by_date = {s.date: s for s in directory.daily_summaries}

    mismatches = []
    for night in sorted(detailed_nights):
        expected_hours = _oscar_total_time_hours(oscar[night]["Total Time"])
        actual_hours = by_date[night].computed_usage
        # Tolerate a few minutes of session-boundary rounding, but not a 2x
        # BRP+PLD double-count.
        if actual_hours != pytest.approx(expected_hours, abs=0.1):
            mismatches.append(
                f"{night}: computed_usage={actual_hours!r}h vs OSCAR total={expected_hours:.4f}h"
            )
    assert not mismatches, "usage disagreements with OSCAR:\n" + "\n".join(mismatches)
