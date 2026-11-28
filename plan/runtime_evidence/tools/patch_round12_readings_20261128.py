"""Round 12: write the readings of this round into the ledger documents.

The two rows of `D-014` §1.1 are single long lines, so they are patched by
substring replacement rather than by a line based patch.
"""

from pathlib import Path


LEDGER = (Path(__file__).resolve().parents[3] / "plan" / "tasks" / "P-01-池子重构"
          / "docs" / "D-014-未完成任务清单_20261123.md")


OLD_TOTAL = ("⇒ **每个框只在一个文件里**，**读数不重复计** |")

NEW_TOTAL = (
    "；**2026-11-28 第十一轮**（`D-245` §13 —— 占用不再改写复核时刻 ＋ 队列动态刷新）"
    "—— 新增 `I2.12`（部署闸门：manager-api release 二进制替换 · **未完成**）、"
    "`I2.13`（`broadcast_pending` 的同源墓碑 · **未完成**）"
    "⇒ 二级 **215 → 217**、总账 **274 → 276**"
    "（`recount_remaining_tasks_20261125.py` **实测**）；"
    "`I2.11`（占用不再改写复核时刻 ＋ 队列动态刷新）**已闭合** ⇒ 不计入未勾选读数 |"
)


OLD_TASKS = ("**二级读数 75 → 215**（新任务包 **140** 个新框）、**总账 134 → 274** |")

NEW_TASKS = (
    "；**2026-11-28 第十一轮**（`D-245` §13）—— 新增 `I2.12` / `I2.13` 两项**未勾选**"
    "（部署闸门 ＋ `broadcast_pending` 同源墓碑）"
    "⇒ 归属任务读数 **215 → 217**、总账 **274 → 276**；"
    "`I2.11` **已闭合** ⇒ 不计入 |"
)


def main() -> int:
    source = LEDGER.read_text(encoding="utf-8")
    for old, new in ((OLD_TOTAL, NEW_TOTAL), (OLD_TASKS, NEW_TASKS)):
        if new in source:
            continue
        if source.count(old) != 1:
            raise SystemExit(f"pattern count = {source.count(old)}: {old[:60]!r}")
        source = source.replace(old, new, 1)
    LEDGER.write_text(source, encoding="utf-8", newline="\n")
    print(f"patched {LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
