"""Tests for deterministic SleepLab 2.0 import planning."""

from pathlib import Path

import pytest

from importer.loaders import ImportPlanError, create_import_plan, prepare_execution


def _write(root: Path, relative_path: str, content: bytes = b"fixture") -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_resmed_plan_reports_coverage_and_execution(tmp_path: Path):
    _write(tmp_path, "STR.edf")
    _write(tmp_path, "Identification.tgt", b"#PNA AirSense_10_AutoSet\n#PCD 37160\n")
    _write(tmp_path, "DATALOG/20260601/20260601_220000_PLD.edf")
    _write(tmp_path, "DATALOG/20260601/20260601_220000_EVE.edf")
    _write(tmp_path, "DATALOG/20260601/20260601_220000_BRP.edf")

    plan = create_import_plan(tmp_path)

    assert plan.executable is True
    assert plan.devices[0].coverage.first_date == "2026-06-01"
    assert plan.devices[0].coverage.therapy_days == 1
    assert plan.devices[0].coverage.estimated_session_blocks == 1
    assert plan.devices[0].coverage.event_files == 1
    assert plan.devices[0].coverage.waveform_files == 1
    execution = prepare_execution(plan, tmp_path, plan.source_manifest.fingerprint)
    assert execution.backend_id == "sleeplab-native-resmed"
    assert execution.import_root == tmp_path / "DATALOG"


def test_source_fingerprint_changes_when_content_changes(tmp_path: Path):
    _write(tmp_path, "STR.edf", b"one")
    _write(tmp_path, "DATALOG/20260601/20260601_220000_PLD.edf")
    original = create_import_plan(tmp_path)

    _write(tmp_path, "STR.edf", b"two")
    changed = create_import_plan(tmp_path)

    assert changed.source_manifest.fingerprint != original.source_manifest.fingerprint
    with pytest.raises(ImportPlanError, match="changed after inspection"):
        prepare_execution(changed, tmp_path, original.source_manifest.fingerprint)


def test_prs1_plan_detects_but_blocks_execution(tmp_path: Path):
    _write(tmp_path, "P-Series/P012345/PROP.TXT", b"ModelNumber=560P\n")
    _write(tmp_path, "P-Series/P012345/p0/000000001.000")

    plan = create_import_plan(tmp_path)

    assert plan.inspection["devices"][0]["adapter_id"] == "philips-prs1-v2"
    assert plan.executable is False
    assert any("does not implement execution" in blocker for blocker in plan.blockers)
