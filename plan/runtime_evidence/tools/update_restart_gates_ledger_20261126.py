'''Register the 2026-11-26 14:3x round: the restart made the gates readable.

Eight task rows in the master registry still carried the boundary
statements of the run before the restart ("the library is still on 017",
"the runtime has not been restarted", "the broadcast requests are still
zero").  Each of those rows gets a dated supplement that says what the
store reads now, so the old statement stays visible as history and the
new one is visible as fact.
'''

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / 'plan' / 'D-001-TASK_REGISTRY.md'
P01 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'README.md'


SUPPLEMENTS = {
    'T-闭.02':
        '**补记**（2026-11-26 14:3x）：**apply 已做**（迁移 **018** 于 **2026-11-25T23:43:20.599Z** 应用；'
        '闭环层在 **2026-11-26T06:19:23Z** 于新二进制上重建）⇒ **2** 项部署闸门**已闭合**：'
        '闭环 **174** ／ 跳 **522** **数量不变**；**174/174** 逐条复读 ⇒ **11/11** 个变了值的代币'
        '**全部**在**快照侧**、**0** 个在旧基础池侧、`valueLost = 0` ⇒ **估值源已换成流动性快照**。'
        '本行上方登记的「apply 未做」「runtime 未重启」**已过时**',
    'T-闭.03':
        '**补记**（2026-11-26 14:3x）：**3** 项部署闸门**全部闭合** —— 迁移 **018** **已应用**'
        '（**2026-11-25T23:43:20.599Z**）；新二进制**从未对着 017 跑过**（此后每次扩展构建 **08:09:06** ／ '
        '**12:11:21** ／ **13:59:08** **都在 018 之后**）⇒ **无中间态**；上线后闭环 **522/522** 跳按 '
        '`(directional_scope_key, directional_pool_id, hop_direction)` 关联到方向池表、**缺 0**。'
        '**★ 口径订正**：`D-161` **在仓里没有这份文件**（`docs/` 下只有 `D-160`）⇒ 该缺口**如实登记**，'
        '**不代 `D-161` 关闭**',
    'T-核.04':
        '**补记**（2026-11-26 14:3x）：**④ 实盘复核读数已取** —— `manager_v4_reviews` 判定分布 '
        '`completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**'
        '（旧读数：**174** 条**全** `data_incomplete`）⇒ **状态已离开 `data_incomplete`**；'
        '`MAX(reviewed_at)` = **2026-11-26T06:27:48Z**（**在重启之后**）。**部署闸门 4 / 4 闭合**',
    'T-核.05':
        '**补记**（2026-11-26 14:3x）：**④ 实盘读数已取** —— `chain_business_initialization` ⇒ '
        '`v4_pool_liquidity_worker` `status = ok` ／ `mode = complete` ／ `error = null` ／ '
        '`pool_count = 322` ／ `refreshed_at = 2026-11-26T06:05:10Z` ⇒ **已离开 `error`**；'
        '快照表 **2486** 行、`MAX(liquidity_read_at)` = **2026-11-26T06:29:53Z**（**在前进**）。'
        '**部署闸门 4 / 4 闭合**',
    'T-核.06':
        '**补记**（2026-11-26 14:3x）：**① 复核层发布新一代 —— 已闭合** —— `MAX(reviewed_at)` = '
        '**2026-11-26T06:27:48Z**（旧读数 **2026-11-25T23:07:30Z**）⇒ 新一代**不是空的**'
        '（`completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**）；'
        '复核 → 广播请求的**时差实测 4.4 s** ⇒ 被放行的正收益闭环**会**被发布。'
        '**② 广播记录出现 —— 仍未闭合**（`broadcast_transaction_db` 仍 **0 文件**；归 `M-7`，'
        '**待市场条件**：`NoProfitableRoute` × **133**）',
    'T-广.02':
        '**补记**（2026-11-26 14:3x）：**2** 项部署闸门**已闭合** —— 迁移 **018** **已应用**'
        '（**2026-11-25T23:43:20.599Z**）；新扩展 **18,379,264 B**（**13:59:08**）上线、runtime **26328**'
        '（看护 **26980**）自 **13:59:28** 起跑新二进制 ⇒ **新旧二进制无中间态**。'
        '**剩 4 项** ＝ **1** 项部署闸门（实盘首笔广播落月度库 · **市场条件**）**＋ 3** 项刻意留占位'
        '（跨月聚合 ／ 落盘失败兜底 ／ 广播请求行生命周期）',
    'T-复核.01':
        '**补记**（2026-11-26 14:3x）：**广播请求 > 0 行 —— 已闭合** —— **1** 行：'
        '`request_status = generated`、`signed_pnl_raw = 0xc`（**1.2e-05** USDG）、'
        '`hop_sqrt_price_limits_json` **3 条**（**3/3**）⇒ **闸门放行的形状是对的**。'
        '本行上方登记的「广播请求仍 0 行」**已过时**',
}


def append_supplement(lines: list[str], task: str, text: str) -> None:
    prefix = f'| `{task}` |'
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f'{task}: matched {len(hits)} rows, expected 1')
    index = hits[0]
    row = lines[index].rstrip()
    if not row.endswith('|'):
        raise SystemExit(f'{task}: row does not end with a pipe')
    lines[index] = row[:-1].rstrip() + ' ' + text + ' |'


def append_module_supplement(lines: list[str], prefix: str, text: str) -> None:
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f'{prefix!r}: matched {len(hits)} rows, expected 1')
    index = hits[0]
    row = lines[index].rstrip()
    if not row.endswith('|'):
        raise SystemExit(f'{prefix!r}: row does not end with a pipe')
    lines[index] = row[:-1].rstrip() + ' ' + text + ' |'


def main() -> int:
    lines = REGISTRY.read_text(encoding='utf-8').splitlines()
    for task, text in SUPPLEMENTS.items():
        append_supplement(lines, task, text)

    # `T-核.02` and `T-池复.01` have no row of their own in the task table
    # (they are listed inside their module rows), so their readings go on
    # the module row instead.
    append_module_supplement(
        lines,
        '| **`核`** |',
        '**2026-11-26 14:3x 补记**：`T-核.02` 的「**运行期生效**」框**已闭合**（runtime **26328** 自 **13:59:28** 起跑新二进制；'
        '方向层槽 **692** ＋ 闭环层槽 **199** ⇒ 闭环 **522/522** 跳按方向解析）—— **只登记读数**，'
        '该任务的代码口径归**另一线程**，**本轮不代它关闭**',
    )
    append_module_supplement(
        lines,
        '| **`池复`** |',
        '**2026-11-26 14:3x 补记**：`T-池复.01` 的「`completed` **不再恒为 0**」框**已闭合**'
        '（`review_status = completed` **1** 行 ＝ **1.2e-05** USDG；概览 `reviewCompletedCount = 1`）',
    )

    anchor = None
    for index, line in enumerate(lines):
        if line.startswith('| 2026-11-26 | **广播闸门放开 ＋ 复核证明接线（`T-广.03`'):
            anchor = index
            break
    if anchor is None:
        raise SystemExit('the D-196 migration row was not found')
    lines.insert(anchor + 1,
        '| 2026-11-26 | **重启把部署闸门变成了可读数（8 个任务 · 11 项）**：runtime 换新二进制'
        '（**26328**，看护 **26980**，**13:59:28**）＋ 闭环层在迁移 **018** 之上重建'
        '（**2026-11-26T06:19:23Z**）⇒ 此前只能「等」的部署闸门**现在能读了**。'
        '**闭合 11 项**：`T-闭.02` **2**（闭环 **174** ／ 跳 **522** **数量不变** ＋ **174/174** 逐条复读 ⇒ '
        '**11/11** 个变了值的代币**全部**在**流动性快照侧**、**0** 个在旧基础池侧、`valueLost = 0` ⇒ '
        '**估值源已换成快照**）＋ `T-闭.03` **3**（**018** 已应用 ＋ 新二进制**从未对着 017 跑过** ⇒ **无中间态** ＋ '
        '**522/522** 跳按 `(directional_scope_key, directional_pool_id, hop_direction)` 解析、**缺 0**）＋ '
        '`T-核.02` **1**（方向层槽 **692** ＋ 闭环层槽 **199** ⇒ **已生效**）＋ `T-核.04` **1**'
        '（复核状态**离开** `data_incomplete`：`completed` **1** ／ `not_executable` **135**）＋ `T-核.05` **1**'
        '（`v4_pool_liquidity_worker` `status = ok` ／ `error = null`）＋ `T-核.06` **1**（复核层**发布新一代**，'
        '`MAX(reviewed_at)` = **06:27:48Z**）＋ `T-池复.01` **1**（`completed` **不再恒为 0**）＋ '
        '`T-复核.01` **1**（广播请求 **1** 行、限值 **3/3**）⇒ **未勾选框读数 103 → 95**。'
        '**诚实边界**：实盘首笔广播**仍未落库** —— 唯一那条正收益请求发布于 **13:56:22**、**在换二进制之前** ⇒ '
        '没人读回且已过 **120 s** 窗口 ⇒ **不会重播**；新一代复核 **133/136** 条 `not_executable` 的内层错误是 '
        '`NoProfitableRoute`（**链上合约自己回退**，此刻市场不赚钱）⇒ **待市场条件**。'
        '**★ 文档缺口（如实登记）**：`D-161` **在仓里没有这份文件**（`docs/` 下只有 `D-160`）⇒ **不代它关闭**。'
        '文档：本轮各任务的 `checklist.md` ＋ `D-196` §5.1 |')
    REGISTRY.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    # ----------------------------------------------------------- P-01 index
    lines = P01.read_text(encoding='utf-8').splitlines()
    anchor = None
    for index, line in enumerate(lines):
        if line.startswith('> **本轮（2026-11-26 14:00 · 广播闸门放开 ＋ 复核证明接线）**：'):
            anchor = index
            break
    if anchor is None:
        raise SystemExit('the D-196 round note was not found in the P-01 index')
    lines[anchor:anchor] = [
        '> **本轮（2026-11-26 14:3x · 重启把部署闸门变成了可读数 · 11 项闭合）**：',
        '> ① **触发**：runtime 换新二进制（**26328**／看护 **26980**，**13:59:28**）＋ 闭环层在迁移 **018** 之上重建'
        '（**06:19:23Z**）⇒ 此前只能「等」的部署闸门**现在能读了**；',
        '> ② **闭合 11 项**：`T-闭.02` **2**（闭环 **174** ／ 跳 **522** **数量不变** ＋ **174/174** 逐条复读）＋ '
        '`T-闭.03` **3**（**018** 已应用 ＋ 新二进制**从未对着 017 跑过** ＋ **522/522** 跳按方向解析）＋ '
        '`T-核.02` **1** ＋ `T-核.04` **1**（复核状态**离开** `data_incomplete`）＋ `T-核.05` **1** ＋ '
        '`T-核.06` **1**（复核层**发布新一代**）＋ `T-池复.01` **1**（`completed` **不再恒为 0**）＋ '
        '`T-复核.01` **1**（广播请求 **1** 行、限值 **3/3**）⇒ **未勾选框读数 103 → 95**；',
        '> ③ **★ 估值源已换成的硬证据**：用**当前**快照重算 A/B ⇒ 实盘值 **11/11** 个变了值的代币**全部**在'
        '**快照侧**、**0** 个在旧基础池侧、`valueLost = 0`（**没有闭环丢了估值**）；',
        '> ④ **广播侧**：广播**工作线程**活着（`v4_broadcast_execution_worker` `status = ok` ／ '
        '`mode = execution_watch_ok`）⇒ 队列**有人消费**；复核 → 广播请求的**时差实测 4.4 s** ⇒ '
        '**远在 120 s 窗口内**；已发布的请求**限值 3/3** ⇒ **闸门放行的形状是对的**；',
        '> ⑤ **诚实边界**：实盘首笔广播**仍未落库** —— 唯一那条正收益请求发布于 **13:56:22**、**在换二进制之前** ⇒ '
        '没人读回且已过 **120 s** 窗口 ⇒ **不会重播**（**时序**，**非**故障）；新一代复核 **133/136** 条 '
        '`not_executable` 的内层错误是 `NoProfitableRoute`（**链上套利合约自己回退**）⇒ **待市场条件**；',
        '> ⑥ **★ 文档缺口（如实登记）**：`D-161` **在仓里没有这份文件**（`docs/` 下只有 `D-160`）⇒ **不代它关闭**；',
        '>',
    ]
    P01.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print('restart gate ledger written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
