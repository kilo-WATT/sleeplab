"""Manufacturer-neutral import planning for SleepLab 2.0."""

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .inspection import inspect_source_root
from .registry import LoaderRegistry, create_default_registry


@dataclass(frozen=True)
class SourceRoleSummary:
    """Count and size for one source-file role."""

    role: str
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class SourceManifestSummary:
    """Stable summary of every file staged for an import."""

    fingerprint: str
    file_count: int
    total_bytes: int
    roles: tuple[SourceRoleSummary, ...]


@dataclass(frozen=True)
class CoverageSummary:
    """Date and session coverage inferred without parsing full payloads."""

    first_date: str | None = None
    last_date: str | None = None
    therapy_days: int = 0
    estimated_session_blocks: int = 0
    waveform_files: int = 0
    event_files: int = 0
    oximetry_files: int = 0
    settings_files: int = 0


@dataclass(frozen=True)
class DeviceImportPlan:
    """Execution decision for one detected machine."""

    adapter_id: str
    device_path: str
    execution_status: str
    execution_backend: str | None
    coverage: CoverageSummary
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportPlan:
    """Complete read-only plan produced before any database writes."""

    plan_version: str
    source_root: str
    source_manifest: SourceManifestSummary
    inspection: dict[str, Any]
    devices: tuple[DeviceImportPlan, ...]
    executable: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)


def create_import_plan(
    source_root: str | Path,
    registry: LoaderRegistry | None = None,
) -> ImportPlan:
    """Inspect a staged source and produce a deterministic execution plan."""

    root = Path(source_root).resolve()
    active_registry = registry or create_default_registry()
    inspection = inspect_source_root(root, active_registry)
    manifest = _build_manifest(root)
    devices: list[DeviceImportPlan] = []
    blockers: list[str] = []

    if not inspection["matched"]:
        blockers.append("No registered loader recognized this source root.")
    if inspection["ambiguous"]:
        blockers.append("Multiple loaders matched with similar confidence.")

    for device in inspection["devices"]:
        adapter_id = device["adapter_id"]
        coverage = _coverage_for_device(root, adapter_id, device["device_path"])
        device_blockers: list[str] = []
        backend: str | None = None
        status = "blocked"
        if adapter_id == "resmed-native-v2":
            backend = "sleeplab-native-resmed"
            status = "ready"
        else:
            device_blockers.append(
                "Detection and planning are available, but this adapter does not implement execution yet."
            )
        if device["requires_user_choice"]:
            status = "blocked"
            device_blockers.append("Loader selection is ambiguous.")
        if coverage.therapy_days == 0:
            status = "blocked"
            device_blockers.append("No detailed therapy-day directories were found.")
        devices.append(
            DeviceImportPlan(
                adapter_id=adapter_id,
                device_path=device["device_path"],
                execution_status=status,
                execution_backend=backend,
                coverage=coverage,
                blockers=tuple(device_blockers),
                warnings=tuple(warning["message"] for warning in device["warnings"]),
            )
        )

    blockers.extend(blocker for device in devices for blocker in device.blockers)
    executable = bool(devices) and all(device.execution_status == "ready" for device in devices) and not blockers
    return ImportPlan(
        plan_version="2.0-alpha-1",
        source_root=str(root),
        source_manifest=manifest,
        inspection=inspection,
        devices=tuple(devices),
        executable=executable,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def import_plan_dict(plan: ImportPlan) -> dict[str, Any]:
    """Serialize an import plan for API and conformance output."""

    return asdict(plan)


def _build_manifest(root: Path) -> SourceManifestSummary:
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.as_posix())
    digest = hashlib.sha256()
    role_counts: Counter[str] = Counter()
    role_bytes: Counter[str] = Counter()
    total_bytes = 0
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        size = path.stat().st_size
        role = _source_role(path)
        role_counts[role] += 1
        role_bytes[role] += size
        total_bytes += size
        digest.update(relative_path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(size).encode())
        digest.update(b"\0")
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    roles = tuple(
        SourceRoleSummary(role=role, file_count=role_counts[role], size_bytes=role_bytes[role])
        for role in sorted(role_counts)
    )
    return SourceManifestSummary(
        fingerprint=f"sha256:{digest.hexdigest()}",
        file_count=len(files),
        total_bytes=total_bytes,
        roles=roles,
    )


def _source_role(path: Path) -> str:
    name = path.name.upper()
    suffix = path.suffix.lower()
    if name in {"IDENTIFICATION.TGT", "IDENTIFICATION.JSON"} or name.startswith("PROP"):
        return "identity"
    if name == "STR.EDF" or "SUM" in name:
        return "summary"
    if "_EVE." in name or suffix in {".eve"}:
        return "events"
    if "_BRP." in name or suffix in {".brp"}:
        return "waveform"
    if "_PLD." in name or suffix in {".pld"}:
        return "low_rate_signals"
    if "_SA2." in name or "_SAD." in name:
        return "oximetry"
    if "_CSL." in name:
        return "events"
    if name == "STR.EDF":
        return "settings"
    return "other"


def _coverage_for_device(root: Path, adapter_id: str, device_path: str) -> CoverageSummary:
    if adapter_id == "resmed-native-v2":
        return _resmed_coverage(root)
    device_root = root if device_path == "." else root / device_path
    files = [path for path in device_root.rglob("*") if path.is_file()]
    return CoverageSummary(
        waveform_files=sum(1 for path in files if _source_role(path) == "waveform"),
        event_files=sum(1 for path in files if _source_role(path) == "events"),
        oximetry_files=sum(1 for path in files if _source_role(path) == "oximetry"),
        settings_files=sum(1 for path in files if _source_role(path) == "settings"),
    )


def _resmed_coverage(root: Path) -> CoverageSummary:
    datalog = root / "DATALOG"
    if not datalog.is_dir():
        return CoverageSummary()
    date_dirs = sorted(
        path for path in datalog.iterdir() if path.is_dir() and len(path.name) == 8 and path.name.isdigit()
    )
    files = [path for folder in date_dirs for path in folder.iterdir() if path.is_file()]
    return CoverageSummary(
        first_date=_display_date(date_dirs[0].name) if date_dirs else None,
        last_date=_display_date(date_dirs[-1].name) if date_dirs else None,
        therapy_days=len(date_dirs),
        estimated_session_blocks=sum(1 for path in files if path.name.upper().endswith("_PLD.EDF")),
        waveform_files=sum(1 for path in files if path.name.upper().endswith("_BRP.EDF")),
        event_files=sum(
            1
            for path in files
            if path.name.upper().endswith("_EVE.EDF") or path.name.upper().endswith("_CSL.EDF")
        ),
        oximetry_files=sum(
            1
            for path in files
            if path.name.upper().endswith("_SA2.EDF") or path.name.upper().endswith("_SAD.EDF")
        ),
        settings_files=int((root / "STR.edf").is_file()),
    )


def _display_date(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
