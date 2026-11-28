#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register the eighth round in the registry, the checklist and the ledger.

Three files carry the round bookkeeping:

* ``plan/D-001-TASK_REGISTRY.md`` -- one history row per round,
* ``plan/tasks/.../D-241-checklist.md`` -- the regression reading of ``I3.2``,
* ``plan/tasks/.../D-014-未完成任务清单_20261123.md`` -- the two ledger rows.

The rows are single very long lines, so every edit is a whole-line rewrite
and the files are written back as UTF-8 without BOM and with LF endings.
"""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "plan" / "D-001-TASK_REGISTRY.md"
CHECKLIST = (
    REPO
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "tasks"
    / "T-池编.02"
    / "D-241-checklist.md"
)
LEDGER = (
    REPO
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "docs"
    / "D-014-未完成任务清单_20261123.md"
)

ROUND_ROW = (
    "| 2026-11-28 | **三套存储方案与长时间写锁的冲突分析 ＋ 写锁重试（`T-池编.02` 第八轮 · `M-8` ＋ `M-10`）**："
    "**用户口径**「**复核记录，广播记录都是存储在外部文件中 jsonl 文件中，按照小时生成新文件，所以是独立的，"
    "内存大对象的存储是在独立的 sqlite 中的，这样三个独立的存储方法。请分析长时间写锁和这三个存储方案有没有冲突……**」。"
    "**① 三套存储落点（代码 ＋ 磁盘实测）**：复核记录 → `flashloan/db/review_record_jsonl/<network>/<chain_id>/<YYYY-MM-DD>/"
    "<loop>_<YYYYMMDD-HH>.jsonl`（**按小时切分的 JSONL**）；广播记录 → `flashloan/db/broadcast_transaction_db/<network>/<chain_id>/`"
    "（**按月的独立 SQLite**，**不是 JSONL**）；大对象 → `flashloan/db/local_store_4663.sqlite3`（**20.2 GB**）；规则原文见 "
    "`flashloan/tools/review_manager/src/records.rs` 文件头（① **JSONL 流不参与任何判定**，主库是当前代次唯一权威；"
    "② **月度库先写、主库行后瘦身**；③ 签名盈亏原样保留）。**② ★ 结论：三套存储不共享写锁，但存在「顺序 ＋ 权威耦合」** —— "
    "JSONL 是纯文件追加（**不被主库锁挡**）、月度库是**另一个 SQLite 文件**（自带锁，**不被主库锁挡**）；真正冲突是：复核的"
    "**权威行先写主库、JSONL 后发布**（`review_manager/src/lib.rs::run_impl` → `commit()` → `publish_review_audit`）⇒ 写锁超时"
    "（等满 60 s busy timeout 仍失败）时**两处都没有记录**（流程在到达 JSONL 之前就中止）⇒「独立的 JSONL 库救不了这条记录」"
    "——它是被主库**喂**的；广播侧：月度库先写 ⇒ **广播记录不丢**，但**广播请求**与判定依据的复核行都在主库 ⇒「只有新鲜明确盈利的"
    "复核才广播」**连评估都做不了**；主库内部两类负载方向相反（级联**整块重写**大对象 vs 每轮每闭环一行的热路径）⇒ 一个文件只有"
    "一个写者 ⇒ **批量重写把热路径饿死**。**③ ★ 写锁实测（第八轮）**：短预算（500 ms）**40 / 40 全锁**；长预算（30 s）一次等 "
    "**33 s** 仍超时、一次等 **7.255 s** 才拿到 ⇒ **长事务**持有；**锁 ＋ 检查点对齐探针**（90 次 / 1 s，新工具 "
    "`probe_write_lock_with_checkpoint_20261128.py`）**89 / 90 锁、0 次检查点**（90 s 内主库文件大小不变、WAL 恒 8.75 MB）⇒ "
    "**排除检查点阻塞**、坐实长事务；采样窗口内**只有运行时在跑**（自愈器只发 `runtime_busy` 体检、无修复周期）⇒ 持锁者是"
    "**运行时自己的扫描**（其扫描自身也报 `database is locked`，`flow=opportunity_scan`）；大对象表很小（`v4_exchange_pools` "
    "**2 550** 行等）⇒ 长事务**不是「行多」**，而是**链上测量被放进了写事务**。**④ 本轮修复（已落地）**：自愈器对管理器"
    "**全部写调用**改用 `api_post_transient`（原只有复核时钟用）：`review-cycle` / `pools/scopes/rebuild` / 级联重建链 / "
    "`reviews/run`（两处）/ `broadcasts/finalize` / `tokens/resolve` ⇒ 5xx / 429 / 传输失败**有界重试 3 次**（2 s / 4 s 退避），"
    "**写锁超时不再被读成「复核没有盈亏数值」**；幂等性：这些调用**按内容幂等**，重试写的是同一代次；回归 **141 passed / 0**"
    "（基线 140 ＋ 新 1）。**⑤ 文档**：`D-245` 新增 §11（三套存储与写锁冲突分析）；`D-241` `I2.7` 按实测改写（仍未勾选）、"
    "`I3.2` 更新为 **141 passed / 0**。**⑥ 诚实边界**：**未伪造任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**、"
    "**未放宽任何门禁**；广播 attempts 仍 **0** ⇒「持续产生链上广播交易」**仍未达成**；**根治未做**（测量移出写事务 / 热路径权威搬出"
    "大对象库 / 大对象级联分块写 —— 见 `D-245` §11.6）；**台账**：未勾选框读数**不变**（二级 **75** ＋ 一级 **8** ＋ 横切 **51** ＝ 总账 "
    "**134**）|"
)

TOTAL_NOTE = (
    "；**2026-11-28 第八轮**（`D-245` §11 —— 三套存储与长时间写锁的冲突分析）—— `I2.7` **按实测改写后仍未勾选**"
    "（写锁 **89/90**、**0** 次检查点 ⇒ 长事务持有；自愈器已对管理器**全部写调用**改为**有界重试**）"
    "⇒ **未勾选框读数不变**（二级 **75** ＋ 一级 **8** ＋ 横切 **51** ＝ 总账 **134**）"
)

ATTRIBUTION_NOTE = (
    "；**2026-11-28 第八轮** —— `I2.7` 改写后**仍未勾选**、`I3.2` 回归读数更新为 **141 passed / 0**（基线 140 ＋ 新 1）"
    "⇒ 归属任务读数 **75 → 75**、总账 **134 → 134**（**读数不变**：本轮既没有新增未勾选框，也没有闭合任何未勾选框）"
)


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_registry() -> None:
    lines = REGISTRY.read_text(encoding="utf-8").split("\n")
    anchor = [index for index, line in enumerate(lines)
              if "常驻 worker 覆盖的真实成因与三处修复" in line]
    if len(anchor) != 1:
        raise SystemExit(f"expected one seventh-round row, found {len(anchor)}")
    index = anchor[0]
    if "第八轮" in lines[index + 1]:
        print("registry already carries the eighth round")
        return
    lines.insert(index + 1, ROUND_ROW)
    write(REGISTRY, "\n".join(lines))
    print(f"inserted the eighth-round row at line {index + 2} of {REGISTRY}")


def patch_checklist() -> None:
    lines = CHECKLIST.read_text(encoding="utf-8").split("\n")
    hits = [index for index, line in enumerate(lines) if line.startswith("- [x] **I3.2**")]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one I3.2 item, found {len(hits)}")
    index = hits[0]
    line = lines[index]
    if "141 passed" in line:
        print("checklist already carries the new regression reading")
        return
    line = line.replace(
        "⇒ **140 passed / 0 failed**（基线 **136** ＋ 新 **4**：重建链顺序回归、观察作用域方向层回归、**爬坡开关回归**、**忙时刷新重试回归**）",
        "⇒ **141 passed / 0 failed**（基线 **140** ＋ 新 **1**：**写锁重试回归** —— 复核物化撞上锁住的库时重投而不是丢弃）",
    )
    lines[index] = line
    write(CHECKLIST, "\n".join(lines))
    print(f"rewrote I3.2 at line {index + 1} of {CHECKLIST}")


def patch_ledger() -> None:
    lines = LEDGER.read_text(encoding="utf-8").split("\n")
    total = [index for index, line in enumerate(lines)
             if "**2026-11-28 第七轮**（`D-245` §7.8" in line]
    attribution = [index for index, line in enumerate(lines)
                   if "**2026-11-28 第七轮** —— 新增 `I2.9` / `I4.5`" in line]
    if len(total) != 1 or len(attribution) != 1:
        raise SystemExit(f"expected one ledger row each, found {len(total)} / {len(attribution)}")
    for index, note in ((total[0], TOTAL_NOTE), (attribution[0], ATTRIBUTION_NOTE)):
        line = lines[index]
        if "第八轮" in line:
            print(f"ledger row {index + 1} already carries the eighth round")
            continue
        marker = line.rstrip()
        if not marker.endswith("|"):
            raise SystemExit(f"ledger row {index + 1} does not end with a pipe")
        head = marker[:-1].rstrip()
        lines[index] = f"{head}{note} |"
    write(LEDGER, "\n".join(lines))
    print(f"appended the eighth-round note to ledger rows {total[0] + 1} and {attribution[0] + 1}")


def main() -> int:
    patch_registry()
    patch_checklist()
    patch_ledger()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
