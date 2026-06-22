"""Bridge + parser-reimport behavior on a bridged upstream-1.4 night.

Follow-up validation to the upstream 1.4 -> 2.0 bridge (beta.4). It answers a
concrete question: after a 1.4 database is bridged, if the user reimports SD-card
/ parser data for a night that already exists as a ``legacy_backfilled`` session,
what happens to that night?

These tests use only synthetic data (no private card data) and the same
``reconcile_machine`` / ``upsert_session`` helpers the real parser import path
uses, so they faithfully reproduce the production matching keys:

* the migration-023 legacy backfill files sessions under a synthetic
  ``legacy-session-v1`` machine, keyed ``legacy-session-v1:serial:<serial>``;
* a parser/native import resolves its machine as ``resmed-native-v2:serial:<serial>``.

Because the two machines have different ``identity_key`` values, and sessions
dedupe on the partial unique index ``(machine_id, source_session_key)``, a parser
import for the same night lands on a *different* machine and therefore a
*different* session row. The legacy night is fully preserved, the parser write is
idempotent on its own machine, and row-backed + chunk-backed waveforms coexist —
but the parser import does **not** merge into / enrich the existing legacy
session. That known limitation is asserted explicitly here so a future
enrichment change updates this test deliberately.

Requires Postgres; skips cleanly without it, like the rest of the DB-backed suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from importer import db as importer_db

_SERIAL = "BRIDGE-REIMPORT-SN"
_NIGHT = date(2026, 6, 1)
_NIGHT_START = datetime(2026, 6, 1, 22, 30, 0, tzinfo=UTC)


def _session_data(**overrides) -> dict:
    """Minimal valid ``upsert_session`` row, overridable per call."""
    data = {
        "session_id": "20260601_223000",
        "folder_date": _NIGHT,
        "block_index": 0,
        "start_datetime": _NIGHT_START,
        "pld_start_datetime": _NIGHT_START,
        "duration_seconds": 8100,
        "device_serial": _SERIAL,
        "manufacturer": "ResMed",
        "ahi": 3.1,
        "central_apnea_count": 0,
        "obstructive_apnea_count": 0,
        "hypopnea_count": 0,
        "apnea_count": 0,
        "arousal_count": 0,
        "total_ahi_events": 0,
        "avg_pressure": None,
        "p95_pressure": None,
        "avg_leak": None,
        "avg_resp_rate": None,
        "avg_tidal_vol": None,
        "avg_min_vent": None,
        "avg_snore": None,
        "avg_flow_lim": None,
        "has_spo2": False,
        "therapy_mode": None,
        "mask_type": None,
        "humidity_level": None,
        "temperature_c": None,
        "machine_tz": "UTC",
        "user_id": "user-1",
    }
    data.update(overrides)
    return data


def _insert_legacy_waveform_rows(cur, session_id: str, count: int = 3) -> None:
    """Seed legacy row-backed ``session_waveform`` samples for a session."""
    for i in range(count):
        cur.execute(
            """
            INSERT INTO session_waveform (session_id, ts, flow, pressure)
            VALUES (%s, %s + (%s || ' seconds')::interval, %s, %s)
            """,
            (session_id, _NIGHT_START, i, 10.0 + i, 8.0 + i),
        )


def _replace_parser_chunk(cur, session_id: str, import_run_id: str) -> None:
    """Mirror persist._write_waveform_chunks' replace semantics for one chunk."""
    cur.execute("DELETE FROM waveform_chunks WHERE session_id = %s", (session_id,))
    cur.execute(
        """
        INSERT INTO waveform_chunks (
            session_id, import_run_id, signal_name, unit, sample_rate_hz,
            start_time, end_time, chunk_index, sample_count, encoding,
            payload, uncompressed_bytes, compressed_bytes, adapter_id
        ) VALUES (
            %s, %s, 'flow_rate', 'L/s', 25.0, %s, %s + INTERVAL '1 second',
            0, 25, 'float32-le-zlib-v1', %s, 100, 40, 'resmed-cpap-parser-v1'
        )
        """,
        (session_id, import_run_id, _NIGHT_START, _NIGHT_START, b"\x00\x01\x02\x03"),
    )


def _new_import_run(cur, user_id: str, machine_id: str, fingerprint: str) -> str:
    """Create a minimal import_runs row and return its id."""
    cur.execute(
        """
        INSERT INTO import_runs (
            user_id, machine_id, adapter_id, source_type, source_fingerprint,
            status, validation_status, started_at
        ) VALUES (%s, %s, 'resmed-native-v2', 'directory', %s, 'running', 'partial', NOW())
        RETURNING id::text
        """,
        (user_id, machine_id, fingerprint),
    )
    return cur.fetchone()[0]


def test_parser_reimport_preserves_bridged_legacy_night_and_is_idempotent(db, test_user):
    """Parser reimport of a bridged legacy night: preserve, coexist, no duplication.

    Proves the current (beta.4) product behavior end to end at the persistence
    matching layer:

    * the bridged ``legacy_backfilled`` session and its row-backed
      ``session_waveform`` samples survive a parser import untouched;
    * the parser import resolves a *different* machine than the legacy backfill
      (``resmed-native-v2`` vs ``legacy-session-v1``), so it writes its own
      session rather than corrupting the legacy one;
    * re-running the parser import is idempotent on its own machine — no duplicate
      parser session and no duplicate ``waveform_chunks``;
    * legacy row-backed and parser chunk-backed waveforms coexist.

    Known limitation asserted explicitly: the parser import does **not** merge
    into / enrich the legacy session. The night ends up represented by two
    sessions across two machines. A future enrichment feature would intentionally
    change the final assertions in this test.
    """
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    # -- A bridged upstream-1.4 night: legacy machine + legacy_backfilled session
    #    + row-backed waveform samples (what migration 023 leaves behind). --------
    legacy_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="legacy-session-v1",
        manufacturer="ResMed",
        serial_number=_SERIAL,
    )
    legacy_session = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=legacy_machine,
            source_session_key="20260601_223000",
            session_id="20260601_223000",
            adapter_id="legacy-session-v1",
            provenance_status="legacy_backfilled",
        ),
    )
    with raw_conn.cursor() as cur:
        _insert_legacy_waveform_rows(cur, str(legacy_session), count=3)

    # -- Parser import for the SAME night/serial. The machine resolves under a
    #    different identity_key, so it is a different machine row. ----------------
    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number=_SERIAL,
    )
    assert parser_machine != legacy_machine, (
        "parser import resolves a distinct machine from the legacy backfill"
    )

    with raw_conn.cursor() as cur:
        run_id = _new_import_run(cur, uid, parser_machine, f"parser-{uid}")

    parser_session = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=parser_machine,
            source_session_key="resmed:2026-06-01:0",
            session_id="cpapparser_20260601",
            adapter_id="resmed-cpap-parser-v1",
            provenance_status="native_resmed_cpap_parser",
            import_run_id=run_id,
        ),
    )
    assert parser_session != legacy_session
    with raw_conn.cursor() as cur:
        _replace_parser_chunk(cur, str(parser_session), run_id)

    # -- Reimport: run the same parser write again (idempotency). ----------------
    parser_session_again = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=parser_machine,
            source_session_key="resmed:2026-06-01:0",
            session_id="cpapparser_20260601",
            adapter_id="resmed-cpap-parser-v1",
            provenance_status="native_resmed_cpap_parser",
            import_run_id=run_id,
            ahi=4.4,  # corrected summary on reimport
        ),
    )
    assert parser_session_again == parser_session, "reimport reuses the parser session row"
    with raw_conn.cursor() as cur:
        _replace_parser_chunk(cur, str(parser_session), run_id)

    # -- Assertions -------------------------------------------------------------
    with raw_conn.cursor() as cur:
        # Legacy session survives, still legacy_backfilled, still on its machine.
        cur.execute(
            "SELECT provenance_status, machine_id::text FROM sessions WHERE id = %s",
            (legacy_session,),
        )
        prov, legacy_session_machine = cur.fetchone()
        assert prov == "legacy_backfilled"
        assert legacy_session_machine == legacy_machine

        # Legacy row-backed waveforms preserved (not deleted by the parser path).
        cur.execute(
            "SELECT COUNT(*) FROM session_waveform WHERE session_id = %s", (legacy_session,)
        )
        assert cur.fetchone()[0] == 3

        # Parser import is idempotent: exactly one parser session, one chunk.
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND source_session_key = %s",
            (parser_machine, "resmed:2026-06-01:0"),
        )
        assert cur.fetchone()[0] == 1, "no duplicate parser session on reimport"
        cur.execute(
            "SELECT COUNT(*) FROM waveform_chunks WHERE session_id = %s", (parser_session,)
        )
        assert cur.fetchone()[0] == 1, "no duplicate waveform_chunks on reimport"

        # Corrected summary landed on the existing parser row.
        cur.execute("SELECT ahi FROM sessions WHERE id = %s", (parser_session,))
        assert float(cur.fetchone()[0]) == 4.4

        # Coexistence: legacy session carries row-backed samples, parser session
        # carries chunk-backed samples, simultaneously.
        cur.execute(
            "SELECT COUNT(*) FROM session_waveform WHERE session_id = %s", (legacy_session,)
        )
        legacy_rows = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM waveform_chunks WHERE session_id = %s", (parser_session,)
        )
        parser_chunks = cur.fetchone()[0]
        assert legacy_rows == 3 and parser_chunks == 1

        # -- KNOWN LIMITATION (current beta.4 behavior, asserted to stay honest) --
        # The night is represented by two sessions across two machines: the parser
        # import did not enrich/merge the legacy session. A future enrichment
        # feature is expected to change these two assertions deliberately.
        cur.execute(
            "SELECT COUNT(DISTINCT machine_id) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 2, "night currently spans two machines (no merge)"
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 2, "night currently has two sessions (legacy + parser)"
