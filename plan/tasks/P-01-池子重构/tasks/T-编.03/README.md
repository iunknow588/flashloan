# `T-编.03`｜主网五段级联自愈（代币 → 候选池 → 闭环 → 复核 → 广播）的端到端验收与广播缺口归因

> 任务编号 `T-<索引>.<序>` ／ `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-编.03`（纯 ID）　**验收**：`AC-编.03`　**优先级**：P0
**编号逻辑**：`T-编.03` ／ `AC-编.03` —— **模块内（单模块索引）**，编号索引 **`编`**
（改动模块：`M-8`＝`scripts/mainnet_arbitrage_healer.py`），该索引号段内第 **03** 个。
（详见 `D-229-PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-8`

**前置模块**：`M-1`、`M-5`、`M-6`、`M-7`、`M-13`　　**需授权**：否

## 文件

| 文件 | 内容 |
|---|---|
| `D-229-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-230-SCOPE_GUARD.md` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-231-checklist.md` | 本任务的逐条验收框（**含诚实边界**） |
| `docs/D-232-主网五段级联实盘读数_20261128.md` | 本轮的**实盘读数**与三处编排缺陷的定位过程 |
| `docs/` | 本任务的**独立技术文案**区 |

## 一句话

> **级联前四层全绿、第五层是市场条件**：
> 代币 **201 / 201**、池 **26 / 26** 深度达标、闭环 **8** 条 **24 / 24** 跳全在作用域、
> 当前目录复核 **8 / 8** 都带明确盈亏 ⇒ **唯一**的阻塞是 **广播层**（**没有一条正收益可执行复核**）。
> 本任务修掉的是**编排层自己的三处缺陷**：崩了说不清、忙了当答了、读不到当空了。

## 用户口径（**本任务的全部依据**）

> 「按照核心库模式加载启动，充分利用主网自愈编排器，完成主网的开发任务。」
>
> 「……从**资金代币、候选代币、闭环**的角度全范围的考察：代币无效 ⇒ 代币管理工具修复；
> 代币有效 ⇒ 检查池子，池子无效 ⇒ 池子工具修复，池子都有效 ⇒ 闭环一定有效；
> 闭环有效 ⇒ 检查复核，复核没有明确盈亏数值 ⇒ 复核管理工具修正，直到每个复核都有明确的盈亏数值；
> 复核有利润 ⇒ 检测自动广播交易，广播出错 ⇒ 广播交易工具修正；
> 重复以上过程，直到主网可以稳定的产生套利广播交易，然后持续观察统计套利交易盈亏情况。」

## 三处编排缺陷（**本次实盘暴露 · 已修**）

| # | 缺陷 | 实盘读数 | 修法 |
|---|---|---|---|
| 1 | 一轮自愈崩了只留**一句**错误 | `healing_cycle_error` = `no such table: dex_candidate_pools`，**无栈** ⇒ 定位靠猜（HEAD 的五处 `dex_candidate_pools` 读**全未护栏**；工作区已加护栏，但**在跑的进程**仍是旧码） | 新增 `cycle_error_event()`：事件**带异常栈** |
| 2 | 管理器**忙**被当成**没有到期** | `advance_review_clocks` 返回 `ok=false status=503` ⇒ 那一轮时钟**没推进** ⇒ 下一轮复核起不来 | 新增 `transient_api_failure()` ／ `api_get_transient()` ／ `api_post_transient()`：**瞬态重试**、被拒绝不重试、`attempts` 上账 |
| 3 | 队列**读不到**被读成**无到期** | `due_review_loops` 失败时返回 `[]` ⇒ 与「确实没有到期」**不可区分** | `due_review_queue()` 报 `ok`；事件里新增 `ok` ／ `dueCount` ／ `waitingCount` |

## 实盘读数（**2026-11-27T19:37:26Z** · 只读体检）

| 层 | 读数 | 判定 |
|---|---|---|
| **L1 代币** | `admitted=201`、`missing=0`、`counterfeit=0` | ✅ |
| **L2 池子** | `uniquePools=26`、`adequate=26`、`insufficient=0`、`thin=0`、`unpriced=0`、`badReading=0`；`bestAmount confirmed=3` | ✅ |
| **L3 闭环** | `loops=8`、`threeHops=8`、`hopsInScope=24/24` | ✅ |
| **L4 复核** | 全窗口 `176` 条（`missing=19`）；**当前目录 8 / 8** 都有明确盈亏、`bestAmountMissing=0`、`positive=0`、`sum=-0.000647` | ✅ 当前目录 |
| **L5 广播** | `attempts = 0` | ⛔ **市场条件** |

**生产态**：`market_no_executable_profit` ／ `healthy = True` ／ `blockers = ["broadcast"]` ／
`safeToAutoBroadcast = False`。

## 独立回归

```powershell
# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

