# D-025 `T-证候储.01`｜「最近一周有交易」数据源 · 修改范围守护（Scope Guard）

> **本文件是 `T-证候储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-5` | 候选层 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | **是**（口径真相源） |
| `M-2` | 证据层 | `flashloan/rs-src/src/evidence.rs`<br>`flashloan/rs-src/schema/business_schema.sql` | **是**（**证据通道** · 新表写入方） |
| `M-10` | 存储层 | `flashloan/tools/pool_manager/src/lib.rs` | **是**（**写入侧** · 唯一写入方） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` | **是**（列语义确认 · **不改 DDL**） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_v4_exchange.py`<br>`flashloan/src/core/db/import_v4_evidence.py`<br>`flashloan/src/core/db/import_existing_json.py` | **是**（Python 侧 helper ＋ 行装配 ＋ **窗口元数据接线**） |
| `M-5` | 候选层 | `flashloan/src/dex/tools/v4_rust_refresh.py` | **是**（标记与深度测量**相互独立**的筛选条件） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **⚠️ 2026-11-24 范围修订（**先扩后跑** · 本文件 §4 的一次执行）**
>
> 原登记的落点（`candidate_edge_pool_manager/` ＋ `storage_business_schema.py`）
> **与实测根因不符** —— 详见 `docs/D-101-recent_swap_active全NULL根因与接线裁决_20261124.md` §2 / §4。
>
> | 项 | 说明 |
> |---|---|
> | **实测根因** | 该列**算出来了但没有落点** —— Python 侧已算出并送进行装配，<br>但 `rs-src/src/evidence.rs:843-844` **整批丢弃** liquidity 行（`liquidityRowsWritten: 0`），<br>而唯一写入方 `tools/pool_manager/src/lib.rs:2689` 的 UPDATE **不含该列**，且它**从不 INSERT** 快照行。 |
> | **为何扩** | 要让该列**可达**，必须改**写入侧**（pool manager）与**口径真相源**（`v4_pool_index.py`）。<br>原落点 `candidate_edge_pool_manager/` **不在**该链路上。 |
> | **改后回归范围** | `M-5` ＋ `M-10` ⇒ **与原范围同集合**，但**文件级落点已换**（见 §1.1）。 |
> | **改后回归命令** | §2 已同步更新（`pool-manager` ＋ `candidate-edge-pool-manager` ＋ `manager-db`）。 |
> | **编号** | **不变**（`T-证候储.01`）—— 模块集合未变 ⇒ 组合索引 `候储` 仍成立。 |

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-5` | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`<br>`flashloan/src/dex/tools/v4_rust_refresh.py` | **无**（本任务独占） |
| `M-2` | `flashloan/rs-src/src/evidence.rs`<br>`flashloan/rs-src/schema/business_schema.sql` | `flashloan/rs-src/src/evidence.rs`<br>↔ 与 T-复编.01 同文件（**不同位置** ⇒ 须错开提交） |
| `M-10` | `flashloan/tools/pool_manager/src/lib.rs` | **无**（本任务独占） |
| `M-10` | `flashloan/src/core/db/storage_business_schema.py` | `flashloan/src/core/db/storage_business_schema.py`<br>↔ 与 T-储.01 / T-储.02 / T-方储.01 / T-方闭储.01 同文件 |
| `M-10` | `flashloan/src/core/db/storage_v4_exchange.py`<br>`flashloan/src/core/db/import_v4_evidence.py`<br>`flashloan/src/core/db/import_existing_json.py` | **无**（本任务独占） |

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
> - `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
> - `flashloan/src/dex/tools/v4_rust_refresh.py`
> - `flashloan/rs-src/schema/business_schema.sql`
> - `flashloan/tools/pool_manager/src/lib.rs`
> - `flashloan/src/core/db/storage_v4_exchange.py`
> - `flashloan/src/core/db/import_v4_evidence.py`
> - `flashloan/src/core/db/import_existing_json.py`
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-5` | 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |
| `M-2` | 证据层 | `cargo test --offline`（在 `flashloan/rs-src` 下） |
| `M-10` | 存储层 | `cargo test --offline -p pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-5` / `M-10` | 候选层 / 存储层（Python） | `python -m pytest`（**只跑受影响的文件**，见 `PLAN.md` §2.1 第 3 条） |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cd flashloan/tools
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
cargo test --offline -p manager-db
cd ../rs-src
cargo test --offline
cd ..
python -m pytest app_runtime/tests/unit/<受影响的文件>.py -q
```

> **Python 侧**：`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
> 属 `M-5`；其单测只跑**受影响的**那几个文件（见 `PLAN.md` §2.1 第 3 条）。

> **⚠️ Rust 扩展必须重建**：活动表的新 DDL 由 `include_str!` **编译进**扩展，
> `flashloan/rs-src/target/release/flashloan_rs.dll` 若陈旧，Python 侧会看到**旧表结构**
> （表现为 `no such table: v4_exchange_pool_recent_swap_activity`）。
> ⇒ 改过 `flashloan/rs-src/schema/business_schema.sql` 后**必须**
> `cd flashloan/rs-src && cargo build --offline --release`。

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| 除上表以外的全部模块 | **未改动模块不跑、不碰**；本任务与其它任务**不耦合** |
| **`flashloan/src/core/db/pool_state_version.py`** | **填列 ≠ 接入版本触发器**（`D-101` §3.2）。<br>ARCH-1 的版本指纹**定义**是 `(liquidity_block_number, sqrt_price_x96)`；<br>交易活跃度**不是链上状态**，用它当版本会把触发器变回**按时间抖动的定时器**。<br>`pool_state_version.py:25-27` 的「故意不用」**结论**保持不变。<br>⇒ **不得**改该文件的指纹构成、**不得**把本列接入任何版本/变更/触发重算判定、**不得**据此新增定时任务。 |
| **`recent_swap_active` 的 `NULL` 语义** | `NULL` = **未测量**；`0` = **量过且无交易**。<br>**不得**把 `NULL` 写成 `0`（否则未覆盖的池会被当成「无交易的池」⇒ 层 6 误淘汰）。<br>⇒ 落点必须用 `Option<i64>`（`as_tri` 对 `None` 写 `Value::Null`，`business_builder.rs:107-111`）。 |
| **准入判据 `L1` / 排序主序 `L2`** | 本任务**只提供数据**，**不改**判据与排序（`D-026` i.2 第 3 项）。 |
| **广播 / 任何门禁放宽** | 本任务**不广播**、**不放宽**任何门禁（`D-026` i.2 第 4 项）。 |
| **`TASK-172` / `AC-221` 的已闭合结论** | **不得**恢复「证据通道写深度快照表」这条通道（`D-101` §2.3）。<br>快照表**唯一写入方**仍是 Rust pool manager。 |
| **池库准入规则**（`pool_enters_library` / `fee` 上下限） | 活动表**引用** `v4_exchange_pools`，所以「池子能否进池库」这条**存储规则**<br>同时约束活动表的**可写集合**（`D-102` §3.2）。<br>⇒ **不得**为了让活动行写进去而**放宽**池库规则，也**不得**给活动表**拆外键**。<br>正确做法是**写入方收口**：只发布池库内的池子标记（Rust `library_pool_ids()` / Python `_library_pool_ids()`）。<br>⇒ **不得**擅自改 `MAX_FIXED_FEE` / `DYNAMIC_FEE_FLAG` / `pool_enters_library` 的语义。 |
| **`recent_swap_active` 的第三态** | 除 `NULL`（未测量）与 `0`（量过、无交易）外，还有**第三态**：<br>**行不存在** = 该池**不在池库内**（如动态费率池）。<br>⇒ **不得**把「不在池库」写成 `0`（会让层 6 把**永远不可用**的池误判成「无交易但可用」）。 |

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
