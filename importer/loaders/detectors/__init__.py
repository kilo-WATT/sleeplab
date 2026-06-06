"""Initial structural detectors modeled after OSCAR's loader lifecycle."""

from .bmc import BmcStructuralAdapter
from .fisher_paykel import FisherPaykelStructuralAdapter
from .lowenstein import LowensteinStructuralAdapter
from .prs1 import Prs1StructuralAdapter
from .resmed import ResMedStructuralAdapter

__all__ = [
    "BmcStructuralAdapter",
    "FisherPaykelStructuralAdapter",
    "LowensteinStructuralAdapter",
    "Prs1StructuralAdapter",
    "ResMedStructuralAdapter",
]
