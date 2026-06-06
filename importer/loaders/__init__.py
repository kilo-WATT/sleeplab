"""SleepLab 2.0 CPAP loader contracts and structural detection registry."""

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
from .registry import LoaderRegistry, create_default_registry

__all__ = [
    "Capabilities",
    "CapabilityStatus",
    "DetectedDevice",
    "DetectionEvidence",
    "DetectionReport",
    "ImportOptions",
    "ImportRun",
    "ImportWarning",
    "LoaderRegistry",
    "MachineIdentity",
    "create_default_registry",
    "inspect_source_root",
]
