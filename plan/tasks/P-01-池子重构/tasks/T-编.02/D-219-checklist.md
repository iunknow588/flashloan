# D-219 `T-编.02`｜验收清单（**checklist**）

> 验收编号 `AC-编.02`。**每一条都必须带实测读数**。

## A. 收敛口径

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | 缺值只统计**当前目录内**的闭环 | `reviews_missing_a_value` 按目录过滤 | 目录内 **10** 行缺值 **0** | ✅ |
| A2 | 目录外的行**单独计数上报** | `reviews_outside_the_current_catalog` | `outsideCatalogReviews=166`、`outsideCatalogMissingValue=19` | ✅ |
| A3 | 收敛**不再**永久 stalled | `converged` / `stalled` | `converged=true`、`stalled=false`、`remainingMissingValue=0` | ✅ |
| A4 | 目录**读不到**时不缩范围 | `current_catalog_loop_keys` 返回 `None` | 单测 `test_a_catalog_that_cannot_be_read_keeps_every_row_in_scope` | ✅ |
| A5 | 动作选择按**在产**口径 | `healing_actions` 读 `currentLoopStats` | 单测 `test_a_positive_review_of_a_loop_that_left_the_catalog_does_not_open_the_broadcast` | ✅ |
| A6 | 生产态按**在产**口径 | `transaction_production_status` | `bestAmountMissingCurrentLoop` | ✅ |
| A7 | 新鲜度按**在产**复核 | `currentLoopLatestReviewAt` | `audit_reviews` 新增字段 | ✅ |
| A8 | 稳定窗口按**在产**口径 | `stability_window` 读 `currentLoopStats` | 代码级 | ✅ |
| A9 | 复核层转绿 | `layers.reviews.valid` | **true**（`bestAmountMissingCurrentLoop = 0`） | ✅ |

## B. 一轮复核耗时

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| B1 | 不再扫旧表 | `review_rows` 有管理器行即返回 | 旧表扫描 **18.2 s / 2.13 GB** ⇒ **0** | ✅ |
| B2 | 收敛链传 `manager_api` | `converge_reviews` **3** 处调用 | 代码级 | ✅ |
| B3 | 一轮复核耗时下降 | 实测 | **17 min 27 s → 7 min 27 s** | ✅ |

## C. 存活探测

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| C1 | 按异常类型分类 | `api_get` 的 `kind` | `timeout` / `connect` / `http` / `io` | ✅ |
| C2 | **忙 ≠ 死** | 超时 ⇒ `{"ok":true,"stage":"busy"}` | 单测 | ✅ |
| C3 | 死仍是死 | 连不上 ⇒ `dead` | 单测 | ✅ |
| C4 | 忙看门狗 | 超 **1800 s** 才按 wedged 重启 | `RUNTIME_BUSY_RESTART_SECONDS = 1800` | ✅ |
| C5 | 实盘不再误重启 | PID 不再无故跳变 | 修复前 **9704 → 3392 → 19492** | ✅ |

## D. 回归

| # | 套件 | 读数 | 状态 |
|---|---|---|:---:|
| D1 | `scripts/tests/test_mainnet_arbitrage_healer.py` | **108 passed**（首轮基线 **85**，逐轮递增） | ✅ |
| D2 | `flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py` | **44 passed** | ✅ |

## E. 诚实边界（**未闭合项**）

| # | 项 | 说明 | 状态 |
|---|---|---|:---:|
| E1 | **持续产生链上广播交易记录** | `manager_v4_review_broadcast_requests` = **0**、`manager_cow_execution_attempts` = **0** ⇒ **未达成** | ⬜ 市场条件 |
| E2 | 缺口定位 | **不在**广播准入（闸门开、私钥已配），**在上游没有一条正收益复核**（**10/10** 全部 `not_executable` / `NoProfitableRoute()`） | ✅ 已定位 |
| E3 | 复核阶梯覆盖 | 每条闭环探测 **7** 个额度（**0.001 – 0.053 ETH**），**最小额度**下价格冲击可忽略 ⇒ 亏损来自**价差本身 + 成本**，不是探测量不够 | ✅ 已定位 |

## F. **首轮实盘周期暴露的三处口径缺陷**（2026-11-26T21:03:57Z · `supervisor_20261126T205604Z.jsonl`）

> 证据文件：`plan/runtime_evidence/diagnostics/mainnet_self_healing_orchestrator/supervisor_20261126T205604Z.jsonl`
> 的 `healing_cycle` 事件（`timestamp = 2026-11-26T21:03:57Z`）。

| # | 缺陷 | 实测读数（修复前） | 修法 | 状态 |
|---|---|---|---|:---:|
| F1 | **复核新鲜度读不到**：Manager 只发布 `reviewedAt`，**不发布** `createdAt`；审计读 `created_at` ⇒ `latestReviewAt` 恒为 `null` ⇒ 生产态恒为 `review_stalled` | `GET /v1/reviews?chainId=4663` ⇒ `filteredCount = 176`、每行 `reviewedAt`（例 `2026-11-26T21:12:36.489859800+00:00`）、**无** `createdAt`／`updatedAt`；`transactionProduction.latestReviewAt = null`、`state = review_stalled`、`reason = no_fresh_review_within_threshold` | 新增 `review_measurement_stamp()` ＋ `newest_review_stamp()`：按 `reviewed_at` → `created_at` → `updated_at` 取**发布**的时刻，并取**最大值**（不依赖行序） | ✅ |
| F2 | **层级判定与审计互相矛盾**：级联层读**整窗口**缺值，审计读**当前目录** | 同一次审计：`reviews.valid = true`、`bestAmountMissingCurrentLoop = 0`、`currentLoopStats.missingValue = 0`；而 `cascadeBefore.layers.reviews = false`、`missingReviewValue = 19` | `cascade_layer_status()` 改读 `currentLoopStats`（与 `audit_reviews` / `transaction_production_status` / `stability_window` 同一口径） | ✅ |
| F3 | **稳定窗口的 `productionHealthy` 永远为假**：拿**状态串**和 `"healthy"` 比较，而生产态的状态串永远不是 `"healthy"` | `stability.window.conditions.productionHealthy = false`（`failedConditions` 含 `productionHealthy`）⇒ 连续窗口数**恒为 0**，`stable` 永远为 `false` | 改读生产态**发布的布尔** `healthy`，**并**要求状态串属于 `PRODUCTION_HEALTHY_STATES`（`market_no_executable_profit` ／ `transaction_records_present`）⇒ **不放宽**任何门禁 | ✅ |

## 未闭合项（**`- [ ]` 口径** · 供 `recount_remaining_tasks_20261125.py` 逐目录计数）

- [ ] **E1** **持续产生链上广播交易记录**：`manager_v4_review_broadcast_requests` = **0**、`manager_cow_execution_attempts` = **0** ⇒ **未达成**（**市场条件** · **灰尘级正利润** ⇒ 未放宽任何门禁）

> **第八轮补记（`D-228`）**：缺口已**定位到市场条件**并**穷尽**：活价重算（区块 **124 435 443**）显示当前目录 **8 / 8** 闭环全部 `not_executable`；
> 能凑出 ETH 锚定三跳的组合**只有 8 条**（4 组直连 × 2）⇒ 闭环目录**没有漏**；
> 离线扫描的「盈利」是**旧价假象**（坏池的价读自 **11 天前**的区块 **89 668 143**）。**未放宽任何门禁。**

> **E2 / E3 已定位**：缺口**不在**广播准入（闸门已开、私钥已配），**在上游没有一条正收益复核**。

- [x] **G9.9** **复核队列的前缀截断**：主线程能看到「第一个未到期元素」⇒ 每轮成本 ∝ **到期条数**（不是作用域闭环总条数）（计划 `D-224` S4 · ✅ **常驻队列已接**：`review_queue_cache` ＋ 指纹校验，指纹不一致就重建）
- [x] **G9.10** **闭环级一个队列 ＋ 两个远期哨兵**：排序键**只有** `next_review_at`；「正在复核」＝**复核哨兵**（2036-06-06）、「等回执」＝**广播哨兵**（2035-05-05）；复核完成**覆盖**同一条记录（计划 `D-224` S2 / S4a / S5 / S5a · ✅ **队列、两个哨兵、运行时覆盖全部落地**）

## G. **用户口径驱动的第二轮修正**（2026-11-26T22:19:16Z · `supervisor_20261126T221917Z.jsonl`）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-220-记录层代价与四处口径修正_20261127.md`
>
> 用户口径：「记录数据是动态生成的外部数据，运行时基本不会分析，因为所有数据都在内存对象中，
> **不应该需要几分钟的时间**；按小时存文件**不影响**按日期生成目录 ⇒ 清理功能**不需要修改**」。

### G1. 记录层**不参与**决策 ⇒ 运行时不再为它付分钟级代价

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G1.1 | 记录层体量被**量化** | 生产库只读实测 | `cow_execution_reviews` = **119,430** 行 ／ **12.936 GiB** payload ／ 平均 **116,299 B** 每行 | ✅ |
| G1.2 | **决策读**是毫秒级 | `manager_v4_reviews` 计数 ／ 作用域索引窗口 | **0.015 s** ／ **0.047 s** | ✅ |
| G1.3 | **记录读**是分钟级 | 整表 payload 字节求和 ／ 带 payload 整扫 | **1,168.75 s ＝ 19 min 29 s** ／ **20.641 s** | ✅ |
| G1.4 | 历史正收益候选**换源** | 有管理器行即**不读**旧表 | 管理器库 **0.187 s** vs 旧表 **463.86 s** | ✅ |
| G1.5 | 换源**不改变答案** | 两个候选集逐键对照 | `sameCandidates = true`、`onlyInLegacy = []`、`onlyInManager = []` | ✅ |
| G1.6 | 回落路径**内存有界** | 旧表回落改两段读 | 只为正收益行取 payload；编排器常驻 **10.7 GB → 49 MB** | ✅ |
| G1.7 | **一轮周期**下降 | `healing_cycle` 事件间隔 | **8 min 09 s → 8 s** | ✅ |
| G1.8 | 小时分桶**不动**保留期清理 | `git diff` 零命中 | `purge_expired_review_files` / `review_day_is_removable` / `collect_day_folders` **三个函数 diff 里零命中** | ✅ |

### G2. 「忙 ≠ 死」：readiness 超时**不再**触发误重启

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G2.1 | 事故留证 | 旧码 journal | `runtime_unhealthy`（**21:44:47Z**，readiness `timed out`）→ `runtime_restarted`（**21:45:04Z**，PID **19620**）⇒ 复核轮被打断 | ✅ 已留证 |
| G2.2 | readiness 超时 ⇒ **忙** | `runtime_business_health` | 返回 `{"ok":true,"stage":"busy","busy":true}` | ✅ |
| G2.3 | readiness 真错 ⇒ 仍不健康 | 同上 | `kind = http` 时 `ok = false`、**无** `busy` | ✅ |
| G2.4 | 重启预算**按连续**计 | 一轮周期跑完 ⇒ 预算清零 | 原来累计 **3** 次即永久停止看护 | ✅ |

### G3. 代币层：NET ／ DJT 是**两个代币宇宙撞名**，审计口径已改

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G3.1 | 链上元数据直读 | `symbol()` ／ `decimals()` ／ `name()` | 四个地址**全是真合约**：allowlist 的 NET = **NetNet / 9 dec**，股票注册表的 NET = **Cloudflare / 18 dec**；DJT 同样两枚 | ✅ |
| G3.2 | 两注册表代号交集 | 逐符号对照 | 交集**只有 NET 与 DJT**，其余 **192** 个股票代号互不相交 | ✅ |
| G3.3 | **库存是对的** | 链上元数据 vs `token_stocks` | NET `0x116F0096…`、DJT `0x1D11f049…`，**18 dec**，逐项吻合 | ✅ |
| G3.4 | 核心库用的就是它们 | `v4_exchange_pools_core4` | NET 股票 **1** 条、DJT 股票 **25** 条；`manager_v4_directional_exchange_pools` NET **4** 条 | ✅ |
| G3.5 | 缺陷数归零 | `token_defects` | **2 → 0**；`defectKinds` `['funding_address_mismatch']` → `[]` | ✅ |
| G3.6 | **不再**触发危险的批量重准入 | 编排器动作集 | 新码首轮动作 = `fill_current_loop_reviews,rereview_historical_positive_candidates,observe_automatic_broadcast` ⇒ **无** `repair_tokens` | ✅ |

### G4. 广播闸门：「可执行正收益」不再是**结构性恒零**

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G4.1 | 库里确有可执行正收益 | `run_arbitrage.ok` ／ `.executable` | **7** 条：`ok = true`、`executable = true`，但 `category` 只有 `not_executable` ⇒ 旧口径数成 **0** | ✅ 已定位 |
| G4.2 | 判据换成**复核行自己**的结论 | `review_row_execution_verdict` | Manager 行读内核 `runArbitrage.executable`，旧行读 `category`（该函数本来就存在，`review_evidence` 早就在用） | ✅ |
| G4.3 | 计数修后读数 | `pnl_stats` | `stats.positive` **0 → 7**；`currentLoopStats.positive` **仍为 0** | ✅ |
| G4.4 | 在产闸门**不被放宽** | `transaction_production_status` 读 `currentLoopStats` | 生产态仍是 `market_no_executable_profit`；闸门**只**在「当前目录里有一条可执行正收益」时打开 | ✅ |
| G4.5 | 广播层的正收益计数不再恒零 | `audit_broadcast` | `historicalPositiveReviewsInWindow` **0 → 7**（来源 `manager_library`） | ✅ |

### G5. 回归

| # | 套件 | 读数 | 状态 |
|---|---|---|:---:|
| G5.1 | `scripts/tests/test_mainnet_arbitrage_healer.py` | **99 passed / 0 failed**（基线 85） | ✅ |
| G5.2 | `flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py` | **44 passed / 0 failed** | ✅ |

### G6. 空档画像（**部署后**）

| 窗口（UTC） | 复核条数 | 每分钟 | 间隔·中位 | **> 8 s 空档占比** |
|---|---:|---:|---:|---:|
| 代次 **51**（部署前） | 174 | — | 1.925 s | **37.9 %** |
| 代次 **52**（部署前） | 174 | — | 1.844 s | **19.6 %** |
| **18:00 – 19:00**（部署后 · **含旧表整扫**） | 1,094 | 18.2 | −0.0 s | **72.55 %**（**非稳态**：那一小时旧编排器在整表扫描） |
| **22:11 – 22:21**（部署后 · **编排器换码后**） | **225** | **22.5** | **1.689 s** | **7.07 %** |

> 口径：`> 8 s` 的空档秒数 ÷ 窗口跨度；工具
> `plan/runtime_evidence/tools/measure_stream_gap_window_20261127.py`（生产库只读）。

### G7. 加载模式的落点、候选数据保留、复核**重新计时**（`D-222`）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|:---:|---|
| G7.1 | 改模式**不动**池子存量 | master 池表 / 代币目录 | `v4_exchange_pools` **2550**、`token_core/stocks/extension/test` **4/194/162/1**、`chain_tokens` **4619** —— 全部保留 | ✅ |
| G7.2 | scope 重建只重写 scope 表 | `pool_manager/src/scope.rs:1433` | `DELETE FROM {scope.pool_table}`；master 表不在其中 | ✅ |
| G7.3 | 候选池数据**没有被清空** | 生产库只读 | `manager_dex_candidate_pools` **26**、`..._recommendations` **52**、`..._anchor_tokens` **18**、`manager_dex_candidate_abnormal_pools` **804**、legacy `dex_candidate_pools` **74** —— 全在 | ✅ |
| G7.4 | **确实没有**清空候选池的入口 | manager-api 路由 | 只有 `POST /v1/reviews/clear`、`POST /v1/broadcasts/clear`；候选池/候选边/闭环目录**无 clear** | ✅ |
| G7.5 | 缺陷 A：模式只落到**锚集合** | `candidate_edge_pool_manager/src/lib.rs:40` 硬编码 `["core","stocks","test"]` | `mode=core`，但 `manager_dex_candidate_anchor_tokens` 里 **9** 行 `anchor_library_key='stocks'`；**10** 条闭环全是 `ETH→股票→股票→ETH` | ⚠️ 已定位 |
| G7.6 | 缺陷 B：`candidate_exchange` scope 对模式免疫 | `pool_manager/src/scope.rs:1114` `"both_end" => {}` | 中间跳来自该表：**66** 条 `extension↔stocks` + **349** 条 `extension↔extension` 池留在 core 模式里 | ⚠️ 已定位 |
| G7.7 | 缺陷 C：派生层是**删+重建** | `replace_candidate_rows`（7 表）／`replace_closed_loop_rows`（5 表） | 闭环 **176 → 10**，上一代被物理删除（可重算，非「留库不用」） | ⚠️ 已定位 |
| G7.8 | 缺陷 D：闭环 **10** 条 vs 复核 **176** 行 | 生产库只读 | **166** 行是已退场闭环的**孤儿复核** | ⚠️ 已定位 |
| G7.9 | 缺陷 E：计时**无处安放** | 改前 runtime_state 列 | 无 `last_reviewed_at`/`next_review_at`；`review_required` **只写不读** | ✅ 已修 |
| G7.10 | 复核后**重新计时** | 迁移 `20261127_019` + 闭环/复核管理器 | `last_reviewed_at = 测量时刻`、`next_review_at = 测量 + 600 s`、`review_required = 0`；重建**沿用**计时 | ✅ |
| G7.11 | 编排器按闭环**自己的**时钟排复核 | `manager_review_schedules` / `review_is_due` | 单测 `test_the_schedule_of_a_loop_decides_when_it_is_measured_again`、`test_the_missing_review_candidates_follow_the_published_schedule` | ✅ |
| G7.12 | 回归 | Rust + pytest | closed-loop-manager **53**、review-manager **176**、manager-db **7** 全绿；healer pytest **143** + 新增 **2** 全绿 | ✅ |

### G8. 复核写回被拒、复核时间入档、按时刻选文件（`D-223`）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---:|---|
| G8.1 | 「旧数据」的定位 | 复核读价 vs 写回 | 复核是链上现读（价格**不旧**）；「旧」在**写回**——`POST /v1/reviews/run` **400** `review evidence references unknown loop …` ⇒ 复核行与计时**都没落库** | ✅ 已定位 |
| G8.2 | 拒绝的根因 | `validate_evidence_membership` | 整批校验：编排器带进 **166** 条**已退场闭环**的旧复核 ⇒ 一条对不上**整次写入作废** | ✅ 已定位 |
| G8.3 | 修法 | `merge_with_stored_evidence` | 只带**当前目录**的闭环；退场的按 `retiredReviewCount` **计数上报**；Manager 侧**严格校验保留** | ✅ |
| G8.4 | 计时重启的**实盘**读数 | 生产库只读 | **10 / 10** 闭环带 `last_reviewed_at`；`next_review_at` **= last + 600 s**（逐条一致）；`review_required=0`；仍无计时 **0** 条；10 条时刻**互不相同** | ✅ |
| G8.5 | 复核**准确时间**入档 | `ReviewRowSnapshot.reviewed_at` ／ `ReviewRecordLine.reviewedAt` | 记录行带**测量时刻**；该时刻**进入**审计状态比较 ⇒ 内容相同的重新测量**也产生记录行** | ✅ |
| G8.6 | **按该时刻**选文件 | `line_hour` | 先读 `reviewedAt`，再回落 `observedAt`；日期目录与保留期清理**同一把尺子** | ✅ |
| G8.7 | 时区 | 固定北京时（UTC+8） | 记录自带 `+08:00` 偏移 ⇒ 文件名可从记录**读回**；链上区块时间在 `pool-manager` 读入即转 | ✅ |
| G8.8 | 回归 | Rust + pytest | review-manager **177 / 0**（＋1）、pool-manager **172 / 0**、healer pytest **101 / 0** | ✅ |
| G8.9 | 诚实边界 | 广播请求行数 | 10 条闭环仍**全部** `not_executable`（费后现货乘积为负）⇒ 广播请求 **0** 行，「持续产生链上广播交易记录」**未达成**（缺口在**上游没有正收益复核**，**未放宽任何门禁**） | ⚠️ |

### G9. 闭环队列与状态机时钟：复核队列排序、前缀截断与广播队列分流（`D-224`）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-224-复核队列与广播队列重构计划_20261127.md`
>
> 用户口径：「闭环复核队列，当前是否支持按照复核时间排序的功能，这样，复核主线程只需要保证复核到
> 第一个无需复核的闭环元素，后面就不需要检测了，避免无效的循环检测。」
> ＋「**是不是应该使用 BTreeSet 来处理复核数据？** 复核完成的闭环对象放在广播队列中
> （预期收益为正数的话），或者**重新插入复核队列**（如果预期收益为负数的话）。」

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G9.1 | 每闭环**只留一个**调度时刻 | `next_review_at`；**删掉**倒计时器 | 迁移 `20261127_020` 把列换成 `review_cycle_started_at`；等待时长在**读取时派生**（`seconds_until_moment` / `seconds_since_moment`） | ✅ |
| G9.2 | 重置辅助函数是**唯一**写点 | `reset_review_start_on_connection` | `AfterRound` = `now + 间隔` → `cooldown`；`Manual` = `now` → `ready_for_review` | ✅ |
| G9.3 | 手工复核**立刻到期** | `ReviewStartReset::Manual` | `next_review_at = now`、`review_required = 1` ⇒ 主线程下一次扫描就能看到它 | ✅ |
| G9.4 | 轮结束**重新计时** | `ReviewStartReset::AfterRound` | `next_review_at = now + 复核间隔`；负收益的闭环**同样**走这一支 | ✅ |
| G9.5 | 认领 ＝ **每闭环的锁** | `begin_review_cycle_on_connection` | `review_pending` / `retired` / `cooldown` 未到期 ⇒ 一律 `false`（不二次认领） | ✅ |
| G9.6 | 启动**错峰** | `CLOSED_LOOP_REVIEW_START_SPACING_SECONDS` | 默认 **2 s**；新目录第 *i* 条 = `now + i × 错峰`；错峰**写在库里** ⇒ 重启不丢 | ✅ |
| G9.7 | 复核间隔**统一**为 **60 s**，且**只**被辅助函数使用 | `CLOSED_LOOP_REVIEW_INTERVAL_SECONDS` | 默认 **60**、钳制 **5–3600**；**没有**第二个 600 s 参数（`600` 只是**旧记录**里的旧值）；**第六轮**起该值**收成私有**（`review_interval_seconds*`），**只有** `reset_review_start_on_connection`（设置复核时刻的辅助函数）读它 —— 见 §G10 | ✅ |
| G9.8 | 队列**按复核时间排序** | `read_due_review_loops` | 排序键**已经是** `next_review_at`（`ORDER BY julianday(next_review_at)`）⇒ **口径一致** | ✅ |
| G9.9 | 主线程**前缀截断** | 主线程能看到「第一个未到期元素」 | **队列结构已落地**（`ReviewQueue` = `BTreeSet` ＋ `HashMap`，`due_entries` 走到**第一个未到期**就停 ⇒ 每轮成本 ∝ **到期条数**）；**常驻队列已接**（`manager_api` 的 `review_queue_cache` ＋ **指纹**校验 ⇒ 指纹不一致就重建；实盘 `GET /v1/closed-loops/review-due` ⇒ `queueResident = true`、`queueScanCost = 8`）⇒ S4 的「主线程改用队列」**未接** | 🟡 `D-224` S4 |
| G9.10 | 闭环级**一个**队列 ＋ **两个远期哨兵** | 排序键**只有** `next_review_at`；「正在复核」＝**复核哨兵**、「等回执」＝**广播哨兵** | **已落地**：`ReviewQueue`（`BTreeSet<(i64, loop_key)>` ＋ `HashMap` 载荷）＋ `ReviewSlot`；两个哨兵 `REVIEW_PENDING_SENTINEL_AT`（**2036-06-06**）／`BROADCAST_PENDING_SENTINEL_AT`（**2035-05-05**），**都在 `i32` 以内**；库里存**日期**、队列里排序键 `i64::MAX` ⇒ 日期不参与排序、永不过期；一条闭环**只有一条**记录（重排＝覆盖）；**第六轮**起两个哨兵在陈旧清扫里**显式跳过**（**忙碌 ≠ 过期**） | ✅ `D-224` S2 ＋ `D-225` |
| G9.11 | **文档** | `docs/复核与广播数据结构/` ＋ `docs/D-224` | **5** 篇文档（索引 / 状态机与复核时间点 / 复核队列与广播队列 / 表结构接口与参数 / 复核记录文件与时间口径）＋ 重构计划 **1** 篇 | ✅ |
| G9.13 | 词表加 **`broadcast_pending`** | `runtime_status` 的 CHECK 扩到 **6** 个值 | **已落地**：迁移 `20261127_021_review_queue_schema.sql` **重建表**（复制全部行 → 删旧表 → 改名 → 建索引），CHECK 扩到 **6** 个值；写点 `ReviewStartReset::BroadcastPending` | ✅ `D-224` S4a |
| G9.14 | 哨兵**四条硬规矩** | 显式排除 ＋ 哨兵永不到期 ＋ 保留起始时刻 ＋ `i64` | **已落地**：SQL `next_review_at NOT IN (?6, ?7)` ＋ 内存 `is_sentinel` **各一处**显式排除；单测 `a_sentinel_is_never_due`（即使「现在」= `i64::MAX` 也不放飞）；`review_cycle_started_at` 保留 ⇒ 卡住仍可查；两个哨兵 epoch 都在 `i32` 以内 | ✅ `D-224` A12 – A16 / K1 – K4 |
| G9.12 | 诚实边界 | 广播请求行数 | 仍 **0** 行（**8/8** 全部 `not_executable`）⇒ 「持续产生链上广播交易记录」**未达成**；**订正**：`D-224` 的 **S5/S5a 其实已在核心里**（`review_manager` 的 `BroadcastPending` 复位 ＋ `broadcast_manager::execution_record` 的终态回插，见 §G11.2）；**未放宽任何门禁**；**第六轮**起生产态的假红 `review_stalled` **已消除**（见 §G10），剩下的缺口**只在**上游没有正收益复核 | ⚠️ |

### G10. 复核间隔只属于**辅助函数**，新鲜度一律**按时刻比**（`D-225` · 第六轮）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-225-复核间隔只属于辅助函数且新鲜度按时刻比_20261127.md`
>
> 用户口径：「**远期哨兵有特殊的含义，这是设计文档中明确说明的，正在复核或者正在广播的特殊状态。**
> 复核审核到第一个不需要复核时间点的闭环就结束了」＋「**复核过程中并不在时间统计范围之内**，
> 只有广播结束或者复核没有盈利计算结束之后才计时的」＋「**时间间隔目前仅仅被辅助函数使用**……
> **凡是使用的的地方都是逻辑处理错误**」＋「**广播过程是在复核发现有正收益之后，直接调用广播消息，
> 没有任何判断新鲜度的处理流程**」。

#### G10.1 真根因：**复核耗时**被当成了「市场走掉的时间」

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---:|:---:|
| G10.1.1 | 一轮复核**要多久** | 生产库只读（`next_review_at − 间隔` = 轮末，减 `last_reviewed_at`） | **min 49.183 s / max 90.464 s / mean 70.905 s**（工具 `measure_review_round_duration_20261127.py`） | ✅ 已定位 |
| G10.1.2 | 旧窗口会误判多少条 | 「测量到现在 > 60 s ⇒ 变安静」 | **9 / 10** | ✅ 已定位 |
| G10.1.3 | 按时刻判会误判多少条 | 测量 vs `review_cycle_started_at` | **0**（current **5** ＋ waiting_for_its_round **5**） | ✅ 已修 |
| G10.1.4 | 旧口径的**三处**落点 | 代码级 | stale sweep ／ 物化广播请求 ／ 广播准入（`review_freshness_window_seconds`） | ✅ 全部改掉 |

#### G10.2 间隔的**归属**收口

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---:|:---:|
| G10.2.1 | 间隔**只有一处**用 | `review_interval_seconds*` 的可见性 | `closed_loop_manager` 里**收成私有**，`review_manager` 不再 `pub use` | ✅ |
| G10.2.2 | 「有没有时刻」不再靠间隔 | `next_review_moment*` | **删掉**（`review_manager` 改用 `parse_from_rfc3339(...).is_err()`） | ✅ |
| G10.2.3 | 全仓**没有**第二处消费者 | `CLOSED_LOOP_REVIEW_INTERVAL_SECONDS` 全仓检索 | 只剩**参数读取**（辅助函数）＋ **参数存储的默认值**（只写不判）＋ 单测 | ✅ |
| G10.2.4 | 哨兵是**忙碌**不是过期 | 陈旧清扫的判定顺序 | 撞上 **2036／2035** 哨兵 ⇒ **跳过** | ✅ |

#### G10.3 实盘（部署后）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G10.3.1 | 新 DLL 上线 | 文件大小 ／ 旧件备份 | **18,653,184 B**（旧 **18,599,424 B** 备份为 `.bak-20261127-he08`）；manager-api **15,857,152 B** | ✅ |
| G10.3.2 | **假红消除** | `transactionProduction` | `review_stalled` / healthy=**false** ⇒ **`market_no_executable_profit` / healthy=true** | ✅ |
| G10.3.3 | 闭环按**时刻**判为当前 | `layers.reviews.legacy.currentLoopFreshness` | `basis = loop_review_time_point`、`checkedLoops = 10`、**`currentLoops = 10`**、**`notCurrentLoops = []`**、`current = true` | ✅ |
| G10.3.4 | 每条复核都有**明确盈亏** | `currentLoopStats` | `total 10`、`valued 10`、**`missingValue = 0`**、`bestAmountConfirmed = 10`、`positive = 0`、`nonPositive = 10` | ✅ |
| G10.3.5 | **没有**陈旧标记 | 当前目录的 `review_reason` | `not_reviewed_since` / `not_reviewed_at_all` / `no_review_time_point` **共 0 条** | ✅ |
| G10.3.6 | 诚实边界 | 广播请求 ／ 执行记录 | **0** 行 ／ **0** 行（**10/10** `not_executable`，`runArbitrage returned ok=false`，PnL 全为负、**灰尘级**）⇒ 「持续产生链上广播交易记录」**未达成**（缺口在**上游没有正收益复核**，**未放宽任何门禁**） | ⚠️ |

#### G10.4 回归

| # | 套件 | 读数 | 状态 |
|---|---|---|:---:|
| G10.4.1 | `cargo test -p closed-loop-manager` | **72 passed / 0 failed** | ✅ |
| G10.4.2 | `cargo test -p review-manager` | **178 passed / 0 failed** | ✅ |
| G10.4.3 | `cargo test -p broadcast-manager` | **43 passed / 0 failed** | ✅ |
| G10.4.4 | `cargo test -p manager-api` | **34 passed / 0 failed** | ✅ |
| G10.4.5 | `cargo test -p manager-db` | **9 passed / 0 failed** | ✅ |
| G10.4.6 | `cargo test -p flashloan-rs --lib` | **278 passed / 0 failed** | ✅ |
| G10.4.7 | `pytest scripts/tests/test_mainnet_arbitrage_healer.py` | **108 passed / 0 failed**（基线 107，净 **+1**） | ✅ |
| G10.4.8 | `pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py` | **44 passed / 0 failed** | ✅ |
| G10.4.9 | `git diff --stat Cargo.lock` | **零变化** | ✅ |

### G11. 运行时广播路径**按时刻判** ＋ 部署读数**订正**（`D-226` · 第七轮）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-226-运行时广播路径按时刻判与部署校正_20261127.md`
>
> 用户口径：「**凡是利用时间间隔的处理流程，基本上应该都是错误的，没有这种需求。**」
> ⇒ 间隔**只**属于「设置复核时刻的辅助函数」，其它任何地方用它**都是逻辑处理错误**。

#### G11.1 第四处落点：运行时广播路径

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G11.1.1 | 旧判据 | `worker_pool.rs` 的 age 窗口 | `MAX_BROADCAST_REQUEST_AGE_SECONDS = 120`，判「测量到现在 > 120 s」⇒ 算的是**复核自己花掉的时间** | ✅ 已定位 |
| G11.1.2 | 实测危害 | 复核「测量→落库」滞后 | 当前 **8** 条＝**52.046 / 59.245 / 59.669 / 65.426 / 73.030 / 74.758 / 94.462 / 94.589 s**；全部 **176** 条 max **1434.695 s** ⇒ 旧窗口会拒掉 **2 / 176** | ✅ 已定位 |
| G11.1.3 | 改法 | 新鲜度 | `now` vs **闭环自己的**复核时间点（`loop_review_moment` ⇒ `read_review_queue` ⇒ `due_at`）；**不重建任何间隔** | ✅ 已改 |
| G11.1.4 | 失败即闭锁 | 没有复核时间点 | **拒绝**（证不出来 ⇒ 不发） | ✅ 已改 |
| G11.1.5 | 哨兵不特殊 | **2036／2035** 两个远期哨兵 | 都在**未来** ⇒ `now < 时刻` ⇒ **放行**（**忙碌 ≠ 过期**） | ✅ 已改 |
| G11.1.6 | 单测 | 三条新用例 | T1 闭环已往后走 ⇒ 拒；T2 复核**老 600 s** 但闭环仍拥有它 ⇒ **放行**；T3 无复核时间点 ⇒ 拒 | ✅ |

#### G11.2 `S5` / `S5a` **其实已在核心里**（订正 `D-224` 的记述）

| # | 步骤 | 落点 | 现状 |
|---|---|---|:---:|
| G11.2.1 | **S5** 正收益分支：停在**广播哨兵** | `review_manager` ⇒ `reset_review_start_on_connection(BroadcastPending)` | ✅ **已在核心里** |
| G11.2.2 | **S5** 负收益分支：`cooldown` ＋ **轮末 + 间隔** | `review_manager` ⇒ `record_review_schedule_on_connection` | ✅ 已在核心里 |
| G11.2.3 | **S5a** 回执终态 ⇒ 回插复核队列 | `broadcast_manager::execution_record` ⇒ `start_review_clock_on_connection`（Success / Failed 两支） | ✅ 已在核心里 |

#### G11.3 部署读数**订正**（上一稿的结论是错的）

| # | 验收项 | 读数 | 状态 |
|---|---|---|:---:|
| G11.3.1 | 运行时加载**哪一个** Rust 产物 | `core/rust_extension.py` **优先工作区自建件**；运行时进程模块表实测指向 **`rs-src/target/release/flashloan_rs.dll`**（**不是** site-packages 的 `.pyd`） | ✅ 已订正 |
| G11.3.2 | 上一轮三处 Rust 改动**上线了吗** | **已上线**（`review_manager` **22:11** ／ `broadcast_manager` **21:52** 早于构件 **22:42** DLL 与 **22:53** manager-api）⇒ 上一稿「三处一次都没执行过」**作废** | ✅ 已订正 |
| G11.3.3 | 本轮改动的上线 | `worker_pool.rs` 写入 **23:29** 晚于 **22:42** 的 DLL ⇒ 必须**重建 + 重启** | ✅ 已做 |
| G11.3.4 | 重建 | `maturin build --release`（先停运行时解锁） | cargo `release` **1 m 17 s**，产物 **18,660,864 B**（**23:59:54**）落在加载器期望的路径 | ✅ |
| G11.3.5 | 二进制**自证** | 新判据字面量**命中**、旧 age 窗口字面量 **0 命中**、备份旧件**命中不到**新判据 | ✅ |
| G11.3.6 | 重启 | 编排器 PID **10560** ／ 运行时 PID **11000** | `/healthz` ＝ **`{"status":"ok"}`**，进程模块表指向**工作区 DLL** | ✅ |

#### G11.4 上线后的实盘（`supervisor_20261127T160052Z.jsonl`）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G11.4.1 | 生产态 | `transactionProduction` | **`market_no_executable_profit` / healthy=true**、`reason = fresh_reviews_have_no_executable_positive_pnl` | ✅ |
| G11.4.2 | 新鲜度口径 | `reviewFreshnessBasis` | **`loop_review_time_point`**、`reviewNotCurrentLoops = []` | ✅ |
| G11.4.3 | 层间级联 | 代币 → 池 → 闭环 → 复核 → 广播 | 代币 ✅／池 ✅（`candidatePoolCount = 26`、深度全 adequate）／闭环 ✅（**8** 条、`consumers_need_rebuild = 0`）／复核 ✅（**8 / 8** 明确盈亏、`missingValue = 0`、`bestAmountConfirmed = 8`）／广播 ⛔（`attempts = 0`、`automaticBroadcastCandidates = 0`） | ✅ 前四层全绿 |
| G11.4.4 | 缺口在哪 | blockers | **只余 `broadcast`** ⇒ 缺口在**上游没有正收益可执行复核**（**市场条件**），**不在**代码门禁 | ⚠️ |
| G11.4.5 | 稳定窗口 | `stableWindows` | **0 / 3**：`broadcastSuccess` 要求**真的发过**交易 | ⚠️ |

#### G11.5 回归

| # | 套件 | 读数 | 状态 |
|---|---|---|:---:|
| G11.5.1 | `cargo test --lib`（`flashloan-rs`） | **280 passed / 0 failed**（基线 278，净 **+2**） | ✅ |
| G11.5.2 | `pytest scripts/tests/test_mainnet_arbitrage_healer.py` | **109 passed / 0 failed**（基线 108，净 **+1**） | ✅ |
| G11.5.3 | `git diff --stat Cargo.lock` | **零变化** | ✅ |

### G12. 级联矩阵的闭环链接**测得到**（`D-227` · 第七轮）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-227-级联矩阵的闭环链接测得到_20261128.md`
>
> 口径：「**空集不是健康**」——测不到的链接不许读成「全都在作用域内」。

#### G12.1 读数：闭环链接**测不了**（假绿 ＋ 假红）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G12.1.1 | 旧读数 | `cascade_matrix` 的跳数 | `hopsTotal = 0`、`hopsInCandidateScope = 0`、`exactThreeHops = 0` ⇒ `notExactThreeHops = 8`（**假红**） | ✅ 已定位 |
| G12.1.2 | 旧读数的**假绿** | `allHopsInScope` | `hops_total == hops_in_scope` ⇒ **0 == 0 恒为 True** ⇒ 「跳都在作用域内」**从未被测过** | ✅ 已定位 |
| G12.1.3 | 错的**第一处** | 字段名 | 管理器不发 `poolIds` / `pool_ids`，发的是 **`directionalPoolIds`** ＋ 独立的**跳行** `hops[]`（含 `directionalPoolId` ＋ `directionalScopeKey`） | ✅ 已定位 |
| G12.1.4 | 错的**第二处** | 池 ID 格式 | 闭环侧发**组合 ID**（`dpool:v1:<网络>:<链>:<作用域>:<池键>:<名次>`），候选侧发**裸池键**（`0x…`）⇒ 直接比较**永不相等** | ✅ 已定位 |
| G12.1.5 | 真相 | 只读核对（跳行 ＋ 活表） | 闭环 **8** 条 × **3** 跳 ＝ **24** 跳，**24 / 24** 的裸池键落在候选作用域池集（**26** 个）内，缺跳闭环 **0** ⇒ 闭环层**本来就健康**，坏的是**尺子** | ✅ |

#### G12.2 修法与回归

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G12.2.1 | 格式归一 | `directional_pool_key()` | 组合 ID ⇒ 裸池键；裸 ID 原样保留 | ✅ |
| G12.2.2 | 权威来源 | 跳行取自**管理 API 的 `hops`** | 闭环行的 `directionalPoolIds` 只作**兜底** | ✅ |
| G12.2.3 | **失败即闭锁** | `allHopsInScope` | 要求 `hops_total > 0` ⇒ 空跳集**不再**读成健康 | ✅ |
| G12.2.4 | 新读数 | `loopsWithoutHopRows` / `hopScopeCounts` / `candidatePoolUniverse` | 三项**随报告上报** | ✅ |
| G12.2.5 | 回归 | `pytest scripts/tests/test_mainnet_arbitrage_healer.py` | **112 passed / 0 failed**（基线 **109**，净 **+3**） | ✅ |

#### G12.3 实盘（`supervisor_20261127T161612Z.jsonl` 的 `sample = 0`）

| # | 验收项 | 读数 | 状态 |
|---|---|---|:---:|
| G12.3.1 | 闭环链接 | `total 8` ／ `exactThreeHops 8` ／ `hopsTotal 24` ／ `hopsInCandidateScope 24` ／ `hopsMissingFromCandidateScope 0` ／ `loopsWithMissingHop 0` ／ `allHopsInScope true` ／ `allExactThreeHops true` ／ `hopScopeCounts {core4: 16, candidate_exchange: 8}` | ✅ |
| G12.3.2 | 候选池 | `total 26` ／ `byDepthStatus {deep: 10, medium: 16}`（**无** thin） | ✅ |
| G12.3.3 | 资金代币 | `expected 5` ／ `valid 5` ／ `invalidCount 0` ／ `counterfeitAdmittedCount 0` | ✅ |
| G12.3.4 | 五层读数 | 代币 ✅ ／ 池 ✅ ／ **闭环 ✅（本轮才真正测到）** ／ 复核 ✅（**8 / 8** 明确盈亏）／ 广播 ⛔（`attempts = 0`） | ⚠️ 缺口在**上游没有正收益复核** |

### G13. 活价重算 ＋ E1 根因 ＋ 状态语义修复（`D-228` · 第八轮）

> 证据文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-228-活价重算与E1根因_20261128.md`
>
> 用户口径：「**问题是，为什么会有旧价格影响……怎么会有旧价格?**」＋「**不可能有旧价格影响系统**」

#### G13.1 「旧价格」的**确切来源**

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G13.1.1 | 活价重算工具 | `extsload(bytes32)` 读 `Slot0` | `probe_live_cycle_prices_20261128.py`，区块 **124 435 443**，**324 / 364** 个池读到活价 | ✅ |
| G13.1.2 | 旧价假象被量化 | 离线扫描 vs 活价扫描 | 离线 **54** 跳 / **9** 条盈利（最好 **+60 221 ppm**）；活价 **42** 跳 / **3** 条盈利（最好 **+582 ppm**） | ✅ 已量化 |
| G13.1.3 | 旧价的真身 | 坏跳的价读自哪 | 区块 **89 668 143**（**2026-11-17**）⇒ 与活价差 **34 767 300** 块（≈ **11 天**） | ✅ 已定位 |
| G13.1.4 | 机理 | 为什么会有旧价 | 被排除出 active 作用域的池**永不刷新** ⇒ 「扫全表」的分析会读到它们的陈旧价 | ✅ 已定位 |
| G13.1.5 | 合约不受影响 | 复核走链上 `runArbitrage` | **运行时判定永远用活价**；旧价只污染**离线分析** | ✅ |

#### G13.2 闭环宇宙**已穷尽**（不是覆盖缺陷）

| # | 验收项 | 读数 | 状态 |
|---|---|---|:---:|
| G13.2.1 | ETH 可达代币 | 核心库 ETH 锚候选池 **12** 个 / **8** 个代币（NVDA SPY CRCL MU GOOGL GLXY MSTR SPCX） | ✅ |
| G13.2.2 | 走不成三角的 | TSLA / PFE / AAPL / AMD / META / NET **没有 ETH 池**（只有 hook 动态费率 **8 388 608** 的池） | ✅ |
| G13.2.3 | 目录**没有漏** | 闭环只用 {GOOGL, NVDA, MU, SPY} ⇒ **4** 组直连 × 2 = **8** 条 = **全部** | ✅ |
| G13.2.4 | 不放宽门禁 | `MAX_CANDIDATE_FIXED_FEE = 30 000`；ETH↔TSLA/PFE 是 **50 000**（5 %）⇒ 放进来只会更亏 | ✅ 未放宽 |

#### G13.3 状态语义修复（顺手修掉的**误导性理由**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G13.3.1 | 旧缺陷 | 无条件覆盖 `status_reason` | 生产库：**8** 条里 **7** 条挂着 `the broadcast never became an attempt`（该轮**根本没有利润**） | ✅ 已定位 |
| G13.3.2 | 修法 | 状态闸门 | 只有 `runtime_status == broadcast_pending` 才写；新增 `read_runtime_status` / `read_runtime_status_on_connection` | ✅ 已改 |
| G13.3.3 | 上线后读数 | 8 条闭环 | **全部 `cooldown` / `measured_without_profit`** | ✅ |
| G13.3.4 | 回归 | 新用例 | `a_loop_that_is_not_parked_at_the_broadcast_sentinel_keeps_its_reason`；`flashloan-rs` **281 / 0**（净 **+1**） | ✅ |

#### G13.4 写锁**不等** ⇒ 手工复核请求被丢掉（第二处缺陷）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| G13.4.1 | 旧缺陷 | `open_connection` **不设 `busy_timeout`** | SQLite 默认 **0** ⇒ 锁被占时**第一次尝试就失败** | ✅ 已定位 |
| G13.4.2 | 实盘危害 | 手工复核请求 | **503** `closed loop storage error: database is locked` ⇒ 复核**根本没开始** | ✅ 已定位 |
| G13.4.3 | 修法 | 可写连接统一设忙等待 | **60 s**（与仓里已有的两处写法一致） | ✅ 已改 |
| G13.4.4 | 回归 | 新用例 | `a_writer_waits_for_the_lock_instead_of_failing_at_once`：持锁者不放手时第二个写者**在等**；放手后拿到锁 | ✅ |
| G13.4.5 | 上线后读数 | 同一个手工复核请求 | **200**、`applied = true`；闭环 `cooldown / measured_without_profit` ⇒ **`ready_for_review` / `review_required`**、`next_review_at` ＝ **请求那一刻**（**19:01:28**） | ✅ |
| G13.4.6 | 手工复核的完整链 | 运行时接到 + 轮末重新计时 | **19:01:36** 测完（请求后 **8 s**）；轮末 `next_review_at` ＝ **19:02:59**（＝轮末 **19:01:59** ＋ **60 s**）⇒ **复核启动时间＝当前时间**、结束**重新计时**（用户口径） | ✅ |
#### G13.5 回归与部署

| 套件 | 读数 |
|---|---|
| `cargo test -p closed-loop-manager -p review-manager -p broadcast-manager -p manager-db` | **303 passed / 0 failed**（`closed-loop-manager` 净 **+1**） |
| `cargo test --lib`（`flashloan-rs`） | **281 passed / 0 failed**（基线 280，净 **+1**） |
| `pytest scripts/tests/test_mainnet_arbitrage_healer.py` | **113 passed / 0 failed**（基线 112，净 **+1**） |

| 项 | 读数 |
|---|---|
| `flashloan_rs.dll` | **18 667 008 B**（**2026-11-28 02:57:18**），新判据字面量**命中** |
| `manager-api.exe` | 重建 **2 m 29 s**，PID **22856** |
| 运行时 | 看护 PID **26092** ／ 运行时 PID **18996**，`/healthz` ＝ `{"status":"ok"}` |
| 编排器 | PID **21204**，`supervise --apply --interval 60` |

#### G13.6 诚实边界（**E1 仍未闭合**）

| # | 项 | 读数 | 状态 |
|---|---|---|:---:|
| G13.6.1 | 广播请求 / 执行记录 | **0 / 0** 行 ⇒ 「持续产生链上广播交易记录」**未达成** | ⬜ 市场条件 |
| G13.6.2 | 缺口位置 | **不在**广播准入（闸门开、私钥已配、复核线程直发已落地），**在上游没有一条正收益复核**（**8 / 8** `not_executable`） | ✅ 已定位 |
| G13.6.3 | 现货差 vs 可执行 | 活价 **+582 ppm** 只是**现货乘积**（不含价格冲击与 gas），同窗口合约结果仍为**负** ⇒ 差还没盖住成本 | ✅ 诚实边界 |
