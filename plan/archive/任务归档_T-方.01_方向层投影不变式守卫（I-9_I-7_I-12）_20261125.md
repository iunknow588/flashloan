# `T-方.01`｜方向层投影不变式守卫（I-9 / I-7 / I-12） · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 方向层投影不变式守卫（I-9 / I-7 / I-12） |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d122"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/D-122-PLAN.md` -->

# D-122 `T-方.01`｜方向层投影不变式守卫（I-9 / I-7 / I-12） · 开发计划（**独立 · 自包含**）

> **本目录是 T-方.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-方.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> 编号是纯 ID：`T-方.01` / `AC-方.01` **不表达任何功能关联**。
> 范围与顺序只按模块编号（`M-n`）表达。

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方.01`** |
| **本任务验收编号** | **`AC-方.01`**（与任务编号**同号段**） |
| **编号作用域** | **模块内编号** |
| **编号索引** | **`方`**（`M-3`（方向层（方向池物化 / 评分 / 准入））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个索引**：本任务**只改动一个模块**（`M-3` 方向层）⇒ 按 §1.2 规则 1，
索引 = 该模块索引 = **`方`**。

> **`方` 号段此前为空** —— 历史上 `M-3` 只出现在组合索引里
> （`方储` / `方授储` / `方闭` / `方闭储`），没有**只**属于 `M-3` 的任务号段。
> ⇒ 本任务占用 `T-方.01`，下一个可用 **`T-方.02`**。

**规则**（复述自 `plan/D-001-TASK_REGISTRY.md` §1.2）：

1. **只改一个模块** ⇒ 索引 = 该模块索引；
2. **改多个平级模块** ⇒ 索引 = **各模块索引按模块号升序拼接**；
3. **模块索引表**见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1；
4. **不同索引的序号互不消耗**；
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-3` | 方向层（方向池物化 / 评分 / 准入） | `方` |

**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方.01`** |
| **验收编号（纯 ID）** | **`AC-方.01`** |
| **标题** | 方向层投影不变式守卫（I-9 / I-7 / I-12） |
| **优先级** | P1 |
| **波次（模块级）** | W-0（与 `M-3` 同波；**不阻断**任何其它任务） |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（只改 Rust 代码 ＋ 文档 ＋ 只读审计工具；**实盘库零写入**） |
| **未完成验收项** | **1**（运行期生效复测，待重建 ＋ 重启） |
| **已闭合验收项** | **15** |

### 1.1 改动模块（**这一列就是 T-方.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |
| `M-3` | 方向层（**公开结构体的调用方**） | `cargo test --offline -p manager-api` |

```powershell
# T-方.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-api
```

> **为什么加跑 `manager-api`**：本任务给 `DirectionalPoolComparison` **新增了字段**
> ⇒ 这是一个**跨 crate 的公开接口改动** ⇒ 按 §2.1 纪律 2，必须加跑调用方。
> `manager-api` 是**唯一**序列化该结构体的 crate（`/v1/directional-pools/compare`）。

### 1.3 本任务要解决什么（**缺口是实测出来的，不是推断的**）

方向层是**基础池的投影**：一个基础 V4 池 ⇒ **两个**方向行（方向 `0` / `1`），
方向行携带的**池自身事实**全部**复制自**基础池行。

系统的**停止条件**里写着三条（见 `D-011` §4）：

| 编号 | 停止条件 | 本任务前的检测器 |
|---|---|---|
| **I-9** | 方向行的 base 字段与基础池行**不一致** | **无** |
| **I-7** | `directional_pool_id` **无法解析**到唯一方向行 | **无**（只在复核层交叉校验跳与池） |
| **I-12** | `hop_direction` 与方向行 `direction` **不一致** | **无**（同上） |

**实测到的缺口**（`flashloan/tools/directional_pool_manager/src/lib.rs::compare_directional_pools`）：

```text
它把同一个基础池的**两个方向行**互相比较
（pool_manager / pool_key_json / token0 / token1 / token0_key_id / token1_key_id
  / fee / tick_spacing / hooks / pair_key_id），
**从不与基础池表**（scope 的 pool_table）比较。
```

⇒ **两个方向行可以一起过期，而比较依然是绿的**：
基础池改了 `fee`、改了 `token` 序、换了 `hooks`，只要方向层没有重物化，
这两行仍然彼此相等 ⇒ **陈旧的事实会被端到端地服务下去**。

> **而 `base_pool_count` 早就在被读**（同一函数里）—— 只是**只用来报数，从不参与判断**。

### 1.4 本任务的边界（**本任务做什么、不做什么**）

| 做 | 不做 |
|---|---|
| 把方向行与**基础池行**逐列比对（I-9 的检测器） | 不改方向层的**投影规则**（动态费率 / 带 hook 的池仍被排除） |
| 把 `I-7` / `I-12` 做成**可复算的只读审计** | 不改闭环层 / 复核层的任何逻辑（`M-4` / `M-6`） |
| 把「未测量 ≠ 零」做成可复算的审计 | 不动主库 schema、迁移、读路径签名（`M-10`） |
| 把比较函数**纳入测试**（此前**零测试**） | 不重启实盘运行时（属运维窗口动作，见 §1.5） |
| 让 `ready_for_cutover` **只加严** | 不放宽任何门禁、阈值、容量口径、利润口径、幂等键 |

### 1.5 与「运行期重启」的关系（**诚实边界**）

本任务的 Rust 改动要进入实盘，须**重建二进制并重启运行时**。

**本轮刻意不重启**，理由与 `T-闭.01` 相同且更强：

| 项 | 事实 |
|---|---|
| 并行进程 | `T-复广.02`（pid **29140**）**仍在途**改动 `broadcast_manager` / `review_manager` / `manager_api` |
| 实盘运行时的依赖 | `flashloan/rs-src`（PyO3 扩展）**依赖** `broadcast-manager` ＋ `review-manager` ＋ `closed-loop-manager` ⇒ 重建即把这些在途改动一起带上线 |
| 当前实盘二进制 | `flashloan/tools/target/release/manager-api.exe` 的 mtime = **2026-11-24 08:06** ⇒ 早于本任务与 `T-闭.01` 的全部改动 |

⇒ **运行期生效复测**如实登记为**剩 1 项**（`AC-方.01` 的最后一项），**不代任何项盖章**。

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
      > **读数（2026-11-25）**：`D-124` 共 **16** 项，**16 / 16** 已勾；
      > 最后一项「运行期生效复测」的读数见 `D-125` §9 与 `D-124` 该项下的引文块。
- [x] §1.2 的**全部**回归命令**全绿**
      > **读数（2026-11-25）**：`cargo test --offline -p directional-pool-manager` ⇒ **25 passed / 0 failed**；
      > `cargo test --offline -p manager-api` ⇒ **33 passed / 0 failed**；
      > 架构守卫 `-k "directional or projection"` ⇒ **7 passed**。
      > 收尾轮**改动文件集合为空**（只补文档），又经工作区回归：
      > `pool-manager 165` / `candidate-edge 14` / `candidate-exchange 7` / `manager-db 5` /
      > `rs-src 220` / `review-manager 158` / 架构守卫 **80** / 受影响 7 文件 **77** /
      > 生产库零写守卫 **14** ⇒ **全绿**。
- [x] 每条已勾选项都有**可复算的实测读数**
      > **读数**：结构 / 行为 / 边界三组共 15 项读数在 `D-125` §2–§4；运行期读数在 `D-125` §9。
      > **无**「未测量」被写成 `0` 的情形。
- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
      > **读数（2026-11-25 实盘复测）**：重启后审计 `verdict.passed = true` / `failureCount = 0`；
      > `ready_for_cutover` **只加严**（新增两个计数进入判定），投影规则**一字未改**。
- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）
      > **读数（2026-11-25）**：本任务产品代码改动 = `directional_pool_manager/src/lib.rs` ＋
      > `directional_pool_manager/tests/directional_pools.rs`（**2 个文件**，见 `D-125` §4）；
      > 收尾轮**未改任何产品代码**。

---

## 4. 归档

闭合后必须移出 `plan/tasks/` —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-方.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-方.01 --dry-run
```

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d123"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/D-123-SCOPE_GUARD.md` -->

# D-123 `T-方.01`｜修改范围守护（Scope Guard）

> **本文件是本任务的「不可越界」凭据。**
> 范围规则见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §2–§4。

---

## 1. 允许改动的文件（**白名单**）

| 文件 | 改动内容 |
|---|---|
| `flashloan/tools/directional_pool_manager/src/lib.rs` | `DirectionalPoolComparison` **新增** 4 个字段 ＋ `compare_directional_pools` **新增**基础池比对 ＋ 新增 `base_fact_differences` 辅助函数 ＋ 新增常量 `BASE_POOL_MISMATCH_SAMPLE_LIMIT` |
| `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | 新增 **3** 个回归用例（新鲜投影 / 陈旧投影 / 孤儿投影） ＋ 导入 `compare_directional_pools` |
| `plan/runtime_evidence/tools/audit_directional_projection_20261125.py` | **新增**只读审计工具（离线取证，**不在运行时包内**） |
| `plan/tasks/P-01-池子重构/tasks/T-方.01/**` | 本任务的工作单元 |
| `plan/runtime_evidence/reports/directional_projection_audit_20261125.{json,md}` | 审计产物 |
| `plan/D-001-TASK_REGISTRY.md` / `plan/tasks/P-01-池子重构/docs/D-014-…` / `plan/tasks/P-01-池子重构/README.md` / `plan/runtime_evidence/README.md` | **台账同步**（编号册是唯一真相源 ⇒ 必改） |

## 2. 禁止触碰的文件（**黑名单**）

| 路径 | 理由 |
|---|---|
| `flashloan/tools/broadcast_manager/**` | `M-7`；**并行进程 `T-复广.02` 在途改动** ⇒ 碰它就是冲突 |
| `flashloan/tools/review_manager/**` | `M-6`；同上 |
| `flashloan/tools/composite_manager/**` | `M-8`；同上（编排层在途） |
| `flashloan/tools/manager_api/**` | `M-8`；同上（**在途**）—— 本任务**只**依赖它的序列化，不改它 |
| `flashloan/tools/closed_loop_manager/**` | `M-4`；`T-闭.01` 刚落地的代码，本任务不碰 |
| `flashloan/src/**`、`flashloan/app_runtime/**` | Python 运行时代码；**架构守卫要求 Python 不直查方向池表** ⇒ 本任务的审计工具**必须**待在 `plan/runtime_evidence/tools/`（守卫扫描范围之外） |
| `flashloan/tools/manager_db/migrations/**`、`schema_policy.json` | `M-10`；本任务**零 DDL** |
| `flashloan/chains/**/chain.json`、`.env`、`.env.example` | 链配置与运维配置；本任务**零配置改动** |

## 3. 越界了怎么办（**流程**）

1. **先停手** —— 不把越界的改动留在工作区里；
2. **先扩后跑**：确需触碰 ⇒ 先改 `D-123` §1 白名单 **＋** `D-122` §1.1 改动模块表 **＋** 任务编号（组合索引可能变）；
3. **再跑**：按扩后的模块集合**重跑回归**，并在 `D-001` §7 **迁移记录**里写明扩范围的原因与读数；
4. **不得**以「顺手改一下」跳过本流程。

## 4. 本轮的实际改动面（**对账**）

| 项 | 值 |
|---|---|
| 改动的**产品代码**文件 | **2**（`directional_pool_manager/src/lib.rs`、`directional_pool_manager/tests/directional_pools.rs`） |
| 改动的**运行时模块** | **仅 `M-3`** ⇒ **未扩范围** |
| 改动的**Python 运行时代码** | **0**（新增工具在 `plan/runtime_evidence/tools/`，守卫扫描范围之外） |
| 改动的**主库 schema / 迁移** | **0** |
| 改动的**链配置 / 环境变量** | **0** |
| 实盘库**写入** | **0**（全部读数 `mode=ro` ＋ `PRAGMA query_only = ON`） |

## 5. 「只加严」的具体落点（**未放宽任何门禁**）

| 位置 | 改动 | 方向 |
|---|---|---|
| `DirectionalPoolComparison::ready_for_cutover` | 新增 `base_pool_mismatches == 0` ＋ `rows_without_base_pool == 0` 两个必要条件 | **只加严**（`false` 仍是 `false`，`true` 可能变 `false`） |
| 投影规则（`is_dynamic_fee_pool` / `hooks_is_absent` / `token_order_is_canonical`） | **一字未改** | 不变 |
| 容量 / 利润 / 阈值 / 幂等键 / 回执关联 | **一字未改** | 不变 |

> `ready_for_cutover` 是**诊断位**，不是**门禁**：实盘读数（改动前后）均为 `false`
> （`T-闭.01` 的 `D-116` §3.4 已记「改动前已是 `false`」）⇒ 本次加严**不会**翻转实盘读数。
> 它**不被**任何广播/复核判定引用（唯一消费点是 `manager-api` 的比对端点与页面展示）。

---

## ④ 测试目标 check list

<a id="d124"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/D-124-checklist.md` -->

# D-124 `T-方.01`｜方向层投影不变式守卫 · 测试目标 check list

**验收前置**：无（`M-3` 方向层已落地：`T-方授储.01` 已闭合）。

> **勾选纪律**：**每一条都必须有可复算的实测读数**；未实测项写「**未测量**」，**不得**用零冒充。
> 全部读数的出处见 [`docs/D-125`](#d125)。

---

#### 结构验收

- [x] **缺口已定位**：`compare_directional_pools` 只把**同一基础池的两个方向行**互相比较，

      **从不与基础池表**（scope 的 `pool_table`）比较 ⇒ **停止条件 I-9 没有任何检测器**

      （`base_pool_count` 早就在被读，但**只用来报数，从不参与判断**）。

- [x] **比较函数已有测试**（此前 `compare_directional_pools` **零测试**）

- [x] `DirectionalPoolComparison` 已**新增**四个字段（**增量** ⇒ 既有字段语义不变）：

      `base_pool_mismatches` ／ `rows_without_base_pool` ／ `projected_base_pool_count` ／
      `base_pool_mismatch_samples`

- [x] `ready_for_cutover` 已纳入两个新计数（**只加严**，见 `D-123` §5）

- [x] 回归范围已按「跨 crate 公开接口改动 ⇒ 加跑调用方」执行：

      `cargo test --offline -p directional-pool-manager` ⇒ **25 passed / 0 failed**（原 **22** ＋ 新 **3**）；
      `cargo test --offline -p manager-api` ⇒ **33 passed / 0 failed**

#### 行为验收

- [x] **新鲜投影**比对干净：`base_pool_mismatches = 0` ／ `rows_without_base_pool = 0` ／

      `projected_base_pool_count = 1` ／ `directional_pool_count = 2` ／ `ready_for_cutover = true`

- [x] **陈旧投影被检出**（I-9）：物化之后改动基础池行的 `fee` ⇒ `base_pool_mismatches = 2`

      （两个方向行都带着这份陈旧的 `fee`），样本**点名** `differing_columns = ["fee"]` ＋ `base_pool_id`；
      `ready_for_cutover = false`

- [x] **同一时刻兄弟比对仍为绿**（`projection_mismatches = 0`、`direction_mismatches = 0`）

      ⇒ **证明缺口为真**：旧比较**看不见**这件事

- [x] **孤儿投影被检出**：基础池行被删而方向层仍在 ⇒ `rows_without_base_pool = 2` ＋

      `base_pool_mismatches = 0` ＋ `ready_for_cutover = false`

- [x] **实盘只读审计全绿**（工具 `audit_directional_projection_20261125.py`，报告

      `directional_projection_audit_20261125.{json,md}`）：**零失败项** ——

      | 检查 | `core4` | `candidate_exchange` |
      |---|---:|---:|
      | 基础池 / 方向行 / 已投影基础池 | 488 / 340 / 170 | 494 / 352 / 176 |
      | **I-9** 与基础池不一致 | **0** | **0** |
      | **I-9** 无基础池行 | **0** | **0** |
      | 方向语义错 | **0** | **0** |
      | 序位重复组 / 缺口组 | **0 / 0** | **0 / 0** |
      | 身份重复 | **0** | **0** |
      | 容量 / 输出「未测量≠零」标记错 | **0 / 0** | **0 / 0** |
      | 投影了动态费率池 / 带 hook 池 | **0 / 0** | **0 / 0** |
      | **I-7** 跳的方向池无法解析 | **0**（跳 **522**） | — |
      | **I-12** 跳的方向与方向行不一致 | **0**（跳 **522**） | — |
      | 闭环体与跳序列不一致 | **0** | — |

#### 边界验收（**不得**发生）

- [x] **实盘库零写入**：审计全程 `mode=ro` ＋ `PRAGMA query_only = ON` ⇒ 写入 **0**

- [x] **未放宽任何门禁**：投影规则（动态费率 / 带 hook / token 序）**一字未改**；

      容量 / 利润 / 阈值 / 幂等键 / 回执关联**一字未改**；`ready_for_cutover` **只加严**

- [x] **未**触碰 `M-6` / `M-7` / `M-8` / Python 运行时代码 / `.env` / `chain.json` /

      主库 schema 与迁移（**产品代码改动仅 2 个文件**，见 `D-123` §4）

- [x] **未**在同一批次内删除旧口径：`recommend_direction` / `pool_score` **仍在**

      （下线是 `T-方储.01` 阶段 2 的活，本任务**只**加检测器）

- [x] **固定费率 · 无 hook 约定未被突破**：审计实测**已投影的池**

      `dynamic_fee = 0` 且 `hooks` 为零地址 ⇒ 与方向层的既有排除规则一致

#### 运行期生效（**2026-11-25 已闭合**）

- [x] **运行期生效复测**：守卫随 `manager-api` / `rs-src` **重建并重启**后，

      对实盘库生效

      > **读数（2026-11-25 · `D-125` §9）**：
      > - **阻塞已解除**：`T-复广.02` 已提交（`c9b4bfb0`）⇒ `broadcast-manager` / `review-manager` /
      >   `manager_api` 的改动**已进回归**（工作区回归全绿）⇒ 可以安全重启。
      > - **重建 ＋ 重启**：`manager-api.exe` mtime **2026-11-25 07:56:27**、
      >   `flashloan_rs.dll` mtime **07:57:52**、实盘运行时 pid **29032** **08:01:47** 启动
      >   ⇒ **二进制 mtime 晚于本任务全部改动** ⇒ 守卫**确实进了运行期**。
      > - **重启后只读审计**（`mode=ro` ＋ `PRAGMA query_only = ON`，实盘库零写入）：
      >   `verdict = {passed: true, failureCount: 0, failures: []}`
      >   报告 `plan/runtime_evidence/reports/directional_projection_audit_after_restart_20261125.json`。
      > - **逐项**：`core4` / `candidate_exchange` 的 **I-9 不一致 = 0**、**无基础池行 = 0**、
      >   方向语义错 = 0、序位重复/缺口 = 0/0、身份重复 = 0、容量/输出标记错 = 0/0、
      >   投影动态费率池/带 hook 池 = 0/0；闭环引用 **跳 522**、无方向指针 = 0、
      >   无法解析方向池 id = 0、跳方向不一致 = 0、scope/rank 不一致 = 0/0、闭环体与跳序列不一致 = 0。
      > - **两次独立审计同数**：`after_restart`（08:27:19）与 `after_depth`（08:45:08）**均零失败项**。
      >
      > **诚实边界**：本项**只**证明守卫对实盘库生效；
      > **不**声称「旧排序可下线」（`T-方储.01` 阶段 2），**不**声称 I-7/I-12 的运行期检测归本任务（仍在复核层 `M-6`），
      > **不**代 `T-闭.01` 关闭其验收项。

---

> **诚实边界（2026-11-25 订正）**：本任务 **16** 项**全部闭合** ⇒ **已闭合 · 已归档**。
> （订正前登记为「**15** 项闭合 ＋ **1** 项待运行期重启」；运行期重启已完成，读数见上。）
> **本任务不代任何其它任务关闭验收项** ——
> `T-闭.01` 的「实盘生效复测」与 `T-方储.01` 阶段 2 的前置条件**各自独立闭合，本任务不代签**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/README.md` -->

# `T-方.01`｜方向层投影不变式守卫（I-9 / I-7 / I-12）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方.01`（纯 ID）　**验收**：`AC-方.01`　**优先级**：P1　**波次**：W-0
**编号逻辑**：`T-方.01` / `AC-方.01` —— **模块内编号**，编号索引 **`方`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入））），该索引号段内第 `01` 个。`方` 号段此前为空（历史上 `M-3` 只出现在组合索引里）。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-3`

**前置模块**：—　　**需授权**：否（只改 Rust 代码 ＋ 文档 ＋ 只读审计工具；**实盘库零写入**）

**状态**：**已闭合 · 已归档**（2026-11-25）—— **16** 项**全部闭合**；运行期重建 ＋ 重启后的只读审计**零失败项**

## 一句话

方向层是基础池的**投影**，但**没有任何代码在检查「方向行仍然是基础池行的忠实副本」** ——
旧比较只看**同一基础池的两个方向行彼此**，两个方向行**可以一起过期**而比较依然绿。
本任务补上 **I-9** 的检测器（并把 I-7 / I-12 做成可复算的只读审计）。

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**16** 项**全部闭合** · 运行期生效复测读数见 `docs/D-125` §9） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-api
```

> **只跑上面这些。** 未改动模块**不重跑**。
> `manager-api` 是 `DirectionalPoolComparison` 的**唯一**序列化调用方 ⇒
> 本任务新增了字段 ⇒ 按「跨 crate 公开接口改动」加跑。

## 只读审计（实盘库零写入）

```powershell
python plan/runtime_evidence/tools/audit_directional_projection_20261125.py `
  --database flashloan/db/local_store_4663.sqlite3 `
  --json-out plan/runtime_evidence/reports/directional_projection_audit_20261125.json `
  --md-out  plan/runtime_evidence/reports/directional_projection_audit_20261125.md
```

> 退出码 **0** ＝ 零失败项；**1** ＝ 有失败项（报告里逐条列名）。
> 工具**故意**放在 `plan/runtime_evidence/tools/` —— 架构守卫要求
> **Python 运行时代码不得直查方向池表**（`M-6` 守卫），本工具是**离线取证**工具。

## 诚实边界

本轮**刻意不重启**实盘运行时：并行进程 `T-复广.02`（pid **29140**）**仍在途**改动
`broadcast_manager` / `review_manager` / `manager_api`，而 `rs-src` 依赖这两个 crate ⇒
此刻重建会把**未经回归验证**的代码带上线。
⇒ 「运行期生效复测」**如实登记为剩 1 项**，**不代任何其它任务关闭验收项**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d125"></a>

### `D-125-方向层投影不变式守卫_实施与实测报告_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/docs/D-125-方向层投影不变式守卫_实施与实测报告_20261125.md` -->

# D-125 `T-方.01`｜方向层投影不变式守卫 · 实施与实测报告

**日期**：2026-11-25　**任务**：`T-方.01`（`M-3` 方向层）
**审计工具**：`plan/runtime_evidence/tools/audit_directional_projection_20261125.py`
**审计报告**：`plan/runtime_evidence/reports/directional_projection_audit_20261125.{json,md}`
**实盘库**：`flashloan/db/local_store_4663.sqlite3`（**零写入**：`mode=ro` ＋ `PRAGMA query_only = ON`）

---

## 1. 触发条件

用户对池子重构的原始要求里有一条贯穿始终：

> **「V4 池子信息只是方向池子的子对象，V4 池子 ＋ 方向值 ＝ 方向池子」**

这句话在**数据面**是成立的（§3 实测：与基础池不一致 **0** 行）；
但在**守卫面**有一个缺口：

**没有任何代码在检查「方向行仍然是基础池行的忠实副本」**。

`compare_directional_pools`（`directional_pool_manager/src/lib.rs`）把
**同一个基础池的两个方向行**互相比较 —— 两个方向行**可以一起过期**，而比较依然是绿的。

而 `D-011` §4「池子专项停止条件」里**明文写着**这一条：

> **方向行的 `base` 字段与基础池行不一致（I-9 违反）** ⇒ 停止广播并归档

⇒ **一条被写进停止条件的缺陷，没有检测器**。本任务补上它。

---

## 2. 缺口（**实测出来的，不是推断的**）

### 2.1 原比较做了什么

```text
compare_directional_pools(...)
  ├─ 按 base_pool_id 分组，每组取两行
  ├─ 逐列比较这两行：pool_manager / pool_key_json / token0 / token1 /
  │                    token0_key_id / token1_key_id / fee / tick_spacing /
  │                    hooks / pair_key_id
  ├─ 检查方向语义：direction 0 ⇒ 输入 token1、输出 token0、zero_for_one = false
  │                direction 1 ⇒ 输入 token0、输出 token1、zero_for_one = true
  └─ 汇总 pools_without_two_rows / direction_mismatches / projection_mismatches
```

### 2.2 原比较**没有**做什么

| 缺什么 | 后果 |
|---|---|
| **不与基础池表比较** | 基础池改了 `fee` / `token` 序 / `hooks`，方向层未重物化 ⇒ **陈旧事实被端到端服务**，比较仍绿 |
| **不检查基础池是否还在** | 基础池离开作用域宇宙而方向行还在 ⇒ 孤儿行不被发现 |
| **不报「哪些列不一致」** | 只有一个计数，**说不出是哪条事实过期了** |
| **没有测试** | `compare_directional_pools` 此前**零测试** ⇒ 改它没有任何回归网 |

> **注意**：`base_pool_count` 早就在同一函数里被读（用于报数）——
> **基础池表本来就在手边**，只是**从不参与判断**。

---

## 3. 实盘读数（**只读**）

### 3.1 投影完整性（逐作用域）

| 作用域 | 基础池表 | 基础池 | 方向行 | 已投影基础池 | 两行齐备 | 缺一方向 |
|---|---|---:|---:|---:|---:|---:|
| `core4` | `v4_exchange_pools_core4` | **488** | **340** | **170** | **0** | **0** |
| `candidate_exchange` | `v4_exchange_pools_candidate_exchange` | **494** | **352** | **176** | **0** | **0** |

⇒ **方向池 ＝ 基础池 × 2**（`346` 个基础池 ⇒ `692` 行）成立；
**未投影的 318 个**不是缺陷，是**深度判决与准入闸**的正确拒绝（见 §3.5）。

### 3.2 **I-9**：方向行 vs 基础池行（**逐列**）

| 作用域 | 与基础池不一致 | 无基础池行 | 样本 |
|---|---:|---:|---|
| `core4` | **0** | **0** | — |
| `candidate_exchange` | **0** | **0** | — |

比对列（**9 列**）：`pool_manager` ／ `pool_key_json` ／ `token0` ／ `token1` ／
`token0_key_id` ／ `token1_key_id` ／ `fee` ／ `tick_spacing` ／ `hooks`。

### 3.3 方向语义与身份

| 项 | `core4` | `candidate_exchange` |
|---|---:|---:|
| 方向语义错（输入/输出/zero_for_one 与方向不符） | **0** | **0** |
| `directional_pool_id` 重复 | **0** | **0** |
| `(pair_key_id, direction)` 组内 rank 重复的组 | **0** | **0** |
| `(pair_key_id, direction)` 组内 rank 缺口的组 | **0** | **0** |

### 3.4 「未测量 ≠ 零」

| 项 | `core4` | `candidate_exchange` |
|---|---:|---:|
| `input_capacity_raw` 为 NULL 但 `input_capacity_measured` 不等于 0 | **0** | **0** |
| `input_capacity_raw` 非 NULL 但 `input_capacity_measured` 不等于 1 | **0** | **0** |
| `directional_output` 为 NULL 但 `directional_output_measured` 不等于 0 | **0** | **0** |
| `directional_output` 非 NULL 但 `directional_output_measured` 不等于 1 | **0** | **0** |

### 3.5 固定费率 · 无 hook 约定（**未被突破**）

| 项 | `core4` | `candidate_exchange` |
|---|---:|---:|
| **已投影**的池里带**动态费率**（`dynamic_fee` 不等于 0） | **0** | **0** |
| **已投影**的池里**带 hook**（`hooks` 不等于零地址） | **0** | **0** |
| （参考）**已投影**的池里带**特殊费率**（`special_fee` 不等于 0） | **71** | **71** |

> **特殊费率不是缺陷**：它是**市场事实**（费率高于阈值），闭环层有权自行权衡；
> 被排除的是**动态费率**与**hook**（这两类池的费率/行为由 hook 在 swap 时决定，
> 链下算术**无法定价**）⇒ 与方向层既有的排除规则**一字不差**。

### 3.6 闭环引用（**I-7 / I-12**）

| 项 | 读数 |
|---|---:|
| 跳数 | **522** |
| 跳未指向方向池 | **0** |
| **方向池无法解析（I-7）** | **0** |
| **方向与方向行不一致（I-12）** | **0** |
| 作用域与方向行不一致 | **0** |
| 序位与方向行不一致 | **0** |
| 闭环体（`directional_pool_ids_json`）与跳序列不一致 | **0** |

⇒ **闭环管理器对方向池的引用是完整的、自洽的**。

### 3.7 总判定

```text
passed = true  ·  failureCount = 0  ·  failures = []
```

---

## 4. 实施（**唯一改动文件 2 个**）

### 4.1 `flashloan/tools/directional_pool_manager/src/lib.rs`

| 改动 | 内容 |
|---|---|
| **新增结构体** | `DirectionalPoolBaseDifference`（`directional_pool_id` / `base_pool_id` / `direction` / `differing_columns`） |
| **新增字段**（`DirectionalPoolComparison`） | `base_pool_mismatches` ／ `rows_without_base_pool` ／ `projected_base_pool_count` ／ `base_pool_mismatch_samples` |
| **新增比对** | `compare_directional_pools` 现在用 `read_scope_pools` 读该 scope 的**基础池表**，逐行比对 **9 列基础事实** |
| **新增函数** | `base_fact_differences(row, base) -> Vec<String>`（返回**不一致的列名**，让报告能点名） |
| **诊断位** | `ready_for_cutover` 新增两个必要条件（**只加严**） |
| **常量** | `BASE_POOL_MISMATCH_SAMPLE_LIMIT = 5` |

> **为什么复用 `read_scope_pools`**：它已经按 `network` / `chain_id` / `protocol`
> 过滤并读出了同一批基础事实 ⇒ **不新增读路径、不新增 SQL 分支**。

### 4.2 `flashloan/tools/directional_pool_manager/tests/directional_pools.rs`

| 用例 | 断言 |
|---|---|
| `a_fresh_projection_compares_clean_against_its_base_pools` | 新鲜投影 ⇒ 全部计数 **0** ＋ `ready_for_cutover = true` |
| `a_stale_projection_is_detected_against_the_base_pool_row` | 物化后改基础池 `fee` ⇒ `base_pool_mismatches = 2` ＋ 样本点名 `fee` ＋ `ready_for_cutover = false` ＋ **兄弟比对仍为 0** |
| `a_projection_whose_base_pool_is_gone_is_detected` | 删基础池行 ⇒ `rows_without_base_pool = 2` ＋ `ready_for_cutover = false` |

> **第二个用例是本任务的关键证据**：它**同时**断言
> 「新的基础池比对抓到了」**和**「旧的兄弟比对没抓到」
> ⇒ **证明这个缺口是真实的，不是假想**。

---

## 5. 回归（**按任务自身的回归范围**）

| 命令 | 读数 |
|---|---|
| `cargo test --offline -p directional-pool-manager` | **25 passed / 0 failed**（原 **22** ＋ 新 **3**） |
| `cargo test --offline -p manager-api` | **33 passed / 0 failed**（`DirectionalPoolComparison` 的**唯一**序列化调用方） |
| `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -k "directional or projection"` | **7 passed**（方向层守卫全绿） |

> **未跑全量** —— §2.1 纪律：只跑 §1.2 的模块集合。

---

## 6. 诚实边界（本文件**不**声称什么）

| 不声称 | 理由 |
|---|---|
| ~~「守卫已在实盘生效」~~ | **已于 2026-11-25 达成** ⇒ 见 **§9**；§6 的这一行**已被 §9 取代**（保留原样以示订正轨迹） |
| 「I-7 / I-12 现在由本任务守护」 | 那两条的**运行期**检测仍在**复核层**（`M-6` 的方向交叉校验）；本任务提供的是**离线可复算的审计** |
| 「旧排序可以下线了」 | 下线是 `T-方储.01` 阶段 2 的活，其 **5 条前置**本轮**一条未动** |
| 「实盘库的投影无风险」 | 本轮读数是**采集时刻**的实测值；实盘运行时仍在写库 ⇒ **读数会漂移**，审计工具就是为漂移而准备的 |

### 6.1 为何本轮**刻意不重启**

> **⚠️ 订正（2026-11-25）**：本小节记录的是**实施轮**（2026-11-25 05:53 之前）的状态。
> **阻塞已解除**：`T-复广.02` 已提交（`c9b4bfb0`），`broadcast-manager` / `review-manager` / `manager_api`
> 的改动**已进回归**（工作区回归全绿）⇒ 二进制得以重建并重启 ⇒ **§9**。
> 本小节**保留原样**，作为「当时为何不重启」的可追溯记录。

| 项 | 事实 |
|---|---|
| 并行进程 | `T-复广.02`（pid **29140**）**仍在途**改动 `broadcast_manager` / `review_manager` / `manager_api` |
| 依赖面 | `flashloan/rs-src`（PyO3 扩展，Python 运行时的 Rust 内核）**依赖** `broadcast-manager` ＋ `review-manager` ＋ `closed-loop-manager` |
| 后果 | 重建 ⇒ 把并行进程**未经回归验证**的改动一起带上线 |
| 决定 | **不重启**；运行期生效复测**如实登记为剩 1 项**（`AC-方.01` 最后一项），**不代任何项盖章** |

---

## 7. 与其它任务的关系（**只读指针，不代关闭**）

| 任务 | 关系 | 本任务**不**做什么 |
|---|---|---|
| `T-闭.01`（`M-4`） | 其「实盘生效复测」的**判据工具**（同一份审计脚本可复算） | **不**代它关闭那一项（仍待运行期重启） |
| `T-方储.01`（`M-3`＋`M-10`） | 阶段 2 前置「**审计口径同步**」的**一部分**已具备（方向层↔基础池的口径现在有检测器） | **不**代它满足其余 **4** 条前置；**不**执行任何删除 |
| `D-011` §4 停止条件 | 给 **I-9** 补上**检测器** | **不**改停止条件本身 |

---

## 8. 复跑方式

```powershell
# 只读审计（实盘库零写入）
python plan/runtime_evidence/tools/audit_directional_projection_20261125.py `
  --database flashloan/db/local_store_4663.sqlite3 `
  --json-out plan/runtime_evidence/reports/directional_projection_audit_20261125.json `
  --md-out  plan/runtime_evidence/reports/directional_projection_audit_20261125.md
# 退出码 0 ＝ 零失败项；1 ＝ 有失败项（报告里逐条列名）

# 回归
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-api
```

> 工具**故意**放在 `plan/runtime_evidence/tools/` 而不是 Python 运行包里：
> 架构守卫要求 **Python 运行时代码不得直查方向池表**（`M-6` 守卫），
> 而本工具是**离线取证**工具，**只读**、**不入运行时**。

---

## 9. 运行期生效复测（**2026-11-25** · 本节闭合 `D-124` 最后一项）

### 9.1 阻塞解除的**时间线**（**实测**）

| 时刻 | 事件 | 出处 |
|---|---|---|
| 2026-11-25 05:53 | 实施轮审计（**零失败项**） | `directional_projection_audit_20261125.json`（`passed: true` / `failureCount: 0`） |
| 2026-11-25 07:56:27 | `manager-api.exe` **重建** | 文件 mtime |
| 2026-11-25 07:57:52 | `flashloan_rs.dll`（release）**重建** | 文件 mtime |
| 2026-11-25 08:01:47 | 实盘运行时 pid **29032** **启动**（新二进制进运行期） | 进程表 |
| 2026-11-25 08:27:19 | **重启后**审计 ⇒ **零失败项** | `directional_projection_audit_after_restart_20261125.json` |
| 2026-11-25 08:45:08 | 深度刷新后**再**审计 ⇒ **零失败项** | `directional_projection_audit_after_depth_20261125.json` |

> **二进制 mtime 晚于本任务的全部源码改动** ⇒ 守卫**确实随重启进入了运行期**。
> 运行期 profile `cow_quote=false` / `cow_submit=false` ⇒ 无资金风险。

### 9.2 重启后审计读数（**逐项**）

工具：`plan/runtime_evidence/tools/audit_directional_projection_20261125.py`
（`mode=ro` ＋ `PRAGMA query_only = ON` ⇒ **实盘库零写入**）

报告：`plan/runtime_evidence/reports/directional_projection_audit_after_restart_20261125.json`

```json
"verdict": { "passed": true, "failureCount": 0, "failures": [] }
```

逐作用域读数（**重启后**）：

| 检查 | `core4` | `candidate_exchange` |
|---|---:|---:|
| 基础池 / 方向行 / 已投影基础池 | 488 / 340 / 170 | 494 / 352 / 176 |
| **I-9** 与基础池不一致 | **0** | **0** |
| **I-9** 无基础池行 | **0** | **0** |
| 方向语义错 | **0** | **0** |
| 序位重复组 / 缺口组 | **0 / 0** | **0 / 0** |
| 身份重复 | **0** | **0** |
| 容量 / 输出「未测量≠零」标记错 | **0 / 0** | **0 / 0** |
| 投影了动态费率池 / 带 hook 池 | **0 / 0** | **0 / 0** |

闭环引用（**I-7 / I-12**）：

| 检查 | 读数 |
|---|---:|
| 跳数 | **522** |
| 无方向指针的跳 | **0** |
| 无法解析的方向池 id | **0** |
| 跳的方向与方向行不一致 | **0** |
| 跳的 scope 不一致 / rank 不一致 | **0 / 0** |
| 闭环体与跳序列不一致 | **0** |

> **两次独立审计同数**：`after_restart`（08:27:19）与 `after_depth`（08:45:08）
> ⇒ **零失败项**不是一次性巧合，也不是「采集时刻恰好干净」。

### 9.3 关闭范围（**不越界**）

| 项 | 状态 |
|---|---|
| 「守卫随重建 ＋ 重启对实盘库生效」 | ✅ **已实测**（§9.1 ＋ §9.2） |
| `AC-方.01` 的 **16** 项验收 | ✅ **16 / 16 闭合** |
| 「旧排序可下线」 | ❌ **本任务不声称**（`T-方储.01` 阶段 2 的活，**5** 条前置本轮**一条未动**） |
| 「I-7 / I-12 的运行期检测归本任务」 | ❌ **不声称**（运行期检测仍在复核层 `M-6`） |
| 「`T-闭.01` 的实盘生效复测」 | ❌ **不代关闭**（那是 `T-闭.01` 自己的验收项，本文件只提供**可复算的判据工具**） |

> **本节闭合 `D-124` 的最后一项** ⇒ **`T-方.01` 的 16 项验收全部闭合**。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.01/docs/README.md` -->

# `T-方.01`｜文档索引

| 文档 | 内容 |
|---|---|
| [`D-125`](#d125) | 缺口定位（实测）· 实盘读数 · 实施明细 · 回归读数 · 诚实边界 |

## 本任务的读数出处

| 读数 | 出处 |
|---|---|
| 缺口（原比较不与基础池表比对） | `flashloan/tools/directional_pool_manager/src/lib.rs::compare_directional_pools`（改动前） |
| 实盘不变式读数（I-9 / I-7 / I-12 / 序位 / 未测量≠零 / 固定费率·无 hook） | `plan/runtime_evidence/reports/directional_projection_audit_20261125.{json,md}` |
| 回归读数（`directional-pool-manager` 25 / `manager-api` 33 / 架构守卫 7） | `D-125` §5 |
| 「为何不重启」 | `D-125` §6.1 ＋ `PLAN.md` §1.5 |

## 关联（**只读指针，本任务不代关闭任何一项**）

| 指针 | 说明 |
|---|---|
| `T-闭.01` 的「实盘生效复测」 | 本任务的审计脚本是该项的**判据工具**；该项**仍待运行期重启** |
| `T-方储.01` 阶段 2 前置「审计口径同步」 | 本任务补上方向层↔基础池的**检测器**；其余 **4** 条前置**未动** |
| `D-011` §4 停止条件 **I-9 / I-7 / I-12** | 本任务给 **I-9** 补上**运行期检测器**；**不改停止条件本身** |

---
