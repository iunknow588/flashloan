#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record the sixth round of ``T-池编.02`` in the registry and the ledger.

Both files carry their history in **very long single lines**, so a line
oriented patch cannot address them.  The replacements below are exact
substring replacements, and the script refuses to write when the anchor
is missing or ambiguous: a silent no-op would leave the ledger
claiming a reading that nobody measured.

Every file is written as UTF-8 without a BOM and with LF line endings.

Usage::

    python patch_d245b_registry_20261128.py [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "plan" / "D-001-TASK_REGISTRY.md"
LEDGER = REPO / "plan" / "tasks" / "P-01-池子重构" / "docs" / "D-014-未完成任务清单_20261123.md"


ROW = (
    "| 2026-11-28 | **USDG 闭环缺口的收口 ＋ 重建链扩为五层（`T-池编.02` 第六轮 · `M-8` ＋ `M-1`）**："
    "**用户口径**「**候选代币库里其实有 USDG，但闭环表把 USDG 作为首/末跳全部跳过。这是严重的问题，请重点考察这个问题发生的原因。**」"
    "＋「**请继续执行推进开发计划……充分利用主网自愈编排器，完成主网的开发任务……**」。"
    "**① 生产修复已落库**：级联按依赖顺序执行（`directionalPools`(core4) → `candidateEdge` → `directionalPools`(candidate_exchange) → `candidateExchange` → `closedLoops`，全部 HTTP 200，`plan/runtime_evidence/reports/cascade_apply_d245_20261128b.json`）"
    "⇒ 候选边层 **v17 → v18 / 推荐 104 → 128（+24）**、候选交换层观察 **v20 → v21**、闭环 **v72 → v73 / 36 → 48 条 / 跳 108 → 144**；"
    "**USDG 回到闭环目录（12 条 active）**，两条净正三角形（`NVDA→MU→USDG→NVDA`、`NVDA→AMD→USDG→NVDA`）已在目录内；"
    "剩余 9 条 USDG 跳过记录变成真实市场事实（`no_reachable_v4_pool` / `no_completing_middle_direction`）⇒ **USDG 缺口已收口**。"
    "**② ★ 本轮新发现两个系统缺陷**：**（a）写锁占用** —— manager 写接口整片 `503 database is locked`（每层等满 60 s busy timeout），"
    "探针读数：旧运行时在跑 **15/15** 采样全锁、停运行时后 **0/5**、新运行时起来后 **65/90（72%）** ⇒ **持锁者是运行时**（链上测量期间开着写事务）"
    "⇒ 自愈写入在运行时忙时形同虚设；本轮靠「停运行时腾锁」落库后立即重启，**根治未做**。"
    "**（b）候选交换作用域方向层陈旧**（v50 @ `2026-11-27T15:07:56Z` < 作用域 `22:16:02`）⇒ 观察视图按 `scope_key` 关联不到有效排名 ⇒ `optimal_pool_id` 取空 ⇒ 闭环层以 `POOL_INVALID` 拒绝发布（实测 422）⇒ **已重建 v50 → v51**。"
    "**③ 代码**：`M-8` `MANAGER_REBUILD_CHAIN` 扩为**五层**（链条项改为 `(name, path, scope_key)`，新增 `directionalPools(candidate_exchange)`）＋ 新回归 `test_the_cascade_rebuilds_the_direction_layer_of_the_observation_scope`；"
    "级联工具 `plan/runtime_evidence/tools/apply_cascade_in_dependency_order_20261128.py` 同步改为 5 层。**④ 回归**：healer **138 / 0**（基线 **137** ＋ 新 **1**）。"
    "**⑤ ★ 剩余卡点（运行时侧，不代运行时关闭）**：管理器 **48** 条 active 闭环里 **21 条没有常驻 worker**（含**全部 12 条 USDG 闭环**），"
    "因为运行时的闭环目录从**它自己的表**构建（`v4_closed_loops` **32** 行 / `updated_at 2026-11-27T00:39:52Z`、`dex_candidate_pool_recommendations` **76** 行、`market_observation_yz_pairs` **142** 行，均落后于管理器），"
    "而 worker 按「运行时自己发现的路线顺序」注册 ⇒ `manual-review` 答 **404** ⇒ 自愈器已按「识别 404 → 刷新该路线注册 worker → 重试」推进；"
    "运行时侧全量同步口 `POST /api/dex-pools/candidates/refresh?full=1&dry_run=false` 本轮实测 **403**（候选边层写入被运行时侧权限拒绝）⇒ 登记为开放项。"
    "**⑥ 诚实边界**：**未伪造任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**、**未放宽任何门禁**；广播 attempts 仍 **0** ⇒「**持续产生链上广播交易**」**仍未达成**；"
    "代次 73 已复核 **21** 条（`missingValue 0` / `positive 0`），**27** 条待复核 ⇒ 盈亏由链上事实决定，**不预判**。"
    "**⑦ 台账**：二级 **76 → 75**、总账 **135 → 134**（`recount_remaining_tasks_20261125.py` 实测；`T-池编.02` unchecked **19 → 18**）；"
    "文档：`D-245` §7.4–§7.7 ＋ `D-241` I2.3 / I2.5 / I2.8 勾选、I2.7 新增 |"
)


LEDGER_HEAD = (
    "> ② **台账订正 ＋ 11 个已闭合任务归档** —— 总读数 **101 → 114 → 116 → 119 → 123 → 127 → 135**（二级 **57** ＋ **60** ＋ **64** ＋ **68** ＋ **76** ＋ 一级 **8** ＋ 横切 **51**），"
)

LEDGER_HEAD_NEW = (
    "> ② **台账订正 ＋ 11 个已闭合任务归档** —— 总读数 **101 → 114 → 116 → 119 → 123 → 127 → 135 → 134**（二级 **57** ＋ **60** ＋ **64** ＋ **68** ＋ **76** ＋ **75** ＋ 一级 **8** ＋ 横切 **51**），"
)


LEDGER_TOTAL = "⇒ 二级 **68 → 76**、总账 **127 → 135**；"
LEDGER_TOTAL_NEW = (
    "⇒ 二级 **68 → 76**、总账 **127 → 135**；"
    "**2026-11-28 第六轮**（`D-245` 生产修复已落库 ＋ 重建链扩为五层）—— `I2.3`（生产数据修复：**USDG 已回到闭环目录 48 → 12 条 active**）、"
    "`I2.5`（同源第二面：**候选交换作用域方向层已重建 v50 → v51**）、`I2.8`（重建链扩为五层：`M-8` 已落地并有回归）**按实测改写后勾选**；"
    "**新增 `I2.7`**（★ 写锁占用：运行时约 **72%** 的时间占着 SQLite 写锁 ⇒ 自愈写入在忙时整片 `503`）⇒ 二级 **76 → 75**、总账 **135 → 134**；"
)

LEDGER_ATTRIBUTION = "⇒ 归属任务读数 **68 → 76**、总账 **127 → 135**|"
LEDGER_ATTRIBUTION_NEW = (
    "⇒ 归属任务读数 **68 → 76**、总账 **127 → 135**；"
    "**2026-11-28 第六轮** —— `I2.3` / `I2.5` / `I2.8` **按实测改写后勾选**（**−3**）、**新增 `I2.7`**（**+1**）⇒ 归属任务读数 **76 → 75**、总账 **135 → 134**|"
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: anchor found {count} times, expected exactly once")
    return text.replace(old, new, 1)


def write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry = REGISTRY.read_text(encoding="utf-8")
    anchor = "| 2026-11-28 | **单 tick 全不盈利的成因审查 ＋ USDG 闭环缺口的根因（`T-池编.02` 第五轮"
    index = registry.find(anchor)
    if index < 0:
        raise SystemExit("registry: the fifth round row was not found")
    end = registry.find("\n", index)
    if end < 0:
        end = len(registry)
    registry = registry[:end + 1] + ROW + "\n" + registry[end + 1:]

    ledger = LEDGER.read_text(encoding="utf-8")
    ledger = replace_once(ledger, LEDGER_HEAD, LEDGER_HEAD_NEW, "ledger head")
    ledger = replace_once(ledger, LEDGER_TOTAL, LEDGER_TOTAL_NEW, "ledger total row")
    ledger = replace_once(ledger, LEDGER_ATTRIBUTION, LEDGER_ATTRIBUTION_NEW,
                          "ledger attribution row")

    if args.dry_run:
        print("dry run: the registry row and the ledger head would be written")
        return 0

    write(REGISTRY, registry)
    write(LEDGER, ledger)
    print("registry row and ledger head written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
