"""Tests for manifest-driven importer conformance scaffolding."""

from pathlib import Path

from importer.conformance import validate_fixture, validate_manifest_metadata

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "conformance"


def test_synthetic_resmed_fixture_matches_manifest():
    fixture = FIXTURE_ROOT / "synthetic-resmed-minimal"

    assert validate_manifest_metadata(fixture) == []
    result = validate_fixture(fixture)

    assert result.passed, result.failures
