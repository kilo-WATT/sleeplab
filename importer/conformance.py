"""Manifest-driven conformance checks for synthetic and anonymized CPAP fixtures.

Two entry points live here, deliberately kept separate (see
``docs/sleeplab_2_import_level_conformance_plan.md``):

* :func:`validate_fixture` — the **planning-only** harness. Runs
  ``create_import_plan`` and compares file-derived inspection results against a
  checked-in ``manifest.json``. Dependency-free: no ``cpap-parser``, no Postgres.
* :func:`validate_import` — the **import-level** harness (plan Step 1 scaffold).
  Drives the optional ``expected.import`` manifest block. This scaffold wires up
  the result type and the dependency/Postgres gating but does **not** parse CPAP
  payloads or write the database yet; every present sub-block is reported as a
  clearly-reasoned *skip*. Real checks land in later plan steps.
"""

import argparse
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.loaders import create_import_plan

#: ``expected.import`` sub-blocks whose (future) checks need a real parse of the
#: card via ``cpap-parser`` + its ``cpap-py`` EDF backend.
_PARSE_DEPENDENT_IMPORT_BLOCKS = (
    "settings",
    "session_blocks",
    "therapy_aggregates",
    "warnings",
    "oscar_reference",
)

#: ``expected.import`` sub-blocks whose (future) checks need persisted database
#: state (an open ``conn``), e.g. persisted-identity-hash stability.
_DB_DEPENDENT_IMPORT_BLOCKS = ("identity_hashes",)


@dataclass(frozen=True)
class ConformanceResult:
    """Result of comparing one source fixture with its checked-in manifest."""

    fixture_id: str
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class ImportConformanceResult:
    """Result of an import-level conformance run for one fixture.

    Distinct from :class:`ConformanceResult` (planning-only) by its ``skipped``
    tuple: a gated-out check (missing parser/``conn``, or a not-yet-built check)
    is recorded as a clearly-reasoned skip rather than masquerading as a pass or
    a failure. A run with no ``failures`` is ``passed`` even when every check was
    skipped.
    """

    fixture_id: str
    passed: bool
    failures: tuple[str, ...]
    skipped: tuple[str, ...]


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


def validate_import(fixture_dir: str | Path, *, conn: Any = None) -> ImportConformanceResult:
    """Import-level conformance entry point (plan Step 1 scaffold).

    Sibling of :func:`validate_fixture`, kept separate so the planning-only
    harness stays dependency-free. This scaffold reads the optional
    ``expected.import`` manifest block and applies the dependency/Postgres gating
    from ``docs/sleeplab_2_import_level_conformance_plan.md`` (§2, §6–§9). It does
    **not** parse CPAP payloads or touch the database yet: every present
    sub-block is reported as a clearly-reasoned *skip*, never a silent pass and
    never a crash. The real comparisons land in later plan steps.

    Behavior:

    * No ``expected.import`` block → ``passed=True``, ``failures=()``, a single
      ``skipped`` reason. Fully backward compatible.
    * ``expected.import`` present → each recognized sub-block is gated and
      skipped (parser/``conn`` unavailable, or "not implemented yet"); an
      unrecognized sub-block is surfaced as a skip too, so a typo is visible.

    Args:
        fixture_dir: Fixture root containing ``manifest.json`` (same shape as
            :func:`validate_fixture`).
        conn: Optional open ``psycopg2`` connection for the (future) DB-backed
            identity-hash checks. ``None`` (the default) skips all DB checks; no
            database is required to call this function.
    """

    root = Path(fixture_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    fixture_id = manifest["fixture_id"]
    import_expected = manifest.get("expected", {}).get("import")

    if not import_expected:
        return ImportConformanceResult(
            fixture_id=fixture_id,
            passed=True,
            failures=(),
            skipped=("expected.import absent — no import-level checks requested",),
        )

    failures: list[str] = []
    skipped: list[str] = []
    parser_available = _import_parser_available()

    for block in _PARSE_DEPENDENT_IMPORT_BLOCKS:
        if block not in import_expected:
            continue
        if not parser_available:
            skipped.append(
                f"expected.import.{block}: skipped — cpap-parser/cpap-py not installed"
            )
        else:
            skipped.append(
                f"expected.import.{block}: skipped — import-level check not implemented "
                "yet (plan Step 1 scaffold)"
            )

    for block in _DB_DEPENDENT_IMPORT_BLOCKS:
        if block not in import_expected:
            continue
        if conn is None:
            skipped.append(
                f"expected.import.{block}: skipped — no database connection"
            )
        else:
            skipped.append(
                f"expected.import.{block}: skipped — import-level check not implemented "
                "yet (plan Step 1 scaffold)"
            )

    # An unrecognized sub-block has no checker; surface it as a skip (not a
    # silent ignore) so a manifest typo or a future block is visible today.
    recognized = set(_PARSE_DEPENDENT_IMPORT_BLOCKS) | set(_DB_DEPENDENT_IMPORT_BLOCKS)
    for block in sorted(set(import_expected) - recognized):
        skipped.append(
            f"expected.import.{block}: skipped — unknown import-level block (no checker)"
        )

    return ImportConformanceResult(
        fixture_id=fixture_id,
        passed=not failures,
        failures=tuple(failures),
        skipped=tuple(skipped),
    )


def _import_parser_available() -> bool:
    """True only when both ``cpap-parser`` and its ``cpap-py`` backend are present.

    Uses :func:`importlib.util.find_spec` so dependency *presence* is detected
    without importing the packages — keeping :func:`validate_import` import-safe
    and free of parser side effects. Decoding an EDF needs the ``cpap-py``
    backend, so both must be importable for a parse-dependent check to run.
    """

    return (
        importlib.util.find_spec("cpap_parser") is not None
        and importlib.util.find_spec("cpap_py") is not None
    )


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
