# D-234 `T-复编储.01`｜目录外复核缺值的收口：**先复价，退役降为兜底** · 开发计划（**独立 · 自包含**）

> **本目录是 T-复编储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-复编储.01`** |
| **本任务验收编号** | **`AC-复编储.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`复编储`** ＝ `M-6`（复核层） ＋ `M-8`（编排层） ＋ `M-10`（存储层），按**模块号升序**拼接（`M-6` → `M-8` → `M-10`） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** —— 本索引号段**新开**（`M-6` ＋ `M-8` ＋ `M-10` 的组合此前没有任务） |

**本任务改动三个模块** `M-6`（复核层，退役语义的**唯一产地**）＋ `M-8`（编排层，窗口口径）＋ `M-10`（存储层，**一条迁移**）
⇒ 组合索引 **`复编储`**。下一个可用序号：**`T-复编储.02`**。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 | 落点 |
|---|---|---|---|
| `M-6` | 复核层（复核发布 / 金额利润） | `复` | `flashloan/tools/review_manager/` |
| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` | `scripts/mainnet_arbitrage_healer.py` |
| `M-10` | 存储层（迁移与库结构） | `储` | `flashloan/tools/manager_db/` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-复编储.01`** |
| **验收编号（纯 ID）** | **`AC-复编储.01`** |
| **标题** | 目录外复核缺值的收口：**先复价，退役降为兜底**（**不是**补值）—— 让那 19 条复核行**重新可定价**；只有恢复作用域后**仍不可定价**的行才给「明确退役结论」 |
| **优先级** | **P0**（它是五段级联第 3 步的唯一残项） |
| **波次** | `W-5` |
| **推荐** | **P0 · W-5 · M-6 ＋ M-8 ＋ M-10 · 下一个可用编号 `T-复编储.02`** |

### 1.1 用户口径（**本任务的全部依据**）

> 「按照核心库模式加载启动，充分利用主网自愈编排器，完成主网的开发任务。」
>
> 「……**闭环有效，继续检查复核问题。如果复核没有给出具体的盈亏数值，就利用复核管理工具修正复核问题，直到每个复核都有明确的盈亏数值。**
> ……重复以上过程，直到主网可以稳定的产生套利广播交易，然后持续观察统计套利交易盈亏情况。」

### 1.2 本任务**不**是什么（**边界，先说清楚**）

| 不是 | 为什么 |
|---|---|
| 给缺值的复核**补一个数**（含补 0） | 缺值是**链上事实**：那 19 条路由**当时没被定价**（诊断 `measured_signed_pnl_missing`）。填 0 会把「**没测到**」说成「**测了，恰好不赚**」⇒ **伪造** |
| 把这 19 条**删掉** | 删掉就**抹掉历史**；用户第 5 步要求「**持续观察统计套利交易盈亏**」⇒ 历史必须留 |
| 把池子**重新塞回**作用域去凑复价 | **更正（2026-11-28）**：那 15 个池**全部仍在主池表**（`v4_exchange_pools`，2550 行）⇒ 把它们纳回作用域**不是造市场**，而是**恢复被误收窄的物化范围**；真正的越界是**收窄**（删掉已在作用域里的池） |
| 改**在产目录**的复核口径 | 在产目录 **8 / 8** 已有明确盈亏（`missingValue = 0`）⇒ 口径**正确**，本任务只补**目录外**的收口 |
| 放宽任何门禁去凑广播记录 | 广播缺口是**市场条件**（`productionState = market_no_executable_profit`），与本任务无关 |

---

## 2. 改动清单（**文件级**）

| # | 文件 | 改动 | 口径 |
|---|---|---|---|
| 1 | `flashloan/tools/manager_db/`（迁移） | 新增迁移 `20261128_022_review_retirement_schema`：**放宽** `manager_v4_reviews.review_status` 的 `CHECK` 约束，加入 `retired` | `CHECK (review_status IN ('completed','not_executable','data_incomplete','failed'))` ⇒ **加一个状态就要一条迁移**（现役最新迁移 `20261127_021_review_queue_schema`） |
| 2 | `flashloan/tools/review_manager/src/lib.rs` | 新增退役判定与写入：`stored` 里**不在当前目录**的复核行 ⇒ 写 `review_status = 'retired'` ＋ `review_reason = 'closed_loop_left_catalog:<版本>'`，并**撤下**该闭环的广播请求 | 退役是**明确结论**，不是缺值；**不删行**（历史留） |
| 3 | 同上 | 库状态统计**分离** `retired_count` 与 `data_incomplete_count` | 今天 `data_incomplete_count = 19` 全是**已退役**的 ⇒ 统计在说谎 |
| 4 | `scripts/mainnet_arbitrage_healer.py` | PnL 窗口口径：`missingValue` 与 `retiredReviews` **分开计数**；**在产目录**判据**一字不改** | 「缺值」与「已退役」是两件事；混在一起会让级联 L4 **永远**红 |

---

## 3. 实测读数（**2026-11-28 · 生产库只读** `mode=ro` ＋ `PRAGMA query_only=ON`）

> 探针：`plan/runtime_evidence/tools/probe_departed_review_value_20261128.py`（本任务的实测脚本，留在仓内备查）。

### 3.1 那 19 条到底是什么

| 项 | 读数 |
|---|---|
| 复核行总数 | **176** ＝ `not_executable` **153**（**全有**值）＋ `data_incomplete` **23**（其中 **19** 缺值、**4** **有**值） |
| 缺值行的身份 | **全部** `closed_loop_version = 53`（**当前目录版本 = 69**）⇒ 全部来自**旧一代**目录 |
| 缺值行的原因 | `review_reason = 'signed PnL is missing or incomplete'`；诊断 **`evidence_missing` / `EvidenceMissing`** ×19 ＋ **`measured_signed_pnl_missing`** ×19（原文："the route was not priced, so it carries no measured signed profit"） |
| 身份核对 | `closed_loop_identity_status = 'matched'`、`closed_loop_pool_identity_status = 'verified'`、`identity_mismatch = NULL` ⇒ 它们**当年是合法的**，只是**没被定价** |
| 库状态的谎 | `manager_review_library_state`：`loop_count = 8`、`reviewed_count = 176`、`data_incomplete_count = 23` ⇒ **在产只有 8 条**，而缺值 **19** 条**全在目录外**（另外 **4** 条 `data_incomplete` 是**在产**闭环被陈旧清扫标记的 `not_reviewed_since:<时刻>`，它们**有**值、只是这一轮还没轮到） |

### 3.2 为什么**补不上**（**★ 本节已于 2026-11-28 更正 —— 下面是被推翻的原读数**）

| 判据 | 读数 | 结论 |
|---|---|---|
| 19 条引用的**池 id**（去重） | **28** 个 | — |
| 其中在**当前候选作用域**（`manager_dex_candidate_pools`，**24** 条 core4）里存在的 | **0 / 28** | 这些池**已不在市场目录** |
| 涉及的**池 hash**（去重） | **15** 个 | — |
| 仍在池目录（`v4_exchange_pools_core4` ／ `…_candidate_exchange`）里的 | **6** 个（**3** 个两个目录都有：`0x1dae69…` ／ `0x5fb97a…` ／ `0x71f966…`，带方向行 **4** ／ 观测行 **2**；**3** 个只在 `candidate_exchange`：`0x7b1c44…` ／ `0x928965…` ／ `0xda14bf…`） | — |
| 已从**两个**池目录**都**消失的 | **9** 个（只余 `v4_exchange_pool_liquidity_snapshots` 的**陈旧**快照行） | — |
| **逐条闭环**的跳存活性（3 跳里还有几跳的池在目录里） | **0 / 19** 条是 **3 / 3**；**16** 条是 **1 / 3**；**3** 条是 **0 / 3** | **没有一条**能被重新定价 |

> **★ 原结论（已推翻）**：那 19 条路由**每一跳都至少有一跳的池子已经不在市场目录里** ⇒
> **要复价就必须把已下架的池子重新塞回作用域** ⇒ 那是**造市场**，不是修复核。
>
> **更正**：原探针 `probe_departed_review_value_20261128.py` **只查作用域表**、**从不查主池表** ⇒
> 把「不再被作用域物化」误读成「已从市场目录消失」。实测（`probe_scope_narrowing_20261128.py`）：
> **15 / 15** 个池 hash **全部仍在 `v4_exchange_pools`**，**19 / 19** 条路由的**每一跳**都还在池表。
> **根因是作用域收窄**（2026-11-26 19:29 healer 写入 `core`），**不是**池消失 ⇒ 详见 `docs/D-242`。

### 3.3 系统今天的行为（**已经是对的**）

| 项 | 读数 | 判定 |
|---|---|---|
| 缺值**是否被填 0** | `signed_pnl_amount` 留空 ＋ 写明 `measured_signed_pnl_missing` ⇒ **没有**填 0 | ✅ 正确，**必须保留** |
| 在产目录 **8 / 8** 条复核 | `missingValue = 0`、`bestAmountMissing = 0` ⇒ **每条都有明确盈亏数值** | ✅ 用户第 3 步在**在产范围**已达成 |
| 级联 L4 判据 | 取**在产目录**的 `currentLoopStats` ⇒ 目录外的历史**不驱动修复** | ✅ 口径正确，**一字不改** |
| 唯一说谎的地方 | 库状态把 **19** 条**目录外**行算成「**缺值**」；PnL 窗口也把它们算成 `missingValue = 19` | ❌ **本任务修的就是这个**（**更正**：它们不是「已退役」，是**被作用域收窄孤儿化**的合法路由 ⇒ **先复价**） |

---

## 4. 回归范围

```powershell
# M-6 复核层
cargo test --offline -p review-manager

# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

