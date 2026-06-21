"""Preservation-first guard and bridge policy for 1.x -> 2.x SleepLab upgrades.

The SleepLab 2.0 line and the upstream 1.4.x line (``joshuamyers-dev/main``)
share an identical migration baseline through ``021_add_session_manufacturer``
but then diverge while *reusing the same migration numbers*:

* upstream 1.4 added ``022_add_adherence_settings`` and ``023_add_adherence_enabled``
  (both only add nullable ``adherence_*`` columns to ``user_import_settings``)
* SleepLab 2.0 added ``022_add_session_leak_semantics`` .. ``032_add_import_result_summary``
  (leak semantics, the CPAP data foundation, ``waveform_chunks``, etc.)

Because the migration runner keys ``schema_migrations`` by *filename*, a database
that recorded the upstream 022/023 files would otherwise have SleepLab 2.0's own
022+ layered on top of a history the runner never modeled. beta.3 stopped that
state outright. This module now adds a **bridge**: the two histories are actually
*orthogonal* (upstream only touches ``user_import_settings`` adherence columns;
2.0 only adds new tables/columns elsewhere; 2.0's adherence analytics are a fixed
policy that never reads those columns), so a *clean* upstream 1.4 database can be
safely reconciled and carried forward without losing data.

The bridge is only offered for a database whose recorded history is *exactly* the
known upstream 1.4 signature. Partial, mixed, or unknown histories are still
blocked or sent to manual review.

Everything here is pure: callers pass the set of applied migration filenames, a
couple of boolean facts (whether the bridge has already been recorded, waveform
backing), and get back a decision. No database connection lives in this module so
it is trivially unit testable without Postgres. The runtime that actually inspects
and writes the database lives in :mod:`api.upgrade_bridge`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Upstream 1.4.x migration filenames that collide, number-for-number, with the
# different 2.0 migrations occupying the same slots. Their presence in
# ``schema_migrations`` is the signature of an upstream-1.4 database.
UPSTREAM_1_4_CONFLICT_MIGRATIONS: frozenset[str] = frozenset(
    {
        "022_add_adherence_settings.sql",
        "023_add_adherence_enabled.sql",
    }
)

# Columns the upstream 1.4 adherence migrations add to ``user_import_settings``.
# The runtime bridge verifies these are actually present before recording a
# bridge, and they are preserved (never dropped) on the 2.0 line.
UPSTREAM_1_4_ADHERENCE_COLUMNS: frozenset[str] = frozenset(
    {
        "adherence_threshold_hours",
        "adherence_borderline_hours",
        "adherence_target_pct",
        "adherence_window_days",
        "adherence_evaluation_days",
        "adherence_window_logic",
        "adherence_lookback_days",
        "adherence_enabled",
    }
)

# Recommendation tokens. Kept as plain strings (not an Enum) so the readiness
# script can print them verbatim and tests can compare without imports.
SAFE_TO_ATTEMPT_IN_PLACE = "SAFE_TO_ATTEMPT_IN_PLACE"
SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS = "SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS"
BRIDGEABLE_UPSTREAM_1_4_TO_2_0 = "BRIDGEABLE_UPSTREAM_1_4_TO_2_0"
BRIDGED_UPSTREAM_1_4_TO_2_0 = "BRIDGED_UPSTREAM_1_4_TO_2_0"
BLOCKED_UNSUPPORTED_1_4_STATE = "BLOCKED_UNSUPPORTED_1_4_STATE"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# Retained for backward compatibility with beta.3 importers/tests. The classifier
# no longer returns it — a clean upstream 1.4 state is now bridgeable, and an
# unsafe one is BLOCKED_UNSUPPORTED_1_4_STATE — but the constant stays defined so
# existing references do not break.
BLOCKED_CONFLICTING_1_4_MIGRATIONS = "BLOCKED_CONFLICTING_1_4_MIGRATIONS"

# Startup actions returned alongside a guard decision.
ACTION_NONE = "none"
ACTION_BRIDGE = "bridge"

# The schema bootstrap file is recorded alongside migrations in schema_migrations
# but is not itself a numbered migration; treat it as always-known.
_SCHEMA_BOOTSTRAP_FILENAME = "schema.sql"

# Migrations numbered at or above this prefix are the 2.0-divergent set (the ones
# that reuse upstream's 022/023 slots with different content).
_DIVERGENCE_START = 22


def _migrations_dir() -> Path:
    """Return the repository ``migrations`` directory."""
    return Path(__file__).resolve().parent.parent / "migrations"


def local_migration_filenames(migrations_dir: Path | None = None) -> set[str]:
    """Return the set of migration filenames shipped by this 2.0 checkout.

    Args:
        migrations_dir: Optional override of the directory to scan; defaults to
            the repository ``migrations`` directory.

    Returns:
        The set of ``*.sql`` filenames (basenames only) found in the directory.
    """
    directory = migrations_dir if migrations_dir is not None else _migrations_dir()
    return {path.name for path in directory.glob("*.sql")}


def divergent_2_0_migrations(local: set[str] | None = None) -> set[str]:
    """Return the 2.0-only migrations that diverge from upstream (number >= 022).

    These are the filenames that occupy the same numeric slots as upstream 1.4's
    022/023 but ship different content. Their presence in a history that also
    carries the upstream adherence migrations — without a recorded bridge — is the
    Frankenstein state the guard must refuse.

    Args:
        local: The local migration filename set; resolved from disk if omitted.

    Returns:
        The set of local migration filenames numbered ``022`` or higher.
    """
    local_set = local if local is not None else local_migration_filenames()
    return {
        name
        for name in local_set
        if name[:3].isdigit() and int(name[:3]) >= _DIVERGENCE_START
    }


def conflicting_migrations(applied: Iterable[str]) -> set[str]:
    """Return the applied filenames that are known upstream 1.4 migrations.

    Args:
        applied: Filenames recorded in ``schema_migrations``.

    Returns:
        The intersection of ``applied`` with the known upstream 1.4 set.
    """
    return set(applied) & UPSTREAM_1_4_CONFLICT_MIGRATIONS


def foreign_migrations(
    applied: Iterable[str],
    local: set[str] | None = None,
) -> set[str]:
    """Return applied filenames this 2.0 checkout neither ships nor recognizes.

    These are entries that are not part of the local 2.0 migration set, not the
    schema bootstrap, and not a known upstream 1.4 migration — i.e. an unknown
    history that warrants manual review rather than a blanket block.

    Args:
        applied: Filenames recorded in ``schema_migrations``.
        local: The local migration filename set; resolved from disk if omitted.

    Returns:
        The set of unrecognized migration filenames.
    """
    known = (local if local is not None else local_migration_filenames()) | {
        _SCHEMA_BOOTSTRAP_FILENAME
    }
    return set(applied) - known - UPSTREAM_1_4_CONFLICT_MIGRATIONS


# -- Upstream 1.4 state classification --------------------------------------

# Internal upstream-1.4 sub-states (filename-level, before consulting the bridge
# marker). Exposed as strings for readability in logs/tests.
_U14_NONE = "none"  # no upstream 1.4 migrations recorded at all
_U14_CLEAN = "clean"  # exactly the known 1.4 signature, nothing else divergent
_U14_PARTIAL = "partial"  # only one of the two adherence migrations recorded
_U14_MIXED = "mixed"  # 1.4 adherence + 2.0-divergent and/or foreign migrations


def upstream_1_4_state(
    applied: Iterable[str],
    local: set[str] | None = None,
) -> str:
    """Classify the upstream-1.4 shape of a recorded migration history.

    This is purely filename-level. It does not consult the bridge marker or the
    live schema — callers combine it with those facts.

    Returns one of ``_U14_NONE``, ``_U14_CLEAN``, ``_U14_PARTIAL``, ``_U14_MIXED``.
    """
    applied_set = set(applied)
    conflict = applied_set & UPSTREAM_1_4_CONFLICT_MIGRATIONS
    if not conflict:
        return _U14_NONE

    local_set = local if local is not None else local_migration_filenames()
    divergent_applied = applied_set & divergent_2_0_migrations(local_set)
    foreign = foreign_migrations(applied_set, local_set)

    if conflict != UPSTREAM_1_4_CONFLICT_MIGRATIONS:
        # Only one adherence migration recorded: an incomplete 1.4 upgrade whose
        # actual schema state we cannot assume. Refuse rather than guess.
        return _U14_PARTIAL
    if divergent_applied or foreign:
        # The full 1.4 signature plus 2.0-divergent or unknown migrations. Without
        # a recorded bridge this is an unmodeled mix.
        return _U14_MIXED
    return _U14_CLEAN


@dataclass
class GuardDecision:
    """Outcome of the startup migration-safety check.

    Attributes:
        blocked: Whether startup must abort before any migration write.
        action: ``ACTION_BRIDGE`` when a one-time bridge step should run before
            the normal apply loop, otherwise ``ACTION_NONE``.
        state: The readiness recommendation token describing the database.
        conflicts: Sorted upstream 1.4 migration filenames detected (if any).
        message: Human-readable explanation suitable for logs / startup abort.
    """

    blocked: bool
    action: str = ACTION_NONE
    state: str = SAFE_TO_ATTEMPT_IN_PLACE
    conflicts: list[str] = field(default_factory=list)
    message: str = ""


def _format_unsupported_message(applied: Iterable[str], local: set[str]) -> str:
    """Compose the human-readable startup-block message for an unsafe 1.4 state."""
    conflicts = sorted(conflicting_migrations(applied))
    divergent = sorted(set(applied) & divergent_2_0_migrations(local))
    foreign = sorted(foreign_migrations(applied, local))
    detail_lines = [f"  - recorded upstream 1.4 migrations: {', '.join(conflicts) or 'none'}"]
    if divergent:
        detail_lines.append(f"  - 2.0-divergent migrations also present: {', '.join(divergent)}")
    if foreign:
        detail_lines.append(f"  - unrecognized migrations present: {', '.join(foreign)}")
    detail = "\n".join(detail_lines)
    return (
        "SleepLab 2.0 upgrade blocked: this database is in an upstream 1.4 state "
        "the automatic bridge cannot safely reconcile.\n\n"
        f"{detail}\n\n"
        "The bridge only handles a clean upstream 1.4 database (both adherence "
        "migrations recorded, nothing else divergent). This database does not "
        "match that signature, so startup has been stopped before any migration "
        "write to avoid corrupting your data.\n\n"
        "No data has been modified. To proceed safely:\n"
        "  1. Back up first:  pg_dump \"$DATABASE_URL\" > sleeplab-backup.sql\n"
        "  2. Run the readiness check:  python scripts/check_2x_upgrade_readiness.py\n"
        "  3. See docs/sleeplab_2_upgrade_from_1x.md for the supported recovery "
        "paths and how to request manual review.\n"
        "Do NOT delete or reset your database volume before backing up — that "
        "discards database-only history the SD card cannot restore."
    )


def evaluate_startup(
    applied: Iterable[str],
    local: set[str] | None = None,
    *,
    bridge_recorded: bool = False,
) -> GuardDecision:
    """Decide whether (and how) startup may proceed for this database.

    Outcomes:

    * Fresh installs, valid 2.0 betas, and known-safe 1.3.x databases -> proceed
      with no action.
    * A clean upstream 1.4 database -> proceed with ``ACTION_BRIDGE`` (the runtime
      records a bridge and preserves adherence data before the normal apply loop).
    * An already-bridged upstream 1.4 database -> proceed with no action.
    * A partial/mixed upstream 1.4 database that has *not* been bridged -> blocked
      with a clear message before any write.
    * Unknown (foreign) histories are not blocked here; they surface as
      ``MANUAL_REVIEW_REQUIRED`` in the readiness script.

    Args:
        applied: Filenames recorded in ``schema_migrations``.
        local: The local migration filename set; resolved from disk if omitted.
        bridge_recorded: Whether ``schema_compatibility`` already records a
            completed upstream 1.4 -> 2.0 bridge for this database.

    Returns:
        A :class:`GuardDecision`.
    """
    local_set = local if local is not None else local_migration_filenames()
    state = upstream_1_4_state(applied, local_set)
    conflicts = sorted(conflicting_migrations(applied))

    if state == _U14_NONE:
        return GuardDecision(blocked=False, action=ACTION_NONE, state=SAFE_TO_ATTEMPT_IN_PLACE)

    if bridge_recorded:
        # The bridge already ran intentionally; the divergent migrations present
        # are expected. Proceed and let the normal loop finish any remainder.
        return GuardDecision(
            blocked=False,
            action=ACTION_NONE,
            state=BRIDGED_UPSTREAM_1_4_TO_2_0,
            conflicts=conflicts,
        )

    if state == _U14_CLEAN:
        return GuardDecision(
            blocked=False,
            action=ACTION_BRIDGE,
            state=BRIDGEABLE_UPSTREAM_1_4_TO_2_0,
            conflicts=conflicts,
        )

    # _U14_PARTIAL or _U14_MIXED without a recorded bridge.
    return GuardDecision(
        blocked=True,
        action=ACTION_NONE,
        state=BLOCKED_UNSUPPORTED_1_4_STATE,
        conflicts=conflicts,
        message=_format_unsupported_message(applied, local_set),
    )


def classify_upgrade_state(
    applied: Iterable[str],
    *,
    has_chunk_waveforms: bool,
    has_row_waveforms: bool,
    bridge_recorded: bool = False,
    local: set[str] | None = None,
) -> str:
    """Classify a database's upgrade state for the readiness report.

    Precedence:

    1. Already-bridged upstream 1.4 -> ``BRIDGED_UPSTREAM_1_4_TO_2_0``.
    2. Clean upstream 1.4 -> ``BRIDGEABLE_UPSTREAM_1_4_TO_2_0``.
    3. Partial/mixed upstream 1.4 -> ``BLOCKED_UNSUPPORTED_1_4_STATE``.
    4. Unrecognized (foreign) migration -> ``MANUAL_REVIEW_REQUIRED``.
    5. Otherwise known-safe; legacy row-backed waveforms without chunks earn
       ``SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS``, else
       ``SAFE_TO_ATTEMPT_IN_PLACE``.

    Args:
        applied: Filenames recorded in ``schema_migrations``.
        has_chunk_waveforms: Whether any ``waveform_chunks`` rows exist.
        has_row_waveforms: Whether any legacy ``session_waveform`` rows exist.
        bridge_recorded: Whether a completed bridge is recorded.
        local: The local migration filename set; resolved from disk if omitted.

    Returns:
        One of the recommendation tokens defined in this module.
    """
    local_set = local if local is not None else local_migration_filenames()
    state = upstream_1_4_state(applied, local_set)

    if state != _U14_NONE:
        if bridge_recorded:
            return BRIDGED_UPSTREAM_1_4_TO_2_0
        if state == _U14_CLEAN:
            return BRIDGEABLE_UPSTREAM_1_4_TO_2_0
        return BLOCKED_UNSUPPORTED_1_4_STATE

    if foreign_migrations(applied, local_set):
        return MANUAL_REVIEW_REQUIRED
    if has_row_waveforms and not has_chunk_waveforms:
        return SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS
    return SAFE_TO_ATTEMPT_IN_PLACE
