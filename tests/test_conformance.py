"""Tests for manifest-driven importer conformance scaffolding."""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import importer.conformance as conformance
from importer.conformance import (
    ImportConformanceResult,
    validate_fixture,
    validate_import,
    validate_manifest_metadata,
)
from importer.loaders.models import (
    Confidence,
    DerivedValue,
    ImportWarning,
    Session,
    SessionBlock,
    SettingsSnapshot,
    ValidationStatus,
)
from importer.loaders.planning import CoverageSummary

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "conformance"
DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"


def _fake_session(
    machine_local_date,
    *,
    blocks=(),
    settings=(),
    derived=None,
    warnings=(),
):
    """Build a normalized :class:`Session` for import-level comparison tests.

    Real model objects (not mocks) so the comparators run against the same shapes
    the ResMed loader produces, but without needing the parser or a real card.
    """
    session = Session(
        source_session_key=f"resmed:SN-TEST:{machine_local_date}",
        machine_key="SN-TEST",
        start_time=datetime(2026, 6, 1, 22, 0),
        end_time=datetime(2026, 6, 2, 6, 0),
        machine_local_date=machine_local_date,
        timezone_basis="machine_local",
    )
    session.blocks = list(blocks)
    session.settings = list(settings)
    session.warnings = list(warnings)
    for key, (value, unit) in (derived or {}).items():
        session.derived_values.append(
            DerivedValue(
                key=key,
                value=value,
                unit=unit,
                method="test",
                input_refs=(),
                validation=ValidationStatus.PARTIAL,
            )
        )
    return session


def _fake_run(*, warnings=(), sessions=()):
    """A duck-typed stand-in carrying only what the comparators read."""
    return SimpleNamespace(warnings=list(warnings), sessions=list(sessions))


def _block(start, end, kind="recording"):
    return SessionBlock(
        source_block_key=f"blk:{start.isoformat()}",
        start_time=start,
        end_time=end,
        block_kind=kind,
        source_file_ids=(),
    )


def _settings_snapshot(**settings):
    """A minimal :class:`SettingsSnapshot`; the comparator only counts these."""
    return SettingsSnapshot(
        effective_at=datetime(2026, 6, 1, 22, 0),
        settings=dict(settings),
        source_names={},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )


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


def test_conformance_coverage_cannot_observe_therapy_aggregates():
    """Alpha 6 §5 boundary: the planning-only harness cannot observe usage/span/gap.

    ``CoverageSummary`` is derived from file inventory and directory structure,
    not parsed payloads — the conformance harness never decodes STR.edf or runs
    nightly aggregation. Usage, wall-clock span, and gap therefore cannot be
    asserted in a manifest here; doing so would require an import-level
    conformance path (checklist §5/§6). Pin the observable field set so that
    adding a parsed-aggregate field to ``CoverageSummary`` forces a conscious
    decision rather than silently implying the harness can check it.
    """
    fields = set(CoverageSummary.__dataclass_fields__)

    # Aggregate/parsed semantics are NOT observable without parsing payloads.
    for parsed_only in (
        "usage_seconds",
        "wall_clock_seconds",
        "gap_seconds",
        "usage_hours",
        "span_hours",
        "mask_on_intervals",
        "therapy_mode",
    ):
        assert parsed_only not in fields, f"{parsed_only} is not planning-observable"

    # The harness observes only file/directory-derived coverage.
    assert fields == {
        "first_date",
        "last_date",
        "therapy_days",
        "estimated_session_blocks",
        "waveform_files",
        "event_files",
        "oximetry_files",
        "settings_files",
    }


def test_unknown_coverage_field_reports_failure_not_crash(tmp_path):
    """A misspelled/unobservable coverage key fails cleanly, never AttributeError.

    The coverage checker iterates manifest-provided keys; a typo like
    ``waveform_file`` (missing the ``s``) must surface as an explicit
    ``coverage.<field>: unknown coverage field`` failure instead of raising and
    aborting the whole run.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["coverage"]["waveform_file"] = 0  # typo: should be waveform_files
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_fixture(fixture)  # must not raise

    assert not result.passed
    assert any(
        failure.startswith("coverage.waveform_file:") and "unknown coverage field" in failure
        for failure in result.failures
    ), f"expected an unknown-coverage-field failure, got {result.failures}"


def test_validate_import_is_importable():
    """Plan Step 1: ``validate_import`` + ``ImportConformanceResult`` exist.

    The import-level conformance scaffold is now built (Step 1 of
    ``docs/sleeplab_2_import_level_conformance_plan.md``), so the entry point and
    its result type must be importable from ``importer.conformance`` alongside
    the planning-only ``validate_fixture``. This replaces the earlier
    design-only boundary test.
    """
    assert hasattr(conformance, "validate_fixture")
    assert hasattr(conformance, "validate_import")
    assert hasattr(conformance, "ImportConformanceResult")
    # The result type is small and explicit: exactly these four fields.
    assert set(ImportConformanceResult.__dataclass_fields__) == {
        "fixture_id",
        "passed",
        "failures",
        "skipped",
    }


def test_validate_import_passes_and_skips_when_import_block_absent():
    """Plan Step 1: a manifest without ``expected.import`` runs nothing, cleanly.

    The committed synthetic fixture carries no ``expected.import`` block, so
    ``validate_import`` must pass with no failures and a single clear skip reason
    — the backward-compatible default that keeps the new entry point inert for
    every existing fixture.
    """
    fixture = FIXTURE_ROOT / "synthetic-resmed-minimal"

    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    assert "import" not in manifest["expected"], "fixture must stay import-block-free"

    result = validate_import(fixture)

    assert isinstance(result, ImportConformanceResult)
    assert result.fixture_id == "synthetic-resmed-minimal"
    assert result.passed
    assert result.failures == ()
    assert len(result.skipped) == 1
    assert "expected.import absent" in result.skipped[0]


def test_validate_import_skips_clearly_when_no_parser_or_db(tmp_path):
    """Plan Step 1: present ``expected.import`` blocks skip with clear reasons.

    With ``expected.import`` present but no parser/DB execution available, the
    scaffold must not crash and must not fabricate a pass for unchecked work: it
    returns ``passed=True`` (nothing was actually verified) with one clearly
    reasoned skip per requested sub-block. Parse-dependent blocks and the
    DB-dependent ``identity_hashes`` block are each named in a skip reason, and
    ``conn=None`` always yields a "no database connection" skip for the latter.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["import"] = {
        "settings": {"2026-06-01": {"therapy_mode": "apap"}},
        "session_blocks": {"2026-06-01": {"block_count": 1}},
        "therapy_aggregates": {"2026-06-01": {"usage_seconds": 600}},
        "warnings": {"codes": ["resmed_summary_only_day"]},
        "identity_hashes": {"algorithm": "sha256", "sessions": "deadbeef"},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_import(fixture, conn=None)

    # Nothing was actually checked, so the run passes with no failures.
    assert result.passed
    assert result.failures == ()

    # Every requested sub-block is named in exactly one skip reason.
    skipped_blob = "\n".join(result.skipped)
    for block in ("settings", "session_blocks", "therapy_aggregates", "warnings", "identity_hashes"):
        assert f"expected.import.{block}:" in skipped_blob, (
            f"{block} should be reported as a skip, got {result.skipped}"
        )
    assert len(result.skipped) == 5

    # The DB-dependent block skips specifically because there is no connection.
    db_skip = next(s for s in result.skipped if "identity_hashes" in s)
    assert "no database connection" in db_skip


def _write_import_manifest(tmp_path, import_block):
    """Copy the synthetic fixture and inject an ``expected.import`` block."""
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)
    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["import"] = import_block
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return fixture


def test_validate_import_warnings_pass_when_codes_present(tmp_path):
    """Plan Step 2: an injected run with the expected warning codes passes.

    The warnings comparator reads run-level (and session-level) ``ImportWarning``
    codes from a normalized ``ImportRun``. Injecting a run lets the real
    comparison run without the parser or a database.
    """
    fixture = _write_import_manifest(
        tmp_path, {"warnings": {"codes": ["resmed_summary_only_day", "resmed_waveform_absent"]}}
    )
    run = _fake_run(
        warnings=[
            ImportWarning(code="resmed_summary_only_day", severity="info", message="x"),
            ImportWarning(code="resmed_waveform_absent", severity="warning", message="y"),
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert result.failures == ()
    # The warnings block was actually checked, so it is not in skipped.
    assert not any("expected.import.warnings:" in s for s in result.skipped)


def test_validate_import_warnings_fail_when_code_absent(tmp_path):
    """Plan Step 2: a missing expected warning code is a real failure, not a skip."""
    fixture = _write_import_manifest(
        tmp_path, {"warnings": {"codes": ["resmed_summary_only_day", "resmed_waveform_absent"]}}
    )
    # Only one of the two expected codes is surfaced.
    run = _fake_run(
        warnings=[ImportWarning(code="resmed_summary_only_day", severity="info", message="x")]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.warnings.codes" in f and "resmed_waveform_absent" in f
        for f in result.failures
    ), f"expected a warnings.codes failure, got {result.failures}"


def test_validate_import_warnings_collected_from_session_level(tmp_path):
    """Plan Step 2: session-level warnings count toward the surfaced codes.

    A loader that leaves a diagnostic only on ``session.warnings`` (not flushed to
    the run) must still satisfy an expected code — the collector reads both.
    """
    fixture = _write_import_manifest(tmp_path, {"warnings": {"codes": ["resmed_summary_only_day"]}})
    session = _fake_session(
        "2026-06-01",
        warnings=[ImportWarning(code="resmed_summary_only_day", severity="info", message="x")],
    )
    run = _fake_run(sessions=[session])

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_warnings_absent_check(tmp_path):
    """Plan Step 2: ``warnings.absent`` fails when a forbidden code is present."""
    fixture = _write_import_manifest(tmp_path, {"warnings": {"absent": ["resmed_waveform_absent"]}})
    run = _fake_run(
        warnings=[ImportWarning(code="resmed_waveform_absent", severity="warning", message="y")]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any("expected.import.warnings.absent" in f for f in result.failures)


# ---------------------------------------------------------------------------
# expected.import.session_blocks
# ---------------------------------------------------------------------------


def test_validate_import_session_blocks_pass_on_matching_count(tmp_path):
    """Plan Step 2: per-date block_count is compared against the normalized run."""
    fixture = _write_import_manifest(
        tmp_path, {"session_blocks": {"2026-06-01": {"block_count": 2}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[
                    _block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0)),
                    _block(datetime(2026, 6, 1, 23, 15), datetime(2026, 6, 2, 0, 30)),
                ],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert not any("expected.import.session_blocks" in s for s in result.skipped)


def test_validate_import_session_blocks_fail_on_count_mismatch(tmp_path):
    """Plan Step 2: a wrong block_count is a real failure."""
    fixture = _write_import_manifest(
        tmp_path, {"session_blocks": {"2026-06-01": {"block_count": 3}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[_block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0))],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.session_blocks.2026-06-01.block_count" in f for f in result.failures
    ), f"expected a block_count failure, got {result.failures}"


def test_validate_import_session_blocks_fail_on_missing_date(tmp_path):
    """Plan Step 2: a requested date absent from the run is a failure, not a skip."""
    fixture = _write_import_manifest(
        tmp_path, {"session_blocks": {"2026-06-09": {"block_count": 1}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", blocks=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.session_blocks.2026-06-09" in f and "date not found" in f
        for f in result.failures
    ), f"expected a missing-date failure, got {result.failures}"


def test_validate_import_session_blocks_interval_key_is_skipped(tmp_path):
    """Plan Step 2: interval/boundary comparison is deferred, surfaced as a skip."""
    fixture = _write_import_manifest(
        tmp_path,
        {"session_blocks": {"2026-06-01": {"block_count": 1, "intervals": [{"start": "x"}]}}},
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[_block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0))],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    # block_count still passes; only the intervals sub-key is skipped.
    assert result.passed, result.failures
    assert any(
        "expected.import.session_blocks.2026-06-01.intervals" in s for s in result.skipped
    ), f"expected an intervals skip, got {result.skipped}"


# ---------------------------------------------------------------------------
# expected.import.therapy_aggregates
# ---------------------------------------------------------------------------


def _aggregate_run(date_key="2026-06-01"):
    """A run whose one session yields usage=8100s, span=9000s, gap=900s, 2 blocks."""
    return _fake_run(
        sessions=[
            _fake_session(
                date_key,
                blocks=[
                    _block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0)),
                    _block(datetime(2026, 6, 1, 23, 15), datetime(2026, 6, 2, 0, 30)),
                ],
                derived={
                    "computed_usage_hours": (2.25, "h"),  # 8100 s
                    "recording_span_hours": (2.5, "h"),   # 9000 s
                },
            )
        ]
    )


def test_validate_import_therapy_aggregates_pass_on_observable_fields(tmp_path):
    """Plan Step 2: usage/wall-clock/gap/block_count derive from the normalized run."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "therapy_aggregates": {
                "2026-06-01": {
                    "usage_seconds": 8100,
                    "wall_clock_seconds": 9000,
                    "gap_seconds": 900,
                    "block_count": 2,
                }
            }
        },
    )

    result = validate_import(fixture, run=_aggregate_run())

    assert result.passed, result.failures
    assert not any("expected.import.therapy_aggregates" in s for s in result.skipped)


def test_validate_import_therapy_aggregates_fail_on_usage_mismatch(tmp_path):
    """Plan Step 2: a wrong usage value is a real failure."""
    fixture = _write_import_manifest(
        tmp_path, {"therapy_aggregates": {"2026-06-01": {"usage_seconds": 9999}}}
    )

    result = validate_import(fixture, run=_aggregate_run())

    assert not result.passed
    assert any(
        "expected.import.therapy_aggregates.2026-06-01.usage_seconds" in f for f in result.failures
    ), f"expected a usage_seconds failure, got {result.failures}"


def test_validate_import_therapy_aggregates_skip_when_derived_absent(tmp_path):
    """Plan Step 2: a field whose source derived value is absent is skipped, not faked."""
    fixture = _write_import_manifest(
        tmp_path, {"therapy_aggregates": {"2026-06-01": {"usage_seconds": 8100}}}
    )
    # Session has no computed_usage_hours derived value.
    run = _fake_run(sessions=[_fake_session("2026-06-01", blocks=[])])

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert any(
        "expected.import.therapy_aggregates.2026-06-01.usage_seconds" in s
        and "derived value absent" in s
        for s in result.skipped
    ), f"expected a derived-absent skip, got {result.skipped}"


def test_validate_import_therapy_aggregates_skip_for_unobservable_field(tmp_path):
    """Plan Step 2: a field that is not observable from the run is skipped clearly."""
    fixture = _write_import_manifest(
        tmp_path, {"therapy_aggregates": {"2026-06-01": {"ahi": 1.0}}}
    )

    result = validate_import(fixture, run=_aggregate_run())

    assert result.passed, result.failures
    assert any(
        "expected.import.therapy_aggregates.2026-06-01.ahi" in s
        and "not observable" in s
        for s in result.skipped
    ), f"expected an unobservable-field skip, got {result.skipped}"


# ---------------------------------------------------------------------------
# expected.import.settings
# ---------------------------------------------------------------------------


def test_validate_import_settings_count_and_presence_pass(tmp_path):
    """Plan Step 2: settings snapshot_count and presence are observable/compared."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"snapshot_count": 2, "present": True}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                settings=[_settings_snapshot(therapy_mode="apap"), _settings_snapshot(ramp="auto")],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_settings_count_mismatch_fails(tmp_path):
    """Plan Step 2: a wrong snapshot_count is a real failure."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"snapshot_count": 1}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.snapshot_count" in f for f in result.failures
    ), f"expected a snapshot_count failure, got {result.failures}"


def test_validate_import_settings_value_key_is_skipped(tmp_path):
    """Plan Step 2: a settings *value* key is deferred (loader maps no settings yet)."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"therapy_mode": "apap"}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[])])

    result = validate_import(fixture, run=run)

    # Not a failure — value comparison is honestly skipped, not faked.
    assert result.passed, result.failures
    assert any(
        "expected.import.settings.2026-06-01.therapy_mode" in s
        and "value comparison not implemented" in s
        for s in result.skipped
    ), f"expected a settings-value skip, got {result.skipped}"


def test_validate_import_mixed_blocks_check_and_skip_together(tmp_path):
    """Plan Step 2: a manifest mixing observable and deferred checks behaves per-block.

    warnings + session_blocks + therapy_aggregates are checked (and pass); a
    settings *value* key is skipped. The overall run passes because nothing
    failed, and the skip is recorded — never a silent pass.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "warnings": {"codes": ["resmed_summary_only_day"]},
            "session_blocks": {"2026-06-01": {"block_count": 2}},
            "therapy_aggregates": {"2026-06-01": {"usage_seconds": 8100}},
            "settings": {"2026-06-01": {"therapy_mode": "apap"}},
        },
    )
    run = _aggregate_run()
    run.warnings = [ImportWarning(code="resmed_summary_only_day", severity="info", message="x")]

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert any("expected.import.settings.2026-06-01.therapy_mode" in s for s in result.skipped)


# ---------------------------------------------------------------------------
# expected.import.oscar_reference (reference-file hash verification)
# ---------------------------------------------------------------------------


def _write_reference_csv(fixture, content: bytes, rel="oscar_reference/summary.csv"):
    """Write a reference export under the fixture and return ``(rel, sha256hex)``."""
    path = fixture / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel, hashlib.sha256(content).hexdigest()


def test_validate_import_oscar_reference_hash_matches(tmp_path):
    """Plan Step 3: a matching reference hash verifies (parser-free); parity skips."""
    content = b"Date,AHI,Total Time\n2026-06-01,1.0,08:00:00\n"
    rel = "oscar_reference/summary.csv"
    digest = hashlib.sha256(content).hexdigest()
    fixture = _write_import_manifest(
        tmp_path, {"oscar_reference": {"export_hash": f"sha256:{digest}", "summary_csv": rel}}
    )
    # Create the reference file inside the (already-copied) fixture tree.
    _write_reference_csv(fixture, content, rel)

    result = validate_import(fixture)  # no run/parser needed for the hash check

    assert result.passed, result.failures
    assert not any("oscar_reference.export_hash" in f for f in result.failures)
    # Numeric parity is deferred and reported as a skip, never a silent pass.
    assert any("oscar_reference.parity" in s for s in result.skipped)


def test_validate_import_oscar_reference_hash_mismatch_fails(tmp_path):
    """Plan Step 3: a wrong reference hash is a real failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {"oscar_reference": {"export_hash": "sha256:" + "0" * 64, "summary_csv": "oscar_reference/summary.csv"}},
    )
    _write_reference_csv(fixture, b"Date,AHI\n2026-06-01,1.0\n")

    result = validate_import(fixture)

    assert not result.passed
    assert any("oscar_reference.export_hash" in f for f in result.failures), result.failures


def test_validate_import_oscar_reference_missing_file_fails(tmp_path):
    """Plan Step 3: a declared reference file that is absent is a failure, not a skip."""
    fixture = _write_import_manifest(
        tmp_path,
        {"oscar_reference": {"export_hash": "sha256:" + "0" * 64, "summary_csv": "oscar_reference/missing.csv"}},
    )

    result = validate_import(fixture)

    assert not result.passed
    assert any(
        "oscar_reference" in f and "reference file not found" in f for f in result.failures
    ), result.failures


def test_validate_import_oscar_reference_hash_without_path_skips(tmp_path):
    """Plan Step 3: an ``export_hash`` with no file path has nothing to verify → skip."""
    fixture = _write_import_manifest(tmp_path, {"oscar_reference": {"export_hash": "sha256:abc"}})

    result = validate_import(fixture)

    assert result.passed, result.failures
    assert any(
        "oscar_reference.export_hash" in s and "no reference file path" in s
        for s in result.skipped
    ), result.skipped


def test_validate_import_surfaces_unknown_import_block(tmp_path):
    """Plan Step 1: an unrecognized ``expected.import`` sub-block is visible.

    A typo'd or future sub-block has no checker yet; it must be surfaced as an
    explicit skip rather than silently ignored, so a manifest mistake is caught.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expected"]["import"] = {"sesion_blocks": {}}  # typo: missing 's'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = validate_import(fixture)

    assert result.passed
    assert any(
        "sesion_blocks" in s and "unknown import-level block" in s for s in result.skipped
    ), f"expected an unknown-block skip, got {result.skipped}"


def test_import_level_conformance_design_doc_is_linked():
    """Alpha 6 §6: the checklist points at the import-level conformance plan.

    Validates the documentation assumption that the design lives in a dedicated
    plan doc and that the checklist references it, so the two cannot drift apart.
    Doc/checklist-only — no parser, DB, or fixture data.
    """
    plan = DOCS_ROOT / "sleeplab_2_import_level_conformance_plan.md"
    assert plan.is_file(), "import-level conformance design doc is missing"

    checklist = (DOCS_ROOT / "sleeplab_2_alpha_6_checklist.md").read_text(encoding="utf-8")
    assert "sleeplab_2_import_level_conformance_plan.md" in checklist, (
        "Alpha 6 checklist §6 must link the import-level conformance design doc"
    )

    # The plan must answer the entry-point and manifest-block design questions.
    plan_text = plan.read_text(encoding="utf-8")
    assert "validate_import" in plan_text
    assert "expected.import" in plan_text


def test_expected_import_block_is_optional_and_absent_today(tmp_path):
    """Alpha 6 §6: ``expected.import`` is optional; no committed fixture uses it.

    The planning-only ``validate_fixture`` must keep passing whether or not an
    ``expected.import`` block is present (backward compatibility for the future
    import-level path). The synthetic fixture ships none today; adding one must
    not change ``validate_fixture``'s verdict, since that harness does not
    consume it.
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"

    # No committed synthetic fixture carries the (future) import-level block yet.
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    assert "import" not in manifest["expected"], "fixture must stay import-block-free"
    assert validate_fixture(src).passed

    # Adding an import block is inert for the planning-only harness.
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)
    manifest_path = fixture / "manifest.json"
    manifest["expected"]["import"] = {
        "therapy_aggregates": {"2026-06-01": {"usage_seconds": 600}}
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert validate_fixture(fixture).passed, (
        "an expected.import block must not affect the planning-only harness"
    )


def test_oximetry_files_coverage_is_observable_and_checked(tmp_path):
    """Alpha 6 §1/§5: SA2/SAD oximetry file coverage is an observable manifest field.

    The synthetic fixture ships zero oximetry (SA2/SAD) files. Asserting the
    correct count passes; asserting a wrong count fails — proving oximetry file
    coverage is a real, file-derived field the harness can check (complementing
    the SA2 channel-metadata mapping work).
    """
    src = FIXTURE_ROOT / "synthetic-resmed-minimal"
    fixture = tmp_path / "synthetic-resmed-minimal"
    shutil.copytree(src, fixture)

    manifest_path = fixture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Correct value (zero oximetry files) passes.
    manifest["expected"]["coverage"]["oximetry_files"] = 0
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    assert validate_fixture(fixture).passed

    # Wrong value is caught.
    manifest["expected"]["coverage"]["oximetry_files"] = 2
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result = validate_fixture(fixture)
    assert not result.passed
    assert any("coverage.oximetry_files" in failure for failure in result.failures), (
        f"expected an oximetry-coverage failure, got {result.failures}"
    )
