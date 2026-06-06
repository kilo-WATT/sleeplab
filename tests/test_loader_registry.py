"""Conformance tests for the executable SleepLab 2.0 loader prototype."""

import json
from pathlib import Path

import pytest

from importer.loaders import LoaderRegistry, create_default_registry, inspect_source_root
from importer.loaders.__main__ import main
from importer.loaders.base import LoaderAdapter
from importer.loaders.models import (
    Capabilities,
    Confidence,
    DetectedDevice,
    DetectionEvidence,
    MachineIdentity,
)


@pytest.fixture
def registry() -> LoaderRegistry:
    return create_default_registry()


def test_default_registry_has_stable_vendor_order(registry: LoaderRegistry):
    assert [adapter.adapter_id for adapter in registry.adapters] == [
        "resmed-native-v2",
        "philips-prs1-v2",
        "lowenstein-v2",
        "fisher-paykel-v2",
        "bmc-v2",
    ]


def test_resmed_detects_root_and_peeks_json_identity(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "DATALOG").mkdir()
    (tmp_path / "STR.edf").write_bytes(b"")
    (tmp_path / "Identification.json").write_text(
        json.dumps(
            {
                "FlowGenerator": {
                    "IdentificationProfiles": {
                        "Product": {
                            "SerialNumber": "TEST-SERIAL-001",
                            "ProductName": "AirSense Test",
                            "ProductCode": "39000",
                            "Series": "AirSense",
                        }
                    },
                    "FirmwareVersion": "test-fw",
                }
            }
        ),
        encoding="utf-8",
    )

    report = registry.detect(tmp_path)

    assert report.matched is True
    assert report.ambiguous is False
    assert len(report.candidates) == 1
    detected = report.candidates[0]
    assert detected.adapter_id == "resmed-native-v2"
    assert detected.confidence == Confidence.EXACT
    adapter = registry.get_adapter(detected.adapter_id)
    identity = adapter.peek_info(detected)
    assert identity.serial_number == "TEST-SERIAL-001"
    assert identity.model == "AirSense Test"
    assert identity.model_number == "39000"


def test_resmed_requires_the_sd_card_root(registry: LoaderRegistry, tmp_path: Path):
    datalog = tmp_path / "DATALOG"
    datalog.mkdir()
    (tmp_path / "STR.edf").write_bytes(b"")

    report = registry.detect(datalog)

    assert report.matched is False
    assert report.warnings[0].code == "unrecognized_source"


def test_inspection_snapshot_uses_explicit_root_and_does_not_import(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "DATALOG").mkdir()
    (tmp_path / "STR.edf").write_bytes(b"")

    snapshot = inspect_source_root(tmp_path, registry)

    assert snapshot["source_root"] == str(tmp_path.resolve())
    assert snapshot["matched"] is True
    assert snapshot["devices"][0]["device_path"] == "."
    assert snapshot["devices"][0]["adapter_id"] == "resmed-native-v2"
    assert snapshot["devices"][0]["capabilities"]["sessions"]["available"] is True


def test_inspection_cli_prints_json_for_source_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "DATALOG").mkdir()
    (tmp_path / "STR.edf").write_bytes(b"")

    exit_code = main([str(tmp_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["source_root"] == str(tmp_path.resolve())
    assert output["devices"][0]["adapter_id"] == "resmed-native-v2"


def test_partial_resmed_layout_reports_missing_summary(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "DATALOG").mkdir()

    detected = registry.detect(tmp_path).candidates[0]

    assert detected.confidence == Confidence.PROBABLE
    assert [warning.code for warning in detected.warnings] == ["resmed_missing_str"]


def test_prs1_enumerates_multiple_machines_and_peeks_text_identity(registry: LoaderRegistry, tmp_path: Path):
    pseries = tmp_path / "p-Series"
    pseries.mkdir()
    first = pseries / "P012345"
    first.mkdir()
    (first / "PROP.TXT").write_text(
        "SerialNumber=PRS1-ONE\nModelNumber=560P\nSoftwareVersion=1.2\n",
        encoding="utf-8",
    )
    second = pseries / "P067890"
    second.mkdir()
    (second / "PROP.BIN").write_bytes(b"encrypted-test-placeholder")

    report = registry.detect(tmp_path)

    assert len(report.candidates) == 2
    assert {candidate.device_key_hint for candidate in report.candidates} == {"P012345", "P067890"}
    assert report.source_root == tmp_path.resolve()
    assert all(candidate.source_root == tmp_path.resolve() for candidate in report.candidates)
    prs1 = next(adapter for adapter in registry.adapters if adapter.adapter_id == "philips-prs1-v2")
    text_candidate = next(candidate for candidate in report.candidates if candidate.device_key_hint == "P012345")
    identity = prs1.peek_info(text_candidate)
    assert identity.serial_number == "PRS1-ONE"
    assert identity.model_number == "560P"


def test_lowenstein_prisma_line_requires_therapy_for_exact_match(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "config.pcfg").write_bytes(b"config")

    partial = registry.detect(tmp_path).candidates[0]
    assert partial.family_hint == "Prisma Line"
    assert partial.confidence == Confidence.PROBABLE

    (tmp_path / "therapy.pdat").write_bytes(b"therapy")
    exact = registry.detect(tmp_path).candidates[0]
    assert exact.confidence == Confidence.EXACT


def test_fisher_paykel_enumerates_sleepstyle_and_icon(registry: LoaderRegistry, tmp_path: Path):
    icon_root = tmp_path / "FPHCARE" / "ICON"
    sleepstyle = icon_root / "TESTSERIAL1"
    sleepstyle.mkdir(parents=True)
    (sleepstyle / "SUM2026.fph").write_bytes(b"one\rtwo\rthree\rfour\rSLEEPSTYLE\r")
    icon = icon_root / "TESTSERIAL2"
    icon.mkdir()
    (icon / "SUM2026.fph").write_bytes(b"one\rtwo\rthree\rfour\rICON\r")

    report = registry.detect(tmp_path)

    assert len(report.candidates) == 2
    assert {candidate.family_hint for candidate in report.candidates} == {"SleepStyle", "ICON"}
    assert {candidate.device_key_hint for candidate in report.candidates} == {"TESTSERIAL1", "TESTSERIAL2"}


def test_bmc_legacy_requires_matching_three_file_fingerprint(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "TEST.USR").write_bytes(b"identity")
    (tmp_path / "TEST.idx").write_bytes(b"index")
    assert not registry.detect(tmp_path).matched

    (tmp_path / "TEST.000").write_bytes(b"waveform")
    detected = registry.detect(tmp_path).candidates[0]
    assert detected.adapter_id == "bmc-v2"
    assert detected.family_hint == "Legacy/G2"


def test_bmc_g3x_is_distinct_from_legacy_layout(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "G3TEST.idx").write_bytes(b"\x00" * 16 + b"RESmart G3" + b"\x00" * 32)
    (tmp_path / "G3TEST.000").write_bytes(b"waveform")

    detected = registry.detect(tmp_path).candidates[0]

    assert detected.family_hint == "G3X"
    assert detected.confidence == Confidence.EXACT


def test_unknown_directory_returns_structured_warning(registry: LoaderRegistry, tmp_path: Path):
    (tmp_path / "notes.txt").write_text("not CPAP data", encoding="utf-8")

    report = registry.detect(tmp_path)

    assert report.matched is False
    assert report.warnings[0].code == "unrecognized_source"


class _SyntheticAdapter(LoaderAdapter):
    def __init__(self, adapter_id: str, confidence: Confidence):
        self.adapter_id = adapter_id
        self.confidence = confidence

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        return [
            DetectedDevice(
                adapter_id=self.adapter_id,
                source_root=source_root,
                device_path=source_root,
                manufacturer_hint=None,
                family_hint=None,
                confidence=self.confidence,
                evidence=(DetectionEvidence("test", ".", "match", "match", 1),),
            )
        ]

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        raise NotImplementedError

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        raise NotImplementedError


def test_registry_marks_similarly_strong_matches_as_ambiguous(tmp_path: Path):
    registry = LoaderRegistry(
        [
            _SyntheticAdapter("specific", Confidence.EXACT),
            _SyntheticAdapter("also-strong", Confidence.STRONG),
            _SyntheticAdapter("weak", Confidence.WEAK),
        ]
    )

    report = registry.detect(tmp_path)

    assert report.ambiguous is True
    exact = next(candidate for candidate in report.candidates if candidate.adapter_id == "specific")
    strong = next(candidate for candidate in report.candidates if candidate.adapter_id == "also-strong")
    weak = next(candidate for candidate in report.candidates if candidate.adapter_id == "weak")
    assert exact.competing_adapter_ids == ("also-strong",)
    assert strong.competing_adapter_ids == ("specific",)
    assert weak.requires_user_choice is False


def test_registry_rejects_duplicate_adapter_ids():
    registry = LoaderRegistry([_SyntheticAdapter("duplicate", Confidence.EXACT)])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_SyntheticAdapter("duplicate", Confidence.STRONG))


def test_registry_rejects_unknown_adapter_lookup(registry: LoaderRegistry):
    with pytest.raises(ValueError, match="Unknown loader adapter"):
        registry.get_adapter("missing")
