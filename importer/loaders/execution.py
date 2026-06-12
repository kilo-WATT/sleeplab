"""Execution handoff for approved SleepLab 2.0 import plans.

**Target architecture (SleepLab 2.0):** the cpap-parser path is the intended
ResMed import path for SleepLab 2.0. The legacy native subprocess is retained as a
fallback / rollback and as the parity oracle, not as the long-term target.

Two execution paths exist:

* The **cpap-parser** path (:func:`run_cpap_parser_import`) is the SleepLab 2.0
  ResMed target. It drives :class:`ResMedNativeLoader` in-process and persists its
  :class:`ImportRun` via :func:`importer.loaders.persist.persist_import_run`. It is
  enabled with ``SLEEPLAB_USE_CPAP_PARSER=1``.
* The **legacy** path returns an :class:`ExecutionRequest` describing the ResMed
  native subprocess (``importer/import_sessions.py``); the API spawns it. It is the
  current *runtime default* only because the ``cpap-py`` dependency/runtime posture
  is not yet settled for clean installs and CI (see
  ``docs/sleeplab_2_resmed_cutover_remaining_work.md``). Once that gate is met the
  default flips to cpap-parser; until then legacy is the safe fallback.

Detection/planning are unchanged regardless of the flag: the detected device is
still ``resmed-native-v2``. Only the *execution* step differs, so existing
detection tests and the legacy fallback stay intact.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .planning import ImportPlan

#: The cpap-parser execution adapter/backend identifiers.
CPAP_PARSER_ADAPTER_ID = "resmed-cpap-parser-v1"
CPAP_PARSER_BACKEND_ID = "sleeplab-cpap-parser-resmed"


class ImportPlanError(ValueError):
    """Raised when a staged source cannot be executed from its import plan."""


def use_cpap_parser() -> bool:
    """Return whether the cpap-parser execution path (the SleepLab 2.0 ResMed
    target) is enabled.

    cpap-parser is the intended 2.0 ResMed import path; this flag is the switch
    that selects it. Controlled by ``SLEEPLAB_USE_CPAP_PARSER`` and currently
    defaults to *off* so the legacy native subprocess remains the runtime default
    until the ``cpap-py`` dependency/runtime posture is settled for clean installs
    and CI. Set ``SLEEPLAB_USE_CPAP_PARSER=1`` to run SleepLab 2.0 on cpap-parser
    (recommended for 2.0 dev/alpha environments where cpap-py is installed). When
    the runtime gate is met, the default is expected to flip to on, with legacy
    retained as the rollback path.
    """

    return os.environ.get("SLEEPLAB_USE_CPAP_PARSER", "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ExecutionRequest:
    """Adapter-neutral request handed to an importer implementation."""

    adapter_id: str
    backend_id: str
    source_root: Path
    import_root: Path


def prepare_execution(
    plan: ImportPlan,
    source_root: str | Path,
    expected_fingerprint: str | None,
) -> ExecutionRequest:
    """Validate an inspected source and select its importer backend."""

    root = Path(source_root).resolve()
    if expected_fingerprint is None:
        raise ImportPlanError("Inspect this source before importing it.")
    if plan.source_manifest.fingerprint != expected_fingerprint:
        raise ImportPlanError("The staged source changed after inspection. Inspect it again.")
    if not plan.executable:
        details = "; ".join(plan.blockers) or "No detected device is ready to import."
        raise ImportPlanError(f"This source cannot be executed: {details}")
    if len(plan.devices) != 1:
        raise ImportPlanError("Select one detected machine before importing a multi-machine source.")

    device = plan.devices[0]
    if device.adapter_id == "resmed-native-v2" and device.execution_backend == "sleeplab-native-resmed":
        import_root = root / "DATALOG"
        if not import_root.is_dir():
            raise ImportPlanError("Detected ResMed source is missing DATALOG.")
        return ExecutionRequest(
            adapter_id=device.adapter_id,
            backend_id=device.execution_backend,
            source_root=root,
            import_root=import_root,
        )

    raise ImportPlanError(f"Execution backend {device.execution_backend or 'unknown'} is not registered.")


def run_cpap_parser_import(
    *,
    source_root: str | Path,
    user_id: str,
    import_run_id: str,
    machine_id: str,
    include_waveforms: bool = True,
) -> dict[str, int]:
    """Execute the cpap-parser loader against a staged source and persist it.

    This is the ``SLEEPLAB_USE_CPAP_PARSER=1`` execution path for the
    ``resmed-cpap-parser-v1`` adapter. It bypasses the legacy
    ``import_sessions.py`` subprocess and routes the upload through cpap-parser
    end-to-end: :meth:`ResMedNativeLoader.import_data` -> ``persist_import_run``.

    The durable ``import_runs`` row (``import_run_id``) and ``cpap_machines`` row
    (``machine_id``) must already exist — they are created by
    ``api.import_runs.create_import_run`` before this runs. Returns the persist
    summary counts.
    """

    # Lazy imports keep ``import importer.loaders`` free of psycopg2 / cpap-parser
    # at module load; they are only needed on this opt-in path.
    from importer.db import finish_import_run, get_conn

    from .models import ImportOptions
    from .persist import persist_import_run
    from .resmed_native import ResMedNativeLoader

    root = Path(source_root).resolve()
    loader = ResMedNativeLoader()
    detected = next(
        (candidate for candidate in loader.detect(root) if candidate.adapter_id == CPAP_PARSER_ADAPTER_ID),
        None,
    )
    if detected is None:
        raise ImportPlanError(f"No ResMed source detected under {root} for the cpap-parser loader.")

    try:
        # Parse once; keep the raw CPAPDirectory so the persistence layer can
        # populate the per-sample tables (session_metrics/session_waveform) that
        # the vendor-neutral ImportRun deliberately does not carry.
        run, directory = loader.import_data_with_directory(
            detected, ImportOptions(include_waveforms=include_waveforms)
        )
    except ImportError as exc:
        # The loader lazily imports cpap_parser; surface a clear dependency error.
        raise ImportError(
            "The cpap-parser execution path requires the pinned 'cpap-parser' package "
            "(and its cpap-py backend). Install it, or unset SLEEPLAB_USE_CPAP_PARSER "
            "to fall back to the legacy native importer."
        ) from exc

    conn = get_conn()
    try:
        counts = persist_import_run(
            run,
            user_id,
            conn,
            import_run_id=import_run_id,
            machine_id=machine_id,
            raw_directory=directory,
        )
        conn.commit()
        finish_import_run(
            conn,
            import_run_id,
            status=_cpap_parser_run_status(run, counts),
            imported_sessions=counts["sessions"],
            imported_blocks=counts["blocks"],
            imported_events=counts["events"],
            imported_channels=counts["channels"],
            imported_settings=counts["settings"],
            summary_only_days=counts["summary_only_days"],
            warnings=[_warning_dict(warning) for warning in run.warnings],
            errors=[],
        )
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cpap_parser_run_status(run, counts: dict[str, int]) -> str:
    """Map the loader's ``ImportStatus`` to an ``import_runs.status`` value."""

    from .models import ImportStatus

    if counts["sessions"] == 0:
        return "failed"
    if run.status == ImportStatus.FAILED:
        return "failed"
    if run.status == ImportStatus.PARTIAL:
        return "partial"
    return "success"


def _warning_dict(warning) -> dict:
    """Serialize an :class:`ImportWarning` for ``import_runs.warnings``."""

    return {
        "code": warning.code,
        "severity": warning.severity,
        "message": warning.message,
        "relative_path": warning.relative_path,
        "source_value": warning.source_value,
        "affects": list(warning.affects),
    }
