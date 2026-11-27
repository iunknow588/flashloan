# `T-核.02`｜内存大对象方向化（池子数组＝方向池） · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-核.02` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 内存大对象方向化（池子数组＝方向池） |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-核.02` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d156"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/D-156-PLAN.md` -->

# D-156 `T-核.02`｜内存大对象方向化（池子数组＝方向池） · 开发计划（**独立 · 自包含**）

> **本目录是 `T-核.02` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-核.02`** |
| **本任务验收编号** | **`AC-核.02`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`核`**（`M-13`） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-核.01`（运行时维护链接线） |

**本任务涉及的模块**：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-13` | 运行时内核与运行时接线（`flashloan/rs-src/`） | `核` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1 / §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-核.02`** |
| **验收编号（纯 ID）** | **`AC-核.02`** |
| **标题** | 内存大对象方向化（池子数组＝方向池） |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-3`（方向层） |
| **需授权** | 否（纯代码） |
| **一句话目标** | 让运行时内存对象的池子数组**装方向池**（`方向池 ＝ V4 基础池 ＋ 方向值`），基础池事实作为子对象随行携带，并把「选池」从「基础池 id 查找」升级为「按方向解析」 |

### 1.1 背景与问题（**用户口径 vs 实测**）

用户口径（两轮都明确说了）：

> 1. 「**所有的池子对象都应该包含方向信息。**」
> 2. 「**V4 池子信息只是方向池子的子对象，V4 池子 ＋ 方向值 ＝ 方向池子。**」
> 3. 「**闭环引用的是有向池，不应该是基础池，请确认。是有向池子里面包含着基础池信息。**」
> 4. 「**内存对象中的池子信息数组也是方向池子，不是基础池子。**」

**实测（`D-143` §2.1）：第 1、2、3 条成立，第 4 条不成立。**

```text
管理层（closed_loop_manager）：
  闭环跳 522/522 带 directional_pool_id / hop_direction / directional_pool_rank   ✅
  基础池 id 在闭环里的唯一用途 = 到方向层查一行                                     ✅

运行时内核（flashloan/rs-src）：
  sqlite_read::load_v4_exchange_pool_registry_rows()   sqlite_read.rs:74
    SELECT pool_id, token0, token1, pool_manager FROM v4_exchange_pools   ← 基础池表

  V4PoolRegistry {
    pool_by_id: HashMap<String, PoolRegistryEntry>,   ← 键 = 基础池 id
    liquidity_by_id: …, pool_ids_by_token: …,
  }
  PoolRegistryEntry { token0, token1, pool_manager }   ← 无方向、无容量、无序位
```

**为什么这不是「文档没写」而是「代码没做」**：
`D-008` §3.1 的盘点只覆盖了 **Rust 管理器 crate**，**漏了 `rs-src` 内核**
⇒ 盘点口径本身有缺口，本任务把它补上（见 `D-159` §2）。

### 1.2 实测：换成方向池**不会**丢覆盖（**这是本任务可以立刻做的依据**）

| 读数（实盘，2026-11-25，只读） | 值 |
|---|---:|
| 两张 scope 表基础池行（`core4` 488 ＋ `candidate_exchange` 494） | **982** |
| 其中不同 `pool_id` | **496** |
| 方向层行 | **692** |
| 其中不同 `base_pool_id` | **178** |
| 方向层覆盖的基础池 | **178 / 496 ＝ 35.89 %** |
| **方向层未覆盖的基础池** | **318** |
| ├ 动态费率池 | **316** |
| ├ 带 hook 的池 | **318** |
| └ 代币序非规范 | **0** |
| **被系统约定排除的并集** | **318** |
| **可准入基础池（496 − 318）** | **178** ＝ **方向层基础池数** |

⇒ **缺的 318 个池，一个不多一个不少，正是系统约定（固定费率 · 无 hook）排除掉的池。**
换句话说：**方向层就是「系统真正考虑的池」的全体**，
内存对象装方向池 ＝ **在运行时层自动落实了那条约定**，
而不是丢掉 318 个池。

**闭环路径的覆盖率（决定复核会不会被「查无此池」打断）**：

| 读数 | 值 |
|---|---:|
| 闭环跳行 | **522** |
| 跳用到的不同基础池 | **49** |
| **方向层覆盖到的** | **49 / 49**，缺 **0** |
| ├ `candidate_exchange` scope | **45 / 45** |
| └ `core4` scope | **46 / 46** |
| 跳用到的池在流动性快照表里的覆盖 | **49 / 49**，缺 **0** |

### 1.3 不做什么（**范围外**）

* **不**改方向层的投影规则、准入判定、容量/利润口径、序位、幂等键 —— 一字不改。
* **不**改闭环层的选池逻辑（`M-4`）—— 闭环层本来就已经只引用有向池。
* **不**改闭环层的 USDG 估值口径（那是 `T-闭.02` 的事）。
* **不**改运行时维护链的 worker 编排（那是 `T-核.01` 的事）。
* **不**做跨月统计、落盘失败降级之类的低概率路径（按用户口径：先留占位）。
* **不**动 `flashloan/tools/**`（管理层 crate）与 `flashloan/src/core/db/builders.py`（并行施工保护）。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-13` | 运行时内核 | `flashloan/rs-src/src/sqlite_read.rs` | 新增方向池装载入口 ＋ 方向层存在性检查 |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/worker_pool.rs` | `V4PoolRegistry` 改为装**方向池**；基础池事实降为子对象；选池改为**按方向解析** |

### 2.2 执行步骤

| 步 | 动作 | 完成判据 |
|---:|---|---|
| **1** | 读 `D-158`，确认每条验收项的当前状态 | 全部条目已知 |
| **2** | 实盘**只读**量化「方向层对基础池的覆盖」与「闭环跳的覆盖」 | 读数可复算（`probe_directional_registry_coverage_20261125.py`） |
| **3** | `sqlite_read` 新增 `load_v4_directional_pool_registry_rows` ＋ `directional_pool_table_exists` | 读的是 `manager_v4_directional_exchange_pools` |
| **4** | `worker_pool`：`PoolRegistryEntry` → `BasePoolFacts`（子对象）＋ `DirectionalPool`（方向池） | 一个基础池在两个方向下**各有一行** |
| **5** | `V4PoolRegistry` 的池数组改为 `directional_by_id`，基础视图由方向行**派生** | **不再读基础池表** |
| **6** | 新增 `(基础池, 方向) → 方向池` 座位索引（取**最佳序位**的行） | 跨 scope 的同一基础池有确定席位 |
| **7** | `pool_manager_for_selected_keys` → `pool_manager_for_selected_directions(keys, directions)` | 方向与路由不符 ⇒ **拒绝**，不猜 |
| **8** | `workerContext` 暴露 `directionalPoolIndexCount` / `basePoolIndexCount` / `directionalLayerPresent` / `groupDirectionalPoolIds` | 诊断可见 |
| **9** | 跑 `cargo test --offline -p flashloan-rs` | 全绿，且**新增**方向解析用例 |
| **10** | 实盘**只读**复核：闭环 **522** 跳按 `(基础池, 方向)` 逐跳解析 | 解析成功 **522 / 522**，缺 **0** |
| **11** | 逐条勾选 `D-158` | 每条都有实测读数 |

### 2.3 技术文案（`docs/`）

* `D-159` —— 装载口径、覆盖读数、
  「为什么缺的 318 个池正是被约定排除的池」、以及**诚实边界**。

### 2.4 三条执行纪律

1. **编译 ≠ 测试**：本任务改了 `rs-src` 的注册表结构 ⇒ `cargo build --offline -p flashloan-rs` 先过，
   再 `cargo test --offline -p flashloan-rs`。
2. **不静默降级**：数据库没有方向层时，**明确报出来**（`directionalLayerPresent = false`），
   而不是让注册表空掉、系统看起来像「没找到机会」。
3. **实盘一律只读**：`mode=ro` ＋ `PRAGMA query_only = ON`；本任务**不写生产库**。

---

## 3. 完成定义

* [ ] `D-158` 中**全部** `- [ ]` 变为 `- [x]`
* [ ] `D-157` 的**全部**回归命令**全绿**
* [ ] 每条已勾选项都有**可复算的实测读数**
* [ ] 内存对象**不再**读基础池表（`v4_exchange_pools` 在 `rs-src` 的注册表路径上**零引用**）
* [ ] **未放宽**任何既有门禁（投影规则 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联）
* [ ] **未**触碰**不属于** `M-13` 的模块

---

## 4. 归档

```powershell
python plan/runtime_evidence/tools/archive_task_20261124.py T-核.02 --dry-run
python plan/runtime_evidence/tools/archive_task_20261124.py T-核.02
```

---

## ③ 修改范围守护

<a id="d157"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/D-157-SCOPE_GUARD.md` -->

# D-157 `T-核.02`｜内存大对象方向化 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.02` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时内核 | `flashloan/rs-src/src/sqlite_read.rs` | 方向池装载入口 ＋ 方向层存在性检查 | **是** |
| `M-13` | 运行时内核 | `flashloan/rs-src/src/worker_pool.rs` | 注册表方向化 ＋ 按方向解析选池 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-13` | 运行时内核 | `cargo test --offline -p flashloan-rs` | 全绿（基线 **220** passed） |

```powershell
cd flashloan/rs-src
cargo test --offline -p flashloan-rs
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；闭环层本来就已经只引用有向池，本任务不改闭环口径 |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的投影规则、准入判定、容量口径一字不改 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| `flashloan/src/dex/runtime/v4_maintenance_workers.py` | 属 `T-核.01`（运行时维护链接线），本任务不碰编排 |
| `flashloan/tools/{broadcast_manager,composite_manager,manager_api}/**` | 属其它模块；本任务不改它们的契约 |
| 任何 `chain.json` / `.env` / `.env.example` | 本任务**不引入**任何运行期参数 |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-156` §2.1；
2. **记录原因** —— 在 `D-156` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-13` 代码 | 否 | 纯代码 |
| 实盘**只读**复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 写生产库 | **否（本任务不写）** | 本任务**不写**任何表；若后续需要 ⇒ 先扩范围并单独申请 |

---

## 6. 范围守护自检

* [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
* [ ] §2 的**全部**命令**已执行**且**全绿**
* [ ] §3 的禁止触碰项**确未被触碰**
* [ ] `flashloan/rs-src` 的注册表路径上**已无** `v4_exchange_pools` 的引用
* [ ] **未**因本任务跑过**全量**测试

---

## ④ 测试目标 check list

<a id="d158"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/D-158-checklist.md` -->

# D-158 `T-核.02`｜内存大对象方向化 · 验收清单

> **本文件是 `T-核.02` 的全部验收框。** 编号 `AC-核.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-13` 的现有单测全绿（基线 **220** passed）

---

## 1. 根因与定位

- [x] 内存大对象的池子数组**改前**确实是基础池

      > **读数（改前）**：`worker_pool.rs` 的 `V4PoolRegistry.pool_by_id` 键 = 基础池 id，
      > 条目 = `PoolRegistryEntry { token0, token1, pool_manager }`（无方向）；
      > 装载口 `sqlite_read.rs:74 load_v4_exchange_pool_registry_rows` 读 `v4_exchange_pools`。

- [x] 方向层的**覆盖缺口**已量化，且缺口**全部**来自系统约定

      > **读数**：496 个基础池里方向层覆盖 **178**，缺 **318**；
      > 缺的这 **318** ＝ 动态费率 **316** ∪ 带 hook **318**（并集 **318**）
      > ⇒ 可准入集合 **178** ＝ 方向层基础池数 **178**。

- [x] 闭环路径**不会**因换装载源而「查无此池」

      > **读数**：闭环 **522** 跳用到的 **49** 个基础池，方向层覆盖 **49 / 49**，缺 **0**
      > （`candidate_exchange` 45/45、`core4` 46/46）。

- [x] 方向层的**方向语义**与运行时的 `zeroForOne` **同义**

      > **读数**：方向层 `direction = 0` ⇒ `zero_for_one = 0`（**346** 行）；
      > `direction = 1` ⇒ `zero_for_one = 1`（**346** 行）；
      > 即 **0 = 高→低**、**1 = 低→高**，与用户口径一致。

---

## 2. 实施

- [x] 装载口换成方向层

      > **读数**：`sqlite_read::load_v4_directional_pool_registry_rows` 读
      > `manager_v4_directional_exchange_pools`；`worker_pool` 的注册表路径上
      > **不再**出现 `v4_exchange_pools`。

- [x] 池数组装的是**方向池**，基础池是**子对象**

      > **读数**：`DirectionalPool { directional_pool_id, direction, scope_key, base: BasePoolFacts, … }`；
      > `BasePoolFacts` 只作为 `base` 字段存在，**没有**第二个基础池数组。

- [x] 一个基础池在**两个方向**下各有一行

      > **读数**：单元测试 `v4_pool_registry_indexes_by_id_and_token` 断言
      > `directional_pool("0xAAA", 0)` 与 `directional_pool("0xAAA", 1)` 各自命中一行，
      > 且方向 `2` **不存在**。

- [x] 选池**按方向**解析，方向不符 ⇒ 拒绝

      > **读数**：单元测试 `the_selected_pool_manager_is_resolved_in_the_chosen_direction`：
      > 正反两个方向各自解析到本方向的行；方向层没有的池 ⇒ **拒绝**（`None`）。

- [x] 未改**门禁**

      > **读数**：本任务**只换装载源与解析方式**，
      > 投影规则 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联**一字未改**。

- [x] 方向层缺失时**明确报出来**，不静默变空

      > **读数**：`directional_pool_table_exists` ＋ `ContextCacheData.directional_layer_present`
      > ＋ `workerContext.directionalLayerPresent`。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p flashloan-rs` 全绿

      > **读数**：**221 passed / 0 failed**（基线 **220** ＋ 本任务新增 **1** 条方向解析用例；
      > 新用例 `the_selected_pool_manager_is_resolved_in_the_chosen_direction`）。

- [x] `cargo build --offline -p flashloan-rs` 成功

      > **读数**：exit code **0**（`cargo build --offline -p flashloan-rs`）。

---

## 4. 实盘（**只读**）

- [x] 闭环 **522** 跳按 `(基础池, 方向)` **逐跳**解析成功

      > **读数**（`audit_directional_hop_resolution_20261125.py`）：解析成功 **522 / 522**，缺 **0**；
      > `zero_for_one` 与跳方向不一致 **0**；行的自身输入/输出代币与方向不一致 **0**。
      > **⚠️ 另记**：座位行与跳已发布的 `directional_pool_id` 不同 **338**（跨 scope，
      > 基础事实逐列一致 ⇒ 运行时读数不受影响）—— 详见 `D-159` §2.8。

- [x] 复核全程**只读**

      > **读数**：`mode=ro` ＋ `PRAGMA query_only = ON`；实盘库**零写入**。

---

## 5. 诚实边界

- [x] 本任务**未**改动闭环层 / 方向层 / 运行时维护链

      > **读数**：改动文件**只有** `flashloan/rs-src/src/sqlite_read.rs` ＋
      > `flashloan/rs-src/src/worker_pool.rs`；三个缺口分别归属 `M-4`（`T-闭.02`）与
      > `M-13`（`T-核.01`），见 `D-143` §3。

- [x] 「运行期生效」**如实登记**（实盘**已重启** ⇒ **已生效**）

      > **读数（2026-11-26 14:3x）**：实盘 runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制
      > ⇒ 旧的「runtime 24524 自 2026-11-25 17:14:00 起未重启」**已过时**。
      > **新口径已在运行期生效**：方向层槽 `v4_directional_pool_worker` `status = ok` ／ `mode = complete` ／
      > `pool_count = 692`（`refreshed_at = 2026-11-26T06:19:21Z`）；
      > 闭环层槽 `v4_closed_loop_worker` `status = ok` ／ `mode = complete` ／ `pool_count = 199`
      > （**06:20:33Z**）⇒ 闭环 **522 / 522** 跳能按 `(directional_scope_key, directional_pool_id, hop_direction)`
      > 关联到方向池表。
      > 探针：`probe_maintenance_slots_20261126.py` ＋ `verify_restart_gates_20261126.py`。

- [x] 内存对象方向化**不**等于「方向层已经是最新的」

      > **读数**：`inv-9` 仍 **FAIL**（**83** 行 / **994** 格漂移），归 `T-方.02` 负责。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/README.md` -->

# `T-核.02`｜内存大对象方向化（池子数组＝方向池）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.02`（纯 ID）　**验收**：`AC-核.02`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-核.02` / `AC-核.02` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时内核与运行时接线）），该索引号段内第 `02` 个。（详见 `D-156` §1.0）

**改动模块（= 回归范围）**：`M-13`

**前置模块**：`M-3`（方向层）　　**需授权**：否（纯代码，不写生产库）

## 文件

| 文件 | 内容 |
|---|---|
| `D-156` | 本任务的开发计划与回归范围（**自包含**） |
| `D-157` | 本任务的修改范围守护与逐模块回归命令 |
| `D-158` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（装载口径 / 实盘覆盖读数） |

## 一句话

> 用户口径：「**内存对象中的池子信息数组也是方向池子，不是基础池子。**」
> 实测**不成立**：运行时内核的 `V4PoolRegistry` 读的是基础池表 `v4_exchange_pools`，
> 字段只有 `pool_id/token0/token1/pool_manager` —— **零方向信息**。
> 本任务把内存对象的池子数组换成**方向池**：
> `方向池 ＝ V4 基础池 ＋ 方向值`，基础池事实作为方向池的**子对象**随行携带。

## 独立回归

```powershell
cd flashloan/rs-src
cargo test --offline -p flashloan-rs
```

**读数（2026-11-25 基线）**：两张 scope 表共 **982** 行 / **496** 个基础池，
方向层 **692** 行 / **178** 个基础池；缺的 **318** 个**全部**是动态费率或带 hook 的池
（可准入集合 **178** ＝ 方向层基础池数）；闭环 **522** 跳用到的 **49** 个基础池
方向层覆盖 **49 / 49**，缺 **0**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d159"></a>

### `D-159-内存大对象方向化_装载口径与实盘覆盖_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/docs/D-159-内存大对象方向化_装载口径与实盘覆盖_20261125.md` -->

# D-159 内存大对象方向化：装载口径与实盘覆盖

> **本文档是一次实施的技术文案。** 它回答两件事：
> 内存对象的池子数组**改前**装的是什么、**改后**装的是什么；
> 以及「换成方向池会不会丢覆盖」这个必须先回答的问题。
>
> 依据：[D-143 闭环引用有向池核实与三处未收口](../../../docs/D-143-闭环引用有向池核实与三处未收口_20261125.md)、
> [D-008 内存对象模型与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)、
> [T-方.02 方向层批量物化入口](../../../../../archive/任务归档_T-方.02_方向层按链批量物化入口_20261128.md#d144)。

---

## 0. 一句话

**改前**：内存对象的池子数组是**基础池**（`pool_id/token0/token1/pool_manager`，零方向信息）。
**改后**：内存对象的池子数组是**方向池**（`方向池 ＝ V4 基础池 ＋ 方向值`），
基础池事实作为方向池的**子对象**随行携带 —— 内存对象里**不再有第二个基础池数组**。

而「换成方向池会不会丢覆盖」这个问题的答案是：**不会**，
因为方向层覆盖的 **178** 个基础池 **恰好就是**系统约定（固定费率 · 无 hook）之后
**可准入**的那 **178** 个 —— 差的 **318** 个，一个不多一个不少，正是被约定排除掉的池。

---

## 1. 装载口径（改前 vs 改后）

### 1.1 改前（基础池）

```text
sqlite_read::load_v4_exchange_pool_registry_rows()      sqlite_read.rs:74
  SELECT pool_id, token0, token1, pool_manager
  FROM v4_exchange_pools
  WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3

V4PoolRegistry {
  pool_by_id:        HashMap<基础池 id, PoolRegistryEntry>,   ← 键 = 基础池 id
  liquidity_by_id:   HashMap<基础池 id, LiquidityRegistryEntry>,
  token_index_count: usize,
  pool_ids_by_token: HashMap<代币, HashSet<基础池 id>>,
}
PoolRegistryEntry { token0, token1, pool_manager }           ← 无方向 / 无容量 / 无序位
```

**后果**：同一个基础池的「高→低」与「低→高」两行**无法区分**；
方向只能靠代币序在运行时反推 —— 这正是 `D-008` §3.3 说的
「方向应该被物化出来的最强证据」**没有被物化**。

### 1.2 改后（方向池）

```text
sqlite_read::load_v4_directional_pool_registry_rows()      新增
  SELECT directional_pool_id, base_pool_id, direction, scope_key,
         pool_manager, pool_key_json, token0, token1, fee, tick_spacing, hooks,
         pair_key_id, input_token_key_id, output_token_key_id, zero_for_one,
         pair_direction_rank, pool_status
  FROM manager_v4_directional_exchange_pools
  WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
  ORDER BY scope_key, pair_key_id, direction, pair_direction_rank, base_pool_id

DirectionalPool {                       ← 池数组的元素
  directional_pool_id, direction, scope_key,
  base: BasePoolFacts {                 ← 基础池 = 方向池的【子对象】
      base_pool_id, token0, token1, pool_manager,
      fee, tick_spacing, hooks, pool_key_json,
  },
  input_token_key_id, output_token_key_id, zero_for_one,
  pair_direction_rank, pool_status,
}

V4PoolRegistry {
  directional_by_id:              HashMap<方向池 id, DirectionalPool>,  ← 主数组
  base_by_id:                     HashMap<基础池 id, BasePoolFacts>,     ← 【派生】索引
  directional_id_by_base_direction: HashMap<(基础池 id, 方向), 方向池 id>, ← 座位索引
  liquidity_by_id, token_index_count,
  pool_ids_by_token,              HashMap<代币, HashSet<基础池 id>>,
  directional_ids_by_token,       HashMap<代币, HashSet<方向池 id>>,
}
```

**三条口径**：

| # | 口径 | 落点 |
|---|---|---|
| 1 | **一个池对象 ＝ 一个方向** | `DirectionalPool.direction`；同一基础池在两个方向下**各有一行** |
| 2 | **基础池是子对象** | `DirectionalPool.base`；内存对象里**没有**第二个基础池数组 |
| 3 | **基础视图是派生** | `base_by_id` 由方向行**派生** ⇒ 两个数组**不可能**各说各话 |

### 1.3 「座位索引」为什么需要

同一个基础池可以同时落在**两个 scope**（`core4` 与 `candidate_exchange`），
两个 scope **各自**给方向行排序 ⇒ 同一个 `(基础池, 方向)` 可能有**多行**。

`directional_id_by_base_direction` 的席位规则：**取最佳序位（`pair_direction_rank` 最小）的那行**；
没有序位的行**永远不能**压过有序位的行。

⇒ 席位**确定** ⇒ 「这一跳用哪个方向池」这个问题**有唯一答案**，不随行序抖动。

### 1.4 选池从「查基础池」升级为「按方向解析」

```text
改前：pool_manager_for_selected_keys(keys)
        key → pool_id → pool_by_id[pool_id].pool_manager
        （方向完全没参与；路由说错方向也照样解析成功）

改后：pool_manager_for_selected_directions(keys, directions)
        key → pool_id
        directions[i]（= zeroForOne）→ 方向（true ⇒ 1 低→高，false ⇒ 0 高→低）
        → directional_pool_for_key(pool_id, 方向)
              ├ 座位命中？          否 ⇒ 拒绝（不猜）
              └ 该行的代币序 == key 的 currency0/currency1？ 否 ⇒ 拒绝
        → 三跳的 pool_manager 必须**同一个**，否则拒绝
```

**为什么这是「拒绝」而不是「降级」**：方向层没有的方向 ⇒ 这个方向**不存在**；
拿另一个方向顶上 ⇒ 复核会按**相反的方向**定价 ⇒ 得到的利润是**假的**。
宁可这一跳不产生复核，也不产生一个方向相反的复核。

### 1.5 诊断位

`workerContext` 新增：

| 字段 | 含义 |
|---|---|
| `directionalPoolIndexCount` | 方向池数组长度（**主数组**规模） |
| `basePoolIndexCount` | 派生基础视图长度 |
| `directionalLayerPresent` | 数据库**有没有**方向层（没有 ⇒ 明确报出来，不静默变空） |
| `groupDirectionalPoolIds` | 路由的**方向池**身份（基础池 id 只说明「哪些池」，方向池 id 才说明「什么方向」） |

`poolIndexCount` 保留，但**语义变为方向池数组长度** ⇒ 读数**不可与旧值直接比较**。

---

## 2. 覆盖读数（**实盘只读**，2026-11-25）

脚本：`plan/runtime_evidence/tools/probe_directional_registry_coverage_20261125.py`
报告：`plan/runtime_evidence/reports/directional_registry_coverage_20261125.json`
打开方式：`mode=ro` ＋ `PRAGMA query_only = ON`（**实盘库零写入**）。

### 2.1 方向层 vs 基础池表

| 读数 | 值 |
|---|---:|
| `v4_exchange_pools_core4` 行 | **488** |
| `v4_exchange_pools_candidate_exchange` 行 | **494** |
| 合计行 / 不同 `pool_id` | **982** / **496** |
| 方向层行 / 不同 `base_pool_id` | **692** / **178** |
| 方向层覆盖的基础池 | **178** |
| **方向层未覆盖的基础池** | **318** |
| 覆盖率 | **35.89 %** |
| 方向层有、基础池表没有的 `base_pool_id` | **0** |

### 2.2 缺口 **318** 的构成（**决定性读数**）

```text
不同基础池            496
  动态费率池          316
  带 hook 的池        318
  代币序非规范         0
  ────────────────────────
  被排除并集          318
  可准入基础池        178   ← 与方向层的基础池数【完全一致】
```

⇒ 方向层**就是**「系统真正考虑的池」的全体
（用户的口径：「当前系统考虑的池子都是固定费率的，动态费率或者带有 hook 的都被排除」）。

**副产品**：内存对象装方向池 ＝ **在运行时层自动落实了那条约定** ——
以前基础池数组里躺着 **318** 个系统明说不该考虑的池（动态费率 / 带 hook），
它们只是「没被选中」而不是「不在场」；现在它们**根本不在内存对象里**。

### 2.3 身份一致性（覆盖集上逐列比对）

| 列 | 不一致行数 |
|---|---:|
| `pool_manager` | **0** |
| `token0` | **0** |
| `token1` | **0** |
| `fee` | **0** |
| `tick_spacing` | **0** |
| `hooks` | **0** |

⇒ 方向行的基础事实与基础池行**逐列一致** ⇒
「基础池是方向池的子对象」在**数据面**成立。

### 2.4 闭环路径的覆盖（**决定复核会不会被打断**）

| 读数 | 值 |
|---|---:|
| 闭环跳行 | **522** |
| 跳用到的不同基础池 | **49** |
| **方向层覆盖到的** | **49** |
| **缺** | **0** |
| └ `candidate_exchange` scope | 覆盖 **45 / 45**，缺 **0** |
| └ `core4` scope | 覆盖 **46 / 46**，缺 **0** |
| 跳用到的池在 `v4_exchange_pool_liquidity_snapshots` 里 | **49 / 49**，缺 **0** |

⇒ **改装载源不会让任何一跳「查无此池」**。

### 2.5 方向语义（与运行时 `zeroForOne` 同义）

```text
direction = 0 → zero_for_one = 0   （346 行）  ⇒ 输入 token1、输出 token0 ⇒ 高→低
direction = 1 → zero_for_one = 1   （346 行）  ⇒ 输入 token0、输出 token1 ⇒ 低→高
```

⇒ 与用户口径（「0 表示从高地址兑换低地址，1 表示从低地址兑换高地址」）**完全一致**；
也 ⇒ 运行时的 `zeroForOne` 布尔可以直接映射成方向层的 `direction` 整数。

### 2.6 方向层自带的快照列

方向层行**自带**下列列（本任务**未**依赖它们，但它们说明方向层是自洽的）：

`sqrt_price_x96`、`tick`、`liquidity`、`price`、`depth_status`、
`input_capacity_raw`、`input_capacity_measured`、
`directional_output`、`directional_output_measured`、`depth_capacity_raw`

⇒ 「方向池 = 基础池 + 方向」在**数据面**还多带了一层**方向特有的容量与产出**。

### 2.7 闭环 **522** 跳按 `(基础池, 方向)` 的**逐跳解析**（**新代码路径的数据前提**）

脚本：`plan/runtime_evidence/tools/audit_directional_hop_resolution_20261125.py`
报告：`plan/runtime_evidence/reports/directional_hop_resolution_20261125.json`

| 读数 | 值 |
|---|---:|
| 闭环跳行 | **522** |
| **解析成功** | **522 / 522** |
| **方向层里查无此 `(基础池, 方向)`** | **0** |
| 行的 `zero_for_one` 与跳的方向**不一致** | **0** |
| 行的**自身输入/输出代币**与方向**不一致** | **0** |
| 座位行与跳已发布的 `directional_pool_id` **不同** | **338**（见 §2.8） |

⇒ 新代码路径依赖的三个数据前提（**有行**、**方向一致**、**代币与方向自洽**）在实盘**全部成立**。

### 2.8 **338** 跳的座位行落在**另一个 scope**（**如实登记 · 不掩盖**）

跳发布的 `directional_pool_id` 形如
`dpool:v1:robinhood:4663:core4:<基础池>:<方向>` —— 它**带 scope**。
而运行时的注册表按 `(基础池, 方向)` 取**跨 scope 的最佳序位**行，
所以 **338 / 522** 跳的座位行落在**另一个 scope** 的同名方向行上。

**这不影响运行时的任何读数**，理由是**实测过的**：

| 事实 | 依据 |
|---|---|
| 同一个基础池在两个 scope 里的**基础事实逐列一致** | §2.3：`pool_manager` / `token0` / `token1` / `fee` / `tick_spacing` / `hooks` 不一致均为 **0** |
| 运行时用到的只有 **pool_manager**、**代币序**、**方向** | `pool_manager_for_selected_directions` ＋ `directional_pool_for_key` |
| 方向与代币的自洽性在**所有**方向行上成立 | §2.7：两处不一致均为 **0** |

⇒ 运行时读到的 pool manager、代币对、方向**与跳自己发布的行完全一样**。

**但这条差异必须登记**，因为它是一条**口径边界**：
「按 `(基础池, 方向)` 解析」得到的是**方向池**，**不是**「跳发布的那一行」；
要让运行时**逐跳**精确绑定到跳发布的那一行，
需要闭环目录（内存里的闭环快照）**逐跳携带** `directional_pool_id` ＋ `directional_scope_key` ——
那是**闭环目录的字段扩展**，**不属于**本任务（`T-核.02` 只改注册表结构）。

---

## 3. 诚实边界

* §2 的全部读数来自**实盘只读**查询，脚本与报告路径见上；
  **未**做任何写入，**未**重启实盘进程。
* 「改后」的行为由**单元测试**覆盖（方向解析、方向不符拒绝、方向层缺失时明确报出）；
  **运行期生效**取决于实盘 runtime 进程何时重启 ⇒ 本轮**未**重启，
  「实盘生效复测」**如实登记为剩 1 项**，**不代任何其它任务关闭验收项**。
* 内存对象方向化**不**解决方向层的**新鲜度**问题：方向行快照列相对流动性快照表的漂移
  （`inv-9` FAIL：**83** 行 / **994** 格）由 `T-方.02` 负责。
* 内存对象方向化**不**改闭环层的 USDG 估值口径（估值读基础池表、折叠读方向行，
  两个快照）—— 那是 `T-闭.02` 的事。
* 内存对象方向化**不**改运行时维护链（闭环重建仍写遗留表）—— 那是 `T-核.01` 的事。
* 「可准入基础池 178 ＝ 方向层基础池数 178」这条等式是**当前实盘**的读数；
  方向层若落后于基础池表（漂移），该等式会**先于**本任务失效 ⇒ 它是 `T-方.02` 的健康指标。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.02/docs/README.md` -->

# `T-核.02` 技术文案

| 文件 | 内容 |
|---|---|
| [`D-159`](#d159) | 装载口径（改前 vs 改后）、实盘覆盖读数、「缺的 318 个池正是被约定排除的池」、诚实边界 |

---
