"""Unit tests for the single leak-normalization policy (api/leak_units.py)."""

import math

import pytest

from api.leak_units import leak_to_lpm


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        # Legacy ResMed stores L/s -> multiply by 60.
        (0.2, "L/s", 12.0),
        (0.1704, "L/s", 10.224),
        # cpap-parser stores L/min already -> passthrough, NOT * 60.
        (12.0, "L/min", 12.0),
        (2.4, "L/min", 2.4),
        # Millilitres per second.
        (200.0, "mL/s", 12.0),
    ],
)
def test_leak_to_lpm_converts_by_unit(value, unit, expected):
    result = leak_to_lpm(value, unit)
    assert result is not None
    assert math.isclose(result, expected, rel_tol=1e-9)


def test_parser_lpm_is_not_multiplied_by_60():
    """The core regression: an L/min value must come back unchanged, not * 60."""
    assert leak_to_lpm(5.0, "L/min") == 5.0
    assert leak_to_lpm(5.0, "L/min") != 300.0


def test_missing_unit_defaults_to_legacy_lps():
    """Rows predating the leak-semantics migration carried no unit and were L/s."""
    assert leak_to_lpm(0.2, None) == 12.0
    assert leak_to_lpm(0.2) == 12.0
    assert leak_to_lpm(0.2, "") == 12.0


def test_unknown_unit_returns_none_instead_of_guessing():
    assert leak_to_lpm(12.0, "vendor-points") is None


def test_non_numeric_or_missing_value_returns_none():
    assert leak_to_lpm(None, "L/min") is None
    assert leak_to_lpm("not-a-number", "L/s") is None
    assert leak_to_lpm(float("nan"), "L/s") is None
