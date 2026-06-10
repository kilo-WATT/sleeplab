"""Tests pinning the ResMed waveform storage row-count estimate.

These lock the numbers recorded in ``docs/sleeplab_2_alpha_6_checklist.md`` §3 so
the event-window vs full-night decision rests on a tested calculation. Pure
arithmetic — no parser, no database, no storage change.
"""

from importer.waveform_estimate import (
    BRP_RATE_HZ,
    EVENT_WINDOW_AFTER_S,
    EVENT_WINDOW_BEFORE_S,
    event_window_row_count,
    full_night_row_count,
)


def test_brp_rate_and_window_constants_match_storage_path():
    """Constants mirror the storage path (25 Hz; 120 s before / 180 s after)."""
    assert BRP_RATE_HZ == 25.0
    assert EVENT_WINDOW_BEFORE_S == 120
    assert EVENT_WINDOW_AFTER_S == 180


def test_full_night_row_count_matches_checklist_estimate():
    """§3: 25 rows/s → 90k rows/hour, 720k for 8 h, ~21.6M for a 30-night card."""
    assert full_night_row_count(1) == 90_000
    assert full_night_row_count(8) == 720_000
    # A 30-night card of 8 h nights, one machine, flow+pressure only.
    assert full_night_row_count(8 * 30) == 21_600_000


def test_full_night_row_count_guards_nonpositive():
    assert full_night_row_count(0) == 0
    assert full_night_row_count(-5) == 0
    assert full_night_row_count(8, rate_hz=0) == 0


def test_event_window_single_event_is_one_padded_window():
    """One event → (120 + 180) s × 25 Hz = 7500 rows, far below a full night."""
    rows = event_window_row_count(1, recorded_hours=8)
    assert rows == (EVENT_WINDOW_BEFORE_S + EVENT_WINDOW_AFTER_S) * 25
    assert rows == 7500
    assert rows < full_night_row_count(8)


def test_event_window_scales_with_event_count_not_night_length():
    """Two non-overlapping events store twice one event's window."""
    one = event_window_row_count(1, recorded_hours=8)
    two = event_window_row_count(2, recorded_hours=8)
    assert two == 2 * one


def test_event_window_is_bounded_above_by_full_night():
    """A pathological event count can never exceed the full-night row count."""
    full_night = full_night_row_count(8)
    assert event_window_row_count(100_000, recorded_hours=8) == full_night


def test_event_window_zero_events_stores_nothing():
    """A night with no scored events stores no event-windowed waveform."""
    assert event_window_row_count(0, recorded_hours=8) == 0


def test_event_window_is_a_small_fraction_for_a_typical_night():
    """A handful of events stores ~1–2 orders of magnitude less than full-night.

    Pins §3's "stores a small fraction of 720k rows" claim for a typical night.
    """
    typical_events = 20
    event_rows = event_window_row_count(typical_events, recorded_hours=8)
    full_night = full_night_row_count(8)
    # Even before window merging, 20 events is well under a fifth of full-night.
    assert event_rows < full_night / 4
