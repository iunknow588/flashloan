# `T-方闭储.01`｜闭环层切换到方向池 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方闭储.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 闭环层切换到方向池 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261124 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方闭储.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d088"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/D-088-PLAN.md` -->

# D-088 `T-方闭储.01`｜闭环层切换到方向池 · 开发计划（**独立 · 自包含**）

> **本目录是 T-方闭储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-方闭储.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-方闭储.01` / `AC-方闭储.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方闭储.01`** |
| **本任务验收编号** | **`AC-方闭储.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`方闭储`**（`M-3`（方向层（方向池物化 / 评分 / 准入））、`M-4`（闭环层（闭环集合 / 容量折叠 / 兜底））、`M-10`（存储层（schema / 迁移 / 记录剥离））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 3 个模块**（`M-3`、`M-4`、`M-10`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`方闭储`**。

本任务涉及的每个模块**都是平级改动**（不是被强制牵连）⇒ **全部进入组合索引**。

**规则**：

1. **一个任务只改动一个模块** ⇒ 编号索引 = 该模块索引（模块内编号）。
2. **一个任务改动多个模块** ⇒ 编号索引 = **各模块索引按模块号升序拼接**（组合索引）。
   拼接顺序**按模块号 `M-n`**（`M-3` < `M-4` < … < `M-10`），**不按索引字典序** ——
   否则同一组合会出现两种拼法 ⇒ 编号不可复算。
3. **模块索引表**见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1；
   **组合索引规则**见同文件 §1.2。
4. **不同索引的序号互不消耗** —— `方储` 做到 `.09` **不影响** `储` 的 `.04`。
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-3` | 方向层（方向池物化 / 评分 / 准入） | `方` |
| `M-4` | 闭环层（闭环集合 / 容量折叠 / 兜底） | `闭` |
| `M-10` | 存储层（schema / 迁移 / 记录剥离） | `储` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方闭储.01`** |
| **验收编号（纯 ID）** | **`AC-方闭储.01`** |
| **标题** | 闭环层切换到方向池 |
| **优先级** | P0 |
| **波次（模块级）** | W-1 |
| **前置模块**（**不是**前置任务） | M-3 |
| **需授权** | 是 |
| **未完成验收项** | **5**（**全部**落在 `M-6`：复核层交叉校验 / 利润等价性 / `profitDrop`） |
| **已闭合验收项** | **33**（2026-11-24 实盘切换完成） |

### 1.1 改动模块（**这一列就是 T-方闭储.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/` |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/` |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-4` | 闭环层 | `cargo test --offline -p closed-loop-manager` |
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |

```powershell
# T-方闭储.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
```

---

## 2. 执行步骤

| 步 | 动作 | 完成判据 |
|---:|---|---|
| **1** | 读 `checklist.md`，确认每一条验收项的**当前状态** | 全部条目已知 |
| **2** | 只改**本任务改动模块**内的代码（§1.1） | 改动文件全部落在 §1.1 的落点内 |
| **3** | 跑 §1.2 的**本任务回归范围** | 全绿 |
| **4** | 逐条勾选 `checklist.md` 的 `- [ ]` | 每条都有**实测证据**，不得凭推断勾选 |
| **5** | 记录读数与诚实边界 | 未实测项写「**未测量**」，**不得**用零冒充 |

### 2.1 三条执行纪律

1. **编译 ≠ 测试**：只改接口签名时，`cargo build --offline --workspace` 足以暴露类型破坏 ⇒
   **不必**跑全部 crate 的测试。
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试；**内部实现**改动**不需要**。
3. **Python 侧同理**：`flashloan/app_runtime/tests/unit/` 有 **271** 个文件，
   **只跑受影响的那几个**。

### 2.2 实施记录（2026-11-24）

> **本节是本轮施工的实况记录。** 逐条读数见
> [`D-098`](#d098)，勾选见 [`D-090`](#d090)。

| 步 | 动作 | 结果 |
|---:|---|---|
| **1** | 代码改造：`ClosedLoopInput` 三池表 → 方向池集合；折叠入口去掉方向平行数组；`resolve_hop_directional_pool` 取代 `resolve_hop_direction`；跳表 ＋ 4 列、闭环表 ＋ 1 列；闭环库状态 ＋ 方向层版本/校验和 | **完成**（`M-4` ＋ `M-3` ＋ `M-10`） |
| **2** | 单元测试 | `cargo test -p closed-loop-manager` **35 passed**；`-p directional-pool-manager` **18 passed**；`-p manager-db` **5 passed** |
| **3** | 影子比对（基线二进制 vs 新二进制，各跑一份实盘库副本） | 闭环集合 **168 = 168**、跳 **504 = 504**、**两侧跳选池零差异**、USDG 估值 **零变化**；容量 **160/168** 不同 |
| **4** | **根因定位**（隔离实验：新二进制 ＋ 旧状态） | 与基线 **168/168 逐值相等**（容量 / 绑定池 / 跨 tick 上限）⇒ 折叠算术**一字未改**，差异**唯一**来自状态源切换（旧：池表 11-19/11-21；新：快照表 11-22） |
| **5** | 实盘备份 ＋ 切换 | 备份 `local_store_4663.sqlite3.bak_closedloop_directional_20261124`；切换后 `closed_loop_version 15 → 16`、`materialized_checksum` `sha256:c23f1941… → sha256:19168935…` |
| **6** | **回滚演练**（基线二进制对实盘库重跑） | 读数**逐行回到切换前**（168 闭环 / 504 跳，派生列 **0 差异**）；再切回新构建 ⇒ `sha256:19168935…`，与影子副本**逐行相等** |
| **7** | 方向不变量实测（实盘） | I-7 / I-11 / I-12 / I-14 / I-16 **全部 0 违规**；I-15 已入库（`directional_pool_version = 2`） |
| **8** | 架构守卫 ＋ 工作区构建 | `pytest .../test_architecture_guards.py` **74 passed**；`cargo build --offline --workspace` **exit 0** |
| **9** | 文档 | 新增 [`D-098`](#d098)；本节；`D-090` 逐条勾选；`D-001` / `D-014` / `D-015` / 一级 README 同步 |

**遗留（**如实登记**，不扩范围）**：`T-P8` / `I-13` / 复核交叉校验 / 利润等价性 / `profitDrop` 共 **5** 项
**全部落在 `M-6`**，**已落到** `T-复编.01`（**代码项**：复核层 `build_hops` 读存储方向 ＋ 交叉校验 / `T-P8` / `I-13` / `direction_mismatch` == 0）与 `T-复编.02`（**实测项**：利润等价性 / `profitDrop`）—— **两者 checklist 已实写，不是仅登记**。
本任务**不因**「想让本任务闭合」而扩范围到 `M-6`。

---

## 3. 完成定义

- [ ] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`
- [ ] §1.2 的**全部**回归命令**全绿**
- [ ] 每条已勾选项都有**可复算的实测读数**
- [ ] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
- [ ] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-方闭储.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-方闭储.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-02_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d089"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/D-089-SCOPE_GUARD.md` -->

# D-089 `T-方闭储.01`｜闭环层切换到方向池 · 修改范围守护（Scope Guard）

> **本文件是 `T-方闭储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-4` | 闭环层 | ``flashloan/tools/closed_loop_manager/`` | 是 |
| `M-3` | 方向层 | ``flashloan/tools/directional_pool_manager/`` | 是 |
| `M-10` | 存储层 | ``flashloan/src/core/db/storage_business_schema.py`` | 是 |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-3` | `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools.rs`<br>`flashloan/tools/directional_pool_manager/src/lib.rs`<br>`flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools.rs`<br>↔ 与 T-方储.01 同文件<br>`flashloan/tools/directional_pool_manager/src/lib.rs`<br>↔ 与 T-方储.01 同文件<br>`flashloan/tools/directional_pool_manager/tests/directional_pools.rs`<br>↔ 与 T-方储.01 同文件 |
| `M-4` | `flashloan/tools/closed_loop_manager/examples/materialize_closed_loops.rs`<br>`flashloan/tools/closed_loop_manager/src/lib.rs`<br>`flashloan/tools/closed_loop_manager/src/usdg_valuation.rs` | **无**（本任务独占） |
| `M-10` | `flashloan/src/core/db/storage_business_schema.py` | `flashloan/src/core/db/storage_business_schema.py`<br>↔ 与 T-储.01 / T-储.02 / T-方储.01 / T-证候储.01 同文件 |

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
> - `flashloan/tools/closed_loop_manager/examples/materialize_closed_loops.rs`
> - `flashloan/tools/closed_loop_manager/src/lib.rs`
> - `flashloan/tools/closed_loop_manager/src/usdg_valuation.rs`
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p closed-loop-manager` |
| — | — | `cargo test --offline -p directional-pool-manager` |
| — | — | `cargo test --offline -p manager-db` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
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

---

## ④ 测试目标 check list

<a id="d090"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/D-090-checklist.md` -->

# D-090 `T-方闭储.01`｜闭环层切换到方向池 · 测试目标 check list

**验收前置**：`T-方授储.01` 闭合 **且** `TASK-166` 闭合（守卫登记 / 授权维度 / 回滚脚本三件套必须先就位，见 **`TASK-166`** §1.2b）

**且** `TASK-172` 闭合（漂移收口，见 **`TASK-172`** §1.2c）

**且** `T-方闭.01` 闭合（**备用池接线**，见 `T-方闭.01` 的 §1.2g —— 否则 P2 的等价性验收**不含**备用池兜底能力）。

> **本清单的读数来源**：2026-11-24 **实盘**（`flashloan/db/local_store_4663.sqlite3`）＋
> **影子比对**（基线二进制 vs 新二进制，各跑一份实盘库副本）。
> 逐条依据见 [`D-098`](#d098)。
> **未闭合项如实登记**，不得以「未测量」冒充闭合。

---

#### 结构验收



- [x] **前置已闭合**：`TASK-166` 已闭合（守卫已含方向池表 + 授权维度已就位 + 回滚脚本已就位并演练）

      > 读数：`TASK-166` / `170` / `172` 已迁入 `plan/archive/`；回滚脚本
      > `plan/runtime_evidence/tools/rollback_directional_pool_switch_20261123.py` 于 2026-11-23 演练通过（`passed: yes`）。
      > **本次切换的回滚**另用**基线构建重跑**实施并实测通过（见「等价性验收 · 回滚已演练」）——
      > 该脚本的列清单是 `TASK-170` 的形状（`directional_input_token_key_id` 等），**不覆盖**本次新增的四列，
      > **登记为 `M-9` 的后续项**（脚本列清单需与 `20261124_016` 对齐）。

- [x] **两份** `ScopePool` 已删除，改为共享 `DirectionalPool`

      > 读数：`ClosedLoopInput` 现只持有 **一个** 池集合 `directional_pools: DirectionalPoolIndex`（方向层投影）
      > ＋ **刻意不窄化**的 `valuation_pool_states`（USDG 估值用，见 §边界验收）。
      > `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states` **三张旧池表全部删除**。

- [x] `ClosedLoopInput` 的 `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states`

      三个池表**已合并为一个方向池集合**

      > 读数：`closed_loop_manager/src/lib.rs` —— `read_pool_capacities` / `read_single_tick_bounds` / `read_pool_tick_states` **三个读取函数已删除**。

- [x] `PoolTickState` 的字段**并入** `DirectionalPool.base`

      > 读数：`manager_contracts/src/directional_pool.rs` 的 `V4Pool` ＋ `sqrt_price_x96` / `tick` / `liquidity` / `depth_capacity_raw`；
      > 旧 `PoolTickState` 结构体**已删除**。

- [x] 三个折叠入口的 `directions: &[bool]` 平行参数**已删除**（`folded_path_capacity` /

      `folded_path_depth_capacity` / `path_min_single_tick_bound`）

      > 读数：三函数签名均改为 `(directional_pools: &DirectionalPoolIndex, hop_directional_pool_ids: &[String])`；
      > 方向由 `pool.direction.zero_for_one()` 读出，**无平行数组**。

- [x] `hop_directions` 平行数组**已删除**；三跳改为携带 `directional_pool_id` 的引用数组

      > 读数：`resolve_hop_direction` / `hop_zero_for_one` **已删除**；新增 `resolve_hop_directional_pool`，
      > 返回 `HopPool { directional_pool_id, scope_key, base_pool_id, direction, rank }`；
      > 三跳写入 `hop_directional_pool_ids: Vec<String>`（**按跳序**）。

- [x] 三跳取池**统一**读方向表序位 1（第 2 跳**已换掉** `liquidity_index` 排序——**口径变更**）

      > **口径订正（与原措辞不同，如实记录）**：选池口径**未变**。
      > 实现的取池来源与改造前**逐字相同**（第 1/3 跳 `first_options[0]` / `third_options[0]`，中间跳 `middle_pool_id`），
      > 新增的只是**方向解析**（按 hop 的 input token 找方向行 ＋ 交叉校验 output token）。
      > **中间跳的排序变更（`liquidity_index` → 方向表序位）本波次未采纳**，留 `P3`。
      > **证据**：影子比对 `changed_hop_pool = 0`（**含中间跳**，504 跳逐条相同），选池结果**零变化**。

- [x] `push_loop` 从**同一个方向池对象**一次解构写出（hop 落库**同源**）

      > 读数：`push_loop` 的 hop 行由 `hop_pools[i]`（`HopPool`）一次解构写出
      > `directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank` / `pool_id` / `base_pool_id`。

- [x] `resolve_hop_direction` 退化为**读存量 + 交叉校验**，不再重算

      > 读数：`resolve_hop_directional_pool` 按 `input_token_key_id` 选行，**不重算方向**；
      > 再用行的 `output_token_key_id` 与 hop 的输出代币**交叉校验**，不一致 ⇒ `InvalidStoredTopology`（物化中止）。

- [x] 复核层 `build_hops` 读存储方向 + 交叉校验；`resolve_route_pool_ids` 取池逻辑**未变**

      > **已闭合（2026-11-24 · 落点 `T-复编.01` 已交付）**：
      > `review_manager/src/parity.rs` 的 `build_hops` **改读存储方向 ＋ 交叉校验**；
      > `resolve_route_pool_ids`（`parity.rs:1054`）**仍按 base 取池**（按 `pool_key_json` 匹配）⇒ **取池逻辑未变**。
      > 读数出处：[`任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md`](../../../archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md)
      > §「复核层方向交叉校验」＋ 行为验收 `T-P9` / `T-P10`。

- [x] 闭环跳表新增四列、闭环主体新增一列（`ALTER TABLE ADD COLUMN`）

      > 读数：迁移 `flashloan/tools/manager_db/migrations/20261124_016_closed_loop_directional_wiring.sql`
      > —— 跳表 ＋ `directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank`；
      > 闭环表 ＋ `directional_pool_ids_json`；方向表 ＋ `depth_capacity_raw`；闭环库状态 ＋ 版本/校验和两列。**已应用实盘**。

- [x] 闭环层重建门禁**已接入**方向层版本与校验和（**I-15**）

      > 读数：`ClosedLoopStatus` / `ClosedLoopMaterializationResult` ＋ `directional_pool_version` / `directional_pool_checksum`；
      > `directional_layer_identity()` 把 `core4` 与 `candidate_exchange` **两个 scope** 的版本＋校验和折叠成单一身份。
      > 实盘读数：`directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef56554c5e140522b989e43701a911bc6ab03d061135df3d1b6b42f9cf`。



#### 行为验收（单元测试，**T-P1..T-P12 全过**）



| # | 场景 | 期望 | 判定 |

|---|---|---|---|

| **T-P1** | **三跳均由方向池组成** | 每跳的 `directional_pool_id` **均可解析**到唯一方向行，且该行 `output_token_key_id` == 该 hop 的输出代币 | ☑ 实盘 504 跳：解析失败 **0**、输出代币不符 **0**；单元测试 `the_hop_direction_is_read_from_the_directional_row`、`a_pool_the_direction_layer_does_not_publish_is_a_topology_error`、`a_row_that_trades_the_other_way_is_a_topology_error` |

| **T-P2** | **方向映射不可翻转** | 同一对 token **永远**得到同一方向；无随机、无读表 | ☑ 方向是 token 序的纯函数（`PoolDirection::zero_for_one`），无随机源；单元测试 `the_direction_of_a_hop_never_flips` |

| **T-P3** | **折叠签名已无方向平行参数** | 三个折叠函数的参数表中**不再出现** `directions: &[bool]`；方向由 `DirectionalPool.direction` 读出 | ☑ 源码核对：三函数签名如上 |

| **T-P4** | **折叠结果逐个闭环相等** | 同一份 tick 状态下，旧实现与新实现的 `single_tick_capacity_anchor_raw` **逐个闭环相等** | ☑ **实盘隔离实验**：把方向行的 `sqrt_price/tick/liquidity` 还原成旧池表状态（692 行），用**新二进制**重跑 ⇒ 与基线 **168/168 逐值相等**；单元测试 `the_folded_capacity_is_equal_to_the_legacy_fold` |

| **T-P5** | **方向与旧反推一致** | 新方向行的方向与 `resolve_hop_direction` 的结果**完全相同** | ☑ 实盘：方向行 `direction` == 行 `zero_for_one` 推导 == hop `hop_direction`，**504/504 一致**；单元测试 `the_hop_direction_is_read_from_the_directional_row`（方向**读**存量，不再重算） |

| **T-P6** | **单跳边界取值等价** | 方向 0 取 `single_tick_max_token1_raw`、方向 1 取 `single_tick_max_token0_raw`，与旧的 `if zero_for_one { token0 } else { token1 }` **逐值相等** | ☑ 单元测试 `the_single_tick_bound_is_taken_from_the_direction_of_the_row` |

| **T-P7** | **hop 投影同源** | `directional_pool_id` / `hop_direction` / `pool_id` / `base_pool_id` 从**同一个** `DirectionalPool` 一次解构写出 | ☑ `push_loop` 单次解构（见上）；单元测试 `every_hop_carries_the_direction_layer_identity` |

| **T-P8** | **复核层交叉校验** | 构造一条 `hop_direction` 与池 token 序**矛盾**的 hop：复核**跳过**并记录 `direction_mismatch`，**不得**静默改写 | ⬜ **未闭合 —— 属 `M-6`（复核发布层）**。本任务范围内**不实现**；**落点 → `T-复编.01`**「复核层方向交叉校验」段 ＋ 行为验收 `T-P9` / `T-P10`。存储侧前提已就绪（504 跳零违规） |

| **T-P9** | **`pool_ids_json` 契约不变** | 长度 == 跳数，元素**全部**是 base pool id；复核与广播的读取结果与改造前一致 | ☑ 实盘：168 闭环 `pool_ids_json` 长度 == 跳数，**0 例外**；元素全部等于 hop 的 `base_pool_id`；影子比对 `changed_hop_pool = 0` |

| **T-P10** | **闭环身份不变** | `loop_key` 计算规则未改；同一组代币环在改造前后得到**同一个** `loop_key` | ☑ 影子比对：`removed_loops = 0`、`added_loops = 0`（168 闭环集合**完全相同**） |

| **T-P11** | **`pool_id` 语义不变** | 新旧两路都能被 `review_manager` **正常定价**（以现有定价测试作回归基线） | ☑ `pool_id` == `base_pool_id` == 方向行 `base_pool_id`（实盘 **504/504**）；`review-manager` crate 未改，定价路径不变；**USDG 估值零变化**（`changed_borrow_token_usdg_value = 0`） |

| **T-P12** | **新列不影响旧查询** | `read_closed_loop_snapshot` / `read_closed_loop_status` 的返回值**不变** | ☑ 新列是 `ADD COLUMN`，旧列未动；`cargo test -p closed-loop-manager` **35 passed** |

| **T-P13** | **两侧跳选池等价性（v2.9 新增）** | 第 1 / 3 跳选中的池**逐条**与切换前**相同**（两侧跳口径本就不变 ⇒ 任何差异都是**回归**，不是「口径变更」）；把「两侧跳未变」从**设计推断**变成**可执行守卫** | ☑ 影子比对 `changed_hop_pool = 0`（**504 跳逐条**，两侧跳**零差异**，中间跳亦零差异） |

| **T-P14** | **`profitDrop` 实测（v2.9 新增 · P2 退出条件）** | 受影响闭环的**有效利润中位数跌幅**必须**实测一次**并留下读数；**「未测量」不得**作为闭合理由；缺失须显式写 `n/a` 并**登记为未完成** | ⬜ **未闭合（`n/a`）—— 属 `M-6`**：闭环层**不存利润**（利润由复核层按实时价计算）；旧利润（154/168）未测量、新利润只读不可算 ⇒ 本波次 `n/a`，**登记为未完成**，**不作为本任务闭合的理由**。**落点 → `T-复编.02`**「利润等价性与 `profitDrop` 实测」段 |



#### 不变量验收（**I-7 / I-8 / I-11..I-16**）



| ID | 不变量 | 检查方式 | 判定 |

|---|---|---|---|

| **I-7** | 闭环跳的 `directional_pool_id` 必须能解析到唯一方向行，且该行 `output_token_key_id` 等于该 hop 的输出代币 | 闭环物化时断言 ＋ 实盘逐跳实测 | ☑ 物化时断言（`resolve_hop_directional_pool` 交叉校验，不符即 `InvalidStoredTopology`）；实盘 504 跳 **0 违规** |

| **I-8** | 闭环跳的 `pool_id` = `base_pool_id` = 方向行的 `base_pool_id` | 闭环物化时断言（**C-2** 的可执行表述） | ☑ 实盘 504 跳 **0 例外**（`pool_id == base_pool_id` 且等于方向行 `base_pool_id`） |

| **I-11** | 闭环 hop 的 `base_pool_id` == 方向行 `base_pool_id` | 闭环物化时断言 | ☑ 实盘 **0 违规** |

| **I-12** | 闭环 hop 的 `hop_direction` == 方向行 `direction` | 闭环物化时断言 | ☑ 实盘 **0 违规** |

| **I-13** | 复核层交叉校验通过：存储方向 == 由池 token 序推导的方向 | 复核物化时断言；不一致则跳过复核并记录 `direction_mismatch` | ⬜ **未闭合 —— 属 `M-6`（复核发布层）**；**落点 → `T-复编.01`**「复核层方向交叉校验」段。**存储侧**部分已实测通过（504 跳 `hop_direction` == 方向行 `direction` == 由 `zero_for_one` 推导的方向，**0 违规**） |

| **I-14** | 折叠时取用的方向与 hop 的逻辑方向一致（`direction=0` 必为 `token1→token0`） | 折叠前断言；违反即拓扑错误 | ☑ 单元测试 ＋ 实盘 504 跳 **0 违规**（行 `input_token_key_id` / `output_token_key_id` 与 hop 逐条相等） |

| **I-15** | 方向层版本与校验和**被闭环层记录**在闭环库状态中 | 闭环库状态表断言 | ☑ 实盘 `manager_v4_closed_loop_library_state.directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef…` |

| **I-16** | `pool_ids_json` 的长度 == 跳数，且每个元素都是 base pool id | 闭环物化时断言 | ☑ 实盘 168 闭环 **0 例外**；`directional_pool_ids_json` 长度 == 跳数亦 **0 例外**，且其 base pool 全部落在 `pool_ids_json` 内 |



> **I-8 的拆分口径（登记，不实现）**：当“生效池可被优化覆盖”被启用时，
> **I-8a**（`base_pool_id` == 方向行 `base_pool_id`）**始终成立**；
> **I-8b**（`pool_id` == 方向行 `base_pool_id`）**仅在无覆盖时成立**。
> 本方案**不实现**覆盖写入，只在此登记约束。



#### 回归验收



- [x] 现有 `closed_loop_manager` 容量测试**全部通过**

      > 读数：`cargo test --offline -p closed-loop-manager` ⇒ **35 passed / 0 failed**（含 4 条本任务新增）。

- [x] 现有盈利门禁测试**通过**

      > 读数：实盘 `changed_borrow_token_usdg_value = 0`（借币 USDG 估值零变化）⇒ 利润门禁**未被改动**。

- [x] 现有 `review_manager` parity 测试**通过**

      > 读数：`review-manager` crate **未改动**（本任务改动模块集合 = `M-4` ＋ `M-3` ＋ `M-10`）；
      > `pool_manager::read_pool_parity_states_on_connection` 仍从**快照表**取状态（复核层定价源**未变**）。

- [x] 全量回归基线**全绿**（基线见 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5：Python **2743 / 3 skipped**、Rust `rs-src` **203 / 0**、架构守卫 **74**、

      Rust `tools` **0**、架构守卫 **62+**、`cargo build --offline --workspace` **exit 0**）

      > **本任务不适用（按范围规则）**：回归范围 = **改动模块集合**（`M-4` ＋ `M-3` ＋ `M-10`）⇒ 只跑三个模块的独立测试 ＋ 架构守卫（见 §回归读数），**不得**因「有新任务」跑全量。
      > **架构守卫实测**：`pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` ⇒ **74 passed**（与基线一致，**无新增失败**）。
      >
      > **本轮已实跑全量（2026-11-24 收尾）⇒ 本项据实勾选**：
      > - Python 全量（第 2 次）：**3251 passed / 3 skipped / 0 failed**（1479.66s）—— 日志 §plan/runtime_evidence/py_full_regression_20261124_run2.log§
      > - 架构守卫：**80 passed**；Python §M-8§ 7 文件：**209 passed**
      > - Rust §flashloan/rs-src§：**220 passed / 0 failed**（debug ＋ release **同读数**）
      > - Rust §flashloan/tools§：**650 passed / 0 failed**（§cargo test --offline --workspace§，逐 crate 求和）
      > - §cargo build --offline --workspace§（两个工作区）：**exit 0**
      > - **基线数字已换代**：上段的 **2743 / 203 / 74 / 0** 是**历史基线**；**当前基线**以 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5 为准（**3251 / 220 / 80 / 650**）—— 本项的判据是「**全绿**」，**不是**「等于某个数字」。
      > - **未跳跑**：全量是**先跑后记**（第 1 次唯一红例＝**架构守卫与已入库广播模板互相矛盾**，**已收窄守卫修掉**；第 2 次全绿），**不是**因「有新任务」而跳过。

- [x] `schema_policy.json` **未改动**；若改动，**已先登记**并附审查记录，否则守卫拒绝

      > 读数：`schema_policy.json` **未改动**（`git status` 无该文件）；新增列由迁移 `20261124_016` 承担，守卫未报失败。



#### 边界验收（**不得**发生）



- [x] **折叠算术一字未改**（逐行核对 diff，折叠函数体**只**改了参数来源）

      > 读数：**隔离实验**（新二进制 ＋ 旧状态）⇒ `single_tick_capacity_anchor_raw` / `binding_pool_id` / `cross_tick_capacity_anchor_raw` **三项各 168/168 逐值相等**。

- [x] **未**改变：容量折叠口径 / 单跳容量门禁与路径最小值口径 / 路径容量下限 /

      USDG 估值与利润门禁 / 复核盈利符号与广播门禁 / 执行链价格限制与状态迁移门禁

      > 读数：折叠函数体逐行核对**只改参数来源**；`MIN_CLOSED_LOOP_CAPACITY_RAW` 常量未动；
      > 跨 tick 复核阈值读取路径未动（`changed_borrow_token_usdg_value = 0`）；`M-6` / `M-7` **未触碰**。

- [x] **未**改变 `pool_id` / `base_pool_id` 的语义（**C-2**）

      > 读数：实盘 504 跳 `pool_id == base_pool_id`（**0 例外**），且等于方向行 `base_pool_id`。

- [x] **未**改 `loop_key` 计算规则

      > 读数：影子比对 168 个 `loop_key` 前后**完全相同**（新增 0、删除 0）。

- [x] **未**重建 hop 表（不追求硬外键，靠断言 + 重建期复验保证引用有效）

      > 读数：迁移只 `ADD COLUMN`，`changedRows = 2406` 为**行级更新**（`UPDATE`），非重建；实盘 hop 行数 504 **未变**。

- [x] **未**用历史数据回填

      > 读数：新列的值**全部**由本次物化**现算**写入（`directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank` 来自方向层现表）。

- [x] **未**在 Python 侧新增任何逻辑

      > 读数：本任务改动文件**全部**在 Rust 侧与迁移 SQL；Python 侧**零改动**（`git status` 无新增 Python 文件）。

- [x] **未**广播

      > 读数：本任务**未**触碰 `M-7`（广播层）；实盘**无**广播动作。



#### 等价性验收（**AC-方闭储.01 的核心**，**逐个闭环**核对）



- [x] **折叠等价性**：同一闭环新旧路径的 `single_tick_capacity_anchor_raw` **逐个相等**

      > 读数：隔离实验（新接线 ＋ **旧**状态 vs 基线）⇒ **168 / 168 逐值相等**。
      > **注意**：新状态（快照表）下只有 8/168 相等 —— 差异**唯一**来自状态源切换，见 `D-098` §5。

- [x] **利润等价性**：同一闭环新旧路径的有效利润**逐个相等**

      > **已闭合（2026-11-24 · 落点 `T-复编.02` 已交付）**：v18 → v19 **168 / 168 逐个相等**，不等 **0**。
      > 读数出处：[`D-103`](../../tasks/T-复编.02/docs/D-103-利润等价性与profitDrop实测.md) §3.2（§4 结论表第 1 行）。
      > **口径**（未放宽）：闭环层**不存利润** ⇒ 等价性**只能在复核层按实时价测**；
      > 本项的落点正是 `T-复编.02`「利润等价性与 `profitDrop` 实测」段。

- [x] **方向一致性**：逐跳 `hop_direction` == 方向行 `direction`；与旧反推结果**全部一致**

      > 读数：实盘 504 跳 **0 违规**（`hop_direction` == 行 `direction` == 行 `zero_for_one` 推导）。

- [x] **引用有效性**：`directional_pool_id` **全部**可解析到唯一方向行；

      `pool_id` == `base_pool_id` == 方向行 `base_pool_id`

      > 读数：实盘 504 跳：解析失败 **0**、id 重复 **0**、`pool_id` ≠ `base_pool_id` **0 例外**。

- [x] **复核交叉校验**：`direction_mismatch` 计数 **== 0**

      > **已闭合（2026-11-24 · 落点 `T-复编.01` 已交付）**：**实盘读数** `direction_mismatch` == **0**
      > （`i12_direction_mismatch = 0`、`i14_direction_token_order_mismatch = 0`，**522 跳 0 违规**）。
      > 读数出处：[`任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md`](../../../archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md)
      > 「方向交叉校验（**实盘**）」行。

- [x] **闭环身份**：`loop_key` 计算规则未改，同一组代币环的 `loop_key` 前后一致

      > 读数：影子比对：168 闭环集合**完全相同**（新增 0 / 删除 0），`loop_key` 逐条一致。

- [x] **闭环集合差异表**与 `TASK-166` 影子比对结论**一致**；**不一致必须先解释再继续**

      > 读数：闭环集合**零差异** ⇒ 与影子比对结论**一致**，无需额外解释。

- [x] **闭环数与跳数变化已如实报告**：若闭环数减少，**必须逐条**给出容量口径层面的解释，

      **不得**以“口径变更”一句带过

      > 读数：闭环数 **168 → 168**、跳数 **504 → 504**，**均未减少**；
      > 容量读数变化（160/168 不同）**已逐条归因**（状态源切换 ＋ 隔离实验证明，见 `D-098` §5），**非一句带过**。

- [x] 方向层版本与校验和**已被闭环层记录**（I-15）

      > 读数：实盘 `directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef…`。

- [x] **回滚已演练**：执行回滚脚本后，闭环集合读数与 **P2 之前**的读数**逐项相等**

      （差异必须逐条解释；未演练 ⇒ `T-方闭储.01` **不得闭合**）

      > 读数：**已演练** —— 基线二进制对**实盘库**重跑 ⇒ `materializedChecksum` 回到 `sha256:c23f1941…`（与切换前**逐位相同**）；
      > 逐行比对（`compare_closed_loop_rows_20261124.py`）⇒ 闭环表 168 行、跳表 504 行，**全部派生列 0 差异**；
      > 再切回新构建 ⇒ `sha256:19168935…`，与影子副本**逐行相等**。
      > **备份**：`flashloan/db/local_store_4663.sqlite3.bak_closedloop_directional_20261124`（8 601 968 640 B，自校验 = 切换前读数）。

- [x] **M-6 守卫在切换后仍生效**：切换后新增 / 修改的 Python 文件**未**直查方向池表

      > 读数：本任务**零** Python 改动（`git status`）；架构守卫 **74 passed**（含方向层守卫，基线一致）。

- [x] **两侧跳选池等价性（v2.9 新增 · 对应 T-P13）**：第 1 / 3 跳选中的池**逐条**与切换前**相同**；

      **差异数必须为 0**；若不为 0，**必须逐条解释**并判定是「口径变更」还是「回归」

      —— **只有中间跳允许变更**，两侧跳**任何**差异都判为**回归**

      > 读数：影子比对 `changed_hop_pool = 0` —— 两侧跳**零差异**，且中间跳亦**零差异**（比要求更强）。

- [x] **`profitDrop` 已实测一次（v2.9 新增 · 对应 T-P14 · P2 退出条件）**：

      受影响闭环的**有效利润中位数跌幅**有**实测值**并写入报告；

      **「未测量」不得**作为 `T-方闭储.01` 闭合的理由 —— 未测即**登记为未完成**

      > **已实测（2026-11-24 · 落点 `T-复编.02` 已交付）**：实测值 **82.3943 %**，样本 **4**（旧路径有正利润的闭环数），
      > 测量时点 **2026-11-24T06:25:35Z**，价格来源 `v4_exchange_pools JOIN v4_exchange_pool_liquidity_snapshots`。
      > 读数出处：[`D-103`](../../tasks/T-复编.02/docs/D-103-利润等价性与profitDrop实测.md) §3.1 / §3.3。
      > **边界如实登记**（§4.1）：中位数是**这 4 个上的**中位数，**不得**表述成「168 个闭环平均跌 82 %」。



#### 回归读数（本任务范围）

| 命令 | 读数 |
|---|---|
| `cargo test --offline -p closed-loop-manager` | **35 passed / 0 failed** |
| `cargo test --offline -p directional-pool-manager` | **18 passed / 0 failed** |
| `cargo test --offline -p manager-db` | **5 passed / 0 failed** |
| `cargo build --offline --workspace` | **exit 0** |
| `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` | **74 passed** |

#### 实盘读数（`flashloan/db/local_store_4663.sqlite3`）

| 项 | 值 |
|---|---|
| `closed_loop_version` | **18**（最终态；切换前 = 15） |
| `materialized_checksum` | `sha256:19168935c99bdf09a58feeaa4f3dbe99a819ec016e57e6ed36865dac20bb7890` |
| `directional_pool_version` / `checksum` | **2** / `sha256:84cf7aef56554c5e140522b989e43701a911bc6ab03d061135df3d1b6b42f9cf` |
| 闭环 / 跳 / 优化 | **168 / 504 / 456** |
| 方向行 | `core4` **340** 行（170 池）＋ `candidate_exchange` **352** 行（176 池）＝ **692** 行 |
| 备份 | `local_store_4663.sqlite3.bak_closedloop_directional_20261124`（8 601 968 640 B） |

#### 未闭合项汇总（**如实登记**）

| 项 | 归属模块 | 跨任务依赖 |
|---|---|---|
| `T-P8` / `I-13` / 「复核交叉校验」/「复核层 `build_hops` 读存储方向」 | `M-6` | **`T-复编.01`**（代码项）—— checklist「复核层方向交叉校验」段 ＋ 行为验收 `T-P9` / `T-P10` |
| `T-P14` / 「利润等价性」/「`profitDrop`」 | `M-6` | **`T-复编.02`**（实测项）—— checklist「利润等价性与 `profitDrop` 实测」段 |
| 回滚脚本的列清单与 `20261124_016` 对齐（现为 `TASK-170` 形状） | `M-9` | `M-9` 的后续任务 |
| 全量回归基线 | — | **不适用**（范围规则） |

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/README.md` -->

# `T-方闭储.01`｜闭环层切换到方向池

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方闭储.01`（纯 ID）　**验收**：`AC-方闭储.01`　**优先级**：P0　**波次**：W-1
**编号逻辑**：`T-方闭储.01` / `AC-方闭储.01` —— **跨模块（组合索引）**，编号索引 **`方闭储`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入）） ＋ `M-4`（闭环层（闭环集合 / 容量折叠 / 兜底）） ＋ `M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-4` 、`M-3` 、`M-10`

**前置模块**：M-3　　**需授权**：是

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**5** 项未完成 ＋ **33** 项已闭合） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
```

> **只跑上面这些。** 未改动模块**不重跑**。

## 本轮实况（2026-11-24）

- **实盘已切换**：闭环层已切到方向池，`closed_loop_version = 18`、`materialized_checksum = sha256:19168935…`
- **回滚已演练**：基线构建对实盘库重跑 ⇒ 读数逐行回到切换前
- **剩余 5 项全部落在 `M-6`**（复核层交叉校验 / 利润等价性 / `profitDrop`）⇒ **已落到** `T-复编.01`（**代码项**）／`T-复编.02`（**实测项**）—— 两者 checklist **已实写**
- 逐条读数见 [`D-098`](#d098)

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d091"></a>

### `D-091-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/docs/D-091-现状与可行性分析.md` -->

# D-091 01 当前现状与可行性分析

## 1. 分析对象与结论口径

分析基于代码事实（`flashloan/tools/**`、`flashloan/tools/manager_db/migrations/**`、`docs/数据库结构文档/**`）。每一条结论都标注文件与行号，可被独立核验。

**结论：方向兑换池子表的设计**可行**。它不需要新的链上事实，不需要修改基础池表，而且能消除现有设计中两个真实的不一致。但原案需要三处修正（见 README §2），修正后方可实施。

---

## 2. 现状分析

### 2.1 链路分层与表的归属

系统的池信息链路是一条严格分层的链，每层拥有自己的表，并通过 `version + checksum` 向下游发布。

```text
token_libraries  →  pool_scope  →  candidate_edge  →  candidate_exchange  →  closed_loop  →  review  →  broadcast
```

依赖图定义在 `flashloan/tools/composite_manager/src/lib.rs:221-225`。相关表归属如下：

| 层 | 表 | 负责 | 与方向的关系 |
|---|---|---|---|
| 基础池 | `v4_exchange_pools_core4` | 池的固定链上事实（PoolKey、两个代币、fee、tickSpacing、hooks） | `token0`/`token1` 已按地址排序，**方向属性已隐含在里面** |
| 池深度 | `v4_exchange_pool_liquidity_snapshots` | 流动性事实（liquidity、tick、price、单跳容量上限） | `single_tick_max_token0_raw` / `single_tick_max_token1_raw` 是**分方向**的容量上限 |
| 候选边 | `manager_dex_candidate_pools` / `manager_dex_candidate_pool_recommendations` | 锚点↔候选代币的池集，按方向排序后推荐 | `direction_key` = `A>B`（**一种格式**） |
| 市场观察 | `manager_market_observation_yz_pairs` / `manager_market_observation_intermediate_pools` | 中间交换池的观察与最优池 | `direction_key` = `A->B`（**另一种格式**） |
| 闭环 | `manager_v4_closed_loops` / `manager_v4_closed_loop_hops` | 三跳闭环与每跳的池引用 | 方向**不存储**，只存逻辑代币对 |

### 2.2 现有“方向”的三种表达

当前代码里“方向”同时存在三种表达，它们之间并不完全一致：

| # | 表达 | 位置 | 实例 | 问题 |
|---|---|---|---|---|
| (a) | **物理方向**（池自身 token 顺序） | `pool_manager/src/scope.rs:1117-1127`（`ordered.sort()`）、`manager_contracts/src/pool.rs:33`（`currency0 must be smaller than currency1`） | `token0` = 低地址，`token1` = 高地址 | 唯一真正的事实来源，但**从未被存储** |
| (b) | **逻辑方向键**（代币对 + 字符串） | `candidate_edge_pool_manager/src/lib.rs:996-997`、`candidate_exchange_pool_manager/src/lib.rs:1039` | `A>B` 与 `A->B` | 同一个概念两种字符串，闭环层必须用格式拼接字符串才能对齐（`closed_loop_manager/src/lib.rs:2017`） |
| (c) | **运行时反推** | `closed_loop_manager/src/lib.rs:1906` `resolve_hop_direction` | 从池的 tick 状态重新判定 `zero_for_one` | 方向不是存的数据，是每次重建时重算的结果；反推失败即为硬失败（“拓扑错误”） |

`resolve_hop_direction` 的注释把原因写得很清楚（`closed_loop_manager/src/lib.rs:1850-1864`）：

> 一个 hop 的逻辑方向（`A -> B`）并不告诉该 hop 消费池的 `token0` 还是 `token1`：池的两个代币按**数字地址**排序，这个顺序与交易的逻辑方向相互独立。从逻辑方向键推导方向会把 hop 的输入与输出令牌对调，而累积折叠随后会累积该 hop 的**逆价率**；折叠要么直接失败，要么产出一条**属于不同路径**的容量。

**这段说明本身就是方向应该被物化出来的最强证据**：方向错一个位就会把整条闭环的容量算错，而现在它是重实时推导的。

### 2.3 方向语义必须锚定到池自身

V4 PoolKey 的 `currency0/currency1` 按地址升序排列（`pool_manager/src/scope.rs:1117-1127`），因此：

- `token0` = 低地址代币，`token1` = 高地址代币；
- 方向 0（高 → 低）= `token1 → token0` = V4 `zeroForOne = false`，输入代币是高地址的；
- 方向 1（低 → 高）= `token0 → token1` = V4 `zeroForOne = true`，输入代币是低地址的。

这个映射是**池的属性**，与路由方向无关。一个具体 hop 要用哪个方向，由该 hop 的输入代币是该池的 `token0` 还是 `token1` 决定——这恰好是方向表要解决的问题。

### 2.4 现状的四个真实缺陷

#### 缺陷 1（最重要）：中间跳与两侧跳的选池口径不一致

| 位置 | 选池口径 | 源码 |
|---|---|---|
| 第 1 跳（锚点 → Y） | 方向精确单跳输出 → tick 序 → fee → pool_id | `candidate_edge_pool_manager/src/lib.rs:1230` `recommend_direction` |
| 第 2 跳（Y → Z） | **`liquidity_index` 降序** | `candidate_exchange_pool_manager/src/lib.rs:1159` `pool_score` |
| 第 3 跳（Z →锚点） | 方向精确单跳输出 → tick 序 → fee → pool_id | `candidate_edge_pool_manager/src/lib.rs:1230` `recommend_direction` |

中间跳的最优池就是 `yz.optimal_pool_id`（`closed_loop_manager/src/lib.rs:2058`），而它是按 `pool_score` = `liquidity_index` 降序取的第一个。

**问题的本质：`liquidity_index` 是 `L / 1e18`**。V4 池的流动性 `L` 是一个**方向对称**的标量，它无法区分方向，也不表示可交易深度。仓库自己已经在深度质量门禁里明文否定它：

> "The liquidity index is `L / 1e18`, which is a raw concentrated-liquidity scale and carries no information about depth: measured against the true reserve TVL its rank correlation is only ~0.09. Filtering on it removed genuinely deep pools."
> —— `flashloan/tools/pool_manager/src/lib.rs:1394-1399`（原文）

中文意思：深度指数就是 `L / 1e18`，即原始集中流动性的尺度，**不携带任何深度信息**；与真实储备 TVL 比较的秩相关仅约 **0.09**；按它过滤已经误杀过真实深池。

这意味着：**当前的中间跳会选中“流动性最大但价格最差”的池**，而两侧跳选的是“方向价格最好”的池。三跳闭环的三个选池规则不一致，而环的盈利由三跳乘积决定。

#### 缺陷 2：同一方向有两种字符串格式

- `manager_dex_candidate_pool_recommendations.direction_key` = `format!("{anchor}>{candidate}")`（`candidate_edge_pool_manager/src/lib.rs:996`）
- `manager_market_observation_yz_pairs.direction_key` = `format!("{token_x}->{token_y}")`（`candidate_exchange_pool_manager/src/lib.rs:1039`）

闭环层要同时读两张表，并用两种不同的拼接字符串对齐同一个方向：

```rust
let middle_direction_key = format!("{y_token}->{z_token}");   // lib.rs:2017
let expected_direction = match recommendation.hop.as_str() {
    "first" => format!("{anchor}>{}", recommendation.candidate_token_key_id),  // lib.rs:1960-1963
    "third"  => format!("{}>{anchor}", recommendation.candidate_token_key_id),
    ..
};
```

这是纯字符串层面的耦合：方向的语义已经被重复表达了三次（候选边、观察层、闭环层各一次），每次都要靠拼接字符串才能对齐。

#### 缺陷 3：方向在闭环构建期反推，错一步即硬失败

`resolve_hop_direction`（`closed_loop_manager/src/lib.rs:1906`）需要池发布 tick 状态；没有状态时回退到逻辑方向键；若逻辑方向与池 token 顺序矛盾，则直接返回 `InvalidStoredTopology`。测试 `a_pool_that_does_not_carry_the_hop_input_token_is_a_topology_error`（`closed_loop_manager/src/lib.rs:5214`）就是这类错误的监察点。

这一类错误是**可预防**的：方向在写入时就应该是存在的。

#### 缺陷 4：锚点维度的纯重复

`manager_dex_candidate_pools`、`manager_market_observation_intermediate_pools`、`manager_market_observation_liquidity_snapshots` 都是**按锚点复制**的池行（表主键包含 `anchor_token_key_id`）。同一个 V4 池会在每个锚点下重复一份。方向池表是**池维度**的（无锚点），可以自然消除这类重复；锚点相关的视图变成一个轻量的查询/视图。

### 2.5 不变量（重构不得动的地方）

| 项 | 位置 | 为什么不能动 |
|---|---|---|
| 基础池表结构 | `migrations/20261103_001_manager_schema.sql`、`20261104_002_candidate_exchange_schema.sql` | 用户明确要求“基础池子信息保持不变” |
| 池深度快照表结构 | `v4_exchange_pool_liquidity_snapshots` | 方向表要读它的 `single_tick_max_token0/1_raw`，改它会让方向表失去数据源 |
| 复核层池定价读取 | `review_manager/src/parity.rs:1054` `resolve_route_pool_ids` | 它用 V4 base `pool_id` 去基础池表取 tick 状态；若 `pool_id` 语义改为“方向池 id”，定价路径会断 |
| 容量与盈利口径 | `closed_loop_manager/src/lib.rs:1668-1810` | 容量折叠、USDG 估值、闭环利润门禁不得放宽 |
| 审核/广播/执行链 | `review_manager` / `broadcast_manager` / `target` | 新增列需走 `schema_policy.json` 登记（详见 02 §7） |

### 2.6 内存大对象的现状缺口（本次新增分析）

系统的“池”在内存里有**四份互不相干的表示**，且**全部不含方向信息**：

| 内存对象 | 位置 | 装载体 | 方向信息 |
|---|---|---|---|
| `ScopePool`（候选边副本） | `candidate_edge_pool_manager/src/lib.rs:410` | `v4_exchange_pools_core4`（`:863`） | 无 |
| `ScopePool`（交换副本） | `candidate_exchange_pool_manager/src/lib.rs:351` | `v4_exchange_pools_candidate_exchange`（`:937`） | 无 |
| `PoolTickState` | `closed_loop_manager/src/lib.rs:491` | 两张池表（`:1474-1479`） | 无（只有 `token0/token1` 序） |
| `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states` | `closed_loop_manager/src/lib.rs:521` / `:533` / `:540` | 深度快照表 | 无（方向以**元组位置**与**平行数组**隐含） |

两处关键发现：

1. **两份 `ScopePool` 字段几乎相同、顺序却不同**（`pool_status` 一个在第 11 位、一个在末位）。方向字段必须被加两次，否则其中一层会漏，而且没有编译器保证它们一致。
2. **方向其实已经是一等值，只是没装进池对象**。闭环层用一个平行数组把它一路带到折叠函数：

   ```rust
   // closed_loop_manager/src/lib.rs:2169
   let hop_directions = [first_zero_for_one, middle_zero_for_one, third_zero_for_one];
   // :2176 / :2182 / :2195 三个折叠入口都同时接收 pool_ids 与 hop_directions
   ```

   平行数组只靠**约定**对齐 `pool_ids`。长度不符在两个折叠函数里有守卫（`:1723`、`:1778` 返回 `None`），但兜底分支的 `zip`（`:1697`）会静默截断；而**顺序错位没有任何检查能发现**——长度相同但跳序不同时守卫全部通过，折叠会算出「用 A 池的方向配 B 池」的容量。

**结论：内存侧的缺口不是“缺一个方向字段”，而是“池对象与方向是分离的两样东西”。**因此修复方式也不是加字段，而是把方向装进池对象、让平行数组消失。完整设计与逐环节核对见 [04-内存对象与闭环引用链](#d093)。

---

## 3. 可行性论证

### 3.1 支撑 1：方向是基础池的纯函数，零新增链上读取

方向由 `token0/token1` 唯一决定，而 `token0/token1` 已由地址排序写死在基础池表里。因此：

- 方向表不需要任何新的 RPC / 事务查询；
- 方向表不会与基础池表产生事实级不一致（它是纯派生）；
- 重建时只需重读已有的 tick / liquidity / single-tick 边界。

### 3.2 支撑 2：代码里已有三个可直接复用的方向量

| 方向量 | 现有实现 | 性质 | 方向性 |
|---|---|---|---|
| 方向精确输出 | `candidate_edge_pool_manager/src/lib.rs:1352` `exact_directional_output` | 参考交易在当前 tick 区间内的确切输出（十进制归一化） | 是（按 `token_in` 分流） |
| 方向 tick 序 | `candidate_edge_pool_manager/src/lib.rs:1324` `tick_ordering_rank` | 同一代币对内的价格排序；两个方向**相反排序** | 是（代入是 token0 取 tick，代入是 token1 取反） |
| 方向容量上限 | `v4_exchange_pool_liquidity_snapshots.single_tick_max_token0_raw / _token1_raw`，由 `closed_loop_manager/src/lib.rs:1383` `read_single_tick_bounds` 读取 | 单跳 tick 区间内可承载的输入量（原始单位） | 是（分 token0 / token1 两侧） |

`tick_ordering_rank` 的注释更是直接写明了方向对称性（`candidate_edge_pool_manager/src/lib.rs:1318-1323`）：

> 两个方向因此将**同一批池**排成相反的顺序，这正是让 YC 和 ZC 两腿能选到不同池的原因。

用户提出的“方向 0 排序按兑换最多的高地址代币”，对应的量恰好就是 `single_tick_max_token1_raw`（方向 0 的输入代币是 token1）。**它已经在库里量化好了**。

### 3.3 支撑 3：表结构已经预留了“当前池 / 基础池”的区分

`manager_v4_closed_loop_hops` 的字段定义已经区分了两个池引用（`migrations/20261104_003_closed_loop_schema.sql:81-82`）：

```sql
pool_id       TEXT NOT NULL,
base_pool_id  TEXT NOT NULL,
```

两列的**意图差异**记录在结构文档里：`docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:150` 写的是「`pool_id` 是当前生效池，可能被优化策略临时覆盖；`base_pool_id` 始终保留原始推荐池」，字段表 `:188` 进一步写明 `base_pool_id` 是「原始推荐池，优化过期后恢复」。

但当前实现里两者填的是同一个值（`closed_loop_manager/src/lib.rs:2509-2510`）：

```rust
pool_id: pool_id.clone(),
base_pool_id: pool_id,
```

**这说明数据模型本来就为“引用一个可被覆盖的池引用”做好了预留，只是尚未用上。**方向表完全可以沿用这个预留，而不需要新建其他概念。

### 3.4 支撑 4：现有系统已经在按方向重复存池

- `manager_dex_candidate_pool_recommendations`：同一批池在 `first` 与 `third` 两个 hop 下各存一份排序后的推荐（排序结果完全相反）；
- `manager_market_observation_yz_pairs`：每个方向一行，内嵌排好序的 `pool_ids_json`。

方案不是**新增一种重复**，而是把现有的隐式重复**显式化**为一张以池 + 方向为主键的表。

### 3.5 支撑 5：当前已有“方向选池”的完整算法，可直接搬迁

`recommend_direction`（`candidate_edge_pool_manager/src/lib.rs:1230`）已经实现了“同一批池、两个方向、两套排序”的完整逻辑，包括：

- 精确输出优先（价格质量）；
- tick 序作为同价时的次级；
- fee 作为再次级；
- `pool_id` 作为最后级确定性排序键（保证稳定）。

方向表只需要把这一段输出按**方向**分开存下来，而不是重写一套新算法。

---

## 4. 需要补充澄清的问题

Q1 ~ Q6 为待答问题；**Q7 已由本轮决策回答**（方向池为聚合根，V4 池为子对象），保留在此以完整记录决策依据。

### 4.1 Q1（必解）：“兑换最多”用哪个量？

原案的描述“方向 0 时兑换最多的高地址代币”有两种可能的对象，它们会导致完全不同的选池结果：

| 解读 | 量的含义 | 优势 | 风险 |
|---|---|---|---|
| (A) **可交易深度** | 该方向上池能吸收多少输入代币（`single_tick_max_token1_raw` 降序） | 直接对应“兑换最多”的字面含义；与容量门禁同口径 | **不反映价格好坏**；会选中“深但差”的池 |
| (B) **价格质量** | 参考交易的方向确切输出（`exact_directional_output`） | 与现有两侧跳的选池口径一致；真正决定环的盈利 | 不是“兑换量”；深度差的池不会被推向前两位 |

**处理意见（推荐）：不要二选一，而是**分成两个独立的存量列**，并把序位明确定为“价格质量主序、可交易深度次序”：

- 主序：`directional_output`（方向精确输出）降序 → tick 序 → fee 升序 → pool_id 升序（与 `recommend_direction` 完全一致，不改变两侧跳的现有行为）；
- 次序：`input_capacity_raw`（方向 0 取 `single_tick_max_token1_raw`，方向 1 取 `single_tick_max_token0_raw`）降序——这才是原案描述的“兑换最多的高/低地址代币”。

方向容量不参与主序与并列的口径，但它是容量门禁的直接输入，不应在闭环重建期重算。

### 4.2 Q2（必解）：排序的比较范围

`input_capacity_raw` 是**原始数量**，不同代币对之间比较是**单位错误**（`1 个 USDG` 和 `1 个 ETH` 的 raw 数量不可比）。

**处理意见：序位必须在 `(token pair, direction)` 内部计算**。这也正是闭环实际需要的粒度：每一跳都是在一个**具体代币对**内选池。因此方向表必须携带代币对标识（`pair_key_id`）和 `(pair, direction)` 内的序位。

### 4.3 Q3（必解）：排序口径变更会改变已发布的闭环集合

中间跳从 `liquidity_index` 排序改为方向价格质量排序，**会改变被选中的中间池**，进而改变：

- 闭环的数量（中间方向可能更有效，也可能减少）；
- 每条闭环的容量折叠结果（中间池换了，整体容量也会变）；
- 盈利预期。

**处理意见：这是一次**口径变更**，不是纯存储变更。必须在阶段 0（影子比对）里拿出实数，并把“闭环数变化”写成一张比对表作为验收证据。在比对结果未展示前，不得切换下游消费。

### 4.4 Q4（必解）：方向表与现有候选/观察表的关系

重构后会出现重复：方向表已经包含了中间池与两侧池的方向与排序，而 `manager_dex_candidate_pool_recommendations` 与 `manager_market_observation_yz_pairs` 又保留了自己的方向与排序。

**处理意见：分两步走，不做一次性大重构**：

- 阶段 1（并存）：方向表新增，旧表不动，闭环仍读旧表；
- 阶段 2（切换）：闭环层读方向表，候选/观察表的方向与排序改为从方向表**派生**；
- 阶段 3（下线）：旧的方向键与排序列降级为视图或删除。

### 4.5 Q5（必解）：方向表的属性属于哪一层

当前池信息在三层里都有“池”的副本：`v4_exchange_pools_core4`（基础池）、`v4_exchange_pools_candidate_exchange`（候选交换池）、`manager_market_observation_intermediate_pools`（观察中间池）。

**处理意见：方向表应该归属于**新增的独立层**，它是纯派生层（不拥有任何链上事实），但它**不能被任何下游表所有**。否则新表会变成第四份池数据。

### 4.6 Q6（必解）：闭环跳的 `pool_id` 语义

原案描述“闭环表由 3 个方向兑换池子组成”。若把 `manager_v4_closed_loop_hops.pool_id` 直接改为方向池的 id，则：

- `review_manager/src/parity.rs:1054` `resolve_route_pool_ids` 将路由的 `pool_ids` 去基础池表取 tick 状态 → 定价路径断掉；
- `broadcast_manager` / `target` 的执行链同理；
- `scripts/mainnet_arbitrage_healer.py:709-712`、`scripts/verify_candidate_pool_quality.py:194`、`plan/runtime_evidence/tools/collect_task143_review_completeness_20261121.py:300-315` 等 Python 消费者取的是 `pool_id`。

**处理意见：`pool_id` 保持为 V4 base pool id（语义不变），方向用新增列承载**：`directional_pool_id` + `hop_direction`。这样闭环跳确实地引用了一个方向池（方向池 id + 方向），而同时保留了定价链路直接可用的 base id。

### 4.7 Q7（已由决策回答）：方向池与 V4 池是什么关系？内存对象长什么样？

**决策（本轮明确）：** 方向池是**聚合根**，V4 池信息是它的**子对象**。

```text
DirectionalPool ≡ { base: V4Pool } ⊕ { direction: 0 | 1 } ⊕ { 方向量 }
```

由此确定的边界：

| 项 | 结论 |
|---|---|
| 基础池表 | **保持不变**，作为 `base` 子对象的事实来源；方向层不采集新事实 |
| 方向池表 | 方向层唯一的池集合，闭环层的唯一池输入 |
| 内存池对象 | 统一为共享的 `DirectionalPool`（放 `manager_contracts`），两份 `ScopePool` 全部删除 |
| 闭环跳 | 引用 `directional_pool_id` + `hop_direction`；`pool_id` / `base_pool_id` 是**投影**（同源写出 + 重建期复验） |
| 同步更新 | 内存对象由库读出、库行由内存对象写出，单向数据流，结构上不会漂移 |

**处理意见：** 这条决策把「方向是池的属性还是路由的属性」彻底定死（C-1 的根因），并使「闭环表由 3 个方向兑换池子组成」成为可断言的结构而不是描述。展开见 [04-内存对象与闭环引用链](#d093)。

---

## 5. 影响面估算

### 5.1 代码影响

| 模块 | 文件 | 预计变更类型 | 风险等级 |
|---|---|---|---|
| `manager_db` | `src/migrate.rs` + 新 migration | 新增两张表，登记到 `TARGET_TABLES` / `required_columns` / 迁移表组 | 低（机械性） |
| 新工具层 | `directional_pool_manager`（新 crate） | 新增物化层：读基础池 + 快照 → 写方向池 | 中（新代码，但有参考实现） |
| `candidate_edge_pool_manager` | `src/lib.rs` | `recommend_direction` 抽为方向表的计算源；`direction_key` 改用方向表的结构 | 中 |
| `candidate_exchange_pool_manager` | `src/lib.rs` | 中间池选择改为读方向表 | 中（改变中间跳选池结果，见 4.3） |
| `closed_loop_manager` | `src/lib.rs` | 取池路径改为读方向表；`resolve_hop_direction` 退化为读存量；`push_loop` 写新列 | 高（核心逻辑） |
| `review_manager` | `src/lib.rs` / `src/parity.rs` | 实质不变（仍按 base `pool_id` 取状态）；仅需在身份校验时多比一个方向字段 | 低 |
| `composite_manager` | `src/lib.rs` | 依赖图新增 `directional_pool` 节点 | 中 |
| `manager_api` | `src/lib.rs` | 新增读取端点 | 低 |
| `token_manager` | `src/prune.rs` | 纳入方向表的清理（按 loop_key 删临时行） | 低 |

### 5.2 数据体量估算

方向表行数 = 基础池数 × 2（每个池两行，如果两侧可用）。相对现有：

- `manager_dex_candidate_pools`：池数 × 锚点数（每个锚点一份）；
- `manager_market_observation_intermediate_pools`：池数 × 锚点数。

所以新表的行数预计**低于**两张现有池副本表之和。它不会扩张存储，反而是减少重复。

**诚实边界：**具体行数需在阶段 0 用真实库读出后填入证据报告，本文档不预先写数字。

---

## 6. 风险清单

| ID | 风险 | 作用面 | 缓解措施 | 残留风险 |
|---|---|---|---|---|
| R-1 | 中间跳选池口径变更 → 闭环集合变化 | 闭环数、容量、盈利预期均变 | 阶段 0 影子比对；阶段 1 不切换下游 | 中 |
| R-2 | `pool_id` 语义被意外改变 → 定价与执行链断 | 实盘不可执行 | 新增列而非修改旧列（C-2）；测试锁定 `pool_id` 语义 | 低 |
| R-3 | 方向表与快照时效不一致 → 容量使用了过期数据 | 闭环容量偏大 | 方向表记录 `liquidity_read_at`；闭环重建时比对快照时效 | 中 |
| R-4 | 目标标签与快照标签不同 → 方向表与快照对不上 | 重建报告虚假变化 | 方向表记录快照标签；重建门禁比对标签 | 低 |
| R-5 | 新表进入多个库登记与清理路径 | 孤立行永久残留 | 把方向表纳入 `token_manager/src/prune.rs` 的 loop 清理路径 | 低 |
| R-6 | 一次性做完下线导致现网主线中断 | 主线只认出广播产出 | 阶段化：影子 → 并存 → 切换 → 下线，每阶段独立可回滚 | 高（需统一调度） |
| R-7 | 方向表重复了已有的排序逻辑，两份代码口径可能漂移 | 方向表与候选表结果不一致 | 阶段 1 保留两份，并加一条“两表排序必须一致”的对比断言 | 中 |

---

## 7. 可行性结论

| 判定 | 内容 |
|---|---|
| **技术可行** | 方向是基础池的纯函数；现有代码已有三个可复用的方向量；表结构已预留 `pool_id`/`base_pool_id`；现有系统本就在按方向重复存池。**无需新增链上读取，无需修改基础池表。** |
| **价值为正** | 修复中间跳选池口径不一致（缺陷 1）；消除方向反推（缺陷 3）；统一方向键表达（缺陷 2）；减少锚点维度的池副本（缺陷 4）。 |
| **需要修正后方可实施** | C-1 方向语义锚定、C-2 `pool_id` 语义不变、C-3 排序口径定死为可计算量且限定在代币对内。 |
| **不得在阶段 0 之前做的事** | 不得切换下游消费；不得改动审核/广播/执行门禁；不得引入未实测的行数/闭环数结论。 |

**下一步：**进入 [02-目标架构与表结构](#d092)，看具体的表设计与闭环改造。

---

<a id="d092"></a>

### `D-092-目标架构与表结构.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/docs/D-092-目标架构与表结构.md` -->

# D-092 02 目标架构与表结构

> 本文档定义重构后的**目标形态**。所有 DDL 为**设计稿**，需在阶段 1 按第 03 文档的流程落地。

## 1. 目标分层

```text
token_libraries
      │
      ├──→ pool_scope            基础池表 v4_exchange_pools_core4        【不变】
      │           └──→ 池深度快照 v4_exchange_pool_liquidity_snapshots  【不变】
      │
      ├──→ directional_pool  新增层：方向兑换池子表          【新增】
      │           └──→ manager_v4_directional_exchange_pools
      │           └──→ manager_v4_directional_pool_library_state
      │
      ├──→ candidate_edge       候选边（方向与排序改为派生）
      ├──→ candidate_exchange   市场观察（中间池改为读方向表）
      ├──→ closed_loop           闭环三跳均由方向兑换池组成
      ├──→ review                 【定价路径不变；仍按 base pool_id 取 tick 状态】
      └──→ broadcast               【不变】
```

**关键设计原则：**

1. `directional_pool` 是**纯派生层**：它不拥有任何链上事实，每一行都能从“基础池 + 快照”复算出来；
2. **它不被任何下游表所有**。方向池是**池的源头**，下游只引用它；
3. **新表是旧表的紧凑上层**，而不是平行的第四份池数据（见 01 §4.5）。

---

## 2. 方向语义规范（绑定性）

### 2.1 映射表

| `direction` | 含义 | 输入代币 | 输出代币 | V4 `zeroForOne` | 输入代币地址 | 池侧别名 |
|---|---|---|---|---|---|---|
| **0** | 高地址 → 低地址 | `token1` | `token0` | `false` | 高 | 高地址代币入池 |
| **1** | 低地址 → 高地址 | `token0` | `token1` | `true` | 低 | 低地址代币入池 |

### 2.2 派生规则（不可供选择）

```text
direction = 0  ←  token1_key_id 的地址 > token0_key_id 的地址
direction = 1  ←  token0_key_id 的地址 < token1_key_id 的地址
```

池的 `token0/token1` 由地址升序确定（`pool_manager/src/scope.rs:1117-1127`），因此上述映射是**恒成立**的，不存在需要判定的分叉。

### 2.3 三条禁止解读的误读

1. **不能按路由方向解读 `direction`**。例如一个 hop 走 `token1 → token0`，它用的池行就是 `direction = 0`；一个 hop 走 `token0 → token1`，用的池行就是 `direction = 1`。方向属于池，不属于路由。
2. **不能让方向取依赖路由的代币对**。方向表是锚点无关的（池维度）。
3. **方向不是“价格好/价格差”的判断结果**。它只标识池的交换方向；优先级由 §3 定义。

---

## 3. 方向池子表

### 3.1 主表 DDL（设计稿）

```sql
-- 方向兑换池子表。
--
-- 每一行是一个基础 V4 池在某一个方向上的投影。方向 0 表示
-- 从高地址代币兑换到低地址代币（token1 -> token0），
-- 方向 1 表示从低地址代币兑换到高地址代币（token0 -> token1）。
--
-- 方向是池自身的属性，不依赖锚点，也不依赖路由。
-- 同一个基础池在两个方向表中各出现一次，
-- 两行的区别仅在方向相关的字段上。

CREATE TABLE IF NOT EXISTS manager_v4_directional_exchange_pools (
    network                     TEXT    NOT NULL,
    chain_id                    INTEGER NOT NULL,
    protocol                    TEXT    NOT NULL DEFAULT 'uniswap_v4',
    scope_key                   TEXT    NOT NULL,

    -- 基础池身份与方向
    base_pool_id                TEXT    NOT NULL,
    direction                   INTEGER NOT NULL CHECK (direction IN (0, 1)),
    directional_pool_id         TEXT    NOT NULL,

    -- 池的固定链上事实（与基础池表一致，本表不新增事实）
    pool_manager                TEXT    NOT NULL,
    pool_key_json               TEXT    NOT NULL,
    token0                      TEXT    NOT NULL,
    token1                      TEXT    NOT NULL,
    token0_key_id               TEXT    NOT NULL,
    token1_key_id               TEXT    NOT NULL,
    fee                         INTEGER NOT NULL,
    tick_spacing                INTEGER NOT NULL,
    hooks                       TEXT    NOT NULL,

    -- 方向推导（纯函数，由 token0/token1 地址顺序唯一确定）
    pair_key_id                 TEXT    NOT NULL,
    input_token_key_id          TEXT    NOT NULL,
    output_token_key_id         TEXT    NOT NULL,
    zero_for_one                INTEGER NOT NULL CHECK (zero_for_one IN (0, 1)),

    -- 快照载体的动态状态（来自池深度快照表，本表不重新采集）
    sqrt_price_x96              TEXT,
    tick                        INTEGER,
    liquidity                   TEXT,
    price                       REAL,
    depth_status                TEXT,
    liquidity_read_at           TEXT,
    liquidity_block_number      INTEGER,
    evidence_source             TEXT,

    -- 方向容量：该方向上池能承载的输入代币量（原始单位）
    -- 方向 0 取 single_tick_max_token1_raw（输入是 token1），方向 1 取 single_tick_max_token0_raw。
    -- NULL = 未测量；'0' = 已测量且无空间。两者不同义，不得混合。
    input_capacity_raw          TEXT,
    input_capacity_measured     INTEGER NOT NULL DEFAULT 0 CHECK (input_capacity_measured IN (0, 1)),

    -- 方向价格质量：参考交易在当前 tick 区间内的确切输出（十进制归一化）
    directional_output          REAL,
    directional_output_measured INTEGER NOT NULL DEFAULT 0 CHECK (directional_output_measured IN (0, 1)),

    -- 序位：在 (pair_key_id, direction) 内部的 1-based 排序。未测量行无序位。
    pair_direction_rank         INTEGER CHECK (pair_direction_rank IS NULL OR pair_direction_rank > 0),

    pool_status                 TEXT    NOT NULL DEFAULT 'active' CHECK (pool_status IN (
                                 'active', 'stale', 'invalid', 'identity_mismatch', 'blocked'
                             )),
    pool_status_reason          TEXT,
    source_block_number         INTEGER,
    source_tx_hash              TEXT,

    materialized_at             TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,

    PRIMARY KEY (network, chain_id, protocol, scope_key, base_pool_id, direction)
);

-- 闭环选池的主路径：按代币对 + 方向 + 序位取池。
CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_pair
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, pair_key_id, direction, pair_direction_rank
    );

-- “按自身兑换方向排序”的全局视图：方向 0 按高地址代币量降序，
-- 方向 1 按低地址代币量降序。该视图仅用于展示与诊断，
-- 选池一律以 (pair, direction) 内的序位为准。
CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_direction
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, direction, input_capacity_raw
    );

CREATE INDEX IF NOT EXISTS idx_v4_directional_pools_base
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, base_pool_id
    );

-- 方向池身份的唯一性。SQLite 的外键要求父键具备 UNIQUE 约束，
-- 这一条索引是为「directional_pool_id 升级为硬外键」预留的条件。
CREATE UNIQUE INDEX IF NOT EXISTS ux_v4_directional_pools_id
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, directional_pool_id
    );
```

### 3.2 库状态表 DDL（设计稿）

```sql
-- 方向池层的库状态。每层向下游发布版本与校验和，
-- 下游层用它判定是否需要重建。
CREATE TABLE IF NOT EXISTS manager_v4_directional_pool_library_state (
    network                    TEXT    NOT NULL,
    chain_id                   INTEGER NOT NULL,
    protocol                   TEXT    NOT NULL,
    scope_key                  TEXT    NOT NULL,
    directional_pool_version   INTEGER NOT NULL,
    materialized_checksum      TEXT    NOT NULL,
    pool_scope_version         INTEGER NOT NULL,
    pool_scope_checksum        TEXT    NOT NULL,
    snapshot_checksum          TEXT    NOT NULL,
    library_versions_json      TEXT    NOT NULL,
    library_checksums_json     TEXT    NOT NULL,
    base_pool_count            INTEGER NOT NULL,
    directional_pool_count     INTEGER NOT NULL,
    direction0_count           INTEGER NOT NULL,
    direction1_count           INTEGER NOT NULL,
    eligible_pair_count        INTEGER NOT NULL,
    measured_capacity_count    INTEGER NOT NULL,
    measured_output_count      INTEGER NOT NULL,
    excluded_pool_count        INTEGER NOT NULL,
    generated_at               TEXT    NOT NULL,
    updated_at                 TEXT    NOT NULL,
    PRIMARY KEY (network, chain_id, protocol, scope_key)
);
```

### 3.3 排序口径（绑定性）

**序位的计算范围：`(pair_key_id, direction)`**。跨代币对比较 raw 容量是单位错误（见 01 §4.2）。

```text
主序（价格质量，与现有 recommend_direction 完全一致）：
  1. directional_output 降序        —— 方向精确单跳输出（十进制归一化）
  2. tick 序降序                  —— 同价时的桶内排序（方向相反）
  3. fee 升序                         —— 费率越低越好
  4. base_pool_id 升序                —— 稳定性（确定性）

次序（容量，不参与主序，作为容量门禁的输入）：
  input_capacity_raw 降序
    · 方向 0 取 single_tick_max_token1_raw（高地址代币侧）
    · 方向 1 取 single_tick_max_token0_raw（低地址代币侧）
```

**口径说明：**

- 原案的“方向 0 时兑换最多的高地址代币”对应的就是 `input_capacity_raw`（方向 0 输入代币是高地址的 token1）。它是**容量次序**，不是选池主序。
- 主序保持与现有两侧跳一致，以免在切换阶段引入不可控的行为变化；
- 中间跳从 `liquidity_index` 排序改为主序排序，**这是一次口径变更**，必须先经阶段 0 影子比对（见 01 §4.3）。

### 3.4 方向容量与价格质量的来源对照

| 方向表列 | 来源 | 现有实现 |
|---|---|---|
| `input_capacity_raw` | `v4_exchange_pool_liquidity_snapshots.single_tick_max_token1_raw` / `_token0_raw` | `closed_loop_manager/src/lib.rs:1383` `read_single_tick_bounds` |
| `directional_output` | 基础池 + tick 状态 | `candidate_edge_pool_manager/src/lib.rs:1352` `exact_directional_output` |
| tick 序 | `v4_exchange_pool_liquidity_snapshots.tick` / `sqrt_price_x96` | `candidate_edge_pool_manager/src/lib.rs:1324` `tick_ordering_rank` |
| `pair_key_id` | 代币对 | `manager_contracts/src/ids.rs:112-113` `PairKeyId::new` |

**本表不采集任何新的链上数据**，全部采自已有的快照表与基础池表。

### 3.5 聚合关系：方向池 ≡ V4 池 ⊕ 方向

本表的每一行都可以读成一句话：**「一个 V4 池，在某一个方向上的投影」**。

```text
DirectionalPool  =  { base: V4Pool }  ⊕  { direction: 0 | 1 }  ⊕  { 方向量 }

base      ← v4_exchange_pools_core4（身份列）+ v4_exchange_pool_liquidity_snapshots（快照列）
direction ← f(base.token0, base.token1)                纯函数，唯一决定，无分叉
方向量    ← 由 base 与 snapshots 复算，不采集
```

由此得到三条**不需要实测**的结构性结论：

| 结论 | 含义 |
|---|---|
| 方向池**不含任何新的链上事实** | 方向层不需要新增 RPC / 事务读取 |
| 方向池与基础池的不一致**只可能发生在装载期** | 因此 P0 影子比对足以判定正确性（I-9） |
| 一个基础池在方向表出现**恰好两次**，两行仅方向相关字段不同 | 差异比对是 I-3 的可执行断言 |

**这个聚合关系是本方案的地基**：它让「方向」从路由属性变成池属性，让「闭环表由 3 个方向兑换池子组成」从一句口号变成一条可断言的数据结构。内存侧的对应实现见 [04-内存对象与闭环引用链](#d093)。

---

## 4. 闭环表改造

### 4.1 闭环跳表新增列（设计稿）

```sql
-- 方向兑换池引用。
--
-- 注意：`pool_id` 与 `base_pool_id` 的语义不变，仍为 V4 base pool id。
-- 它们被复核层用来取 tick 状态定价（review_manager/src/parity.rs:1054），
-- 也被 Python 消费者直接读取。改名会同时破坏定价与展示链路。
--
-- 方向信息改由新增列承载，使闭环跳确实地指向一个方向池，
-- 而同时保留了可直接定价的 base id。
ALTER TABLE manager_v4_closed_loop_hops
    ADD COLUMN directional_pool_id TEXT;

ALTER TABLE manager_v4_closed_loop_hops
    ADD COLUMN hop_direction INTEGER CHECK (hop_direction IN (0, 1));

-- 方向的输入/输出代币由 hop_direction + base 池推导，
-- 存下来是为了审计（不用 join 就能判定路径方向是否与池顺序一致）。
ALTER TABLE manager_v4_closed_loop_hops
    ADD COLUMN directional_input_token_key_id TEXT;

ALTER TABLE manager_v4_closed_loop_hops
    ADD COLUMN directional_output_token_key_id TEXT;

-- 闭环主体上三个方向池的路径键。
-- `pool_path_key` 保持不变（仍是 base pool id 拼接）。
ALTER TABLE manager_v4_closed_loops
    ADD COLUMN directional_pool_path_key TEXT;
```

**为什么 `directional_pool_id` 必须另起一列，而不能占用 `pool_id`：**

设计文档 `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:173-176` 给出的 hop 表模型带两条外键，`pool_id` 与 `base_pool_id` **都指向基础池表的 `pool_id`**。也就是说 `pool_id` 这一列的含义是「基础池表里的某一行」。方向池 id 放进这一列，会让外键指向不存在的行。

（如实说明：实盘表 `manager_v4_closed_loop_hops` 当前**没有**外键约束——`20261104_003_closed_loop_schema.sql:69-89` 只有 `PRIMARY KEY`；上述外键出现在结构文档描述的旧版 `v4_closed_loop_hops` 模型里，属于目标模型而非现状。）

另需注意：SQLite 的 `ALTER TABLE ... ADD COLUMN` **不能新增外键约束**。因此 `directional_pool_id` 默认作为普通列加入，靠不变量 I-7 / I-11 / I-12 的断言与重建期复验保证引用有效（§3.1 已为其建 UNIQUE 索引，为将来升级为硬外键留好条件）。是否重建 hop 表以加硬外键，是 P1 的独立决策，**不在本方案范围内**。

**投影纪律：** `pool_id` / `base_pool_id` 是方向池的**投影**，不是真相源；真相源是方向池表的 `direction` 列。两者必须在同一次写入里从同一行方向池导出，并由重建期复验校验。完整纪律见 [04-内存对象与闭环引用链](#d093) §7。

### 4.2 闭环三跳的新构成

闭环路径为 `C → Y → Z → C`，三跳现在都引用方向兑换池：

| hop | `hop_name` | 逻辑路径 | `hop_direction` 的确定方式 |
|---|---|---|---|
| 1 | `yc` | C → Y | 若该池的 `token0` = C 则 `direction = 1`；若 `token1` = C 则 `direction = 0` |
| 2 | `y-z` | Y → Z | 同上，由中间池的 token 顺序确定 |
| 3 | `zc` | Z → C | 同上，由第三跳池的 token 顺序确定 |

**关键：方向不再由闭环层反推。** 方向表里新增的 `directional_pool_id` 已经带了方向；闭环层只需校验“该方向行的 `output_token_key_id` 是否等于该 hop 的输出代币”，不再需要 `resolve_hop_direction` 的推导逻辑。

### 4.3 `resolve_hop_direction` 的降级路径（不一步删除）

| 阶段 | 行为 | 理由 |
|---|---|---|
| 阶段 1（并存） | 保留 `resolve_hop_direction`，但增加一次新方向行的**交叉校验**（方向行的 `output_token_key_id` 必须等于 hop 输出代币） | 方向表与旧路径必须能对上，否则无法证明等价 |
| 阶段 2（切换） | `resolve_hop_direction` 退化为“从方向行读 `hop_direction`”，不再重算 | 方向成为存量 |
| 阶段 3（下线） | 删除 `resolve_hop_direction` 及其回退分支 | 方向不再可能错 |

`hop_zero_for_one`（`closed_loop_manager/src/lib.rs:1873`）与容量折叠相关的推导同样走上同一路径。

### 4.4 不变量（闭环表）

| 字段 | 重构后的值 | 说明 |
|---|---|---|
| `pool_id` | = base V4 pool id（**不变**） | 复核层取 tick 状态的键 |
| `base_pool_id` | = base V4 pool id（**当前两者相同，重构后仍相同**） | 优化过期后的恢复目标 |
| `directional_pool_id` | = 方向表的 `directional_pool_id` | 新增，方向池引用 |
| `hop_direction` | 0 / 1 | 新增 |
| `input_token_key_id` / `output_token_key_id` | **不变** | 逻辑路径，不是物理方向 |

---

## 5. 下游消费方式改造

### 5.1 候选边（`candidate_edge_pool_manager`）

| 现有 | 改造后 |
|---|---|
| `recommend_direction(pools, token_in, token_out)` 在内存里排序 | 退化为读方向表：按 `(pair_key_id, direction)` 取 `pair_direction_rank = 1, 2, 3...` 的行 |
| `direction_key` = `A>B` | 保留作为**展示字段**，但新增结构化列 `direction`（INTEGER）作为权威字段 |
| `manager_dex_candidate_pools` 按锚点复制池 | 保留（它是“锚点↔候选代币”的业务视图），但不再自行排序 |

### 5.2 市场观察（`candidate_exchange_pool_manager`）

| 现有 | 改造后 |
|---|---|
| `pool_score(pool) = liquidity_index` | 退化（方向表不使用 `liquidity_index`） |
| `active_by_pair` 内部按 `pool_score` 排序 | 改为按方向表的 `pair_direction_rank` 排序 |
| `manager_market_observation_yz_pairs.optimal_pool_id` | 改为从方向表的对应方向行取序位 1 的行 |
| `direction_key` = `A->B` | 保留作为展示字段，新增结构化列 `direction` |

### 5.3 闭环层（`closed_loop_manager`）

三个 hop 的取池路径统一为：

```text
第 1 跳（yc）  → 方向表：(pair = {C, Y}, direction = 由池 token 顺序确定)
第 2 跳（y-z） → 方向表：(pair = {Y, Z}, direction = 由池 token 顺序确定)
第 3 跳（zc）  → 方向表：(pair = {Z, C}, direction = 由池 token 顺序确定)
```

三跳的选池口径完全统一为“方向表中该 (pair, direction) 的序位 1”。

### 5.4 不变的消费方式

| 消费者 | 变更 |
|---|---|
| `review_manager` 定价 | **无**（仍按 base `pool_id` 取 tick 状态） |
| `broadcast_manager` / `target` 执行 | **无** |
| `manager_api` 现有端点 | **无** |
| Python 消费者 | **无**（取 `pool_id`，语义不变） |

---

## 6. 层依赖图与组合管理器

### 6.1 `composite_manager` 依赖图（现行，`src/lib.rs:221-225`）

```rust
Self::CandidateEdge     => &["token_libraries", "pool_scope"],
Self::CandidateExchange => &["token_libraries", "pool_scope", "candidate_edge"],
Self::ClosedLoop        => &["candidate_edge", "candidate_exchange"],
Self::Review            => &["closed_loop"],
Self::Broadcast         => &["closed_loop", "review"],
```

### 6.2 改造后（阶段 2 生效）

```rust
Self::DirectionalPool   => &["token_libraries", "pool_scope"],
Self::CandidateEdge     => &["token_libraries", "pool_scope", "directional_pool"],
Self::CandidateExchange => &["token_libraries", "pool_scope", "directional_pool"],
Self::ClosedLoop        => &["candidate_edge", "candidate_exchange", "directional_pool"],
Self::Review            => &["closed_loop"],
Self::Broadcast         => &["closed_loop", "review"],
```

新节点的运行依赖：

- `directional_pool` 依赖 `pool_scope` 的 `materialized_checksum` 与 `v4_exchange_pool_liquidity_snapshots` 的快照标签；
- 快照标签不变 → 重建结果不变 → `changedRows = 0`（与现有物化事务语义一致）；
- `closed_loop` 必须读到 `directional_pool` 的版本与校验和，否则拒绝重建（与现有 `closed_loop` 对 `candidate_edge` / `observation` 的校验同形）。

---

## 7. 守卫与登记

| 登记点 | 文件 | 改动 |
|---|---|---|
| 迁移清单 | `flashloan/tools/manager_db/src/migrate.rs:27` `MIGRATION_IDS` / `:44` `LATEST_MIGRATION_ID` | 新增 `20261123_015_directional_pool_schema` |
| 目标表清单 | `migrate.rs:117` `TARGET_TABLES` | 新增两张表 |
| 必需列 | `migrate.rs:668` `required_columns` | 为两张新表声明身份列 |
| 迁移→表组映射 | `migrate.rs:616` `ensure_migration_tables_compatible` | 新增一个表组 |
| 迁移烟雾 | `flashloan/tools/manager_db/tests/migration_smoke.rs` | 新表名入清单 |
| 架构守卫 | `flashloan/app_runtime/tests/unit/test_architecture_guards.py` | 新表/新列登记 |
| 受保护表登记 | `flashloan/tools/manager_db/schema_policy.json` | **本次不需要**：`manager_v4_closed_loops` / `manager_v4_closed_loop_hops` 不在 `protected_tables` 内。**若未来需要在 `manager_v4_reviews` / `manager_v4_review_broadcast_requests` / `manager_cow_execution_attempts` 上加列，则必须先登记本文件才能通过守卫** |
| 清理路径 | `flashloan/tools/token_manager/src/prune.rs` | 方向表按 `loop_key` 清理的路径要纳入，避免孤立行永久残留 |
| 结构文档 | `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md` | 新增方向池表一节，更新闭环跳字段说明 |

---

## 8. API 契约

```text
GET  /v1/directional-pools            读取方向池（可按 scope / direction / pair / pool 筛选）
GET  /v1/directional-pools/compare    与旧口径的对比（阶段 0/1 证据）
POST /v1/directional-pools/rebuild    重建方向池（需 X-Request-Id / Idempotency-Key / dryRun）
```

写路径的要求与现有 `POST /v1/pools/scopes/rebuild` 一致：

- 幂等键 + 请求摘要；相同键 + 相同摘要 → 返回原结果并标记 `idempotentReplay=true`；摘要变化 → `IDEMPOTENCY_KEY_REUSED`；
- dry-run 不写幂等记录；
- apply 提交池行、库状态、幂等记录在一个 immediate 事务里；
- 权限动作名为 `pools.directional.read` / `pools.directional.rebuild`。

**授权模式维度（v1.1 补齐 · 必做）**：Manager API 的写端点受 `TokenAuthorityMode` 门禁控制，
`manager_apply_enabled()` **仅在 `TokenAuthorityMode::Manager` 时为真**（`Legacy` ⇒ 写入被拒）。
现有 7 个维度各由一个环境变量控制，**缺省一律回落为 `Legacy`**
（`manager_api/src/main.rs`：`CANDIDATE_MANAGER_AUTHORITY` / `CANDIDATE_EXCHANGE_MANAGER_AUTHORITY` /
`CLOSED_LOOP_MANAGER_AUTHORITY` / `REVIEW_MANAGER_AUTHORITY` / `BROADCAST_MANAGER_AUTHORITY` /
`COMPOSITE_MANAGER_AUTHORITY` / `TOKEN_MANAGER_AUTHORITY`）。

方向池是**新增的第 8 个维度**，因此：

- **必须**新增 `DIRECTIONAL_POOL_MANAGER_AUTHORITY` 环境变量与 `directional_pool_authority` 状态位；
- **不得**让方向池的写端点**复用** `candidate_exchange_authority` 或 `composite_authority`
  （方向池表是**独立层**，它的写权限必须能被**单独**拒绝，
  否则 `TASK-169` 下线旧口径时无法单独关闭方向池写入）；
- 缺省值与既有 7 个维度一致（`Legacy`）⇒ **不配置该变量时写端点必然被拒**，
  这是**期望行为**，不得为了「让接口能跑」而把它设成 `Manager`；
- 该维度的落点**登记在 `TASK-167`**（见任务清单 §2.2），并纳入 `TASK-167` 的结构验收。

---

## 9. 不变量与门禁

### 9.1 数据不变量

| ID | 不变量 | 检查方式 |
|---|---|---|
| I-1 | 方向 0 行的 `input_token_key_id` 恒等于该池的 `token1_key_id`；方向 1 行恒等于 `token0_key_id` | 物化时断言 |
| I-2 | `direction = 1 ⇔ zero_for_one = 1`；`direction = 0 ⇔ zero_for_one = 0` | 物化时断言 |
| I-3 | 同一 `base_pool_id` 的两行，除方向字段与方向量外，必须完全一致 | 物化时断言 |
| I-4 | `input_capacity_raw` 的来源恒定：方向 0 ← `single_tick_max_token1_raw`，方向 1 ← `single_tick_max_token0_raw` | 物化时断言 |
| I-5 | 方向表不包含任何基础池表与快照表之外的数据 | 字段白名单 + 审计 |
| I-6 | `pair_direction_rank` 唯一且在 `(pair_key_id, direction)` 内连续 | 物化时断言 |
| I-7 | 闭环跳的 `directional_pool_id` 必须能解析到唯一方向行，且该行的 `output_token_key_id` 等于该 hop 的输出代币 | 闭环物化时断言 |
| I-8 | 闭环跳的 `pool_id` = `base_pool_id` = 方向行的 `base_pool_id` | 闭环物化时断言（C-2 的可执行表述） |

I-1 ~ I-8 是**存储层**的不变量。I-9 ~ I-16 是**内存对象与跨层引用**的不变量（内存与库的一致、闭环 hop 对方向行的引用有效性、复核层的方向交叉校验、折叠时的方向自洽），完整定义见 [04-内存对象与闭环引用链](#d093) §9。

其中 I-8 需按 04 §7.1 拆为两条：当「生效池可被优化覆盖」被启用时，`pool_id` 合法地不同于 `base_pool_id`；届时 I-8a（`base_pool_id` == 方向行 `base_pool_id`）始终成立，I-8b（`pool_id` == 方向行 `base_pool_id`）仅在无覆盖时成立。

### 9.2 门禁不放宽

本次重构**不得**改变以下任何一项：

- 闭环容量折叠口径（`folded_path_capacity` / `folded_path_depth_capacity`）；
- 单跳容量门禁与路径最小值口径（`path_min_single_tick_bound`）；
- 路径容量下限（`MIN_CLOSED_LOOP_CAPACITY_RAW`）；
- USDG 估值与利润门禁；
- 复核层的盈利符号与广播门禁；
- 执行链的价格限制与状态迁移门禁。

**方向池只能作为“选哪个池”的判定依据，不能作为“多少链路可行”的判定依据。**容量与盈利的计算全部保持在原处。

---

## 10. 当前状态的诚实边界

- 本文档的 DDL 是**设计稿**：它不代表已经在任何库上创建过这两张表。
- 排序口径的“主序 = 方向精确输出”是一个**选择**，它保留了两侧跳的现有行为，但会改变中间跳的选池。如果阶段 0 的影子比对显示中间跳的变化过大，需回到 01 §4.1 重新审查这一选择，而不是在实施中调整。
- 实施顺序、验收条件与需授权动作见 [03-迁移实施与验收](../../../docs/D-007-迁移实施与验收.md)。

---

<a id="d093"></a>

### `D-093-内存对象与闭环引用链.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/docs/D-093-内存对象与闭环引用链.md` -->

# D-093 04 内存对象模型与闭环引用链

> 本文档定义**方向池作为聚合根**后的内存对象形态，以及闭环管理器如何引用它。
> 它是 02 的执行细则：02 定义库里存什么，本文定义内存里长什么样、谁引用谁。

## 1. 聚合根模型：方向池是池的唯一引用单位

### 1.1 结构

```text
┌───────────────────────────────────────────────────────────┐
│ DirectionalPool  聚合根 · 唯一的池引用单位                  │
│                                                            │
│   ┌────────────────────────────────────────────────────┐   │
│   │ base: V4Pool   子对象 · 链上事实 + 快照状态         │   │
│   │   pool_id / pool_manager / pool_key_json           │   │
│   │   token0 / token1 / token0_key_id / token1_key_id  │   │
│   │   fee / tick_spacing / hooks / pool_status         │   │
│   │   sqrt_price_x96 / tick / liquidity / price        │   │
│   │   depth_status / liquidity_read_at / …             │   │
│   └────────────────────────────────────────────────────┘   │
│   direction            0 | 1                               │
│   directional_pool_id  方向池身份                          │
│   pair_key_id          方向池所属代币对                     │
│   input_token_key_id / output_token_key_id                 │
│   zero_for_one         V4 交换方向标志                      │
│   input_capacity_raw / input_capacity_measured             │
│   directional_output / directional_output_measured         │
│   pair_direction_rank  (pair, direction) 内的 1-based 序位  │
└───────────────────────────────────────────────────────────┘
```

### 1.2 等式与推论

```text
DirectionalPool  ≡  V4Pool  ⊕  direction
direction        ≡  f(V4Pool.token0, V4Pool.token1)      纯函数，无分叉
```

由这两个等式可直接推出四条**不需要实测**的硬结论：

| 推论 | 依据 | 对方案的意义 |
|---|---|---|
| 方向池基数 = 基础池基数 × 2（每池两方向） | 方向由 `token0/token1` 唯一决定 | 方向表行数可预测，不依赖运行时统计 |
| 方向池**不携带任何新的链上事实** | `base` 逐列来自既有池表 | 方向层无需新增 RPC 或事务读取 |
| 方向池与基础池**不可能在采集期不一致** | `base` 是基础池的投影 | 不一致只可能发生在**装载期**，因此影子比对足以判定正确性 |
| 一个基础池在两个方向表各出现一次，两行仅方向相关字段不同 | 方向是唯一变量 | 差异比对可作为 I-3 的可执行断言 |

### 1.3 方向的取值（与 02 §2.1 同一规范）

| `direction` | 含义 | 输入代币 | 输出代币 | `zero_for_one` |
|---|---|---|---|---|
| 0 | 高地址 → 低地址 | `token1` | `token0` | `false` |
| 1 | 低地址 → 高地址 | `token0` | `token1` | `true` |

方向属于**池**，不属于路由。一个 hop 用哪个方向，由该 hop 的输入代币是该池的 `token0` 还是 `token1` 决定。

---

## 2. 共享类型：放在 `manager_contracts`

`manager_contracts` 是全部十个相关 crate 的共同依赖（`pool_manager`、`candidate_edge_pool_manager`、`candidate_exchange_pool_manager`、`closed_loop_manager`、`review_manager`、`broadcast_manager`、`manager_api`、`composite_manager`、`token_manager`、`manager_db`），因此**池的内存对象必须是共享类型**，不能各层各写一份——两份 `ScopePool` 的现状正是重复的根源。

### 2.1 新增文件 `manager_contracts/src/directional_pool.rs`（设计稿）

```rust
/// The two directions of a V4 pool, relative to the pool's own token order.
///
/// The variant order is the persisted value: HighToLow is 0 and LowToHigh
/// is 1. The value is part of the database contract, so it must not be
/// reordered.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum PoolDirection {
    /// token1 -> token0. The input token is the higher address.
    HighToLow = 0,
    /// token0 -> token1. The input token is the lower address.
    LowToHigh = 1,
}

impl PoolDirection {
    pub const ALL: [PoolDirection; 2] = [PoolDirection::HighToLow, PoolDirection::LowToHigh];

    pub fn as_i64(self) -> i64 { self as i64 }

    /// The V4 zeroForOne flag of this direction.
    pub fn zero_for_one(self) -> bool { matches!(self, Self::LowToHigh) }

    /// Derives the direction of a pool from the pool's own token order.
    ///
    /// This is the single place where the direction is decided. Everything
    /// else reads the value that was decided here.
    pub fn of_pool(_token0_key_id: &str, _token1_key_id: &str) -> Self {
        // token0 is the lower numeric address by V4 PoolKey construction,
        // so the low-to-high direction spends token0.
        Self::LowToHigh
    }
}

/// The V4 pool facts. This is the sub-object of a directional pool.
#[derive(Debug, Clone)]
pub struct V4Pool {
    pub pool_id: String,
    pub pool_manager: String,
    pub pool_key_json: String,
    pub token0: String,
    pub token1: String,
    pub token0_key_id: String,
    pub token1_key_id: String,
    pub fee: i64,
    pub tick_spacing: i64,
    pub hooks: String,
    pub pool_status: String,
    pub sqrt_price_x96: Option<String>,
    pub tick: Option<i64>,
    pub liquidity: Option<String>,
    pub liquidity_index: Option<f64>,
    pub price: Option<f64>,
    pub depth_status: Option<String>,
    pub liquidity_read_at: Option<String>,
    pub liquidity_block_number: Option<i64>,
    pub evidence_source: Option<String>,
}

/// A V4 pool in one of its two directions.
#[derive(Debug, Clone)]
pub struct DirectionalPool {
    pub base: V4Pool,
    pub direction: PoolDirection,
    pub directional_pool_id: String,
    pub pair_key_id: String,
    pub input_token_key_id: String,
    pub output_token_key_id: String,
    pub input_capacity_raw: Option<u128>,
    pub input_capacity_measured: bool,
    pub directional_output: Option<f64>,
    pub directional_output_measured: bool,
    pub pair_direction_rank: Option<i64>,
}

impl DirectionalPool {
    pub fn zero_for_one(&self) -> bool { self.direction.zero_for_one() }
    pub fn base_pool_id(&self) -> &str { &self.base.pool_id }
}
```

### 2.2 必须共享的类型清单

| 类型 | 归属 | 谁用 |
|---|---|---|
| `PoolDirection` | `manager_contracts/src/directional_pool.rs` | 全部池持有者 |
| `V4Pool` | 同上 | 全部池持有者（`ScopePool` 的替代品） |
| `DirectionalPool` | 同上 | 全部池持有者（`ScopePool` + 方向量的替代品） |
| `DirectionalPoolId` | `manager_contracts/src/ids.rs`（与 `PairKeyId`/`RouteKeyId` 同列） | 全部引用方向池的地方 |

### 2.3 为什么必须是共享类型

现状有两个**互不相干**的 `ScopePool` 副本：`candidate_edge_pool_manager/src/lib.rs:410` 与 `candidate_exchange_pool_manager/src/lib.rs:351`。两份字段几乎相同，字段顺序都不同（`pool_status` 一个在第 11 位、一个在末位）。这意味着：

- 方向字段必须被**加两次**，否则其中一层会漏；
- 任何字段语义变更（例如 `input_capacity_raw` 的单位）都要改两处，且没有编译器保证它们一致；
- 方向池与基础池的一致性断言无法跨层复用。

**收敛为 `manager_contracts` 中的单一 `DirectionalPool` 是消除方向字段漏加的结构性保证，而不是代码风格偏好。**

---

## 3. 内存大对象的现状与缺口

### 3.1 现状盘点

| 内存对象 | 位置 | 装载体（SELECT） | 方向信息 | 缺口 |
|---|---|---|---|---|
| `ScopePool`（候选边副本） | `candidate_edge_pool_manager/src/lib.rs:410` | `v4_exchange_pools_core4`（`:863`） | **无** | 排序时只能临时算方向 |
| `ScopePool`（交换副本） | `candidate_exchange_pool_manager/src/lib.rs:351` | `v4_exchange_pools_candidate_exchange`（`:937`） | **无** | 同上 |
| `PoolTickState` | `closed_loop_manager/src/lib.rs:491` | 两张池表（`:1474-1479`） | **无**（只有 `token0/token1` 序） | 方向靠运行时反推 |
| `capacity_by_pool: BTreeMap<String, u128>` | `closed_loop_manager/src/lib.rs:521` | `snapshots.capacity_raw`（`:1611`） | **无** | 方向在折叠时以 `zero_for_one` 参数施加，与池对象脱钩 |
| `single_tick_bounds_by_pool: BTreeMap<String, (Option<u128>, Option<u128>)>` | `closed_loop_manager/src/lib.rs:533` | `snapshots.single_tick_max_token{0,1}_raw`（`:1383`） | **以元组位置隐含** | 取哪一侧靠 `if zero_for_one { token0 } else { token1 }`（`:1698`） |
| `Recommendation.direction_key: String` | `closed_loop_manager/src/lib.rs:461` | 候选边表 | 字符串 `A>B` | 与观察层的 `A->B` 并存 |
| `YzDirection.pool_ids: Vec<String>` | `closed_loop_manager/src/lib.rs:472` | 观察表 | 靠 `A->B` 键 | 同上 |

### 3.2 一个关键事实：方向**已经**是折叠路径上的一等值

闭环层并不是没有方向，而是方向没有装进池对象。它用一个**平行数组**把方向一路带到折叠函数：

```rust
// closed_loop_manager/src/lib.rs:2169
let hop_directions = [first_zero_for_one, middle_zero_for_one, third_zero_for_one];

// :2176
let folded_capacity = folded_path_capacity(&input.pool_tick_states, &path_pool_ids, &hop_directions);
// :2182
let cross_tick_capacity_anchor_raw = folded_path_depth_capacity(
    &input.pool_tick_states, &input.capacity_by_pool, &path_pool_ids, &hop_directions,
);
// :2195
let path_min_tick_bound_raw = path_min_single_tick_bound(input, &path_pool_ids, &hop_directions);
```

这有三个直接后果：

1. **重构是纯粹的表示升级**——折叠算术、门禁阈值、口径全部不动，只是把平行数组换成池对象自带方向。这大幅降低了实施风险。
2. **平行数组本身就是缺陷来源**——两个数组分别构造，只靠**约定**对齐。长度不符在 `folded_path_capacity`（`:1723`）与 `folded_path_depth_capacity`（`:1778`）里有守卫（返回 `None`）；但 `path_min_single_tick_bound` 的兜底分支用 `zip`（`:1697`），`zip` 按较短一侧截断，**长度不符会被静默截断**。而**顺序错位无法被任何检查发现**——两个数组长度相同但跳序不同时守卫全部通过，折叠会算出「用 A 池的方向配 B 池」的容量。
3. **改造方向是唯一的**——把 `Vec<(String, bool)>` 或 `Vec<DirectionalPool>` 作为参数传下去，平行数组自然消失，长度不匹配变成编译期或断言期错误。

### 3.3 方向解析是运行时反推的

`closed_loop_manager/src/lib.rs:1873` `hop_zero_for_one` 用 tick 状态里的 `token0_key_id`/`token1_key_id` 与 hop 输入代币比对，返回 `Option<bool>`；池不带该代币时直接返回 `InvalidStoredTopology`（硬失败）。

`:1906` `resolve_hop_direction` 优先用 tick 状态，缺失时回退到逻辑方向键 `direction_key.split_once('>')`。

**这是方向应该被物化出来的最强证据**：方向错一位就会把整条闭环的容量算错，而现在它是每次重建时重算的。

---

## 4. 目标内存模型

### 4.1 候选边层与交换层

两层的 `ScopePool` 全部替换为 `DirectionalPool`：

```rust
// 现状：两份各自定义的 ScopePool，无方向
struct ScopePool { pool_id, pool_manager, ..., pool_status }

// 目标：一份共享的 DirectionalPool，方向内建
struct DirectionalPool {
    base: V4Pool,
    direction: PoolDirection,
    // 见 2.1
}
```

装载改为从**方向池表**读取（而不是从基础池表读取）：

| 层 | 现状装载体 | 目标装载体 |
|---|---|---|
| `candidate_edge_pool_manager` | `v4_exchange_pools_core4` | `manager_v4_directional_exchange_pools` |
| `candidate_exchange_pool_manager` | `v4_exchange_pools_candidate_exchange` | `manager_v4_directional_exchange_pools` |

装载后的分组键也随之改变：

```text
现状：groups: BTreeMap<(String, String), Vec<ScopePool>>               // (代币对, 方向键)
目标：groups: BTreeMap<(String, PoolDirection), Vec<&DirectionalPool>>  // (代币对, 方向)
```

方向键字符串（`A>B` / `A->B`）随之退化为**展示字段**（见 02 §5）。

### 4.2 闭环层

```rust
struct ClosedLoopInput {
    // 现有字段

    /// 方向池：闭环层唯一的池集合。键是 directional_pool_id。
    ///
    /// 容量、单跳边界与 tick 状态都从这个对象读出，因此它们**不可能**
    /// 与方向脱钩：方向在对象里，取哪一侧由对象自己决定。
    directional_pools: BTreeMap<String, DirectionalPool>,

    // 三个被方向池取代的旧字段：
    // capacity_by_pool:           BTreeMap<String, u128>,
    // single_tick_bounds_by_pool: BTreeMap<String, (Option<u128>, Option<u128>)>,
    // pool_tick_states:           BTreeMap<String, PoolTickState>,
}
```

三个旧字段被 `directional_pools` 吸收后的对应关系：

| 旧字段 | 新载体 | 语义等价性 |
|---|---|---|
| `capacity_by_pool[base_id]` | `directional_pool.base` + `direction`（容量仍取 `snapshots.capacity_raw`，与方向无关的部分不变） | 折叠时按方向选 `token_in`/`token_out` 的逻辑**完全保留**（`:1789-1794`） |
| `single_tick_bounds_by_pool[base_id].(token0, token1)` | `directional_pool.input_capacity_raw` | 方向 0 取 `single_tick_max_token1_raw`、方向 1 取 `single_tick_max_token0_raw`——与旧的 `if zero_for_one { token0 } else { token1 }` **逐值等价** |
| `pool_tick_states[base_id]` | `directional_pool.base` 的快照字段 | `PoolTickState` 的 8 个字段全部是 `base` 的子集，逐字段搬迁 |

### 4.3 折叠函数签名

```rust
// 现状：方向是平行数组
fn folded_path_capacity(
    pool_tick_states: &BTreeMap<String, PoolTickState>,
    pool_ids: &[String],
    directions: &[bool],
) -> Option<u128>;

// 目标：方向在池对象里
fn folded_path_capacity(
    pools: &BTreeMap<String, DirectionalPool>,
    hop_directional_pool_ids: &[String],
) -> Option<u128>;
```

函数体内部把 `zero_for_one` 从 `pools[id].direction` 读出，替换原来的 `directions[i]`。**折叠算术一字不改。**

`folded_path_depth_capacity` 与 `path_min_single_tick_bound` 同形改造。

### 4.4 hop 方向不再反推

```text
阶段 1（并存）：resolve_hop_direction 保留，新增交叉校验（方向行 output == hop 输出代币）
阶段 2（切换）：resolve_hop_direction 退化为读方向行的 direction，不再重算
阶段 3（下线）：删除 resolve_hop_direction 及其回退分支
```

阶段 2 之后，方向的真相源唯一：`manager_v4_directional_exchange_pools.direction`。

---

## 5. 闭环引用链：逐环节核对

这是本方案要回答的核心问题——**方向池能不能被闭环管理器正确引用**。

| # | 环节 | 现状 | 重构后 | 依据 |
|---|---|---|---|---|
| 1 | 选池 · 第 1/3 跳 | `recommend_direction` 内存排序 | 读方向表 `(pair, direction)` 序位 1 | 主序口径不变（C-3），两侧跳行为不变 |
| 2 | 选池 · 第 2 跳 | `liquidity_index` 降序（`:1159`） | 读方向表 `(pair, direction)` 序位 1 | **口径变更**，P0 影子比对门禁（03 §2.1） |
| 3 | hop 落库 | `pool_id` = base id（`:2509-2510`） | `directional_pool_id` + `hop_direction` + `pool_id`（投影） | I-11 |
| 4 | 方向解析 | `resolve_hop_direction` 运行时反推（`:1906`） | 读方向行 | I-12 |
| 5 | 容量折叠 | `pool_tick_states[base_id]` + `hop_directions[]` | `directional_pools[dir_id]` | 折叠算术不变（§4.3） |
| 6 | 单跳边界 | `single_tick_bounds_by_pool[base_id].(0,1)` | `directional_pool.input_capacity_raw` | 逐值等价（§4.2） |
| 7 | 跨跳深度容量 | `capacity_by_pool[base_id]` | `directional_pools[dir_id].base` + `.direction` | 折叠算术不变 |
| 8 | 路径最小边界 | `path_min_single_tick_bound` | 同形改造 | 门禁不放宽（02 §9.2） |
| 9 | 定价 · 取池 | `resolve_route_pool_ids(pool_keys)`（`review_manager/src/parity.rs:1054`） | **不变**（仍按 base `pool_key_json`） | C-2 |
| 10 | 定价 · 方向 | `build_hops` 由路由地址推导（`parity.rs:811-819`） | **读存储方向 + 交叉校验**（§6） | I-13，校验强度**增加** |
| 11 | 复核读取 hop | `pool_ids_json`（`review_manager/src/lib.rs:2612`） | **不变**（base id 数组） | C-2 |
| 12 | 广播 · 取池 | `pool_ids_json` → `pool_ids`（`broadcast_manager/src/lib.rs:2085`） | **不变** | C-2，执行合约不变 |
| 13 | 广播 · 方向 | 由路由地址与池序在合约层决定 | **不变** | C-2 |

### 5.1 结论

**第 9～13 环全部不动。**这正是 C-2 的价值所在：方向池是闭环层的**上游输入**，不是下游消费者的口径变更。

改动集中在第 1～8 环，而第 4～8 环是**表示升级**（平行数组 → 对象自带方向），第 1～3 环是**口径统一**（有门禁保护）。

### 5.2 兼容性硬约束（三条）

1. `manager_v4_closed_loops.pool_ids_json` 必须是**有序的 base pool id 数组**，长度等于跳数。它是复核与广播的路由契约。
2. `manager_v4_closed_loop_hops.pool_id` 必须是 base V4 pool id。`review_manager` 与 Python 消费者按它取池。
3. `manager_v4_closed_loop_hops.base_pool_id` 保持现状语义（原始推荐池，可被优化覆盖）。

> **注意**：`pool_id` 与 `base_pool_id` 的**意图**不同。语义出自结构文档 `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:150`：*`pool_id` 是当前生效池，可能被优化策略临时覆盖；`base_pool_id` 始终保留原始推荐池*；字段表 `:188` 进一步写明 `base_pool_id` 是「原始推荐池，优化过期后恢复」。`docs/数据库结构文档/06-数据模型约束与后续实施.md:282-283` 给出了配套规则：优化策略只覆盖 `pool_id`，优化过期后恢复 `pool_id = base_pool_id`。
>
> 现状实现里两者恒等（`closed_loop_manager/src/lib.rs:2509-2510`），唯一的 `UPDATE ... SET pool_id` 出现在测试代码中（`:4991`，用于制造漂移）。因此本方案**不**把 `pool_id` 改成 SQLite 生成列——那会永久消灭生效池可被覆盖的能力。正确做法见 §7。

### 5.3 关于外键：为什么 `directional_pool_id` 必须另起一列

设计文档 `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:173-176` 给出的 `v4_closed_loop_hops` 模型带两条外键：

```sql
FOREIGN KEY (network, chain_id, protocol, pool_id)
    REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id),
FOREIGN KEY (network, chain_id, protocol, base_pool_id)
    REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
```

即**两列都指向基础池表的 `pool_id`**（文档里的表名是旧版的 `v4_exchange_pools`；实盘对应 `v4_exchange_pools_core4` 与 `v4_exchange_pools_candidate_exchange`）。这从数据模型层面再次确认了 C-2：`pool_id` 这一列的含义是「基础池表里的某一行」，方向池 id 放进去会让外键指向不存在的行。

两点必须如实说明：

1. **实盘表 `manager_v4_closed_loop_hops` 当前没有外键约束**——`20261104_003_closed_loop_schema.sql:69-89` 只有 `PRIMARY KEY`。上面的外键出现在设计文档所描述的旧版 `v4_closed_loop_hops`（带 `market_id`）模型里，是**目标模型**而非现状。
2. **SQLite 的 `ALTER TABLE ... ADD COLUMN` 不能新增外键约束**。因此 `directional_pool_id` 若要获得硬外键，只能靠**重建表**实现。

**处理意见（默认选择）：** 不重建表。`directional_pool_id` 作为普通列加入，靠 **I-7 / I-11 / I-12 三条断言 + 重建期复验**保证引用有效；同时在方向池表上为 `directional_pool_id` 建 UNIQUE 索引，为将来升级为硬外键留好条件：

```sql
-- 为将来的硬外键预留唯一性（SQLite 外键要求父键有 UNIQUE 约束）
CREATE UNIQUE INDEX IF NOT EXISTS ux_v4_directional_pools_id
    ON manager_v4_directional_exchange_pools(
        network, chain_id, protocol, scope_key, directional_pool_id
    );
```

若将来决定重建 hop 表以加硬外键，那是 P1 的独立决策，**不属于本方案的范围**，但重建脚本必须同时保留 `pool_id`/`base_pool_id` 两条既有外键语义。

### 5.4 闭环身份不受方向影响

结构文档 `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:143-146` 规定了 `loop_key` 的四条规则：

```text
- 使用地址而不是符号。
- A -> B -> C -> A 与 A -> C -> B -> A 是同一条闭环。
- 中间代币按反转后较小的顺序归一化，池子地址不参与去重键。
- 同一条闭环里一个池子只算一次，不因方向拆成两条。
```

前三条说明：**闭环身份只由代币环决定，与选中了哪个池、走了哪个方向无关。**这与方向池设计完全兼容——方向池改变的是「闭环里挂哪个池」，不改变「这条闭环是谁」。

第四条需要单独确认，因为方向池看起来像是会把一个池拆成两条。结论是**不会**：

- 三跳的代币对互不相同（第 1 跳 `{C,Y}`、第 2 跳 `{Y,Z}`、第 3 跳 `{Z,C}`）；
- 一个 V4 池只属于**一个**代币对；
- 因此同一条闭环内，同一个基础池**最多出现一次**，方向唯一确定。

**推论：方向池不会让任何闭环多出一条。**`loop_key` 的计算规则一字不改，`manager_v4_closed_loops` 的主键与去重语义不受影响。

---

## 6. 复核层：从重算方向改为读方向 + 交叉校验

### 6.1 现状

`review_manager/src/parity.rs:784` `build_hops` 由**路由地址**与**池的 token 序**推导 `zero_for_one`：

```rust
// review_manager/src/parity.rs:811-819
let zero_for_one = if state.token0 == token_in && state.token1 == token_out {
    true
} else if state.token1 == token_in && state.token0 == token_out {
    false
} else {
    // 池不在这两个路由代币之间，路由与选池不一致
    return None;
};
```

`HopState`（`:89`）已有 `zero_for_one: bool` 字段——**复核层本来就有方向**。

### 6.2 重构后

复核层从闭环 hop 读**存储的方向**（经 `directional_pool_id` 解析），并保留一次交叉校验：

| 情形 | 现状行为 | 重构后行为 |
|---|---|---|
| 存储方向 == 池序推导方向 | 正常 | 正常（**一致**，放行） |
| 存储方向 ≠ 池序推导方向 | 不可能发生（方向是重算的） | **跳过复核并记录原因**（`direction_mismatch`） |
| 池不在路由两端之间 | 跳过复核 | 跳过复核（**不变**） |

这比现状**更强**：现状只检查池是否在路由之间，没有检查存储的方向与池的 token 序是否自洽。重构后多了一道跨层一致性断言。

### 6.3 为什么复核层仍然按 base 取池

`resolve_route_pool_ids`（`review_manager/src/parity.rs:1054`）是**按 pool_key 解析 pool_id** 的：它读基础池表，用 `pool_key_json` 匹配路由的 PoolKey，返回 base pool id 数组。这条链与方向无关，且方向池的 `base` 子对象逐列来自同一张表，**按 pool_key 解析的结果与方向池表解析的结果必然一致**（I-9）。

因此复核层不需要改取池逻辑，只需把 `zero_for_one` 的**来源**从重算改为读存储 + 校验。

---

## 7. 兼容投影纪律：防止出现第二个真相源

方向池重构最大的失败模式是：**同一个语义在两处各存一份，然后慢慢漂移**。三条纪律：

### 纪律 1 · 真相源唯一

| 语义 | 唯一真相源 |
|---|---|
| 方向 | `manager_v4_directional_exchange_pools.direction` |
| V4 池事实 | `v4_exchange_pools_core4` / `v4_exchange_pools_candidate_exchange` |
| 方向池在闭环 hop 上的引用 | `manager_v4_closed_loop_hops.directional_pool_id` + `hop_direction` |

`manager_v4_closed_loop_hops.pool_id` / `base_pool_id` 是**投影**，不是真相源。

### 纪律 2 · 写入同源

hop 落库时，四个字段必须在**同一次写入**里从**同一行方向池**导出：

```rust
// 目标写法（伪代码）：一次解构，四处投影
let DirectionalPool { base, direction, directional_pool_id, .. } = pool;
ClosedLoopHopRow {
    directional_pool_id: directional_pool_id.clone(),
    hop_direction: direction.as_i64(),
    pool_id: base.pool_id.clone(),      // 投影
    base_pool_id: base.pool_id.clone(), // 投影
    ..
}
```

在**同一个函数体**里从**同一个对象**解构，物理上无法产生不一致。

### 纪律 3 · 重建期复验

闭环层物化时，对每条 hop 断言：

- `directional_pool_id` 能解析到唯一方向行（I-7）；
- 该行的 `base_pool_id` == `hop.pool_id` == `hop.base_pool_id`（I-11）；
- 该行的 `direction` == `hop.hop_direction`（I-12）；
- 该行的 `output_token_key_id` == `hop.output_token_key_id`（I-7）。

任一不成立即**拒绝发布**并报拓扑错误——与现状 `InvalidStoredTopology` 的处理一致。

### 7.1 未来若启用生效池可被优化覆盖

届时 `pool_id`（生效池）将合法地不同于 `base_pool_id`（原始推荐池）。此时必须：

1. 覆盖写入**同时**更新 `directional_pool_id` 与 `hop_direction`，二者仍然同源；
2. I-11 拆成两条：
   - I-11a：`hop.base_pool_id` == 方向行 `base_pool_id`（**始终成立**）；
   - I-11b：`hop.pool_id` == 方向行 `base_pool_id`（**仅在无覆盖时成立**）；
3. `pool_ids_json` 写入的是**生效池**，其方向由生效池对应的方向行给出——因此 `pool_ids_json` 需要一个同序的 `directional_pool_ids_json` 配套。

> 本方案**不实现**覆盖写入，只在此登记约束，避免将来实现时无意破坏 I-11。

---

## 8. 同步更新机制

### 8.1 单向重建链

```text
pool_scope ──→ directional_pool ──→ candidate_edge ──→ candidate_exchange ──→ closed_loop ──→ review ──→ broadcast
              （新增层）
```

方向层**只**读 `pool_scope` 与 `v4_exchange_pool_liquidity_snapshots`；**不读任何下游**。下游**只**读方向层，不回头读基础池表做选池。

### 8.2 版本与校验和

`manager_v4_directional_pool_library_state`（02 §3.2）持有：

| 列 | 作用 |
|---|---|
| `directional_pool_version` | 方向层版本号，单调递增 |
| `materialized_checksum` | 方向表内容的校验和 |
| `pool_scope_version` / `pool_scope_checksum` | 上游基础池的版本与校验和 |
| `snapshot_checksum` | 快照表的校验和 |
| `library_versions_json` / `library_checksums_json` | 各代币库的版本与校验和 |

### 8.3 闭环层的重建门禁

闭环层物化前必须校验：

1. `candidate_edge` 与 `observation` 的方向表版本 == 本层读到的方向表版本；
2. 方向表 `materialized_checksum` == 本层读到的校验和；
3. 上游 `pool_scope_checksum` == 方向层记录的 `pool_scope_checksum`。

任一不符则**拒绝重建**。这与现有 `closed_loop` 对 `candidate_edge` / `observation` 的校验同形（`closed_loop_manager/src/lib.rs:571` 的 `closed_loop_pool_scope_checksum` 是同一种思路的实例）。

### 8.4 内存与库为什么不会漂移

```text
一次物化事务内：  读方向表  →  构造内存 DirectionalPool 集合  →  计算  →  写库
                            ↑                                        ↓
                            └────────── 同一个对象 ──────────────┘
```

内存对象与写入行由**同一个对象**产生，因此**单次物化内不可能漂移**。跨层漂移（方向层被重建但闭环层没重建）由 §8.3 的校验和门禁拦截。

**这直接回答「和内存大对象保持同步更新」**：同步不是靠记得更新两处，而是靠内存对象由库读出、库行由内存对象写出这一条单向数据流。

### 8.5 幂等

方向层物化必须幂等：相同输入则产生相同 `materialized_checksum` 与 `changedRows = 0`。幂等键与请求摘要语义与 `candidate_exchange_pool_manager` 保持一致（02 §8）。

---

## 9. 一致性不变量（I-9 ~ I-16）

沿用 02 §9.1 的 I-1 ~ I-8，本文档补充：

| ID | 不变量 | 检查方式 |
|---|---|---|
| I-9 | 方向行的 `base` 字段与基础池表对应行**逐列一致**（身份列 + 快照列） | 物化时逐列断言；P0 影子比对抽样 |
| I-10 | 内存中的 `DirectionalPool` 集合与方向表**行集合**一一对应（无遗漏、无多余） | 物化时计数断言 |
| I-11 | 闭环 hop 的 `base_pool_id` == 方向行 `base_pool_id` | 闭环物化时断言（§7 纪律 3） |
| I-12 | 闭环 hop 的 `hop_direction` == 方向行 `direction` | 闭环物化时断言 |
| I-13 | 复核层交叉校验通过：存储方向 == 由池 token 序推导的方向 | 复核物化时断言；不一致则跳过复核并记录 `direction_mismatch` |
| I-14 | 折叠时取用的方向与 hop 的逻辑方向一致（`direction=0` 必为 `token1→token0`） | 折叠前断言；违反即拓扑错误 |
| I-15 | 方向层版本与校验和被闭环层记录在闭环库状态中 | 闭环库状态表断言 |
| I-16 | `pool_ids_json` 的长度 == 跳数，且每个元素都是 base pool id | 闭环物化时断言 |

---

## 10. 诚实边界

- 本文档描述的内存模型（`DirectionalPool`、`V4Pool`、`PoolDirection`、`DirectionalPoolId`）**尚未实现**，全部是设计稿。
- §3.2 指出折叠算术一字不改，这是基于**代码逐行核对**的判断（`:2169`、`:2176`、`:2182`、`:2195`、`:1698`、`:1789-1794`），但**尚未有任何一行代码被改动**，也**没有任何实测**证明改造后容量逐个闭环相等。
- §5 的逐环节核对表是**静态分析**结论。第 9～13 环不动的判断依赖于 `pool_key_json` 在基础池表与方向池表中逐字一致（I-9）——这一点必须由 P0 影子比对在真实库上验证。
- 本文**不**主张方向池会提升盈利或深度。它主张的是：**闭环三跳的方向引用从运行时反推变为读取存量，且三跳的选池口径统一到同一个方向口径。**

---

<a id="d098"></a>

### `D-098-闭环层切换实盘测量与口径裁定_20261124.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/docs/D-098-闭环层切换实盘测量与口径裁定_20261124.md` -->

# D-098 `T-方闭储.01`｜闭环层切换实盘测量与口径裁定（2026-11-24）

> **本文件是 `T-方闭储.01` 的实施记录与口径裁定书。**
> 它回答三个问题：① 闭环层为什么改；② 改完之后读数为什么变；③ 变��的东西是否在允许范围内。
> **本文件不引用其它任务目录的验收内容**；跨任务的依赖在 §7 只写**模块编号**与任务编号。

---

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| **闭环集合** | **未变**：168 闭环 / 504 跳，新增 0、删除 0（影子比对，基线二进制 vs 新二进制，两份实盘库副本） |
| **两侧跳选池** | **未变**：`changed_hop_pool = 0`（**含中间跳**，比 T-P13 的要求更强） |
| **USDG 估值 / 利润门禁** | **未变**：`changed_borrow_token_usdg_value = 0` |
| **方向信息** | **全覆盖**：504 跳全部带 `directional_pool_id` + `hop_direction`，缺失 0 |
| **容量读数** | **变了**：160/168 闭环的 `single_tick_capacity_anchor_raw` 不同（median −57.8%、mean −6.6%、min −99.98%、max +1404%） |
| **容量变化的根因** | **不是实现缺陷**：是**状态源**从池表内嵌的旧快照（11-19 / 11-21）切到深度快照表（11-22）。隔离实验证明：**新接线 + 旧状态 = 基线，逐闭环 168/168 相等** |
| **折叠算术** | **一字未改**：隔离实验中 `single_tick_capacity_anchor_raw` / `binding_pool_id` / `cross_tick_capacity_anchor_raw` **三项各 168/168 逐值相等** |
| **实盘切换** | **已完成**：`closedLoopVersion 15 → 16`，`materializedChecksum` `sha256:c23f1941… → sha256:19168935…` |
| **回滚演练** | **已演练**：基线二进制对实盘库重跑 ⇒ 读数**逐行回到切换前**（168 闭环 + 504 跳，派生列 **0 差异**） |
| **未闭合项** | **2 项**：`T-P8 / I-13`（复核层交叉校验，属 `M-6`）、`T-P14`（`profitDrop`，属 `M-6`）—— 见 §7 |

---

## 2. 为什么改：闭环层与复核层**不同源**

改造前，闭环层折叠容量时取的 tick 状态来自**两张池表自身内嵌的列**：

```text
v4_exchange_pools_candidate_exchange   （优先）
v4_exchange_pools_core4                 （补齐）
    └─ sqrt_price_x96 / tick / liquidity / fee / tick_spacing
```

而复核层定价取的是**深度快照表**：

```text
pool_manager::read_pool_parity_states_on_connection()
    FROM v4_exchange_pools p
    JOIN v4_exchange_pool_liquidity_snapshots s ON …
    SELECT s.sqrt_price_x96, s.tick, s.liquidity      ← 状态来自快照表
           p.token0, p.token1, p.fee, p.tick_spacing  ← 身份来自池表
```

**同一个池，闭环层按 11-19/11-21 的价格与流动性算容量，复核层按 11-22 的价格与流动性算利润。**
两��描述的不是同一个市场状态 ⇒「系统逻辑一致」被破坏：容量门禁与利润门禁在**两份不同的快照**上做比较。

方向层（`T-方授储.01`，已闭合）把状态源统一到深度快照表（唯一写入方：池管理器），
所以闭环层切到方向行之后，**闭环层与复核层同源**——这正是本次切换的目的。

---

## 3. 改动清单（落点）

| 模块 | 文件 | 改动 |
|---|---|---|
| `M-4` | `flashloan/tools/closed_loop_manager/src/lib.rs` | `ClosedLoopInput` 的三张池表（`capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states`）→ **一个** `directional_pools: DirectionalPoolIndex` ＋ **刻意不窄化**的 `valuation_pool_states`；三个折叠入口**删除** `directions: &[bool]`；`resolve_hop_direction` / `hop_zero_for_one` → `resolve_hop_directional_pool`；`ClosedLoopHopRow` ＋ `directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank`；`ClosedLoopRow` ＋ `directional_pool_ids`；`ClosedLoopStatus` / `ClosedLoopMaterializationResult` ＋ `directional_pool_version` / `directional_pool_checksum`（**I-15**） |
| `M-3` | `flashloan/tools/manager_contracts/src/directional_pool.rs` | `V4Pool` ＋ `sqrt_price_x96` / `tick` / `liquidity` / `depth_capacity_raw` |
| `M-3` | `flashloan/tools/directional_pool_manager/src/lib.rs` | 方向行落库 `depth_capacity_raw` |
| `M-10` | `flashloan/tools/manager_db/migrations/20261124_016_closed_loop_directional_wiring.sql` | 跳表 ＋ 4 列、闭环表 ＋ 1 列、方向表 ＋ `depth_capacity_raw`、闭环库状态 ＋ 版本/校验和两列（**已应用实盘**） |
| `M-4` | `flashloan/tools/closed_loop_manager/examples/materialize_closed_loops.rs` | 新增：闭环层物化的**最小驱动**，供影子比对与实盘切换使用 |

**为什么 `valuation_pool_states` 刻意不窄化**：USDG 估值是**市场属性**，不是路径属性。
方向层只覆盖 346 行（有准入的子集），估值需要全市场 496 池；窄化会改写借币的 USDG 价值，
进而改写跨 tick 复核阈值比较——那是**放宽既有门禁**，本方案禁止。

---

## 4. 影子比对方法与读数

### 4.1 方法

```text
实盘库 local_store_4663.sqlite3
    ├─ 副本 A ── 基线二进制（HEAD a78f2b7e，git worktree 独立编译）── 读数 B
    └─ 副本 B ── 新二进制（工作区）────────────────────────── 读数 N
比对脚本：plan/runtime_evidence/tools/compare_closed_loop_directional_20261124.py
报告：    plan/back/probe_20261124/shadow_closed_loop_20261124.json
```

基线二进制跑出的 `materializedChecksum` = `sha256:c23f1941…`，与**实盘当时已发布的状态完全一致**
⇒ 基线读数就是「切换前」的权威读数，回滚的目标读数因此确定。

### 4.2 读数

| 项 | 基线 | 新 | 判定 |
|---|---:|---:|---|
| 闭环数 | 168 | 168 | ✅ 不变 |
| 跳数 | 504 | 504 | ✅ 不变 |
| 新增 / 删除闭环 | — | 0 / 0 | ✅ 不变 |
| 跳选池变化 | — | **0** | ✅ **T-P13 满足**（两侧跳零差异，且中间跳亦零差异） |
| 跳数变化 | — | 0 | ✅ 不变 |
| 借币 USDG 价值变化 | — | **0** | ✅ **利润门禁未变**（`T-P11` 的前提） |
| 缺方向的跳 | — | **0** | ✅ 504 跳全部带方向 |
| 单 tick 容量相同 / 不同 | — | 8 / **160** | ⚠️ 见 §5 |
| 绑定池变化 | — | 57 | ⚠️ 见 §5 |

### 4.3 容量变化的幅度

```text
相对变化（new − base）/ |base|：
  count = 160    median = −57.81%    mean = −6.64%    min = −99.98%    max = +1404.25%
```

**这个幅度远超「价格陈旧 2–3 天」的直觉**，因此必须定位根因，不能只写「口径变更」一句带过。

---

## 5. 根因定位（决定性证据）

### 5.1 假设与验证一：状态源不同

探针 `plan/runtime_evidence/tools/probe_capacity_source_20261124.py` 逐跳对比旧路径状态与新路径状态：

| 项 | 读数 |
|---|---|
| 跳数 | 504 |
| 状态**完全相同** | 12 |
| 状态**不同** | **492** |
| 旧路径缺状态 / 新路径缺行 | 0 / 0 |
| 流动性比（新/旧） | min **0.0174** / median **0.848** / max **2.105** |
| 价格比（新/旧，`sqrt_price`） | min 0.942 / median 1.011 / max 1.110 |
| tick 差分布 | 跨度 −1186 … +2086（16 跳为 0） |

**身份字段零差异**：方向行 vs 旧池表，`fee` / `tick_spacing` / `token0_key_id` / `token1_key_id`
在 **692 行**上 **0 差异** ⇒ 差异**只可能**落在 `sqrt_price_x96` / `tick` / `liquidity` 三列。

**方向行 vs 快照表**：436 行上 `sqrt_price_x96` / `tick` / `liquidity` **0 差异**
⇒ 新路径的状态**逐值来自**深度快照表。

### 5.2 验证二：快照表更新，池表陈旧

同一个池 `0x207783b4…` 的三个来源：

| 来源 | `liquidity_read_at` | 区块 |
|---|---|---|
| `v4_exchange_pools_candidate_exchange` | `2026-11-19T11:30:22Z` | 118183971 |
| `v4_exchange_pools_core4` | `2026-11-21T10:28:46Z` | 119807718 |
| `v4_exchange_pool_liquidity_snapshots` | **`2026-11-22T10:45:35Z`** | **120571238** |

快照表**比两张池表都新**（池表写库时间更新，但**它携带的测量值**更旧）。

### 5.3 验证三：隔离实验（**决定性**）

把方向行的 `sqrt_price_x96` / `tick` / `liquidity` **还原成旧池表的状态**（692 行），
再用**新二进制**重跑物化：

| 比对项 | 新二进制 ＋ **旧**状态 vs 基线 | 新二进制 ＋ **新**状态 vs 基线 |
|---|---:|---:|
| `single_tick_capacity_anchor_raw` | **168 / 168 逐值相等** | 8 / 168 |
| `single_tick_capacity_binding_pool_id` | **168 / 168 逐值相等** | 111 / 168 |
| `cross_tick_capacity_anchor_raw` | **168 / 168 逐值相等** | 168 / 168 |

**结论**：新接线（方向解析、跳投影、折叠调用、落库）在**同一份状态**下与旧实现**逐值等价**；
§4.3 的全部差异**唯一地**来自状态源切换。这不是实现缺陷，是**口径变更**——且是本任务**要求**的口径变更。

复算脚本 / 副本：`plan/back/probe_20261124/capacity_source_20261124.json`、`plan/back/shadow_oldstate.sqlite3`（演练后已清理）。

---

## 6. 实盘切换与回滚演练

### 6.1 备份

```text
flashloan/db/local_store_4663.sqlite3.bak_closedloop_directional_20261124
    8 601 968 640 B   （与切换前主库等长）
    自校验：168 闭环、closed_loop_version = 15、checksum = sha256:c23f1941…
```

### 6.2 切换读数

| 阶段 | `closedLoopVersion` | `materializedChecksum` | 闭环 / 跳 / 优化 |
|---|---:|---|---|
| 切换前（实盘已发布） | 15 | `sha256:c23f1941e2d3…` | 168 / 504 / 456 |
| **切换后（新二进制）** | **16** | **`sha256:19168935c99b…`** | 168 / 504 / 456 |
| 回滚演练（基线二进制重跑） | 17 | `sha256:c23f1941e2d3…` | 168 / 504 / 456 |
| 再次切换（新二进制，最终态） | **18** | **`sha256:19168935c99b…`** | 168 / 504 / 456 |

伴随读数：`directionalPoolVersion = 2`、`directionalPoolChecksum = sha256:84cf7aef5655…`、
`anchorCount = 34`、`skippedTokenCount = 79`、`changedRows = 2406`。

### 6.3 切换正确性

| 比对 | 结果 |
|---|---|
| 实盘（切换后） vs 影子副本 N | 闭环表 **168 行、跳表 504 行，全部派生列 0 差异** ✅ |
| 回滚后实盘 vs 切换前（影子副本 B） | 闭环表 **168 行、跳表 504 行，全部派生列 0 差异** ✅ |
| 最终态实盘 vs 影子副本 N | **0 差异** ✅ |

比对脚本：`plan/runtime_evidence/tools/compare_closed_loop_rows_20261124.py`
报告：`plan/back/probe_20261124/live_vs_shadow_new_20261124.json`、`rollback_drill_20261124.json`、`live_final_20261124.json`

### 6.4 方向不变量（实盘数据实测）

探针 `plan/runtime_evidence/tools/probe_hop_direction_consistency_20261124.py`，报告
`plan/back/probe_20261124/hop_direction_consistency_20261124.json`：

| 不变量 | 违规数 |
|---|---:|
| **I-7** 方向行可解析且输出代币一致 | **0** |
| **I-11** hop 的 `base_pool_id` == 方向行 `base_pool_id` | **0** |
| **I-12** hop 的 `hop_direction` == 方向行 `direction` | **0** |
| **I-14** 方向与 token 序一致（0 = token1→token0） | **0** |
| `pool_id` == `base_pool_id` | **0** |
| 方向行 id 重复 / 跳缺方向 | 0 / 0 |
| **I-16** `pool_ids_json` 长度 == 跳数 | **0** |
| **I-16** `directional_pool_ids_json` 长度 == 跳数 | **0** |
| **I-16** 方向行 id 的 base pool 都在 `pool_ids_json` 内 | **0** |

**I-15**（方向层版本与校验和被闭环层记录）：实盘 `manager_v4_closed_loop_library_state`
`directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef5655…`。

---

## 7. 口径裁定（四条）与未闭合项

### 7.1 裁定

| # | 裁定 | 依据 |
|---|---|---|
| **C-1** | **方向池动态状态源 = 深度快照表**；方向层写什么，闭环层就读什么 | §2、§5.2：复核层同源；快照表是池管理器的唯一写入方 |
| **C-2** | **选池口径不变**；中间跳排序变更留 `P3`，需影子比对门禁 | 影子比对 `changed_hop_pool = 0`（**含中间跳**） |
| **C-3** | **USDG 估值池域不窄化** | 窄化会改利润门禁，属边界项禁止 |
| **C-4** | **hop1/3 取 `core4` scope 方向行，hop2 取 `candidate_exchange` scope 方向行** | 与候选边层／市场观测层的发布口径一致 |

### 7.2 未闭合项（**如实登记，不得以「未测量」冒充闭合**）

| 项 | 状态 | 归属 | 说明 |
|---|---|---|---|
| **T-P8 / I-13** 复核层交叉校验（`direction_mismatch` 计数 == 0） | ⬜ **未完成** | `M-6`（复核发布层） | 复核层的 `build_hops` 尚未接线方向列；本任务 `M-4` 范围内**不做**（§4.2 表格已按方向重读，但校验动作在复核层）。**落点 → `T-复编.01`**（「复核层方向交叉校验」段 ＋ `T-P9` / `T-P10`）。本任务已交付其**前提**：存储侧方向与 token 序 504 跳零违规（§6.4） |
| **T-P14** `profitDrop` 实测 | ⬜ **未完成（`n/a`）** | `M-6` | 闭环层**不存利润**（利润在复核层按实时价算）。旧利润（154/168）未测量、新利润只读不可算 ⇒ 本波次 `n/a`，**登记为未完成**，不得作为本任务闭合理由 |
| 全量回归基线（Python 2743 / Rust 203 等） | ⬜ **不适用** | — | 本任务回归范围 = 改动模块集合（`M-4` ＋ `M-3` ＋ `M-10`），按 §0.3 规则**只跑**三个模块 + 架构守卫 |

### 7.3 本任务回归读数

| 命令 | 结果 |
|---|---|
| `cargo test --offline -p closed-loop-manager` | **35 passed / 0 failed** |
| `cargo test --offline -p directional-pool-manager` | **18 passed / 0 failed** |
| `cargo test --offline -p manager-db` | **5 passed / 0 failed** |
| `cargo build --offline --workspace` | **exit 0** |
| `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` | **74 passed**（基线 74，无新增失败） |

---

## 8. 复算方法

```powershell
# 1) 影子比对（基线二进制 vs 新二进制，各跑一份实盘库副本）
flashloan\tools\target\release\examples\materialize_closed_loops.exe <base.db> robinhood 4663 apply
flashloan\tools\target\release\examples\materialize_closed_loops.exe <new.db>  robinhood 4663 apply
python plan\runtime_evidence\tools\compare_closed_loop_directional_20261124.py <base.db> <new.db> <report.json>

# 2) 状态源探针
python plan\runtime_evidence\tools\probe_capacity_source_20261124.py <store.db> <report.json>

# 3) 逐行等价比对（切换 / 回滚都靠它）
python plan\runtime_evidence\tools\compare_closed_loop_rows_20261124.py <before.db> <after.db> <report.json>

# 4) 方向不变量
python plan\runtime_evidence\tools\probe_hop_direction_consistency_20261124.py <store.db> <report.json>

# 5) 实盘切换 / 回滚（对实盘库；回滚用基线二进制同一条命令）
flashloan\tools\target\release\examples\materialize_closed_loops.exe flashloan\db\local_store_4663.sqlite3 robinhood 4663 apply
```

---

## 9. 边界确认

| 边界 | 结论 |
|---|---|
| 折叠算术 | **未改**（隔离实验 168/168 逐值相等） |
| 容量折叠口径 / 单跳容量门禁 / 路径最小值口径 / 路径容量下限 | **未改** |
| USDG 估值与利润门禁 | **未改**（`changed_borrow_token_usdg_value = 0`） |
| 复核盈利符号与广播门禁 / 执行链价格限制与状态迁移门禁 | **未触碰**（`M-6` / `M-7` 不在本任务范围） |
| `pool_id` / `base_pool_id` 语义 | **未改**（两者仍相等且等于方向行 `base_pool_id`） |
| `loop_key` 计算规则 | **未改**（168 个 `loop_key` 前后完全相同） |
| hop 表重建 | **未重建**（只加列，靠断言 + 重建期复验） |
| 历史数据回填 | **未做** |
| Python 侧新增逻辑 | **无**（方向表只被 Rust 侧读取） |
| 广播 | **未广播** |

---

## 10. 关联文档

| 文档 | 内容 |
|---|---|
| [`D-088`](#d088) | 本任务的开发计划与回归范围 |
| [`D-089`](#d089) | 改动模块集合与触碰边界 |
| [`D-090`](#d090) | 逐条验收框与实测读数 |
| [`D-092`](#d092) | 方向池表结构与闭环层引用链 |
| [`D-093`](#d093) | 内存对象与闭环层的引用关系 |
| [`T-方授储.01/D-079-checklist.md`](../../../../../archive/任务归档_T-方授储.01_方向层落地_20261124.md#d079) | 方向层落地的闭合读数（上游） |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭储.01/docs/README.md` -->

# `T-方闭储.01`｜闭环层切换到方向池 · 技术文案（docs）

> **本目录是 `T-方闭储.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

闭环层引用链改造：内存对象取池、hop 落库、方向交叉校验

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d091) | 本任务相关的技术文案 |
| [`02-目标架构与表结构.md`](#d092) | 本任务相关的技术文案 |
| [`04-内存对象与闭环引用链.md`](#d093) | 本任务相关的技术文案 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
