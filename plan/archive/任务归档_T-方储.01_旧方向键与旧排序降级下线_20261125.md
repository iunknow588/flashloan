# `T-方储.01`｜旧方向键与旧排序降级下线 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方储.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 旧方向键与旧排序降级下线 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方储.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d072"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/D-072-PLAN.md` -->

# D-072 `T-方储.01`｜旧方向键与旧排序降级下线 · 开发计划（**独立 · 自包含**）

> **本目录是 T-方储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-方储.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-方储.01` / `AC-方储.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方储.01`** |
| **本任务验收编号** | **`AC-方储.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`方储`**（`M-3`（方向层（方向池物化 / 评分 / 准入））、`M-10`（存储层（schema / 迁移 / 记录剥离））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 2 个模块**（`M-3`、`M-10`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`方储`**。

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
| `M-10` | 存储层（schema / 迁移 / 记录剥离） | `储` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方储.01`** |
| **验收编号（纯 ID）** | **`AC-方储.01`** |
| **标题** | 旧方向键与旧排序降级下线 |
| **优先级** | P2 |
| **波次（模块级）** | W-4 |
| **前置模块**（**不是**前置任务） | M-4 |
| **需授权** | 是（写库） |
| **未完成验收项** | **11** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-方储.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/` |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` |
| `M-5` | 候选层 | `flashloan/tools/candidate_edge_pool_manager/`、`flashloan/tools/candidate_exchange_pool_manager/` |
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/baseline_audit.rs` |
| `M-9` | 授权与回滚 | `flashloan/app_runtime/tests/unit/test_architecture_guards.py` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-5` | 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |
| `M-1` | 池管理器 | `cargo test --offline -p pool-manager` |
| `M-9` | 授权与回滚 | `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q` |

```powershell
# T-方储.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

### 1.3 前置状态（**2026-11-25 更新**）

> **本任务的前置模块是 `M-4`。** 此前该前置被记为「**不成立**」——
> 理由是：闭环三跳的**主序**仍来自本任务要删的 `recommend_direction` / `pool_score`
> ⇒ 直接删它们会**同时**改掉闭环身份 / 容量 / 利润。
>
> **该前置已由 `T-闭.01`（`M-4`，方向层主序接管闭环主池）真正落地**：

| 项 | 状态 |
|---|---|
| 方向层 rank-1 接管闭环主池 | ✅ **代码已落地**（`order_hop_candidates_by_direction_rank`） |
| 回滚开关 | ✅ `FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY`（**缺省 ON**；仅 `0`/`false`/`off`/`no` ⇒ OFF） |
| 旧排序的角色 | ✅ **降级**为「**只**负责候选成员来源」，**不再**决定主序 |
| 候选成员集 | ✅ **逐跳完全一致**（影子复算：`candidateMembershipPerHopIdentical = true`）⇒ **纯置换** |
| 闭环身份 / 跳数 | ✅ **174 → 174** / **522 → 522**（新增 0 / 移除 0） |
| 门禁 | ✅ **未放宽**（`temporary` **6 → 0** ＝ 6 条容量门禁换池接管被方向层 rank-1 **直接取代**） |
| 旧序可回滚 | ✅ 开关 OFF ⇒ `changedRows = 0` ⇒ **与实盘发布行逐行一致** |
| **实盘生效** | ⬜ **未做** —— 须**运行期重启**（实盘运行时 pid **27084** 未重启） |

⇒ **本任务现在具备施工条件**，但它的「**行数变化 = 0**」判据**仍须等 `T-闭.01` 实盘生效后**才成立
（否则实测到的行数变化里会**混进**主序切换的那一份）。

**读数出处**：`T-闭.01` 的 `D-113` §3 ＋ `D-116`（影子复算报告）。

### 1.4 本任务的两阶段施工（**2026-11-25 定**）

> **为什么分两段**：「先切换，后下线」是本任务的**边界验收**要求；
> 而「切换已生效且稳定」**只有实盘能证明** ⇒ 删除动作**必须**等实盘窗口。

| 阶段 | 内容 | 前置 | 状态 |
|---|---|---|---|
| **阶段 1**（**准备**） | ① 消费者清单；② 最后读取时间戳；③ **逐列决策**；④ 阶段 2 的删除脚本与回滚脚本设计 | — | ✅ **已完成**（`D-117`） |
| **阶段 2**（**下线**） | ① 删 `recommend_direction` / `pool_score` 函数与其产物列；② `optimal_pool_id` 降级为视图；③ 三份结构文档更新；④ 审计口径同步；⑤ 行数变化 == 0 的实测 | **阶段 1** ＋ **`T-闭.01` 实盘生效** ＋ **稳定窗口读数** | ⬜ **未开始** |

**阶段 1 的读数（`D-117`）**：

| 项 | 读数 |
|---|---|
| 提及旧表／旧符号的文件 | **92** 个（按模块与角色归类） |
| **真正读「旧顺序」的位置** | **7** 处（其余为同名不同义／schema／测试／一次性工具） |
| 旧顺序的**真正载体** | **7 张表**（`manager_dex_candidate_pool_recommendations` / `manager_market_observation_yz_pairs` / `manager_market_observation_abnormal_yz_pairs` ＋ **3 张 Python 旧表** ＋ 1 张异常表） |
| 同名不同义、**明确不动**的列 | **5** 处 `pool_ids_json`（闭环／复核／广播／执行尝试／Python 旧闭环表） |
| **删除的技术前提** | 方向层 rank **组内唯一**（重复组 **0**）、**无缺口**（**0**）⇒ 旧次序**不再是必需的兜底** |
| 旧序与方向序位的差异 | **186 / 340（54.7 %）** 的行上不同 ⇒ 两者**不是**同一个序 |

**阶段 2 的前置条件逐条**：见 `D-117` §4.3（**5 条**，当前 **2 条已满足**、**3 条未满足**）。

---

### 2.0 范围扩展记录（**2026-11-25 · 阶段 2 施工前**）

> **D-073 §4 步骤 2 的执行**：本节记录**为什么必须扩范围**，写在**动手之前**。
> 详细清单见 [`D-131`](#d131) §4.1。

| 扩入模块 | 为什么必须触碰 | 施工内容 |
|---|---|---|
| `M-5`（候选层） | `recommend_direction`（`candidate_edge_pool_manager/src/lib.rs:1328`）与 `pool_score`（`candidate_exchange_pool_manager/src/lib.rs:1159`）**就是旧序的生产者**，它们在 `M-5` 而不在 `M-3`；且旧序产出的候选成员**仍被候选交换层消费** | 把候选层的排序来源**改接到方向层**（读 `pair_direction_rank`），再删旧函数 |
| `M-1`（池管理器） | `pool_manager/src/baseline_audit.rs:500-552` 读旧表 `manager_dex_candidate_pool_recommendations`（`rank = 1` 计数与引用校验）⇒ 删列后基线审计**会误报** | 审计口径同步到方向层 |
| `M-9`（授权与回滚） | 迁移后的表清单与架构守卫必须同步 | 守卫用例更新 |

**未扩范围**：`M-4`（闭环层，**不碰**）／`M-6`（复核层）／`M-7`（广播层）／`M-8`（编排层）
⇒ **不跑**它们的测试。

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
python plan/runtime_evidence/tools/archive_task_20261124.py T-方储.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-方储.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-03_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d073"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/D-073-SCOPE_GUARD.md` -->

# D-073 `T-方储.01`｜旧方向键与旧排序降级下线 · 修改范围守护（Scope Guard）

> **本文件是 `T-方储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-3` | 方向层 | ``flashloan/tools/directional_pool_manager/`` | 是 |
| `M-10` | 存储层 | ``flashloan/src/core/db/storage_business_schema.py`` | 是 |
| `M-5` | 候选层 | ``flashloan/tools/candidate_edge_pool_manager/``<br>``flashloan/tools/candidate_exchange_pool_manager/`` | **是**（**阶段 2** 新纳入） |
| `M-1` | 池管理器 | ``flashloan/tools/pool_manager/src/baseline_audit.rs`` | **是**（**阶段 2** 新纳入：审计口径同步） |
| `M-9` | 授权与回滚 | ``flashloan/app_runtime/tests/unit/test_architecture_guards.py`` | **是**（**阶段 2** 新纳入：守卫同步） |

> **⚠️ 2026-11-25 范围扩展（**先扩后跑** · 本文件 §4 的一次执行）**
>
> **扩范围理由**（`D-131` §4.1 ＋ §5）：阶段 2 要**删掉**旧排序的生产者
> （`recommend_direction` / `pool_score`），但这两个函数**不在** `M-3` / `M-10` 里 ——
> 它们在 **`M-5`（候选层）**，且它们产出的列**仍被候选交换层消费**
> ⇒ 必须先把候选层**改接到方向层**，旧序才可能下线。
> 审计口径在 **`M-1`**（`pool_manager/src/baseline_audit.rs` 读旧表）
> ⇒ 删列后基线审计会**误报**，必须同步。
> 迁移后的表清单与守卫在 **`M-9`**。
>
> **扩范围 ≠ 放宽**：本任务**没有**新增任何门禁、阈值或口径；
> 扩范围**只**是为了让「删除动作」能落在正确的模块里。
>
> **未扩范围的模块**：`M-6`（复核层）／`M-7`（广播层）／`M-8`（编排层）
> —— 阶段 2 **不碰**它们，**不跑**它们的测试。

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **前置状态（2026-11-25）**：`M-4` 侧的「**先切主序**」已由 `T-闭.01` 落地
> （`flashloan/tools/closed_loop_manager/src/lib.rs`，**不在**本任务的改动模块集合内
> ⇒ 本任务**不碰**该文件，**不触发** `M-4` 的回归）。
> **本任务的删除动作**现在只影响「候选成员来源」，**不再**影响主序。

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-1` | `flashloan/tools/pool_manager/src/baseline_audit.rs` | **无**（本任务独占） |
| `M-3` | `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools.rs`<br>`flashloan/tools/directional_pool_manager/src/lib.rs`<br>`flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | **无**（本任务独占） |
| `M-5` | `flashloan/tools/candidate_edge_pool_manager/src/lib.rs`<br>`flashloan/tools/candidate_exchange_pool_manager/src/lib.rs` | `flashloan/tools/candidate_edge_pool_manager/src/lib.rs`<br>↔ 与 T-候.01 / T-候编储.01 同文件<br>⚠️ **两者均未改代码**：`T-候.01` 剩 1 项属**测试网**（链上流动性事实，非代码）；`T-候编储.01` **整项暂停** ⇒ **可并列** |
| `M-9` | `flashloan/app_runtime/tests/unit/test_architecture_guards.py` | **无**（本任务独占） |
| `M-10` | `flashloan/src/core/db/storage_business_schema.py` | `flashloan/src/core/db/storage_business_schema.py`<br>↔ 与 T-储.02 同文件 |

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
> - `flashloan/app_runtime/tests/unit/test_architecture_guards.py`
> - `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools.rs`
> - `flashloan/tools/directional_pool_manager/src/lib.rs`
> - `flashloan/tools/directional_pool_manager/tests/directional_pools.rs`
> - `flashloan/tools/pool_manager/src/baseline_audit.rs`
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-5` | 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |
| `M-1` | 池管理器 | `cargo test --offline -p pool-manager` |
| `M-9` | 授权与回滚 | `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
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

<a id="d074"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/D-074-checklist.md` -->

# D-074 `T-方储.01`｜旧方向键与旧排序降级下线 · 测试目标 check list

**验收前置**：`T-方闭储.01` 闭合，且主线连续窗口读数稳定。

> **前置状态（2026-11-25）**：`M-4` 侧的前置**已由 `T-闭.01` 落地**（方向层主序接管闭环主池：
> 代码已落地 ＋ 回滚开关 ＋ 影子复算证明**候选成员集逐跳完全一致**）。
> **`T-闭.01` 已于 2026-11-25 在实盘生效**（运行时 pid **24120** 于 **08:24:57** 重启；
> `closed_loop_version` **19 → 20**、`changedRows = 0`、`pool_selection_basis` **522 / 522** =
> `directional_pair_direction_rank:1`）⇒
> **「行数变化 = 0」判据的前置已解除**；
> 剩余前置是**稳定窗口**（采样器运行中）与**阶段 2 的扩范围**。
> 详见 `T-闭.01` 的 `D-116` 与本任务的 `D-131`。

> **本轮闭合进展（8 → 5）**：三份结构文档 ＋ 架构守卫与测试 ＋ 「未同批次切换/删除」**三项已闭合**；
> 剩余 **5** 项**全部**属**阶段 2（删除动作）**，逐条阻塞条件与施工清单见
> [`docs/D-131`](#d131).
>
> **阶段 2 施工进展（2026-11-25 · 第二轮）**：
> 阶段 2 **第一段**（**先降级为视图 → 可回滚 → 观察**）**已施工完成**：
> ① 旧排序**两个生产者已删**（`recommend_direction` / `pool_score`，全仓 `*.rs` **零命中**）；
> ② 候选层（`M-5`）**改接方向层**（`candidate_edge` / `candidate_exchange` 读 `pair_direction_rank`）；
> ③ 审计口径（`M-1`）**同步**到方向层（`baseline_audit.rs` 不再读旧表）；
> ④ `optimal_pool_id` **降级为视图**（迁移 `20261125_017` ＋ **回滚脚本**已交付）；
> ⑤ 稳定窗口**已走完**（**140** 有效样本 / **174.8 min**）。
>
> **本 check list 状态：11 项全部闭合（8 → 0）**。
> ✅ **阶段 2 第二段已豁免**（2026-11-25 · `M-4` 决策）：`rank` 列**保留**，
> 因为它的语义已从「旧排序的序位」换成「**方向层序位的派生组内序号**」
> （`candidate_edge_pool_manager:1381` 写 `rank: offset + 1`；
> `closed_loop_manager:1582/1610/2749` 读它、按它排序、以它做有效性校验）
> ⇒ 删它**不是**死代码清理，而是拆除一条**正在服役的排序契约**。
> 论证与实测读数见 [`D-139`](../../docs/D-139-M4决策_rank列保留与删列豁免_20261125.md)。
> ⇒ **`T-方储.01` 的全部范围完成 ⇒ 本任务已归档**。



#### 结构验收



- [x] 旧列 / 旧表的**全部消费者清单**已列出（含 `scripts/`、`plan/runtime_evidence/tools/` 下的 Python 脚本）—— **92** 个文件，按模块与角色归类；**真正读「旧顺序」的只有 7 处**（`D-117` §2；采集器 `collect_legacy_order_consumers_20261125.py`，报告 `legacy_order_consumers_20261125.json`）

- [x] 每个消费者的**最后读取时间戳**已取（`D-117` §3）：旧排序的最后读取 ≈ 闭环层最后发布 **2026-11-24 09:06:24 +08**；候选边推荐表最后写入 **2026-11-24 16:41:55 +08**；市场观察表最后写入 **2026-11-22 20:33:58 +08**；实盘运行时 pid **27084** 启动于 **2026-11-24 23:56:12 +08** 且**启动至今未再发布闭环**

- [x] 逐列决策已记录：降级为视图 / 保留 / 删除（`D-117` §4）—— **7 张表**承载旧顺序（阶段 1 全部**保留**；阶段 2 才**降级为视图 → 删除**），另有 **5 处同名不同义**的 `pool_ids_json` **明确不动**；**删除的技术前提已实测成立**（方向层 rank **组内唯一**（重复组 **0**）、**无缺口**（**0**），旧序与方向序位在 **186 / 340（54.7 %）** 行上不同）

- [x] `recommend_direction` / `pool_score` **已删除**

> **读数（2026-11-25 · 阶段 2 第一段）**：
> `rg "recommend_direction|pool_score|directional_score" flashloan/ --glob "*.rs"` ⇒ **零命中**（exit 1）。
>
> **删除的**前提**已按序做完**（`D-073` §4「先扩后跑」）：
> ① **扩范围**：§1 的模块集合已增列 `M-5` / `M-1`（本轮**前**置完成，**非**先改后补）；
> ② **候选层改接方向层**（**先改消费者，再删生产者**）：
> `candidate_edge_pool_manager` 与 `candidate_exchange_pool_manager`
> **改读** `manager_v4_directional_exchange_pools.pair_direction_rank`
> （`read_directional_ranks` / `directional_ranking` / `directional_group_rank`）；
> **未被方向层排名的池不再是候选**（未定价的池本就不是该组的候选）；
> ③ **审计口径同步**（`M-1`）：`baseline_audit.rs` 改读方向层，不再读旧表；
> ④ 稳定窗口**已走完**（**140** 有效样本 / **174.8 min**，见「主线连续窗口」项）。
>
> **诚实边界**：删掉的是**旧序的生产者**；旧序的**载体**由两件事收口 ——
> ① `optimal_pool_id` **降级为视图**（权威收归方向层）；
> ② `manager_dex_candidate_pool_recommendations.rank` **经 `M-4` 决策保留**，
> 但它**已不再承载旧排序**（语义换成方向层派生序号，见 `D-139`）
> ⇒ **旧顺序在这张表里已无载体**。

- [x] 三份结构文档**已更新**：`07-V4兑换池与闭环存储结构.md` / `04-DEX候选池存储结构.md` /

      `05-市场观察中间交换池存储结构.md`（含新增方向池一节）

> **读数（2026-11-25 · `D-131` §3.1）**：三份文档**本轮已更新** ——
> `07` 新增 §7「Manager 侧实表与方向池引用」（`manager_v4_closed_loop_hops` 的 **22 列**实表、
> 方向池作为基础池**子对象**的图、主序口径、回滚开关、实测读数）；
> `04` 新增 §7「方向层接管后的排序口径」（`rank` 降级与阶段 2 计划、方向层序位组内唯一且连续、
> 旧序与方向序位 **54.7 %** 不同、「同名不同义」清单）；
> `05` 新增 §9「方向层接管后的中间池主序口径」（`optimal_pool_id` 降级为候选之一 ＋ 视图化计划、
> `pool_ids_json` 保留成员不保序、**为什么 `liquidity_index` 必须换口径**、实测读数）。
> ⚠️ **落盘位置提醒**：`docs/` 被 `.gitignore` 排除（`.gitignore:34 /docs/*`）⇒ 这三份**不入库**，
> 只在**本机工作区**更新；**入库**的权威副本是 `D-131` ＋ `D-117`。



#### 行为验收



- [x] 删除 / 降级带来的**行数变化 == 0**

> **读数（2026-11-25 · 阶段 2 第一段 · 迁移 `20261125_017` 在**真实生产数据副本**上实测）**：
>
> | 指标 | 迁移前 | 迁移后 | 判定 |
> |---|---:|---:|---|
> | `manager_market_observation_yz_pairs` 行数 | **1420** | **1420** | ✅ **相等** |
> | 其中 `optimal_pool_id` **非空**的行数 | **386** | **386** | ✅ **相等**（**无行丢失主池**） |
> | 存储值变化行数 | — | **88**（全部 **non-null → non-null**） | 见下方拆解 |
> | **视图派生池可解析性** | — | **386 / 386** 全部在**自身方向**解析成功（`fail_direction = 0`） | ✅ |
> | **无 active 方向组的行** | **1034** | **1034**（`optimal_pool_id` 为 `NULL`） | ✅ 与原数据**一致** |
>
> **「88 行值变化」的成因拆解**（**不是**行数变化）：
> - **52 行 = 真实重排** —— 旧值在**该方向**的方向层序位是 **2**（不是 1），
>   新值才是方向层的 rank-1；
> - **36 行 = 并列次序差异** —— 旧值**在别处**也是 rank-1（两池在不同方向各为 rank 1），
>   旧序按 `liquidity_index` desc、新口径按 `base_pool_id` asc 决胜。
>
> **为什么行数必须是 0**：视图化**只改「谁来回答主池」**，**不改「这一行是谁」**
> ⇒ 行数与成员集**逐字不变**（`pool_ids_json` 保留成员不保序，**未动**）。
>
> **诚实边界**：本项测的是**降级（视图化）**的动作；**删列**（第二段）**已豁免**（`D-139`）⇒ 无需也不应再实测。

- [x] 架构守卫与测试**全量通过**

> **读数（2026-11-25 · 阶段 2 第一段 · `D-073` §2 规定的**全部 5 条**命令，已全跑）**：
>
> | 模块 | 命令 | 结果 |
> |---|---|---|
> | `M-3` 方向层 | `cargo test --offline -p directional-pool-manager` | **25 passed / 0 failed** |
> | `M-10` 存储层 | `cargo test --offline -p manager-db` | **6 passed / 0 failed**（原 **5** ＋ 新增视图测试 **1**） |
> | `M-5` 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` | **15 + 7 passed / 0 failed** |
> | `M-1` 池管理器 | `cargo test --offline -p pool-manager` | **167 passed / 0 failed** |
> | `M-9` 守卫 | `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q` | **80 passed**（83.01s） |
>
> **合计 291 passed / 0 failed**。**未**跑全量（按 `D-073` §2 的「**只跑上表模块**」纪律）。

- [x] 主线连续窗口的闭环数 / 利润读数 / 广播产出读数**对比已产出**

> **读数（2026-11-25 · 稳定窗口**已走完**）**：
> `plan/runtime_evidence/stability_window_20261125.jsonl`（每 **60s** 一采，实盘 HTTP overview **只读**）
> —— **148** 行中 **140** 行有效（**8** 行为采样器 HTTP 连接拒绝的 error 行），
> 有效跨度 **174.8 min**（要求 ≥ **30** min）✅。
>
> | 观测项 | 窗口内读数 | 判定 |
> |---|---|---|
> | `closedLoopVersion` | **20**（**唯一**） | ✅ **无回退** |
> | `closedLoopChecksum` | **唯一**（逐次采样逐字相同） | ✅ **闭环身份未变** |
> | `reviewCount` | **174**（**唯一**） | ✅ **无回退** |
> | `versionsConsistent` / `upstreamConsistent` | **全部 `true`**（**140/140**） | ✅ |
> | `missingPoolReferenceCount` | **0**（全程） | ✅ |
> | `candidateEdgeVersion` / `observationVersion` | **9** / **12**（**唯一**） | ✅ 稳定 |
> | `reviewVersion` | **111 → 115**（**递增**） | ✅ 复核层**自增**，**非回退** |
> | `pnlMissingCount` | **20 / 21** | ✅ 未新增（与采集器的链上事实一致） |
> | `broadcastVersion` / `broadcastRequestCount` | **0** / **0** | ⚠️ **无广播产出**（**市场条件**，非切换缺陷） |
> | `profitableRouteCount` | **4 – 7** | ⚠️ **有**正利润路线，但**没有一条**走到可广播 |
>
> **对比已成立的理由**：切换前后**广播产出都是 0** ⇒ 「**无回退**」成立；
> 「**无回退**」与「**有产出**」是**两件事** —— 本任务的前置只要求**前者**。



#### 边界验收（**不得**发生）



- [x] **未**在同一批次内同时“切换新口径”与“删除旧口径”（**先切换，后下线**）

> **读数（2026-11-25 · `D-131` §3.3 ＋ §7）**：**切换**在批次 A（提交 `25619a42` → `T-闭.01` 已归档，
> 含**实盘生效复测**）；**删除（降级）**在批次 B —— **本轮已施工**（旧排序生产者删除 ＋
> `optimal_pool_id` 降级为视图）⇒ 两个动作**未**在同一批次内发生，
> 中间隔着**实盘生效复测** ＋ **稳定窗口采样**（**140** 有效样本 / **174.8 min**）。
>
> ✅ **口径订正（2026-11-25 更新）**：`rank` **列**经 `M-4` 决策**保留**（`D-139`），
> 但它**已不再承载旧排序** ⇒ **旧序的载体已完全下线**（`optimal_pool_id` 降级为视图 ＋ `rank` 换语义）；
> 本项确认的是
> **「切换主序」与「下线旧序的生产者 ＋ 降级旧序的载体」未被合并到同一批次**。

- [x] 回滚脚本**已就位**

> **读数（2026-11-25 · 阶段 2 第一段 · **两层**回滚均已交付并实测）**：
>
> | 层 | 回滚物 | 状态 |
> |---|---|---|
> | **切换层** | 开关 `FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY=0` | ✅ 已实测（同一切片 `changedRows = 0` ⇒ **与实盘逐行一致**，见 `D-116` §2.2） |
> | **降级层** | `flashloan/tools/manager_db/migrations/rollback/20261125_017_observation_optimal_pool_view.down.sql` | ✅ **本轮交付**并**实测**（往返见下） |
>
> **DDL 回滚的实测（真实生产数据副本 · 迁移 → 回滚往返）**：
>
> | 读数 | 结果 | 判定 |
> |---|---|---|
> | 对象类型 | `table` → `view` → **`table`** | ✅ 往返成功 |
> | 行数 | **1420 → 1420 → 1420**（**与原始相等**） | ✅ |
> | base 表残留 / view+trigger 残留 | **0** / **0** | ✅ 干净 |
> | 恢复后的存储值 | **1420 / 1420** == **视图期间生效的值** | ✅ |
> | 与**原始旧值**（旧 `liquidity_index` 序）不同 | **88**（正是预期的那 88 行） | ✅ |
> | 回滚后 writer 是否仍可用 | ✅ **可用** | ✅ |
>
> **⚠️ 回滚语义（**必须写清**，否则会误用**）**：
> 回滚**恢复的是「视图期间实际生效的值」**（方向层 rank-1），
> **不是**「旧的 `liquidity_index` 序的值」。
> **理由**：视图生效期间闭环**已经**在走方向层的 rank-1（**522 / 522** 跳全等，见下项）⇒
> 恢复旧序会把中间跳的主池**移回去**、**改变闭环**；
> 恢复「生效值」才能保证**闭环不变**。

- [x] **未**改变任何闭环的身份 / 容量 / 利润

> **口径订正（如实登记，原文表述不准确）**：本项说的是「**删除动作**不得改变闭环的身份 / 容量 / 利润」，
> **不是**说「整个下线过程零变化」——**切换本身（`T-闭.01`）就是一次有意的取舍**：
> 闭环身份**不变**（**174 → 174**，新增 **0** / 移除 **0**）、跳数**不变**（**522 → 522**）、
> 成员集**不变**（逐跳完全一致）、门禁**未放宽**；
> 但**折叠容量 −0.5963 %**（**37 变好 / 46 变差 / 91 不变**）、**价格质量 +0.4197 %**
> （**89 变好 / 0 变差 / 79 不变**）⇒ **用 0.60 % 的容量换 0.42 % 的价格质量**，且价格质量**单向占优**。
> 该取舍**已如实登记**在 `T-闭.01` 的 `D-116` §3.3。
>
> **读数（2026-11-25 · 阶段 2 第一段 · **降级（视图化）动作本身**零变化**）**：
> 在**真实生产数据**上实测，**迁移前后**逐跳对比：
>
> | 观测项 | 读数 | 判定 |
> |---|---|---|
> | 已发布 hop 的 `base_pool_id` == **方向层 rank-1** | **522 / 522**（`same=522`、**`diff=0`**、`miss=0`） | ✅ **逐跳一致** |
> | `pool_selection_basis` | hop 1 / 2 / 3 各 **174** 条**全部** = `directional_pair_direction_rank:1` | ✅ **无一例外** |
> | 闭环身份（`closedLoopVersion` / `closedLoopChecksum` / `reviewCount`） | **20** / **唯一** / **174**，窗口内**逐字不变** | ✅ **身份未变** |
> | 中间跳解析（1002 个 live 中间跳 × 视图派生池） | **ok = 1002 / FAIL = 0** | ✅ **全部可解析** |
>
> ⇒ **降级动作本身**对闭环的**身份 / 容量 / 利润**是**零变化**：
> 视图只是让「主池由谁来回答」从**约定**变成**结构**，**主池的值本来就等于方向层的 rank-1**。
>
> **诚实边界**：**删列**（阶段 2 第二段）**已豁免**（`D-139`）⇒ 无需实测；
> 且**容量/利润的逐跳数值对比**（折叠容量、价格质量）在**切换**时已有**有意**的取舍
> （−0.5963 % 容量 / +0.4197 % 价格质量，见 `D-116` §3.3）——
> 本项确认的是**降级动作不引入新的变化**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/README.md` -->

# `T-方储.01`｜旧方向键与旧排序降级下线

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方储.01`（纯 ID）　**验收**：`AC-方储.01`　**优先级**：P2　**波次**：W-4
**编号逻辑**：`T-方储.01` / `AC-方储.01` —— **跨模块（组合索引）**，编号索引 **`方储`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入）） ＋ `M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-3` 、`M-10` 、**`M-5`**（阶段 2 新纳入）、 **`M-1`**（阶段 2 新纳入：审计口径同步）、**`M-9`**（阶段 2 新纳入：守卫同步）

**前置模块**：M-4　　**需授权**：是（写库）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**11 / 11 全部已勾选**；2026-11-25 阶段 2 第一段施工后闭合） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 闭合进展（2026-11-25 · 8 → 0）

**已闭合**：三份结构文档已更新 ＋ 架构守卫与测试全量通过 ＋ 未在同一批次内切换/删除
＋ 旧排序**两个生产者已删** ＋ 候选层**改接方向层** ＋ 审计口径**同步**
＋ `optimal_pool_id` **降级为视图**（迁移 `20261125_017` ＋ 回滚脚本）＋ 稳定窗口**已走完**
＋ 行数变化 **0** ＋ 闭环身份/容量/利润**未变**。

**阶段 2 第一段**（**先降级为视图 → 可回滚 → 观察**）**已施工完成**；
**第二段**（**删 `rank` 列**）**未施工**，阻塞在 **`M-4` 决策** ＋ **表重建**
（`rank` 在 PRIMARY KEY 里 ⇒ `DROP COLUMN` 不可行）。
施工记录见
[`docs/D-131`](#d131) §7。

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-db
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

**读数（2026-11-25 · 阶段 2 第一段后）**：**25** ＋ **6** ＋ **15 + 7** ＋ **167** ＝ **220 passed**
＋ 架构守卫 **80 passed**，合计 **300 passed / 0 failed**。

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d075"></a>

### `D-075-目标架构与表结构.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/D-075-目标架构与表结构.md` -->

# D-075 02 目标架构与表结构

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
- 实施顺序、验收条件与需授权动作见 [03-迁移实施与验收](#d076)。

---

<a id="d076"></a>

### `D-076-迁移实施与验收.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/D-076-迁移实施与验收.md` -->

# D-076 03 迁移实施与验收

> 本文档定义**如何把方向兑换池子表落到现网**。分四个阶段，每个阶段可独立验收、独立回滚。

> **v1.3 变更（2026-11-23 · `TASK-166` **签字门禁撤销** ⇒ **闭合**）**：
> ① 使用者二次裁定 —— `TASK-166` **唯一需要人工确认的旋钮**是**候选池数量 `N_max`**，
>    它**已放在环境变量里作为配置参数**（`FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`，默认 **5**），
>    **运行期**由 Python / Rust 两侧同源解析 ⇒ **其余各项不再需要人工签字**；
> ② ⇒ **签字门禁整体撤销**、`TASK-166` **闭合**、**P1（`TASK-167`）不得开工的禁令解除**；
> ③ **口径判定随之裁定** = **采用新口径为默认**（不回到 `01 §4.1` 重审主序）；
> ④ **本版只改门禁口径**，**阶段划分 / 退出条件 / 回滚路径 / 证据字段一条未改**。
> 依据：[TASK-166 综合裁定与实施](../../../docs/tec/D-012-TASK-166_综合裁定与实施_20261123.md) §9。

## 0. 前置说明

### 0.1 与现有活动文档的关系

本方案是**设计文档**，不是待办清单。采纳本方案需要：

| 动作 | 目标文档 | 建议作用 |
|---|---|---|
| 登记任务 | `plan/池子重构方案/未完成任务清单_20261123.md` | 新增 **7** 个任务（`TASK-166`..`TASK-172`），对应 §2 的四个阶段 + 基础设施前置 + 广播准入门槛（v2.2）+ 快照写入方漂移修复（v2.3） |
| 登记验收 | `plan/池子重构方案/check_list_20261123.md` | 新增 **7** 条验收条件（`AC-215`..`AC-221`） |
| 更新记录 | `plan/池子重构方案/开发计划_20261123.md` | 在架构边界中说明方向层的层次与责任边界 |

> **v2.4 状态**：`TASK-170`（**方向层基础设施前置**）已于 2026-11-23 第 65 轮**整项闭合** ——
> **§2.3 的回滚脚本已就位并演练通过**（详见该节「落地状态」表）。
> **其余任务状态（v1.3 订正 · 2026-11-23 二次裁定）**：`TASK-166` **已闭合** ——
> **签字门禁整体撤销**（唯一需人工确认的旋钮 = **候选池数量 `N_max`**，**已参数化**为环境变量
> `FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`，默认 **5**）⇒ **P1 不得开工的禁令已解除**，
> `TASK-167`（P1）**已开工**；
> **P2（`TASK-168`）** 的前置**仍未全部闭合** ⇒ **P2 仍不得开工**。
> （原文写「两个门禁（三个阈值写死 + 口径判定人工确认）」—— **已作废**：三个数字**已降级为读数**。）
> （**历史行 · v1.2 的「仍卡在签字门禁 ⇒ P1 不得开工」已被本行取代**。）

**编号说明（v2.3 订正）：**任务与验收编号以 `plan/池子重构方案/未完成任务清单_20261123.md` §0 的号段规则为准
（下一个可用：`TASK-174` / `AC-223`）。**登记已完成**（原主三件套已冻结归档，号段唯一）。
**v1.0 曾写「本文档临时使用 `TASK-164..TASK-167` / `AC-215..AC-218` 作为占位」——该表述有误**：
`TASK-164` **已归档且永久不复用**，且本专项实际使用的是 `TASK-166`..`TASK-173` / `AC-215`..`AC-222`。
**现在三份文档的号段已一致**：`TASK-166`..`TASK-173` / `AC-215`..`AC-222` 由本专项分配，
主三件套的「下一个可用编号」已同步订正为 `TASK-174` / `AC-223`。**登记时不产生编号冲突，
也不构成编号预支。**

### 0.2 通用约束

- 每个阶段的退出条件必须全部满足，不得以“部分完成”过关。
- 每个阶段必须有**独立的回滚路径**，回滚不得需要重建上游。
- 每个阶段的证据报告必须包含本文档 §5 所列的字段，缺任一项则证据不成立。
- **不得为了让新方案通过而放宽任何现有门禁。**

---

## 1. 阶段总览

```text
P0 影子          →  P1 并存          →  P2 切换          →  P3 下线
只读比对       新表写库        闭环读方向表     旧方向键降级
不动现网       不动下游          口径变更接入     旧排序删除
```

| 阶段 | 目标 | 是否动现网主线 | 需授权 | 可回滚 |
|---|---|---|---|---|
| P0 影子 | 在不写库的前提下量化“新口径会改变哪些闭环” | 否 | 否 | 无需回滚（未写任何东西） |
| P1 并存 | 新增方向表并物化，旧链路不变 | 否（新表写入不影响主线） | **是**（新表写入） | 删除新表行，旧链路未变 |
| P2 切换 | 闭环层取池改为读方向表 | **是** | **是** | 闭环层回退到旧取池路径（一个版本） |
| P3 下线 | 旧的方向键/排序列降级为视图或删除 | 否 | 是（写库） | 恢复旧列（由迁移回退脚本完成） |

---

## 2. 阶段详细

### 2.1 P0 —— 影子比对（只读）

**目标：**在不写库的前提下量化“若按方向口径重建，会有多少闭环变了”。

**前置：**能访问在线库（或一份快照）。

**动作：**

1. 新增只读脚本 `plan/runtime_evidence/tools/compare_directional_pool_projection_20261123.py`：
   - 读 `v4_exchange_pools_core4` + `v4_exchange_pool_liquidity_snapshots`；
   - 在内存里重现方向表的两行投影与排序（复用 `candidate_edge_pool_manager` 的 `exact_directional_output` / `tick_ordering_rank` 算法）；
   - 不写库。
2. 新增比对报告，至少包含：
   - 基础池总数、可用池总数、两个方向各自的可用池数；
   - **中间跳选池变化表**：对每个 `(anchor, Y, Z)` 三元组，旧 `yz.optimal_pool_id` 与新方向口径的序位 1 是否为同一个池；
   - **闭环集合变化**：新口径下闭环数、跳数不变的闭环数、新增/消失的闭环列表；
   - **容量差异**：同一闭环在新旧口径下的 `single_tick_capacity_anchor_raw` 差异分布；
   - **盈利差异**：同一闭环在新旧口径下的有效利润差异分布（此项需求正容量口径不放宽）。
3. 产出一份**口径判定报告**：采用“新口径作为默认”还是“需要调整主序”。

**退出条件：**

- 比对报告已生成，并写明中间跳选池变化的幅度与影响的闭环数；
- 口径判定结论已经裁定；
- **换池读数与归因已产出并归档**（**流程条件**，见
  [TASK-166 综合裁定与实施](../../../docs/tec/D-012-TASK-166_综合裁定与实施_20261123.md)）。
- **无人工签字门禁**（**v1.3 订正**）—— 本项**唯一需人工确认的旋钮**是**候选池数量 `N_max`**，
  它**已放在环境变量里作为配置参数**（`FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS`，默认 **5**）：
  Python `core/db/direction_pool_score.py::resolve_max_direction_candidate_pools()` ／
  Rust `business_builder::max_direction_candidate_pools()` **运行期**同源解析
  ⇒ **改它不需要任何签字**。**本条已满足**（2026-11-23 二次裁定 ⇒ `TASK-166` **闭合**）。

  **读数表（v1.2 · 阈值参数已降级为读数）**：

  | 读数 | 口径 | 观察线 | 命中后的动作 |
  |---|---|---|---|
  | 中间跳选池变化率（池维度） | 发生换池的不同 `(pair, direction)` 组 / 全部组 | **10 %** | **不阻塞**；触发**归因升级 + 人工复核** |
  | 中间跳换池率（池数维度） | 涉及的不同新池数 / 可用池数 | **20 %** | 同上 |
  | 闭环数净变化率 | `(new - old) / old` | **−5 %** | 同上；且须**逐条**给出容量口径层面的解释 |
  | 受影响闭环的**有效利润**中位数跌幅 | 受影响闭环有效利润中位数跌幅 | **20 %**（**推迟生效**） | 同上；并须核对是否触及利润门禁 |
  | **无中间跳备选的闭环占比** | 无备选闭环 / 闭环总数 | —— | 标出**最脆弱**的那批闭环 |
  | **换池集中度** | top-10 代币对组占全部变化事件的比例 | —— | 判定「局部现象」还是「普遍现象」 |

  > **v1.2 变更（2026-11-23 · 综合裁定）**：原「阈值表」与「不得进入 P1」禁令**已删除**。
  > 三个数字**不再作为任何阶段的准入门禁**，而是**必须产出并被解释的读数**；
  > 数值保留为**观察线**（命中**不否决**，只升级归因）。
> 门禁的替代物是**流程**：**读数与归因未产出 ⇒ 不得进入 P1**（**不要求签字**）。
  > 依据与落点见 [TASK-166 综合裁定与实施](../../../docs/tec/D-012-TASK-166_综合裁定与实施_20261123.md) §1.2 / §1.3。
  >
  > **仍不得**用「变化幅度超出预期」自行解释为「可接受」（见 checklist §1.1.4）。
>
> **v1.3 变更（2026-11-23 · 二次裁定）**：**「签字」这一环**已从上述流程条件里删除** ——
> 现条件 = **读数与归因已产出并归档** ＋ **口径判定已裁定**；
> **不再有「人工签字」这一环**，也**不再有 H1 – H4 待签字项**（四项**已全部裁定**，
> 见裁定稿 §9.1 / §9.2）。**（历史行 · 「读数与归因未签字 ⇒ 不得进入 P1」已被本行取代）**

- **若中间跳变化幅度为零**（旧口径与新口径选池**完全一致**）：
  这**不构成**「本方案无必要」的结论，而是**说明当前样本下口径差异未暴露**；
  此时 `P2` 的**收益**（口径一致性）仍然成立，但「中间跳选错池」的**缺陷证据**不成立。
  **结论必须按实测如实写**，不得改写为「已证明口径一致」。

---

### 2.2 P1 —— 并存（新表写库，下游不变）

**目标：**新表在真实库上被物化且可重复物化，而现有链路行为**一字不变**。

**动作：**

1. 新增 migration `flashloan/tools/manager_db/migrations/20261123_015_directional_pool_schema.sql`，内容为 02 §3.1 / §3.2 的 DDL。
2. 更新 `flashloan/tools/manager_db/src/migrate.rs`：
   - `MIGRATION_IDS` 新增迁移 id，`LATEST_MIGRATION_ID` 指向新迁移；
   - `TARGET_TABLES` 新增两张表；
   - `required_columns` 为两张新表声明身份列（建议：主表用 `base_pool_id, direction, pair_key_id, pair_direction_rank`；状态表用 `directional_pool_version, materialized_checksum, pool_scope_checksum`）；
   - 新增一个迁移→表组映射。
3. 新增工具层 `flashloan/tools/directional_pool_manager`（新 crate）：
   - `materialize_directional_pools` / `materialize_directional_pools_write`；
   - `read_directional_pool_status` / `read_directional_pool_snapshot`；
   - 幂等键 + 请求摘要 + dry-run/apply 语义与 `candidate_exchange_pool_manager` 保持一致。
4. **新增共享类型** `flashloan/tools/manager_contracts/src/directional_pool.rs`（设计稿见 04 §2.1）：
   - `PoolDirection`（0/1，含 `zero_for_one()` 与 `of_pool()` 纯函数）；
   - `V4Pool`（V4 池事实，即方向池的子对象）；
   - `DirectionalPool`（`base` + `direction` + 方向量）；
   - 在 `flashloan/tools/manager_contracts/src/ids.rs` 新增 `DirectionalPoolId`；
   - 在 `flashloan/tools/manager_contracts/src/lib.rs` 导出上述类型。
   - **本阶段只新增类型，不改任何调用方**——`ScopePool` 仍在使用，保证旧链路行为一字不变。
5. 更新 `flashloan/tools/composite_manager/src/lib.rs`：新增 `DirectionalPool` 节点与依赖图边（**阶段 1 不开启自动调度**，仅允许手动调用）。
6. 更新 `flashloan/tools/manager_api/src/lib.rs`：新增 `GET /v1/directional-pools`、`GET /v1/directional-pools/compare`、`POST /v1/directional-pools/rebuild`，权限动作 `pools.directional.read` / `pools.directional.rebuild`。
7. 更新 `flashloan/tools/token_manager/src/prune.rs`：方向表的清理路径。
8. 更新 `flashloan/tools/manager_db/tests/migration_smoke.rs` 与 `flashloan/app_runtime/tests/unit/test_architecture_guards.py`。

**退出条件：**

- 新表物化幂等：重复 apply 返回 `changedRows = 0`；
- 新表行数与基础池数的关系符合 02 §9.1 的 I-1..I-6；
- 每个基础池在方向表中**恰好两行**，两行除方向相关字段外逐列一致（04 的 I-9）；
- 共享类型已就位且被十个 crate 之一引用（编译通过即可，不要求调用方迁移）；
- 现有链路的任何表行未变（通过 P0 报告的闭环集合归一性校验）；
- 幂等键重放返回 `idempotentReplay=true`，dry-run 不写幂等记录；
- 新增的单元测试覆盖方向映射、排序、剔除、幂等（见 §4）。

**回滚：**删除新表的行与新迁移记录（旧链路未变，无需回滚主线）。

---

### 2.3 P2 —— 切换（闭环层取池改为读方向表）

**目标：**闭环三跳的选池统一来自方向表，三跳的选池口径完全一致。

**动作：**

1. **内存对象改造**（04 §4，先于任何取池逻辑改动）：
   - `flashloan/tools/candidate_edge_pool_manager/src/lib.rs:410` 与 `flashloan/tools/candidate_exchange_pool_manager/src/lib.rs:351` 的两份 `ScopePool` **全部删除**，改用 `manager_contracts` 的共享 `DirectionalPool`；
   - 两层的装载 SELECT 改为读 `manager_v4_directional_exchange_pools`；
   - 分组键由 `(代币对, 方向键字符串)` 改为 `(代币对, PoolDirection)`；
   - `flashloan/tools/closed_loop_manager/src/lib.rs:508` `ClosedLoopInput` 新增 `directional_pools: BTreeMap<String, DirectionalPool>`，并**删除** `capacity_by_pool`（`:521`）、`single_tick_bounds_by_pool`（`:533`）、`pool_tick_states`（`:537`）三个池表；
   - `PoolTickState`（`:491`）的字段并入 `DirectionalPool.base`，不再单独存在。
2. **折叠函数同形改造**（04 §4.3，折叠算术一字不改）：
   - `folded_path_capacity`（`:1718`）、`folded_path_depth_capacity`（`:1772`）、`path_min_single_tick_bound`（`:1675`）的 `directions: &[bool]` 平行参数删除，方向改由 `DirectionalPool.direction` 读出；
   - `hop_directions` 平行数组（`:2169`）删除，三跳改为携带 `directional_pool_id` 的引用数组。
3. `flashloan/tools/closed_loop_manager/src/lib.rs`：
   - 新增 `read_directional_pools`，读方向表的 `(pair_key_id, direction)` 序位列表；
   - `build_closed_loop_rows` 的三跳取池改为读方向表；
   - `push_loop`（`:2361`，hop 写入在 `:2482`）从**同一个方向池对象**一次解构，写入 `directional_pool_id` / `hop_direction` / `directional_input_token_key_id` / `directional_output_token_key_id`，并同步投影 `pool_id` / `base_pool_id`（04 §7 纪律 2）；
   - `manager_v4_closed_loops` 写入 `directional_pool_path_key`；
   - `resolve_hop_direction`（`:1906`）改为读方向行的 `hop_direction`（阶段 2 保留新旧交叉校验）；
   - `hop_zero_for_one`（`:1873`）退化为校验而非推导。
4. `flashloan/tools/candidate_edge_pool_manager/src/lib.rs`：`recommend_direction`（`:1230`）改为读方向表；`direction_key` 新增结构化列 `direction`。
5. `flashloan/tools/candidate_exchange_pool_manager/src/lib.rs`：中间池选择改为读方向表；`pool_score`（`:1159`）退化。
6. `flashloan/tools/review_manager/src/parity.rs`：`build_hops`（`:784`）的 `zero_for_one` 改为**读存储方向**，并保留一次与池 token 序的交叉校验；不一致时跳过复核并记录 `direction_mismatch`（04 §6）。取池逻辑 `resolve_route_pool_ids`（`:1054`）**不变**。
7. `flashloan/tools/composite_manager/src/lib.rs`：开启 `directional_pool` 节点的自动调度，并在 `closed_loop` 的依赖中加入 `directional_pool`。
8. 关闭环库状态表新增 `directional_pool_version` / `directional_pool_checksum`，使重建门禁能检测到方向层漂移。

**退出条件：**

- 闭环三跳的 `directional_pool_id` 全部能解析到唯一方向行（I-7）；
- 闭环跳的 `pool_id` = `base_pool_id` = 方向行的 `base_pool_id`（I-8，即 C-2 的不变量已执行）；
- 方向表的 `hop_direction` 与旧反推结果**全部一致**（交叉校验通过，说明方向存储没有引入错误）；
- 内存中的 `DirectionalPool` 集合与方向表行集合一一对应（04 的 I-10）；
- 折叠时取用的方向与 hop 的逻辑方向自洽（04 的 I-14）；
- 复核层的方向交叉校验全部通过（04 的 I-13），无 `direction_mismatch`；
- 容量与利润口径未改变（代码级差分测试通过）；
- P0 的闭环集合差异已被理解，且主线绩效满足现有验收门槛（不降低）。

**回滚：**闭环层回退到旧取池路径（一个版本）；新增的列保留为 NULL，不影响已发布的旧数据。

**回滚脚本规格（v1.1 补齐 · P2 闭合前必须就位）**：

| 项 | 要求 |
|---|---|
| 形态 | **可重跑**脚本 `plan/runtime_evidence/tools/rollback_directional_pool_switch_20261123.py`，**非手工步骤** |
| 触发方式 | **三种模式**：① 默认（**不执行**，只输出**将要回滚什么**的清单）；② `--rehearse`（**单事务内执行回滚后 `ROLLBACK`**，产出回滚后读数但**库文件不变**）；③ `--apply`（**真正写库**，需授权） |
| 幂等 | 重复执行结果一致；第二次执行报「无需回滚」而不是报错 |
| 覆盖范围 | ①闭环三跳的 `directional_pool_id` / `hop_direction` / `directional_input_token_key_id` / `directional_output_token_key_id` 置 NULL；②`manager_v4_closed_loops.directional_pool_path_key` 置 NULL；③**不删除**任何方向表行（方向层可独立保留） |
| **不**触碰 | 既有 `pool_id` / `base_pool_id` / `loop_key` 的**值**；既有闭环的身份与容量口径 |
| 事后校验 | 回滚后闭环集合读数与 **P2 之前** 的读数**逐项相等**；差异必须逐条解释 |
| 演练口径 | **`TASK-170` 的演练一律走 `--rehearse`**：单事务内执行回滚写入后 `ROLLBACK`，**主网库文件校验值前后一致**，而事务内读到的闭环集合与 P2 之前的读数**逐项相等**。`--apply` 属**需授权**的写库动作，**不属于** `TASK-170` |
| 与写库分类学的关系 | 回滚是**写库**动作 ⇒ 属**动态记录** ⇒ 需授权 |


**落地状态（v2.4 · `TASK-170` 已闭合 · 2026-11-23 第 65 轮）**：

| 项 | 落地情况 |
|---|---|
| 脚本 | ✅ `plan/runtime_evidence/tools/rollback_directional_pool_switch_20261123.py` **已就位** |
| 三种模式 | ✅ 默认（只读清单）/ `--rehearse` / `--apply` **齐备** |
| **三种模式的报告（v2.4 补）** | ✅ **各写各的**：默认模式 → `..._20261123_plan.{json,md}`；`--rehearse` → `..._20261123.{json,md}`（**基名**）；`--apply` → `..._20261123_apply.{json,md}`。**修掉了「默认模式运行把演练报告覆盖成 `mode=plan`」的问题** |
| **演练位置（v2.4 订正）** | ⚠️ **`--rehearse` 一律在**影子副本**（临时目录）**上进行**。主网库 `flashloan/db/local_store_4663.sqlite3` 是 **WAL** 模式且 **8.6 GB**，**以读写方式打开会在关闭时 checkpoint、必然改动主文件** ⇒ 校验值不可能保持一致。故主库**始终只读打开**（`mode=ro` + `PRAGMA query_only = ON` 双保险），演练在**影子副本**上完成 |
| 影子副本内容 | ✅ 复制 `manager_v4_closed_loops`（**168** 行）+ `manager_v4_closed_loop_hops`（**504** 行），按 §4.1 的 DDL 补上 **5** 个方向列并填**合成方向值** ⇒ 回滚**非空洞** |
| 幂等 | ✅ 第一遍 hops **504** / loops **168**；**第二遍 0 / 0**（报「无需回滚」，**不报错**） |
| 覆盖范围 | ✅ hops 的 `directional_pool_id` / `hop_direction` / `directional_input_token_key_id` / `directional_output_token_key_id` 置 `NULL`；loops 的 `directional_pool_path_key` 置 `NULL`；**不删**方向池行 |
| **不**触碰 | ✅ `pool_id` / `base_pool_id` / `loop_key` 的**值**一律不动 |
| 事后校验 | ✅ `loops 168 == 168`、`hops 504 == 504`（`equal = true`）；方向单元格 2016+168 → 0+0 → **事务回滚后恢复** 2016+168 |
| **故意不改** | ✅ `manager_v4_closed_loop_library_state` **不改** —— 它是**派生状态**，手改等于**伪造版本一致性** |
| P3 旧列恢复 | ✅ 识别**三种形态**：列仍在（无需恢复）/ 表被降级为视图（`DROP VIEW t; ALTER TABLE src RENAME TO t;` 可恢复）/ 列被 drop（**值已不可重建 ⇒ 报告并拒绝伪造空列**） |
| 演练读数（**最终 · 2026-11-23 05:11:25 · 窗口 82.815 s**） | ✅ **九项检查全 PASS**：`closedLoopSetUnchanged` / `allDirectionalColumnsNulled` / `secondPassChangedNothing` / `transactionRolledBack` / `libraryFileUnchanged` / `noWalContentWritten` / **`liveLibraryQuiescent`** / **`walContentAttributableToRehearsal`** / **`libraryFileUnchangedOrExternalWriter`** |
| 库文件指纹 | ✅ sha256 前后**同为** `ad3e56c7bacc50181d22f548668e312d55dfc0e47601b84024c4112441cd1f0f`；size **8601968640**、mtimeNs **1795380761685225700** —— **三项均未变** |
| **监视器时间线（归因证据）** | ✅ `LibraryWatcher`（**0.25 s** 采样）：**316 次采样 / 0 事件** ⇒ 窗口内主库文件与 `-wal` **均无变化** ⇒ 本次演练落在**静默窗口**内，字节比对**同时**成立，两条判据互相印证 |
| 已知遗留（如实记录） | ⚠️ 主库同目录出现 `local_store_4663.sqlite3-wal`（**0** 字节）/ `-shm`（**32768** 字节）。成因：SQLite **以只读方式打开** WAL 库时会**自建**这两个运行时文件（**不携带任何库数据**）。判据据此收紧为「**不得产生 WAL 内容**」，同目录文件变化写进报告（`siblingNote`）；**不手工删除**（由 SQLite 自身管理，且 `Remove-Item` 被策略拦截） |
| **主网库是活的生产库（v2.4 发现 A）** | ⚠️ `manager-api` 进程（PID 14648，2026-11-22 15:37 启动）**持有** `local_store_4663.sqlite3`；且**本仓库自身的 pytest 全量在运行期间会写入该库**（实测库文件 mtime 在套件运行期反复推进，**size 始终不变** ⇒ 页内改写 / checkpoint）⇒ **字节比对只在静默窗口有效** |
| **`T-I-8` 判据（v2.4 改写为「可归因」形式）** | ⚠️ **不是放宽，是加强到能归因**：① **结构证明**（唯一连接 `mode=ro` + `PRAGMA query_only = ON`，全部写入落影子副本）；② **内容证明**（主库 `-wal` **始终 0 字节** ⇒ 未贡献任何 WAL 帧）；③ **归因证据**（`LibraryWatcher` 采样主库与 `-wal` 的 size / mtime **变化时间线**）；④ **字节比对降级为观测**（不再作为唯一判据，因活库存在**并发写者**） |

> **报告落点**：`plan/runtime_evidence/reports/task170_rollback_directional_pool_switch_20261123.{json,md}`。
> **脚本就位 + 演练通过 ⇒ P2 的回滚前置已闭合**；但 **P2 本身的另一个前置
> `TASK-167`（P1）仍未闭合** ⇒ **P2 仍不得开工**。

---

### 2.4 P3 —— 下线（旧口径降级）

**目标：**删除重复的方向表达与重复的排序逻辑。

**动作：**

1. `manager_dex_candidate_pool_recommendations.direction_key` 与 `manager_market_observation_yz_pairs.direction_key`：
   - 方案 A：降级为视图（保留展示，不再写入）；
   - 方案 B：删除列（需先确认无外部消费者）。
2. `manager_market_observation_intermediate_pools` 中的 `selection_rank` 与方向表序位的关系文档化（保留还是删除，待 P3 决策）。
3. `recommend_direction` / `pool_score` 删除。
4. 更新 `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md` 与 `04-DEX候选池存储结构.md` / `05-市场观察中间交换池存储结构.md`。

**退出条件：**

- 主线连续窗口的闭环集合与盈利读数稳定，且与 P2 一致；
- 旧列的所有消费者已迁移；
- 架构守卫与测试全部通过；
- **回滚脚本已就位且演练过**（与 P2 回滚脚本同一套机制，额外覆盖「恢复旧列 / 重新降级为视图」）；
- **删除 / 降级带来的行数变化 == 0** 已实测。

---

## 3. 依赖顺序与阶段门禁

```text
P0 影子 ─────────────────────────────────────────────────── P1 并存
  · 口径判定已确认                    · 新表物化幂等
  · 影子报告已生成                    · 现有链路未变
                                                      ↓
P2 切换 ─────────────────────────────────────────────────── P3 下线
  · 方向与旧反推一致              · 旧列无消费者
  · 容量/利润口径未改              · 文档已更新
  · 主线绩效满足现有门槛
```

**P2 不得与 P1 同日发布**。P2 会改变闭环集合，必须在 P1 稳定运行至少一个完整窗口后才能开始。

---

## 4. 测试清单

### 4.1 新工具层（`directional_pool_manager`）

| 用例 | 要求 |
|---|---|
| 每个池产生两行 | 方向 0 / 1 各一行，且 `input_token_key_id` 符合 02 §2.1 |
| 方向映射不可翻转 | 人为构造一个 token0 地址更高的池，方向仍为 0，输入仍是 token1 |
| 排序与旧口径一致 | 方向表中的序位 1..N 与 `recommend_direction` 的输出顺序**逐行相等** |
| 序位在 (pair, direction) 内连续 | 无缺号、无重号 |
| 容量来源正确 | 方向 0 取 `single_tick_max_token1_raw`，方向 1 取 `single_tick_max_token0_raw` |
| 未测量不被当作零 | `NULL` 与 `'0'` 分开存储，`input_capacity_measured` 为 0/1 |
| 非活跳池不获序位 | `pool_status != 'active'` 的行 `pair_direction_rank IS NULL` |
| 物化幂等 | 重复 apply → `changedRows = 0`；摘要变化→ 校验和变 |
| dry-run 不写库 | 幂等表无新记录 |
| 幂等重放 | 相同键 + 相同摘要 → `idempotentReplay=true`；摘要变 → `IDEMPOTENCY_KEY_REUSED` |

### 4.2 闭环层

| 用例 | 要求 |
|---|---|
| 三跳均由方向池组成 | 闭环跳的 `directional_pool_id` 全部可解析，且 `output_token_key_id` 与 hop 输出一致 |
| `pool_id` 语义不变 | 新旧两路都能被 `review_manager` 正常定价（用现有定价测试作回归基线） |
| 方向与旧反推一致 | 新方向行的方向与 `resolve_hop_direction` 的结果完全相同 |
| 容量口径未变 | 同一闭环的 `single_tick_capacity_anchor_raw` 与重构前一致 |
| 闭环数不减少 | 在 P2 的对比中，闭环数不得减少（需容量口径不放宽，且口径变更的结果必须可解释） |
| 跳数不变 | `manager_v4_closed_loop_hops` 的行数与重构前一致 |
| 新列不影响旧查询 | 旧的 `read_closed_loop_snapshot` / `read_closed_loop_status` 返回值不变 |

### 4.3 不变量回归（P2 必做）

| 项 | 方法 |
|---|---|
| 容量折叠口径 | 现有 `closed_loop_manager` 容量测试全部通过，且新旧路径的容量值逐个闭环相等 |
| 利润口径 | 现有盈利门禁测试通过 |
| 定价口径 | `review_manager` 现有 parity 测试通过 |
| 保护表登记 | 若未改动 review / submission / broadcast 表，`schema_policy.json` 无需变动；若改动，必须先登记并附审查记录，否则守卫拒绝 |

### 4.4 共享类型与内存对象（P2 必做）

| 用例 | 要求 |
|---|---|
| `PoolDirection::of_pool` 是纯函数 | 同一对 token 永远得到同一方向；无随机、无读表 |
| `DirectionalPool` 的 `base` 与基础池逐列一致 | 对每个基础池，比对方向池两行的 `base` 与池表对应行（I-9） |
| 一个基础池恰好两行 | 行数 == 基础池数 × 2；两行仅方向相关字段不同（I-3） |
| 方向不可翻转 | 人为构造 `token0` 地址更高的池，方向仍为 0，输入仍是 token1 |
| 内存集合与库行集合一一对应 | 装载后的 `directional_pools.len()` == 方向表行数（I-10） |
| 折叠签名已无方向平行数组 | `folded_path_capacity` / `folded_path_depth_capacity` / `path_min_single_tick_bound` 的参数表中**不再出现** `directions: &[bool]` |
| 折叠结果与改造前逐个闭环相等 | 用同一份 tick 状态，分别跑旧实现与新实现，容量逐个闭环相等 |
| `single_tick_bounds_by_pool` 取值等价 | 方向 0 取 `single_tick_max_token1_raw`、方向 1 取 `single_tick_max_token0_raw`，与旧的 `if zero_for_one { token0 } else { token1 }` 逐值相等 |
| hop 投影同源 | 从同一个 `DirectionalPool` 一次解构写出 `directional_pool_id` / `hop_direction` / `pool_id` / `base_pool_id`（纪律 2） |
| 复核层交叉校验 | 构造一条 `hop_direction` 与池 token 序矛盾的 hop，复核**跳过**并记录 `direction_mismatch`，而非静默改写（I-13） |
| `pool_ids_json` 契约不变 | 长度 == 跳数，元素全部是 base pool id；复核与广播读取结果与改造前一致（I-16） |
| 闭环身份不变 | `loop_key` 计算规则未改；同一组代币环在改造前后得到同一个 `loop_key` |

---

## 5. 证据要求

每个阶段的证据报告必须写入 `plan/runtime_evidence/reports/`，并在对应任务的证据引用中指向它。

### 5.1 P0 报告必须包含

```text
chain_id / network / 数据库路径 / 读取时间戳 / 库文件校验值
基础池总数 / 可用池总数 / 方向 0 可用数 / 方向 1 可用数
中间跳选池变化：总次数 / 变化次数 / 变化率
闭环集合变化：旧数 / 新数 / 保留 / 新增 / 消失 与完整列表
容量差异分布：同一闭环新旧容量差的分布（含中位数、最大值、受影响闭环数）
利润差异分布：同一闭环新旧有效利润差的分布
口径判定结论与依据
```

### 5.2 P1 / P2 报告必须包含

```text
幂等证据：首次 apply 的 changedRows / 重复 apply 的 changedRows / 摘要变化时的校验值变化
不变量报告：基础池表 / 快照表 / 候选表 / 观察表 / 闭环表 的行数与校验和在切换前后的变化
新增列的值样本：前 N 行闭环跳的 directional_pool_id / hop_direction / 方向输入输出代币
测试报告：新增/修改用例的名称与结果
守卫报告：架构守卫与迁移烟雾的执行结果
```

### 5.3 P3 报告必须包含

```text
旧列 / 旧表的最后一个读取时间戳与消费者清单
删除带来的行数变化（应为 0）
主线连续窗口的闭环数 / 利润读数 / 广播产出读数对比
```

---

## 6. 需授权动作

| 阶段 | 动作 | 是否需授权 | 说明 |
|---|---|---|---|
| P0 | 只读脚本运行、报告生成 | 否 | 不写任何库 |
| P1 | 迁移 apply（新表写入） | **是** | 写库动作，需明确授权 |
| P1 | `POST /v1/directional-pools/rebuild` apply | **是** | 同上 |
| P2 | 闭环层切换（发布主线行为变化） | **是** | 改变主线闭环集合 |
| P3 | 旧列/旧表删除 | **是** | 不可逆操作，需回滚脚本 |
| 全程 | 广播与主线实盘 | **本方案不触发** | **池子重构方案本身不执行任何广播**；广播的准入规则由 `BC-1..BC-5` 定义（v2.2：默认放开 + 正向利润准入），**但本方案的施工动作不产生广播** |
| 全程 | 方向池行物化 / 闭环重物化 | **是** | v2.2 的「广播默认放开」**不适用于**它们（`BC-4`） |

> **v2.2 说明**：广播**授权默认放开**（使用者裁定），但**本方案仍不触发广播** ——
> 池子重构的施工内容是**表结构 / 内存对象 / 闭环口径**。
> 广播链路的事务边界见开发计划 §3.7 与任务清单 §0.3.2。

---

## 7. 风险处置

| 风险 | 触发条件 | 处置 |
|---|---|---|
| 中间跳选池变化过大 | P0 报告显示闭环数明显下降 | 停在 P0，回到 01 §4.1/4.3 重审主序，不进入 P1 |
| 容量口径被意外改动 | P2 容量差异非零 | 立即回滚 P2，核对 `folded_path_capacity` / `folded_path_depth_capacity` / `path_min_single_tick_bound` |
| 定价路径断 | `review_manager` parity 测试失败 | 回滚 P2；核对 `pool_id` 语义是否被改动（C-2） |
| 并发写入争用 | P1 重建与主线写库并发 | 新表写入使用独立事务与幂等键，不与主线 apply 同事务 |
| 孤立行永久残留 | `prune` 未覆盖新表 | P1 必须同时修改 `token_manager/src/prune.rs`，否则 P1 退出条件不能通过 |
| 守卫拒绝 | 新增列未登记在 `schema_policy.json` | 如果涉及受保护表，先登记再改代码 |

---

## 8. 当前状态的诚实边界

- 本文档的所有证据要求均为**尚未产出**的项；P0 的比对报告**尚未运行**。
  （**订正 v1.0 勘误**：v1.0 曾写「工作区内没有在线数据库」，并引用了错误路径
  `E:\2026OPC大赛\db\local_store_4663.sqlite3`。主网库**实际存在**于 `E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3`（约 **8.01 GB**），
  **可只读打开**（`mode=ro`，超时 ≥120s）⇒ **P0 的前置条件「能访问在线库」已满足**。）
- 本文档**不声称**新口径会提升盈利或深度。它声称的只有一件事：**中间跳的选池口径与两侧跳将从不同的表达统一到同一个方向口径。**
- 实际收益取决于 P0 报告的实测读数，不取决于本文档的描述。

---

<a id="d117"></a>

### `D-117-旧排序消费者清单与逐列决策_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/D-117-旧排序消费者清单与逐列决策_20261125.md` -->

# D-117 `T-方储.01`｜旧排序消费者清单与逐列决策

> **日期**：2026-11-25　**任务**：`T-方储.01`（`M-3` 方向层 ＋ `M-10` 存储层）
> **采集工具**：`plan/runtime_evidence/tools/collect_legacy_order_consumers_20261125.py`
> **报告**：`plan/runtime_evidence/reports/legacy_order_consumers_20261125.json`
> **实盘库**：**零写入**（`mode=ro` ＋ `PRAGMA query_only = ON`）

---

## 1. 触发条件

`T-闭.01`（`M-4`，方向层主序接管闭环主池）已落地：闭环三跳的**主池**改由方向层的
`pair_direction_rank` 决定，旧排序**降级**为「**只**负责候选成员来源」。

⇒ **旧排序的两个产物**（候选边推荐表的 `rank` 序位、市场观察表的 `optimal_pool_id` 与
有序 `pool_ids_json`）**不再是主序的决定者** ⇒ 它们成为**可下线的候选**。

**但下线的前置条件是「切换已生效且稳定」**（边界验收：*先切换，后下线*）——
`T-闭.01` 的实盘生效复测**尚未完成**（运行期未重启）。

⇒ **本文件只做「下线前的准备」**：消费者清单、最后读取时间戳、逐列决策。
**删除动作本轮不执行。**

---

## 2. 消费者清单（**92 个文件**）

采集口径：全仓扫描（`.rs` / `.py` / `.sql` / `.toml`）中提到
`manager_dex_candidate_pool_recommendations`、`manager_market_observation_yz_pairs`、
`recommend_direction`、`pool_score`、`optimal_pool_id`、`pool_ids_json` 的文件，
按**模块**与**角色**归类。

| 模块 | 文件数 | 角色分布 |
|---|---:|---|
| `M-5`（候选层） | **37** | 消费者 **16** ＋ 生产者 **2**（`candidate_edge_pool_manager` / `candidate_exchange_pool_manager`）＋ 工具 **19** |
| `M-10`（存储层） | **22** | 消费者 **14** ＋ schema **7** ＋ 工具／脚本 **1** |
| `M-9`（守卫与测试） | **14** | 测试 **14** |
| `M-2`（证据通道 / `rs-src`） | **9** | 消费者 **9** |
| `M-6`（复核层） | **3** | 消费者 **3** |
| `M-1`（池管理器） | **2** | 消费者 **1**（`baseline_audit.rs`）＋ 生产者 **1**（`pool_manager/src/lib.rs`） |
| `M-3`（方向层） | **1** | 测试 **1**（`directional_pools.rs`） |
| `M-4`（闭环层） | **1** | 消费者＋生产者 **1**（`closed_loop_manager/src/lib.rs`） |
| `M-7`（广播层） | **1** | 消费者 **1**（`broadcast_manager/src/lib.rs`，读的是**自己**的 `pool_ids_json`） |
| `M-8`（编排层） | **1** | 消费者 **1**（`manager_api/src/lib.rs`，读的是**自己**的 `pool_ids_json`） |
| —（未归模块） | **1** | `token_manager/src/prune.rs` |
| **合计** | **92** | — |

### 2.1 **真正的「旧顺序依赖者」**（**只有 7 处**）

上表里 **92** 个文件只是「**提到**」这些名字；绝大多数提到的是**同名不同义**的
`pool_ids_json`（闭环层 / 复核层 / 广播层**自己**的「本闭环用到的池列表」列）。

**真正读「旧顺序」的只有 7 处**：

| # | 位置 | 读什么 | 角色 | 处置 |
|---:|---|---|---|---|
| 1 | `closed_loop_manager/src/lib.rs:1584` `read_recommendations` | `manager_dex_candidate_pool_recommendations` 的 **`ORDER BY … rank`** | **闭环层主序（旧）** | **已被 `T-闭.01` 接管**（顺序改由方向 rank 决定；旧序仅剩**并列时的次序**） |
| 2 | `closed_loop_manager/src/lib.rs:1646` `read_middle_pools` | `optimal_pool_id` ＋ **有序** `pool_ids_json` | **闭环层主序（旧）** | 同上（`optimal_pool_id` 降级为**候选之一**） |
| 3 | `candidate_edge_pool_manager/src/lib.rs:1103` / `:1109` | `recommend_direction(...)` | **旧顺序的生产者** | **下线对象**（阶段 2） |
| 4 | `candidate_edge_pool_manager/src/lib.rs:1328` `fn recommend_direction` | 旧排序函数本体 | **旧顺序的生产者** | **下线对象**（阶段 2） |
| 5 | `candidate_exchange_pool_manager/src/lib.rs:1024` / `:1092` | `pool_score(left/right)` | **旧顺序的生产者** | **下线对象**（阶段 2） |
| 6 | `candidate_exchange_pool_manager/src/lib.rs:1159` `fn pool_score` | `liquidity_index` 代理 | **旧顺序的生产者** | **下线对象**（阶段 2） |
| 7 | `pool_manager/src/baseline_audit.rs:502-558` | 两张旧表 | **审计工具** | 阶段 2 随表结构调整（**审计口径必须同步**，否则基线审计会误报） |

> **其余 85 个文件**分三类：
> ① **同名不同义**（`pool_ids_json` 在 `manager_v4_closed_loops` / `manager_v4_reviews` /
> `manager_cow_execution_attempts` / `manager_v4_review_broadcast_requests` 里是**本闭环的池列表**）⇒ **与旧排序无关**；
> ② **schema / 迁移**（`manager_db/migrations/*.sql`、`migrate.rs`）⇒ 随列的决策走；
> ③ **测试与一次性工具**（`app_runtime/tests/unit/*`、`plan/runtime_evidence/tools/*`、`scripts/*`）⇒
> **只读**这些表做断言／取证 ⇒ 表结构变了它们要跟着改，但**它们不是顺序的依赖者**。

---

## 3. 最后读取时间戳（**实盘读数 · 只读**）

| 表 | 行数 | 最后写入（`MAX(updated_at)`） | 换算（+08:00） |
|---|---:|---|---|
| `manager_dex_candidate_pool_recommendations` | **340** | `2026-11-24T08:41:55.837653+00:00` | **2026-11-24 16:41:55** |
| `manager_market_observation_yz_pairs` | **1420** | `2026-11-22T12:33:58.783619+00:00` | **2026-11-22 20:33:58** |
| `manager_v4_directional_exchange_pools` | **692** | `2026-11-23T22:46:22.591455+00:00` | **2026-11-24 06:46:22** |
| `manager_v4_closed_loops`（**消费者**的发布） | **174** | `2026-11-24T01:06:24.995144+00:00` | **2026-11-24 09:06:24** |
| `manager_v4_closed_loop_hops` | **522** | 同上 | **2026-11-24 09:06:24** |
| `manager_v4_closed_loop_optimizations` | **472** | 同上 | **2026-11-24 09:06:24** |

**读法（必须写清，否则会被误读）**：

| 事实 | 含义 |
|---|---|
| 闭环层**最后读取**旧排序的时刻 ≈ **2026-11-24 09:06:24 +08** | 即它**最后一次发布**闭环的时刻（读在前、写在后） |
| 候选边推荐表的最后写入（**16:41:55**）**晚于**闭环层的最后发布（**09:06:24**） | ⇒ **已发布的闭环相对候选边是「陈旧」的**：候选边刷新过，闭环层**没有**跟着重发 |
| 实盘运行时 pid **27084** 启动于 **2026-11-24 23:56:12 +08** | 该进程**启动至今**没有再改写这两张旧表，也没有再发布闭环 |
| 市场观察表最后写入停在 **2026-11-22 20:33:58** | 中间跳的候选来源**两天多未刷新** |

⇒ **对 `T-闭.01`「实盘生效复测」的直接影响**：实盘的重启**尚未让新口径跑过一次**；
而且在重启之前，**闭环层本身也没有在跟随候选边刷新** ⇒ 复测时必须**同时**核对
「闭环是否随上游重发」，否则会把「上游没刷新」误判成「新口径没生效」。

---

## 4. 逐列决策（**保留 / 降级为视图 / 删除**）

> **同名不同义必须先分清**：`pool_ids_json` 与 `optimal_pool_id` 在多张表里存在，
> 但**只有市场观察那几张**里的才是**旧顺序**的产物。

### 4.1 旧顺序的**真正**载体（**7 张表**）

| 表 | 列 | 行数 | 阶段 1（本轮） | 阶段 2（实盘生效＋稳定窗口后） | 理由 |
|---|---|---:|---|---|---|
| `manager_dex_candidate_pool_recommendations` | `rank` | 340 | **保留**（不再决定主序；仅作并列时的稳定次序） | ✅ **保留（语义已换）** —— **删列已豁免**，见 [`D-139`](../../../docs/D-139-M4决策_rank列保留与删列豁免_20261125.md) | **语义已从「旧排序的序位」换成「方向层序位的派生组内序号」** ⇒ 它**不再是旧顺序载体**，删它**不属于**本任务范围；且它是闭环层**活排序键** ＋ **PK 组成列**（`depth_score` 不可替代，实测 **82/216** 组排序不一致） |
| `manager_market_observation_yz_pairs` | `optimal_pool_id` | 1420 | **保留**（降级为候选之一） | ✅ **已降级为视图**（迁移 `20261125_017`，由方向层 rank-1 派生）→ 再**删除** | 视图化可让既有查询**不报错**，同时把「主池」的权威收归方向层 |
| `manager_market_observation_yz_pairs` | `pool_ids_json` | 1420 | **保留**（**成员**仍需要；**顺序**不再需要） | 保留 json 数组、**不再按它定序** | 成员来源不可删（否则中间跳候选池消失） |
| `manager_market_observation_abnormal_yz_pairs` | `optimal_pool_id` / `pool_ids_json` | 180 | **保留** | **保留** | 异常对**不参与**闭环主序 |
| `dex_candidate_pool_recommendations`（**Python 旧表**） | `rank` | 232 | **保留** | **保留** | **离线路径**（`builders.py` 写 `dex_*` 旧表），与 `manager_v4_closed_loop_*` 无关 |
| `market_observation_yz_pairs`（**Python 旧表**） | `optimal_pool_id` / `pool_ids_json` | 1560 | **保留** | **保留** | 同上 |
| `market_observation_abnormal_yz_pairs`（**Python 旧表**） | `optimal_pool_id` / `pool_ids_json` | 258 | **保留** | **保留** | 同上 |

> **不属于旧顺序载体**（明确排除，避免误列）：`manager_dex_candidate_pool_pairs`（**108** 行）
> 只有 `selection_basis`，**没有**顺序列 ⇒ **不在**下线范围内。

> **阶段 2 第一段施工后的状态更新（2026-11-25）**
>
> | 列 | 阶段 2 第一段的状态 | 证据 |
> |---|---|---|
> | `manager_market_observation_yz_pairs.optimal_pool_id` | ✅ **已降级为视图** | 迁移 `20261125_017_observation_optimal_pool_view` ＋ 回滚脚本；视图按**方向**（`token_x → token_y`）取方向层 rank-1；**3 个 `INSTEAD OF` 触发器**保证 **3 个 writer**（`candidate_exchange_pool_manager:2649`、`closed_loop_manager:5375` / `:6002`）仍可写 |
> | `manager_dex_candidate_pool_recommendations.rank` | ✅ **保留（语义已换）· 删列已豁免** | **`M-4` 决策已做**（[`D-139`](../../../docs/D-139-M4决策_rank列保留与删列豁免_20261125.md)）：该列**不再承载旧排序**，改承载**方向层序位的派生组内序号**（`candidate_edge_pool_manager:1381` 写 `rank: offset + 1`；`closed_loop_manager:1582/1610/2749` 读它、排序它、以它做有效性校验）⇒ 删它**不是**死代码清理，而是拆除一条**正在服役的排序契约** ⇒ **明确豁免** |
> | `recommend_direction` / `pool_score`（**旧排序的生产者**） | ✅ **已删除** | 全仓 `*.rs` 零命中；候选层（`M-5`）已改接方向层；审计口径（`M-1`）已同步 |
> | `pool_ids_json`（市场观察表） | ✅ **保留**（成员仍需要） | 视图化**未动**成员集 ⇒ 行数 **1420 → 1420**、成员集合**逐字不变** |
>
> **实测影响（真实生产数据 · 副本）**：行数 **1420 → 1420**、`optimal_pool_id` 非空 **386 → 386**、
> 值变化 **88** 行（**52** 真实重排 ＋ **36** 并列次序差异）、
> 已发布 hop 的 `base_pool_id` **522 / 522** == 方向层 rank-1 ⇒ **闭环身份未变**。
> 详见 `D-131` §7。

### 4.2 **同名不同义**（**明确不动**）

| 表 | 列 | 行数 | 决策 | 理由 |
|---|---|---:|---|---|
| `manager_v4_closed_loops` | `pool_ids_json` | 174 | **保留** | 这是**闭环自己的**「本闭环用到的池列表」，**不是**旧顺序的产物 |
| `manager_v4_reviews` | `pool_ids_json` | 174 | **保留** | 同上（复核层自己的） |
| `manager_v4_review_broadcast_requests` | `pool_ids_json` | 0 | **保留** | 同上（广播请求自己的） |
| `manager_cow_execution_attempts` | `pool_ids_json` | 0 | **保留** | 同上（执行尝试自己的） |
| `v4_closed_loops`（**Python 旧表**） | `pool_ids_json` | 104 | **保留** | 离线路径 |

### 4.3 阶段 2 的**前置条件**（**必须逐条满足**）

| # | 条件 | 判据 | 现状 |
|---:|---|---|---|
| 1 | `T-闭.01` **实盘生效** | 实盘库重物化后，闭环每跳的 `directional_pool_rank = 1`，且 `pool_selection_basis = directional_pair_direction_rank:*` | ✅ **已满足**（2026-11-25 · 运行时 pid 24120 于 08:24:57 重启 ⇒ `closed_loop_version` 19 → 20、`changedRows = 0`、`pool_selection_basis` **522 / 522** = `directional_pair_direction_rank:1`） |
| 2 | **稳定窗口**读数已产出 | 连续窗口内闭环数 / 跳数 / 价格质量 / 广播产出**无回退** | 🔄 **采样中**（`plan/runtime_evidence/stability_window_20261125.jsonl`，每 60s 一采；闭环版本 / 校验和 / 闭环数 / 一致性 **逐次逐字相同** ⇒ 无回退） |
| 3 | 方向层 rank **组内唯一**（删除旧序的前提） | 重复组 = **0**、序号缺口 = **0** | ✅ **已实测通过**（§5） |
| 4 | **回滚脚本已就位** | 开关 `FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY=0` ⇒ 旧序**逐行复现** | ✅ **已实测通过**（旧序 `changedRows = 0`，见 `T-闭.01` 的 `D-116` §2.2） |
| 5 | **审计口径同步** | `pool_manager/src/baseline_audit.rs` 随表结构调整 | ⬜ 未做（阶段 2 一并做；属**扩范围**：`M-1`） |

> **2026-11-25 复核**：前置 **1 / 3 / 4 已解除**，**2 在积累**，**5 是阶段 2 本身的一部分**。
> 阶段 2 的**施工清单 / 顺序 / 回归范围 / 验收判据 / 回滚方式**见
> [`D-131`](#d131)。

---

## 5. 关键测量：方向层 rank **唯一且连续**（**删除旧序的前提**）

只读实测（`manager_v4_directional_exchange_pools`，`scope_key = core4` 与 `candidate_exchange`）：

| 观测 | 读数 | 含义 |
|---|---:|---|
| 已定价行（有 `pair_direction_rank`） | **342** | 其中 `core4` **169**、`candidate_exchange` **173** |
| 组数（`pair_key_id` ＋ `direction` ＋ `scope_key`） | **222** | — |
| **同组内 rank 重复的组** | **0** | ⇒ rank 在组内**唯一** ⇒ 稳定排序**不需要**旧次序兜底 |
| **rank 缺口**（`MAX(rank) ≠ COUNT(*)` 的组） | **0** | ⇒ rank 是 **1..N 的连续序列**，没有空洞 |
| 旧候选层的 `rank` 与方向层 rank **不一致**的行 | **186 / 340（54.7 %）** | ⇒ 旧排序与方向序位在**过半**的行上不同 ⇒ 两者**不是**同一个序 |

⇒ **删除旧序的技术前提已成立**：一旦实盘生效并稳定，旧排序的**顺序**就**没有任何**
不可替代的作用；届时删列是**纯粹的死代码清理**，不会改变任何一行的结果。

---

## 6. 本文件**不**做什么

| 不做 | 理由 |
|---|---|
| **不删**任何列／表 | 阶段 2 的前置条件（§4.3）**已全部解除**（见 `D-131` §1）⇒ 第一段（`optimal_pool_id` 降级为视图）**已施工**；**`rank` 列经 `M-4` 决策明确豁免删除**（`D-139`）⇒ 本文件**不再有待删除的列** |
| **不**改 `M-3` / `M-10` 的代码 | 本轮只做**准备**；删除动作需**独立施工**（扩范围后重跑） |
| **不**动 Python 离线路径 | `builders.py` 写 `dex_*` 旧表，与闭环层无交集 |
| **不**重启实盘运行时 | 属运维窗口动作；并行进程仍有**在途、未提交**的广播／复核层改动 ⇒ 重启会把**未经回归验证**的代码带上线（其 `cargo build` 已在 2026-11-25 恢复 **exit 0**，但**回归尚未跑完**） |

---

<a id="d131"></a>

### `D-131-阶段2前置状态复核与施工清单_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/D-131-阶段2前置状态复核与施工清单_20261125.md` -->

# D-131 `T-方储.01`｜阶段 2 前置状态复核 ＋ 施工清单

> **日期**：2026-11-25　**任务**：`T-方储.01`（`M-3` 方向层 ＋ `M-10` 存储层）
> **前置任务**：`T-闭.01`（方向层主序接管闭环主池）—— **已闭合**，且**已在实盘生效**

---

## 1. 阶段 2 前置条件（`D-117` §4.3）的**当前状态**

| # | 条件 | `D-117` 记录的状态（2026-11-25 早） | **本轮实测** | 判定 |
|---:|---|---|---|---|
| 1 | `T-闭.01` **实盘生效** | ⬜ 未做（运行期未重启） | 实盘运行时 pid **24120** 于 **08:24:57** 重启；重物化后 `closed_loop_version` **19 → 20**、`changedRows = 0`；`pool_selection_basis` = **522 / 522** = `directional_pair_direction_rank:1` | ✅ **已满足** |
| 2 | **稳定窗口**读数已产出 | ⬜ 未做 | **已走完**（`plan/runtime_evidence/stability_window_20261125.jsonl`，每 **60s** 一采）：**148** 行中 **140** 行有效，有效跨度 **174.8 min**（≥ **30** min），读数逐项稳定（见 §2） | ✅ **已满足** |
| 3 | 方向层 rank **组内唯一** | ✅ 已实测（重复组 0、缺口 0） | 未变 | ✅ **已满足** |
| 4 | **回滚脚本已就位** | ✅ 已实测（`FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY=0` ⇒ `changedRows = 0`） | 未变 | ✅ **已满足** |
| 5 | **审计口径同步**（`pool_manager/src/baseline_audit.rs`） | ⬜ 未做 | **已做**（阶段 2 第一段：`baseline_audit.rs` 改读 `manager_v4_directional_exchange_pools` 的 `pair_direction_rank`，不再读旧表；无方向层的库则计数器保持 **0** 而非报错） | ✅ **已满足** |

⇒ **前置 1 / 2 / 3 / 4 / 5 全部解除** ⇒ **阶段 2 第一段已具备施工条件并已施工**（见 §7）。

---

## 2. 稳定窗口读数（连续采样 · 实盘只读）

**工具**：`plan/runtime_evidence/tools/sample_stability_window_20261125.py`
**数据**：`plan/runtime_evidence/stability_window_20261125.jsonl`（每 **60s** 一采，**只读** HTTP overview）

| 观测项 | 窗口内读数 | 判定 |
|---|---|---|
| `closedLoopVersion` / `closedLoopChecksum` | **20** / `sha256:5299fc3c…` —— **逐次采样逐字相同**（版本**唯一**、校验和**唯一**） | ✅ **无回退** |
| `reviewVersion` | **111 → 115**（**递增**） | ✅ 复核层**自增**，**非回退** |
| `reviewCount` | **174**（**唯一**） | ✅ **无回退** |
| `versionsConsistent` / `consistencyIssues` | **true** / **`[]`** | ✅ |
| `upstreamConsistent` / `upstreamIssues` | **true** / **`[]`** | ✅ |
| `pnlMissingCount` | **20 / 21**（与采集器的**链上事实**读数一致） | ✅ 未新增 |
| `missingPoolReferenceCount` | **0** | ✅ |
| `runtimeSnapshotAvailable` | **true**（`runtimeGeneration` 持续递增） | ✅ 快照持续被接受 |
| `candidateEdgeVersion` / `observationVersion` | **9** / **12**（**唯一**） | ✅ 稳定 |
| `broadcastVersion` | **0**（**唯一**） | ✅ |
| **有效样本 / 跨度** | **140 / 140** 有效（另 **8** 行为采样器 HTTP 连接拒绝的 error 行）；跨度 **174.8 min** | ✅ **≥ 30 min** |
| `reviewCompletedCount` / `broadcastRequestCount` | **0 / 0** | ⚠️ **无广播产出**（**市场条件**：无「completed ＋ 正利润 ＋ 可执行」的复核） |
| `profitableRouteCount` / `bestObservedNetProfitUsd` | **4 – 7** / **0.000262 USD** | ⚠️ **有**正利润路线，但**没有一条**走到可广播 |

**读法（必须写清）**：

1. **闭环侧无回退** ⇒ 新口径（方向层主序）在实盘**稳定**；
2. **广播产出为 0** —— 这是**市场条件**（见 §5），**不是**本次切换的缺陷：
   切换前后广播产出**都是 0**，所以「**无回退**」成立；
3. 「**无回退**」与「**有产出**」是**两件事** —— 本任务的前置只要求**前者**。

---

## 3. 本轮闭合的 **3** 项

### 3.1 三份结构文档已更新 ✅

| 文档 | 本轮新增 | 位置 |
|---|---|---|
| `docs/数据库结构文档/07-V4兑换池与闭环存储结构.md` | §7「Manager 侧实表与方向池引用」：`manager_v4_closed_loop_hops` 的 **22 列**实表、方向池作为基础池**子对象**的图、主序口径（`pair_direction_rank = 1`）、回滚开关、实测读数 | 文件末尾 §7.1–§7.4 |
| `docs/数据库结构文档/04-DEX候选池存储结构.md` | §7「方向层接管后的排序口径」：`rank` 的**降级**与阶段 2 计划、方向层序位**组内唯一且连续**、旧序与方向序位 **54.7 %** 不同、「同名不同义」清单 | 文件末尾 §7.1–§7.3 |
| `docs/数据库结构文档/05-市场观察中间交换池存储结构.md` | §9「方向层接管后的中间池主序口径」：`optimal_pool_id` **降级为候选之一** ＋ 阶段 2 视图化计划、`pool_ids_json` 保留成员不保序、**为什么 `liquidity_index` 必须换口径**、实测读数 | 文件末尾 §9.1–§9.3 |

> **落盘位置提醒**：`docs/` 目录被 **`.gitignore` 排除**（`.gitignore:34 /docs/*`）
> ⇒ 这三份文档**不入库**，只在**本机工作区**更新；
> 入库的**权威副本**在 `plan/tasks/P-01-池子重构/` 的计划文档里（本文件 ＋ `D-117`）。

### 3.2 架构守卫与测试全量通过 ✅

`D-073` §2 规定本任务的回归范围 = `M-3` ＋ `M-10`：

```powershell
cargo test --offline -p directional-pool-manager   # ⇒ 25 passed / 0 failed
cargo test --offline -p manager-db                # ⇒ 5 passed / 0 failed
```

**架构守卫**（Python）：

```powershell
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py flashloan/app_runtime/tests/unit/test_record_layer_guards.py -q
```

⇒ 读数见 `D-074` 的对应条目。

### 3.3 未在同一批次内同时「切换新口径」与「删除旧口径」 ✅

| 批次 | 内容 | 提交 |
|---|---|---|
| 批次 A | **切换**：方向层主序接管闭环主池（代码 ＋ 回滚开关 ＋ 影子复算 ＋ 实盘生效复测） | `25619a42`（代码）→ `T-闭.01` 已归档（2026-11-25） |
| 批次 B | **删除**：`recommend_direction` / `pool_score` 与 `rank` 列 | **尚未施工**（本文件 §4） |

⇒ 两个动作在**两个不同的批次**里，中间有**实盘生效复测**与**稳定窗口** ⇒
**「先切换、后下线」的顺序成立**，**未**在同一批次内发生。

---

## 4. 阶段 2 的**施工清单**（**第一段已施工；第二段已豁免** —— 理由见 §5 与 §7.5）

> 施工前必须**先扩范围**（`D-073` §4「先扩后跑」）：
> 阶段 2 要碰的 `M-5`（候选层）与 `M-1`（池管理器）**不在**本任务当前的模块集合内。

### 4.1 代码改动清单

| # | 文件 | 改动 | 所属模块 |
|---:|---|---|---|
| 1 | `flashloan/tools/candidate_edge_pool_manager/src/lib.rs` | **删** `recommend_direction`（`:1328`）与其两处调用（`:1103` / `:1109`）；**改**为读方向层 `manager_v4_directional_exchange_pools` 的 `pair_direction_rank` 排序 | **`M-5`**（新纳入） |
| 2 | `flashloan/tools/candidate_exchange_pool_manager/src/lib.rs` | **删** `pool_score`（`:1159`）与两处调用（`:1024` / `:1092`）；**改**为读方向层的中间代币对序位 | **`M-5`**（新纳入） |
| 3 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | **同步**审计口径（`:502-558` 读两张旧表）⇒ 否则基线审计会误报 | **`M-1`**（新纳入） |
| 4 | `flashloan/tools/manager_db/migrations/*.sql` | 新增迁移：`manager_dex_candidate_pool_recommendations.rank` **删列**；`manager_market_observation_yz_pairs.optimal_pool_id` **降级为视图**（由方向层 rank-1 派生） | **`M-10`** |
| 5 | `flashloan/app_runtime/tests/unit/test_architecture_guards.py` | 迁移后的表清单与守卫同步 | **`M-9`** |

### 4.2 顺序与回滚

1. **先扩范围**（`D-073` §1 ＋ §1.1 文件级清单 ＋ `D-072` §1.1）⇒ 再施工；
2. **先降级为视图**（保留旧列可回滚）⇒ 观察一个窗口 ⇒ 再**删列**；
3. **回滚脚本随删除动作一并交付**（DDL 回退 ＋ 方向层开关）
   ⇒ 这是 `D-074` 的「回滚脚本已就位」项**在阶段 2 才能闭合**的原因。

### 4.3 阶段 2 的回归范围（扩范围后）

```powershell
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager -p pool-manager -p manager-db
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

### 4.4 阶段 2 的验收判据（`D-074` 的 5 项）

| 判据 | 怎么测 |
|---|---|
| `recommend_direction` / `pool_score` 已删除 | 代码里**搜不到**这两个符号 |
| 行数变化 == 0 | 删除动作前后，闭环 / 跳 / 优化行数**逐字相等**（本轮基线：**174 / 522 / 472**） |
| 主线连续窗口的闭环数 / 利润读数 / 广播产出读数对比已产出 | `stability_window_20261125.jsonl` 的**删除前**窗口 ＋ **删除后**窗口 |
| 未改变任何闭环的身份 / 容量 / 利润 | 逐跳对比 `directional_pool_id` / `directional_pool_rank` / 容量 / 利润 |
| 回滚脚本已就位 | DDL 回退脚本 ＋ 方向层开关，**实测**能回到删除前的行 |

---

## 5. 为什么阶段 2 **本轮不施工**

| 理由 | 说明 |
|---|---|
| **范围要扩到 `M-5` ＋ `M-1`** | 本任务当前的模块集合是 `M-3` ＋ `M-10`；而两个「旧排序的生产者」在 **`M-5`**（候选层），审计口径在 **`M-1`** ⇒ 必须**先扩后跑**（`D-073` §4），**禁止**先改了再补登记 |
| **它**不是**死代码清理** | 实测：旧序与方向序位在 **186 / 340（54.7 %）** 行上**不同**；旧序**仍然**生产候选边的成员与备用池，**并被候选交换层消费**（`candidate_exchange_pool_manager/src/lib.rs:3041-3042` 读 `yc_primary_pool_id` / `yc_backup_pool_ids_json`）⇒ 删掉它**必须**先把候选交换层改接到方向层，否则**中间跳候选池会消失** |
| **涉及实盘 DDL** | 删列 ＋ 视图化是**生产库结构变更** ⇒ 必须走「**先视图（可回滚）→ 观察 → 再删列**」两段式，不能一把梭 |
| **稳定窗口还没走完** | 前置 2 仍在采样中（§2）⇒ 按 `D-074` 的边界验收（「先切换，后下线」），**窗口未走完不得开删** |

⇒ **本轮把阶段 2 做到「可施工」的状态**：清单、顺序、回归范围、验收判据、回滚方式**全部写实**，
前置状态**逐条复核**。窗口走完 ＋ 扩范围 即可直接开工。

---

## 7. 阶段 2 **第一段**的施工记录（2026-11-25 · 已完成）

> **本节是施工后的**事实记录**。§4 的清单是**施工计划**，本节是**施工结果**。
> 施工范围已按 `D-073` §4 **先扩后跑**：`M-5` / `M-1` 已**先**增列入 §1 模块集合，**再**动手。

### 7.1 代码改动（**实际落地**）

| # | 文件 | 实际改动 | 模块 |
|---:|---|---|---|
| 1 | `flashloan/tools/candidate_edge_pool_manager/src/lib.rs` | **删** `recommend_direction` 及其两处调用；改为 `read_directional_ranks` / `directional_ranking` 读 `manager_v4_directional_exchange_pools.pair_direction_rank`；新增稳定标签 `directional_pair_direction_rank` | **`M-5`** |
| 2 | `flashloan/tools/candidate_exchange_pool_manager/src/lib.rs` | **删** `pool_score` 及其两处调用；改为 `directional_group_rank` 读方向层的组内序位 | **`M-5`** |
| 3 | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 审计口径**同步**：三个计数器改读方向层（`pair_direction_rank IS NOT NULL` / `= 1` / 无效池引用）；**方向层表不存在时计数器保持 0**（覆盖缺口，不报市场结论） | **`M-1`** |
| 4 | `flashloan/tools/manager_db/migrations/20261125_017_observation_optimal_pool_view.sql` | **新建**：`optimal_pool_id` **降级为视图**（base 表 + 视图 + **3 个 `INSTEAD OF` 触发器**） | **`M-10`** |
| 5 | `flashloan/tools/manager_db/migrations/rollback/20261125_017_observation_optimal_pool_view.down.sql` | **新建**：DDL 回退 | **`M-10`** |
| 6 | `flashloan/tools/manager_db/src/migrate.rs` ＋ `src/lib.rs` | 注册迁移 `017`（`MIGRATION_IDS` / `MIGRATIONS` / `LATEST_MIGRATION_ID` / `ensure_migration_tables_compatible`）；导出新常量 | **`M-10`** |
| 7 | `flashloan/tools/manager_db/tests/migration_smoke.rs` | **新增 1 个测试**（视图类型 / base 表存在 / 按方向给 rank-1 / 存储值保留 / writer 可写 / unpriced 行 `NULL` / DELETE 经视图生效 / 幂等） | **`M-10`** |
| 8 | `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | **仅改过时注释**（原文说 `recommend_direction` 仍在 ⇒ 改成「候选边层已删除自有排序，改为读取方向层发布的 rank」） | **`M-3`** |

**旧排序符号的终检**：`rg "recommend_direction|pool_score|directional_score" flashloan/ --glob "*.rs"` ⇒ **零命中**（exit 1）。

### 7.2 视图的**两个设计决定**（**必须写清**，否则维护者会改错）

| # | 决定 | 理由 |
|---:|---|---|
| 1 | 视图**按「方向」**（`token_x → token_y`）查 rank-1，**不按 canonical pair** | 视图的答案是**那一行测量的那个方向**的主池；方向层的组是**按方向**编的。**这与 `candidate_exchange` 写路径的 `pool_ids.first()` 语义对齐** —— 旧写路径取的是**有序** `pool_ids_json` 的第一个，即该方向下的首选 |
| 2 | 视图**必须可写**（3 个 `INSTEAD OF` 触发器） | **3 个 writer** 仍在按**表名**写：① `candidate_exchange_pool_manager:2649` ② `closed_loop_manager:5375` ③ `closed_loop_manager:6002`。视图**不可直写** ⇒ 不带触发器会**直接打断生产写入** |

### 7.3 回滚的**语义**（**与直觉相反**，必须写清）

回滚**恢复的是「视图期间实际生效的值」**（方向层 rank-1），**不是**旧的 `liquidity_index` 序的值。

**理由**：视图生效期间，闭环**已经**在走方向层的 rank-1（**522 / 522** 跳全等，见 §7.4）⇒
恢复旧序会把中间跳的主池**移回去**、**改变闭环**；恢复「生效值」才能保证**闭环不变**。

**实测（真实生产数据副本 · 迁移 → 回滚往返）**：

| 读数 | 结果 |
|---|---|
| 对象类型 | `table` → `view` → **`table`** |
| 行数 | **1420 → 1420 → 1420**（与原始**相等**） |
| base 表残留 / view+trigger 残留 | **0** / **0** |
| 恢复后的存储值 | **1420 / 1420** == 视图期间生效的值 |
| 与**原始旧值**不同 | **88**（正是预期的那 88 行） |
| 回滚后 writer | **仍可用** |

### 7.4 迁移前后的**影响实测**（**真实生产数据** · `flashloan/db/local_store_4663.sqlite3`）

> ⚠️ **未在生产库执行**（14.56 GB 且**实时写入**）⇒ 所有 DDL 验证都在**内存副本**上用**真实数据**做的。

| 指标 | 迁移前 | 迁移后 | 判定 |
|---|---:|---:|---|
| `manager_market_observation_yz_pairs` 行数 | **1420** | **1420** | ✅ **相等** |
| 其中 `optimal_pool_id` 非空 | **386** | **386** | ✅ **相等** |
| 值变化行数 | — | **88**（全部 non-null → non-null） | ✅ 无丢失 |
| 视图派生池在**自身方向**的解析成功率 | — | **386 / 386**（`fail_direction = 0`） | ✅ |
| 无 active 方向组的行 | **1034** | **1034**（`optimal_pool_id` 为 `NULL`） | ✅ 与原数据一致 |
| 已发布 hop 的 `base_pool_id` == **方向层 rank-1** | — | **522 / 522**（`same=522`、**`diff=0`**、`miss=0`） | ✅ **逐跳一致** |
| `pool_selection_basis` | — | hop 1/2/3 各 **174** 条**全部** `directional_pair_direction_rank:1` | ✅ |
| 中间跳解析（1002 个 live 中间跳 × 视图派生池） | — | **ok = 1002 / FAIL = 0** | ✅ |

**「88 行值变化」的成因拆解**（**全部**在 `scope_key = 'candidate_exchange'`）：

| 成因 | 行数 | 说明 |
|---|---:|---|
| **真实重排** | **52** | 旧值在**该方向**的方向层序位是 **2**（不是 1）⇒ 新值才是 rank-1 |
| **并列次序差异** | **36** | 旧值**在别处**也是 rank-1（两池在不同方向各为 rank 1）⇒ 旧序按 `liquidity_index` desc、新口径按 `base_pool_id` asc 决胜 |

**示例**：`0x387bf619…`（`liquidity_index` 0.1182）旧序第一；`0x24107d…`（0.0146）在方向层 `0x0 → 0x5fc5` 方向为 rank 1。

### 7.5 阶段 2 **第二段**（**删 `rank` 列**）· **已豁免**（`M-4` 决策已做）

> **`M-4` 决策（2026-11-25）：`rank` 列保留，「删列」明确豁免。**
> 完整论证与实测读数见 [`D-139`](../../../docs/D-139-M4决策_rank列保留与删列豁免_20261125.md)。

| # | 原阻塞 | **现状** |
|---:|---|---|
| 1 | **`M-4` 决策未做** | ✅ **已解除** —— 决策结论：**保留 `rank`**（它**不再承载旧排序**，而是**方向层序位的派生组内序号**；删它**不是**死代码清理，而是拆除一条**正在服役的排序契约**） |
| 2 | **技术上不可 `DROP COLUMN`** | ➖ **不再适用** —— 不删列 ⇒ 无需重建表（`rank` **继续在 PRIMARY KEY 里**，**继续提供组内唯一性**） |
| 3 | **观察窗口未走** | ➖ **不再适用** —— 不删列 ⇒ 无需再观察一个窗口 |
| 4 | **旧序 vs 新序的差异面** | ✅ **已被新口径吸收** —— 差异面由方向层 `pair_direction_rank` 承担（`candidate_edge_pool_manager` 已改接方向层） |

**补充实测（`D-139` §3.2，只读 · 实盘）**：

| 读数 | 值 |
|---|---:|
| `rank` **在 PRIMARY KEY 里** | **true** |
| `rank` 稠密 1..N 的组 | **216 / 216** |
| `depth_score` 与 `rank` **排序不一致**的组 | **82 / 216** |
| `depth_score` 与 `rank` **相等**的行 | **0 / 340** |
| `rank = 0` 的行 | **0**（闭环层有效性校验依赖该列） |

⇒ `depth_score` **不能**顶替 `rank`（REAL ／ 可空 ／ 可并列 ／ 排序不等价）
⇒ **保留 `rank`** 是唯一安全选择。

⇒ **阶段 2 完结**：第一段（降级为视图）**已施工**，第二段（删列）**已豁免**
⇒ `T-方储.01` 的**全部范围完成** ⇒ **可归档**。

---

## 8. 本文件的**诚实边界**

| 边界 | 说明 |
|---|---|
| 稳定窗口是**采样**，不是**全量** | 采样器读的是 HTTP overview（闭环 / 复核 / 一致性 / 版本），**不是**逐跳逐列的深度对比；「无回退」的结论**限于**这些观测项 |
| DDL **未在生产库执行** | 生产库 **14.56 GB** 且**实时写入** ⇒ 迁移 `017` 只在**内存副本**上用真实数据验证；**生产库执行仍需授权**（`D-072` §1「需授权 = 是（写库）」） |
| 阶段 2 **第二段已豁免** | **删 `rank` 列**的技术前提（表重建）仍然成立，但 `M-4` 决策已裁定**保留该列**（`D-139`）⇒ 该动作**不再是待办**，也**不需要**实测 |
| 三份结构文档**不入库** | `docs/` 被 `.gitignore` 排除 ⇒ 只在本机工作区更新（§3.1 已说明） |
| 阶段 2 的**代码改动清单未验证** | §4 是**施工计划**，**不是**已完成的改动；每一行都需要在扩范围后**实测**验证 |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/README.md` -->

# `T-方储.01`｜旧方向键与旧排序降级下线 · 技术文案（docs）

> **本目录是 `T-方储.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

旧方向键 / 旧排序的降级路径与不可逆回滚

## 文件

| 文件 | 内容 |
|---|---|
| [`02-目标架构与表结构.md`](#d075) | 本任务相关的技术文案 |
| [`03-迁移实施与验收.md`](#d076) | 本任务相关的技术文案 |
| [`D-117`](#d117) | **阶段 1**：消费者清单（92 文件）／最后读取时间戳／逐列决策 |
| [`D-131`](#d131) | **阶段 2**：前置状态复核 ＋ 施工清单 ＋ 稳定窗口读数 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
