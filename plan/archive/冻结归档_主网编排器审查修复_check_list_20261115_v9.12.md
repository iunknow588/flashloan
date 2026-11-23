# 主网编排器审查修复 Checklist v9.13（2026-11-15 归档纪律收口版）

> 本 checklist 与 `未完成任务清单_20261113.md` 一一对应，**只保留待验收项**（全部为未勾选状态）。
> 已完成项及其证据路径见 `plan/back/已完成任务归档索引_20261114_v9.8.md`；归档前完整正文见 `plan/back/冻结归档_*_20261114_v9.8.md` 与 `plan/back/冻结归档_*_20261114_v9.12.md`。
> 完成一项后：先写机器证据 JSON 与人工摘要 Markdown，再登记到归档索引，最后从本文件删除对应条目。

## 0. 架构与口径硬约束

- 权威业务逻辑位于对应 Rust manager；Python 只通过 RuntimeCore/Manager API 间接工作。
- 精确数学复用 Rust `pool_manager` / Uniswap SDK；版本 1 每跳只允许 single-tick，跨 tick 接口仅保留。
- fixed fee 范围为 `0 < fee <= 30000`；动态费率池在所有池角色中排除。
- 候选排序为 normalized single-tick 精确输出降序 → 方向 tick 顺序 → fee → pool id。
- USDG 在经济计价上替代 USD，但外部 fiat 价格源和 TVL 不得进入池深准入。
- 股票代币受 USDG 可兑换性约束；核心/测试仅在 token 分类层豁免部分估值门禁，但测试库不得因此进入候选、观察、闭环或广播；扩展代币只做完整性与传递关系审计。
- 成熟 SDK 可复用，但一个生产结论只能有一个 Rust authority；Python 不得成为第二 authority。
- 当前版本只处理 single-tick；跨 tick 仅保留接口与后续版本任务，不得进入候选、复核、广播或生产闭环。
- scope 契约：`core4 = [core, stocks]`、`candidate_exchange = [core, stocks]`；扩展库与测试库不得进入主路径。
- 测试代币允许保留在数据库和内部启动校验中，但不得展示在 UI，也不得进入候选、观察、闭环、统计或广播；除 schema/启动完整性或主路径污染外不作为专项优化对象。
- 无显式授权不得写生产库、切换 Authority、广播或替换被锁定二进制。
- 数据分层（2026-11-14 用户裁决）：token 与 pool 基础数据是长期权威事实；
  review / submission / broadcast 记录是可再生瞬时运行记录，不具备长期统计意义。
- 不得为 review / submission / broadcast 记录扩展长期持久化 schema；
  新字段必须先证明其支撑 token/pool 基础结构正确性。
- 瞬时记录清理必须走管理器清理接口（`review_manager` / `broadcast_manager`），
  不得由 Python 直接写业务库；必须支持 dry-run、幂等键与精确 scope 匹配。
- 清理不得触碰 token 目录、pool 目录、准入状态与 scope 状态。

## 1. P0 低风险收口（剩余尾项）

### 1.1 候选排序副本物化（生产存量，P0-2）

- [ ] 在生产库显式授权写入前完成备份、SHA-256、WAL 一致性检查和回滚点记录。
- [ ] 将生产旧 label `direction_tick_ordering_then_fee_pool_id` 迁移为 `direction_single_tick_exact_output_then_tick_fee_pool_id`。
- [ ] 重建候选、推荐、观察、闭环消费者，验证 TOP-5/TOP-1、排序方向、checksum 和幂等性。
- [ ] 证明生产旧 label 计数为 0，且不存在新 label 混用。
- [ ] 复跑 P0-2 差异报告，确认旧副本比较差异全部得到解释。

### 1.2 单 Tick 最佳交易量与池状态突变算法（P0-8）

> 2026-11-15 归档纪律收口：本 checklist 已移除全部完成项，只保留未勾选门禁；已完成项见 `plan/back/已完成任务归档索引_20261114_v9.8.md` 的 P0-8a/P0-8b/P0-8c 行。
> 依据：`docs/关于Robinhood/单tick最佳交易量计算说明.md`、
> `docs/关于Robinhood/V4单Tick套利池子状态变化处理与算法优化.md`。
> 已完成的数学口径折算与代码接线见归档索引 P0-8 行。
> 2026-11-15（第 4 轮）新增：**主网自愈编排器的广播修复与复核收敛已补齐**。用户固定的自愈顺序为：资金代币 → 候选代币 → 闭环 → 复核（必须有明确盈亏）→ 广播（错误必须修复）→ 持续观察统计。审查结论：代币修复、池子修复、闭环重建、复核重跑四项已有实现；缺口是 **(a) 广播层此前完全跳过，无任何广播错误修复**，**(b) 复核缺盈亏只做单轮重跑，没有"直到每个复核都有明确盈亏"的收敛**。本轮补齐：广播失败六类分类器（池状态缺失/池状态过期/交易被拒/RPC 不可达/无利润/未知）、广播修复器（被拒交易经广播管理器终结；**无利润是市场结果，永不修复**；pending 永不触碰；未知只报告）、复核有界收敛器（**停滞检测**，停滞报 `converged=false, stalled=true`）、`healing_actions` 与 supervisor 接入。专项测试 15 项全通过。主网只读实测：工具部署缺口已修复（重建二进制 + 写 source stamp，`pool-manager` 校验转 `valid=True`），`productionState` 由 `tooling_deployment_gap` 推进为 `pool_scope_drift`；dry-run repair 按 tooling→reviewIndexes→depthSchema→poolDepth→databaseBaseline→tokens→pools→broadcast 顺序完整跑通；数据库 SHA-256 前后一致，全程只读。**仍未授权**：生产库写入、广播、Authority 切换、主网 bounded executor 部署。
> 2026-11-15（第 5 轮）新增：**主网 scope 漂移根因定位完成，自愈死锁已修复**。主网 healer `check` 的生产状态为 `pool_scope_drift`，漂移层 `manager_v4_closed_loop_library_state`。逐层核对确认：**闭环 hop 健康**——318 hop 的 65 个 distinct pool 全部命中 `v4_exchange_pools_candidate_exchange`，`missingPoolHopCount=0`、`affectedLoopCount=0`；缺陷是闭环库记录的 `pool_scope_checksum` 属于 `candidate_exchange`（`8ceb9e02...`）而它所属 scope 是 `core4`（`5434a25c...`），生产库闭环库于 2026-11-09 物化、早于 Rust `closed_loop_pool_scope_checksum` 修复落地。scope 策略同样陈旧（库中 `core4=[core]`、`candidate_exchange=[core,stocks,extension,test]`，源码目标均为 `[core, stocks]`）。**死锁修复**：闭环重建触发条件扩展为 `consumers_need_rebuild>0 || 闭环层 invalid || 闭环层 scope 漂移`（触发源 `pool_scope_drift`）；`healing_actions` 不再把闭环层漂移误判为池修复；新增 `closed_loop_scope_drift()` 区分两类漂移。**另修复 dry-run 假失败**：`ensure-indexes`/`ensure-depth-schema` 无 `--apply` 时的设计性非零退出不再被读成 tool failure（新增 `tool_entry_failed()`）。主网 dry-run：core4 重物化为 `[core, stocks]`（eligible 2022）、candidate_exchange 重物化为 `[core, stocks]`（eligible 2073）、闭环以 `trigger=pool_scope_drift` 重建（`changedRows=1146`，写入正确 core4 checksum）、广播层 `candidateCount=0`；数据库 SHA-256 前后一致，全程只读。新增 8 个测试（文件共 23 个），定向套件 90 passed。**未执行 `--apply`**：未部署、未广播、未切换 Authority、未写生产库、未替换锁定 release 二进制。
> 2026-11-14（第 2 轮）新增勾选项：折叠 `M_cap` 贯通（Manager 持久化 → 复核门禁 → 广播载荷 → 运行时）、复核层三态执行门禁、状态变好/变坏稳定原因码。
> 机器证据：`plan/runtime_evidence/reports/p0_8_capacity_and_transition_gate_20261114.json`、
> `plan/runtime_evidence/reports/p0_8_bounded_v4_flash_batch_20261115.json`。

#### 1.2.1 单 Tick 池状态突变三态处理

- [ ] 链上只做常数次减法、乘法和除法；比例分子/分母由链下 O(1) 计算并写入 calldata。
  - 链下算法已就绪；链上比例 calldata 尚未实现。
- [ ] 最终 Swap 保留 `amountOutMinimum`，利润不足时整笔回滚。
  - **路线级 reviewed minimum output 已实现并回归通过**：Rust 使用复核 `selectedKeys` / `selectedDirections` / `outTb` / `outTa` / `outTz` 与三跳限价自动构造 `executorRequest`，PoolManager 地址从 pool 基础目录按 pool id 同源解析，不扩展瞬时 review/broadcast schema。合约比较本次路线的**增量输出** `balanceAfter >= balanceBefore + minAmountOut`，旧余额不得补贴劣化成交；事件字段为 `minAmountOut`。**逐跳下限仍未实现**：Uniswap V4 原生 `swap` 无 `amountOutMinimum` 参数，逐跳下限需要新 ABI；主网 Executor 仅余 25 字节。
- [ ] 状态变化证据至少包含旧/新 `sqrtPriceX96`、Tick 边界、判定分支、比例分子/分母、放大前后容量、最终 `amountOutMinimum` 与失败码。
  - 复核诊断现已包含旧/新 `sqrtPriceX96`、旧/新精确输出、Tick 边界、价格限制、旧/当前/候选容量、容量口径、精确比例分子/分母、倍率与失败码；路线级最终 `minAmountOut=outTz` 已进入自动构造的 executor 请求。逐跳输出下限仍待补齐，且比例分子/分母尚未写入主网合约 calldata。

#### 1.2.2 代码接线与回归

- [ ] 完整生产物化前发布每跳 tick 状态与 `single_tick_max_token0_raw` / `single_tick_max_token1_raw`，并完成重物化 dry-run 影响评估。
- [ ] 合约实现前后执行状态突变回放：跨 Tick、变坏、变好、流动性骤降、比例极端值、`amountOutMinimum` 不足、批内单闭环失败隔离。
  - 批内单闭环失败隔离已在 Executor 单元层证明（失败项不回滚成功项）；链上回放仍需在部署后的 bounded executor 上执行。
- [ ] 池状态变化导致容量放大时，复核按最终输入量重新计算，不复用旧利润。
  - 复核按最终输入量逐 hop 重算已实现；状态变好后的容量放大与实际最终输入量联动仍需端到端验证。
- [ ] 单 Tick 单元测试、三跳闭环测试、链上/链下 parity、状态变好/变坏/跨 Tick 回归全部通过。
  - 单 tick、三跳闭环、状态变好/变坏/跨 tick、逐跳限价与链上当前状态方向校验已通过；主网只读 parity 已执行，但当前瞬时 review 已按数据分层原则清理，结果为 `routeCount=0`。空证据现以 `no_routes` fail-closed；仍需从 token/pool 基础事实与当次实时复核生成非持久化 parity 输入。

#### 1.2.4 复核权威源与观察纪元（第 6 轮补齐的验收口径）

- [ ] `manager_v4_reviews` 非零物化，且 healer 从**权威库**读复核（`review_source=manager`）。
  - 已实现：`review_rows` 优先读 `manager_v4_reviews`，权威库为空时才回退 legacy。
  - 当前主网权威库 0 条、legacy 2 条，`review_library_not_materialized`。
- [ ] 每条复核在权威库与 healer 报告中都能解析出明确的盈亏数值；缺值仍报告为缺失，绝不读成 0。
  - 已实现：`pnl()` 同时支持 `selected_profit_amount` 与 `signed_pnl_amount`，
    并可回落到 `run_arbitrage_json.signedPnl.amount`。
- [ ] 复核物化请求携带 `expectedUpstream`（闭环/候选边/观察层版本与 checksum、池 scope checksum）。
  - 已实现：`materialize_reviews()` 经 `/v1/reviews/run` 发送；Review Manager 会拒绝上游不匹配的复核。
- [ ] 观察纪元从 `plan/runtime_evidence/observation_epoch/mainnet_current.json` 读取，
      `observationEpochFallback=false`。
  - 已修复（原 healer 仍指向已迁移的 `logs/plan/`，纪元永久回退到整表窗口）。
- [ ] `pool-manager baseline-audit` 报告精确的 `missingMigrations` 与 `appliedMigrations`。
  - 已实现；主网缺 3 个（007/008/009），已应用 7、未知 0。

#### 1.2.3 主网 scope 漂移与闭环库重建（P0-8-L，需显式生产写入授权）

> 复核证据门禁已在第 7 轮收口：手工复核异步 ACK 不算证据，只有完整 `signedPnl` 的真实结果
> 才能进入 Review Manager；缺值不得补零。机器证据：
> `plan/runtime_evidence/reports/p0_8_async_review_evidence_gate_20261115.json`。

- [ ] `pool_scope_config` 中 `core4` 与 `candidate_exchange` 的 `library_keys_json` 均为 `["core","stocks"]`。
  - 当前库中为 `core4=["core"]`、`candidate_exchange=["core","stocks","extension","test"]`；
    Rust 源码目标已是 `[core, stocks]`，`baseline-audit` 已报
    `core_scope_library_policy_stale` / `candidate_scope_library_policy_stale`。
- [ ] `manager_v4_closed_loop_library_state.pool_scope_checksum` 等于 **core4 scope** 的 checksum。
  - 当前为 `sha256:8ceb9e02...dfc1`（错误，属于 `candidate_exchange`）；
    正确值应为 `sha256:5434a25c...c9926`。dry-run 已证明当前二进制会写入正确值。
- [ ] `manager_v4_closed_loop_library_state.consumers_need_rebuild` 归零，`pool_scope_drift` 消失。
  - dry-run 闭环重建 `changedRows=1146`；生产写入未授权。
- [ ] 闭环 hop 全部命中候选集与观察集，`missingPoolHopCount=0`、`affectedLoopCount=0`。
  - 当前已满足（65 个 distinct hop pool 全部命中 `v4_exchange_pools_candidate_exchange`）。
- [ ] `manager_v4_reviews` 非零物化，且**每个复核都带明确盈亏数值**；缺失者经复核管理工具修正，
      不得用估算值或零值代替。
  - 当前 manager review 0 条、legacy 2 条，`review_library_not_materialized`。
- [ ] 自动广播交易出现错误时经广播交易工具修正；`no_profit` 与 `pending` 不修。
  - 分类器与修复器已实现并通过离线测试；主网尚无广播记录可验证。
- [ ] 持续观察统计套利交易盈亏，确认主网可稳定产生套利广播交易。

#### 1.2.5 测试网闭环复核往返验证（P0-8o，2026-11-15）

> 门禁已通过：基础条件具备 → 测试网完整复核与提交 → 切回主网只读复验 → 网络隔离成立。
> 全程未发送链上交易。机器证据：
> `plan/runtime_evidence/reports/testnet_review_roundtrip_20261115.json`。

- [ ] 测试网基础条件：`robinhood-testnet/46630` 的 DB `local_store_46630.sqlite3`、
      RPC `https://rpc.testnet.chain.robinhood.com/rpc` 可用，且存在**带链上确认流动性**的
      三跳闭环（`ETH -> PLTR -> NFLX -> ETH`），三个池 `liquidityErrorCount=0`。
  - 已满足：`liquiditySource=pool_manager_extsload_live_state_batch`，
    `scannedPoolCount=3`、`blockNumber=153449191`。
- [ ] 闭环刷新返回 HTTP 200 且 `ok=true`，`submittedGroupKeys` 含目标 group，
      `failures` 为空。
  - 已满足：`POST /api/dex/closed-loop-groups/eth%7Cpltr%7Cnflx/refresh` →
    `groupKey=eth|pltr|nflx`、`groupsConsidered=1`、`failures=[]`。
- [ ] 复核提交返回 HTTP 200，且 `action=trigger_immediate_review`、
      `transitionQueued=true`、`waitingDeadlineCleared=true`——手工复核必须**立即执行**，
      不得只延长等待截止时间。
  - 已满足：`mode=native_closed_loop_group_manual_review`、`dataSource=rust_runtime_core`。
- [ ] 每条复核都解析出**明确盈亏数值**；缺值必须报缺失，绝不读成 0。
  - 已满足：`selected_profit_raw=0x1f333ae117e8e`、
    `selected_profit_amount=0.000548878265974414`、`symbol=ETH`、
    `source=native_rust_contract_preview`、`final_delta_amount=0.000548878265974414`。
- [ ] 广播关闭时复核完成后**不得请求链上执行**，终态为 `execution_not_requested`，
      不得误报 `broadcast_result_missing`。
  - 已满足：`state=execution_not_requested`、`last_error=execution_not_requested`。
- [ ] 切回主网后 runtime 使用主网 scope 与主网 DB，`last_error=null`，
      不得残留测试网 scope。
  - 已满足：scope 仅 `robinhood|4663|uniswap_v4`，maintenance DB 仅
    `local_store_4663.sqlite3`，`auto_execute_enabled=false`。
- [ ] **结构与数据分离**验证：两网 DB 文件独立、表 schema 完全一致、业务行均带
      `network/chain_id` 且为单一 scope，跨链行计数为 0。
  - 已满足：`v4_exchange_pools` / `v4_exchange_pool_liquidity_snapshots` /
    `v4_closed_loops` / `v4_closed_loop_hops` 全部单 scope，跨链行 0。
- [ ] 规模性缺陷已消除：47k 池批量删除不再触发 SQLite 参数上限；
      实时流动性行 `observed_at` 非空。
  - 已满足：`evidence.rs` 按 500 分批删除；`import_existing_json.py` 优先取
    `observedAt/liquidityReadAt/liquidityScanAt/generatedAt`。
- [ ] 回归：目标 `pytest` 与 `cargo test --offline` 全部通过，Rust 扩展已重建。
  - 已满足：`pytest` 138 passed；`cargo test --offline` 127 passed / 0 failed；
    `maturin develop --release` 已执行。
- [ ] `composite_manager` 去除 chain `4663` 硬编码，测试网 Manager 全工具链可用；
      测试网 Manager 权威表按需物化。
  - **工具链侧已完成**（2026-11-15 第 15 轮）：`composite_manager` CLI 改为
    `network_for_chain_id()` 推导 scope（未知 chain id 显式拒绝）；`token_manager`
    `materialize-libraries` 新增 `--network`/`--chain-id`，读取/写库/迁移回执全部按 scope
    参数化；`build_library_migration_receipt` 不再硬编码主网 scope；测试网
    `ClassificationPolicy` 补齐 core4 核心地址；`value-tokens` 按 registry 自身 scope 选链。
  - **测试网实测**：副本上 `manager-db migrate`（10 迁移，全为纯 DDL）→
    `token-manager materialize-libraries`（`migrationReceipt.valid=true`，core 4 个 admitted）→
    `pool-manager materialize-scope core4/candidate_exchange` →
    `composite-manager materialize-arbitrage --chain-id 46630` 全部命中
    `robinhood-testnet/46630`，链门禁解除；`--chain-id 1` 被正确拒绝。
  - **未完成**：测试网 5 只股票代币缺 USDG 估值（Trading API 不服务 chain 46630 →
    `api_error` → unknown，fail-closed 未误判为负值），导致 scope 内 0 个可用池，
    闭环物化无法进行。属**数据缺口**，非工具链缺口。
  - 机器证据：`plan/runtime_evidence/reports/testnet_manager_toolchain_20261115.json`。
  - 本轮完成的是 legacy SQLite + RuntimeCore/native worker 闭环复核链路 +
    Manager **工具链**测试网可用；**不得宣称 Manager 全链迁移完成**。

### 1.3 主网数据库启动基线与库完整性（P0-9）

- [ ] 携带持久 USDG 估值库或受控报告，在生产一致性副本重跑 `materialize-libraries` dry-run；历史源 6,353 行、缺 decimals 拒绝 1,735 行必须稳定复现。
- [ ] 历史源 → 标准化 token → 四库 membership → pool scope → 候选/推荐/观察/闭环 lineage receipt 完整，逐层包含源版本、行数、拒绝原因、checksum 和工具版本。
- [ ] 194 只股票不再全部 `pending:not_measured`；`migrationReceipt.valid=true`，每个库 `projectedConsistentWithSource=true`，并保留行数、状态分布、metadata checksum、缺失/多余/身份冲突 token。
- [ ] dry-run 的 `projectedTarget` 与授权 apply 后实际目标库一致；任何 `projectedConsistentWithSource=false` 必须 fail-closed。
- [ ] 显式授权后执行 `manager-db migrate`，应用 `20261112_007_usdg_valuation_schema`（当前生产库该表**不存在**）。
- [ ] 显式授权后执行 `pool-manager ensure-depth-schema --apply`，补齐 `capacity_raw`、`capacity_binding_raw`、`single_tick_max_token0_raw`、`single_tick_max_token1_raw`；uint128 TEXT 不得再经 SQLite INTEGER/Rust i64 截断。
- [ ] 显式授权后运行 token-manager 处理 TB/TA 未锚定扩展成员，并重建 `core4 = [core, stocks]` 与 `candidate_exchange = [core, stocks]` scope。
- [ ] 扩展库完成完整性审计但不进入版本 1 候选主路径；扩展/测试成员在物化层和 Manager API 查询层均被阻断。
- [ ] 重建候选、推荐、观察、Manager 闭环消费者；记录候选池、推荐、观察池、闭环/hop 的 row count、checksum、版本和变化原因。
- [ ] Manager 闭环全部恰好 3 hops，全部 pool 引用命中 active candidate scope；legacy 88 条旧目录及 2 条 TZ/TB/TA 旧闭环不得进入运行时/UI/复核/统计/广播。
- [ ] UI 代币、候选池、观察池、闭环和统计视图对测试库成员返回 0 展示结果；数据库仍可保留测试库行。
- [ ] UI 隐藏测试代币依赖库角色过滤而非删除数据；主网、测试网和历史快照的测试数据完整性保持不变。
- [ ] 测试网数据库 `integrity_check=ok`，原始 `chain_tokens`/`v4_exchange_pools` 保持完整；不因 UI 展示需求创建额外测试代币物化任务。
- [ ] 重跑 baseline-audit，除生产授权/数据修复项外不得出现其他 issue。
- [ ] `back/阶段性设计与审计_20261114/p0_9_database_baseline_adr_20261114.md` 已补写生产执行结果、回滚点、最终 checksum 与收敛证据路径。

## 2. P1 生产池库收敛（授权）

### 2.1 备份与影响评估

- [ ] 用户显式授权 production prune。
- [ ] 生产 DB 备份、hash、schema、行数、空间记录完成。
- [ ] 回滚脚本保存并在副本真实演练。

### 2.2 Production prune

- [ ] 子表优先 prune 完成。
- [ ] `overCeiling=0`、动态费率池=0、orphan=0。

### 2.3 推荐/候选/观察/闭环重物化

- [ ] pool scope 重新物化完成（`core4 = [core, stocks]`、`candidate_exchange = [core, stocks]`）。
- [ ] candidate edge/exchange 重新物化完成。
- [ ] `selection_basis=direction_single_tick_exact_output_then_tick_fee_pool_id`。
- [ ] 幂等重跑 `changedRows=0`。
- [ ] 准入失败原因可区分 `token_library_out_of_scope`；扩展库/测试库不得进入候选 exchange。
- [ ] 测试代币保留在数据库时，UI 默认列表、搜索结果、聚合计数和导出均不展示测试库成员。
- [ ] 三跳闭环恰好三跳，hop 引用全部命中 active candidate pool。
- [ ] 推荐池是候选池的确定性子集；观察池、候选池和闭环使用同一状态版本，不存在跨快照拼接。
- [ ] 每个消费者的 row count、checksum、版本、耗时和失败项均记录。

### 2.4 USDG 估值生产落地

- [ ] 生产迁移完成后落库 194 只股票代币估值，并按 membership TTL 重新物化核心/股票/扩展/测试库。
- [ ] 生产 `token_stocks` 的 admitted/blocked/pending 分布与持久估值证据一致，不再沿用“194 全 admitted”的旧状态。
- [ ] 估值刷新编排满足 membership 刷新间隔 < 24 h、magnitude 刷新间隔 < 1 h。
- [ ] `scripts/refresh_robinhood_token_libraries.py` 按 TTL 触发刷新并输出过期报告。
- [ ] `flashloan/src/dex/tools/band_robinhood_candidate_usdg_capacity.py` 读取估值时执行 magnitude TTL 门禁。

## 3. ARCH-2b / D4-d

- [ ] 剩余偏差逐条归因，无伪造零差异。
- [ ] D4-d `unit_profit` 切到 Rust 精确口径。
- [ ] 切换前后收益/gas/fee/single-tick bound 可回溯。
- [ ] `best_amount_ladder_incomplete` fail-closed 未放宽。
- [ ] `eth_call` 回退开关与演练通过。
- [ ] 最终复核使用实际输入量验证每个 hop 不跨 tick。

## 4. ARCH-3 链上保护

- [ ] 合约传入 expected price/state version。
- [ ] 主参数/降级参数/revert 分档完成。
- [ ] V4 `sqrtPriceLimitX96` 正确使用。
- [ ] 状态陈旧 fail-closed 测试通过。
- [ ] 降级数组顺序、上限和回放证据完成。

## 5. ARCH-4-B/C

### 5.1 影子旁路

- [ ] 内存主算、`eth_call` 影子。
- [ ] 连续 7 天无不可归因差异。
- [ ] 一键回切及演练通过。

### 5.2 纯内存

- [ ] 累计观测至少 30 天。
- [ ] executable 不一致=0。
- [ ] amountOut 不可归因差异=0。
- [ ] 异常台账清零。
- [ ] ARCH-3 已上线。
- [ ] 内存状态新鲜度门禁生效。
- [ ] Multicall 补数据验证。
- [ ] 手动回切保留至少一个发布周期。

## 6. W6 生产铺开

- [ ] 用户显式授权 W6 apply。
- [ ] schema/index/dry-run/apply 报告齐全。
- [ ] `depthSnapshot.stale=false`。
- [ ] `pool_scope_drift` 消除。
- [ ] `probeRecords>=2`。
- [ ] `ladderIncomplete=0`。
- [ ] `best_amount_missing` 解除。
- [ ] `bestAmountBacktrace` 非 unresolved。
- [ ] `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED=1`。
- [ ] capacity unknown 升级为阻断。
- [ ] 未测量 hop 清单和补测路径完成。

## 7. W7 闭环重建

- [ ] 停机窗口批准。
- [ ] cleanup / refresh-key / scope apply / composite apply 成功。
- [ ] 闭环 100% 命中候选集。
- [ ] hop 100% 命中候选集。
- [ ] 四方 `pool_scope_checksum` 一致。
- [ ] `missingPoolHopCount=0`。
- [ ] `consumers_need_rebuild=0`。
- [ ] single-tick bound / capacity gate 生效。
- [ ] 闭环全部 hop 的最终输入量 single-tick 证明可回溯。
- [ ] 未引用 TVL、`market_depth_exhausted`、相关性或 `li<1` 下架规则。

## 8. W8 Authority 与广播

- [ ] M0-P migration。
- [ ] M1-P token authority。
- [ ] M2-P pool authority。
- [ ] M3-P edge authority。
- [ ] M4-P exchange authority。
- [ ] M5-P closed-loop authority。
- [ ] M6-P review authority。
- [ ] M7-P broadcast authority。
- [ ] M8-P composite authority。
- [ ] `manager_v4_reviews` 非零物化。
- [ ] legacy 只读交叉校验，未双写。
- [ ] `reviewSources.sourcesDisagree=false`。
- [ ] 连续 3 个 ≥30 分钟观察窗口通过。
- [ ] `safeToAutoBroadcast` 判定依据明确。
- [ ] GET/command API/UI/fail-closed/架构守卫回归通过。
- [ ] 完成 RuntimeCore/Manager API 最终调用链审计，确认 Python 未保留 token/pool/route/review/broadcast 权威判断。
- [ ] legacy 仅作只读交叉校验，未长期双写；架构守卫可阻止新增 Python 权威业务逻辑。

## 9. 池生命周期、WSS 与上限

- [ ] 备用池三日活跃清理开关默认关闭。
- [ ] 正式运行且数据量达到配置阈值后，每 3 个自然日的当日 24:00 调度验证。
- [ ] 候选池/推荐池不参与 TTL 清理。
- [ ] 动态费率池未进入备用/候选/推荐角色。
- [ ] 清理配置含默认关闭开关和数据量启用阈值。
- [ ] 清理配置含 3 日窗口、Asia/Shanghai 时区、单次最大删除量和 dry-run/apply。
- [ ] 候选/推荐刷新与备用清理使用同一 pool state version，竞态测试通过。
- [ ] WSS 缺失时清理 fail-closed 停用。
- [ ] 监听→状态→活跃→晋升/降级/淘汰一体化验证。
- [ ] RPC 总速率 ≤15 req/s；manager 目标 8 req/s、batch 50。
- [ ] 生产池表总量 ≤3000。
- [ ] 超限淘汰规则确定性、角色保护和幂等性验证。

## 9.5 瞬时记录清理与数据分层（P0-10）

> 依据：开发计划 §2.4 数据分层原则。
> 已完成基线及机器证据：
> `plan/runtime_evidence/reports/p0_10_review_cleanup_and_catalog_preservation_20261114.json`。
> 已完成内容不在本 checklist 保留完成勾选；以下只列剩余待验收门禁。

- [ ] 后续新增 review/submission/broadcast 字段时，先在评审记录中证明其支撑
  token/pool 基础结构正确性；否则不得进入长期持久化 schema。
- [ ] 架构守卫覆盖：Python 不得直接清理业务库，清理必须通过管理器接口，
  且 token/pool 目录与其他 network/chain scope 保持不变。

## 10. 长期统计与运维

- [ ] token/route/window expected/realized/gas/success/failed 统计完成。
- [ ] `pnl_*.jsonl` 长期视图完成。
- [ ] 快照新鲜度、工具版本、容量实测门禁常开。
- [ ] 连续观察期未依据单次快照下架 token 对。
- [ ] `flashloan/tools/target/release/manager-api.exe` 停机替换完成。
- [ ] 守护器与 `flashloan/app_runtime/run.py` 重启后 health/version/页面验证。

## 11. 回归与文件格式

- [ ] healer 测试达到当前基线。
  - `test_mainnet_arbitrage_healer_repair_chain.py` 现有 23 项；定向套件 90 passed。
- [ ] binary-source-match 测试和脚本退出码 0。
- [ ] capacity + pool-index 合并套件达到当前基线。
- [ ] `app_runtime/tests/unit` 既有失败集合未扩大。
- [ ] `mainnet_arbitrage_healer --mode check` 全程只读。
- [ ] `cargo test --locked` 和 release 构建通过。
- [ ] SDK 升级附 parity、性能、许可证和回滚差异报告。
- [ ] `flashloan/app_runtime` 架构守卫阻止新增 Python 权威业务逻辑。
- [ ] 计划与证据文件统一 UTF-8 无 BOM、LF 换行、JSON `sort_keys=true` / `ensure_ascii=false`。

## 12. 生产写入最终确认

- [ ] 命令明确显示目标为生产库。
- [ ] 用户授权文本已记录。
- [ ] 备份与回滚点已验证。
- [ ] 写入期间 RPC/worker/authority 状态可控。
- [ ] 写入后 checksum/row-count/orphan/幂等验证完成。
