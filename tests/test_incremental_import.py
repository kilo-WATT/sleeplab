"""Unit tests for incremental-import night selection and source staging.

These are pure filesystem/logic tests — no database or cpap-parser runtime — so
they run everywhere, unlike the DB-backed persist/execution paths.
"""

from datetime import date
from pathlib import Path

from importer.loaders.incremental import (
    select_skipped_day_folders,
    stage_incremental_source,
)


def _fake_card(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "STR.edf").write_bytes(b"summary")
    (root / "Identification.tgt").write_bytes(b"id")
    datalog = root / "DATALOG"
    for day_name in ("20260506", "20260517", "20260528"):
        day = datalog / day_name
        day.mkdir(parents=True)
        (day / f"{day_name}_223519_BRP.edf").write_bytes(b"waveform")
    return root


def test_select_skipped_day_folders_matches_detailed_dates():
    assert select_skipped_day_folders(
        {"20260506", "20260517", "20260528"},
        {date(2026, 5, 6), date(2026, 5, 17)},
    ) == {"20260506", "20260517"}


def test_stage_omits_skipped_day_folders_but_keeps_whole_card(tmp_path):
    card = _fake_card(tmp_path / "card")
    staged, cleanup = stage_incremental_source(card, {date(2026, 5, 6), date(2026, 5, 17)})
    try:
        assert staged != card
        # Whole-card files are always preserved so detection + summaries work.
        assert (staged / "STR.edf").exists()
        assert (staged / "Identification.tgt").exists()
        # Only the un-skipped night's DATALOG folder is staged.
        assert sorted(p.name for p in (staged / "DATALOG").iterdir()) == ["20260528"]
        assert (staged / "DATALOG" / "20260528" / "20260528_223519_BRP.edf").exists()
    finally:
        cleanup()
    assert not staged.exists()  # cleanup removes the temp dir


def test_stage_is_noop_when_nothing_to_skip(tmp_path):
    card = _fake_card(tmp_path / "card")
    staged, cleanup = stage_incremental_source(card, set())
    cleanup()
    assert staged == card  # original returned, no temp copy made


def test_stage_is_noop_without_a_datalog_layout(tmp_path):
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "STR.edf").write_bytes(b"summary")
    staged, cleanup = stage_incremental_source(flat, {date(2026, 5, 6)})
    cleanup()
    assert staged == flat
