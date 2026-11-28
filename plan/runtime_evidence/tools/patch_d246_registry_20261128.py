#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record the seventh round of the USDG closed-loop investigation.

The registry rows and the ledger rows are single long lines, so the change
is a substring replacement: the anchor has to be unique, and the script
refuses to write when it is not.  ``--dry-run`` prints the result without
touching the files.

Usage::

    python patch_d246_registry_20261128.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "plan" / "D-001-TASK_REGISTRY.md"
LEDGER = REPO / "plan" / "tasks" / "P-01-池子重构" / "docs" / "D-014-未完成任务清单_20261123.md"


REGISTRY_ANCHOR = "| 2026-11-28 | **USDG 闭环缺口的收口 ＋ 重建链扩为五层（`T-池编.02` 第六轮 · `M-8` ＋ `M-1`）**"

REGISTRY_ROW = (
    "| 2026-11-28 | **常驻 worker 覆盖的真实成因与三处修复（`T-池编.02` 第七轮 · `M-8` ＋ `M-10`）**："
    "**用户口径**「**这是严重的问题，请重点考察这个问题发生的原因。**」＋「**请继续执行推进开发计划……"
    "充分利用主网自愈编排器，完成主网的开发任务……**」。"
    "**① ★ 对上一轮归因的更正（实测）**：上一轮把「闭环没有常驻 worker」归因于「运行时的闭环目录从**它自己的表**构建」—— "
    "**该归因不成立**：运行时自己的 `v4_closed_loops`（**32** 行 / `2026-11-27T00:39:35Z`）里 USDG 行 **0**，"
    "运行时却已有**活着的 USDG worker**（`usdg|amd|nvda`）⇒ **worker 注册读的是管理器的闭环视图**"
    "（`dex_opportunity_scanner_rust_mixin.py::_native_manager_closed_loop_route_payload` → `manager_view(\"closed_loops\")` → "
    "`closed_loop_manager::read_closed_loop_snapshot` → `manager_v4_closed_loops` **48** 行），并与扫描载荷**取并集**后注册；"
    "运行时自己那张陈旧表只喂自己的闭环锚点与展示，**不是 worker 覆盖的瓶颈**。"
    "**② 真实成因（三条叠加）**：**提交爬坡**（运行时默认每轮扫描只接纳 **16** 个新常驻组，目录 32 → 48 要三轮）＋ "
    "**一轮扫描 ≈ 269 秒**（实测 **269.373 s**）＋ **后台扫描提交期间手动刷新被拒**（`closed_loop_submission_busy`，`retryable: true`，"
    "自愈器此前**不重试**）⇒ 实测常驻 worker **13** 条 / 已发布 **48** 条。"
    "**③ 三处修复（已落地并投产）**：**（a）读路径不再写库** —— `ensure_runtime_state_table` 被 **25** 处调用、其中至少 **5** 处在纯读路径，"
    "旧实现每次都开 `BEGIN IMMEDIATE` 写事务 ⇒ 读一次状态就写一次库，既抢写锁又在锁被占时让**读取本身**失败"
    "（`closed-loop runtime state sync failed: database is locked`）⇒ 改为**先读后写**（`flashloan/src/dex/runtime/closed_loop_state_machine.py`）；"
    "**（b）去掉提交爬坡** —— 自愈器拉起运行时时设 `DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN=0`（运行时自己文档化的「零等待、一轮接纳全部」开关，"
    "**常驻上限**与**复核并发**仍是上限）；**（c）忙时刷新有界重试** —— 自愈器对 `closed_loop_submission_busy` 重试 **3** 次（10 s / 20 s 退避），其余答复不重试。"
    "**④ 回归**：healer **140 passed / 0**（基线 **138** ＋ 新 **2**）；状态机 **41 passed / 0**。"
    "**⑤ 投产**：停运行时（写锁探针 **0/6** 空闲）→ 停旧自愈器 → 用新代码重启自愈器（pid **27204**），它按新环境拉起运行时（pid **12240**）"
    "⇒ 新自愈器随即对 **USDG 路线**发出刷新（`amd|nvda|usdg`、`mu|nvda|usdg`、`nvda|usdg|mu`、`nvda|usdg|amd`、`usdg|mu|nvda`，全部 HTTP 200）"
    "⇒ **USDG 路线第一次拿到 worker 注册请求**。"
    "**⑥ 诚实边界**：**未伪造任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**、**未放宽任何门禁**；"
    "广播 attempts 仍 **0** ⇒「**持续产生链上广播交易**」**仍未达成**；代次 73 已复核 **23** 条（`missingValue 0` / `positive 0`），**25** 条待复核"
    "（含 **11** 条 USDG 闭环）⇒ 盈亏由链上事实决定，**不预判**；写锁占用（`I2.7`）**未根治**（读路径的写流量已削掉，链上测量本身仍占着锁）。"
    "**⑦ 台账**：未勾选框读数**不变**（二级 **75** ＋ 一级 **8** ＋ 横切 **51** ＝ 总账 **134**；`recount_remaining_tasks_20261125.py` 实测）"
    "—— 本轮新增 `I2.9` / `I4.5` 均为**已闭合**项、`I3.4` 为回归读数 ⇒ 无新增未勾选框、无闭合未勾选框；"
    "文档：`D-245` §7.7 **归因更正** ＋ 新增 §7.8、`D-241` I2.7 / I4.4 改写、**I2.9 / I4.5 / I3.4 新增** |\n"
)

LEDGER_TOTAL_ANCHOR = (
    "；**11** 个已闭合任务归档 ⇒ **归属**由「在盘」变「已归档」，**读数不变**（归档件内对应条目**均已勾选**）"
)

LEDGER_TOTAL_NOTE = (
    "；**2026-11-28 第七轮**（`D-245` §7.8 —— 常驻 worker 覆盖的真实成因与三处修复）—— "
    "新增 `I2.9`（读路径不得写库 · **已闭合**）、`I4.5`（常驻 worker 覆盖修复 · **已闭合**）、`I3.4`（状态机回归 **41 passed / 0**）"
    "⇒ 未勾选框读数**不变**（二级 **75** ＋ 一级 **8** ＋ 横切 **51** ＝ 总账 **134**）"
)

LEDGER_TASK_ANCHOR = "⇒ 归属任务读数 **76 → 75**、总账 **135 → 134**"

LEDGER_TASK_NOTE = (
    "；**2026-11-28 第七轮** —— 新增 `I2.9` / `I4.5` 均为**已闭合**项、`I3.4` 为回归读数，`I2.7` / `I4.4` 改写后**仍未勾选**"
    "⇒ 归属任务读数 **75 → 75**、总账 **134 → 134**（**读数不变**：本轮既没有新增未勾选框，也没有闭合任何未勾选框）"
)


def replace_once(text: str, anchor: str, addition: str, *, path: Path) -> str:
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(f"anchor is not unique in {path.name}: {count} matches")
    return text.replace(anchor, anchor + addition)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    registry_text = REGISTRY.read_text(encoding="utf-8")
    if registry_text.count(REGISTRY_ANCHOR) != 1:
        raise SystemExit("the registry anchor is not unique")
    # The new row is appended to the table the anchor belongs to: the row
    # ends at the end of its own line.
    tail = registry_text[registry_text.index(REGISTRY_ANCHOR):]
    line_end = tail.index("\n")
    registry_position = registry_text.index(REGISTRY_ANCHOR) + line_end
    registry_text = (
        registry_text[:registry_position] + "\n" + REGISTRY_ROW.rstrip("\n")
        + registry_text[registry_position:]
    )

    ledger_text = LEDGER.read_text(encoding="utf-8")
    ledger_text = replace_once(
        ledger_text, LEDGER_TOTAL_ANCHOR, LEDGER_TOTAL_NOTE, path=LEDGER)
    ledger_text = replace_once(
        ledger_text, LEDGER_TASK_ANCHOR, LEDGER_TASK_NOTE, path=LEDGER)

    if args.dry_run:
        print("registry rows:", registry_text.count("| 2026-11-28 | **常驻 worker 覆盖的真实成因与三处修复"))
        print("ledger total note:", ledger_text.count("2026-11-28 第七轮"))
        return 0

    REGISTRY.write_text(registry_text, encoding="utf-8", newline="\n")
    LEDGER.write_text(ledger_text, encoding="utf-8", newline="\n")
    print("registry and ledger updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
