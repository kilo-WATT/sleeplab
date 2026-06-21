"""Preservation-first guard for 1.x -> 2.x SleepLab database upgrades.

The SleepLab 2.0 line and the upstream 1.4.x line (``joshuamyers-dev/main``)
share an identical migration baseline through ``021_add_session_manufacturer``
but then diverge while *reusing the same migration numbers*:

* upstream 1.4 added ``022_add_adherence_settings`` and ``023_add_adherence_enabled``
* SleepLab 2.0 added ``022_add_session_leak_semantics`` .. ``032_add_import_result_summary``

Because the migration runner keys ``schema_migrations`` by *filename*, a database
that already recorded the upstream 022/023 files would silently pass the
"already applied?" check for those names and then have SleepLab 2.0's own
``022``/``023`` (and beyond) layered on top of a schema the runner never modeled.
That is the dangerous, irreversible state this module exists to detect and stop
*before* any 2.0 migration is applied.

Everything here is pure: callers pass the set of applied migration filenames (and,
for the readiness classifier, a few boolean facts about the data) and get back a
decision. No database connection lives in this module so it is trivially unit
testable without Postgres.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Upstream 1.4.x migration filenames that collide, number-for-number, with the
# different 2.0 migrations occupying the same slots. Their presence in
# ``schema_migrations`` is the signature of an upstream-1.4 database that cannot
# be auto-migrated forward onto the 2.0 line.
UPSTREAM_1_4_CONFLICT_MIGRATIONS: frozenset[str] = frozenset(
    {
        "022_add_adherence_settings.sql",
        "023_add_adherence_enabled.sql",
    }
)

# Recommendation tokens. Kept as plain strings (not an Enum) so the readiness
# script can print them verbatim and tests can compare without imports.
SAFE_TO_ATTEMPT_IN_PLACE = "SAFE_TO_ATTEMPT_IN_PLACE"
SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS = "SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS"
BLOCKED_CONFLICTING_1_4_MIGRATIONS = "BLOCKED_CONFLICTING_1_4_MIGRATIONS"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# The schema bootstrap file is recorded alongside migrations in schema_migrations
# but is not itself a numbered migration; treat it as always-known.
_SCHEMA_BOOTSTRAP_FILENAME = "schema.sql"


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


def conflicting_migrations(applied: Iterable[str]) -> set[str]:
    """Return the applied filenames that are known upstream 1.4 conflicts.

    Args:
        applied: Filenames recorded in ``schema_migrations``.

    Returns:
        The intersection of ``applied`` with the known upstream 1.4 conflict set.
    """
    return set(applied) & UPSTREAM_1_4_CONFLICT_MIGRATIONS


def foreign_migrations(
    applied: Iterable[str],
    local: set[str] | None = None,
) -> set[str]:
    """Return applied filenames this 2.0 checkout neither ships nor recognizes.

    These are entries that are not part of the local 2.0 migration set, not the
    schema bootstrap, and not a known upstream 1.4 conflict — i.e. an unknown
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


@dataclass
class GuardDecision:
    """Outcome of the startup migration-safety check.

    Attributes:
        blocked: Whether 2.0 migrations must not be auto-applied.
        conflicts: Sorted list of detected upstream 1.4 conflict filenames.
        message: Human-readable explanation suitable for logs / startup abort.
    """

    blocked: bool
    conflicts: list[str] = field(default_factory=list)
    message: str = ""


def _format_block_message(conflicts: list[str]) -> str:
    """Compose the human-readable startup-block message for a conflict state."""
    listed = "\n".join(f"  - {name}" for name in conflicts)
    return (
        "SleepLab 2.0 upgrade blocked: this database recorded upstream 1.4 "
        "migrations that collide with the 2.0 migration history.\n\n"
        "Conflicting migrations already applied:\n"
        f"{listed}\n\n"
        "Upstream 1.4 reused migration numbers 022/023 for unrelated changes "
        "(adherence settings), while SleepLab 2.0 uses 022-032 for the CPAP data "
        "foundation and waveform_chunks work. Auto-applying the 2.0 migrations on "
        "top of this schema could irreversibly corrupt your data, so startup has "
        "been stopped on purpose.\n\n"
        "No data has been modified. To proceed safely:\n"
        "  1. Back up first:  pg_dump \"$DATABASE_URL\" > sleeplab-backup.sql\n"
        "  2. Run the readiness check:  python scripts/check_2x_upgrade_readiness.py\n"
        "  3. See docs/sleeplab_2_upgrade_from_1x.md for the supported recovery "
        "paths.\n"
        "Do NOT delete or reset your database volume before backing up — that "
        "discards database-only history the SD card cannot restore."
    )


def evaluate_startup(
    applied: Iterable[str],
    local: set[str] | None = None,
) -> GuardDecision:
    """Decide whether 2.0 migrations may be auto-applied to this database.

    Only *known* upstream 1.4 conflicts block startup. Fresh installs, valid 2.0
    beta databases, and known-safe 1.3.x-style databases (shared baseline only)
    are all allowed through. Unknown-but-not-conflicting histories are *not*
    blocked here — they surface as ``MANUAL_REVIEW_REQUIRED`` in the readiness
    script rather than stopping startup.

    Args:
        applied: Filenames recorded in ``schema_migrations``.
        local: The local migration filename set; resolved from disk if omitted.

    Returns:
        A :class:`GuardDecision`. ``blocked`` is True only on a known conflict.
    """
    conflicts = sorted(conflicting_migrations(applied))
    if conflicts:
        return GuardDecision(
            blocked=True,
            conflicts=conflicts,
            message=_format_block_message(conflicts),
        )
    return GuardDecision(blocked=False)


def classify_upgrade_state(
    applied: Iterable[str],
    *,
    has_chunk_waveforms: bool,
    has_row_waveforms: bool,
    local: set[str] | None = None,
) -> str:
    """Classify a database's upgrade state for the readiness report.

    The classification is layered:

    1. Any known upstream 1.4 conflict -> ``BLOCKED_CONFLICTING_1_4_MIGRATIONS``.
    2. Any unrecognized (foreign) migration -> ``MANUAL_REVIEW_REQUIRED``.
    3. Otherwise the history is known-safe; the recommendation then depends on
       waveform backing: legacy row-backed data without chunk-backed data earns
       ``SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS`` (reimport enriches waveforms),
       everything else is ``SAFE_TO_ATTEMPT_IN_PLACE``.

    Args:
        applied: Filenames recorded in ``schema_migrations``.
        has_chunk_waveforms: Whether any ``waveform_chunks`` rows exist.
        has_row_waveforms: Whether any legacy ``session_waveform`` rows exist.
        local: The local migration filename set; resolved from disk if omitted.

    Returns:
        One of the recommendation tokens defined in this module.
    """
    local_set = local if local is not None else local_migration_filenames()
    if conflicting_migrations(applied):
        return BLOCKED_CONFLICTING_1_4_MIGRATIONS
    if foreign_migrations(applied, local_set):
        return MANUAL_REVIEW_REQUIRED
    if has_row_waveforms and not has_chunk_waveforms:
        return SAFE_BUT_REIMPORT_RECOMMENDED_FOR_CHUNKS
    return SAFE_TO_ATTEMPT_IN_PLACE
