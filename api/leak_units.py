"""Single source of truth for interpreting stored mask-leak values.

Leak is persisted in different units depending on the import path, and the unit
is recorded per session in ``sessions.leak_unit``:

* legacy ResMed importer  -> ``'L/s'``   (raw EDF physical value)
* cpap-parser ResMed path -> ``'L/min'`` (cpap-py already reports L/min)

Because the stored number is meaningless without its unit, every backend
consumer — therapy score, PDF/report export, AI summary context, and API
responses — must convert through :func:`leak_to_lpm` instead of assuming a
unit (the old ``avg_leak * 60`` shortcut silently inflated parser nights 60x).
This mirrors the frontend ``leakToLpm`` helper in ``frontend/src/lib/units.ts``
so the two layers can never disagree.
"""

from __future__ import annotations

from math import isfinite

#: Multiplicative factors that convert a value in the keyed unit to L/min.
_LPM_FACTORS: dict[str, float] = {
    "L/s": 60.0,
    "mL/s": 0.06,
    "L/min": 1.0,
}

#: Unit assumed when a session carries no explicit ``leak_unit`` (legacy rows
#: predating the leak-semantics migration were stored in L/s).
DEFAULT_LEAK_UNIT = "L/s"


def leak_to_lpm(value: object, source_unit: str | None = DEFAULT_LEAK_UNIT) -> float | None:
    """Convert a stored leak ``value`` to liters/minute using its ``source_unit``.

    Args:
        value: The stored leak number (or ``None``).
        source_unit: The session's ``leak_unit``. ``None``/empty is treated as
            the legacy default (``'L/s'``); an unrecognized non-empty unit
            returns ``None`` rather than guessing.

    Returns:
        The leak in L/min, or ``None`` when the value is missing/non-numeric or
        the unit is unknown.
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(numeric):
        return None
    unit = (source_unit or DEFAULT_LEAK_UNIT).strip()
    factor = _LPM_FACTORS.get(unit)
    if factor is None:
        return None
    return numeric * factor
