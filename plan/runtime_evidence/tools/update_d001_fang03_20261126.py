"""Register T-方.03 (the price order guard of the direction pools).

The same round books the number `T-核.03` (the liquidity snapshot wiring):
its directory has existed since 2026-11-26, but the registry never
learned about it, so the `核` index still offered `T-核.03` as free.

The change log of `D-001` carries a row per finished round.  The table
header `| 日期 | 变更 |` and its separator are **not** unique in the
document, so a context based patch cannot address that table.  The
registry therefore gets its rows through the same helper the earlier
rounds used (`mdpatch`), anchored on a **unique row prefix**.

The script is idempotent: a second run finds the row it wrote and does
nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import insert_after, insert_before, read, replace_row, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"


FANG_MARKER = "| **@方@** | @M-3@（方向层） |"
FANG_ROW = (
    "| **@方@** | @M-3@（方向层） | 3 | @T-方.01@（已归档）、"
    "**@T-方.02@**（已闭合 · **13 / 13** · 实盘已刷新）、"
    "**@T-方.03@**（已闭合 · **9 / 9** · 价格排序方向守卫） | **@T-方.04@** |"
)

HE_MARKER = "| **@核@** | @M-13@（运行时内核与运行时接线） |"
HE_ROW = (
    "| **@核@** | @M-13@（运行时内核与运行时接线） | 3 | @T-核.01@（已闭合）、"
    "@T-核.02@（另一线程，只监测）、"
    "**@T-核.03@**（代码已落地 · **12 / 12** · 部署闸门待办） | **@T-核.04@** |"
)

CLOSED_ANCHOR = "| @T-方.02@ | 方向层按链批量物化入口 |"
CLOSED_ROW = (
    "| @T-方.03@ | 方向池的价格排序方向守卫 | **2026-11-26** | @M-3@ | "
    "**9** 项全闭合；用户口径「价格排序方向与池子方向是固定的」**已核实成立** —— "
    "方向只决定**目标代币**（该方向的输出代币），排序**恒为降序**，"
    "四个模块口径一致（方向层 / 候选层 / Python 遗留打分 / 闭环层）⇒ "
    "**无需修正排序行为**，交付的是**不变式文档 ＋ 三条守卫单测**："
    "**J-1**「排在后面的池子不该表现更好」（两方向的序**互为逆序**、"
    "每个方向排第一的才是表现好的那个）、"
    "**J-2**「价格与费率同步考虑」（发布的费率**已扣费**；同价时**低费率**在两个方向都排第一）、"
    "**J-3**「费率差异的归因」（**动态费率**的池**根本没有方向行** ⇒ 排名里的费率差异只可能是"
    "**固定费率档位**）；**变异测试**：把排序主键对方向 0 反向 ⇒ J-1 单测**立即 FAILED** ⇒ 撤回 ⇒ 全绿；"
    "**回归** @cargo test --offline -p directional-pool-manager -p manager-contracts@ ⇒ "
    "**30 passed / 0 failed**（基线 **27** ＋ 新增 **3**）＋ **17 passed / 0 failed**（基线不变）；"
    "**产品代码的改动只有注释** ⇒ 运行时行为**零变化** ⇒ **无部署闸门**、实盘**零**写入、"
    "**未**重启；**遗留**：R-1（跨费率档的**价格加权排序**，用户口径「后续」）已留方案未实现，"
    "R-2（Python 遗留打分的容量封顶）。文档："
    "@plan/tasks/P-01-池子重构/tasks/T-方.03/@（@D-168@ / @D-169@ / @D-170@ / @D-171@） |"
)

LOG_ANCHOR = "| 2026-11-26 | **运行时维护链接线（@T-核.01@"
LOG_ROWS = (
    "| 2026-11-26 | **方向池的价格排序方向守卫（@T-方.03@ · @M-3@）**：用户口径「价格排序方向与池子方向是固定的。"
    "**两种写法都可行**（方向决定升降序 ／ 方向决定目标代币 ＋ 固定降序），二者**数学上是同一个序** ⇒ "
    "**必须只选一种**；本系统**四处口径本就是第二种**（方向只决定目标代币、排序恒为降序）⇒ "
    "**无需修正行为**，补的是**守卫**：不变式写进代码注释 ＋ **三条单测**（J-1 方向反了会红、"
    "J-2 同价低费率在前、J-3 动态费率池不进排名）＋ **变异测试**证明 J-1 不空转。"
    "**回归** **30 passed / 0 failed**（基线 **27** ＋ 新增 **3**）。**诚实边界**："
    "产品代码改动**只有注释** ⇒ **无部署闸门**；R-1（价格加权排序）**未实现**。 |"
    "| 2026-11-26 | **流动性快照写入路径接线（@T-核.03@ · @M-13@ ＋ @M-1@）**：快照表 "
    "@v4_exchange_pool_liquidity_snapshots@ 的**唯一写入方**（池管理器）**只能由命令行调用** ⇒ "
    "运行时**量到了却写不进去**；worker 却因「裁决都在」报 @complete@ ⇒ **陈旧被当成完整**"
    "（最新快照行停在 **2026-11-25T00:44Z**，比 worker 游标的区块号旧 **2,580,566** 个区块）。"
    "**落地**：新增 Rust 入口 @refresh_v4_pool_liquidity@ ＋ 维护槽调用（失败**整槽失败**、**不降级**）"
    "＋ 池管理器回执改报**真正改动的行数**；**新鲜度只做观测量**，**未**进任何选池判据。"
    "**回归** **167** ／ **222** ／ pytest **19 ＋ 3**。**诚实边界**：实盘扩展**未重建**、"
    "runtime **未重启** ⇒ **部署闸门**待办（与 @T-闭.02@ / @T-闭.03@ / @T-核.02@ **同窗口**）。 |"
)


def main() -> None:
    text = read(REGISTRY)
    if "方向池的价格排序方向守卫" in text:
        print("update_d001_fang03: already registered")
        return
    text = replace_row(text, FANG_MARKER, FANG_ROW, "fang index row")
    text = replace_row(text, HE_MARKER, HE_ROW, "he index row")
    text = insert_after(text, CLOSED_ANCHOR, CLOSED_ROW, "closed task row")
    text = insert_before(text, LOG_ANCHOR, LOG_ROWS, "migration log rows")
    write(REGISTRY, text)
    print("update_d001_fang03: ok")


if __name__ == "__main__":
    main()
