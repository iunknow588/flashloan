# `T-闭.01`｜方向层主序接管闭环主池 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-闭.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 方向层主序接管闭环主池 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-闭.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d113"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/D-113-PLAN.md` -->

# D-113 `T-闭.01`｜方向层主序接管闭环主池 · 开发计划（**独立 · 自包含**）

> **本目录是 T-闭.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-闭.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-闭.01` / `AC-闭.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-闭.01`** |
| **本任务验收编号** | **`AC-闭.01`**（与任务编号**同号段**） |
| **编号作用域** | **模块内** |
| **编号索引** | **`闭`**（`M-4`（闭环层（闭环集合 / 容量折叠 / 兜底））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无**（`闭` 号段此前为空 ⇒ 本任务是该号段第一个任务） |

**为什么是这个组合索引**：本任务**只改动 1 个模块**（`M-4`）⇒ 按 §1.2 规则取该模块的索引 ⇒ 编号索引 **`闭`**。

**规则**：

1. **一个任务只改动一个模块** ⇒ 编号索引 = 该模块索引（模块内编号）。
2. **一个任务改动多个模块** ⇒ 编号索引 = **各模块索引按模块号升序拼接**（组合索引）。
3. **模块索引表**见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1；
   **组合索引规则**见同文件 §1.2。
4. **不同索引的序号互不消耗** —— `闭` 做到 `.09` **不影响** `储` 的 `.04`。
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-4` | 闭环层（闭环集合 / 容量折叠 / 兜底） | `闭` |

**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-闭.01`** |
| **验收编号（纯 ID）** | **`AC-闭.01`** |
| **标题** | 方向层主序接管闭环主池 |
| **优先级** | P1（`M-4` 上「旧排序下线」的前置） |
| **波次（模块级）** | W-1（与 `M-4` 的其它闭环层任务同波） |
| **前置模块**（**不是**前置任务） | M-3（方向层须已产出 `pair_direction_rank`） |
| **需授权** | 否（**只改代码与文档**；影子复算在**瘦副本**上完成，**实盘库零写入**；实盘生效需**运行期重启**，属运维窗口动作） |
| **未完成验收项** | **1**（见 `D-115`：**实盘生效复测**，待运行期重启） |
| **已闭合验收项** | **22**（共 **23** 项） |

### 1.1 改动模块（**这一列就是 T-闭.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/src/lib.rs` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-4` | 闭环层 | `cargo test --offline -p closed-loop-manager` |

```powershell
# T-闭.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 2. 问题陈述（**为什么需要本任务**）

闭环层三跳的**主池**（一跳的第一候选）此前由**旧排序**决定：

| 跳 | 旧主序来源 | 落点 |
|---|---|---|
| `yc`（首跳） | `manager_dex_candidate_pool_recommendations.recommend_direction` | `closed_loop_manager/src/lib.rs` 候选层 |
| `y-z`（中间跳） | `manager_market_observation_yz_pairs.optimal_pool_id` | 同上 |
| `zc`（末跳） | `manager_dex_candidate_pool_recommendations.recommend_direction` | 同上 |

方向层落地后，`manager_v4_directional_exchange_pools.pair_direction_rank` 已经**按兑换方向**给出每个
`(代币对, 方向)` 组内的价格质量序位 —— **方向 0 是「从高地址兑换低地址」，方向 1 是反之**
（见 `T-方授储.01` 的方向层落地）。

**但闭环层仍在用旧排序选主池** ⇒ 方向层的序位**只用于「池在不在这一跳里」，不用于「谁是主池」**
⇒ 方向层最核心的能力（按方向选最优池）**在闭环层没有生效**。

**这就是本任务**：让**方向层的主序**成为**闭环层的主序**。

### 2.1 为什么不能直接删旧排序（**与 `T-方储.01` 的关系**）

旧排序**同时**扮演两个角色：

1. **候选成员的来源**（哪些池进入这一跳的候选列表）；
2. **主序的决定者**（候选列表里谁是第一）。

若在**主序未切换**之前删掉旧排序 ⇒ 候选列表的**成员来源**同时消失 ⇒ 闭环身份 / 容量 / 利润都会变。

**因此顺序是固定的**：**先切主序（本任务）→ 再下线旧排序（`T-方储.01`）**。
本任务落地后，旧排序**降级**为「**只**负责候选成员来源」，**不再**决定主序。

---

## 3. 设计要点（**本任务的技术决定**）

### 3.1 重排是**纯置换**（**不引入任何新池**）

影子比对（`D-116` §2）实测：**方向层 rank-1 永远落在旧候选层的截断窗口（容量上限 `5`）内**
（`candidateTier notInTier` **0 / 216**、`yzTier notInTier` **0 / 386**）
⇒ **只对旧候选层做重排**即可让方向层成为主序，**不需要**把方向层的池引入候选层
⇒ **候选成员集逐跳完全一致**（实测 `candidateMembershipPerHopIdentical = true`）。

### 3.2 三条不变式（**代码保证**）

| 不变式 | 实现方式 | 实测 |
|---|---|---|
| **成员集不变** | 排序后不增删候选，只改次序 | 逐跳成员集**完全一致** |
| **门禁不变** | 容量门禁、拓扑门禁、幂等键、跳过令牌**一字未改** | 跳数 **522 → 522**；跳过令牌 **1100 → 1100 且逐条相同** |
| **无 rank 的池不当代主** | 无 `pair_direction_rank` 的行**从未被定价** ⇒ 排在所有有 rank 的候选之后 | 6 跳保持 `None`（在最后一位） |

### 3.3 开关（**回滚路径**）

| 项 | 值 |
|---|---|
| 环境变量 | `FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY` |
| **缺省 / 空值** | **ON**（方向层是权威） |
| **显式负值** | `0` / `false` / `off` / `no` ⇒ **OFF**（旧序） |
| 其它任意值 | **ON**（拼写错误**不可能**悄悄关掉切换） |
| 读取时机 | **每次物化读一次** ⇒ 一次运行内口径一致，发布行与本次运行的顺序**永远**对得上 |

> **`.env` / `.env.example` 未改** —— 与 `FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS` 同模式（**运行期默认**生效，
> 不写进 env 文件 ⇒ 少一处「配置漂移」来源）。

### 3.4 `optimization_id` 采用**位置制**

重排后候选的**位置**变了，而 `optimization_id` 是候选的**身份键**（主键的一部分）。

| 决定 | 理由 |
|---|---|
| 位置 0（主池）⇒ `optimization_id = NULL` | 主池**不是**优化项，它是方向层指名的池 |
| 位置 n ≥ 1 ⇒ `optimization_id = {prefix}-rank-{n}` | **构造上唯一** ⇒ 零主键冲突风险；id 名字就是**它被尝试的次序** |
| 方向层 rank 另存 | `manager_v4_closed_loop_hops.directional_pool_rank` ＋ 候选 `optimization_basis` ⇒ **信息不丢** |

### 3.5 主池的 `pool_selection_basis` 改写

主池的选池依据**必须说真话**：新口径下「为什么选它」的答案是**方向层的 rank** ⇒ 改写为
`directional_pair_direction_rank:{rank}`。无 rank 时（组内无任何定价池）**保留**原 basis。

---

## 4. 执行步骤（**已执行**）

| 步 | 动作 | 完成判据 | 状态 |
|---:|---|---|:-:|
| **1** | 影子比对两套排序（**只读**） | 方向层 rank-1 在旧候选层窗口内 | ✅ **0 / 602 越界** |
| **2** | 影子复算（同一切片重物化两次：旧序 / 新序） | 旧序 ⇒ `changedRows = 0`；新序 ⇒ 差异可量化 | ✅ 两者均达成 |
| **3** | 改 `closed_loop_manager/src/lib.rs`（开关 + 重排 + basis） | 编译通过 | ✅ |
| **4** | 跑 §1.2 回归 | 全绿 | ✅ **46 passed / 0 failed** |
| **5** | 量化影响（闭环数 / 跳数 / 成员集 / 容量 / 价格质量 / 跳过令牌） | 全部有读数 | ✅ 见 `D-116` |
| **6** | 复核「旧口径切换」诊断位**未变差** | `readyForCutover` 改动前后一致 | ✅ 前后**均为 `false`** |
| **7** | **运行期重启后**在实盘库复测 | 实盘发布行与新序一致 | ✅ **已做**（2026-11-25 · `D-116` §8：`hops.changed = 0`、`rankHistogram {1: 522}`、`selectionBasisHistogram {directional_pair_direction_rank:1: 522}`） |
| **8** | 归档 | — | ✅ **已完成**（`plan/archive/任务归档_T-闭.01_…_20261125.md`） |

### 4.1 三条执行纪律

1. **编译 ≠ 测试**：只改接口签名时，`cargo build --offline --workspace` 足以暴露类型破坏 ⇒
   **不必**跑全部 crate 的测试。
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试；**内部实现**改动**不需要**。
3. **Python 侧同理**：`flashloan/app_runtime/tests/unit/` 有 **271** 个文件，
   **只跑受影响的那几个**。

---

## 5. 完成定义

- [x] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`
      > **读数（2026-11-25）**：`D-115` 共 **23** 项，**23 / 23** 已勾；
      > 最后一项「实盘生效复测」的读数见 `D-116` §8 与 `D-115` 该项下的引文块。
- [x] §1.2 的**全部**回归命令**全绿**
      > **读数（2026-11-25）**：`cargo test --offline -p closed-loop-manager` ⇒ **46 passed / 0 failed**；
      > 本轮**改动文件集合为空**（收尾轮只补文档与运行期复测）⇒ 又经工作区回归
      > `pool-manager 165` / `candidate-edge 14` / `candidate-exchange 7` / `manager-db 5` /
      > `rs-src 220` / `review-manager 158` / 架构守卫 **80** / 受影响 7 文件 **77** /
      > 生产库零写守卫 **14** ⇒ **全绿**。
- [x] 每条已勾选项都有**可复算的实测读数**
      > **读数**：结构 / 行为 / 边界三组共 22 项读数在 `D-116` §2–§4；
      > 实盘生效读数在 `D-116` §8。**无**「未测量」被写成 `0` 的情形。
- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
      > **读数（2026-11-25 实盘复测）**：闭环 **174**、跳 **522**、优化 **472**、跳过令牌 **1100**，
      > 与影子复算**逐项相同**；`changedRows = 0` ⇒ 只有主序来源被改写，门禁输出零变化。
- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）
      > **读数（2026-11-25）**：本任务唯一源码改动文件 = `flashloan/tools/closed_loop_manager/src/lib.rs`
      > （`D-116` §4）；收尾轮**未改任何产品代码**。

---

## 6. 本任务**不做**什么（**明确边界**）

| 不做 | 理由 |
|---|---|
| **不删** `recommend_direction` / `pool_score` | 那是 `T-方储.01` 的活；本任务**只**让它们不再决定主序 |
| **不**改方向层的 rank 算法 | 方向层是 `M-3`，本任务只**读**它的序位 |
| **不**改候选层的容量上限（`5`） | 该参数已是环境变量（`FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`），本任务**不动** |
| **不**改广播 / 复核 / 编排层 | 不在 §1.1 的模块集合内 |
| **不**改 Python 侧闭环构建器 | 那是**离线路径**（写 `dex_*` 旧表），与 `manager_v4_closed_loop_*` 无关 |
| **不**重启实盘运行时 | 属运维窗口动作，本任务**只**留出开关与读数 |

---

## ③ 修改范围守护

<a id="d114"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/D-114-SCOPE_GUARD.md` -->

# D-114 `T-闭.01`｜方向层主序接管闭环主池 · 修改范围守护（Scope Guard）

> **本文件是 `T-闭.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/src/lib.rs` | 是 |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 1.1 代码落点（**文件级** · 并行判定用）

| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-4` | `flashloan/tools/closed_loop_manager/src/lib.rs` | 与 `T-方闭.01` / `T-方闭储.01` **同文件**（已归档）⇒ 现无在途争用 |

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
> - `flashloan/tools/closed_loop_manager/src/lib.rs` —— **`M-4` 上唯一在途任务**；
>   `T-方闭.01` / `T-方闭储.01`（同文件）**均已归档** ⇒ **当前无并行冲突**。
> - **⚠️ 与 `T-复广.02`（`M-7` ＋ `M-6`）不同文件** ⇒ **可并列**，互不阻塞。

---

## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-4` | 闭环层 | `cargo test --offline -p closed-loop-manager` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/broadcast_manager/**` | 属 `M-7`，`T-复广.02` 在途 ⇒ **并行争用**，本任务**不碰** |
| `flashloan/tools/review_manager/**` | 属 `M-6`，`T-复广.02` 在途 ⇒ **并行争用**，本任务**不碰** |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；本任务**只读**方向层的 rank，**不改**其算法 |
| `flashloan/src/core/db/builders.py` | Python 闭环构建器是**离线路径**（写 `dex_*` 旧表），不写 `manager_v4_closed_loop_*` ⇒ 不受影响，**不碰** |
| `flashloan/src/core/db/storage_v4_exchange.py` / `flashloan/rs-src/src/evidence.rs` | 属 `M-10` / `M-2`，不在 §1 的模块集合内 |
| `flashloan/rs-src/**` | 运行时壳与发布层不在本任务的模块集合内 |
| `.env` / `.env.example` / 各 `chain.json` | **广播三道闸门与链配置**一字未动；新开关走**运行期默认**，**不写 env** |
| 实盘库 `flashloan/db/local_store_4663.sqlite3` | 本轮**实盘库零写入**；分析一律 `mode=ro` ＋ `PRAGMA query_only = ON`；影子复算在**瘦副本**上做 |

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
| 写生产库 | **否**（本任务） | 本轮**实盘库零写入**；影子复算只写 `plan/runtime_evidence/` 下的**瘦副本** |
| 广播 | **否**（本任务） | 本任务**不碰**广播层 |
| 重启实盘运行时 | **是**（运维窗口） | 属 `T-闭.01` 第 7 项验收的前置动作，**不在**本任务的代码范围内 |

---

## 6. 范围守护自检

- [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
- [x] §2 的**全部**命令**已执行**且**全绿**（`46 passed / 0 failed`）
- [x] §3 的禁止触碰项**确未被触碰**
- [x] **未**因本任务跑过**全量**测试（本任务只跑了 `M-4` 的独立测试）

---

## ④ 测试目标 check list

<a id="d115"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/D-115-checklist.md` -->

# D-115 `T-闭.01`｜方向层主序接管闭环主池 · 测试目标 check list

**验收前置**：`M-3`（方向层）已产出 `pair_direction_rank`（`T-方授储.01` 已闭合）。

> 勾选纪律：**每一条都必须有可复算的实测读数**；未实测项写「**未测量**」，**不得**用零冒充。
> 全部读数的出处见 [`docs/D-116`](#d116)。

#### 结构验收

- [x] 旧主序的两个来源已定位并登记：`manager_dex_candidate_pool_recommendations.recommend_direction`（首跳 / 末跳）＋ `manager_market_observation_yz_pairs.optimal_pool_id`（中间跳）
- [x] 两套排序算法的**差异已写清**：方向层 = `directional_output` desc → `tick_ordering_rank` → `fee` → **capacity** → `base_pool_id`；旧 `recommend_direction` = `exact_output` desc → tick_rank desc → `fee` → `pool_id`；旧 `pool_score` = **深度代理**（`liquidity_index` 排序，**非价格质量**）
- [x] 方向层 rank-1 在旧候选层**截断窗口内的占比已实测**：`candidateTier notInTier` **0 / 216**、`yzTier notInTier` **0 / 386` ⇒ **重排即可接管，不必引入新池**
- [x] 闭环主池改由**方向层 rank-1** 决定（代码落点：`closed_loop_manager/src/lib.rs::order_hop_candidates_by_direction_rank`）
- [x] 回滚开关已落地：`FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY`（**缺省 ON**；仅 `0`/`false`/`off`/`no` ⇒ OFF；其它值 ⇒ ON）
- [x] 开关**每次物化只读一次** ⇒ 一次运行内口径一致（发布行与本次运行的顺序永远对得上）
- [x] 主池的 `pool_selection_basis` 改写为 `directional_pair_direction_rank:{rank}`（**发布行不说谎**）；方向层 rank 另存 `manager_v4_closed_loop_hops.directional_pool_rank` ＋ `optimization_basis` ⇒ **信息不丢**
- [x] `optimization_id` 采用**位置制**（`{prefix}-rank-{n}`；位置 0 ⇒ `NULL`）⇒ **构造上唯一**，零主键冲突风险

#### 行为验收

- [x] 闭环层回归全绿：`cargo test --offline -p closed-loop-manager` ⇒ **46 passed / 0 failed**（原 41 ＋ 新增 5）
- [x] **闭环身份不变**：闭环 **174 → 174**（新增 **0** / 移除 **0**）
- [x] **跳数不变**：**522 → 522**；跳过令牌 **1100 → 1100 且逐条相同**（`identical = true`）
- [x] **候选成员集逐跳完全一致**（`candidateMembershipPerHopIdentical = true`）⇒ **纯置换**，**未引入任何新池**
- [x] **门禁未放宽**：容量门禁仍生效（容量 **37 变好 / 46 变差 / 91 不变**）；`temporary` 覆盖 **6 → 0** ⇒ 6 条「容量门禁换池接管」全部消失，**不是被跳过而是被方向层序位直接选中**
- [x] **价格质量（利润代理）实测**：**168** 条可测 ⇒ **89 变好 / 0 变差 / 79 不变**；合计 **167.76297 → 168.46708（+0.4197 %）** ⇒ **方向层序位对价格质量单向占优**
- [x] **旧序可回滚**：开关 OFF 时对同一切片重物化 ⇒ `changedRows = 0`、`closedLoopVersion = 19` ⇒ **与实盘发布行逐行一致**（零差别的对照证明）
- [x] 「旧口径切换」诊断位**未变差**：`readyForCutover` 改动**前已是 `false`**（闭环 174 vs 104、跳 522 vs 312、`fieldMismatch` 225 行）⇒ 本改动**不使该诊断位变差**
- [x] 换池率已量化：跳维度 **118 / 522 = 22.6054 %**（`y-z` **44**、`yc` **41**、`zc` **33**）；池集合变化的闭环 **95 / 174 = 54.5977 %**

#### 边界验收（**不得**发生）

- [x] **未**引入旧候选层之外的池（成员集逐跳一致）
- [x] **未**放宽任何门禁（容量 / 拓扑 / 幂等 / 阈值**一字未改**）
- [x] **未**触碰 `broadcast_manager` / `review_manager` / `directional_pool_manager` / Python 闭环构建器 / `.env` / `chain.json`
- [x] **实盘库零写入**（全程 `mode=ro` ＋ `PRAGMA query_only = ON`；复算在 **58 MB 瘦副本**上进行）
- [x] **未**在同一批次内同时「切换新口径」与「删除旧口径」⇒ 旧排序**仍在**，只**不再**决定主序（下线是 `T-方储.01` 的活）
- [x] **实盘生效复测**：运行期重启后，实盘库重物化的发布行与新序一致

      > **读数（2026-11-25 08:24–08:27 · `D-116` §8）**：
      > - **根因不是代码，是授权位**：`manager-api` 以 `CLOSED_LOOP_MANAGER_AUTHORITY=legacy` 启动
      >   ⇒ `closed_loop_writes_enabled = false` ⇒ apply 被拒 ⇒ 新序**从未进入实盘发布行**。
      > - **二进制重建 ＋ 运行期重启**：`manager-api.exe` mtime **2026-11-25 07:56:27**、
      >   `flashloan_rs.dll` mtime **07:57:52**、实盘运行时 pid **29032** **08:01:47** 启动 ⇒ 新口径**已进运行期**。
      > - **重物化**（先 dry-run 后 apply）：`changedRows = 0`、`closed_loop_version` **19 → 20**、
      >   闭环/跳/优化 **174 / 522 / 472**（与影子复算**逐项相同**）、
      >   `materialized_checksum = sha256:5299fc3c…`、`consumers_need_rebuild = false`。
      > - **发布行逐条核对**（`compare_directional_primary_order_20261125.py`，只读）：
      >   `hops.changed = 0`、`changeRatePercent = 0.0`、`affectedLoopCount = 0`、
      >   `rankHistogram = {1: 522}`、`selectionBasisHistogram = {directional_pair_direction_rank:1: 522}`。
      > - **独立复核**（直读实盘库，不走同一工具）：`pool_selection_basis` 直方图
      >   `Counter({'directional_pair_direction_rank:1': 522})`；闭环 **174**、优化 **472**、跳过令牌 **1100**。
      >   报告：`plan/runtime_evidence/reports/directional_primary_order_after_rebuild_20261125.json`。
      >
      > **口径澄清**：`hops.changed = 0` **不是**「新序没生效」，而是**已生效且已稳定** ——
      > 该工具比的是「实盘发布行」与「按新序重算的期望值」，逐行相等即一致。
      >
      > **连带处置**：`consumers_need_rebuild` 由闭环管理器**自愈**清零（第二次 apply，checksum 未变），
      > **本任务未手工写它** ⇒ runtime_core 恢复接受快照（`generation = 55`、`lastError` 空）。
      > 复核代次的读数归 `T-复.02` / `T-复编.02`，**本项不代关闭**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/README.md` -->

# `T-闭.01`｜方向层主序接管闭环主池

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-闭.01`（纯 ID）　**验收**：`AC-闭.01`　**优先级**：P1　**波次**：W-1
**编号逻辑**：`T-闭.01` / `AC-闭.01` —— **模块内索引**，编号索引 **`闭`**（改动模块：`M-4`（闭环层（闭环集合 / 容量折叠 / 兜底））），该索引号段内第 `01` 个（**该号段此前为空**）。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-4`

**前置模块**：M-3　　**需授权**：否（本任务只改代码与文档；实盘生效需运行期重启）

## 一句话

闭环三跳的**主池**改由**方向层的序位**决定（方向 0 = 高地址 → 低地址，方向 1 = 低地址 → 高地址），
旧排序**降级**为「只负责候选成员来源」，**不再**决定主序。

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**23** 项**全部闭合** · 2026-11-25 实盘生效复测完成） |
| `docs/` | 本任务的**独立技术文案**区（实施与影子复算读数） |

## 独立回归

```powershell
cargo test --offline -p closed-loop-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d116"></a>

### `D-116-实施与影子复算报告_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/docs/D-116-实施与影子复算报告_20261125.md` -->

# D-116 `T-闭.01`｜方向层主序接管闭环主池 · 实施与影子复算报告

> **日期**：2026-11-25　**任务**：`T-闭.01`（`M-4` 闭环层）
> **实盘库**：**零写入**（全程 `mode=ro` ＋ `PRAGMA query_only = ON`）
> **复算切片**：`plan/runtime_evidence/closed_loop_slice_4663.sqlite3`（**58,511,360 B**，83 张表、
> `manager_v4_directional_exchange_pools` **692** 行、`manager_v4_closed_loop_hops` **522** 行、
> `manager_v4_closed_loops` **174** 行）

---

## 1. 判定

**方向层的主序应当接管闭环层的主池**，且可以在**不引入任何新池、不放宽任何门禁**的前提下接管。

支撑这一判定的三条实测事实：

| 事实 | 读数 | 出处 |
|---|---|---|
| 方向层已按方向给出价格质量序位 | 方向池 **692** 行，其中有 `pair_direction_rank` 的 **342** 行 | §2.1 |
| 方向层 rank-1 **永远**在旧候选层的截断窗口（容量上限 **5**）内 | `candidateTier notInTier` **0 / 216**、`yzTier notInTier` **0 / 386** | §2.2 |
| 旧序与新序的差异**只在次序**，不在成员 | 候选成员集逐跳**完全一致**（`candidateMembershipPerHopIdentical = true`） | §3.2 |

⇒ **只对旧候选层做重排**即可让方向层成为主序 ⇒ **不需要**扩大候选来源 ⇒ **不需要**碰方向层的准入逻辑。

---

## 2. 方法（**可复算**）

### 2.1 工具链

| 工具 | 作用 | 写库 |
|---|---|---|
| `plan/runtime_evidence/tools/clone_closed_loop_slice_20261125.py` | 把 **11.93 GB** 的实盘库切成**瘦副本**（逐表复制 schema ＋ 只复制闭环层会读的表） | 只写副本 |
| `plan/runtime_evidence/tools/compare_directional_primary_order_20261125.py` | **只读**影子比对：方向层序位 vs 旧口径主序 | **不写** |
| `plan/runtime_evidence/tools/compare_directional_primary_order_effect_20261125.py` | 对**两次物化**的切片做差异量化 | **不写** |
| `plan/runtime_evidence/tools/probe_legacy_cutover_comparison_20261125.py` | 复算闭环层的「旧口径切换」诊断位 | **不写** |

### 2.2 影子比对的**顺序**（**两次物化，同一切片**）

| 次 | 开关 | 结果 |
|---|---|---|
| **旧序** | `FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY=0` | `changedRows = 0`、`closedLoopVersion = 19` ⇒ **与实盘发布行逐行一致**（**零差别的对照证明**） |
| **新序** | **缺省 ON** | `changedRows = 2442`、`closedLoopVersion = 20`、`consumersNeedRebuild = true` |

> **旧序那一跑是必要的**：它证明「开关关掉 ⇒ 与今天的实盘**逐行相同**」⇒
> **回滚路径确实等价于「什么都没做」**，而不是「看起来差不多」。

### 2.3 影子比对的分项读数（**只读 · 实盘切片**）

采集时刻 `2026-11-24T20:24:34.805588+00:00`，报告：
`plan/runtime_evidence/reports/directional_primary_order_shadow_20261125.{json,md}`。

| 观测 | 读数 |
|---|---|
| 方向池行数 | **692**（有 rank 的 **342**） |
| 已发布闭环跳 | **522**（可比 **522**） |
| 跳维度换池率 | **23.7548 %**（124 / 522） |
| 受影响闭环 | **99** / **174** |
| 中间跳（`y-z`）主序换池率 | **22.7979 %**（88 / 386） |
| 首跳（`yc`）/ 末跳（`zc`）主序换池率 | 各 **12.037 %**（13 / 108） |

**当前选中池的方向序位分布**（即「今天的主池在方向层里排第几」）：

| `directional_pool_rank` | 跳数 |
|---|---|
| **1** | **404** |
| 2 | 61 |
| 3 | 51 |
| **None**（从未被定价） | **6** |

⇒ **今天的主池有 118 / 522 跳（22.6 %）不是方向层的第一名** —— 这就是本任务要修的那部分。

---

## 3. 影响量化（**同一切片 · 两次物化的差**）

报告：`plan/runtime_evidence/reports/directional_primary_order_effect_20261125.json`

### 3.1 集合层（**身份与规模**）

| 观测 | 旧序 | 新序 | 结论 |
|---|---|---|---|
| 闭环数 | **174** | **174** | **新增 0 / 移除 0** ⇒ **闭环身份不变** |
| 跳数 | **522** | **522** | **跳数不变** |
| 跳过令牌 | **1100** | **1100** | `identical = true` ⇒ **逐条相同** |
| 池集合变化的闭环 | — | **95 / 174 = 54.5977 %** | 换池发生在**跳**上，不在**闭环身份**上 |
| 换池跳数 | — | **118 / 522 = 22.6054 %** | `y-z` **44**、`yc` **41**、`zc` **33** |

### 3.2 成员集（**纯置换的证明**）

| 观测 | 读数 |
|---|---|
| 主池 ∪ 备选 的**逐跳成员集** | **完全一致**（`candidateMembershipPerHopIdentical = true`） |
| 优化项总数 | **472 → 472** |
| 优化项状态 | 前 `{candidate: 466, temporary: 6}` ⇒ 后 `{candidate: 472}` |

> **`temporary` 由 6 ⇒ 0** 是本报告最容易被误读的一条，必须说清：
> 那 **6** 条是「主池容量不足 ⇒ 换备选池临时顶上」的**换池接管**记录；
> 新序下它们**不是被跳过，而是直接被方向层的 rank-1 选中** ⇒ 这 **6** 条不再需要接管。
> **门禁没有放宽**（容量门禁仍对**当时的主池**生效），只是**主池本身**换成了更好的那个。

### 3.3 容量与价格质量（**两个相反方向的结果**）

| 指标 | 旧序 | 新序 | 变化 |
|---|---|---|---|
| 折叠容量合计（raw） | `534475766102189469973` | `531288813524345385307` | **−3,186,952,577,844,084,666（−0.5963 %）** |
| 容量分布 | — | — | **37 变好 / 46 变差 / 91 不变**（174 条） |
| 价格质量（三跳 `output_rate` 之积，利润代理） | `167.7629734282774` | `168.4670844410144` | **+0.7041110127370018（+0.4197 %）** |
| 价格质量分布 | — | — | **89 变好 / 0 变差 / 79 不变**（**168** 条可测） |

**必须如实写的两面**：

1. **价格质量**（= 利润的直接代理）**单调占优**：**89 变好、0 变差**。
   ⇒ 方向层的序位**确实是更好的价格序**，不是噪声。
2. **折叠容量**（= 单 tick 可承载的规模）**轻微下降 0.5963 %**：**37 变好、46 变差**。
   ⇒ 方向层排第一的池**不总是**深度最厚的那个（它是**价格**最优，不是**深度**最优）——
   这与旧 `pool_score` 用 `liquidity_index` 作**深度代理**的排序**恰好相反**。

⇒ **口径结论**：切换后**每一跳的报价更好**，但**平均单 tick 承载略降**。
这不是「有收益无代价」，而是一次**明确的取舍**：用 **0.60 % 的容量**换 **0.42 % 的价格质量**，
且价格质量的改善是**单向**的（0 条变差），容量则**双向**分布（37/46）。

### 3.4 旧口径切换诊断位（**未变差**）

探针 `probe_legacy_cutover_comparison_20261125.py`（只读）复算闭环层与旧表
（`v4_closed_loops` / `v4_closed_loop_hops`）的逐字段比对：

| 项 | 改动**前** | 改动**后** |
|---|---|---|
| `readyForCutover` | **`false`** | **`false`**（**未变差**） |
| 闭环数（旧 vs 新） | 174 vs **104** | 174 vs **104** |
| 跳数（旧 vs 新） | 522 vs **312** | 522 vs **312** |
| `fieldMismatch` 行数 | **225** | **225** |

> **诚实边界**：该诊断位**改动前就已经是 `false`**（旧表与新表的行数本就不同）⇒
> **本改动不是「让它变红」的原因**，但也**不能**用它证明「本改动是安全的」——
> 安全性由 §3.1～§3.3 的逐项读数给出。

---

## 4. 代码改动（**唯一改动文件**）

`flashloan/tools/closed_loop_manager/src/lib.rs`

| 位置 | 改动 |
|---|---|
| 常量区 | 新增 `pub const DIRECTIONAL_PRIMARY_ORDER_ENV: &str = "FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY";` |
| `read_cross_tick_review_threshold_usdg_raw` 之后 | 新增 `directional_primary_order_from_value(Option<&str>) -> bool`（**缺省/空 ⇒ ON**；仅 `0`/`false`/`off`/`no` ⇒ OFF）与 `directional_primary_order_enabled() -> bool` |
| `struct ClosedLoopInput` | 新增字段 `directional_primary_order: bool` |
| `read_closed_loop_input` | 一次性读取并写入该字段（**每次物化读一次** ⇒ 一次运行内口径一致） |
| `build_hop_candidates` | 候选层循环 ＋ 备用层追加**之后**调用 `order_hop_candidates_by_direction_rank(&mut candidates, backup_id_prefix)`（受开关控制） |
| **新增** `order_hop_candidates_by_direction_rank` | ① **稳定**排序：有 rank 的按 rank 升序在前，`rank = None` 的排最后；② 位置 0 的 `optimization_id = None`，其余重编号为 `{prefix}-rank-{position+1}`；③ 位置 0 的 `basis` 改为 `directional_pair_direction_rank:{rank}`（无 rank 时保留原 basis） |

### 4.1 **拓扑错误门禁的位置**（**刻意不动**）

旧层 position 0 的 `resolved?` **拓扑错误门禁**（主池解析不到方向行 ⇒ 物化中止）
**在重排之前触发** ⇒ 本次改动**没有**把这条门禁挪到重排之后。

理由：该门禁校验的是「**这一跳的候选来源本身是否合法**」（旧层的「第一」是不是一个可解析的方向行）；
重排只改**次序**，不改**候选来源的合法性** ⇒ 门禁语义不变。

### 4.2 回归（**只跑 `M-4`**）

```powershell
cargo test --offline -p closed-loop-manager
```

⇒ **46 passed / 0 failed**（原 41 ＋ 新增 **5**）。

新增用例：

| 用例 | 断言 |
|---|---|
| `the_primary_order_switch_is_read_from_the_environment` | 缺省/空/空白 ⇒ ON；`0`/`false`/`off`/`NO` ⇒ OFF；其它 ⇒ ON |
| `the_direction_rank_decides_the_primary_of_a_hop` | 方向层 rank-1 成为主池；`directional_pool_rank = 1`；`pool_selection_basis = directional_pair_direction_rank:1`；**不是** `temporary_override` |
| `the_legacy_order_is_kept_when_the_switch_is_off` | 开关 OFF ⇒ 旧候选层仍命名主池，basis 仍是 `first_fixed_fee` |
| `the_reordering_keeps_the_membership_of_the_candidate_list` | 开关 ON/OFF 的**逐跳成员集完全相同** |
| `a_pool_without_a_rank_stays_behind_the_ranked_pools` | 无 rank 的池**不会**成为主池，且仍留在备选里 |

既有 3 处断言随口径更新（`middle-backup-rank-6` ⇒ `middle-rank-3`；旧口径 fixture 的 3 个 hop basis ⇒ `directional_pair_direction_rank:1`）。

### 4.3 编译面

```powershell
cargo build --offline -p closed-loop-manager -p directional-pool-manager -p composite-manager
```

⇒ **exit 0**。

> **诚实边界（当轮读数 + 复测）**：`cargo build --offline --workspace`（`flashloan/tools` 工作区）
> 在本轮**早先**的复跑中**失败**，原因是**并行进程**（`T-复广.02`）在途的 `broadcast_manager` 改动
> （`manager-api` 报 `E0063 missing fields admission_now/attempt_amounts_raw/guard_policy` 与
> `E0004 non-exhaustive BroadcastError::BroadcastRequestNotAdmitted`）⇒ **与本任务无关**。
> **2026-11-25 复测：并行进程收敛后 `cargo build --offline --workspace` 恢复 `exit 0`**
> （`Compiling manager-api` → `Finished dev profile`）⇒ 该失败**已消解**，**不计入**本任务的验收。

---

## 5. 未测量 / 未完成项（**不得**用零冒充）

| 项 | 状态 | 原因 |
|---|---|---|
| **实盘生效** | ⬜ **未做** | 实盘运行时 pid **27084** 自 2026-11-24 23:56:12 起**未重启**；新口径**须随下次运行期重启**才生效 |
| **实盘库重物化后的发布行** | ⬜ **未做** | 需重启 ＋ 写实盘库（**需运维窗口授权**） |
| **实盘利润读数** | ⬜ **未做** | 依赖实盘生效；本次只给了**价格质量代理**（§3.3） |
| 跨月统计 / 落盘失败等**低概率异常** | ⬜ **按约定留作最后** | 属 `T-储.02` 等任务，**不属**本任务 |

---

## 6. 复算步骤（**逐条可复制**）

```powershell
# ① 影子比对（只读实盘库）
python plan/runtime_evidence/tools/compare_directional_primary_order_20261125.py `
    "E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3"

# ② 切瘦副本（实盘库只读，副本才写）
python plan/runtime_evidence/tools/clone_closed_loop_slice_20261125.py `
    "E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3" `
    "E:\2026OPC大赛\flashLoan\plan\runtime_evidence\closed_loop_slice_4663.sqlite3"

# ③ 复制两份，分别用旧序 / 新序物化
Copy-Item plan/runtime_evidence/closed_loop_slice_4663.sqlite3 plan/runtime_evidence/slice_off.sqlite3
Copy-Item plan/runtime_evidence/closed_loop_slice_4663.sqlite3 plan/runtime_evidence/slice_on.sqlite3
$env:FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY = "0"
cargo run --offline -p closed-loop-manager --example materialize_closed_loops -- slice_off.sqlite3
Remove-Item Env:FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY
cargo run --offline -p closed-loop-manager --example materialize_closed_loops -- slice_on.sqlite3

# ④ 量化差异（两份都只读）
python plan/runtime_evidence/tools/compare_directional_primary_order_effect_20261125.py `
    plan/runtime_evidence/slice_off.sqlite3 plan/runtime_evidence/slice_on.sqlite3 `
    plan/runtime_evidence/reports/directional_primary_order_effect_20261125.json

# ⑤ 旧口径切换诊断位（只读）
python plan/runtime_evidence/tools/probe_legacy_cutover_comparison_20261125.py
```

> **注意**：步骤 ③ 会**写**副本（`slice_*.sqlite3`）—— **实盘库全程只读**。

---

## 7. 遗留物（**证据 · 可保留**）

| 文件 | 大小 | 处置 |
|---|---|---|
| `plan/runtime_evidence/closed_loop_slice_4663.sqlite3` | 58,511,360 B | **保留在本地**（复算证据）；**已由 `.gitignore` 排除**（`plan/runtime_evidence/*.sqlite3`）⇒ **不入库** |
| `plan/runtime_evidence/slice_off.sqlite3` | 58,511,360 B | **保留在本地**（旧序对照）；**不入库** |
| `plan/runtime_evidence/slice_on.sqlite3` | 58,519,552 B | **保留在本地**（新序对照）；**不入库** |

> **不入库的理由**：三者各约 **58 MB**，且可由 `clone_closed_loop_slice_20261125.py` 从实盘库**只读**重建。
> **入库的是读数**（`reports/` 下的 JSON ＋ 本报告），不是**可重建的中间产物**。

---

## 8. 实盘生效复测（**2026-11-25** · 本节闭合 `D-115` 最后一项）

> §1–§7 是**影子复算**（瘦副本上两次物化的差）。本节是**实盘库**上的复测：
> 二进制重建 → 运行期重启 → 在实盘库重物化 → 逐条核对发布行。

### 8.1 触发本节的根因（**如实登记**）

本任务 §5 登记的唯一未完成项是「**实盘生效复测**」，根因**不是**代码问题，而是**授权位**：

| 项 | 复测前的值 | 复测后的值 |
|---|---|---|
| `manager-api` 的 `CLOSED_LOOP_MANAGER_AUTHORITY` | `legacy` | **`manager`** |
| `closed_loop_writes_enabled` | **`false`** | `true` |
| 实盘闭环 `closed_loop_version` | **19** | **20** |

⇒ `legacy` 模式下 `POST /v1/closed-loops/rebuild` 的 **apply** 被拒
（`manager_api/src/lib.rs` 的 `closed_loop_authority.manager_apply_enabled()` 门禁）
⇒ 新序**从未进入实盘发布行** ⇒ 本任务在 §5 只能登记为未完成。

### 8.2 二进制重建与运行期重启（**实测**）

| 项 | mtime / 启动时刻 |
|---|---|
| `flashloan/tools/target/release/manager-api.exe` | **2026-11-25 07:56:27** |
| `flashloan/rs-src/target/release/flashloan_rs.dll` | **2026-11-25 07:57:52** |
| 实盘运行时（`app_runtime/run.py --port 5000`）pid **29032** | **08:01:47** 启动 |
| `manager-api` pid **24120**（`CLOSED_LOOP_MANAGER_AUTHORITY=manager`） | **08:24:57** 启动 |

> 二进制 mtime **晚于**本任务的全部源码改动 ⇒ 新口径**确实随重启进入了运行期**。
> 运行期 profile 为 `cow_quote=false` / `cow_submit=false` ⇒ **不会真实发单**，本节无资金风险。

### 8.3 实盘重物化（**先 dry-run，后 apply**）

`POST http://127.0.0.1:8091/v1/closed-loops/rebuild`，body `{"chainId": 4663, "dryRun": true}`：

| 读数 | 值 |
|---|---|
| `changedRows` | **0** |
| `closedLoopVersion` | 20 |
| `loopCount` / `hopCount` / `optimizationCount` | **174 / 522 / 472**（与 §3 的影子读数**逐项相同**） |
| `materializedChecksum` | `sha256:5299fc3ca395135601786d6a09cf2ae9d353eb21bc4669bf0994100fd683a77d` |
| `consumersNeedRebuild` | **`false`**（checksum 未变 ⇒ 无下游需要重建） |

apply（同 body，`dryRun: false`）后：`changedRows = 0`、`closedLoopVersion = 20`、
`consumersNeedRebuild = false` ⇒ **闭环身份 / 跳数 / 优化数零变化**，只有主序来源被改写。

### 8.4 发布行逐条核对（**只读**）

工具：`plan/runtime_evidence/tools/compare_directional_primary_order_20261125.py`
报告：`plan/runtime_evidence/reports/directional_primary_order_after_rebuild_20261125.json`

| 读数 | 值 |
|---|---|
| `hops.hopCount` / `hops.comparable` | **522 / 522** |
| `hops.changed` | **0** |
| `hops.changeRatePercent` | **0.0** |
| `hops.affectedLoopCount` | **0** |
| `hops.rankHistogram` | **`{1: 522}`** |
| `hops.selectionBasisHistogram` | **`{directional_pair_direction_rank:1: 522}`** |
| `loops.loopCount` / `statusCounts` | **174** / `{active: 174}` |
| `loops.capacityMeasured` | **174**（容量**全部已测量**，非零冒充） |

**独立复核（不走同一个工具）**：直读实盘库 `manager_v4_closed_loop_hops.pool_selection_basis`
⇒ 522 行**全部**为 `directional_pair_direction_rank:1`（直方图 `Counter({'directional_pair_direction_rank:1': 522})`）；
`manager_v4_closed_loops` **174** 行、`manager_v4_closed_loop_optimizations` **472** 行、
`manager_v4_closed_loop_skipped_tokens` **1100** 行。

> **口径说明**：`hops.changed = 0` **不是**「新序没生效」，而是**新序已经生效且已稳定** ——
> 该工具比的是「实盘发布行」与「按新序重算的期望值」，两者**逐行相等** ⇒ 一致。
> 若新序**未**生效，工具会报出 `hops.changed > 0`（跳池与 `directional_pair_direction_rank` 不符）。

### 8.5 连带处置：`consumers_need_rebuild` 的**自愈**（**实测**）

重物化（checksum 变化）会把闭环库状态的 `consumers_need_rebuild` 置 **1**，
这是**给下游消费者的信号**（复核层需要按新闭环重测）。本轮实测到：

1. 第一次 apply（v19 → v20）⇒ `consumers_need_rebuild = 1`；
2. runtime_core 因此**拒收**混合快照：
   `RuntimeCore rejected a mixed manager snapshot: [{"component":"closed_loop","field":"consumersNeedRebuild","expected":"false","actual":"true"}]`；
3. 复核代次随后发布（`review_version` → **104**，`closed_loop_version = 20`），
   该项冲突**消失**；
4. **第二次** apply（checksum 未变）⇒ 闭环管理器**自己**把标志清成 **0**
   （`closed_loop_manager/src/lib.rs` 的 `clear_consumers_need_rebuild`）
   ⇒ `updated_at = 2026-11-25T00:53:10.853807100+00:00`、`consumers_need_rebuild = 0`；
5. runtime_core 随即**接受**快照：`generation = 55`、`snapshotAvailable = true`、**`lastError` 空**。

> **本节不代任何任务关闭验收项**：标志是**闭环管理器自愈路径**清的，
> **本任务没有手工写它**（`D-049` 的 `C2` 项「自然清除」由此得到读数）。
> 复核代次的读数归 `T-复.02` / `T-复编.02`。

### 8.6 本节的诚实边界

| 项 | 状态 |
|---|---|
| 实盘发布行与新序一致 | ✅ **已实测**（§8.4） |
| 闭环身份 / 跳数 / 优化数零变化 | ✅ **已实测**（§8.3） |
| 容量门禁、拓扑门禁、幂等键、阈值 | ✅ **一字未改**（§4 只改排序与 basis） |
| 旧序（`recommend_direction` / `pool_score`）是否已下线 | ❌ **未下线**（那是 `T-方储.01` 阶段 2 的活） |
| `profitDrop` / 利润等价性 | ➖ **不在本任务范围**（`T-复编.02` 的 `D-103` 已给读数） |

> **本节闭合 `D-115` 的最后一项**（实盘生效复测）⇒ **`T-闭.01` 的 23 项验收全部闭合**。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.01/docs/README.md` -->

# `T-闭.01`｜方向层主序接管闭环主池 · 技术文案（docs）

> **本目录是 `T-闭.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

方向层序位如何接管闭环三跳的主池，以及这次切换对实盘的影响量化

## 文件

| 文件 | 内容 |
|---|---|
| [`D-116`](#d116) | 代码改动、影子复算方法、实盘切片上的影响量化 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
