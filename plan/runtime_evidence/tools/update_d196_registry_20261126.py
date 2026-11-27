'''Register the D-196 round (broadcast gate opened, review proof wired).

Touches only the four documents that carry the registry of tasks and docs:
the master registry, the P-01 index, the P-01 doc index, and D-179 (which
gets a pointer to its own supplement).  Every edit is anchored on a unique
prefix so a drifting line fails loudly instead of writing a second copy.
'''

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read(path: Path) -> list[str]:
    return path.read_text(encoding='utf-8').splitlines()


def write(path: Path, lines: list[str]) -> None:
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def find(lines: list[str], prefix: str) -> int:
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f'anchor {prefix!r} matched {len(hits)} lines, expected 1')
    return hits[0]


def sub_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f'fragment {old!r} matched {text.count(old)} times, expected 1')
    return text.replace(old, new)


def main() -> int:
    # --------------------------------------------------------------- D-001
    registry = ROOT / 'plan' / 'D-001-TASK_REGISTRY.md'
    lines = read(registry)

    at = find(lines, '| `D-195` |')
    lines.insert(at + 1, '| `D-196` | 广播闸门放开与复核证明接线_20261126 | P-01 T-广.03 | '
                 '`plan/tasks/P-01-池子重构/tasks/T-广.03/docs/'
                 'D-196-广播闸门放开与复核证明接线_20261126.md` |')

    at = find(lines, '| **`广`** |')
    lines[at] = sub_once(
        lines[at],
        '**`T-广.03`**（代码已闭合 · **部署闸门待办**）',
        '**`T-广.03`**（**第二轮**代码已闭合 · 部署闸门 **2 / 4** 已闭合）',
    )

    at = find(lines, '| `T-广.03` | 广播链路 Rust 归位 |')
    lines[at] = sub_once(
        lines[at],
        '**代码项全部闭合，剩 4 项部署闸门未做**',
        '**第一轮代码项全部闭合；第二轮（`D-196`）再闭合「复核证明接线」与「经济性闸门只记录不否决」'
        '，部署闸门 **2 / 4** 已闭合**',
    )
    lines[at] = sub_once(
        lines[at],
        '（`D-176` / `D-177` / `D-178` / `D-179`） |',
        '（`D-176` / `D-177` / `D-178` / `D-179` / **`D-196`**） |',
    )

    at = find(lines, '| 2026-11-26 | **基础池内化：方向池是唯一的池对象（`T-闭.03`')
    lines.insert(at + 1,
        '| 2026-11-26 | **广播闸门放开 ＋ 复核证明接线（`T-广.03` · `M-13`）**：'
        '用户口径「**这个可以把它放开了，只要复核有正收益，就应该广播测试了**」⇒ '
        '授权闸门**本来就开着**（`broadcast_policy.rs::read_switch` **缺省即开**；实盘链级授权已 true ＋ 私钥已配置），'
        '概览 `summary.broadcastEnabled = false` 是**结果**（广播库从未发布状态行）**不是闸门**。'
        '**真正的闸门是价格限值恒 0/3**：`hop_price_limit_gate_blocks_broadcast` 要求载荷正好 **3** 条 '
        '`hopSqrtPriceLimits`，**内核原生载荷从不写它**，三条限值只存在于**复核**的落库结果 ⇒ '
        '`should_route_to_execution_queue` **恒为 false** ⇒ **队列永远收不到任务，一笔也发不出去**。'
        '**复核侧实测**：174 行复核里 `runArbitrage.ok = true` **7** 条（正收益 `1.2e-05` / `4.5e-07` / '
        '`2.6e-07` / `7.5e-09` USDG），其中 **1** 条被复核放行并发布成广播请求'
        '（`request_status = generated`、`signed_pnl_raw = 0xc`、`created_at = 2026-11-26T05:56:22Z`）'
        '⇒ **缺的只是广播侧把这份证明接回载荷**。'
        '**落地**：`review_manager::read_broadcast_requests_for_chain`（**只读**回读）＋ '
        '`worker_pool.rs::wake_broadcast_with_review_proof`（读回证明 → 逐条校验 → 写回载荷 → 入队）＋ '
        '**调用顺序倒置**（先发布复核、后唤醒广播）＋ `review_authorized_broadcast`（**唯一**判据）＋ '
        '`economic_gates_allow` ＋ `raw_profit_is_positive` / `signed_pnl_is_positive`（**精确原始值**定符号）＋ '
        '时效 **120 s** / 未来容差 **30 s**。'
        '**七条裁定**：授权默认放开／准入＝复核的正收益裁定／**经济性闸门只记录不否决**'
        '（`broadcastEconomicOverride`）／**正确性闸门永不放宽**（三条限值必须齐全）／'
        '**证明从库里读回，不二次推导**（运行时绝不自己再算一遍）／时效窗口与复核侧同口径／**没有降级**。'
        '**回归**：`flashloan-rs` **258 passed / 0 failed**（基线 **251** ＋ 新 **7**）；'
        '`flashloan/tools` 工作区**全绿 · 0 failed**；`cargo check --offline --all-targets` **0 error / 0 warning**。'
        '**部署闸门 2 / 4 已闭合**：迁移 **018** 已应用（**2026-11-25T23:43:20.599Z**）＋ '
        '旧 DLL 备份（`plan/runtime_evidence/backups/flashloan_rs_pre_D196_20261126T1400.dll`）、'
        '新 DLL（**18,379,264 B** / **13:59:08**）上线、runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制 '
        '⇒ **新旧二进制无中间态**。'
        '**剩 2 项**（`broadcastAdmission.broadcastEnabled = true` ＋ 首笔真实广播落月度库）**待市场条件**：'
        '复核在 **13:56:22** 发布的那一条**发生在重启之前**，已过 **120 s** 窗口；且复核**自身的正确性门禁**仍在挡'
        '（`tick_range_changed` / `expected_amount_crosses_tick` / `final_input_exceeds_single_tick_capacity`）⇒ '
        '本轮**未改复核的判断口径**（属 `T-复核.01` / `M-4` / `M-7`）。'
        '文档：`plan/tasks/P-01-池子重构/tasks/T-广.03/`（`D-176` / `D-177` / `D-178` / `D-179` / **`D-196`**） |')

    write(registry, lines)

    # ----------------------------------------------------------- P-01 index
    p01 = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'README.md'
    lines = read(p01)

    at = find(lines, '| [`T-广.03`](./tasks/T-广.03/) |')
    lines[at] = sub_once(
        lines[at],
        '**代码项已闭合**：',
        '**代码项已闭合（两轮）**：',
    )
    lines[at] = sub_once(
        lines[at],
        '**回归** `flashloan-rs` **241 passed / 0 failed**（基线 **230** ＋ 新 **11**）；旁证 `broadcast-manager` **40** ／ '
        '`manager-api` **33**（全 **0 failed**）。'
        '**剩 8 项 ＝ 4 项部署闸门**（迁移 **018** ＋ 新扩展/新二进制同批上线 ＋ 上线后 `broadcastEnabled` 变 `true` ＋ '
        '实盘首笔真实广播落月度库）**＋ 4 项刻意留占位**',
        '**第二轮（`D-196`，触发口径「**只要复核有正收益，就应该广播测试了**」）**：概览 `summary.broadcastEnabled = false` '
        '是**结果**（广播库从未发布状态行）**不是闸门** —— 真授权在 `broadcast_policy.rs`（**缺省即开**），'
        '实盘链级授权已 true ＋ 私钥已配置 ⇒ **闸门本来就开着**；**真正卡死的是价格限值恒 0/3**'
        '（内核载荷从不写 `hopSqrtPriceLimits`，三条限值只在复核落库里）⇒ `should_route_to_execution_queue` '
        '**恒为 false** ⇒ **一笔也发不出去**；**复核实测**：**1** 条正收益请求已发布'
        '（`request_status = generated`、`signed_pnl_raw = 0xc`）⇒ **缺的只是广播侧把这份证明接回载荷**。'
        '**落地**：`read_broadcast_requests_for_chain`（只读回读）＋ `wake_broadcast_with_review_proof`'
        '（读回证明 → 校验 → 写回载荷 → 入队）＋ **调用顺序倒置** ＋ `review_authorized_broadcast` ＋ '
        '`economic_gates_allow`（**已授权 ⇒ 放行**）＋ `raw_profit_is_positive` / `signed_pnl_is_positive`；'
        '**七条裁定**见 `D-196` §2。'
        '**回归** `flashloan-rs` **258 passed / 0 failed**（基线 **251** ＋ 新 **7**）、`flashloan/tools` 工作区**全绿**、'
        '`cargo check --all-targets` **0 warning**。'
        '**部署闸门 2 / 4 已闭合**（迁移 **018** 已应用 ＋ 新扩展/新二进制**同批**上线，runtime **26328** 自 **13:59:28** 起跑新二进制 '
        '⇒ **无中间态**）⇒ **剩 2 项**（`broadcastAdmission.broadcastEnabled = true` ＋ 首笔真实广播落月度库）**待市场条件**'
        '**＋ 4 项刻意留占位**',
    )

    at = find(lines, '> **本轮（2026-11-26 12:20 · 复核发布按闭环独立化 ＋ 陈旧闭环显式报错）**：')
    lines[at:at] = [
        '> **本轮（2026-11-26 14:00 · 广播闸门放开 ＋ 复核证明接线）**：',
        '> ① **触发口径**：「**这个可以把它放开了，只要复核有正收益，就应该广播测试了**」⇒ 本轮把「复核有正收益」'
        '**变成广播的准入条件**，并把这条路从「复核发布」一直接通到「月度库落下记录」；',
        '> ② **定位（实测 · 只读）**：概览 `summary.broadcastEnabled = false` 是**结果**（广播库从未发布状态行）**不是闸门** —— '
        '真授权在 `broadcast_policy.rs::read_switch`（**缺省即开**），实盘链级授权已 true ＋ 私钥已配置 ⇒ **闸门本来就开着**；'
        '**真正卡死的是价格限值闸门恒为 0/3**（`hopSqrtPriceLimits` 只在复核落库里、内核载荷从不写）⇒ '
        '`should_route_to_execution_queue` **恒为 false** ⇒ **一笔也发不出去**；',
        '> ③ **复核侧读数**：174 行复核里 `runArbitrage.ok = true` **7** 条（正收益 `1.2e-05` / `4.5e-07` / `2.6e-07` / '
        '`7.5e-09` USDG），其中 **1** 条被复核放行并发布成广播请求（`request_status = generated`、'
        '`signed_pnl_raw = 0xc`、`created_at = 2026-11-26T05:56:22Z`）⇒ **缺的只是广播侧把这份证明接回载荷**；',
        '> ④ **落地**：`review_manager::read_broadcast_requests_for_chain`（**只读**回读）＋ '
        '`worker_pool.rs::wake_broadcast_with_review_proof`（读回证明 → 逐条校验 → 写回载荷 → 入队）＋ '
        '**调用顺序倒置**（先发布复核、后唤醒广播）＋ `review_authorized_broadcast`（**唯一**判据）＋ '
        '`economic_gates_allow`（已授权 ⇒ 放行；否则照旧跑 gas/容量/经济下限）＋ '
        '`raw_profit_is_positive` / `signed_pnl_is_positive`（**精确原始值**定符号）；',
        '> ⑤ **七条裁定**（`D-196` §2）：授权默认放开／准入＝复核的正收益裁定／**经济性闸门只记录不否决**／'
        '**正确性闸门永不放宽**（三条限值必须齐全）／**证明从库里读回，不二次推导**／时效 **120 s** ／ **没有降级**；',
        '> ⑥ **回归**：`flashloan-rs` **258 passed / 0 failed**（基线 **251** ＋ 新 **7**）；'
        '`flashloan/tools` 工作区**全绿 · 0 failed**；`cargo check --offline --all-targets` **0 error / 0 warning**；',
        '> ⑦ **部署闸门 2 / 4 已闭合**：迁移 **018**（**2026-11-25T23:43:20.599Z** 已应用）＋ 旧 DLL 备份、'
        '新 DLL（**18,379,264 B** / **13:59:08**）上线、runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制 ⇒ '
        '**新旧二进制无中间态**（**回滚办法** ＝ 把备份复制回 `flashloan/rs-src/target/release/` 并重启 runtime）；',
        '> ⑧ **诚实边界**：复核**自身的正确性门禁仍在**（`tick_range_changed` / `expected_amount_crosses_tick` / '
        '`final_input_exceeds_single_tick_capacity`）⇒ 正收益闭环被判「不可执行」时**复核不发布请求** ⇒ '
        '本轮**未改复核的判断口径**（属 `T-复核.01` / `M-4` / `M-7`）；**剩 2 项实盘读数待市场条件**'
        '（13:56:22 那条**发生在重启之前**，已过 **120 s** 窗口）。',
        '>',
    ]

    write(p01, lines)

    # ---------------------------------------------------------- P-01 doc index
    doc_index = ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'docs' / 'README.md'
    lines = read(doc_index)
    at = find(lines, '| [`流动性维护槽的价格源依赖_20261126.md`](../tasks/T-核.06/docs/D-191-')
    lines.insert(at + 1,
        '| [`广播闸门放开与复核证明接线_20261126.md`](../tasks/T-广.03/docs/'
        'D-196-广播闸门放开与复核证明接线_20261126.md) | **广播闸门放开与复核证明接线**（`T-广.03`）：'
        '概览 `broadcastEnabled = false` 是**结果**不是闸门 ＋ **真闸门＝价格限值恒 0/3** ＋ '
        '**复核已放出正收益请求**的实测 ＋ **七条裁定** ＋ **经济性闸门只记录不否决**的落地与回归（2026-11-26） |')
    write(doc_index, lines)

    # ------------------------------------------------------------------ D-179
    d179 = (ROOT / 'plan' / 'tasks' / 'P-01-池子重构' / 'tasks' / 'T-广.03' / 'docs'
            / 'D-179-广播准入归位与复核线程不再广播_20261126.md')
    lines = read(d179)
    at = find(lines, '> **实盘写入**：')
    lines[at + 1:at + 1] = [
        '>',
        '> **⚠ 本文已被补充裁定**：**2026-11-26 14:00** 的第二轮（用户口径「**只要复核有正收益，就应该广播测试了**」）'
        '定位到**真正的闸门是价格限值恒 0/3**（本文第 1 节的读数当时只覆盖了「授权」这一层），并据此落地 '
        '**复核证明接线**。见 [`D-196-广播闸门放开与复核证明接线_20261126.md`]'
        '(./D-196-广播闸门放开与复核证明接线_20261126.md)。',
        '> **本文的六条裁定一字未改**；**只有实盘读数已过时** —— 迁移 **018** 已应用（**2026-11-25T23:43:20.599Z**）、'
        'runtime 已于 **13:59:28** 换成新二进制。',
    ]
    write(d179, lines)

    print('D-196 registration written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
