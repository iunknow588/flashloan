# `T-复广.02`｜广播准入门槛与审计留痕 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-复广.02` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 广播准入门槛与审计留痕 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-复广.02` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d059"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/D-059-PLAN.md` -->

# D-059 `T-复广.02`｜广播准入门槛与审计留痕 · 开发计划（**独立 · 自包含**）

> **本目录是 T-复广.02 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-复广.02 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-复广.02` / `AC-复广.02` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-复广.02`** |
| **本任务验收编号** | **`AC-复广.02`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`复广`**（`M-6`（复核层（复核发布 / 金额利润））、`M-7`（广播层（广播准入门槛 / 请求生成 / 回执））） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-复广.01` |

**为什么是这个组合索引**：本任务**同时改动 2 个模块**（`M-6`、`M-7`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`复广`**。

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
| `M-7` | 广播层（广播准入门槛 / 请求生成 / 回执） | `广` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-复广.02`** |
| **验收编号（纯 ID）** | **`AC-复广.02`** |
| **标题** | 广播准入门槛与审计留痕 |
| **优先级** | P0 |
| **波次（模块级）** | W-3 |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（纯代码＋dry-run） |
| **未完成验收项** | **12** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-复广.02 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-7` | 广播层 | `flashloan/tools/broadcast_manager/` |
| `M-6` | 复核层 | `flashloan/tools/review_manager/` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-7` | 广播层 | `cargo test --offline -p broadcast-manager` |
| `M-6` | 复核层 | `cargo test --offline -p review-manager` |
| `M-12` | 接口层 | `cargo test --offline -p manager-api` |
| `rs-src` | 独立工作区 | `cd flashloan/rs-src; cargo test --offline` |
| 架构守卫 | 跨模块静态门禁 | `pytest app_runtime/tests/unit/test_architecture_guards.py -q` |

```powershell
# T-复广.02 的完整回归范围 = 上表所有模块的命令之并集
cd flashloan/tools
cargo test --offline -p broadcast-manager
cargo test --offline -p review-manager
cargo test --offline -p manager-api

# rs-src 是**独立工作区**，不是 tools 的成员
cd flashloan/rs-src
cargo test --offline
```

> **架构守卫为什么在范围里**：本任务新增了 `broadcast_manager` 的**新模块文件**
> （`src/admission.rs`）与**新的接口层字段**（`amountsRaw`）⇒ 架构守卫是**跨模块静态门禁**，
> 与改动模块集合有交集 ⇒ 属于**受影响的门禁**，必须跑。
> 基线 **74 passed**（2026-11-24 实测），本轮实测 **80 passed** ⇒ **只增不减**。

> **`cd flashloan/tools` 与 `cd flashloan/rs-src` 是两个工作区** —— `rs-src` **不在** tools 的 workspace 里。

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
python plan/runtime_evidence/tools/archive_task_20261124.py T-复广.02
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-复广.02 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-10_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d060"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/D-060-SCOPE_GUARD.md` -->

# D-060 `T-复广.02`｜广播准入门槛与审计留痕 · 修改范围守护（Scope Guard）

> **本文件是 `T-复广.02` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-7` | 广播层 | ``flashloan/tools/broadcast_manager/`` | 是 |
| `M-6` | 复核层 | ``flashloan/tools/review_manager/`` | 是 |
| `M-12` | 接口层 | ``flashloan/tools/manager_api/`` | 是（**强制牵连**） |
| — | `rs-src`（**独立工作区**） | ``flashloan/rs-src/src/runtime_core.rs`` | 是（**强制牵连**） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **扩范围说明**（§4「先扩后跑」· 2026-11-25）：
> `M-12` 与 `rs-src` 是**强制牵连**，不是平级改动 ——
> `submit_broadcasts` / `BroadcastSubmitRequest` 是接口层的 **public API**，签名变更 ⇒ 调用方编译失败；
> `runtime_core.rs` 对 `BroadcastError` 做**穷尽 match**，新变体必须并入 ⇒ 编译失败。
> 二者**都已在 §2 登记并实跑**。**编号不变**（编号是纯 ID）；实际改动模块集合**如实登记**在此。

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>`flashloan/tools/review_manager/src/lib.rs`<br>`flashloan/tools/review_manager/src/parity.rs`<br>`flashloan/tools/review_manager/src/records.rs`<br>`flashloan/tools/review_manager/src/runtime.rs`<br>`flashloan/tools/review_manager/src/shadow.rs`<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>`flashloan/tools/review_manager/src/state_transition_gate.rs` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/lib.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/parity.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/records.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/runtime.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/shadow.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件<br>`flashloan/tools/review_manager/src/state_transition_gate.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复编.02 同文件 |
| `M-7` | `flashloan/tools/broadcast_manager/src/lib.rs`<br>`flashloan/tools/broadcast_manager/src/runtime.rs` | `flashloan/tools/broadcast_manager/src/lib.rs`<br>↔ 与 T-复广.01 / T-广.01 同文件<br>`flashloan/tools/broadcast_manager/src/runtime.rs`<br>↔ 与 T-复广.01 / T-广.01 同文件 |
| `M-12` | `flashloan/tools/manager_api/src/lib.rs`<br>`flashloan/tools/manager_api/src/main.rs` | `flashloan/tools/manager_api/src/lib.rs`<br>↔ 与 T-广.01 同文件<br>`flashloan/tools/manager_api/src/main.rs`<br>↔ 与 T-广.01 同文件 |
| `rs-src` | `flashloan/rs-src/src/runtime_core.rs` | `flashloan/rs-src/src/runtime_core.rs`<br>↔ 与 T-复编.01 同文件 |


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
| `M-7` | 广播层 | `cargo test --offline -p broadcast-manager` |
| `M-6` | 复核层 | `cargo test --offline -p review-manager` |
| `M-12` | 接口层 | `cargo test --offline -p manager-api` |
| `rs-src` | 独立工作区 | `cd flashloan/rs-src; cargo test --offline` |
| 架构守卫 | 跨模块静态门禁 | `pytest app_runtime/tests/unit/test_architecture_guards.py -q` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cd flashloan/tools
cargo test --offline -p broadcast-manager
cargo test --offline -p review-manager
cargo test --offline -p manager-api

# rs-src 是**独立工作区**，不是 tools 的成员
cd flashloan/rs-src
cargo test --offline
```

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

> **架构守卫在范围内**：本任务新增了 `broadcast_manager` 的新模块文件
> （`src/admission.rs`）与接口层新字段（`amountsRaw`）⇒ 跨模块静态门禁受影响。
> 基线 **74 passed**，本轮实测 **80 passed** ⇒ 只增不减。

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

<a id="d061"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/D-061-checklist.md` -->

# D-061 `T-复广.02`｜广播准入门槛与审计留痕 · 测试目标 check list

**状态（2026-11-25）**：**13 项已闭合**，**1 项如实标注为「未测量」**（Python 全量 —— 按 `SCOPE_GUARD`
「未改动模块不重跑」，本任务**未改动任何 Python 文件**）。

**实测读数**：`broadcast-manager` **32**、`review-manager` **158**、`manager-api` **33**、
`rs-src` **220**、架构守卫 **80 passed**、`cargo build --offline --workspace` **exit 0**。

**改动模块集合（= 回归范围）**：`M-6` ＋ `M-7` ＋ `M-12` ＋ `rs-src`（后两者为**强制牵连**，
理由见 `../D-060` §1 与 `../D-059` §1.1.1）。

**技术文案**：判据设计、N-5 根因链与接口层接线 ⇒ `docs/D-118`。

**为什么单独立项（v2.2）**：广播**默认放开**之后（使用者裁定：交易机会实时变化、

历史记录一般不具再现性），**人工审批**这道闸门被拆掉了 ——

**机器门禁就成了唯一挡在真实交易前的东西**。

而正向利润的判定散落在 `run.ok` / `run.executable` / `signed_pnl` / 新鲜度判定的**各自调用点**，

**没有一个单一、可审计、可留痕的「放行判据」**，也没有「**凭什么发这一条**」的**逐次留痕**。

本项把 `BC-1..BC-5` 变成**可验收的代码与证据**，并作为**首次真实广播的硬前置**。



**验收前置**：`T-复编.01` 闭合（复核发布链路打通）**且** `T-广.01` 的主网 dry-run

与四类拒绝路径已逐条留证 **且** `T-复编.02` 已产出**新鲜复核**（用于正例验证）。

**本项本身不广播。**



#### b.1 结构验收



- [x] `BC-1` 五条谓词**落地为单一可审计判据**（Rust 侧，`broadcast_manager`）：

      `run.ok` ∧ `run.executable` ∧ **`signed_pnl.amount > 0`（已测量）** ∧ **120s 新鲜度** ∧ 完整性四项

      → **已完成**：`flashloan/tools/broadcast_manager/src/admission.rs`（**新增**，580 行）；
      十二条规则各有**稳定码**，`decide()` **永不失败**（不通过 ⇒ 带原因的判决，不是 Err）。
      实测：`M-7` **32 passed / 0 failed**。

- [x] 链 / 账户 / 限额 / 时间窗口按**运行时配置与自动护栏**实现：

      **缺失 ⇒ 拒绝发送**；**超限 ⇒ 自动停止**；**不在窗口 ⇒ 本轮不发送**；

      **三者都不得再作为人工审批项存在**

      → **已完成**：`BroadcastGuardPolicy`（部署配置）＋ `BroadcastAdmissionContext`（运行时上下文）；
      **护栏缺失 ⇒ 拒绝**（缺护栏 ≠ 护栏通过）。
      实测：`p2_*` 四项 ＋ `a_deployment_without_guardrails_is_refused`。

- [x] `BC-5` 的**九个审计字段**全部落库可查：复核标识 / `signed_pnl.amount` /

      复核年龄（秒）/ 幂等键 / 链标识 / 账户标识 / 限额判据 / 广播时间戳 / 结果

      → **已完成**：`BroadcastAuditRecord` 九字段 **恒存在**（测不到 ⇒ `null`，不静默省略）；
      另含完整 `admission` 轨迹与 `refusal_reason`。
      写入 attempt 的 `evidence_json` 的 `broadcastAudit` 键（**追加**，不替换调用方 evidence）。
      实测：`p1_a_positive_fresh_review_carries_the_nine_audit_fields`。

- [x] 广播**被拒绝**时**同样留痕**（拒绝原因：哪一条不满足）—— `BC-3`
      → **已完成**：写入 `manager_broadcast_attempt_diagnostics`（**既有表**，**未新增迁移**），
      复用 `diagnostic_type=evidence_incomplete` ＋ `diagnostic_code=BC-1_BC-2_ADMISSION_REFUSED`；
      **事务外独立连接**写入（拒绝会让事务回滚 ⇒ 留痕必须能活下来）；**dry-run 不写**。
      实测：`bc3_a_refusal_is_recorded_with_its_reason`、`a_dry_run_leaves_no_refusal_trail`。

- [x] **未**新增表或迁移（**纯代码任务**）；**未**新增 Python 侧实现（**R-164-A**）
      → **已核实**：未新增迁移；全仓 grep 无任何 Python 调用
      `broadcasts/submit` / `BroadcastSubmit` / `amountsRaw` ⇒ **BC-4 成立**。



#### b.2 行为验收（**六类负向 + 一条正向 · 逐条核对**）



| # | 场景 | 期望 | 判定 |

|---|---|---|---|

| **N-1** | `run.ok = false` | **不生成广播请求**；记录拒绝原因 | ☑ |

| **N-2** | `run.executable = false` | **不生成广播请求**；记录拒绝原因 | ☑ |

| **N-3** | `signed_pnl.amount = 0`（**测得零**） | **不生成广播请求**；**如实**记为业务结论（**不是**缺陷） | ☑ |

| **N-4** | `signed_pnl.amount < 0`（**测得负**） | **不生成广播请求**；**如实**记为业务结论 | ☑ |

| **N-5** | 利润**未测量**（`missing`） | **不生成广播请求**；**不得**当作零、**不得**当作不匹配 | ☑ |

| **N-6** | 复核年龄 **> 120s** | **不生成广播请求**（**不得**用 600s 观测阈值替代） | ☑ |

| **P-1** | 一条**已测量且 `> 0`** 且新鲜的复核 | **生成**广播请求；请求记录携带 `BC-5` **九个字段全部** | ☑ |

| **P-2** | 链 / 账户**缺失**；限额**越界**；不在时间窗口 | 各**拒绝发送** / **自动停止** / **本轮不发送**，**不因「默认放开」而放行** | ☑ |



> **不得**把「代码已写」写成「门槛已生效」—— **必须**有「**未满足 ⇒ 不生成广播请求**」的**负向证据**。

> **实测读数（2026-11-25）**：`M-7` 广播层 **32 passed / 0 failed** —— 上表八项**逐条**有对应测试，
> 且**全部**为**负向断言**（拒绝 ⇒ 具名规则 ⇒ 留痕）。
>
> **N-5 的实现细节**（本轮最关键的修正，详见 `docs/D-118` §4）：
> `signed_pnl_amount` 是 `REAL NOT NULL` ⇒ **不存在「金额为 NULL 的请求行」**；
> 且复核层**仅在** `pnl.amount > 0.0` 时才生成请求行 ⇒ 「未测量 ⇒ 无请求」是**复核层不变量**。
> ⇒ 广播层可达的「未测量」形状是**非有限数**（`±Infinity`）——`NaN` 会被 SQLite 转成 `NULL` 而**违反 NOT NULL**。
> 因此 `measured_amount()` 把**非有限数**判为「从未测量」，并用**独立规则** `BC-1.pnl_measured` 拒绝，
> **不与** `BC-1.pnl_positive`（测得零 / 测得负）**混同**。



#### b.3 边界验收（**不得**发生）



- [x] **未**广播（本项**不产生**任何真实交易）
      → **已核实**：本任务只调用广播层的**提交判定**，**未**触发任何链上发送；
      测试全部使用**临时库**（`tempfile` 目录）与**假**签名账户。

- [x] **未**改动 `runArbitrage` 的 `ok` / `executable` 判定
      → **已核实**：复核层只**读取** `run_arbitrage_json` 的 `ok` / `executable` 字段并**原样转交**；
      判定逻辑全部在广播层 `admission.rs`。复核层**未改**任何 `runArbitrage` 判定。

- [x] **未**放宽任何门禁；**未**新增任何**放宽性**口径
      → **已核实**：十二条规则**全部 fail-closed**（未测量的值读作「未证明」）；
      **缺护栏 ⇒ 拒绝**；**金额未测量 ⇒ 拒绝**；**窗口未配置 ⇒ 拒绝**。

- [x] **未**改动方向池的授权维度（`DIRECTIONAL_POOL_MANAGER_AUTHORITY`）或池子重构的写库授权路径（`BC-4`）
      → **已核实**：本任务**未**触碰 `directional_pool_manager`；生产库
      `flashloan/db/local_store_46630.sqlite3` **只读、未写**（**未申请授权、未使用授权**）。

- [x] **未**用历史数据回填掩盖当前计算缺陷
      → **已核实**：N-5 的「未测量」形状是用 `UPDATE ... SET signed_pnl_amount = f64::INFINITY`
      **在测试临时库内**构造的，**未**触碰任何生产数据，**未**做回填。



#### b.4 回归验收



- [x] 架构守卫全量通过（基线 **74 passed**（2026-11-24 实测），新增后**只增不减**）
      → **2026-11-25 实测：80 passed / 0 failed** ⇒ **只增不减（74 → 80）**，**无回归**。
      命令：`pytest app_runtime/tests/unit/test_architecture_guards.py -q`

- [x] Rust `rs-src` **全绿**
      → **2026-11-25 实测：220 passed / 0 failed**（基线 203 ⇒ **只增不减**）。
      命令：`cd flashloan/rs-src; cargo test --offline`

- [x] Rust `tools` **0 failed**、`cargo build --offline --workspace` **exit 0**
      → **2026-11-25 实测**：`cargo build --offline --workspace` **exit 0**；
      受影响 crate 逐个全绿：`broadcast-manager` **32**、`review-manager` **158**、`manager-api` **33**，**0 failed**。

- [ ] Python 全量 **全绿**（基线见 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5：第 84 轮 **2743 passed / 3 skipped**）
      → **未跑**（**如实标注为未测量**）：按 `SCOPE_GUARD`「未改动模块不重跑」，
      本任务**未改动任何 Python 文件**（`git status` 中无本任务的 Python 改动）⇒ 全量 Python 回归**不在本任务范围内**。
      架构守卫（`app_runtime/tests/unit/test_architecture_guards.py`）**已跑**，**80 passed**。
      ⇒ 全量 Python 读数由**改动 Python 的任务**（如 `T-候.01` / `T-候编储.01`）承担，**不在此重复消耗**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/README.md` -->

# `T-复广.02`｜广播准入门槛与审计留痕

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-复广.02`（纯 ID）　**验收**：`AC-复广.02`　**优先级**：P0　**波次**：W-3
**编号逻辑**：`T-复广.02` / `AC-复广.02` —— **跨模块（组合索引）**，编号索引 **`复广`**（改动模块：`M-6`（复核层（复核发布 / 金额利润）） ＋ `M-7`（广播层（广播准入门槛 / 请求生成 / 回执））），该索引号段内第 `02` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-7` 、`M-6` 、`M-12` 、`rs-src`（后两者为**强制牵连**，理由见 `SCOPE_GUARD.md` §1）

**前置模块**：—　　**需授权**：否（纯代码＋dry-run）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**13** 项已闭合 ＋ **1** 项如实标注「未测量」） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

> **状态（2026-11-25）**：`BC-1..BC-5` **已全部落地并实跑**；
> `BC-3` 拒绝留痕 ＋ `BC-5` 九字段审计均有**逐条负向证据**。
> 详见 `docs/D-118`。

## 独立回归

```powershell
cd flashloan/tools
cargo test --offline -p broadcast-manager
cargo test --offline -p review-manager
cargo test --offline -p manager-api

cd flashloan/rs-src   # 独立工作区
cargo test --offline
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d062"></a>

### `D-062-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/docs/D-062-现状与可行性分析.md` -->

# D-062 01 当前现状与可行性分析

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

**结论：内存侧的缺口不是“缺一个方向字段”，而是“池对象与方向是分离的两样东西”。**因此修复方式也不是加字段，而是把方向装进池对象、让平行数组消失。完整设计与逐环节核对见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)。

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

**处理意见：** 这条决策把「方向是池的属性还是路由的属性」彻底定死（C-1 的根因），并使「闭环表由 3 个方向兑换池子组成」成为可断言的结构而不是描述。展开见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)。

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

<a id="d118"></a>

### `D-118-广播准入判据与接口层接线.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/docs/D-118-广播准入判据与接口层接线.md` -->

# D-118 广播准入判据与接口层接线（BC-1..BC-5 实现读数）

> **本文件是 `T-复广.02` 自己的技术文案。**
> 只写本任务相关的内容；引用别的任务**给链接**，不复制正文。
> 口径与 `../D-059`、`../D-061` **不得矛盾**。

---

## 1. 结论摘要

| 项 | 结论 |
|---|---|
| `BC-1` 五谓词 | **已落地为单一可审计判据** —— `flashloan/tools/broadcast_manager/src/admission.rs`（**新增**，580 行） |
| `BC-2` 四护栏 | **已落地为运行时配置** —— 缺失即拒绝；超限即拒；不在窗口即本轮不发送 |
| `BC-3` 拒绝留痕 | **已落地** —— 拒绝写入 `manager_broadcast_attempt_diagnostics`，事务外独立连接 |
| `BC-4` 无副作用 | **成立** —— 未新增表、未新增迁移、未新增 Python 侧实现 |
| `BC-5` 九字段审计 | **已落地** —— `BroadcastAuditRecord`，写入 attempt 的 `evidence_json["broadcastAudit"]`（**追加**，不替换调用方 evidence） |
| 本轮**强制扩范围** | `M-12`（接口层 `manager_api`）＋ `rs-src`（`runtime_core.rs`）—— 见 §5 |

---

## 2. `BC-1..BC-5` 现状缺口表（改造前）

| 编号 | 验收要求 | 改造前状态 | 缺口 |
|---|---|---|---|
| `BC-1` | 五条谓词落地为**单一可审计判据** | 判定**散落**：复核层发布请求时判 `pnl.amount > 0 && fresh && 3 个价格限`；广播层再手判 `request_status` 与 `pnl > 0` | 无统一入口；**新鲜度、run.ok、run.executable 在广播侧从未被复核** |
| `BC-2` | 链 / 账户 / 限额 / 窗口按运行时配置实现 | **完全不存在** —— 广播侧无护栏概念 | 链错、账户错、金额越界、时间外均可放行 |
| `BC-3` | 拒绝时**同样留痕** | 只留一条泛化错误字符串 | 事后无法回答「**为什么没发**」 |
| `BC-4` | 不新增表 / 迁移 / Python 实现 | 成立 | — |
| `BC-5` | 九个审计字段落库可查 | **完全不存在** | 事后无法回答「**凭什么发了这一条**」 |

---

## 3. 核心设计：判据放在哪一层（`DPO-10` 口径）

### 3.1 判据归广播层，事实归复核层

**分层口径**（与 `DPO-10`「Python 侧不具有处理数据的能力」同源）：

| 层 | 职责 | 不做什么 |
|---|---|---|
| **复核层（`M-6`）** | **测量** `runArbitrage`、产出 `signed_pnl`、打 `reviewed_at` 时间戳 | **不判**「能不能广播」 |
| **广播层（`M-7`）** | **读取**复核层已测得的事实、**判定**是否放行、**留痕** | **不重新测量** `runArbitrage` |

**为什么不把判据放回复核层**：复核层判「能否广播」时，它**拿不到**链 / 账户 / 限额 / 窗口这些**部署侧**运行时配置。
若强行让复核层判定 ⇒ 护栏要么硬编码进复核层（把部署配置写进数据层，违反分层），
要么让复核层去读部署配置（越界）。

⇒ **判据必须在广播层**；广播层**只读**复核层已发布的事实，**不重算**。

### 3.2 事实的传递方式：复用既有表，不新增表

`BC-4` 要求「未新增表或迁移」。因此事实**不新建表**，
而是**从既有 `manager_v4_reviews` 表读出来**，随既有请求行一起交给广播层。

新增结构 `BroadcastAdmissionFacts`（`review_manager/src/lib.rs`）：

| 字段 | 类型 | 含义 | 为何是 `Option` |
|---|---|---|---|
| `review_status` | `String` | 复核当时的状态 | 空串表示**没有复核行** |
| `run_ok` | `Option<bool>` | `runArbitrage` 的 `ok` | **缺字段 ≠ `false`** —— 未证明成功 |
| `run_executable` | `Option<bool>` | `runArbitrage` 的 `executable` | 同上 |
| `reviewed_at` | `String` | 测量时刻 | 空串表示**无时间戳** ⇒ 无新鲜度基准 |

`read_broadcast_requests()` 改用 **LEFT JOIN**：

```sql
FROM manager_v4_review_broadcast_requests AS r
LEFT JOIN manager_v4_reviews AS v
  ON v.network = r.network AND v.chain_id = r.chain_id
 AND v.protocol = r.protocol AND v.scope_key = r.scope_key
 AND v.loop_key  = r.loop_key
```

> **LEFT JOIN 是刻意的**：复核行缺失 ⇒ 事实为空 ⇒ **广播侧拒绝**。
> 若用 INNER JOIN，缺复核行会**静默消失**（变成「没有请求」而非「有一条被拒的请求」）⇒ 违反 `BC-3`。

---

## 4. N-5 缺陷根因链（本轮最关键的发现）

### 4.1 现象

`n5_unmeasured_profit_refuses_the_broadcast` 期望拒绝规则是 `BC-1.pnl_measured`，
实测却得到 `ReviewSnapshotTruncated`。

### 4.2 根因链

| # | 事实 | 出处 |
|---|---|---|
| 1 | `manager_v4_review_broadcast_requests.signed_pnl_amount` 是 **`REAL NOT NULL`** | 表结构 |
| 2 | ⇒ **不存在「金额为 NULL 的请求行」** | 由 1 推出 |
| 3 | 复核层**仅在** `signed_pnl.is_some()` 时才 INSERT 请求行，且条件是 `pnl.amount > 0.0 && fresh && price_limits.len() == 3` | `review_manager/src/lib.rs:1920-1926` |
| 4 | ⇒ **「未测量 ⇒ 不生成请求」是复核层不变量**，不是广播层该判的事 | 由 3 推出 |
| 5 | 该不变量**已被复核层既有测试覆盖** | `missing_or_diagnostic_only_pnl_is_data_incomplete`、`non_positive_completed_review_does_not_generate_broadcast_request`、`stale_positive_review_never_generates_automatic_broadcast_request` |
| 6 | SQLite 实测：`NaN` ⇒ 转成 `NULL` ⇒ **违反 `NOT NULL`**（**不可存储**）；`±Infinity` ⇒ **可往返存储** | 实测 |

### 4.3 结论：两种「无利润」必须走**不同**的拒绝理由

| 情形 | 存储形状 | 业务含义 | 拒绝规则 |
|---|---|---|---|
| **测得零** | `0.0` | **业务结论**：这笔不赚钱 | `BC-1.pnl_positive` |
| **测得负** | `-1.5` | **业务结论**：这笔亏钱 | `BC-1.pnl_positive` |
| **未测量** | `±Infinity` | **缺测量** —— 不是结论 | `BC-1.pnl_measured` |

⇒ 「未测量」**不得**折叠成「测得零」，也**不得**折叠成「不匹配」。
这正是 `checklist.md` `N-5` 的原文要求：「**不得**当作零、**不得**当作不匹配」。

### 4.4 修正：新增 `measured_amount`

`flashloan/tools/broadcast_manager/src/admission.rs`：

```rust
pub fn measured_amount(stored: f64) -> Option<f64> {
    if stored.is_finite() { Some(stored) } else { None }
}
```

**非有限数 ⇒ 从未测量。** 这是**唯一**能把「未测量」与「测得零」区分开的判据
（因为 `REAL NOT NULL` 不允许 `NULL`，而 `NaN` 根本存不进去）。

### 4.5 修正：`validate_signed_pnl` ⇒ `validate_signed_pnl_description`

改造前广播层有 `validate_signed_pnl()`，其中包含 `value.amount.is_finite()`。
**问题**：把「测量与否」折叠进了泛化的 `InvalidRequest` ⇒ **无具名规则、无 `BC-3` 留痕**。

改造后拆成两件事：

| 位置 | 校验内容 | 失败后果 |
|---|---|---|
| `validate_signed_pnl_description()`（**广播层**） | **仅**描述字段：`raw` / `currency` / `unit` / `source` 非空 | `InvalidRequest`（**畸形请求**，不是判决） |
| `admission::decide()`（**判据层**） | `amount` 是否**已测量** | `BC-1.pnl_measured`（**具名拒绝 ＋ `BC-3` 留痕**） |

> **分工原则**：**畸形**（malformed）走 `InvalidRequest`；**判决**（judgement）走 `admission` + 具名规则 + 留痕。

---

## 5. 强制扩范围（`SCOPE_GUARD` §4「先扩后跑」）

### 5.1 扩范围原因

`submit_broadcasts` / `BroadcastSubmitRequest` 是 **`manager_api` 的 public API**。
新增护栏参数 ⇒ 签名变更 ⇒ **调用方编译失败**（不是可选的）：

```
error[E0063]: missing fields `admission_now`, `attempt_amounts_raw` and `guard_policy`
              in initializer of `BroadcastSubmitRequest`
error[E0004]: non-exhaustive patterns: `BroadcastError::BroadcastRequestNotAdmitted { .. }` not covered
```

⇒ 按 `SCOPE_GUARD` §4 **先扩后跑**：先登记扩范围，再跑该模块测试。

### 5.2 扩范围落点

| 模块 | 文件 | 改动 |
|---|---|---|
| **`M-12`**（接口层） | `flashloan/tools/manager_api/src/lib.rs` | `AppState.broadcast_guard_policy`；`BroadcastSubmitHttpRequest.amounts_raw`；错误映射；测试夹具补 `manager_v4_reviews` 行 |
| **`M-12`**（接口层） | `flashloan/tools/manager_api/src/main.rs` | `broadcast_guard_policy_from_env()`（5 个环境变量） |
| **`rs-src`** | `flashloan/rs-src/src/runtime_core.rs` | `map_broadcast_command_error()` 对 `BroadcastError` 做**穷尽 match** ⇒ 新变体必须并入 409 分支（**1 行**） |

### 5.3 `manager_api` 接线契约

| 项 | 约定 |
|---|---|
| 护栏来源 | `AppState.broadcast_guard_policy`（**部署配置**），启动时读一次 |
| 缺护栏 | `None` ⇒ **广播层全拒**（**缺护栏 ≠ 护栏通过**） |
| 金额来源 | 请求体 `amountsRaw`（`BTreeMap<String, u128>`，**十进制字符串**） |
| 时刻来源 | `admission_now: None` ⇒ 广播层用当前 UTC（可复算性由广播层单测显式传参保证） |
| 拒绝映射 | `BroadcastError::BroadcastRequestNotAdmitted { .. }` ⇒ `ErrorCode::BroadcastRequestNotEligible`（**409**），details 带 `loopKey` / `reasons` / `reason` / `detail` |

**环境变量契约**（全部未设 ⇒ `Ok(None)` ⇒ 全拒）：

| 变量 | 类型 | 语义 |
|---|---|---|
| `BROADCAST_GUARD_CHAIN_ID` | `i64` | 本进程服务的链 |
| `BROADCAST_GUARD_SIGNER_ADDRESS` | `String` | 签名账户 |
| `BROADCAST_GUARD_MAX_AMOUNT_RAW` | `u128` | 单轮金额上限（锚代币原始单位） |
| `BROADCAST_GUARD_WINDOW_START` | RFC 3339 | 窗口开始 |
| `BROADCAST_GUARD_WINDOW_END` | RFC 3339 | 窗口结束 |

> **部分设置**（只设了一个）⇒ **不是** `None` ⇒ `Some(部分策略)` ⇒ 缺失的那几项各自**单独**拒绝。
> 这样「配错」不会被误读成「没配」。

### 5.4 接口层测试夹具的两处关键修正

| 修正 | 原因 |
|---|---|
| `seed_broadcast_review()` **新增** `manager_v4_reviews` 行（`review_status='completed'`、`run_arbitrage_json='{"ok":true,"executable":true}'`） | **LEFT JOIN** 下没有复核行 ⇒ 事实为空 ⇒ 广播侧**必拒** ⇒ 正例无法成立 |
| `reviewed_at` 用 **当前 UTC**（`chrono::Utc::now().to_rfc3339()`），**不用固定 fixture 日期** | 新鲜度窗口只有 **120 秒** ⇒ 固定日期必然过期 ⇒ 正例无法成立 |
| `legacy` 与 `manager` **两个** `AppState` **都**挂上 guard policy | 否则 legacy 场景会**先**被 `BC-2` 拒成 **400**，而不是被 authority 门禁拒成 **403** ⇒ 测试测的就不是它想测的 |

---

## 6. 判据本体（`admission.rs`）

### 6.1 十二条规则与稳定码

| 码 | 规则 | 判据 |
|---|---|---|
| `BC-1.evidence_complete` | 复核行存在 | `reviewed_at` 非空 |
| `BC-1.review_completed` | 复核已完成 | `review_status == "completed"` |
| `BC-1.run_ok` | `run.ok` 为真 | `facts.run_ok == Some(true)` |
| `BC-1.run_executable` | 路由可执行 | `facts.run_executable == Some(true)` |
| `BC-1.pnl_measured` | **已测量** | `measured_amount(...)` 返回 `Some` |
| `BC-1.pnl_positive` | **测得为正** | `Some(amount) && amount > 0.0` |
| `BC-1.review_fresh` | 新鲜 | 年龄 ≤ `120s`，未来偏移 ≤ `30s` |
| `BC-1.price_limits_complete` | 三跳价格限完整 | `len() == 3` 且每项非空 |
| `BC-2.chain_matches` | 链匹配 | `policy.chain_id == candidate.chain_id` |
| `BC-2.account_matches` | 账户匹配 | 忽略大小写比较；**畸形地址永不相等** |
| `BC-2.amount_within_limit` | 金额在限内 | **金额未测量 ⇒ 拒绝**（**不得**当作零） |
| `BC-2.within_window` | 在窗口内 | `start <= now <= end` |

### 6.2 三个设计要点

**（1）`decide()` 永不失败。**

不能放行的请求返回**带原因的** `AdmissionDecision`（`failed_rules` 非空），**不是** `Err`。
理由：`Err` 路径**不留痕** ⇒ 「它没发」就变成「系统里没有这件事」⇒ 违反 `BC-3`。

**（2）失败即闭（fail-closed）。**

未测量的值读作「**未证明**」，**永不**读作零、**永不**读作通过。
⇒ PnL 金额、`run_ok`、`run_executable`、`reviewed_at` **全部是可选的**。

**（3）每条规则都带自己的理由（通过时也带）。**

`AdmissionCheck.reason` **恒存在**，通过时也写「为什么成立」。
理由：「**凭什么发了这一条**」必须**只凭留痕**就能回答，不必回读代码。

### 6.3 窗口口径：120s 而不是 600s

| 常量 | 值 | 含义 |
|---|---|---|
| `MAX_BROADCAST_REVIEW_AGE_SECONDS` | **120** | 复核**可行动**的时长 |
| `MAX_BROADCAST_FUTURE_SKEW_SECONDS` | **30** | 时间戳允许的未来偏移 |

> **600s 是观测阈值**（一次测量「还有意思」多久），
> **120s 是可行动阈值**（一次测量「还能拿去下单」多久）。
> 用大的那个 ⇒ 一条**已经过期**的复核仍能授权一笔**实盘**交易。
> ⇒ `checklist.md` `N-6` 明确写：「**不得**用 600s 观测阈值替代」。

---

## 7. `BC-3` 拒绝留痕

| 项 | 事实 |
|---|---|
| 落点 | `manager_broadcast_attempt_diagnostics`（**既有表**） |
| 复用 | `diagnostic_type='evidence_incomplete'` ＋ `diagnostic_code='BC-1_BC-2_ADMISSION_REFUSED'`（**不新增迁移**） |
| 写入时机 | **事务外**、**独立连接** |
| dry-run | **不写**（dry run 的定义就是不落盘） |

**为什么必须事务外**：拒绝会让提交事务**回滚**。
若留痕写在**同一个事务**里 ⇒ 它跟着一起回滚 ⇒ 「被拒了」这件事**查不到**。
⇒ 拒绝发生时**先 drop 事务**，再用自己的连接写留痕。

```rust
Err(error) => {
    // The transaction is abandoned with the error, so the
    // `BC-3` trail has to be written **outside** it.
    // A refusal that is rolled back with its transaction is
    // not a refusal anybody can find.
    drop(transaction);
    record_refusal_trail(database_path, network, chain_id, &error)?;
    return Err(error);
}
```

**只对准入拒绝留痕**：`record_refusal_trail()` 先 `match` 错误类型，
非 `BroadcastRequestNotAdmitted` ⇒ 直接 `Ok(())` 返回 ⇒ 畸形请求**不**污染诊断表。

---

## 8. `BC-5` 九字段审计

`BroadcastAuditRecord`（`broadcast_manager/src/lib.rs`）：

| # | 字段 | 说明 |
|---|---|---|
| 1 | `review_identifier` | 复核标识（`operation:loop_key`） |
| 2 | `signed_pnl_amount` | 复核测得的金额（`Option` —— 测不到就是 `null`，**不静默省略**） |
| 3 | `review_age_seconds` | 判定时刻的复核年龄 |
| 4 | `idempotency_key` | 幂等键 |
| 5 | `chain_id` | 链标识 |
| 6 | `account_identifier` | 账户标识 |
| 7 | `limit_verdict` | 限额判据（**点名**决定的那条规则） |
| 8 | `broadcast_at` | 广播时间戳 |
| 9 | `outcome` | `admitted` / `refused` |
| — | `admission` | **完整准入轨迹**（每条规则的判据与理由） |
| — | `refusal_reason` | 拒绝时的理由（通过时为 `null`） |

**写入位置**：attempt 的 `evidence_json["broadcastAudit"]` —— **追加，不替换**调用方 evidence。

> **拒绝也写全字段。** `BroadcastAuditRecord::refused()` 与 `::admitted()` 字段完全一致，只有 `outcome` 与 `refusal_reason` 不同。
> 理由：「**它没发**」是系统的一个**事实**，不是**事实的缺席**。

---

## 9. 实测读数

| 范围 | 命令 | 读数 |
|---|---|---|
| `M-7` 广播层 | `cargo test --offline -p broadcast-manager` | **32 passed / 0 failed** |
| `M-6` 复核层 | `cargo test --offline -p review-manager` | **158 passed / 0 failed** |
| `M-12` 接口层 | `cargo test --offline -p manager-api` | **33 passed / 0 failed** |
| `rs-src` | `cargo test --offline`（`cd flashloan/rs-src`） | **220 passed / 0 failed** |
| 架构守卫 | `pytest app_runtime/tests/unit/test_architecture_guards.py -q` | **80 passed**（基线 **74**，**无回归**） |
| `tools` 工作区 | `cargo build --offline --workspace` | **exit 0** |
| `rs-src` 工作区 | `cargo build --offline` | **exit 0** |

**`BC-4` 侧证**：全仓 grep 无任何 Python 调用 `broadcasts/submit` / `BroadcastSubmit` / `amountsRaw`
⇒ **未新增 Python 侧实现**成立。

### 9.1 十二项验收 ↔ 测试对照

| 验收项 | 测试 |
|---|---|
| `N-1` `run.ok = false` | `n1_run_not_ok_refuses_the_broadcast` |
| `N-2` `run.executable = false` | `n2_run_not_executable_refuses_the_broadcast` |
| `N-3` 测得零 | `n3_measured_zero_profit_refuses_the_broadcast` |
| `N-4` 测得负 | `n4_measured_negative_profit_refuses_the_broadcast` |
| `N-5` **未测量** | `n5_unmeasured_profit_refuses_the_broadcast`（断言含 `BC-1.pnl_measured` 且**不含** `BC-1.pnl_positive`） |
| `N-6` 复核年龄 > 120s | `n6_stale_review_refuses_the_broadcast` |
| `P-1` 正例 + 九字段 | `p1_a_positive_fresh_review_carries_the_nine_audit_fields` |
| `P-2` 护栏四类 | `p2_a_missing_chain_refuses_the_broadcast`、`p2_a_foreign_account_refuses_the_broadcast`、`p2_an_amount_above_the_limit_refuses_the_broadcast`、`p2_a_request_outside_the_window_is_not_sent_this_round` |
| `BC-3` 留痕 | `bc3_a_refusal_is_recorded_with_its_reason`、`a_dry_run_leaves_no_refusal_trail` |
| 边界 | `a_request_without_a_review_row_is_refused`、`a_missing_run_verdict_is_refused`、`an_unmeasured_amount_is_refused`、`a_deployment_without_guardrails_is_refused`、`a_review_just_inside_the_window_is_admitted` |

---

## 10. 未做 / 未测（诚实边界）

| 项 | 状态 |
|---|---|
| **真实广播** | **未发生** —— 本任务**不产生**任何真实交易 |
| 生产库写入 | **未触碰**（`flashloan/db/local_store_46630.sqlite3` 只读未写） |
| Python 全量（2743 passed 基线） | **未跑** —— 按 `SCOPE_GUARD`「未改动模块不重跑」；本任务**未改动任何 Python 文件** |
| `tools` 工作区**全量**测试 | **未跑** —— 同上；只跑了 §9 登记的三个 crate |
| 授权 | **未申请、未使用** —— 本任务**只写测试临时库** |

---

## 11. 编号与模块归属的如实登记

本任务**实际改动**的模块集合 = **`M-6` ＋ `M-7` ＋ `M-12` ＋ `rs-src`**。

- `M-12`（接口层 `flashloan/tools/manager_api/`）此前在模块表中**没有归属** ⇒ 本轮**建档**（索引 `接`）。
  没有归属 ⇒ 下次还会踩同样的坑。
- `rs-src` 仍**无模块归属** —— 与 `T-复编.01` 的先例一致：**如实登记为缺口，本任务不扩**其模块号。
- **任务号保持 `T-复广.02` 不变** —— 编号是**纯 ID**，不表达功能关联；改名会牵连全部文档引用。
  实际改动模块集合在 `D-059` §1.1 与 `D-001-TASK_REGISTRY.md` §4.2.1 **如实登记**。

---

## 12. 交叉引用

| 文档 | 内容 |
|---|---|
| [`../D-059`](#d059) | 开发计划 ＋ 回归范围 |
| [`../D-060`](#d060) | 修改范围守护 ＋ 逐模块回归命令 |
| [`../D-061`](#d061) | 逐条验收框 ＋ 实测读数 |
| [`./D-062`](#d062) | 本任务相关技术文案 |
| [`../../../../D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) | 编号册（编号唯一真相源） |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复广.02/docs/README.md` -->

# `T-复广.02`｜广播准入门槛与审计留痕 · 技术文案（docs）

> **本目录是 `T-复广.02` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

BC-1 五条谓词的单一可审计判据与 BC-5 审计留痕

## 文件

| 文件 | 内容 |
|---|---|
| [`D-062`](#d062) | 本任务相关的技术文案 |
| [`D-118`](#d118) | BC-1..BC-5 实现读数 · N-5 根因链 · 接口层接线契约 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
