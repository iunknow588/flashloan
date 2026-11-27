"""Correct the acceptance item counts of the two registered tasks.

The checklists of @T-方.03@ and @T-核.03@ carry **11** and **16** closed
boxes; the rows that were written first said 9 and 12.  The numbers are
measured, so the ledger has to carry the measured ones.

The script is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import read, replace_once, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"


EDITS = [
    (
        "（已闭合 · **9 / 9** · 价格排序方向守卫）",
        "（已闭合 · **11 / 11** · 价格排序方向守卫）",
        "index row count",
    ),
    (
        "（代码已落地 · **12 / 12** · 部署闸门待办）",
        "（代码已落地 · **16 / 16** · 部署闸门待办）",
        "he index row count",
    ),
    (
        "| @T-方.03@ | 方向池的价格排序方向守卫 | **2026-11-26** | @M-3@ | **9** 项全闭合；",
        "| @T-方.03@ | 方向池的价格排序方向守卫 | **2026-11-26** | @M-3@ | **11** 项全闭合；",
        "closed row count",
    ),
]


def main() -> None:
    text = read(REGISTRY)
    changed = 0
    for old, new, label in EDITS:
        if restore(old) in text:
            text = replace_once(text, old, new, label)
            changed += 1
    if changed:
        write(REGISTRY, text)
    print("fix_d001_fang03_counts: %d edit(s)" % changed)


if __name__ == "__main__":
    main()
