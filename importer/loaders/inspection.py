"""Serializable inspection output for the SleepLab 2.0 loader prototype."""

from pathlib import Path
from typing import Any

from .registry import LoaderRegistry, create_default_registry


def inspect_source_root(
    source_root: str | Path,
    registry: LoaderRegistry | None = None,
) -> dict[str, Any]:
    """Detect machines and report identity and capabilities without importing."""

    active_registry = registry or create_default_registry()
    report = active_registry.detect(source_root)
    devices: list[dict[str, Any]] = []
    for detected in report.candidates:
        adapter = active_registry.get_adapter(detected.adapter_id)
        identity = adapter.peek_info(detected)
        capabilities = adapter.capabilities(detected)
        devices.append(
            {
                "adapter_id": detected.adapter_id,
                "adapter_version": adapter.adapter_version,
                "device_path": detected.device_path.relative_to(report.source_root).as_posix() or ".",
                "device_key_hint": detected.device_key_hint,
                "manufacturer_hint": detected.manufacturer_hint,
                "family_hint": detected.family_hint,
                "confidence": detected.confidence.value,
                "requires_user_choice": detected.requires_user_choice,
                "competing_adapter_ids": list(detected.competing_adapter_ids),
                "evidence": [
                    {
                        "kind": evidence.kind,
                        "relative_path": evidence.relative_path,
                        "expected": evidence.expected,
                        "observed": evidence.observed,
                        "weight": evidence.weight,
                    }
                    for evidence in detected.evidence
                ],
                "identity": {
                    "manufacturer": identity.manufacturer,
                    "family": identity.family,
                    "model": identity.model,
                    "model_number": identity.model_number,
                    "serial_number": identity.serial_number,
                    "firmware_version": identity.firmware_version,
                    "data_format_version": identity.data_format_version,
                    "confidence": identity.identity_confidence.value,
                },
                "capabilities": {
                    name: {
                        "available": status.available,
                        "validation": status.validation.value,
                        "notes": status.notes,
                    }
                    for name, status in vars(capabilities).items()
                    if hasattr(status, "available")
                },
                "timezone_basis": capabilities.timezone_basis,
                "leak_kinds": list(capabilities.leak_kinds),
                "warnings": [_warning_dict(warning) for warning in detected.warnings + identity.warnings],
            }
        )
    return {
        "source_root": str(report.source_root),
        "matched": report.matched,
        "ambiguous": report.ambiguous,
        "devices": devices,
        "warnings": [_warning_dict(warning) for warning in report.warnings],
    }


def _warning_dict(warning: Any) -> dict[str, Any]:
    return {
        "code": warning.code,
        "severity": warning.severity,
        "message": warning.message,
        "relative_path": warning.relative_path,
        "affects": list(warning.affects),
    }
