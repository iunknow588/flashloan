"""Finish the D-002 update: counts, module set, and the occupancy order.

The first pass left two placeholders unresolved and the task count still
said 19 while 22 secondary tasks are registered.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, read, replace_once, restore, write  # noqa: E402

PLAN = (
    Path(__file__).resolve().parents[3]
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "D-002-PLAN.md"
)


def main() -> None:
    text = read(PLAN)

    # The first pass replaced the note with raw placeholders.
    text = text.replace("@D-001@", chr(96) + "D-001" + chr(96))

    text = replace_once(
        text,
        "| **总改动模块** | @M-3@ ＋ @M-4@ ＋ @M-5@ ＋ @M-6@ ＋ @M-7@ ＋ @M-8@ ＋ @M-9@ ＋ @M-10@ |",
        "| **总改动模块** | @M-1@ ＋ @M-2@ ＋ @M-3@ ＋ @M-4@ ＋ @M-5@ ＋ @M-6@ ＋ @M-7@ ＋ @M-8@ ＋ "
        "@M-9@ ＋ @M-10@ ＋ @M-12@ |",
        "total module set",
    )
    text = replace_once(
        text,
        "| **二级任务数** | **19**（@T-方授储.01@ … @T-储.04@，**按模块分组**） |",
        "| **二级任务数** | **22**（@T-方授储.01@ … @T-储.04@，**按模块分组**；含 "
        "**2** 个守卫任务 @T-方.01@ / @T-闭.01@ ＋ **1** 个快照一致性任务 @T-池复.01@） |",
        "secondary task count",
    )
    text = replace_once(
        text,
        "- [ ] **全部 17** 个二级任务**已闭合并归档**（@tasks/@ 下**为空**）",
        "- [ ] **全部 22** 个二级任务**已闭合并归档**（@tasks/@ 下**为空**）",
        "completion definition count",
    )

    # Put the occupancy table into module order.
    lines = text.split(NL)
    start = None
    end = None
    for index, line in enumerate(lines):
        if line.startswith(restore("| @M-3@ | 方向层 |")):
            start = index
        if start is not None and line.startswith(restore("| @M-12@ | 接口层")):
            end = index
            break
    if start is None or end is None:
        raise SystemExit("occupancy table not found")
    block = lines[start : end + 1]
    def module_number(row: str) -> int:
        head = row.split("|")[1].strip().strip(chr(96))
        return int(head.split("-")[1])

    lines[start : end + 1] = sorted(block, key=module_number)
    text = NL.join(lines)

    write(PLAN, text)
    print("D-002 counts and occupancy order updated")


if __name__ == "__main__":
    main()
