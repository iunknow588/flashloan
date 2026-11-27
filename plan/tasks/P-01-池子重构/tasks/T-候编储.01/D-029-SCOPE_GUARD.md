# D-029 `T-候编储.01`｜测试网候选池与闭环恢复 · 修改范围守护（Scope Guard）

> **本文件是 `T-候编储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-8` | 编排层 | ``flashloan/tools/composite_manager/`` | 是 |
| `M-5` | 候选层 | ``flashloan/tools/candidate_edge_pool_manager/`` | 是 |
| `M-5` | 候选层 | ``flashloan/src/dex/intent_trade/chains/robinhood_testnet/v4_pool_index.py`` | **是**（**第 ① 步**：刷 `core4` 池域深度） |
| `M-5` | 候选层 | ``flashloan/src/dex/intent_trade/chains/robinhood_common/v4_pool_index_core.py`` | **是**（**第 ① 步**：池索引构建内核） |
| `M-5` | 候选层 | ``flashloan/src/dex/tools/refresh_robinhood_v4_live.py`` | **是**（**第 ① 步**：SQLite 池表重建入口） |
| `M-10` | 存储层 | ``flashloan/src/core/db/storage_v4_exchange.py`` | **是**（**第 ① 步**：池行落库） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **⚠️ 2026-11-24 范围扩张（**先扩后跑** · 本文件 §4 的一次执行）**
>
> **扩范围理由**：`D-110` 实测确认**链上已有 68 个 `poolId`**（`Initialize` 事件），
> 而本地库 `v4_exchange_pools_core4` **仍只有 5 个** ⇒ **性质从「等链上」变为「软件侧可刷新」**
> ⇒ 五步序列的**第 ① 步（刷 `core4` 池域深度）现在有可执行对象**。
>
> | 项 | 说明 |
> |---|---|
> | **为何必须扩** | 第 ① 步的**写入方**在 `flashloan/src/dex/tools/refresh_robinhood_v4_live.py`<br>（`--database` 参数 ⇒ 可指向**副本**，**不写生产库**），<br>池索引构建在 `robinhood_testnet/v4_pool_index.py` ＋ `robinhood_common/v4_pool_index_core.py`，<br>池行落库在 `core/db/storage_v4_exchange.py` ⇒ **三者都不在**原 §1 的两个落点内。 |
> | **模块归属依据** | `T-证候储.01` 的 `D-025` §1 **已确立**同一族落点的归属：<br>`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` ＋ `flashloan/src/dex/tools/v4_rust_refresh.py` ⇒ **`M-5`**；<br>`flashloan/src/core/db/storage_*.py` ⇒ **`M-10`**。本次是**同族落点的测试网分支**，**沿用同一归属**，**不新增模块号**。 |
> | **扩后模块集合** | `M-5` ＋ `M-8` ＋ `M-10` ⇒ **与原集合相比只多了 `M-10`**。 |
> | **编号** | **变更** —— 模块集合由 `M-5`＋`M-8` 变为 `M-5`＋`M-8`＋`M-10` ⇒ 组合索引按 `M-n` 升序拼接为 **`候编储`** ⇒ 任务编号**改为 `T-候编储.01`**（**编号四者同步**：本文件标题、`D-028-PLAN.md`、`D-030-checklist.md`、`D-001-TASK_REGISTRY.md` §4.2 与 §5.5）。 |
> | **授权边界** | 第 ① 步**只在副本** `flashloan/db/sandbox/t46630_directional_probe.sqlite3` 上执行 ⇒ **不写生产库** ⇒ **不需生产库写授权**；<br>广播**不涉及**（第 ① 步只建池域，**不生成复核、不生成广播请求**）。 |
> | **回归范围** | §2 已同步：新增 `M-10`（`cargo test --offline -p pool-manager`）<br>与 `M-5`/`M-10` 的 **Python** 定向用例（**只跑受影响的文件**）。 |

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-5` | `flashloan/src/dex/intent_trade/chains/robinhood_common/v4_pool_index_core.py`<br>`flashloan/src/dex/intent_trade/chains/robinhood_testnet/v4_pool_index.py`<br>`flashloan/src/dex/tools/refresh_robinhood_v4_live.py`<br>`flashloan/tools/candidate_edge_pool_manager/src/lib.rs` | `flashloan/tools/candidate_edge_pool_manager/src/lib.rs`<br>↔ 与 T-候.01 同文件 |
| `M-8` | `flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`<br>`flashloan/tools/composite_manager/src/lib.rs`<br>`flashloan/tools/composite_manager/src/main.rs`<br>`flashloan/tools/composite_manager/src/overview.rs`<br>`flashloan/tools/composite_manager/src/recompute_latency.rs` | `flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`<br>↔ 与 T-编.01 同文件<br>`flashloan/tools/composite_manager/src/lib.rs`<br>↔ 与 T-编.01 同文件<br>`flashloan/tools/composite_manager/src/main.rs`<br>↔ 与 T-编.01 同文件<br>`flashloan/tools/composite_manager/src/overview.rs`<br>↔ 与 T-复编.02 / T-编.01 同文件<br>`flashloan/tools/composite_manager/src/recompute_latency.rs`<br>↔ 与 T-编.01 同文件 |
| `M-10` | `flashloan/src/core/db/storage_v4_exchange.py` | **无**（本任务独占） |

**判定规则**：

| 本任务与他任务的关系 | 判定 | 允许的动作 |
|---|---|---|
| **无同文件**（上表末列 = **无**） | ✅ **可并列** | 独立施工、独立回归、独立提交 |
| **有同文件**，但**改动落在不同函数/不同列** | ⚠️ **可并列，但须错开提交** | 各自改动**同一文件的不同位置** ⇒ **先合者不得覆盖后合者** |
| **有同文件**，且**改动落在同一处** | ⛔ **必须串行** | **模块级**顺序（见 `PLAN.md` §1 前置模块） |

> **同文件 ≠ 同任务**：同文件只说明**提交要错开**，**不说明**两个任务有功能依赖。
> 功能依赖**只按模块号 `M-n`** 表达（见 `PLAN.md` §1 与 §4）。

> **本任务的独占文件**（**无争用** ⇒ 本任务可与其它任务**同时施工**）：
>
> - `flashloan/src/core/db/storage_v4_exchange.py`
> - `flashloan/src/dex/intent_trade/chains/robinhood_common/v4_pool_index_core.py`
> - `flashloan/src/dex/intent_trade/chains/robinhood_testnet/v4_pool_index.py`
> - `flashloan/src/dex/tools/refresh_robinhood_v4_live.py`
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p composite-manager -p manager-api` |
| — | — | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p composite-manager -p manager-api
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| 除上表以外的全部模块 | **未改动模块不跑、不碰**；本任务与其它任务**不耦合** |

---

## 4. 范围变更流程（**先扩后跑**）

若施工中发现必须触碰**不在** §1 模块集合内的模块：

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `PLAN.md` §1.1 / §2.1；
2. **记录原因** —— 在 `PLAN.md` 追加一行说明为什么必须扩；
3. **后施工** —— 然后才动该模块；
4. **后重跑** —— **只**跑扩范围后模块的测试，**不**回头跑全部。

> **禁止**先改了再补登记。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 写生产库 | 见 `PLAN.md` §1「需授权」行 | 仅**真正写生产库**才需要授权 |
| 广播 | 见 `PLAN.md` §1「需授权」行 | — |

---

## 6. 范围守护自检

- [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
- [ ] §2 的**全部**命令**已执行**且**全绿**
- [ ] §3 的禁止触碰项**确未被触碰**
- [ ] **未**因本任务跑过**全量**测试（若跑了，需说明原因）
