"""Manifest-driven conformance checks for synthetic and anonymized CPAP fixtures."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.loaders import create_import_plan


@dataclass(frozen=True)
class ConformanceResult:
    """Result of comparing one source fixture with its checked-in manifest."""

    fixture_id: str
    passed: bool
    failures: tuple[str, ...]


def validate_fixture(fixture_dir: str | Path) -> ConformanceResult:
    """Inspect a fixture source and compare stable normalized expectations."""

    root = Path(fixture_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    source_root = root / manifest.get("source_directory", "source")
    plan = create_import_plan(source_root)
    failures: list[str] = []
    expected = manifest["expected"]

    _expect(failures, "matched", plan.inspection["matched"], expected["matched"])
    _expect(failures, "ambiguous", plan.inspection["ambiguous"], expected.get("ambiguous", False))
    _expect(failures, "device_count", len(plan.inspection["devices"]), expected["device_count"])
    _expect(failures, "executable", plan.executable, expected["executable"])
    _expect(failures, "source_file_count", plan.source_manifest.file_count, expected["source_file_count"])

    if plan.inspection["devices"]:
        device = plan.inspection["devices"][0]
        _expect(failures, "adapter_id", device["adapter_id"], expected["adapter_id"])
        _expect(
            failures,
            "manufacturer",
            device["identity"]["manufacturer"] or device["manufacturer_hint"],
            expected["manufacturer"],
        )
        _expect(
            failures,
            "family",
            device["identity"]["family"] or device["family_hint"],
            expected["family"],
        )
        for capability, expected_state in expected.get("capabilities", {}).items():
            actual = device["capabilities"][capability]
            _expect(failures, f"capabilities.{capability}.available", actual["available"], expected_state["available"])
            _expect(
                failures,
                f"capabilities.{capability}.validation",
                actual["validation"],
                expected_state["validation"],
            )

    expected_coverage = expected.get("coverage", {})
    if plan.devices:
        coverage = plan.devices[0].coverage
        for field, expected_value in expected_coverage.items():
            _expect(failures, f"coverage.{field}", getattr(coverage, field), expected_value)

    return ConformanceResult(
        fixture_id=manifest["fixture_id"],
        passed=not failures,
        failures=tuple(failures),
    )


def validate_manifest_metadata(fixture_dir: str | Path) -> list[str]:
    """Validate privacy, provenance, and redistribution metadata."""

    root = Path(fixture_dir)
    manifest: dict[str, Any] = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    required = {
        "fixture_id",
        "schema_version",
        "fixture_kind",
        "redistribution",
        "anonymization",
        "source_hash",
        "reference",
        "expected",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        failures.append(f"missing manifest fields: {', '.join(missing)}")
    if manifest.get("fixture_kind") not in {"synthetic", "anonymized", "private_manifest_only"}:
        failures.append("fixture_kind must describe privacy/availability")
    if manifest.get("redistribution") not in {"repository", "restricted", "prohibited"}:
        failures.append("redistribution must be repository, restricted, or prohibited")
    if not manifest.get("anonymization", {}).get("reviewed"):
        failures.append("anonymization.reviewed must be true")
    return failures


def _expect(failures: list[str], field: str, actual: object, expected: object) -> None:
    if actual != expected:
        failures.append(f"{field}: expected {expected!r}, got {actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a SleepLab CPAP conformance fixture")
    parser.add_argument("fixture_dir")
    args = parser.parse_args()
    metadata_failures = validate_manifest_metadata(args.fixture_dir)
    result = validate_fixture(args.fixture_dir)
    failures = [*metadata_failures, *result.failures]
    print(json.dumps({"fixture_id": result.fixture_id, "passed": not failures, "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
