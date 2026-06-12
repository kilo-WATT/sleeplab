"""Legacy-vs-cpap-parser DB parity snapshot + classification helpers.

This is the analysis core of the ResMed cutover parity harness
(``tests/test_resmed_cutover_db_parity.py``). It is deliberately split out so the
*classification* logic can be unit-tested with synthetic snapshots — no database
and no parser backend required — while the DB-touching snapshot function is
exercised only by the gated harness test.

Design goals (see ``docs/sleeplab_2_resmed_cutover_readiness_audit.md``):

* **Safe.** Snapshots capture only *aggregates* (row counts, distinct counts, and
  category-label sets such as ``provenance_status`` / ``event_type`` / channel
  names). No serials, no raw timestamps, no source paths, no DB-generated ids —
  nothing that could leak a private identifier. The two import paths even key
  sessions on different calendars (legacy on the DATALOG folder date, cpap-parser
  on the shifted STR date), so aggregate comparison is also the only *robust*
  comparison.
* **Repeatable.** Each table query is wrapped in a ``SAVEPOINT`` so a missing
  table/column degrades to a recorded ``_error`` instead of aborting the
  transaction or crashing the harness.
* **Honest.** Known current differences (from the cutover audit) are classified as
  ``expected_difference`` and never hidden; anything else that differs surfaces as
  ``unexpected_difference``.
"""

from __future__ import annotations

from typing import Any

# Category labels for a per-table parity verdict.
EQUAL = "equal"
EXPECTED_DIFFERENCE = "expected_difference"
UNEXPECTED_DIFFERENCE = "unexpected_difference"
MISSING_IN_LEGACY = "missing_in_legacy"
MISSING_IN_PARSER = "missing_in_parser"
SKIPPED = "skipped"
NOT_IMPLEMENTED = "not_implemented"

VALID_CATEGORIES = frozenset(
    {
        EQUAL,
        EXPECTED_DIFFERENCE,
        UNEXPECTED_DIFFERENCE,
        MISSING_IN_LEGACY,
        MISSING_IN_PARSER,
        SKIPPED,
        NOT_IMPLEMENTED,
    }
)

#: Tables compared by the harness, in report order. Per-sample tables
#: (``session_metrics``/``session_waveform``) contribute **row counts only** —
#: never sample values or waveform blobs.
PARITY_TABLES: tuple[str, ...] = (
    "sessions",
    "session_blocks",
    "settings_snapshots",
    "session_events",
    "session_spo2",
    "signal_channels",
    "derived_values",
    "session_metrics",
    "session_waveform",
    "import_source_files",
    "nightly_therapy_aggregates",
)

#: Differences the cutover audit already documents. A table listed here whose two
#: snapshots differ is an ``expected_difference`` (with this reason), not an
#: ``unexpected_difference``. Keyed by table; the reason is shown in the report.
KNOWN_DIFFERENCES: dict[str, str] = {
    "sessions": (
        "SleepLab 2.0 uses one session row per machine-local night with therapy "
        "intervals represented by session_blocks; legacy block-scoped row counts "
        "are accepted only when block, usage, and event totals still match"
    ),
    "session_blocks": (
        "block source differs (legacy: STR mask-interval + PLD recording-span "
        "blocks; cpap-parser: cpap-py file-session blocks, now honestly labeled "
        "source_kind='recording_span' — no longer mislabeled as "
        "resmed_str_mask_interval) — audit P1 #5"
    ),
    "settings_snapshots": (
        "cpap-parser now persists loader-provided therapy_mode snapshots, but "
        "legacy STR snapshots contain additional settings fields that cpap-parser "
        "does not expose; full settings parity remains partial — audit P0 #4"
    ),
    "session_spo2": (
        "legacy parses SA2/SAD oximetry into session_spo2; cpap-parser path drops "
        "oximetry (has_spo2 hardcoded False) — audit P0 #3"
    ),
    "session_events": (
        "DECIDED POLICY (SleepLab 2.0 = Option A): preserve the full device-scored "
        "event list. Both paths read an identical raw EVE event list (the EVE parsers "
        "agree exactly) and large-leak derivation matches, but legacy clips device-"
        "scored events to each PLD recording window (events_for_block), dropping events "
        "in inter-block gaps / at boundaries, while the cpap-parser path keeps the full "
        "device-scored list. So parser >= legacy is the EXPECTED, accepted direction "
        "(the parser is not overcounting — it preserves events legacy dropped); legacy "
        "recording-window clipping is treated as legacy behavior, not the target. "
        "Accepted only while consistent with that policy: matching event-type sets and "
        "parser totals/AHI >= legacy. A type-set mismatch, a net-negative (parser < "
        "legacy), or evidence of duplication stays unexpected — inspect type_counts / "
        "ahi_event_count. Do not claim exact OSCAR parity here without an OSCAR "
        "reference — audit P1 event-window policy"
    ),
    "signal_channels": (
        "channel metadata source differs (legacy: raw EDF header; cpap-parser: "
        "normalized ImportRun.signals) — audit P1 #8"
    ),
    "import_source_files": (
        "both uploaded-root paths start with the same persisted source manifest, "
        "and parser preserves exact STR.edf settings linkage while "
        "cpap-parser emits synthetic source ids that cannot be mapped safely — audit P1 #7"
    ),
    "derived_values": (
        "derived-value key vocabulary and granularity differ (legacy: per-PLD-block "
        "event-count + stat keys incl. apnea/arousal counts; cpap-parser: per-night "
        "usage-semantics keys incl. computed/recording-span hours, has_detailed_data) "
        "— consequence of the granularity split, audit P1 #6"
    ),
    "session_waveform": (
        "event-windowed high-rate waveform row counts differ slightly (~0.1%) from "
        "event-onset rebasing / window-merge boundary differences between the two "
        "paths — row counts only, no blobs compared; audit P1 #8 area"
    ),
    "nightly_therapy_aggregates": (
        "derived view over sessions/blocks; usage differs because the parser path "
        "has no STR mask intervals: summary-only nights now contribute STR-reported "
        "therapy usage (recovered via the session-duration fallback), but detailed "
        "nights still contribute recording spans rather than per-mask therapy time "
        "(usage_source='recording_spans' vs legacy 'resmed_str_mask_intervals'). "
        "Full numeric parity needs upstream mask intervals or a view/product "
        "decision — audit P1 #6"
    ),
}


def _safe_row(conn: Any, sql: str, params: tuple) -> Any:
    """Run a single-row query under a SAVEPOINT; return the row or an error marker.

    A missing table/column (schema drift) rolls back just this statement and
    returns ``{"_error": ...}`` so the snapshot never aborts the surrounding
    transaction or crashes the harness.
    """
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT parity_q")
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            cur.execute("RELEASE SAVEPOINT parity_q")
            return row
        except Exception as exc:  # noqa: BLE001 — schema drift must not crash the harness
            cur.execute("ROLLBACK TO SAVEPOINT parity_q")
            return {"_error": f"{type(exc).__name__}: {exc}"}


def _scalar(row: Any, default: Any = None) -> Any:
    if isinstance(row, dict):  # error marker
        return row
    if row is None:
        return default
    return row[0]


def snapshot_parity_tables(conn: Any, *, machine_id: str, import_run_id: str) -> dict[str, dict]:
    """Capture a redacted aggregate snapshot of the parity tables for one machine.

    Scoped to ``machine_id`` (and ``import_run_id`` for run-scoped tables) so the
    legacy and cpap-parser writes — which the harness performs under *different*
    machine ids in the same rolled-back transaction — never bleed together.

    Returns ``{table: {field: aggregate}}``. Every value is a count, a distinct
    count, or a sorted list of category labels — never a serial, timestamp, path,
    or id. A query that fails (missing table/column) yields ``{"_error": ...}`` for
    that table.
    """
    m = (machine_id,)
    snap: dict[str, dict] = {}

    snap["sessions"] = {
        "row_count": _scalar(_safe_row(conn, "SELECT COUNT(*) FROM sessions WHERE machine_id = %s", m)),
        "distinct_dates": _scalar(
            _safe_row(conn, "SELECT COUNT(DISTINCT folder_date) FROM sessions WHERE machine_id = %s", m)
        ),
        "distinct_block_index": _scalar(
            _safe_row(conn, "SELECT COUNT(DISTINCT block_index) FROM sessions WHERE machine_id = %s", m)
        ),
        "max_block_index": _scalar(
            _safe_row(conn, "SELECT COALESCE(MAX(block_index), 0) FROM sessions WHERE machine_id = %s", m)
        ),
        "has_spo2_count": _scalar(
            _safe_row(conn, "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND has_spo2", m)
        ),
        "total_ahi_events": _scalar(
            _safe_row(conn, "SELECT COALESCE(SUM(total_ahi_events), 0) FROM sessions WHERE machine_id = %s", m)
        ),
        "provenance_statuses": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT provenance_status ORDER BY provenance_status), '{}') "
                "FROM sessions WHERE machine_id = %s",
                m,
            )
        ),
    }
    snap["session_blocks"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_blocks b JOIN sessions s ON s.id = b.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
        "block_kinds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT b.block_kind ORDER BY b.block_kind), '{}') "
                "FROM session_blocks b JOIN sessions s ON s.id = b.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        # ``source_kind`` is the authority label the nightly aggregate keys on:
        # ``resmed_str_mask_interval`` (authoritative therapy) vs ``recording_span``
        # (wall-clock recording extent, a usage *proxy* only). Capturing the distinct
        # set makes it obvious whether a path is contributing real mask intervals or
        # recording spans — the crux of the usage parity gap.
        "source_kinds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT b.source_kind ORDER BY b.source_kind), '{}') "
                "FROM session_blocks b JOIN sessions s ON s.id = b.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        # Recording-span seconds vs therapy seconds, summed across all blocks. These
        # diverge exactly when a path stores recording spans without true per-mask
        # therapy time, so reporting both keeps the "wrapper vs candy" distinction
        # visible rather than collapsing it into one ambiguous total.
        "total_recording_seconds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(SUM(b.recording_duration_seconds), 0) "
                "FROM session_blocks b JOIN sessions s ON s.id = b.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        "total_therapy_seconds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(SUM(b.therapy_duration_seconds), 0) "
                "FROM session_blocks b JOIN sessions s ON s.id = b.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        "with_source_files": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_blocks b JOIN sessions s ON s.id = b.session_id "
                "WHERE s.machine_id = %s AND array_length(b.source_file_ids, 1) IS NOT NULL",
                m,
            )
        ),
    }
    snap["settings_snapshots"] = {
        "row_count": _scalar(
            _safe_row(conn, "SELECT COUNT(*) FROM settings_snapshots WHERE machine_id = %s", m)
        ),
        # Distinct setting *names* (not values) across all snapshots — safe category
        # labels. Row counts can match while the persisted setting set differs
        # (legacy persists the full STR settings; the parser path only therapy_mode),
        # so this keeps the verdict honest rather than a false "equal".
        "setting_keys": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT k ORDER BY k), '{}') "
                "FROM settings_snapshots s, jsonb_object_keys(s.normalized_settings) AS k "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
        "therapy_mode_values": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT normalized_settings->>'therapy_mode' "
                "ORDER BY normalized_settings->>'therapy_mode'), '{}') "
                "FROM settings_snapshots WHERE machine_id = %s "
                "AND normalized_settings->>'therapy_mode' IS NOT NULL",
                m,
            )
        ),
        "session_therapy_mode_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND therapy_mode IS NOT NULL",
                m,
            )
        ),
        "session_mask_type_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND mask_type IS NOT NULL",
                m,
            )
        ),
        "session_humidity_level_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND humidity_level IS NOT NULL",
                m,
            )
        ),
        "session_temperature_c_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND temperature_c IS NOT NULL",
                m,
            )
        ),
    }
    snap["session_events"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_events e JOIN sessions s ON s.id = e.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
        "event_types": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT e.event_type ORDER BY e.event_type), '{}') "
                "FROM session_events e JOIN sessions s ON s.id = e.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        # Per-type counts as ``type=count`` labels (sorted). Safe aggregate: only
        # event-type category names and integer counts, never timestamps or onsets.
        # This is what localizes a total-count divergence to a specific event type
        # (e.g. is the gap in Hypopnea, or in derived Large Leak?) without exposing
        # any individual event.
        "type_counts": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(t.event_type || '=' || t.cnt ORDER BY t.event_type), '{}') "
                "FROM (SELECT e.event_type, COUNT(*) AS cnt FROM session_events e "
                "JOIN sessions s ON s.id = e.session_id WHERE s.machine_id = %s "
                "GROUP BY e.event_type) t",
                m,
            )
        ),
        # AHI-contributing events only (apnea/hypopnea family) — the medically
        # load-bearing subset, kept separate from derived/annotation events.
        "ahi_event_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_events e JOIN sessions s ON s.id = e.session_id "
                "WHERE s.machine_id = %s AND e.event_type IN "
                "('Central Apnea', 'Obstructive Apnea', 'Hypopnea', 'Apnea')",
                m,
            )
        ),
        # Zero-/null-duration events. A path that emits instantaneous markers the
        # other does not will show here; isolates duration-semantics differences
        # from genuine extra detections.
        "zero_duration_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_events e JOIN sessions s ON s.id = e.session_id "
                "WHERE s.machine_id = %s AND COALESCE(e.duration_seconds, 0) = 0",
                m,
            )
        ),
    }
    snap["session_spo2"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_spo2 p JOIN sessions s ON s.id = p.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
    }
    snap["signal_channels"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM signal_channels c JOIN sessions s ON s.id = c.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
        "normalized_names": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT c.normalized_name ORDER BY c.normalized_name), '{}') "
                "FROM signal_channels c JOIN sessions s ON s.id = c.session_id WHERE s.machine_id = %s",
                m,
            )
        ),
        "units": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT c.unit ORDER BY c.unit), '{}') "
                "FROM signal_channels c JOIN sessions s ON s.id = c.session_id "
                "WHERE s.machine_id = %s AND c.unit IS NOT NULL",
                m,
            )
        ),
    }
    snap["derived_values"] = {
        "row_count": _scalar(
            _safe_row(conn, "SELECT COUNT(*) FROM derived_values WHERE machine_id = %s", m)
        ),
        "keys": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT key ORDER BY key), '{}') "
                "FROM derived_values WHERE machine_id = %s",
                m,
            )
        ),
    }
    snap["session_metrics"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_metrics x JOIN sessions s ON s.id = x.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
    }
    snap["session_waveform"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM session_waveform x JOIN sessions s ON s.id = x.session_id "
                "WHERE s.machine_id = %s",
                m,
            )
        ),
    }
    snap["import_source_files"] = {
        "row_count": _scalar(
            _safe_row(conn, "SELECT COUNT(*) FROM import_source_files WHERE import_run_id = %s", (import_run_id,))
        ),
        "used_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM import_source_files "
                "WHERE import_run_id = %s AND disposition = 'used'",
                (import_run_id,),
            )
        ),
        "unknown_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM import_source_files "
                "WHERE import_run_id = %s AND disposition = 'unknown'",
                (import_run_id,),
            )
        ),
        "skipped_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM import_source_files "
                "WHERE import_run_id = %s AND disposition = 'skipped'",
                (import_run_id,),
            )
        ),
        "roles": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT parser_role ORDER BY parser_role), '{}') "
                "FROM import_source_files WHERE import_run_id = %s",
                (import_run_id,),
            )
        ),
        "linked_blocks": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(DISTINCT sf.id) FROM import_source_files sf "
                "JOIN session_blocks b ON sf.id = ANY(b.source_file_ids) "
                "WHERE sf.import_run_id = %s",
                (import_run_id,),
            )
        ),
        "linked_events": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(DISTINCT sf.id) FROM import_source_files sf "
                "JOIN session_events e ON e.source_file_id = sf.id "
                "WHERE sf.import_run_id = %s",
                (import_run_id,),
            )
        ),
        "linked_channels": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(DISTINCT sf.id) FROM import_source_files sf "
                "JOIN signal_channels c ON c.source_file_id = sf.id "
                "WHERE sf.import_run_id = %s",
                (import_run_id,),
            )
        ),
        "linked_settings": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(DISTINCT sf.id) FROM import_source_files sf "
                "JOIN settings_snapshots s ON sf.id = ANY(s.source_file_ids) "
                "WHERE sf.import_run_id = %s",
                (import_run_id,),
            )
        ),
    }
    snap["nightly_therapy_aggregates"] = {
        "row_count": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM nightly_therapy_aggregates WHERE machine_id = %s",
                m,
            )
        ),
        # The headline usage total the comparison turns red on. Read alongside
        # ``usage_sources`` below: a total built from ``recording_spans`` is not the
        # same quantity as one built from ``resmed_str_mask_intervals`` even when the
        # numbers look comparable.
        "total_usage_seconds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(SUM(usage_seconds), 0) "
                "FROM nightly_therapy_aggregates WHERE machine_id = %s",
                m,
            )
        ),
        # STR-reported usage carried on the view (authoritative therapy as the device
        # reported it). Lets the report show how close each path's selected usage is
        # to the device's own reported therapy time.
        "total_summary_reported_seconds": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(SUM(summary_reported_usage_seconds), 0) "
                "FROM nightly_therapy_aggregates WHERE machine_id = %s",
                m,
            )
        ),
        # Whether each night's usage came from mask intervals or recording spans —
        # the single most important label for interpreting the totals above.
        "usage_sources": _scalar(
            _safe_row(
                conn,
                "SELECT COALESCE(array_agg(DISTINCT usage_source ORDER BY usage_source), '{}') "
                "FROM nightly_therapy_aggregates WHERE machine_id = %s",
                m,
            )
        ),
        # Nights the view reports as zero therapy usage. This is the direct symptom of
        # the dropped summary-only nights (was 37 on the parser side before the
        # summary-reported fallback recovered their STR usage).
        "zero_usage_nights": _scalar(
            _safe_row(
                conn,
                "SELECT COUNT(*) FROM nightly_therapy_aggregates "
                "WHERE machine_id = %s AND COALESCE(usage_seconds, 0) = 0",
                m,
            )
        ),
    }
    return snap


def classify_parity(
    legacy: dict[str, dict] | None,
    parser: dict[str, dict] | None,
    *,
    tables: tuple[str, ...] = PARITY_TABLES,
    known_differences: dict[str, str] = KNOWN_DIFFERENCES,
) -> dict[str, dict]:
    """Classify per-table parity between two snapshots.

    Either side may be ``None`` (that path was not run — e.g. the parser backend or
    a test DB was unavailable): every table is then ``skipped``. A table present in
    only one snapshot is ``missing_in_<other>``. A table whose query errored on
    either side is ``not_implemented``. Otherwise equal snapshots are ``equal``,
    and unequal ones are ``expected_difference`` when listed in
    ``known_differences`` (carrying its reason) or ``unexpected_difference`` when
    not — the latter is what a reviewer must investigate.

    Two tables get a *policy-aware* verdict instead of a flat known-difference
    lookup, so an accepted shape is not confused with a genuine regression:

    * ``sessions`` — the night-level-vs-block-scoped row-count difference is only
      accepted when block, usage, and event totals reconcile
      (:func:`_session_shape_totals_match`).
    * ``session_events`` — SleepLab 2.0 preserves the full device-scored event list
      (Option A), so a difference is accepted only when it matches that policy:
      equal event-type sets and parser totals/AHI ``>=`` legacy. A type-set
      mismatch, a net-negative, or unavailable fields stay
      ``unexpected_difference`` (:func:`_event_policy_difference_accepted`).

    Returns ``{table: {"category", "reason", "legacy", "parser"}}``.
    """
    report: dict[str, dict] = {}
    for table in tables:
        legacy_side = legacy.get(table) if legacy is not None else None
        parser_side = parser.get(table) if parser is not None else None

        if legacy is None or parser is None:
            report[table] = _verdict(
                SKIPPED,
                "one or both import paths were not run (no test DB / parser backend)",
                legacy_side,
                parser_side,
            )
            continue
        if legacy_side is None and parser_side is None:
            report[table] = _verdict(NOT_IMPLEMENTED, "table not captured on either side", None, None)
            continue
        if legacy_side is None:
            report[table] = _verdict(MISSING_IN_LEGACY, "captured only on the parser side", None, parser_side)
            continue
        if parser_side is None:
            report[table] = _verdict(MISSING_IN_PARSER, "captured only on the legacy side", legacy_side, None)
            continue
        if _errored(legacy_side) or _errored(parser_side):
            report[table] = _verdict(
                NOT_IMPLEMENTED, "snapshot query failed (missing table/column)", legacy_side, parser_side
            )
            continue

        if legacy_side == parser_side:
            report[table] = _verdict(EQUAL, "", legacy_side, parser_side)
        elif table == "sessions":
            totals_match, reason = _session_shape_totals_match(legacy, parser)
            category = EXPECTED_DIFFERENCE if totals_match else UNEXPECTED_DIFFERENCE
            report[table] = _verdict(category, reason, legacy_side, parser_side)
        elif table == "session_events":
            accepted, reason = _event_policy_difference_accepted(legacy_side, parser_side)
            category = EXPECTED_DIFFERENCE if accepted else UNEXPECTED_DIFFERENCE
            report[table] = _verdict(category, reason, legacy_side, parser_side)
        elif table in known_differences:
            report[table] = _verdict(EXPECTED_DIFFERENCE, known_differences[table], legacy_side, parser_side)
        else:
            report[table] = _verdict(
                UNEXPECTED_DIFFERENCE,
                "differs and is not a documented cutover difference — investigate",
                legacy_side,
                parser_side,
            )
    return report


def _session_shape_totals_match(
    legacy: dict[str, dict], parser: dict[str, dict]
) -> tuple[bool, str]:
    """Guard the accepted night-level-vs-block-scoped session row difference.

    Block rows and nightly usage seconds must reconcile exactly. Event totals need
    not be *equal*: under the device-scored-event policy (Option A) the parser may
    legitimately retain more events than legacy, so an event difference is allowed
    as long as it is the accepted policy difference
    (:func:`_event_policy_difference_accepted`); a type-set mismatch or a
    net-negative still blocks acceptance.
    """
    comparisons = (
        ("block rows", "session_blocks", "row_count"),
        ("nightly usage seconds", "nightly_therapy_aggregates", "total_usage_seconds"),
    )
    mismatches: list[str] = []
    for label, table, field in comparisons:
        legacy_value = legacy.get(table, {}).get(field)
        parser_value = parser.get(table, {}).get(field)
        if legacy_value is None or parser_value is None:
            mismatches.append(f"{label} unavailable ({legacy_value!r} vs {parser_value!r})")
        elif legacy_value != parser_value:
            mismatches.append(f"{label} differ ({legacy_value!r} vs {parser_value!r})")

    legacy_events = legacy.get("session_events", {})
    parser_events = parser.get("session_events", {})
    if legacy_events != parser_events:
        accepted, event_reason = _event_policy_difference_accepted(legacy_events, parser_events)
        if not accepted:
            mismatches.append(f"event totals not an accepted policy difference — {event_reason}")

    if mismatches:
        return (
            False,
            "session row-count difference is not accepted because "
            + "; ".join(mismatches),
        )
    return (
        True,
        "SleepLab 2.0 uses night-level sessions plus session_blocks; differing "
        "session row counts are accepted because block and usage totals match and any "
        "event-count difference is the accepted device-scored-event policy (parser >= legacy)",
    )


def _event_policy_difference_accepted(
    legacy_events: dict, parser_events: dict
) -> tuple[bool, str]:
    """Guard the accepted device-scored-event policy difference (Option A).

    SleepLab 2.0 preserves the full device-scored event list; legacy clips events to
    PLD recording windows. A ``session_events`` difference is therefore accepted as
    this *policy* difference only when it is consistent with it:

    * event-type sets are equal (no type-mapping drift), and
    * the parser retains at least as many total events and AHI events as legacy
      (parser ``>=`` legacy — the parser preserves, it does not drop).

    Anything else stays ``unexpected`` so the harness keeps flagging genuine
    problems rather than waving them through:

    * a type-set mismatch (parser invents or drops an event type),
    * a net-negative (parser persists fewer events/AHI than legacy — a regression
      or dropped data, the opposite of the preserve-everything policy), or
    * missing fields needed to make the call.

    Note: this cannot, from aggregates alone, prove the extra events are unique
    rather than duplicated. Duplication must still be watched via ``type_counts`` /
    ``ahi_event_count`` and the per-session DB dedupe; the accepted verdict is not a
    duplication clearance.
    """
    le_types = set(legacy_events.get("event_types") or [])
    pa_types = set(parser_events.get("event_types") or [])
    if le_types != pa_types:
        return (
            False,
            "event-type sets differ ("
            f"legacy-only={sorted(le_types - pa_types)}, parser-only={sorted(pa_types - le_types)}"
            ") — type-mapping mismatch, not the device-scored clipping policy; investigate",
        )

    le_total = legacy_events.get("row_count")
    pa_total = parser_events.get("row_count")
    le_ahi = legacy_events.get("ahi_event_count")
    pa_ahi = parser_events.get("ahi_event_count")
    if any(v is None for v in (le_total, pa_total, le_ahi, pa_ahi)):
        return (
            False,
            "event totals / ahi_event_count unavailable, cannot confirm the "
            "device-scored policy direction — investigate",
        )
    if pa_total < le_total or pa_ahi < le_ahi:
        return (
            False,
            "parser persists FEWER events than legacy "
            f"(total {pa_total} vs {le_total}, ahi {pa_ahi} vs {le_ahi}); SleepLab 2.0 "
            "preserves the full device-scored list, so a net-negative is unexpected "
            "(dropped events or a regression) — investigate",
        )
    return (
        True,
        KNOWN_DIFFERENCES.get("session_events", "")
        + f" [accepted: types match, parser >= legacy — total {pa_total} vs {le_total}, "
        f"ahi {pa_ahi} vs {le_ahi}]",
    )


def _errored(side: dict) -> bool:
    return isinstance(side, dict) and "_error" in side


def _verdict(category: str, reason: str, legacy: Any, parser: Any) -> dict:
    return {"category": category, "reason": reason, "legacy": legacy, "parser": parser}


def format_report(report: dict[str, dict]) -> str:
    """Render a classified parity report as a readable, log-friendly table."""
    lines = ["ResMed legacy-vs-cpap-parser DB parity report", "=" * 46]
    width = max((len(t) for t in report), default=0)
    for table, verdict in report.items():
        lines.append(f"{table.ljust(width)}  {verdict['category'].upper()}")
        if verdict["reason"]:
            lines.append(f"{' ' * width}    reason: {verdict['reason']}")
        lines.append(f"{' ' * width}    legacy: {verdict['legacy']}")
        lines.append(f"{' ' * width}    parser: {verdict['parser']}")
    return "\n".join(lines)


def categories_present(report: dict[str, dict]) -> set[str]:
    """The set of categories appearing in a report (for assertions/summaries)."""
    return {verdict["category"] for verdict in report.values()}
