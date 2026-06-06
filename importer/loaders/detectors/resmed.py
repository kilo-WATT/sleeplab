"""Structural ResMed detection and identity peeking."""

import json
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


class ResMedStructuralAdapter(LoaderAdapter):
    """Detect a ResMed card without parsing session EDF files."""

    adapter_id = "resmed-native-v2"
    priority = 10

    def detect(self, source_root: Path) -> list[DetectedDevice]:
        root = source_root.resolve()
        datalog = find_child_case_insensitive(root, "DATALOG")
        if datalog is None or not datalog.is_dir():
            return []
        str_file = find_child_case_insensitive(root, "STR.edf")
        ident = find_child_case_insensitive(root, "Identification.json") or find_child_case_insensitive(
            root, "Identification.tgt"
        )
        evidence = [
            DetectionEvidence(
                kind="required_path",
                relative_path="DATALOG",
                expected="directory",
                observed="directory",
                weight=50,
            )
        ]
        warnings: list[ImportWarning] = []
        if str_file:
            evidence.append(
                DetectionEvidence(
                    kind="required_path",
                    relative_path=str_file.name,
                    expected="ResMed summary EDF",
                    observed="file",
                    weight=35,
                )
            )
        else:
            warnings.append(
                ImportWarning(
                    code="resmed_missing_str",
                    severity="warning",
                    message="DATALOG was found without root STR.edf.",
                    relative_path="STR.edf",
                    affects=("summaries", "settings", "detection"),
                )
            )
        if ident:
            evidence.append(
                DetectionEvidence(
                    kind="identity_record",
                    relative_path=ident.name,
                    expected="ResMed identification record",
                    observed="file",
                    weight=25,
                )
            )
        confidence = Confidence.EXACT if str_file and ident else Confidence.STRONG if str_file else Confidence.PROBABLE
        return [
            DetectedDevice(
                adapter_id=self.adapter_id,
                source_root=root,
                device_path=root,
                manufacturer_hint="ResMed",
                family_hint=None,
                confidence=confidence,
                evidence=tuple(evidence),
                warnings=tuple(warnings),
            )
        ]

    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        json_path = find_child_case_insensitive(detected.source_root, "Identification.json")
        tgt_path = find_child_case_insensitive(detected.source_root, "Identification.tgt")
        fields: dict[str, str] = {}
        warnings: list[ImportWarning] = []
        if json_path:
            try:
                fields = _read_json_identity(json_path)
            except (OSError, ValueError, TypeError) as exc:
                warnings.append(
                    ImportWarning(
                        code="resmed_identity_invalid",
                        severity="warning",
                        message=f"Could not parse Identification.json: {exc}",
                        relative_path=json_path.name,
                        affects=("identity",),
                    )
                )
        elif tgt_path:
            fields = _read_tgt_identity(tgt_path)
        else:
            warnings.append(
                ImportWarning(
                    code="resmed_identity_missing",
                    severity="warning",
                    message="No Identification.json or Identification.tgt was found.",
                    affects=("identity",),
                )
            )
        serial = fields.get("serial")
        return MachineIdentity(
            manufacturer="ResMed",
            family=fields.get("series"),
            model=fields.get("model"),
            model_number=fields.get("model_number"),
            serial_number=serial or None,
            firmware_version=fields.get("firmware"),
            data_format_version=None,
            loader_identity=self.adapter_id,
            identity_confidence=Confidence.EXACT if serial else Confidence.PROBABLE,
            source_fields=fields,
            warnings=tuple(warnings),
        )

    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        has_str = find_child_case_insensitive(detected.source_root, "STR.edf") is not None
        validated = CapabilityStatus(True, ValidationStatus.VALIDATED, "Existing native ResMed parser behavior.")
        partial = CapabilityStatus(
            True, ValidationStatus.PARTIAL, "Available but not normalized into the 2.0 model yet."
        )
        return Capabilities(
            identity=partial,
            sessions=validated,
            session_blocks=validated,
            settings=CapabilityStatus(
                has_str, ValidationStatus.UNVALIDATED, "STR/CSL settings are not yet normalized."
            ),
            events=validated,
            low_rate_signals=validated,
            waveforms=partial,
            oximetry=partial,
            summary_only_days=CapabilityStatus(has_str, ValidationStatus.UNVALIDATED),
            source_manifest=CapabilityStatus(False, ValidationStatus.UNVALIDATED),
            timezone_basis="machine_local",
            leak_kinds=("unintentional",),
        )


def _read_json_identity(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    flow = data.get("FlowGenerator", {})
    product = flow.get("IdentificationProfiles", {}).get("Product", {})
    if not isinstance(product, dict):
        product = {}
    serial = (
        product.get("SerialNumber") or flow.get("Identification", {}).get("SerialNumber") or flow.get("SerialNumber")
    )
    return {
        key: str(value)
        for key, value in {
            "serial": serial,
            "model": product.get("ProductName") or flow.get("ProductName"),
            "model_number": product.get("ProductCode") or flow.get("ProductCode"),
            "series": product.get("Series") or flow.get("Series"),
            "firmware": flow.get("FirmwareVersion"),
        }.items()
        if value not in (None, "")
    }


def _read_tgt_identity(path: Path) -> dict[str, str]:
    mapping = {"SRN": "serial", "PNA": "model", "PCD": "model_number", "SW": "firmware"}
    fields: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip().lstrip("#")
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        else:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            key, value = parts
        normalized = mapping.get(key.strip().upper())
        if normalized and value.strip():
            fields[normalized] = value.strip().replace("_", " ") if normalized == "model" else value.strip()
    model = fields.get("model", "")
    for series in ("AirSense 11", "AirCurve 11", "AirSense 10", "AirCurve 10", "Sleepmate 10"):
        if series.casefold() in model.casefold():
            fields["series"] = series
            break
    return fields
