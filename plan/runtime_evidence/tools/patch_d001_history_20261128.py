#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append the second-phase recovery row to the D-001 history table.

The row records what this round actually did in production (depth repair
through the pool tool, scope re-materialization, cascade rebuild), the
idempotency trap that made the first rebuild a no-op, the encoding
alignment, and the counters that moved because of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan/runtime_evidence/tools"))

from mdpatch import insert_after, read, write  # noqa: E402

TARGET = REPO / "plan/D-001-TASK_REGISTRY.md"

ROW = (
    "| 2026-11-28 | **深度重测 ＋ 作用域重新物化 ＋ 级联重建 ＋ 编码对齐"
    "（`T-池编.02` 第二阶段 · `M-1` ＋ `M-8`）**：**用户口径**「如果池子无效，"
    "可以使用池子工具修复池子的有效性」＋「**请继续处理。修改完毕后提交到 github**」。"
    "**① 深度重测（池子工具 · 唯一写入方）**：**9** 个 `depth_status = 'unavailable'` 的池"
    "（**测量缺失**，不是「薄」）⇒ dry run `returncode = 0` / `refreshedCount = 9` / "
    "`writtenCount = 0`；apply **`writtenCount = 9`**、**`errors = []`** ⇒ 档位 "
    "**deep 4 / medium 2 / dust 3** ⇒ **测量缺失已消除**。"
    "**② 作用域重新物化**：两个作用域各 **`insertedRows = 6`**、**`deletedRows = 0`**、"
    "`activePoolCount` **26 → 32**、**`idempotentReplay = false`** ⇒ 表行数 **428 → 434** "
    "⇒ 池覆盖 **6 / 15 → 12 / 15**、路由完整覆盖 **0 / 19 → 6 / 19**。"
    "**③ 级联重建**：candidateEdge **52 → 64**、candidateExchange **216 → 278**、"
    "closedLoops **8 → 36** 闭环（**24 → 108** 跳）⇒ 新代次复核版本 **70 / 71** 共 **9** 行"
    "**全部有明确盈亏**（**全负**，灰尘级）⇒ **恢复的路由正在被真实定价**。"
    "**④ 操作陷阱**：作用域重建接口按**幂等键**重放**已存结果** ⇒ 重建脚本原用**固定幂等键**，"
    "于是「重建」只**重放了旧结果**（`idempotentReplay = true`、`changedRows = 0`）、"
    "**池集根本没重新物化**；已改为**每次调用唯一幂等键**"
    "（`rehearse_scope_rebuild_20261127.py`）⇒ **深度修好之后必须重建作用域，且必须用新的幂等键**。"
    "**⑤ 诚实边界**：剩下 **3** 个池的深度档位**本身就是灰尘级**（**链上事实**）⇒ "
    "**未放宽任何判据**；路由覆盖停在 **12 / 15** 池、**6 / 19** 路由"
    "（`D-241` D5 原写的「15 / 15、19 / 19」**已被读数推翻并订正**）；"
    "**未伪造任何盈亏**、**未填 0**、**未删任何历史行**；广播请求 **0** 行 ⇒ "
    "「**持续产生链上广播交易记录**」**未达成**（**市场条件**）。"
    "**⑥ 编码对齐**：设计要求**读写文件都是 UTF-8 无 BOM ＋ LF** ⇒ 入库索引实测 "
    "**5957 / 5966** 文本文件已是 **LF**（本轮改动文件 **100% `i/lf`**），"
    "工作区看到的 CRLF 是 `core.autocrlf = true` 的 checkout 产物；归一工具由 "
    "`normalize_crlf_20261125.py`（写 CRLF）改为 **`normalize_text_lf_20261128.py`**"
    "（写 LF、去 BOM），`verify_pool_refactor_docs_20261123.py` 的编码基线同步改为「**无 BOM ＋ LF**」；"
    "`D-237` **整篇改写**（根因＝**作用域收窄**，旧「退役语义」**撤回**）＋ 更名。"
    "**⑦ 台账**：二级 **57 → 60**、总账 **116 → 119**（增量＝`T-池编.02` 的 **3** 项"
    "**诚实边界永久留占位**，其 D1–D5 ＋ D6 **已按实测读数闭合**）；"
    "文档：`D-239` / `D-240` / `D-241` ＋ `docs/D-242` ＋ `docs/D-237`（**已改写**） |"
)


def main() -> int:
    text = read(TARGET)
    text = insert_after(
        text,
        "| 2026-11-28 | **作用域加载模式不得收窄 ＋ 上一轮结论的推翻与更正",
        ROW,
        "history-row-2026-11-28-second-phase",
    )
    write(TARGET, text)
    print("D-001 history row appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
