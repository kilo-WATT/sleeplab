"""Vendor-neutral data contracts for SleepLab 2.0 CPAP loaders."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Confidence(StrEnum):
    """Strength of a detector or identity conclusion."""

    NONE = "none"
    WEAK = "weak"
    PROBABLE = "probable"
    STRONG = "strong"
    EXACT = "exact"


class ValidationStatus(StrEnum):
    """Validation state for one adapter capability."""

    UNVALIDATED = "unvalidated"
    PARTIAL = "partial"
    VALIDATED = "validated"
    FAILED = "failed"


class ImportStatus(StrEnum):
    """Lifecycle state for an import run."""

    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


type NormalizedScalar = str | int | float | bool | None


@dataclass(frozen=True)
class DetectionEvidence:
    """One positive or negative fact used to identify a source layout."""

    kind: str
    relative_path: str
    expected: str
    observed: str
    weight: int


@dataclass(frozen=True)
class ImportWarning:
    """Structured diagnostic emitted during detection or import."""

    code: str
    severity: str
    message: str
    relative_path: str | None = None
    source_value: str | None = None
    expected_values: tuple[str, ...] = ()
    affects: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetectedDevice:
    """A single machine candidate discovered within an explicit source root."""

    adapter_id: str
    source_root: Path
    device_path: Path
    manufacturer_hint: str | None
    family_hint: str | None
    confidence: Confidence
    evidence: tuple[DetectionEvidence, ...]
    device_key_hint: str | None = None
    competing_adapter_ids: tuple[str, ...] = ()
    requires_user_choice: bool = False
    warnings: tuple[ImportWarning, ...] = ()


@dataclass(frozen=True)
class DetectionReport:
    """Complete result of running every registered detector."""

    source_root: Path
    candidates: tuple[DetectedDevice, ...]
    warnings: tuple[ImportWarning, ...] = ()

    @property
    def matched(self) -> bool:
        """Return whether at least one candidate was detected."""

        return bool(self.candidates)

    @property
    def ambiguous(self) -> bool:
        """Return whether any candidate requires an explicit choice."""

        return any(candidate.requires_user_choice for candidate in self.candidates)


@dataclass(frozen=True)
class MachineIdentity:
    """Machine metadata available before full session parsing."""

    manufacturer: str | None
    family: str | None
    model: str | None
    model_number: str | None
    serial_number: str | None
    firmware_version: str | None
    data_format_version: str | None
    loader_identity: str
    identity_confidence: Confidence
    source_fields: dict[str, str] = field(default_factory=dict)
    warnings: tuple[ImportWarning, ...] = ()


@dataclass(frozen=True)
class CapabilityStatus:
    """Availability and validation state for one data category."""

    available: bool
    validation: ValidationStatus
    notes: str = ""


@dataclass(frozen=True)
class Capabilities:
    """Capabilities reported for a specific detected machine."""

    identity: CapabilityStatus
    sessions: CapabilityStatus
    session_blocks: CapabilityStatus
    settings: CapabilityStatus
    events: CapabilityStatus
    low_rate_signals: CapabilityStatus
    waveforms: CapabilityStatus
    oximetry: CapabilityStatus
    summary_only_days: CapabilityStatus
    source_manifest: CapabilityStatus
    timezone_basis: str
    leak_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportOptions:
    """Backend-independent controls for a full import."""

    include_waveforms: bool = True
    include_oximetry: bool = True
    allow_partial: bool = True


@dataclass(frozen=True)
class SourceFile:
    """A source artifact considered by an adapter."""

    source_file_id: str
    relative_path: str
    size_bytes: int
    content_hash: str
    role: str
    used: bool
    parser_component: str | None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionBlock:
    """A contiguous source-defined block inside a therapy session."""

    source_block_key: str
    start_time: datetime
    end_time: datetime
    block_kind: str
    source_file_ids: tuple[str, ...]


@dataclass(frozen=True)
class SettingsSnapshot:
    """Machine settings effective at a point in time."""

    effective_at: datetime
    settings: dict[str, NormalizedScalar]
    source_names: dict[str, str]
    source_file_ids: tuple[str, ...]
    confidence: Confidence


@dataclass(frozen=True)
class SignalChannel:
    """Metadata describing a normalized signal channel."""

    channel_key: str
    source_label: str
    unit: str
    sample_rate_hz: float | None
    value_kind: str
    leak_kind: str | None
    source_file_ids: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    """A normalized event retaining its source identity."""

    source_event_key: str
    event_type: str
    source_type: str
    start_time: datetime
    duration_seconds: float | None
    source_file_id: str
    confidence: Confidence


@dataclass(frozen=True)
class WaveformSegment:
    """A contiguous waveform segment and its storage metadata."""

    channel_key: str
    start_time: datetime
    sample_rate_hz: float
    sample_count: int
    unit: str
    source_file_id: str
    storage_ref: str | None = None


@dataclass(frozen=True)
class DerivedValue:
    """A value calculated from normalized or source data."""

    key: str
    value: NormalizedScalar
    unit: str | None
    method: str
    input_refs: tuple[str, ...]
    validation: ValidationStatus


@dataclass
class Session:
    """A normalized machine-scoped therapy interval."""

    source_session_key: str
    machine_key: str
    start_time: datetime
    end_time: datetime
    machine_local_date: str
    timezone_basis: str
    blocks: list[SessionBlock] = field(default_factory=list)
    settings: list[SettingsSnapshot] = field(default_factory=list)
    signals: list[SignalChannel] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    waveforms: list[WaveformSegment] = field(default_factory=list)
    derived_values: list[DerivedValue] = field(default_factory=list)
    source_file_ids: list[str] = field(default_factory=list)
    warnings: list[ImportWarning] = field(default_factory=list)


@dataclass
class ImportRun:
    """Persistence-independent result of one adapter import."""

    run_id: str
    adapter_id: str
    adapter_version: str
    source_fingerprint: str
    started_at: datetime
    completed_at: datetime | None
    status: ImportStatus
    detected_device: DetectedDevice
    machine: MachineIdentity
    capabilities: Capabilities
    source_files: list[SourceFile] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    warnings: list[ImportWarning] = field(default_factory=list)
