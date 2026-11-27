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
