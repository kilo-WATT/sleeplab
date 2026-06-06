"""Execution handoff for approved SleepLab 2.0 import plans."""

from dataclasses import dataclass
from pathlib import Path

from .planning import ImportPlan


class ImportPlanError(ValueError):
    """Raised when a staged source cannot be executed from its import plan."""


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
