"""Round 13: the checklist of the closed-loop task follows the walk.

The walk over the review queue stops for **one** reason only -- the review
time point of the entry it stopped at has not come -- and a loop that is
parked at a marker (it is not to be reviewed for now) is **described**
beside that stop instead of being a stop of its own.  The deployment gate
of the eleventh round is closed: the new binary runs in production, and
the orchestrator runs the code of this round.

The checklist is edited by **line replacement**, because its entries are
one line each and the sentences that carry the rule are long.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKLIST = (
    ROOT / "plan" / "tasks" / "P-01-池子重构" / "tasks" / "T-池编.02" /
    "D-241-checklist.md"
)


OLD_STOP = (
    "③ `ReviewQueue::walk` **报停点**（`not_due` / `parked`），"
)

NEW_STOP = (
    "③ `ReviewQueue::walk` **报停点**（**只有 `not_due` 一个原因**："
    "哨兵（暂时不需要复核，复核时间非常长）就是"
    "「复核时刻未到」的最极端情形，另用 "
    "`walkStoppedAtIsSentinel` **描述**），"
)

OLD_TESTS = (
    "回归：`closed-loop-manager` **78** / `manager-api` **35** / "
    "`review-manager` **178** / healer **147**（见 `D-245` §13）"
)

NEW_TESTS = (
    "回归：`closed-loop-manager` **79** / `manager-api` **35** / "
    "`review-manager` **178** / healer **149**（见 `D-245` §13·§14）"
)

OLD_HEALER_TESTS = (
    "- [x] **I3.2** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` "
    "⇒ **147 passed / 0 failed**（基线 **144** ＋ 新 **3**：队列停点字段透传、"
    "停了要**重读队列从队头重来**、没停就只读一次）；"
)

NEW_HEALER_TESTS = (
    "- [x] **I3.2** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` "
    "⇒ **149 passed / 0 failed**（基线 **147** ＋ 新 **2**：停点只有"
    "**一个原因**（哨兵只是描述）、**停在哨兵上也要重读队列**）；"
)

I2_12 = (
    "- [x] **I2.12** **★ 部署闸门（本轮闭合）**：release 重建 → "
    "停旧（pid 4544）→ 替换（旧件备份 `.bak-20261128T075207Z`）→ "
    "起新（pid **5428**，**07:52:07Z**，SHA256 `C083471B…`）⇒ I2.11 的修复**已在产**："
    "在产验证 `GET /v1/closed-loops/review-due?chainId=4663` ⇒ `walkStoppedReason=not_due` / "
    "`walkStoppedAtIsSentinel=true` / `queueNeedsRefresh=true` / `restartFromHead=true`；"
    "`POST /v1/closed-loops/review-cycle` 干跑答复含 `runtimeStatus`；"
    "**在产证据：占用不再改写复核时刻**（07:31 的四条认领"
    "保留了原时刻 06:53:57 / 07:09:52）；**旧哨兵行仍在哨兵上**（4 条，"
    "认领时刻均早于 07:27:06Z）⇒ 由**过期归还**放回（陈旧窗口 45 分钟）；"
    "**本轮不手工写生产库**\n"
)

I2_12B = (
    "- [x] **I2.12b** **★ 自愈器重启闸门（本轮闭合）**：在产的自愈器"
    "（pid **8124**）仍是**第九轮**代码 ⇒ 它的占用归还靠**哨兵判断**"
    "（`nextReviewAt == REVIEW_PENDING_SENTINEL_AT`）⇒ 在新二进制下**不生效**；"
    "**本轮已重启**：pid **24212**，代码为本轮（占用归还靠**状态**判定），"
    "带的参数与旧进程一致（`--mode supervise --apply --interval 60 "
    "--pnl-interval-seconds 300 --observation-hours 6 --leave-runtime-running`）\n"
)


def main() -> int:
    lines = CHECKLIST.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    seen_gate = False
    for line in lines:
        if line.startswith("- [ ] **I2.12**") or line.startswith("- [x] **I2.12**"):
            out.append(I2_12)
            out.append(I2_12B)
            seen_gate = True
            continue
        line = line.replace(OLD_STOP, NEW_STOP)
        line = line.replace(OLD_TESTS, NEW_TESTS)
        if line.startswith("- [x] **I3.2**"):
            line = NEW_HEALER_TESTS + line[len(OLD_HEALER_TESTS):]
        out.append(line)
    if not seen_gate:
        raise SystemExit("the deployment gate I2.12 was not found")
    CHECKLIST.write_text("".join(out), encoding="utf-8", newline="\n")
    print("the checklist of round 13 is written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
