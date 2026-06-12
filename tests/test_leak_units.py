"""Unit tests for the single leak-normalization policy (api/leak_units.py)."""

import math

import pytest

from api.leak_units import leak_to_lpm


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        # Both ResMed paths (legacy importer and cpap-parser) store raw Leak.2s
        # in L/s -> multiply by 60 for display.
        (0.2, "L/s", 12.0),
        (0.1704, "L/s", 10.224),
        # The OSCAR June-11 case: p95 of 0.04 L/s renders as 2.40 L/min.
        (0.04, "L/s", 2.4),
        # mL/s.
        (200.0, "mL/s", 12.0),
        # Defensive L/min branch: a value already in L/min passes through. No
        # SleepLab import path stores L/min today, but the helper must not double-
        # convert if a future adapter reports a true L/min value.
        (12.0, "L/min", 12.0),
        (2.4, "L/min", 2.4),
    ],
)
def test_leak_to_lpm_converts_by_unit(value, unit, expected):
    result = leak_to_lpm(value, unit)
    assert result is not None
    assert math.isclose(result, expected, rel_tol=1e-9)


def test_parser_ls_p95_renders_as_oscar_lpm():
    """The real regression: parser p95 leak of 0.04 L/s must display as 2.4 L/min.

    Before alpha.10 the parser persisted 0.04 with leak_unit='L/min', so it rendered
    as 0.04 (~0.0) L/min — ~60x too low versus OSCAR's 2.40 L/min. Stored truthfully
    as L/s, it scales to 2.4.
    """
    assert leak_to_lpm(0.04, "L/s") == 2.4
    assert leak_to_lpm(0.14, "L/s") == pytest.approx(8.4)  # OSCAR p99.5 ~8.40 L/min


def test_true_lmin_value_is_not_multiplied_by_60():
    """An explicit L/min value must come back unchanged (defensive branch)."""
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
