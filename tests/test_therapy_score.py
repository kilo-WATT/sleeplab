from api.therapy_score import compute_therapy_score, grade_for_score


def _session(**overrides):
    data = {
        "ahi": 2.0,
        "avg_leak": 0.1,
        "duration_seconds": 8 * 3600,
        "has_spo2": True,
        "avg_spo2": 96.0,
        "min_spo2": 93.0,
        "manufacturer": "ResMed",
        "parser_validated": True,
    }
    data.update(overrides)
    return data


def test_resmed_session_with_normal_validated_data_scores_high():
    score = compute_therapy_score(_session())

    assert score.total >= 90
    assert score.grade == "A"
    assert score.low_confidence is False
    assert score.components.ahi is not None
    assert score.components.leak is not None
    assert score.components.duration is not None
    assert score.components.spo2 is not None


def test_resmed_large_leak_reduces_leak_component():
    score = compute_therapy_score(_session(avg_leak=0.8, ahi=8.0))

    assert score.components.leak is not None
    assert score.components.leak.score < score.components.leak.max_score
    assert "Large leak" in score.callout


def test_unknown_manufacturer_scores_available_non_leak_components():
    score = compute_therapy_score(_session(manufacturer="Unknown", avg_leak=0.8))

    assert score.total > 0
    assert score.components.ahi is not None
    assert score.components.duration is not None
    assert score.components.spo2 is not None
    assert score.components.leak is None
    assert "Large leak" not in score.callout


def test_missing_manufacturer_does_not_assume_resmed_leak_units():
    score = compute_therapy_score(_session(manufacturer=None, avg_leak=0.8))

    assert score.components.leak is None
    assert score.components.ahi is not None
    assert score.components.duration is not None
    assert score.components.spo2 is not None


def test_unvalidated_parser_sets_low_confidence_but_still_scores():
    score = compute_therapy_score(_session(parser_validated=False))

    assert score.total > 0
    assert score.low_confidence is True
    assert "Scoring confidence is lower" in score.callout


def test_without_spo2_redistributes_spo2_weight():
    score = compute_therapy_score(_session(has_spo2=False, avg_spo2=None, min_spo2=None))

    assert score.components.spo2 is None
    max_total = sum(
        component.max_score
        for component in [score.components.ahi, score.components.leak, score.components.duration]
        if component is not None
    )
    assert max_total == 100


def test_unavailable_leak_redistributes_leak_weight():
    score = compute_therapy_score(_session(avg_leak=None))

    assert score.components.leak is None
    assert score.components.ahi is not None
    assert score.components.duration is not None
    assert score.components.spo2 is not None
    assert score.components.ahi.max_score + score.components.duration.max_score + score.components.spo2.max_score == 100


def test_unconfirmed_manufacturer_redistributes_leak_weight():
    score = compute_therapy_score(_session(manufacturer="Fisher & Paykel", avg_leak=0.8))

    assert score.components.leak is None
    assert score.components.ahi is not None
    assert score.components.duration is not None
    assert score.components.spo2 is not None
    assert score.components.ahi.max_score + score.components.duration.max_score + score.components.spo2.max_score == 100


def test_parser_ls_leak_scaled_to_lpm_not_under_or_over_scaled():
    """A cpap-parser session stores leak in L/s; the component shows it in L/min.

    Regression (alpha.10): parser leak is the raw Leak.2s magnitude (L/s). A night
    with avg_leak 0.2 L/s must display as 12 L/min — not 0.2 (the alpha.9 bug, which
    mislabeled it L/min and so under-scaled ~60x) and not 720 (the original blanket
    pre-alpha.9 over-scale). 12 L/min is below the 24 L/min threshold -> full credit.
    """
    score = compute_therapy_score(_session(avg_leak=0.2, leak_unit="L/s"))

    assert score.components.leak is not None
    assert score.components.leak.value == 12.0
    assert score.components.leak.unit == "L/min"
    assert score.components.leak.score == score.components.leak.max_score


def test_same_physical_leak_scores_identically_across_units():
    """The same physical leak scores the same regardless of stored unit.

    0.5 L/s and an explicit 30 L/min are the same leak; both exceed the 24 L/min
    threshold and must produce the same leak component value and score.
    """
    via_lps = compute_therapy_score(_session(avg_leak=0.5, leak_unit="L/s", ahi=8.0))
    via_lpm = compute_therapy_score(_session(avg_leak=30.0, leak_unit="L/min", ahi=8.0))

    assert via_lps.components.leak is not None and via_lpm.components.leak is not None
    assert via_lps.components.leak.value == via_lpm.components.leak.value == 30.0
    assert via_lps.components.leak.score == via_lpm.components.leak.score
    assert via_lps.components.leak.score < via_lps.components.leak.max_score


def test_parser_large_leak_reduces_component_and_flags_callout():
    """A genuinely large parser leak (0.8 L/s = 48 L/min) still penalizes the component."""
    score = compute_therapy_score(_session(avg_leak=0.8, leak_unit="L/s", ahi=2.0))

    assert score.components.leak is not None
    assert score.components.leak.value == 48.0
    assert score.components.leak.score < score.components.leak.max_score
    assert "low AHI may be understated" in score.callout


def test_grade_mapping():
    assert grade_for_score(90) == "A"
    assert grade_for_score(80) == "B"
    assert grade_for_score(70) == "C"
    assert grade_for_score(60) == "D"
    assert grade_for_score(59) == "F"


def test_large_leak_with_low_ahi_mentions_understated_ahi():
    score = compute_therapy_score(_session(avg_leak=0.8, ahi=2.0))

    assert "low AHI may be understated" in score.callout


def test_invalid_or_null_inputs_do_not_crash():
    score = compute_therapy_score({
        "ahi": "not-a-number",
        "avg_leak": None,
        "duration_seconds": None,
        "has_spo2": True,
        "avg_spo2": None,
        "min_spo2": None,
        "manufacturer": "Unknown",
        "parser_validated": False,
    })

    assert score.total == 0
    assert score.grade == "F"
    assert score.components.ahi is None
    assert score.components.leak is None
    assert score.components.duration is None
    assert score.components.spo2 is None
