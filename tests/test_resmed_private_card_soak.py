"""Opt-in aggregate-only soak for a private local ResMed card.

Set ``SLEEPLAB_PRIVATE_RESMED_CARD`` to a card root. The test never copies card
files, prints paths, serials, dates, or session identifiers, and writes no report.
"""

import os
from pathlib import Path

import pytest

from importer.loaders.execution import cpap_parser_runtime_available
from importer.loaders.models import ImportOptions
from importer.loaders.resmed_native import ResMedNativeLoader

pytestmark = pytest.mark.private_card


def test_private_resmed_card_parser_soak():
    raw_path = os.environ.get("SLEEPLAB_PRIVATE_RESMED_CARD")
    if not raw_path:
        pytest.skip("Set SLEEPLAB_PRIVATE_RESMED_CARD to run the private-card soak")
    if not cpap_parser_runtime_available():
        pytest.skip("cpap-parser/cpap-py runtime is unavailable")

    root = Path(raw_path).expanduser().resolve()
    if not root.is_dir():
        pytest.fail("SLEEPLAB_PRIVATE_RESMED_CARD must point to an existing directory")

    loader = ResMedNativeLoader()
    detected = loader.detect(root)
    assert len(detected) == 1, "private card must resolve to exactly one ResMed device"

    first = loader.import_data(detected[0], ImportOptions())
    second = loader.import_data(detected[0], ImportOptions())

    def aggregates(run):
        return {
            "sessions": len(run.sessions),
            "blocks": sum(len(session.blocks) for session in run.sessions),
            "events": sum(len(session.events) for session in run.sessions),
            "signals": sum(len(session.signals) for session in run.sessions),
            "settings": sum(len(session.settings) for session in run.sessions),
            "summary_only": sum(
                any(
                    value.key == "has_detailed_data" and value.value is False
                    for value in session.derived_values
                )
                for session in run.sessions
            ),
            "warnings": sorted({warning.code for warning in run.warnings}),
        }

    result = aggregates(first)
    assert result == aggregates(second), "repeat parse changed aggregate output"
    assert result["sessions"] > 0
    assert first.capabilities.oximetry.validation != "validated"
    print(
        "private-card aggregate soak passed: "
        f"sessions={result['sessions']} blocks={result['blocks']} "
        f"events={result['events']} signals={result['signals']} "
        f"settings={result['settings']} summary_only={result['summary_only']} "
        f"warning_codes={len(result['warnings'])}"
    )
