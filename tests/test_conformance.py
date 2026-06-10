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
