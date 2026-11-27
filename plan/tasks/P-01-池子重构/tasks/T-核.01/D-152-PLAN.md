# `T-核.01`｜运行时维护链接线 · 开发计划（**独立 · 自包含**）

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
| **1** | 读 `D-154-checklist.md`，确认每条验收项的当前状态 | 全部条目已知 |
| **2** | 盘点维护链现状：worker 槽、依赖图、游标表 | ✅ 拓扑写入 `D-155` §1（槽 **8** 个、依赖图、游标表 `chain_business_initialization`） |
| **3** | `rs-src` 暴露 Rust 入口（方向层按链物化 ＋ 闭环重建） | ✅ `runtime_core.rs:2923` / `:2953` ＋ `lib.rs` 两个 `#[pyfunction]` |
| **4** | `v4_maintenance_workers.py` 新增**方向层 worker 槽** ＋ 依赖图 | ✅ 槽名 `v4_directional_pool_worker`（`:50` / `:479`），名册已增列 |
| **5** | 闭环槽改为调 Rust 入口（**保留**遗留 builder 并**显式标注**） | ✅ `:631` 先调入口；入口失败**整槽失败**（**不降级**）；遗留 builder 标注为遗留路径（`:618-626`） |
| **6** | 跑 `cargo test --offline -p flashloan-rs` ＋ Python 侧既有测试 | ✅ **221 passed / 0 failed** ＋ **160 passed / 0 failed** |
| **7** | 实盘**只读**复核：新槽的读数与既有槽的读数 | ✅ 在**只读瘦副本**上完成（实盘库零写入）：`inv-9` **FAIL → PASS**、闭环 **174 / 522** |
| **8** | 逐条勾选 `D-154-checklist.md` | ✅ **17 / 17**，每条附实测读数 |
| **9** | （扩范围记录）`rs_bridge.py` / `v4_pool_liquidity_worker.py` / `lib.rs` / 测试文件并入 `M-13` | 见 `D-153` §1 的扩范围记录 |

### 2.3 技术文案（`docs/`）

* [`D-155-维护链接线拓扑与实盘读数_20261126.md`](./docs/D-155-维护链接线拓扑与实盘读数_20261126.md)
  —— 槽位拓扑、依赖图、调用链、实盘读数（瘦副本演练）、**部署闸门**三件套。

### 2.4 三条执行纪律

1. **Python 侧不做数据处理**：只允许「调 Rust 入口 ＋ 读回执」两种动作。
2. **不动 `builders.py`**（并行施工保护）：改动只落在**调用方**（运行时维护链）。
3. **实盘一律只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）；维护链**自动**写生产库属正常业务，
   但**手工**触发写操作前先跑 dry-run 并读回执。

---

## 3. 完成定义

* [x] `D-154-checklist.md` 中**全部** `- [ ]` 变为 `- [x]` —— **17 / 17**
* [x] `D-153-SCOPE_GUARD.md` 的**全部**回归命令**全绿** —— **221** ＋ **160** passed
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
