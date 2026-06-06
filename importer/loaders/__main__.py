"""Inspect an SD-card or extracted-archive root with the loader registry."""

import argparse
import json
from collections.abc import Sequence

from .inspection import inspect_source_root


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a CPAP SD-card or extracted-archive root without importing it."
    )
    parser.add_argument("source_root", help="Mounted SD card or extracted archive root")
    args = parser.parse_args(argv)
    snapshot = inspect_source_root(args.source_root)
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0 if snapshot["matched"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
