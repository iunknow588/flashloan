'''Finalise the unchecked-box count of 93 across the three ledgers.

The restart round closed 13 boxes (11 deployment gates + 2 more in
T-guang.02).  recount_remaining_tasks_20261125.py measures the board as
secondary 34 + first level 8 + cross cutting 51 = 93, so every published
per-task remainder and every aggregate line has to move to that reading.

Markdown backticks are written with chr(96) and newlines with chr(10) so
this file can be patched without any escaping.
'''

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BT = chr(96)
NL = chr(10)

# task id -> (old remaining, new remaining)
REMAINING = {
    'T-池复.01': ('1', '0'),
    'T-闭.02': ('2', '0'),
    'T-核.02': ('1', '0'),
    'T-闭.03': ('3', '0'),
    'T-广.02': ('6', '4'),
    'T-核.04': ('1', '0'),
    'T-核.05': ('1', '0'),
    'T-核.06': ('2', '1'),
    'T-复核.01': ('1', '0'),
}


def sub(path: Path, pairs: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding='utf-8')
    for old, new in pairs:
        n = text.count(old)
        if n == 1:
            text = text.replace(old, new)
            continue
        if n == 0 and text.count(new) == 1:
            continue
        if n != 1:
            raise SystemExit(f'{path.name}: {old!r} matched {n} times, expected 1')
    path.write_text(text, encoding='utf-8')


def set_remaining(path: Path, task_id: str, old: str, new: str) -> None:
    '''Rewrite the remaining cell of the one row that carries task_id.'''
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    hits = 0
    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if not any(task_id in cells[k] for k in (1, 2, 3)):
            continue
        cell = cells[7].strip()
        if cell == '**' + new + '**':
            hits += 1
            continue
        if cell != '**' + old + '**':
            continue
        cells[7] = cell.replace('**' + old + '**', '**' + new + '**')
        lines[i] = '|'.join(cells)
        hits += 1
    if hits != 1:
        raise SystemExit(f'{path.name}: {task_id} remainder cell hit {hits} times, expected 1')
    path.write_text(''.join(lines), encoding='utf-8')


def insert_rows(path: Path, after_task: str, rows: list[str]) -> None:
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    first_key = rows[0].split('|')[2].strip(' *')
    for line in lines:
        if line.startswith('|') and first_key in line:
            return
    at = None
    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if not any(after_task in cells[k] for k in (1, 2, 3)):
            continue
            at = i
            break
    if at is None:
        raise SystemExit(f'{path.name}: anchor row {after_task} not found')
    for offset, row in enumerate(rows):
        lines.insert(at + 1 + offset, row.rstrip(NL) + NL)
    path.write_text(''.join(lines), encoding='utf-8')


def main() -> int:
    d014 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'docs' / 'D-014-未完成任务清单_20261123.md'
    p01 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'README.md'

    # ---- D-014 section 1.1 (aggregate rows) ----
    sub(d014, [
        ('**44** → **36** |', '**44** → **36** → **34** |'),
        ('**103** → **95** | 实测（**36** ＋ **8** ＋ **51**）',
         '**103** → **95** → **93** | 实测（**34** ＋ **8** ＋ **51**）'),
        ('⇒ **11** 项**部署闸门**变成**可读数**并闭合 ⇒ 未勾选框读数 **103 → 95** |',
         '⇒ **13** 项**部署闸门**变成**可读数**并闭合（含 ' + BT + 'T-广.02' + BT
         + ' **2**：迁移 **018** 已应用 ＋ 新扩展与迁移**同批**上线）'
         ' ⇒ 未勾选框读数 **103 → 95** → **93** |'),
        ('⇒ **44 − 8 = 36**',
         '⇒ **44 − 10 = 34**'),
        ('＋ ' + BT + 'T-核.05' + BT + ' **1**（' + BT + 'v4_pool_liquidity_worker' + BT
         + ' ' + BT + 'status = ok' + BT + '）⇒ **44 − 10 = 34**；',
         '＋ ' + BT + 'T-核.05' + BT + ' **1**（' + BT + 'v4_pool_liquidity_worker' + BT
         + ' ' + BT + 'status = ok' + BT + '）＋ ' + BT + 'T-广.02' + BT + ' **2**（迁移 **018** 已应用 ＋'
         ' 新扩展与迁移**同批**上线）⇒ **44 − 10 = 34**；'),
        ('**三者的旧读数已含在上表**（**106** 的实测口径），本轮总账 **103 → 95** |',
         '**三者**（' + BT + 'T-核.06' + BT + ' ／ ' + BT + 'T-池复.01' + BT + ' ／ '
         + BT + 'T-复核.01' + BT + '）的旧读数**已含在上表**（**106** 的实测口径）'
         ' ⇒ **不重复扣减**；本轮总账 **103 → 95** → **93** |'),
    ])

    # ---- D-014 section 1.2 (per task table) ----
    insert_rows(d014, 'T-核.05', [
        '| **34** | **' + BT + 'T-核.06' + BT + '** | 流动性维护槽的价格源依赖 | '
        + BT + 'AC-核.06' + BT + ' | **P0** | ' + BT + 'M-13' + BT + ' | **1** | ⬜ **代码已闭合**'
        '（2026-11-26）：网络修复后复测 ⇒ 链上 RPC **活 2 / 5**，但**外部价格源活 1 / 12**'
        '（**只有 ' + BT + 'api.gateio.ws' + BT + '**）⇒ 槽**不是失败，是卡住**（' + BT + 'py-spy' + BT
        + ' 读到线程停在 ' + BT + 'eth_price_usd()' + BT + '）⇒ **剩 1 项** ＝ 价格源**回退链**的可读数 | **—** |'
        ' 否（只读） | [' + BT + 'T-核.06/' + BT + '](../tasks/T-核.06/) |',
        '| **35** | **' + BT + 'T-复核.01' + BT + '** | 复核发布按闭环独立化与陈旧闭环显式报错 | '
        + BT + 'AC-复核.01' + BT + ' | **P0** | ' + BT + 'M-6' + BT + ' ＋ ' + BT + 'M-13' + BT
        + ' | **0** | ✅ **代码与部署闸门均已闭合**（2026-11-26）：复核记录**按闭环独立**落 JSONL'
        '（**一条闭环的写入不影响其它闭环**）、**陈旧闭环显式报错**（不静默丢弃）、**多轮累加**保留；'
        '实盘读数：广播请求 **1** 行（限值 **3/3**） | ' + BT + 'M-6' + BT
        + ' | 否（只读 ＋ 落盘） | [' + BT + 'T-复核.01/' + BT + '](../tasks/T-复核.01/) |',
    ])

    # ---- D-014 section 1.3 (recomputation) ----
    sub(d014, [
        ('＋ **本轮重启闭合 11 项**', '＋ **本轮重启闭合 13 项**'),
        ('二级任务归属 **36** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **95** ✔',
         '二级任务归属 **34** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **93** ✔'),
        ('> **二级任务归属 36** = **19** 个在盘任务目录逐目录数',
         '> **二级任务归属 34** = **19** 个在盘任务目录逐目录数'),
        (BT + 'T-广.02' + BT + ' **6**', BT + 'T-广.02' + BT + ' **4**'),
        (BT + 'T-复核.01' + BT + ' **0** = **36**）。',
         BT + 'T-复核.01' + BT + ' **0** = **34**）。'),
        ('> **★ 本轮 −11**', '> **★ 本轮 −13**'),
        ('> ' + BT + 'T-池复.01' + BT + ' **1**（' + BT + 'completed' + BT + ' **不再恒为 0**）＋ '
         + BT + 'T-复核.01' + BT + ' **1**（广播请求 **1** 行、限值 **3/3**）。',
         '> ' + BT + 'T-广.02' + BT + ' **2**（迁移 **018** 已应用 ＋ 新扩展与迁移**同批**上线）＋ '
         + BT + 'T-池复.01' + BT + ' **1**（' + BT + 'completed' + BT + ' **不再恒为 0**）＋ '
         + BT + 'T-复核.01' + BT + ' **1**（广播请求 **1** 行、限值 **3/3**）。'),
        ('> **与 §1.2 逐行合计相等（44 = 44）** —— 口径已统一为**唯一口径**',
         '> **与 §1.2 逐行合计相等（34 = 34）** —— 口径已统一为**唯一口径**'),
    ])

    for task_id, (old, new) in REMAINING.items():
        set_remaining(d014, task_id, old, new)

    # ---- P-01 README (task table + aggregate line + round note) ----
    sub(p01, [
        ('> ② **闭合 11 项**：', '> ② **闭合 13 项**：'),
        ('**合计未完成验收项**：**47**（二级任务 · **逐目录实测**，2026-11-26）＋ **8**（一级特有）'
         '＋ **0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **106**',
         '**合计未完成验收项**：**34**（二级任务 · **逐目录实测**，2026-11-26 14:3x）＋ **8**（一级特有）'
         '＋ **0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **93**'),
    ])
    for task_id, (old, new) in REMAINING.items():
        set_remaining(p01, task_id, old, new)

    print('counts carried to 93 in D-014 and P-01')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
