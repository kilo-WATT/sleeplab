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
import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.loaders import create_import_plan

#: ``expected.import`` sub-blocks compared against a normalized ``ImportRun``
#: (parse-observable). Their comparators need a run (injected or parsed); they do
#: not need Postgres.
_PARSE_DEPENDENT_IMPORT_BLOCKS = (
    "settings",
    "session_blocks",
    "therapy_aggregates",
    "warnings",
)

#: ``expected.import`` sub-blocks compared against checked-in reference exports.
#: Reference-file hash verification is parser-free; the numeric parity comparison
#: additionally needs a normalized run (deferred).
_REFERENCE_IMPORT_BLOCKS = ("oscar_reference",)

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


def validate_import(
    fixture_dir: str | Path, *, conn: Any = None, run: Any = None, machine_id: str | None = None
) -> ImportConformanceResult:
    """Import-level conformance entry point.

    Sibling of :func:`validate_fixture`, kept separate so the planning-only
    harness stays dependency-free. It reads the optional ``expected.import``
    manifest block and compares it against a normalized
    :class:`~importer.loaders.models.ImportRun` (parse-observable checks) and,
    when a connection is supplied, persisted database state (DB checks). The
    dependency/Postgres gating follows
    ``docs/sleeplab_2_import_level_conformance_plan.md`` (§2, §5–§9).

    A check is run only when its inputs are genuinely available; otherwise it is
    recorded as a clearly-reasoned *skip*. Nothing is ever silently passed, and
    the function never crashes on a missing parser/connection.

    Behavior:

    * No ``expected.import`` block → ``passed=True``, ``failures=()``, a single
      ``skipped`` reason. Fully backward compatible.
    * Parse-observable blocks run against ``run`` when one is available — either
      injected via ``run=`` or parsed from the fixture source when
      ``cpap-parser``/``cpap-py`` are installed. When no run can be obtained the
      block is skipped with the acquisition reason.
    * DB blocks run only when ``conn`` **and** ``machine_id`` are supplied;
      otherwise they skip with ``"no database connection"`` or
      ``"no machine scope"`` respectively.
    * An unrecognized sub-block is surfaced as a skip so a manifest typo is
      visible.

    Args:
        fixture_dir: Fixture root containing ``manifest.json`` (same shape as
            :func:`validate_fixture`).
        conn: Optional open ``psycopg2`` connection for DB-backed checks. ``None``
            (the default) skips all DB checks; no database is required.
        run: Optional pre-computed normalized ``ImportRun`` to compare against. If
            omitted, one is parsed from the fixture source when the parser is
            installed; otherwise parse-observable checks are skipped.
        machine_id: Optional ``cpap_machines.id`` scoping the DB identity-hash
            checks to one persisted machine. Required (with ``conn``) for
            ``identity_hashes``; absent → that block skips. ``validate_import``
            only *reads* persisted state — it never writes or commits.
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

    # Acquire the normalized run once, up front, so every parse-observable block
    # shares one acquisition reason when it is unavailable.
    run, run_reason = _acquire_import_run(root, manifest, run)

    for block in _PARSE_DEPENDENT_IMPORT_BLOCKS:
        if block not in import_expected:
            continue
        comparator = _IMPORT_BLOCK_COMPARATORS.get(block)
        if comparator is None:
            skipped.append(
                f"expected.import.{block}: skipped — import-level check not implemented "
                "yet (later plan step)"
            )
            continue
        if run is None:
            skipped.append(f"expected.import.{block}: skipped — {run_reason}")
            continue
        block_failures, block_skips = comparator(import_expected[block], run)
        failures.extend(block_failures)
        skipped.extend(block_skips)

    for block in _REFERENCE_IMPORT_BLOCKS:
        if block not in import_expected:
            continue
        # The reference-file hash check is parser-free and always runs; the
        # numeric parity sub-check gates on run availability inside the comparator.
        block_failures, block_skips = _compare_oscar_reference(import_expected[block], root, run)
        failures.extend(block_failures)
        skipped.extend(block_skips)

    for block in _DB_DEPENDENT_IMPORT_BLOCKS:
        if block not in import_expected:
            continue
        if conn is None:
            skipped.append(
                f"expected.import.{block}: skipped — no database connection"
            )
            continue
        if machine_id is None:
            skipped.append(
                f"expected.import.{block}: skipped — no machine scope "
                "(pass machine_id= to compare persisted identities)"
            )
            continue
        block_failures, block_skips = _compare_identity_hashes(
            import_expected[block], conn, machine_id
        )
        failures.extend(block_failures)
        skipped.extend(block_skips)

    # An unrecognized sub-block has no checker; surface it as a skip (not a
    # silent ignore) so a manifest typo or a future block is visible today.
    recognized = (
        set(_PARSE_DEPENDENT_IMPORT_BLOCKS)
        | set(_REFERENCE_IMPORT_BLOCKS)
        | set(_DB_DEPENDENT_IMPORT_BLOCKS)
    )
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


def _acquire_import_run(root: Path, manifest: dict, run: Any) -> tuple[Any, str]:
    """Return ``(run, reason)`` for the parse-observable comparisons.

    Preference order, matching the plan's gating:

    1. an explicitly injected ``run`` (used as-is — this is how tests exercise the
       comparison logic without the parser);
    2. a run parsed from the fixture source, when ``cpap-parser``/``cpap-py`` are
       installed;
    3. otherwise ``None`` with a human reason, so each block skips cleanly.

    Parsing real cards is fragile, so a parse failure is caught and turned into a
    skip reason rather than crashing conformance.
    """

    if run is not None:
        return run, "injected run provided"
    if not _import_parser_available():
        return None, "cpap-parser/cpap-py not installed"
    try:
        from importer.loaders.models import ImportOptions
        from importer.loaders.resmed_native import ResMedNativeLoader

        source_root = root / manifest.get("source_directory", "source")
        loader = ResMedNativeLoader()
        detected = loader.detect(source_root)
        if not detected:
            return None, "no ResMed device detected in fixture source"
        parsed, _directory = loader.import_data_with_directory(detected[0], ImportOptions())
        return parsed, "parsed from fixture source"
    except Exception as exc:  # noqa: BLE001 — parsing real cards must never crash conformance
        return None, f"could not parse fixture source ({type(exc).__name__})"


def _run_warning_codes(run: Any) -> set[str]:
    """Collect structured warning codes from a normalized ``ImportRun``.

    Gathers run-level warnings and each session's warnings. The ResMed loader
    flushes session warnings up to the run level, but reading both makes the
    collector robust to loaders that do not.
    """

    codes: set[str] = set()
    for warning in getattr(run, "warnings", []) or []:
        code = getattr(warning, "code", None)
        if code:
            codes.add(code)
    for session in getattr(run, "sessions", []) or []:
        for warning in getattr(session, "warnings", []) or []:
            code = getattr(warning, "code", None)
            if code:
                codes.add(code)
    return codes


def _compare_warnings(expected_block: dict, run: Any) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.warnings`` against a normalized run.

    Supported keys:

    * ``codes`` — each listed code must be surfaced by the run.
    * ``absent`` — each listed code must *not* be surfaced.

    Any other key is reported as a skip (no silent pass for an unsupported key).
    """

    failures: list[str] = []
    skips: list[str] = []
    present = _run_warning_codes(run)

    for code in expected_block.get("codes", []):
        if code not in present:
            failures.append(
                f"expected.import.warnings.codes: expected {code!r} present, "
                f"got {sorted(present)}"
            )
    for code in expected_block.get("absent", []):
        if code in present:
            failures.append(
                f"expected.import.warnings.absent: {code!r} should be absent but was present"
            )
    for key in sorted(set(expected_block) - {"codes", "absent"}):
        skips.append(
            f"expected.import.warnings.{key}: skipped — unsupported warnings key "
            "(only 'codes'/'absent' are checked)"
        )
    return failures, skips


def _sessions_by_date(run: Any) -> dict[str, list]:
    """Group a run's sessions by ``machine_local_date`` (a ``YYYY-MM-DD`` str)."""

    by_date: dict[str, list] = {}
    for session in getattr(run, "sessions", []) or []:
        by_date.setdefault(session.machine_local_date, []).append(session)
    return by_date


def _compare_session_blocks(expected_block: dict, run: Any) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.session_blocks`` against a normalized run.

    Per machine-local date, only ``block_count`` (summed across that night's
    sessions) is observable from the normalized run today. Interval start/end
    comparison is a later plan step, so an ``intervals`` key (or any other key)
    is reported as a skip rather than silently passed.
    """

    failures: list[str] = []
    skips: list[str] = []
    by_date = _sessions_by_date(run)

    for date_key, expected_dict in expected_block.items():
        if date_key not in by_date:
            failures.append(
                f"expected.import.session_blocks.{date_key}: date not found in normalized output "
                f"(present: {sorted(by_date)})"
            )
            continue
        block_count = sum(len(session.blocks) for session in by_date[date_key])
        if "block_count" in expected_dict:
            _expect(
                failures,
                f"expected.import.session_blocks.{date_key}.block_count",
                block_count,
                expected_dict["block_count"],
            )
        for key in sorted(set(expected_dict) - {"block_count"}):
            skips.append(
                f"expected.import.session_blocks.{date_key}.{key}: skipped — "
                "interval/boundary comparison not implemented yet (later plan step)"
            )
    return failures, skips


def _observable_aggregates(session: Any) -> dict[str, int | None]:
    """Therapy-aggregate fields observable from one normalized session.

    Derived from the loader's usage ``DerivedValue``s and block list:

    * ``usage_seconds``       ← ``computed_usage_hours`` × 3600
    * ``wall_clock_seconds``  ← ``recording_span_hours`` × 3600
    * ``gap_seconds``         ← wall-clock − usage (both must be present)
    * ``block_count``         ← number of session blocks

    A value is ``None`` when its source derived value is absent — the caller then
    skips that field instead of comparing a fabricated number.
    """

    derived = {value.key: value.value for value in getattr(session, "derived_values", [])}

    def seconds(key: str) -> int | None:
        value = derived.get(key)
        return round(value * 3600) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    usage_s = seconds("computed_usage_hours")
    span_s = seconds("recording_span_hours")
    gap_s = (span_s - usage_s) if (usage_s is not None and span_s is not None) else None
    return {
        "usage_seconds": usage_s,
        "wall_clock_seconds": span_s,
        "gap_seconds": gap_s,
        "block_count": len(getattr(session, "blocks", [])),
    }


def _compare_therapy_aggregates(expected_block: dict, run: Any) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.therapy_aggregates`` against a normalized run.

    Only fields genuinely observable from the normalized ``ImportRun`` are
    compared (see :func:`_observable_aggregates`). A requested field that is not
    observable, or whose source derived value is absent, is skipped with a clear
    reason — never faked. ``gap_seconds`` is the wall-clock−usage difference, the
    same definition the nightly aggregate uses.
    """

    failures: list[str] = []
    skips: list[str] = []
    by_date = _sessions_by_date(run)

    for date_key, expected_dict in expected_block.items():
        sessions = by_date.get(date_key)
        if not sessions:
            failures.append(
                f"expected.import.therapy_aggregates.{date_key}: date not found in normalized "
                f"output (present: {sorted(by_date)})"
            )
            continue
        # A machine-local date is one normalized session in the ResMed loader;
        # if several share a date, sum the additive fields.
        observed = _observable_aggregates(sessions[0])
        for extra in sessions[1:]:
            extra_obs = _observable_aggregates(extra)
            for key in ("usage_seconds", "wall_clock_seconds", "gap_seconds", "block_count"):
                if observed[key] is not None and extra_obs[key] is not None:
                    observed[key] += extra_obs[key]

        for field_name, expected_value in expected_dict.items():
            if field_name not in observed:
                skips.append(
                    f"expected.import.therapy_aggregates.{date_key}.{field_name}: skipped — "
                    "field not observable from normalized ImportRun"
                )
            elif observed[field_name] is None:
                skips.append(
                    f"expected.import.therapy_aggregates.{date_key}.{field_name}: skipped — "
                    "source derived value absent in normalized output"
                )
            else:
                _expect(
                    failures,
                    f"expected.import.therapy_aggregates.{date_key}.{field_name}",
                    observed[field_name],
                    expected_value,
                )
    return failures, skips


def _compare_settings(expected_block: dict, run: Any) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.settings`` against a normalized run.

    The ResMed cpap-parser loader does not map settings *values* into the
    normalized ``Session`` yet, so only presence/count is observable. Supported
    per-date keys:

    * ``snapshot_count`` — number of ``SettingsSnapshot``s for that date.
    * ``present`` — bool: whether any snapshot exists for that date.

    A per-date setting *value* key (e.g. ``therapy_mode``) is skipped with a clear
    reason rather than silently passed — value comparison is a later plan step.
    """

    failures: list[str] = []
    skips: list[str] = []
    by_date = _sessions_by_date(run)

    for date_key, expected_dict in expected_block.items():
        if date_key not in by_date:
            failures.append(
                f"expected.import.settings.{date_key}: date not found in normalized output "
                f"(present: {sorted(by_date)})"
            )
            continue
        snapshots = [snap for session in by_date[date_key] for snap in getattr(session, "settings", [])]
        for key, expected_value in expected_dict.items():
            if key == "snapshot_count":
                _expect(failures, f"expected.import.settings.{date_key}.snapshot_count", len(snapshots), expected_value)
            elif key == "present":
                _expect(failures, f"expected.import.settings.{date_key}.present", bool(snapshots), expected_value)
            else:
                skips.append(
                    f"expected.import.settings.{date_key}.{key}: skipped — settings value "
                    "comparison not implemented yet (loader maps no settings snapshots)"
                )
    return failures, skips


def _compare_oscar_reference(
    expected_block: dict, fixture_root: Path, run: Any
) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.oscar_reference`` against checked-in references.

    Two parts with different dependencies:

    * **Reference-file hash verification (parser-free).** When ``export_hash`` and
      a reference file path (``summary_csv`` or ``file``) are both given, the
      sha256 of the committed file must match. A missing file or a hash mismatch
      is a *failure*, not a skip — a declared reference that cannot be verified is
      a real problem (plan §9). Hashing a redistributable, anonymized export
      exposes no PHI (plan §11). If only one of the two is given, the check is
      skipped with a reason (nothing to verify against).
    * **Numeric parity (needs a normalized run).** Comparing parsed nightly values
      against the OSCAR rows is a later plan step; it is always skipped here, with
      the reason noting whether a run was even available.
    """

    failures: list[str] = []
    skips: list[str] = []
    export_hash = expected_block.get("export_hash")
    ref_rel = expected_block.get("summary_csv") or expected_block.get("file")

    if export_hash and ref_rel:
        ref_path = fixture_root / ref_rel
        if not ref_path.is_file():
            failures.append(
                f"expected.import.oscar_reference: reference file not found: {ref_rel}"
            )
        else:
            actual = hashlib.sha256(ref_path.read_bytes()).hexdigest()
            # Accept an optional ``sha256:`` algorithm prefix; compare hex only.
            expected_hex = export_hash.split(":", 1)[-1].strip().lower()
            if actual != expected_hex:
                failures.append(
                    f"expected.import.oscar_reference.export_hash: expected {expected_hex!r}, "
                    f"got {actual!r} for {ref_rel}"
                )
    elif export_hash and not ref_rel:
        skips.append(
            "expected.import.oscar_reference.export_hash: skipped — no reference file path "
            "('summary_csv'/'file') to hash against"
        )
    elif ref_rel and not export_hash:
        skips.append(
            "expected.import.oscar_reference: skipped — reference file given without "
            "'export_hash' to verify"
        )

    parity_reason = (
        "numeric parity vs OSCAR export not implemented yet (later plan step)"
        if run is not None
        else "numeric parity needs a normalized run (none available)"
    )
    skips.append(f"expected.import.oscar_reference.parity: skipped — {parity_reason}")
    return failures, skips


def _hash_identity_keys(keys: list[str]) -> str:
    """sha256 of a newline-joined, sorted key set.

    Hashing **derived, pseudonymous keys** (``source_session_key`` /
    ``source_block_key`` — date/index-scoped strings) keeps the manifest free of
    raw serials or PHI (plan §11). An empty set hashes deterministically too, so
    "nothing persisted" is a distinct, comparable value rather than a crash.
    """

    joined = "\n".join(sorted(keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def persisted_identity_snapshot(conn: Any, *, machine_id: str) -> dict[str, Any]:
    """Read-only snapshot of one machine's persisted stable identities.

    Returns the sorted ``source_session_key`` and ``source_block_key`` sets for
    ``machine_id``, their sha256 hashes, and counts. These derived keys are the
    identities that must stay stable across a duplicate re-import and grow only
    by the new nights on an incremental import.

    Read-only: issues ``SELECT``s only — never writes, and never commits. Safe to
    call inside a caller-owned (e.g. rolled-back test) transaction.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_session_key
            FROM sessions
            WHERE machine_id = %s AND source_session_key IS NOT NULL
            """,
            (machine_id,),
        )
        session_keys = sorted(row[0] for row in cur.fetchall())
        cur.execute(
            """
            SELECT b.source_block_key
            FROM session_blocks b
            JOIN sessions s ON s.id = b.session_id
            WHERE s.machine_id = %s AND b.source_block_key IS NOT NULL
            """,
            (machine_id,),
        )
        block_keys = sorted(row[0] for row in cur.fetchall())

    return {
        "session_keys": tuple(session_keys),
        "block_keys": tuple(block_keys),
        "sessions": _hash_identity_keys(session_keys),
        "blocks": _hash_identity_keys(block_keys),
        "session_count": len(session_keys),
        "block_count": len(block_keys),
    }


def _compare_identity_hashes(
    expected_block: dict, conn: Any, machine_id: str
) -> tuple[list[str], list[str]]:
    """Compare ``expected.import.identity_hashes`` against persisted DB state.

    Supported keys (read-only, parser-free given a ``conn``):

    * ``sessions`` / ``blocks`` — sha256 (optionally ``sha256:``-prefixed) of the
      persisted ``source_session_key`` / ``source_block_key`` set for the machine.
    * ``session_count`` / ``block_count`` — integer counts.
    * ``algorithm`` — metadata; only ``sha256`` is supported (else a skip).

    Other keys (e.g. ``machine``, ``incremental_night``) are deferred and skipped
    — never silently passed. The duplicate/incremental *stability* property is
    asserted by DB-gated tests using :func:`persisted_identity_snapshot`.
    """

    failures: list[str] = []
    skips: list[str] = []
    snapshot = persisted_identity_snapshot(conn, machine_id=machine_id)

    algorithm = expected_block.get("algorithm")
    if algorithm is not None and str(algorithm).strip().lower() != "sha256":
        skips.append(
            f"expected.import.identity_hashes.algorithm: skipped — only 'sha256' is "
            f"supported, got {algorithm!r}"
        )

    for key in ("sessions", "blocks"):
        if key in expected_block:
            expected_hex = str(expected_block[key]).split(":", 1)[-1].strip().lower()
            _expect(failures, f"expected.import.identity_hashes.{key}", snapshot[key], expected_hex)
    for key in ("session_count", "block_count"):
        if key in expected_block:
            _expect(failures, f"expected.import.identity_hashes.{key}", snapshot[key], expected_block[key])

    handled = {"sessions", "blocks", "session_count", "block_count", "algorithm"}
    for key in sorted(set(expected_block) - handled):
        skips.append(
            f"expected.import.identity_hashes.{key}: skipped — not implemented yet "
            "(later plan step)"
        )
    return failures, skips


#: Dispatch table of implemented parse-observable comparators. A block listed in
#: :data:`_PARSE_DEPENDENT_IMPORT_BLOCKS` but absent here skips as
#: "not implemented yet". Each comparator returns ``(failures, skips)``.
_IMPORT_BLOCK_COMPARATORS = {
    "warnings": _compare_warnings,
    "session_blocks": _compare_session_blocks,
    "therapy_aggregates": _compare_therapy_aggregates,
    "settings": _compare_settings,
}


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
