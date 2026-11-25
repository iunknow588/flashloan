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

- [ ] 实盘漂移拒绝**归零**

      > 判据：`/v1/reviews` 中 `reviewReason` **不再**出现
      > `single_tick_gate_failed:pool_state_version_drift`。

- [ ] `completed` **不再恒为 0**

      > 判据：`/v1/reviews` 的 `completedCount > 0`
      > （前提：存在**正利润且通过容量门禁**的路线）。

- [ ] 实盘库**零写入**

      > 判据：本任务**不写**实盘库；只读脚本一律 `mode=ro` ＋ `query_only`。

