#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def migration_files() -> list[Path]:
    return sorted(Path("migrations").glob("*.sql"))


def validate_names(files: list[Path]) -> list[str]:
    errors: list[str] = []
    prefixes: dict[str, list[str]] = {}

    for path in files:
        match = MIGRATION_RE.match(path.name)
        if not match:
            errors.append(f"{path}: migration filename must match NNN_snake_case.sql")
            continue
        prefixes.setdefault(match.group(1), []).append(str(path))

    for prefix, paths in sorted(prefixes.items()):
        if len(paths) > 1:
            errors.append(f"Duplicate migration prefix {prefix}: {', '.join(paths)}")

    numbers = sorted(int(prefix) for prefix in prefixes)
    if numbers:
        expected = list(range(1, numbers[-1] + 1))
        missing = sorted(set(expected) - set(numbers))
        if missing:
            formatted = ", ".join(f"{number:03d}" for number in missing)
            errors.append(f"Missing migration number(s): {formatted}")

    return errors


def validate_pr_changes(base_ref: str) -> list[str]:
    errors: list[str] = []
    merge_base = run_git(["merge-base", base_ref, "HEAD"])
    diff = run_git(["diff", "--name-status", f"{merge_base}...HEAD", "--", "migrations"])

    if not diff:
        return errors

    for line in diff.splitlines():
        status, *paths = line.split("\t")
        path = paths[-1] if paths else ""
        if not path.endswith(".sql"):
            continue
        if status.startswith("A"):
            continue
        errors.append(
            f"{path}: existing migrations should not be modified, deleted, or renamed in PRs; add a new migration instead"
        )

    return errors


def main() -> int:
    base_ref = sys.argv[1] if len(sys.argv) > 1 else None
    errors = validate_names(migration_files())

    if base_ref:
        errors.extend(validate_pr_changes(base_ref))

    if errors:
        for error in errors:
            print(f"::error::{error}")
        return 1

    print("Migration safety checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
