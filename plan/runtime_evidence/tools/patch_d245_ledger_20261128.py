#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Correct the ledger readings that this round moved.

The round added the **I** group to `D-241`: nine read-only readings and
three regression rows are closed, four open items and four permanent
honest-boundary placeholders stay open.  The recount script measured the
result: `T-池编.02` unchecked **11 -> 19**, the secondary total
**68 -> 76**, the grand total **127 -> 135**.

The readings live inside single long table rows, so they are replaced by
substring rather than by line.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan/runtime_evidence/tools"))

from mdpatch import read, replace_once, write  # noqa: E402


REGISTRY = REPO / "plan/D-001-TASK_REGISTRY.md"
LEDGER = REPO / "plan/tasks/P-01-池子重构/docs/D-014-未完成任务清单_20261123.md"


def main() -> int:
    registry = read(REGISTRY)
    registry = replace_once(
        registry,
        "**⑪ 台账**：二级 **68 → 72**、总账 **127 → 131**"
        "（增量＝`D-245` 的 **I4** 组 **4** 项**诚实边界永久留占位** ＋ I2 开放项）",
        "**⑪ 台账**：二级 **68 → 76**、总账 **127 → 135**"
        "（增量＝`D-245` 的 **I2** 组 **4** 项**开放项** ＋ **I4** 组 **4** 项"
        "**诚实边界永久留占位**；`recount_remaining_tasks_20261125.py` 实测 "
        "`T-池编.02` unchecked **11 → 19**）",
        "registry ledger of D-245",
    )
    write(REGISTRY, registry)

    ledger = read(LEDGER)
    ledger = replace_once(
        ledger,
        "总读数 **101 → 114 → 116 → 119 → 123 → 127**"
        "（二级 **57** ＋ **60** ＋ **64** ＋ **68** ＋ 一级 **8** ＋ 横切 **51**）",
        "总读数 **101 → 114 → 116 → 119 → 123 → 127 → 135**"
        "（二级 **57** ＋ **60** ＋ **64** ＋ **68** ＋ **76** ＋ 一级 **8** ＋ 横切 **51**）",
        "ledger headline",
    )
    ledger = replace_once(
        ledger,
        "→ **116** → **119** → **123** → **127** | 实测（**68** ＋ **8** ＋ **51**）",
        "→ **116** → **119** → **123** → **127** → **135** | 实测（**76** ＋ **8** ＋ **51**）",
        "ledger recount table",
    )
    ledger = replace_once(
        ledger,
        "**2026-11-28 再登记 `D-244`**（单 tick 与跨 tick 两个测量的分离发布）—— "
        "其 checklist **H** 组新增 **4** 项未勾（＝ **4** 项**诚实边界永久留占位**："
        "不伪造 ／ 不放宽广播门禁 ／ 不预判 ／ 不代运行时关闭）⇒ 二级 **64 → 68**、"
        "总账 **123 → 127**",
        "**2026-11-28 再登记 `D-244`**（单 tick 与跨 tick 两个测量的分离发布）—— "
        "其 checklist **H** 组新增 **4** 项未勾（＝ **4** 项**诚实边界永久留占位**："
        "不伪造 ／ 不放宽广播门禁 ／ 不预判 ／ 不代运行时关闭）⇒ 二级 **64 → 68**、"
        "总账 **123 → 127**；**2026-11-28 再登记 `D-245`**（单 tick 全不盈利的成因审查 "
        "＋ USDG 闭环缺口的根因）—— 其 checklist **I** 组新增 **8** 项未勾"
        "（＝ **I2** 的 **4** 项开放项：生产数据修复 ／ 内核实测两条净正三角形 ／ "
        "同源第二面 ／ 4 个校验脚本重写，＋ **I4** 的 **4** 项**诚实边界永久留占位**："
        "不伪造 ／ 不放宽广播门禁 ／ 不预判 ／ 不代运行时关闭）⇒ 二级 **68 → 76**、"
        "总账 **127 → 135**",
        "ledger counting chain",
    )
    ledger = replace_once(
        ledger,
        "→ **42** → **55** → **49** → **60** → **64** → **68** |",
        "→ **42** → **55** → **49** → **60** → **64** → **68** → **76** |",
        "ledger attribution chain",
    )
    ledger = replace_once(
        ledger,
        "**2026-11-28 再登记 `D-244`** —— 其 checklist **H4** 组 **4** 项"
        "**诚实边界永久留占位** ⇒ 归属任务读数 **64 → 68**、总账 **123 → 127**",
        "**2026-11-28 再登记 `D-244`** —— 其 checklist **H4** 组 **4** 项"
        "**诚实边界永久留占位** ⇒ 归属任务读数 **64 → 68**、总账 **123 → 127**；"
        "**2026-11-28 再登记 `D-245`** —— 其 checklist **I2** 组 **4** 项**开放项** "
        "＋ **I4** 组 **4** 项**诚实边界永久留占位** ⇒ 归属任务读数 **68 → 76**、"
        "总账 **127 → 135**",
        "ledger attribution note",
    )
    write(LEDGER, ledger)
    print("ledger readings updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
