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
from datetime import date, datetime, timedelta
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


def _timestamp_shift_days() -> int:
    """Anonymizer day-offset applied to ``STR.edf`` EDF timestamps (manifest).

    ``scrub_sdcard.py`` shifted every *EDF timestamp* by this many days
    (``timestamp_shift_days: -508``) but left the OSCAR reference export
    (``oscar_reference/summary.csv``) and the ``DATALOG/<YYYYMMDD>`` directory
    names on the *original* calendar — see fixtures/README.md "anonymized data".
    So parsed summary dates (which come out of the shifted STR.edf) must be
    un-shifted back onto that original calendar before they can be joined
    against either reference. This corrects the date-join only; it does not
    relax any value assertion.
    """
    return int(_manifest()["anonymization"]["timestamp_shift_days"])


def _summaries_by_oscar_date(directory) -> dict[date, CPAPSessionSummary]:
    """Re-key parsed daily summaries onto the OSCAR/DATALOG (un-shifted) calendar.

    ``parsed_date = real_date + shift`` (shift is negative), so the original
    calendar date is ``parsed_date - shift``. Relative spacing is preserved by
    the anonymizer, so this is a pure axis translation — the per-night AHI,
    usage, and ghost-flag values are untouched.
    """
    shift = timedelta(days=_timestamp_shift_days())
    return {summary.date - shift: summary for summary in directory.daily_summaries}


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
    """Per-night AHI matches OSCAR for nights that ship detailed EVE data.

    The parser now computes AHI the way OSCAR does — the count of respiratory
    events (Clear Airway/Central + Obstructive + Unclassified apneas +
    Hypopneas, RERAs excluded) divided by mask-on hours — for every night that
    carries detailed DATALOG/EVE files. On those nights it reproduces OSCAR's
    event-derived AHI exactly.

    STR-only "ghost" nights are deliberately *not* asserted against OSCAR: this
    anonymized fixture ships DATALOG/EVE data for only 3 of 40 nights, so the
    remaining 37 have no event stream to count. For those the parser keeps
    STR.edf's device-reported AHI (quantized to 0.1 by the device), which
    cannot reproduce OSCAR's finer event-derived precision — not a parser bug
    but a limit of the summary-only source. We therefore scope the exact-match
    assertion to the detailed nights, where parser and OSCAR are directly
    comparable; the tolerance itself is unchanged (abs=0.05).
    """
    directory = _parsed_directory()
    oscar = _oscar_summary_by_date()

    parsed = _summaries_by_oscar_date(directory)
    common = sorted(parsed.keys() & oscar.keys())
    # The parser and OSCAR both derive nightly summaries from STR.edf, so they
    # should agree on (nearly) all 40 calendar nights being present.
    assert len(common) >= len(oscar) - 1, (
        f"only {len(common)} of {len(oscar)} OSCAR nights matched parsed summaries"
    )

    # Only nights with detailed EVE data carry an event stream the parser can
    # count; guard against a vacuous pass if the fixture's DATALOG ever changes.
    detailed = [day for day in common if parsed[day].has_detailed_data]
    assert len(detailed) >= 3, (
        f"expected >= 3 nights with detailed DATALOG/EVE data, got {len(detailed)}"
    )

    mismatches = []
    for day in detailed:
        expected_ahi = float(oscar[day]["AHI"])
        actual_ahi = parsed[day].ahi
        if actual_ahi != pytest.approx(expected_ahi, abs=0.05):
            mismatches.append(f"{day}: parser AHI={actual_ahi!r} vs OSCAR={expected_ahi!r}")
    assert not mismatches, (
        "AHI disagreements with OSCAR (detailed nights):\n" + "\n".join(mismatches)
    )


def test_fixture_ghost_nights_flagged_not_deleted():
    """Fix #4 on real data: STR-only nights survive and are flagged.

    The card carries 40 nights of STR summary history but detailed DATALOG data
    for only a few; the summary-only nights must be kept and flagged
    ``has_detailed_data is False`` rather than dropped.
    """
    directory = _parsed_directory()
    oscar_dates = set(_oscar_summary_by_date())
    detailed_nights = _detailed_night_dates()

    by_date = _summaries_by_oscar_date(directory)
    parsed_dates = set(by_date)
    # No STR night is dropped: every OSCAR night is still present.
    assert oscar_dates <= parsed_dates, (
        f"missing nights dropped by parser: {sorted(oscar_dates - parsed_dates)}"
    )

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
    by_date = _summaries_by_oscar_date(directory)

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


def test_fixture_normalized_import_run_acquired_via_loader():
    """Setup path: ``ResMedNativeLoader`` yields a normalized ``ImportRun`` from the card.

    This is the loader-level (vendor-neutral) twin of the raw-adapter tests above:
    it proves the exact path ``validate_import``'s ``_acquire_import_run`` takes —
    resolve the source via the manifest's ``source_directory`` (now ``"."``),
    structurally ``detect`` the ResMed device, then ``import_data_with_directory``
    to build an :class:`~importer.loaders.models.ImportRun`. It establishes only
    that the *setup* path works; it deliberately asserts **no** semantic
    ``expected.import`` values (those stay un-authored until verified — see the gap
    audit §8), and exposes no private values.

    Two-tier gating, like the OSCAR-numeric tests above: detection is parser-free,
    but building the run decodes EDF via the ``cpap-py`` backend, so this
    ``importorskip``s ``cpap_py`` and skips with a visible reason when it is absent.
    """
    from importer.loaders.models import ImportOptions
    from importer.loaders.resmed_native import ResMedNativeLoader

    # Resolve the source exactly as _acquire_import_run does, via the manifest.
    source_directory = _manifest().get("source_directory", "source")
    source_root = _FIXTURE_DIR / source_directory

    loader = ResMedNativeLoader()
    detected = loader.detect(source_root)
    assert detected, "structural detection must find the ResMed card at the resolved source root"

    # Decoding STR.edf/DATALOG needs the cpap-py backend; skip cleanly without it.
    pytest.importorskip(
        "cpap_py",
        reason="cpap-py EDF backend not installed; STR.edf/DATALOG cannot be decoded",
    )

    run, directory = loader.import_data_with_directory(detected[0], ImportOptions())

    # Prove the normalized run was produced — shape only, never private values.
    assert run is not None
    assert run.machine is not None, "ImportRun must carry a resolved machine identity"
    assert run.sessions, "ImportRun must contain at least one normalized session"
    assert run.adapter_id == loader.adapter_id
    # The raw directory is handed back alongside the run (single parse, two outputs).
    assert directory is not None


def test_fixture_semantic_expected_import_matches_normalized_run():
    """Phase 2: the committed AirSense 10 semantic ``expected.import`` blocks verify.

    First **committed, value-level, fixture-backed** import-level coverage on a real
    (anonymized) card. The manifest's ``warnings`` / ``session_blocks.block_count`` /
    ``therapy_aggregates`` / ``events`` (``count`` + per-type ``types``) / ``settings``
    blocks were authored *from* the actual normalized ``ImportRun`` (default
    ``ImportOptions``), and this drives ``validate_import`` against that same parsed
    run to prove they are not drifting fabrications: every authored value must match
    the loader's real output, or the test fails.

    Scope guardrails baked in:

    * Only non-timestamped aggregates are checked (counts, warning codes,
      usage/wall-clock/gap seconds, SleepLab-normalized event *type* counts — not
      OSCAR parity — and the device-reported ``settings.therapy_mode``, the only
      setting cpap-parser exposes). No exact block-interval or event timestamps, no
      ordered event lists/durations; no settings beyond ``therapy_mode``.
    * ``oscar_reference`` stays ``"skipped"`` because its numeric-parity sub-check is
      deferred — its hash half still verifies (no failure).
    * ``cpap-py``-gated: skips cleanly (never fabricates a pass) where the EDF
      backend is absent, so Windows/CI without the parser stay green by skipping.
    """
    from importer.conformance import summarize_import_blocks, validate_import
    from importer.loaders.models import ImportOptions
    from importer.loaders.resmed_native import ResMedNativeLoader

    pytest.importorskip(
        "cpap_py",
        reason="cpap-py EDF backend not installed; STR.edf/DATALOG cannot be decoded",
    )

    loader = ResMedNativeLoader()
    detected = loader.detect(_FIXTURE_DIR / _manifest().get("source_directory", "source"))
    assert detected, "structural detection must find the ResMed card"
    run, _directory = loader.import_data_with_directory(detected[0], ImportOptions())

    result = validate_import(_FIXTURE_DIR, run=run)

    # Every authored semantic value matched the normalized run.
    assert result.passed, result.failures
    assert result.failures == ()

    statuses = summarize_import_blocks(_FIXTURE_DIR, result)
    # The five semantic blocks are checked-and-passed (not gated/absent).
    for block in ("warnings", "session_blocks", "therapy_aggregates", "events", "settings"):
        assert statuses.get(block) == "passed", (block, statuses, result.skipped)
    # oscar_reference hash verified but parity is deferred → block reads "skipped".
    assert statuses.get("oscar_reference") == "skipped", statuses
    assert not any("oscar_reference" in f for f in result.failures), result.failures


def test_fixture_settings_snapshot_maps_only_therapy_mode():
    """Parser-backed: every session carries one therapy-mode-only ``SettingsSnapshot``.

    cpap-parser exposes a single normalized therapy *setting* — the daily summary's
    ``pressure_mode`` (an ``"APAP"`` AutoSet device here) — so the loader maps it to
    ``SettingsSnapshot.therapy_mode`` and nothing else. This pins, against the real
    card, that:

    * each session has exactly one snapshot (mode is present for every STR night);
    * the only mapped key is ``therapy_mode`` — no fabricated min/max/set pressure,
      EPR, ramp, humidifier, or mask_type (none exist in the parser schema);
    * no placeholder leaks in — no ``"Unknown"``/``""``/fake-zero/fake-false;
    * provenance + a conservative confidence are recorded.

    ``cpap-py``-gated: skips cleanly where the EDF backend is absent.
    """
    from importer.loaders.models import Confidence, ImportOptions
    from importer.loaders.resmed_native import ResMedNativeLoader

    pytest.importorskip(
        "cpap_py",
        reason="cpap-py EDF backend not installed; STR.edf/DATALOG cannot be decoded",
    )

    loader = ResMedNativeLoader()
    detected = loader.detect(_FIXTURE_DIR / _manifest().get("source_directory", "source"))
    assert detected, "structural detection must find the ResMed card"
    run, _directory = loader.import_data_with_directory(detected[0], ImportOptions())

    sessions_with_settings = [s for s in run.sessions if s.settings]
    assert sessions_with_settings, "loader should now populate Session.settings"

    mapped_keys: set[str] = set()
    for session in sessions_with_settings:
        assert len(session.settings) == 1, session.machine_local_date
        snapshot = session.settings[0]
        # Only therapy_mode, and only a real value — never a fabricated placeholder.
        assert set(snapshot.settings) == {"therapy_mode"}, snapshot.settings
        assert snapshot.settings["therapy_mode"] not in ("", "Unknown", None)
        assert snapshot.source_names.get("therapy_mode") == "pressure_mode"
        assert snapshot.source_file_ids == ("STR.edf",)
        assert snapshot.confidence == Confidence.PROBABLE
        assert snapshot.effective_at == session.start_time
        mapped_keys |= set(snapshot.settings)

    # Across the whole card, therapy_mode is the *only* setting ever emitted.
    assert mapped_keys == {"therapy_mode"}, mapped_keys


def test_fixture_event_type_counts_match_normalized_run():
    """Parser-backed: the manifest's ``events.types`` equal the real per-type tallies.

    Extends the committed ``events`` coverage from total ``count`` to per-type
    counts. These are **SleepLab normalized** ``event_type`` counts as emitted by
    ``ResMedNativeLoader`` — the raw cpap-parser labels (``"Central Apnea"`` /
    ``"Obstructive Apnea"`` / ``"Hypopnea"``) plus the loader-derived ``"Large
    Leak"`` — **not** OSCAR event-type parity (the raw→OSCAR enum mapping is still
    deferred; see gap audit §12). The test proves the authored ``types`` are the
    real run's tallies (not fabricated) and that ``validate_import`` actually
    checks them: it compares the manifest's ``types`` to ``Counter(event_type)``
    from the parsed run for each detailed night, then drives ``validate_import``.

    ``cpap-py``-gated: skips cleanly where the EDF backend is absent.
    """
    from collections import Counter

    from importer.conformance import summarize_import_blocks, validate_import
    from importer.loaders.models import ImportOptions
    from importer.loaders.resmed_native import ResMedNativeLoader

    pytest.importorskip(
        "cpap_py",
        reason="cpap-py EDF backend not installed; STR.edf/DATALOG cannot be decoded",
    )

    loader = ResMedNativeLoader()
    detected = loader.detect(_FIXTURE_DIR / _manifest().get("source_directory", "source"))
    assert detected, "structural detection must find the ResMed card"
    run, _directory = loader.import_data_with_directory(detected[0], ImportOptions())

    by_date: dict[str, list] = {}
    for session in run.sessions:
        by_date.setdefault(session.machine_local_date, []).append(session)

    events_block = _manifest()["expected"]["import"]["events"]
    assert events_block, "manifest must carry an events block"

    for date_key, expected in events_block.items():
        assert "types" in expected, f"{date_key} must pin event types"
        actual = Counter(
            ev.event_type for s in by_date.get(date_key, []) for ev in s.events
        )
        # Authored types are the real, complete per-type tally for that night.
        assert dict(actual) == expected["types"], (date_key, dict(actual), expected["types"])
        # Type counts reconcile with the already-pinned total count.
        assert sum(expected["types"].values()) == expected["count"], date_key

    result = validate_import(_FIXTURE_DIR, run=run)
    assert result.passed, result.failures
    assert summarize_import_blocks(_FIXTURE_DIR, result).get("events") == "passed"
