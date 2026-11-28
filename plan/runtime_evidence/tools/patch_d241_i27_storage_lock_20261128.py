#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rewrite the I2.7 checklist item with the eighth-round write-lock readings.

The item is one very long line of the checklist, so the line is replaced as
a whole: a substring patch over such a line is easy to get wrong, and the
file is written back as UTF-8 without BOM and with LF line endings.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
CHECKLIST = (
    REPO
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "tasks"
    / "T-池编.02"
    / "D-241-checklist.md"
)

NEW_ITEM = (
    "- [ ] **I2.7** **★ 写锁占用（开放项 · 第八轮再次取证，根治仍未做）**：manager 的写接口整片 "
    "`503 database is locked`（每层等满 60 s busy timeout 才失败）⇒ 自愈器在运行时忙时**写不进去**；"
    "第八轮探针读数：短预算（`busy_timeout=500 ms`，40 次）**40 / 40 全锁**；长预算（30 s）一次等 "
    "**33 s** 仍超时、一次等 **7.255 s** 才拿到 ⇒ 锁是**长事务**持有，不是短写风暴；**锁 ＋ 检查点对齐探针**"
    "（90 次 / 1 s，`plan/runtime_evidence/tools/probe_write_lock_with_checkpoint_20261128.py`）"
    "**89 / 90 锁、0 次检查点**（90 s 内主库文件大小不变、WAL 恒为 8.75 MB）⇒ **排除「检查点阻塞」**，"
    "坐实**长事务**；采样窗口内**只有运行时在跑**（自愈器只发 `runtime_busy` 体检、没有修复周期）⇒ "
    "**持锁者是运行时自己的扫描**（其扫描自身也报 `database is locked`，`flow=opportunity_scan`）；"
    "大对象表本身很小（`v4_exchange_pools` **2 550** 行、`dex_candidate_pools` **26**、"
    "`manager_v4_directional_exchange_pools` **464**、`manager_v4_closed_loop_hops` **144**）⇒ 长事务"
    "**不是「行多」造成的**，而是**链上测量被放进了写事务**；**本轮已做**：自愈器对管理器**全部写调用**"
    "改用 `api_post_transient`（5xx / 429 / 传输失败**有界重试 3 次**），锁超时不再被读成「复核没有盈亏数值」；"
    "**根治未做**（测量移出写事务 / 热路径权威搬出大对象库 / 大对象级联分块写 —— 见 `D-245` §11.6）"
)


def main() -> int:
    text = CHECKLIST.read_text(encoding="utf-8")
    lines = text.split("\n")
    hits = [index for index, line in enumerate(lines) if line.startswith("- [ ] **I2.7**")]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one I2.7 item, found {len(hits)}")
    lines[hits[0]] = NEW_ITEM
    CHECKLIST.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"rewrote I2.7 at line {hits[0] + 1} of {CHECKLIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
