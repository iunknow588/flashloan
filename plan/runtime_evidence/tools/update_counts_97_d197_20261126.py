'''Carry the unchecked-box count from 93 to 97 through the three ledgers.

The D-197 round added four **unchecked** deployment gates to the
``T-广.03`` checklist (the live readouts of the confirmed-pool rules).
The count is therefore 38 + 8 + 0 + 51 = 97, and every published number
is carried with it so that no ledger states a count the recount does not
reproduce.

The recount itself is ``recount_remaining_tasks_20261125.py``; this
script only writes the numbers it printed.
'''

from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
D014 = REPO / 'plan' / 'tasks' / 'P-01-池子重构' / 'docs' / 'D-014-未完成任务清单_20261123.md'
README = REPO / 'plan' / 'tasks' / 'P-01-池子重构' / 'README.md'
REGISTRY = REPO / 'plan' / 'D-001-TASK_REGISTRY.md'


REASON = (
    '**2026-11-26 17:0x** 登记 `D-197`（复核确认池 → 广播用确认池）—— 其 checklist '
    '新增 **4** 项未勾（**4** 项**实盘部署闸门**：复核行确认池 ／ '
    '`expected_pool_state_unavailable` 归零 ／ 灰尘行不再进广播请求 ／ 首笔真实广播）'
    '⇒ 未勾选框读数 **93 → 97**'
)


def patch(path, replacements):
    text = path.read_text(encoding='utf-8')
    for old, new in replacements:
        if old not in text:
            raise SystemExit('not found in ' + str(path) + ': ' + old[:90])
        if text.count(old) != 1:
            raise SystemExit('ambiguous in ' + str(path) + ': ' + old[:90])
        text = text.replace(old, new)
    path.write_text(text, encoding='utf-8')
    print('patched', path)


def main():
    patch(D014, [
        (
            '**102** → **103** → **95** → **93** | 实测（**34** ＋ **8** ＋ **51**）；',
            '**102** → **103** → **95** → **93** → **97** | 实测（**38** ＋ **8** ＋ **51**）；',
        ),
        (
            '⇒ 未勾选框读数 **103 → 95** → **93** |',
            '⇒ 未勾选框读数 **103 → 95** → **93** → **97**；' + REASON + ' |',
        ),
        (
            '→ **44** → **36** → **34** | **19** 个在盘二级任务目录**逐一实测**',
            '→ **44** → **36** → **34** → **38** | **19** 个在盘二级任务目录**逐一实测**',
        ),
        (
            '二级任务归属 **34** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **93** ✔',
            '二级任务归属 **38** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **51** = **97** ✔',
        ),
        (
            '| **31** | **`T-广.03`** | 广播链路 Rust 归位 | `AC-广.03` | **P0** | `M-13` | **8** |',
            '| **31** | **`T-广.03`** | 广播链路 Rust 归位 | `AC-广.03` | **P0** | `M-13` | **12** |',
        ),
    ])
    patch(README, [
        (
            '**合计未完成验收项**：**34**（二级任务 · **逐目录实测**，2026-11-26 14:3x）'
            '＋ **8**（一级特有）＋ **0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **93**',
            '**合计未完成验收项**：**38**（二级任务 · **逐目录实测**，2026-11-26 17:0x）'
            '＋ **8**（一级特有）＋ **0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **97**',
        ),
        (
            '| [`T-广.03`](./tasks/T-广.03/) | 广播链路 Rust 归位 | `AC-广.03` | `M-13` | M-7 | ``W-3`` | **8** |',
            '| [`T-广.03`](./tasks/T-广.03/) | 广播链路 Rust 归位 | `AC-广.03` | `M-13` | M-7 | ``W-3`` | **12** |',
        ),
    ])
    patch(REGISTRY, [
        (
            '| `D-196` | 广播闸门放开与复核证明接线_20261126 | P-01 T-广.03 | '
            '`plan/tasks/P-01-池子重构/tasks/T-广.03/docs/D-196-广播闸门放开与复核证明接线_20261126.md` |',
            '| `D-196` | 广播闸门放开与复核证明接线_20261126 | P-01 T-广.03 | '
            '`plan/tasks/P-01-池子重构/tasks/T-广.03/docs/D-196-广播闸门放开与复核证明接线_20261126.md` |\n'
            '| `D-197` | 复核确认池与广播用池对齐_20261126 | P-01 T-广.03 | '
            '`plan/tasks/P-01-池子重构/tasks/T-广.03/docs/D-197-复核确认池与广播用池对齐_20261126.md` |',
        ),
    ])
    print('counts carried to 97')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
