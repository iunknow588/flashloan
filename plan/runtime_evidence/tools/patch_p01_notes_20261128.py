#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Correct the P-01 ledger notes and append the round of 2026-11-28.

Note 10 of plan/tasks/P-01-池子重构/README.md still carried the withdrawn
conclusion ("0 / 19 可复价" / "退役语义") and the superseded counters.
This script rewrites those two items and appends note 11, which records what
this round actually did: the depth repair through the pool tool, the scope
re-materialization, the cascade rebuild and the corrected counters.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan/runtime_evidence/tools"))

from mdpatch import insert_after, read, replace_once, write  # noqa: E402

TARGET = REPO / "plan/tasks/P-01-池子重构/README.md"

NOTE_11 = """> **本轮（2026-11-28 · 深度修复 ＋ 作用域重新物化 ＋ 编码对齐）**：
> ① **深度重测（池子工具 · 唯一写入方）**：**9** 个 @depth_status = 'unavailable'@ 的池
> （**测量缺失**，不是「薄」）⇒ dry run @returncode = 0@ / @refreshedCount = 9@ / @writtenCount = 0@；
> apply **@writtenCount = 9@**、**@errors = []@** ⇒ 档位 **deep 4 / medium 2 / dust 3**
> ⇒ **测量缺失已消除**；
> ② **作用域重新物化**：两个作用域各 **@insertedRows = 6@**、**@deletedRows = 0@**、
> @activePoolCount@ **26 → 32**、**@idempotentReplay = false@** ⇒ 表行数 **428 → 434**
> ⇒ 池覆盖 **6 / 15 → 12 / 15**、路由完整覆盖 **0 / 19 → 6 / 19**；
> ③ **级联重建**：candidateEdge **52 → 64**、candidateExchange **216 → 278**、
> closedLoops **8 → 36** 闭环（**24 → 108** 跳）⇒ 新代次复核版本 **70 / 71** 共 **9** 行
> **全部有明确盈亏**（**全负**，灰尘级）⇒ **恢复的路由正在被真实定价**；
> ④ **⚠️ 操作陷阱**：作用域重建接口按**幂等键**重放**已存结果** —— 重建脚本原用**固定幂等键**，
> 于是「重建」只**重放了旧结果**（@idempotentReplay = true@、@changedRows = 0@），**池集根本没重新物化**；
> 已改为**每次调用唯一幂等键**（`rehearse_scope_rebuild_20261127.py`）⇒ **深度修好之后必须重建作用域，且必须用新的幂等键**；
> ⑤ **诚实边界**：剩下 **3** 个池的深度档位**本身就是灰尘级**（**链上事实**）⇒ **未放宽任何判据**；
> 路由覆盖停在 **12 / 15** 池、**6 / 19** 路由（**D5 原写的「15 / 15、19 / 19」已被读数推翻并订正**）；
> **未伪造任何盈亏**、**未填 0**、**未删任何历史行**；广播请求 **0** 行 ⇒ 「**持续产生链上广播交易记录**」**未达成**（**市场条件**）；
> ⑥ **编码对齐**：仓库设计要求**读写文件都是 UTF-8 无 BOM ＋ LF** ⇒ 入库索引实测 **5957 / 5966** 文本文件已是 **LF**
> （本轮改动的文件 **100% @i/lf@**）；工作区看到的 CRLF 是 @core.autocrlf=true@ 的 checkout 产物；
> 归一工具由 @normalize_crlf_20261125.py@（写 CRLF）改为 **@normalize_text_lf_20261128.py@**（写 LF、去 BOM），
> 校验器 @verify_pool_refactor_docs_20261123.py@ 的编码基线同步改为「**无 BOM ＋ LF**」；
> ⑦ **台账**：`D-001` §5.5 补登本轮、`D-014` 聚合行改写、二级 **57 → 60**、总账 **116 → 119**
> （**3** 项增量＝`T-池编.02` 的**诚实边界永久留占位**，其 D1–D5 ＋ D6 **已按实测读数闭合**）。"""


def main() -> int:
    text = read(TARGET)

    text = replace_once(
        text,
        "**19** 条缺值复核 **0 / 19 可复价**（其跳的池**已从市场目录消失**）"
        "⇒ **填数即伪造** ⇒ 收口＝**退役语义**（`retired` ＋ 原因，**不删行**、**不填数**、**不碰门禁**）；",
        "**19** 条缺值复核 ⇒ **原「0 / 19 可复价、其跳的池已从市场目录消失」已被推翻**"
        "（**15 / 15** 个去重池 hash **全部仍在主池表 `v4_exchange_pools`（2550 行）**、"
        "**19 / 19** 条路由**每一跳**都还在池表 ⇒ **池从未消失，缺的是作用域的物化范围**）"
        "⇒ 收口改为：**先复价**（由 `T-池编.02` 恢复作用域 ＋ 深度，让路由**重新进入目录、被真实定价**），"
        "**退役语义降级为兜底**（只对恢复后**仍不可定价**的行使用；`retired` ＋ 原因，**不删行**、**不填数**、**不碰门禁**）；",
        "note-10-item-2",
    )

    text = replace_once(
        text,
        "> ④ **台账**：二级 **49 → 57**、总账 **108 → 116**、登记 **43** 个二级任务"
        "（在盘 **16** ＋ 已闭合归档 **27**）；",
        "> ④ **台账**：二级 **49 → 57**、总账 **108 → 116**、登记 **42** 个二级任务"
        "（在盘 **15** ＋ 已闭合归档 **27**）；",
        "note-10-item-4",
    )

    text = insert_after(
        text,
        "> ⑥ **诚实边界**：**未放宽任何门禁**、**未碰 git**、**未执行真实交易**；",
        NOTE_11,
        "note-11",
    )

    write(TARGET, text)
    print("P-01 README notes updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
