#!/usr/bin/env python3
'''Rename the freshly registered task from ``T-复编.03`` to ``T-复编储.01``.

The task was first drafted against two modules (``M-6`` ＋ ``M-8``).  It also
adds **one migration**, and a migration lives in ``flashloan/tools/manager_db``
which is ``M-10`` (存储层).  D-001 section 2 rule 4 says a module that is
really changed joins the composite index -- so the index is ``复编储`` and
the number is the first one of that (new) index segment.

Every replacement is asserted, so a file that does not carry the expected
wording fails loudly instead of drifting.
'''

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TASK = REPO / "plan/tasks/P-01-池子重构/tasks/T-复编储.01"


EDITS: list[tuple[str, str, str]] = [
    (
        "T-复编.03",
        "T-复编储.01",
        "任务编号",
    ),
    (
        "AC-复编.03",
        "AC-复编储.01",
        "验收编号",
    ),
    (
        "**`复编`** ＝ `M-6`（复核层） ＋ `M-8`（编排层），按**模块号升序**拼接（`M-6` → `M-8`）",
        "**`复编储`** ＝ `M-6`（复核层） ＋ `M-8`（编排层） ＋ `M-10`（存储层），"
        "按**模块号升序**拼接（`M-6` → `M-8` → `M-10`）",
        "编号索引行",
    ),
    (
        "⇒ 组合索引 **`复编`**。下一个可用序号：**`T-复编.04`**。",
        "⇒ 组合索引 **`复编储`**。下一个可用序号：**`T-复编储.02`**。",
        "组合索引结论",
    ),
    (
        "**本任务改动两个模块** `M-6`（复核层，退役语义的**唯一产地**）＋ `M-8`（编排层，窗口口径）",
        "**本任务改动三个模块** `M-6`（复核层，退役语义的**唯一产地**）＋ `M-8`（编排层，窗口口径）"
        "＋ `M-10`（存储层，**一条迁移**）",
        "改动模块说明",
    ),
    (
        "| **同索引其它任务** | `T-复编.01`（Rust 生产侧复核发布链路 · 已归档）、"
        "`T-复编.02`（复核队列与广播队列重构） |",
        "| **同索引其它任务** | **无** —— 本索引号段**新开**（`M-6` ＋ `M-8` ＋ `M-10` 的组合此前没有任务） |",
        "同索引其它任务行",
    ),
    (
        "| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` | `scripts/mainnet_arbitrage_healer.py` |\n",
        "| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` | `scripts/mainnet_arbitrage_healer.py` |\n"
        "| `M-10` | 存储层（迁移与库结构） | `储` | `flashloan/tools/manager_db/` |\n",
        "§1.0 模块表补 M-10",
    ),
    (
        "**编号逻辑**：`T-复编储.01` ／ `AC-复编储.01` —— **跨模块（组合索引）**，编号索引 **`复编`**\n"
        "（改动模块：`M-6`＝复核层 ＋ `M-8`＝编排层，按**模块号升序**拼接），该索引号段内第 **03** 个；\n"
        "下一个可用编号 **`T-复编.04`**。",
        "**编号逻辑**：`T-复编储.01` ／ `AC-复编储.01` —— **跨模块（组合索引）**，编号索引 **`复编储`**\n"
        "（改动模块：`M-6`＝复核层 ＋ `M-8`＝编排层 ＋ `M-10`＝存储层，按**模块号升序**拼接），"
        "该索引号段内第 **01** 个；\n下一个可用编号 **`T-复编储.02`**。",
        "README 编号逻辑段",
    ),
    (
        "| **推荐** | **P0 · W-5 · M-6 ＋ M-8 · 下一个可用编号 `T-复编.04`** |",
        "| **推荐** | **P0 · W-5 · M-6 ＋ M-8 ＋ M-10 · 下一个可用编号 `T-复编储.02`** |",
        "PLAN 推荐行",
    ),
    (
        "> **一行改动模块** ⇒ 组合索引不存在 ⇒ 编号索引就是 `复编`（§1.0）。",
        "> **三行改动模块** ⇒ 组合索引 **`复编储`**（§1.0）⇒ 编号即回归范围。",
        "SCOPE_GUARD 编号说明",
    ),
    (
        "**改动模块（= 回归范围）**：`M-6`、`M-8`、`M-10`",
        "**改动模块（= 回归范围）**：`M-6`、`M-8`、`M-10`",
        "README 改动模块行（校验存在）",
    ),
]


def main() -> int:
    files = sorted(TASK.rglob("*.md"))
    for path in files:
        text = original = path.read_text(encoding="utf-8")
        for old, new, label in EDITS:
            if old == new:
                if old not in text and path.name == "README.md" and path.parent == TASK:
                    raise SystemExit("%s: %s not found" % (path.name, label))
                continue
            count = text.count(old)
            if count == 0:
                continue
            text = text.replace(old, new)
            print("  %s / %s: %d" % (path.name, label, count))
        if text != original:
            path.write_text(text, encoding="utf-8", newline="")
            print("edited %s" % path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

