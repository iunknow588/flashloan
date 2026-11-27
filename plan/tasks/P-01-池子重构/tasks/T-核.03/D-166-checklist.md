# D-166 `T-核.03`｜流动性快照写入路径接线 · 验收清单

> **本文件是 `T-核.03` 的全部验收框。** 编号 `AC-核.03` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-1` 现有单测全绿

**本轮状态（2026-11-26）**：**16** 框**全部**闭合（每条附实测读数）。
**实盘侧**：**未做**任何实盘写入；只读探针读数见 §1。
**如实登记**：修复**只在代码里** —— 实盘扩展未重建、runtime 未重启（部署闸门，见 `D-164` §4）；
`mode` 判据**未切换**（新鲜度只做观测量）。

---

## 1. 根因与定位

- [x] 快照表的新鲜度已量化（**只读**）

      > **读数**（`plan/runtime_evidence/liquidity_snapshot_freshness_20261126.json`，
      > 探针 `probe_liquidity_snapshot_freshness_20261126.py`，`mode=ro` ＋ `query_only`）：
      > `v4_exchange_pool_liquidity_snapshots` **2486** 行 / **2486** 个池；
      > 最新 `liquidity_read_at` = **2026-11-25T00:44:27.222895100+00:00**（+08 08:44）；
      > 行龄分布 `2026-11-17: 1244`、`11-22: 358`、`11-25: 96`（其余为 10-16 的 776 行）；
      > `max(liquidity_block_number)` = **120653940**。

- [x] 「陈旧被当成完整」已定位到**源码行**

      > **读数**：`robinhood_v4_idle_maintenance.py:1682`
      > `mode = "complete" if missing_after == 0 and wrapped else "scanning"`；
      > 而 `missing_after` = `_candidate_eligible_missing_liquidity_count`（`:696`）
      > → `_candidate_eligible_liquidity_counts` ⇒ `eligibleUnknownCount`
      > = 「`l.pool_id IS NULL` **或** `l.has_liquidity IS NULL`」的池数
      > ⇒ 数的是**裁决是否存在**，**不是**裁决有多旧。
      > 实测佐证：合格池（`fee ≤ 30000`、无 hook、已知代币）**792** 个，
      > `no_row = 0` / `null_verdict = 0` ⇒ `missing_after = 0` ⇒ `complete`；
      > 而同期快照行最新只到 9 天前 ⇒ **判据成立、事实陈旧**。

- [x] 快照表的**唯一写入方**与其**全部调用点**已定位

      > **读数**：唯一写入方 = `pool_manager::refresh_depth`（`tools/pool_manager/src/lib.rs:1931`）
      > → `write_depth_snapshot`（`:2677`，**UPDATE** 语句）；
      > 全仓 `refresh_depth` 的调用点**只有一个**：`tools/pool_manager/src/main.rs:536`
      > （**命令行** `depth --apply`）⇒ **运行时没有任何一条路能写这张表**。
      > 证据通道**刻意**不写：`rs-src/src/evidence.rs:1012` 回执明写
      > `liquidityRowsWritten: 0`、`depthSnapshotWriter: pool_manager`
      > （TASK-172 / AC-221：证据通道写一半列会把旧量程的 single-tick 边界与新价格焊在同一行）。

- [x] 「量到了却写不进去」的分叉点已定位

      > **读数**：同一 tick 内 `v4_exchange_pools` 前进 **331** 行（`updated_at` = **2026-11-25T18:36:57Z**），
      > 而快照表零写入 ⇒ **证据通道写了池索引、没写快照表**；
      > worker 游标 `last_scanned_at` = **2026-11-25T18:36:58Z**、`last_block_number` = **123234506**
      > （比快照行的最大区块 **120653940** 新 **2,580,566** 个区块）⇒ **worker 读到了最新区块，
      > 但读到的状态没有落库**。

---

## 2. 实施（写入路径）

- [x] 新增 Rust 入口：池管理器深度刷新

      > **读数**：`flashloan/rs-src/src/pool_depth.rs`（**新文件**）
      > `refresh_pool_liquidity(...)` → `pool_manager::refresh_depth(...)`；
      > `resume: false`（resume 会跳过「已有时间戳」的池，而 tick 的目的正是把时间戳推前）、
      > `max_pools: None`（调用方点名了池，池管理器不自己挑）、
      > 网络与链 id 不一致 ⇒ **拒绝写入**（单测 `a_network_that_does_not_match_the_chain_id_is_refused`）。

- [x] 入口已注册到扩展，Python 侧只「调入口 ＋ 读回执」

      > **读数**：`rs-src/src/lib.rs` 注册 `#[pyfunction] refresh_v4_pool_liquidity`（**加法式**）；
      > `src/core/rs_bridge.py` 新增同名桥（只做 JSON 编解码 ＋ 回执校验）；
      > Python 侧**零算法**：不量测、不算容量、不判准入。

- [x] 维护槽已接线，且**失败即整槽失败**（不降级）

      > **读数**：`robinhood_v4_idle_maintenance.py:1490` 调用入口；
      > 入口不可用（`rust_available()` 为假）⇒ `errorCode = BRIDGE_UNAVAILABLE`；
      > 入口拒绝 ⇒ `errorCode = POOL_MANAGER_REFUSED`；
      > 两者都 ⇒ `phase = snapshot_materialization_failed`、游标 `mode = error` ＋ 带错，
      > **不**回退到旧测量、**不**继续发布下游（单测 `a_refused_pool_manager_write_fails_the_tick`、
      > `a_missing_rust_extension_fails_the_tick`）。

- [x] 池管理器回执改为**诚实计数**

      > **读数**：`write_depth_snapshot` 由 `Result<(), String>` 改为 `Result<usize, String>`，
      > 返回 `connection.execute` **真正改动的行数**；回执新增 `rowsWithoutSnapshotCount`
      > （有测量、但表里没有行可写的池数）。
      > 改前 `written` 计的是**尝试次数**（`written += 1`）⇒ 一个不存在的行也算「写了」；
      > 改后才与「行是否真的动了」一致。

- [x] **未**改任何选池条件，也**未**把新鲜度接进判据

      > **读数**：`mode` 判据（`:1682`）**一字未改**；
      > 方向层 `assign_ranks`（`directional_pool_manager/src/lib.rs:1738-1744`）**零改动**；
      > 候选层准入（`M-5`）**零改动**；闭环层（`M-4`）**零改动**。
      > 新鲜度的落点**只有**回执的 `snapshotFreshness` 与游标的 `snapshot_read_at` /
      > `snapshot_rows_written` ⇒ **纯观测量**。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p pool-manager` 全绿

      > **读数（2026-11-26）**：**167 passed / 0 failed / 0 ignored**（基线 **167** 不变）。

- [x] `cargo test --offline`（`flashloan/rs-src`）全绿

      > **读数（2026-11-26）**：**222 passed / 0 failed**（基线 **221** ＋ 本任务新增 **1**：
      > `pool_depth::tests::a_network_that_does_not_match_the_chain_id_is_refused`）。

- [x] Python 维护槽回归全绿

      > **读数（2026-11-26）**：`test_v4_maintenance_workers.py` **19 passed / 0 failed**（基线不变）；
      > `test_v4_pool_liquidity_snapshot_writer.py`（**新增**）**3 passed / 0 failed**。

---

## 4. 实盘（**只读**）

- [x] 实盘读数由**只读**探针取得，实盘库**零写入**

      > **读数**：探针 `probe_liquidity_snapshot_freshness_20261126.py` 以
      > `file:...?mode=ro` 打开 ＋ `PRAGMA query_only = ON`；
      > 本任务**未**对实盘库执行任何写操作；
      > 实盘 runtime（pid **24524**，2026-11-25 17:14 启动）**未重启、未受影响**。

---

## 5. 诚实边界

- [x] 修复**尚未在实盘生效**，如实登记

      > **读数**：实盘扩展 `flashloan/rs-src/target/release/flashloan_rs.dll`（2026-11-25 14:32）
      > **不含**新入口 `refresh_v4_pool_liquidity` ⇒ 实盘维护槽**仍不写快照表**；
      > 重建 Rust 扩展 ＋ 重启 runtime 属**部署闸门**（`D-164` §4），
      > 前置是 `T-核.02`（内核大对象，**另一线程**）的 `rs-src` 改动收敛并回归
      > ⇒ 此刻重建会把**未经其回归**的代码带上线 ⇒ **本轮不做**。

- [x] `mode` 判据**未切换**，如实登记后果

      > **读数**：`worker_complete()`（`v4_maintenance_workers.py:196-212`）= mode ∈ {complete, …}
      > 且有 `refreshed_at` 且**无 error**；若把陈旧写进游标 `error`，下游候选 / 观测 / 闭环槽
      > 会判 not-ready ⇒ **运行时会安静下来** ⇒ 判据切换**必须**在写入路径实盘验证之后。

- [x] 本任务**未**改动内核大对象（另一线程的地盘）

      > **读数**：`rs-src/src/worker_pool.rs` 与 `rs-src/src/runtime_core.rs` 本任务**零改动**；
      > `rs-src` 的改动落在**新文件** `pool_depth.rs` ＋ `lib.rs` 的加法式注册
      > ⇒ 与另一线程的 `lib.rs` 改动**不重叠**。

