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
        known_fields = set(type(coverage).__dataclass_fields__)
        for field, expected_value in expected_coverage.items():
            # Guard against a misspelled/unobservable coverage key: report it as
            # a clear failure instead of raising AttributeError and aborting the
            # whole run. Only file/directory-derived coverage is observable here
            # (parsed aggregates are not — see checklist §5 boundary).
            if field not in known_fields:
                failures.append(
                    f"coverage.{field}: unknown coverage field "
                    f"(observable: {sorted(known_fields)})"
                )
                continue
            _expect(failures, f"coverage.{field}", getattr(coverage, field), expected_value)

    # Optional, backward-compatible diagnostics expectations. A manifest may
    # assert that specific structured warning codes are surfaced by the plan
    # (e.g. ``resmed_missing_str``). Fixtures without an ``expected.diagnostics``
    # block are unaffected. Only detection/planning diagnostics are observable
    # here; import-time codes (``resmed_summary_only_day``/``resmed_waveform_absent``)
    # require the cpap-parser execution path and are checked elsewhere.
    expected_diagnostics = expected.get("diagnostics", {})
    expected_codes = expected_diagnostics.get("warning_codes", [])
    if expected_codes:
        present = _plan_diagnostic_codes(plan)
        for code in expected_codes:
            if code not in present:
                failures.append(
                    f"diagnostics.warning_codes: expected {code!r} present, got {sorted(present)}"
                )

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


def _plan_diagnostic_codes(plan: Any) -> set[str]:
    """Collect structured warning codes surfaced by an import plan.

    Gathers ``code`` from both the inspection-level warnings and each detected
    device's warnings. Entries without a ``code`` (or non-dict entries) are
    ignored so the collector tolerates older/looser diagnostic shapes.
    """

    codes: set[str] = set()
    inspection = plan.inspection
    for warning in inspection.get("warnings", []):
        if isinstance(warning, dict) and warning.get("code"):
            codes.add(warning["code"])
    for device in inspection.get("devices", []):
        for warning in device.get("warnings", []):
            if isinstance(warning, dict) and warning.get("code"):
                codes.add(warning["code"])
    return codes


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
