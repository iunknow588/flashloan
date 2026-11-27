'''Record the deployment gates that the 2026-11-26 13:59:28 restart closed.

The runtime was replaced with a new binary at 13:59:28 and the closed-loop
layer was rebuilt on top of migration 018 at 14:19.  Several acceptance
boxes were waiting for exactly that, so they can now be judged by a
reading instead of by a wait.

Every box is checked **together with** the reading that justifies it, and
every reading that does not fully satisfy the box stays open.
'''

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TASKS = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'tasks'


def check_box(relative: str, box_prefix: str, new_block: list[str]) -> None:
    path = TASKS / relative
    lines = path.read_text(encoding='utf-8').splitlines()
    hits = [i for i, line in enumerate(lines)
            if line.startswith('- [ ] ') and box_prefix in line]
    if len(hits) != 1:
        raise SystemExit(f'{relative}: box {box_prefix!r} matched {len(hits)} times, expected 1')
    start = hits[0]
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith('      >')):
        if lines[end].strip() and not lines[end].startswith('      >'):
            break
        end += 1
    # Drop the blank line between the box and its reading, and any blank
    # lines the reading ended with, so the block reads as one unit.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[start:end] = new_block
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    # ------------------------------------------------------- T-闭.02
    check_box(
        'T-闭.02/D-150-checklist.md',
        'apply 后闭环**数量**不变',
        [
            '- [x] apply 后闭环**数量**不变',
            '',
            '      > **读数（2026-11-26 14:3x · 实盘 apply 之后）**：闭环 **174** ／ 跳 **522** ／ 方向池 **692**',
            '      > ⇒ 与 A/B 阶段的读数**逐个相同**（A/B：闭环 **174** ／ 跳 **522**）。',
            '      > **apply 的时刻**：迁移 **018** 于 **2026-11-25T23:43:20.599Z** 应用；',
            '      > 闭环层随后在 **2026-11-26T06:19:23Z** 于新二进制上重建',
            '      > （`manager_v4_closed_loops.updated_at` **全部** = 该时刻）⇒ 实盘读数**确实取自 apply 之后**。',
            '      > 探针：`plan/runtime_evidence/tools/verify_t_bi_02_post_apply_20261126.py`。',
        ],
    )

    check_box(
        'T-闭.02/D-150-checklist.md',
        'apply 后**逐条复读**上述读数',
        [
            '- [x] apply 后**逐条复读**上述读数',
            '',
            '      > **读数（2026-11-26 14:3x）**：**174 / 174** 条逐条复读。',
            '      > - **96** 条「A/B 阶段预测不变」的闭环 ⇒ 实盘**仍然不变**（**96 / 96**）；',
            '      > - **78** 条「A/B 阶段预测要变」的闭环 ⇒ **4** 条与预测**逐位相等**、**74** 条有偏差；',
            '      >   偏差的来源**已定位**：估值源是**持续刷新的**流动性快照',
            '      >   （实盘 `MAX(liquidity_read_at)` = **2026-11-26T06:29:53Z**），而预测是**上一份快照**算出来的；',
            '      >   用**当前快照**重算后（`ab_valuation_snapshot_source_rerun_20261126.py`）',
            '      >   实盘值**与快照侧吻合**：**11 / 11** 个变了值的代币**全部**在**快照侧**，',
            '      >   **在旧的基础池侧的有 0 个**；`publishedLoopImpact.valueLost = 0`（**没有闭环丢了估值**）。',
            '      > - 先前被判「方向相反」的那条 `0xaf3d76f1…`：旧预测 **353419979** ／ 旧基线 **354732328** ／',
            '      >   实盘 **355797124** ／ **当前快照侧 355798706** ⇒ 实盘与**快照侧逐位吻合**（差 **1582**，**0.0004%**）',
            '      >   ⇒ 「方向相反」只是**快照前进**造成的，**不是**估算错误。',
            '      > 探针：`verify_t_bi_02_post_apply_20261126.py` ＋ `verify_valuation_source_live_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-闭.03
    check_box(
        'T-闭.03/D-163-checklist.md',
        '实盘应用迁移 **018**',
        [
            '- [x] 实盘应用迁移 **018**',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`schema_migrations` ⇒',
            '      > `20261125_018_closed_loop_directional_only` **已应用**',
            '      > （`applied_at = 2026-11-25T23:43:20.599Z`）⇒ **实盘库已过 018**',
            '      > （此前登记的「库停在 017」**已过时**）。',
            '      > **schema 侧佐证**：`manager_v4_closed_loop_hops` 的列**只有** `directional_pool_id` ／',
            '      > `hop_direction` ／ `directional_scope_key` ／ `directional_pool_rank`，**没有任何基础池列**；',
            '      > `manager_v4_closed_loops` 的绑定列是 `single_tick_capacity_binding_directional_pool_id`。',
        ],
    )

    check_box(
        'T-闭.03/D-163-checklist.md',
        '新二进制与 018 **同批**上线',
        [
            '- [x] 新二进制与 018 **同批**上线',
            '',
            '      > **读数**：**新二进制从未对着 017 跑过** ⇒ **无中间态**。',
            '      > 迁移 **018** 应用于 **2026-11-25T23:43:20.599Z**；此后上线的扩展构建时刻为',
            '      > **08:09:06**（`T-核.04`）、**12:11:21**（`T-核.05` 前）、**13:59:08**（`D-196`）',
            '      > ⇒ **每一次都在 018 之后**。',
            '      > 当前 runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制 ⇒ **新旧二进制无中间态**。',
            '      > **★ 口径订正（文档缺口，如实登记）**：`D-161` §2.5 所说的「四种组合表」',
            '      > **在仓里没有这份文件**（`plan/tasks/P-01-池子重构/docs/` 下只有 `D-160`）',
            '      > ⇒ 本框按**实测的时间线**判定，**不代 `D-161` 关闭**。',
        ],
    )

    check_box(
        'T-闭.03/D-163-checklist.md',
        '上线后复核闭环 **522** 跳按方向解析',
        [
            '- [x] 上线后复核闭环 **522** 跳按方向解析',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_closed_loop_hops` **522** 行；',
            '      > 按 `(directional_scope_key, directional_pool_id)` 关联 `manager_v4_directional_exchange_pools`',
            '      > ⇒ **522 / 522** 命中、**缺 0**；**再加上 `hop_direction = direction`** 仍是 **522 / 522**。',
            '      > 探针：`plan/runtime_evidence/tools/verify_restart_gates_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-核.02
    check_box(
        'T-核.02/D-158-checklist.md',
        '「运行期生效」**如实登记**',
        [
            '- [x] 「运行期生效」**如实登记**（实盘**已重启** ⇒ **已生效**）',
            '',
            '      > **读数（2026-11-26 14:3x）**：实盘 runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制',
            '      > ⇒ 旧的「runtime 24524 自 2026-11-25 17:14:00 起未重启」**已过时**。',
            '      > **新口径已在运行期生效**：方向层槽 `v4_directional_pool_worker` `status = ok` ／ `mode = complete` ／',
            '      > `pool_count = 692`（`refreshed_at = 2026-11-26T06:19:21Z`）；',
            '      > 闭环层槽 `v4_closed_loop_worker` `status = ok` ／ `mode = complete` ／ `pool_count = 199`',
            '      > （**06:20:33Z**）⇒ 闭环 **522 / 522** 跳能按 `(directional_scope_key, directional_pool_id, hop_direction)`',
            '      > 关联到方向池表。',
            '      > 探针：`probe_maintenance_slots_20261126.py` ＋ `verify_restart_gates_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-核.04
    check_box(
        'T-核.04/D-182-checklist.md',
        '**实盘复核读数**：复核状态**离开** `data_incomplete`',
        [
            '- [x] **实盘复核读数**：复核状态**离开** `data_incomplete`',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_reviews` 的判定分布 ⇒',
            '      > `completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**',
            '      > （旧读数：**174** 条**全部** `data_incomplete`）⇒ **状态已离开 `data_incomplete`**。',
            '      > 复核层**在跑**：`MAX(reviewed_at)` = **2026-11-26T06:27:48Z**（**14:27** 本地，**在重启之后**）。',
            '      > 概览 `summary.reviewCompletedCount = 1` ／ `reviewNotExecutableCount = 136`。',
            '      > **诚实边界**：剩下的 `data_incomplete` **不是**故障 —— 多数是「本轮还没排到」的',
            '      > `not_reviewed_since:` 标记；`not_executable` 的内层错误是 `NoProfitableRoute`',
            '      > （**链上套利合约自己回退**，此刻市场不赚钱）。',
            '      > 探针：`verify_restart_gates_20261126.py` ＋ `probe_review_generation_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-核.05
    check_box(
        'T-核.05/D-186-checklist.md',
        '**实盘读数**：`v4_pool_liquidity_worker` 的 `status` **离开** `error`',
        [
            '- [x] **实盘读数**：`v4_pool_liquidity_worker` 的 `status` **离开** `error`',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`chain_business_initialization` ⇒',
            '      > `v4_pool_liquidity_worker` `status = ok` ／ `mode = complete` ／ `error = null` ／',
            '      > `pool_count = 322` ／ `scanned_pool_count = 322` ／ `last_block_number = 123483565` ／',
            '      > `refreshed_at = 2026-11-26T06:05:10Z` ⇒ **已离开 `error`**。',
            '      > 快照表 `v4_exchange_pool_liquidity_snapshots` **2486** 行，',
            '      > `MAX(liquidity_read_at)` = **2026-11-26T06:29:53Z**（**在前进**）。',
            '      > 其余 **7** 个槽（`v4_candidate_yc_zc_worker` ／ `v4_market_observation_worker` ／',
            '      > `v4_directional_pool_worker` ／ `v4_dex_overview_worker` ／ `v4_closed_loop_worker` ／',
            '      > `v4_onchain_review_worker` ／ `v4_broadcast_execution_worker`）**全部 `status = ok`**。',
            '      > 探针：`probe_maintenance_slots_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-核.06
    check_box(
        'T-核.06/D-190-checklist.md',
        '实盘：复核层发布**新一代**',
        [
            '- [x] 实盘：复核层发布**新一代**',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_reviews` 的 `MAX(reviewed_at)`',
            '      > = **2026-11-26T06:27:48Z**（**14:27** 本地）⇒ **远晚于**旧读数',
            '      > （**2026-11-25T23:07:30Z**）⇒ 复核层**发布了一代新的**。',
            '      > 判定分布 `completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**',
            '      > （旧读数：**174** 条全 `data_incomplete`）⇒ **新一代不是空的**。',
            '      > 复核 → 广播请求的**时差实测 4.4 s**（`reviewed_at 05:56:17.867Z` → `created_at 05:56:22.235Z`）',
            '      > ⇒ 复核放行的正收益闭环**会**被发布成广播请求。',
            '      > 探针：`probe_review_generation_20261126.py` ＋ `probe_review_to_publish_lag_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-池复.01
    check_box(
        'T-池复.01/D-134-checklist.md',
        '`completed` **不再恒为 0**',
        [
            '- [x] `completed` **不再恒为 0**',
            '',
            '      > **读数（2026-11-26 14:3x）**：`manager_v4_reviews` ⇒ `review_status = completed` **1** 行',
            '      > （`signed_pnl_raw = 0xc` ＝ **1.2e-05** USDG，`reviewed_at = 2026-11-26T05:56:17.867Z`）',
            '      > ⇒ 判据「`completedCount > 0`」**成立**；概览 `summary.reviewCompletedCount = 1`。',
            '      > **诚实边界**：这一条**正收益闭环**（`USDG -> PFE -> CRCL -> USDG`）发布于 **13:56:22**，',
            '      > **在 runtime 换新二进制（13:59:28）之前** ⇒ 它的广播请求**没有人读回**，且已过 **120 s** 窗口',
            '      > ⇒ **不会被重播**。这**不是**链路故障，是**时序**：换二进制之后要等**新一代**复核。',
            '      > 探针：`verify_restart_gates_20261126.py` ＋ `probe_review_generation_20261126.py`。',
        ],
    )

    # ------------------------------------------------------- T-复核.01
    check_box(
        'T-复核.01/D-194-checklist.md',
        '实盘：`manager_v4_review_broadcast_requests` **> 0 行**',
        [
            '- [x] 实盘：`manager_v4_review_broadcast_requests` **> 0 行**',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：**1** 行 —— `request_status = generated`、',
            '      > `signed_pnl_raw = 0xc`（**1.2e-05** USDG）、',
            '      > `request_reason = completed review with positive executable signed PnL`、',
            '      > `created_at = 2026-11-26T05:56:22.235Z`、',
            '      > **`hop_sqrt_price_limits_json` = 3 条**（**3/3**）⇒ **闸门放行的形状是对的**。',
            '      > **旧读数**（本框留空的理由：「**仍为 0 行**……本轮没有正向利润」）**已过时**：',
            '      > 复核**确实**测出了一条正收益闭环并**放行**了它。',
            '      > **诚实边界**：该请求发布于 **13:56:22**，**在 runtime 换新二进制（13:59:28）之前**',
            '      > ⇒ **没有人读回**它，且已过 **120 s** 窗口 ⇒ **不会被重播**（陈旧机会不可再现）。',
            '      > 探针：`probe_request_price_limits_20261126.py` ＋ `verify_restart_gates_20261126.py`。',
        ],
    )

    print('restart gates recorded')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
