#!/usr/bin/env python3
"""Fail when the current package version has no ``changesets/<version>.md``.

Usage:
    python scripts/check_changeset.py            # check the change-set exists
    python scripts/check_changeset.py --version  # print the current version and exit
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "ninjavault_cdn" / "_version.py"
CHANGESETS = ROOT / "changesets"


def current_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"Could not find __version__ in {VERSION_FILE}")
    return match.group(1)


def main(argv: list[str]) -> int:
    version = current_version()
    if "--version" in argv:
        print(version)
        return 0

    changeset = CHANGESETS / f"{version}.md"
    relative = changeset.relative_to(ROOT).as_posix()
    if changeset.is_file():
        print(f"ok       ninjavault-cdn {version}  ({relative})")
        return 0

    print(
        f"MISSING  ninjavault-cdn {version}  (expected: {relative} - run: make changeset)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
