# D-146 `T-方.02`｜方向层按链批量物化入口 · 验收清单

> **本文件是 `T-方.02` 的全部验收框。** 编号 `AC-方.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-3` 的现有单测全绿

**本轮状态（2026-11-26）**：**13** 框**全部**闭合（每条附实测读数）。
**实盘侧**：方向层已**真实刷新**（apply 落地，1.63 秒，`failedScopes` 为空），
刷新前已留 **692 行**行级备份；复核全程只读（`mode=ro` ＋ `PRAGMA query_only = ON`）。
**如实登记**：闭环层**未**重建（`closed_loop_version` 21 → 21 不变）——
闭环重建归 `T-闭.03` 的部署闸门（迁移 018 ＋ 新二进制同批），**不在本任务里代做**。

---

## 1. 根因与定位

- [x] 方向行**唯一**的生产写入方已定位到源码行

      > **读数（2026-11-26 复核）**：`directional_pool_manager/src/lib.rs:1897`
      > `replace_directional_rows` —— 全仓唯一写入
      > `manager_v4_directional_exchange_pools` 的生产语句
      > （`DELETE` 在 `:1907`、`INSERT` 在 `:1913` ⇒ 删旧插新 ＝ **全量替换**）；
      > `candidate_edge_pool_manager:3429` 与 `candidate_exchange_pool_manager:3231` 的同名字语句
      > **只在 `#[cfg(test)]` 的夹具里**出现。

- [x] 方向层的**快照列**来源已定位

      > **读数（2026-11-26 复核）**：`directional_pool_manager/src/lib.rs:1359-1364` ⇒
      > 逐列来自 `v4_exchange_pool_liquidity_snapshots`（即流动性 worker 刷新的那一份）；
      > 该表不存在时报 `MissingSnapshotTable`（`:1326-1333`），**不**降级成「测得为零」。

- [x] 实盘漂移量已量化

      > **读数**（`audit_directional_invariants_20251125` → `directional_invariants_postapply_20261125.json`）：
      > 方向行 **692**；可比 **436**；漂移 **83** 行 / **994** 格；无覆盖 **256** 行；
      > 方向层物化时刻 **2026-11-23 22:46**；快照表最新 **2026-11-25 00:44**。
      > **刷新后（2026-11-26 实盘）**：漂移 **0** 行 / **0** 格（FAIL → PASS），
      > 可比 436 / 无覆盖 256 不变 ⇒ 漂移**由本任务消除**（§4 有逐条读数）。

---

## 2. 实施

- [x] 新增公共入口「按链批量物化」已落地

      > **读数**：`materialize_directional_pools_for_chain`（`directional_pool_manager/src/lib.rs:505-556`）；
      > 返回体 `DirectionalPoolChainMaterializationResult`（`:459-482`）字段清单见 `D-147` §2.2；
      > 最小驱动 `examples/materialize_directional_pools_for_chain.rs`（只调入口，**不**重算任何口径）。

- [x] 枚举口径**确定**（按 `pool_scope_config.enabled = 1`，`scope_key` 升序）

      > **读数**：SQL 在 `lib.rs:564-567`（`enabled = 1` ＋ `ORDER BY scope_key`）；
      > 实盘枚举结果 `scopeKeys = ["candidate_exchange", "core4"]`（`D-147` §3.2）；
      > `pool_scope_config` 只有这 **2** 行 scope（本链），启用的也是这 2 个
      > （`fang02_preapply_state_20261126.json` 的 `scopeConfiguration`）。

- [x] 单 scope 行为与既有入口**逐字一致**

      > **读数**：单测 `the_chain_entry_materializes_every_enabled_scope_in_scope_key_order`
      > 逐字段比对链级入口与既有单 scope 入口的返回体
      > （`directional_pool_count` / `direction0_count` / `direction1_count` / `materialized_checksum`）；
      > **实现层面**：链级入口的每个 scope 调的就是同一个 `materialize_impl`（`lib.rs:530`），
      > 投影 / 准入 / 容量**一行未改** ⇒ 逐字一致不是「测得像」，是**同一段代码**。

- [x] **未放宽**任何门禁（投影规则 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联）

      > **读数**：`git diff -- flashloan/tools/directional_pool_manager/src/lib.rs` =
      > **1 个 hunk、124 行新增、0 行删除** ⇒ 既有函数**一字未改**；
      > 投影 / 准入 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联**全部落在既有函数里**，
      > 它们没有被这个 diff 触及。
      > 另：测试文件改了夹具助手（`insert_pool` → `insert_pool_into`），**仅测试代码**。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p directional-pool-manager` 全绿

      > **读数（2026-11-26）**：**27 passed / 0 failed / 0 ignored**
      > （基线 **25** ＋ 本任务新增 **2**：`the_chain_entry_materializes_every_enabled_scope_in_scope_key_order`、
      > `a_scope_that_fails_is_reported_and_does_not_hide_the_other_scope`）。

---

## 4. 实盘（**只读复核 → apply → 复核**）

- [x] dry-run 读数与 `inv-9` 读数对得上

      > **读数**：干跑 `changedRows` = **352**（`candidate_exchange`）/ **340**（`core4`）
      > ＝ 刷新前方向行数（`scope` 内每一行都要重写）⇒ **692 行全部被刷新**；
      > `directionalPoolVersion` 预读 **3**（刷新前库内存的是 **2**）。
      > **A/B**：干跑读数与只读副本上的同一次演练**逐字段相等**（机器比对 `True`），
      > 副本上 apply 后 `inv-9` 由 83 行/994 格转 **0/0** ⇒ 干跑**对得上** `inv-9`。

- [x] apply 后 `inv-9` 由 FAIL 转 PASS

      > **读数（2026-11-26 实盘 apply 后）**：`inv-9` **漂移 0 行 / 0 格**
      > （FAIL → PASS）；可比 436 / 无覆盖 256 **不变**。
      > 全量不变式审计：**9 passed / 0 failed**（`inv-1`…`inv-9` 全 PASS，
      > 其中 `inv-2` 692 行 / 0 不符、`inv-4` 522 跳全部可解析、`inv-7` 346 组各 2 行、
      > `inv-8` 222 组序位连续）；证据 `fang02_postapply_invariants_20261126.json`。

- [x] 方向层版本与校验和前进，且**闭环层读数未变**

      > **读数（2026-11-26 实盘）**：
      > `directional_pool_version` **2 → 3**（两个 scope 各前进一代）；
      > `materialized_checksum` `f6836f97…` → `4aa8b791…`（`candidate_exchange`）、
      > `5a609caf…` → `e0d8d6d1…`（`core4`）⇒ **前进**；
      > 方向层记录的 `snapshot_checksum` `863851e3…` → `01adb94a…`（与快照表当前值一致）；
      > `manager_v4_closed_loop_library_state.closed_loop_version` **21 → 21（不变）**、
      > 校验和 `034e7801…` **不变**、`updated_at` 仍为 **2026-11-25T10:14:49Z**
      > ⇒ **闭环层一行未动**（本任务只物化方向层）。

- [x] 实盘复核全程**只读**

      > **读数**：复核探针 `probe_directional_layer_state_20261126.py` 以
      > `file:...?mode=ro` URI 打开 ＋ `PRAGMA query_only = ON`（探针内 `open_read_only`）；
      > 入口的干跑用 `SQLITE_OPEN_READ_ONLY` 句柄（`lib.rs:2264-2271`）；
      > 不变式审计器 `audit_directional_invariants_20251125.py` 亦为只读。
      > ⇒ **实盘的写操作只有一次**：入口的 `apply`（692 行方向行 ＋ 2 行库状态），
      > 其余全部只读；闭链表、遗留表、快照表**零写入**。

---

## 5. 诚实边界

- [x] 本任务**未**改动闭环层 / 运行时维护链 / 内存大对象

      > **读数**：本任务的改动**只**在 `M-3`（`D-145` §1）⇒ 闭环层（`M-4`）、
      > 运行时维护链（`M-13`）、内存大对象（`M-2`）**均未触碰**；
      > 实盘复核也证明这一点：闭环层 `closed_loop_version` 21 → 21、校验和与 `updated_at` 不变。
      > 这三个缺口分别归属 `M-4`（`T-闭.03`）/ `M-13` / `M-2`（`T-核.02`，**另一线程**）的任务（见 `D-143` §3）。

