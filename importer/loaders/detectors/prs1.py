"""Structural Philips Respironics PRS1 and DreamStation detection."""

from pathlib import Path

from ..base import LoaderAdapter, find_child_case_insensitive
from ..models import (
    Capabilities,
    CapabilityStatus,
    Confidence,
    DetectedDevice,
    DetectionEvidence,
    ImportWarning,
    MachineIdentity,
    ValidationStatus,
)


class Prs1StructuralAdapter(LoaderAdapter):
    """Detect native P-Series cards and enumerate every machine directory."""

    adapter_id = "philips-prs1-v2"
    priority = 20

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        root = source_root.resolve()
        pseries = find_child_case_insensitive(root, "P-Series")
        if pseries is None or not pseries.is_dir():
            return []
        candidates: list[DetectedDevice] = []
        for device_dir in sorted(
            (path for path in pseries.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        ):
            prop = _find_property_file(device_dir)
            if prop is None:
                continue
            confidence = Confidence.EXACT if prop.name.upper() == "PROP.BIN" else Confidence.STRONG
            candidates.append(
                DetectedDevice(
                    adapter_id=self.adapter_id,
                    source_root=root,
                    device_path=device_dir,
                    manufacturer_hint="Philips Respironics",
                    family_hint="DreamStation 2" if prop.name.upper() == "PROP.BIN" else "PRS1",
                    confidence=confidence,
                    evidence=(
                        DetectionEvidence(
                            kind="required_path",
                            relative_path=f"P-Series/{device_dir.name}/{prop.name}",
                            expected="PRS1 machine properties",
                            observed="file",
                            weight=80,
                        ),
                    ),
                    device_key_hint=device_dir.name,
                )
            )
        return candidates

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        prop = _find_property_file(detected.device_path)
        fields: dict[str, str] = {}
        warnings: list[ImportWarning] = []
        if prop and prop.suffix.upper() != ".BIN":
            fields = _read_text_properties(prop)
        elif prop:
            warnings.append(
                ImportWarning(
                    code="prs1_encrypted_identity",
                    severity="warning",
                    message="DreamStation 2 PROP.BIN identity requires a parser implementation.",
                    relative_path=prop.name,
                    affects=("identity",),
                )
            )
        serial = fields.get("SerialNumber") or fields.get("SN")
        model = fields.get("ModelNumber") or fields.get("MN")
        return MachineIdentity(
            manufacturer="Philips Respironics",
            family=detected.family_hint,
            model=None,
            model_number=model,
            serial_number=serial,
            firmware_version=fields.get("SoftwareVersion") or fields.get("SV"),
            data_format_version=fields.get("DataFormatVersion") or fields.get("DFV"),
            loader_identity=self.adapter_id,
            identity_confidence=Confidence.EXACT if serial and model else Confidence.PROBABLE,
            source_fields=fields,
            warnings=tuple(warnings),
        )

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        unavailable = CapabilityStatus(False, ValidationStatus.UNVALIDATED, "Structural prototype only.")
        identity = CapabilityStatus(
            True, ValidationStatus.PARTIAL, "Text properties are supported; PROP.BIN is pending."
        )
        return Capabilities(
            identity=identity,
            sessions=unavailable,
            session_blocks=unavailable,
            settings=unavailable,
            events=unavailable,
            low_rate_signals=unavailable,
            waveforms=unavailable,
            oximetry=unavailable,
            summary_only_days=unavailable,
            source_manifest=unavailable,
            timezone_basis="machine_local",
            leak_kinds=("total", "unknown"),
        )


def _find_property_file(device_dir: Path) -> Path | None:
    files = [path for path in device_dir.iterdir() if path.is_file()]
    for preferred in ("PROP.BIN", "PROPERTIES.TXT", "PROP.TXT", "PROP.BAK"):
        for path in files:
            if path.name.upper() == preferred:
                return path
    for path in files:
        if path.name.upper().startswith("PROP") and path.suffix.upper() == ".TXT":
            return path
    return None


def _read_text_properties(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields
