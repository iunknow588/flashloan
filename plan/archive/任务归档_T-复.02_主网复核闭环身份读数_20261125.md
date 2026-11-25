# `T-复.02`｜主网复核闭环身份读数 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-复.02` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 主网复核闭环身份读数 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-复.02` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d051"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/D-051-PLAN.md` -->

# D-051 `T-复.02`｜主网复核闭环身份读数 · 开发计划（**独立 · 自包含**）

> **本目录是 T-复.02 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-复.02 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-复.02` / `AC-复.02` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-复.02`** |
| **本任务验收编号** | **`AC-复.02`**（与任务编号**同号段**） |
| **编号作用域** | **模块内（单模块索引）** |
| **编号索引** | **`复`**（`M-6`（复核层（复核发布 / 金额利润））） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-复编.02` |

本任务**只改动一个模块** `M-6`（复核层（复核发布 / 金额利润））⇒ 编号索引即该模块索引 **`复`**。



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


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-复.02`** |
| **验收编号（纯 ID）** | **`AC-复.02`** |
| **标题** | 主网复核闭环身份读数 |
| **优先级** | P1 |
| **波次（模块级）** | W-2 |
| **前置模块**（**不是**前置任务） | M-6 |
| **需授权** | 否（只读） |
| **未完成验收项** | **0**（2026-11-25 闭合） |
| **已闭合验收项** | **6** |

### 1.1 改动模块（**这一列就是 T-复.02 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-6` | 复核层 | `flashloan/tools/review_manager/` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-6` | 复核层 | `cargo test --offline -p review-manager` |

```powershell
# T-复.02 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
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

- [x] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`
      > **读数（2026-11-25）**：`D-053` 共 **6** 项，**6 / 6** 已勾；
      > 读数出处统一为 [`docs/D-127`](#d127)。
- [x] §1.2 的**全部**回归命令**全绿**
      > **读数（2026-11-25）**：`cargo test --offline -p review-manager` ⇒ **158 passed / 0 failed**；
      > 其中三个身份用例（`matched` / **未测量** / `mismatch`）**全绿**。
      > 另加工作区回归：`pool-manager 165` / `candidate-edge 14` / `candidate-exchange 7` /
      > `manager-db 5` / `rs-src 220` / 架构守卫 **80** / 受影响 7 文件 **77** /
      > 生产库零写守卫 **14** ⇒ **全绿**。
- [x] 每条已勾选项都有**可复算的实测读数**
      > **读数**：`D-127` §3（分布）＋ §4（逐项对照）＋ §5（不历史回填的三条线索）＋ §6（回归）。
      > 分布读数有**两条独立来源**（发布层自报 ＋ 独立复算）**互为交叉验证**。
- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
      > **读数（2026-11-25）**：本任务**只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）⇒ **零写入**；
      > **未**改任何门禁、阈值、幂等键或回执关联。
- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）
      > **读数（2026-11-25）**：本任务**未改任何产品代码** ——
      > 唯一新增 = 取证工具 `plan/runtime_evidence/tools/audit_review_closed_loop_identity_20261125.py`
      > （**只读**、**不入运行时**）；
      > 身份四列的**唯一写入方**是既有生产发布器，本任务**未**新增写入点。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-复.02
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-复.02 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-07_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d052"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/D-052-SCOPE_GUARD.md` -->

# D-052 `T-复.02`｜主网复核闭环身份读数 · 修改范围守护（Scope Guard）

> **本文件是 `T-复.02` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-6` | 复核层 | ``flashloan/tools/review_manager/`` | 是 |

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
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>`flashloan/tools/review_manager/src/lib.rs`<br>`flashloan/tools/review_manager/src/parity.rs`<br>`flashloan/tools/review_manager/src/records.rs`<br>`flashloan/tools/review_manager/src/runtime.rs`<br>`flashloan/tools/review_manager/src/shadow.rs`<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>`flashloan/tools/review_manager/src/state_transition_gate.rs` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/lib.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/parity.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/records.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/runtime.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/shadow.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/state_transition_gate.rs`<br>↔ 与 T-复广.01 / T-复广.02 / T-复编.02 同文件 |

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

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
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

<a id="d053"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/D-053-checklist.md` -->

# D-053 `T-复.02`｜主网复核闭环身份读数 · 测试目标 check list

- [x] 身份随 `T-复编.02` 的物化**落库**（迁移 `20261121_014` 已就位，**随重新物化写入，不历史回填**）。

      > **读数（2026-11-25 · `D-127` §3.1）**：四列**全部非空** ——
      > `closed_loop_identity_status` = `matched` **174 / 174**；
      > `closed_loop_pool_identity_status` = `verified` **174 / 174**；
      > `identity_mismatch` = NULL **174**；`closed_loop_repair_target` = NULL **174**。
      > 迁移 `20261121_014_review_closed_loop_identity_schema.sql` **已就位**（四列均可空）。

- [x] **重取**分布读数（**不得**沿用落库前读数）：

      `closedLoopIdentityStatus` / `closedLoopPoolIdentityStatus` /

      `identity_mismatch` / `closed_loop_repair_target`。

      > **读数（2026-11-25 · `D-127` §3）**：
      > - 发布层**自报**：`matched` **174** / `verified` **174** / `mismatch` **0** / 缺失 **0**；
      > - **独立复算**（`audit_review_closed_loop_identity_20261125.py`，**不采信**自报，
      >   自己读闭环目录、按 `identity_for` 的**同一规则**解析路径、逐跳比对池集合）：
      >   `matched` **174** / `mismatch` **0** / `unmeasured` **0**；
      >   `verdict = {passed: true, failureCount: 0, failures: []}`。
      > - 报告：`plan/runtime_evidence/reports/review_closed_loop_identity_audit_20261125.json`。
      > - **落库前基线（`matched=0 / mismatch=0 / unmeasured=44`）只作对照，本清单不沿用。**

- [x] **三态分开报**：`matched` / `mismatch` / `unmeasured`

      > **读数（2026-11-25 · `D-127` §3.2）**：三个计数**各自独立**输出 ——
      > `matched = 174` / `mismatch = 0` / `unmeasured = 0`。
      > 三态的**定义在代码里**（不是本文发明的）：
      > `worker_pool.rs:3191/3231/3244` 写 `mismatch`、`3260-3272` 写 `matched` / `matched_address_only`、
      > `review_manager/src/lib.rs:2812-2818` 在四列全 NULL 时返回 `None`（**未测量**）。
      > 三个状态**被测试钉住**：`review-manager` 的三个身份用例全绿（见 `D-127` §6）。

- [x] **缺失 ≠ 不匹配**（`unmeasured` **不得**计入 `mismatch`）

      > **读数（2026-11-25 · `D-127` §2）**：
      > - 迁移文件**写死**了这条语义：*"the absence is a coverage gap, **not** a mismatch,
      >   so it is stored as NULL instead of being recorded as a negative verdict"*；
      > - `read_closed_loop_identity()` 在四列全 NULL 时**返回 `None`**
      >   （代码注释：*"read back as an absent identity, never as a 'matched' verdict"*）
      >   ⇒ **未测量**永远**不会**被折进 `mismatch`；
      > - 实测：本轮 `unmeasured = 0`、`mismatch = 0` ⇒ 两个计数**互不污染**。

- [x] 诊断给出**可执行的修复目标**（`closed_loop_repair_target`）。

      > **读数（2026-11-25 · `D-127` §4 第 5 项）**：
      > - 落点链：`route.repairTarget` → `rs-src/src/review_publish.rs:840-851` →
      >   `manager_v4_reviews.closed_loop_repair_target`；
      > - 失配时写入的具体目标 = **`closed_loop_manager`**（`worker_pool.rs:3192` / `3232` / `3245`），
      >   且**同时**写 `identityMismatch`（点名 `managerPoolIds` / `routePoolIds` / `reason`）
      >   ⇒ 目标**可执行**（指到具体的管理器，不是泛泛的「请检查」）；
      > - 本轮**无失配** ⇒ 该列 **174 条全 NULL** ⇒ **不凭空造修复目标**；
      > - 失配分支的证据 = 单测 `the_review_manager_publishes_the_diagnosis_of_a_broken_identity`（✅ ok）。

- [x] **不历史回填**；落库随 `T-复编.02` 的物化**同时**发生

      > **读数（2026-11-25 · `D-127` §5 · 三条独立线索）**：
      > ① **写入时刻 = 发布时刻**：`manager_v4_reviews.updated_at` 全部 =
      > **`2026-11-25T00:53:21.622729500+00:00`**（`review_version = 104` 发布的那一秒）
      > ⇒ 身份是**随发布一起写下**的，不是事后补的；
      > ② **逐条各自测量**：`distinctReviewedAt` = **174** ⇒ 逐条重新测量；
      > ③ **代码里没有回填路径**：四列的**唯一**写入方是生产发布器
      > （`review_publish.rs::closed_loop_identity()`），全仓**无第二处**写入点。



> **落库前基线（第 60 轮，只读）**：`matched=0 / mismatch=0 / unmeasured=44`，池身份同为 `unmeasured`。

> **该读数仅作对照**，重取后**不得**沿用。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/README.md` -->

# `T-复.02`｜主网复核闭环身份读数

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-复.02`（纯 ID）　**验收**：`AC-复.02`　**优先级**：P1　**波次**：W-2
**编号逻辑**：`T-复.02` / `AC-复.02` —— **模块内（单模块索引）**，编号索引 **`复`**（改动模块：`M-6`（复核层（复核发布 / 金额利润））），该索引号段内第 `02` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-6`

**前置模块**：M-6　　**需授权**：否（只读）

**状态**：**已闭合 · 已归档**（2026-11-25）—— **6** 项**全部闭合**；
复核链重发布后重取分布读数（发布层自报 ＋ 独立复算**互为交叉验证**）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**6** 项**全部闭合** · 读数见 `docs/D-127`） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p review-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d054"></a>

### `D-054-内存对象与闭环引用链.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/docs/D-054-内存对象与闭环引用链.md` -->

# D-054 04 内存对象模型与闭环引用链

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

<a id="d127"></a>

### `D-127-主网复核闭环身份读数实测_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/docs/D-127-主网复核闭环身份读数实测_20261125.md` -->

# D-127 `T-复.02`｜主网复核闭环身份读数 · 实测报告

> **本文件是 `D-053` 全部 **6** 项验收的**唯一读数出处**。**
> 采集时刻：**2026-11-25 09:08:33（复核代次 `review_version = 104`）**，
> 闭环代次 `closed_loop_version = 20`。
> 复核库状态行的 `generated_at` = **`2026-11-25T01:06:58.855668500+00:00`**。
> 全部读数取自**实盘库只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）⇒ **零写入**。

---

## 1. 为什么这一轮能取到读数（**落库前的阻塞已解除**）

`D-053` 的「落库前基线（第 60 轮，只读）」是：
`matched = 0 / mismatch = 0 / unmeasured = 44`。

那一轮**读不到**身份，不是身份机制有问题，而是**没有可读的身份**：

| 环节 | 当时的状态 | 本轮的状态 |
|---|---|---|
| 闭环代次 | `closed_loop_version = 19`（**旧序**） | **`20`**（方向层主序已接管） |
| 复核代次 | 仍绑在旧闭环代次上 | **`review_version = 104`**，`closed_loop_version = 20` |
| `consumers_need_rebuild` | — | **0**（闭环管理器自愈清零，非手工写） |

⇒ **复核链重发布**完成后，`manager_v4_reviews` 才带上**当前代次**的身份读数。

---

## 2. 三态的**定义**（**代码即定义**，不是本文发明的）

| 状态 | 含义 | 落点 |
|---|---|---|
| `matched` | 路由的地址路径在闭环目录里解析成功，**且**路由定价的池集合与 Manager 为同一批跳发布的池集合**逐个相等** | `worker_pool.rs:3260-3272` |
| `matched_address_only` | 地址路径解析成功，但**路由不携带池集合** ⇒ 只能按地址路径判定 | 同上（`pool_identity_status == "unverifiable"` 时） |
| `mismatch` | 路径在目录里**解析不到**，或路由定价的池**不是** Manager 发布的那些池 | `worker_pool.rs:3191` / `3231` / `3244` |
| **未测量（unmeasured）** | **四列全为 NULL** —— 目录没发布任何闭环，或这条复核根本不是按当前代次测的 | `review_manager/src/lib.rs:2812-2818` |

**「缺失 ≠ 不匹配」是迁移文件写死的语义**（`flashloan/tools/manager_db/migrations/20261121_014_review_closed_loop_identity_schema.sql`）：

> *The columns are nullable on purpose. A review measured against a catalog
> that published no loop has **no** identity; the absence is a coverage gap,
> not a mismatch, so it is stored as NULL instead of being recorded as a
> negative verdict.*

⇒ `read_closed_loop_identity()`（`review_manager/src/lib.rs:2794-2826`）在四列全 NULL 时
**返回 `None`**，而不是返回 `matched` —— 代码注释原文：
*"read back as an absent identity, never as a 'matched' verdict"*。

---

## 3. 分布读数（**重取**，不沿用落库前读数）

### 3.1 发布层**自报**（直读 `manager_v4_reviews` 四列）

| 列 | 读数 |
|---|---|
| `closed_loop_identity_status` | **`matched` = 174**（`mismatch` = 0、`其他` = 0、`缺失` = 0） |
| `closed_loop_pool_identity_status` | **`verified` = 174**（`unverifiable` = 0、`mismatch` = 0、`缺失` = 0） |
| `identity_mismatch` | **NULL = 174**（**没有一条**带失配诊断） |
| `closed_loop_repair_target` | **NULL = 174**（**没有一条**需要修复目标） |

### 3.2 **独立复算**（不信发布层的自报）

工具：`plan/runtime_evidence/tools/audit_review_closed_loop_identity_20261125.py`
报告：`plan/runtime_evidence/reports/review_closed_loop_identity_audit_20261125.json`

该工具**不读**四列的判定，而是**从库里重算**：

1. 读 `manager_v4_closed_loops` ⇒ 建「有序三地址路径 → 闭环键 ＋ 逐跳池集合」目录；
2. 对每条复核，取 `path_addresses_json` ⇒ 按 `ClosedLoopCatalog::identity_for` 的**同一规则**
   解析身份路径（`[_, _, _]` 或 `[first, _, _, last]` 且首尾相同）；
3. 比对**闭环键**与**逐跳池集合**是否与目录一致。

**读数**：

```json
"threeStates": { "matched": 174, "mismatch": 0, "unmeasured": 0 }
"verdict": { "passed": true, "failureCount": 0, "failures": [] }
```

> **「独立」的含义**：复算**没有**采信发布层写下的 `matched` / `verified`，
> 而是自己把闭环目录读出来、重新解析路径、重新比对池集合 ——
> 两者**逐条一致**（174 / 174）⇒ 发布层的判定**可被复算**，不是自说自话。

---

## 4. 逐项对照 `D-053` 的 **6** 项验收

| # | 验收项 | 读数 | 结论 |
|---:|---|---|---|
| 1 | 身份随物化**落库** | 四列**全部非空**（174 / 174） | ✅ |
| 2 | **重取**分布读数 | §3.1 ＋ §3.2（**未**沿用第 60 轮基线） | ✅ |
| 3 | **三态分开报** | `matched` / `mismatch` / `unmeasured` 三个计数**各自独立**（§3.2） | ✅ |
| 4 | **缺失 ≠ 不匹配** | `unmeasured` 走**独立分支**（`review_manager/src/lib.rs:2812`）⇒ **不**计入 `mismatch`；本轮 `unmeasured = 0`、`mismatch = 0` | ✅ |
| 5 | 诊断给出**可执行的修复目标** | 代码路径 `route.repairTarget` → `closed_loop_repair_target`（`review_publish.rs:840-851`）；失配时写 `closed_loop_manager`（`worker_pool.rs:3192` / `3232` / `3245`）。本轮**无失配** ⇒ 全部 NULL（**不**凭空造目标） | ✅ |
| 6 | **不历史回填** | 见 §5 | ✅ |

---

## 5. 「不历史回填」的证据（**三条独立线索**）

| 线索 | 读数 |
|---|---|
| **写入时刻 = 发布时刻** | `manager_v4_reviews.updated_at` 全部 = **`2026-11-25T00:53:21.622729500+00:00`**，即 `review_version = 104` 的**发布那一秒** ⇒ 身份是**随发布一起写下**的，不是事后补的 |
| **逐条各自测量** | `distinctReviewedAt` = **174**（每条复核各有自己的测量时间戳）⇒ **逐条重新测量** |
| **代码里没有回填路径** | 四列的**唯一**写入方是生产发布器：`rs-src/src/review_publish.rs::closed_loop_identity()` 取内核诊断 ⇒ 交给复核管理器落库。**不存在**任何 UPDATE 回填脚本（`rg` 全仓无第二处写入点） |

> **「随物化同时发生」的精确表述**：身份**不是**闭环物化时写进复核表的，
> 而是在**复核发布**时由内核按**当前闭环目录**算出并**同时**落库。
> ⇒ 与 `D-053` 第 1 / 6 项的口径一致：**随重新物化写入**（物化换了代次 ⇒ 重发布 ⇒ 重测 ⇒ 重写），
> **不历史回填**。

---

## 6. 回归（**本任务的回归范围 = `M-6`**）

```powershell
cargo test --offline -p review-manager
```

**读数（2026-11-25）**：**158 passed / 0 failed**。

其中与本任务直接相关的**三个**身份用例**全绿**：

| 用例 | 覆盖的状态 | 结果 |
|---|---|---|
| `the_review_manager_publishes_the_closed_loop_identity_of_a_review` | `matched` ＋ `verified` | ✅ ok |
| `a_review_without_an_identity_publishes_no_identity` | **未测量**（四列全 NULL ⇒ `None`，**不是** `matched`） | ✅ ok |
| `the_review_manager_publishes_the_diagnosis_of_a_broken_identity` | `mismatch` ＋ 修复目标 | ✅ ok |

> 三个用例**合起来**正好覆盖**三态** ⇒ 三态**不是**文档约定，是**被测试钉住**的行为。

---

## 7. 诚实边界（本文件**不**声称什么）

| 不声称 | 理由 |
|---|---|
| 「身份机制在**所有**链路上都验过」 | 本轮只覆盖 `robinhood` / `4663` / `uniswap_v4` / `core4` 一条链 |
| 「`mismatch` 分支在实盘被走过」 | 本轮 `mismatch = 0` ⇒ 该分支**只有单测证据**，无实盘样本（如实登记） |
| 「`closed_loop_repair_target` 在实盘有值」 | 本轮全 NULL（**无失配**）⇒ 修复目标**只有单测证据**（`review_manager` 用例 3） |
| 「复核的**金额 / 利润**完整性已闭合」 | 那是 `T-复编.02` 的验收范围，**本任务不代签** |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复.02/docs/README.md` -->

# `T-复.02`｜主网复核闭环身份读数 · 技术文案（docs）

> **本目录是 `T-复.02` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

闭环身份落库后的分布读数（matched / mismatch / unmeasured 三态）

## 文件

| 文件 | 内容 |
|---|---|
| [`04-内存对象与闭环引用链.md`](#d054) | 本任务相关的技术文案 |
| [`D-127`](#d127) | **本清单 6 项验收的唯一读数出处**（三态分布 · 不回填证据 · 回归） |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
