# `T-方闭.01`｜备用池接线 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方闭.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 备用池接线 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261124 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方闭.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d083"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/D-083-PLAN.md` -->

# D-083 `T-方闭.01`｜备用池接线 · 开发计划（**独立 · 自包含**）

> **本目录是 T-方闭.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-方闭.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-方闭.01` / `AC-方闭.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方闭.01`** |
| **本任务验收编号** | **`AC-方闭.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`方闭`**（`M-3`（方向层（方向池物化 / 评分 / 准入））、`M-4`（闭环层（闭环集合 / 容量折叠 / 兜底））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 2 个模块**（`M-3`、`M-4`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`方闭`**。

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


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方闭.01`** |
| **验收编号（纯 ID）** | **`AC-方闭.01`** |
| **标题** | 备用池接线 |
| **优先级** | P0 |
| **波次（模块级）** | W-1 |
| **前置模块**（**不是**前置任务） | M-3 |
| **需授权** | 是（写库） |
| **未完成验收项** | **0** |
| **已闭合验收项** | **20**（2026-11-24 全闭合） |

### 1.1 改动模块（**这一列就是 T-方闭.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/` |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-4` | 闭环层 | `cargo test --offline -p closed-loop-manager` |
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |

```powershell
# T-方闭.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
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
- [x] §1.2 的**全部**回归命令**全绿**

      > `cargo test -p closed-loop-manager` ⇒ **41 passed / 0 failed**；
      > `cargo test -p directional-pool-manager` ⇒ **22 passed / 0 failed**；
      > 架构守卫 ⇒ **74 passed**（基线 74，**只增不减**）。

- [x] 每条已勾选项都有**可复算的实测读数**

      > 逐条读数见 `D-085`；实盘读数见
      > `docs/D-099` §6。

- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）

      > 实盘 168 条共有闭环的**派生列 0 差异**；方向层校验和**逐字节不变**。

- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）

      > 改动落点：`flashloan/tools/directional_pool_manager/`（M-3）、
      > `flashloan/tools/closed_loop_manager/`（M-4），
      > ＋ 两个**驱动用的 example**（`materialize_closed_loops` / `materialize_directional_pools`，
      > 仅用于从 shell 驱动实盘读数，**不含业务逻辑**）。
      > **未**碰 Python 侧（X-1 已由 Python 线程闭合）。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-方闭.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-方闭.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-04_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d084"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/D-084-SCOPE_GUARD.md` -->

# D-084 `T-方闭.01`｜备用池接线 · 修改范围守护（Scope Guard）

> **本文件是 `T-方闭.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-4` | 闭环层 | ``flashloan/tools/closed_loop_manager/`` | 是 |
| `M-3` | 方向层 | ``flashloan/tools/directional_pool_manager/`` | 是 |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p closed-loop-manager` |
| — | — | `cargo test --offline -p directional-pool-manager` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
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

<a id="d085"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/D-085-checklist.md` -->

# D-085 `T-方闭.01`｜备用池接线 · 测试目标 check list

**为什么单独立项**：`TASK-166` 的裁定稿查出了**「备用池从未被消费」**这一实锤缺口

（裁定 §6.4）。它是**接线问题**，**不是阈值问题** ——

`optimizations` **456** 行**全部** `status='candidate'`、`temporary_override` **0 / 504**、

代码中**无任何** `candidate→active` 流转路径、`read_optimizations` 的 SQL **无 `WHERE status`**、

容量门禁失败即整条闭环 `continue` ⇒ **该表是「只写不读」的表**

⇒ **「最多 5 个候选」今天不起作用**（实测最大候选数仅 **4**）。



**验收前置**：`T-方授储.01` 闭合（备用池由方向层的**排序分层**产出）；

**必须早于** `T-方闭储.01`（否则 P2 验收时备用池仍是「只写不读」）。

**依据**：[**`TASK-166`** 综合裁定与实施](../../docs/tec/D-012-TASK-166_综合裁定与实施_20261123.md) §5.2 / §5.3 / §6.4 / §7.1。

> **本轮（2026-11-24）全部 20 项已闭合**，每项附**可复算的实测读数**。
> 读数的出处：[`D-099`](#d099) §6。


#### g.1 结构验收



- [x] `optimizations` 的 `status` 存在**真实**的 `candidate → active` 流转路径

      （守卫须能**构造**该流转 ⇒ **非空洞**）

      > ✅ `closed_loop_manager/src/lib.rs:3336-3357`：`push_loop` 按「生效 / 试过未过门禁 / 待命」
      > 三态写 `status`（`temporary` / `expired` / `candidate`）。
      > **实盘读数**：`temporary` **6** 行、`expired` **0** 行、`candidate` **466** 行
      > （切换前：**456 / 456 / 0**）⇒ **流转行数 ≠ 0**。
      > 单测 `an_optimization_that_failed_the_gate_is_recorded_as_expired` **构造**了 `expired` 流转。

- [x] `read_optimizations` 的 SQL **含** `WHERE status`（只消费**已生效**的候选）

      > ✅ `:3884-3892`：`AND o.status IN ('temporary','accepted')`。
      > 另有 `read_pending_optimizations`（`:3934`）单列 `status='candidate'` 的待命项。
      > 单测 `only_the_optimizations_in_effect_are_consumed_and_the_rest_stand_by`。

- [x] 容量门禁失败时**按序试下一个候选**（备用池），而**不是**整条闭环 `continue`

      > ✅ `hop_candidate_combinations`（`:733`）＋ `capacity_gate_verdict`（`:2519`）：
      > 组合按 `(偏离跳数, i, j, k)` 升序枚举，**顶层 `(0,0,0)` 先试**，
      > 失败则试下一个组合，**全部失败才丢弃该路径**。
      > **实盘**：6 条路径由「整条丢弃」变为「换池后发布」（D-099 §6.2）。

- [x] **截断改分层**：落选池**留在备用池**，**不得**被丢弃

      > ✅ `build_hop_candidates`（`:2428`）：候选层之后追加**方向层同组**的备用层
      > （`rows_of_group`，真组键 `(scope, pair_key_id, direction)`）。
      > 实测**不丢池**：222 组**全部**满足 `合格池数 == 候选数 + 备用数`（**0 例外**）。

- [x] `MAX_DIRECTION_CANDIDATE_POOLS` 的注释写明：**容量旋钮，不是阈值**

      > ✅ `directional_pool_manager/src/lib.rs:43-56`（本轮新增）＋ `rs-src/src/business_builder.rs:9-17`（既有）
      > ＋ Python `direction_pool_score.py:34-39`（既有）⇒ **三侧同名同默认（5）**。
      > 单测 `the_candidate_capacity_is_a_knob_and_not_a_threshold`。


#### g.2 行为验收



- [x] **恒等式**：`N == min(N_max, 合格池数)` 恒成立

      > ✅ `candidate_tier_size(eligible) == eligible.min(max_direction_candidate_pools())`
      > （`directional_pool_manager/src/lib.rs:80-82`）。
      > 单测 `the_candidate_tier_is_never_wider_than_the_group_it_belongs_to`（`eligible` 取 0..=16）。
      > 实测：222 组**全部**满足（`probe_directional_tiering_20261124.py`）。

- [x] **不丢池**：同一 `(pair, direction)` 组内 **合格池数 == 候选数 ＋ 备用数**

      > ✅ 同上探针：**0 例外**。
      > 方向层**从不截断**（落选池带连续序位留在表里），分层的**不丢池**由**构造**保证。

- [x] **序位连续**：候选序位 **1..N** 连续无缺号（保 **I-6**）；备用序位 **N+1..** 同样连续

      > ✅ 实测：222 组的序位**全部**为 `1..K` 无缺号（**0 例外**）。
      > 顶替**不重排序位** ⇒ 不会出现两个序位 1（见下一条）。

- [x] **顶替可判定**：同组内按主序键比较；主序键全同才比容量；再同则比 `pool_id`（**确定性**）

      > ✅ `assign_ranks`（`directional_pool_manager/src/lib.rs:1511-1534`）：
      > `directional_output ↓ → tick 序 ↓ → fee ↑ → 方向容量 ↓ → base_pool_id ↑`。
      > **实盘零并列** ⇒ 补容量键**不改变任何一行**（方向层 dry-run 校验和
      > `sha256:5a609caf…` / `sha256:f6836f97…` 与库内**逐字节相同**，`changedRows=0`）。

- [x] **不得跨代币对**比较 `directional_output`（单位不同）

      > ✅ 分组键含 `pair_key_id` ⇒ 比较**只在同一代币对同一方向内**发生。
      > 实测**主序键并列的组 = 0**（无跨对比较的机会）。

- [x] 顶替后**重排序位**（否则出现两个序位 1 ⇒ 破坏 **I-6**）

      > ✅ **不重排** —— 顶替**不改变**方向层的序位表：被顶替的池**仍在方向层**里带着原序位，
      > 换的是**闭环这一跳用哪个池**。实测方向层校验和**逐字节不变** ⇒ 序位表未被动过。

- [x] **不得**为凑够 `N_max` 而放宽任何准入判据（`DPO-11` 纪律 ②）

      > ✅ `directional_pool_manager` 的准入判据**一条未改**；
      > 方向层实盘 dry-run 的 `basePoolCount` / `excludedPoolCount` / `measuredCapacityCount` /
      > `measuredOutputCount` **与库内逐字段相同**（488 / 318 / 212 / 208 与 494 / 318 / 216 / 212）。

- [x] **`status` 流转行数 ≠ 0** 的读数（接线**已生效**的直接证据）

      > ✅ 切换前 `candidate` **456 / 456**、`temporary_override` **0 / 504**；
      > 切换后 `candidate` **466** ＋ `temporary` **6**、`temporary_override` **0 / 516** ＋ **6 / 6**。

- [x] 容量不过时**下一个候选**被选中的对照读数

      > ✅ 6 条路径：主选池 `0x84e2847d…`（5 条）/ `0x0b765108…`（1 条）单跳容量为 0 ⇒
      > 旧代码整条 `continue`；新代码换用 `0x3bb34a44…`（5 条）/ `0x319bac87…`（1 条）**继续发布**。
      > 跳行自证：`pool_id ≠ base_pool_id`、`temporary_override=1`、`optimization_id='third-rank-2'`。


#### g.3 回归与边界验收



- [x] 架构守卫全量通过（基线**只增不减**）

      > ✅ `pytest app_runtime/tests/unit/test_architecture_guards.py -q` ⇒ **74 passed**（基线 **74**）。

- [x] 全量回归 **0 failed**（基线见任务清单 §7.2）

      > ✅ `cargo test -p closed-loop-manager` ⇒ **41 passed / 0 failed**（基线 **33**）。
      > ✅ `cargo test -p directional-pool-manager` ⇒ **22 passed / 0 failed**（基线 **18**）。
      > ✅ `cargo build --offline -p closed-loop-manager -p directional-pool-manager -p manager-contracts -p manager-db` ⇒ 通过。
      > **回归范围 = 本任务改动模块集合（M-3 / M-4）**，未跑全量 Python 单测。

- [x] **未**改动准入判据（`L1`）与排序主序（`L2`）的任何一条（`DPO-11`）

      > ✅ 主序**键**未改（只在其后**补**一个并列键）；准入判据未改（见上条实测读数）。

- [x] **未**放宽任何门禁；**未**广播

      > ✅ 门禁**一条未放宽**：折叠口径、`MIN_CLOSED_LOOP_CAPACITY_RAW`、USDG 估值、利润门禁、
      > 复核层盈利符号与广播门禁**全部未动**（只改了「**选哪个池**」）。
      > ✅ **未广播**（本任务不涉及广播链路）。
      > ✅ 实盘 `cross_tick_review_threshold_usdg_raw` 等派生列在 168 条共有闭环上**0 差异**。


#### g.4 与 `T-方闭储.01` 的关系（**顺序纪律**）



- [x] `T-方闭.01` **闭合在** `T-方闭储.01` **之前**（否则 P2 的等价性验收**不含**备用池兜底能力）

      > ✅ `T-方闭储.01` 于 2026-11-24 先行实盘切换（`closed_loop_version` **15 → 18**），
      > `T-方闭.01` 在其**之后**接线（**18 → 19**）⇒ **顺序成立**。
      > ⚠️ **诚实边界**：`T-方闭储.01` 的等价性验收是在**接线之前**做的，
      > 因此其「168 条闭环 0 差异」的读数**不含**备用池兜底能力；
      > 本任务自己的等价性验收（**168 → 174**，共有 168 条**逐行 0 差异**）**含**。

- [x] 若 `T-方闭储.01` 已开工 ⇒ `T-方闭.01` **让路**（两者都改 `closed_loop_manager` 的候选选择邻域）

      > ✅ `T-方闭储.01` 的改动落在**方向池的读法**（切表），
      > `T-方闭.01` 的改动落在**候选的选法**（接线）⇒ **邻域相邻但不重叠**。
      > 实测：`T-方闭储.01` 切换后的基线读数（**168 / 504 / 456**）**逐行保持**到本任务接线之前，
      > 本任务在其之上**只增 6 条**（D-099 §6.1）⇒ **两个任务的效果可分离**。


#### g.5 实盘与诚实边界（**本轮新增**）



- [x] **实盘已切换**且**回滚已演练**

      > ✅ 备份 `local_store_4663.sqlite3.bak_tfangbi_20261124`（8.2 GB，含 `-wal` / `-shm`）。
      > ✅ 回滚演练：还原备份 ⇒ 读数**逐行回到切换前**（**168 / 504 / 456 / 1110**，
      > `version=18`，校验和 `sha256:19168935…`）；对还原库重跑新二进制 ⇒ 落到**同一**读数
      > （**174 / 522 / 472 / 1100`，校验和 `sha256:6927d8d6…`）⇒ **回滚可用、重放幂等**。

- [x] **未闭合项如实登记**（**不冒充**）

      > **U-1**：`expired` 的**实盘读数为 0**（被试过而未过门禁的那个池是**候选层主选**，
      > `optimization_id` 为空 ⇒ 不产生 `expired` 行）；**通路只有单测覆盖**。
      > **U-2**：方向层**备用层实盘读数为 0**（真组键 `(scope, pair, direction)` 下
      > **没有**任何组越过 `N_max` = 5 ⇒ 备用层今天为空）；**通路只有单测覆盖**。
      > **U-3**：老化淘汰 / 交易事件触发 ⇒ 属 `M-5`（`T-候储.01`），**本任务留占位不实现**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/README.md` -->

# `T-方闭.01`｜备用池接线

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方闭.01`（纯 ID）　**验收**：`AC-方闭.01`　**优先级**：P0　**波次**：W-1
**编号逻辑**：`T-方闭.01` / `AC-方闭.01` —— **跨模块（组合索引）**，编号索引 **`方闭`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入）） ＋ `M-4`（闭环层（闭环集合 / 容量折叠 / 兜底））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-4` 、`M-3`

**前置模块**：M-3　　**需授权**：是（写库）

## 文件

| 文件 | 内容 |
|---|---|
| [`D-083`](#d083) | 本任务的开发计划与回归范围（**自包含**） |
| [`D-084`](#d084) | 本任务的修改范围守护与逐模块回归命令 |
| [`D-085`](#d085) | 本任务的逐条验收框（**0** 项未完成 / **20** 项已闭合） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d086"></a>

### `D-086-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/docs/D-086-现状与可行性分析.md` -->

# D-086 01 当前现状与可行性分析

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

**下一步：**进入 [02-目标架构与表结构](#d087)，看具体的表设计与闭环改造。

---

<a id="d087"></a>

### `D-087-目标架构与表结构.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/docs/D-087-目标架构与表结构.md` -->

# D-087 02 目标架构与表结构

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

**这个聚合关系是本方案的地基**：它让「方向」从路由属性变成池属性，让「闭环表由 3 个方向兑换池子组成」从一句口号变成一条可断言的数据结构。内存侧的对应实现见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)。

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

**投影纪律：** `pool_id` / `base_pool_id` 是方向池的**投影**，不是真相源；真相源是方向池表的 `direction` 列。两者必须在同一次写入里从同一行方向池导出，并由重建期复验校验。完整纪律见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md) §7。

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

I-1 ~ I-8 是**存储层**的不变量。I-9 ~ I-16 是**内存对象与跨层引用**的不变量（内存与库的一致、闭环 hop 对方向行的引用有效性、复核层的方向交叉校验、折叠时的方向自洽），完整定义见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md) §9。

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

<a id="d099"></a>

### `D-099-备用池接线开发计划与裁定_20261124.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/docs/D-099-备用池接线开发计划与裁定_20261124.md` -->

# D-099 `T-方闭.01`｜备用池接线 · 开发计划与裁定

> **本文件是 `T-方闭.01` 的开发计划。** 它把 `TASK-166` 裁定稿 §5.2 / §5.3 / §6.1 / §6.2 /
> §6.4 / §7.1 的结论落成**可施工的步骤**，并给出每一项的口径裁定与实测读数。
> 任务本身的编号逻辑见 [`D-083`](../../D-083)，验收项见
> [`D-085`](../../D-085)。

---

## 1. 结论先行

**「备用池从未被消费」是接线问题，不是阈值问题**，本任务把它接上：

| # | 缺什么（裁定 §6.4） | 本任务的落点 | 改动模块 |
|---|---|---|---|
| **1** | 换池的**触发条件**（何时换） | 闭环重建时**路径级容量门禁**失败（唯一触发条件） | M-4 |
| **2** | 换池的**决策执行**（按序逐个试） | 候选组合按「偏离主序的跳数」升序枚举，取第一个过门禁者 | M-4 |
| **3** | 备用池的**消费路径**（status 流转） | `candidate → temporary`（顶替生效）/ `candidate → expired`（试过未过门禁） | M-4 |

**不新增阈值、不放宽门禁、不改选池主序。** 顶层组合 `(主, 主, 主)` 永远第一个被试，
所以**现有 168 条闭环逐行不变**，变更**只可能新增**此前被丢弃的路径（单调增）。

---

## 2. 现状实测（2026-11-24 · `flashloan/db/local_store_4663.sqlite3`）

### 2.1 备用池今天是「只写不读」的表

| 读数 | 值 | 来源 |
|---|---|---|
| `manager_v4_closed_loop_optimizations` 行数 | **456** | `probe_backup_pool_wiring_20261124.py` |
| 其中 `status='candidate'` | **456 / 456（100 %）** | 同上 |
| `manager_v4_closed_loop_hops.temporary_override = 1` | **0 / 504** | 同上 |
| 代码中 `candidate → 其它` 的流转路径 | **不存在** | 全仓搜索 |
| `read_optimizations` 的 SQL | **无 `WHERE status`** | `closed_loop_manager/src/lib.rs:3400-3446` |
| 容量门禁失败时的行为 | **整条路径 `continue`** | `closed_loop_manager/src/lib.rs:2532` / `:2559` |

### 2.2 方向层（候选/备用分层的权威来源）

| 读数 | 值 |
|---|---|
| 方向池行数 | `core4` **340**（170 池 / 109 对）、`candidate_exchange` **352**（176 池 / 112 对） |
| **组内合格池数分布（按 `(scope, pair, direction)`）** | 1:**140**、2:**51**、3:**24**、4:**7**（共 **222** 组） |
| **合格池数 > 5 的组（按真组键）** | **0** ⇒ **今天的备用层是空的**（最大组 4 < `N_max` = 5） |
| 组内合格池数分布（**跨 scope 合并**，**错误口径**） | 1:6、2:67、4:24、5:3、6:9、7:3、8:2 ⇒ 「> 5 的组 = 14」**来自这个错误口径** |
| 主序键（`directional_output` / tick 序 / `fee`）**并列**的组 | **0**（`probe_rank_ties_20261124.py`） |

⇒ **方向层从不截断**：合格池全部带连续序位（保 **I-6**），落选池（序位 > `N_max`）留在表里。
⇒ **主序键零并列** ⇒ 把「容量」补进并列判定是**零影响补全**（不改任何现有序位）。

> **订正（2026-11-24 · 实施后实测）**：本节初稿写的「组内合格池数分布 1:6、2:67、4:24、5:3、6:9、7:3、8:2」
> 与「合格池数 > 5 的组 = **14**」是**按 `(pair, direction)` 跨 scope 合并**统计的，
> 而**闭环层的真组键是 `(scope, pair_key_id, direction)`**（方向层逐 scope 物化，序位也逐 scope 排定）。
> 按真组键重测（`probe_directional_tiering_20261124.py`）：**最大组 = 4 < `N_max` = 5**
> ⇒ **今天没有任何一个组越过候选层，备用层为空**。
>
> **结论不变**（接线是必要的、候选层口径一字未改），但**「落选池确实存在」的表述要改**：
> 落选池在**合并口径**下存在（14 个组），在**真组键**下**今天不存在**（0 个组）。
> 接线因此是**为未来准备**，不是**为今天补救** —— 今天的 6 条被救回的路径用的是**候选层内的第 2 候选**（见 §6.3）。

### 2.3 闭环层今天用掉的候选列表

| 读数 | 值 |
|---|---|
| 候选边推荐组大小分布（`(anchor, candidate, hop)`） | 1:132、2:48、3:32、**4:4**（**最大 4，从未触到 `N_max=5`**） |
| 市场观察方向池列表长度分布 | 0:1034、1:198、2:114、3:26、**4:48** |
| 闭环层候选列表**漏掉**方向层同组池的次数 | **0**（`probe_directional_group_coverage_20261124.py`） |

⇒ 截断今天**没有真的丢过池**（组内最大 4 < `N_max=5`），但**接线缺失是真的**：
`optimizations` 456 行全是待命候选，没有任何一行被消费过。

### 2.4 容量门禁今天丢弃的路径（**兜底的价值所在**）

`manager_v4_closed_loop_skipped_tokens` 的原因分布（`probe_capacity_gate_drops_20261124.py`）：

| 原因 | 行数 | 兜底可救？ |
|---|---|---|
| `yz_pool_missing` | 1030 | 中间方向**根本没有池**，无备可选 |
| `no_completing_middle_direction` | 72 | 同上 |
| **`path_single_tick_bound_unusable`** | **8** | **容量门禁失败 ⇒ 兜底的作用对象** |

8 行 = **6 条路径**（每条路径写 `first` + `third` 两行）。逐条核对
（`probe_capacity_gate_rescue_candidates_20261124.py`、`probe_backup_capacity_20261124.py`）：

| 路径 | 阻断跳 | 阻断池 | 该跳是否有备选 |
|---|---|---|---|
| 6 条 | **第三跳**（5 条 `0x84e2…`、1 条 `0x0b76…`） | `0x84e2…` / `0x0b76…` | **6 / 6 条都有** |

⇒ **兜底今天有真实的、可测量的作用对象**：6 条路径的第三跳主选池单跳容量为 0，
而第三跳都存在备选池。**这就是「容量不过时下一个候选被选中的对照读数」的落点。**

---

## 3. 口径裁定（**本任务的绑定性决定**）

### 3.1 D-1 接线对象 = 两张本来就为「生效池可被覆盖」预留的表

| 表 | 列 | 语义 |
|---|---|---|
| `manager_v4_closed_loop_hops` | `pool_id` | **当前生效池**（可被优化覆盖） |
| 同上 | `base_pool_id` | **原始推荐池**（覆盖失效后恢复） |
| 同上 | `optimization_id` | 生效的是哪一条优化 |
| 同上 | `temporary_override` | 该跳是否处于临时覆盖态 |
| `manager_v4_closed_loop_optimizations` | `status` | `candidate` 待命 / `temporary` 生效中 / `accepted` 永久采纳（**本任务不启用**）/ `expired` 试过未过门禁 |

**依据**：`docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:150` 写明 `pool_id` 是当前生效池、
可能被优化策略临时覆盖，而 `base_pool_id` 始终保留原始推荐池 —— **数据模型早就预留了，
只是从来没有人用过。** 本任务只是把这条既有语义接上，**不改表结构**。

### 3.2 D-2 触发条件 = 路径级容量门禁失败（**唯一**）

```text
path_single_tick_bound_unusable   （折叠后的单跳容量为 0）
path_capacity_below_minimum        （折叠后的路径容量低于 MIN_CLOSED_LOOP_CAPACITY_RAW）
```

**不引入**交易事件触发、不引入老化淘汰（裁定 §6.2 第 6/7 层依赖「最近一周有交易」
新数据源，见 §6.5）—— 这两层**留占位**，不实现。

### 3.3 D-3 决策执行 = 按「偏离主序的跳数」升序枚举

```text
组合 (i, j, k)：i = 第一跳候选下标，j = 中间跳候选下标，k = 第三跳候选下标
排序键： ( [i>0] + [j>0] + [k>0],  i,  j,  k )
```

- **下标 0 = 主选**，其余为备选；
- **偏离跳数少的优先** ⇒ 单跳顶替永远优先于多跳顶替（换池 = 换**池**，不是换**路**）；
- 同层按跳序（第一 → 中间 → 第三）⇒ 确定性；
- **顶层 `(0,0,0)` 第一个被试** ⇒ 现有闭环**逐行不变**。

### 3.4 D-4 候选清单 = 候选层（M-5 口径不变）＋ 备用层（方向层落选池）

```text
某跳的候选清单（有序）:
    [0] 候选层：候选边推荐表 / 市场观察方向表给出的有序列表   <- 口径不变，今天 <= 4 个
    [1..] 备用层：方向层同 (pair, direction) 组内**未出现在候选层**的池，按序位升序
```

- **候选层不变** ⇒ 主选池与选池主序**一字不改**；
- **备用层来自方向层** ⇒ 截断丢掉的落选池（序位 > `N_max`）**留在备用池，不得被丢弃**；
- 备用层**今天为空**（组内最大 4 < `N_max=5`），接线为未来准备，**不改变今天的读数**。

### 3.5 D-5 分层的三个恒等式由**方向层**保证

```text
合格池数 K（组内带序位的行数）
候选数 N = min(N_max, K)          <- N_max 是容量旋钮，不是阈值
备用数   = K - N
=> N + 备用数 == K               （不丢池）
=> 序位 1..N 连续、序位 N+1..K 连续（保 I-6）
```

方向层**从不截断**，三个恒等式**由构造保证**，不是由运行时校验保证。

### 3.6 D-6 顶替可判定（同组内，主序键相同时才比容量）

```text
directional_output down -> tick 序 down -> fee up -> 方向容量 down -> base_pool_id up
```

- **只在同一 `(pair, direction)` 组内比较** ⇒ 不跨代币对比较 `directional_output`（单位不同）；
- **方向容量是同组同代币的量** ⇒ 组内可比；
- 实测主序键**零并列** ⇒ 补进容量键是**零影响补全**（§2.2）。

### 3.7 D-7 门禁不放宽

折叠口径、路径最小容量口径、`MIN_CLOSED_LOOP_CAPACITY_RAW`、USDG 估值与利润门禁、
复核层的盈利符号与广播门禁 —— **一条都不动**。方向池与备选池只决定「**选哪个池**」，
不决定「**多少链路可行**」。

---

## 4. 施工步骤

| 步 | 模块 | 动作 | 完成判据 | 状态 |
|---:|---|---|---|---|
| 1 | M-3 | 方向层加**容量旋钮**（`FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`，默认 5）＋ 候选/备用**分层**函数 | 旋钮注释写明「容量旋钮，不是阈值」；`N == min(N_max, K)` 可断言 | ✅ `directional_pool_manager/src/lib.rs:43-95`；单测 `the_candidate_capacity_is_a_knob_and_not_a_threshold` / `the_ranks_split_the_group_into_a_candidate_and_a_backup_tier` |
| 2 | M-3 | `assign_ranks` 补**方向容量**并列键（主序键之后、`base_pool_id` 之前） | 实测零并列 ⇒ 现有序位**逐行不变** | ✅ `:1526-1533`；**实盘 dry-run 校验和逐字节不变**（§6.4） |
| 3 | M-4 | `DirectionalPoolIndex` 增加 `(scope, pair, direction)` 分组索引 | 备用层可枚举 | ✅ `closed_loop_manager/src/lib.rs:579-588` / `:2020-2050` |
| 4 | M-4 | 抽出「单组合的门禁裁决」函数，原路径改为**按序试组合** | 顶层组合先试 ⇒ 现有闭环不变 | ✅ `capacity_gate_verdict` `:2519` / `hop_candidate_combinations` `:733`；**实盘 168 条逐行不变**（§6.1） |
| 5 | M-4 | 生效顶替写 `optimization_id` / `temporary_override` / `pool_id` ≠ `base_pool_id` | 跳行可自证「现在是哪条优化在生效」 | ✅ `push_loop` `:3290-3301`；**实盘 6 跳 `temporary_override=1`**（§6.2） |
| 6 | M-4 | `optimizations.status` 流转：`candidate → temporary` / `candidate → expired` | 流转行数 ≠ 0（实盘读数） | ✅ `:3336-3357`；**实盘 `temporary` 6 行**；`expired` 实盘 **0**（U-1） |
| 7 | M-4 | `read_optimizations` 加 `WHERE status`（只消费已生效的候选） | SQL 可见 `WHERE` | ✅ `read_optimizations` `:3884-3892`（`status IN ('temporary','accepted')`）＋ 新增 `read_pending_optimizations` `:3934` |
| 8 | — | 回归：`cargo test --offline -p closed-loop-manager`、`-p directional-pool-manager`、架构守卫 | 全绿 | ✅ **41 / 22 / 74 passed**（基线 33 / 18 / 74，**只增不减**） |
| 9 | — | 实盘：影子库 → 回滚演练 → 切换 → 读数 | 见 §6 | ✅ **已切换**（`closed_loop_version 18 → 19`）；回滚**已演练**；读数见 §6 |

### 4.1 回归范围（= 改动模块集合，不多跑）

```powershell
cargo test --offline -p closed-loop-manager
cargo test --offline -p directional-pool-manager
```

---

## 5. 跨线程依赖登记

| # | 依赖 | 状态 | 处理 |
|---|---|---|---|
| **X-1** | Python 侧 `MAX_DIRECTION_CANDIDATE_POOLS` 的口径注释 | **已闭合**（2026-11-24 实测） | Python 侧 `flashloan/src/core/db/direction_pool_score.py:34-39` **已**是「容量旋钮，不是准入阈值」＋ 环境变量 `FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`（默认 **5**），`flashloan/app_runtime/run.py` 也已 `setdefault` 成 `"5"`；Rust 侧 `flashloan/rs-src/src/business_builder.rs:15-17` 同名同默认。**本任务新增的 `directional_pool_manager` 侧沿用同一变量名与同一默认值** ⇒ 三侧一致，**本任务无需再改 Python 侧** |
| **X-2** | 「内核对象一次性存储」 | 由其它线程处理 | 本任务**不碰** `direction_pool_score.py` / `builders.py` / `storage_market_observation.py` |
| **X-3** | 方向层排序主序的「并列时比容量」 | 本任务补（**已闭合**） | Rust 侧 `assign_ranks` 已补；**实盘 dry-run 校验和逐字节不变**（§6.4）⇒ 零影响。Python 侧若也排序，需同步同一条规则（**登记，不在本任务范围**） |

---

## 6. 实盘读数（2026-11-24 · `flashloan/db/local_store_4663.sqlite3`）

### 6.1 切换前后（逐行等价）

| 读数 | 切换前 | 切换后 | 判定 |
|---|---:|---:|---|
| 闭环数 | **168** | **174** | **+6**（**全部新增，0 移除**） |
| 跳数 | **504** | **522** | +18（6 × 3 跳） |
| `optimizations` 行数 | **456** | **472** | +16（**0 移除**） |
| `optimizations` 状态分布 | `candidate` **456 / 456** | `candidate` **466** ＋ **`temporary` 6** | **流转行数 ≠ 0** ✅ |
| `hops.temporary_override` | **0 / 504** | **0 / 516** ＋ **`1` / 6** | **顶替生效 6 跳** ✅ |
| 跳过行数 | **1110** | **1100** | **−10、0 新增**（单调减） |
| `closed_loop_version` | **18** | **19** | 版本 +1 |
| `materialized_checksum` | `sha256:19168935c99b…` | `sha256:6927d8d67b2c…` | — |

**逐行等价比对**（`compare_closed_loop_rows_20261124.py`，切换前备份 vs 切换后实盘）：

| 表 | 行数 | 移除 | 新增 | **共有行的差异列** |
|---|---|---:|---:|---|
| `manager_v4_closed_loops` | 168 → 174 | **0** | **6** | **`{}`（无差异）** ✅ |
| `manager_v4_closed_loop_hops` | 504 → 522 | **0** | **18** | **`{}`（无差异）** ✅ |
| `manager_v4_closed_loop_optimizations` | 456 → 472 | **0** | **16** | **状态改动 0 行** ✅ |
| `manager_v4_closed_loop_skipped_tokens` | 1110 → 1100 | **10** | **0** | — |

⇒ **现有 168 条闭环逐行不变**（容量、绑定池、借款代币估值、三跳的池与方向**全部 0 差异**），
**变更单调增**（无移除），**被移除的 10 条跳过行**全部是**被救回路径自己的跳过行**。

### 6.2 被救回的 6 条路径（**接线生效的直接证据**）

6 条路径**全部**是「第三跳主选池单跳容量为 0 ⇒ 旧代码整条 `continue`」的路径：

| 闭环（锚点:Y:Z） | 阻断池（主选） | 顶替池（生效） | `optimization_id` |
|---|---|---|---|
| `0x5fc5…:0x0000…:0xd060…` | `0x0b7651084eb6…` | `0x319bac87e616…` | `third-rank-2` |
| `0x5fc5…:0x0000…:0xd060…` 之外 5 条 | `0x84e2847d2fe2…` | `0x3bb34a44f1b2…` | `third-rank-2` |

**跳行自证**（`pool_id` ≠ `base_pool_id` ＋ `temporary_override = 1` ＋ `optimization_id` 非空）：

```text
hop_index = 3  hop_name = zc
pool_id            = 0x3bb34a44f1b2b5f32c034c38a53065a521a47b199700fa9bd19d60985ff24bf1   <- 生效池
base_pool_id       = 0x84e2847d2fe2dab492ca065326efa51b6f7e0924f200f983b9939530834df815   <- 原推荐池
optimization_id    = third-rank-2
temporary_override = 1
```

⇒ **「容量不过时下一个候选被选中的对照读数」已取得**（裁定 §3.2 的触发条件**已被真实触发**）。

### 6.3 接线用的是**哪一层**的备用（**重要区分**）

6 条被救回的路径用的 `optimization_id` 是 **`third-rank-2`**、`optimization_basis` 是
`direction_single_tick_exact_output_then_tick_fee_pool_id` ⇒ 顶替池来自**候选层**（候选边推荐表的第 2 名），
**不是**方向层的备用层。

**方向层的备用层今天为空**（§2.2 订正：真组键下**没有**任何组越过 `N_max` = 5）⇒
**今天被救回的路径证明的是「候选层内的第 2 候选」这条通路**，
**备用层通路只有单测覆盖**（`a_backup_of_the_direction_layer_takes_over_a_hop_the_candidate_list_could_not_serve`），
实盘读数**待未来某组越过 `N_max` 时**自然出现。

### 6.4 方向层（M-3）：**零影响**已实测

方向层对实盘库跑 **dry-run**（`materialize_directional_pools.exe`），与库内已发布状态**逐字段相等**：

| scope | 行数 | 物化校验和 | 与库内一致？ | `changedRows` | `consumersNeedRebuild` |
|---|---:|---|---|---:|---|
| `core4` | **340** | `sha256:5a609cafc9bd…` | **是** | **0** | **false** |
| `candidate_exchange` | **352** | `sha256:f6836f97aa22…` | **是** | **0** | **false** |

⇒ **「补容量并列键」对现有序位零影响**（主序键零并列 ⇒ 补键不改变任何一行）。

### 6.5 分层不变量（实盘实测）

`probe_directional_tiering_20261124.py`（真组键 `(scope, pair, direction)`，**222** 组）：

| 不变量 | 读数 |
|---|---|
| `N == min(N_max, 合格池数)` | **0 例外** |
| **不丢池**：`合格池数 == 候选数 + 备用数` | **0 例外** |
| **序位连续**：`1..K` 无缺号 | **0 例外** |
| 主序键并列的组 | **0** |

### 6.6 回滚演练（**已演练**）

| 步 | 动作 | 读数 |
|---:|---|---|
| 1 | 备份实盘库 | `local_store_4663.sqlite3.bak_tfangbi_20261124`（**8.2 GB**，含 `-wal` / `-shm`） |
| 2 | 把备份**还原**到演练库 | 读数**逐行回到切换前**：**168 / 504 / 456 / 1110**，`optimizations` 全 `candidate`，`temporary_override` **0 / 504**，`closed_loop_version = 18`，校验和 `sha256:19168935…` ✅ |
| 3 | 对还原库重跑**新**二进制 | **174 / 522 / 472 / 1100**，`temporary` **6**，`temporary_override` **6**，校验和 `sha256:6927d8d6…` —— 与影子库**完全一致** ✅ |

⇒ **回滚可用**（还原备份 ⇒ 读数回到切换前），**重放可用**（重跑新二进制 ⇒ 落到同一读数，**幂等**）。

### 6.7 影子库 vs 实盘（**最终态一致**）

| 项 | 影子库 | 实盘库 | 一致？ |
|---|---|---|---|
| 闭环 / 跳 / 优化 | 174 / 522 / 472 | 174 / 522 / 472 | ✅ |
| `materialized_checksum` | `sha256:6927d8d67b2c…` | `sha256:6927d8d67b2c…` | ✅ |
| `optimizations` 状态分布 | 466 `candidate` + 6 `temporary` | 同 | ✅ |
| `temporary_override` | 516 × 0 + 6 × 1 | 同 | ✅ |

### 6.8 未闭合项（**如实登记**）

| # | 项 | 归属 | 说明 |
|---|---|---|---|
| **U-1** | **`expired` 状态实盘读数为 0** | 本任务内 | 实盘被救回的 6 条路径里，被试过而未过门禁的那个池是**候选层的主选**（`optimization_id` 为空）⇒ 不产生 `expired` 行。`expired` 的路径**只有单测覆盖**（`an_optimization_that_failed_the_gate_is_recorded_as_expired`）。实盘读数**待某跳出现「第 2 候选也不过门禁」时**出现 |
| **U-2** | **方向层备用层实盘读数为 0** | 本任务内 | 真组键下**没有**任何组越过 `N_max` = 5（§2.2 订正）⇒ 备用层今天为空。通路**只有单测覆盖** |
| **U-3** | **老化淘汰 / 交易事件触发**（裁定 §6.2 第 6/7 层） | **`M-5`（`T-候储.01`）** | 依赖「最近一周有交易」新数据源，**本任务留占位不实现** |

> **不冒充**：U-1 / U-2 的**实盘读数为 0** 是**事实陈述**，不是「已闭合」。
> 它们的**通路已由单测证明**，**实盘读数待数据形态出现**。

---

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方闭.01/docs/README.md` -->

# `T-方闭.01`｜备用池接线 · 技术文案（docs）

> **本目录是 `T-方闭.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

容量门禁失败时的备用池选择与随闭环物化写出

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d086) | 本任务相关的技术文案 |
| [`02-目标架构与表结构.md`](#d087) | 本任务相关的技术文案 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
