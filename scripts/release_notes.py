"""Print the CHANGELOG.md section for a version, for the GitHub Release body.

Usage: python scripts/release_notes.py v0.3.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(version: str, changelog: str) -> str:
    version = version.removeprefix("v")
    heading = re.compile(rf"^## {re.escape(version)}(?:\s|$)")
    lines = changelog.splitlines()
    for start, line in enumerate(lines):
        if heading.match(line):
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            return "\n".join(lines[start + 1 : end]).strip() + "\n"
    raise SystemExit(f"CHANGELOG.md has no '## {version}' section")


if __name__ == "__main__":
    sys.stdout.write(section(sys.argv[1], CHANGELOG.read_text(encoding="utf-8")))
