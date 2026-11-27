# `T-核.01`｜运行时维护链接线 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-核.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 运行时维护链接线 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-核.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d152"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.01/D-152-PLAN.md` -->

# D-152 `T-核.01`｜运行时维护链接线 · 开发计划（**独立 · 自包含**）

> **本目录是 `T-核.01` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-核.01`** |
| **本任务验收编号** | **`AC-核.01`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`核`**（`M-13`） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-核.02`（内存大对象方向化） |

**本任务涉及的模块**：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-13` | 运行时内核与运行时接线（`flashloan/rs-src/`、`flashloan/src/dex/runtime/`） | `核` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1 / §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-核.01`** |
| **验收编号（纯 ID）** | **`AC-核.01`** |
| **标题** | 运行时维护链接线（方向层 ＋ 闭环层进维护链） |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-3`（方向层）＋ `M-4`（闭环层） |
| **需授权** | 是（维护链会写生产库） |
| **一句话目标** | 让**方向层**与**闭环层**进入运行时的自动维护链，Python 侧**只调 Rust 入口、不自己算**，消掉「两张闭环表」「方向层永不刷新」两个缺口 |

### 1.1 背景与问题（**本轮最要紧的一条**）

```text
运行时 v4_closed_loop_worker
  └─ run_closed_loop_build()             flashloan/src/dex/runtime/v4_maintenance_workers.py:470
       └─ core.db.builders.build_closed_loops_anchor(...)     :512
            └─ 只读写【遗留表】：dex_* / v4_closed_loop* / v4_exchange_pools
               builders.py 全文【无一处】manager_* 表
```

**实盘读数（生产库，2026-11-25）**：

| 表组 | 行数 | 最后更新 | 谁写的 |
|---|---:|---|---|
| `v4_closed_loops`（**遗留**） | **46** | 2026-11-25 **15:08 +08** | 运行时 `v4_closed_loop_worker`（Python 遗留 builder） |
| `v4_closed_loop_hops`（**遗留**） | **138** | 同上 | 同上 |
| `manager_v4_closed_loops`（**管理器**） | **174** | 2026-11-25 **08:25 +08** | `closed_loop_manager`（Rust） |
| `manager_v4_closed_loop_hops`（**管理器**） | **522** | 同上 | 同上 |
| `manager_v4_closed_loop_optimizations`（**管理器**） | **472 → 503** | 2026-11-25 **18:14 +08** | 同上（本轮显式重建） |

**系统真正消费的是管理器表**（运行时内核 `load_manager_closed_loop_rows`、
复核层的方向交叉校验、广播层，全部读 `manager_v4_*`）。

⇒ **后果**：迁移 017 落地后，运行时的维护 worker **永远不会**把新口径带进闭环。
这既解释了 `D-142` §9 的「重建不落地」，**也违反用户口径**：
「python 侧没有自己的数据处理逻辑，仅仅使用内存对象中的实际值，
内存对象直接调用相关的管理工具（rust 实现）」——
`builders.build_all_anchor_data` 已经**正确地**委托给 Rust 内核
（`core.rs_bridge.build_v4_anchor_data`，并明确写「kernel 缺失是坏系统，
不是可以退回 Python 本地重建的模式」），
但 `run_closed_loop_build` **绕过**它、直接调 Python 本地实现。

### 1.2 方向层同样没有自动刷新路径（**`T-方.02` 的运行期一侧**）

方向行的**唯一**生产写入方是 `directional_pool_manager`（`lib.rs:1785 replace_directional_rows`），
它的入口有三条，**都不在运行时维护链里**：

```text
① manager_api 的 POST /v1/directional-pools/rebuild（需 DIRECTIONAL_POOL_MANAGER_AUTHORITY=manager）
② directional_pool_manager 的 example 二进制（手工）
③ composite-manager materialize-arbitrage —— 它【读】方向层、【不】物化方向层
```

⇒ 实盘方向层停在 **2026-11-23 22:46**，相对流动性快照表漂移 **83** 行 / **994** 格（`inv-9` FAIL）。

### 1.3 不做什么（**范围外**）

* **不**重写闭环算法、方向层投影算法 —— 一字不改，只**接线**。
* **不**在 Python 侧实现任何方向层 / 闭环层的数据处理逻辑（用户口径的红线）。
* **不**删遗留表（遗留表仍被别处读；下线是独立任务）。
* **不**做跨月统计、落盘失败降级之类的低概率路径。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-13` | 运行时接线 | `flashloan/src/dex/runtime/v4_maintenance_workers.py` | 新增方向层 worker 槽 ＋ 闭环槽改走 Rust 入口 |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/runtime_core.rs` | 暴露「按链物化方向层」「重建闭环」两个 Rust 入口 |
| `M-13` | 运行时接线 | `flashloan/src/core/rs_bridge.py` | 两个入口的 Python 薄封装 |
| `M-13` | 运行时接线 | `flashloan/src/core/db/v4_pool_liquidity_worker.py` | 槽位名册增列新槽 |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/lib.rs` | 两个入口的 `#[pyfunction]` 外壳 |
| `M-13` | 运行时接线 | `flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py` | 既有断言随新槽更新 |

### 2.2 执行步骤

| 步 | 动作 | 完成判据 |
|---:|---|---|
| **1** | 读 `D-154`，确认每条验收项的当前状态 | 全部条目已知 |
| **2** | 盘点维护链现状：worker 槽、依赖图、游标表 | ✅ 拓扑写入 `D-155` §1（槽 **8** 个、依赖图、游标表 `chain_business_initialization`） |
| **3** | `rs-src` 暴露 Rust 入口（方向层按链物化 ＋ 闭环重建） | ✅ `runtime_core.rs:2923` / `:2953` ＋ `lib.rs` 两个 `#[pyfunction]` |
| **4** | `v4_maintenance_workers.py` 新增**方向层 worker 槽** ＋ 依赖图 | ✅ 槽名 `v4_directional_pool_worker`（`:50` / `:479`），名册已增列 |
| **5** | 闭环槽改为调 Rust 入口（**保留**遗留 builder 并**显式标注**） | ✅ `:631` 先调入口；入口失败**整槽失败**（**不降级**）；遗留 builder 标注为遗留路径（`:618-626`） |
| **6** | 跑 `cargo test --offline -p flashloan-rs` ＋ Python 侧既有测试 | ✅ **221 passed / 0 failed** ＋ **160 passed / 0 failed** |
| **7** | 实盘**只读**复核：新槽的读数与既有槽的读数 | ✅ 在**只读瘦副本**上完成（实盘库零写入）：`inv-9` **FAIL → PASS**、闭环 **174 / 522** |
| **8** | 逐条勾选 `D-154` | ✅ **17 / 17**，每条附实测读数 |
| **9** | （扩范围记录）`rs_bridge.py` / `v4_pool_liquidity_worker.py` / `lib.rs` / 测试文件并入 `M-13` | 见 `D-153` §1 的扩范围记录 |

### 2.3 技术文案（`docs/`）

* [`D-155`](#d155)
  —— 槽位拓扑、依赖图、调用链、实盘读数（瘦副本演练）、**部署闸门**三件套。

### 2.4 三条执行纪律

1. **Python 侧不做数据处理**：只允许「调 Rust 入口 ＋ 读回执」两种动作。
2. **不动 `builders.py`**（并行施工保护）：改动只落在**调用方**（运行时维护链）。
3. **实盘一律只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）；维护链**自动**写生产库属正常业务，
   但**手工**触发写操作前先跑 dry-run 并读回执。

---

## 3. 完成定义

* [x] `D-154` 中**全部** `- [ ]` 变为 `- [x]` —— **17 / 17**
* [x] `D-153` 的**全部**回归命令**全绿** —— **221** ＋ **160** passed
* [x] 每条已勾选项都有**可复算的实测读数**（`D-155` §5 给出复算命令）
* [x] Python 侧**新增**的代码里**没有**任何方向层 / 闭环层的数据处理逻辑
* [x] 管理器表（`manager_v4_*`）**有**自动刷新路径 —— 新增槽 ＋ 闭环槽改走 Rust 入口
* [x] **未**触碰**不属于** `M-13` 的模块 —— `builders.py` 与两个管理器 crate 均**不在**改动清单内

> **收口口径（诚实）**：本任务**代码与读数**已闭合；
> 「实盘运行期生效」**未发生**，卡在**部署闸门**
> （迁移 **018** ＋ **重建 Rust 扩展** ＋ 新二进制，**同批、无中间态**，见 `D-155` §3）⇒
> 闸门本身归属 `T-闭.03` 的**部署窗口**，本任务**不代开**。

---

## 4. 归档

```powershell
python plan/runtime_evidence/tools/archive_task_20261124.py T-核.01 --dry-run
python plan/runtime_evidence/tools/archive_task_20261124.py T-核.01
```

---

## ③ 修改范围守护

<a id="d153"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.01/D-153-SCOPE_GUARD.md` -->

# D-153 `T-核.01`｜运行时维护链接线 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.01` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时接线 | `flashloan/src/dex/runtime/v4_maintenance_workers.py` | 新增方向层 worker 槽 ＋ 闭环槽改走 Rust 入口 | **是** |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/runtime_core.rs` | 暴露两个 Rust 维护入口 | **是** |
| `M-13` | 运行时接线 | `flashloan/src/core/rs_bridge.py` | 两个 Rust 入口的 Python 薄封装（只调入口 ＋ 读回执） | **是** |
| `M-13` | 运行时接线 | `flashloan/src/core/db/v4_pool_liquidity_worker.py` | 槽位名册增列 `v4_directional_pool_worker` | **是** |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/lib.rs` | 两个入口的 `#[pyfunction]` 外壳（注册与回执形状） | **是** |
| `M-13` | 运行时接线 | `flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py` | 既有槽位/依赖断言随新槽更新 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **扩范围记录（2026-11-25）**：`rs_bridge.py` 是入口的 Python 侧唯一落点，
> 不扩它就无法「只调入口 ＋ 读回执」；`v4_pool_liquidity_worker.py` 持有**槽位名册**
> （`V4_MAINTENANCE_WORKER_DOMAINS` / `MAINTENANCE_WORKER_ORDER`），
> 新槽不在名册里 ⇒ 游标虽然能落库，但 UI 与状态读数**看不见这个槽**；
> `lib.rs` 是 `#[pyfunction]` 的**唯一**注册处（`runtime_core.rs` 只放逻辑）；
> 测试文件随断言变更同步。三者都属 `M-13`，无跨模块扩散。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-13` | 运行时内核 | `cargo test --offline -p flashloan-rs` | 全绿（基线 **220** passed；**2026-11-26 实测 221 passed / 0 failed** ＝ 基线 ＋ `T-核.02` 线程在 `rs-src` 的 **1** 条新增用例，**本任务未新增 Rust 用例**） |
| `M-13` | 运行时接线 | `python -m pytest flashloan/app_runtime/tests -k "maintenance or worker" -q` | 全绿（**2026-11-26 实测 160 passed / 0 failed**，3017 deselected） |

```powershell
cd flashloan/rs-src
cargo test --offline -p flashloan-rs
```

> **只跑上面这些。** 未改动模块**不重跑**。

> **口径订正（2026-11-26）**：本节原写的 `flashloan/tests` **不存在**
> （实测 `Test-Path` ＝ False）⇒ 实际目录是 **`flashloan/app_runtime/tests`**，
> 上表按实际路径执行。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/src/core/db/builders.py` | **并行施工保护** —— 改动只落在**调用方**（运行时维护链） |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的投影规则、准入判定、容量口径一字不改（本任务只**接线**） |
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；闭环算法一字不改（本任务只**接线**） |
| `flashloan/rs-src/src/worker_pool.rs` | 属 `T-核.02`（内存大对象方向化）；本任务**不碰**注册表结构 |
| `flashloan/tools/{broadcast_manager,review_manager,manager_api}/**` | 属 `M-6` / `M-7` / `M-12`；本任务不改它们的契约 |
| 各 `chain.json` / `.env` / `.env.example` | 本任务**不引入**任何运行期参数（间隔沿用既有 `_env_int` 模式） |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-152` §2.1；
2. **记录原因** —— 在 `D-152` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-13` 代码 | 否 | 纯代码 |
| 实盘**只读**复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 手工触发会写生产库的维护槽 | **是** | 用户口径「所有需授权操作默认授权通过」；触发前先 dry-run 并读回执 |

---

## 6. 范围守护自检

* [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）—— 六个 `M-13` 文件，无扩散
* [x] §2 的**全部**命令**已执行**且**全绿** —— **221** ＋ **160** passed
* [x] §3 的禁止触碰项**确未被触碰** —— `directional_pool_manager/**`、`closed_loop_manager/**`、
      `worker_pool.rs`（属 `T-核.02`）**均不在**本任务改动清单内
* [x] `flashloan/src/core/db/builders.py` **未被触碰** —— `git diff --stat` **输出为空**
* [x] Python 侧**新增**代码里**没有**方向层 / 闭环层的数据处理逻辑 —— 只做「调入口 ＋ 读回执 ＋ 存游标」
* [x] **未**因本任务跑过**全量**测试 —— 只跑了 `M-13` 的两个模块

---

## ④ 测试目标 check list

<a id="d154"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.01/D-154-checklist.md` -->

# D-154 `T-核.01`｜运行时维护链接线 · 验收清单

> **本文件是 `T-核.01` 的全部验收框。** 编号 `AC-核.01` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-13` 的现有单测全绿（基线 **220** passed）

**本轮状态（2026-11-26）**：**17** 框**全部**闭合（每条附实测读数）。
**未**按推断勾选；实盘侧的读数**全部来自只读瘦副本**（实盘库**零写入**），
「实盘运行期生效」**如实登记为未生效**（部署闸门未开）。

---

## 1. 根因与定位

- [x] 闭环维护槽写的是**遗留表**，已定位到源码行

      > **读数**（改动前）：`run_closed_loop_build` `flashloan/src/dex/runtime/v4_maintenance_workers.py:470`
      > → `core.db.builders.build_closed_loops_anchor` `:512`；
      > `builders.py` 全文**无一处** `manager_*` 表。
      > **改动后复测**：`builders.py` 里 `manager_` 命中 **0** 次（`build_closed_loops_anchor` 在 `:1283`）；
      > 槽的**遗留**调用仍在 `:699`，而**管理器**调用在 `:631`（**先跑**）。

- [x] 两张闭环表的存在与最后更新时刻已实测

      > **读数**（2026-11-26 实测，只读）：遗留 `v4_closed_loops` / `_hops` = **46 / 138**，
      > 最后更新 **2026-11-25T17:50:04Z**（＝ **2026-11-26 01:50 +08**，**运行时仍在写**）；
      > 管理器 `manager_v4_closed_loops` / `_hops` = **174 / 522**，最后更新 **2026-11-25T10:14:49Z**
      > （＝ **2026-11-25 18:14 +08**）⇒ 遗留层**比**管理层**新**，正是「两个口径」的现场。

- [x] 方向层的自动刷新路径**不存在**，已定位到源码行（**本任务已补上**）

      > **读数**：方向行唯一生产写入方 `directional_pool_manager/src/lib.rs:1785`；
      > 三条入口都不在维护链里（`manager-api` 端点 / example 二进制 / `composite-manager materialize-arbitrage`）；
      > 维护链 **7** 个 worker 槽里**没有**方向层槽。
      > **本任务落地后**：新增槽 `v4_directional_pool_worker`（`v4_maintenance_workers.py:50` 定义、
      > `:479` 实现），方向层**第一次**有了自动刷新路径；实盘方向行仍停在
      > **2026-11-23T22:46:22Z**（**未生效**，见 §5.3）。

---

## 2. 实施

- [x] Rust 侧暴露**按链物化方向层**入口

      > **读数**：`runtime_core.rs:2923` `pub fn rebuild_directional_pools(database_path, network, chain_id, apply) -> Result<Value, RuntimeCoreError>`；
      > 内层调 `directional_pool_manager::materialize_directional_pools_for_chain`；
      > `lib.rs` 的 `#[pyfunction] rebuild_directional_pools(network, chain_id, apply=false, database_path=None)`；
      > 回执＝管理器自己的回执 ＋ `ok` ＋ `manager = "directional_pool_manager"`；
      > 字段清单见 [`D-155`](#d155) §1.2。

- [x] Rust 侧暴露**重建闭环**入口

      > **读数**：`runtime_core.rs:2953` `pub fn rebuild_closed_loops(...)`；
      > 内层调 `closed_loop_manager::materialize_closed_loops`；
      > `lib.rs` 的 `#[pyfunction] rebuild_closed_loops(network, chain_id, apply=false, database_path=None)`。

- [x] 维护链接入**方向层 worker 槽**（含依赖图与游标）

      > **读数**：槽名 `v4_directional_pool_worker`（`:50` `WORKER_DIRECTIONAL_POOL`）；
      > 间隔 `V4_DIRECTIONAL_POOL_WORKER_INTERVAL_SECONDS`（缺省 **300**）；
      > 依赖 `("v4_pool_liquidity_worker",)`；闭环槽依赖扩为
      > `("v4_market_observation_worker", "v4_directional_pool_worker")`；
      > 名册 `V4_MAINTENANCE_WORKER_DOMAINS` / `MAINTENANCE_WORKER_ORDER` 已增列该槽；
      > 游标落 `chain_business_initialization`（`domain` ＝ 槽名）。

- [x] 闭环槽**改走 Rust 入口**

      > **读数**：`:631` 先调 `rebuild_closed_loops(apply=True)`；
      > **入口失败 ⇒ 整槽失败**（`:661` 写失败回执并 `return`，**遗留 builder 不再兜底**，
      > 符合用户口径「要么成功要么失败」）；
      > 成功后才跑遗留 builder（`:699`），其定位在代码注释里**显式标注为遗留路径**
      > （`v4_maintenance_workers.py:618-626`：「the manager path is the one the system consumes …
      > it is labelled as the legacy path in the receipt so nobody mistakes its rows for the manager's rows」）。

- [x] Python 侧**没有**新增任何方向层 / 闭环层的数据处理逻辑

      > **读数**：`rs_bridge.py` 新增 **+69** 行只做「转发 `apply` / `database_url` ＋ 回执透传」；
      > 槽侧新增 **+110** 行只做「调入口 ＋ 读回执 ＋ 求和 ＋ 存游标」；
      > **无**任何投影 / 准入 / 容量 / 选池规则的 Python 实现（逐行核对 `D-155` §1.2 的调用链）。

- [x] **未**触碰 `flashloan/src/core/db/builders.py`

      > **读数**：`git diff --stat -- flashloan/src/core/db/builders.py` **输出为空**；
      > 文件里 `manager_` 命中 **0** 次。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p flashloan-rs` 全绿

      > **读数**（2026-11-26 实测）：**221 passed / 0 failed / 0 ignored**
      > （清单写的基线 **220** ＝ **实测 221**，差 **1** 来自 `T-核.02` 线程在 `rs-src` 的新增用例；
      > **本任务没有新增 Rust 用例** ⇒ **如实登记基线漂移**，不冒领）。

- [x] 运行时接线的既有测试全绿

      > **读数**：`python -m pytest flashloan/app_runtime/tests -k "maintenance or worker" -q`
      > ⇒ **160 passed / 0 failed**（**3017 deselected**）；
      > 另跑 `test_v4_maintenance_workers.py ＋ test_architecture_guards.py` ⇒ **102 passed / 0 failed**。
      > **口径订正**：`D-153` §2 写的路径 `flashloan/tests` **不存在**（实测 `Test-Path` ＝ False），
      > 实际目录是 `flashloan/app_runtime/tests` ⇒ 按实际路径执行。

---

## 4. 实盘（**只读复核**）

> **读数取自「实盘库的只读瘦副本」**：源库用 `mode=ro` ＋ `PRAGMA query_only = ON` 打开，
> 副本 ＝ 实盘的**表结构全文 ＋ 35 张表的行**（**15,049** 行）；
> 干跑与物化**只在副本上**执行 ⇒ **实盘库零写入**。

- [x] 方向层槽的**干跑**读数与 `inv-9` 读数对得上

      > **读数**（副本，`robinhood` / `4663`）：干跑 `scopeKeys` ＝ `candidate_exchange` ＋ `core4`、
      > `failedScopes` ＝ **0**；
      > `candidate_exchange`：`basePoolCount` **494**、`directionalPoolCount` **352**（方向 0/1 ＝ **176/176**）、
      > **`changedRows` 352**、`directionalPoolVersion` **3**；
      > `core4`：**488** / **340**（**170/170**）、**`changedRows` 340**、`directionalPoolVersion` **3**。
      > **对得上**：`changedRows` 合计 **692** ＝ 方向行总数 **692** ⇒ 整层过时；
      > 物化后 `inv-9` 由 **FAIL（漂移 83 行 / 994 格）** 变为 **PASS（0 行 / 0 格）**，
      > 方向层物化时刻由 **2026-11-23 22:46** 变为 **2026-11-25 17:45**（本次）。

- [x] 闭环槽的干跑读数与既有物化读数对得上

      > **读数**（副本，已含迁移 **018** 与刷新后的方向层）：
      > `anchorCount` **34**、**`loopCount` 174**、**`hopCount` 522**、
      > `optimizationCount` **516**、`skippedTokenCount` **79**、
      > **`changedRows` 2486**、**`closedLoopVersion` 22**、
      > `directionalPoolVersion` **3**、`candidateEdgeVersion` **10**、`observationVersion` **13**。
      > **对得上**：实盘管理层闭环 **174** / 跳 **522** ⇒ **逐条一致**（含身份集合：
      > 共有 **174**、只在实盘 **0**、只在副本 **0**）；
      > 上游版本 **10 / 13** 与实盘**相同** ⇒ 读的是同一份上游。
      > **差异（如实登记）**：`changedRows` **2486** vs `T-闭.01` 的 **2442**（差 **44**）——
      > 方向层换代 ＋ 估值口径换源（`T-闭.02`）所致；`optimizationCount` **516** vs 实盘 **503**。

- [x] 复核全程**只读**

      > **读数**：副本工具 `clone_direction_layer_slice_20261126.py` 的源连接
      > `file:…?mode=ro` ＋ `PRAGMA query_only = ON`；
      > 审计工具 `audit_directional_invariants_20261125.py --db <副本>` 同样只读；
      > 对照工具 `compare_clone_live_closed_loop_20261126.py` 对实盘也是 `mode=ro`；
      > **实盘库零写入**（本次未对实盘发起任何写事务）。

---

## 5. 诚实边界

- [x] 本任务**未**改动方向层 / 闭环层的算法本身

      > **读数**：本任务的改动文件**全部**落在 `D-153` §1 的六个 `M-13` 文件里；
      > `directional_pool_manager/**` 与 `closed_loop_manager/**` **不在**本任务改动清单内
      > （`git diff --stat` 只显示**它们各自的其它任务**改动）；
      > 投影规则、准入判定、容量口径、选池算法**一字未改**。

- [x] 遗留表的**下线**不在本任务范围

      > **读数**：遗留 builder 仍在跑（`v4_maintenance_workers.py:699`），遗留表仍被别处读；
      > 下线是独立任务，本任务**不删表、不删 builder**。

- [x] 「运行期生效」**如实登记**（实盘未重启 ⇒ 新槽未生效）

      > **读数**：实盘 runtime **pid 24524** 自 **2026-11-25 17:14:00** 起未重启 ⇒
      > 新槽**未生效**；实盘方向行仍停在 **2026-11-23T22:46:22Z**，管理层闭环仍停在
      > **2026-11-25T10:14:49Z**。
      > **★ 生效前提（三样必须同批，缺一即失败）**：
      > ① 迁移 **018**（实盘库停在 **017**）；
      > ② **重建 Rust 扩展** —— `rs-src/target/release/flashloan_rs.dll`（**2026-11-25 14:32**）
      > 的 ASCII 符号扫描**不含** `rebuild_directional_pools` / `rebuild_closed_loops`
      > （`target/debug` 的**含**），而装载器 `core/rust_extension.py` **优先**取 `target/release`
      > ⇒ 不重建就重启，新槽会按**设计**失败（`ENTRY_UNAVAILABLE`，**不降级**）；
      > ③ 新二进制（内核读闭环行要 `single_tick_capacity_binding_directional_pool_id`）
      > ⇒ **无中间态**。详见 [`D-155`](#d155) §3。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.01/README.md` -->

# `T-核.01`｜运行时维护链接线（方向层 ＋ 闭环层进维护链）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.01`（纯 ID）　**验收**：`AC-核.01`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-核.01` / `AC-核.01` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时内核与运行时接线）），该索引号段内第 `01` 个。（详见 `D-152` §1.0）

**改动模块（= 回归范围）**：`M-13`

**前置模块**：`M-3`（方向层）＋ `M-4`（闭环层）　　**需授权**：是（维护链会写生产库）

## 文件

| 文件 | 内容 |
|---|---|
| `D-152` | 本任务的开发计划与回归范围（**自包含**） |
| `D-153` | 本任务的修改范围守护与逐模块回归命令 |
| `D-154` | 本任务的逐条验收框 |
| `docs/D-155` | **槽位拓扑 · 调用链 · 实盘读数 · 部署闸门**（本任务的技术文案） |

## 一句话

> **管理层流水线本来没有自动重建路径**：运行时 `v4_closed_loop_worker` 调
> `core.db.builders.build_closed_loops_anchor`（Python 遗留 builder），
> 而该函数**只读写遗留表** —— `builders.py` 全文**无一处** `manager_*` 表。
> 实盘（2026-11-26 只读实测）：遗留表闭环 **46** / 跳 **138**（最后更新
> **2026-11-26 01:50 +08**），管理器表闭环 **174** / 跳 **522**（最后更新
> **2026-11-25 18:14 +08**）⇒ **两个口径并存**。
> **本任务已把它接上**：新增维护槽 `v4_directional_pool_worker`，
> 闭环槽改为**先调 Rust 入口**、入口失败**整槽失败**（**无降级**）；
> Python 侧只调入口、**不自己算**。
> **当前状态（2026-11-26）**：**17 / 17** 项闭合；
> 实盘侧读数全部来自**只读瘦副本**（实盘库**零写入**）——
> 方向层 `inv-9` 由 **FAIL（83 行 / 994 格）→ PASS（0）**，闭环仍 **174 / 522**；
> **运行期生效**卡在**部署闸门**（迁移 **018** ＋ **重建扩展** ＋ 新二进制，**同批、无中间态**）。

## 独立回归

```powershell
cd flashloan/rs-src
cargo test --offline -p flashloan-rs
```

**读数（2026-11-26 实测）**：`cargo test --offline -p flashloan-rs` ⇒
**221 passed / 0 failed**（清单基线 **220** ＝ 实测 **221**，差 1 来自 `T-核.02` 线程）；
`python -m pytest flashloan/app_runtime/tests -k "maintenance or worker" -q` ⇒
**160 passed / 0 failed**。

**技术文案**：[`docs/D-155`](#d155)

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d155"></a>

### `D-155-维护链接线拓扑与实盘读数_20261126.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.01/docs/D-155-维护链接线拓扑与实盘读数_20261126.md` -->

# D-155 `T-核.01`｜维护链接线拓扑与实盘读数

> **本文件回答 `D-152` §2.2 第 2–4、7 步的问题**：
> 方向层与闭环层**怎么接进**运行时维护链、接进去之后**读数是什么**、
> 上线前**还差哪几步**。
>
> 依据：[`D-152 开发计划`](#d152)／
> [`D-153 范围守护`](#d153)／
> [`D-154 验收清单`](#d154)。

> **实盘全程只读**：读实盘库用 `mode=ro` ＋ `PRAGMA query_only = ON`；
> 演练在**瘦副本**上做，实盘库**零写入**。

---

## 0. 一句话结论

> **接线已落地**：新增维护槽 `v4_directional_pool_worker`，闭环槽改为**先调 Rust 入口**、
> 入口失败**整槽失败**（**无降级**，遗留 builder 保留但**不再兜底**）；
> Python 侧**只有**「调入口 ＋ 读回执 ＋ 存游标」三种动作。
> **回归全绿**（`flashloan-rs` **221 passed / 0 failed**；运行时接线 **102 passed**）。
> **实盘侧尚未生效**，卡在**部署闸门**：实盘库停在迁移 **017**，
> 且**已安装的 Rust 扩展不含**两个新入口 ⇒ 必须在**同一个窗口**里
> 「应用迁移 **018** ＋ 重建扩展 ＋ 换二进制」，**无中间态**。
> 演练已在**只读瘦副本**上跑通：方向层 `inv-9` 由 **FAIL（83 行 / 994 格漂移）→ PASS（0）**，
> 闭环仍是 **174 / 522**。

---

## 1. 槽位拓扑

### 1.1 依赖图（`v4_maintenance_workers.py` `worker_dependencies()`）

```text
v4_pool_liquidity_worker
   ├─ v4_candidate_yc_zc_worker
   │     └─ v4_market_observation_worker
   │            ├─ v4_dex_overview_worker（与闭环槽并行）
   │            ├─ v4_onchain_review_worker → v4_broadcast_execution_worker
   │            └─ v4_closed_loop_worker ◀─┐
   └─ v4_directional_pool_worker ─────────┘（新增槽）
```

| 槽 | 间隔 | 依赖 | 入口 | 游标 domain |
|---|---|---|---|---|
| **`v4_directional_pool_worker`**（新增） | `V4_DIRECTIONAL_POOL_WORKER_INTERVAL_SECONDS`，缺省 **300** | `v4_pool_liquidity_worker` | `rebuild_directional_pools` | `v4_directional_pool_worker` |
| `v4_closed_loop_worker`（改） | `V4_CLOSED_LOOP_WORKER_INTERVAL_SECONDS`，缺省 **300** | `v4_market_observation_worker` ＋ **`v4_directional_pool_worker`** | `rebuild_closed_loops`（**先跑**）→ 遗留 builder（后跑，**不兜底**） | `v4_closed_loop_worker` |

> **游标表**：`chain_business_initialization`（`domain` ＝ 槽名，`completeness_json` 存回执数字）；
> 槽位名册在 `core/db/v4_pool_liquidity_worker.py` 的 `V4_MAINTENANCE_WORKER_DOMAINS` /
> `MAINTENANCE_WORKER_ORDER`（新槽已增列，**UI 与状态读数能看见这个槽**）。

### 1.2 调用链（**Python 侧没有第二份算法**）

```text
v4_maintenance_workers.run_directional_pool_build   ← 槽：只调入口、读回执、存游标
  └─ core.rs_bridge.rebuild_directional_pools        ← 薄封装（apply / database_url）
       └─ flashloan_rs.rebuild_directional_pools      ← #[pyfunction]（rs-src/src/lib.rs）
            └─ runtime_core::rebuild_directional_pools ← 错误映射 ＋ 回执加签
                 └─ directional_pool_manager::materialize_directional_pools_for_chain

v4_maintenance_workers.run_closed_loop_build
  └─ core.rs_bridge.rebuild_closed_loops
       └─ flashloan_rs.rebuild_closed_loops
            └─ runtime_core::rebuild_closed_loops
                 └─ closed_loop_manager::materialize_closed_loops
```

| 层 | 文件 | 新增行数 | 做了什么 |
|---|---|---:|---|
| 槽 | `flashloan/src/dex/runtime/v4_maintenance_workers.py` | **+110** | 新槽 ＋ 依赖 ＋ 间隔 ＋ 闭环槽改走 Rust |
| 薄封装 | `flashloan/src/core/rs_bridge.py` | **+69** | 两个入口的 `apply` / `database_url` 转发 ＋ 回执透传 |
| 内核 | `flashloan/rs-src/src/runtime_core.rs` | **+115** | 两个入口 ＋ 两张错误码表（**不重算任何数字**） |
| 注册 | `flashloan/rs-src/src/lib.rs` | **+14** | 两个 `#[pyfunction]` 外壳 ＋ 模块注册 |

> **口径**：Python 侧**没有**任何方向层 / 闭环层的数据处理逻辑 ——
> 槽里的数字全部来自 Rust 回执（`changedRows` / `directionalPoolCount` /
> `directionalPoolVersion` / `failedScopes`），Python 只做**求和与搬运**。

---

## 2. 实盘读数（瘦副本演练）

### 2.1 副本是怎么来的（**实盘零写入**）

| 项 | 值 |
|---|---|
| 工具 | `plan/runtime_evidence/tools/clone_direction_layer_slice_20261126.py` |
| 源 | `flashloan/db/local_store_4663.sqlite3`（**16.60 GB**），`mode=ro` ＋ `PRAGMA query_only = ON` |
| 目标 | `plan/runtime_evidence/kernel01_slice_4663.sqlite3`（**136.4 MB**） |
| 复制 | 全库**表结构**逐字复制 ＋ **35** 张表的行（**15,049** 行） |

> **★ 副本工具修掉的一个真缺陷（第一版副本曾让闭环干跑算出 0 个闭环）**：
> 实盘的 `manager_market_observation_yz_pairs` 是**视图**（迁移 **017** 的
> 「observation optimal pool view」），底表是 `manager_market_observation_yz_pairs_base`；
> 只复制视图的行 ⇒ 视图建在**空底表**上 ⇒ 闭环层**找不到任何 y-z 对** ⇒
> `loopCount = 0`。副本工具因此改为**复制底表**（`VIEW_TABLES_REPLACED_BY_BASE`），
> 并补上 `pool_scope_config`（作用域配置）与 `token_library_catalog`（代币目录）。
> ⇒ **这个缺陷只影响副本，不影响实盘**，但它会让「干跑读数」变成**假的 0**。

### 2.2 方向层槽：干跑（`apply=false`）

```powershell
cargo run --offline -p directional-pool-manager --example materialize_directional_pools_for_chain -- `
    plan/runtime_evidence/kernel01_slice_4663.sqlite3 robinhood 4663 dry-run
```

| scope | basePoolCount | directionalPoolCount | 方向 0 / 1 | changedRows | directionalPoolVersion | consumersNeedRebuild |
|---|---:|---:|---:|---:|---:|---|
| `candidate_exchange` | **494** | **352** | 176 / 176 | **352** | **3** | true |
| `core4` | **488** | **340** | 170 / 170 | **340** | **3** | true |

| 项 | 读数 |
|---|---|
| `scopeKeys` | `candidate_exchange` ＋ `core4` |
| `failedScopes` | **0**（空） |
| `snapshotChecksum` | 两个 scope **相同**（`sha256:01adb94a1…`）⇒ 同一张流动性快照表 |
| 干跑写入 | **0 行**（`dryRun: true`） |

### 2.3 `inv-9` 复测：**FAIL → PASS**

审计工具：`plan/runtime_evidence/tools/audit_directional_invariants_20261125.py`（**只读**）。

| 时点 | 总判 | `inv-9` 读数 |
|---|---|---|
| 物化**前**（副本＝实盘快照） | **8 passed / 1 failed** | 方向行 **692**、可比 **436**、**漂移 83 行 / 994 格**、方向层物化于 **2026-11-23 22:46**、快照最新读数 **2026-11-25 00:44** |
| 物化**后**（同一副本） | **9 passed / 0 failed** | 漂移 **0 行 / 0 格**、方向层物化于 **2026-11-25 17:45**（本次） |

⇒ 干跑读数（`changedRows` **352 ＋ 340 = 692** ＝ 方向行总数 ⇒ **整层要重写**）
与 `inv-9` 的读数**对得上**：漂移不是局部现象，是**整层过时**。

### 2.4 迁移 **018** 演练（副本）

| 项 | 读数 |
|---|---|
| `preflight` | `readyForMigration = true`、`quickCheck = ok`、目标表**不兼容数 0**、台账 `schema_migrations` |
| `migrate` | `20261125_018_closed_loop_directional_only` → `appliedNow = true`（**只此一条**） |

### 2.5 闭环槽：干跑（`apply=false`）

```powershell
cargo run --offline -p closed-loop-manager --example materialize_closed_loops -- `
    plan/runtime_evidence/kernel01_slice_4663.sqlite3 robinhood 4663 dry-run
```

| 项 | 读数 | 与实盘对照 |
|---|---:|---|
| `anchorCount` | **34** | — |
| **`loopCount`** | **174** | 实盘闭环 **174** ⇒ **一致** |
| **`hopCount`** | **522** | 实盘跳 **522** ⇒ **一致** |
| `optimizationCount` | **516** | 实盘表 **503**（**+13**） |
| `skippedTokenCount` | **79** | 实盘跳过的令牌行 **1100** |
| `changedRows` | **2486** | 既有物化读数 **2442**（`T-闭.01`）⇒ 差 **44**（方向层换代 ＋ 估值口径换源） |
| `closedLoopVersion` | **22** | 实盘 **20** |
| `directionalPoolVersion` | **3** | 实盘物化后 **3**（一致） |
| `candidateEdgeVersion` / `observationVersion` | **10** / **13** | 实盘 **10** / **13** ⇒ **一致** |

### 2.6 闭环行逐条对照（**副本已 apply** vs 实盘只读）

工具：`plan/runtime_evidence/tools/compare_clone_live_closed_loop_20261126.py`（实盘 `mode=ro`）。

| 项 | 读数 |
|---|---|
| 闭环行 | 实盘 **174** / 副本 **174** / 共有 **174** / 只在实盘 **0** / 只在副本 **0** ⇒ **闭环身份不变** |
| 跳行 | 实盘 **522** / 副本 **522** ⇒ **跳数不变** |
| `borrow_token_usdg_value_raw` 变化 | **78** 条 |
| `single_tick_capacity_anchor_raw` 变化 | **170** 条 |
| `directional_pool_ids_json`（路径）变化 | **118** 条（与 `T-闭.01` 记录的 **118 / 522** 换池跳一致） |

### 2.7 **A/B 预测与实际落地逐条吻合**

`D-151` 的 A/B 探针**只读**算出「B 侧」估值；副本 apply 后的闭环行是**生产代码真正写下的**值。
两者逐条比对：

| 项 | 读数 |
|---|---|
| 估值变化的闭环 | **78** |
| A/B 预测 ＝ 实际落地 | **78** |
| **不符** | **0** |
| A/B 里缺失的闭环 | **0** |

⇒ A/B 探针不是「估算」，它**预测的就是生产代码会写下的数** ⇒ `D-151` 的判定成立。

---

## 3. 部署闸门（**三样东西必须同批**）

| # | 东西 | 为什么必须同批 | 实测 |
|---:|---|---|---|
| **1** | 迁移 **018** | 闭环表删基础池列、方向列 `NOT NULL` | 实盘库停在 **017**；副本上 `preflight` 通过、`migrate` 只落 **018** |
| **2** | **重建 Rust 扩展** | 扩展里**才有**两个新入口 | `rs-src/target/release/flashloan_rs.dll`（**2026-11-25 14:32**）**ASCII 符号扫描不含** `rebuild_directional_pools` / `rebuild_closed_loops`；`target/debug` 的**含** |
| **3** | 新二进制 | 内核读闭环行要 `single_tick_capacity_binding_directional_pool_id` | 实盘库 **017** ⇒ **无中间态** |

> **★ 装载路径（决定了第 2 项怎么做）**：`core/rust_extension.py` 的候选顺序是
> `FLASHLOAN_RS_EXTENSION_PATH` → **`rs-src/target/release/flashloan_rs.dll`** → 已安装包。
> ⇒ **重建 `target/release` 即可**，**不必**替换 site-packages 里正在被实盘进程使用的扩展；
> 但**重建动作必须与 018 同窗口** —— 扩展一旦带上新入口，重启后的闭环槽就会要求 018 的表结构。
> **当前实盘装的扩展（site-packages）**同样**不含**两个新入口
> ⇒ 此刻若重启，两个新槽会按**设计**失败（`ENTRY_UNAVAILABLE`，**不降级**）。

---

## 4. 诚实边界

| 项 | 状态 |
|---|---|
| 实盘 **apply** | ❌ **未做** —— 方向层与闭环层**都没有**在实盘库上物化过；全部读数来自**瘦副本** |
| 实盘 **运行期生效** | ❌ **未生效** —— 实盘 runtime **pid 24524** 自 **2026-11-25 17:14:00** 起未重启 |
| 「闭环数量不变」的**副本**证据 | ✅ 有（**174 / 522**），但**实盘**的那一次仍属部署窗口 |
| 遗留 builder | **保留**（`builders.build_closed_loops_anchor` 仍在跑，遗留表仍被别处读），但**不再兜底**：管理器入口失败 ⇒ **整槽失败** |
| `runtime_core.rs` 里 `UPSTREAM_NOT_MATERIALIZED` 的注释 | ⚠️ 写的是「调用方**退回**遗留路径」，而 Python 槽的**实际行为**是**整槽失败**（无降级）。**注释与行为不一致**（行为符合用户口径「要么成功要么失败」）⇒ 已登记，**本任务不改**（`rs-src` 的注释归属 `T-核.02` 线程） |
| `T-核.02` 的改动 | `rs-src/src/worker_pool.rs` 等改动**不属于**本任务（`D-153` §3 明令禁止触碰），本任务**未**碰 |
| 副本与实盘的一致性 | 副本 ＝ 实盘的**表结构全文 ＋ 35 张表的行**；副本上的改动**不回写**实盘 |

---

## 5. 复算方式

```powershell
# 副本（只读实盘库）
python plan/runtime_evidence/tools/clone_direction_layer_slice_20261126.py `
    flashloan/db/local_store_4663.sqlite3 plan/runtime_evidence/kernel01_slice_4663.sqlite3

# 方向层干跑 / 物化（副本）
cargo run --offline -p directional-pool-manager --example materialize_directional_pools_for_chain -- `
    plan/runtime_evidence/kernel01_slice_4663.sqlite3 robinhood 4663 dry-run
cargo run --offline -p directional-pool-manager --example materialize_directional_pools_for_chain -- `
    plan/runtime_evidence/kernel01_slice_4663.sqlite3 robinhood 4663 apply

# inv-9 复测（只读）
python plan/runtime_evidence/tools/audit_directional_invariants_20261125.py `
    --db plan/runtime_evidence/kernel01_slice_4663.sqlite3 --out <json>

# 迁移演练 ＋ 闭环干跑（副本）
cargo run --offline -p manager-db -- preflight --db plan/runtime_evidence/kernel01_slice_4663.sqlite3
cargo run --offline -p manager-db -- migrate   --db plan/runtime_evidence/kernel01_slice_4663.sqlite3
cargo run --offline -p closed-loop-manager --example materialize_closed_loops -- `
    plan/runtime_evidence/kernel01_slice_4663.sqlite3 robinhood 4663 dry-run

# 闭环逐条对照（实盘只读）
python plan/runtime_evidence/tools/compare_clone_live_closed_loop_20261126.py `
    --out plan/runtime_evidence/kernel01_clone_vs_live_20261126.json
```

---
