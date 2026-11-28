"""Round 12: write the migration record of this round into the registry.

`D-001` §7 is a table whose rows are single long lines, so the row is
appended by substring replacement rather than by a line based patch.
"""

from pathlib import Path


REGISTRY = Path(__file__).resolve().parents[3] / "plan" / "D-001-TASK_REGISTRY.md"


ANCHOR = "文档：`D-261`（总览）＋ **5** 个任务包 |"


ROW = (
    "| 2026-11-28 | **复核队列是动态的：占用不再改写复核时刻（`T-池编.02` 第十一轮 · "
    "`M-8` ＋ `M-10`）**：**用户口径**「**这是非常严重的错误，因为队列是动态刷新的，要根据最新的"
    "复核时间进行刷新，每次循环到一个时间未到的闭环（其中也包括哨兵时刻），都会重新刷新一次队列，"
    "其次，每次手动设置复核，也要刷新一次队列，此时，复核主线程都要重新开始复核，而不是停下死等，"
    "因为数据是变化的，必须重新开始，从队列头部开始循环。**」**① 根因（实测 · 只读）**：占用"
    "（`POST /v1/closed-loops/review-cycle`）在 `begin_review_cycle_on_connection` 里把 "
    "`next_review_at` **写成哨兵** `2036-06-06T06:06:00+00:00` ⇒ 把闭环的**最新复核时间**整个顶掉"
    "（干跑实测 `nextReviewAt = 2036-06-06T06:06:00+00:00` / `reviewWaitSeconds = 300495681` ≈ "
    "9.5 年）；而 `advance_review_clocks` 跳过哨兵、`begin_review_cycle` 拒斥哨兵闭环、"
    "`read_due_review_loops` 排除哨兵 ⇒ **只有测量落地才解除** ⇒ **一轮没跑起来的占用就把这条闭环"
    "判死刑**；**② 在产读数（07:03Z）**：48 条闭环 = `cooldown` **43** ＋ `review_pending` **5**"
    "（**5 条全部**停在哨兵；其中 **3** 条占用已过 45 分钟陈旧窗口）；在产二进制的干跑答复**没有 "
    "`runtimeStatus`** ⇒ 占用**无法被识别** ⇒ 第九轮的「过期归还」**当前不生效** ⇒ **修复必须落到"
    "二进制**。**③ 本轮已做（代码）**：`begin_review_cycle_on_connection` **保留原时刻**（缺失或是哨兵"
    "才写 `now`），**忙状态由 `runtime_status` 承担**；`advance_review_clock_on_connection` 只推进 "
    "`cooldown`（**不清掉占用**）；新增 `ReviewQueue::walk` ＋ `ReviewWalk`（**报停点** `not_due` / "
    "`parked`，**忙碌跳过但不停**，`needs_refresh()`）；`manager-api` 的 `review-due` 增 "
    "`walkStoppedAt` / `walkStoppedReason` / `queueNeedsRefresh` / `restartFromHead`，`review-cycle` 的"
    "答复增 `runtimeStatus`；自愈器新增 `walk_review_queue`（**停了 ⇒ 再读一次队列、从队头重来**，"
    "有界 **3** 次）＋ 占用归还**一律按状态**判定；**手动复核**把时刻写成 `now` ⇒ 队列重读后**从队头**"
    "就取得到它。**④ 回归**：`closed-loop-manager` **78 / 0**（基线 78 ＋ 改写 2 ＋ 新 5）、"
    "`manager-api` **35 / 0**（基线 33 ＋ 新 2）、`review-manager` **178 / 0**、healer **147 / 0**"
    "（基线 144 ＋ 新 3）—— 另修掉一个**时间炸弹式**旧用例（硬编码时刻跨过 45 分钟陈旧窗口后变成"
    "「过期占用」）⇒ 改用**相对时刻**。**⑤ 部署闸门（未完成）**：manager-api 的 release 二进制"
    "（pid **4544**）**仍是旧版** ⇒ 修复**未在产**；需要 release 重建 → 停旧 → 替换 → 起新 ⇒ 墓碑由"
    "**自愈路径**放回（**不手工写生产库**）。**⑥ 诚实边界**：**未伪造任何盈亏**、**未填 0**、"
    "**未删任何历史复核行**、**未改任何历史代次**、**未放宽任何门禁**；广播 attempts 仍 **0** ⇒ "
    "「**持续产生链上广播交易**」**仍未达成**（**市场条件**）；写锁根治与热路径搬库**均未实施**；"
    "`broadcast_pending` 的同源墓碑**本轮未修**（登记为 `I2.13`）。**⑦ 台账**：二级 **215 → 217**、"
    "总账 **274 → 276**（`recount_remaining_tasks_20261125.py` **实测**）；文档：`D-245` §13 ＋ "
    "`D-224`（S8–S11 / A18–A22）＋ `D-241` `I2.11` / `I2.12` / `I2.13` |\n"
)


def main() -> int:
    source = REGISTRY.read_text(encoding="utf-8")
    if ROW.strip() in source:
        print("already in place")
        return 0
    if source.count(ANCHOR) != 1:
        raise SystemExit(f"anchor count = {source.count(ANCHOR)}")
    source = source.replace(ANCHOR, ANCHOR + "\n" + ROW, 1)
    REGISTRY.write_text(source, encoding="utf-8", newline="\n")
    print(f"patched {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
