"""Incremental import: skip nights already imported with detailed data.

The pinned ``cpap-parser`` decodes the whole card in one pass, so to avoid
re-parsing unchanged nights we stage a source containing only the ``DATALOG``
day folders that are not yet imported *with detail*, then parse that subset.
Whole-card files (identity, ``STR.edf``, settings) are always kept so detection
and the nightly summaries still work.

A persist-time filter is the safety net: a summary-only night whose date already
has detail is never written, so an existing detailed night can never be
clobbered by the ``STR``-derived whole-card summaries even if staging is bypassed
(e.g. a non-DATALOG card layout or a re-detect fallback).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

DATALOG_DIRNAME = "DATALOG"


def detailed_dates(conn: Any, user_id: str, machine_id: str | None) -> set[date]:
    """Return the dates already imported *with detail* for this machine.

    Detailed nights persist at least one ``session_blocks`` row; the parser's
    summary-only nights are deliberately block-less, so this join cleanly
    separates "fully imported" nights from summary-only ones.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT s.folder_date
            FROM sessions s
            JOIN session_blocks b ON b.session_id = s.id
            WHERE s.user_id = %s
              AND s.machine_id IS NOT DISTINCT FROM %s
            """,
            (user_id, machine_id),
        )
        return {row[0] for row in cur.fetchall()}


def select_skipped_day_folders(day_folders: set[str], already_detailed: set[date]) -> set[str]:
    """Day-folder names (``YYYYMMDD``) to omit from the parse — those already detailed."""
    detailed_names = {value.strftime("%Y%m%d") for value in already_detailed}
    return {name for name in day_folders if name in detailed_names}


def _find_datalog(source_root: Path) -> Path | None:
    for entry in source_root.iterdir():
        if entry.is_dir() and entry.name.upper() == DATALOG_DIRNAME:
            return entry
    return None


def stage_incremental_source(
    source_root: Path,
    skip_dates: set[date],
) -> tuple[Path, Callable[[], None]]:
    """Stage a copy of the card with only non-skipped ``DATALOG`` day folders.

    Returns ``(staged_root, cleanup)``. When there is nothing to skip — or the
    card has no ``DATALOG`` layout to filter — returns the original root with a
    no-op cleanup so the caller can treat both paths uniformly.
    """
    skip_names = {value.strftime("%Y%m%d") for value in skip_dates}
    if not skip_names:
        return source_root, lambda: None

    datalog = _find_datalog(source_root)
    if datalog is None:
        return source_root, lambda: None

    staged = Path(tempfile.mkdtemp(prefix="sleeplab-incremental-"))

    def cleanup() -> None:
        shutil.rmtree(staged, ignore_errors=True)

    try:
        # Copy every whole-card entry (identity, STR.edf, settings, …) verbatim.
        for entry in source_root.iterdir():
            if entry == datalog:
                continue
            dest = staged / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dest)
            else:
                shutil.copy2(entry, dest)

        # Recreate DATALOG with only the day folders we still need to parse.
        dst_datalog = staged / datalog.name
        dst_datalog.mkdir(parents=True, exist_ok=True)
        for day in datalog.iterdir():
            if day.is_dir() and day.name in skip_names:
                continue
            dest = dst_datalog / day.name
            if day.is_dir():
                shutil.copytree(day, dest)
            else:
                shutil.copy2(day, dest)
        return staged, cleanup
    except Exception:
        cleanup()
        raise
