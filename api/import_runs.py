"""Durable import-run and CPAP machine persistence for the 2.0 loader flow."""

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from importer.loaders.planning import ImportPlan, _source_role


def create_import_run(
    db: Session,
    *,
    user_id: str,
    plan: ImportPlan,
    source_root: Path,
    source_label: str,
) -> tuple[str, str]:
    """Persist a reviewed import plan and return ``(run_id, machine_id)``."""

    device = plan.inspection["devices"][0]
    identity = device["identity"]
    adapter_id = device["adapter_id"]
    adapter_version = device["adapter_version"]
    serial_number = _clean(identity.get("serial_number"))
    identity_key = _identity_key(
        adapter_id=adapter_id,
        serial_number=serial_number,
        source_fingerprint=plan.source_manifest.fingerprint,
        device_path=device["device_path"],
    )
    validation_status = _device_validation(device)
    support_status = "validated" if validation_status == "validated" else "experimental"

    machine_id = db.execute(
        text("""
            INSERT INTO cpap_machines (
                user_id, manufacturer, family, model, product_code, serial_number,
                firmware_version, data_format_version, adapter_id, adapter_version,
                identity_key, identity_confidence, support_status, validation_status,
                source_identity, last_seen_at, updated_at
            ) VALUES (
                CAST(:user_id AS uuid), :manufacturer, :family, :model, :product_code,
                :serial_number, :firmware_version, :data_format_version, :adapter_id,
                :adapter_version, :identity_key, :identity_confidence, :support_status,
                :validation_status, CAST(:source_identity AS jsonb), NOW(), NOW()
            )
            ON CONFLICT (user_id, identity_key) DO UPDATE SET
                manufacturer = COALESCE(EXCLUDED.manufacturer, cpap_machines.manufacturer),
                family = COALESCE(EXCLUDED.family, cpap_machines.family),
                model = COALESCE(EXCLUDED.model, cpap_machines.model),
                product_code = COALESCE(EXCLUDED.product_code, cpap_machines.product_code),
                serial_number = COALESCE(EXCLUDED.serial_number, cpap_machines.serial_number),
                firmware_version = COALESCE(EXCLUDED.firmware_version, cpap_machines.firmware_version),
                data_format_version = COALESCE(EXCLUDED.data_format_version, cpap_machines.data_format_version),
                adapter_version = EXCLUDED.adapter_version,
                identity_confidence = EXCLUDED.identity_confidence,
                support_status = EXCLUDED.support_status,
                validation_status = EXCLUDED.validation_status,
                source_identity = cpap_machines.source_identity || EXCLUDED.source_identity,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING id::text
        """),
        {
            "user_id": user_id,
            "manufacturer": _clean(identity.get("manufacturer")) or _clean(device.get("manufacturer_hint")),
            "family": _clean(identity.get("family")) or _clean(device.get("family_hint")),
            "model": _clean(identity.get("model")),
            "product_code": _clean(identity.get("model_number")),
            "serial_number": serial_number,
            "firmware_version": _clean(identity.get("firmware_version")),
            "data_format_version": _clean(identity.get("data_format_version")),
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "identity_key": identity_key,
            "identity_confidence": identity.get("confidence") or device["confidence"],
            "support_status": support_status,
            "validation_status": validation_status,
            "source_identity": _json(identity),
        },
    ).scalar_one()

    run_id = db.execute(
        text("""
            INSERT INTO import_runs (
                user_id, machine_id, adapter_id, adapter_version, source_type,
                source_fingerprint, import_fingerprint, source_label, status,
                validation_status, identity_confidence, detected_manufacturer,
                detected_family, detected_capabilities, warnings, started_at, updated_at
            ) VALUES (
                CAST(:user_id AS uuid), CAST(:machine_id AS uuid), :adapter_id,
                :adapter_version, 'uploaded_root', :source_fingerprint,
                :import_fingerprint, :source_label, 'running', :validation_status,
                :identity_confidence, :manufacturer, :family,
                CAST(:capabilities AS jsonb), CAST(:warnings AS jsonb), NOW(), NOW()
            )
            RETURNING id::text
        """),
        {
            "user_id": user_id,
            "machine_id": machine_id,
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "source_fingerprint": plan.source_manifest.fingerprint,
            "import_fingerprint": f"{plan.plan_version}:{plan.source_manifest.fingerprint}:{adapter_id}",
            "source_label": source_label,
            "validation_status": validation_status,
            "identity_confidence": identity.get("confidence") or device["confidence"],
            "manufacturer": _clean(identity.get("manufacturer")) or _clean(device.get("manufacturer_hint")),
            "family": _clean(identity.get("family")) or _clean(device.get("family_hint")),
            "capabilities": _json(device["capabilities"]),
            "warnings": _json([*plan.inspection["warnings"], *device["warnings"]]),
        },
    ).scalar_one()

    _persist_source_manifest(db, run_id, source_root)
    db.commit()
    return run_id, machine_id


def _persist_source_manifest(db: Session, run_id: str, source_root: Path) -> None:
    rows: list[dict[str, Any]] = []
    for path in sorted((path for path in source_root.rglob("*") if path.is_file()), key=lambda item: item.as_posix()):
        rows.append(
            {
                "run_id": run_id,
                "relative_path": path.relative_to(source_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "content_hash": _hash_file(path),
                "parser_role": _source_role(path),
            }
        )
    if rows:
        db.execute(
            text("""
                INSERT INTO import_source_files (
                    import_run_id, relative_path, size_bytes, content_hash, parser_role
                ) VALUES (
                    CAST(:run_id AS uuid), :relative_path, :size_bytes, :content_hash, :parser_role
                )
            """),
            rows,
        )


def _identity_key(
    *,
    adapter_id: str,
    serial_number: str | None,
    source_fingerprint: str,
    device_path: str,
) -> str:
    if serial_number:
        return f"{adapter_id}:serial:{serial_number.casefold()}"
    unresolved = hashlib.sha256(f"{source_fingerprint}\0{device_path}".encode()).hexdigest()
    return f"{adapter_id}:unresolved:{unresolved}"


def _device_validation(device: dict[str, Any]) -> str:
    available = [
        capability["validation"]
        for capability in device["capabilities"].values()
        if capability["available"]
    ]
    if available and all(status == "validated" for status in available):
        return "validated"
    if any(status in {"validated", "partial"} for status in available):
        return "partial"
    if any(status == "failed" for status in available):
        return "failed"
    return "unvalidated"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _clean(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True)
