'''Repair the layout damage of the count update.

Two problems are fixed here:
1. the two rows for T-he.06 / T-fuhe.01 were inserted into the section 1.1
   table because the anchor match was too wide (the 1.1 narrative row also
   mentions those task ids) - they belong to the section 1.2 per task table;
2. the rewritten remaining cells lost their padding, so the markdown table
   reads |**4**| instead of | **4** |.
'''

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BT = chr(96)
NL = chr(10)

ROW_KEYS = (BT + 'T-核.06' + BT, BT + 'T-复核.01' + BT)
TASKS = (
    'T-池复.01', 'T-闭.02', 'T-核.02', 'T-闭.03', 'T-广.02',
    'T-核.04', 'T-核.05', 'T-核.06', 'T-复核.01',
)


def task_of(cells: list[str]) -> str:
    for k in (1, 2, 3):
        for name in TASKS:
            if name in cells[k]:
                return name
    return ''


def fix_d014(path: Path) -> None:
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)

    section12 = next(i for i, l in enumerate(lines) if l.startswith('### 1.2'))

    stray = []
    for i, line in enumerate(lines):
        if not line.startswith('|') or i > section12:
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if any(key in cells[2] for key in ROW_KEYS):
            stray.append(i)
    for i in reversed(stray):
        lines.pop(i)

    anchor = None
    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if BT + 'T-核.05' + BT in cells[2]:
            anchor = i
            break
    if anchor is None:
        raise SystemExit('D-014: section 1.2 anchor row for T-核.05 not found')
    for offset in range(len(stray)):
        lines.insert(anchor + 1 + offset, STRAY_ROWS.pop(0))

    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if not task_of(cells):
            continue
        cells[7] = ' ' + cells[7].strip() + ' '
        lines[i] = '|'.join(cells)

    path.write_text(''.join(lines), encoding='utf-8')


def fix_p01(path: Path) -> None:
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = line.split('|')
        if len(cells) < 9:
            continue
        if not task_of(cells):
            continue
        cells[7] = ' ' + cells[7].strip() + ' '
        lines[i] = '|'.join(cells)
    path.write_text(''.join(lines), encoding='utf-8')


STRAY_ROWS: list[str] = []


def main() -> int:
    d014 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'docs' / 'D-014-未完成任务清单_20261123.md'
    p01 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'README.md'

    raw = d014.read_text(encoding='utf-8').splitlines(keepends=True)
    for line in raw:
        if line.startswith('|') and (BT + 'T-核.06' + BT in line.split('|')[2]
                                    or BT + 'T-复核.01' + BT in line.split('|')[2]):
            if line not in STRAY_ROWS:
                STRAY_ROWS.append(line)

    fix_d014(d014)
    fix_p01(p01)
    print('layout repaired')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
