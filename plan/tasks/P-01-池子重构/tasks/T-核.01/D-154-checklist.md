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
      > 字段清单见 [`D-155`](./docs/D-155-维护链接线拓扑与实盘读数_20261126.md) §1.2。

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
      > ⇒ **无中间态**。详见 [`D-155`](./docs/D-155-维护链接线拓扑与实盘读数_20261126.md) §3。
