# `T-核.01`｜运行时维护链接线 · 修改范围守护（Scope Guard）

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

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-152-PLAN.md` §2.1；
2. **记录原因** —— 在 `D-152-PLAN.md` §2.2 追加一行说明为什么必须扩；
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
