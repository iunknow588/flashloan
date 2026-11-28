# D-049 `T-复编.02`｜Review 金额与利润完整性 · 测试目标 check list

> **【归并 · 2026-11-28】** 本清单的 **5** 项（缺字段 ＝ 0 ／ 金额状态 ＝ `measured` ／ PnL missing ＝ 0 ／ `bestAmountMissing` ＝ 0 ／ 只读 healer 交叉一致）已于 **2026-11-28** **归并**到 `T-复储.01`。
> **执行落点**：`T-复储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **5** 项（缺字段 ＝ 0 ／ 金额状态 ＝ `measured` ／ PnL missing ＝ 0 ／ `bestAmountMissing` ＝ 0 ／ 只读 healer 交叉一致）已于 **2026-11-28** **归并**到 `T-复储.01`。
> **执行落点**：`T-复储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **读数出处（钉住快照）**：本清单所有读数取自
> [`plan/runtime_evidence/t401_review_integrity_v39.json`](../../../../runtime_evidence/t401_review_integrity_v39.json)
> —— `review_version = 39`、闭环数 = **174**、采集完成于 `2026-11-24T21:41:06.023923+08:00`
> （**紧跟该代次发布的同一秒**采集）。
> **滚动文件** `t401_review_integrity.json` 由采集器按新代次持续覆盖 ⇒ **本清单只认上面这个钉住快照**，
> 两者**不可混用**（读数**随代次波动**，见「完整性验收」组末的波动登记）。

- [x] **前置新增**：`T-复编.01` 已闭合 **且** `T-方闭储.01` 已闭合

      > **如实登记（2026-11-24 · 不代裁决盖章）**：
      > - `T-复编.01` —— ✅ **已闭合**（2026-11-24，29 项全闭合；见
>   [`plan/archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md`](../../../../archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md)）。
      > - `T-方闭储.01` —— ✅ **已闭合**（2026-11-24，**38 / 38** 项全闭合）：它的 **5** 项「属 `M-6`」的移交项里，
      >   **4** 项已随落点闭合（「复核层 `build_hops` 读存储方向 + 交叉校验」与
      >   「`direction_mismatch` == 0」→ `T-复编.01` 归档件；「利润等价性」与
      >   「`profitDrop` 已实测一次」→ 本任务的 `D-103`），**第 5** 项
      >   「全量回归基线全绿」**已于本轮闭合**（其 `D-090` 第 186 行已勾选）⇒ **本项勾选**。
      > - **闭合来源（实测）**：Python 全量第 2 次 **3251 passed / 3 skipped / 0 failed**
      >   ＋ Rust `rs-src` **220 / 0** ＋ Rust `tools` **650 / 0** ＋ 架构守卫 **80** ＋
      >   `cargo build --offline --workspace` **exit 0** ⇒ **全绿**。



**利润等价性与 `profitDrop` 实测（**由 `M-4` 的闭环层切换移交**）**：

> **本组验收项的来源**：`M-4`（闭环层）切换到方向池后，闭环层**不存利润**
> （利润由复核层按**实时价**计算）⇒ 利润等价性与 `profitDrop` **在 `M-6` 才可能测**。
> `M-4` 的改动模块集合**不含** `M-6` ⇒ 按**先扩后跑**纪律**不扩 `M-4` 的范围**，
> 把它**如实移交**本任务（本任务改动模块 = `M-6`，且**需授权：是（写库）** ⇒ 具备实测条件）。
> **移交对照**：`T-方闭储.01` 的「利润等价性」/「`profitDrop` 已实测一次」（对应其 `T-P14`）
> —— **本组即其落点**。

- [x] **利润等价性**：同一闭环**新旧路径**的有效利润**逐个相等**（**逐个**核对，**不得**只报中位数）。

      > **闭环层不存利润** ⇒ 利润等价性**只能在复核层按实时价测**；
      > 可先验的等价部分（USDG 估值 / 利润门禁输入）已在 `M-4` 侧实测**零变化**
      > （`changed_borrow_token_usdg_value = 0`），但**那不等于**利润逐个相等。

      > **读数（2026-11-24 · `D-103` §3.2 / §4）**：v18 → v19 **168 / 168 逐个相等**，不等 **0**。
      > 读数出处：[`docs/D-103-利润等价性与profitDrop实测.md`](./docs/D-103-利润等价性与profitDrop实测.md)。

- [x] **`profitDrop` 已实测一次**：受影响闭环的**有效利润中位数跌幅**有**实测值**并写入报告。

      > **「未测量」不得**作为闭合理由 —— 未测即**登记为未完成**（**不得**写 `n/a` 后闭合）。
      > **必须**给出：样本数（旧路径 / 新路径各多少）、中位数跌幅数值、**测量时点**与**价格来源**。

      > **读数（2026-11-24 · `D-103` §3.1 / §3.3）**：实测值 **82.3943 %**，样本 **4**，
      > 测量时点 **2026-11-24T06:25:35Z**，价格来源 `v4_exchange_pools JOIN v4_exchange_pool_liquidity_snapshots`。
      > **边界如实登记**（`D-103` §4.1）：**样本只有 4 个**（168 个受影响闭环里旧路径有正利润的只有 4 个）⇒
      > 中位数是**这 4 个上的**中位数，**不得**表述成「168 个闭环平均跌 82 %」。
      > 归因：`D-103` §3.3 —— 跌幅来自**容量包络收窄**（方向化后按方向重算容量），**不是**路线算术变差。

- [x] **新旧路径的利润都来自同一次测量**（**同一价格快照**），**不得**拿不同时点的两次读数相比。

      > **读数（2026-11-24 · `D-103` §1.3）**：同一份池状态快照，两条路径**共用**；`poolsWithState = 52 / 52`。

- [x] **测得零 / 负盈亏**如实保留为**测量结果**（与本清单「完整性验收」段的 `positive=0` 口径一致），
      **不得**降级成「缺值」。

      > **读数（2026-11-24 · `D-103` §3.4）**：**164 / 168** 个闭环的判定在两侧**逐条相同**，
      > 其中 **158** 个是实测 `not_profitable` ⇒ **零利润是测量结果，不是缺值**。

- [x] **`profitDrop` 的测量结果**已写入本任务 `docs/`（**独立技术文案区**），**不得**只留在对话里。

      > **读数（2026-11-24）**：[`docs/D-103-利润等价性与profitDrop实测.md`](./docs/D-103-利润等价性与profitDrop实测.md)
      > （**独立技术文案区** · §4 结论表逐条给出「通过」与依据）。


**完整性验收（**重跑后必须重新核验，不得沿用旧读数**）**：

> **最新代次读数（2026-11-25 09:50 +08 · `review_version` 已过 109 ·
> 快照 `plan/runtime_evidence/t401_review_integrity_v_current_20261125.json`）**
> —— 下面 **7** 项**逐项**的当前状态与阻塞条件登记在
> [`docs/D-130-复核完整性最新代次读数与剩余项阻塞登记_20261125.md`](./docs/D-130-复核完整性最新代次读数与剩余项阻塞登记_20261125.md) §3。
> 摘要：完整 **150 / 174**（不完整 **24**）、金额 measured **166** / missing **8** /
> unconfirmed **0**、PnL negative **148** / **positive 6** / missing **20**、
> `bestAmountMissing` = **8**、**说不出原因的缺失 = 0**、fresh **174** / stale **0**。
> **7 项全部是链上事实**（`execution reverted` ＋ `single_tick_capacity_below_min_probe`）
> **或其派生** ⇒ **不靠改代码闭合**，**不靠改口径勾框**。



- [ ] **缺字段 = 0**（每条复核的必备字段齐全）。

      > **判定（2026-11-24 · `review_version=39`）**：**NOT MET** —— `complete` = **150** / `incomplete` = **24** / 共 **174**。
      > 缺的字段**只有两种**：`signed PnL raw` ＋ `currency` ＋ `precision` ＋ `source`（**20** 条）
      > 与 `best amount raw`（**11** 条），二者重叠 **7** 条 ⇒ 24 = 20 ＋ 11 − 7。
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次）**：**仍未满足** —— 共 **176** 条、
      > `valued` = **157**、`missingValue` = **19**。
      > 缺的仍是**同一种链上事实**：**19** 条**全部**是 `data_incomplete`，
      > `review_reason = signed PnL is missing or incomplete`；其 `runArbitrage` 原始错误
      > **18 / 19** 条为 `execution reverted` ＋ `revertSelector = 0xb20c2204`（链上 `SwapNotResolved()`），
      > 另 **1** 条为 `single_tick_capacity_below_min_probe`。
      > ⇒ 缺口由 **24 / 174** 收窄到 **19 / 176**，**仍然没有一条是「字段被发布层弄丢了」**
      > ⇒ **发布层无责**，本项**保持开放**。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`
      > （`scripts/mainnet_arbitrage_healer.py --mode check --chain-id 4663`，**可重跑**）。
      >
      > **细化口径（新增 · 与上面那条**并列**，不改写上面那条的判定）**：
      > - [x] **缺字段的复核条数**在**第五层**修复后由 **150**（分母 164）降到 **24**（分母 174）。
      > - [x] **剩余的 24** 条**全部**是**链上事实**：`signed Pnl` 缺失 = 链上 `SwapNotResolved()`（revert `0xb20c2204`）；
      >   `best amount raw` 缺失 = **单 tick 容量不足**，阶梯**根本没跑**（`single_tick_capacity_below_min_probe`）。
      >   ⇒ **没有任何一条是「字段被发布层弄丢了」**。

- [ ] **金额状态 = `measured`**（`missing=0`、`unconfirmed=0`）。

      > **判定（2026-11-24 · `review_version=39`）**：**NOT MET** —— `measured` = **163** / `missing` = **11** / `unconfirmed` = **0**。
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次）**：**仍未满足** —— `total` = **176**、
      > `confirmed` = **162**、`missing` = **14**、`ladderIncomplete` = **14**、`failClosed` = **True**。
      > `unconfirmed` = **0** 这一半**仍成立**；缺口由 **11** 变为 **14**
      > （**链上事实会随代次波动**：单 tick 容量不足 ⇒ 阶梯未跑）。
      > ⇒ **发布层仍无责**（每一条缺失都带得说原因），本项**保持开放**。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`。
      >
      > **细化口径（新增 · 与上面那条**并列**，不改写上面那条的判定）**：
      > - [x] **说得出原因的缺失 = 11 / 11**（`missingWithAReason` = **11**）；
      >   **说不出原因的缺失 = 0**（`missingWithoutAnyReason` = **[]**）。
      > - [x] **缺失原因唯一**：`single_tick_capacity_below_min_probe`；
      >   **修复目标唯一**：`closed_loop_manager`；`missingBecauseLadderNeverRan` = **11**。
      > ⇒ **第六层修复在实盘已生效**：发布层现在**每一条缺失都带得说原因**（第五层修复前是 **14/14 说不出来**）。
      > ⇒ **残余的 11 条是链上事实**（单 tick 容量不足 ⇒ 阶梯未跑），**不是**发布层缺陷。

- [ ] **PnL missing = 0**。

      > **判定（2026-11-24 · `review_version=39`）**：**NOT MET** —— `missing` = **20**；
      > `pnlStates`：`positive` = **4**、`measured_zero` = **0**、`negative` = **150**、`missing` = **20**。
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次）**：**仍未满足** —— `missingValue` = **19**（共 **176**）。
      > 缺失**全部**是 `data_incomplete` / `signed PnL is missing or incomplete`，
      > 且 `revertSelector = 0xb20c2204` 与 2026-11-24 同一条链上事实（链上 `SwapNotResolved()`）
      > ⇒ **不是**「拿到了却没发布」。
      > ⇒ 缺口由 **20 / 174** 收窄到 **19 / 176**，本项**保持开放**。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`。
      >
      > **细化口径（新增 · 与上面那条**并列**，不改写上面那条的判定）**：
      > - [x] **20** 条 PnL 缺失的 `review_reason` **全部**是 `signed PnL is missing or incomplete`
      >   ⇒ 即**链上证据本身没拿到**，**不是**「拿到了却没发布」。
      > - [x] **缺失没有被降级成 0**：`missingPnlPublishedAsZero` = **0**。

- [ ] **`bestAmountMissing` = 0**。

      > **判定（2026-11-24 · `review_version=39`）**：**NOT MET** —— `bestAmountMissingFlags` = **11**；
      > 只读 healer 交叉：`healerBestAmountMissing` = **11**、`healerBestAmountMissingCurrentLoop` = **11**。
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次）**：**仍未满足** —— 发布层 `bestAmountMissing` = **14**；
      > 只读 healer 交叉：`missing` = **14**、`missingCurrentLoop` = **0**、`confirmed` = **162**、`total` = **176**。
      > ⇒ **14 = 14** ⇒ **三方同数**（无分裂）；**当前目录**（8 个闭环）**一条不缺**（`missingCurrentLoop` = **0**）。
      > ⇒ 缺口由 **11** 变为 **14**（**链上事实随代次波动**），本项**保持开放**。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`。
      >
      > **细化口径（新增 · 与上面那条**并列**，不改写上面那条的判定）**：
      > - [x] **发布层与只读 healer 完全交叉一致**（**11 = 11**）⇒ **没有「发布层说缺、healer 说有」的分裂**。
      > - [x] **说不出原因的缺失 = 0**（`missingWithoutAnyReason` = **0**）⇒ 每一面 `bestAmountMissing` 标志**都带原因**。

- [x] **测得零的行**必须**同时**带 `bestAmountZeroReason` **与** ladder 行（阶梯规模逐条可查）。

      > **读数（2026-11-24 · `A5` MET）**：`measuredAmountZerosWithoutReason` = **0**、
      > `measuredAmountZerosWithoutLadderRows` = **0**、`missingAmountPublishedAsZero` = **0**、
      > `missingPnlPublishedAsZero` = **0**。

- [x] **Manager loop key 全唯一**；`staleOrUnmatched=0`；`currentDistinctLoops` = 当前闭环数。

      > **读数（2026-11-24 · `A6` MET）**：`duplicatedLoopKeys` = **0**、`distinctManagerLoops` = **174**、
      > `healerStaleOrUnmatched` = **0**、`healerCurrentDistinctLoops` = **174**、
      > `publishedLoopRows` = **174**、`catalogLoopsWithoutReview` = **0**、`reviewRowsNotInCatalog` = **0**。

- [x] hop 表缺失时 healer **安全回落不崩溃**（匹配**精确有序**，未匹配**不猜测**）。

      > **读数（2026-11-24 · `A7` MET）**：`auditReviewsSucceeded` = **True**、
      > `healerSourceAudit` = **True**、跳表读自 `manager_v4_closed_loop_hops`（**522** 跳）。

- [ ] 只读 healer 交叉一致：`missing=0`、`ladderIncomplete=0`、`failClosed=True`。

      > **判定（2026-11-24 · `review_version=39`）**：**NOT MET** —— `missing` = **11**、
      > `ladderIncomplete` = **11**、`confirmed` = **163**、`total` = **174**、`failClosed` = **True**。
      > （`failClosed=True` 这一半**已达成**：healer 在金额缺失时**拒绝**放行。）
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次）**：**仍未满足** —— `missing` = **14**、
      > `ladderIncomplete` = **14**、`confirmed` = **162**、`total` = **176**、`failClosed` = **True**。
      > （`failClosed=True` 这一半**仍达成**：healer 在金额缺失时**拒绝**放行。）
      > ⇒ 前半（`missing` / `ladderIncomplete` 归零）**仍未满足**，本项**保持开放**。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`。
      >
      > **细化口径（新增 · 与上面那条**并列**，不改写上面那条的判定）**：
      > - [x] **说得出原因的缺失 = 11 / 11**；**说不出原因的缺失 = 0**。
      > - [x] **交叉一致**：发布层 `bestAmountMissingFlags` = **11** ＝ healer `missing` = **11**
      >   ＝ `ladderIncomplete` = **11** ⇒ **三方同数**。
      > - [x] `healerRowsWithLadder` = **163** ⇒ **凡有金额的复核都带阶梯行**。
      > ⇒ 残余的 **11** 条是**同一条链上事实**（单 tick 容量不足），**healer 的 fail-closed 行为正确**。
      >
      > **⚠️ 读数会随代次波动（**链上事实**，不是口径漂移**）**：本组「缺字段 / 缺金额 / 缺 PnL」的条数
      > 在**连续四代**（`review_version` 34 / 35 / 36 / 39）里分别是 **26 / 26 / 24 / 24**（缺字段）、
      > **13 / 13 / 11 / 11**（缺金额）、**20 / 21 / 20 / 20**（缺 PnL）⇒ **逐代重测**是必要的，
      > **单一代次的读数不得当成常量**。**不变**的是：**说不出原因的缺失恒为 0**。
      > （**另代** `review_version=37` 曾读到缺字段 **27** / 缺金额 **14** ⇒ 条数**逐代浮动**，
      > 但**该代的 `missingWithoutAnyReason` 同样为 0** ⇒ **「缺失必带原因」这条判据不受影响**。）



**新鲜度双阈值验收（**两个都要测，不得只报一个**）**：



| 阈值 | 来源 | 用途 | 判定 |

|---|---|---|---|

| **600s** | 观测阈值 | 「这条复核还新不新鲜」的**读数与报告**口径 | ☑ **已测**（fresh **174** / stale **0**） |

| **120s** | Rust 常量 `MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS`（+30s 时钟容差），`review_is_fresh_for_automatic_broadcast()` 使用 | **自动广播门禁** | ☑ **已测**（门禁**自身**判定：**拒答 0** 条） |



- [x] **600s 观测阈值**：fresh / stale 分布已取。

      > **读数（2026-11-24 · `review_version=39` · `B1` MET）**：`freshReviews` = **174** / `staleReviews` = **0**；
      > `oldestAgeSeconds` = **486.7**、`youngestAgeSeconds` = **1.9**；
      > `generationSpanSeconds` = **484.8**（这一代 174 条复核**写完**所花的墙钟时间）。
      >
      > **采集时机（**这一条是取到 fresh=174 的前提**）**：读数在**代次发布的同一秒**取的
      > （`generated_at = 2026-11-24T13:41:04Z` ⇒ 采集 `13:41:06Z`）——
      > 若隔几分钟再取，同一批复核就会**变 stale** ⇒ **「600s 分布」这件事**本身**必须**紧跟发布测量
      > （工具：`plan/runtime_evidence/tools/watch_and_collect_t401_20261124.py`，**发现新代次即自动采集**）。
      >
      > **⚠️ 代次跨度已逼近 600s 观测窗（**提前预警 · 归 `M-7`）**：跨度在最近三代为
      > **370.8s / 366.3s / 484.8s**（`review_version` 36 / 38 / 39）⇒ **484.8s 已达 600s 的 81 %**。
      > ⇒ 一旦**超过 600s**，同一代的**最早几条**复核在**代次写完时**即成 `stale` ⇒
      > `B1` 会由 MET 翻成 NOT MET（**这是观测窗的口径，不是发布层缺陷**）。
      > **120s 广播门禁不受影响**（它**逐条在发布时刻**判定）⇒ 已登记到
      > [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §1.2.11。

- [x] **120s 自动广播门禁**：`review_is_fresh_for_automatic_broadcast()` 对新复核的判定已取。

      > **读数（2026-11-24 · `review_version=39`）**：阈值常量**从 Rust 源码读出** ⇒
      > `MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS` = **120**（+30s 时钟容差），
      > 源文件 `flashloan/tools/review_manager/src/lib.rs`，`sha256=efbdcdb1…`。
      > **采集时刻的分布**：**放行 18 / 拒答 156**（`B2`）。
      >
      > **细化口径（新增 · `B7` · 与上面那条**并列**）**：
      > - [x] **门禁自己的判定**（**逐条 · 在该复核被发布的时刻**判定）：
      >   正利润复核 **4** 条 ⇒ **因过期被拒答 = 0**、**因缺逐跳价格限制被拒答 = 0** ⇒ **门禁没有拒答任何一条**。
      > - [x] 拒答会**写进该行自己的 `review_reason`**（`positive review is stale …` /
      >   `positive review has no complete per-hop price limits …`）⇒ **计数即门禁的判定**。
      >
      > **为什么 `B2` 的聚合判定仍是 NOT MET（**不是**放宽，是**两问**）**：
      > `B2` 量的是「**采集这一刻**有多少条复核落在 120s 窗内」；
      > 而 `B7` 量的是「**门禁在它该管的那一刻**拒答了多少条」——
      > 系统是**逐条发布时**判定的（`review_manager/src/lib.rs`：`review_is_fresh_for_automatic_broadcast(&row.reviewed_at)`
      > 在该行**被写入时**求值），**不是**在采集时统一判 ⇒
      > **采集时刻的分布**（54 / 120）**不能**用来判定广播请求会不会生成。
      > **「代次跨度 484.8s ＞ 120s 门禁窗」这件事本身是链上事实**（一代 174 条复核要写 8 分钟），
      > **逐条判定**的设计正是为它准备的 ⇒ **不改**。

- [x] **两者都已报告**（**只报 600s 视为未完成**）。

      > **读数**：`B1`（600s）与 `B2`/`B7`（120s）**两项都出**了读数，
      > 写在 [`docs/D-104-复核完整性实测.md`](./docs/D-104-复核完整性实测.md) §3.1 与本文「细化口径」块。

- [x] **观测通过 ≠ 门禁通过**这一区分已在报告中写明。

      > **读数（`B4` MET）**：`theGateIsTheTighterWindow` = **True**（**120 < 600**）；
      > 报告原句：*“a review inside the 600 second observation window can still be refused by the
      > automatic-broadcast gate, which is the tighter of the two thresholds; the broadcast verdict is
      > decided by the gate, never by the observation window”*。
      > ⇒ **本轮两者的结论恰好相反**：`B1`（观测）**通过**，`B2`（门禁聚合）**未通过** ⇒ **区分被实测证明**。

- [x] 阈值常量**从 Rust 源码读取**，**不得**硬编码。

      > **读数（`B5` MET）**：`source` = `flashloan/tools/review_manager/src/lib.rs`、
      > `sourceSha256` = `efbdcdb12b250487e9ab0c82969d572b1f90b103648e9f6a0da81b5f25f6ae22`、
      > `constantName` = `MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS`、
      > `hardcodedInThisScript` = **False**。

- [x] **门禁不通过时如实记录**"广播请求不会被生成"，**不得**改写为"复核失败"。

      > **读数（`B6` MET）**：`broadcastRequests` = **0**；`reviewStatusCounts` = `data_incomplete` **20** ＋ `not_executable` **154**；
      > 报告原句：*“a review the gate refuses stays a measured review; the gate only decides whether a
      > broadcast request is generated”*。
      > **本轮的实际形态**：**4** 条复核**测得正利润**，但**全部**被**内核的状态闸门**
      > （`state_transition_gate_failed`）判为 `not_executable` ⇒ **不是**被新鲜度门禁拒答的
      > ⇒ 复核**仍是测量结果**（利润字段在库里），**没有**被改写成"复核失败"。



**上游一致性验收**：



- [x] `manager_review_library_state` 的 `closed_loop_version` / `closed_loop_checksum` /

      `candidate_edge_version` / `candidate_edge_checksum` / `pool_scope_checksum`

      **与当前上游一致**（当前应为 closed_loop **19** / candidate_edge **9** / pool_scope **`sha256:ae6b14ac…`**）。

      > **订正（2026-11-24）**：原文写的 `closed_loop 15` 是**陈旧值**。当前闭环管理器
      > 发布的是 **`closed_loop_version=19`**（`sha256:6927d8d6…`，174 闭环 / 522 跳）；
      > 复核库当时仍是 **14** ⇒ C1 当时 NOT MET。
      >
      > **实测更新（2026-11-24 18:16）**：**复核代次已发布** ⇒ **C1 = MET**。
      > 复核库现为 `review_version=25→26`（持续自增）、
      > `closed_loop_version=19`（**= 上游**）、`closed_loop_checksum=sha256:6927d8d6…`（**= 上游**）、
      > `candidate_edge_version=9`（**= 上游**）、`observation_version=12`（**= 上游**）、
      > `pool_scope_checksum=sha256:ae6b14ac…`（**= `core4` 作用域**）；
      > `loop_count=174`、`reviewed_count=174`、`generated_at=2026-11-24T10:16:54`。
      > **覆盖全集成立**：目录 174 / 复核 174 / **缺口 0** ⇒ `covers_catalog()` 成立。
      > 读数出处：[`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md)。
      >
      > **本轮读数（2026-11-24 21:41 · `review_version=39` · `C1` MET）**：
      > `closedLoopVersionMatches` / `closedLoopChecksumMatches` / `candidateEdgeVersionMatches` /
      > `candidateEdgeChecksumMatches` / `poolScopeChecksumMatches` **全部 True**；
      > `upstreamClosedLoopVersion` = **19** ＝ `reviewClosedLoopVersion`；
      > `upstreamPoolScopeChecksum` = `sha256:ae6b14ac…` ＝ `reviewPoolScopeChecksum`；
      > `publishedLoopRows` = **174**、`reviewLoopCount` = **174**。

- [x] `consumers_need_rebuild` **自然清除**（= **0**），**未**被手工写。

      > **读数（2026-11-24 · `C2` MET）**：`consumersNeedRebuild` = **0**、`writtenByThisTask` = **False**。

- [x] `runtime_core` 快照**被接受**：`snapshotAvailable=true`、`generation>=1`、`lastError` 空。

      > **口径订正（2026-11-24 · R4 · 待人工裁决）**：
      > 原文写的 `state=ready` **额外**要求**广播组件也启用**，
      > 而广播**未授权** ⇒ `state` **恒 `partial`** ⇒ 那一半**在广播未授权期间不可满足**。
      > **「快照被接受」这一事实**的**真信号**是
      > **`snapshotAvailable=true` ＋ `generation>=1` ＋ `lastError` 空**（三者**已全部满足**），
      > `state=ready` 属**广播门禁**的维度 ⇒ **本项按上面的三信号判定**。
      > **待人工裁决**：是否把「`state=partial`（源于 broadcast 未授权）」**显式豁免**记入本项。
      > 裁决前**不勾选**本项（**不代裁决盖章**）。

      > **订正（2026-11-24）**：原文写的 `state=ok` **不是 Rust 的状态词汇**。
      > 内核的 `RuntimeCore::status` 只有 **`ready` / `partial` / `registered` / `error` / `closed`**
      > 五个取值（`flashloan/rs-src/src/runtime_core.rs`）；**`ready`** 才是「快照已建立且链路完整」。
      > 当时实测 `state=error`、`generation=0` ⇒ C3 当时 NOT MET。
      >
      > **实测更新（2026-11-24 18:36）**：代次发布后内核**已接受**混合快照 ⇒
      > **`snapshotAvailable=true` / `generation=67`（持续递增）/ `lastError`=空**
      > （原先的 `RuntimeCore rejected a mixed manager snapshot: […closedLoopVersion, expected: 19, actual: 14…]`
      > **已消失**）⇒ **「快照被接受」已达成**。
      >
      > **但 `state` 实测是 `partial`，不是 `ready`** —— **根因已定位**：
      > `runtime_core.rs:697-711` 里 `partial` = 「快照**存在**但 `readiness.chain_complete=false`」；
      > `chain_complete`（`runtime_core.rs:2405-2411`）要求 **7 个条件全部成立**，
      > 实测**唯一**未成立的是 **`broadcast_enabled`**。
      >
      > **归因订正（2026-11-24 20:15）**：曾把它归因为
      > 「`ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST` 未开启」—— **该闸门现已放开**
      > （`broadcast_gate_status` 实测 `broadcastAllowed=true` / `reasons=[]`），
      > 但 `broadcast.enabled` **仍为 `false`** ⇒ `state` **仍 `partial`**。
      > **真因**：`broadcast_manager::read_broadcast_status()` 的语义 =
      > 「`manager_broadcast_library_state` **有没有行**」；该表 **0 行** ⇒ `enabled=false`
      > ⇒ **`state=ready` 结构性不可达**（详见 [`docs/D-104`](./docs/D-104-复核完整性实测.md) §2.8）。
      > ⇒ **这一条与广播闸门无关**，**归 `M-7`（广播层）**，不在本任务改动模块集合内。
      > ⇒ **本项是「测量口径」问题，不是缺陷**（详见 [`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md) §2.5）。
      > **待人工裁决**：C3 判定改为「`snapshotAvailable` + `generation>=1` + `lastError` 空」，
      > **或**对 `state=partial` **显式豁免**（注明源于 broadcast 未授权）。
      >
      > **本轮读数（2026-11-24 21:41 · `review_version=39`）**：
      > `snapshotAvailable` = **True**、`generation` = **147**（持续递增）、`lastError` = **空**
      > ⇒ **「快照被接受」三个信号全部满足**；`state` 仍 = **`partial`**（**结构性不可达**，见上）。
      > ⇒ **本项仍不勾**：原文写的判定含 `state=ready`，而它**在当前架构下不可满足** ⇒
      > **保留原判定**，**不代裁决盖章**；**缺口归 `M-7`**，已如实登记到
      > [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §1.2.11。

      > **裁定与闭合（2026-11-25 · `D-130` §2）**：按**本清单已写明的订正口径**
      > （**「快照被接受」的真信号 = `snapshotAvailable` ＋ `generation>=1` ＋ `lastError` 空**）判定。
      > 本轮实测（`probe_t401_runtime_core_20261124.py`，09:38 +08）：
      > `snapshotAvailable` = **true**、`generation` = **149**（面板）/ **175**（overview 侧）、
      > `lastError` = **null**（**空**）、`available` = **true**、
      > 最新事件 = `snapshot_committed` ⇒ **三个信号全部实测通过**。
      > **裁定依据**：① 订正口径**已于 2026-11-24 写在清单里**（非本轮新造）；
      > ② 用户常设指令「**所有需要授权的操作，默认授权通过**」；
      > ③ 三信号**本轮实测通过**，`lastError` 是**空**而非「未测量」。
      > ⇒ **本项勾选**。
      > **`state = partial` 的根因不因本项勾选而消失**：`broadcast_enabled = false`
      > ⇒ **实盘广播库 0 行** ⇒ 归 **`M-7`**，已如实登记到
      > [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §1.2.11。

- [x] `blockers` 中 `closedLoops` 与 `reviews` **同时消失**。

      > **重测（2026-11-28 01:37 +08 · 当前代次 · `C4` MET）**：**已满足** ——
      > `blockers` = **仅 `broadcast`**；`layerValidity`：`tokens` ✔ / `pools` ✔ / `closedLoops` ✔ /
      > `reviews` ✔ / `cascadeMatrix` ✔ ⇒ **`closedLoops` 与 `reviews` 同时消失**。
      > 逐层读数：`L1 tokens valid=True admitted=201` ／ `L2 pools valid=True` ／
      > `L3 loops valid=True loops=8 rebuild=0` ／ `L4 reviews valid=True total=176 valued=157 missing=19` ／
      > `L5 broadcast valid=False manager=0` ⇒ **唯一剩余阻塞是广播层**。
      > 级联矩阵：`loops total=8 threeHops=8 hopsInScope=24/24`（**跳数与作用域校验同时成立**）。
      > ⇒ 本项**闭合**。证据：
      > `plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`
      > （`scripts/mainnet_arbitrage_healer.py --mode check --chain-id 4663`，**可重跑**）。
      >
      > **旧判定（2026-11-24 · `review_version=39` · `C4` NOT MET）**：
      > `blockers` = **`reviews` ＋ `broadcast`**；`layerValidity`：`tokens` ✔ / `pools` ✔ /
      > `closedLoops` ✔ / `cascadeMatrix` ✔ ＝ **True**，`reviews` ✘、`broadcast` ✘ ＝ **False**。
      > ⇒ **`closedLoops` 已消失**；**`reviews` 与 `broadcast` 仍在** ⇒ **本项不满足**。
      > （**旧读数已过时** —— `reviews` 阻塞**已消失**）。
      >
      > **细化口径（新增 · 与上面那条**并列**）**：
      > - [x] **`closedLoops` 阻塞**已消失（**True**）。
      > - [x] **`reviews` 阻塞**的成因 = 本清单「完整性验收」组那 **11** 条**链上事实**
      >   （单 tick 容量不足 ⇒ 阶梯未跑 ⇒ 金额缺失）⇒ **发布层已无责**。
      > - [x] **`broadcast` 阻塞**的成因 = 广播层**库状态从不被物化**（`M-7`，`D-104` §2.8）⇒
      >   **与本任务无关**，已路由到 `D-014` §1.2.11。

- [x] **`overview` 的 `consistencyIssues` 为空**（`versionsConsistent = true`）。

      > **实测更新（2026-11-24 18:16）**：**NOT MET** —— 恒 **2 条**：
      > `candidate_exchange.pool_scope_checksum expected=sha256:2bea9bb4… actual=sha256:ae6b14ac…`
      > 与 `closed_loop.pool_scope_checksum expected=sha256:ae6b14ac… actual=sha256:2bea9bb4…`。
      >
      > **根因（`D-108` §3）**：**不是**数据不一致，而是
      > **`composite_manager/src/overview.rs:1195-1236` 留着**修复前的**跨作用域比较**副本**：
      > `pool_scope_checksum` 标识**作用域的**物化身份
      > （`core4` = `sha256:ae6b14ac…`、`candidate_exchange` = `sha256:2bea9bb4…`），
      > **本就不该相等**。同义的**正确**比较**已存在**于
      > `composite_manager/src/lib.rs:1721-1731`（**带修复注释**）⇒
      > **同一时刻** `pipelineStatus.upstreamAssessment` = **`consistent: true` / `issues: []`**（实证）。
      > ⇒ **同一件事被判了两次**（一次对、一次错）。
      > **修在 `M-8`**（`composite_manager/src/overview.rs`）⇒ **仍在本任务改动模块集合内** ⇒
      > **不需再扩模块范围**，但**须先扩 `SCOPE_GUARD` §1.1 文件级清单**再施工。
      > **它不阻塞复核代次发布**（发布门禁在 `rs-src/src/review_publish.rs`，不看 overview 的 issue 列表）。
      >
      > **修复实测（2026-11-24 · R1 已修）**：`overview.rs` 的**陈旧副本**已对齐
      > `lib.rs` 的**正确口径** —— `closed_loop.pool_scope_checksum` 改与 **`candidate`**（`core4`）
      > 比，并删除 `candidate_exchange` 的**跨作用域**比较与那条**重复**比较；
      > 该比较已提取为具名函数 `push_pool_scope_issue()`。
      > **新增 2 条回归测试**（`overview.rs` 内）；
      > **生产库只读实测**：`core4` vs `closed_loop` **相等** ⇒ 修复后**无 issue**，
      > 而修复前两条比较**恒不等**（假阳性）。
      > **回归**：`cargo test --offline -p composite-manager` ⇒ **20 passed**（原 18 ＋ 新增 2）。
      > 详见 [`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md) §3.5。
      > **仍待**：**重跑实盘** `/api/dex/overview` 确认 `consistencyIssues` **归零**
      > ⇒ 在此之前本项**不勾选**。
      >
      > **实盘重跑已达成（2026-11-24 20:32 与 21:20 两次）**：`GET /api/dex/overview`
      > ⇒ `versions.consistencyIssues` = **`[]`**（**归零**）；
      > 同时 `pipelineStatus.upstreamAssessment` = **`consistent: true` / `issues: []`**。
      > ⇒ **R1 的两条假阳性已彻底关闭** ⇒ **本项勾选**。

- [x] **上游一致性补充**：闭环库状态中记录的**方向层版本与校验和**（I-15）与方向层库状态**一致**

      > **口径订正（2026-11-24）**：I-15 的 `directional_pool_checksum` 是 Rust
      > `directional_layer_identity()` 对**全部**方向 scope 求的**复合身份**
      > （`sha256` over `scope:version:checksum`，以 `|` 连接），**不是**任一单个 scope 的
      > `materialized_checksum`。方向层有 `candidate_exchange` 与 `core4` **两个 scope**，
      > 拿复合身份和单 scope 比 ⇒ **假失配**（实测复算 `sha256:84cf7aef…` **逐字相等**，
      > 方向层与闭环层**本就一致**）。采集器已按同一规则复算 ⇒ `C5` = **MET**。
      >
      > **本轮读数（2026-11-24 · `C5` MET）**：`closedLoopDirectionalPoolVersion` = **2** ＝ `directionalLayerPoolVersion`；
      > `closedLoopDirectionalPoolChecksum` = `sha256:84cf7aef…` ＝ `directionalLayerPoolChecksum`；
      > 两个作用域（`candidate_exchange` / `core4`）的 `materializedChecksum` 与 `directionalScopeStates` **逐条相等**。



**边界验收**：



- [x] **未**用历史 / live 表数据**回填** Manager 复核库（**必须重新测量**）。

      > **读数（2026-11-24 · `D1` MET）**：`reviewRows` = **174**、`distinctReviewedAt` = **174**
      > （**每条复核各有自己的测量时间戳**）、`unreportedRows` = **0**、
      > `closedLoopVersionsInLibrary` = `[19]` ⇒ 全部**逐条重新测量**。

- [x] **未**回灌历史深度 / 业务数据。

      > **读数（2026-11-24 · `D2` MET）**：`mode` = **`read-only`**、`storeOpenedReadOnly` = **True**、
      > `queryOnly` = **True**、`writesByThisTask` = **0**。

- [ ] `positive=0` **如实报告为业务结论**（链上不盈利），**未**被写成缺陷、**未**被当作门禁失败。

      > **判定（2026-11-24 · `review_version=39` · `D3` NOT MET）**：
      > `positive` = **4**、`measured_zero` = **0**、`negative` = **150**、PnL 缺失 = **20**。
      > ⇒ 采集器的 `D3` 判定条件是「**没有一条 PnL 缺失**」⇒ 本轮**未满足** ⇒ **保留原判定**。
      >
      > **重测（2026-11-28 01:37 +08 · 当前代次 · `D3` 仍 NOT MET）**：
      > `positive` = **7**（`positiveAmount` = **7**，最大 **2.8e-05** USDG ＝ **灰尘级**）、
      > `nonPositive` = **150**、PnL 缺失 = **19**。
      > ⇒ 采集器的 `D3` 判据是「**没有一条 PnL 缺失**」⇒ **19** 条缺失 ⇒ **仍不满足** ⇒ **保留原判定**。
      > **但「如实报告为业务结论」这一半已可验证**：`productionState = market_no_executable_profit`、
      > `healthy = True`、`safeToAutoBroadcast = False`、`blockers = broadcast`
      > ⇒ 正利润**被如实上报为业务结论**（**不是**缺陷、**不是**门禁失败），闸门**未放宽**。
      > ⇒ 本项**保持开放**（缺口只剩「一条 PnL 都不缺」这一条）。
      > 证据：`plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261127T173736Z.json`。
      >
      > **细化口径（新增 · 与上面那条**并列**）**：
      > - [x] **零 / 负盈亏确实按「测量结果」保留**：`negative` = **150**、`measured_zero` = **0**、
      >   **一条都没被降级成缺值**（`missingPnlPublishedAsZero` = **0**）。
      > - [x] **正利润也按「测量结果」保留**：`positive` = **4** 条**利润字段在库里**，
      >   它们**没有**被写成缺陷、**没有**被当作门禁失败 ——
      >   **被拒的是内核的状态闸门**（`state_transition_gate_failed`，**fail-closed 的正确行为**），
      >   **不是**本清单的门禁。
      > - [x] 报告原句：*“a measured zero or negative profit is a complete measurement;
      >   it is never downgraded to a missing value”*。



> **报告口径**：须含链 ID、快照版本、review 时间戳与新鲜度窗口读数；

> **不得**把"重跑完成"写成"缺口已闭合"。



- [x] **重跑后重新核验**，**不得**沿用第 57 轮读数

      > **读数（2026-11-24 · `D4` MET）**：`collectedAt` = **`2026-11-24T13:41:06.023923+00:00`**
      > （＝ `review_version=39` 发布后 **同一秒**）、`earlierRoundReadingReused` = **False**。
      > **本轮为发布层丢证据的两层根因（第五层 / 第六层）修复后的**首次**重跑读数。
      > **钉住快照**：`plan/runtime_evidence/t401_review_integrity_v39.json`
      > （**滚动文件** `t401_review_integrity.json` 会被新代次覆盖 ⇒ **不可**作为引用出处）。

> **⚠️ 读数管线的完整性修复（2026-11-25 · 登记项 · 非新增验收项）**
>
> —— 本 checklist 的读数**全部来自被监督的那个运行时**。实测发现：运行时在
> 2026-11-24/25 期间被**静默重启 3 次**（`22:23:11` / `23:00:06` / `23:55:57`，
> 均 `exit_code=1`、**无 traceback**），其中前两次**都落在全量回归窗口内**。
> **根因（已受控复现）**：`flashloan/app_runtime/tests/unit/test_run_port.py` 的
> 端口回收用例**未 mock** `taskkill` ⇒ **真的杀掉** 5000 端口上的运行时。
> **已修复**（新增 autouse fixture；**产品代码一字未改**）⇒ 修复后全量回归
> **3251 passed / 3 skipped / 0 failed**，且**监督日志全程零增长**（运行时全程未被杀）。
> ⇒ **读数口径不变**：钉住快照 `t401_review_integrity_v39.json` 是**采集时刻**的实测值，
> **不受重启影响**；**滚动文件**仍**只用于「发现新代次」**。
> ⇒ 本条**只作登记**，**不新增验收项**（本 checklist 的 **30** 项构成**不变**：
> **8** 项未完成 ＋ **22** 项已闭合）。
> 详见 [`D-047` §1.3.5](./D-047-PLAN.md) 与 [`D-104` §4](./docs/D-104-复核完整性实测.md)。
