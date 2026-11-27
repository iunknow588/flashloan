# `T-核.03`｜流动性快照写入路径接线 · 开发计划（**独立 · 自包含**）

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

* `D-167-流动性快照写入路径缺失与新鲜度实测_20261126.md` —— 根因链、实测读数、
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

