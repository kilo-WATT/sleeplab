"""Regression coverage for the app version surfaced in the UI footer.

The footer reads `/version`, which is fed by `get_app_version()` parsing the
committed `VERSION` file. During the alpha.8 OSCAR comparison the footer still
advertised `v2.0.0-alpha.2` because the `VERSION` file had not been bumped past
the alpha.2 release even though the tagged milestone was alpha.8. These tests
pin the parse behavior and guard against the version source of truth going
stale again.
"""

import re

from api.main import VERSION_FILE, get_app_version, normalize_version


def test_get_app_version_extracts_semver_from_bracketed_version_file():
    """`get_app_version` returns the bracketed semver, not the calver prefix."""
    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    match = re.search(r"\[([^\]]+)\]", raw)
    assert match, f"VERSION must embed a bracketed semver, got: {raw!r}"
    assert get_app_version() == match.group(1)


def test_committed_version_is_not_stale_alpha2():
    """The committed VERSION must not regress to the stale alpha.2 footer value.

    Parses the `2.0.0-alpha.N` pre-release number and asserts it is at least the
    current alpha.9 milestone, so a forgotten bump (the recurring footer bug) fails
    loudly rather than silently shipping an old version string to users.
    """
    version = get_app_version()
    match = re.fullmatch(r"2\.0\.0-alpha\.(\d+)", version)
    assert match, f"VERSION semver must be a 2.0.0-alpha.N pre-release, got {version!r}"
    assert int(match.group(1)) >= 9, f"VERSION {version!r} is stale (expected >= alpha.9)"


def test_committed_version_matches_latest_published_alpha():
    assert get_app_version() == "2.0.0-alpha.13"


def test_sleeplab_version_env_override_wins(monkeypatch):
    """An explicit `SLEEPLAB_VERSION` overrides the committed VERSION file."""
    monkeypatch.setenv("SLEEPLAB_VERSION", "2.0.0-beta.1")
    assert get_app_version() == "2.0.0-beta.1"


def test_normalize_version_strips_v_prefix_and_whitespace():
    assert normalize_version("  v2.0.0-alpha.8 ") == "2.0.0-alpha.8"
    assert normalize_version(None) is None
