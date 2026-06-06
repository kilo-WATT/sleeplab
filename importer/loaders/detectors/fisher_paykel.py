"""Structural Fisher & Paykel SleepStyle/ICON detector."""

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


class FisherPaykelStructuralAdapter(LoaderAdapter):
    """Detect each machine directory under FPHCARE/ICON."""

    adapter_id = "fisher-paykel-v2"
    priority = 40

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        root = source_root.resolve()
        fphcare = find_child_case_insensitive(root, "FPHCARE")
        icon = find_child_case_insensitive(fphcare, "ICON") if fphcare else None
        if icon is None or not icon.is_dir():
            return []
        candidates: list[DetectedDevice] = []
        for device_dir in sorted(
            (path for path in icon.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        ):
            summary = next(iter(sorted(device_dir.glob("SUM*.fph"))), None)
            if summary is None:
                summary = next(iter(sorted(device_dir.glob("SUM*.FPH"))), None)
            if summary is None:
                continue
            family = _read_family_marker(summary)
            if family not in {"SLEEPSTYLE", "ICON"}:
                continue
            candidates.append(
                DetectedDevice(
                    adapter_id=self.adapter_id,
                    source_root=root,
                    device_path=device_dir,
                    manufacturer_hint="Fisher & Paykel",
                    family_hint="SleepStyle" if family == "SLEEPSTYLE" else "ICON",
                    confidence=Confidence.EXACT,
                    evidence=(
                        DetectionEvidence(
                            "file_header",
                            f"FPHCARE/ICON/{device_dir.name}/{summary.name}",
                            "SLEEPSTYLE or ICON header",
                            family,
                            100,
                        ),
                    ),
                    device_key_hint=device_dir.name,
                )
            )
        return candidates

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        return MachineIdentity(
            manufacturer="Fisher & Paykel",
            family=detected.family_hint,
            model=detected.family_hint,
            model_number=None,
            serial_number=detected.device_key_hint,
            firmware_version=None,
            data_format_version=None,
            loader_identity=self.adapter_id,
            identity_confidence=Confidence.STRONG,
            source_fields={"serial_directory": detected.device_key_hint or ""},
        )

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        summary = CapabilityStatus(
            True, ValidationStatus.UNVALIDATED, "Summary parser exists but lacks shared fixtures."
        )
        unavailable = CapabilityStatus(False, ValidationStatus.UNVALIDATED)
        return Capabilities(
            identity=summary,
            sessions=summary,
            session_blocks=summary,
            settings=CapabilityStatus(True, ValidationStatus.PARTIAL),
            events=unavailable,
            low_rate_signals=unavailable,
            waveforms=unavailable,
            oximetry=unavailable,
            summary_only_days=summary,
            source_manifest=unavailable,
            timezone_basis="machine_local",
            leak_kinds=("unknown",),
        )


def _read_family_marker(summary: Path) -> str:
    data = summary.read_bytes()[:512]
    text = data.decode("ascii", errors="ignore").replace("\n", "\r")
    lines = [line.strip().upper() for line in text.split("\r") if line.strip()]
    for marker in ("SLEEPSTYLE", "ICON"):
        if marker in lines[:10]:
            return marker
    return ""
