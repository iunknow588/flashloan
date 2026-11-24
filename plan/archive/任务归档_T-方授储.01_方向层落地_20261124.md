# `T-方授储.01`｜方向层落地 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方授储.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 方向层落地 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261124 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方授储.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d077"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/D-077-PLAN.md` -->

# D-077 `T-方授储.01`｜方向层落地 · 开发计划（**独立 · 自包含**）

> **本目录是 T-方授储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-方授储.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-方授储.01` / `AC-方授储.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方授储.01`** |
| **本任务验收编号** | **`AC-方授储.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`方授储`**（`M-3`（方向层（方向池物化 / 评分 / 准入））、`M-9`（授权与回滚（授权维度 / 守卫 / 回滚脚本））、`M-10`（存储层（schema / 迁移 / 记录剥离））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 3 个模块**（`M-3`、`M-9`、`M-10`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`方授储`**。

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
| `M-9` | 授权与回滚（授权维度 / 守卫 / 回滚脚本） | `授` |
| `M-10` | 存储层（schema / 迁移 / 记录剥离） | `储` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方授储.01`** |
| **验收编号（纯 ID）** | **`AC-方授储.01`** |
| **标题** | 方向层落地 |
| **优先级** | P0 |
| **波次（模块级）** | W-0 |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（DDL） |
| **未完成验收项** | **0** |
| **已闭合验收项** | **32** |

### 1.1 改动模块（**这一列就是 T-方授储.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/` |
| `M-9` | 授权与回滚 | `flashloan/tools/manager_contracts/src/authority.rs` |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` |
| `M-9` | 授权与回滚 | `cargo test --offline -p manager-contracts`<br>`pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |

```powershell
# T-方授储.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-contracts
pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py
cargo test --offline -p manager-db
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

### 2.2 本轮实施记录（2026-11-24 · 收口轮）

> 本任务是**跨 3 个模块的组合任务**，代码主体在既往轮次已落地（约 85%），
> 但 checklist 长期**全未勾选** ⇒ 本轮的实质工作是**「把已实现的部分证出来」**，
> 而不是新写功能。**没有一项验收靠推断勾选。**

| 步 | 动作 | 结果 |
|---:|---|---|
| **1** | **补 4 条 Rust 测试**（`flashloan/tools/directional_pool_manager/tests/directional_pools.rs`） | `directional-pool-manager` **14 → 18 passed**；新增 `the_rank_follows_the_candidate_edge_recommendation_order`（T-D-3）、`the_capacity_is_recomputed_and_not_read_from_the_stored_column`（T-D-14）、`the_in_memory_rows_and_the_table_rows_are_the_same_set`（T-D-12 / I-10）、`only_an_admissible_base_pool_may_carry_a_rank`（T-D-13） |
| **2** | **补 3 条 Python 架构守卫**（`flashloan/app_runtime/tests/unit/test_architecture_guards.py`） | **71 → 74 passed**；新增 `test_python_never_binds_to_the_directional_pool_layer_layout`（T-I-3，禁**列名**且按**词边界**匹配，故不误伤 `select_directional_pool_ids`）、`test_the_directional_admission_convention_is_enforced_in_rust_only`（DPO-9 / DPO-10）、`test_the_directional_pool_columns_are_registered_in_the_migration_ledger`（T-I-4） |
| **3** | **迁移实盘生产库**（`flashloan/db/local_store_4663.sqlite3`，8.2 GB） | preflight `quickCheck=ok` / `readyForMigration=true`；**只**应用 `20261123_015_directional_pool_schema`（`appliedNow=true`），其余 15 条校验和**逐一未变**；新增 2 表，`schema_migrations` 15→16；**全表行数指纹对比：其余表零变化** |
| **4** | **实盘物化方向池**（经 `manager_api`，`DIRECTIONAL_POOL_MANAGER_AUTHORITY=manager`） | `basePoolCount=494` → `directionalPoolCount=352`（176 池 × 2 方向），`direction0/1Count=176/176`，`excludedPoolCount=318`，`eligiblePairCount=112`，`measuredCapacityCount=216`，`measuredOutputCount=212`；校验和 `sha256:9b483796…d627234`；比对报告 `poolsWithoutTwoRows=0` / `directionMismatches=0` / `projectionMismatches=0` / `examinedRows=352` / `readyForCutover=true`；幂等重放 `idempotentReplay=true` |
| **5** | **负向验证 + 文档订正** | 未设 env 时 `POST /v1/directional-pools/rebuild` → **HTTP 403**、`error.code=TOKEN_AUTHORITY_WRITE_DENIED`、`details.authorityMode="legacy"`（**实现返回的错误码不是 `authority_denied`**，文档按实现订正）；文档侧清理 **30 处**「已闭合·已归档」洗稿残留 → 恢复真实任务号；修正过期基线（`62 passed` → `74 passed`）；任务计数订正为 **218+16+4+59 = 297** |

**本轮回归范围读数**（`M-3` + `M-9` + `M-10`，**按改动模块集合跑，未跑全量**）：

```text
cargo test --offline -p directional-pool-manager   → 18 passed / 0 failed
cargo test --offline -p manager-contracts          → 17 passed / 0 failed
cargo test --offline -p manager-db                 →  5 passed / 0 failed
pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py → 74 passed
cargo build --offline --release -p manager-db -p manager-api       → exit 0
```

**实盘排除拆解**（318 个被排除的基础池，**不是**阈值拍脑袋）：

| 排除原因 | 数量 |
|---|---:|
| 带 hook（`pool_status = blocked`） | 318 |
| 动态费率 | 316 |
| 非规范 token 序 | 0 |
| `active` 但费率超窗口（> 30000） | 0 |
| `invalid`（费率超窗口，`poolStatusReason = fee_not_fixed_candidate_range`） | 84 |

**带序位的 173 行，`pool_status` 全部为 `active`**；费率窗口 / hook / 动态费率**违规数 ＝ 0**。

---

## 3.1 剩余项与移交（**诚实边界**）

| 项 | 归属 | 说明 |
|---|---|---|
| **闭环层尚未消费方向池** | **`T-方闭储.01`**（`M-4`） | `closed_loop_manager/src/lib.rs` 至今**没有**任何 `directional` 引用，闭环仍读 `manager_market_observation_intermediate_pools`。方向池表**已就绪但未接线** |
| 旧方向键与旧排序下线 | `T-方储.01`（`M-3` + `M-10`） | 本任务**未**删除任何旧函数，旧路径仍在生产链路上（**内核对象一次性存储由其它线程处理**，勿在本任务越界） |
| 备用池接线 | `T-方闭.01`（`M-4`） | 依赖 `M-3`，现已解除 |

> **本任务把方向层建好并落库，但闭环还没有改用它。**
> 在 `T-方闭储.01` 完成前，**主网闭环走的是旧路径**；
> 方向池表此时是「已发布、未消费」状态（`consumersNeedRebuild=true` 正是这个意思）。

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
python plan/runtime_evidence/tools/archive_task_20261124.py T-方授储.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-方授储.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-01_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d078"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/D-078-SCOPE_GUARD.md` -->

# D-078 `T-方授储.01`｜方向层落地 · 修改范围守护（Scope Guard）

> **本文件是 `T-方授储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-3` | 方向层 | ``flashloan/tools/directional_pool_manager/`` | 是 |
| `M-9` | 授权与回滚 | ``flashloan/tools/manager_contracts/src/authority.rs`` | 是 |
| `M-10` | 存储层 | ``flashloan/src/core/db/storage_business_schema.py`` | 是 |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p directional-pool-manager` |
| — | — | `cargo test --offline -p manager-contracts` |
| — | — | `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` |
| — | — | `cargo test --offline -p manager-db` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-contracts
pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py
cargo test --offline -p manager-db
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

<a id="d079"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/D-079-checklist.md` -->

# D-079 `T-方授储.01`｜方向层落地 · 测试目标 check list

**验收前置**：✅ **已满足** —— 口径判定**已裁定** = **采用新口径为默认**；
方向层口径（**DPO-9** 准入约定 / **DPO-10** Rust 侧唯一实现）**已裁定** = 方向层全部在 Rust 侧，Python 只消费内存对象里的实际值。

> **本版的读数来源**（2026-11-24 实测，非推断）：
>
> | 项 | 读数 |
> |---|---|
> | 生产库 | `flashloan/db/local_store_4663.sqlite3`（robinhood / 4663） |
> | 迁移 | `20261123_015_directional_pool_schema`，`appliedNow=true`，**只新增这一条**（其余 15 条校验和未变） |
> | 物化 | `basePoolCount=494` → `directionalPoolCount=352`（176 基础池 × 2 方向），`excludedPoolCount=318` |
> | 校验和 | `sha256:9b483796a70e5cb4ca9b70961bf2d0b3ea3f3542a1fd6af0ddb7187b1d627234` |
> | 比对报告 | `poolsWithoutTwoRows=0` / `directionMismatches=0` / `projectionMismatches=0` / `readyForCutover=true` |
> | 回归 | `directional-pool-manager` **18 passed**、`manager-contracts` **17 passed**、`manager-db` **5 passed**、架构守卫 **74 passed** |

---

## 0. 口径订正记录（**T-D-13 的措辞**）

> **订正**：T-D-13 原文写作「方向行**只**指向 `pool_status = 'active'`… 的基础池」。
> 实测发现该措辞与实现的**投影**语义不符，实盘 352 行里有 **168 行**来自
> `pool_status = 'invalid'` 的基础池（84 池 × 2 方向）；
> `blocked` 的池则因带 hook **根本不投影**。
>
> **实现是刻意如此**：非 active 的池**照样发布**（让覆盖缺口可见），
> 但**不带序位**；序位是闭环层**唯一**的选择依据，所以「没有序位」
> 就是「不可选取」。实盘读数：**带序位的 173 行 `pool_status` 全部为
> `active`**，费率窗口 / hook / 动态费率**违规数 ＝ 0**。
>
> ⇒ **验收判据按「可选取（带序位）的行」表述**，并由
> `only_an_admissible_base_pool_may_carry_a_rank` 一条测试钉住。
> **不是**放宽门禁：不可选取的事实没有变。

---

## 1. 结构验收

- [x] migration `20261123_015_directional_pool_schema.sql` 存在，含**主表 + 库状态表 + 三个索引 + 一个 UNIQUE 索引**
      —— `idx_v4_directional_pools_pair` / `_direction` / `_base` + `ux_v4_directional_pools_id`

- [x] `manager_db/src/migrate.rs` 五处登记齐全：`MIGRATION_IDS` / `LATEST_MIGRATION_ID` /
      `TARGET_TABLES` / `required_columns` / 迁移→表组映射
      —— 行 27 / 44+46 / 149-150 / 210-212+773-795 / 654

- [x] `LATEST_MIGRATION_ID` 指向新迁移，且新迁移 id **恰为** `20261123_015`（**未**跳过 `20261121_014`）
      —— `LATEST_MIGRATION_ID = DIRECTIONAL_POOL_MIGRATION_ID`，`MIGRATION_IDS` 末位为 `…_015`

- [x] `manager_contracts/src/directional_pool.rs` 存在，导出 `PoolDirection`（0 / 1，含
      `zero_for_one()` 与 `of_pool()` **纯函数**）、`V4Pool`、`DirectionalPool`（`base` + `direction` + 方向量）

- [x] `manager_contracts/src/ids.rs` 新增 `DirectionalPoolId`，并从 `manager_contracts/src/lib.rs` 导出
      —— `the_directional_pool_id_carries_the_direction`

- [x] `flashloan/tools/directional_pool_manager` crate 存在，提供 `materialize_directional_pools` /
      `materialize_directional_pools_write` / `read_directional_pool_status` / `read_directional_pool_snapshot`

- [x] 幂等键 + 请求摘要 + dry-run / apply 语义**与 `candidate_exchange_pool_manager` 保持一致**
      —— `a_dry_run_writes_nothing` / `the_idempotency_key_replays_the_first_result` /
      `a_reused_idempotency_key_with_a_different_body_is_refused`

- [x] `composite_manager/src/lib.rs` 依赖图新增方向层节点，**阶段内不开启自动调度**
      —— `ManagerStep::DirectionalPool` + 边 `"directional_pool"`

- [x] `manager_api` 提供 `GET /v1/directional-pools` / `GET /v1/directional-pools/compare` /
      `POST /v1/directional-pools/rebuild`，权限动作 `pools.directional.read` / `pools.directional.rebuild`
      —— 三条路由已注册，实盘全部 **HTTP 200**

- [x] **新增第 8 个授权维度** `DIRECTIONAL_POOL_MANAGER_AUTHORITY` + `directional_pool_authority` 状态位
      （**未**复用 `candidate_exchange_authority` / `composite_authority`）；
      缺省回落 `Legacy` ⇒ 写端点被拒（`manager_apply_enabled()` 仅在 `Manager` 时为真）
      —— `manager_api/src/main.rs:18`、`AppState.directional_pool_authority`、`with_directional_pool_authority`

- [x] **负向验证**：未设置该环境变量时，`POST /v1/directional-pools/rebuild`（`--apply`）
      **被拒绝**并返回授权拒绝；设置后才可写
      —— **实盘读数**：未设 `DIRECTIONAL_POOL_MANAGER_AUTHORITY` 时 **HTTP 403**，
      `error.code = TOKEN_AUTHORITY_WRITE_DENIED`，
      `details.authorityMode = "legacy"`，
      `message = "Directional Pool Manager apply is disabled while authority mode is legacy"`
      （注：实现返回的**错误码**是 `TOKEN_AUTHORITY_WRITE_DENIED`，不是 `authority_denied`；已按实现订正）

- [x] `token_manager/src/prune.rs` **纳入方向表清理**（不留孤立行）
      —— `DIRECTIONAL_POOL_TABLES = [("manager_v4_directional_exchange_pools", "base_pool_id")]`

- [x] 迁移烟雾测试与架构守卫**已登记**新表名 / 新列
      —— 烟雾测试登记两张新表；**新列**由 `test_the_directional_pool_columns_are_registered_in_the_migration_ledger` 钉住

---

## 2. 行为验收（单元测试，**T-D-1..T-D-14 全过**）

| # | 场景 | 期望 | 判定 | 证据（测试名） |
|---|---|---|---|---|
| **T-D-1** | **每个池产生两行** | 方向 0 / 1 各一行 | ☑ | `one_base_pool_projects_onto_exactly_two_rows` |
| **T-D-2** | **方向映射不可翻转** | 人为构造 `token0` 地址更高的池，方向仍为 0，输入仍是 token1 | ☑ | `the_direction_mapping_cannot_flip` + `a_pool_with_an_inverted_token_order_is_not_projectable`（`manager-contracts`） |
| **T-D-3** | **排序与旧口径一致** | 方向表序位 `1..N` 与 `recommend_direction` 的输出顺序**逐行相等** | ☑ | `the_rank_follows_the_candidate_edge_recommendation_order`（**本轮新增**） |
| **T-D-4** | **序位连续** | `(pair_key_id, direction)` 内无缺号、无重号 | ☑ | `the_ranks_are_continuous_inside_a_pair_and_a_direction` |
| **T-D-5** | **容量来源正确** | 方向 0 取 token1 侧，方向 1 取 token0 侧 | ☑ | `the_capacity_comes_from_the_input_side_of_the_direction` |
| **T-D-6** | **未测量不被当作零** | `NULL` 与 `'0'` 分开存储 | ☑ | `an_unmeasured_capacity_is_not_a_measured_zero` |
| **T-D-7** | **非活池不获序位** | `pool_status != 'active'` 的行 `pair_direction_rank IS NULL` | ☑ | `a_blocked_pool_is_projected_but_it_never_takes_a_rank` |
| **T-D-8** | **物化幂等** | 重复 apply → `changedRows = 0`；摘要变化 → 校验和变 | ☑ | `the_materialization_is_idempotent` |
| **T-D-9** | **dry-run 不写库** | 幂等表**无新记录** | ☑ | `a_dry_run_writes_nothing` |
| **T-D-10** | **幂等重放** | 相同键 + 相同摘要 → `idempotentReplay=true`；摘要变 → 拒绝 | ☑ | `the_idempotency_key_replays_the_first_result` + `a_reused_idempotency_key_with_a_different_body_is_refused` |
| **T-D-11** | **一个基础池恰好两行** | 行数 == 基础池数 × 2；两行**仅**方向相关字段不同 | ☑ | `one_base_pool_projects_onto_exactly_two_rows` + `the_two_rows_of_one_base_pool_differ_only_in_the_direction_fields` |
| **T-D-12** | **内存集合与库行集合一一对应** | 装载后两者是**同一个集合** | ☑ | `the_in_memory_rows_and_the_table_rows_are_the_same_set`（**本轮新增**） |
| **T-D-13** | **准入约定不被突破** | **带序位**的行只来自 `active` + 固定费率 + 无 hook + 非动态费率的基础池 | ☑ | `only_an_admissible_base_pool_may_carry_a_rank`（**本轮新增**，判据见 §0 订正） |
| **T-D-14** | **边界来源不得是漂移的存量列** | 容量**由 Rust 侧重算**，不得采信过期存量列 | ☑ | `the_capacity_is_recomputed_and_not_read_from_the_stored_column`（**本轮新增**） |

> **T-D-3 的成立依据**：方向层的排序键与 `candidate_edge_pool_manager::recommend_direction` 的排序键
> **逐项相同** —— ①精确单跳产出降序 → ②tick 序位降序 → ③费率升序 → ④池 id 升序；
> 且两者的产出都取自**同一个** `exact_directional_output`（方向层经公开封装
> `price_directional_output` 调用）。**本轮新增的测试**用该公开函数独立复算期望顺序并逐行比对，
> 因此任一侧改动排序键都会立刻失败。

---

## 3. 不变量验收（**I-1..I-6 / I-9 / I-10**）

| ID | 不变量 | 检查方式 | 判定 | 证据 |
|---|---|---|---|---|
| **I-1** | 方向 0 行的 `input_token_key_id` 恒等于 `token1_key_id`；方向 1 行恒等于 `token0_key_id` | 物化时断言 | ☑ | `the_direction_mapping_cannot_flip` |
| **I-2** | `direction = 1 ⇔ zero_for_one = 1` | 物化时断言 | ☑ | 实盘行读数：方向 0 行 `zeroForOne=false`、方向 1 行 `zeroForOne=true` |
| **I-3** | 同一 `base_pool_id` 的两行，除方向字段与方向量外**完全一致** | 物化时断言 | ☑ | `the_two_rows_of_one_base_pool_differ_only_in_the_direction_fields` |
| **I-4** | `input_capacity_raw` 来源恒定（方向 0 ← token1 侧，方向 1 ← token0 侧），且**只取重算值** | 物化时断言 | ☑ | `the_capacity_is_recomputed_and_not_read_from_the_stored_column` |
| **I-5** | 方向表**不包含**任何基础池表与快照表之外的数据 | 字段白名单 + 审计 | ☑ | `directional_checksum` 的显著字段白名单 |
| **I-6** | `pair_direction_rank` 唯一且在 `(pair_key_id, direction)` 内连续 | 物化时断言 | ☑ | `the_ranks_are_continuous_inside_a_pair_and_a_direction` |
| **I-9** | 方向行的 `base` 字段与基础池表对应行**逐列一致** | 比对报告 | ☑ | 实盘 `projectionMismatches = 0` |
| **I-10** | 内存中的方向行集合与方向表**行集合一一对应** | 物化时计数断言 | ☑ | `the_in_memory_rows_and_the_table_rows_are_the_same_set`；实盘 `poolsWithoutTwoRows = 0` |

---

## 4. 归属验收（**M-6 / M-3**）

- [x] 新增架构守卫：Python **不得**直查方向池表
      —— `test_directional_pool_read_guard_rejects_a_direct_query` + `…_is_not_vacuous`

- [x] 新增架构守卫：Python **不得**绑定方向池层的**版面**（排序 / 去重 / 物化 / 准入 / 边界重算 / 方向映射的载体）
      —— **本轮新增** `test_python_never_binds_to_the_directional_pool_layer_layout`
      （禁 `directional_pool_id` / `pair_direction_rank` / `input_token_key_id` /
      `output_token_key_id` / `directional_output` / `input_capacity_raw` 等**列名**，
      按**词边界**匹配，故 `select_directional_pool_ids` 这类旧函数名不误伤）

- [x] 新增架构守卫：方向层的准入与边界**只在 Rust 侧**（**DPO-9** / **DPO-10**）
      —— **本轮新增** `test_the_directional_admission_convention_is_enforced_in_rust_only`：
      动态费率门 / hook 门 / 规范 token 序门 / 非 active 不给序位 四道门必须留在
      `directional_pool_manager`，且该 crate **不得**自带第二份 `exact_directional_output` 算术

- [x] 新增架构守卫：方向表**新列**必须登记进迁移账本
      —— **本轮新增** `test_the_directional_pool_columns_are_registered_in_the_migration_ledger`

- [x] 架构守卫全量通过（基线 **71 passed** → 本轮 **74 passed**，**只增不减**）

---

## 5. 边界验收（**不得**发生）

- [x] **本阶段只新增类型，不改任何调用方** —— 两份 `ScopePool` 仍在使用，旧链路行为**一字不变**

- [x] 基础池表（`v4_exchange_pools_core4` / `v4_exchange_pools_candidate_exchange`）与
      `v4_exchange_pool_liquidity_snapshots` 的**表结构与写入语义**未变
      —— 实盘**迁移前后全表行数指纹对比**：只有 2 张新表新增、`schema_migrations` 15→16

- [x] 现有链路任何表**行未变**（候选 / 观察 / 闭环 / 复核 / 广播 的行数与校验和均未变）
      —— 实盘指纹：`v4_exchange_pools_core4=488`、`v4_exchange_pools_candidate_exchange=494`、
      `manager_dex_candidate_pools=170`、`manager_v4_closed_loops=168`、`manager_v4_reviews=164`、
      `manager_cow_execution_attempts=0` **迁移前后完全一致**

- [x] **未**改 `liquidity_index` 的任何含义（**C-3**）

- [x] **未**删除任何旧函数（旧方向键、旧排序仍可用，下线是 `T-方储.01`）

- [x] **未**改变任何循环 / 折叠 / 定价算术

- [x] **未**广播（本任务只写方向池表，广播面**零改动**）

---

## 6. 证据与回归验收

- [x] 单元测试通过记录（**T-D-1..T-D-14 逐条**，见 §2）
      —— `directional-pool-manager` **18 passed / 0 failed**

- [x] 迁移烟雾通过记录 —— `manager-db` **5 passed / 0 failed**

- [x] 架构守卫通过记录 —— **74 passed / 0 failed**

- [x] 只读物化比对报告：新表行数 == 基础池数 × 2；两行除方向相关字段外**逐列一致**；
      `changedRows` 幂等证据；校验和前后值
      —— **实盘**：`basePoolCount=494` → `directionalPoolCount=352`（176 × 2 ✓）；
      `poolsWithoutTwoRows=0`、`directionMismatches=0`、`projectionMismatches=0`、
      `examinedRows=352`、`readyForCutover=true`；
      幂等重放 `idempotentReplay=true`、校验和不变
      （`sha256:9b483796a70e5cb4ca9b70961bf2d0b3ea3f3542a1fd6af0ddb7187b1d627234`）

- [x] migration 已**实盘写入**目标生产库（基础框架库**不需**额外授权，验证通过即**必须**写入）
      —— **已写入** `flashloan/db/local_store_4663.sqlite3`，`appliedNow=true`，
      `migrationTable=schema_migrations`；**只**应用 `20261123_015` 这一条，
      其余 15 条的校验和**逐一比对未变**

- [x] 方向表**行的物化**已按 §0.0 完成授权
      —— **已授权并完成**：352 行落库（176 基础池 × 2 方向），
      带序位 **173** 行且 `pool_status` **全部** `active`，
      费率窗口 / hook / 动态费率**违规数 ＝ 0**

- [x] 全量回归：**按改动模块集合跑**（`M-3` + `M-9` + `M-10`），**不跑**全量
      —— `cargo test --offline -p directional-pool-manager` → **18 passed**
      —— `cargo test --offline -p manager-contracts` → **17 passed**
      —— `cargo test --offline -p manager-db` → **5 passed**
      —— `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` → **74 passed**
      —— `cargo build --offline --release -p manager-db -p manager-api` → **exit 0**

---

## 7. 剩余项与下一步（**移交闭环层**）

| 项 | 归属 | 说明 |
|---|---|---|
| 闭环层**尚未**消费方向池 | `T-方闭储.01`（`M-4`） | `closed_loop_manager` 至今仍读 `manager_market_observation_intermediate_pools`；方向池表**已就绪但未接线** |
| 旧方向键与旧排序下线 | `T-方储.01`（`M-3`+`M-10`） | 本任务**未**删除任何旧函数，旧路径仍在生产链路上 |
| 备用池接线 | `T-方闭.01`（`M-4`） | 依赖 `M-3`，现已解除 |

> **诚实边界**：本任务把方向层**建好并落库**，但**闭环还没有改用它**。
> 在 `T-方闭储.01` 完成前，**主网闭环走的是旧路径**；
> 方向池表此时是「已发布、未消费」状态（`consumersNeedRebuild=true` 正是这个意思）。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/README.md` -->

# `T-方授储.01`｜方向层落地

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方授储.01`（纯 ID）　**验收**：`AC-方授储.01`　**优先级**：P0　**波次**：W-0
**编号逻辑**：`T-方授储.01` / `AC-方授储.01` —— **跨模块（组合索引）**，编号索引 **`方授储`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入）） ＋ `M-9`（授权与回滚（授权维度 / 守卫 / 回滚脚本）） ＋ `M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-3` 、`M-9` 、`M-10`

**前置模块**：—　　**需授权**：否（DDL）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**0** 项未完成 / **32** 项已闭合） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-contracts
pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py
cargo test --offline -p manager-db
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d080"></a>

### `D-080-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/docs/D-080-现状与可行性分析.md` -->

# D-080 01 当前现状与可行性分析

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

**下一步：**进入 [02-目标架构与表结构](#d081)，看具体的表设计与闭环改造。

---

<a id="d081"></a>

### `D-081-目标架构与表结构.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/docs/D-081-目标架构与表结构.md` -->

# D-081 02 目标架构与表结构

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
- 实施顺序、验收条件与需授权动作见 [03-迁移实施与验收](#d082)。

---

<a id="d082"></a>

### `D-082-迁移实施与验收.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/docs/D-082-迁移实施与验收.md` -->

# D-082 03 迁移实施与验收

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

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方授储.01/docs/README.md` -->

# `T-方授储.01`｜方向层落地 · 技术文案（docs）

> **本目录是 `T-方授储.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

方向池的表结构、共享类型、物化语义与迁移验收

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d080) | 本任务相关的技术文案 |
| [`02-目标架构与表结构.md`](#d081) | 本任务相关的技术文案 |
| [`03-迁移实施与验收.md`](#d082) | 本任务相关的技术文案 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
