#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write the ninth round into the registry and the unfinished-task ledger.

The registry keeps one history row per round and the ledger keeps two long
lines with the reading of the unchecked boxes.  Both are single lines that
are too long to patch by hand, so the edit is done here: the row is built as
one string and the two lines are rewritten with their new reading appended.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "plan" / "D-001-TASK_REGISTRY.md"
LEDGER = REPO / "plan" / "tasks" / "P-01-池子重构" / "docs" / "D-014-未完成任务清单_20261123.md"

ROW = (
    "| 2026-11-28 | **手工复核调用流程的合法性审查 ＋ 评审占用归还"
    "（`T-池编.02` 第九轮 · `M-8` ＋ `M-10`）**：**用户口径**「**手工复核怎么会重复调用呢，"
    "确认调用流程的合法性……**」＋「**复核记录，广播记录都是存储在外部文件中 jsonl 文件中……"
    "请分析长时间写锁和这三个存储方案有没有冲突……**」。**① 调用流程合法性**：手工复核**只有一个调用方**"
    "（自愈器），每轮每候选 **1** 次 ＋ worker 注册后 **1** 次重发，累计 **29** 次"
    "（**24×404 / 4×409 / 1×200**）；同一路径 GET 得 **405** ⇒ **路由存在**，404 是运行时的**领域答复**"
    " `closed_loop_group_not_found`（该组无常驻 worker），409 = `closed_loop_group_busy`，"
    "200 = 轮次已启动 ⇒ **调用本身合法、可对账、不是风暴**。**② ★ 真正的缺陷是「占用没有归还」**："
    "占用把闭环**停在哨兵** `2036-06-06T06:06:00+00:00`，而 `advance_review_clocks` 跳过哨兵、"
    "`begin_review_cycle` 拒斥哨兵闭环 ⇒ **只有测量落地才解除** ⇒ 一轮没跑起来的占用就把闭环**判死刑**"
    "（复核停在旧代次、门禁永远读不到它），且**每轮都会再停几条**（实测 **6 → 9 → 4**；"
    "13:19–13:20 一轮停 **4** 条）。**③ 本轮已做**：运行时**明确拒绝**（404 且注册失败）⇒ **立刻归还**；"
    "占用超 **45 分钟** ⇒ **过期归还**；释放前**干跑认领**读回轮次开始时刻做**比较交换**，"
    "别人的轮次一个不动 ⇒ 未放宽任何门禁；healer **144 passed / 0**（基线 **141** ＋ 新 **3**）。"
    "详见 `D-245` §12 |"
)

LEDGER_TOTAL = (
    "；**2026-11-28 第九轮**（`D-245` §12 —— 手工复核调用流程的合法性审查）—— "
    "新增 `I2.10`（评审占用必须能归还 · **已闭合**）、`I3.2` 回归读数更新为 "
    "**144 passed / 0**（基线 141 ＋ 新 3）⇒ 未勾选框读数**不变**"
    "（二级 **75** ＋ 一级 **8** ＋ 横切 **51** ＝ 总账 **134**）"
)

LEDGER_OWNED = (
    "；**2026-11-28 第九轮** —— 新增 `I2.10` 为**已闭合**项、`I3.2` 回归读数更新为 "
    "**144 passed / 0**（基线 141 ＋ 新 3），`I2.7` **仍未勾选** ⇒ 归属任务读数 **75 → 75**、"
    "总账 **134 → 134**（**读数不变**：本轮既没有新增未勾选框，也没有闭合任何未勾选框）"
)


def read(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read().split("\n")


def write(path: Path, lines: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def patch_registry(lines: list[str]) -> list[str]:
    anchor = "| 2026-11-28 | **三套存储方案与长时间写锁的冲突分析"
    for index, line in enumerate(lines):
        if line.startswith(anchor):
            if any(item.startswith("| 2026-11-28 | **手工复核调用流程的合法性审查")
                   for item in lines):
                return lines
            return lines[: index + 1] + [ROW] + lines[index + 1 :]
    raise SystemExit("the eighth round row was not found")


def patch_ledger(lines: list[str]) -> list[str]:
    patched = list(lines)
    for index, line in enumerate(patched):
        if line.startswith("| checklist 中**未勾选**框总数") and "第九轮" not in line:
            patched[index] = line[: line.rfind("|")] + LEDGER_TOTAL + " |"
        elif line.startswith("| ├ **归属于具体任务**的未完成项") and "第九轮" not in line:
            patched[index] = line[: line.rfind("|")] + LEDGER_OWNED + " |"
    return patched


def main() -> int:
    registry = patch_registry(read(REGISTRY))
    write(REGISTRY, registry)
    ledger = patch_ledger(read(LEDGER))
    write(LEDGER, ledger)
    print("registry and ledger patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
