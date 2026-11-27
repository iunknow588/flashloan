# Rewrite the remaining-task ledger of `D-014` with the measured numbers.
#
# The ledger rows of `D-014` are single lines of more than a thousand
# characters, so they are edited by **assertion-guarded replacement** instead
# of by hand: every replacement below must match exactly once, otherwise the
# script refuses to write the file.
#
# The numbers are not typed in: they are recomputed with
# `recount_remaining_tasks_20261125.py`, so the ledger cannot drift away from
# the checklists it claims to measure.
#
# Usage:
#     python sync_d014_ledger_20261126.py

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS = REPO_ROOT / 'plan' / 'runtime_evidence' / 'tools'
D014 = (
    REPO_ROOT
    / 'plan'
    / 'tasks'
    / 'P-01-池子重构'
    / 'docs'
    / 'D-014-未完成任务清单_20261123.md'
)
D001 = REPO_ROOT / 'plan' / 'D-001-TASK_REGISTRY.md'


def recount() -> str:
    result = subprocess.run(
        [sys.executable, str(TOOLS / 'recount_remaining_tasks_20261125.py')],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def measured(output: str) -> dict[str, int]:
    numbers = {}
    for key in ('secondary tasks', 'first level', 'cross cutting', 'TOTAL'):
        match = re.search(rf'{key}\s*=\s*(\d+)', output)
        if match is None:
            raise SystemExit(f'the recount did not report {key!r}')
        numbers[key] = int(match.group(1))
    return numbers


def task_unchecked(output: str, task: str) -> int:
    match = re.search(rf'{re.escape(task)}\s+unchecked=\s*(\d+)', output)
    if match is None:
        raise SystemExit(f'the recount did not report {task!r}')
    return int(match.group(1))


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'the anchor matched {count} times: {old!r}')
    return text.replace(old, new)


def replace_exact(text: str, old: str, new: str, expected: int) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f'the anchor matched {count} times, expected {expected}: {old!r}')
    return text.replace(old, new)


def main() -> int:
    output = recount()
    numbers = measured(output)
    secondary = numbers['secondary tasks']
    total = numbers['TOTAL']
    guang = task_unchecked(output, 'T-广.03')

    text = D014.read_text(encoding='utf-8')

    # The total row of the ledger.
    text = replace_once(
        text,
        '| 实测（**43** ＋ **8** ＋ **51**）',
        f'| 实测（**{secondary}** ＋ **8** ＋ **51**）',
    )
    text = replace_once(text, '→ **97** → **102** |', f'→ **97** → **{total}** |')
    text = replace_once(
        text,
        '⇒ 未勾选框读数 **97 → 102** |',
        f'⇒ 未勾选框读数 **97 → {total}** |',
    )

    # The row that belongs to the concrete tasks.
    text = replace_once(text, '→ **38** → **43** |', f'→ **38** → **{secondary}** |')
    text = replace_once(
        text,
        '⇒ 归属任务读数 **38 → 43**、总账 **97 → 102** |',
        f'⇒ 归属任务读数 **38 → {secondary}**、总账 **97 → {total}** |',
    )

    # The reconciliation line.
    text = replace_once(
        text,
        '二级任务归属 **43** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **102** ✔',
        f'二级任务归属 **{secondary}** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **{total}** ✔',
    )

    # The row of the task this round touched.
    text = replace_once(
        text,
        '| **P0** | `M-13` | **17** |',
        f'| **P0** | `M-13` | **{guang}** |',
    )

    # The per-directory breakdown of the current round.
    text = replace_once(text, '**`T-广.03` 17**', f'**`T-广.03` {guang}**')
    text = replace_once(text, '＝ **43** ✔', f'＝ **{secondary}** ✔')
    text = replace_exact(
        text,
        '`T-广.03` **12 → 17**',
        f'`T-广.03` **12 → {guang}**',
        2,
    )

    D014.write_text(text, encoding='utf-8')

    # The registry records the same reading of the same round.
    registry = D001.read_text(encoding='utf-8')
    registry = replace_once(
        registry,
        '未勾选框读数 **97 → 102**（`T-广.03` **12 → 17** ＝ 新增 **5** 项**实盘部署闸门**）',
        f'未勾选框读数 **97 → {total}**（`T-广.03` **12 → {guang}** ＝ 新增 **5** 项**实盘部署闸门**，其中**部署**一项已闭合）',
    )
    D001.write_text(registry, encoding='utf-8')

    print(f'D-014 and D-001 ledger synced: secondary={secondary} total={total} T-广.03={guang}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
