"""Read-only APIs for CPAP machines, import diagnostics, and settings history."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db

router = APIRouter()


@router.get("/runs")
def list_import_runs(
    limit: int = 25,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return recent imports with machine identity and structured diagnostics."""

    safe_limit = max(1, min(limit, 100))
    db.execute(
        text("""
            UPDATE import_runs
            SET status = 'failed',
                validation_status = 'failed',
                completed_at = NOW(),
                errors = COALESCE(errors, '[]'::jsonb) || jsonb_build_array(
                    jsonb_build_object(
                        'code', 'IMPORT_PROCESS_INTERRUPTED',
                        'message', 'The importer stopped without reporting completion.',
                        'severity', 'error'
                    )
                )
            WHERE user_id = CAST(:user_id AS uuid)
              AND status = 'running'
              AND started_at < NOW() - INTERVAL '2 hours'
        """),
        {"user_id": current_user["id"]},
    )
    db.commit()
    rows = db.execute(
        text("""
            SELECT
                r.id::text,
                r.adapter_id,
                r.adapter_version,
                r.source_type,
                r.source_fingerprint,
                r.source_label,
                r.status,
                r.validation_status,
                r.detected_manufacturer,
                r.detected_family,
                r.detected_capabilities,
                r.warnings,
                r.errors,
                r.skipped_files,
                r.imported_session_count,
                r.imported_block_count,
                r.imported_event_count,
                r.imported_channel_count,
                r.imported_settings_count,
                r.summary_only_day_count,
                r.capability_status,
                r.started_at,
                r.completed_at,
                m.id::text AS machine_id,
                m.manufacturer AS machine_manufacturer,
                m.family AS machine_family,
                m.model AS machine_model,
                m.product_code AS machine_product_code,
                m.serial_number AS machine_serial_number,
                m.firmware_version AS machine_firmware_version,
                m.support_status AS machine_support_status,
                m.validation_status AS machine_validation_status,
                (
                    SELECT COUNT(*)::int
                    FROM import_source_files sf
                    WHERE sf.import_run_id = r.id
                ) AS source_file_count
            FROM import_runs r
            LEFT JOIN cpap_machines m ON m.id = r.machine_id
            WHERE r.user_id = CAST(:user_id AS uuid)
            ORDER BY r.created_at DESC
            LIMIT :limit
        """),
        {"user_id": current_user["id"], "limit": safe_limit},
    ).mappings()
    return [dict(row) for row in rows]


@router.get("/runs/{run_id}")
def get_import_run(
    run_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return one import plus its source-file manifest."""

    run = db.execute(
        text("""
            SELECT r.*, m.manufacturer, m.family, m.model, m.product_code,
                   m.serial_number, m.firmware_version, m.support_status
            FROM import_runs r
            LEFT JOIN cpap_machines m ON m.id = r.machine_id
            WHERE r.id = CAST(:run_id AS uuid)
              AND r.user_id = CAST(:user_id AS uuid)
        """),
        {"run_id": run_id, "user_id": current_user["id"]},
    ).mappings().first()
    if run is None:
        raise HTTPException(status_code=404, detail="Import run not found")
    files = db.execute(
        text("""
            SELECT id::text, relative_path, size_bytes, content_hash, parser_role,
                   disposition, parser_component, warning_state, error_state
            FROM import_source_files
            WHERE import_run_id = CAST(:run_id AS uuid)
            ORDER BY relative_path
        """),
        {"run_id": run_id},
    ).mappings()
    return {**dict(run), "source_files": [dict(row) for row in files]}


@router.get("/machines")
def list_cpap_machines(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return durable CPAP machine identities for the current user."""

    rows = db.execute(
        text("""
            SELECT
                m.id::text, m.manufacturer, m.family, m.model, m.product_code,
                m.serial_number, m.firmware_version, m.data_format_version,
                m.adapter_id, m.adapter_version, m.identity_confidence,
                m.support_status, m.validation_status, m.first_seen_at,
                m.last_seen_at,
                COUNT(DISTINCT s.id)::int AS session_count,
                COUNT(DISTINCT r.id)::int AS import_count
            FROM cpap_machines m
            LEFT JOIN sessions s ON s.machine_id = m.id
            LEFT JOIN import_runs r ON r.machine_id = m.id
            WHERE m.user_id = CAST(:user_id AS uuid)
            GROUP BY m.id
            ORDER BY m.last_seen_at DESC
        """),
        {"user_id": current_user["id"]},
    ).mappings()
    return [dict(row) for row in rows]


@router.get("/machines/{machine_id}/settings")
def list_machine_settings(
    machine_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return versioned settings snapshots for one owned machine."""

    rows = db.execute(
        text("""
            SELECT ss.id::text, ss.session_id::text, ss.import_run_id::text,
                   ss.effective_at, ss.normalized_settings, ss.vendor_settings,
                   ss.source_names, ss.adapter_id, ss.confidence,
                   ss.validation_status, ss.parser_id, ss.parser_version,
                   ss.diagnostics
            FROM settings_snapshots ss
            JOIN cpap_machines m ON m.id = ss.machine_id
            WHERE ss.machine_id = CAST(:machine_id AS uuid)
              AND m.user_id = CAST(:user_id AS uuid)
            ORDER BY ss.effective_at DESC
        """),
        {"machine_id": machine_id, "user_id": current_user["id"]},
    ).mappings()
    return [dict(row) for row in rows]
