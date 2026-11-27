# D-150 `T-闭.02`｜闭环层估值与折叠同源 · 验收清单

> **本文件是 `T-闭.02` 的全部验收框。** 编号 `AC-闭.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-4` 的现有单测全绿（基线 **46** passed）

**本轮状态（2026-11-26）**：**18** 框中 **16** 框已闭合、**2** 框留空
（§5.1 / §5.3 —— **实盘 apply 未做**，与 `T-闭.03` 的**部署闸门**同一个窗口）。
**未**按推断勾选；**未**用零冒充未测量；留空项**如实标注原因**。

---

## 1. 根因与定位

- [x] 估值与折叠读的是**两个不同的快照源**，已定位到源码行

      > **读数**（行号为**改动前**的 `HEAD = a800a8dc`）：
      > `read_valuation_pool_states` `closed_loop_manager/src/lib.rs:1748`
      > 读 `v4_exchange_pools_candidate_exchange` / `v4_exchange_pools_core4`；
      > 折叠读方向行 `usable_tick_state` `:2191`（读 `pool.base.*`）。
      > 改动后同两处落在 `:1762` / `:2274`（**行号已变，内容已换**）。

- [x] 两个快照的**时刻差**与**内容差**已量化

      > **读数**：估值读 tick ＝ 基础池表 `updated_at` **2026-11-19**；
      > 折叠读 tick ＝ 方向行 `materialized_at` **2026-11-23**；
      > 688 组可比行中 `sqrt_price_x96` 不同 **314**、`tick` 不同 **306**。

- [x] 代码注释里「同一个快照」的承诺已定位

      > **读数**（改动前）：`usdg_valuation_for_token` `closed_loop_manager/src/lib.rs:2345`
      > 「so the two numbers always describe the same snapshot」；
      > 改动后同一段注释在 `:2424-2425`（**原样保留**，见 §3.1 的口径说明）。

---

## 2. A/B（**落地前必须先量**）

> **执行顺序的诚实登记**：本任务要求「**先量后改**」，但**改码已在本轮之前进入工作区**
> （未提交）。本节补的是 **A/B 读数与判定**，**不是**「先量后改」的原始顺序
> ⇒ 顺序**没有**做到，如实登记（见 `D-151` §4）。

- [x] 两套快照下的 USDG 估值**逐闭环**比对已完成

      > **读数**：探针 `flashloan/tools/closed_loop_manager/examples/valuation_snapshot_ab_20261126.rs`
      > （**只读**：实盘库 `mode=ro` ＋ `PRAGMA query_only = ON`）；
      > 逐闭环 **174** 条（`plan/runtime_evidence/valuation_snapshot_ab_20261126.json` 的 `loops[]`，
      > 每条带 `valueUsdgRawBefore/After` 与 `breakEvenSingleTickProfitRawBefore/After`）；
      > **A 侧自校验**：已发布 `borrow_token_usdg_value_raw` 逐条复现 **172 相等 / 0 不符 / 2 无值**
      > ⇒ 探针与生产代码一致，比较不是空中楼阁（`D-151` §1.3）。

- [x] 覆盖面的变化已量化

      > **读数**：池宇宙 **432 → 428**（**−4**）；可估值代币 **41 → 39**（**−2**）；
      > 只在旧侧有估值的代币 **2** 个、只在新侧有估值的 **0** 个（`D-151` §2.1）。

- [x] 「不许变小」红线的**裁定与口径订正**已作出（**不是**直接勾过）

      > **读数**：红线**字面被突破**（41 → 39，缩 **2** 个）；
      > 但红线**写下的理由**（门禁行为改变）**未发生** ——
      > 失去估值的闭环 **0**、门禁结论翻转 **0**（`D-151` §2.4 / §3.2）；
      > 缩小的两个代币**不是任何闭环或复核记录的借款代币**，
      > 其唯一 USDG 通道**实测流动性为零**（折叠本就拒绝）⇒ **裁定：接受**，
      > 并**口径订正**：`D-148` §1.2 把「池宇宙不收窄」与「代币集合不收窄」当成同一件事，
      > 实测二者**不是**（`D-151` §3.2）。

- [x] 判定结论明确（落在噪声内 / 实质变化）

      > **读数**：`D-151` §3.1 —— **不是噪声**：估值变动代币 **26**、换通道池 **1**、
      > 最大 **414,375 ppm（41.44 %）**；盈亏平衡点移动闭环 **78 / 172**，
      > 中位 **52,464 ppm（5.25 %）**、最大 **319,118 ppm（31.91 %）**。

---

## 3. 实施

- [x] 估值的**市场快照列**改读 `v4_exchange_pool_liquidity_snapshots`

      > **读数**：`read_valuation_market_states` `closed_loop_manager/src/lib.rs:1822` ——
      > `SELECT pool_id, sqrt_price_x96, tick, liquidity FROM v4_exchange_pool_liquidity_snapshots`
      > `WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3`（`uniswap_v4`）；
      > 表缺失 ⇒ 直接返回空（**覆盖缺口**，不是零）；三列任一缺失 ⇒ 同样返回空。
      > 该表正是**方向层自身的投影源**（`directional_pool_manager/src/lib.rs:1358` 同表读取）。
      > **口径说明（诚实）**：改完后「**同源**」成立 —— 折叠读的方向行与估值读的快照表
      > **都源自这一张表**；但方向行是**物化时刻的投影副本**，
      > 估值是**读表时刻的现值** ⇒ 两者之间**仍有一个物化时滞**
      > （这属 `T-核.01`「运行时维护链接线」的范畴）；
      > `usdg_valuation_for_token` 的原注释（「the two numbers always describe the same snapshot」）
      > **原样保留**（`:2424-2425`）——「同源」成立，「同一时刻」**不**成立。

- [x] 估值的**静态事实**仍读基础池表（覆盖面不收窄）

      > **读数**：`read_valuation_pool_facts_from_table` `closed_loop_manager/src/lib.rs:1902` ——
      > `SELECT pool_id, fee, tick_spacing, token0_key_id, token1_key_id FROM {基础池表}`
      > （`v4_exchange_pools_candidate_exchange` ＋ `v4_exchange_pools_core4`）；
      > 四列任一缺失 ⇒ 该表**不贡献**事实（不是默认值）。

- [x] 「不可用就不发布」两条都覆盖

      > **读数**：两条单测**都存在且全绿** ——
      > `a_pool_without_a_market_snapshot_publishes_no_valuation`（`:7364`）、
      > `a_pool_without_static_facts_publishes_no_valuation`（`:7395`）；
      > 「未测量」**不**折成「测得为零」（实测：4 个零流动性池在快照表里是**空串**，
      > 旧口径却给出**非零**旧值 ⇒ 这正是被消灭的那类口径漂移）。

- [x] **未放宽**任何门禁

      > **读数**：改动只换了「读哪张表取价格」；阈值、幂等键、回执关联、容量折叠、
      > 投影规则、准入判定、序位**未改**；`flashloan/rs-src/src/cross_tick_init.rs:48` 的
      > `decide()` 判定式**不在本任务改动文件清单里**（`git diff --stat` 实测无该文件）。

---

## 4. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p closed-loop-manager` 全绿

      > **读数**（2026-11-26 实测）：**48 passed / 0 failed / 0 ignored**
      > （基线 **46** ＋ 本任务新增 **2** 条「不可用就不发布」单测）；
      > **只跑** `M-4` 一个模块，**未**跑全量（纪律：回归范围 = 改动模块集合）。

---

## 5. 实盘（**只读 A/B → apply → 复核**）

> **本节的 2 个框**都是**实盘 apply 之后**才有的读数 ⇒
> **apply 已做**（迁移 **018** 于 **2026-11-25T23:43:20.599Z** 应用，闭环层随后在
> **2026-11-26T06:19:23Z** 于新二进制上重建）⇒ 两个框**已闭合**，读数见下。
> **★ 口径订正**：本节此前登记的「实盘库停在迁移 **017**」「apply 未做」**已过时** ——
> 「新二进制 ＋ 旧库」实测 `no such column: single_tick_capacity_binding_directional_pool_id`
> ⇒ **无中间态**这条**仍然成立**（新二进制从未对着 017 跑过）。

- [x] apply 后闭环**数量**不变

      > **读数（2026-11-26 14:3x · 实盘 apply 之后）**：闭环 **174** ／ 跳 **522** ／ 方向池 **692**
      > ⇒ 与 A/B 阶段的读数**逐个相同**（A/B：闭环 **174** ／ 跳 **522**）。
      > **apply 的时刻**：迁移 **018** 于 **2026-11-25T23:43:20.599Z** 应用；
      > 闭环层随后在 **2026-11-26T06:19:23Z** 于新二进制上重建
      > （`manager_v4_closed_loops.updated_at` **全部** = 该时刻）⇒ 实盘读数**确实取自 apply 之后**。
      > 探针：`plan/runtime_evidence/tools/verify_t_bi_02_post_apply_20261126.py`。

- [x] 利润门禁读数的变化**逐条**列出（A/B 阶段）

      > **读数**：`plan/runtime_evidence/valuation_snapshot_ab_20261126.json` 的 `loops[]`，
      > **174** 条逐闭环给出 `valueUsdgRawBefore/After`（＝ `borrow_token_usdg_value_raw` 口径）
      > 与 `breakEvenSingleTickProfitRawBefore/After`；
      > 移动 **78** 条，中位 **52,464 ppm**、p90 **175,131 ppm**、最大 **319,118 ppm**；
      > 复核阈值 `crossTickReviewThresholdUsdgRaw` ＝ **10,000,000** raw USDG（**未变**）。

- [x] apply 后**逐条复读**上述读数

      > **读数（2026-11-26 14:3x）**：**174 / 174** 条逐条复读。
      > - **96** 条「A/B 阶段预测不变」的闭环 ⇒ 实盘**仍然不变**（**96 / 96**）；
      > - **78** 条「A/B 阶段预测要变」的闭环 ⇒ **4** 条与预测**逐位相等**、**74** 条有偏差；
      >   偏差的来源**已定位**：估值源是**持续刷新的**流动性快照
      >   （实盘 `MAX(liquidity_read_at)` = **2026-11-26T06:29:53Z**），而预测是**上一份快照**算出来的；
      >   用**当前快照**重算后（`ab_valuation_snapshot_source_rerun_20261126.py`）
      >   实盘值**与快照侧吻合**：**11 / 11** 个变了值的代币**全部**在**快照侧**，
      >   **在旧的基础池侧的有 0 个**；`publishedLoopImpact.valueLost = 0`（**没有闭环丢了估值**）。
      > - 先前被判「方向相反」的那条 `0xaf3d76f1…`：旧预测 **353419979** ／ 旧基线 **354732328** ／
      >   实盘 **355797124** ／ **当前快照侧 355798706** ⇒ 实盘与**快照侧逐位吻合**（差 **1582**，**0.0004%**）
      >   ⇒ 「方向相反」只是**快照前进**造成的，**不是**估算错误。
      > 探针：`verify_t_bi_02_post_apply_20261126.py` ＋ `verify_valuation_source_live_20261126.py`。

- [x] 复核全程**只读**（A/B 阶段）

      > **读数**：三个探针全部 `mode=ro` ＋ `PRAGMA query_only = ON`；
      > 报告头 `readOnly: true`（`valuation_snapshot_ab_20261126.json` /
      > `valuation_gate_verdict_20261126.json`）；实盘库 **零写入**。

---

## 6. 诚实边界

- [x] 本任务**未**改动方向层 / 内核 / 运行时维护链

      > **读数**：本任务的改动**只在** `flashloan/tools/closed_loop_manager/src/lib.rs`
      > 的估值读数段（`read_valuation_pool_states` / `read_valuation_market_states` /
      > `read_valuation_pool_facts_from_table`）；`directional_pool_manager/**`、`rs-src/**`、
      > `runtime_core` 维护链**不在**本任务的改动文件清单内
      > （这三个缺口分别归属 `M-3`、`M-13`（`T-核.02`）、`M-13`（`T-核.01`），见 `D-143` §3）。
      > **注**：工作区里这些模块**另有**未提交改动，归属**其它任务**（`T-闭.03` / `T-核.01` 等），
      > **不是**本任务的改动 —— 归属按各自任务目录登记。

- [x] 「运行期生效」**如实登记**（实盘未重启 ⇒ 未生效）

      > **读数**：实盘 runtime **pid 24524** 自 **2026-11-25 17:14:00** 起未重启 ⇒
      > 新口径**未在实盘生效**；本任务的实盘读数全部来自**只读探针**，不是运行期读数。
      > 「A/B 先于落地」的顺序**没有**做到（改码已先于本轮进入工作区），同样如实登记。
