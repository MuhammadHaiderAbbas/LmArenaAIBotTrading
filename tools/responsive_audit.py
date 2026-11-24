#!/usr/bin/env python3
"""Flag layout patterns that break at small widths.

Not a renderer — a checklist. Each rule encodes a failure I have actually seen in this
codebase or that is near-certain at 320px: unbounded grids, fixed pixel widths, tables
with no scroll container, and long unbroken strings in narrow cells.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"

CHECKS = [
    (
        "unprefixed-grid",
        re.compile(r'(?<!:)\bgrid-cols-([3-9]|1[0-2])\b'),
        "multi-column grid with no responsive prefix: will be cramped below ~400px",
    ),
    (
        "fixed-width",
        re.compile(r'(?<![\w-])(w|min-w|max-w)-\[(\d+)px\]'),
        "fixed pixel width: overflows narrow viewports",
    ),
    (
        "unscrollable-table",
        re.compile(r'<table[^>]*className="[^"]*table-base'),
        "table: its Panel body must carry overflow-x-auto",
    ),
    (
        "nowrap-cell",
        re.compile(r'(?<![\w:-])whitespace-nowrap'),
        "nowrap: forces horizontal overflow instead of wrapping",
    ),
    (
        "huge-horizontal-padding",
        re.compile(r'className="[^"]*\bpx-(1[2-9]|[2-9]\d)\b'),
        "very large horizontal padding: wastes a 320px viewport",
    ),
]


def main() -> int:
    failures = 0
    for path in sorted(ROOT.rglob("*.tsx")):
        if "test" in path.name:
            continue
        lines = path.read_text().splitlines()
        for lineno, line in enumerate(lines, 1):
            if line.strip().startswith(("//", "*", "/*")):
                continue
            for name, pattern, message in CHECKS:
                if not pattern.search(line):
                    continue
                if name == "unscrollable-table":
                    # A table is acceptable if its Panel body scrolls, which is
                    # checked by the paired rule below.
                    continue
                if name == "fixed-width" and "max-w-[" in line:
                    continue
                failures += 1
                print(f"{path.relative_to(ROOT)}:{lineno}: [{name}] {message}")
                print(f"    {line.strip()[:120]}")

    # Every Panel that renders a table must let it scroll sideways. `Panel` clips
    # content (`overflow-hidden`), so a table with nowhere to scroll is cut off.
    for path in sorted(ROOT.rglob("*.tsx")):
        if "test" in path.name:
            continue
        text = path.read_text()
        for match in re.finditer(r"<Panel(?P<attrs>[^>]*?)>", text, re.S):
            attrs = match.group("attrs")
            after = text[match.end() : match.end() + 600]
            if "<table" in after and "scrollBody" not in attrs and "overflow-x-auto" not in attrs:
                failures += 1
                line = text[: match.start()].count("\n") + 1
                print(
                    f"{path.relative_to(ROOT)}:{line}: [table-scroll] "
                    "Panel holds a table but does not scroll"
                )

    print(f"\n{'-' * 60}")
    print(f"{failures} issue(s)" if failures else "No responsive issues found")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
