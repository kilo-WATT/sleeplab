"""Structural legacy BMC and BMC G3X detector."""

from pathlib import Path

from ..base import LoaderAdapter
from ..models import (
    Capabilities,
    CapabilityStatus,
    Confidence,
    DetectedDevice,
    DetectionEvidence,
    MachineIdentity,
    ValidationStatus,
)

_G3X_MARKERS = (b"G3", b"RESmart G3", b"BMC G3")


class BmcStructuralAdapter(LoaderAdapter):
    """Distinguish legacy .USR BMC cards from G3X index layouts."""

    adapter_id = "bmc-v2"
    priority = 50

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        root = source_root.resolve()
        legacy = self._detect_legacy(root)
        if legacy:
            return [legacy]
        g3x = self._detect_g3x(root)
        if g3x:
            return [g3x]
        return []

    def _detect_legacy(self, root: Path) -> DetectedDevice | None:
        for usr in sorted(root.glob("*.USR")):
            base = usr.with_suffix("")
            idx = base.with_suffix(".idx")
            waveform = base.with_suffix(".000")
            if idx.is_file() and waveform.is_file():
                return self._candidate(
                    root,
                    "Legacy/G2",
                    usr.stem,
                    (
                        DetectionEvidence("required_path", usr.name, "BMC .USR", "file", 40),
                        DetectionEvidence("required_path", idx.name, "matching BMC index", "file", 30),
                        DetectionEvidence("required_path", waveform.name, "matching waveform", "file", 30),
                    ),
                )
        return None

    def _detect_g3x(self, root: Path) -> DetectedDevice | None:
        for idx in sorted(root.glob("*.idx")):
            waveform = idx.with_suffix(".000")
            if not waveform.is_file():
                continue
            header = idx.read_bytes()[:256]
            if not any(marker in header for marker in _G3X_MARKERS):
                continue
            return self._candidate(
                root,
                "G3X",
                idx.stem,
                (
                    DetectionEvidence("file_header", idx.name, "BMC G3X index marker", "matched", 70),
                    DetectionEvidence("required_path", waveform.name, "matching waveform", "file", 30),
                ),
            )
        return None

    def _candidate(
        self,
        root: Path,
        family: str,
        key: str,
        evidence: tuple[DetectionEvidence, ...],
    ) -> DetectedDevice:
        return DetectedDevice(
            adapter_id=self.adapter_id,
            source_root=root,
            device_path=root,
            manufacturer_hint="BMC",
            family_hint=family,
            confidence=Confidence.EXACT,
            evidence=evidence,
            device_key_hint=key,
        )

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        return MachineIdentity(
            manufacturer="BMC",
            family=detected.family_hint,
            model=None,
            model_number=None,
            serial_number=None,
            firmware_version=None,
            data_format_version=None,
            loader_identity=self.adapter_id,
            identity_confidence=Confidence.WEAK,
            source_fields={"source_key": detected.device_key_hint or ""},
        )

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        is_legacy = detected.family_hint == "Legacy/G2"
        parser = CapabilityStatus(
            is_legacy, ValidationStatus.UNVALIDATED, "Legacy parser exists; G3X parser is pending."
        )
        unavailable = CapabilityStatus(False, ValidationStatus.UNVALIDATED)
        return Capabilities(
            identity=parser,
            sessions=parser,
            session_blocks=parser,
            settings=parser,
            events=parser,
            low_rate_signals=parser,
            waveforms=parser,
            oximetry=parser,
            summary_only_days=parser,
            source_manifest=unavailable,
            timezone_basis="machine_local",
            leak_kinds=("unknown",),
        )
