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
