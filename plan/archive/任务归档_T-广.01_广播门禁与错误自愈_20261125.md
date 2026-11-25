# `T-广.01`｜广播门禁与错误自愈 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-广.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 广播门禁与错误自愈 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-广.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d068"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/D-068-PLAN.md` -->

# D-068 `T-广.01`｜广播门禁与错误自愈 · 开发计划（**独立 · 自包含**）

> **本目录是 T-广.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-广.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-广.01` / `AC-广.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-广.01`** |
| **本任务验收编号** | **`AC-广.01`**（与任务编号**同号段**） |
| **编号作用域** | **模块内（单模块索引）** |
| **编号索引** | **`广`**（`M-7`（广播层（广播准入门槛 / 请求生成 / 回执））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

本任务**只改动一个模块** `M-7`（广播层（广播准入门槛 / 请求生成 / 回执））⇒ 编号索引即该模块索引 **`广`**。



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
| `M-7` | 广播层（广播准入门槛 / 请求生成 / 回执） | `广` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-广.01`** |
| **验收编号（纯 ID）** | **`AC-广.01`** |
| **标题** | 广播门禁与错误自愈 |
| **优先级** | P2 |
| **波次（模块级）** | W-3 |
| **前置模块**（**不是**前置任务） | M-7 |
| **需授权** | 否（dry-run） |
| **未完成验收项** | **4** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-广.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-7` | 广播层 | `flashloan/tools/broadcast_manager/` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-7` | 广播层 | `cargo test --offline -p broadcast-manager` |

```powershell
# T-广.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p broadcast-manager
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

      > **读数（2026-11-25）**：`D-070` **4 / 4** 已勾选（详见该文件的逐项读数块）。

- [x] §1.2 的**全部**回归命令**全绿**

      > **读数（2026-11-25）**：`cargo test --offline -p broadcast-manager`
      > ⇒ **32 passed / 0 failed / 0 ignored**（`D-128` §7）。

- [x] 每条已勾选项都有**可复算的实测读数**

      > **读数（2026-11-25）**：全部读数落在 `docs/D-128`，
      > 报告 `plan/runtime_evidence/reports/t401_broadcast_refusal_paths_20261125.json`；
      > 复算工具：`read_fixture_broadcast_state_20261125.py` / `check_live_store_untouched_20261125.py`。

- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）

      > **读数（2026-11-25）**：本任务**未改一行产品代码**（门禁实现由 `T-复广.02` / 提交 `c9b4bfb0` 交付）；
      > 实测证明**门禁全部按原样生效**：正向对照的 **12** 条规则 `failedRules = []`、
      > 越限 / 错账户 / 缺价限 / 陈旧 / 缺利润**逐条被拒**；
      > 幂等键**同键不同 body 仍被拒**（`IDEMPOTENCY_KEY_REUSED`）⇒ **没有任何一条被放宽**。

- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）

      > **读数（2026-11-25）**：本任务**零产品代码改动** ⇒ 回归范围**就是** §1.2 的 `M-7` 单模块，
      > **未**跑全量；夹具跑在**独立的 fixture 库 + 独立的 `manager-api` 进程**（端口 8092）上，
      > **实盘库零写入**（`check_live_store_untouched_20261125.py` ⇒ `passed = true`）。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-广.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-广.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-09_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d069"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/D-069-SCOPE_GUARD.md` -->

# D-069 `T-广.01`｜广播门禁与错误自愈 · 修改范围守护（Scope Guard）

> **本文件是 `T-广.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-7` | 广播层 | ``flashloan/tools/broadcast_manager/`` | 是 |

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
| `M-7` | `flashloan/tools/broadcast_manager/src/lib.rs`<br>`flashloan/tools/broadcast_manager/src/runtime.rs` | `flashloan/tools/broadcast_manager/src/lib.rs`<br>↔ 与 T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/broadcast_manager/src/runtime.rs`<br>↔ 与 T-复广.01 / T-复广.02 同文件 |

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
| — | — | `cargo test --offline -p broadcast-manager` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p broadcast-manager
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

<a id="d070"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/D-070-checklist.md` -->

# D-070 `T-广.01`｜广播门禁与错误自愈 · 测试目标 check list

- [x] 核对 **链 / 账户 / nonce / gas / 目标合约 / 金额 / 快照 / 幂等键**逐项一致。

      > **读数（2026-11-25 · `D-128` §2）**：8 项里 **7 项逐项一致且有实测读数**
      > （链 `BC-2.chain_matches`、账户 `BC-2.account_matches`（正反两面）、`nonce = 42`、
      > `gas_limit = 500000` / `gas_price_wei = "1000000000"`、金额 `1000 ≤ 1000000`、
      > 快照 `closed_loop_version = 7` / `review_version = 9` **逐字相等**、
      > 幂等键落库且**同键不同 body ⇒ 409 `IDEMPOTENCY_KEY_REUSED`**）。
      > **目标合约**：**结构性不适用** —— `AttemptCreateInput` 与落库表的 **43 列**
      > **都没有**目标合约字段（目标合约由执行器在构造交易时决定，不在广播门禁口径内）
      > ⇒ 如实登记为「不适用」，**不代为编造**。

- [x] 四类拒绝路径（`profit<=0` / 利润或金额未知 / 状态陈旧 / 权限错配）**逐条真实触发并留证**。

      > **读数（2026-11-25 · `D-128` §3 + §5）**：**7 / 7** 用例经**真实 `manager-api` 二进制 + HTTP**
      > 触发，全部 **409 `BROADCAST_REQUEST_NOT_ELIGIBLE`** 且拒绝规则名**逐条相等**：
      > `BC-1.pnl_positive`（零利润 / 负利润）、`BC-1.pnl_measured`、`BC-1.price_limits_complete`、
      > `BC-1.review_fresh`、`BC-2.account_matches`、`BC-2.amount_within_limit`。
      > 留痕：**7 条** `BC-1_BC-2_ADMISSION_REFUSED`，`broadcasted = false` / `outcome = refused`。
      > 正向对照 **200** 放行，**12** 条规则全部 `satisfied = true`。
      > **实盘库零写入**（`check_live_store_untouched_20261125.py` ⇒ `passed = true`）。

- [x] **未**出现 txHash（dry-run 不广播）。

      > **读数（2026-11-25 · `D-128` §4）**：两条被放行的 attempt 落库 `tx_hash = null`、
      > `status = pending`；报告 `verdict.txHashesPresent = []`；
      > 库状态 `success_count = 0` / `failed_count = 0` / `terminal_count = 0`。

- [x] 失败时**不广播**。

      > **读数（2026-11-25 · `D-128` §5）**：7 条 `BC-3` 留痕 `payload.broadcasted` **全为 `false`**、
      > `outcome = refused`；`manager_broadcast_library_state` 的 `success_count` / `terminal_count` 均为 **0**。
      > 幂等键侧：同键不同 body ⇒ **409 `IDEMPOTENCY_KEY_REUSED`**（*already used for different request content*）。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/README.md` -->

# `T-广.01`｜广播门禁与错误自愈

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-广.01`（纯 ID）　**验收**：`AC-广.01`　**优先级**：P2　**波次**：W-3
**编号逻辑**：`T-广.01` / `AC-广.01` —— **模块内（单模块索引）**，编号索引 **`广`**（改动模块：`M-7`（广播层（广播准入门槛 / 请求生成 / 回执））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-7`

**前置模块**：M-7　　**需授权**：否（dry-run）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**4** 项**全部闭合** · 2026-11-25 四类拒绝路径实测留证完成） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 闭合状态（2026-11-25）

**4 / 4 项验收全部闭合**，证据落在
[`docs/D-128`](#d128)：

- 四类拒绝路径 **7 / 7** 用例经**真实 `manager-api` 二进制 + HTTP** 触发，全部 **409** 且规则名逐条相等；
- **未**出现 txHash（两条 attempt `tx_hash = null` / `status = pending`）；
- 失败时**不广播**（7 条 `BC-3` 留痕 `broadcasted = false`）；
- **实盘库零写入**（夹具跑在独立库 + 独立进程，端口 8092）。

回归：`cargo test --offline -p broadcast-manager` ⇒ **32 passed / 0 failed**。

## 独立回归

```powershell
cargo test --offline -p broadcast-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d071"></a>

### `D-071-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/docs/D-071-现状与可行性分析.md` -->

# D-071 01 当前现状与可行性分析

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

<a id="d128"></a>

### `D-128-广播门禁四类拒绝路径实测与逐项一致性核对_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/docs/D-128-广播门禁四类拒绝路径实测与逐项一致性核对_20261125.md` -->

# D-128 `T-广.01`｜广播门禁四类拒绝路径实测与逐项一致性核对

> **日期**：2026-11-25　**任务**：`T-广.01`（`M-7` 广播层）
> **报告**：`plan/runtime_evidence/reports/t401_broadcast_refusal_paths_20261125.json`
> **工具**：`build_broadcast_admission_fixture_20261125.py` / `drive_broadcast_admission_paths_20261125.py` /
> `read_fixture_broadcast_state_20261125.py` / `check_live_store_untouched_20261125.py`
> **夹具库**：`plan/runtime_evidence/broadcast_admission_fixture_20261125.sqlite3`（**独立文件**，已 `.gitignore` 排除）
> **实盘库**：`flashloan/db/local_store_4663.sqlite3` —— **零写入**（见 §6）
> **实现来源**：广播门禁的**代码**由 `T-复广.02` 交付（提交 `c9b4bfb0`，`BC-1..BC-5` 准入判据与审计留痕）；
> `T-广.01` 是**它的验收任务** —— 4 个验收项全部是「核对 / 逐条真实触发并留证」类，**本任务不改产品代码**。

---

## 1. 为什么必须用**夹具库**，不能用实盘库

`D-070` 要求四类拒绝路径「**逐条真实触发并留证**」。
拒绝路径的**留证**就是写一行 `BC-3` 诊断（`manager_broadcast_attempt_diagnostics`）——
不写库就没有「证」。⇒ 触发拒绝**必然**要写库。

而实盘库的广播侧**本来就是空的**：
至今没有任何复核走到「`completed` ＋ 正利润 ＋ 可执行」，
所以实盘端点对任何请求只能答 `UnknownRequest`
⇒ **实盘库上无法构造出「四类拒绝路径各一条」**。

⇒ 本轮的做法是**另建一个库**，而不是改实盘库：

| 项 | 做法 |
|---|---|
| schema | 用**真实的** `manager-db migrate` 建（`manager-db.exe migrate --db …`）⇒ **45 张表 = 生产 schema** |
| 服务 | **真实的** `manager-api` 二进制，**独立进程**，绑 `MANAGER_DB_PATH` = 夹具库，端口 **8092**（实盘运行时在 8091，两者不共库） |
| 入口 | 走**出货**端点 `POST /v1/broadcasts/submit`（不是内部函数调用）⇒ 拒绝判定走的是**出货代码** |
| 链上 | `rpcUrl = https://rpc.example.invalid`（不可解析）⇒ **不可能发出真实交易** |
| 运行时 profile | `cow_quote=false` / `cow_submit=false` ⇒ 即便库里有正利润，也不会真的发单 |

---

## 2. 逐项一致性核对（`D-070` 第 1 项）

核对对象 = 落库的 `manager_cow_execution_attempts` 行（`read_fixture_broadcast_state_20261125.py` 的输出）。

| # | 字段 | 提交值 | 判据（准入规则） | 实测结果 |
|---:|---|---|---|---|
| 1 | **链** | `chainId = 4663` | `BC-2.chain_matches` | ✅ `satisfied = true`（规则原文：*the request is on chain 4663, which the policy governs*） |
| 2 | **账户** | `0xaaaa…aaaa`（策略账户） | `BC-2.account_matches` | ✅ `satisfied = true`（*the attempt names the configured account*） |
| 2′ | 账户（反向） | `0xbbbb…bbbb`（非策略账户） | `BC-2.account_matches` | ✅ **被拒**（409 / `BROADCAST_REQUEST_NOT_ELIGIBLE`）—— 同一规则的两面都成立 |
| 3 | **nonce** | `42` | 无独立规则（`validate_gas_field` 之外的整型校验） | ✅ 落库 `nonce = 42`（与提交值逐字相等） |
| 4 | **gas** | `gasLimit = 500000` / `gasPriceWei = 1000000000` | `validate_gas_field` / `validate_wei`（`broadcast_manager/src/lib.rs:1693-1694`） | ✅ 落库 `gas_limit = 500000`、`gas_price_wei = "1000000000"`（逐字相等）；`max_fee_per_gas_wei` / `max_priority_fee_per_gas_wei` = `null`（提交未给，**不补默认值**） |
| 5 | **目标合约** | — | — | ⚠️ **结构性不适用（如实登记，不代为编造）**：`AttemptCreateInput`（`broadcast_manager/src/lib.rs:325-339`）**没有**目标合约字段；落库表 `manager_cow_execution_attempts` 的 **43 列**里也**没有** `to` / `target_contract`。目标合约由**执行器**（cow executor / router）在构造交易时决定，**不在广播门禁的判定口径内** ⇒ 本项在 `M-7` **无对应字段可核** |
| 6 | **金额** | `amountsRaw["fixture:positive-control"] = 1000`；策略上限 `1000000` | `BC-2.amount_within_limit` | ✅ `satisfied = true`（*the amount 1000 is within the limit 1000000*）；越限用例 `10¹²` → **被拒** |
| 7 | **快照** | `expected_upstream`：`closed_loop_version = 7` / `sha256:closed-loop`；`review_version = 9` / `sha256:review` | `BC-1.evidence_complete`（*the review row and its evidence are present*） | ✅ 落库 `closed_loop_version / closed_loop_checksum / review_version / review_checksum` **逐字相等**；HTTP 响应回显 `reviewChecksum` / `closedLoopChecksum` / `materializedChecksum`（`sha256:8548761b…`）⇒ **请求带的快照 = 落库的快照 = 物化后的快照** |
| 8 | **幂等键** | `Idempotency-Key: t401-apply-idem-positive-control` | 同键不同 body ⇒ `IDEMPOTENCY_KEY_REUSED` | ✅ 落库于 `evidence_json.broadcastAudit.idempotencyKey`；**同键 + 改 `nonce` / 改金额** ⇒ **409 `IDEMPOTENCY_KEY_REUSED`**（见 §5） |

**结论**：8 项里 **7 项逐项一致且有实测读数**；第 5 项（目标合约）**在广播层没有这个字段**，如实登记为**不适用**。

---

## 3. 四类拒绝路径逐条真实触发（`D-070` 第 2 项）

每个用例**提交两次**：`dryRun = true`（**只答不写**）与 `dryRun = false`（**留痕**）。

| # | 类别 | 用例 | 期望规则 | HTTP | 实际规则 | 判定 |
|---:|---|---|---|---:|---|---|
| 1 | `profit<=0` | `profit-zero` | `BC-1.pnl_positive` | **409** | `BC-1.pnl_positive` | ✅ |
| 2 | `profit<=0` | `profit-negative` | `BC-1.pnl_positive` | **409** | `BC-1.pnl_positive` | ✅ |
| 3 | 利润或金额未知 | `profit-unmeasured` | `BC-1.pnl_measured` | **409** | `BC-1.pnl_measured` | ✅ |
| 4 | 利润或金额未知 | `price-limits-missing` | `BC-1.price_limits_complete` | **409** | `BC-1.price_limits_complete` | ✅ |
| 5 | 状态陈旧 | `review-stale` | `BC-1.review_fresh` | **409** | `BC-1.review_fresh` | ✅ |
| 6 | 权限错配 | `guard-account-mismatch` | `BC-2.account_matches` | **409** | `BC-2.account_matches` | ✅ |
| 7 | 权限错配 | `guard-amount-over-limit` | `BC-2.amount_within_limit` | **409** | `BC-2.amount_within_limit` | ✅ |
| — | **正向对照** | `positive-control` | （全规则成立 ⇒ 放行） | **200** | `failedRules = []` | ✅ 12 条规则**全部** `satisfied = true` |

错误码统一为 `BROADCAST_REQUEST_NOT_ELIGIBLE`（`retryable = false`，`stage = broadcast.submit`），
响应 `error.details.reasons` 给出**未满足的规则名**。

**正向对照的 12 条规则逐条**：
`evidence_complete` / `review_completed` / `run_ok` / `run_executable` / `pnl_measured` / `pnl_positive` /
`review_fresh` / `price_limits_complete` / `chain_matches` / `account_matches` / `amount_within_limit` /
`within_window` —— **全部 `satisfied = true`**。

---

## 4. 未出现 txHash（`D-070` 第 3 项）

| 观测 | 读数 |
|---|---|
| 落库 attempt 行的 `tx_hash` | **两条都是 `null`** |
| 落库 attempt 行的 `status` | **两条都是 `pending`**（入队，不是已发） |
| 报告 `verdict.txHashesPresent` | **`[]`**（空数组） |
| 报告 `verdict.pendingAttempts` | `attempt-idem-probe`、`attempt-positive-control` |
| 库状态 `success_count` / `failed_count` / `terminal_count` | **0 / 0 / 0** |

⇒ **入队 ≠ 已发**：被放行的请求只到 `pending`，**没有任何交易哈希**。

---

## 5. 失败时不广播（`D-070` 第 4 项）

7 条 `BC-3` 留痕（`manager_broadcast_attempt_diagnostics`）**逐条**读数：

| 字段 | 读数 |
|---|---|
| `diagnostic_code` | `BC-1_BC-2_ADMISSION_REFUSED`（**7 条全部**） |
| `diagnostic_type` | `evidence_incomplete`（**7 条全部**） |
| `payload.broadcasted` | **`false`**（**7 条全部**） |
| `payload.outcome` | `refused`（**7 条全部**） |
| `payload.unsatisfiedRules` | 与 §3 的期望规则**逐条相等** |

**幂等键的拒绝**（第二次同键、不同 body）：

| 项 | 读数 |
|---|---|
| 第一次 | `200`（放行，写回执） |
| 第二次 | **`409`** / `IDEMPOTENCY_KEY_REUSED` / *Idempotency-Key t401-idem-probe was already used for different request content* |

⇒ **失败路径全程零广播**：库状态 `success_count = 0`、`terminal_count = 0`，7 条留痕的 `broadcasted` 全为 `false`。

---

## 6. 实盘库零写入（复核）

`check_live_store_untouched_20261125.py`（`mode=ro` ＋ `PRAGMA query_only = ON`）读实盘库：

| 观测 | 读数 |
|---|---|
| `manager_cow_execution_attempts` 行数 | **0** |
| 其中 `loop_key LIKE 'fixture:%'` | **0** |
| `manager_broadcast_attempt_diagnostics` 行数 | **0** |
| `manager_broadcast_library_state` 行数 | **0** |
| `manager_v4_review_broadcast_requests` 中 `loop_key LIKE 'fixture:%'` | **0** |
| `rpc_url LIKE '%example.invalid%'` | **0** |
| `passed` | **`true`** |

⇒ 夹具跑的 7 条留痕与 2 条 attempt **全部**在夹具库里，实盘库**一行未动**。

---

## 7. 回归读数（本任务的独立回归范围）

`D-068` §1.2 规定 T-广.01 的回归范围 = `M-7` 一个模块：

```powershell
cargo test --offline -p broadcast-manager
```

**读数（2026-11-25）**：**32 passed / 0 failed / 0 ignored**（`running 0 tests` 的 doc-test 段同样 `ok`）。

---

## 8. 本文件的**诚实边界**

| 边界 | 说明 |
|---|---|
| 夹具库 ≠ 实盘库 | 拒绝路径是在**同一套 schema** 上、由**同一个出货二进制**拒绝的；但**实盘**此刻一条广播请求都没有 ⇒ 实盘端点今天仍然只会答 `UnknownRequest` |
| 「目标合约」无对应字段 | 见 §2 第 5 项 —— **不适用**，不是「已核对一致」 |
| 正向对照只到 `pending` | 证明的是「**准入**放行」，**不是**「交易上链」；上链那一段由执行器负责，本任务不覆盖 |
| 未覆盖的拒绝规则 | `BC-1.run_ok` / `BC-1.run_executable` / `BC-1.review_completed` / `BC-1.evidence_complete` / `BC-2.chain_matches`（错链）/ `BC-2.within_window`（窗口外）**未**在夹具里单独触发 —— 它们由 `broadcast-manager` 的单元测试覆盖（如 `p2_a_missing_chain_refuses_the_broadcast`、`p2_a_request_outside_the_window_is_not_sent_this_round`），**不在 `D-070` 的四类之内** |
| 广播库状态行数 | 夹具库 `manager_broadcast_library_state` = 1 行（`request_count = 8`、`attempt_count = 2`、`pending_count = 2`）⇒ 与「7 条拒绝 + 1 条放行」自洽 |

---

## 9. 闭合判定

`D-070` 的 **4** 项逐条对照：

| `D-070` 项 | 落点 | 结论 |
|---|---|---|
| 链 / 账户 / nonce / gas / 目标合约 / 金额 / 快照 / 幂等键 逐项一致 | §2 | ✅（目标合约**结构性不适用**，已登记） |
| 四类拒绝路径逐条真实触发并留证 | §3 + §5 | ✅ **7 / 7** 用例，全部 409 且规则名逐条相等 |
| 未出现 txHash（dry-run 不广播） | §4 | ✅ `txHashesPresent = []`，两条 attempt `tx_hash = null` / `status = pending` |
| 失败时不广播 | §5 | ✅ 7 条留痕 `broadcasted = false`；`success_count = 0` |

⇒ **`D-070` 4 / 4 闭合**；`D-068` §3 完成定义 **5 / 5** 满足（回归 32 passed、每条已勾项均有实测读数、
**未放宽任何门禁**、**未触碰 `M-7` 以外的模块**）。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-广.01/docs/README.md` -->

# `T-广.01`｜广播门禁与错误自愈 · 技术文案（docs）

> **本目录是 `T-广.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

广播门禁判定输出与错误自愈路径

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d071) | 本任务相关的技术文案 |
| [`D-128`](#d128) | **四类拒绝路径实测留证 ＋ 逐项一致性核对**（`D-070` 4/4 的落点） |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
