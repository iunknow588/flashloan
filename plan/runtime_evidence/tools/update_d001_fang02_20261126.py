"""Register T-方.02 (the direction layer chain entry) in the task registry.

The change log of `D-001` carries a row per finished round.  The table
header `| 日期 | 变更 |` and its separator `|---|---|---|` are **not**
unique in the document, so a context based patch cannot address that
table.  The registry therefore gets its rows through the same helper the
earlier rounds used (`mdpatch`), anchored on a **unique row prefix**.

The script is idempotent: a second run finds the row it wrote and does
nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_after, read, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"

ANCHOR = "| 2026-11-26 | **运行时维护链接线（`T-核.01`"

ROW = (
    "| 2026-11-26 | **方向层按链批量物化入口（`T-方.02` · `M-3`）**：方向层是闭环的**唯一池权威**，"
    "却**没有自动刷新路径** —— 实盘方向行停在 **2026-11-23T22:46Z**，快照列相对流动性快照表漂移 "
    "**83** 行 / **994** 格（`inv-9` FAIL）。**落地**：新增公共入口 "
    "`materialize_directional_pools_for_chain`（`directional_pool_manager/src/lib.rs:505-556`），"
    "按 `pool_scope_config.enabled = 1` / `scope_key` 升序枚举并逐 scope 调**既有** "
    "`materialize_impl` ⇒ 单 scope 行为**逐字一致**（投影 / 准入 / 容量 / 幂等键 / 回执关联**一字未改**："
    "`lib.rs` 的 diff ＝ **1 hunk / 124 行新增 / 0 行删除**）；失败 scope **记入 `failedScopes`**"
    "（**不吞掉**），运行时按 `not failedScopes` 判链级失败 ⇒ 下游闭环槽**不发布**（**不降级**）；"
    "最小驱动 `examples/materialize_directional_pools_for_chain.rs`。**回归**："
    "`cargo test --offline -p directional-pool-manager` ⇒ **27 passed / 0 failed**（基线 **25** ＋ 新增 **2**）。"
    "**★ 实盘已刷新（首次）**：刷新前留 **692 行**行级备份；干跑读数与只读副本演练**逐字段相等**"
    "（机器比对 `True`）；apply **1.63 秒**、`failedScopes` 空 ⇒ 物化时刻 → **2026-11-25T18:19:39Z**、"
    "版本 **2 → 3**、校验和 `f6836f97…`/`5a609caf…` → `4aa8b791…`/`e0d8d6d1…`、"
    "`inv-9` **FAIL → PASS（0 行 / 0 格）**、全量不变式审计 **9 / 9 PASS**；**闭环层一行未动**"
    "（`closed_loop_version` **21 → 21**，校验和与 `updated_at` 不变）⇒ 闭环重建仍归 `T-闭.03` 部署闸门。"
    "**诚实边界**：运行时「方向层自动刷新槽」**尚未在实盘生效**（Rust 扩展未重建 ⇒ 归 `T-核.02` 线程）；"
    "快照表自身最新只到 **2026-11-25T00:44Z**（上游停更 ⇒ 方向层刷新的上限）。文档："
    "`plan/tasks/P-01-池子重构/tasks/T-方.02/`（`D-144` / `D-145` / `D-146` / `D-147`） |"
)


def main() -> None:
    text = read(REGISTRY)
    if "方向层按链批量物化入口（`T-方.02`" in text:
        print("update_d001_fang02: already registered")
        return
    text = insert_after(text, ANCHOR, ROW, "fang02 migration row")
    write(REGISTRY, text)
    print("update_d001_fang02: ok")


if __name__ == "__main__":
    main()
