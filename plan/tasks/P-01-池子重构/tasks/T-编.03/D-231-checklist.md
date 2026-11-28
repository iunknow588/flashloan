# D-231 `T-编.03`｜验收清单（**checklist**）

> **【归并 · 2026-11-28】** 本清单的 **2** 项（`E2` 每一个复核都有明确盈亏 ⇒ `T-复储.01`；`E3` 广播后盈亏统计 ⇒ `T-广储.01`）已于 **2026-11-28** **归并**到新任务。
> **执行落点**：`T-复储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **2** 项（`E2` 每一个复核都有明确盈亏 ⇒ `T-复储.01`；`E3` 广播后盈亏统计 ⇒ `T-广储.01`）已于 **2026-11-28** **归并**到新任务。
> **执行落点**：`T-复储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> 验收编号 `AC-编.03`。**每一条都必须带实测读数**。
> 读数取自 **2026-11-27T19:37:26Z** 的只读体检（`--mode check`）与
> `supervisor_20261127T193642Z.jsonl` 的自愈事件。

## A. 加载模式与级联口径

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | 按**核心库**模式加载启动 | `coreScopeConfiguredLibraries = ["core"]`、`candidateScopeConfiguredLibraries = ["core"]`、`candidateScopePolicyMatches = true` | 核心库模式**已生效**（核心库加载模式**已落地**，实盘 `coreScopeConfiguredLibraries = [core]`） | ✅ |
| A2 | 代币无效 ⇒ **代币管理工具**修复 | `healing_actions` 在代币层无效时给出 `resolve_token` / `repair_tokens` | 代码级（代币层**当前有效**，本轮**未触发**） | ✅ |
| A3 | 池子无效 ⇒ **池子工具**修复 | `healing_actions` 在池层无效时给出 `repair_pools` ＋ 对应修复 | 实盘**已触发一次**：样本 **2** 的 `actions=['repair_pools']`（`supervisor_20261127T191757Z.jsonl`） | ✅ |
| A4 | 池子都有效 ⇒ **闭环一定有效** | `hopsInScope = 24/24`、`threeHops = 8` | **不变式成立**（池 `adequate=26/26` → 闭环 **8** 条 · **24** 跳全在作用域内） | ✅ |
| A5 | **全范围**考察：资金代币 → 候选代币 → 闭环 | `cascade_matrix` 三层同时有读数 | `funding valid=5/5 counterfeit=0` → `candidateTokens admitted=201 symbols=201` → `loops total=8 hopsInScope=24/24` | ✅ |

## B. 编排缺陷（三处，**本次实盘暴露**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| B1 | 一轮崩了**说得出在哪** | `healing_cycle_error` 事件带 **异常栈** | 新增 `cycle_error_event()`；单测 `test_a_cycle_that_died_carries_the_stack_that_says_where` | ✅ |
| B2 | 管理器**忙**不等于**答了** | `503` ⇒ **重试**；被**拒绝** ⇒ 不重试 | 新增 `transient_api_failure()` ／ `api_get_transient()` ／ `api_post_transient()`；单测 **2** 条 | ✅ |
| B3 | 实盘 **503** 不再让一轮白跑 | `advance_review_clocks` 走瞬态重试并**上账** `attempts` | 实盘读数 `status=503`（旧码）⇒ 新码重试并报 `attempts` | ✅ |
| B4 | 队列**读不到** ≠ **无到期** | `due_review_loops` 事件带 `ok` | 新增 `ok` ／ `dueCount` ／ `waitingCount`；单测 `test_a_queue_that_could_not_be_read_is_not_read_as_an_empty_queue` | ✅ |
| B5 | 缺表**不中断整轮** | 缺表报为缺口（带 `repairPath`）而非抛错 | **缺表护栏已落地**；新码实盘连跑 **4** 轮（样本 **0–3**）**零** `healing_cycle_error` | ✅ |

## C. 五层级联读数（**2026-11-27T19:37:26Z**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| C1 | **L1 代币**有效 | `layers.tokens.valid` | **true**；`admitted=201`、`missing=0`、`counterfeit=0` | ✅ |
| C2 | **L2 池子**有效 | `layers.pools.valid` | **true**；`adequate=26`、`insufficient=0`、`thin=0`、`unpriced=0`、`badReading=0` | ✅ |
| C3 | **L3 闭环**有效 | `layers.closedLoops.valid` | **true**；`loops=8`、`rebuild=0`、`threeHops=8` | ✅ |
| C4 | **L4 复核**有效（**在产目录**） | `layers.reviews.valid` ／ `currentLoopStats.missingValue` | **true**；当前目录 **8 / 8** 都有明确盈亏、`bestAmountMissing=0` | ✅ |
| C5 | **L5 广播**有效 | `layers.broadcast.valid` | **false**；`attempts = 0` | ⛔ **市场条件**（见 E1） |
| C6 | 生产态**如实** | `productionState` ／ `healthy` | `market_no_executable_profit` ／ **true**；`blockers = ["broadcast"]` | ✅ |

## D. 复核有利润 → 检测广播 → 广播出错则修正（**链路自检**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| D1 | 有正收益复核时**会**检测自动广播 | `observe_automatic_broadcast` 在 `actions` 内 | 代码级：**每轮**都带该动作（样本 **0–3** 全部含 `observe_automatic_broadcast`） | ✅ |
| D2 | 广播出错 ⇒ **广播交易工具**修正 | `repair_broadcast_failures` 在 `actions` 内且**只**在复核层已正收益时触发 | 代码级（**GAP-E**：上游闸门未过 ⇒ **不**修广播） | ✅ |
| D3 | 广播**不会**被凭空放行 | 门禁**未放宽** | `safeToAutoBroadcast = False`（当前无正收益复核 ⇒ **闸门正确不放行**） | ✅ |
| D4 | 实盘首笔**真实广播** | `manager_v4_review_broadcast_requests > 0` | **0 行** | ⛔ **市场条件**（见 E1） |

## E. 回归

| # | 套件 | 读数 | 状态 |
|---|---|---|:---:|
| E0a | `scripts/tests/test_mainnet_arbitrage_healer.py` | **119 passed / 0 failed**（基线 **114** ＋ 新 **5**） | ✅ |
| E0b | `flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py` | **44 passed / 0 failed** | ✅ |
| E0c | 七项台账校验器 | 编号 **715 / 0**、链接 **468 / 0**、目录结构 **PASS ×3**、交叉引用 **0**、池子文档 **0**、分解 **clean**、复算 **114 → 117** | ✅ |

## F. 诚实边界（**未闭合项**）

| # | 项 | 说明 | 归属 | 状态 |
|---|---|---|---|:---:|
| **E1** | **持续产生链上广播交易记录** | 当前目录 **8 / 8** 条复核全部 `not_executable`（`positive = 0`）⇒ **上游没有一条正收益可执行复核** ⇒ 广播请求 **0** 行、执行记录 **0** 行 | **市场条件**（`M-6` ／ `M-7` 的链上事实） | ⬜ |
| **E2** | **每一个**复核都有明确盈亏数值 | 当前目录 **8 / 8** **已达成**；**目录外**（已离开目录的历史闭环）**19** 条缺明确盈亏、**14** 条缺最佳额度 —— 复核管理工具**不再受理**已离开目录的闭环；**实测 0 / 19 可复价**（其跳的池**已从市场目录消失**）⇒ **填数即伪造** | **归 `M-6` ＋ `M-8` ＋ `M-10`**（**退役语义**：给已离开目录的闭环一条明确退役结论，**不删行**、**不填数**、**不碰门禁**）—— 已由 `T-复编储.01` 立项收口 ⇒ **不代为关闭** | ⬜ |
| **E3** | **广播后**的套利盈亏统计 | PnL 快照**每轮**落盘（`pnl_*.jsonl`，含 `expectedPnl` ／ `gasEth` ／ `ethNetAfterGas`）；**广播后**的已实现盈亏**取不到数**（没有广播 ⇒ 没有已实现） | **市场条件**（随 E1） | ⬜ |

## 未闭合项（**`- [ ]` 口径** · 供 `recount_remaining_tasks_20261125.py` 逐目录计数）

- [ ] **E1** **持续产生链上广播交易记录**：当前目录 **8 / 8** 复核全部 `not_executable`（`positive = 0`）⇒ **上游没有一条正收益可执行复核** ⇒ 广播请求 **0** 行、执行记录 **0** 行 ⇒ **未达成**（**市场条件** · **未放宽任何门禁**）
- [ ] **E2** **每一个**复核都有明确的盈亏数值：当前目录 **8 / 8** **已达成**；**目录外**（已离开目录的历史闭环）**19** 条缺明确盈亏、**14** 条缺最佳额度 —— 复核管理工具**不再受理**已离开目录的闭环；**实测 0 / 19 可复价**（其跳的池**已从市场目录消失**）⇒ **填数即伪造** ⇒ **归 `M-6` ＋ `M-8` ＋ `M-10`**（**退役语义**，**不删行**、**不填数**、**不碰门禁**），已由 `T-复编储.01` 立项收口，**不代为关闭**
- [ ] **E3** **广播后**的套利盈亏统计：PnL 快照链**已就位**（每轮落盘），但**广播后**的已实现盈亏**取不到数** ⇒ 随 **E1** 一并待市场条件

