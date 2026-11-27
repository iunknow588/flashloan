# `T-闭.03`｜基础池内化：方向池是唯一的池对象 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-闭.03` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 基础池内化：方向池是唯一的池对象 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-闭.03` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d161"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.03/D-161-PLAN.md` -->

# D-161 `T-闭.03`｜基础池内化：方向池是唯一的池对象 · 开发计划（**独立 · 自包含**）

> **本目录是 `T-闭.03` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-闭.03`** |
| **本任务验收编号** | **`AC-闭.03`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`闭`**（`M-4` 闭环层） |
| **序号** | 第 `03` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-闭.01`、`T-闭.02`（方向层接线 / USDG 估值口径） |

**本任务涉及的模块**：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-3` | 方向层契约（`manager_contracts`） | `方` |
| `M-4` | 闭环层（`closed_loop_manager`） | `闭` |
| `M-6` | 复核发布层（`review_manager`） | `复` |
| `M-10` | 存储层（`manager_db` ＋ 迁移） | `储` |
| `M-2` | **扩范围**：内核对象层（`flashloan/rs-src`） | `内` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1 / §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-闭.03`** |
| **验收编号（纯 ID）** | **`AC-闭.03`** |
| **标题** | 基础池内化：方向池是唯一的池对象 |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-3`（方向层已上线） |
| **需授权** | 是（删闭环表的基础池列 ＋ 改内核目录读 ＋ 实盘迁移 018） |
| **一句话目标** | 把基础池从**公开字段**变成方向池的**私有内部对象**，并用「类型层 ＋ 结构层 ＋ 审计层」三层防线，让「闭环只引用方向池」成为**写不出来**的状态，而不是靠人记得 |

### 1.1 用户口径与裁定

用户口径（本轮）：

> 「为了强制处理方向池子数据，请把基础池子数据直接做成方向池子的内部对象，
> 这样外部无法访问，自然不会造成遗漏问题，请分析是否可行?」

**裁定：可行，但成立的是「封住裸基础池引用」，不是「封住基础池」。**

完整论证见 [`docs/D-160-基础池内化可行性裁定与实施_20261125.md`](../../docs/D-160-基础池内化可行性裁定与实施_20261125.md)。
一句话版本：

| 层 | 封住了什么 | 机制 |
|---|---|---|
| **类型层** | 外部**拿不到**方向池里的基础池对象 | `DirectionalPool.base` 私有；只给**标量**访问器（不给 `&V4Pool`） |
| **结构层** | 数据库里**写不出**裸基础池闭环 | 迁移 018 删闭环/跳表的基础池列，方向列 `NOT NULL` |
| **审计层** | SQL 绕过去时**判红** | 跳引用按「方向池可解析性」检查 ＋ 架构守卫（编译期 + 文本期） |

### 1.2 封不住的三件事（**诚实边界**，不可消除）

| # | 事实 | 为什么不能封 | 处理 |
|---|---|---|---|
| 1 | **基础池 id 必须可见** | 它是方向池身份的组成部分（`dpool:v1:{net}:{chain}:{scope}:{base}:{dir}`），也是幂等键与审计锚 | 保留可见，但**只在方向层内解析**；内核的解析集中在 `base_pool_of()` 一处 |
| 2 | **价格 / tick / 流动性天生是基础池级的事实** | 一个池只有一个价格，与方向无关 | 方向行里是**投影副本**；内核与复核层的定价事实改读方向行是**下一轮**（`D-160` §5 ③） |
| 3 | **池管理器必须保留基础池级入口** | 它按基础池寻址链上数据（`pool_key` → 链上 `PoolManager`） | 基础池**表不能删**，只能对外封闭；`V4Pool` 类型仍公开 |

> **推论**：类型层封住的是「**从方向池里把基础池整个拿走**」这件事；
> 基础池**类型**仍在契约里公开（池管理器要用），
> 但**方向池 → 基础池**这条通道被封了。

### 1.3 不做什么（**范围外**）

* **不**改方向层的投影规则、准入判定、容量口径、序位、幂等键 —— 一字不改。
* **不**改闭环层的选池逻辑（`M-4`）—— 闭环层本来就只引用有向池。
* **不**改复核层的定价事实来源（基础池 id 读快照表）—— 归**下一轮**（`D-160` §5 ③）。
* **不**做跨月统计、落盘失败降级之类的低概率路径（用户口径：先留占位）。
* **不**动 `chain.json` / `.env*` / `flashloan/src/core/db/builders.py`（并行施工保护）。
* **不**改 `flashloan/tools/{broadcast_manager,composite_manager,manager_api}` 的**生产**契约 ——
  只在**编译/夹具**必须时最小改动，并按 `D-162` §4 登记。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-3` | 方向层契约 | `flashloan/tools/manager_contracts/src/directional_pool.rs` | `base` 改私有 ＋ 14 个标量访问器 |
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/src/lib.rs` | 8 处 `.base` → 访问器；容量绑定改**方向池 id**；删除死字段 `HopCandidate.base_pool_id` |
| `M-6` | 复核发布层 | `flashloan/tools/review_manager/src/lib.rs` | `pool_ids` ← 闭环的方向池 id；容量绑定池 id ← 方向池 id；夹具补方向层行 |
| `M-10` | 存储层 | `flashloan/tools/manager_db/src/migrate.rs`、`migrations/20261125_018_closed_loop_directional_only.sql` | 018 迁移；台账 pending 集合修正；新增列登记 |
| `M-10` | 存储层（审计） | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 跳引用检查改按**方向池可解析性** |
| `M-2` | **扩范围** 内核对象层 | `flashloan/rs-src/src/{sqlite_read,closed_loop_identity,worker_pool,review_publish}.rs` | 读方向列；`base_pool_of()` 解析；**读失败不再降级成空目录**；`run_native_review` 的目录/池数组**改为非可选**（编译期强制） |
| `M-2` | **扩范围** 组合层 | `flashloan/tools/composite_manager/src/overview.rs` | 概览改读方向列（**编译必须**，见 `D-162` §1.2） |
| `M-2` | **扩范围** API 层 | `flashloan/tools/manager_api/src/lib.rs` | **仅测试夹具**：闭环表改方向列 ＋ 补方向层行（见 `D-162` §1.2） |
| `M-6` | 复核层示例 | `flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs` | 探测工具改读方向列 ＋ 边界处解析基础池 |

### 2.2 执行步骤

| 步 | 动作 | 完成判据 |
|---:|---|---|
| **1** | 读 `D-163`，确认每条验收项的当前状态 | 全部条目已知 |
| **2** | **类型层**：`DirectionalPool.base` 改私有，补 14 个标量访问器 | 编译通过；**不出现**任何 `&V4Pool` 出口 |
| **3** | `closed_loop_manager` 的 `.base` 全量改访问器 | `rg '\.base\b'` 在 `flashloan/tools/**` 只命中契约文件 |
| **4** | **语义修正**：容量绑定、路径最小容量、过期判定改用**方向池 id** | 断言从 `base_pool_id` 改为 `directional_pool_id` |
| **5** | **结构层**：写迁移 018（跳表重建 ＋ 方向列 NOT NULL ＋ 删基础池列 ＋ 闭环体改方向列） | 迁移台账登记；`MIGRATION_ADDED_COLUMNS` 同步 |
| **6** | **迁移台账修正**：`ensure_migration_tables_compatible` 的 pending 集合改为「**全部**未执行迁移」 | 003 不再被 016 新增列误判（原来 30 failed） |
| **7** | **审计层**：`baseline_audit` 的跳引用检查改查方向池表 | 018 之后不再报 `no such column: h.pool_id` |
| **8** | **复核层**：`pool_ids` 与容量绑定改方向池 id；夹具补方向层行 | 复核层**不再**按基础池 id 关联闭环 |
| **9** | **内核层**：`sqlite_read` 读方向列；`closed_loop_identity` 边界解析基础池 | 闭环目录的池集来自**方向池 id** |
| **10** | **内核层（关键）**：读失败**不再** `unwrap_or_default()`；`run_native_review` 的目录/池数组改为**非可选** | 「库读不出来」⇒ **worker 报错**，而不是「系统看起来没机会」 |
| **11** | **守卫**：Python 架构守卫（类型层 ＋ 闭环表 SQL 层）＋ Rust 迁移后表结构断言 | 守卫会**真的**判红（配「非空断言」） |
| **12** | 逐模块回归（见 `D-162` §2） | 全绿 |
| **13** | 实盘：018 落地前在**只读副本**跑 `preflight_read_only`；落地与新二进制**同批** | 见 §2.5 部署闸门 |
| **14** | 逐条勾选 `D-163` | 每条都有实测读数 |

### 2.3 技术文案（`docs/`）

* `D-160-基础池内化可行性裁定与实施_20261125.md` —— 可行性裁定、三层防线、
  诚实边界、风险登记与回归读数。

### 2.4 三条执行纪律

1. **编译 ≠ 测试**：改了 `manager_contracts` 的可见性 ⇒ 先 `cargo check`，再 `cargo test`。
2. **不静默降级**：库读不出来 ⇒ **worker 失败**；不返回空目录、不返回空池数组。
3. **实盘一律只读**：`mode=ro` ＋ `PRAGMA query_only = ON`；
   写生产库只在**部署窗口**内按 §2.5 执行。

### 2.5 部署闸门（**硬约束，不可颠倒**）

迁移 018 与新二进制**必须同批落地**。两个方向都会立刻失败，没有中间态：

| 组合 | 结果 |
|---|---|
| 旧二进制 ＋ 旧库 | ✅ 现状（实盘当前） |
| 旧二进制 ＋ 新库 | ❌ `no such column: pool_ids_json`（旧内核读已删的闭环体列） |
| 新二进制 ＋ 旧库 | ❌ `no such column: single_tick_capacity_binding_directional_pool_id`（本轮实测，见 `D-160` §7） |
| 新二进制 ＋ 新库 | ✅ 目标态 |

⇒ **落地顺序**：① 停旧运行时 → ② 只读副本 `preflight_read_only` →
③ 备份 018 之前的库 → ④ 应用 018 → ⑤ 换新二进制 → ⑥ 起运行时 → ⑦ 复核。

> **本轮只做代码与只读核对**；实盘落地是**独立的部署动作**，
> 需要单独的授权与窗口（见 `D-163` §5）。

---

## 3. 完成定义

* [ ] `D-163` 中**本轮范围内**的 `- [ ]` 全部变为 `- [x]`
* [ ] `D-162` 的**全部**回归命令**全绿**
* [ ] 每条已勾选项都有**可复算的实测读数**
* [ ] `flashloan/tools/**` 中除契约文件外**零** `.base` 访问
* [ ] 契约里**没有**任何返回 `&V4Pool` / `V4Pool` 的出口
* [ ] 闭环表 / 跳表的**基础池列已从存储层消失**（迁移后表结构断言）
* [ ] 内核**读失败不再降级**为空目录（worker 失败而非静默空转）
* [ ] **未**触碰**不属于** §2.1 的模块

---

## 4. 归档

```powershell
python plan/runtime_evidence/tools/archive_task_20261124.py T-闭.03 --dry-run
python plan/runtime_evidence/tools/archive_task_20261124.py T-闭.03
```

---

## ③ 修改范围守护

<a id="d162"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.03/D-162-SCOPE_GUARD.md` -->

# D-162 `T-闭.03`｜基础池内化 · 修改范围守护（Scope Guard）

> **本文件是 `T-闭.03` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-3` | 方向层契约 | `flashloan/tools/manager_contracts/src/directional_pool.rs` | `base` 私有 ＋ 标量访问器 | **是** |
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/src/lib.rs` | `.base` → 访问器；容量绑定改方向池 id；删死字段 | **是** |
| `M-6` | 复核发布层 | `flashloan/tools/review_manager/src/lib.rs` | 复核与闭环的方向口径对齐；夹具补方向层行 | **是** |
| `M-6` | 复核层示例 | `flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs` | 探测工具改读方向列 ＋ 边界解析基础池 | **是** |
| `M-10` | 存储层 | `flashloan/tools/manager_db/src/{lib,migrate}.rs` | 018 迁移登记；台账 pending 集合修正 | **是** |
| `M-10` | 存储层（迁移） | `flashloan/tools/manager_db/migrations/20261125_018_closed_loop_directional_only.sql` | 新增迁移文件 | **是** |
| `M-10` | 存储层（审计） | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 跳引用检查改方向池 | **是** |
| `M-2` | **扩范围** 内核对象层 | `flashloan/rs-src/src/{sqlite_read,closed_loop_identity,worker_pool,review_publish}.rs` | 方向列读；边界解析；**读失败不降级** | **是** |
| `M-2` | **扩范围** 组合层 | `flashloan/tools/composite_manager/src/overview.rs` | 概览改方向列 | **是**（见 §1.2） |
| `M-2` | **扩范围** API 层 | `flashloan/tools/manager_api/src/lib.rs` | **仅测试夹具** | **是**（见 §1.2） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

### 1.1 为什么必须扩到 `M-2`（内核对象层）

本任务的口径是「**闭环引用的是有向池**」与「**要么成功，要么失败，没有降级处理**」。
这两条在内核里有一个**真实的、可复现的违反**：

```rust
// flashloan/rs-src/src/worker_pool.rs（改前）
let closed_loop_rows =
    sqlite_read::load_manager_closed_loop_rows(&connection, network, chain_id)
        .unwrap_or_default();          // ← 读失败 = 空闭环目录
let directional_pool_rows = sqlite_read::load_v4_directional_pool_registry_rows(...)
    .unwrap_or_default();             // ← 读失败 = 空池数组
```

空目录与空池数组**和「市场里没有机会」长得一模一样**。
叠加 018 迁移的破坏性（新二进制 ＋ 旧库 ⇒ `no such column`），
最坏结果不是「报错」，而是**系统看起来正常却永远不出单**。

⇒ 本任务把内核的这两处读取改为**全有或全无**：
读失败 ⇒ 快照构建失败 ⇒ worker **报错**；
并把 `run_native_review` 的「闭环目录」与「池数组」由 `Option` 改为**非可选**，
让「无目录复核」这种降级在**编译期**就不存在。

### 1.2 扩范围登记（**逐条说明，附原因**）

| # | 落点 | 性质 | 原因 |
|---|---|---|---|
| 1 | `flashloan/tools/composite_manager/src/overview.rs` | **生产代码** | 概览读 `manager_v4_closed_loops` 的 `pool_ids_json`；018 删了该列 ⇒ **不编译**。改动限于「读方向列 ＋ 基础池按方向层解析」。**本轮因编译必须破禁改**，已在此登记。 |
| 2 | `flashloan/tools/manager_api/src/lib.rs` | **仅测试夹具** | 夹具按 018 之前的列名插入闭环表 ⇒ 夹具本身报错（`no such column: pool_ids_json`），4 个测试连坐失败。生产读路径只读 `manager_v4_reviews` / `manager_v4_review_broadcast_requests`（**018 未触碰**），**未改生产契约**。夹具同时补了方向层行，因为复核层是通过方向层解析基础池的。 |
| 3 | `flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs` | **诊断示例** | 探测工具读闭环表的 `pool_id` / `pool_ids_json`；018 之后这两个列不存在 ⇒ 工具失效。改为读方向列 ＋ 在边界处解析基础池（池状态仍按基础池发布）。 |
| 4 | `flashloan/rs-src/src/worker_pool.rs` | **生产代码** | 见 §1.1。**「内核对象一次性存储」由另一线程负责**，本轮只改「读失败不降级」与「目录/池数组非可选」两处，**不碰**内核的池对象存储结构。 |

> **禁改名单本轮的实际破例**：仅 `composite_manager`（生产代码，编译必须）与
> `manager_api`（测试夹具）两处，均为**最小改动**且已逐条登记。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-3` `M-4` `M-10` | 契约 / 闭环 / 存储 | `cd flashloan/tools && cargo test --offline -p manager-contracts -p closed-loop-manager -p manager-db` | 全绿 |
| `M-6` | 复核发布层 | `cd flashloan/tools && cargo test --offline -p review-manager` | 全绿 |
| `M-10` | 存储层（审计） | `cd flashloan/tools && cargo test --offline -p pool-manager` | 全绿 |
| `M-2` | 内核对象层 | `cd flashloan/rs-src && cargo test --offline` | 全绿（基线 **220** passed） |
| `M-2` | 组合层 / API 层（扩范围） | `cd flashloan/tools && cargo test --offline -p composite-manager -p manager-api` | 全绿 |
| 全局 | 架构守卫 | `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q` | 全绿 |

```powershell
cd flashloan/tools
cargo test --offline -p manager-contracts -p closed-loop-manager -p manager-db -p review-manager -p pool-manager -p composite-manager -p manager-api

cd ..\rs-src
cargo test --offline

cd ..\..
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/broadcast_manager/**` | 属 `M-7`；它的 `pool_ids_json` 属于**复核/广播记录表**（`manager_v4_reviews` / `manager_cow_execution_attempts` / `manager_v4_review_broadcast_requests`），**018 未触碰** ⇒ 本任务不改 |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的投影规则、准入判定、容量口径一字不改 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| `flashloan/rs-src/src/evidence.rs` | 并行施工保护 |
| `flashloan/tools/manager_api/src/lib.rs` 的**生产**路径 | 只允许改**测试夹具**（§1.2 第 2 条） |
| 任何 `chain.json` / `.env` / `.env.example` | 本任务**不引入**任何运行期参数 |
| 实盘**写**操作（迁移 018） | 本轮**只做只读核对**；落地是独立部署动作（`D-161` §2.5） |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-161` §2.1；
2. **记录原因** —— 在 §1.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-3` / `M-4` / `M-6` / `M-10` 代码 | 否 | 纯代码 |
| 改 `M-2` 内核（读失败不降级） | 否 | 纯代码，不改存储结构 |
| 破禁改 `composite_manager` / `manager_api` | **已授权**（用户：默认授权通过） | 已在 §1.2 逐条登记 |
| 实盘**只读**复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 实盘应用迁移 018 ＋ 换二进制 | **需单独授权** | 破坏性且与新二进制强耦合（`D-161` §2.5） |

---

## 6. 范围守护自检

* [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
* [ ] §2 的**全部**命令**已执行**且**全绿**
* [ ] §3 的禁止触碰项**确未被触碰**
* [ ] `flashloan/tools/**` 中除 `manager_contracts/src/directional_pool.rs` 外**零** `.base` 访问
* [ ] `manager_contracts` 中**没有**返回 `&V4Pool` / `V4Pool` 的出口
* [ ] 内核的闭环目录与池数组**读失败即失败**（无 `unwrap_or_default` 降级）
* [ ] **未**因本任务跑过**全量**测试

---

## ④ 测试目标 check list

<a id="d163"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.03/D-163-checklist.md` -->

# D-163 `T-闭.03`｜基础池内化 · 验收清单

> **本文件是 `T-闭.03` 的全部验收框。** 编号 `AC-闭.03` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-3` / `M-4` / `M-6` / `M-10` 的现有单测全绿。

---

## 1. 可行性裁定

- [x] 用户口径**可行**，但成立的是「封住裸基础池引用」而不是「封住基础池」

      > **读数**：`docs/D-160-基础池内化可行性裁定与实施_20261125.md` §2 / §3：
      > 封住的是「**从方向池里把基础池整个拿走**」；
      > 基础池 **id**（身份的一部分）、**价格/流动性**（基础池级事实）、
      > **池管理器的基础池入口**（按基础池寻址链上数据）三件事**封不住**，已如实登记。

- [x] 落成**三层防线**，而不是靠人记得

      > **读数**：类型层（`base` 私有 ＋ 标量访问器）／
      > 结构层（迁移 018 删基础池列 ＋ 方向列 `NOT NULL`）／
      > 审计层（跳引用按方向池可解析性检查 ＋ 架构守卫）。

- [x] 与用户此前口径**一致**（「要么成功，要么失败，没有降级处理」）

      > **读数**：内核的两处 `unwrap_or_default()` 已改为**全有或全无**；
      > `run_native_review` 的「闭环目录」与「池数组」由 `Option` 改为**非可选**；
      > 见 §2.4。

---

## 2. 实施

### 2.1 类型层（`M-3`）

- [x] `DirectionalPool.base` **私有**

      > **读数**：`manager_contracts/src/directional_pool.rs:196` 为 `base: V4Pool`（无 `pub`）；
      > 契约文件中**不存在** `pub base`；
      > 守卫 `test_the_base_pool_is_an_internal_object_of_the_directional_pool` 通过。

- [x] 只给**标量**访问器，**不给** `&V4Pool`

      > **读数**：14 个标量访问器（`base_pool_id / pool_manager / pool_key_json / token0 / token1 /
      > token0_key_id / token1_key_id / fee / tick_spacing / hooks / sqrt_price_x96 / tick /
      > liquidity / depth_capacity_raw`）；
      > 契约里**没有**任何 `-> &V4Pool` / `-> V4Pool` 的出口（守卫断言 `escape_hatches == []`）。

- [x] 序列化后的方向行**不含**嵌套的裸基础池对象

      > **读数**：`base` 标 `#[serde(flatten)]` ⇒ 序列化的方向行**就是一行方向行**
      > （基础事实以投影副本平铺，与数据库方向行同形），**没有** `"base"` 子对象可拿；
      > 契约单测 `the_serialized_row_is_a_direction_row_and_carries_no_base_object`
      > 断言序列化结果**无** `"base"` 键、基础事实平铺在行内、且**往返可重建**；
      > 守卫**同时**断言 `base` **私有** **且** **被 flatten**。

- [x] `flashloan/tools/**` 中除契约文件外**零** `.base` 访问

      > **读数**：`rg '\.base\b' flashloan/tools --glob '*.rs'` 只命中
      > `manager_contracts/src/directional_pool.rs`（14 处，全是契约内部的访问器实现）；
      > 守卫 `test_no_closed_loop_statement_reaches_for_a_base_pool` 通过。

### 2.2 结构层（`M-10`）

- [x] 迁移 018 落地为**迁移文件 ＋ 台账登记**

      > **读数**：`migrations/20261125_018_closed_loop_directional_only.sql`；
      > `MIGRATION_IDS` 含 `20261125_018_closed_loop_directional_only`；
      > `LATEST_MIGRATION_ID` 指向它；`MIGRATION_ADDED_COLUMNS` 补齐 018 的 4 个改名列。

- [x] 跳表**重建**为方向列 `NOT NULL`，基础池列**消失**

      > **读数**：迁移后 `PRAGMA table_info(manager_v4_closed_loop_hops)` ——
      > **无** `pool_id`、**无** `base_pool_id`；
      > **有** `directional_pool_id` / `hop_direction` / `directional_scope_key` 且 **NOT NULL**。
      > 断言见 `manager_db/tests/migration_smoke.rs::the_closed_loop_tables_carry_no_base_pool_column`。

- [x] 闭环体**删**基础池列，改方向列

      > **读数**：`manager_v4_closed_loops` **无** `pool_ids_json`；
      > **有** `directional_pool_ids_json` 与 `single_tick_capacity_binding_directional_pool_id`；
      > `backup_pool_ids_json` → `backup_directional_pool_ids_json`。

- [x] 迁移台账的 pending 集合**修正**

      > **读数**：`ensure_migration_tables_compatible` 的 pending 由「仅当前迁移」改为
      > 「**全部**未执行迁移」⇒ 003 不再被 016 新增的列误判为不兼容
      > （修正前 **30 failed**）。

### 2.3 语义修正（`M-4` / `M-6`）

- [x] 容量绑定 / 路径最小容量 / 过期判定改用**方向池 id**

      > **读数**：`folded_path_capacity` 的 `CapacityHop.pool_id` 传**方向池 id**；
      > `path_min_capacity` / `path_min_single_tick_bound` 的 blocking 池名改方向池 id；
      > 门禁过期判定（`:3159`）比对 `hop_pool.directional_pool_id`。
      > **修正前**这里写的是基础池 id ⇒ 写进 directional 列 = 错。

- [x] 审计器的跳引用检查改按**方向池可解析性**

      > **读数**：`baseline_audit.rs` 的 `invalid_hop_directional_reference_count` 查
      > `manager_v4_directional_exchange_pools`（按 `directional_scope_key` ＋ `directional_pool_id`
      > ＋ `direction` ＋ `pool_status='active'`）
      > ⇒ 修掉 018 之后真实的 `no such column: h.pool_id`。

- [x] 复核层的池集与容量绑定改**方向池 id**

      > **读数**：`review_manager` 的 `pool_ids: closed_loop.directional_pool_ids`；
      > `single_tick_capacity_binding_pool_id` ← 闭环的 `single_tick_capacity_binding_directional_pool_id`；
      > 夹具写 `manager_v4_directional_exchange_pools` 的方向层行。

- [x] 遗留探测工具（`t401_profit_drop_probe`）同步改口径

      > **读数**：读 `directional_pool_id` / `directional_pool_ids_json`；
      > 池状态仍按**基础池**发布 ⇒ 在**边界**用 `base_pool_of()` 解析一次
      > （`DirectionalPoolId::from_str` 解析，非方向 id 原样返回）。

### 2.4 内核层（`M-2`，扩范围）

- [x] 内核读的是**方向列**，闭环目录的池集来自方向池 id

      > **读数**：`sqlite_read.rs` 读
      > `directional_pool_ids_json AS pool_ids_json`、
      > `single_tick_capacity_binding_directional_pool_id AS single_tick_capacity_binding_pool_id`
      > （**不改 JSON key** ⇒ 下游零改动）；
      > `closed_loop_identity.rs` 在**边界**把方向 id 解析成基础池 id（`base_pool_of()`），
      > 用于「Manager 定价的池」比对。

- [x] 读失败**不再**降级成空目录 / 空池数组

      > **读数（改前）**：`load_manager_closed_loop_rows(...).unwrap_or_default()` 与
      > `load_v4_directional_pool_registry_rows(...).unwrap_or_default()`
      > ⇒ 读失败与「市场里没有机会」**无法区分**。
      > **改后**：两处均改为 `match` ＋ 失败即 `return None`（快照构建失败）；
      > worker 侧 `None` 分支返回 **Err**（不再是「无目录复核」）；
      > 刷新路径保留上一份快照但**打印告警**。

- [x] 「无闭环目录复核」在**编译期**被禁止

      > **读数**：`run_native_review` 的 `pool_registry: Option<&V4PoolRegistry>` →
      > `&V4PoolRegistry`，`closed_loops: Option<&ClosedLoopCatalog>` → `&ClosedLoopCatalog`；
      > ⇒ 「没有目录也复核」这种降级**写不出来**。

- [x] 配置错误**优先**于存储错误报出

      > **读数**：新增 `missing_command_config()`；worker 在**装载内存对象之前**先查
      > `rpcUrl` / `arbAddress` ⇒ 用例 `missing_rpc_config_records_native_worker_error` 仍报原错误。

### 2.5 守卫

- [x] 架构守卫：类型层 ＋ 闭环表 SQL 层

      > **读数**：`flashloan/app_runtime/tests/unit/test_architecture_guards.py` 新增 3 条 ——
      > ① 基础池是方向池的内部对象（私有 ＋ 无 `&V4Pool` 出口）；
      > ② 没有任何语句向闭环表要基础池列；
      > ③ 守卫**非空断言**（会真的判红 ＋ 方向列不误报）。

- [x] 迁移后**真实表结构**断言（不是文本匹配）

      > **读数**：`manager_db/tests/migration_smoke.rs` 新增
      > `the_closed_loop_tables_carry_no_base_pool_column` —— 在**迁移后的库**上读
      > `PRAGMA table_info` 断言 ⇒ 注释里的列名**满足不了**这条断言。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p closed-loop-manager` 全绿

      > **读数**：**48 passed / 0 failed**（`_cargo_cl17_test.out.log`）。

- [x] `cargo test --offline -p manager-db` 全绿

      > **读数**：**7 passed / 0 failed**（`_cargo_cl20_test.out.log`，基线 6 ＋ 本任务新增 1）。

- [x] `cargo test --offline -p review-manager` 全绿

      > **读数**：**161 passed / 0 failed**（`_cargo_cl20_test.out.log`）。

- [x] `cargo test --offline -p manager-api` 全绿（扩范围）

      > **读数**：**33 passed / 0 failed**（`_cargo_cl20_test.out.log`）；
      > 修正前 **29 passed / 4 failed**（夹具按旧列名插入闭环表 ⇒ `no such column`）。

- [x] `cargo test --offline -p pool-manager` 全绿

      > **读数**：**167 passed / 0 failed**（`_cargo_cl17_test.out.log`）。

- [x] `cargo test --offline -p directional-pool-manager` 全绿

      > **读数**：**26 passed / 0 failed**（`_cargo_cl17_test.out.log`）。

- [x] `cargo test --offline -p composite-manager` 全绿（扩范围）

      > **读数**：**20 passed / 0 failed**（`_cargo_cl17_test.out.log`）。

- [x] `cd flashloan/rs-src && cargo test --offline` 全绿

      > **读数**：**221 passed / 0 failed**（`_cargo_rs05_test.out.log`；
      > 基线 **220** ＋ 本任务新增 **1** 条「实盘库未落 018 时跳过并点名迁移」的用例）。

- [x] 架构守卫全绿

      > **读数**：`python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q`
      > ⇒ 3 条新守卫通过（`_pytest_arch_guard_20261126.out.log`）。

---

## 4. 实盘（**只读**）

- [x] 实盘库当前**停在迁移 017**，018 **未落地**

      > **读数**（`mode=ro` ＋ `PRAGMA query_only = ON`）：
      > `schema_migrations` 共 **18** 行，最后一行是
      > `20261125_017_observation_optimal_pool_view`；
      > `manager_v4_closed_loops` **仍带** `pool_ids_json` / `backup_pool_ids_json` /
      > `single_tick_capacity_binding_pool_id`；
      > `manager_v4_closed_loop_hops` **仍带** `pool_id` / `base_pool_id`。

- [x] 实盘全程**只读**，零写入

      > **读数**：本轮对 `flashloan/db/local_store_4663.sqlite3`（**15.32 GB**）
      > 只做 `SELECT` / `PRAGMA table_info`；**未**复制整库、**未**应用迁移。

- [x] 「新二进制 ＋ 旧库」会立刻失败，已实测

      > **读数**：`the_live_closed_loop_pool_sets_are_reproducible_from_the_pool_keys`
      > 在实盘库上失败：
      > `no such column: single_tick_capacity_binding_directional_pool_id`。
      > ⇒ 部署必须「018 ＋ 新二进制」同批（`D-161` §2.5）。

---

## 5. 部署闸门（**本轮未做，需单独授权**）

- [x] 实盘应用迁移 **018**

      > **读数（2026-11-26 14:3x · 只读）**：`schema_migrations` ⇒
      > `20261125_018_closed_loop_directional_only` **已应用**
      > （`applied_at = 2026-11-25T23:43:20.599Z`）⇒ **实盘库已过 018**
      > （此前登记的「库停在 017」**已过时**）。
      > **schema 侧佐证**：`manager_v4_closed_loop_hops` 的列**只有** `directional_pool_id` ／
      > `hop_direction` ／ `directional_scope_key` ／ `directional_pool_rank`，**没有任何基础池列**；
      > `manager_v4_closed_loops` 的绑定列是 `single_tick_capacity_binding_directional_pool_id`。

- [x] 新二进制与 018 **同批**上线

      > **读数**：**新二进制从未对着 017 跑过** ⇒ **无中间态**。
      > 迁移 **018** 应用于 **2026-11-25T23:43:20.599Z**；此后上线的扩展构建时刻为
      > **08:09:06**（`T-核.04`）、**12:11:21**（`T-核.05` 前）、**13:59:08**（`D-196`）
      > ⇒ **每一次都在 018 之后**。
      > 当前 runtime **26328**（看护 **26980**）自 **13:59:28** 起跑新二进制 ⇒ **新旧二进制无中间态**。
      > **★ 口径订正（文档缺口，如实登记）**：`D-161` §2.5 所说的「四种组合表」
      > **在仓里没有这份文件**（`plan/tasks/P-01-池子重构/docs/` 下只有 `D-160`）
      > ⇒ 本框按**实测的时间线**判定，**不代 `D-161` 关闭**。

- [x] 上线后复核闭环 **522** 跳按方向解析

      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_closed_loop_hops` **522** 行；
      > 按 `(directional_scope_key, directional_pool_id)` 关联 `manager_v4_directional_exchange_pools`
      > ⇒ **522 / 522** 命中、**缺 0**；**再加上 `hop_direction = direction`** 仍是 **522 / 522**。
      > 探针：`plan/runtime_evidence/tools/verify_restart_gates_20261126.py`。

---

## 6. 诚实边界与未闭合项

- [x] 基础池 **id** 仍然可见（是身份的一部分）

      > **读数**：方向池 id = `dpool:v1:{net}:{chain}:{scope}:{base}:{dir}`；
      > 内核的解析集中在 `closed_loop_identity::base_pool_of()` **一处**。

- [x] 基础池 **id 离开方向层**这件事**尚未彻底收口**

      > **读数**：内核在**读边界**把方向 id 解析成基础池 id，用于
      > 「Manager 定价的池」比对（`manager_pools_are_priced_by_route`）；
      > 复核层同样经方向层解析基础池（`read_base_pools_of_directional_pools`）。
      > ⇒ 「基础池 id 不再离开方向层」是**下一轮**（`D-160` §5 ③），**未**在本轮闭合。

- [x] 复核层的**定价事实**仍按基础池 id 读快照表

      > **读数**：`D-160` §5 ③ 第 1 条；改读方向行需要 **A/B 读数**，本轮**未**做。

- [x] 遗留表（`v4_closed_loops` / `v4_closed_loop_hops`）仍是基础池形状

      > **读数**：写路径已退出维护流水线；下线归 `T-方储.01` 阶段 2（本轮**未**动）。

- [x] 内核的**池对象一次性存储**由**另一线程**负责，本轮不抢

      > **读数**：本轮对 `rs-src` 的改动只有「读失败不降级」与「目录/池数组非可选」两处，
      > **未**触碰内核的池对象存储结构（`D-162` §1.2 第 4 条）。

- [x] 「运行期生效」**如实登记**（实盘未重启 ⇒ 新代码未生效）

      > **读数**：实盘 runtime 进程未重启；本轮**未**重启实盘；
      > 且 §5 的部署闸门**未**执行 ⇒ **新口径在实盘未生效**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-闭.03/README.md` -->

# `T-闭.03`｜基础池内化：方向池是唯一的池对象

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-闭.03`（纯 ID）　**验收**：`AC-闭.03`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-闭.03` / `AC-闭.03` —— **模块内编号**，编号索引 **`闭`**
（改动模块：`M-3`（方向层契约）/ `M-4`（闭环层）/ `M-6`（复核发布层）/ `M-10`（存储层）
＋ **扩范围** `M-2`（内核对象层，见 `D-162` §1.2）），该索引号段内第 `03` 个。

**改动模块（= 回归范围）**：`M-3` / `M-4` / `M-6` / `M-10` ＋ 扩范围 `M-2`

**前置模块**：`M-3`（方向层已上线）　　**需授权**：是（删闭环表的基础池列 ＋ 改内核目录读）

## 文件

| 文件 | 内容 |
|---|---|
| `D-161` | 本任务的开发计划与回归范围（**自包含**） |
| `D-162` | 本任务的修改范围守护与逐模块回归命令 |
| `D-163` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（可行性裁定与诚实边界） |

## 一句话

> 用户的口径是：**把基础池子数据直接做成方向池子的内部对象，外部拿不到，
> 自然不会造成遗漏**。
>
> **裁定：可行**（`docs/D-160-基础池内化可行性裁定与实施_20261125.md`），
> 但成立的是「**封住裸基础池引用**」，不是「封住基础池」：
> 基础池 id 是方向池身份的一部分（要留），价格与流动性天生是基础池级的事实
> （方向行里是投影副本），池管理器仍要保留基础池级入口（它按基础池寻址链上数据）。
>
> 本任务把这套口径落成**三层防线**：
> **类型层**（`base` 私有 ＋ 只给标量访问器）→
> **结构层**（018 迁移删基础池列、方向列 NOT NULL）→
> **审计层**（跳引用按方向池可解析性检查 ＋ 架构守卫）。

## 独立回归

```powershell
cd flashloan/tools
cargo test --offline -p closed-loop-manager -p manager-db -p review-manager -p pool-manager -p directional-pool-manager

cd ..\rs-src
cargo test --offline
```

**读数（2026-11-25）**：见 `D-163` §4。

---
