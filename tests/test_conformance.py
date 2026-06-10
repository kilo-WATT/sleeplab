"""Tests for manifest-driven importer conformance scaffolding."""

import json
import shutil
from pathlib import Path

from importer.conformance import validate_fixture, validate_manifest_metadata

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "conformance"


def test_synthetic_resmed_fixture_matches_manifest():
    fixture = FIXTURE_ROOT / "synthetic-resmed-minimal"

    assert validate_manifest_metadata(fixture) == []
    result = validate_fixture(fixture)

    assert result.passed, result.failures


def test_waveform_coverage_absence_is_detected(tmp_path):
    """Alpha 6 absence diagnostics: the harness must *catch* a wrong waveform count.

    The synthetic fixture ships zero BRP/SA2 waveform files (``waveform_files: 0``).
    Guard against a vacuous pass — if the manifest claims a waveform is present
    where none is persisted, ``validate_fixture`` must fail with an explicit
    ``coverage.waveform_files`` discrepancy rather than silently accepting it.
    This pins the conformance harness as a real absence diagnostic for the
    full-night/event-window waveform work in Alpha 6.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["expected"]["coverage"]["waveform_files"] == 0, (
        "fixture baseline changed: expected no waveform files"
    )
    # Claim a waveform that the source does not actually carry.
    manifest["expected"]["coverage"]["waveform_files"] = 1
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_fixture(fixture)

    assert not result.passed
    assert any("coverage.waveform_files" in failure for failure in result.failures), (
        f"expected a waveform-coverage failure, got {result.failures}"
    )


def test_manifest_without_diagnostics_field_still_passes():
    """Alpha 6 §5: ``expected.diagnostics`` is optional/backward-compatible.

    The committed synthetic manifest carries no ``diagnostics`` block, so it must
    keep passing unchanged after the manifest schema gains expected-diagnostics
    support. This pins backward compatibility for older fixtures.
    """
    fixture = FIXTURE_ROOT / "synthetic-resmed-minimal"

    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    assert "diagnostics" not in manifest["expected"], "fixture must stay diagnostics-free"
    assert validate_fixture(fixture).passed


def test_manifest_expected_diagnostics_fails_when_warning_code_absent(tmp_path):
    """Alpha 6 §5: a manifest asserting an absent warning code must fail.

    The synthetic fixture ships ``STR.edf``, so the plan surfaces no diagnostics;
    a manifest that requires a warning code which is not present must produce an
    explicit ``diagnostics.warning_codes`` failure rather than passing silently.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["diagnostics"] = {"warning_codes": ["resmed_missing_str"]}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_fixture(fixture)

    assert not result.passed
    assert any(
        failure.startswith("diagnostics.warning_codes") and "resmed_missing_str" in failure
        for failure in result.failures
    ), f"expected a diagnostics.warning_codes failure, got {result.failures}"


def test_manifest_expected_diagnostics_passes_when_warning_code_present(tmp_path):
    """Alpha 6 §5: a manifest asserting a present warning code passes that check.

    Removing ``STR.edf`` makes the planning layer surface the structured
    ``resmed_missing_str`` diagnostic. The ``expected.diagnostics`` sub-check must
    then pass. We isolate the feature by asserting no ``diagnostics.warning_codes``
    failure appears — removing STR also shifts other expected fields, which is
    irrelevant to whether the diagnostics assertion itself holds.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)
    (fixture / "source" / "STR.edf").unlink()

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["diagnostics"] = {"warning_codes": ["resmed_missing_str"]}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_fixture(fixture)

    assert not any(
        failure.startswith("diagnostics.warning_codes") for failure in result.failures
    ), f"diagnostics check should pass when the code is present, got {result.failures}"
