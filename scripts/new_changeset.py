#!/usr/bin/env python3
"""Create ``changesets/<version>.md`` from ``changesets/_template.md`` for the current version.

Usage:
    python scripts/new_changeset.py [Patch|Minor|Major]
"""

from __future__ import annotations

import sys
from datetime import date

from check_changeset import CHANGESETS, ROOT, current_version

TYPES = ("Patch", "Minor", "Major")


def main(argv: list[str]) -> int:
    change_type = argv[0] if argv else "Patch"
    if change_type not in TYPES:
        print(f"Type must be one of {', '.join(TYPES)}; got {change_type!r}.", file=sys.stderr)
        return 2

    version = current_version()
    target = CHANGESETS / f"{version}.md"
    relative = target.relative_to(ROOT).as_posix()
    if target.exists():
        print(
            f"{relative} already exists - bump __version__ in _version.py first.",
            file=sys.stderr,
        )
        return 1

    template = (CHANGESETS / "_template.md").read_text(encoding="utf-8")
    content = (
        template.replace("{{PackageId}}", "ninjavault-cdn")
        .replace("{{Version}}", version)
        .replace("{{Date}}", date.today().isoformat())
        .replace("{{Type}}", change_type)
    )
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"Created {relative} - fill in Changes / Why / Breaking Changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
