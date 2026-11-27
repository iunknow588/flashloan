# `T-核.03`｜流动性快照写入路径接线 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-核.03` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 流动性快照写入路径接线 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-核.03` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d164"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/D-164-PLAN.md` -->

# D-164 `T-核.03`｜流动性快照写入路径接线 · 开发计划（**独立 · 自包含**）

> **本目录是 `T-核.03` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-核.03`** |
| **本任务验收编号** | **`AC-核.03`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`核`**（`M-13`） |
| **序号** | 第 `03` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-核.01`（已闭合）、`T-核.02`（**另一线程**，只监测） |

**本任务涉及的模块**：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-13` | 运行时内核与运行时接线（维护链） | `核` |
| `M-1` | 池管理器（链上池状态 / 深度 / 边界） | `方储` / `池复` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-核.03`** |
| **验收编号（纯 ID）** | **`AC-核.03`** |
| **标题** | 流动性快照写入路径接线（把「量到了却写不进去」补上） |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-1`（池管理器）／`M-13`（维护链） |
| **需授权** | 否（本任务只改代码；实盘生效属部署闸门，见 §4） |
| **一句话目标** | 让运行时**真的把量到的流动性写进快照表** —— 把唯一写入方（Rust 池管理器）接进维护链，并让回执报告**真正写进去的行数**。 |

### 1.1 背景与问题（**只读实测 · 2026-11-26**）

```text
快照表 v4_exchange_pool_liquidity_snapshots = 方向层 / 闭环层的「当前价格与深度」的唯一来源。

实测（probe_liquidity_snapshot_freshness_20261126）：
  · 2486 行，最新 liquidity_read_at = 2026-11-25T00:44:27Z（+08 08:44）
  · 行龄分布：1244 行停在 2026-11-17、358 行 11-22、96 行 11-25
  · 运行时 live 视图 manager_market_observation_liquidity_snapshots：最新 2026-11-19T11:30:22Z
  · 池索引 v4_exchange_pools.updated_at 最大 = 2026-11-25T18:36:57Z  ← 索引在前进
  · worker 游标：mode=complete、last_block_number=123234506、last_scanned_at=2026-11-25T18:36:58Z
                                                            ← 声称在最新区块扫完一轮
  · 快照行最大区块 = 120653940                                  ← 却停在 9 天前
```

**根因（源码级）**：快照表的**唯一写入方**是 Rust 池管理器的
`pool_manager::refresh_depth`（`pool_manager/src/lib.rs:1931`）→ `write_depth_snapshot`
（`:2677`）。而 `refresh_depth` 在全仓**只有一个调用点**：
`pool_manager/src/main.rs:536` —— **命令行**。

运行时那条路是：

```text
维护槽 v4_pool_liquidity_worker
  → refresh_v4_live(skip_index=True)         ← Python 侧量到链上状态
  → import_v4_evidence_rows(liquidity_payload=…)
  → rs-src evidence::import_v4_evidence_payloads
      ↳ 回执里明写 "liquidityRowsWritten": 0
        "depthSnapshotWriter": "pool_manager"
        "depthSnapshotNote": "…its single writer is the Rust pool manager (TASK-172 / AC-221)"
      ↳ 只写 v4_exchange_pools（331 行 @ 18:36:57Z）＋ recent_swap 标记
      ↳ 不写快照表（这是 TASK-172 的刻意设计：证据通道写一半列会把
         旧量程的 single-tick 边界和新价格焊在同一行）
  → 快照表零写入 ⇒ 测量被丢弃
```

⇒ **每 tick 都量，每 tick 都丢**。快照表只在运维手工跑池管理器命令时才前进。

**为什么 worker 仍报 `complete`**：
`missing_after` 数的是「**没有裁决**的池」（`has_liquidity IS NULL`），
**不是**「裁决陈旧的池」（`robinhood_v4_idle_maintenance.py:696` →
`_candidate_eligible_liquidity_counts`）。裁决**在**（`has_liquidity ∈ {0,1}`）
⇒ 缺行数 0 ⇒ `mode = "complete"`（`:1682`）。
⇒ **陈旧被当成完整**：下游候选层 / 观测层 / 方向层 / 闭环层 / 复核层 / 广播层
全都相信这批数据是当前的。

### 1.2 选池条件与新鲜度的关系（**本任务的口径边界**）

**用户口径（2026-11-26）**：从备用池倒候选池**是有条件的** —— 必须方向上有价格优势，且满足深度 / 费率等前提条件。**新鲜度不是挑选池子的条件。**

代码里选池条件落在三处，**本任务一处都不改**：

| 层 | 条件 | 代码落点 | 本任务是否改 |
|---|---|---|---|
| 候选层准入 | 深度、费率、代币类别 → `admission_status='admitted'` | `M-5`（`candidate_edge_pool_manager` / `candidate_exchange_pool_manager`） | **否** |
| 方向层取序位 | `pool_status='active'` **且** `directional_output_measured`（方向上量到价格） | `directional_pool_manager/src/lib.rs:1738-1744`（`assign_ranks`） | **否** |
| 组内排序 | `directional_output` 降序 → tick 序 → fee → 方向容量 | 同上 | **否** |
| 容量截断 | `MAX_DIRECTION_CANDIDATE_POOLS`（缺省 **5**，环境变量） | `directional_pool_manager/src/lib.rs:43-81` | **否** |

⇒ **本任务改的是「判据的输入」，不是「判据」**：
方向价格优势与深度本来就是**当场量的**，量完却不落库 ⇒ 判据一直在拿 9 天前的读数算。
补上写入路径后，**判据一个字节没改**，但它读到的是当前链上状态。

**新鲜度在本任务里的唯一身份是「观测量」**：
回执里的 `snapshotFreshness` 与游标里的 `snapshot_read_at` / `snapshot_rows_written`
**只用于报告**，`mode` 判据**一字未改**（仍按裁决存在与否判 `complete`）。

### 1.3 不做什么（**范围外**）

* **不**把新鲜度变成选池条件或 `complete` 的判据（见 §1.2；切换判据是**部署闸门之后**的独立一步，见 §3.2）。
* **不**改方向层 / 闭环层 / 候选层的任何准入、排序、容量、阈值口径。
* **不**碰 `flashloan/rs-src/src/worker_pool.rs`（内核大对象，**另一线程**在改）。
* **不**做跨月统计、降级优雅之类的低概率路径（按用户口径：功能点先留占位）。
* **不**动实盘运行中的进程（本轮只改代码 ＋ 跑回归；上线属部署闸门）。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/pool_depth.rs`（**新增**） | 新增 `refresh_pool_liquidity` —— 调用池管理器量测并落库 |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/lib.rs` | 注册 `#[pyfunction] refresh_v4_pool_liquidity`（**加法式**，不动他人改动） |
| `M-13` | 运行时内核与接线 | `flashloan/src/core/rs_bridge.py` | 新增 `refresh_v4_pool_liquidity` 桥（只调入口 ＋ 读回执） |
| `M-13` | 运行时内核与接线 | `flashloan/src/dex/runtime/robinhood_v4_idle_maintenance.py` | 维护槽在量测后调用池管理器入口；失败 ⇒ **整槽失败**（不降级） |
| `M-13` | 运行时内核与接线 | `flashloan/src/core/db/v4_pool_liquidity_worker.py` | 游标增列 `snapshot_read_at` / `snapshot_rows_written`（**加法式**） |
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/lib.rs` | `write_depth_snapshot` 返回**真正改动的行数**（原为 `()`）；回执增列 `rowsWithoutSnapshotCount` |

> **`M-1` 的改动边界**：只改**私有**函数 `write_depth_snapshot` 的返回类型与回执字段，
> **不改**任何公开接口（`DepthOptions` / `DepthScope` / `refresh_depth` 签名**一字未改**）
> ⇒ 不触发调用方加跑（`D-014` §0.3 纪律 2）。

### 2.2 执行步骤

| 步 | 动作 | 完成判据 | 状态 |
|---:|---|---|---|
| **1** | 只读探针：两张快照表的新鲜度 / 行龄 / 区块 ＋ worker 游标 | 读数可复算 | ✅ **完成**（`liquidity_snapshot_freshness_20261126.json`） |
| **2** | 定位写入路径归属：快照表唯一写入方 ＋ 它的全部调用点 | 根因到源码行 | ✅ **完成**（`refresh_depth` 只被 CLI 调用） |
| **3** | 新增 Rust 入口 `pool_depth::refresh_pool_liquidity` | 网络与链 id 不匹配 ⇒ 拒绝写入 | ✅ **完成**（`pool_depth.rs` ＋ 1 条单测） |
| **4** | 池管理器回执改为**诚实计数**（真正改动的行数 ＋ 无行可写的池数） | 「写了」不再等于「试过」 | ✅ **完成**（`write_depth_snapshot` → `Result<usize>`） |
| **5** | rs-src 注册 `#[pyfunction]`，`rs_bridge` 加桥 | Python 侧零算法 | ✅ **完成**（`lib.rs` 加法式 ＋ `rs_bridge.py`） |
| **6** | 维护槽调用池管理器入口；失败 ⇒ 整槽失败（**不降级**） | 失败时 `mode=error` ＋ 游标带错 | ✅ **完成**（`robinhood_v4_idle_maintenance.py:1490`） |
| **7** | 回执 / 游标**加法式**发布新鲜度（**不改** `mode` 判据） | 新鲜度只做观测量 | ✅ **完成**（`snapshotFreshness` / `snapshot_read_at`） |
| **8** | 跑改动模块回归 | 全绿 | ✅ **完成**（见 §2.3） |
| **9** | 部署闸门：重建 Rust 扩展 ＋ 重启实盘 runtime ＋ 实盘复核 | 快照行前进 ＋ `inv-9` 不退化 | ⏸ **未做**（见 §4） |
| **10** | 判据切换：`complete` 从「裁决存在」改成「裁决新鲜」 | —— | ⏸ **后置**（见 §3.2） |

### 2.3 回归（**只跑改动模块**）

| 模块 | 命令 | 读数（2026-11-26） |
|---|---|---|
| `M-1` | `cargo test --offline -p pool-manager` | **167 passed / 0 failed**（基线 **167** 不变） |
| `M-13` | `cargo test --offline`（`flashloan/rs-src`） | 见 `D-166` 读数 |
| `M-13` | `pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py` | **19 passed / 0 failed**（基线不变） |
| `M-13` | `pytest flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py`（**新增**） | **3 passed / 0 failed** |

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline

cd ../..
pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py `
       flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py -q
```

### 2.4 技术文案（`docs/`）

* `D-167` —— 根因链、实测读数、
  选池条件与新鲜度的边界、部署闸门与判据切换的后果预告。

---

## 3. 完成定义

### 3.1 本任务（`T-核.03`）的完成定义

* [x] 写入路径的根因定位到**源码行**，且有只读探针可复算
* [x] 运行时**能**把量到的流动性写进快照表（Rust 入口 ＋ 维护槽调用 ＋ 桥）
* [x] 池管理器回执**诚实**：报告真正改动的行数，而非尝试次数
* [x] 写入失败 ⇒ **整槽失败**（不降级、不静默吞掉）
* [x] 新鲜度**只做观测量**：`mode` 判据与选池条件**一字未改**
* [x] 改动模块回归全绿（`pool-manager` **167** ／ `rs-src` 见 `D-166` ／ pytest **19 ＋ 3**）
* [x] **未**触碰 `worker_pool.rs`（另一线程）、方向层 / 闭环层 / 候选层的任何口径

### 3.2 后置项（**不属于本任务**，如实登记）

| 项 | 为什么后置 | 后果预告 |
|---|---|---|
| **部署闸门**（重建 Rust 扩展 ＋ 重启实盘 runtime ＋ 实盘复核） | 实盘扩展 `flashloan_rs.dll` 由 `rs-src` 构建，而 `rs-src/src/worker_pool.rs` **另一线程**在改 ⇒ 此刻重建会把**未经其回归**的代码带上线 | 未部署前，快照表**仍旧不前进**；本任务的修复**只在代码里** |
| **`mode` 判据切换**（`complete` ⇒ 裁决新鲜） | 必须在写入路径**实盘验证**之后做 | 若在写入路径未生效时切换，`worker_complete()` 会因游标带错而判 not-ready ⇒ **运行时会安静下来**（候选 / 观测 / 闭环槽全部不发布）⇒ **必须后置** |

---

## 4. 部署闸门（**下一步的唯一步骤**）

```text
① 等 T-核.02（内核大对象，另一线程）的 rs-src 改动收敛并回归
② cargo build --offline --release（flashloan/rs-src）⇒ 产出新的 flashloan_rs.dll
③ 备份实盘快照表行级快照（2486 行快照表 ＋ 692 行方向层）
④ 重启实盘 runtime（pid 由守护脚本接管）
⑤ 只读复核：快照表 liquidity_read_at 是否前进 ＋ 行龄分布是否收敛
⑥ inv-9（方向层投影不变式）复核：方向行与基础池行仍 0 差异
```

---

## ③ 修改范围守护

<a id="d165"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/D-165-SCOPE_GUARD.md` -->

# D-165 `T-核.03`｜流动性快照写入路径接线 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.03` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/pool_depth.rs`（**新增**） | 池管理器深度刷新的 Rust 入口 ＋ 网络 / 链 id 一致性拒绝 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/lib.rs` | `mod pool_depth;` ＋ `#[pyfunction] refresh_v4_pool_liquidity` ＋ 注册 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/src/core/rs_bridge.py` | `refresh_v4_pool_liquidity` 桥 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/src/dex/runtime/robinhood_v4_idle_maintenance.py` | 维护槽调用入口 ＋ 新鲜度读数 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/src/core/db/v4_pool_liquidity_worker.py` | 游标增列 `snapshot_read_at` / `snapshot_rows_written` | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py`（**新增**） | 本任务的单测 | **是** |
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/lib.rs` | `write_depth_snapshot` 返回真正改动的行数；回执增列 `rowsWithoutSnapshotCount` | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-1` | 池管理器 | `cargo test --offline -p pool-manager` | 全绿（基线 **167** passed） |
| `M-13` | 运行时内核与接线（Rust） | `cargo test --offline`（`flashloan/rs-src`） | 全绿（基线 **221** passed） |
| `M-13` | 运行时内核与接线（Python） | `pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py` | 全绿 |

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline

cd ../..
pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py `
       flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/rs-src/src/worker_pool.rs` | 内核大对象，**另一线程**在改（`T-核.02`） |
| `flashloan/rs-src/src/runtime_core.rs` | 同上；本任务在 `rs-src` 里**另起新文件**避开它 |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的准入 / 序位 / 容量口径**一字不改** |
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；闭环层的折叠与容量口径**一字不改** |
| `flashloan/tools/candidate_*_pool_manager/**` | 属 `M-5`；候选层准入口径**一字不改** |
| `flashloan/tools/manager_db/**` | 属 `M-10`；本任务**不涉及迁移** |
| 选池条件本身（准入 / 方向价格优势 / 深度 / 费率） | 新鲜度**不是**选池条件（用户口径 2026-11-26）⇒ 不得以任何形式进入判据 |
| `mode` 判据（`complete` / `scanning`） | 本轮**只加法式**发布新鲜度读数，判据切换留待部署闸门之后 |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-164` §2.1；
2. **记录原因** —— 在 `D-164` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-13` / `M-1` 代码 | 否 | 纯代码 |
| 跑改动模块的测试 | 否 | 测试库，**不碰实盘** |
| 实盘 **apply**（写快照表） | **是** | 本任务**未做**；属部署闸门（用户口径「所有需授权操作默认授权通过」） |
| 实盘只读复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 重启实盘 runtime | **是** | 属部署闸门；本任务**未做** |

---

## 6. 范围守护自检

* [x] 本次改动**全部**落在 §1 的模块集合内
      ⇒ 读数：改动文件 = `rs-src/src/pool_depth.rs`（新）、`rs-src/src/lib.rs`、
      `src/core/rs_bridge.py`、`src/dex/runtime/robinhood_v4_idle_maintenance.py`、
      `src/core/db/v4_pool_liquidity_worker.py`、
      `app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py`（新）、
      `tools/pool_manager/src/lib.rs` ⇒ **七者都在 §1 表内**；**未**扩范围
* [x] §2 的**全部**命令**已执行**且**全绿**
      ⇒ 读数：`pool-manager` **167 passed / 0 failed**；`flashloan-rs` **222 passed / 0 failed**
      （基线 **221** ＋ 本任务新增 **1**）；pytest **19 ＋ 3 passed / 0 failed**
* [x] §3 的禁止触碰项**确未被本任务触碰**
      ⇒ 读数：`worker_pool.rs` / `runtime_core.rs` **本任务零改动**（`rs-src` 的改动落在
      `pool_depth.rs` 新文件 ＋ `lib.rs` 的加法式注册）；方向层 / 闭环层 / 候选层 /
      `manager_db` **零改动**（`git status` 里这些文件的改动属其它任务）
* [x] **未**把新鲜度接入任何选池判据或 `mode` 判据
      ⇒ 读数：`snapshotFreshness` / `snapshot_read_at` / `snapshot_rows_written`
      **只出现在回执与游标里**；`mode = "complete" if missing_after == 0 and wrapped`
      （`robinhood_v4_idle_maintenance.py:1682`）**一字未改**；
      `assign_ranks`（`directional_pool_manager/src/lib.rs:1738-1744`）**零改动**
* [x] **未**因本任务跑过**全量**测试
      ⇒ 读数：只跑了 §2 的三条命令；`rs-src` 全量是**该 crate 自身**的测试
      （改动模块集合包含 `rs-src`），不是全工作区

---

## ④ 测试目标 check list

<a id="d166"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/D-166-checklist.md` -->

# D-166 `T-核.03`｜流动性快照写入路径接线 · 验收清单

> **本文件是 `T-核.03` 的全部验收框。** 编号 `AC-核.03` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-1` 现有单测全绿

**本轮状态（2026-11-26）**：**16** 框**全部**闭合（每条附实测读数）。
**实盘侧**：**未做**任何实盘写入；只读探针读数见 §1。
**如实登记**：修复**只在代码里** —— 实盘扩展未重建、runtime 未重启（部署闸门，见 `D-164` §4）；
`mode` 判据**未切换**（新鲜度只做观测量）。

---

## 1. 根因与定位

- [x] 快照表的新鲜度已量化（**只读**）

      > **读数**（`plan/runtime_evidence/liquidity_snapshot_freshness_20261126.json`，
      > 探针 `probe_liquidity_snapshot_freshness_20261126.py`，`mode=ro` ＋ `query_only`）：
      > `v4_exchange_pool_liquidity_snapshots` **2486** 行 / **2486** 个池；
      > 最新 `liquidity_read_at` = **2026-11-25T00:44:27.222895100+00:00**（+08 08:44）；
      > 行龄分布 `2026-11-17: 1244`、`11-22: 358`、`11-25: 96`（其余为 10-16 的 776 行）；
      > `max(liquidity_block_number)` = **120653940**。

- [x] 「陈旧被当成完整」已定位到**源码行**

      > **读数**：`robinhood_v4_idle_maintenance.py:1682`
      > `mode = "complete" if missing_after == 0 and wrapped else "scanning"`；
      > 而 `missing_after` = `_candidate_eligible_missing_liquidity_count`（`:696`）
      > → `_candidate_eligible_liquidity_counts` ⇒ `eligibleUnknownCount`
      > = 「`l.pool_id IS NULL` **或** `l.has_liquidity IS NULL`」的池数
      > ⇒ 数的是**裁决是否存在**，**不是**裁决有多旧。
      > 实测佐证：合格池（`fee ≤ 30000`、无 hook、已知代币）**792** 个，
      > `no_row = 0` / `null_verdict = 0` ⇒ `missing_after = 0` ⇒ `complete`；
      > 而同期快照行最新只到 9 天前 ⇒ **判据成立、事实陈旧**。

- [x] 快照表的**唯一写入方**与其**全部调用点**已定位

      > **读数**：唯一写入方 = `pool_manager::refresh_depth`（`tools/pool_manager/src/lib.rs:1931`）
      > → `write_depth_snapshot`（`:2677`，**UPDATE** 语句）；
      > 全仓 `refresh_depth` 的调用点**只有一个**：`tools/pool_manager/src/main.rs:536`
      > （**命令行** `depth --apply`）⇒ **运行时没有任何一条路能写这张表**。
      > 证据通道**刻意**不写：`rs-src/src/evidence.rs:1012` 回执明写
      > `liquidityRowsWritten: 0`、`depthSnapshotWriter: pool_manager`
      > （TASK-172 / AC-221：证据通道写一半列会把旧量程的 single-tick 边界与新价格焊在同一行）。

- [x] 「量到了却写不进去」的分叉点已定位

      > **读数**：同一 tick 内 `v4_exchange_pools` 前进 **331** 行（`updated_at` = **2026-11-25T18:36:57Z**），
      > 而快照表零写入 ⇒ **证据通道写了池索引、没写快照表**；
      > worker 游标 `last_scanned_at` = **2026-11-25T18:36:58Z**、`last_block_number` = **123234506**
      > （比快照行的最大区块 **120653940** 新 **2,580,566** 个区块）⇒ **worker 读到了最新区块，
      > 但读到的状态没有落库**。

---

## 2. 实施（写入路径）

- [x] 新增 Rust 入口：池管理器深度刷新

      > **读数**：`flashloan/rs-src/src/pool_depth.rs`（**新文件**）
      > `refresh_pool_liquidity(...)` → `pool_manager::refresh_depth(...)`；
      > `resume: false`（resume 会跳过「已有时间戳」的池，而 tick 的目的正是把时间戳推前）、
      > `max_pools: None`（调用方点名了池，池管理器不自己挑）、
      > 网络与链 id 不一致 ⇒ **拒绝写入**（单测 `a_network_that_does_not_match_the_chain_id_is_refused`）。

- [x] 入口已注册到扩展，Python 侧只「调入口 ＋ 读回执」

      > **读数**：`rs-src/src/lib.rs` 注册 `#[pyfunction] refresh_v4_pool_liquidity`（**加法式**）；
      > `src/core/rs_bridge.py` 新增同名桥（只做 JSON 编解码 ＋ 回执校验）；
      > Python 侧**零算法**：不量测、不算容量、不判准入。

- [x] 维护槽已接线，且**失败即整槽失败**（不降级）

      > **读数**：`robinhood_v4_idle_maintenance.py:1490` 调用入口；
      > 入口不可用（`rust_available()` 为假）⇒ `errorCode = BRIDGE_UNAVAILABLE`；
      > 入口拒绝 ⇒ `errorCode = POOL_MANAGER_REFUSED`；
      > 两者都 ⇒ `phase = snapshot_materialization_failed`、游标 `mode = error` ＋ 带错，
      > **不**回退到旧测量、**不**继续发布下游（单测 `a_refused_pool_manager_write_fails_the_tick`、
      > `a_missing_rust_extension_fails_the_tick`）。

- [x] 池管理器回执改为**诚实计数**

      > **读数**：`write_depth_snapshot` 由 `Result<(), String>` 改为 `Result<usize, String>`，
      > 返回 `connection.execute` **真正改动的行数**；回执新增 `rowsWithoutSnapshotCount`
      > （有测量、但表里没有行可写的池数）。
      > 改前 `written` 计的是**尝试次数**（`written += 1`）⇒ 一个不存在的行也算「写了」；
      > 改后才与「行是否真的动了」一致。

- [x] **未**改任何选池条件，也**未**把新鲜度接进判据

      > **读数**：`mode` 判据（`:1682`）**一字未改**；
      > 方向层 `assign_ranks`（`directional_pool_manager/src/lib.rs:1738-1744`）**零改动**；
      > 候选层准入（`M-5`）**零改动**；闭环层（`M-4`）**零改动**。
      > 新鲜度的落点**只有**回执的 `snapshotFreshness` 与游标的 `snapshot_read_at` /
      > `snapshot_rows_written` ⇒ **纯观测量**。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p pool-manager` 全绿

      > **读数（2026-11-26）**：**167 passed / 0 failed / 0 ignored**（基线 **167** 不变）。

- [x] `cargo test --offline`（`flashloan/rs-src`）全绿

      > **读数（2026-11-26）**：**222 passed / 0 failed**（基线 **221** ＋ 本任务新增 **1**：
      > `pool_depth::tests::a_network_that_does_not_match_the_chain_id_is_refused`）。

- [x] Python 维护槽回归全绿

      > **读数（2026-11-26）**：`test_v4_maintenance_workers.py` **19 passed / 0 failed**（基线不变）；
      > `test_v4_pool_liquidity_snapshot_writer.py`（**新增**）**3 passed / 0 failed**。

---

## 4. 实盘（**只读**）

- [x] 实盘读数由**只读**探针取得，实盘库**零写入**

      > **读数**：探针 `probe_liquidity_snapshot_freshness_20261126.py` 以
      > `file:...?mode=ro` 打开 ＋ `PRAGMA query_only = ON`；
      > 本任务**未**对实盘库执行任何写操作；
      > 实盘 runtime（pid **24524**，2026-11-25 17:14 启动）**未重启、未受影响**。

---

## 5. 诚实边界

- [x] 修复**尚未在实盘生效**，如实登记

      > **读数**：实盘扩展 `flashloan/rs-src/target/release/flashloan_rs.dll`（2026-11-25 14:32）
      > **不含**新入口 `refresh_v4_pool_liquidity` ⇒ 实盘维护槽**仍不写快照表**；
      > 重建 Rust 扩展 ＋ 重启 runtime 属**部署闸门**（`D-164` §4），
      > 前置是 `T-核.02`（内核大对象，**另一线程**）的 `rs-src` 改动收敛并回归
      > ⇒ 此刻重建会把**未经其回归**的代码带上线 ⇒ **本轮不做**。

- [x] `mode` 判据**未切换**，如实登记后果

      > **读数**：`worker_complete()`（`v4_maintenance_workers.py:196-212`）= mode ∈ {complete, …}
      > 且有 `refreshed_at` 且**无 error**；若把陈旧写进游标 `error`，下游候选 / 观测 / 闭环槽
      > 会判 not-ready ⇒ **运行时会安静下来** ⇒ 判据切换**必须**在写入路径实盘验证之后。

- [x] 本任务**未**改动内核大对象（另一线程的地盘）

      > **读数**：`rs-src/src/worker_pool.rs` 与 `rs-src/src/runtime_core.rs` 本任务**零改动**；
      > `rs-src` 的改动落在**新文件** `pool_depth.rs` ＋ `lib.rs` 的加法式注册
      > ⇒ 与另一线程的 `lib.rs` 改动**不重叠**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/README.md` -->

# `T-核.03`｜流动性快照写入路径接线

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.03`（纯 ID）　**验收**：`AC-核.03`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-核.03` / `AC-核.03` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时维护链）＋ `M-1`（池管理器）），该索引号段内第 `03` 个。

**改动模块（= 回归范围）**：`M-13` ＋ `M-1`

**前置模块**：`M-1`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-164` | 本任务的开发计划与回归范围（**自包含**） |
| `D-165` | 本任务的修改范围守护与逐模块回归命令 |
| `D-166` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（根因链 / 实测读数 / 边界） |

## 一句话

> 快照表 `v4_exchange_pool_liquidity_snapshots` 的**唯一写入方**是 Rust 池管理器，
> 而它**只能通过命令行调用** ⇒ 维护槽每 tick 量到的链上状态**被测量、被丢弃**；
> worker 却因「裁决都在」报 `complete` ⇒ **陈旧被当成完整**。
> 本任务把池管理器接进维护链，并让回执报告**真正写进去的行数**。

**当前状态（2026-11-26）**：**16 / 16** 项闭合。
修复**已在代码里落地**：`rs-src/src/pool_depth.rs`（新入口）＋ 维护槽调用 ＋ 桥 ＋
池管理器回执诚实计数；回归 `pool-manager` **167** ／ `flashloan-rs` **222** ／ pytest **19 ＋ 3**。
**如实登记**：**实盘尚未生效**（扩展未重建、runtime 未重启 ⇒ 属部署闸门）；
`mode` 判据**未切换** —— 新鲜度在本任务里**只做观测量**，
**不是选池条件**（选池条件是方向价格优势 ＋ 深度 ＋ 费率，本任务**一字未改**）。

## 实盘读数（只读 · 2026-11-26）

快照表 **2486** 行，最新 `liquidity_read_at` = **2026-11-25T00:44:27Z**（+08 08:44）；
行龄 `11-17: 1244` / `11-22: 358` / `11-25: 96`；
worker 游标 `mode = complete`、`last_block_number = 123234506`（快照行的最大区块只有 **120653940**）。
证据：`plan/runtime_evidence/liquidity_snapshot_freshness_20261126.json`。

## 独立回归

```powershell
cd flashloan/tools  ; cargo test --offline -p pool-manager
cd ../rs-src       ; cargo test --offline
cd ../..           ; pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py `
                            flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py -q
```

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d167"></a>

### `D-167-流动性快照写入路径缺失与新鲜度实测_20261126.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/docs/D-167-流动性快照写入路径缺失与新鲜度实测_20261126.md` -->

# D-167 流动性快照写入路径缺失与新鲜度实测（2026-11-26）

> 本文是 `T-核.03` 的独立技术文案：**根因链**、**实测读数**、**口径边界**、**闸门**。
> 所有读数都可用 `plan/runtime_evidence/tools/probe_liquidity_snapshot_freshness_20261126.py` 复算。

---

## 1. 这张表是什么

`v4_exchange_pool_liquidity_snapshots` 是**方向层与闭环层的事实来源**：

```text
链上 extsload（slot0 + liquidity）
  → 池管理器量测 ⇒ 单 tick 容量（single_tick_max_token0_raw / _token1_raw）
  → 快照行（一行 = 一个链上时刻：价格、tick、流动性、容量、深度带同一个语句写入）
  → 方向层投影（方向池的快照列逐列来自这张表）
  → 闭环层折叠（容量 ＋ 价格同源，见 T-闭.02）
  → 复核层 / 广播层
```

**这张表的行就是「当前价格与深度」这句话的全部依据。** 它不前进，后面全部都是旧读数。

---

## 2. 实测（只读 · `mode=ro` ＋ `PRAGMA query_only = ON`）

证据文件：`plan/runtime_evidence/liquidity_snapshot_freshness_20261126.json`

### 2.1 两张快照表

| 表 | 行数 | 池数 | 最新 `liquidity_read_at` | 最大区块 |
|---|---:|---:|---|---:|
| `v4_exchange_pool_liquidity_snapshots` | **2486** | 2486 | **2026-11-25T00:44:27.222895100+00:00**（+08 08:44） | **120653940** |
| `manager_market_observation_liquidity_snapshots`（运行时 live 视图） | **2710** | 429 | **2026-11-19T11:30:22.579462Z** | **118183971** |

行龄分布（前表）：`2026-10-16: 776`、`11-17: 1244`、`11-18: 1`、`11-19: 1`、
`11-21: 10`、`11-22: 358`、`11-25: 96`。

### 2.2 池索引与 worker 游标

| 对象 | 读数 |
|---|---|
| `v4_exchange_pools.updated_at` 最大 | **2026-11-25T18:36:57Z**（331 行同一时刻 ⇒ 索引**在前进**） |
| worker 游标 `v4_pool_liquidity_worker` | `mode = complete`、`pool_count = 363`、`scanned = 363`、`last_block_number = **123234506**`、`last_scanned_at = 2026-11-25T18:36:58Z`、`error = null` |
| worker 游标 `v4_live_refresh` | 最后刷新 **2026-11-09T03:09:15Z**（17 天前）、`index_to_block = 109733549` |

⇒ **worker 声称在区块 123,234,506 扫完一轮，快照行只到区块 120,653,940**（差 **2,580,566** 个区块）。
⇒ 同时：`v4_exchange_pools` 在同一个 tick 里前进了，**快照表没有**。
⇒ **分叉点就在「池索引写、快照表不写」。**

### 2.3 为什么 worker 仍报 `complete`

```text
robinhood_v4_idle_maintenance.py:1682
    mode = "complete" if missing_after == 0 and wrapped else "scanning"

missing_after = _candidate_eligible_missing_liquidity_count(...)      # :696
  → _candidate_eligible_liquidity_counts(...)
    → eligibleUnknownCount = COUNT(l.pool_id IS NULL OR l.has_liquidity IS NULL)
```

**它数的是「没有裁决」，不是「裁决陈旧」。** 实测复核：

| 口径 | 读数 |
|---|---|
| 合格池（`fee > 0 且 ≤ 30000`、无 hook、已知代币） | **792** |
| 其中无快照行 | **0** |
| 其中 `has_liquidity IS NULL` | **0** |
| ⇒ `missing_after` | **0** ⇒ `mode = complete` |
| 同期快照行最新读数 | **9 天前** |

⇒ **判据成立、事实陈旧**。下游全部相信这批数据是当前的。

---

## 3. 根因链（源码级）

### 3.1 唯一写入方只挂在命令行上

```text
pool_manager::refresh_depth                 tools/pool_manager/src/lib.rs:1931
  └─ write_depth_snapshot                   tools/pool_manager/src/lib.rs:2677   （UPDATE 语句）

全仓 refresh_depth 的调用点：**只有一个**
  tools/pool_manager/src/main.rs:536         （命令行 depth --apply）
```

⇒ **运行时没有任何一条路能写这张表。**

### 3.2 运行时那条路为什么写不进去

```text
v4_pool_liquidity_worker
  → refresh_v4_live(skip_index=True)          Python 侧量到链上状态（量得到）
  → import_v4_evidence_rows(liquidity_payload=…)
  → rs-src evidence::import_v4_evidence_payloads
      回执原文：liquidityRowsWritten: 0
                depthSnapshotWriter: pool_manager
                depthSnapshotNote: the depth snapshot table is not written by the
                                   evidence channel; its single writer is the Rust
                                   pool manager (TASK-172 / AC-221)
      实际写的是：v4_exchange_pools（331 行 @ 18:36:57Z）＋ v4_pool_recent_swap_activity
```

**这个「不写」是刻意的、也是对的**：证据通道以前只写价格列、不写 single-tick 边界，
于是**旧量程的边界**和**新价格**被焊在同一行，边界相对自己的价格永远是旧的。
所以 TASK-172 把证据通道降级成「不写」，把写权限**全部**交给池管理器。

**问题在于：交给池管理器之后，没有人把池管理器接上。**
⇒ 每 tick 都量，每 tick 都丢。

### 3.3 池管理器的写入是 UPDATE（新池的结构性缺口）

`write_depth_snapshot` 是 `UPDATE` ⇒ **池没有快照行时改 0 行**。
实测：全部 64 个「有池行、无快照行」的池 `fee = 950000`（超出 `MAX_CANDIDATE_FIXED_FEE = 30000`）
⇒ **当前合格池全部有行**（792 个），这个缺口**当前不发作**；
但**将来新出现的合格池**若先被索引、后被量测，就会落进「有池行、无快照行」⇒ 永远测不到。
⇒ 登记为遗留增强项 **R-1**（见 §6）。

---

## 4. 口径边界：新鲜度**不是**选池条件

**用户口径（2026-11-26）**：从备用池倒候选池**是有条件的** —— 必须方向上有价格优势，
且满足深度 / 费率等前提条件。**新鲜度不是挑选池子的条件。**

代码里的选池条件（**本任务一处未改**）：

| 层 | 条件 | 落点 |
|---|---|---|
| 候选层准入 | 深度、费率、代币类别 → `admission_status = 'admitted'` | `M-5`：`candidate_edge_pool_manager` / `candidate_exchange_pool_manager` |
| 方向层取序位 | `pool_status = 'active'` **且** `directional_output_measured`（方向上量到价格） | `directional_pool_manager/src/lib.rs:1738-1744`（`assign_ranks`） |
| 组内排序 | `directional_output` 降序 → tick 序 → fee → 方向容量 | 同上 |
| 容量截断 | `MAX_DIRECTION_CANDIDATE_POOLS`（缺省 **5**，环境变量 `FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`） | `directional_pool_manager/src/lib.rs:43-81` |

⇒ **本任务改的是判据的输入，不是判据。**
方向价格优势与深度本来就是**当场量**的；量完不落库 ⇒ 判据一直在拿 9 天前的读数算。
补上写入路径后，判据**一个字节没改**，但它读到的是当前链上状态。

**新鲜度在本任务里的唯一身份是「观测量」**：

| 落点 | 内容 | 用途 |
|---|---|---|
| 回执 `snapshotFreshness` | `newestReadAt` / `newestBlockNumber` / `rowCount` / `ageSeconds` | **报告** |
| 游标 `snapshot_read_at` / `snapshot_rows_written` | 最新读数时刻 ＋ 上一 tick 真正改动的行数 | **报告** |
| `mode` 判据 | `missing_after == 0 and wrapped` | **一字未改** |
| 选池判据 | 见上表 | **一字未改** |

---

## 5. 落地（代码已改，实盘未生效）

```text
flashloan/rs-src/src/pool_depth.rs（新增）
    refresh_pool_liquidity(...)  →  pool_manager::refresh_depth(...)
        · 网络与链 id 不一致 ⇒ 拒绝写入
        · resume = false（resume 会跳过「已有时间戳」的池，而 tick 的目的正是推前时间戳）
        · max_pools = None（调用方点名了池，池管理器不自己挑）
flashloan/rs-src/src/lib.rs
    #[pyfunction] refresh_v4_pool_liquidity ＋ 注册（加法式，避开另一线程的 worker_pool.rs / runtime_core.rs）
flashloan/src/core/rs_bridge.py
    refresh_v4_pool_liquidity 桥（只调入口 ＋ 读回执）
flashloan/src/dex/runtime/robinhood_v4_idle_maintenance.py
    维护槽在量测后调用入口；入口不可用 / 拒绝 ⇒ 整槽失败（mode = error，不降级）
    回执与游标加法式发布新鲜度
flashloan/tools/pool_manager/src/lib.rs
    write_depth_snapshot → Result<usize>（真正改动的行数）
    回执新增 rowsWithoutSnapshotCount（有测量但无行可写的池数）
```

### 5.1 为什么池管理器的回执必须是诚实的

改前 `written += 1` 计的是**尝试次数** ⇒ 一个不存在的行也算「写了」；
改后 `written` = `connection.execute` **真正改动的行数**。
没有这个数，回执就分不出「量到了并落库了」与「量到了但没行可写」——
而这正是本次缺陷的形状（表不前进，量却一直在做）。

---

## 6. 遗留增强项（**本轮不做**，功能点先留占位）

| 编号 | 事项 | 为什么留到后面 | 触发条件 |
|---|---|---|---|
| **R-1** | 池管理器对**没有快照行**的池做 UPSERT（当前是 UPDATE ⇒ 改 0 行） | 当前 792 个合格池**全部**有行 ⇒ 缺口不发作；改动会碰到外键与写入顺序，**收益在缺陷真正发作时**才出现 | 新合格池出现且先被索引后被量测 |
| **R-2** | `has_liquidity = 0`（显式零流动性）的 **495** 个池被增量扫描**排除**，其价格与 tick **永久冻结** | 这是**既有设计**（「显式零流动性对增量扫描是终态，全量回扫负责重新评估」），改动它等于改扫池范围 | 池从零流动性变成有流动性时，只有全量回扫能发现 |
| **R-3** | Python 侧量测与 Rust 侧量测**两次**读链 | 本轮保留 Python 侧那次是因为它还负责写池索引与 recent_swap 标记；合并需要把索引写入也搬进 Rust | 链路速率压力成为问题时 |

---

## 7. 闸门与后果预告

| 闸门 | 内容 | 后果 |
|---|---|---|
| **部署闸门** | 重建 `flashloan_rs.dll` ＋ 重启实盘 runtime ＋ 只读复核 | 前置是 `T-核.02`（内核大对象，**另一线程**）的 `rs-src` 改动收敛并回归 ⇒ 此刻重建会把**未经其回归**的代码带上线 ⇒ **本轮不做** |
| **判据切换** | `mode` 从「裁决存在」改成「裁决新鲜」 | **必须**在写入路径实盘验证之后：`worker_complete()` 要求游标**无 error**，把陈旧写进 `error` ⇒ 下游候选 / 观测 / 闭环槽全部判 not-ready ⇒ **运行时会安静下来** |

---

## 8. 与其它任务的关系

| 任务 | 关系 |
|---|---|
| `T-核.01`（维护链接线） | 本任务是它的**上游补洞**：维护链里有了槽，但槽没把数据写下去 |
| `T-核.02`（内核大对象） | **另一线程**；本任务**不碰** `worker_pool.rs` / `runtime_core.rs`，且部署闸门等它收敛 |
| `T-闭.02`（估值与折叠同源） | 它的快照列同样来自这张表 ⇒ 本任务修好写入后，它的「同源」才真正是**当前**的同源 |
| `T-闭.03`（基础池内化） | 同批部署闸门（迁移 **018** ＋ 新二进制）⇒ 重建扩展时可一并满足 |
| `T-池复.01`（单 tick 门禁快照一致性） | 它的漂移判定读的是**批次**（`materialization_batch`）⇒ 本任务让批次继续前进 ⇒ 它的「`completed` 恒为 0」很可能是同一根因的下游症状 |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.03/docs/README.md` -->

# `T-核.03`｜技术文案

| 文件 | 内容 |
|---|---|
| `D-167` | 根因链（源码级）、只读实测读数、选池条件与新鲜度的边界、部署闸门与判据切换的后果预告 |

---
