"""Bridge + parser-reimport reconciliation on a bridged upstream-1.4 night.

Follow-up to the upstream 1.4 -> 2.0 bridge (beta.4). It answers a concrete
question: after a 1.4 database is bridged, if the user reimports SD-card / parser
data for a night that already exists as a ``legacy_backfilled`` session, what
happens to that night?

These tests use only synthetic data (no private card data) and the same
``reconcile_machine`` / ``upsert_session`` helpers the real parser import path
uses, so they faithfully reproduce the production matching keys:

* the migration-023 legacy backfill files sessions under a synthetic
  ``legacy-session-v1`` machine, keyed ``legacy-session-v1:serial:<serial>``;
* a parser/native import resolves its machine as ``resmed-native-v2:serial:<serial>``.

As of the beta.5 reconciliation work, a parser/native ResMed import for the same
serial now *folds* the legacy backfill into the modern device instead of leaving
a duplicate:

* ``reconcile_machine`` re-points the legacy machine's sessions onto the modern
  machine and retires the empty legacy machine row (machine-level reconciliation);
* ``upsert_session`` rekeys a single matching ``legacy_*`` night to the incoming
  parser key, so the ON CONFLICT enriches that row in place rather than inserting
  a second session (session-level reconciliation).

The legacy night, its row-backed ``session_waveform`` samples, and any
notes/tags are preserved; the parser's summary, provenance and ``waveform_chunks``
attach to the same surviving session; and reimport stays idempotent. Ambiguous
matches (different serial, multiple legacy block-fragments, an already-present
parser session) are kept separate rather than destructively merged.

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


def _seed_legacy_night(raw_conn, uid, *, serial=_SERIAL, session_id="20260601_223000",
                       folder_date=_NIGHT, start=_NIGHT_START, waveform_rows=3):
    """Create a bridged legacy machine + legacy_backfilled session (+ row waveform).

    Returns ``(legacy_machine_id, legacy_session_id)``.
    """
    legacy_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="legacy-session-v1",
        manufacturer="ResMed",
        serial_number=serial,
    )
    legacy_session = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=legacy_machine,
            device_serial=serial,
            folder_date=folder_date,
            start_datetime=start,
            pld_start_datetime=start,
            source_session_key=session_id,
            session_id=session_id,
            adapter_id="legacy-session-v1",
            provenance_status="legacy_backfilled",
        ),
    )
    if waveform_rows:
        with raw_conn.cursor() as cur:
            _insert_legacy_waveform_rows(cur, str(legacy_session), count=waveform_rows)
    return legacy_machine, legacy_session


def test_parser_reimport_reconciles_bridged_legacy_night_and_is_idempotent(db, test_user):
    """Parser reimport of a bridged legacy night: enrich in place, no duplication.

    Proves the beta.5 reconciliation behavior end to end at the persistence
    matching layer:

    * machine reconciliation folds the legacy backfill machine into the modern
      ResMed machine (the duplicate machine row is retired);
    * session reconciliation enriches the single legacy night in place — the
      parser write lands on the *same* session row, so the night is not
      duplicated;
    * the legacy row-backed ``session_waveform`` samples survive and now coexist
      with the parser's ``waveform_chunks`` on that one session;
    * re-running the parser import is idempotent — no duplicate session, no
      duplicate chunk, and the corrected summary lands on the surviving row.
    """
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    # -- A bridged upstream-1.4 night: legacy machine + legacy_backfilled session
    #    + row-backed waveform samples (what migration 023 leaves behind). --------
    legacy_machine, legacy_session = _seed_legacy_night(raw_conn, uid)

    # -- Parser import for the SAME night/serial. Resolving the modern machine now
    #    canonicalizes the legacy machine into it. -------------------------------
    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number=_SERIAL,
    )
    assert parser_machine != legacy_machine, "the modern machine is a distinct row"

    with raw_conn.cursor() as cur:
        # The legacy machine row was retired and its session re-pointed onto the
        # modern machine.
        cur.execute("SELECT 1 FROM cpap_machines WHERE id = %s", (legacy_machine,))
        assert cur.fetchone() is None, "legacy machine row retired"
        cur.execute("SELECT machine_id::text FROM sessions WHERE id = %s", (legacy_session,))
        assert cur.fetchone()[0] == parser_machine, "legacy session re-pointed to modern machine"

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
    # Session reconciliation: the parser write enriched the existing legacy night
    # in place rather than inserting a second session.
    assert parser_session == legacy_session, "parser import enriched the legacy session row"
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
    assert parser_session_again == parser_session, "reimport reuses the same session row"
    with raw_conn.cursor() as cur:
        _replace_parser_chunk(cur, str(parser_session), run_id)

    # -- Assertions -------------------------------------------------------------
    with raw_conn.cursor() as cur:
        # The surviving session is the original legacy row, now enriched with
        # parser provenance and living on the modern machine.
        cur.execute(
            "SELECT provenance_status, machine_id::text, source_session_key FROM sessions WHERE id = %s",
            (legacy_session,),
        )
        prov, session_machine, source_key = cur.fetchone()
        assert prov == "native_resmed_cpap_parser"
        assert session_machine == parser_machine
        assert source_key == "resmed:2026-06-01:0"

        # Legacy row-backed waveforms preserved on the surviving row.
        cur.execute(
            "SELECT COUNT(*) FROM session_waveform WHERE session_id = %s", (legacy_session,)
        )
        assert cur.fetchone()[0] == 3

        # Parser write is idempotent: exactly one session, one chunk for the night.
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE machine_id = %s AND source_session_key = %s",
            (parser_machine, "resmed:2026-06-01:0"),
        )
        assert cur.fetchone()[0] == 1, "no duplicate parser session on reimport"
        cur.execute(
            "SELECT COUNT(*) FROM waveform_chunks WHERE session_id = %s", (parser_session,)
        )
        assert cur.fetchone()[0] == 1, "no duplicate waveform_chunks on reimport"

        # Corrected summary landed on the surviving row.
        cur.execute("SELECT ahi FROM sessions WHERE id = %s", (parser_session,))
        assert float(cur.fetchone()[0]) == 4.4

        # Coexistence: row-backed *and* chunk-backed waveforms on the one session.
        cur.execute(
            "SELECT COUNT(*) FROM session_waveform WHERE session_id = %s", (legacy_session,)
        )
        legacy_rows = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM waveform_chunks WHERE session_id = %s", (parser_session,)
        )
        parser_chunks = cur.fetchone()[0]
        assert legacy_rows == 3 and parser_chunks == 1

        # The night is now represented by ONE machine and ONE session — the
        # duplicate-looking machine/session is gone.
        cur.execute(
            "SELECT COUNT(DISTINCT machine_id) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 1, "night spans a single machine after reconciliation"
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 1, "night is a single session after reconciliation"


def test_different_serials_do_not_merge(db, test_user):
    """A legacy night for serial A is untouched by a parser import for serial B."""
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    legacy_machine, legacy_session = _seed_legacy_night(raw_conn, uid, serial="SERIAL-A")

    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number="SERIAL-B",
    )
    assert parser_machine != legacy_machine

    with raw_conn.cursor() as cur:
        run_id = _new_import_run(cur, uid, parser_machine, f"parser-b-{uid}")
    parser_session = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=parser_machine,
            device_serial="SERIAL-B",
            source_session_key="resmed:2026-06-01:0",
            session_id="cpapparser_20260601",
            adapter_id="resmed-cpap-parser-v1",
            provenance_status="native_resmed_cpap_parser",
            import_run_id=run_id,
        ),
    )

    with raw_conn.cursor() as cur:
        # The serial-A legacy machine and session are entirely preserved.
        cur.execute("SELECT 1 FROM cpap_machines WHERE id = %s", (legacy_machine,))
        assert cur.fetchone() is not None, "different-serial legacy machine preserved"
        cur.execute(
            "SELECT machine_id::text, provenance_status FROM sessions WHERE id = %s",
            (legacy_session,),
        )
        machine, prov = cur.fetchone()
        assert machine == legacy_machine
        assert prov == "legacy_backfilled"
        # The two nights stay distinct: two machines, two sessions.
        assert parser_session != legacy_session
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 2


def test_ambiguous_multiple_legacy_nights_kept_separate(db, test_user):
    """Two legacy block-fragments for one night are ambiguous — never merged."""
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    legacy_machine, first = _seed_legacy_night(raw_conn, uid, waveform_rows=0)
    # A second legacy session for the SAME night/serial (e.g. a fragmented night
    # backfilled as two block rows by migration 023).
    second = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=legacy_machine,
            block_index=1,
            source_session_key="20260601_233000",
            session_id="20260601_233000",
            adapter_id="legacy-session-v1",
            provenance_status="legacy_backfilled",
        ),
    )
    assert second != first

    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number=_SERIAL,
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

    with raw_conn.cursor() as cur:
        # Both legacy rows survive untouched, and the parser created its own row:
        # three sessions on the (single, canonicalized) machine for the night.
        cur.execute(
            "SELECT provenance_status FROM sessions WHERE id IN (%s, %s)",
            (first, second),
        )
        assert {row[0] for row in cur.fetchall()} == {"legacy_backfilled"}
        assert parser_session not in (first, second)
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 3, "ambiguous night kept separate (not destructively merged)"


def test_existing_parser_session_not_clobbered(db, test_user):
    """A legacy night beside an existing parser session for the same key is kept.

    Reproduces a pre-existing beta.4 duplicate (legacy + parser session for one
    night). Machine reconciliation puts them on one machine, but the parser key
    already exists, so the legacy row is *not* rekeyed onto it — no clobber.
    """
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    legacy_machine, legacy_session = _seed_legacy_night(raw_conn, uid, waveform_rows=2)

    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number=_SERIAL,
    )
    with raw_conn.cursor() as cur:
        run_id = _new_import_run(cur, uid, parser_machine, f"parser-{uid}")

    # First parser write: with a single legacy night present, this enriches it.
    first_parser = importer_db.upsert_session(
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
    assert first_parser == legacy_session

    # Now seed a *second*, independent legacy night for the same night/key space
    # to simulate a leftover legacy backfill row sitting beside the parser row.
    leftover = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=parser_machine,
            block_index=2,
            source_session_key="20260601_legacy_leftover",
            session_id="20260601_legacy_leftover",
            adapter_id="legacy-session-v1",
            provenance_status="legacy_backfilled",
        ),
    )

    # A subsequent parser write for the existing key must update the parser row in
    # place and must NOT rekey/clobber the leftover legacy row.
    second_parser = importer_db.upsert_session(
        raw_conn,
        _session_data(
            user_id=uid,
            machine_id=parser_machine,
            source_session_key="resmed:2026-06-01:0",
            session_id="cpapparser_20260601",
            adapter_id="resmed-cpap-parser-v1",
            provenance_status="native_resmed_cpap_parser",
            import_run_id=run_id,
            ahi=5.5,
        ),
    )
    assert second_parser == first_parser

    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT provenance_status, source_session_key FROM sessions WHERE id = %s",
            (leftover,),
        )
        prov, key = cur.fetchone()
        assert prov == "legacy_backfilled", "leftover legacy row untouched"
        assert key == "20260601_legacy_leftover", "leftover legacy key not rewritten"
        # Parser row carries the corrected summary; leftover preserved alongside.
        cur.execute("SELECT ahi FROM sessions WHERE id = %s", (first_parser,))
        assert float(cur.fetchone()[0]) == 5.5


def test_valid_parser_import_without_legacy_is_idempotent(db, test_user):
    """A clean 2.0 parser import (no legacy backfill) is unaffected and idempotent."""
    raw_conn = db.connection().connection.driver_connection
    uid = test_user["id"]

    parser_machine = importer_db.reconcile_machine(
        raw_conn,
        user_id=uid,
        adapter_id="resmed-native-v2",
        manufacturer="ResMed",
        serial_number="CLEAN-2X-SN",
    )
    with raw_conn.cursor() as cur:
        run_id = _new_import_run(cur, uid, parser_machine, f"clean-{uid}")

    def _write(ahi):
        return importer_db.upsert_session(
            raw_conn,
            _session_data(
                user_id=uid,
                machine_id=parser_machine,
                device_serial="CLEAN-2X-SN",
                source_session_key="resmed:2026-06-01:0",
                session_id="cpapparser_20260601",
                adapter_id="resmed-cpap-parser-v1",
                provenance_status="native_resmed_cpap_parser",
                import_run_id=run_id,
                ahi=ahi,
            ),
        )

    first = _write(3.3)
    second = _write(4.4)
    assert first == second, "clean parser reimport reuses the same row"
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = CAST(%s AS uuid) AND folder_date = %s",
            (uid, _NIGHT),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT ahi FROM sessions WHERE id = %s", (first,))
        assert float(cur.fetchone()[0]) == 4.4
