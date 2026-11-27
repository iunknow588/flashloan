# D-211 `T-池编.01`｜核心库加载模式 · 开发计划（**独立 · 自包含**）

> **本目录是 T-池编.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `D-213-checklist.md` 即 T-池编.01 的全部开发与测试计划。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-池编.01`** |
| **本任务验收编号** | **`AC-池编.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`池编`** ＝ `M-1`（池管理器）＋ `M-8`（编排层），**按模块号升序拼接** |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是组合索引**：本任务改了两个**平级**模块 ——

| 模块 | 名称 | 索引 | 改动落点 |
|---|---|---|---|
| `M-1` | 池管理器（链上池状态 / 深度 / 边界） | `池` | `flashloan/tools/pool_manager/src/scope.rs`、`src/baseline_audit.rs` |
| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` | `scripts/mainnet_arbitrage_healer.py` |

`M-1` 是**被强制牵连**的一方：作用域的加载模式由池管理器拥有，
而编排器**驱动**这次重建并把审计口径（`AUDITED_SCOPE_TOKEN_LOAD_MODE`）
传给池管理器 ⇒ 两者改的是**同一件事**，拆开会让其中一半无处安放。

**本任务涉及的模块（= 回归范围）**：`M-1`、`M-8`。

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-池编.01`** |
| **验收编号（纯 ID）** | **`AC-池编.01`** |
| **标题** | 核心库加载模式（**测试期**收敛闭环规模） |
| **优先级** | P0 |
| **用户口径** | 「系统启动按照资金池加载方式，可以有选择的加载核心代币与股票代币，测试代币。**为了便于测试，请把加载模式限制为核心库**，这样，系统启动的时候，资金库仅仅加载核心库，降低闭环数量，便于确定问题。」 |

### 1.1 口径边界（**必须随文档一起被读**）

> **核心库模式是用户明示的「测试期配置」，不是生产策略。**
> 它把资金库的加载集合从「核心＋股票＋测试」收敛到「**仅核心**」，
> 目的是**降低闭环数量、便于定位问题**。
> 生产环境恢复全部库时，**只需改一处**（`TokenLoadMode::default_for_scope()`），
> 编排器与审计会**跟随**该默认走，不需要再改第二处。

---

## 2. 改动清单（**文件级**）

| # | 文件 | 改动 | 口径 |
|---|---|---|---|
| 1 | `flashloan/tools/pool_manager/src/scope.rs` | `TokenLoadMode::default_for_scope()` 由「全部库」改为 **`Core`** | 加载模式的**唯一**所有者；其它模块**跟随**它 |
| 2 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 审计策略改为**跟随** `default_for_scope()`，不再自带一份加载模式 | 审计与物化**同源** ⇒ 审计不再可能与实际加载集合脱节 |
| 3 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 修掉 `core_scope_library_policy_stale` 被 `push` **6 次**的缺陷 | 同一 issue 重复 6 次会让运维读数失真；一次一个 issue |
| 4 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | `busy_timeout` 由 **5 s** 改为 **60 s** | 审计与运行时的写入并发；5 s 会把「正忙」误报成「失败」 |
| 5 | `scripts/mainnet_arbitrage_healer.py` | 作用域重建时携带 `AUDITED_SCOPE_TOKEN_LOAD_MODE` | 编排器**把审计判定的口径**传给池管理器，而不是自带一份 |

### 2.1 随之修正的**既有**测试（**不是放宽断言**）

以下 **5** 条 Rust 测试断言的是**旧默认值**（全部库）。改动后它们必须跟着改成
**新默认值**（核心库）—— 断言的**含义**没变，变的只是**被断言的那个默认**。

| 测试 | 修正点 |
|---|---|
| `the_default_token_load_mode_loads_the_core_library_only` | 断言的期望值改为 `core` |
| `a_test_library_fund_token_is_a_scope_anchor` | 同上（测试库的**锚点**角色不再随默认加载） |
| `core4_scope_is_edge_any_and_dry_run_matches_apply` | 配置参数由 `["core","stocks",…]` 改为 `["core"]` |
| `candidate_exchange_scope_admits_every_admitted_token_library` | 同上 |
| `scope_write_replays_persisted_result_and_keeps_one_record` | 记录数由 **8** 改为 **7**（少加载一个库的必然结果） |

---

## 3. 回归范围

```powershell
# M-1 池管理器
cargo test --offline -p pool-manager

# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q
```

**未改动的模块不重跑。**

---

## 4. 实盘读数（**2026-11-27 · 只读复核**）

命令：`pool-manager baseline-audit --store flashloan/db/local_store_4663.sqlite3 --chain-id 4663`

| 项 | 读数 |
|---|---|
| `coreScopeConfiguredLibraries` | **`["core"]`** |
| `coreScopePolicyMatches` | **`true`** |
| `candidateScopeConfiguredLibraries` | **`["core"]`** |
| `candidateScopePolicyMatches` | **`true`** |
| `candidateScopeOutOfPolicyPoolCount` | **0** |
| `closedLoops.loopCount` / `hopCount` | **10 / 30** |
| `closedLoops.invalidHopDirectionalReferenceCount` | **0** |
| `closedLoops.incompleteLoopCount` | **0** |
| 库规模（tokenLibraries） | `core` **4/4**、`stocks` **194/194**、`extension` **2/162**、`test` **1/1**；`missingAddress/Decimals/Name/Symbol` 全 **0** |
| `issues` | **2** 条**覆盖事实**（非缺陷）：`extension_library_contains_unanchored_tokens`（**6** 个）、`candidate_pairs_missing_top1_recommendation`（**18** 对） |

### 4.1 规模收敛（**落盘读数**）

| 层 | 改前 | 改后 |
|---|---|---|
| `v4_exchange_pools_core4` | **429** 行 | **415** 行（删 **14**） |
| `manager_v4_closed_loops` | **42** | **10** |
| `manager_v4_closed_loop_hops` | **126** | **30** |

重建顺序（**不可跳过**：每层读上面一层的版本与校验和）：
`candidateEdge(1222)` → `candidateExchange(710)` → `directionalPools(194)` → `closedLoops(152)`。

### 4.2 诚实边界

* **2** 条基线 issue 是**覆盖事实**，**没有工具可清** ⇒ 只带计数上报，**不驱动修复**。
* 核心库模式**不是**生产策略（见 §1.1）。

