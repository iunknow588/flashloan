# `T-核.02`｜内存大对象方向化 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.02` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时内核 | `flashloan/rs-src/src/sqlite_read.rs` | 方向池装载入口 ＋ 方向层存在性检查 | **是** |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/worker_pool.rs` | 注册表方向化 ＋ 按方向解析选池 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-13` | 运行时内核 | `cargo test --offline -p flashloan-rs` | 全绿（基线 **220** passed） |

```powershell
cd flashloan/rs-src
cargo test --offline -p flashloan-rs
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；闭环层本来就已经只引用有向池，本任务不改闭环口径 |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的投影规则、准入判定、容量口径一字不改 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| `flashloan/src/dex/runtime/v4_maintenance_workers.py` | 属 `T-核.01`（运行时维护链接线），本任务不碰编排 |
| `flashloan/tools/{broadcast_manager,composite_manager,manager_api}/**` | 属其它模块；本任务不改它们的契约 |
| 任何 `chain.json` / `.env` / `.env.example` | 本任务**不引入**任何运行期参数 |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-156-PLAN.md` §2.1；
2. **记录原因** —— 在 `D-156-PLAN.md` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-13` 代码 | 否 | 纯代码 |
| 实盘**只读**复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 写生产库 | **否（本任务不写）** | 本任务**不写**任何表；若后续需要 ⇒ 先扩范围并单独申请 |

---

## 6. 范围守护自检

* [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
* [ ] §2 的**全部**命令**已执行**且**全绿**
* [ ] §3 的禁止触碰项**确未被触碰**
* [ ] `flashloan/rs-src` 的注册表路径上**已无** `v4_exchange_pools` 的引用
* [ ] **未**因本任务跑过**全量**测试
