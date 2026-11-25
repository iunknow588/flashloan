# D-133 `T-池复.01`｜单 tick 门禁的快照一致性判定 · 修改范围守护

> **本文件是 T-池复.01 的范围边界。** 越界 = 缺陷，不是「顺手」。

---

## 1. 允许改动的文件（**逐个列出**）

| 模块 | 文件 | 允许的改动 |
|---|---|---|
| `M-1` | `flashloan/tools/pool_manager/src/parity_state.rs` | `PoolParityState` **加一个字段** ＋ SELECT 增列 ＋ 构造点补齐 ＋ 新增单测 |
| `M-6` | `flashloan/tools/review_manager/src/single_tick.rs` | `SingleTickHopState` **加一个字段** ＋ 漂移判定改口径 ＋ 新增单测 |
| `M-6` | `flashloan/tools/review_manager/src/lib.rs` | `evaluate_final_single_tick_gate` **喂一个新字段** |
| `M-6` | `flashloan/tools/review_manager/src/parity.rs` | 构造点补齐（**不新增逻辑**） |
| `M-6` | `flashloan/tools/review_manager/src/shadow.rs` | 构造点补齐（**不新增逻辑**） |
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs` | 构造点补齐 |

**文档区**（不在上面的表里，但允许写）：
`plan/tasks/P-01-池子重构/tasks/T-池复.01/**` ＋ `plan/runtime_evidence/**` ＋
`plan/tasks/P-01-池子重构/docs/**` ＋ `plan/D-001-TASK_REGISTRY.md`。

---

## 2. 禁止触碰（**任何一条被碰 = 本任务失败**）

| # | 禁止项 | 为什么 |
|---:|---|---|
| 1 | **广播层 `flashloan/tools/broadcast_manager/**`** | 广播准入（`BC-1`..`BC-5`）是**另一个任务**的门禁；本任务只让复核**能走到** `completed`，**放不放行由广播层自己判** |
| 2 | **单 tick 门禁的**逐跳**判定**（`evaluate_hop` / 容量 / tick 区间） | 本任务改的是**「同一市场视图」判定**，**不是**「这条路线能不能走」判定。后者是**放宽门禁**，明令禁止 |
| 3 | **金额 / 容量 / 利润 / 新鲜度（120s）/ 幂等 / 回执关联**的任何口径 | 同上 |
| 4 | **Python 侧任何文件**（`flashloan/src/**`、`flashloan/app_runtime/**`） | 本任务**零** Python 改动；架构守卫应当**不需要**改 |
| 5 | **实盘库写入** | 分析一律 `mode=ro` ＋ `PRAGMA query_only = ON`；本任务**不建表、不改列、不删数据** |
| 6 | `flashloan/tools/{composite_manager,manager_api,candidate_edge_pool_manager,candidate_exchange_pool_manager,directional_pool_manager,closed_loop_manager,manager_db}/**` | 不在本任务模块集合内 |
| 7 | `flashloan/rs-src/src/evidence.rs`、各 `chain.json`、`.env` / `.env.example` | 他人任务的地盘 |
| 8 | `plan/runtime_evidence/tools/normalize_md_crlf_20260724.py`（及**任何**既有归一脚本） | 并行进程改过；本任务用**新**脚本 `normalize_crlf_20261125.py` |

---

## 3. 必须执行的命令（**回归范围 = 改动模块集合**）

```powershell
cargo test --offline -p pool-manager
cargo test --offline -p review-manager
cd flashloan/rs-src; cargo test --offline
```

**未改动模块不重跑。**

---

## 4. 执行纪律

1. **编译 ≠ 测试**：只改接口签名时 `cargo build` 即可暴露类型破坏；
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试
   ⇒ 本任务 `PoolParityState` 加字段 ⇒ `review-manager` ＋ `rs-src` **必须**跑；
3. **实盘只读**：所有对 `flashloan/db/local_store_4663.sqlite3` 的访问一律
   `file:...?mode=ro` ＋ `PRAGMA query_only = ON`；
4. **重启实盘运行时**（重建 DLL ＋ 重启）**必须**在两个 crate 的回归**全绿之后**，
   且**先停后建**（Windows 锁文件：不停止就构建会失败，且会把**旧**二进制重新拉起）。

