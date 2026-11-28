"""Round 13: the registry and the ledger follow the walk.

The walk over the review queue stops for one reason only, the deployment
gate of the eleventh round is closed, and the orchestrator runs the code
of this round.  The registry gains a row and the ledger gains the reading
of this round; the numbers are the ones the recount measured.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"
LEDGER = (
    ROOT / "plan" / "tasks" / "P-01-池子重构" / "docs" /
    "D-014-未完成任务清单_20261123.md"
)


REGISTRY_ROW = (
    "| 2026-11-28 | **遍历只有一个终止条件（`T-池编.02` 第十二轮 · `M-8` ）**："
    "**用户口径**「**不一定是读到哨兵，有可能是读取到一个未到点的闭环，"
    "不能使用哨兵作为循环结束的条件。**」另补充：「**两个哨兵的含义是"
    "暂时不需要复核，所以它的复核时间可以认为是非常长。没有其他的特殊含义，"
    "就只是表示暂时不需要复核而已。**」"
    "**① 事实**：`ReviewQueue::walk` 的哨兵分支在前、「时刻未到」分支在后 ⇒ 停点有**两个原因**"
    "（`parked` / `not_due`）⇒ 终止条件不唯一，而且哨兵分支会抢在真正的停点前面。"
    "**② 本轮已做**：① `closed_loop_manager::ReviewQueue::walk` 两个分支合为一个 ⇒ **停点原因只有 `not_due`**"
    "（复核时刻未到），哨兵单独判定「暂时不需要复核」⇒ **永远不到期**，用 "
    "`stopped_at_is_sentinel` **描述**（不是第二个原因）；② `manager-api` 的 `review-due` 新增 `walkStoppedAtIsSentinel`；"
    "③ 自愈器带回该字段并记入日志；④ `i64::MAX` 是队列的**排序标记**（排在最后）而非时钟，"
    "`now` 始终是真实时刻。**③ 回归**：`closed-loop-manager` **79 / 0**、`manager-api` **35 / 0**、`review-manager` **178 / 0**、healer **149 / 0**。"
    "**④ 在产**：release 重建 → 停旧（pid 4544）→ 替换（备份 `.bak-20261128T075207Z`）→ 起新（pid **5428**，**07:52:07Z**）；"
    "自愈器重启为本轮代码（pid **24212**）。**⑤ 在产读数**：`GET /v1/closed-loops/review-due?chainId=4663` ⇒ "
    "`walkStoppedReason=not_due` / `walkStoppedAtIsSentinel=true` / `queueNeedsRefresh=true` / `restartFromHead=true`；"
    "**占用不再改写复核时刻**（07:31 的四条认领保留了原时刻）。"
    "**⑥ 旧哨兵存量**：库里仍有 **4 条**修复前的哨兵行（认领时刻均早于 07:27:06Z）⇒ 由**过期归还**放回（陈旧窗口 45 分钟）。"
    "**⑦ 诚实边界**：未伪造任何盈亏、未填 0、未删历史复核行、未改历史代次、未放宽任何门禁；"
    "广播 attempts 仍 **0** ⇒ 「持续产生链上广播交易」**仍未达成**（**市场条件**）；写锁根治与热路径搬库**均未实施**；"
    "`broadcast_pending` 的同源墓碑**本轮未修**（登记为 `I2.13`）。**⑧ 台账**：二级 **217 → 216**、总账 **276 → 275**"
    "（`recount_remaining_tasks_20261125.py` **实测**）；文档：`D-245` §14 ＋ `D-224` §2.4 ＋ `D-241` `I2.11` / `I2.12` / `I2.12b` |" + chr(10)
)


LEDGER_11_OLD = (
    "**2026-11-28 第十一轮**（`D-245` §13 —— 占用不再改写复核时刻 ＋ 队列动态刷新）—— "
    "新增 `I2.12`（部署闸门：manager-api release 二进制替换 · **未完成**）、`I2.13`（`broadcast_pending` 的同源墓碑 · "
    "**未完成**）⇒ 二级 **215 → 217**、总账 **274 → 276**（`recount_remaining_tasks_20261125.py` **实测**）；"
    "`I2.11`（占用不再改写复核时刻 ＋ 队列动态刷新）**已闭合** ⇒ 不计入未勾选读数 |"
)

LEDGER_11_NEW = LEDGER_11_OLD[:-1] + (
    "；**2026-11-28 第十二轮**（`D-245` §14 —— 遍历只有一个终止条件）—— "
    "`I2.12`（部署闸门）**已闭合**（新件 pid **5428**，07:52:07Z）、`I2.12b`（自愈器重启）**已闭合**"
    "（pid **24212**）⇒ 二级 **217 → 216**、总账 **276 → 275**（`recount_remaining_tasks_20261125.py` **实测**）；"
    "`I2.13`（`broadcast_pending` 同源墓碑）**仍未勾选** |"
)

LEDGER_12_OLD = (
    "**2026-11-28 第十一轮**（`D-245` §13）—— 新增 `I2.12` / `I2.13` 两项**未勾选**（部署闸门 ＋ "
    "`broadcast_pending` 同源墓碑）⇒ 归属任务读数 **215 → 217**、总账 **274 → 276**；"
    "`I2.11` **已闭合** ⇒ 不计入 |"
)

LEDGER_12_NEW = LEDGER_12_OLD[:-1] + (
    "；**2026-11-28 第十二轮**（`D-245` §14）—— `I2.12`（部署闸门）＋ `I2.12b`（自愈器重启）"
    "**均已闭合** ⇒ 归属任务读数 **217 → 216**、总账 **276 → 275**（`recount_remaining_tasks_20261125.py` **实测**）；"
    "`I2.13` **仍未勾选** |"
)


def main() -> int:
    rows = REGISTRY.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(rows):
        if line.startswith("| 2026-11-28 | **复核队列是动态的：占用不再改写复核时刻"):
            rows.insert(index + 1, REGISTRY_ROW)
            break
    else:
        raise SystemExit("the row of the eleventh round was not found")
    REGISTRY.write_text("".join(rows), encoding="utf-8", newline="\n")

    ledger = LEDGER.read_text(encoding="utf-8")
    for old, new in ((LEDGER_11_OLD, LEDGER_11_NEW), (LEDGER_12_OLD, LEDGER_12_NEW)):
        if ledger.count(old) != 1:
            raise SystemExit(f"the anchor is not unique: {old[:60]!r}")
        ledger = ledger.replace(old, new, 1)
    LEDGER.write_text(ledger, encoding="utf-8", newline="\n")
    print("the registry and the ledger of round 13 are written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
