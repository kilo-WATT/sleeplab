"""Registry and arbitration for SleepLab 2.0 CPAP loaders."""

from collections import defaultdict
from pathlib import Path

from .base import LoaderAdapter
from .detectors import (
    BmcStructuralAdapter,
    FisherPaykelStructuralAdapter,
    LowensteinStructuralAdapter,
    Prs1StructuralAdapter,
    ResMedStructuralAdapter,
)
from .models import Confidence, DetectedDevice, DetectionReport, ImportWarning

_CONFIDENCE_RANK = {
    Confidence.NONE: 0,
    Confidence.WEAK: 1,
    Confidence.PROBABLE: 2,
    Confidence.STRONG: 3,
    Confidence.EXACT: 4,
}


class LoaderRegistry:
    """Run all adapters and report ambiguity rather than choosing by order."""

    def __init__(self, adapters: list[LoaderAdapter] | None = None) -> None:
        self._adapters: list[LoaderAdapter] = []
        for adapter in adapters or []:
            self.register(adapter)

    @property
    def adapters(self) -> tuple[LoaderAdapter, ...]:
        """Return registered adapters in deterministic priority order."""

        return tuple(self._adapters)

    def register(self, adapter: LoaderAdapter) -> None:
        """Register one unique adapter."""

        if any(item.adapter_id == adapter.adapter_id for item in self._adapters):
            raise ValueError(f"Loader adapter already registered: {adapter.adapter_id}")
        self._adapters.append(adapter)
        self._adapters.sort(key=lambda item: (item.priority, item.adapter_id))

    def get_adapter(self, adapter_id: str) -> LoaderAdapter:
        """Return a registered adapter by stable identity."""

        for adapter in self._adapters:
            if adapter.adapter_id == adapter_id:
                return adapter
        raise ValueError(f"Unknown loader adapter: {adapter_id!r}")

    def detect(self, source_root: str | Path) -> DetectionReport:
        """Inspect exactly one SD-card or extracted-archive root."""

        root = Path(source_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Not a readable source root: {root}")

        raw_candidates = [candidate for adapter in self._adapters for candidate in adapter.detect(root)]
        candidates = self._mark_competition(raw_candidates)
        warnings: list[ImportWarning] = []
        if not candidates:
            warnings.append(
                ImportWarning(
                    code="unrecognized_source",
                    severity="warning",
                    message="No registered CPAP loader recognized the SD-card or archive root.",
                    relative_path=".",
                    affects=("detection",),
                )
            )
        if any(candidate.requires_user_choice for candidate in candidates):
            warnings.append(
                ImportWarning(
                    code="ambiguous_source",
                    severity="warning",
                    message="Multiple loaders produced similarly strong evidence.",
                    relative_path=".",
                    affects=("routing",),
                )
            )
        return DetectionReport(
            source_root=root,
            candidates=tuple(candidates),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _mark_competition(candidates: list[DetectedDevice]) -> list[DetectedDevice]:
        groups: dict[tuple[Path, Path], list[DetectedDevice]] = defaultdict(list)
        for candidate in candidates:
            groups[(candidate.source_root, candidate.device_path)].append(candidate)

        output: list[DetectedDevice] = []
        for group in groups.values():
            strongest = max(_CONFIDENCE_RANK[item.confidence] for item in group)
            contenders = [item for item in group if strongest - _CONFIDENCE_RANK[item.confidence] <= 1]
            contender_ids = tuple(sorted(item.adapter_id for item in contenders))
            for candidate in group:
                is_contender = candidate.adapter_id in contender_ids
                competing = (
                    tuple(adapter_id for adapter_id in contender_ids if adapter_id != candidate.adapter_id)
                    if is_contender
                    else ()
                )
                output.append(
                    DetectedDevice(
                        adapter_id=candidate.adapter_id,
                        source_root=candidate.source_root,
                        device_path=candidate.device_path,
                        manufacturer_hint=candidate.manufacturer_hint,
                        family_hint=candidate.family_hint,
                        confidence=candidate.confidence,
                        evidence=candidate.evidence,
                        device_key_hint=candidate.device_key_hint,
                        competing_adapter_ids=competing,
                        requires_user_choice=bool(competing),
                        warnings=candidate.warnings,
                    )
                )
        return sorted(
            output,
            key=lambda item: (
                -_CONFIDENCE_RANK[item.confidence],
                item.adapter_id,
                item.device_key_hint or "",
            ),
        )


def create_default_registry() -> LoaderRegistry:
    """Create the initial SleepLab 2.0 structural loader registry.

    ``ResMedNativeLoader`` is intentionally **not** registered here. Its
    ``detect()`` delegates to :class:`ResMedStructuralAdapter` and re-stamps the
    same candidate with its own adapter id at the same confidence, so adding it
    would make every ResMed card detect ambiguously (``requires_user_choice``) and
    leave the import plan non-executable. Detection/planning therefore stay keyed
    on the single ``resmed-native-v2`` structural detector. The cpap-parser
    execution path instantiates ``ResMedNativeLoader`` directly
    (see :func:`importer.loaders.execution.run_cpap_parser_import`); use
    :func:`create_execution_registry` if a flag-gated, id-addressable registry is
    needed.
    """

    return LoaderRegistry(
        [
            ResMedStructuralAdapter(),
            Prs1StructuralAdapter(),
            LowensteinStructuralAdapter(),
            FisherPaykelStructuralAdapter(),
            BmcStructuralAdapter(),
        ]
    )


def create_execution_registry() -> LoaderRegistry:
    """Registry for the opt-in execution path, flag-gated by env var.

    When ``SLEEPLAB_USE_CPAP_PARSER=1`` this additionally registers
    :class:`~importer.loaders.resmed_native.ResMedNativeLoader` (priority 20, so it
    sorts *after* the structural ``resmed-native-v2`` detector) so it is
    resolvable by id via :meth:`LoaderRegistry.get_adapter`. It is **not** used for
    source detection — doing so would reintroduce the ResMed ambiguity described in
    :func:`create_default_registry` — only as an id-addressable lookup for code
    that already knows it wants the cpap-parser adapter. With the flag unset this
    returns exactly the same set as :func:`create_default_registry`, so default
    behavior and all detection tests are unchanged.
    """

    registry = create_default_registry()
    from .execution import use_cpap_parser

    if use_cpap_parser():
        from .resmed_native import ResMedNativeLoader

        registry.register(ResMedNativeLoader())
    return registry
