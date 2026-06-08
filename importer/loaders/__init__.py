"""SleepLab 2.0 CPAP loader contracts and structural detection registry."""

from .execution import ExecutionRequest, ImportPlanError, prepare_execution
from .inspection import inspect_source_root
from .models import (
    Capabilities,
    CapabilityStatus,
    DetectedDevice,
    DetectionEvidence,
    DetectionReport,
    ImportOptions,
    ImportRun,
    ImportWarning,
    MachineIdentity,
)
from .planning import ImportPlan, create_import_plan, import_plan_dict
from .registry import LoaderRegistry, create_default_registry
from .resmed_native import ResMedNativeLoader

__all__ = [
    "Capabilities",
    "CapabilityStatus",
    "DetectedDevice",
    "DetectionEvidence",
    "DetectionReport",
    "ExecutionRequest",
    "ImportOptions",
    "ImportPlan",
    "ImportPlanError",
    "ImportRun",
    "ImportWarning",
    "LoaderRegistry",
    "MachineIdentity",
    "ResMedNativeLoader",
    "create_default_registry",
    "create_import_plan",
    "import_plan_dict",
    "inspect_source_root",
    "prepare_execution",
]
