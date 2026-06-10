"""Storage-sizing estimates for ResMed BRP waveform persistence.

Pure, dependency-free helpers that codify the row-count estimate recorded in
``docs/sleeplab_2_alpha_6_checklist.md`` §3 so the event-window vs full-night
storage decision rests on a tested calculation rather than prose that can drift.

These estimate **row counts** for the ``session_waveform`` table — which stores
*one row per timestamp* carrying both ``flow`` and ``pressure`` (migration
``013_add_session_waveform.sql``) — at the BRP sample rate. They are measurement
only: nothing here reads the database, parses a card, or changes any storage
behaviour. Full-night storage is **not** implemented; this module exists to size
the decision, not to make it.

Grounding facts (see ``importer/edf_parser.py:parse_brp`` and
``importer/db.py:replace_session_waveform`` / ``importer/loaders/persist.py``):

* BRP carries ``Flow.40ms`` + ``Press.40ms`` at **25 Hz** (1500 samples per 60 s
  record), and ``session_waveform`` keeps one row per timestamp → 25 rows/s.
* The current default stores only merged windows of **120 s before / 180 s
  after** each scored event, clipped to the recorded span — so storage scales
  with event count, not night length, and is bounded above by the full-night
  figure.
"""

from __future__ import annotations

#: BRP high-rate sample rate (Hz): ``Flow.40ms`` / ``Press.40ms`` at 25 Hz.
BRP_RATE_HZ = 25.0

#: Event-window padding (seconds) used by ``db.replace_session_waveform`` and
#: ``persist._write_session_waveform``.
EVENT_WINDOW_BEFORE_S = 120
EVENT_WINDOW_AFTER_S = 180


def full_night_row_count(recorded_hours: float, *, rate_hz: float = BRP_RATE_HZ) -> int:
    """``session_waveform`` rows to store a *whole* night at ``rate_hz``.

    One row per timestamp (flow and pressure share a row), so ``rate_hz`` rows per
    second. Returns ``0`` for a non-positive duration or rate.

    Reference: ``full_night_row_count(1) == 90_000`` (rows/hour),
    ``full_night_row_count(8) == 720_000`` (an 8 h night).
    """

    if recorded_hours <= 0 or rate_hz <= 0:
        return 0
    return round(recorded_hours * 3600 * rate_hz)


def event_window_row_count(
    event_count: int,
    recorded_hours: float,
    *,
    rate_hz: float = BRP_RATE_HZ,
    before_s: float = EVENT_WINDOW_BEFORE_S,
    after_s: float = EVENT_WINDOW_AFTER_S,
) -> int:
    """Upper-bound ``session_waveform`` rows for the current event-window scheme.

    Each scored event contributes a ``(before_s + after_s)``-second window at
    ``rate_hz``. This returns the **non-overlapping upper bound**: real merging of
    overlapping windows (``db._merge_waveform_windows``) only reduces the count,
    and it is clipped to :func:`full_night_row_count`, which it can never exceed
    (a recorded night cannot store more than every sample it contains).

    Returns ``0`` when there are no events or the rate is non-positive — a night
    with no scored events stores no event-windowed waveform.
    """

    if event_count <= 0 or rate_hz <= 0:
        return 0
    window_rows = round((before_s + after_s) * rate_hz)
    upper_bound = event_count * window_rows
    full_night = full_night_row_count(recorded_hours, rate_hz=rate_hz)
    if full_night <= 0:
        return upper_bound
    return min(upper_bound, full_night)
