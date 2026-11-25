"""Keep the number-index row of the 复广 group at its numbering basis.

The index of a task group is the module set its number was built from.
T-复广.02 later took a forced M-12 (interface layer) dependency, which is
registered per task in section 4.2.1; putting M-12 into the group row
would claim it for T-复广.01 as well.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import read, replace_row, write  # noqa: E402

REGISTRY = Path(__file__).resolve().parents[3] / "plan" / "D-001-TASK_REGISTRY.md"


def main() -> None:
    text = read(REGISTRY)
    text = replace_row(
        text,
        "| **@复广@** |",
        "| **@复广@** | @M-6@（复核层）＋ @M-7@（广播层） | 2 | "
        "@T-复广.01@、@T-复广.02@ | **@T-复广.03@** |",
        "复广 index row",
    )
    write(REGISTRY, text)
    print("复广 index row restored")


if __name__ == "__main__":
    main()
