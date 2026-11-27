# `T-池复.01`｜单 tick 门禁的快照一致性判定 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-池复.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 单 tick 门禁的快照一致性判定 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-池复.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d132"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/D-132-PLAN.md` -->

# D-132 `T-池复.01`｜单 tick 门禁的快照一致性判定 · 开发计划（**独立 · 自包含**）

> **本目录是 T-池复.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-池复.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的模块先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-池复.01` / `AC-池复.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-池复.01`** |
| **本任务验收编号** | **`AC-池复.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`池复`**（`M-1`（池管理器（链上池状态 / 深度 / 边界））、`M-6`（复核层（复核发布 / 金额利润））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `无` |

**为什么是这个组合索引**：本任务**同时改动 2 个模块**（`M-1`、`M-6`）⇒ 按 §1.2 规则取这些模块的索引
**按模块号升序拼接**（`M-1` < `M-6`）⇒ 组合索引 **`池复`**。

**本任务涉及的每个模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 | 本任务在它里面改什么 |
|---|---|---|---|
| `M-1` | 池管理器（链上池状态 / 深度 / 边界） | `池` | **发布**快照身份（`materialization_batch`）给复核层 |
| `M-6` | 复核层（复核发布 / 金额利润） | `复` | **用**快照身份判定单 tick 门禁的「同一市场视图」 |

**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-池复.01`** |
| **验收编号（纯 ID）** | **`AC-池复.01`** |
| **标题** | 单 tick 门禁的快照一致性判定 |
| **改动模块（= 回归范围）** | `M-1` ＋ `M-6` |
| **模块级前置** | 无 |
| **需授权** | 否（**只读实盘库**；本任务**不改实盘库结构**、**不发单**） |
| **波次** | `W-2`（与 `T-复广.01` / `T-编.01` 同波） |

---

## 2. 问题陈述（**为什么必须做**）

实盘复核在 2026-11-25 的读数：**174** 条复核里 **`completed = 0`**、
`notExecutable = 154`、`dataIncomplete = 20` ⇒ **广播请求恒为 0 条**，**主网一次也广播不出去**。

逐条打开那 **6** 条**正利润**复核（`signedPnl.raw > 0`），
它们的 `reviewReason` **全部**是 `single_tick_gate_failed:*`：

| 拒绝原因 | 条数 | 性质 |
|---|---:|---|
| `single_tick_gate_failed:pool_state_version_drift` | **5** | **判定口径错误**（本任务） |
| `single_tick_gate_failed:final_input_exceeds_single_tick_capacity` | **1** | **真实容量约束**（**不是**缺陷，**不归本任务**） |

⇒ **正利润路线全部死在单 tick 门禁上**；而其中 **5** 条的死因是**判定口径错误**。

**根因**（`flashloan/tools/review_manager/src/single_tick.rs`）：
门禁把一条路线的**各跳**的 `pool_state_version` 拿来**逐字比较**，要求**全部相等**。
而这个 `pool_state_version` 喂进去的值是**每个池各自的 `liquidity_read_at`（读数时间戳）**。
池是**一个一个读的** ⇒ 同一批物化里的两个池**必然**差几毫秒
⇒ **任何有读数时间戳的多跳路线都必然被判「漂移」**。

**实测坐实**（见 `docs/D-135-…`）：被拦的 **5** 条路线，各跳的 `liquidity_read_at`
**3 个互不相同**（`distinct = 3`），但它们的 `materialization_batch`
**完全相同**（`pool-manager-000000000000000018eb230dce637ab0`）
⇒ **它们本来就出自同一次物化**，是**一次市场视图**，却被判成「两个市场」。

**正确口径**：Pool Manager 给**一次刷新运行的每一行**盖**同一个** `materialization_batch`
⇒ **「同批次」就是「同一市场视图」**；**「同读数时间戳」不是**。

---

## 3. 改动清单（**逐文件 · 逐点**）

| # | 文件 | 改动 | 模块 |
|---:|---|---|---|
| 1 | `flashloan/tools/pool_manager/src/parity_state.rs` | `PoolParityState` **新增** `materialization_batch` 字段；SELECT **增列**；表**没有该列**时降级为 `NULL`（老库不炸） | `M-1` |
| 2 | `flashloan/tools/review_manager/src/single_tick.rs` | `SingleTickHopState` **新增** `snapshot_batch` 字段；`evaluate_route_single_tick` 的漂移判定改为**「批次优先、时间戳兜底」** | `M-6` |
| 3 | `flashloan/tools/review_manager/src/lib.rs` | `evaluate_final_single_tick_gate` 把 `materialization_batch` 喂进 `snapshot_batch` | `M-6` |
| 4 | `flashloan/tools/review_manager/src/parity.rs`、`shadow.rs` | 跟随字段的构造点补齐（`to_parity_state` 填 `None`：复核期记录里没有批次） | `M-6` |
| 5 | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs` | 示例跟随字段 | `M-6` |

**没有做的事**（**明确写出来，免得被误读**）：

- **没有**放宽单 tick 门禁 —— 逐跳的 tick 区间判定、单跳容量判定、折叠容量判定**全部原样保留**；
- **没有**改金额、容量、利润、新鲜度（120s）、幂等、回执关联**任何**一个口径；
- **没有**动广播层（`M-7`）—— 本任务只让**复核**能走到 `completed`，
  广播层仍然按 `BC-1`..`BC-5` 自己的门禁判；
- **没有**动 Python 侧（架构守卫**零改动**）。

---

## 4. 回归范围（**只跑这一列**）

```powershell
# M-1
cargo test --offline -p pool-manager
# M-6
cargo test --offline -p review-manager
# 强制牵连：rs-src（生产复核发布链路的宿主，依赖上面两个 crate）
cd flashloan/rs-src; cargo test --offline
```

**不跑**：其余 crate、Python 全量（**本任务未改动任何 Python 文件**）。

---

## 5. 验收判据（**逐条对应 `checklist.md`**）

1. **根因坐实**：实盘 5 条漂移路线的 `liquidity_read_at` 互不相同、`materialization_batch` 相同；
2. **同批次放行**：同一批次的多跳路线 **不再**被判漂移（**新增单测钉住**）；
3. **跨批次仍拦**：两个批次的路线**仍然**被判漂移（**新增单测钉住**）；
4. **兜底不变**：没有批次信息时**仍然**走时间戳比较（**新增单测钉住**）；
5. **门禁未放宽**：逐跳容量 / tick 区间 / 折叠容量 / 状态迁移门禁**全部原样**；
6. **实盘复测**：重建 ＋ 重启后，复核层的漂移拒绝**归零**，`completed` **不再恒为 0**。

**读数与证据**：[`docs/D-135`](#d135)。

---

## 6. 回滚

代码回滚 =`git revert`（两个 crate 的改动都在本任务目录对应的提交里）。
**实盘侧无需回滚**：本任务**不写实盘库**、**不改 schema**、**不发单**。

---

## ③ 修改范围守护

<a id="d133"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/D-133-SCOPE_GUARD.md` -->

# D-133 `T-池复.01`｜单 tick 门禁的快照一致性判定 · 修改范围守护

> **本文件是 T-池复.01 的范围边界。** 越界 = 缺陷，不是「顺手」。

---

## 1. 允许改动的文件（**逐个列出**）

| 模块 | 文件 | 允许的改动 |
|---|---|---|
| `M-1` | `flashloan/tools/pool_manager/src/parity_state.rs` | `PoolParityState` **加一个字段** ＋ SELECT 增列 ＋ 构造点补齐 ＋ 新增单测 |
| `M-6` | `flashloan/tools/review_manager/src/single_tick.rs` | `SingleTickHopState` **加一个字段** ＋ 漂移判定改口径 ＋ 新增单测 |
| `M-6` | `flashloan/tools/review_manager/src/lib.rs` | `evaluate_final_single_tick_gate` **喂一个新字段** |
| `M-6` | `flashloan/tools/review_manager/src/parity.rs` | 构造点补齐（**不新增逻辑**） |
| `M-6` | `flashloan/tools/review_manager/src/shadow.rs` | 构造点补齐（**不新增逻辑**） |
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs` | 构造点补齐 |

**文档区**（不在上面的表里，但允许写）：
`plan/tasks/P-01-池子重构/tasks/T-池复.01/**` ＋ `plan/runtime_evidence/**` ＋
`plan/tasks/P-01-池子重构/docs/**` ＋ `plan/D-001-TASK_REGISTRY.md`。

---

## 2. 禁止触碰（**任何一条被碰 = 本任务失败**）

| # | 禁止项 | 为什么 |
|---:|---|---|
| 1 | **广播层 `flashloan/tools/broadcast_manager/**`** | 广播准入（`BC-1`..`BC-5`）是**另一个任务**的门禁；本任务只让复核**能走到** `completed`，**放不放行由广播层自己判** |
| 2 | **单 tick 门禁的**逐跳**判定**（`evaluate_hop` / 容量 / tick 区间） | 本任务改的是**「同一市场视图」判定**，**不是**「这条路线能不能走」判定。后者是**放宽门禁**，明令禁止 |
| 3 | **金额 / 容量 / 利润 / 新鲜度（120s）/ 幂等 / 回执关联**的任何口径 | 同上 |
| 4 | **Python 侧任何文件**（`flashloan/src/**`、`flashloan/app_runtime/**`） | 本任务**零** Python 改动；架构守卫应当**不需要**改 |
| 5 | **实盘库写入** | 分析一律 `mode=ro` ＋ `PRAGMA query_only = ON`；本任务**不建表、不改列、不删数据** |
| 6 | `flashloan/tools/{composite_manager,manager_api,candidate_edge_pool_manager,candidate_exchange_pool_manager,directional_pool_manager,closed_loop_manager,manager_db}/**` | 不在本任务模块集合内 |
| 7 | `flashloan/rs-src/src/evidence.rs`、各 `chain.json`、`.env` / `.env.example` | 他人任务的地盘 |
| 8 | `plan/runtime_evidence/tools/normalize_md_crlf_20260724.py`（及**任何**既有归一脚本） | 并行进程改过；本任务用**新**脚本 `normalize_crlf_20261125.py` |

---

## 3. 必须执行的命令（**回归范围 = 改动模块集合**）

```powershell
cargo test --offline -p pool-manager
cargo test --offline -p review-manager
cd flashloan/rs-src; cargo test --offline
```

**未改动模块不重跑。**

---

## 4. 执行纪律

1. **编译 ≠ 测试**：只改接口签名时 `cargo build` 即可暴露类型破坏；
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试
   ⇒ 本任务 `PoolParityState` 加字段 ⇒ `review-manager` ＋ `rs-src` **必须**跑；
3. **实盘只读**：所有对 `flashloan/db/local_store_4663.sqlite3` 的访问一律
   `file:...?mode=ro` ＋ `PRAGMA query_only = ON`；
4. **重启实盘运行时**（重建 DLL ＋ 重启）**必须**在两个 crate 的回归**全绿之后**，
   且**先停后建**（Windows 锁文件：不停止就构建会失败，且会把**旧**二进制重新拉起）。

---

## ④ 测试目标 check list

<a id="d134"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/D-134-checklist.md` -->

# D-134 `T-池复.01`｜单 tick 门禁的快照一致性判定 · 验收清单

> **本清单只登记本任务自己的验收项。** 不引用其它任务的验收内容。

---

## 1. 根因与定位

- [x] 实盘 **174** 条复核的 `reviewStatus` 分布已逐条读出

      > **读数（2026-11-25 10:09 +08 · `/v1/reviews`）**：
      > `not_executable|ok=false` **148**、`not_executable|ok=true` **6**、`data_incomplete` **20**
      > ⇒ `completed = 0` ⇒ **广播请求恒为 0**。

- [x] **6** 条 `ok=true`（正利润）复核的拒绝原因已逐条读出

      > **读数**：`single_tick_gate_failed:pool_state_version_drift` **5**、
      > `single_tick_gate_failed:final_input_exceeds_single_tick_capacity` **1**。

- [x] 漂移判定的**比较对象**已定位到**源码行**

      > **读数**：`review_manager/src/single_tick.rs:276-283`（旧）比较各跳的 `pool_state_version`；
      > `review_manager/src/lib.rs:1642` 把该值喂成 `liquidity_read_at`。

- [x] 实盘读数证明「同批次 ≠ 同时间戳」

      > **读数（`probe_single_tick_drift_pool_versions_20261125.py`）**：
      > **5** 条路线各跳的 `liquidity_read_at` **distinct = 3**（**逐条互不相同**），
      > 而 `materialization_batch` **5 条路线全部相同**
      > （`pool-manager-000000000000000018eb230dce637ab0`）。

- [x] 「同一批次 = 一次物化 = 一个市场视图」已由**写入侧**确认

      > **读数**：`pool_manager/src/lib.rs:2642` `materialization_batch_id()` —— 一次运行生成**一个** id，
      > 该运行的**每一行**都盖**同一个** id（`pool_manager/src/lib.rs:2790`）。

---

## 2. 实施

- [x] `PoolParityState` **发布**快照身份

      > **读数**：`PoolParityState` 新增 `materialization_batch`；
      > `read_pool_parity_states_on_connection` 按 `table_has_column` **增列**，
      > 老库**没有**该列时降级为 `NULL`（**不炸**）。

- [x] 单 tick 门禁的漂移判定改为**「批次优先、时间戳兜底」**

      > **读数**：`evaluate_route_single_tick` —— **全部**跳都带批次 ⇒ 比**批次**；
      > 否则回落比 `pool_state_version`（**旧口径一字未改**）。

- [x] **门禁未放宽**（逐跳 tick 区间 / 单跳容量 / 折叠容量 / 状态迁移门禁**一字未改**）

      > **读数**：`diff` 范围仅限 `PoolParityState` 字段、`SingleTickHopState` 字段、
      > `evaluate_route_single_tick` 的**比较表达式**、以及构造点的字段补齐。

---

## 3. 回归（**只跑改动模块 ＋ 强制牵连**）

- [x] `cargo test --offline -p pool-manager`

      > **读数（2026-11-25）**：**167 passed / 0 failed**（新增 **2** 条：批次被发布 / 老库降级）。

- [x] `cargo test --offline -p review-manager`

      > **读数（2026-11-25）**：**161 passed / 0 failed**（新增 **3** 条：同批次放行 / 跨批次拦截 / 无批次兜底）。

- [x] `rs-src`（生产复核发布链路的宿主，**强制牵连**）

      > **读数（2026-11-25）**：见 `D-135` §4。

---

## 4. 实盘复测（**读数以重启后为准**）

- [x] 实盘漂移拒绝**归零**

      > 判据：`/v1/reviews` 中 `reviewReason` **不再**出现
      > `single_tick_gate_failed:pool_state_version_drift`。
      >
      > **读数（2026-11-25 16:08 +08 · `review_version=116` · `plan/runtime_evidence/reports/remaining_closure_v116_20261125.json`）**：
      > `poolScope.driftRejections = 0`、`singleTickGateFailures = 0`、
      > `singleTickGateFailuresOtherThanDrift = 0`、`otherGateReasons = []`、
      > `driftExamples = []`。
      >
      > 逐条复核 `/v1/reviews?chainId=4663&limit=200`（174 条）：`reviewReason` 分布 =
      > `runArbitrage returned ok=false` **150**、`signed PnL is missing or incomplete` **21**、
      > `state_transition_gate_failed:expected_pool_state_unavailable:0x632d…` **2**、
      > `state_transition_gate_failed:expected_amount_crosses_tick:0x2410…` **1**。
      > ⇒ **漂移拒绝由 5 归零**，单 tick 门禁的**拒绝原因整体消失**；
      > 剩下的 3 条是**状态迁移门禁**（另一条门禁），**不是**漂移。
      >
      > **诚实边界**：漂移归零**不等于**「路线可执行」——`completedCount = 0` 仍为 **0**
      > （见下一项），原因见 §5 与 `remaining_closure_v116_20261125.json` 的
      > `reviewIntegrity.signedPnlMissing = 21` / `bestAmountMissing = 8`。

- [x] `completed` **不再恒为 0**

      > **读数（2026-11-26 14:3x）**：`manager_v4_reviews` ⇒ `review_status = completed` **1** 行
      > （`signed_pnl_raw = 0xc` ＝ **1.2e-05** USDG，`reviewed_at = 2026-11-26T05:56:17.867Z`）
      > ⇒ 判据「`completedCount > 0`」**成立**；概览 `summary.reviewCompletedCount = 1`。
      > **诚实边界**：这一条**正收益闭环**（`USDG -> PFE -> CRCL -> USDG`）发布于 **13:56:22**，
      > **在 runtime 换新二进制（13:59:28）之前** ⇒ 它的广播请求**没有人读回**，且已过 **120 s** 窗口
      > ⇒ **不会被重播**。这**不是**链路故障，是**时序**：换二进制之后要等**新一代**复核。
      > 探针：`verify_restart_gates_20261126.py` ＋ `probe_review_generation_20261126.py`。

- [x] 实盘库**零写入**

      > 判据：本任务**不写**实盘库；只读脚本一律 `mode=ro` ＋ `query_only`。
      >
      > **读数（2026-11-25 16:12 +08）**：
      > - 本任务的**唯一**实盘读取脚本
      >   `plan/runtime_evidence/tools/probe_single_tick_drift_pool_versions_20261125.py`
      >   **L57** `uri = f"file:{DB.as_posix()}?mode=ro"`、**L58** `sqlite3.connect(..., uri=True)`、
      >   **L60** `PRAGMA query_only = ON` ⇒ **只读**；
      > - 本轮复核用的两个探针同样只读：
      >   `probe_remaining_closure_20261125.py` 与 `probe_performance_baseline_20261125.py`
      >   ⇒ `openedReadOnly = true`、`queryOnly = true`、`writesByThisProbe = 0`；
      > - 物化计时由 **Rust** 侧 `materialization_timing` example 执行，`apply = false`
      >   且复合管理器在**非 apply** 分支以 `SQLITE_OPEN_READ_ONLY` 打开库
      >   （`flashloan/tools/composite_manager/src/main.rs:44`）；
      > - 本任务的**代码改动**只落在 `pool_manager` / `review_manager` 的**判定与字段**，
      >   **未新增任何写入路径**（见 §2 与 `D-133§ §1 的允许改动清单）。
      >
      > ⇒ 本任务**零实盘写入**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/README.md` -->

# `T-池复.01`｜单 tick 门禁的快照一致性判定

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-池复.01`（纯 ID）　**验收**：`AC-池复.01`　**优先级**：P0　**波次**：`W-2`
**编号逻辑**：`T-池复.01` / `AC-池复.01` —— **跨模块（组合索引）**，编号索引 **`池复`**
（改动模块：`M-1`（池管理器）＋ `M-6`（复核层）），该索引号段内第 `01` 个。（详见 `D-132` §1.0）

**改动模块（= 回归范围）**：`M-1` 、 `M-6`

**前置模块**：无　　**需授权**：否（**只读**实盘库，**零**写入）

## 文件

| 文件 | 内容 |
|---|---|
| `D-132` | 本任务的开发计划与回归范围（**自包含**） |
| `D-133` | 本任务的修改范围守护与逐模块回归命令 |
| `D-134` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（根因推导 / 实测读数） |

## 一句话

> 单 tick 门禁把「**各跳池的读数时间戳**」拿来逐字比较，
> 而池是**一个一个读的** ⇒ **任何多跳路线都必然被判「版本漂移」**。
> 正当口径是**物化批次**（一次物化 = 一个市场视图）。
> 实盘：**6** 条正利润复核里 **5** 条死在这条口径上 ⇒ **主网广播链整条是死的**。

## 独立回归

```powershell
cargo test --offline -p pool-manager
cargo test --offline -p review-manager
cd flashloan/rs-src; cargo test --offline
```

**读数（2026-11-25）**：**167** ＋ **161** passed，**0 failed**（详见 `D-134`）。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d135"></a>

### `D-135-单tick门禁版本漂移根因与实测_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/docs/D-135-单tick门禁版本漂移根因与实测_20261125.md` -->

# D-135 `T-池复.01`｜单 tick 门禁的「版本漂移」根因与实测

> **一句话**：单 tick 门禁把「**各跳池各自的读数时间戳**」拿来**逐字比较**，
> 而池是**一个一个读的** ⇒ **任何带读数时间戳的多跳路线都必然被判「版本漂移」**。
> 正当的「同一市场视图」证据是**物化批次**（一次物化 = 一个批次 = 一个市场视图）。
>
> **后果（实测）**：实盘 **6** 条**正利润**复核里 **5** 条死在这条口径上
> ⇒ `completed = 0` ⇒ **广播请求恒为 0** ⇒ **主网广播链整条是死的**。

---

## 1. 现象

**读数（2026-11-25 10:09 +08 · `GET /v1/reviews?chainId=4663&limit=500`）**：

| `reviewStatus` | `runArbitrage.ok` | 条数 |
|---|---|---:|
| `not_executable` | `false` | **148** |
| `not_executable` | `true` | **6** |
| `data_incomplete` | `false` | **20** |
| `completed` | — | **0** |

⇒ `completedCount = 0`、`broadcastRequestCount = 0`、
`manager_v4_review_broadcast_requests` **0 行** ⇒ **一条广播请求都产生不出来**。

**那 6 条 `ok = true` 的复核，正是系统里唯一 6 条正利润路线**：

| 拒绝原因（`reviewReason`） | 条数 | 性质 |
|---|---:|---|
| `single_tick_gate_failed:pool_state_version_drift` | **5** | **判定口径错误** |
| `single_tick_gate_failed:final_input_exceeds_single_tick_capacity` | **1** | **真实容量约束**（输入额 **2.019792 USDG** > 折叠单 tick 容量 **0.168316 USDG**） |

**采集器**：`plan/runtime_evidence/tools/probe_review_executable_20261125.py`（**只读 HTTP**，不碰库）。

---

## 2. 根因链（**逐段可查**）

### 2.1 门禁在比什么

`flashloan/tools/review_manager/src/single_tick.rs`（改动前）：

```rust
let versions: Vec<&str> = extras
    .iter()
    .filter_map(|extra| extra.pool_state_version.as_deref())
    .collect();
let version_drift = versions.windows(2).any(|pair| pair[0] != pair[1]);
```

注释写的是意图：**「一条路线的每一跳必须来自同一个 pool state version」**。

### 2.2 那个 "version" 实际是什么

`flashloan/tools/review_manager/src/lib.rs`（改动前）：

```rust
pool_state_version: state.and_then(|value| value.liquidity_read_at.clone()),
```

⇒ **不是**版本号，而是**这个池自己的流动性读数时间戳**。

### 2.3 为什么它必然不相等

`liquidity_read_at` 来自 `v4_exchange_pool_liquidity_snapshots`
（`flashloan/tools/pool_manager/src/parity_state.rs` 的 `read_pool_parity_states_on_connection`）。
池管理器**逐池**刷新深度，**每个池**写下**自己那一刻**的时间戳。

⇒ **同一批物化里的 N 个池，N 个互不相同的时间戳**（相差毫秒）。
⇒ **「时间戳逐字相等」这条判据对多跳路线恒为假**。

### 2.4 实盘坐实（**5 条被拦路线逐条**）

**采集器**：`plan/runtime_evidence/tools/probe_single_tick_drift_pool_versions_20261125.py`
（实盘库**只读**：`file:...?mode=ro` ＋ `PRAGMA query_only = ON`）。

| 路线 | 三跳的 `liquidity_read_at` | distinct | 三跳的 `materialization_batch` |
|---|---|---:|---|
| CRCL → USDG → PFE → CRCL | `00:44:04.000401` / `00:44:04.040169` / `00:44:16.516518` | **3** | **三者相同** |
| USDG → ETH → CRCL → USDG | `00:43:51.709185` / `00:43:51.686182` / `00:44:04.000401` | **3** | **三者相同** |
| META → SPY → USDG → META | `00:44:16.491868` / `00:44:27.179412` / `00:44:16.539799` | **3** | **三者相同** |
| USDG → PFE → CRCL → USDG | `00:44:04.040169` / `00:44:16.516518` / `00:44:04.000401` | **3** | **三者相同** |
| CRCL → USDG → ETH → CRCL | `00:44:04.000401` / `00:43:51.709185` / `00:43:51.686182` | **3** | **三者相同** |

**批次值**：`pool-manager-000000000000000018eb230dce637ab0`（**5 条路线全部相同**）。

**结论**：这 5 条路线**本来就出自同一次物化** ⇒ **一个市场视图**，
却被判成「两个市场」⇒ **被误拦**。

### 2.5 为什么「批次」才是对的证据

`flashloan/tools/pool_manager/src/lib.rs:2642`：

```rust
fn materialization_batch_id() -> String {
    // 运行开始时刻 + 随机后缀 ⇒ 两次运行绝不会撞号
    format!("pool-manager-{nanos:032x}")
}
```

该运行的**每一行**都盖**同一个** id（写入点 `:2790`）⇒
**「同批次」=「同一批测量」=「同一个市场视图」**。

**实测旁证**（实盘库只读）：

- **同时**存在 `NULL` 批次行与有批次行的池：**0** 个 ⇒ 批次有无**不歧义**；
- 实盘闭环的 **49** 个跳池：**全部**行都带批次，且**每个池只有 1 个**不同批次
  ⇒ 池的批次**唯一确定**。

---

## 3. 口径裁定

| 项 | 裁定 |
|---|---|
| **「同一市场视图」怎么判** | **批次**（主判据）→ **时间戳**（无批次时的兜底） |
| **有没有放宽门禁** | **没有**。逐跳 tick 区间、单跳容量、折叠容量、状态迁移门禁**一字未改**；本任务只改**「两个版本」这件事怎么比** |
| **为什么不直接删掉这条判据** | 它**有**意图（别拿两个市场的状态拼一条路线）。删掉 = 放弃这个保证；改成批次 = **保住**这个保证，且用**更强的**证据（同批次比同时间戳强得多） |
| **老库怎么办** | 表里**没有** `materialization_batch` 列 ⇒ 读成 `NULL` ⇒ **自动落回**旧的时间戳口径（**行为不变**，不炸） |
| **批次只有一部分跳有怎么办** | 只要**不是每一跳都有**批次 ⇒ **落回**时间戳口径（**fail-closed**，不因为「缺证据」而放行） |

---

## 4. 实施与回归读数

### 4.1 改动

| 文件 | 改动 |
|---|---|
| `flashloan/tools/pool_manager/src/parity_state.rs` | `PoolParityState` **新增** `materialization_batch`；SELECT 增列（用 `table_has_column` 判列，老库降级 `NULL`） |
| `flashloan/tools/review_manager/src/single_tick.rs` | `SingleTickHopState` **新增** `snapshot_batch`；漂移判定改为**批次优先、时间戳兜底** |
| `flashloan/tools/review_manager/src/lib.rs` | 把 `materialization_batch` 喂进 `snapshot_batch` |
| `flashloan/tools/review_manager/src/parity.rs` / `shadow.rs` / `examples/p0_6_single_tick_gate_probe.rs` | 构造点补齐 |

### 4.2 回归（**只跑改动模块 ＋ 强制牵连**）

| 命令 | 读数（2026-11-25） |
|---|---|
| `cargo test --offline -p pool-manager` | **167 passed / 0 failed**（新增 **2** 条） |
| `cargo test --offline -p review-manager` | **161 passed / 0 failed**（新增 **3** 条） |
| `cd flashloan/rs-src; cargo test --offline` | **220 passed / 0 failed**（与基线**同数**，**零新增零减少**） |

**新增单测**（**钉住三条语义**）：

- `a_route_priced_from_one_materialization_batch_is_not_drift` —— 同批次 ⇒ **放行**；
- `a_route_priced_from_two_materialization_batches_is_blocked` —— 跨批次 ⇒ **拦截**；
- `a_route_without_batch_information_falls_back_to_the_timestamps` —— 无批次 ⇒ **兜底拦截**；
- `the_materialization_batch_is_published_with_the_state` / `a_store_without_the_batch_column_still_publishes_the_other_fields`
  —— 批次**被发布** ＋ 老库**不炸**。

---

## 5. 实盘复测（**重启后**）

**操作顺序**（Windows 锁文件 ⇒ 必须先停后建）：

1. 停 **supervisor**（`supervise_runtime_t401_20261124.py`）⇒ 再停 **runtime**（`run.py --port 5000`）
   ⇒ 再停 **manager-api**（否则 supervisor 会把**旧**二进制重新拉起）；
2. `cargo build --offline --release`（`flashloan/rs-src` ⇒ `flashloan_rs.dll`）
   ＋ `cargo build --offline --release -p manager-api`（`flashloan/tools`）；
3. 按原脚本重启 **manager-api**（`start_mapi.ps1`）与 **supervisor**；
4. 等**一代**复核发布，再读 `/v1/reviews`。

**安全边界（实测前提）**：实盘部署**没有** `BROADCAST_GUARD_*` 环境变量
⇒ `broadcast_guard_policy_from_env()` 返回 `None`
⇒ 广播层**拒绝每一条提交**（`manager_api/src/main.rs:69-95`）
⇒ **本轮不可能发出任何真实交易**。

**读数**：见 `D-134` §4（**以重启后的实测为准**）。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-池复.01/docs/README.md` -->

# `T-池复.01`｜技术文案

| 文档 | 内容 |
|---|---|
| [`D-135`](#d135) | 根因链、实盘读数、改动口径、回归读数 |

---
