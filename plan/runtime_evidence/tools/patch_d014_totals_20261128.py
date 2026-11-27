#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update the aggregate counters of D-014 after T-池编.02 was closed out.

D-014 carries the ledger of the remaining (unchecked) acceptance items.
The recount tool (recount_remaining_tasks_20261125.py) is the authority for
the numbers; this script writes that reading back into the two aggregate
rows so the document cannot drift away from the checklists.

The replacements are substrings, not whole lines: these rows are very long
and the file is written with LF line endings.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan/runtime_evidence/tools"))

from mdpatch import insert_after, read, replace_once, write  # noqa: E402

TARGET = REPO / "plan/tasks/P-01-池子重构/docs/D-014-未完成任务清单_20261123.md"


def main() -> int:
    text = read(TARGET)

    text = replace_once(
        text,
        "→ **93** → **97** → **101** | 实测（**42** ＋ **8** ＋ **51**）",
        "→ **93** → **97** → **101** → **116** → **119** | 实测（**60** ＋ **8** ＋ **51**）",
        "unchecked-total-series",
    )
    text = replace_once(
        text,
        "⇒ 二级 **49 → 57**、总账 **108 → 116**）—— 增量来自本轮新登记二级任务的未勾选框",
        "⇒ 二级 **49 → 57**、总账 **108 → 116**；"
        "**2026-11-28 再登记 @T-池编.02@**（作用域加载模式**不得收窄**）—— 其 checklist "
        "**18** 框已闭合、**3** 框未勾（＝ **3** 项**诚实边界永久留占位**）"
        "⇒ 二级 **57 → 60**、总账 **116 → 119**）"
        "—— 增量来自本轮新登记二级任务的未勾选框",
        "unchecked-total-narrative",
    )
    text = replace_once(
        text,
        "→ **57** | **16** 个在盘二级任务目录**逐一实测**",
        "→ **60** | **17** 个在盘二级任务目录**逐一实测**",
        "attributed-total-series",
    )
    text = replace_once(
        text,
        "⇒ 归属任务读数 **49 → 57**、总账 **108 → 116** |",
        "⇒ 归属任务读数 **49 → 57**、总账 **108 → 116**；"
        "**2026-11-28 再登记 @T-池编.02@**（作用域加载模式**不得收窄**）—— 其 checklist "
        "**18** 框已闭合、**3** 框未勾（＝ **3** 项**诚实边界永久留占位**）"
        "⇒ 归属任务读数 **57 → 60**、总账 **116 → 119** |",
        "attributed-total-narrative",
    )
    write(TARGET, text)
    print("D-014 counters updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
