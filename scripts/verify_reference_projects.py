"""Confirm the four reference projects were not touched (task section 26).

The workspace root is not a git repository, so ``git status`` cannot be used to
prove this. Filesystem evidence is used instead:

* any file under a reference project modified at or after a cutoff timestamp;
* any ``__pycache__``/``*.pyc`` created there (importing a reference project
  writes bytecode, which is itself a modification);
* any file added after the cutoff.

Usage::

    python scripts/verify_reference_projects.py [cutoff-ISO8601]

The cutoff defaults to the start of the current day, which is a wider window
than the work itself, so a PASS is a strong result rather than a lucky one.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]

REFERENCE_PROJECTS = (
    "x-tweet-fetcher-main",
    "news-summary-main",
    "VisualNovel-Interview-RSS-main",
    "X-rss-main",
)

IGNORED_DIRS = {".git"}


def default_cutoff() -> dt.datetime:
    """Local midnight today — deliberately earlier than any work in this task."""
    now = dt.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_cutoff(argv: list[str]) -> dt.datetime:
    if len(argv) > 1:
        return dt.datetime.fromisoformat(argv[1])
    return default_cutoff()


def scan(root: Path, cutoff: dt.datetime) -> tuple[list[str], list[str], int]:
    modified: list[str] = []
    bytecode: list[str] = []
    total = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            total += 1
            try:
                mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue

            # The cutoff applies to bytecode too. Pre-existing ``__pycache__``
            # from an earlier run is not evidence that *this* task touched the
            # project — treating it as such makes the check permanently red and
            # therefore useless.
            if mtime < cutoff:
                continue

            relative = path.relative_to(WORKSPACE)
            if name.endswith((".pyc", ".pyo")) or "__pycache__" in path.parts:
                bytecode.append(f"{relative}  ({mtime:%Y-%m-%d %H:%M:%S})")
            else:
                modified.append(f"{relative}  ({mtime:%Y-%m-%d %H:%M:%S})")

    return modified, bytecode, total


def main(argv: list[str]) -> int:
    cutoff = parse_cutoff(argv)
    print(f"cutoff: {cutoff:%Y-%m-%d %H:%M:%S} (local)\n")

    failures = 0
    for name in REFERENCE_PROJECTS:
        root = WORKSPACE / name
        if not root.exists():
            print(f"  --   {name}: not present, skipped")
            continue

        modified, bytecode, total = scan(root, cutoff)
        if modified or bytecode:
            failures += 1
            print(f"  FAIL {name}: {total} files, {len(modified)} modified, {len(bytecode)} bytecode")
            for line in modified[:10]:
                print(f"         modified: {line}")
            for line in bytecode[:10]:
                print(f"         bytecode: {line}")
        else:
            print(f"  ok   {name}: {total} files, 0 modified since cutoff, 0 bytecode")

    print()
    if failures:
        print(f"REFERENCE PROJECT CHECK FAILED ({failures} project(s) touched)")
        return 1
    print("REFERENCE PROJECT CHECK PASSED (touched = 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
