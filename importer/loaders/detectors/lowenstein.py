"""Structural Lowenstein/Weinmann detector."""

from pathlib import Path

from ..base import LoaderAdapter, find_child_case_insensitive
from ..models import (
    Capabilities,
    CapabilityStatus,
    Confidence,
    DetectedDevice,
    DetectionEvidence,
    MachineIdentity,
    ValidationStatus,
)


class LowensteinStructuralAdapter(LoaderAdapter):
    """Detect Prisma SMART, Prisma Line, and legacy WM_DATA layouts."""

    adapter_id = "lowenstein-v2"
    priority = 30

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        root = source_root.resolve()
        smart = find_child_case_insensitive(root, "config.pscfg")
        line = find_child_case_insensitive(root, "config.pcfg")
        therapy = find_child_case_insensitive(root, "therapy.pdat")
        legacy = find_child_case_insensitive(root, "WM_DATA.TDF")
        if line:
            evidence = [
                DetectionEvidence(
                    "required_path",
                    line.name,
                    "Prisma Line config archive",
                    "file",
                    60,
                )
            ]
            if therapy:
                evidence.append(
                    DetectionEvidence(
                        "required_path",
                        therapy.name,
                        "Prisma Line therapy archive",
                        "file",
                        40,
                    )
                )
            return [
                self._candidate(
                    root,
                    "Prisma Line",
                    Confidence.EXACT if therapy else Confidence.PROBABLE,
                    evidence,
                )
            ]
        if smart:
            return [
                self._candidate(
                    root,
                    "Prisma SMART",
                    Confidence.STRONG,
                    [
                        DetectionEvidence(
                            "required_path",
                            smart.name,
                            "Prisma SMART config",
                            "file",
                            80,
                        )
                    ],
                )
            ]
        if legacy:
            return [
                self._candidate(
                    root,
                    "Weinmann legacy",
                    Confidence.STRONG,
                    [
                        DetectionEvidence(
                            "required_path",
                            legacy.name,
                            "Legacy WM_DATA",
                            "file",
                            80,
                        )
                    ],
                )
            ]
        return []

    def _candidate(
        self,
        root: Path,
        family: str,
        confidence: Confidence,
        evidence: list[DetectionEvidence],
    ) -> DetectedDevice:
        return DetectedDevice(
            adapter_id=self.adapter_id,
            source_root=root,
            device_path=root,
            manufacturer_hint="Lowenstein",
            family_hint=family,
            confidence=confidence,
            evidence=tuple(evidence),
        )

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        return MachineIdentity(
            manufacturer="Lowenstein",
            family=detected.family_hint,
            model=None,
            model_number=None,
            serial_number=None,
            firmware_version=None,
            data_format_version=None,
            loader_identity=self.adapter_id,
            identity_confidence=Confidence.WEAK,
            source_fields={},
        )

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        is_line = detected.family_hint == "Prisma Line"
        parser_state = ValidationStatus.PARTIAL if is_line else ValidationStatus.UNVALIDATED
        available = CapabilityStatus(True, parser_state, "cpap-parser implementation requires fixture conformance.")
        unavailable = CapabilityStatus(False, ValidationStatus.UNVALIDATED)
        return Capabilities(
            identity=available,
            sessions=available,
            session_blocks=available,
            settings=unavailable,
            events=CapabilityStatus(is_line, parser_state),
            low_rate_signals=CapabilityStatus(is_line, parser_state),
            waveforms=CapabilityStatus(is_line, parser_state),
            oximetry=CapabilityStatus(is_line, parser_state),
            summary_only_days=available,
            source_manifest=unavailable,
            timezone_basis="assumed_utc",
            leak_kinds=("unknown",),
        )
