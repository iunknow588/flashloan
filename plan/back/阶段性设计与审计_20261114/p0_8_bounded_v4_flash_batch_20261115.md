# P0-8 多盈利闭环单交易批处理与批内失败隔离（2026-11-15）

## 1. 审计结论

用户提出的“广播管理器一次把所有有正向盈利预期的闭环打包调用”在此前**不成立**：

- `broadcast_manager` 的写入接口确实接受多个 `attempts`；
- 但 Rust `worker_pool` 在 `run_native_review` 中只保留 `best_route`，只构造单条 `runArbitrageBestPoolsWithSelectedAmount` 交易；
- 每条闭环各发一笔独立交易，既不是一次交易，也没有批内失败隔离。

## 2. 本轮实现

### 2.1 合约层（主网 + 测试网同构）

`RobinhoodPausedFlashLoanExecutor` 新增：

- `requestV4FlashLoanBatch(V4FlashAccountingRequest[])`：一次交易打包多个 V4 flash 闭环；
- `MAX_BATCH_REQUESTS = 16`：单批上限，超过后由管理器分片；
- `executeV4FlashLoanBatchItem(V4FlashAccountingRequest)`：仅 self 可调用，外部调用拒绝；
- 每项通过合约自调用形成独立回滚边界：失败项只记录 revert selector，不回滚整批；
- `RobinhoodV4FlashBatchItemResult` / `RobinhoodV4FlashBatchCompleted` 事件；
- **增量利润门禁**：每项余额必须独立覆盖自身 `minProfit`，禁止前序闭环利润补贴后续不盈利闭环。

### 2.2 Rust 层

- `execution::build_v4_flash_batch_calldata`：批次 ABI 编码，与 ethers 编码向量逐字节一致；
- `execution::broadcast_v4_flash_batch_inner`：批次交易签名与广播；
- `worker_pool::eligible_executor_batch_request`：只收集已通过 gas / capacity / economic floor / 逐 hop 限价门禁的正收益闭环；
- 批次分支默认关闭，仅当 `executorBatchEnabled=true` 且
  `executorBatchRequests` 非空并与 `executorBatchEligibleRouteCount` 一致时才启用；
- 单闭环广播路径完全保持原样。

## 3. 验证

| 项 | 结果 |
|---|---|
| 主网 Executor 套件 | 29 passing |
| 测试网 Executor 套件 | 27 passing |
| rs-src | 116 passed / 0 failed |
| 批次 ABI 向量 | 与 ethers 逐字节一致 |
| 主网部署字节码 | 24,454 / 24,576 字节 |
| 测试网部署字节码 | 24,331 / 24,576 字节 |

## 4. 安全边界

- 未部署合约、未切换 Authority、未广播、未写生产库、未替换锁定二进制；
- 批次功能默认关闭，生产保持单闭环路径；
- EIP-170 上限触发后已移除无消费价值的返回数组并复用 `BadFlashCallback` 作为 only-self 错误。

## 5. 剩余门禁

1. 主网 bounded executor 部署与批次入口接线（需显式授权）。
2. 复核侧为每条正收益闭环构造 `executorRequest`。
3. 主网 live parity 与同源数学精确链上三态分类。
4. 批次广播在生产开启前的端到端回放。

## 6. 证据

`plan/runtime_evidence/reports/p0_8_bounded_v4_flash_batch_20261115.json`

## 7. 归档纪律收口（2026-11-15 追加）

本轮结束复核发现：活动文档（`未完成任务清单_20261113.md`、`主网编排器审查修复_check_list_20261113.md`）仍保留 20 条历史 `[x]` 完成项，与 `plan/back/已完成任务归档索引_20261114_v9.8.md` §5 归档纪律第 1 条冲突（完成项必须移入索引，不得留在活动文档）。

收口动作：

- 将 20 条完成项补登记为归档索引 `P0-8b`、`P0-8c` 两行（容量折算与三态门禁、逐跳限价与链上当前状态校验）；
- 生成 `plan/back/冻结归档_未完成任务清单_20261114_v9.12.md` 与 `plan/back/冻结归档_主网编排器审查修复_check_list_20261114_v9.12.md` 正文快照；
- 活动清单与 checklist 升级为 v9.13，`[x]` 计数归零，只保留未完成门禁；
- `README.md` §11 归档规则新增第 5 条：每轮结束复核 `[x]` 计数必须为 0。

证据：`plan/runtime_evidence/reports/p0_8_archive_discipline_closure_20261115.json`

## 8. Reviewed minimum output 利润锁（2026-11-15 追加）

### 8.1 审计结论

checklist 的「最终 Swap 保留 `amountOutMinimum`，利润不足时整笔回滚」此前**完全没有实现**：

- Uniswap V4 的原生 `swap` **没有** `amountOutMinimum` 参数，只有 `sqrtPriceLimitX96`；
- 直通 PoolManager 路由的每一跳都走原生 `swap`，链上没有任何输出下限；
- Rust 与复核侧也从未构造过任何 `amountOutMinimum` 载荷；
- 唯一的兜底是余额门禁（`balance >= requiredRepay + minProfit`），它约束的是**利润**，不是**成交价**。

因此一条「按更差价格成交但仍然盈利」的路线会通过门禁并按更差价格结算。

### 8.2 EIP-170 预算约束

主网 Executor 的部署字节码余量只有 **122 字节**。实测：

| 方案 | 字节数 | 结论 |
|---|---|---|
| 在 `V4FlashAccountingRequest` 上加 `uint256` 字段 | 24,677 | 超限 101 字节 |
| 在 `V4FlashAccountingRequest` 上加 `uint256[]` 字段 | 24,779 | 超限 203 字节 |
| 两个结构体都加 `uint256` 字段 | 24,852 | 超限 276 字节 |
| 新增外部入口（`uint256` 参数） | 24,687 | 超限 111 字节 |
| 逐跳下限（calldata 尾随 32 字节） | 24,576 | **正好等于上限，余量 0** |
| 路线级下限（复用既有 `gasReserve` 字段） | 24,500 | **余量 76 字节** |

热路径结构体 `V4FlashAccountingRequest` 的 ABI 编码器是字节数爆炸的主因：任何新增字段都会同时放大编码器与解码器。

### 8.3 实现

采用**路线级 reviewed minimum output**：

- 复核侧为每条正收益闭环给出「本次最终余额的最小可接受值」（raw units）；
- 该值经请求的第七个字传入，该字段已由 `gasReserve` 改名为 `minAmountOut`（ABI 布局不变，字节兼容）。V4 记账路径此前**从未强制**旧字段——gas 是沉没成本，余额判定只用 `minProfit`，该字段仅被回显进事件——因此复用它**不改变任何既有语义**，也不需要改动 ABI 布局；
- 合约在 V4 记账回调里、利润门禁之前、结算之前比较最终余额与该下限，低于下限则 `BadFlashRepayment` 回滚；
- 事件 `RobinhoodProfitGuardChecked` 的第五个字段由 `gasReserve` 改名为 `minAmountOut`，使新语义显式可查；
- Rust 侧 `reviewed_min_amount_out` 门禁：缺字段、为 0、无法解析的路线一律不进入 executor batch。

### 8.4 边界

- **逐跳下限仍未实现**：逐跳需要新 ABI，实测无预算余量。路线级下限是当前预算内能达到的最强约束。
- 未部署、未切换 Authority、未广播、未写生产库。
- Morpho 路径不受影响（该路径不经过 V4 记账回调），其事件上报 `minAmountOut = 0`。

### 8.5 验证

| 项 | 结果 |
|---|---|
| 主网 Executor 套件 | 32 passing（新增 3 项） |
| 测试网 Executor 套件 | 30 passing（新增 3 项） |
| rs-src | 118 passed / 0 failed（新增 2 项） |
| tools workspace | 退出码 0 |
| 主网部署字节码 | 24,500 / 24,576 字节（余量 76） |
| 测试网部署字节码 | 24,377 / 24,576 字节（余量 199） |

证据：`plan/runtime_evidence/reports/p0_8_reviewed_minimum_output_profit_lock_20261115.json`

## 9. `executorRequest` 自动构造与增量输出反补贴（2026-11-15 追加）

### 9.1 审计结论

批次编码器与广播函数虽然已经存在，但生产代码此前只从 route config / route payload **读取**
`executorRequest`；全仓库没有生产路径构造 `swapTargets` 与 `swapCalldatas`。因此批次分支即使开启，
也无法把真实的正收益复核结果转换为 bounded executor 请求。

同时，路线级 `minAmountOut` 原实现比较 Executor 的**总余额**。若 Executor 已持有借款代币，旧余额
可能掩盖低于复核输出的成交，形成历史余额补贴。

### 9.2 实现

- Rust 从成功复核结果读取 `selectedKeys`、`selectedDirections`、`selectedAmountRaw`、`outTb`、
  `outTa`、`outTz` 与三跳 `hopSqrtPriceLimits`；
- 按 `[selectedAmountRaw, outTb, outTa]` 确定性编码三笔 PoolManager exact-input `swap` calldata；
- 校验三跳 token 连续且首尾回到借款 token；
- 由三只 PoolKey 计算 pool id，并从 pool 基础目录解析共同的 `pool_manager`；三只池不属于同一
  PoolManager 时 fail-closed；
- 自动生成 `executorRequest`，其中 `minAmountOut = outTz`；缺任一复核输出、限价、PoolManager
  或闭环连续性时不进入 executor batch；
- 合约比较 `balanceAfter >= balanceBefore + minAmountOut`，旧余额不得补贴本次劣化成交；
- 不新增 review/submission/broadcast 持久化字段，瞬时请求由 pool 基础目录与当次复核结果随时再生。

### 9.3 验证与边界

| 项 | 结果 |
|---|---|
| 主网 Executor 套件 | 33 passing |
| 测试网 Executor 套件 | 31 passing |
| rs-src | 121 passed / 0 failed |
| tools workspace | 退出码 0 |
| 主网部署字节码 | 24,551 / 24,576 字节（余量 25） |
| 测试网部署字节码 | 24,428 / 24,576 字节（余量 148） |

仍未部署、未切换 Authority、未广播、未写生产库；`executorBatchEnabled` 仍默认关闭。
逐跳输出下限仍未实现，且主网 Executor 只剩 25 字节，后续合约变更必须重新实测 EIP-170。

证据：`plan/runtime_evidence/reports/p0_8_executor_request_autobuild_20261115.json`

## 10. 批次请求完整性 fail-closed（2026-11-15 追加）

### 10.1 审计结论

旧逻辑用成功构造的 `executorBatchRequests.len()` 直接生成
`executorBatchEligibleRouteCount`。若某条已经通过 gas、single-tick capacity、economic floor 与逐跳限价
门禁的正收益路线，在构造 `executorRequest` 时因缺少复核输出或 PoolManager 失败，该路线会被静默忽略；
剩余请求仍可满足「eligible count 等于 request count」，从而广播不完整批次。

这不符合「一次性打包所有通过执行门禁的正收益闭环」的完整性要求。成功请求数量不能自证没有遗漏。

### 10.2 实现

- `eligible_executor_batch_request` 改为 `Result<Option<Value>, String>`：未通过执行门禁返回
  `Ok(None)`，通过门禁且构造成功返回 `Ok(Some(request))`，通过门禁但构造失败返回稳定错误；
- 新增 `executorBatchGatedRouteCount`、`executorBatchBuildFailureCount` 与
  `executorBatchBuildFailures`，构造失败证据包含路线序号、符号、地址与错误码；
- `validated_executor_batch_requests` 统一校验失败计数、失败数组、gated/eligible/request 三者一致性
  与非空请求；任一构造失败或计数不一致均拒绝整个批次；
- 直接广播与 deferred execution queue 共用该完整性校验，禁止任一入口发送部分批次；
- 自动构造时允许从已通过门禁的 route evidence 补入 `hopSqrtPriceLimits`，避免门禁已证明但
  builder 因配置层级不同误报缺失；
- 不扩展 review/submission/broadcast 长期 schema，全部字段只属于当次可再生运行载荷。

### 10.3 稳定失败语义

| 错误 | 含义 |
|---|---|
| `executor_request_missing_selected_keys` | 缺少三跳 PoolKey |
| `executor_request_pool_manager_unavailable` | 无法从配置或 pool 基础目录解析共同 PoolManager |
| `executor_request_build_failed:<detail>` | 请求编码或闭环连续性校验失败 |
| `executor_request_reviewed_minimum_output_unavailable` | 缺少可证明的路线级最小输出 |

### 10.4 验证与边界

| 项 | 结果 |
|---|---|
| rs-src | 122 passed / 0 failed |
| tools workspace | 退出码 0 |
| 部分请求成功、另一条构造失败 | 整批拒绝 |
| deferred execution queue | 与直接广播使用同一完整性校验 |
| 持久化 schema | 无新增 |

本轮未修改合约，因此未重复执行 Hardhat 与 EIP-170 字节码测量；上一轮主网部署字节码仍为
24,551 / 24,576 字节（余量 25）。仍未部署、未切换 Authority、未广播、未写生产库，且批次功能
继续默认关闭。

证据：`plan/runtime_evidence/reports/p0_8_batch_completeness_fail_closed_20261115.json`

## 11. 同源精确三态证据可复算化（2026-11-15 追加）

### 11.1 缺口

三态分类已经使用 `pool_manager::depth::single_tick_swap_amount_out` 对相同最终输入量计算旧状态与
当前状态的精确输出，但发布到 Review Manager 的诊断此前只保留价格、当前容量、候选容量与分类，
没有保留容量缩放公式的精确分子/分母，也没有保留旧/新精确输出。独立复核者无法仅凭诊断重算
`favorable / unchanged / adverse` 与 token0 倒数价格修正是否一致。

### 11.2 实现

- token1 容量比例发布 `|s_b-s_current| / |s_b-s_expected|`；
- token0 容量比例发布
  `(|s_b-s_current|*s_expected) / (|s_b-s_expected|*s_current)`，使用 `U256` 保留完整大整数；
- 流动性变化时不套用距离比例，发布
  `exactCurrentCapacity / expectedCapacity`，并继续使用精确当前容量；
- `StateTransitionVerdict` 新增 `capacityRatioNumerator` 与 `capacityRatioDenominator`；
- Review Manager 每 hop 诊断同步发布旧/新精确输出、Tick 边界、旧/当前/候选容量、容量口径、
  比例分子/分母、倍率、价格限制、三态分支与稳定失败码；
- 所有 raw amount 与比例大整数均使用十进制字符串，避免 JSON 数值精度丢失。

### 11.3 验证与边界

离线状态探针覆盖 unchanged、adverse exact output、token0 favorable、token1 favorable、流动性变化
与跨 Tick 六种情形；tools workspace 全量测试退出码 0。

本轮未修改合约，不消耗主网仅余 25 字节的 EIP-170 预算。比例分子/分母仍未进入链上 calldata，
因此 checklist 的「链上常数次仲裁」门禁仍保持未完成；主网 live parity、部署、Authority 接线与广播
也未执行。

证据：`plan/runtime_evidence/reports/p0_8_exact_transition_ratio_evidence_20261115.json`

## 12. 主网 parity 空证据 fail-closed（2026-11-15 追加）

### 12.1 实测发现

对 `flashloan/db/local_store_4663.sqlite3` 执行只读 `review_parity_replay`。由于 review 属于可再生瞬时
数据，已按数据分层原则清理，当前结果为 `routeCount=0`、`comparableCount=0`、
`deviationCount=0`。旧门禁只判断 `deviationCount == 0`，因此会把「没有任何证据」错误解释为
`gatePassed=true`。

### 12.2 修复

- `ReplayReport` 新增 `evidenceAvailable`、`evidenceComplete` 与 `gateReason`；
- 只有 `routeCount > 0`、`comparableCount == routeCount` 且 `deviationCount == 0` 才允许
  `gatePassed=true`；
- 空路线返回 `no_routes`，路线存在但无可比较项返回 `no_comparable_routes`，部分不可比较返回
  `incomplete_comparable_evidence`，存在偏差返回 `parity_deviation_detected`；
- RuntimeCore 事件与 CLI 摘要同步发布证据完整性字段；
- parity CLI 在目标链未预注册时，按显式数据库、network 与 protocol 注册对应 RuntimeCore，仍只
  执行只读命令，不要求先启动 Web 应用。

### 12.3 当前主网结果与边界

使用新 debug 构建通过 `FLASHLOAN_RS_EXTENSION_PATH` 隔离加载后，主网只读结果为：

| 字段 | 结果 |
|---|---|
| `routeCount` | 0 |
| `comparableCount` | 0 |
| `evidenceAvailable` | false |
| `evidenceComplete` | false |
| `gatePassed` | false |
| `gateReason` | `no_routes` |

数据库文件长度与修改时间在执行后保持 `3,507,453,952` 字节、`2026-11-15 00:20:47 +08:00`；
未替换锁定 release 二进制、未部署、未广播、未切换 Authority、未写生产库。

这次修复只消除了真空通过，尚未完成 live parity。后续必须从 token/pool 长期基础事实和当次实时
复核结果生成**非持久化** parity 输入；不得为了 parity 恢复长期 review 垃圾记录。

证据：`plan/runtime_evidence/reports/p0_8_live_parity_empty_evidence_fail_closed_20261115.json`

## 13. 非持久化实时 parity 输入（2026-11-15 追加）

### 13.1 缺口

§12 修复了「空证据真空通过」，但没有回答「证据从哪里来」。review 是可再生瞬时数据，已经按数据分层
原则清理，因此不能为了 parity 恢复长期 review 记录。parity 必须从**长期权威事实**（token/pool
基础数据）加**当次实时复核结果**生成，并且完全不落盘。

### 13.2 实现

**Review Manager**（`flashloan/tools/review_manager/src/parity.rs`）：

- `parity_request_from_review_payload` 从实时复核 payload 提取 route/pool ids、选定量、链上金额与
  **复核时刻**的池状态；缺复核时刻状态时 fail-closed，不用 live 快照冒充；
- `evaluate_route_parity_with_review_time_state` 让复核时刻状态覆盖 live snapshot，live 只作 fallback；
- 修复 live snapshot 读失败时整段回放被中止的问题：改为降级为仅用复核时刻状态；
- **修复大小写 bug**：`pool_ids_from_path_key`、`poolIds` 列表、live map、review-time map、
  `build_hops` 查找全部统一 lower-case 归一化。之前大小写不一致把主网路线误判为 `not_comparable`。

**RuntimeCore**（`flashloan/rs-src/src/runtime_core.rs`）：

- 新增命令 `review_parity_replay_payload`：只读、`persisted=false`，输出 `reviewTimeStateUsed` 与
  `reviewTimePoolCount`；
- 新增 2 个测试：只读性与复核时刻状态使用；缺 payload 或缺复核时刻状态时拒绝。

**Rust worker**（`flashloan/rs-src/src/worker_pool.rs`）：

- 新函数 `live_review_parity_payload`：复核成功时用 `selectedKeys` 推导 pool id，并用**同一 RPC 节点**
  读取当刻链上状态，生成 `quote.candidate_pools`；
- `WorkerState` 新增 `last_parity_payload` / `last_parity_payload_at`，**仅内存**，不进入任何持久化表；
- worker status 的 `include_payloads` 分支暴露 `last_parity_payload`；
- 新增 3 个测试：不盈利、无 registry、路线不闭合均拒绝生成 parity 输入。

**Pool Manager**（`flashloan/tools/pool_manager/src/depth.rs`）：

- **修复 panic**：`U256 -> u128` 未检查转换改为 `to_u128_checked`，溢出返回错误而不是 panic 中断进程。
  原代码在链上数值超过 128 bit 时会直接终止进程；
- 新增 2 个单元测试。

**CLI**：`scripts/p0_8_live_parity_replay.py`（薄壳，不开库、不读表、不算 V4 算术）与
`flashloan/app_runtime/tests/unit/test_p0_8_live_parity_replay.py`（15 测试）。

### 13.3 主网只读实测

| 字段 | 结果 |
|---|---|
| `state` | `match` |
| `persisted` | `false` |
| `reviewTimeStateUsed` | `true` |
| `reason` | `off-chain and chain agree` |

数据库全程只读：`flashloan/db/local_store_4663.sqlite3` 的 size（3,507,453,952 字节）、mtime
（`2026-11-15 06:45:18 +08:00`）与 SHA-256
（`285CE72AEF57A15EA390232D7B7B76966BD5DA09C90D55048522284B89C697A0`）前后**完全一致**。

### 13.4 边界

parity 输入**不持久化**：没有新增表、列或索引。复核、提交、广播仍然是瞬时可再生数据；token/pool
基础信息才是长期权威事实。本轮未部署、未广播、未切换 Authority、未写生产库，也未替换锁定 release
二进制。

证据：`plan/runtime_evidence/reports/p0_8_non_persistent_live_parity_and_healer_repair_chain_20261115.json`

## 14. 主网自愈编排器：复核收敛与广播失败修复（2026-11-15 追加）

### 14.1 缺口

用户要求的固定顺序是：代币 -> 池子 -> 闭环 -> 复核 -> 广播，并且明确：

- 复核没有给出**具体盈亏数值**时，用复核管理工具修正，**直到每个复核都有明确盈亏**；
- 自动广播出现错误时，调用广播交易工具修正。

原编排器在这两处都只是「报告」，不是「修复」：复核层每轮只重跑一次 `rereview_missing_pnl`，不检查
是否真的收敛；广播层在 `repair()` 里被硬编码成 `skipped: True`，任何广播失败都只是计数。

### 14.2 实现

**广播失败分类**（`scripts/mainnet_arbitrage_healer.py`）：

- `BROADCAST_FAILURE_CLASSES` + `_SEPARATOR_RE` + `classify_broadcast_failure`：六类失败
  （`pool_state_unavailable` / `stale_pool_state` / `transaction_rejected` / `rpc_unavailable` /
  `no_profit` / `unknown`），空格、下划线、短横线归一化后匹配；
- `broadcast_failure_candidates` + `repair_broadcast_failures`：经广播管理器 `/v1/broadcasts/finalize`
  终结被拒交易；
  - `no_profit` **永不修复**：没有盈利是市场结果，不是缺陷；
  - `pending` **永不触碰**：待确认交易不是失败，修复它会导致双花风险；
  - `unknown` 只报告、不猜测；
  - 池状态类缺陷（`pool_state_unavailable` / `stale_pool_state`）上交上游池子层处理；
- `repair()` 中广播层由硬编码 `skipped: True` 改为真实调用 `repair_broadcast_failures`；
- `healing_actions` 新增 `repair_broadcast_failures`。

**复核收敛**：

- `reviews_missing_a_value` + `converge_reviews`（`REVIEW_CONVERGENCE_MAX_ROUNDS = 3`）：有界收敛，
  并做**停滞检测**；停滞时报告 `converged=false, stalled=true`，而不是假装成功；
- supervisor 循环把单轮 `rereview_missing_pnl` 换成 `converge_reviews`，并在每轮后执行广播修复。

### 14.3 边界

`no_profit` 与 `pending` 的「不修复」是硬边界：前者是市场结果，后者是待确认交易。任何把二者当作
缺陷去终结的逻辑都会破坏盈亏统计的正确性或造成双花。

未完成：复核收敛与广播修复目前只在编排器层实现并通过离线测试，主网还没有产生实际的 review 记录
（review 库为 0 条），因此真实闭环尚未跑通。见 §15。

证据：`plan/runtime_evidence/reports/p0_8_non_persistent_live_parity_and_healer_repair_chain_20261115.json`

## 15. 主网 scope 漂移根因定位与自愈闭环重建（2026-11-15 追加）

### 15.1 实测症状

主网 healer `check` 的生产状态为 `pool_scope_drift`，漂移层为
`manager_v4_closed_loop_library_state`：

| 字段 | 结果 |
|---|---|
| `expectedChecksum`（core4 scope） | `sha256:5434a25c...c9926` |
| `manager_dex_candidate_library_state` | `sha256:5434a25c...c9926`（一致） |
| `manager_v4_closed_loop_library_state` | `sha256:8ceb9e02...dfc1`（**不一致**） |
| `manager_market_observation_library_state` | `sha256:8ceb9e02...dfc1`（属于 `candidate_exchange`，正常） |
| `missingPoolHopCount` | 0 |
| `affectedLoopCount` | 0 |

关键事实：**闭环 hop 本身没有失效**。318 个 hop 使用的 65 个 distinct pool 全部命中
`v4_exchange_pools_candidate_exchange`，38 个不在 core4 候选集内的 hop 全部由市场观察层合法提供
（中间跳）。所以这不是「闭环引用了被淘汰的池子」的数据缺陷。

### 15.2 根因

闭环库记录的是**它实际所属 scope 的 checksum**。`closed_loop_manager` 现已通过
`closed_loop_pool_scope_checksum` 从 `candidate_edge`（core4）读取 checksum，而不是从观察层读取。
但生产库的闭环库是在 **2026-11-09** 物化的——早于该修复落地，因此仍写着
`candidate_exchange` 的 checksum `8ceb9e02...dfc1`。

同时生产库的 scope 策略也是旧的：

| scope | 数据库中的 `library_keys_json` | Rust 源码中的目标 |
|---|---|---|
| `core4` | `["core"]` | `["core", "stocks"]` |
| `candidate_exchange` | `["core", "stocks", "extension", "test"]` | `["core", "stocks"]` |

`pool-manager baseline-audit` 已经把这两项分别报为 `core_scope_library_policy_stale` 与
`candidate_scope_library_policy_stale`。

### 15.3 自愈死锁与修复

原 `repair()` 的闭环重建只由 `consumers_need_rebuild > 0` 或闭环层 `valid=false` 触发。但闭环库是
从**错误 scope** 物化的，所以它自认为是最新的（`consumers_need_rebuild=0`、`valid=true`）。于是：

```text
pool 层因 scope 漂移 invalid
  -> 修复链去跑 materialize-scope（不会改闭环库）
  -> 闭环库仍是旧 checksum，漂移不消失
  -> pool 层持续 invalid，闭环重建永远不触发
```

**修复**（`scripts/mainnet_arbitrage_healer.py`）：

1. 新增 `CLOSED_LOOP_SCOPE_LAYER` 与 `closed_loop_scope_drift()`，把两类漂移分开：
   - **闭环层 checksum 漂移**（`missingPoolHopCount == 0`）：闭环库缺陷，重建闭环库即可；
   - **hop 引用了候选集之外的池**（`missingPoolHopCount > 0`）：池集合缺陷，必须先修池集合；
2. 修复链的闭环重建触发条件扩展为
   `consumers_need_rebuild > 0 || 闭环层 invalid || 闭环层 scope 漂移`，触发源记为 `pool_scope_drift`；
3. `healing_actions` 在「闭环层漂移且 hop 全部可解析」时返回 `repair_closed_loops`，
   不再误判为 `repair_pools`（池集合在这种情况下是健康的）；
4. `transaction_production_status` 的 `reason` 细分为
   `closed_loop_library_records_a_stale_pool_scope` 与 `candidate_and_closed_loop_scopes_disagree`；
5. **修复 dry-run 假失败**：`ensure-indexes` / `ensure-depth-schema` 在没有 `--apply` 时按设计以
   非零码退出，旧逻辑把它读成 tool failure，导致每次 dry run 都报告「链路损坏」。新增模块级
   `tool_entry_failed()`，显式 `skipped` 的步骤不再算失败。

### 15.4 主网 dry-run 验证

按「工具 -> 索引 -> 深度 schema -> 池深度 -> 数据库基线 -> 代币 -> 池 scope(core4) ->
池 scope(candidate_exchange) -> 闭环 -> 广播」顺序执行，只读 dry-run：

| 步骤 | 结果 |
|---|---|
| `pools.core4` | `libraryKeys=[core, stocks]`，eligible 2022，skipped 2719（`token_not_admitted` 2668 / `no_core_anchor` 51），checksum `sha256:e8892b7c...d3ab` |
| `pools.candidate_exchange` | `libraryKeys=[core, stocks]`，eligible 2073，skipped 2668，checksum `sha256:41726f38...5182f` |
| `closedLoops` | `trigger=pool_scope_drift`，`changedRows=1146`，`poolScopeChecksum=sha256:5434a25c...c9926` |
| `broadcast` | `candidateCount=0`，`stopped=None` |

闭环重建在池 scope 之后执行，因此写入的是**重建后**的 scope checksum，顺序正确。

### 15.5 边界

本节全部为**只读 dry-run**。生产写入（`--apply`）需要显式授权，本轮未执行：未部署、未广播、
未切换 Authority、未写生产库、未替换锁定 release 二进制。闭环库重物化会改写 1146 行，属于生产
写入，必须在授权窗口内执行。

证据：`plan/runtime_evidence/reports/p0_8_scope_drift_closed_loop_rebuild_20261115.json`

## 16. 复核层权威源统一与观察纪元修复（2026-11-15 追加）

### 16.1 复核管线只读 legacy 表（缺口）

主网审计早已把复核层标为 `review_library_not_materialized`：`manager_v4_reviews` 0 条、
`cow_execution_reviews` 2 条。但更深的问题是**整个复核管线只读 legacy 表**：

| 函数 | 读表 |
|---|---|
| `review_rows` | `cow_execution_reviews` |
| `pnl` / `best_amount_state` | `payload_json`（legacy 形状） |
| `reviews_missing_a_value` | 间接走 `review_rows` |
| `missing_pnl_review_candidates` | 间接走 `review_rows` |
| `pnl_stats` | 间接走 `review_rows` |

后果是：一旦 Review Manager 把权威复核物化进 `manager_v4_reviews`，收敛循环**看不见它们**，
既检测不到「缺明确盈亏」，也永远收敛不了。用户要求「直到每个复核都有明确的盈亏数值」
在权威库被填充的那一刻就会失效。

### 16.2 修复

**统一复核源**（`scripts/mainnet_arbitrage_healer.py`）：

- 新增 `manager_review_rows()`：读 `manager_v4_reviews`，该表携带闭环、候选边、市场观察层与
  池 scope 的**版本与 checksum**，是唯一能把一条复核绑定到当前结构的来源；
- 新增 `legacy_review_rows()`：保留原 legacy 查询；
- `review_rows()` 改为**优先读权威库**，仅在权威库为空时回退 legacy（保证未迁移的库仍能出报告）；
  每一行带 `review_source` 标记（`manager` / `legacy`）；
- 新增 `row_route_addresses()`：manager 行从 `path_addresses_json` 取路线，legacy 行从
  `payload_json` 取，两者必须归一到同一个 loop identity；
- `review_loop_key()`：manager 行直接用 `loop_key` 列（权威身份）；
- `pnl()`：同时支持 `selected_profit_amount`（legacy）与 `signed_pnl_amount`（manager），
  并可回落到 `run_arbitrage_json.signedPnl.amount`；**缺值仍然是 `None`，绝不读成 0**；
- 新增 `review_missing_reason()`：legacy 读 `selected_profit_missing_reason`，manager 读
  `review_status` + `review_reason`，归一到同一词汇表；
- `best_amount_state()`：manager 行的 best-amount 证据在 `run_arbitrage_json` /
  `static_preview_json`，两处都读；否则已物化的复核会被误报成「缺最佳交易量」。

**复核物化**（新增 `materialize_reviews()`）：

- 经广播管理器同族的 `/v1/reviews/run` 把实时复核证据写入权威库；
- 请求体携带 `expectedUpstream`（闭环版本/checksum、候选边版本/checksum、观察层版本/checksum、
  池 scope checksum），因为 Review Manager 会拒绝上游不匹配的复核；
- `rereview_missing_pnl` / `fill_missing_reviews` / `rereview_historical_positive` 三条路径
  都在拿到实时复核证据后调用它；`converge_reviews` 与 supervisor 循环把 manager API 透传下去。

### 16.3 观察纪元失效（计划目录迁移遗漏）

healer 的路径常量仍指向 `logs/plan/...`，而计划树已于 **2026-11-14** 迁移到 `plan/`
（`plan/back/计划目录迁移记录_20261114_v9.11.md` 明确写明「代码和文档中的 `logs/plan` 默认路径
已改为 `plan`」）。结果：

- `EPOCH_FILE` 永远找不到 → `observationEpochFallback=true`、`startedAt` 退化成
  `2026-01-01T00:00:00Z`；
- 复核窗口实际变成「整表」，报告里的计数无法复现；
- 新证据继续写进旧的 `logs/plan/runtime_evidence/reports/`，与计划文档脱节。

**修复**：新增 `PLAN_ROOT` / `LEGACY_PLAN_ROOT` 与 `_plan_path()`，当前路径优先、旧路径回退；
`REPORT_DIR` / `WATCH_DIR` / `SUPERVISOR_DIR` / `EPOCH_FILE` 全部改到 `plan/runtime_evidence/`；
`scripts/measure_robinhood_api_status.py` 同步修正。

修复后主网只读实测：`observationStartedAt=2026-11-10T19:57:50Z`、`observationEpochFallback=false`、
`observationEpochUnknown=false`，报告写入 `plan/runtime_evidence/reports/`。

### 16.4 迁移基线精确化

`pool_manager::baseline_audit` 原本只报「最新期望迁移未应用」，运营方无法据此估算变更窗口规模。
现 `MigrationBaseline` 增加 `missing_migrations` 与 `applied_migrations`：

主网当前精确缺 3 个迁移：

| 迁移 | 说明 |
|---|---|
| `20261112_007_usdg_valuation_schema` | USDG 估值表 |
| `20261114_008_single_tick_capacity_schema` | single-tick 容量列 |
| `20261114_009_hop_price_limit_schema` | 逐 hop 限价列 |

已应用 7 个，`unknownMigrationCount=0`。`manager-db preflight` 结果为 `quickCheck=ok`、
`readyForMigration=true`，除 `token_usdg_valuations` 外目标表全部兼容。

### 16.5 验证与边界

| 项 | 结果 |
|---|---|
| healer 专项测试 | 31 passed（新增 8 项） |
| pool-manager 测试 | 135 passed（新增 2 项） |
| 二进制与源码一致 | `valid=True`，source digest `73c10dac...3ecc` |
| 主网只读实测 | epoch 恢复、迁移清单精确、阻塞项不变 |
| 生产写入 | 未执行（未部署、未广播、未切换 Authority、未写生产库） |

本轮全部为只读检查、离线测试与二进制重建，未对生产库做任何写入。

## 17. 异步手工复核响应与 Review Manager 证据门禁（2026-11-15 追加）

### 17.1 缺口

`/api/dex/closed-loop-groups/manual-review` 调用 `RuntimeCore manual_review`，实际只把驻留 worker 从等待态唤醒。
正常 HTTP 200 返回的是 `accepted/transitionQueued/processingAsync`，不包含 `ReviewEvidenceInput`。
旧 `live_review_evidence()` 却会把响应中的任意 `runArbitrage` 片段直接交给 Review Manager，既没有强制
`loopKey`，也没有校验 `signedPnl` 的 `raw/amount/currency/unit/source`，存在把异步确认或不完整片段误物化的风险。

### 17.2 修复

1. `live_review_evidence(response, candidate)` 始终使用 Manager 候选闭环的权威 `loopKey`；
2. 递归兼容 `finalReview/workerRefresh/initialReview`、`task.result.evidence` 与 snake_case 运行时字段；
3. 只接受 `ok`、`executable` 均为布尔值且 `signedPnl` 五个必需字段完整、`amount` 为有限数值的真实结果；
4. 输出完整 `ReviewEvidenceInput`：`loopKey/staticPreview/runArbitrage/diagnostics/reviewedAt`；
5. 纯异步确认、缺 `signedPnl`、非有限盈亏或不完整盈亏一律返回无证据，不调用 `/v1/reviews/run`，更不补零。

### 17.3 验证与边界

- healer repair-chain：33 passed；手工复核路由：11 passed；Rust worker bridge 定向：3 passed；
- `cargo test --offline --workspace` 全部通过；
- 主网只读 check 仍为 `pool_scope_drift`，repair dry-run `stopped=None`；
- 数据库保持 3,507,453,952 字节、7 个迁移、`manager_v4_reviews=0`、106 个闭环，旧 scope checksum 未变；
- 未执行 `--apply`，未部署、未广播、未切换 Authority。

机器证据：`plan/runtime_evidence/reports/p0_8_async_review_evidence_gate_20261115.json`。

## 18. 单 Tick 容量与状态突变闭环收口（2026-11-15 追加）

### 18.1 本轮范围

本轮只做**代码与离线回归收口**，不触碰生产：`TASK-006`（方向判定/放大倍率/限价/利润锁）、
`TASK-007`（状态变好后的容量放大不得复用旧利润）、`TASK-008`（单 Tick / 三跳 / parity / 状态回归）
三条任务在**离线层面**已可证明；`TASK-009`（主网 bounded executor 部署与批次接线）与
`TASK-010`（主网 live parity 与同源链上三态分类）仍然开放，不得提前宣称完成。

### 18.2 修复的编译阻塞

两个未跟踪的新模块在上一轮落地时带有重复定义，导致 workspace 无法编译：

| 文件 | 缺陷 | 处理 |
|---|---|---|
| `flashloan/tools/pool_manager/src/baseline_audit.rs` | `read_scope_libraries` 重复定义 5 份 | 保留 1 份，删除其余 4 份 |
| `flashloan/tools/review_manager/src/lib.rs` | 新鲜度常量/函数重复定义 5 份；重复分支结构错误；`review_reason` 绑定不需要 `mut` | 删除重复定义，修正分支与移动语义，去掉多余 `mut` |

修复后 workspace 全量编译通过，行为未变。

### 18.3 口径确认

**方向判定**：按同输入 exact-output 单调性判定，不依赖“买入/卖出”文字标签。

- token1 容量比例：线性 `sqrtPrice` 距离比例；
- token0 容量比例：额外包含倒数价格项；
- 两者统一向下取整，零分母与溢出有显式检查。

**放大倍率**：硬上限 `DEFAULT_MAX_CAPACITY_MULTIPLIER_BPS = 20000`；超出上限的期望容量被拒绝
（`an_untrusted_expected_capacity_above_the_exact_bound_is_fatal`），而不是被截断后放行。

**Tick 越界**：当前价格离开期望 tick 区间即 `fatal`，原因码 `tick_range_changed`，`broadcast_allowed=false`。

**限价**：`route_transition_price_limits()` 要求恰好 3 跳且每跳都给出非空
`sqrtPriceLimitX96`；任一跳缺失即返回空列表，拒绝回落到全局 V4 价格边界。

**利润锁**：路线级 reviewed minimum output 经既有 `gasReserve` 字段传入，合约在利润门禁与结算之前比较
**本次路线的增量输出**（`balanceAfter >= balanceBefore + minAmountOut`），旧余额不得补贴劣化成交。

### 18.4 状态变好不等于利润证明

`favorable` 只发布**候选上界** `candidate_capacity_raw`，绝不作为利润证明。
`review_manager::evaluate_final_state_transition_gate()` 以 `final_input_amount_raw(run)` 为唯一输入量：

- 按**最终输入量**逐跳重算期望容量与当前精确输出；
- 最终 single-tick 门禁与状态突变门禁共享同一最终输入量；
- 缺最终输入量返回 `final_input_amount_unavailable` 并 fail-closed，绝不复用旧利润。

### 18.5 验证

| 项 | 结果 |
|---|---|
| `cargo test -p pool-manager state_transition --locked` | 9 passed / 0 failed |
| `cargo test -p pool-manager single_tick --locked` | 21 passed / 0 failed |
| `cargo test -p review-manager --locked` | 129 passed / 0 failed |
| `cargo test --locked --workspace` | 退出码 0，0 failed |
| `npx hardhat test test/RobinhoodPausedFlashLoanExecutor.test.js` | 33 passing / 0 failing |

Executor 套件覆盖 reviewed minimum output 利润锁、增量输出比较、旧余额不补贴、
批内单闭环失败隔离（失败项不回滚成功项）、批次上界与自调用防护。

### 18.6 边界

本轮**未**部署合约、**未**广播、**未**切换 Authority、**未**写生产库、**未**执行迁移或 prune、
**未**替换锁定 release 二进制；RPC 请求数 0。

仍开放的门禁：链上比例 calldata（AC-006）、逐跳 `amountOutMinimum` 下限（AC-007）、
状态变化证据写入链上 calldata（AC-008）、每跳 tick 状态与 `single_tick_max_token0_raw` /
`single_tick_max_token1_raw` 的生产物化影响评估（AC-009）、主网 executor 部署接线（TASK-009）、
主网 live parity 与同源链上三态分类（TASK-010）。

机器证据：`plan/runtime_evidence/reports/p0_8_single_tick_capacity_state_transition_closure_20261115.json`。