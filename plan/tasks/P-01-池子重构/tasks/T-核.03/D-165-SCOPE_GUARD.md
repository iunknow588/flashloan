# `T-核.03`｜流动性快照写入路径接线 · 修改范围守护（Scope Guard）

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

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-164-PLAN.md` §2.1；
2. **记录原因** —— 在 `D-164-PLAN.md` §2.2 追加一行说明为什么必须扩；
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

