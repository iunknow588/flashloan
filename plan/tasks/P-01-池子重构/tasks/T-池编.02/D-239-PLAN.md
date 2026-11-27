# D-239 `T-池编.02`｜作用域加载模式不得收窄（根因修复与生产恢复）

> 任务编号 `T-池编.02`（纯 ID）　验收编号 `AC-池编.02`
> 编号索引 `池编` ＝ `M-1`（池管理器）＋ `M-8`（编排层），**按模块号升序拼接**；该索引号段内第 `02` 个。

**改动模块（= 回归范围）**：`M-1`、`M-8`
**优先级**：P0　　**前置模块**：无　　**需授权**：否（写入走 `dryRun=false` 的既有接口）

## 1.0 编号逻辑（**本任务的编号从哪来**）

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-池编.02`** |
| **本任务验收编号** | **`AC-池编.02`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`池编`** ＝ `M-1`（池管理器）＋ `M-8`（编排层），**按模块号升序拼接** |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **`T-池编.01`**（核心库加载模式 · **已实盘生效**） |

**为什么是组合索引**：本任务改了两个**平级**模块 ——

| 模块 | 名称 | 索引 | 改动落点 |
|---|---|---|---|
| `M-1` | 池管理器（链上池状态 / 深度 / 边界） | `池` | `flashloan/tools/pool_manager/src/scope.rs`、`src/baseline_audit.rs` |
| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` | `scripts/mainnet_arbitrage_healer.py` |

`M-1` 是**被强制牵连**的一方：作用域的**加载模式**由池管理器拥有，
而编排器**驱动**这次重建、并把审计口径传给池管理器
⇒ 两者改的是**同一件事**，拆开会让其中一半无处安放。

**本任务涉及的模块（= 回归范围）**：`M-1`、`M-8`。

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

## 1. 缺陷（**用户报告 · 生产库只读实测已复现**）

用户原话：**「系统启动状态为核心库加股票库加测试库的状态，所有池子都是正常能用的，
改为资金池启动只带核心库，池子就缺失了……修改资金池启动模式，不应该修改现有资金池的数据，
所有的池子都不会变化，只是启动加载到内存中的池子变少，但是绝对不会缺池子。」**

用户是对的。**上一轮（D-237）的结论错了**：那 19 条缺值复核的池
**从未离开市场目录**，缺的是**作用域（scope）的物化范围**。

| 读数（生产库只读 · 2026-11-28） | 值 |
|---|---|
| 主池表 `v4_exchange_pools` | **2550** 行 |
| 19 条缺值复核引用的**去重池 hash** | **15** 个 |
| 其中**仍在主池表**里的 | **15 / 15**（**全部**） |
| 19 条路由中**每一跳的池都在主池表**里的 | **19 / 19**（**全部**） |
| 其中**被当前作用域覆盖**的 | **6 / 15** |
| 当前被**任一作用域完整覆盖**的路由 | **0 / 19** |
| 四个代币库 | `token_core` 4/4、`token_stocks` 194/194、`token_extension` 162(2)、`token_test` 1/1 —— **全部完好** |

> 探针：`plan/runtime_evidence/tools/probe_scope_narrowing_20261128.py`（**只读**）
> 报告：`plan/runtime_evidence/reports/scope_narrowing_20261128.json`

## 2. 缺陷链（三处代码 · 逐处定位）

| # | 位置 | 缺陷 |
|---|---|---|
| 1 | `flashloan/tools/pool_manager/src/scope.rs` `TokenLoadMode::default_for_scope()` | 返回 `Core`，而两个作用域**自己声明** `library_keys = ["core","stocks"]` ⇒ **默认比声明更窄** |
| 2 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 策略判定用**精确相等**比较 ⇒ 跑 `core_stocks` / `core_stocks_test` 的**正常**作用域被判 `core_scope_library_policy_stale` |
| 3 | `scripts/mainnet_arbitrage_healer.py` | `AUDITED_SCOPE_TOKEN_LOAD_MODE = "core"` **硬编码**；基线报 stale 时用 `apply=True` 把 `tokenLoadMode=core` **写进**持久化作用域 ⇒ **重建作用域时删掉股票池**（`deletedRows`） |

## 3. 时间线（`manager_write_requests` 台账 · 只读）

| 时刻（UTC） | 发起方 | 写入的 `libraryKeys` | `deletedRows` |
|---|---|---|---|
| 2026-11-18 17:36 / 19:17 | 手工 | `["core","stocks"]` | 1583 / 0 / 3 |
| **2026-11-18 22:50 · 2026-11-19 00:10** | 手工 | **`["core","stocks","test"]`** | 59 / 59 / 0 / 0 |
| 2026-11-26 19:07 | **healer** | `["core","stocks"]` | 0 / **66** |
| **2026-11-26 19:29** | **healer** | **`["core"]`** | **14** / 0 |
| 之后每次（11-27 共 10 次） | healer | `["core"]` | 0 / 0 / 1 |

**缺陷发生点**：**2026-11-26 19:29** —— healer 把自己硬编码的 `core` 模式写进了生产作用域。

而那 19 条复核写于 **2026-11-26 19:06:40 – 19:11:20**（作用域还是 `core_stocks` 时），
**全部是股票代币路由**（NVDA / MSTR / SPY / AMD / MU 锚）⇒ 它们是**被 18 分钟后的收窄「孤儿化」**的，
**不是「测不到」**。

## 4. 修复（**已落地**）

| 模块 | 修复 |
|---|---|
| `M-1` | `scope.rs`：`default_for_scope()` 改为按**作用域自身声明**取模式（新增 `for_library_keys()`）⇒ **默认永不窄于声明**；`baseline_audit.rs`：策略判定由**精确相等**改为 `scope_carries_declared_libraries()`（**声明 ⊆ 已配置** ⇒ 宽于声明不算 stale，缺库才算 stale） |
| `M-8` | `mainnet_arbitrage_healer.py`：`AUDITED_SCOPE_TOKEN_LOAD_MODE = None`（healer **不再自带模式**）；新增 `scope_expected_modes()` / `scope_configured_libraries()` / `scope_mode_narrows()`；`repair_scope_policy()` 加**收窄守卫**（请求模式会丢掉作用域已有库 ⇒ **跳过并记录 skipped**） |

## 5. 生产恢复（**顺序不可反**）

1. **先部署**：`cargo build --offline --release -p pool-manager -p manager-api`（manager-api 链接 pool-manager）
   ＋ 重启 manager-api ＋ 重启 healer（healer 是 Python，改完必须重启进程才生效）。
2. **再恢复作用域**：两个作用域回到**声明模式** `core_stocks`（= `coreScopeExpectedLibraries`）。
3. **再重建级联**：`candidateEdge → candidateExchange → directionalPools → closedLoops`（每层读上一层的版本与校验和）。

> **顺序不能反**：healer 未修好前，下一轮循环会再次把作用域收窄成 `core`。

## 6. 独立回归

````powershell
# M-1 池管理器
cargo test --offline -p pool-manager

# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q

# 生产库只读复核（不改任何数据）
python plan/runtime_evidence/tools/probe_scope_narrowing_20261128.py
````

> **只跑上面这些。** 未改动模块**不重跑**。

## 7. 诚实边界

- **本任务不伪造任何盈亏数值**，不填 0，不放宽任何门禁，不删任何历史复核行。
- 恢复作用域只是让那 19 条路由**重新进入目录、可被定价**；
  它们**是否真的有盈亏**由链上事实决定，**不预判**。
- 广播缺口仍是**市场条件**问题（正利润是灰尘级），本任务**不代任何其它任务关闭**。
