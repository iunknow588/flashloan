# D-241 `T-池编.02`｜验收清单（**checklist**）

> **【归并 · 2026-11-28】** 本清单的 **5** 项（`G4.1` ／ `I2.4` ／ `I4.4` ⇒ `T-核.09`；`I2.6` ⇒ `T-储接核.01`；`I2.7` ⇒ `T-核.08`）已于 **2026-11-28** **归并**到新任务。
> **执行落点**：`T-核.09`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **5** 项（`G4.1` ／ `I2.4` ／ `I4.4` ⇒ `T-核.09`；`I2.6` ⇒ `T-储接核.01`；`I2.7` ⇒ `T-核.08`）已于 **2026-11-28** **归并**到新任务。
> **执行落点**：`T-核.09`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> 验收编号 `AC-池编.02`。**每一条都必须带实测读数**；推断不算读数。
> 读数取自**生产库只读**体检（`mode=ro` ＋ `PRAGMA query_only=ON`）与**本地回归**。

## A. 缺陷取证（**已读 · 只读**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | **池是否真的消失** | 19 条缺值复核的去重池 hash 是否还在**主池表** `v4_exchange_pools` | **15 / 15 全部还在**（主池表 2550 行）⇒ **池从未消失** | ✅ |
| A2 | **路由是否可复价** | 逐条看 3 跳的池是否都在主池表 | **19 / 19** 条的**每一跳**都还在 ⇒ **可复价** | ✅ |
| A3 | **缺的是作用域** | 被当前作用域覆盖的池 hash | **6 / 15**；被任一作用域**完整**覆盖的路由 **0 / 19** | ✅ |
| A4 | **代币库是否完好** | 四个代币库的 admitted 行数 | `token_core` 4、`token_stocks` 194、`token_extension` 2、`token_test` 1 ⇒ **全部完好** | ✅ |
| A5 | **缺陷发生点** | `manager_write_requests` 里 `pools.scope.rebuild` 的 `libraryKeys` 时间线 | 11-18 22:50／11-19 00:10 = `["core","stocks","test"]`（**用户所述可用状态**）→ **11-26 19:29 healer 写入 `["core"]`（def=14）** → 之后 10 次保持 `["core"]` | ✅ |
| A6 | **复核被孤儿化** | 19 条复核的写入时刻 vs 收窄时刻 | 复核写于 **11-26 19:06:40–19:11:20**（作用域仍是 `core_stocks`）⇒ 比收窄早 **18 分钟** ⇒ **被收窄孤儿化**，不是「测不到」 | ✅ |

## B. 代码项（**已落地 · 回归已跑**）

- [x] **B1** **`M-1` 默认加载模式不得窄于声明**：`scope.rs` 的 `TokenLoadMode::default_for_scope()` 改为按**作用域自身声明**取模式（新增 `for_library_keys()`）；注释写明「默认不得比声明更窄」
- [x] **B2** **`M-1` 审计判定由「精确相等」改为「声明 ⊆ 已配置」**：`baseline_audit.rs` 新增 `scope_carries_declared_libraries()`；宽于声明**不算** stale，缺库**才算** stale
- [x] **B3** **`M-1` 审计把声明的库报出来**：`PoolBaseline` 新增 `coreScopeExpectedLibraries` / `candidateScopeExpectedLibraries`（camelCase 线格式）
- [x] **B4** **`M-8` healer 不再自带模式**：`AUDITED_SCOPE_TOKEN_LOAD_MODE = None`；新增 `scope_load_mode_name()` / `scope_mode_narrows()` / `scope_expected_libraries()` / `scope_configured_libraries()` / `scope_expected_modes()`
- [x] **B5** **`M-8` 收窄守卫**：`repair_scope_policy()` 在请求模式会**丢掉作用域已有库**时**跳过**并记录 `skipped`，且说明原因

## C. 回归（**只跑改动模块**）

- [x] **C1** **`M-1`**：`cargo test --offline -p pool-manager` ⇒ **175 passed / 0 failed**（含新增 2 项：默认不得窄于声明；宽于声明不算 stale / 缺库才算 stale）
- [x] **C2** **`M-8`**：`python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **126 passed / 0 failed**（基线 119 ＋ 新增 7）
- [x] **C3** **`M-8`**：`python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**

## D. 部署与生产恢复（**顺序不可反**）

- [x] **D1** **先部署**：`cargo build --offline --release -p pool-manager -p manager-api` **成功（3 min 56 s）**，日志 `plan/runtime_evidence/logs/t401_runtime_sup/cargo_build_release_scope_restore_20261128.log` ⇒ 二进制带修复
- [x] **D2** **重启 manager-api**（Windows 下运行中的 exe 无法被覆盖 ⇒ 先停后建后起）与 **重启 healer**（Python 改完必须重启进程）：manager-api 重启为 **pid 25572**；healer 以修复后的脚本重启为 **pid 20160**（命令与日志见 `plan/runtime_evidence/logs/t401_runtime_sup/healer_supervise_scope_restore_20261128.log`）⇒ **首个周期后作用域仍是 `["core","stocks"]` 未被收窄** ⇒ 缺陷在生产上闭环
- [x] **D3** **恢复作用域**：两个作用域由 `["core"]` 重建回**声明模式** `["core","stocks"]`（`manager-api` `/v1/pools/scopes/rebuild`，`dryRun=false`）⇒ `v4_exchange_pools_core4` **414 → 428 行（删 0、加 14）**，`..._candidate_exchange` **428**
- [x] **D4** **重建级联**：`candidateEdge → candidateExchange → directionalPools → closedLoops` 按序重建 ⇒ 第一轮 **52 池 → 216 方向 → 24 跳 → 8 闭环**；**深度重测后的第二轮**（§D6）**64 池 → 278 方向 → 108 跳 → 36 闭环**
- [x] **D5** **实盘读数**：审计 `core_scope_library_policy_stale` / `candidate_scope_library_policy_stale` **消失**（`coreScopePolicyMatches = True`、`candidateScopePolicyMatches = True`、`candidateScopeOutOfPolicyPoolCount = 0`，报告 `plan/runtime_evidence/reports/baseline_audit_after_restore_20261128.json`）；路由每一跳都在主池表 **19 / 19**；作用域覆盖 **12 / 15** 池、**6 / 19** 路由完整覆盖

> **★ D5 的期望值更正（读数推翻原写法）**：本框原写「15 / 15 池被作用域覆盖、19 / 19 路由完整覆盖」。
> 实测**达不到**，原因是**链上事实**而不是缺陷：剩下 **3** 个池的深度档位是 **dust**
> （`0x24107d15` / `0x84bd4e2d` / `0xe1ae6a4c`），而作用域判据只放行 `deep` / `liquid` / `medium`。
> **未为此放宽任何判据** ⇒ 覆盖停在 **12 / 15** 池、**6 / 19** 路由（§D6）。

## D6. 深度修复与重新物化（**本轮第二阶段 · 已执行**）

- [x] **D6.1** **池子工具重测**：`repair_unavailable_pool_depth_20261128.py` ⇒ dry run `returncode = 0` / `refreshedCount = 9` / `writtenCount = 0`；apply **`writtenCount = 9`**、**`errors = []`**
- [x] **D6.2** **档位落地**：`unavailable` → **deep 4 / medium 2 / dust 3**（`has_liquidity = true` ＋ 链上 `tick` 流动性读数）⇒ **测量缺失**已消除
- [x] **D6.3** **作用域重新物化**：两个作用域各 `insertedRows = 6`、**`deletedRows = 0`**、`activePoolCount` **26 → 32**、`idempotentReplay = false` ⇒ 表行数 **428 → 434**
- [x] **D6.4** **幂等键陷阱已修**：`rehearse_scope_rebuild_20261127.py` 原用**固定幂等键** ⇒ 重建只**重放旧结果**、**不重新物化**；已改为**每次调用唯一幂等键**（见 `D-242` §7.1 的教训）
- [x] **D6.5** **级联与复核**：闭环 **8 → 36** 条（**24 → 108** 跳）；新代次复核版本 **70 / 71** 共 **9** 行**全部有明确盈亏**（**全负**，灰尘级）⇒ 恢复的路由**正在被真实定价**

## E. 诚实边界（**永久留占位**）

- [ ] **E1** **不伪造**：本任务**不填任何盈亏数值**、不填 0、不放宽任何门禁、不删任何历史复核行。恢复作用域只让那 19 条路由**重新可定价**，它们**是否真的有盈亏**由链上事实决定，**不预判**。
- [ ] **E2** **不代为关闭**：广播缺口仍是**市场条件**问题（正利润是灰尘级），本任务**不代任何其它任务关闭**；本框**不代为关闭**。
- [ ] **E3** **撤回旧结论**：上一轮登记的「目录外复核不可复价 → 退役语义」**前提已被推翻**（A1／A2）⇒ 该任务的收口口径**必须改写**，不得按「退役」结案。

## G. 第二轮：全负期望下的 tick 交易量比对 ＋ 复核代次（**2026-11-28**）

### G1. 用户口径与读数（**只读**）

- [x] **G1.1** **问题落地**：「预期盈利全部为负数时，一并检测最佳交易量（tick 计算值）是否大于初始交易量（单 tick 计算值）」⇒ 新增 `tick_amount_comparison()` 与 `tickAmount` 区块（`compared` / `nonPositive` / `bestAboveSingleTickStart` / `bestNotAboveSingleTickStart` / `unknown` / `executableSizeUnsearched` / `verdicts` / `gaps`）
- [x] **G1.2** **读数（当前代次 29 条）**：预期盈利为负 **29 / 29**；**最佳交易量 > 单 tick 初始交易量 = 0 / 29** ⇒ **答案是否**；**≤ = 29 / 29**（`bestAmountRaw` 全为实测零 `0x0`，`initialAmountRaw` 全为正）
- [x] **G1.3** **可执行域**：阶梯上行可达 ＝ `min(2 × initial, 单tick上限)`；**缺口 > 1 raw 单位 = 0 / 29** ⇒ `executableSizeUnsearched = 0`、结论 `best_not_above_start_executable_domain_covered` **29**
- [x] **G1.4** **跨 tick 域不可执行**：深度上限 > 单 tick 上限 **21 / 36**（**2.4× – 30×**），但 Review Manager 的单 tick 门（`review_manager/src/lib.rs:3028`）以 `final_input_exceeds_single_tick_capacity` 拒绝 ⇒ **不列为缺口**
- [x] **G1.5** **四态结论**：大于／不大于且已覆盖／不大于且有未搜尺寸（**缺口**）／读不到（**不猜**）
- [x] **G1.6** **在产首周期读数**（自愈器新代码重启后的第一个生产周期）：`compared = 33`、`nonPositive = 33`、**`bestAboveSingleTickStart = 0`**、`executableSizeUnsearched = 0`、结论 `best_not_above_start_executable_domain_covered` **33** ⇒ **用户问题的在产答案仍然是「否」**

### G2. ★ 复核代次缺陷（**本轮真正的收口**）

- [x] **G2.1** **取证**：在产闭环 **36** 条中，**7** 条复核的代次是 **陈旧**的（`closedLoopVersion 54` vs 当前 **72**、`poolScopeChecksum sha256:9987e6cc…` vs 当前 `sha256:9ec36f1a…`、`candidateEdgeVersion 11` vs **17**、`observationVersion 14` vs **20**），`reviewed_at` ＝ **2026-11-26 19:28:49 – 19:29:53**（**收窄作用域时期**）
- [x] **G2.2** **缺陷点 ①**：复核层的「当前闭环」判定**只按 loop key 匹配** ⇒ 陈旧代次复核被算成在产复核（`matchedMissingPnl = 0`、`bestAmountMissing = 0` ⇒ **误报绿**）
- [x] **G2.3** **缺陷点 ②**：`reviewCycleStartedAt = null` 时**默认判 current** ⇒ 7 条陈旧代次闭环的 `notCurrentLoops = []`
- [x] **G2.4** **缺陷点 ③**：`stored_review_evidence()` **不带 `closedLoopGeneration`** ⇒ Review Manager 按「没写就用当前代次」归档 ⇒ **旧测量被重贴当前代次的标签**（**洗代次**）
- [x] **G2.5** **修复 ①**：新增 `current_generation()` / `review_generation_status()`（`current` / `stale` / `unknown` 三态 ＋ 不匹配字段清单）
- [x] **G2.6** **修复 ②**：`audit_reviews()` 按**代次**分层；`current_rows` 只取当前代次；新增 `generation` 区块与 `missingCurrentMeasurement`
- [x] **G2.7** **修复 ③**：`review_freshness_by_loop()` 新增 `stale_generation_loops` 参数 ⇒ 陈旧代次闭环判 `measured_in_older_generation`
- [x] **G2.8** **修复 ④**：`stored_review_evidence()` 复制该行**自己的**代次进 `closedLoopGeneration` ⇒ 回写**不再洗代次**
- [x] **G2.9** **修复 ⑤**：新动作 `repair_stale_generation_reviews` ⇒ 对**没有当前代次复核**的在产闭环发起**新复核**（**走复核路径，绝不碰广播**）；普通 `fill_missing_reviews` **排除**这些闭环，避免同周期重复请求
- [x] **G2.10** **修复后读数**：`L4 reviews valid=False`、`staleGeneration = 7`、`loopsWithoutCurrentReview = 7`、`productionState = review_stalled`、原因 `a_closed_loop_does_not_carry_its_current_measurement`、`blockers = [reviews, broadcast]`
- [x] **G2.11** **在产首周期**（journal `supervisor_20261127T235312Z.jsonl`）：动作 **`repair_stale_generation_reviews` 已触发**（`candidateCount = 3`、`evidenceCount = 0`、`materialization = null` ⇒ **没有任何盈亏被写回**）；代次读数 `staleGeneration 4 → 3`（运行时补测 1 条）；运行时答复 **5 × 409**（忙）＋ **3 × 404**（常驻 worker 未注册）＋ **3 × `timed out`** ⇒ 剩下的 3 条**仍需运行时按当前路由重新注册常驻 worker**（**不代运行时关闭**）

### G3. 回归

- [x] **G3.1** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **132 passed / 0 failed**（基线 **126** ＋ 新 **6**：tick 比对 3 条 ＋ 代次 3 条）
- [x] **G3.2** `python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**
- [x] **G3.3** `cargo test --offline -p pool-manager` ⇒ **175 passed / 0 failed**（本轮**未改** `M-1` ⇒ 只作基线）

### G4. 诚实边界（**永久留占位**）

- [ ] **G4.1** **不绕过运行时**：7 条陈旧代次闭环需要**按当前路由重新注册常驻 worker**（运行时侧）；healer 侧只**持续重试**并如实记录原因（`CLOSED_LOOP_GROUP_NOT_FOUND` / `CLOSED_LOOP_GROUP_BUSY` / `timed out`）⇒ **不代运行时关闭**
- [ ] **G4.2** **不放宽任何门禁**：「没有正利润复核 ⇒ 不广播」的口径**未变**；广播 attempts 仍为 **0**（**市场条件**，无正利润复核）
- [ ] **G4.3** **不伪造**：本轮**未填任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**
- [ ] **G4.4** **不预判**：这 7 条路由**重测之后**是否有盈亏由**链上事实**决定，**不预判**

## H. 单 tick 与跨 tick 两个测量的分离发布（**2026-11-28 · `D-244`**）

### H1. 用户口径与落地

- [x] **H1.1** **用户口径**：「**单tick 如果不盈利，应该单独列出来，因为单tick 不盈利，也不需要计算跨tick 了……把跨tick 最佳交易量写成单tick 就可以，就可以判断出来是单tick没有盈利了**」＋「**无需单独考虑，但是统计的时候，要统计出来**」
- [x] **H1.2** **内核事实（只读取证）**：跨 tick 的**种子就是单 tick 最优金额**；**单 tick 最优为零 ⇒ 绝不启动跨 tick**（`cross_tick_init.rs` 的 `single_tick_best_amount_zero`）⇒「跨 tick 最佳交易量 < 单 tick 交易量」**结构上不可能**
- [x] **H1.3** **发布层（`M-13` · `review_publish.rs`）**：发布 `singleTickBestAmountRaw` ／ `singleTickProfitable` ／ `crossTickStarted` ／ `crossTickReason` ／ **`crossTickBestAmountRaw`**（**跨 tick 未启动 ＝ 单 tick 的值**）／ `crossTickBestAmountBasis` ／ `crossTickStatus` ／ `crossTickReview` ／ `crossTickSearch` ／ `searchMode`；**只发布内核已测到的事实，不发明数字**
- [x] **H1.4** **自愈器（`M-8`）**：新增 `_single_tick_paid()`（发布字段 → 选择基线 → 探针记录 → `bestAmountMeasured`；**读不到 ＝ `None`**）；`tick_amount_comparison()` 的比较对象改用**跨 tick 最佳交易量**（未启动时**就是单 tick 的值**）；**单 tick 不盈利 ⇒ 不比较**（`bestGreaterThanInitial = null`、`applicable = false`、结论 `single_tick_unprofitable_cross_tick_not_started`）
- [x] **H1.5** **统计分开计数**：`tickAmount` 区块新增 **`singleTickUnprofitable`** ／ **`crossTickNotStarted`** ／ `inspected`；`compared` 改为**真正比较过的条数**；`unknown` 只算「既不是单 tick 不盈利、又读不到数」的；`pnl_snapshot()` 同步（含日志行 `tickSingleUnprofitable=`）
- [x] **H1.6** **常驻 worker 语义查清（回答用户）**：**一个 group 一条线程**、**按需 spawn**、**空闲 ≥ 60 s 且无 stored command 会退出**（**不是**「永不退出」）；group 全集**每轮重建**；请求自带 group ⇒ 不在全集 ＝ **404**、组忙 ＝ **409**；「**注册入口**」是**按当前路由预热一条常驻线程**，不是「每请求都要注册」；manager 读失败会被**误报成 404**

### H2. 回归

- [x] **H2.1** `cargo test --offline --lib review_publish`（`flashloan/rs-src`）⇒ **22 passed / 0 failed**（新增 **2** 条）
- [x] **H2.2** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **136 passed / 0 failed**（基线 **132** ＋ 新 **4**）
- [x] **H2.3** `python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**

### H3. 部署与在产读数

- [x] **H3.1** **先停后建**：停 healer（**24988**）与运行时（**17364**）⇒ `cargo build --offline --release` **1 min 08 s** ⇒ 新 DLL `rs-src\target\release\flashloan_rs.dll`（**18 672 640 B**，09:14:31）
- [x] **H3.2** **再起**：healer 以**新代码**重启（**25308**）⇒ 运行时由 healer 拉起（**25848**，09:14:49）⇒ `/healthz` ＝ `{"status":"ok"}`；进程内加载的模块 ＝ **新 DLL**（**18 672 640 B**）
- [x] **H3.3** **在产复核行带上了两个测量**：新代次复核的 `static_preview.bestAmountCalculation` 里出现 `singleTickBestAmountRaw` ／ `singleTickProfitable` ／ `crossTickStarted` ／ `crossTickReason` ／ **`crossTickBestAmountRaw`** ／ `crossTickBestAmountBasis`（读数见 `D-244` §7）
- [x] **H3.4** **在产统计**：healer 报告的 `tickAmount` 区块给出 `singleTickUnprofitable` ／ `crossTickNotStarted` ／ `compared` ／ `bestAboveSingleTickStart` ／ `bestNotAboveSingleTickStart`（读数见 `D-244` §7）

### H4. 诚实边界（**永久留占位**）

- [ ] **H4.1** **不伪造**：本轮**未填任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**
- [ ] **H4.2** **不放宽任何门禁**：「没有正利润复核 ⇒ 不广播」的口径**未变**；广播 attempts 仍为 **0**（**市场条件**）
- [ ] **H4.3** **不预判**：重测之后某条路由是否有盈亏，由**链上事实**决定，**不预判**
- [ ] **H4.4** **不代运行时关闭**：常驻 worker 的 404／409／超时属**运行时侧**；自愈器只**如实记录并重试**

## I. 单 tick 全不盈利的成因审查与 USDG 闭环缺口（**2026-11-28 · `D-245`**）

### I1. 观察与审查读数（只读）

- [x] **I1.1** **十分钟在产观察**（`plan/runtime_evidence/reports/profit_window_20261128.json`，窗口 `01:35:41Z → 01:55:41Z`）：窗口内测得复核 **132** 条、**正利润 0**、**广播尝试 0**；每分钟最新复核均 `singleTickProfitable=false` / `crossTickStarted=false` / `crossTickReason=single_tick_best_amount_zero`；历史 7 条正利润复核均为 2026-11-26 的**陈旧代次**（`not_executable`）⇒ 广播策略 `fresh_positive_review_only` 正确拒播
- [x] **I1.2** **候选池与排序审查（0 违例）**（`audit_directional_ranking_20261128.py`）：排名组 **86**、`rank1IsTheHighestRate=true`、`rankingViolations=[]`、`ranksAreContinuous=true`；接线跳 **108**、`everyHopUsesTheRank1Pool=true`；剪枝已测价被拒池 **78**、`noRejectedPoolBeatsTheKeptOne=true`、`lostCandidates=[]` ⇒ **排序本身无错**
- [x] **I1.3** **正反两向费率自洽**：58 个双向已测价池，`rate(in→out)·rate(out→in)` 与 `(1−fee)²` 最大偏差 **0.063%** ⇒ **无方向性定价错误**
- [x] **I1.4** **x-y-z-x 与 x-z-y-x 对比（用户指定重点）**：每个已建三角形的两个朝向都建了，费率相同、边际率不同（ETH-MU-SPY −0.1752% vs −0.4941%；AMD-NVDA-SPY −0.0901% vs −1.0902%；MU-NVDA-SPY −0.8376% vs −1.8720%；ETH-NVDA-SPY −0.1538% vs −0.2032%）
- [x] **I1.5** **速率图上仅有的两条净正三角形**：`NVDA→MU→USDG→NVDA` **+1.1214%**（费率合计 2.55%）与 `NVDA→AMD→USDG→NVDA` **+0.2543%**（1.80%），**闭环覆盖 0/2** ⇒ **两条都含 USDG**（⇒ 覆盖缺口恰好吃掉了仅有的两条净正路线）
- [x] **I1.6** **USDG 本身健康**：USDG 在 `token_core` + `token_extension` 双库且 admitted；core4 作用域有 **5 个 active 池**（费率 0.24%～1.0%，deep/medium，`materialized_at = 2026-11-27T22:15:13.977846900Z`）；方向层 core4 对这 5 个池**两个方向都是 rank 1 / active**；当前方向层覆盖 core4 **24/24** 活跃交易对（**0 漏排**）⇒ **不是代币问题，也不是池子问题**
- [x] **I1.7** **断点在候选边层**：`manager_dex_candidate_anchor_tokens` 的 8 条 USDG 行 `yc/zc_primary_pool_id = NULL`；`manager_dex_candidate_pool_pairs` 的 8 条 USDG 行主池 `NULL`；`manager_dex_candidate_pool_recommendations` **USDG 行数 0**（全表 104 行）；`manager_v4_closed_loop_skipped_tokens` **8 条**（`no_completing_first_hop` × 4 ＋ `no_completing_third_hop` × 4）；36 条闭环的路径符号**只含 ETH/SPY/NVDA/MU/GOOGL/AMD，USDG 零出现**
- [x] **I1.8** **★ 因果链（物化时刻逐条对齐）**：作用域 core4 重建 `22:15:13.979` → 候选边层 `22:17:32.402` → 观察层 `22:17:32.582` → **方向层 `22:17:33.011`（晚 0.609 s）** → 闭环层 `22:18:01.477` ⇒ **候选边层与观察层读的都是上一轮的方向快照**；该顺序与自愈器写死的 `MANAGER_REBUILD_CHAIN`（candidateEdge → candidateExchange → directionalPools → closedLoops）**逐字一致**
- [x] **I1.9** **代码层证据**：`candidate_edge_pool_manager::read_directional_ranks` 与 `candidate_exchange_pool_manager::read_directional_ranks` 都从方向层读排名；`validate_expected_upstream` **只校验** `pool_scope_checksum` 与代币库版本/校验和 ⇒ **方向层落后一代无人能看见**；`manager_dex_candidate_library_state` **不记录**方向层版本 ⇒ 闭环层自检（`directional_pool_version=54` ＋ `candidate_edge_version=17` 都「当前」）为绿 ⇒ **陈旧的是「候选边层用的那份方向排名」这一派生数据，它没有版本号**

### I2. 修复与开放项

- [x] **I2.1** **重建链顺序修正（`M-8` · `scripts/mainnet_arbitrage_healer.py`）**：`MANAGER_REBUILD_CHAIN` 改为 **方向层 → 候选边层 → 候选交换层 → 闭环层**，并在注释里写明「消费方读生产方的排名，这是硬约束」；新增回归测试锁死顺序（`test_the_cascade_rebuilds_the_direction_layer_before_the_layers_that_read_it`）
- [x] **I2.2** **候选边层的时序守卫（`M-1` · `candidate_edge_pool_manager`）**：新增 `CandidateEdgeError::DirectionalLayerStale` —— 物化前读方向层状态（`manager_v4_directional_pool_library_state`），若方向层物化时刻**早于**作用域 `last_rebuilt_at` 则**写入前拒绝发布**（把静默丢失变成响亮失败）；**不引入新表/新列/新迁移**；没有方向层状态的库保持原行为
- [x] **I2.3** **生产数据修复（已执行 · `2026-11-28T10:46Z–11:02Z`）**：级联按依赖顺序落库（`directionalPools`(core4) → `candidateEdge` → `directionalPools`(candidate_exchange) → `candidateExchange` → `closedLoops`，全部 HTTP 200，`plan/runtime_evidence/reports/cascade_apply_d245_20261128b.json`）：候选边层 **v17 → v18 / 推荐 104 → 128（+24）**、闭环 **v72 → v73 / 36 → 48 条 / 跳 108 → 144**；**USDG 回到闭环目录（12 条 active）**，两条净正三角形已在目录内；剩余 9 条 USDG 跳过记录变成真实市场事实（`no_reachable_v4_pool` / `no_completing_middle_direction`）⇒ **USDG 缺口已收口**
- [ ] **I2.4** **内核复核给出两条净正三角形的真实盈亏**：+1.1214% / +0.2543% 只是**边际速率读数**，必须由内核复核实测后判定 ⇒ **不预判**；**现状**：代次 73 已复核 21 条（`missingValue 0` / `positive 0`），**27 条闭环尚无本代次复核**（含全部 12 条 USDG 闭环），自愈器正在逐条实测
- [x] **I2.5** **同源第二面：`candidate_exchange` 作用域的方向层早于它的作用域**（version 50，生成于 `2026-11-27T15:07:56Z` < 作用域 `22:16:02`）⇒ **本轮已重建（v50 → v51，changedRows 232）**：不重建它，观察视图 `manager_market_observation_yz_pairs` 按 `scope_key` 关联不到有效排名 ⇒ `optimal_pool_id` 取空 ⇒ 闭环层以 `POOL_INVALID` 拒绝发布；该作用域原有的 **5 个「有池无排名」**是否随之归零 ⇒ **待复核**
- [ ] **I2.6** **4 个只读审计/校验脚本因迁移 018 之后的表结构失配而失败**（`verify_optimal_pool_view_20261125.py`、`verify_task150_pool_set_20261121.py`、`audit_directional_hop_resolution_20261125.py`、`audit_directional_invariants_20261125.py`：`no such column: direction_key / pool_ids_json / base_pool_id`）⇒ 需按当前表结构重写，**不为了通过校验而改表**

- [ ] **I2.7** **★ 写锁占用（开放项 · 第八轮再次取证，根治仍未做）**：manager 的写接口整片 `503 database is locked`（每层等满 60 s busy timeout 才失败）⇒ 自愈器在运行时忙时**写不进去**；第八轮探针读数：短预算（`busy_timeout=500 ms`，40 次）**40 / 40 全锁**；长预算（30 s）一次等 **33 s** 仍超时、一次等 **7.255 s** 才拿到 ⇒ 锁是**长事务**持有，不是短写风暴；**锁 ＋ 检查点对齐探针**（90 次 / 1 s，`plan/runtime_evidence/tools/probe_write_lock_with_checkpoint_20261128.py`）**89 / 90 锁、0 次检查点**（90 s 内主库文件大小不变、WAL 恒为 8.75 MB）⇒ **排除「检查点阻塞」**，坐实**长事务**；采样窗口内**只有运行时在跑**（自愈器只发 `runtime_busy` 体检、没有修复周期）⇒ **持锁者是运行时自己的扫描**（其扫描自身也报 `database is locked`，`flow=opportunity_scan`）；大对象表本身很小（`v4_exchange_pools` **2 550** 行、`dex_candidate_pools` **26**、`manager_v4_directional_exchange_pools` **464**、`manager_v4_closed_loop_hops` **144**）⇒ 长事务**不是「行多」造成的**，而是**链上测量被放进了写事务**；**本轮已做**：自愈器对管理器**全部写调用**改用 `api_post_transient`（5xx / 429 / 传输失败**有界重试 3 次**），锁超时不再被读成「复核没有盈亏数值」；**根治未做**（测量移出写事务 / 热路径权威搬出大对象库 / 大对象级联分块写 —— 见 `D-245` §11.6）
- [x] **I2.9** **★ 读路径不得写库（本轮新发现并修复）**：`closed_loop_state_machine.ensure_runtime_state_table` 被 **25** 处调用（本文件 ＋ `closed_loop_object.py` 3 处），其中至少 **5** 处落在**纯读路径**（`load_error_isolation` / `scope_error_isolation_active` / `_resolve_directed_loop_key` / `load_loop_state` / `closed_loop_object.py::load`）；旧实现每次调用都开 `BEGIN IMMEDIATE` 写事务（建表 DDL + 补列）⇒ **读一次状态就写一次库**：既与运行时自己的长事务抢写锁，又在锁被占时让**读取本身**失败（运行日志 `closed-loop runtime state sync failed: database is locked` ⇒ 状态同步整片失败 ⇒ 闭环状态写不进去）。改为**先读后写**：列齐了不写、缺列才 `ALTER` ⇒ 读路径零写事务；回归 `flashloan/app_runtime/tests/unit/test_closed_loop_state_machine.py` ⇒ **41 passed / 0**
- [x] **I2.8** **重建链扩为 5 层（`M-8` · `scripts/mainnet_arbitrage_healer.py`）**：链条由 4 层扩为 5 层 —— `directionalPools(core4)` → `candidateEdge` → **`directionalPools(candidate_exchange)`** → `candidateExchange` → `closedLoops`；因为观察视图 `manager_market_observation_yz_pairs` 按 `scope_key` 关联**该作用域自己的**方向层，而 `MANAGER_REBUILD_CHAIN` 原先只重建 core4 的方向层 ⇒ 候选交换作用域的方向层一旦陈旧，闭环层就以 `POOL_INVALID` 拒绝发布（2026-11-28 落库时实测到的 422）；链条项改为 `(name, path, scope_key)` 三元组，回归新增 `test_the_cascade_rebuilds_the_direction_layer_of_the_observation_scope` ⇒ **138 passed / 0**（基线 **137** ＋ 新 **1**）；级联工具 `plan/runtime_evidence/tools/apply_cascade_in_dependency_order_20261128.py` 同步改为 5 层

      - [x] **I2.10** **★ 评审占用必须能归还（本轮新发现并修复）**：自愈器的顺序是**先占用、再请求**（`POST /v1/closed-loops/review-cycle` → `POST /api/dex/closed-loop-groups/manual-review`），占用把闭环**停在哨兵** `REVIEW_PENDING_SENTINEL_AT = 2036-06-06T06:06:00+00:00`；而 `advance_review_clocks_on_connection` 的 WHERE **跳过两个哨兵**、`begin_review_cycle_on_connection` 对哨兵闭环直接 `return Ok(false)`（「不是到期，也不是被认领」）⇒ **只有测量落地（`review_manager::run_impl` → `record_review_schedules`）才解除停车**；一轮没跑起来的占用就把该闭环**判死刑**（复核停在旧代次，门禁永远读不到它），且**每一轮修复都会再停几条**（实测 12:57 **6** → 13:20 **9** → 13:35 **4**；13:19–13:20 一轮就停了 **4** 条 `claimed=True`，其中 3 条的初始复核是**超时**、1 条是 404 且 worker 注册也失败）；**本轮已做**：① 运行时**明确拒绝**（404 且 worker 注册失败）⇒ 轮次确定没开始 ⇒ **立刻归还**占用（`release_review_claim`）；② 占用超过 **45 分钟**（`REVIEW_CLAIM_STALE_SECONDS`，远长于一轮复核的 ≈ 24 分钟）⇒ **过期归还**（`release_abandoned_review_claims`），闭环重新到期；③ 释放前**干跑一次认领**读回「轮次开始时刻」做**比较交换**，只有属于自己的（或已过期的）占用才释放 ⇒ 不打断正在跑的轮次；**未放宽任何门禁**（释放只是让「永远不会被测量的闭环」重新到期）；回归新增 3 个测试 ⇒ **144 passed / 0**（见 `D-245` §12）

- [x] **I2.11** **★ 占用不得改写复核时刻；队列是动态的，读了要再读（本轮新发现并修复）**：用户口径「**这是非常严重的错误，因为队列是动态刷新的……每次循环到一个时间未到的闭环（其中也包括哨兵时刻），都会重新刷新一次队列……必须重新开始，从队列头部开始循环。**」⇒ 占用把闭环的**最新复核时间**整个顶成哨兵（干跑实测：`nextReviewAt = 2036-06-06T06:06:00+00:00` / `reviewWaitSeconds = 300495681` ≈ 9.5 年），而时钟推进跳过哨兵、认领拒绝哨兵闭环、到期集合排除哨兵 ⇒ **一轮没跑起来的占用就把这条闭环判死刑**；**本轮已做**：① `begin_review_cycle_on_connection` **保留原时刻**（缺失或是哨兵才写 `now`），**忙状态由 `runtime_status` 承担**；② `advance_review_clock_on_connection` 只推进 `cooldown`（**不清掉占用**）；③ `ReviewQueue::walk` **报停点**（**只有 `not_due` 一个原因**：哨兵（暂时不需要复核，复核时间非常长）就是「复核时刻未到」的最极端情形，另用 `walkStoppedAtIsSentinel` **描述**），**忙碌跳过但不停**（不饿死后面的到期条目），`needs_refresh()` ⇒ 消费方**重读队列、从队头重来**；④ `manager-api` 的 `review-due` 增 `walkStoppedAt` / `walkStoppedReason` / `queueNeedsRefresh` / `restartFromHead`，`review-cycle` 答复增 `runtimeStatus`；⑤ 自愈器 `walk_review_queue`（停了 ⇒ 再读一次，有界 `REVIEW_QUEUE_REFRESH_LIMIT = 3`）＋ 占用归还**一律按状态**判定；⑥ **手动复核**把时刻写成 `now` ⇒ 队列重读后**从队头**就取得到它；回归：`closed-loop-manager` **79** / `manager-api` **35** / `review-manager` **178** / healer **149**（见 `D-245` §13·§14）
- [x] **I2.12** **★ 部署闸门（本轮闭合）**：release 重建 → 停旧（pid 4544）→ 替换（旧件备份 `.bak-20261128T075207Z`）→ 起新（pid **5428**，**07:52:07Z**，SHA256 `C083471B…`）⇒ I2.11 的修复**已在产**：在产验证 `GET /v1/closed-loops/review-due?chainId=4663` ⇒ `walkStoppedReason=not_due` / `walkStoppedAtIsSentinel=true` / `queueNeedsRefresh=true` / `restartFromHead=true`；`POST /v1/closed-loops/review-cycle` 干跑答复含 `runtimeStatus`；**在产证据：占用不再改写复核时刻**（07:31 的四条认领保留了原时刻 06:53:57 / 07:09:52）；**旧哨兵行仍在哨兵上**（4 条，认领时刻均早于 07:27:06Z）⇒ 由**过期归还**放回（陈旧窗口 45 分钟）；**本轮不手工写生产库**
- [x] **I2.12b** **★ 自愈器重启闸门（本轮闭合）**：在产的自愈器（pid **8124**）仍是**第九轮**代码 ⇒ 它的占用归还靠**哨兵判断**（`nextReviewAt == REVIEW_PENDING_SENTINEL_AT`）⇒ 在新二进制下**不生效**；**本轮已重启**：pid **24212**，代码为本轮（占用归还靠**状态**判定），带的参数与旧进程一致（`--mode supervise --apply --interval 60 --pnl-interval-seconds 300 --observation-hours 6 --leave-runtime-running`）
- [ ] **I2.13** **★ 同源第二面：`broadcast_pending` 的闭环仍停在广播哨兵上**：广播回执**终态**会把它覆盖回 `cooldown`（`D-224` S5a），但**回执永不到达**时它同样会被卡住（与 I2.11 同类的墓碑）⇒ **本轮未修**，登记为开放项

      ### I3. 回归

- [x] **I3.1** `cargo test --offline -p candidate-edge-pool-manager`（`flashloan/tools`）⇒ **17 passed / 0 failed**（基线 **15** ＋ 新 **2**：方向层早于作用域 ⇒ 拒绝且不写库／方向层晚于作用域 ⇒ 正常发布）
      - [x] **I3.2** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **147 passed / 0 failed**（基线 **144** ＋ 新 **3**：队列停点字段透传、停了要**重读队列从队头重来**、没停就只读一次）；另修掉一个**时间炸弹式**旧用例（硬编码时刻跨过 45 分钟陈旧窗口后变成「过期占用」）⇒ 改用**相对时刻**
- [x] **I3.5** `cargo test -p closed-loop-manager -p manager-api -p review-manager`（`flashloan/tools`）⇒ **78 / 35 / 178 passed · 0 failed**（`closed-loop-manager` 改写 2 个旧用例 ＋ 新 5：`a_claim_keeps_the_review_moment_of_the_loop` / `the_walk_says_where_it_stopped` / `a_parked_loop_stops_the_walk` / `a_busy_loop_does_not_stop_the_walk` / `a_loop_whose_claim_is_given_back_is_due_again_at_once` / `the_clock_advance_does_not_clear_a_claim`；`manager-api` 新 2：端点报停点 ＋ 哨兵闭环报 `parked`）
- [x] **I3.4** `python -m pytest flashloan/app_runtime/tests/unit/test_closed_loop_state_machine.py -q` ⇒ **41 passed / 0 failed**（读路径不再写库：`ensure_runtime_state_table` 改为先读后写）
- [x] **I3.3** `python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**

### I4. 诚实边界（**永久留占位**）

- [ ] **I4.1** **不伪造**：本轮**未填任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**
- [ ] **I4.2** **不放宽任何门禁**：广播 attempts 仍为 **0**（**市场条件**，无正利润复核）；「持续产生链上广播交易」**仍未达成**
- [ ] **I4.3** **不预判**：新闭环与两条净正三角形是否有盈亏由**链上事实**决定，**不预判**
- [ ] **I4.4** **不代运行时关闭（★ 本轮已更正归因）**：常驻 worker 仍需运行时**按当前路由重新注册** —— 规模已量化：管理器 **48** 条 active 闭环里 **21 条没有常驻 worker**（含**全部 12 条 USDG 闭环**）；**原归因（运行时的闭环目录从它自己的表构建）不成立** —— 运行时自己的 `v4_closed_loops` 里 USDG 行 **0**，运行时却已有活着的 USDG worker ⇒ **worker 注册读的是管理器的闭环视图**（`manager_view("closed_loops")` → `manager_v4_closed_loops`），与运行时自己的陈旧表无关（详见 `D-245` §7.8）；**真实成因**是提交爬坡 + 扫描周期 + 忙时刷新被拒 ⇒ **已由 I4.5 修复并投产**；本条保留的是**运行时自己那张陈旧表**（`v4_closed_loops` **32** 行 / `2026-11-27T00:39:35Z`、`dex_candidate_pool_recommendations` **76** 行、`market_observation_yz_pairs` **142** 行）与运行时同步口 `POST /api/dex-pools/candidates/refresh?full=1&dry_run=false` 的 **403**（候选边层写入被运行时侧权限拒绝）⇒ 仍需运行时侧处理，**不代运行时关闭**

## J. 广播策略改为正向盈利直发（**2026-11-28 · `D-262`**）

### J1. 缺陷取证（**只读 · 代码自证 · `D-262` §2**）

- [x] **J1.1** **★ 旧门禁量的不是「测量有多旧」**：广播层 `AdmissionRule::ReviewFresh`（码 `BC-1.review_fresh`）的判据是「**当前时刻 `<` 闭环的下一次复核时刻**」⇒ 它回答的是「闭环到期了没」，**测量有多旧从未被量过**
- [x] **J1.2** **★ 双向都判错**：闭环到期（**待测的常态**）⇒ **刚测完**的复核被判「不是当前测量」；闭环停在广播哨兵（2035-05-05）⇒ `now < scheduled` 恒成立 ⇒ **两天前的复核也算「新鲜」**
- [x] **J1.3** **★ 复核→广播是同一线程的一次迭代**：链上测量 → `review_publish_block` → `wake_broadcast_with_review_proof` → 签名 → `eth_sendRawTransaction`；**中间没有任何时刻比较** ⇒ 「复核结束到广播开始」在物理上**不可能过时**
- [x] **J1.4** **在产 7 条正利润复核被拒的真实原因**：它们的闭环**已离开目录**（`manager_v4_closed_loop_runtime_state` 的 `next_review_at = null` ＋ `runtime_status = null`）⇒ **与新鲜度无关**

### J2. 修复（三层 ＋ 编排器 · `D-262` §3）

- [x] **J2.1** **复核层（`M-6`）**：`review_is_current_for_automatic_broadcast(reviewed_at, round, published)` → **`review_stamp_is_usable(reviewed_at)`**（只判「时刻**可读**」且「**不在未来**」）；`read_loop_review_moments` ／ `LoopReviewMoments` ／ `build_review_rows` 的 `loop_moments` 参数整条删除；请求生成条件**不变**（`completed` ＋ 正 PnL ＋ 三跳价格限 ＋ 三跳确认池 ＋ 时刻可用）
- [x] **J2.2** **广播层（`M-7`）**：规则码 `BC-1.review_fresh` → **`BC-1.review_clock_consistent`**；**删掉** `now < loop_scheduled_at` 这个比较；闭环的下一次复核时刻随决策记录为**描述字段** `loop_scheduled_at`（**不再拒绝发送**）；**保留**「未来时刻」的拒绝（时钟不一致 ≠ 测量）
- [x] **J2.3** **运行时直发路径（`M-13`）**：`worker_pool::wake_broadcast_with_review_proof` 删掉 `loop_review_moment` 的两个拒绝分支（「闭环没有复核时刻」＋「闭环已到时刻」）；该值保留为**描述字段** `loopScheduledAt`；**保留**的拒绝：`run_ok` ／ `run_executable` ／ `completed` ／ 正盈亏 ／ 三条价格限 ／ **路线必须交易复核确认的池**
- [x] **J2.4** **编排器（`M-8`）**：策略标签 `fresh_positive_review_only` → **`positive_profit_direct`**；`automaticBroadcastCandidates` 由**硬编码 0** 改为**真实读数**（带可执行判元的正利润复核数）；原因串去掉 “fresh”
- [x] **J2.5** **★ 同轮修复：运行时忙不再饿死级联**：`busy → continue` 跳过整轮 ⇒ 新增 `run_management_writes()` ／ `busy_management_cycle()`，**忙的分支也跑** `release_abandoned_review_claims` ／ `advance_review_clocks` ／ `walk_review_queue`（日志事件 `management_cycle`）；忙轮次**计入样本**（修 `--max-samples` 死循环）
- [x] **J2.6** **★ 同轮修复：忙看门狗改为看「库有没有动」**：新增 `review_progress_marker()`（管理器各闭环 `lastReviewedAt` 的最大值）⇒ **库在动** ⇒ 忙窗口重新计时、**不重启**；**库不动**且一直不回答 ⇒ 才重启；`runtime_busy` 事件带 `reviewProgressAt`
- [x] **J2.7** **★ 报告口径归正（「新鲜度」措辞清零）**：报告头 `successCriteria` 的 `continuousFreshReviews` ／ `broadcastOnlyFromFreshProfitableReview` → `continuousCurrentReviews` ／ **`broadcastOnlyFromProfitableReview`**；`fill_missing_reviews()` 的 `policy` 串「only fresh positive review may trigger broadcast」→「**a positive profitable review triggers a broadcast as it is**」；复核层的**本轮覆盖**判据 `review_freshness_by_loop` ／ `currentLoopFreshness` ／ `reviewFreshnessBasis` ／ `reviewNotCurrentLoops` ／ `basis = loop_review_time_point` → `review_coverage_by_loop` ／ `currentLoopCoverage` ／ `reviewCoverageBasis` ／ `loopsWithoutCurrentMeasurement` ／ `basis = loop_review_round`（**该判据不是门禁**：它答的是「这一轮有没有测到这条闭环」，**不量年龄**、**不读任何间隔**，**不进任何广播判断**）

### J3. 回归与部署（`D-262` §5）

- [x] **J3.1** `cargo test --offline -p broadcast-manager` ⇒ **44 passed / 0 failed**（含改写的 N-6 ＋ 新增的「未来时刻仍被拒」）
- [x] **J3.2** `cargo test --offline -p review-manager` ⇒ **178 passed / 0 failed**（含改写的「当前轮次」用例）
- [x] **J3.3** `cargo test --offline`（`flashloan/rs-src`）⇒ **284 passed / 0 failed**（含改写的两个直发用例）
- [x] **J3.4** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **152 passed / 0 failed**（基线 **149** ＋ 新 **3**：无新鲜度窗口 ／ 忙时不饿死管理写入 ／ 看门狗看库不看沉默）
- [x] **J3.5** **部署闸门（已闭合）**：新 `manager-api`（pid **27124**，**16:44:47**）＋ 新扩展模块 `flashloan_rs.dll`（**16:44:41** 重建）＋ 新自愈器（pid **22936**，**16:44:47**）**同批上线** ⇒ **新旧无中间态**
- [x] **J3.6** **在产首个自愈轮次（`09:03:50Z`）**：`broadcastPolicy = **positive_profit_direct**`（新策略已在产）；动作 `repair_reviews` ／ `repair_stale_generation_reviews` ／ `fill_current_loop_reviews` ／ `rereview_historical_positive_candidates` ／ `observe_automatic_broadcast`；`manualBroadcastInvoked = false`；生产态 `review_stalled` ＋ `executablePositiveReviews = 0` ＋ `transactionRecords = 0` ＋ `loopsWithoutCurrentMeasurement = 25`（**48 条闭环里 25 条本轮尚未测到**）⇒ 报红的原因是**本轮还没测完**，**不是**「利润过时」；**二进制自证**：`manager-api.exe` 命中 `BC-1.review_clock_consistent`、**不再命中** `BC-1.review_fresh`；`flashloan_rs.dll` 命中 `loopScheduledAt`、**不再命中**旧拒绝串「no longer the current measurement of the loop」
- [x] **J3.7** **在产忙分支与看门狗（`09:14:38Z` – `09:15:26Z`）**：新自愈器（pid **8300**，**09:13:57Z** 启动）**接管**在产运行时（pid **12864**，`runtime_adopted`）⇒ `runtime_busy` 事件带 **`reviewProgressAt = 09:13:34Z`**（**库在动** ⇒ 忙窗口重新计时、**不重启**）＋ **`management_cycle` 事件落地**（`releasedClaims = {ok, released 0}` ／ `reviewClocks = {ok, status 200, dueCount 7, attempts 1}` ／ `dueQueue = {count 47, dueCount 47, waitingCount 1, restartFromHead true}`）⇒ **忙的时候三个管理写入照样跑**（旧行为：整轮跳过）

### J4. 诚实边界（**永久留占位**）

- [ ] **J4.1** **不伪造**：本轮**未填任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**
- [ ] **J4.2** **不放宽任何门禁**：只删了「新鲜度」这一道**本来就不应该存在**的门；其余门禁**一条不少**（复核完成 ＋ 正盈亏 ＋ 三跳价格限 ＋ 三跳确认池 ＋ `BC-2` 链/账户/额度/窗口 ＋ 路线必须交易复核确认的池）
- [ ] **J4.3** **广播 attempts 仍为 0**（**市场条件**）：库里 `completed` 的复核是 **0** 条，那 **7** 条正利润复核的状态是 `not_executable` ⇒ **本轮改动不会凭空产生广播**；它改变的是「一旦出现一条『完成 ＋ 可执行 ＋ 正盈利』的复核，就**立即**发出广播指令」
- [ ] **J4.4** **不代运行时关闭**：写锁根治（`I2.7`）与热路径搬库**未实施**；`broadcast_pending` 同源墓碑（`I2.13`）**未修**；**19 GB** 主库体积构成**未查明**（不 `VACUUM`、不删文件）

## K. 复核状态语义归正（**2026-11-28 · `D-263`**）

### K1. 缺陷取证（**只读 · 代码自证 · `D-263` §2**）

- [x] **K1.1** **★ `ok=false` 被当成「测量失败」**：复核层 `build_review_rows` 的 `Some(run) if !run.ok` 一支**不看 `executable`**、**不看回滚原因** ⇒ 「**可成交但亏钱**」与「**跑不了**」被并成 `not_executable`，理由串写「`runArbitrage returned ok=false`」（读起来像测量失败）
- [x] **K1.2** **★ 在产读数（`manager_v4_reviews`，只读对表）**：`not_executable` **150**（含 **7** 条正盈亏）／ `data_incomplete` **26** ／ **`completed` 0**；理由码 `runArbitrage returned ok=false` **128**（其中 **126** 条是 `ok=false ∧ executable=true`）／ `executable=false` **15** ／ `signed PnL is missing or incomplete` **13** ／ `loop_options_unavailable:hop_N` **7**
- [x] **K1.3** **★ 样本原文（实测）**：最新一条 `run_arbitrage_json` ＝ `{"ok":false,"executable":true,"error":"NoProfitableRoute","revertSelector":"0x16301234","noProfitableRoute":true,"arbitrageNotProfitable":{received<required},"signedPnl":-5.54791623303329e-4 SPY}` ⇒ **路线可成交**、合约回滚说了「不赚钱」、并回传了**确切亏损**
- [x] **K1.4** **★ 阶梯量过**：126 条里 **121** 条 `bestAmountMeasured=1` ／ `probeCount` **1–21** ／ `bestAmountLadderStatus=not_profitable_after_costs`；只有 **5** 条 `single_tick_capacity_below_min_probe`（阶梯确实没跑起来）
- [x] **K1.5** **★ 后果**：①「持续观察统计套利交易 盈亏情况」读不出「测到了什么」（统计说「128 条不可成交」，事实是「128 条成交了、钱没回来」）；② 编排器以为这 128 条**测量坏了**，去追一个**根本没坏**的测量；③ 广播门禁的输入被自己判没了（库里 `completed` ＝ **0** 条）

### K2. 修复（复核层 ＋ 编排器 · `D-263` §3）

- [x] **K2.1** **复核层（`M-6`）**：新增 `rejection_is_profitability_verdict` ／ `rejection_kind` ／ `identity_gate_reason`；`¬ok ∧ executable ∧ 盈利判定` ⇒ **`completed` ＋ `measured_not_profitable:<kind>`**（⇒ 页面积分类 `confirmed_no_profit`，**盈亏统计第一次读得出「测到了什么」**）
- [x] **K2.2** **★ 阶梯闸门在新支上依然生效**：`¬ok ∧ executable ∧ 盈利判定` ＋ 阶梯没量 ⇒ `data_incomplete` ＋ `ladder_not_measured:*`（**量级不足仍是量级不足**，不得冒充「量过了」）
- [x] **K2.3** **★ 矛盾情形 fail-closed**：`¬ok ∧ executable ∧ 盈利判定` ＋ 实测为**正** ⇒ `not_executable` ＋ `measured_profit_contradicts_contract_verdict`（合约说不赚钱、阶梯说赚钱 ⇒ 复核无法裁定 ⇒ 复测，**不得**进广播请求）
- [x] **K2.4** **★ 身份闸门优先且只判一次**：`hop_without_options` ／ `pool_outside_options` 两段重复 `format!` 收成 `identity_gate_reason()`，**先于**盈利判定支生效（路线不是闭环 ⇒ 无论钱怎样都不得确认）
- [x] **K2.5** **★ 两条回退支的理由串归正**：`¬ok ∧ ¬executable` ⇒ `runArbitrage rejected the route and reported it as not executable:<kind>`；`ok ∧ ¬executable` ⇒ `runArbitrage reported the route as not executable`（旧的 `runArbitrage returned ok=false` **把两类事实并成了一类**，措辞也读起来像测量失败）
- [x] **K2.6** **★ 不新增状态值**：`review_status` 的取值集合被数据库 CHECK 约束、API 参数校验、页面积分类、广播准入事实、界面读面、Python 侧统计**共同依赖**（`completed / data_incomplete / not_executable / failed`）⇒ 本轮用**既有词汇**表达新语义（`completed` ＝ 测到了；`data_incomplete` ＝ 没测到；`not_executable` ＝ 跑不了）
- [x] **K2.7** **★ 广播门禁一条没松**：复核层生成广播请求的条件**未变**（`completed` ＋ 正盈亏 ＋ 三跳价格限 ＋ 三跳确认池 ＋ 时刻可用），而盈利判定这一支的实测值**必为负** ⇒ **不会**产生广播请求；广播层 `BC-1.run_ok` **仍在**（`ok=false` 的运行永远过不了准入）；`review_round_outcome` 只在「`completed` ＋ 正盈亏」时停驻闭环 ⇒ 本轮这些行**照旧**排队复核
- [x] **K2.8** **编排器（`M-8`）**：新增 `review_status_counts()`（`completed` ／ `completedWithProfit` ／ `completedNoProfit` ／ **`measuredNotProfitable`** ／ `not_executable` ／ `data_incomplete` ／ `failed`），挂在 `layers.reviews.manager.statusCounts`，随 `pnl_snapshot` 的 `reviews.statusCounts` 落进观察文件 ⇒ **「测到了多少 ／ 赚了多少 ／ 亏了多少」三件事分开读**

### K3. 回归与部署（`D-263` §5）

- [x] **K3.1** `cargo test --offline -p review-manager` ⇒ **182 passed / 0 failed**（基线 **178** ＋ 新 **4**：盈利判定＋实测亏损 ⇒ `completed` ／ 盈利判定＋阶梯没量 ⇒ `data_incomplete` ／ 非盈利判定的回滚 ⇒ 仍 `not_executable` ／ 合约没跑成 ⇒ `not_executable`）
- [x] **K3.2** `cargo test --offline --workspace`（`flashloan/tools`）⇒ **14 个套件全绿 / 0 failed**（含 `review-manager` **182**、`manager-api` **35**、`closed-loop-manager` **79**、`broadcast-manager` **44**）
- [x] **K3.3** `python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **153 passed / 0 failed**（基线 **152** ＋ 新 **1**）
- [x] **K3.4** `python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**
- [x] **K3.5** **★ 部署闸门（三件同批 · 新旧无中间态）**：新 `manager-api` ＋ 新扩展模块 `flashloan_rs.dll` ＋ 新自愈器**同批上线**。**★ 部署范围的一处更正**：复核行**不只**由 `manager-api` 落库 —— 运行时的 `review_publish.rs` 在发布测量时**进程内**调用 `review_manager::run_reviews_write`，而扩展模块**内嵌**复核库 crate ⇒ **运行中的运行时仍在用旧代码写复核行**（实测：部署后仍出现 `updated_at = 09:52:32Z` ＋ 旧理由串 `runArbitrage returned ok=false` 的行）⇒ 必须**重建扩展 ＋ 重启运行时**
- [x] **K3.6** **在产读数**：见 `D-263` §5.2（**2026-11-28 18:49 实测**）—— `completed` **0 → 28**（同一语句的一次快照），这 28 条**全部**是 `measured_not_profitable:NoProfitableRoute`、**全部带确切的负盈亏数值**（`signed_pnl_source = native_rust_ladder`，`-3.26e-2` ～ `-6.18e-15`）、**一条都不缺数值**；`not_executable` **121**（其中 **106** 条是 **2026-11-26** 的旧理由串存量行，新代码**一次也没再写过**旧串）、`data_incomplete` **27**；**广播请求仍为 0**（被改判的行实测值全为负 ⇒ 不生成广播请求）

### K4. 诚实边界（**永久留占位**）

- [ ] **K4.1** **不伪造**：本轮**未填任何盈亏**、**未填 0**、**未删任何历史复核行**、**未改任何历史代次**、**未手工写生产库**
- [ ] **K4.2** **不放宽任何门禁**：只把「**可成交但不盈利**」从「**不可成交**」里**分出来**；广播准入的每一道（`completed` ＋ 正盈亏 ＋ 三跳价格限 ＋ 三跳确认池 ＋ `BC-1.run_ok` ＋ `BC-2` 链/账户/额度/窗口 ＋ 路线必须交易复核确认的池）**都在原位**
- [ ] **K4.3** **广播 attempts 仍为 0**（**市场条件**）：被改判的那些行实测值**全为负** ⇒ 本轮改动**不会**凭空产生广播
- [ ] **K4.4** **那 7 条正盈亏复核的状态未变**：它们的阻断理由是**闭环目录没有给那一跳发布池选项**（`loop_options_unavailable:hop_N`，**身份问题**），与本轮的语义归正**无关**；该缺口另有登记（`D-228`）
- [ ] **K4.5** **不代运行时关闭**：写锁根治（`I2.7`）、热路径搬库、`broadcast_pending` 同源墓碑（`I2.13`）、**19 GB** 主库体积构成**均未实施**
