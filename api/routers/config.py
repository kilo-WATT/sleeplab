"""
App configuration endpoint.

GET /config  — returns server-side settings the frontend needs at runtime,
               including the display timezone for plot labels.
"""

import os

from fastapi import APIRouter

from importer.loaders.execution import cpap_parser_runtime_available, use_cpap_parser

router = APIRouter()


@router.get("")
def get_config():
    """
    Return runtime configuration for the frontend.

    display_tz  IANA timezone name used to format all time labels in the UI
                (plot axes, event timeline, session start time).  Defaults to UTC.

    machine_tz  IANA timezone name the CPAP machine was set to when sessions
                were recorded.  The importer uses this to correctly interpret
                the naive local timestamps embedded in EDF files.  Defaults to UTC.
    """
    parser_selected = use_cpap_parser()
    parser_available = cpap_parser_runtime_available()
    return {
        "display_tz": os.environ.get("DISPLAY_TZ", "UTC"),
        "machine_tz": os.environ.get("MACHINE_TZ", "UTC"),
        "resmed_import_backend": "cpap-parser" if parser_selected else "legacy",
        "cpap_parser_available": parser_available,
        "resmed_import_ready": not parser_selected or parser_available,
        "datalog_import_backend": "legacy",
        "datalog_import_available": not parser_selected,
        "cpap_parser_oximetry_supported": False,
        "cpap_parser_source_provenance": "manifest-level-partial",
    }
