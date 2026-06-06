"""Protocols and helpers for SleepLab 2.0 structural detectors."""

from abc import ABC, abstractmethod
from pathlib import Path

from .models import Capabilities, DetectedDevice, ImportOptions, ImportRun, MachineIdentity


class LoaderAdapter(ABC):
    """Base interface implemented by each manufacturer adapter."""

    adapter_id: str
    adapter_version = "0.1"
    priority = 100

    @abstractmethod
    def detect(self, source_root: Path) -> list[DetectedDevice]:
        """Return all machine candidates found below an explicit source root."""

    @abstractmethod
    def peek_info(self, detected: DetectedDevice) -> MachineIdentity:
        """Read machine identity without parsing therapy sessions."""

    @abstractmethod
    def capabilities(self, detected: DetectedDevice) -> Capabilities:
        """Report data categories available for a detected machine."""

    def import_data(
        self,
        detected: DetectedDevice,
        options: ImportOptions,
    ) -> ImportRun:
        """Import normalized data after detection, identity, and policy checks."""

        raise NotImplementedError(f"{self.adapter_id} does not implement full import yet")


def relative_display(path: Path, root: Path) -> str:
    """Return a stable relative path for evidence and diagnostics."""

    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        return path.name
    return value or "."


def find_child_case_insensitive(root: Path, name: str) -> Path | None:
    """Find one immediate child by name without assuming filesystem casing."""

    if not root.is_dir():
        return None
    expected = name.casefold()
    for child in root.iterdir():
        if child.name.casefold() == expected:
            return child
    return None
