# `T-方.03`｜方向池的价格排序方向守卫 · 修改范围守护（Scope Guard）

> **本文件是 `T-方.03` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/src/lib.rs` | **仅注释**：投影处写「方向决定目标代币」＋ `assign_ranks` 写「方向永不翻转这个序」 | **是**（**注释**） |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | **三条守卫单测** ＋ 夹具扩展（可设方根价与 tick） | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **产品代码的改动是注释** ⇒ 运行时行为**零变化** ⇒ 不需要重建扩展、不需要重启实盘。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager -p manager-contracts` | 全绿（基线 **27** ＋ 本任务 **3** ＝ **30** passed；`manager-contracts` **17** passed 不变） |

```powershell
cd flashloan/tools
cargo test --offline -p directional-pool-manager -p manager-contracts
```

> **只跑上面这一条。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/rs-src/src/worker_pool.rs` | 内核大对象，**另一线程**在改 |
| `flashloan/rs-src/src/runtime_core.rs` | 同上 |
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；闭环层**只读**方向层发布的 rank，**不参与定价** ⇒ 一字不改 |
| `flashloan/tools/candidate_edge_pool_manager/**` | 属 `M-5`；候选层**不自己排序**（直接读方向层的 rank）⇒ 一字不改 |
| `flashloan/src/core/db/direction_pool_score.py` | 属 `M-3` 附属的**遗留**路径；其**容量封顶**已登记为 R-2，**本轮不改** |
| `flashloan/tools/manager_contracts/**` | 属 `M-3`；契约的**方向语义**只读不改（只**跑**它的测试） |
| 选池条件本身（准入 / 方向价格优势 / 深度 / 费率） | 本任务**只加守卫**，**不改**任何准入判据 |
| 实盘库（`flashloan/db/**`） | 本任务**零**实盘读写 |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 `1 增列该模块，同步改 `D-168-PLAN.md`；
2. **记录原因** —— 在 `D-168-PLAN.md` 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-3` 的注释与单测 | 否 | 纯代码 |
| 跑改动模块的测试 | 否 | 测试库，**不碰实盘** |
| 实盘 **apply** / 重启 runtime | **是** | 本任务**未做**，也**不需要**（无部署闸门） |
| 实盘只读复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |

---

## 6. 范围守护自检

- [x] 本次改动**全部**落在 `1 的模块集合内
      ⇒ 读数：改动文件 = `tools/directional_pool_manager/src/lib.rs`（**仅注释**）
      ＋ `tools/directional_pool_manager/tests/directional_pools.rs`（**三条单测** ＋ 夹具扩展）
      ⇒ **两者都在 `1 表内**；**未**扩范围
- [x] `2 的**全部**命令**已执行**且**全绿**
      ⇒ 读数（2026-11-26）：`directional-pool-manager` **30 passed / 0 failed**
      （基线 **27** ＋ 本任务新增 **3**）；`manager-contracts` **17 passed / 0 failed**（基线不变）
- [x] `3 的禁止触碰项**确未被本任务触碰**
      ⇒ 读数：`closed_loop_manager/`、`candidate_edge_pool_manager/`、
      `rs-src/src/worker_pool.rs`、`rs-src/src/runtime_core.rs`、
      `direction_pool_score.py` **本任务零改动**
      （工作区里这些文件的改动属**其它任务**，本任务未碰）
- [x] **未**改动任何**排序行为**，**未**新增任何阈值或参数
      ⇒ 读数：`directional_pool_manager/src/lib.rs` 的 diff **只有注释行**
      （无一行非注释的新增）；`assign_ranks` 的比较器**逐字未改**
      （变异测试的改动已**撤回**）
- [x] **未**跑过**全量**测试
      ⇒ 读数：只跑了 `2 的**一条**命令；未跑 `tools` 工作区全量、未跑 `rs-src`
