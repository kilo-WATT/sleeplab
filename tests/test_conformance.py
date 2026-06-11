"""Tests for manifest-driven importer conformance scaffolding."""

import hashlib
import json
import shutil
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import importer.conformance as conformance
import importer.db as importer_db
from importer.conformance import (
    ImportConformanceResult,
    persisted_identity_snapshot,
    summarize_import_blocks,
    validate_fixture,
    validate_import,
    validate_manifest_metadata,
)
from importer.loaders.models import (
    Confidence,
    DerivedValue,
    Event,
    ImportWarning,
    Session,
    SessionBlock,
    SettingsSnapshot,
    ValidationStatus,
)
from importer.loaders.planning import CoverageSummary

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "conformance"
DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
AIRSENSE10_FIXTURE = (
    Path(__file__).resolve().parent / "conformance" / "fixtures" / "resmed_airsense10_001"
)


def _fake_session(
    machine_local_date,
    *,
    blocks=(),
    settings=(),
    derived=None,
    warnings=(),
    events=(),
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
    session.events = list(events)
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


def _event(event_type, start, duration=None, *, source_event_key=None):
    """A minimal normalized :class:`Event` for event-parity comparison tests."""
    return Event(
        source_event_key=source_event_key or f"evt:{event_type}:{start.isoformat()}",
        event_type=event_type,
        source_type="resmed",
        start_time=start,
        duration_seconds=duration,
        source_file_id="f1",
        confidence=Confidence.PROBABLE,
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


def _two_block_run(date_key="2026-06-01"):
    """A run whose one session has two blocks: 22:00–23:00 and 23:15–00:30."""
    return _fake_run(
        sessions=[
            _fake_session(
                date_key,
                blocks=[
                    _block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0)),
                    _block(datetime(2026, 6, 1, 23, 15), datetime(2026, 6, 2, 0, 30)),
                ],
            )
        ]
    )


def test_validate_import_session_blocks_intervals_pass(tmp_path):
    """Alpha 7: expected interval boundaries matching the run's blocks pass.

    Both start/end boundaries match within tolerance, so the intervals check runs
    and produces no failure and no skip for the intervals sub-key.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "block_count": 2,
                    "intervals": [
                        {"start": "2026-06-01T22:00:00", "end": "2026-06-01T23:00:00"},
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"},
                    ],
                }
            }
        },
    )

    result = validate_import(fixture, run=_two_block_run())

    assert result.passed, result.failures
    assert not any("session_blocks.2026-06-01.intervals" in s for s in result.skipped)


def test_validate_import_session_blocks_intervals_within_tolerance_pass(tmp_path):
    """Alpha 7: a sub-second boundary difference is within the 1s tolerance."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        # +0.5s on start, -0.5s on end — both inside ±1s.
                        {"start": "2026-06-01T21:59:59.500000", "end": "2026-06-01T23:00:00.500000"},
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"},
                    ]
                }
            }
        },
    )

    result = validate_import(fixture, run=_two_block_run())

    assert result.passed, result.failures


def test_validate_import_session_blocks_intervals_count_mismatch_fails(tmp_path):
    """Alpha 7: expecting two intervals when the run has one is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        {"start": "2026-06-01T22:00:00", "end": "2026-06-01T23:00:00"},
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"},
                    ]
                }
            }
        },
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
        "session_blocks.2026-06-01.intervals" in f and "interval count mismatch" in f
        for f in result.failures
    ), f"expected an interval-count-mismatch failure, got {result.failures}"


def test_validate_import_session_blocks_interval_start_mismatch_fails(tmp_path):
    """Alpha 7: a start boundary beyond tolerance is a specific start failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        # Start is 5 minutes off; end matches.
                        {"start": "2026-06-01T22:05:00", "end": "2026-06-01T23:00:00"},
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"},
                    ]
                }
            }
        },
    )

    result = validate_import(fixture, run=_two_block_run())

    assert not result.passed
    assert any(
        "session_blocks.2026-06-01.intervals[0].start" in f for f in result.failures
    ), f"expected a start-boundary failure, got {result.failures}"
    # The matching end boundary must not also be flagged.
    assert not any("intervals[0].end" in f for f in result.failures)


def test_validate_import_session_blocks_interval_end_mismatch_fails(tmp_path):
    """Alpha 7: an end boundary beyond tolerance is a specific end failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        {"start": "2026-06-01T22:00:00", "end": "2026-06-01T23:00:00"},
                        # End is 10 minutes off; start matches.
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:40:00"},
                    ]
                }
            }
        },
    )

    result = validate_import(fixture, run=_two_block_run())

    assert not result.passed
    assert any(
        "session_blocks.2026-06-01.intervals[1].end" in f for f in result.failures
    ), f"expected an end-boundary failure, got {result.failures}"


def test_validate_import_session_blocks_interval_invalid_timestamp_fails(tmp_path):
    """Alpha 7: a malformed expected timestamp fails clearly rather than crashing."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [{"start": "not-a-timestamp", "end": "2026-06-01T23:00:00"}]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[_block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0))],
            )
        ]
    )

    result = validate_import(fixture, run=run)  # must not raise

    assert not result.passed
    assert any(
        "intervals[0].start" in f and "invalid expected timestamp" in f
        for f in result.failures
    ), f"expected an invalid-timestamp failure, got {result.failures}"


def test_validate_import_session_blocks_interval_bad_shape_fails(tmp_path):
    """Alpha 7: an interval missing 'start'/'end' is a clear shape failure, not a skip."""
    fixture = _write_import_manifest(
        tmp_path,
        {"session_blocks": {"2026-06-01": {"intervals": [{"start": "2026-06-01T22:00:00"}]}}},
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
        "intervals[0]" in f and "unexpected interval shape" in f for f in result.failures
    ), f"expected a shape failure, got {result.failures}"


def test_validate_import_session_blocks_intervals_missing_date_fails(tmp_path):
    """Alpha 7: intervals for a date absent from the run is a failure, not a skip."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-09": {
                    "intervals": [{"start": "2026-06-09T22:00:00", "end": "2026-06-09T23:00:00"}]
                }
            }
        },
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", blocks=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.session_blocks.2026-06-09" in f and "date not found" in f
        for f in result.failures
    ), f"expected a missing-date failure, got {result.failures}"


def test_validate_import_session_blocks_intervals_tz_awareness_mismatch_fails(tmp_path):
    """Alpha 7: a tz-aware expected boundary vs a naive actual block fails clearly.

    The harness must not invent a timezone conversion. When the manifest carries a
    tz-aware ISO string but the normalized block is naive machine-local, the
    boundary is reported as an explicit, non-crashing failure.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        {"start": "2026-06-01T22:00:00+00:00", "end": "2026-06-01T23:00:00+00:00"}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[_block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0))],
            )
        ]
    )

    result = validate_import(fixture, run=run)  # must not raise

    assert not result.passed
    assert any(
        "intervals[0].start" in f and "naive and timezone-aware" in f
        for f in result.failures
    ), f"expected a tz-awareness failure, got {result.failures}"


def test_validate_import_session_blocks_intervals_sorted_by_start_end_key(tmp_path):
    """Alpha 7: actual blocks are sorted by (start, end, source key) before comparison.

    The run lists its two blocks out of chronological order; the expected
    intervals are listed chronologically. The comparator canonically sorts the
    actual blocks, so the match still holds — documenting that expected intervals
    are compared in listed (chronological) order against sorted actuals.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "session_blocks": {
                "2026-06-01": {
                    "intervals": [
                        {"start": "2026-06-01T22:00:00", "end": "2026-06-01T23:00:00"},
                        {"start": "2026-06-01T23:15:00", "end": "2026-06-02T00:30:00"},
                    ]
                }
            }
        },
    )
    # Blocks emitted later-first; the comparator must sort them.
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[
                    _block(datetime(2026, 6, 1, 23, 15), datetime(2026, 6, 2, 0, 30)),
                    _block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0)),
                ],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


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


def test_validate_import_settings_bare_value_key_is_skipped(tmp_path):
    """Alpha 7: a bare setting key (not under ``values``) is skipped, not silently passed.

    Per-setting comparison now lives under the nested ``values`` key, so a setting
    name placed directly beside ``snapshot_count``/``present`` is an unsupported
    key and is surfaced as a clear skip (never a fabricated pass).
    """
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"therapy_mode": "apap"}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[])])

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert any(
        "expected.import.settings.2026-06-01.therapy_mode" in s
        and "use 'values'" in s
        for s in result.skipped
    ), f"expected a bare-setting-key skip, got {result.skipped}"


def test_validate_import_settings_values_pass(tmp_path):
    """Alpha 7: per-setting values under ``values`` compare against the snapshot."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "settings": {
                "2026-06-01": {
                    "snapshot_count": 1,
                    "present": True,
                    "values": {
                        "therapy_mode": "apap",
                        "minimum_pressure_cm_h2o": 4.0,
                        "maximum_pressure_cm_h2o": 15.0,
                        "epr_enabled": False,
                    },
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                settings=[
                    _settings_snapshot(
                        therapy_mode="apap",
                        minimum_pressure_cm_h2o=4.0,
                        maximum_pressure_cm_h2o=15.0,
                        epr_enabled=False,
                    )
                ],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures
    assert not any("expected.import.settings.2026-06-01.values" in s for s in result.skipped)


def test_validate_import_settings_values_missing_key_fails(tmp_path):
    """Alpha 7: an expected (non-null) key absent from the snapshot is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"ramp_mode": "auto"}}}}
    )
    run = _fake_run(
        sessions=[_fake_session("2026-06-01", settings=[_settings_snapshot(therapy_mode="apap")])]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values.ramp_mode" in f
        and "missing expected key" in f
        for f in result.failures
    ), f"expected a missing-key failure, got {result.failures}"


def test_validate_import_settings_values_value_mismatch_fails(tmp_path):
    """Alpha 7: a differing value is a clear value-mismatch failure."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"therapy_mode": "apap"}}}}
    )
    run = _fake_run(
        sessions=[_fake_session("2026-06-01", settings=[_settings_snapshot(therapy_mode="cpap")])]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values.therapy_mode" in f
        and "value mismatch" in f
        for f in result.failures
    ), f"expected a value-mismatch failure, got {result.failures}"


def test_validate_import_settings_values_null_means_missing(tmp_path):
    """Alpha 7: expected ``null`` is satisfied by an absent key or a present ``None``.

    Pins the missing-vs-off semantics: ``null`` = missing. An absent key passes,
    a present ``None`` passes, but a fabricated ``0``/``false``/``off`` fails.
    """
    # Absent key passes; present-None passes.
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"epr_level": None, "ramp_mode": None}}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                # ramp_mode present-but-None; epr_level absent entirely.
                settings=[_settings_snapshot(ramp_mode=None)],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_settings_values_null_rejects_fabricated_off(tmp_path):
    """Alpha 7: expected ``null`` (missing) is NOT satisfied by a real ``0``/``off`` value."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"epr_level": None}}}}
    )
    run = _fake_run(
        sessions=[_fake_session("2026-06-01", settings=[_settings_snapshot(epr_level=0)])]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values.epr_level" in f
        and "expected missing/null" in f
        for f in result.failures
    ), f"expected a missing/null failure, got {result.failures}"


def test_validate_import_settings_values_present_true_but_none_fails(tmp_path):
    """Alpha 7: present=True with no snapshot is a clear failure (not faked)."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"present": True}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.present" in f for f in result.failures
    ), f"expected a present failure, got {result.failures}"


def test_validate_import_settings_values_present_false_but_exists_fails(tmp_path):
    """Alpha 7: present=False while a snapshot exists is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"present": False}}}
    )
    run = _fake_run(
        sessions=[_fake_session("2026-06-01", settings=[_settings_snapshot(therapy_mode="apap")])]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.present" in f for f in result.failures
    ), f"expected a present failure, got {result.failures}"


def test_validate_import_settings_values_no_snapshot_fails(tmp_path):
    """Alpha 7: requesting ``values`` with no snapshot produced is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"therapy_mode": "apap"}}}}
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values" in f
        and "no settings snapshot" in f
        for f in result.failures
    ), f"expected a no-snapshot failure, got {result.failures}"


def test_validate_import_settings_values_multiple_snapshots_resolve_latest(tmp_path):
    """Alpha 7: with several snapshots, the latest effective_at ≤ session start wins.

    The session starts at 22:00; two snapshots are effective at 20:00 and 21:30
    (both ≤ start). The 21:30 snapshot is selected, so its therapy_mode is the one
    compared.
    """
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"therapy_mode": "apap"}}}}
    )
    early = SettingsSnapshot(
        effective_at=datetime(2026, 6, 1, 20, 0),
        settings={"therapy_mode": "cpap"},
        source_names={},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )
    late = SettingsSnapshot(
        effective_at=datetime(2026, 6, 1, 21, 30),
        settings={"therapy_mode": "apap"},
        source_names={},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[early, late])])

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_settings_values_ambiguous_snapshot_fails(tmp_path):
    """Alpha 7: several snapshots with none effective at/before start fails clearly."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"therapy_mode": "apap"}}}}
    )
    # Both snapshots are effective AFTER the 22:00 session start, so none qualifies.
    after_a = SettingsSnapshot(
        effective_at=datetime(2026, 6, 1, 22, 30),
        settings={"therapy_mode": "apap"},
        source_names={},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )
    after_b = SettingsSnapshot(
        effective_at=datetime(2026, 6, 1, 23, 0),
        settings={"therapy_mode": "cpap"},
        source_names={},
        source_file_ids=(),
        confidence=Confidence.PROBABLE,
    )
    run = _fake_run(sessions=[_fake_session("2026-06-01", settings=[after_a, after_b])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values" in f
        and "ambiguous settings snapshot" in f
        for f in result.failures
    ), f"expected an ambiguous-snapshot failure, got {result.failures}"


def test_validate_import_settings_values_float_within_tolerance_pass(tmp_path):
    """Alpha 7: a float value within 1e-6 passes."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"minimum_pressure_cm_h2o": 4.0}}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", settings=[_settings_snapshot(minimum_pressure_cm_h2o=4.0000001)]
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_settings_values_float_beyond_tolerance_fails(tmp_path):
    """Alpha 7: a float value beyond 1e-6 is a clear value mismatch."""
    fixture = _write_import_manifest(
        tmp_path, {"settings": {"2026-06-01": {"values": {"minimum_pressure_cm_h2o": 4.0}}}}
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", settings=[_settings_snapshot(minimum_pressure_cm_h2o=4.5)]
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.settings.2026-06-01.values.minimum_pressure_cm_h2o" in f
        and "value mismatch" in f
        for f in result.failures
    ), f"expected a float value-mismatch failure, got {result.failures}"


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
# expected.import.events
# ---------------------------------------------------------------------------


def _events_run(date_key="2026-06-01"):
    """A run whose one session has 3 events: 1 obstructive_apnea, 2 hypopnea."""
    return _fake_run(
        sessions=[
            _fake_session(
                date_key,
                events=[
                    _event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0),
                    _event("hypopnea", datetime(2026, 6, 1, 23, 15), 18.0),
                    _event("hypopnea", datetime(2026, 6, 2, 0, 5), 20.0),
                ],
            )
        ]
    )


def test_validate_import_events_count_pass(tmp_path):
    """Alpha 7: total event count is compared against the normalized run."""
    fixture = _write_import_manifest(tmp_path, {"events": {"2026-06-01": {"count": 3}}})

    result = validate_import(fixture, run=_events_run())

    assert result.passed, result.failures
    assert not any("expected.import.events" in s for s in result.skipped)


def test_validate_import_events_count_fail(tmp_path):
    """Alpha 7: a wrong total event count is a clear failure."""
    fixture = _write_import_manifest(tmp_path, {"events": {"2026-06-01": {"count": 5}}})

    result = validate_import(fixture, run=_events_run())

    assert not result.passed
    assert any(
        "expected.import.events.2026-06-01.count" in f for f in result.failures
    ), f"expected a count failure, got {result.failures}"


def test_validate_import_events_type_counts_pass(tmp_path):
    """Alpha 7: per-type event tallies are compared."""
    fixture = _write_import_manifest(
        tmp_path,
        {"events": {"2026-06-01": {"types": {"obstructive_apnea": 1, "hypopnea": 2}}}},
    )

    result = validate_import(fixture, run=_events_run())

    assert result.passed, result.failures


def test_validate_import_events_type_counts_fail(tmp_path):
    """Alpha 7: a wrong per-type tally is a clear type-count-mismatch failure."""
    fixture = _write_import_manifest(
        tmp_path, {"events": {"2026-06-01": {"types": {"hypopnea": 5}}}}
    )

    result = validate_import(fixture, run=_events_run())

    assert not result.passed
    assert any(
        "expected.import.events.2026-06-01.types.hypopnea" in f
        and "type count mismatch" in f
        for f in result.failures
    ), f"expected a type-count-mismatch failure, got {result.failures}"


def test_validate_import_events_ordered_list_pass(tmp_path):
    """Alpha 7: an ordered event list matches the canonically sorted actual events.

    The run emits its events out of chronological order; the comparator sorts the
    actual events, so the chronologically listed expected events still match.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "count": 2,
                    "events": [
                        {"type": "obstructive_apnea", "start": "2026-06-01T22:10:00", "duration_seconds": 12.0},
                        {"type": "hypopnea", "start": "2026-06-01T23:15:00", "duration_seconds": 18.0},
                    ],
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                events=[
                    # Emitted later-first; the comparator must sort.
                    _event("hypopnea", datetime(2026, 6, 1, 23, 15), 18.0),
                    _event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0),
                ],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_events_list_length_mismatch_fail(tmp_path):
    """Alpha 7: an event list length mismatch is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "hypopnea", "start": "2026-06-01T23:15:00"}
                    ]
                }
            }
        },
    )

    result = validate_import(fixture, run=_events_run())  # run has 3 events

    assert not result.passed
    assert any(
        "expected.import.events.2026-06-01.events" in f
        and "event list length mismatch" in f
        for f in result.failures
    ), f"expected a length-mismatch failure, got {result.failures}"


def test_validate_import_events_type_in_list_mismatch_fail(tmp_path):
    """Alpha 7: a per-event type mismatch in the ordered list is a clear failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "clear_airway", "start": "2026-06-01T22:10:00", "duration_seconds": 12.0}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                events=[_event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0)],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0].type" in f and "event type mismatch" in f
        for f in result.failures
    ), f"expected an event-type-mismatch failure, got {result.failures}"


def test_validate_import_events_start_mismatch_fail(tmp_path):
    """Alpha 7: a start beyond the 1s tolerance is a clear start-mismatch failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        # 5 minutes off.
                        {"type": "obstructive_apnea", "start": "2026-06-01T22:15:00", "duration_seconds": 12.0}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                events=[_event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0)],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0].start" in f and "event start mismatch" in f
        for f in result.failures
    ), f"expected a start-mismatch failure, got {result.failures}"


def test_validate_import_events_start_within_tolerance_pass(tmp_path):
    """Alpha 7: a sub-second start difference is within the 1s tolerance."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "obstructive_apnea", "start": "2026-06-01T22:10:00.400000", "duration_seconds": 12.0}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                events=[_event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0)],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_events_duration_mismatch_fail(tmp_path):
    """Alpha 7: a duration beyond the 1s tolerance is a clear duration-mismatch failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "obstructive_apnea", "start": "2026-06-01T22:10:00", "duration_seconds": 30.0}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01",
                events=[_event("obstructive_apnea", datetime(2026, 6, 1, 22, 10), 12.0)],
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0].duration_seconds" in f and "duration mismatch" in f
        for f in result.failures
    ), f"expected a duration-mismatch failure, got {result.failures}"


def test_validate_import_events_null_duration_pass(tmp_path):
    """Alpha 7: expected null duration is satisfied by an actual None duration."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "rera", "start": "2026-06-01T22:10:00", "duration_seconds": None}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", events=[_event("rera", datetime(2026, 6, 1, 22, 10), None)]
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert result.passed, result.failures


def test_validate_import_events_invalid_timestamp_fail(tmp_path):
    """Alpha 7: a malformed expected timestamp fails cleanly, never crashes."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [{"type": "hypopnea", "start": "not-a-timestamp"}]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", events=[_event("hypopnea", datetime(2026, 6, 1, 22, 10), 12.0)]
            )
        ]
    )

    result = validate_import(fixture, run=run)  # must not raise

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0].start" in f and "invalid expected timestamp" in f
        for f in result.failures
    ), f"expected an invalid-timestamp failure, got {result.failures}"


def test_validate_import_events_malformed_object_fail(tmp_path):
    """Alpha 7: an expected event missing required keys is a clear malformed-object failure."""
    fixture = _write_import_manifest(
        tmp_path,
        {"events": {"2026-06-01": {"events": [{"start": "2026-06-01T22:10:00"}]}}},
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", events=[_event("hypopnea", datetime(2026, 6, 1, 22, 10), 12.0)]
            )
        ]
    )

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0]" in f and "malformed expected event object" in f
        for f in result.failures
    ), f"expected a malformed-object failure, got {result.failures}"


def test_validate_import_events_missing_date_fail(tmp_path):
    """Alpha 7: events for a date absent from the run is a failure, not a skip."""
    fixture = _write_import_manifest(tmp_path, {"events": {"2026-06-09": {"count": 1}}})
    run = _fake_run(sessions=[_fake_session("2026-06-01", events=[])])

    result = validate_import(fixture, run=run)

    assert not result.passed
    assert any(
        "expected.import.events.2026-06-09" in f and "date not found" in f
        for f in result.failures
    ), f"expected a missing-date failure, got {result.failures}"


def test_validate_import_events_tz_awareness_mismatch_fail(tmp_path):
    """Alpha 7: a tz-aware expected start vs a naive actual event fails clearly."""
    fixture = _write_import_manifest(
        tmp_path,
        {
            "events": {
                "2026-06-01": {
                    "events": [
                        {"type": "hypopnea", "start": "2026-06-01T22:10:00+00:00", "duration_seconds": 12.0}
                    ]
                }
            }
        },
    )
    run = _fake_run(
        sessions=[
            _fake_session(
                "2026-06-01", events=[_event("hypopnea", datetime(2026, 6, 1, 22, 10), 12.0)]
            )
        ]
    )

    result = validate_import(fixture, run=run)  # must not raise

    assert not result.passed
    assert any(
        "events.2026-06-01.events[0].start" in f and "naive and timezone-aware" in f
        for f in result.failures
    ), f"expected a tz-awareness failure, got {result.failures}"


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


def test_validate_import_oscar_reference_hash_pinned_on_committed_airsense10_fixture():
    """Phase 2: the committed AirSense 10 manifest pins its OSCAR export hash.

    This is the **first committed-fixture-backed** ``expected.import`` coverage
    (every other ``validate_import`` comparator is exercised injected-only — by a
    ``_fake_run`` / a ``tmp_path`` manifest / synthetic DB rows). Here
    ``validate_import`` reads the *committed* anonymized fixture's
    ``expected.import.oscar_reference`` and verifies the sha256 of the *committed*
    ``oscar_reference/summary.csv`` — parser-free.

    A trivial run is injected so the check never depends on ``cpap-parser`` /
    ``cpap-py`` or an expensive real multi-night parse: the hash check reads only
    the committed manifest + committed reference file. This asserts **reference
    file integrity**, NOT a capability-``validated`` claim; numeric parity stays a
    skip. No PHI is exposed — the assertion is over a sha256 of a redistributable
    export (plan §11).
    """
    manifest = json.loads((AIRSENSE10_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    oscar_ref = manifest["expected"]["import"]["oscar_reference"]
    assert oscar_ref["export_hash"].startswith("sha256:"), "fixture must pin a sha256 export hash"
    assert oscar_ref["summary_csv"] == "oscar_reference/summary.csv"

    # Inject a trivial run so no real parse is attempted; the oscar_reference hash
    # check is parser-free and reads the committed file regardless.
    result = validate_import(AIRSENSE10_FIXTURE, run=SimpleNamespace(warnings=[], sessions=[]))

    assert result.passed, result.failures
    # The hash was actually verified — a mismatch/missing file would be a failure,
    # and the path-present branch means it was not skipped for "no reference file".
    assert not any("oscar_reference" in f for f in result.failures), result.failures
    assert not any("no reference file path" in s for s in result.skipped), result.skipped
    # Numeric parity is explicitly deferred, never silently passed.
    assert any("oscar_reference.parity" in s for s in result.skipped), result.skipped


def test_validate_import_oscar_reference_sessions_csv_hash_pinned_on_committed_airsense10_fixture():
    """Phase 2: the committed AirSense 10 manifest also pins its sessions.csv hash.

    ``summary.csv`` (per-day) was the first committed-fixture-backed
    ``oscar_reference`` pin; its twin ``sessions.csv`` (per-session) is now pinned
    too, via the manifest's ``files`` list. ``validate_import`` verifies the sha256
    of the *committed* ``oscar_reference/sessions.csv`` parser-free — an integrity
    pin over a redistributable, anonymized export, not a capability-``validated``
    claim. A trivial run is injected so the check never depends on ``cpap-parser`` /
    ``cpap-py``; numeric parity stays a skip.
    """
    manifest = json.loads((AIRSENSE10_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["expected"]["import"]["oscar_reference"]["files"]
    sessions_pin = next(f for f in files if f["file"] == "oscar_reference/sessions.csv")
    assert sessions_pin["export_hash"].startswith("sha256:"), "sessions.csv must pin a sha256"

    result = validate_import(AIRSENSE10_FIXTURE, run=SimpleNamespace(warnings=[], sessions=[]))

    assert result.passed, result.failures
    # Both committed reference files were hash-verified — no oscar_reference failure
    # and no "reference file not found" / "no reference file path" skip.
    assert not any("oscar_reference" in f for f in result.failures), result.failures
    assert not any("no reference file path" in s for s in result.skipped), result.skipped
    assert not any("reference file not found" in f for f in result.failures), result.failures


def test_validate_import_oscar_reference_additional_files_hash_mismatch_fails(tmp_path):
    """Phase 2: a wrong hash in the ``files`` list is a real failure, like the legacy pin.

    Proves the additional-reference path is verified, not merely accepted: the
    top-level ``summary_csv`` matches, but a bogus ``files`` entry hash for
    ``sessions.csv`` must fail the whole block.
    """
    summary = b"Date,AHI\n2026-06-01,1.0\n"
    sessions = b"Date,Session,AHI\n2026-06-01,1,1.0\n"
    fixture = _write_import_manifest(
        tmp_path,
        {
            "oscar_reference": {
                "summary_csv": "oscar_reference/summary.csv",
                "export_hash": "sha256:" + hashlib.sha256(summary).hexdigest(),
                "files": [
                    {
                        "file": "oscar_reference/sessions.csv",
                        "export_hash": "sha256:" + "0" * 64,
                    }
                ],
            }
        },
    )
    _write_reference_csv(fixture, summary, "oscar_reference/summary.csv")
    _write_reference_csv(fixture, sessions, "oscar_reference/sessions.csv")

    result = validate_import(fixture)

    assert not result.passed
    assert any(
        "oscar_reference.export_hash" in f and "sessions.csv" in f for f in result.failures
    ), result.failures


def test_validate_import_oscar_reference_additional_files_missing_file_fails(tmp_path):
    """Phase 2: a ``files`` entry naming an absent reference file is a failure, not a skip."""
    summary = b"Date,AHI\n2026-06-01,1.0\n"
    fixture = _write_import_manifest(
        tmp_path,
        {
            "oscar_reference": {
                "summary_csv": "oscar_reference/summary.csv",
                "export_hash": "sha256:" + hashlib.sha256(summary).hexdigest(),
                "files": [
                    {
                        "file": "oscar_reference/sessions.csv",
                        "export_hash": "sha256:" + "0" * 64,
                    }
                ],
            }
        },
    )
    _write_reference_csv(fixture, summary, "oscar_reference/summary.csv")  # sessions.csv absent

    result = validate_import(fixture)

    assert not result.passed
    assert any(
        "reference file not found" in f and "sessions.csv" in f for f in result.failures
    ), result.failures


def test_summarize_import_blocks_classifies_passed_skipped_failed(tmp_path):
    """Phase 2 clarity: per-block status distinguishes passed / skipped / failed.

    A reviewer cannot tell from the result alone whether a block was checked-and-
    passed (absent from both failures and skipped) or simply not requested.
    ``summarize_import_blocks`` reads the requested blocks from the manifest and
    labels each: ``warnings`` passes, a bare-key ``settings`` is gated to a skip,
    and a wrong ``session_blocks.block_count`` fails.
    """
    fixture = _write_import_manifest(
        tmp_path,
        {
            "warnings": {"codes": ["resmed_summary_only_day"]},
            "settings": {"2026-06-01": {"therapy_mode": "apap"}},  # bare key → skip
            "session_blocks": {"2026-06-01": {"block_count": 9}},  # wrong → fail
        },
    )
    run = _fake_run(
        warnings=[ImportWarning(code="resmed_summary_only_day", severity="info", message="x")],
        sessions=[
            _fake_session(
                "2026-06-01",
                blocks=[_block(datetime(2026, 6, 1, 22, 0), datetime(2026, 6, 1, 23, 0))],
            )
        ],
    )

    result = validate_import(fixture, run=run)
    statuses = summarize_import_blocks(fixture, result)

    assert statuses == {
        "warnings": "passed",
        "settings": "skipped",
        "session_blocks": "failed",
    }


def test_summarize_import_blocks_oscar_reference_skipped_when_parity_deferred():
    """Phase 2 clarity: oscar_reference reads 'skipped' even when its hash verifies.

    On the committed AirSense 10 fixture the export-hash check passes (no failure),
    but the deferred numeric-parity sub-check always skips — so the block-level
    label is the honest ``"skipped"`` ("not every sub-check ran"), never ``"failed"``.
    """
    result = validate_import(AIRSENSE10_FIXTURE, run=SimpleNamespace(warnings=[], sessions=[]))
    statuses = summarize_import_blocks(AIRSENSE10_FIXTURE, result)

    assert statuses == {"oscar_reference": "skipped"}
    assert result.passed, result.failures


def test_summarize_import_blocks_empty_without_import_block():
    """Phase 2 clarity: a fixture with no expected.import yields an empty summary."""
    fixture = FIXTURE_ROOT / "synthetic-resmed-minimal"

    result = validate_import(fixture)
    assert summarize_import_blocks(fixture, result) == {}


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


# ---------------------------------------------------------------------------
# expected.import.identity_hashes (DB-gated; skips cleanly without a database)
# ---------------------------------------------------------------------------


def test_validate_import_identity_hashes_skips_without_conn(tmp_path):
    """Plan Step 4: identity_hashes skips clearly when no connection is supplied.

    Runs with no database — pins the backward-compatible default that the DB
    block is gated, not silently passed.
    """
    fixture = _write_import_manifest(tmp_path, {"identity_hashes": {"sessions": "x"}})

    result = validate_import(fixture)  # conn=None

    assert result.passed
    assert any(
        "expected.import.identity_hashes" in s and "no database connection" in s
        for s in result.skipped
    ), result.skipped


def test_validate_import_identity_hashes_skips_without_machine_scope(tmp_path):
    """Plan Step 4: with a conn but no machine_id there is nothing to scope to.

    Uses a dummy non-None ``conn`` (never touched, since the block skips before
    any query) so this stays a pure no-DB unit test.
    """
    fixture = _write_import_manifest(tmp_path, {"identity_hashes": {"sessions": "x"}})

    result = validate_import(fixture, conn=object(), machine_id=None)

    assert result.passed
    assert any(
        "expected.import.identity_hashes" in s and "no machine scope" in s
        for s in result.skipped
    ), result.skipped


# -- DB-gated tests below. The db/test_user fixtures pytest.skip when no
# -- TEST_DATABASE_URL is configured, so these never require a live database for
# -- the normal local suite. They mirror the persistence pattern in
# -- tests/test_resmed_import_regressions.py and read identities back read-only.


def _db_session_row(**overrides):
    """Minimal ``upsert_session`` payload for identity-hash DB tests."""
    row = {
        "session_id": "sess",
        "folder_date": date(2026, 6, 1),
        "block_index": 0,
        "start_datetime": datetime(2026, 6, 1, 22, 0, tzinfo=UTC),
        "pld_start_datetime": datetime(2026, 6, 1, 22, 0, tzinfo=UTC),
        "duration_seconds": 3600,
        "device_serial": "SN-FIXTURE",
        "ahi": 1.0,
        "central_apnea_count": 0,
        "obstructive_apnea_count": 0,
        "hypopnea_count": 0,
        "apnea_count": 0,
        "arousal_count": 0,
        "total_ahi_events": 0,
        "avg_pressure": None,
        "p95_pressure": None,
        "avg_leak": None,
        "avg_resp_rate": None,
        "avg_tidal_vol": None,
        "avg_min_vent": None,
        "avg_snore": None,
        "avg_flow_lim": None,
        "has_spo2": False,
        "therapy_mode": None,
        "mask_type": None,
        "humidity_level": None,
        "temperature_c": None,
        "machine_tz": "UTC",
        "manufacturer": "ResMed",
        "provenance_status": "native_resmed_cpap_parser",
    }
    row.update(overrides)
    return row


def _new_machine_and_run(raw_conn, *, user_id, serial):
    """Reconcile a machine and open an import_run; return ``(machine_id, run_id)``."""
    machine_id = importer_db.reconcile_machine(
        raw_conn,
        user_id=user_id,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number=serial,
    )
    with raw_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, source_type, source_fingerprint,
                status, validation_status, started_at
            ) VALUES (%s, %s, 'resmed-native-v2', 'directory', %s, 'running', 'partial', NOW())
            RETURNING id::text
            """,
            (user_id, machine_id, f"idhash-{uuid.uuid4().hex}"),
        )
        run_id = cur.fetchone()[0]
    return machine_id, run_id


def _persist_nights(raw_conn, *, user_id, machine_id, import_run_id, nights):
    """Persist ``nights`` = list of ``(session_key, session_id, [block_keys])``.

    Uses the same idempotent upserts as the production persist path, so calling
    it twice with the same nights is a duplicate re-import (no new rows).
    """
    for session_key, session_id, block_keys in nights:
        session_db_id = importer_db.upsert_session(
            raw_conn,
            _db_session_row(
                user_id=user_id,
                machine_id=machine_id,
                import_run_id=import_run_id,
                source_session_key=session_key,
                session_id=session_id,
            ),
        )
        for bk in block_keys:
            importer_db.upsert_session_block(
                raw_conn,
                session_db_id=str(session_db_id),
                import_run_id=import_run_id,
                source_block_key=bk,
                start_datetime=datetime(2026, 6, 1, 22, 0, tzinfo=UTC),
                end_datetime=datetime(2026, 6, 1, 23, 0, tzinfo=UTC),
                source_file_ids=[],
                source_kind="resmed_str_mask_interval",
                therapy_duration_seconds=3600,
            )


def test_validate_import_identity_hashes_match_persisted(db, test_user, tmp_path):
    """Plan Step 4: persisted session/block identity hashes + counts are compared."""
    raw_conn = db.connection().connection.driver_connection
    machine_id, run_id = _new_machine_and_run(raw_conn, user_id=test_user["id"], serial="ID-HASH-MATCH")
    _persist_nights(
        raw_conn,
        user_id=test_user["id"],
        machine_id=machine_id,
        import_run_id=run_id,
        nights=[("resmed:IDM:2026-06-01", "idm_20260601", ["resmed:IDM:2026-06-01:0", "resmed:IDM:2026-06-01:1"])],
    )
    snap = persisted_identity_snapshot(raw_conn, machine_id=machine_id)

    fixture = _write_import_manifest(
        tmp_path,
        {
            "identity_hashes": {
                "algorithm": "sha256",
                "sessions": f"sha256:{snap['sessions']}",  # prefix tolerated
                "blocks": snap["blocks"],                  # bare hex tolerated
                "session_count": 1,
                "block_count": 2,
            }
        },
    )

    result = validate_import(fixture, conn=raw_conn, machine_id=machine_id)

    assert result.passed, result.failures
    # The block was actually checked (no bare block-level skip).
    assert not any(s.startswith("expected.import.identity_hashes:") for s in result.skipped)


def test_validate_import_identity_hashes_mismatch_fails(db, test_user, tmp_path):
    """Plan Step 4: a wrong persisted-identity hash is a real failure."""
    raw_conn = db.connection().connection.driver_connection
    machine_id, run_id = _new_machine_and_run(raw_conn, user_id=test_user["id"], serial="ID-HASH-MISMATCH")
    _persist_nights(
        raw_conn,
        user_id=test_user["id"],
        machine_id=machine_id,
        import_run_id=run_id,
        nights=[("resmed:IDX:2026-06-01", "idx_20260601", ["resmed:IDX:2026-06-01:0"])],
    )

    fixture = _write_import_manifest(tmp_path, {"identity_hashes": {"sessions": "0" * 64}})

    result = validate_import(fixture, conn=raw_conn, machine_id=machine_id)

    assert not result.passed
    assert any(
        "expected.import.identity_hashes.sessions" in f for f in result.failures
    ), result.failures


def test_validate_import_identity_hashes_stable_across_duplicate_import(db, test_user):
    """Plan Step 4: a duplicate re-import leaves persisted identities unchanged.

    Persist a night, snapshot its identity hashes, then re-import the *same*
    night via the idempotent upserts. The session/block source-key sets, their
    hashes, and counts must be byte-for-byte identical — no duplicate rows.
    """
    raw_conn = db.connection().connection.driver_connection
    machine_id, run_id = _new_machine_and_run(raw_conn, user_id=test_user["id"], serial="ID-HASH-DUP")
    nights = [("resmed:DUP:2026-06-01", "dup_20260601", ["resmed:DUP:2026-06-01:0", "resmed:DUP:2026-06-01:1"])]

    _persist_nights(raw_conn, user_id=test_user["id"], machine_id=machine_id, import_run_id=run_id, nights=nights)
    snap1 = persisted_identity_snapshot(raw_conn, machine_id=machine_id)

    # Duplicate import of the identical night.
    _persist_nights(raw_conn, user_id=test_user["id"], machine_id=machine_id, import_run_id=run_id, nights=nights)
    snap2 = persisted_identity_snapshot(raw_conn, machine_id=machine_id)

    assert snap1 == snap2, "duplicate import must not change persisted identities"
    assert snap1["session_count"] == 1
    assert snap1["block_count"] == 2


def test_validate_import_identity_hashes_incremental_preserves_first_night(db, test_user):
    """Plan Step 4: adding a newer night leaves the first night's identity intact.

    Persist night A, snapshot; then import a card containing A *and* a newer
    night B. A's source key must still be present, A's session row id unchanged,
    and only B's session/block added (counts +1 each). The combined set hash
    necessarily grows, which is the expected incremental behavior — not churn.
    """
    raw_conn = db.connection().connection.driver_connection
    machine_id, run_id = _new_machine_and_run(raw_conn, user_id=test_user["id"], serial="ID-HASH-INC")

    night_a = ("resmed:INC:2026-06-01", "inc_20260601", ["resmed:INC:2026-06-01:0"])
    night_b = ("resmed:INC:2026-06-02", "inc_20260602", ["resmed:INC:2026-06-02:0"])

    _persist_nights(raw_conn, user_id=test_user["id"], machine_id=machine_id, import_run_id=run_id, nights=[night_a])
    snap_a = persisted_identity_snapshot(raw_conn, machine_id=machine_id)

    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM sessions WHERE machine_id = %s AND source_session_key = %s",
            (machine_id, "resmed:INC:2026-06-01"),
        )
        a_id_before = cur.fetchone()[0]

    # Incremental re-import: the card now contains both nights.
    _persist_nights(
        raw_conn, user_id=test_user["id"], machine_id=machine_id, import_run_id=run_id, nights=[night_a, night_b]
    )
    snap_b = persisted_identity_snapshot(raw_conn, machine_id=machine_id)

    # A's identity is preserved and only B was added.
    assert "resmed:INC:2026-06-01" in snap_b["session_keys"]
    assert set(snap_a["session_keys"]).issubset(set(snap_b["session_keys"]))
    assert snap_b["session_count"] == snap_a["session_count"] + 1
    assert snap_b["block_count"] == snap_a["block_count"] + 1

    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM sessions WHERE machine_id = %s AND source_session_key = %s",
            (machine_id, "resmed:INC:2026-06-01"),
        )
        a_id_after = cur.fetchone()[0]
    assert a_id_after == a_id_before, "first night's session row id must be stable"

    # The combined-set hash grows (B added) — expected, not churn.
    assert snap_b["sessions"] != snap_a["sessions"]
