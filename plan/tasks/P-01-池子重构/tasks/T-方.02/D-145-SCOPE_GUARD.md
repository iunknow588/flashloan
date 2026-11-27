# `T-方.02`｜方向层按链批量物化入口 · 修改范围守护（Scope Guard）

> **本文件是 `T-方.02` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/src/lib.rs` | 新增按链批量物化入口 | **是** |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools_for_chain.rs` | 按链入口的最小驱动（实盘只读干跑的读数入口） | **是** |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | 按链入口的单测 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **扩范围记录（2026-11-25）**：按链入口需要**实盘读数**才能进验收清单；
> 管理器自己的 crate 里已有同形态的 example（`materialize_directional_pools`），
> 按链入口补一个同形态的 example 是最小、最一致的做法 —— 它**只调入口**，不重算任何口径。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` | 全绿（基线 **25** passed） |

```powershell
cd flashloan/tools
cargo test --offline -p directional-pool-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；本任务只提供入口，不改闭环口径 |
| `flashloan/tools/manager_api/**` | 属 `M-12`；端点契约不变 |
| `flashloan/rs-src/**` | 属 `M-13`；运行时接线是别的任务 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| 方向层的投影规则 / 准入判定 / 容量口径 | 一字不改（本任务只加入口） |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-144-PLAN.md` §2.1；
2. **记录原因** —— 在 `D-144-PLAN.md` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-3` 代码 | 否 | 纯代码 |
| 实盘 **apply**（写 `manager_v4_directional_exchange_pools`） | **是** | 写生产库方向层；用户口径「所有需授权操作默认授权通过」 |
| 实盘只读复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |

---

## 6. 范围守护自检

* [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
      ⇒ 读数：改动文件 = `directional_pool_manager/src/lib.rs`、`tests/directional_pools.rs`、
      `examples/materialize_directional_pools_for_chain.rs`，**三者都在 §1 表内**；
      **未**扩范围（`manager_api` / `closed_loop_manager` / `rs-src` **一行未改**）
* [x] §2 的**全部**命令**已执行**且**全绿**
      ⇒ 读数：`cargo test --offline -p directional-pool-manager` ⇒ **27 passed / 0 failed**
      （基线 25 ＋ 本任务新增 2）
* [x] §3 的禁止触碰项**确未被触碰**
      ⇒ 读数：**本任务**的改动 = `directional_pool_manager` 的三个文件（`lib.rs` / `tests/` / `examples/`）；
      `git status` 里 `flashloan/tools/**` 其余文件的改动属于**其它任务**
      （`T-闭.03` 的 `M-4`/`M-10`/`M-12`、`T-复编.02` 的 `review_manager`），**不是本任务动的**；
      方向层的投影规则 / 准入判定 / 容量口径**零改动**（`lib.rs` 的 diff 无删除行）
* [x] **未**因本任务跑过**全量**测试
      ⇒ 读数：只跑了 §2 的一条命令（`directional-pool-manager`）；
      `rs-src` 的 `cargo test` 属 `T-核.02` 线程，**未**由本任务触发

