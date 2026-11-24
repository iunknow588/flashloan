# D-115 `T-闭.01`｜方向层主序接管闭环主池 · 测试目标 check list

**验收前置**：`M-3`（方向层）已产出 `pair_direction_rank`（`T-方授储.01` 已闭合）。

> 勾选纪律：**每一条都必须有可复算的实测读数**；未实测项写「**未测量**」，**不得**用零冒充。
> 全部读数的出处见 [`docs/D-116-实施与影子复算报告_20261125.md`](./docs/D-116-实施与影子复算报告_20261125.md)。

#### 结构验收

- [x] 旧主序的两个来源已定位并登记：`manager_dex_candidate_pool_recommendations.recommend_direction`（首跳 / 末跳）＋ `manager_market_observation_yz_pairs.optimal_pool_id`（中间跳）
- [x] 两套排序算法的**差异已写清**：方向层 = `directional_output` desc → `tick_ordering_rank` → `fee` → **capacity** → `base_pool_id`；旧 `recommend_direction` = `exact_output` desc → tick_rank desc → `fee` → `pool_id`；旧 `pool_score` = **深度代理**（`liquidity_index` 排序，**非价格质量**）
- [x] 方向层 rank-1 在旧候选层**截断窗口内的占比已实测**：`candidateTier notInTier` **0 / 216**、`yzTier notInTier` **0 / 386` ⇒ **重排即可接管，不必引入新池**
- [x] 闭环主池改由**方向层 rank-1** 决定（代码落点：`closed_loop_manager/src/lib.rs::order_hop_candidates_by_direction_rank`）
- [x] 回滚开关已落地：`FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY`（**缺省 ON**；仅 `0`/`false`/`off`/`no` ⇒ OFF；其它值 ⇒ ON）
- [x] 开关**每次物化只读一次** ⇒ 一次运行内口径一致（发布行与本次运行的顺序永远对得上）
- [x] 主池的 `pool_selection_basis` 改写为 `directional_pair_direction_rank:{rank}`（**发布行不说谎**）；方向层 rank 另存 `manager_v4_closed_loop_hops.directional_pool_rank` ＋ `optimization_basis` ⇒ **信息不丢**
- [x] `optimization_id` 采用**位置制**（`{prefix}-rank-{n}`；位置 0 ⇒ `NULL`）⇒ **构造上唯一**，零主键冲突风险

#### 行为验收

- [x] 闭环层回归全绿：`cargo test --offline -p closed-loop-manager` ⇒ **46 passed / 0 failed**（原 41 ＋ 新增 5）
- [x] **闭环身份不变**：闭环 **174 → 174**（新增 **0** / 移除 **0**）
- [x] **跳数不变**：**522 → 522**；跳过令牌 **1100 → 1100 且逐条相同**（`identical = true`）
- [x] **候选成员集逐跳完全一致**（`candidateMembershipPerHopIdentical = true`）⇒ **纯置换**，**未引入任何新池**
- [x] **门禁未放宽**：容量门禁仍生效（容量 **37 变好 / 46 变差 / 91 不变**）；`temporary` 覆盖 **6 → 0** ⇒ 6 条「容量门禁换池接管」全部消失，**不是被跳过而是被方向层序位直接选中**
- [x] **价格质量（利润代理）实测**：**168** 条可测 ⇒ **89 变好 / 0 变差 / 79 不变**；合计 **167.76297 → 168.46708（+0.4197 %）** ⇒ **方向层序位对价格质量单向占优**
- [x] **旧序可回滚**：开关 OFF 时对同一切片重物化 ⇒ `changedRows = 0`、`closedLoopVersion = 19` ⇒ **与实盘发布行逐行一致**（零差别的对照证明）
- [x] 「旧口径切换」诊断位**未变差**：`readyForCutover` 改动**前已是 `false`**（闭环 174 vs 104、跳 522 vs 312、`fieldMismatch` 225 行）⇒ 本改动**不使该诊断位变差**
- [x] 换池率已量化：跳维度 **118 / 522 = 22.6054 %**（`y-z` **44**、`yc` **41**、`zc` **33**）；池集合变化的闭环 **95 / 174 = 54.5977 %**

#### 边界验收（**不得**发生）

- [x] **未**引入旧候选层之外的池（成员集逐跳一致）
- [x] **未**放宽任何门禁（容量 / 拓扑 / 幂等 / 阈值**一字未改**）
- [x] **未**触碰 `broadcast_manager` / `review_manager` / `directional_pool_manager` / Python 闭环构建器 / `.env` / `chain.json`
- [x] **实盘库零写入**（全程 `mode=ro` ＋ `PRAGMA query_only = ON`；复算在 **58 MB 瘦副本**上进行）
- [x] **未**在同一批次内同时「切换新口径」与「删除旧口径」⇒ 旧排序**仍在**，只**不再**决定主序（下线是 `T-方储.01` 的活）
- [ ] **实盘生效复测**：运行期重启后，实盘库重物化的发布行与新序一致（**本轮未重启** —— 实盘运行时 pid **27084** 自 2026-11-24 23:56:12 起**未重启**；新口径**须随下次重启才生效**）

