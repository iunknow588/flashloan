"""Sort the D-002 module occupancy table by module number."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, read, restore, write  # noqa: E402

PLAN = (
    Path(__file__).resolve().parents[3]
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "D-002-PLAN.md"
)


def main() -> None:
    text = read(PLAN)
    lines = text.split(NL)
    start = None
    end = None
    for index, line in enumerate(lines):
        if line.startswith(restore("| @M-1@ |")):
            start = index
        if start is not None and line.startswith(restore("| @M-12@ |")):
            end = index
            break
    if start is None or end is None:
        raise SystemExit("occupancy table not found")
    block = lines[start : end + 1]

    def module_number(row: str) -> int:
        head = row.split("|")[1].strip().strip(chr(96))
        return int(head.split("-")[1])

    lines[start : end + 1] = sorted(block, key=module_number)
    write(PLAN, NL.join(lines))
    print("occupancy table sorted")


if __name__ == "__main__":
    main()
