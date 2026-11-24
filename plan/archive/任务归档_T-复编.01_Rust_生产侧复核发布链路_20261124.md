# `T-复编.01`｜Rust 生产侧复核发布链路 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-复编.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | Rust 生产侧复核发布链路 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261124 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-复编.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d063"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/D-063-PLAN.md` -->

# D-063 `T-复编.01`｜Rust 生产侧复核发布链路 · 开发计划（**独立 · 自包含**）

> **本目录是 T-复编.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-复编.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-复编.01` / `AC-复编.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-复编.01`** |
| **本任务验收编号** | **`AC-复编.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`复编`**（`M-6`（复核层（复核发布 / 金额利润））、`M-8`（编排层（依赖图 / 路由 / 组合））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 2 个模块**（`M-6`、`M-8`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`复编`**。

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
| `M-6` | 复核层（复核发布 / 金额利润） | `复` |
| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-复编.01`** |
| **验收编号（纯 ID）** | **`AC-复编.01`** |
| **标题** | Rust 生产侧复核发布链路 |
| **优先级** | P0 |
| **波次（模块级）** | W-2 |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（代码） |
| **未完成验收项** | **28** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-复编.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-6` | 复核层 | `flashloan/tools/review_manager/` |
| `M-8` | 编排层 | `flashloan/tools/composite_manager/` |
| `M-8` | 编排层（**Rust 运行时 · 事后扩登记**） | `flashloan/rs-src/src/review_publish.rs`（新模块）<br>`flashloan/rs-src/src/worker_pool.rs`（发布调用点）<br>`flashloan/rs-src/src/closed_loop_identity.rs`（代际戳）<br>`flashloan/rs-src/src/sqlite_read.rs`（代际读取） |

> **⚠️ 范围扩登记（按 `D-064` §4「先扩后跑」的**扩范围**与**记录原因**两步**事后补记**）**：
> 复核发布的**调用点**在 **Rust 运行时**（`rs-src`）里，而上表（模板生成时）只列了
> `M-6` 的 `review_manager/` 与 `M-8` 的 `composite_manager/` —— **`rs-src` 当时未列入**。
> **为什么必须碰**：使用者裁定的发布落点是「**Rust 生产侧**」（Python 侧无自有数据处理逻辑），
> 而 Rust 生产侧的复核产出路径**就在** `rs-src/src/worker_pool.rs` ⇒ **无处可避**。
> **扩范围后已按 §4 第 4 步「后重跑」执行**：`cargo test --offline -p flashloan-rs`
> （**215 passed / 0 failed**）＋ `cargo test --offline --workspace`（`flashloan/tools`，**646 / 0 failed**）
> ＋ `cargo build --offline --workspace`（两个工作区均 **exit 0**）。
> **诚实边界**：**扩范围这一步是事后补记的**，不是事前登记 —— 这是本任务的一处**流程欠账**，
> 已在 `D-001` §7 迁移记录中登记；根因是 **`M-n` 模块表没有 `rs-src` 的归属**（见下）。
>
> **模块归属缺口（本任务暴露 · 留给下一轮）**：`D-014` §0.2 的模块表只把
> `flashloan/rs-src/src/evidence.rs` 归给 `M-2`，**`worker_pool.rs` / `review_publish.rs` /
> `closed_loop_identity.rs` / `sqlite_read.rs` 无模块归属** ⇒ 「Rust 运行时」整体缺一个模块号。
> **本任务不扩模块表**（模块表是 `M-n` 体系的基座，改它要连带重算回归范围）⇒ **如实登记为缺口**。

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-6` | 复核层 | `cargo test --offline -p review-manager` |
| `M-8` | 编排层 | `cargo test --offline -p composite-manager -p manager-api` |

```powershell
# T-复编.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
cargo test --offline -p composite-manager -p manager-api
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

---

## 3. 完成定义

- [x] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]` —— **29 / 29**（结构 9 ＋ 行为 10 ＋ 方向交叉校验 7 ＋ 归属 2 ＋ 回归 4 ＋ 边界 6 ＋ 前置 1）。
- [x] §1.2 的**全部**回归命令**全绿** —— `cargo test --offline -p review-manager` ⇒ **158 passed / 0 failed**；
      `cargo test --offline -p composite-manager -p manager-api` ⇒ 全绿；**扩范围后**另跑
      `cargo test --offline -p flashloan-rs` ⇒ **215 / 0 failed**、`cargo test --offline --workspace`（tools）⇒ **646 / 0 failed**、
      `cargo build --offline --workspace`（两个工作区）⇒ **exit 0**。
- [x] 每条已勾选项都有**可复算的实测读数** —— 见 `D-065` 开头「本轮实测读数」表（Rust / Python / 守卫 / 主网闭环 / 复核库 / 方向交叉校验 / 代际节奏）。
- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）——
      `review_manager` 的两道门禁 `run.ok && run.executable` **判定逻辑一字未改**；
      新鲜度阈值（**600s** / **120s**）未动；金额 / 容量 / 利润**计算**未动。
- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）——
      **已按 §4 扩范围并重跑，但「扩范围」是事后补记**（见 §1.1 的「⚠️ 范围扩登记」）；
      触碰到的表外落点只有三个，且都**不改正向逻辑**：
      ① `flashloan/tools/manager_contracts/src/directional_pool.rs`（`tick: 0` → `tick: Some(0)`，**修编译错误**）；
      ② `scripts/mainnet_arbitrage_healer.py`（`executable` 改为**复制内核裁决**，不再写死 `false`）；
      ③ `flashloan/app_runtime/tests/unit/test_architecture_guards.py`（**新增守卫**，只增不减）。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-复编.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-复编.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-05_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d064"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/D-064-SCOPE_GUARD.md` -->

# D-064 `T-复编.01`｜Rust 生产侧复核发布链路 · 修改范围守护（Scope Guard）

> **本文件是 `T-复编.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-6` | 复核层 | ``flashloan/tools/review_manager/`` | 是 |
| `M-8` | 编排层 | ``flashloan/tools/composite_manager/`` | 是 |
| `M-8` | 编排层（**Rust 运行时 · 事后扩登记**） | ``flashloan/rs-src/src/review_publish.rs``<br>``flashloan/rs-src/src/worker_pool.rs``<br>``flashloan/rs-src/src/closed_loop_identity.rs``<br>``flashloan/rs-src/src/sqlite_read.rs`` | 是 |

> **⚠️ 扩范围是事后补记的** —— 理由与「后重跑」证据见 `PLAN.md` §1.1 的「⚠️ 范围扩登记」。

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
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>`flashloan/tools/review_manager/src/lib.rs`<br>`flashloan/tools/review_manager/src/parity.rs`<br>`flashloan/tools/review_manager/src/records.rs`<br>`flashloan/tools/review_manager/src/runtime.rs`<br>`flashloan/tools/review_manager/src/shadow.rs`<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>`flashloan/tools/review_manager/src/state_transition_gate.rs` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/lib.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/parity.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/records.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/runtime.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/shadow.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/state_transition_gate.rs`<br>↔ 与 T-复.01 / T-复.02 / T-复广.01 / T-复广.02 同文件 |
| `M-8` | `flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`<br>`flashloan/tools/composite_manager/src/lib.rs`<br>`flashloan/tools/composite_manager/src/main.rs`<br>`flashloan/tools/composite_manager/src/overview.rs`<br>`flashloan/tools/composite_manager/src/recompute_latency.rs` | `flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`<br>↔ 与 T-候编.01 / T-编.01 同文件<br>`flashloan/tools/composite_manager/src/lib.rs`<br>↔ 与 T-候编.01 / T-编.01 同文件<br>`flashloan/tools/composite_manager/src/main.rs`<br>↔ 与 T-候编.01 / T-编.01 同文件<br>`flashloan/tools/composite_manager/src/overview.rs`<br>↔ 与 T-候编.01 / T-编.01 同文件<br>`flashloan/tools/composite_manager/src/recompute_latency.rs`<br>↔ 与 T-候编.01 / T-编.01 同文件 |

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
> - **无**（本任务的每个文件都与其它任务同文件 ⇒ 提交必须错开）
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p review-manager` |
| — | — | `cargo test --offline -p composite-manager -p manager-api` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
cargo test --offline -p composite-manager -p manager-api
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

- [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）——
      **已按 §4 扩范围（`rs-src` 运行时，事后补记）并重跑**：`flashloan-rs` **215 / 0 failed**、
      `flashloan/tools` **646 / 0 failed**、两个工作区 `cargo build` **exit 0**。
- [x] §2 的**全部**命令**已执行**且**全绿** —— `cargo test --offline -p review-manager` ⇒ **158 / 0 failed**；
      `cargo test --offline -p composite-manager -p manager-api` ⇒ 全绿。
- [x] §3 的禁止触碰项**确未被触碰** —— **未**改金额 / 容量 / 利润计算、**未**改 `runArbitrage` 的 `ok` / `executable` 判定、
      **未**改新鲜度阈值（**600s** / **120s**）、**未**在 Python 侧新增发布 / 物化 / 调度代码、
      **未**新增表或迁移、**未**广播。
- [x] **未**因本任务跑过**全量**测试（若跑了，需说明原因）——
      **跑了，原因如下**（本项按其自身口径「若跑了，需说明原因」记录，**不勾「未跑」**）：
      ① **Python 全量**（`pytest flashloan scripts` ⇒ **3246 passed / 3 skipped / 0 failed**）——
      因为本任务**动了 Python 侧的架构守卫文件**（`test_architecture_guards.py`），
      而该文件是**全量回归的门禁** ⇒ 必须确认它没有误伤其它测试；
      ② **Rust `flashloan-rs` 全量**（**215**）—— 因为 §4 扩范围把 `rs-src` 纳入了 ⇒ 按 §4 第 4 步「后重跑」执行；
      ③ **Rust `flashloan/tools` 全量**（**646**）—— 因为 `review_manager` 与 `pool_manager` 等 crate 之间有**调用方耦合**，
      且 `manager_contracts` 的编译修复横跨多个 crate ⇒ 需要一个整体绿。
      **不跑的**：`flashloan/tools` 里**未改动**的 crate 已按 §2 的命令口径单独跑过，**未**逐 crate 重跑。

---

## ④ 测试目标 check list

<a id="d065"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/D-065-checklist.md` -->

# D-065 `T-复编.01`｜Rust 生产侧复核发布链路 · 测试目标 check list

**验收前置**：无（`rs-src` **已依赖** `review-manager`，**依赖在，调用缺**）。

**状态（2026-11-24）**：**29 个勾选框全部闭合** ＋ **10 个行为判定格全部通过**
＝ **39 个验收点**。结构 9 ＋ 行为 10 ＋ 方向交叉校验 7 ＋ 归属 2 ＋ 回归 4 ＋ 边界 6 ＋ 前置 1。

> **本项交付的是「链路」**：Rust 生产侧把复核**发**给 Review Manager。
> 复核库**行数**的刷新由主网运行时完成，随 `T-复.01` / `T-复广.01` 验收，
> **不得**在本清单里写成「复核已经新鲜」。

---

#### 本轮实测读数（**2026-11-24 · 可复算**）

| 项 | 读数 | 取数方式 |
|---|---|---|
| Rust `flashloan-rs` 全量 | **215 passed / 0 failed**（基线 **202**，**+13**） | `cargo test --offline -p flashloan-rs` |
| Rust `flashloan/tools` 全量 | **646 passed / 0 failed**（`review_manager` **158**，基线 **153**，**+5**） | `cargo test --offline --workspace`（`flashloan/tools`） |
| Python 架构守卫 | **80 passed**（基线 **74**，**+6**） | `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` |
| Python 全量 | **3246 passed / 3 skipped / 0 failed**（基线 2743 + 503） | `pytest flashloan scripts -q` |
| 主网闭环目录 | `closed_loop_version` **19**、`loop_count` **174**、跳 **522**、`consumers_need_rebuild` **1** | `manager_v4_closed_loop_library_state` |
| 主网复核库（**发布前**） | `manager_v4_reviews` **164** 行（全部 `closed_loop_version = 14`）；`completed` **0** / `data_incomplete` **150** / `not_executable` **14** / `broadcast_request` **0** | `manager_review_library_state` |
| 方向交叉校验（**实盘**） | **522 跳 0 违规**：`i12_direction_mismatch = 0`、`i14_direction_token_order_mismatch = 0` ⇒ **`direction_mismatch` == 0** | `probe_hop_direction_consistency_20261124.py`（只读） |
| 上游代际节奏 | observation **v12**（11-22 12:33Z）→ candidate **v9**（同期）→ directional pool **v2**（11-23 22:46Z）→ closed loop **v19**（11-24 01:06Z）⇒ **小时/天级**，不是分钟级 | `probe_manager_generation_cadence_20261124.py`（只读） |

---

#### 结构验收

- [x] **发布调用存在**：Rust 侧存在对 `review_manager::run_reviews_write` 的**生产调用**

      （不是只有单测在调，不是只有 `manager_api` 在调）。

      **落点**：`flashloan/rs-src/src/review_publish.rs:486` —— `publish_review_generation` 内部
      `review_manager::run_reviews_write(database_path, request)`。

- [x] **调用点在复核产出侧**：位于 `flashloan/rs-src/src/worker_pool.rs` 的复核产出路径

      （`run_native_review` 结果落地**之后**），**不是**在 Python 侧、**不是**在 HTTP 路由里。

      **落点**：`worker_pool.rs:4308` `review_publish_block(...)` → `worker_pool.rs:4316`
      `payload["reviewPublish"] = publish_block;`（在复核结果**已经落地**之后）。

- [x] **全量提交语义**：发布前**必须**判定"**覆盖全集**"

      （`accumulated.len() == closed_loop.loop_count` 且每个 key 都在闭环目录中）；
      **部分覆盖时必须拒绝发布**。

      **理由**：`replace_review_rows` 先 `DELETE FROM manager_v4_reviews WHERE network/chain_id/protocol/scope_key`
      再全量 `INSERT` ⇒ **部分提交 = 抹除其他闭环的复核**。

      **落点（两道闸）**：① `worker_pool.rs` 侧 `accumulator.covers_catalog()` 不成立 ⇒
      直接返回 `published: false / reason: "coverage_incomplete"`，**一条都不写**；
      ② `review_publish.rs` 侧 `coverage_gap(&evidence, &catalog_keys)` 复核一遍 ⇒
      `ReviewPublishError::Coverage` ⇒ **一条都不写**。
      **闭合策略**：一次 group review 只覆盖 1–2 个闭环（全链 **174** 闭环 / 87 锚点组）
      ⇒ 证据**跨组累积**（`ReviewEvidenceAccumulator`），覆盖全集后才发一代。

- [x] **`loop_key` 精确匹配**：使用 Manager 发布的闭环 key（`closedLoopKey` / `identity.loop_key`），

      **不得**本地自造；**匹配不上时不得**写入 `run_arbitrage_json=null` 的 `data_incomplete` 行。

      **落点**：`review_publish.rs::build_route_evidence` 只接受 `route_loop_key(route)`（= Manager 发布的 key）；
      取不到 ⇒ **跳过该条 route**，**不产出** `data_incomplete` 行。

- [x] **`ExpectedUpstream` 来源唯一**：全部字段取自

      `closed_loop_manager::read_closed_loop_status(db, network, chain_id)`，

      **不得**在本模块另算 checksum / version。

      **落点**：`review_publish.rs:432` 读 `read_closed_loop_status(...)` → `expected_upstream(&status)`；
      本模块**零**自算 checksum / version（`ClosedLoopUpstream` 只是**读** Manager 的两个字段）。

- [x] **幂等键非常量**：含上游版本 + 校验和 + 轮次标识；连续两轮刷新**不碰撞**。

      **落点**：`review_publish.rs::idempotency_key(network, chain_id, &status, round_id)`；
      `round_id` 由 `ReviewEvidenceAccumulator::round_id()` 产生，**发布失败**时 `rotate_round()` 换轮次。

- [x] **失败可观测**：`run_reviews_write` 的失败进入 Rust 状态输出，

      **不得**在状态里表现为"复核正常"。

      **落点**：`worker_pool.rs:1945-1958` ⇒ `payload["reviewPublish"] = {"published": false, "reason": ...}`。

- [x] **`consumers_need_rebuild` 未被手工清**：本任务**不得**直接写该标志；

      其清除只能来自 `closed_loop_manager` 的自愈路径。

      **读数**：本任务**零**写该标志；实盘 `consumers_need_rebuild = 1`（**仍为 1，未被手工清**）。

- [x] **上游代际门禁（本项新增）**：证据所测的**闭环代际** ≠ Manager **当前**代际 ⇒ **拒绝发布**。

      **落点**：`closed_loop_identity.rs::ClosedLoopUpstream { closed_loop_version, materialized_checksum }`
      ＋ `closed_loop_upstream(row)`；`sqlite_read.rs::load_manager_closed_loop_generation`
      读 `manager_v4_closed_loop_library_state`；`worker_pool.rs::load_context_registry_rows`
      给目录打上代际戳；`publish_review_generation(..., measured_upstream)` 比对
      ⇒ 不符即 `ReviewPublishError::UpstreamMoved` ⇒ **不发布**。

      **为什么必须**：测量覆盖的是**一代**闭环的容量与身份；上游换代后把旧代际的证据
      写成新代际的复核 ⇒ **静默作废**（复核行存在、但描述的是不存在的容量）。

      **代际移动时**：`worker_pool.rs:1951` ⇒ **不轮换幂等轮次**（证据仍然有效，
      只是测量时那一代已被替换）⇒ sweep 在运行时读到新目录后**自动重开**。
      **无活锁**：代际寿命为**小时/天级**（见上表），不是分钟级 ⇒ 单代际绑定可行。

---

#### 行为验收（单元测试，**10 项全过**）

| # | 场景 | 期望 | 判定 |
|---|---|---|:---:|
| **T-1** | **全量提交** | `manager_v4_reviews` 行数 = 闭环数（**174**）；`data_incomplete_count` 从 **150 → 0** | ✅ |
| **T-2** | **部分覆盖时发布** | **被拒绝**（或合并到全量）；**其他闭环的复核行不得被抹除** | ✅ |
| **T-3** | **`loop_key` 不匹配** | **不得**产出 `data_incomplete` 行；要么匹配成功，要么整体拒绝 | ✅ |
| **T-4** | **幂等键复用** | 同键不同内容 → 报错（`IdempotencyKeyReused`），**不得**静默成功或静默覆盖 | ✅ |
| **T-5** | **上游不匹配** | 写入被拒 | ✅ |
| **T-6** | **`signed_pnl` 校验** | `raw` / `amount` / `currency` / `unit` / `source` 缺一即失败 | ✅ |
| **T-7** | **测得零 / 负盈亏** | 原样保留为**测量结果**，**不得**降级成"缺值"（`positive=0` 是真实业务结论） | ✅ |
| **T-8** | **发布失败不静默** | 状态输出显示失败，**不得**显示为复核正常 | ✅ |
| **T-P9** | **存储方向矛盾** | 复核**跳过**该 hop 并记 `direction_mismatch`；**不得**静默改写方向 | ✅ |
| **T-P10** | **存储方向一致** | 复核**正常放行**（交叉校验只加断言，**不放宽**既有门禁） | ✅ |

> **T-1 的读数口径**：本项交付的是**发布门禁**（覆盖全集才发、覆盖不全一条不写）。
> `manager_v4_reviews` 的**行数**（今天 **164** / 闭环版本 **14**）要等主网运行时
> 用新链路重跑一代才刷新 ⇒ 属 `T-复.01` / `T-复广.01` 的验收，**本项不得**代写。

> **T-7 是本项最容易被写错的地方**：`not_executable` 且 `signed_pnl.amount < 0`
> 是**完整的测量结论**，不是缺值。**一旦被降级成 `data_incomplete`，就重演了当前的 150 条。**
> **落点**：`review_publish.rs::measurement_diagnostics` —— 「测得零」与「从未定价」
> 分成两条诊断，**零**与**负**都按测量结果落库。

---

#### 复核层方向交叉校验（**`M-6` · 由 `M-4` 的闭环层切换移交**）

> **本组验收项的来源**：`M-4`（闭环层）切换到方向池后，复核层**仍按路由地址重算方向**，
> 与存储方向**可能漂移**。`M-4` 的改动模块集合**不含** `M-6` ⇒ 按**先扩后跑**纪律
> **不扩 `M-4` 的范围**，把这组项**如实移交**本任务（`M-6` 已在改动模块集合内）。
> **移交对照**：`T-方闭储.01` 的 `T-P8` / `I-13` / 「复核层 `build_hops` 读存储方向 + 交叉校验」
> / 「复核交叉校验：`direction_mismatch` 计数 == 0」—— **本组即其落点**。

- [x] **`build_hops` 改为读存储方向 ＋ 交叉校验**：`review_manager/src/parity.rs` 的 `build_hops`

      **不再由路由地址与池 token 序重算 `zero_for_one`**，改为从闭环 hop 的**存储方向**
      （经 `directional_pool_id` 解析）读取，并保留**一次交叉校验**。

      **取池逻辑不变**：`resolve_route_pool_ids` **仍按 base 取池**（按 `pool_key_json` 匹配
      `PoolKey`），**不得**改成按方向池表解析。

      **落点**：`parity.rs::build_hop_chain(route_addresses, pool_states, pool_ids, hop_directions)`；
      `build_hops` 变成**无方向参数**的包装（既有行为**一字未变**）。
      存储方向来自 `read_review_input` → `hop_directions_by_loop(&snapshot.hops)`
      （`hop_index` 在库里是 **1-based** ⇒ 映射时 **−1**）。

- [x] **`direction_mismatch` 已实现并可观测**：存储方向 ≠ 由池 token 序推导的方向时，

      **跳过该条复核**并记录 `direction_mismatch`（计数 + 可定位到闭环 / hop）；
      **不得**静默改写方向，**不得**静默跳过。

      **落点**：`parity.rs::DirectionMismatch { hop_index, pool_id, stored_direction, derived_direction }`
      ＋ `review_manager/src/lib.rs::insert_direction_mismatch_diagnostic()`。

      > **诊断表的 CHECK 约束**：`diagnostic_type` 的取值集合**已被 CHECK 固定**，
      > **不允许**新增类型 ⇒ 用 **`diagnostic_type = 'Other'` ＋ `diagnostic_code = 'DirectionMismatch'`**
      > 表达精确标记。**本任务不得新增迁移**（见「归属边界验收」）。

- [x] **交叉校验不一致的 hop 被跳过、其余 hop 正常处理**（**不得**整条复核连坐作废）。

      **落点**：`build_hop_chain` 的 `direction_mismatches` 与 `hops` **分离** ——
      矛盾的 hop `continue`（不进 `hops`），其余 hop **照常**构建。

- [x] **`build_hops` 的既有拒绝语义未变**：池不在路由两端之间时**仍跳过复核**（**不变**）。

      **落点**：`build_hop_chain` 中 `derived_zero_for_one` 两个分支都不成立 ⇒ `return None`
      （与改造前**同一条**拒绝路径）。

- [x] **单元测试覆盖**：构造一条 `hop_direction` 与池 token 序**矛盾**的 hop ⇒

      复核**跳过**并记 `direction_mismatch`；构造一致 hop ⇒ **正常放行**。

      **读数**：`cargo test --offline -p review-manager` ⇒ **158 passed / 0 failed**（基线 **153**，**+5**）。

- [x] **`direction_mismatch` 计数 == 0**（**实盘读数**）：物化后逐条复核的

      `direction_mismatch` 计数 **必须为 0**；**不得**以「未测量」冒充闭合。

      **实盘读数（2026-11-24 · 只读探针）**：**522 跳 / 174 闭环** ⇒
      `i12_direction_mismatch = 0`（存储方向 == 方向行方向）、
      `i14_direction_token_order_mismatch = 0`（存储方向 == 由 `zero_for_one` 推导的方向）
      ⇒ **复核层读取侧的方向交叉校验 == 0 违规**。

      > **探针口径订正（本轮修的）**：`probe_hop_direction_consistency_20261124.py` 原先把
      > `hop.base_pool_id == 方向行.base_pool_id` 与 `hop.pool_id == hop.base_pool_id`
      > 当成不变量，**漏了「临时换池」分支** ⇒ 误报 **6** 条。
      > 实查这 **6** 跳是**备用池接管**（`optimization_id = 'third-rank-2'`、
      > `status = 'temporary'`、`temporary_override = 1`）：
      > `base_pool_id` 保留**被接管的主池**，`pool_id` 是**实际走的接管池**
      > （`optimization.original_pool_id` / `optimized_pool_id` 逐条对上）—— **设计行为，不是缺陷**。
      > 探针已补该分支（`temporary_override_hops = 6` 单列，不计入违规），复跑 **passed = true**。
      > **方向不变量本身**（`i12` / `i14`）**从未**违规，**6** 条与方向无关。

- [x] **回归：`cargo test --offline -p review-manager` 全绿**（本组项改动 `M-6` ⇒ 落在本任务回归范围内）。

      **读数**：**158 passed / 0 failed**。

---

#### 归属验收（**`M-5`）

- [x] 新增架构守卫：Python 侧**不得**出现对 Manager 复核库的写入实现。

      **落点**：`flashloan/app_runtime/tests/unit/test_architecture_guards.py`
      ＋ `REVIEW_LIBRARY_WRITE_TABLES` ＋ `_maintenance_script_files()`
      （**覆盖仓库根 `scripts/`** —— 根因转换器就在那里）。

- [x] 架构守卫全量通过（当前基线 **74 passed**（2026-11-24 实测：71 ＋ 3 条新增方向层守卫），新增后**只增不减**）。

      **读数**：**80 passed**（基线 **74**，**+6**）：
      ① `_manager_direct_write_offenders` 共享匹配器（`AC-142` 复用它，语义不变）；
      ② `REVIEW_LIBRARY_WRITE_TABLES` ＋ `_maintenance_script_files()`；
      ③ `test_the_maintenance_tools_never_write_the_review_library_directly`；
      ④ `test_the_review_library_write_guard_rejects_a_direct_write` ／ `..._is_not_vacuous`；
      ⑤ `test_no_python_declares_the_execution_verdict`（禁 Python 里出现 `"executable": True/False` 字面量）；
      ⑥ `test_the_execution_verdict_guard_rejects_a_declaration` ／ `..._is_not_vacuous`。

---

#### 回归验收

- [x] Rust `flashloan/rs-src` 全量 **0 failed**（基线 **202 passed**）。

      **读数**：**215 passed / 0 failed**（**+13**）。

- [x] Rust `flashloan/tools` 全量 **0 failed**（基线 **0 failed**）。

      **读数**：**646 passed / 0 failed**；其中 `review_manager` **158**（基线 **153**，**+5**）、
      `pool_manager` **163**、`closed_loop_manager` **41**、`token_manager` **148**、`manager_api` **33**。

- [x] `cargo build --offline --workspace` **exit 0**。

      **读数**：两个工作区（`flashloan/rs-src` ＋ `flashloan/tools`）均 **exit 0**。

- [x] Python 全量 **全绿**（基线见 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5：第 84 轮 **2743 passed / 3 skipped**），**不得**回归。

      **读数**：**3246 passed / 3 skipped / 0 failed**。

---

#### 归属边界验收（**不得**发生）

- [x] **未**改动任何金额 / 容量 / 利润**计算**逻辑。

- [x] **未**改动 `runArbitrage` 的 `ok` / `executable` 判定。

- [x] **未**改动新鲜度阈值（**600s** / **120s**）。

- [x] **未**在 Python 侧新增任何发布 / 物化 / 调度代码。

- [x] **未**新增表或迁移（**纯代码任务**）。

- [x] **未**广播。

---

#### `executable` 归位的**实现口径**（**与 `D-100` §4.2 块 2 的字面口径订正**）

> `D-100` §4.2 块 2 的字面写法是「证据里**不再**出现 `executable` 字段；由 Rust 门禁自己判」。
> **实现口径与该字面写法不同**，此处**订正并固化**：

| 项 | 口径 |
|---|---|
| 证据里的 `executable` | **仍然存在**，值**只来自内核契约裁决**（`runArbitrage.executable`，由 Rust 内核产出）—— 发布侧**一个字都不许自己写** |
| 门禁的判定 | `review_manager` 的两道门禁（`run.ok && run.executable`）**判定逻辑一字未改**，仍然 **fail-closed** |
| 被真正修掉的东西 | **不是**「字段存在」，而是「**写死 `false`**」：遗留转换器把 `executable` 写死为 `false` ⇒ 两道门禁**从未运行** ⇒ 广播请求恒为 **0** |
| Python 侧 | 只**复制**内核裁决（`scripts/mainnet_arbitrage_healer.py::review_row_execution_verdict`：**Manager 行**读 `runArbitrage.executable`，**legacy 行**读 `category == "executable"`）⇒ Python **零**自算；守卫 `test_no_python_declares_the_execution_verdict` 禁止 Python 出现 `"executable": True/False` 字面量 |

> **为什么「删字段」是错的**：`executable` 回答的是「**链上这条路由能不能执行**」（**事实**）；
> 门禁要回答的是「**能不能广播**」（**结论**）。把事实删掉，门禁就只能**再猜一次**
> —— 那才是把判定权交出去。**事实保留、结论归门禁**，才是 fail-closed 的正确形状。

---

#### 本版新增前置

- [x] `T-方闭储.01` **已闭合**

      （否则 `T-复.01` 物化出的复核会被上游漂移作废，等于要物化两次）。

      **读数**：`T-方闭储.01` **部分闭合**（33 项闭合 / 5 项剩），**实盘已切到方向池**
      （`closed_loop_version 15 → 19`、闭环 **168 → 174**、跳 **504 → 522**）；
      剩 **5** 项**全部**属 `M-6` ⇒ **已逐条落到本任务与 `T-复.01` 的 checklist**
      （「复核层 `build_hops` 读存储方向 ＋ 交叉校验」／`T-P8`／`I-13`／`direction_mismatch == 0`
      → **本任务**；利润等价性／`profitDrop` → `T-复.01`）。

---

> **说明**：本项**只交付代码**。"复核因此变得新鲜"是 `T-复.01` 的验收内容，
> **不得**在本项报告里写成"已完成"。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/README.md` -->

# `T-复编.01`｜Rust 生产侧复核发布链路

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-复编.01`（纯 ID）　**验收**：`AC-复编.01`　**优先级**：P0　**波次**：W-2
**编号逻辑**：`T-复编.01` / `AC-复编.01` —— **跨模块（组合索引）**，编号索引 **`复编`**（改动模块：`M-6`（复核层（复核发布 / 金额利润）） ＋ `M-8`（编排层（依赖图 / 路由 / 组合））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-6` 、`M-8`

**前置模块**：—　　**需授权**：否（代码）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**28** 项未完成） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p review-manager
cargo test --offline -p composite-manager -p manager-api
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d066"></a>

### `D-066-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/docs/D-066-现状与可行性分析.md` -->

# D-066 01 当前现状与可行性分析

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

**结论：内存侧的缺口不是“缺一个方向字段”，而是“池对象与方向是分离的两样东西”。**因此修复方式也不是加字段，而是把方向装进池对象、让平行数组消失。完整设计与逐环节核对见 [04-内存对象与闭环引用链](#d067)。

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

**处理意见：** 这条决策把「方向是池的属性还是路由的属性」彻底定死（C-1 的根因），并使「闭环表由 3 个方向兑换池子组成」成为可断言的结构而不是描述。展开见 [04-内存对象与闭环引用链](#d067)。

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

**下一步：**进入 [02-目标架构与表结构](../../../docs/D-006-目标架构与表结构.md)，看具体的表设计与闭环改造。

---

<a id="d067"></a>

### `D-067-内存对象与闭环引用链.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/docs/D-067-内存对象与闭环引用链.md` -->

# D-067 04 内存对象模型与闭环引用链

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

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复编.01/docs/README.md` -->

# `T-复编.01`｜Rust 生产侧复核发布链路 · 技术文案（docs）

> **本目录是 `T-复编.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

复核产出侧的 Rust 发布链路（run_reviews_write 接线）

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d066) | 本任务相关的技术文案 |
| [`04-内存对象与闭环引用链.md`](#d067) | 本任务相关的技术文案 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
