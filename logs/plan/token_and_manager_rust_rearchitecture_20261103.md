# 代币准入与 Rust 模块化管理重构方案

> 文档状态：目标架构，`M0-M9` 尚未全部实现。当前实现状态以
> [开发计划_20261108.md](开发计划_20261108.md) 为准，未完成项和整体推进路线见
> [未完成任务清单_20261108.md](未完成任务清单_20261108.md) 与
> [整体工作计划_20261107.md](整体工作计划_20261107.md)。
>
> 可直接用于编码和迁移的表结构、接口示例、代码落点及测试矩阵见
> [Rust管理模块实施手册_20261103.md](Rust管理模块实施手册_20261103.md)。

## 1. 结论

该方向可行，并且比当前 Python 运行时直接读写 SQLite 更容易收敛数据边界。
核心前提不是“全部模块都用 Rust 写”，而是：

1. 代币管理是全局准入层，只允许被它确认和注册的代币进入资金池、候选池、
   YZ、闭环和 UI。
2. 每个管理模块拥有自己的数据库表范围，只通过模块接口发布数据，下游模块
   不跨模块写表。
3. UI 不再直查 SQLite，只读取各管理模块发布的版本化 JSON DTO。
4. Rust 负责高频、强一致和链上相关的写入；Python 在迁移期只作为兼容读取层，
   不参与新数据的权威写入。

需要接受的主要代价：

- 现有 `dex/`、`core/db/`、`rust_closed_loop.py` 中大量硬编码 SQL 不是一次
  重写能完成的，需要分模块接管。
- 迁移期要保留当前主网观察能力，因此 Python 只读路径不能立即删除。
- 模块间需要明确的版本号、检查点和“下游需要重建”信号，否则会出现各模块
  数据快照不一致。

### 1.1 当前实现基线

本文档同时描述目标架构和迁移路径，不能把目标设计误认为已经完成：

| 模块 | 当前状态 | 与目标的主要差距 |
|---|---|---|
| Token Manager | M0 schema、D1.1.2 事务存储、D1.1.3 metadata 归一化、D1.1.4 分类、D1.1.6 差异审计、D1.1.7 临时库模拟、D1.2.1 query/resolve、D1.2.2 admit/block 和 D1.2.3 audit API 已完成 | 主网 bootstrap apply 和 Python 写入切换尚未完成 |
| Pool Manager | 独立 Rust crate、四库 admitted catalog、共享 PoolKey/PoolId、`core4 -> edge_any` scope、事务写入口和主网只读 PoolId 审计已完成 | 生产 scope/bootstrap apply 和 authority 切换尚未完成 |
| Candidate Edge Pool Manager | 独立 Rust crate、shadow 写入口、只读 API、compare-only 和 RuntimeCore 刷新命令已完成 | 真实主网副本双读、生产 `dex_candidate_*` 切换尚未完成 |
| Candidate Exchange Pool Manager | 独立 Rust crate、shadow 写入口、只读 API、compare-only 和 RuntimeCore 刷新命令已完成 | 真实主网副本双读、生产 authority apply 和 `market_observation_*` 切换尚未完成 |
| Closed-loop Manager | 独立 Rust crate、shadow、只读 API、compare-only、版本门禁和 RuntimeCore 手动复核/广播状态迁移入口已完成 | 生产闭环表切换仍依赖 M1-M4 真实副本 bootstrap、双读和维护窗口 |
| Review Manager | 独立 Rust crate、shadow/API、signed PnL 完整性、结构化诊断和复核 supervisor 已完成 | 生产复核权威和真实 worker 证据链尚未切换 |
| Broadcast Manager | 独立 Rust crate、shadow/API、队列 supervisor、nonce/gas/RPC/回执终态和失败诊断已完成 | 生产广播权威及真实广播仍受 M1-M7 切换和 P0 门禁约束 |
| Composite Manager | 独立 Rust crate、shadow/API、版本一致性、step 幂等、checkpoint 和 pipeline 审计已完成 | 生产 pipeline 尚未接管 |
| UI 兼容层 | 主要 DEX 页面已 manager-only；常规 GET 走 Rust `RuntimeCore.manager_view`，D9.30-D9.41 已迁移刷新/清除/手动复核/手动广播命令、广播失败记录投影、控制面板 scanner 生命周期只读化并隔离 quote 页面 route-test 活动路径，D9.35 已退役 Flask UI evidence JSON 直读，D9.39 又退役广播失败 JSONL 直读，D9.40 清理迁移页面死旧 SQLite 包装，D9.36-D9.37 已把路由符号/地址身份解析下沉到 core 并收敛重复 helper | 其余页面仍有 Python 直查 SQLite 或实时业务数据路径，旧写路径需按表范围逐步退役 |

### 1.2 已冻结的设计决策

| 决策 | 结论 | 原因 |
|---|---|---|
| 代币主数据 | Token Manager 是唯一准入和元数据权威 | 防止池、候选、闭环各自维护代币副本 |
| 四库归属 | `core/stocks/test` 主库唯一；`extension` 是可与主库重叠的派生成员库 | 防止 metadata 和 admission 状态多写，同时保留跨类池邻接能力 |
| Pool 配置 | 一种启动方式对应一张物化池表 | 参数边界清晰，启动不读取无关池集合 |
| 代币引用 | symbol 只用于配置解析，KEY ID 用于持久化 | symbol 不唯一且可变 |
| V4 身份 | Token Manager 发布 currency key，Pool Manager 计算 PoolKey/PoolId | fee、tick spacing、hooks 属于池 |
| 模块接口 | crate 发布 Rust lib，`manager_api` 统一发布 HTTP/JSON | 避免每个模块独立服务造成部署和版本分裂 |
| UI | URL 和页面结构保持不变 | 降低重构期间用户操作回归 |
| PnL | 缺少具体 signed 数值时不得完成复核 | 诊断原因不能替代盈亏事实 |
| 广播 | Review 确认后才允许 Broadcast Manager 提交 | 禁止候选或闭环模块直接发交易 |
| 跨模块流程 | CPU 轻量编排由 Composite Manager 负责 | 需要版本一致性和断点续跑 |

## 2. 模块分层

```text
Token Manager
  |
  v
Pool Manager
  |
  v
Candidate Edge Pool Manager
  |
  v
Candidate Exchange Pool Manager
  |
  v
Closed-loop Manager
  |
  v
Review Manager
  |
  v
Broadcast Manager
  |
  +--> Composite Manager (cross-module orchestration only)
```

### 2.1 Token Manager

责任：

- 维护系统可见代币元数据：地址、符号、名称、精度、类型、链 ID。
- 维护准入状态：`admitted`、`blocked`、`candidate_only`、`pending_review`。
- 维护代币分级库：核心库、股票库、扩展库、测试库。
- 维护代币分类：`pool_token`、`intermediate_pool_token`、`stock_token`、
  `native`、`stable`、`anchor`。
- 输出经过清洗后的代币表，所有下游模块只消费该表。
- 原始资料的管理只由 Token Manager 完成，Pool Manager 等下游不得修改库内容。

### 2.2 Token Manager 四张代币库表

Token Manager 按用途维护四张独立代币库表：

| 库 | 物理表 | 内容 | 定位 |
|---|---|---|---|
| 核心库 `core` | `token_core` | `USDG`、`USDE`、`ETH`、`WETH` | 主网闭环基础资产 |
| 股票库 `stocks` | `token_stocks` | 股票代币 | 二级资产库 |
| 扩展库 `extension` | `token_extension` | 与 core、stock 或 TZ 存在通过流动性门槛的直接 V4 池，且 metadata 已归一化通过的代币；可包含 core、stock、TA、TB | 可进入中间 YC/ZC 的候选资产 |
| 测试库 `test` | `token_test` | 每个 network 仅显式 TZ 地址 | 隔离测试和回归资产 |

约束：

- 后续 `YC`、`ZC` 只能从 Token Manager 已准入的分级库中选择。
- 候选边池和 YZ 交换池只要求上游 admitted、PoolKey/PoolId 有效和链上流动性
  成立，不得因缺少美元估值、美元深度或旧 `thin/unpriced` 标签被拒绝。
- 扩展库的准入门槛是“与 core 任一代币、stock 代币或 TZ 存在直接 V4 池，
  同时该池通过 Pool Manager 流动性门槛、对端 metadata 已归一化通过”，
  不是任意链上 token，也不是仅凭池邻接即可收录。扩展关系不以美元资产或
  美元深度为前提；`deep/thin/unpriced` 和 `estimated_tvl_usd` 只作诊断。
- `core/stocks/test` 主库互斥；`extension` 可与任意主库重叠。extension 重叠行
  只表达池邻接资格，不覆盖主库 metadata 和 admission 权威。
- TA/TB 不得进入 test 主库；通过 TZ 直接池进入 extension。股票库和测试库必须
  保留原始来源标记，不得与 extension 派生行混用权威来源。
- Pool Manager 可以根据启动方式选择某一类或多类库中的代币作为初始化范围，
  但不负责维护代币来源、metadata、准入状态和库归属。
- `core4` 只存在于 Token Manager。Pool Manager 通过库版本引用它，不复制
  `core4` 代币清单，因此代币所有权和资金池引用不会冲突。

四张表使用一致的核心字段：

```text
network, chain_id, token_key_id, token_address
token_symbol, token_name, token_decimals, token_kind
admission_status, admission_source, metadata_checksum
v4_currency_key, v4_currency_pair_hint_json
library_version, first_seen_at, first_admitted_at, updated_at
blocked_reason, source_json
```

`token_key_id` 的主键语义是 `chain_id + normalized_token_address`。`token_symbol`
只是展示字段，不能单独作为跨模块外键。`v4_currency_key` 与 Uniswap V4
`currency0/currency1` 使用同一规范：小写 0x 地址，地址排序规则一致。
`v4_currency_pair_hint_json` 只保存该代币当前已确认的可选配对提示，不作为
池子事实来源。`chain_tokens` 在迁移完成后只作为兼容投影或 Token Manager 的
导入来源，不再由 Python 入口直接维护权威内容。authority 在 Rust 和 Python
两端统一由 `TOKEN_MANAGER_AUTHORITY` 控制：

- `legacy` 是当前生产默认值，只允许旧 Python 写，Rust admission apply 关闭。
- `manager` 只允许 Token Manager 写准入，Python 只能执行非破坏
  `chain_tokens` 兼容投影。
- `rollback` 恢复旧 Python 写但必须带 `TOKEN_MANAGER_ROLLBACK_REASON`，且不得
  删除 manager 行、版本、event 或幂等记录。

兼容投影只发布 `admitted/candidate_only`，按
`core -> stocks -> test -> extension` 去重 overlap；不删除旧
`chain_tokens` 中的其他行。任一时刻不得通过双环境变量制造 Python 与 Rust
同时写入。blocked/pending token 若仍存在于旧投影，必须报告
`readyForProjection=false` 并拒绝 apply。

### 2.2.1 KEY ID 契约

Pool Manager 可以使用 Token Manager 返回的 symbol 做日志和展示，但不能使用
symbol 文本组合持久化主键。KEY ID 按以下层级生成：

```text
v4_currency_key = lower(token_address)
token_key_id    = network:chain_id:v4_currency_key
pair_key_id     = canonical(sorted(v4_currency_key0, v4_currency_key1))
route_key_id    = ordered(token_in_key_id, token_out_key_id, ...)
v4_pool_id      = keccak256(V4PoolKey)  # Pool Manager 负责
```

- `token_key_id` 由 Token Manager 唯一发布。
- `pair_key_id` 用于无方向的池子和交易对索引。
- `route_key_id` 用于有方向的候选边、Y-Z、闭环跳和复核。
- `v4_pool_id` 按现有 `V4PoolKey` 编码计算，字段为 `currency0, currency1,
  fee, tick_spacing, hooks`。四张代币表只保存 currency 地址和元数据，不保存
  pool 的 fee、tick spacing 或 hooks。
- Pool ID、PoolKey 和 pool manager 地址仍保留为链上执行身份，不得由 symbol
  推导。
- 如果 symbol 重复、缺失或与地址不一致，解析必须失败并要求 Token Manager
  修复，不能猜测。

Token Manager 对外发布的每张库摘要必须包含：

```text
library_key
library_version
token_addresses
metadata_checksum
source_checkpoint
generated_at
```

Pool Manager 只保存 `library_key + library_version + checksum`，不复制代币
原始资料。库内容发生增量变化时，Pool Manager 比较版本并决定是否刷新对应池表。

### 2.3 初始化与增量维护

不做全量区块重放。首期基线从现有可信数据库导入：

1. 从 `chain_tokens` 导入已有可信 token metadata。
2. 把 core、股票库和每个 network 的 TZ 测试库显式导入，并记录
   `bootstrap_source`、`bootstrap_at` 和 checksum。
3. 从 `v4_exchange_pools` 找出与 core、stock 或 TZ 存在直接兑换池的代币，
   并用 `v4_exchange_pool_liquidity_snapshots` 校验该池当前流动性证据；
   只有通过 Pool Manager 质量门槛的直接池才生成 extension 候选成员；
   extension 可包含 core、stock、TA/TB，但不包含 TZ。
4. Token Manager 对 extension 成员重新确认 symbol、decimals、来源和池证据；
   metadata 或流动性证据未确认的地址不分配 extension membership，
   并保留结构化审计原因。
5. 后续只做增量维护：Pool Manager 发现新池后，把未注册代币提交给 Token
   Manager 审核；审核通过后先确定唯一主库，再按需增加 extension 派生成员。

增量维护要求：

- 新池、新股票、新稳定币不得绕过 Token Manager 直接进入下游业务表。
- Pool Manager 可以报告“发现新 token”，但不能直接写入 Token Manager 代币库。
- 新池流动性不足、动态/特殊费、非零 hook 或对端缺失 decimals 时，作为
  `candidate pending` 保留原因，不自动进入 `token_extension`。
- 每个代币必须有来源记录，禁止无来源的 metadata 覆盖。

### 2.4 Pool Manager

责任：

- 从链上 `PoolManager Initialize` 事件、RPC 状态和流动性快照构建资金池。
- 只保存 Token Manager 已准入代币，未准入代币的池子不进入活动池表。
- 从 Token Manager 分级库中选择启动代币范围，不管理代币库内容。
- 支持启动模式对应不同资金池配置表。
- 按 `library_keys` 从 `token_core`、`token_stocks`、`token_extension`、
  `token_test` 中加载指定库；未配置的库不参与本次启动。
- 使用 Token Manager 返回的 `token_key_id` 组合池子和交易对，不复制 symbol，
  不以 symbol 代替地址或 KEY ID。
- 使用 Token Manager 四表返回的 `v4_currency_key` 组合 V4 `PoolKey`，并按现有
  `rs-src/src/pool_index.rs` 定义计算 `pool_id`；pool_id 和 pool_key_json 只能
  由 Pool Manager 持久化。
- 负责合规校验、池有效性修复、池删除、流动性刷新。
- 不直接生成候选池、YZ 或闭环。

启动模式示例：

```text
full          -> v4_exchange_pools_full
core4         -> v4_exchange_pools_core4
all_except_tz -> v4_exchange_pools_all_except_tz
```

Pool Manager 的启动配置只引用 Token Manager 的库，不维护库内代币：

```text
pool_scope_config
  network, chain_id, scope_key
  library_keys_json
  library_versions_json
  pool_table
  enabled, updated_at
```

例如：

```text
core4        -> ["core"]
stocks       -> ["stocks"]
test         -> ["test"]
core_stock   -> ["core", "stocks"]
core_ext     -> ["core", "extension"]
core_test    -> ["core", "test"]
```

`core4` 只读取 `USDG/USDE/ETH/WETH`：

- `core4` 是 Token Manager 管理的库，不是 Pool Manager 自己维护的代币表。
- Pool Manager 只保存 `core4` 的库版本、checksum 和对应资金池配置表引用。
- 池筛选使用单独配置，不得把库引用隐式解释为池子两端过滤。
- `edge_any` 表示只要池子任一端属于启动库，就可进入候选边池扫描。
- `both_end` 表示池子两端都属于指定库，才可进入该池表。
- 候选池、YZ 和闭环都继承最终解析出的池版本与候选库版本。
- 方向数量由实际合格池和候选库范围计算，不写死为某个理论数字。

### 2.5 Candidate Edge Pool Manager

责任：

- 以 Pool Manager 的合格边池为输入，并校验代币来自 Token Manager 的 admitted
  候选库范围。
- 把资金 token 和候选 token 的 `token_key_id` 组合成候选边 KEY ID；不得从
  symbol 字符串反推地址。
- 专门管理“资金代币到候选代币”的边池：`资金池 X -> YC` 和
  `ZC -> 资金池 X`。
- 构建 YC/ZC 候选集合、交易对、推荐池、异常池和边池引用。
- 维护候选边池状态，支持动态新增、删除、重新校验。
- 输出 YC/ZC 集合版本和 `consumersNeedRebuild`，不直接写 Y-Z 中间池或闭环。

现有表范围：

```text
dex_candidate_anchor_tokens
dex_candidate_pool_pairs
dex_candidate_pools
dex_candidate_pool_recommendations
dex_candidate_abnormal_*
```

### 2.6 Candidate Exchange Pool Manager

责任：

- 以 Candidate Edge Pool Manager 输出的 YC/ZC 集合为候选边界，同时消费 Pool
  Manager 的合格交换池和 Token Manager 的元数据；不得直接查询或写入其他模块
  拥有的业务表。
- 使用 Token Manager 的 `token_key_id` 和有序 route KEY ID 构建 Y-Z 方向，
  symbol 只进入 DTO 展示字段。
- 专门管理“候选代币自身的交换池”：任意有序 `YC -> ZC` 方向和中间交换池。
- 和 Candidate Edge Pool Manager 分离，避免把第一/第三跳的锚点边池和第二跳
  的候选交换池混在同一模块内。
- 维护直接池、空原因、异常池和流动性快照。
- 输出 YZ 版本摘要，供闭环模块使用。

现有表范围：

```text
market_observation_yz_pairs
market_observation_intermediate_pools
market_observation_abnormal_*
market_observation_liquidity_snapshots
```

### 2.7 Closed-loop Manager

责任：

- 以候选池、YZ 池和 Token Manager 元数据为输入。
- 构建闭环、闭环跳、优化和运行状态。
- 维护闭环身份、路径、状态和版本，不负责链上复核和执行广播。
- 输出可复核闭环集合和版本摘要。

现有表范围：

```text
v4_closed_loops
v4_closed_loop_hops
v4_closed_loop_optimizations
v4_closed_loop_runtime_state
```

### 2.8 Review Manager

责任：

- 是复核工作的唯一管理工具，拥有一个复核主线程、唯一
  `ClosedLoopReviewQueue`、有界复核工作线程池及其启动、停止、心跳、背压、
  超时、僵死恢复和重试生命周期。
- 主线程只负责排队、派发、回收、状态推进和故障隔离；工作线程只执行静态预览、
  候选路由和 `runArbitrage` 等受控复核任务，不自行创建第二队列或第二主线程。
- 以 Closed-loop Manager 的闭环为输入。
- 调用静态预览、候选路由和 `runArbitrage` 复核。
- 每个已完成复核输出具体正负 PnL、单位、币种、来源、PoolKey 和跳次证据。
- revert、报价不可用、`runArbitrage` 未确认等原因是诊断，不得替代具体 PnL。
- 缺少具体 PnL 的复核进入 `data_incomplete`，必须修复并重试，不得进入完成态。
- 统一写入复核记录、队列/运行状态和证据，不直接发送交易。
- 正向但未确认的 rate delta 只能作为参考，不得触发广播。

数据范围：

```text
cow_execution_reviews
review evidence / diagnostics
```

### 2.9 Broadcast Manager

责任：

- 是广播工作的唯一管理工具，拥有一个广播主线程、唯一广播执行队列、有界广播
  工作线程池及其启动、停止、心跳、nonce 分配、RPC failover、回执观察和僵死恢复
  生命周期。
- 主线程只负责授权请求入队、派发、结果回收、重试和终态推进；工作线程只执行
  signer、gas、广播、回执等受控任务，不得自行创建第二执行队列或第二主线程。
- 只接受 Review Manager 已明确 `executionEligible=true` 的广播请求。
- 负责 signer、nonce、gas、RPC、failover、广播、回执和失败重试。
- 写入真实 broadcast attempt 与 success/failed 终态。
- 不计算套利 PnL，不修改闭环拓扑，不修改候选池或代币库。
- 失败必须保留错误、RPC、交易哈希（如有）和重试轨迹，不伪造成功。

数据范围：

```text
cow_execution_attempts
broadcast terminal state
```

### 2.10 Composite Manager

结论：需要增加一个“综合管理模块”，但职责必须很薄，只处理跨模块编排。

`manager_api` 是各管理工具的唯一 HTTP/JSON 入口、DTO/错误转换和命令转发层，
不是线程、队列或 worker 的所有者。UI 不直接调用 `manager_api`，而是消费 Rust
`RuntimeCore` 通过 PyO3 暴露的只读 DTO；Python 只保留薄适配，不实现权威大对象、
业务缓存或跨 Manager 协调。

Review/Broadcast 工具化的首选手段是重组现有运行能力，而不是平行开发新实现：
把主线程、队列、worker、执行适配器和状态模块收口到对应的 Rust Manager crate，
保持业务行为，再暴露命令/状态接口。旧 Python 文件迁移期间只保留测试或 legacy
fallback，不得拥有 native 生产主路径。

2026-11-06 首批 Rust 收口已完成：复核主线程/并发所有权进入
`tools/review_manager`，广播主线程/执行队列所有权进入
`tools/broadcast_manager`；`rs-src/src/worker_pool.rs` 只保留 PyO3 宿主和执行
适配。不存在 `dex/managers/*` Python 权威实现，后续不得并行新建第二套运行时。

纳入条件：

- 一次任务需要多个模块按依赖顺序刷新。
- 上游版本变化后需要下游重建。
- 需要保证候选、YZ、闭环和复核使用同一组库版本和池版本。
- 需要输出统一 pipeline 状态、失败点、重试点和审计记录。

不纳入条件：

- 单个模块内部的池质量判断、PnL 计算、交易签名或页面业务逻辑。

数据范围：

```text
manager_pipeline_runs
manager_pipeline_steps
manager_events
```

综合管理模块不得复制业务表内容，不得代替任何业务模块写自己的业务表。
UI 页面可以继续调用原页面接口，由兼容层把请求转给对应模块；页面结构保持不变。

### 2.11 Rust RuntimeCore 大对象

`RuntimeCore` 是 UI 与 Manager 工具之间唯一的内存大对象，必须由 Rust 实现并通过
PyO3 暴露给 Flask/UI。它不替代任何 Manager：

- 每个 chain 一个 `RuntimeCore`，持有 chain identity、Manager DTO 快照、聚合版本/
  checksum、pipeline 状态、最近事件序列和显式命令入口。
- 它按 `candidate_edge -> candidate_exchange -> closed_loop -> review -> broadcast
  -> composite` 协调读取顺序，但 durable 写入、版本校验和 checkpoint 仍由所属
  Rust Manager 负责。
- 它不得直接连接业务 SQLite、执行池质量判断、计算 PnL、签名或广播；不得拥有页面
  刷新定时器，也不得把 UI 展示状态写入业务表。
- Python `core/runtime/chain_runtime.py` 在迁移期只作为宿主和 property proxy；
  Python scanner、state machine 或 supervisor 不得继续承担权威大对象、业务缓存或
  跨 Manager 顺序协调。
- 完成门禁是：同一 chain 只有一个 `RuntimeCore`；Python 无权威业务缓存；UI 页面
  不直接调用 `manager_api`、SQLite 或 RPC；上游版本变化时 snapshot 原子拒绝而非
  混用。

2026-11-06 当前状态：Rust `RuntimeCore`、每 chain 注册表、Manager DTO 聚合刷新、
fingerprint 校验、PyO3 状态/快照/命令接口和单链关闭已经落地。Python
`ChainRuntimeContext` 只保留 chain identity、PyO3 调用和页面 DTO 映射，不再缓存
Manager snapshot 或 native pool。`native_pool_registry.rs` 已成为
`ClosedLoopWorkerPool` 的进程内 lifecycle owner；Python 字典只作为旧扩展 fallback。
wheel 以 `--auditwheel repair` 构建并在全新临时环境验证 9 个 RuntimeCore/native-pool
导出接口和实际池启停。2026-11-06 D9.27-D9.28 进一步把 overview、candidate edge、
candidate exchange、closed-loop、review 和 broadcast 常规页面投影收口到
`runtime_core_manager_view`；视图选项按白名单严格校验，Python 只做序列化和 PyO3
转发。D9.29 已把提交记录清除接入 `runtime_core_command("clear_broadcasts")`，
由 RuntimeCore 直接调用 Broadcast Manager 公共写入 API，并在 Rust 侧保留
`BROADCAST_MANAGER_AUTHORITY` 的 apply 门禁。D9.30 进一步把候选池刷新接入
`runtime_core_command("rebuild_candidate_edges")`，由 RuntimeCore 直接调用
Candidate Edge Pool Manager 公共写入 API；候选 authority 与广播 authority 同时
校验注册身份，legacy authority 只能 dry-run。D9.31 已把市场观察刷新接入
`runtime_core_command("rebuild_candidate_exchange_pools")`，由 RuntimeCore 直接
调用 Candidate Exchange Pool Manager；token、候选、候选交换和广播 authority
均独立校验注册身份。Flask 不再执行旧观测 builder、缓存或 SQLite 写入，只消费
M4 只读快照映射的页面 DTO。D9.32-D9.33 又把逐路线手动复核和手动广播接入
`manual_review`、`manual_broadcast` RuntimeCore 异步状态迁移命令，只唤醒既有
review worker 或 waiting execution task，不重新报价、不重算 PnL，也不回退
Python scanner。D9.34 进一步关闭 quote 页面活动按钮回退 legacy
`/api/dex/quote-route/test` 的路径，活动按钮只分发到手动复核或手动广播；
旧 `runRouteTest(...)` 和 route-test 接口只保留为回归比较、诊断与测试夹具。
D9.35 又移除 Flask data route 对 UI evidence JSON 的直接读取，提交记录继续只
消费 RuntimeCore/Manager DTO。
D9.36 把路由符号/地址身份解析从 market 下沉到
`core.route_symbol_identity`，`cow_execution_review` 不再跨包导入 market；
同时收口 5 个 silent broad exception，并清理空的嵌套 `app_runtime/flashloan`
目录。core 现在只读链 owned allowlist 和活动 SQLite `chain_tokens` 股票身份，
未知、缺失或歧义身份不猜测地址。
D9.37 继续把页面 token 查询 helper 独立到
`core.db.page_payload_tokens`，并把 market route helper 改为 core 身份模块的兼容
导出；page payload 和 route helper 均降到 1000 行以下，active source 行数债务
从 5 项降为 3 项。
下一阶段继续把其余页面显式命令逐步接入 RuntimeCore command surface，同时保持
现有 URL、权限和页面刷新周期不变。

编排约束：

- 每个 pipeline step 记录输入版本、输出版本、幂等键、状态和失败点。
- 跨模块流程不依赖单个跨库事务；各模块提交自己的事务，综合模块通过 checkpoint
  和版本一致性判断能否继续。
- 失败后从最后一个已提交步骤恢复，不重复广播，也不混用不同版本的上游快照。
- 单页 CRUD 和单模块刷新直接调用所属模块，不经过综合管理模块。

## 3. 接口契约

### 3.1 接口形态

每个 Rust 模块以 library crate 发布稳定接口，由单个 `manager_api` 进程统一提供
对外 HTTP/JSON。生产 UI 不分别连接多个模块服务，也不直接连接 SQLite；同进程
Flask 页面通过 Rust `RuntimeCore` 的 PyO3 view/command 接口调用所属 Manager，
`manager_api` 保留外部命令、比较诊断和尚未迁移命令的兼容入口。

```text
GET  /v1/tokens
POST /v1/tokens/admit
POST /v1/tokens/block
GET  /v1/tokens/audit

GET  /v1/pools
POST /v1/pools/refresh
POST /v1/pools/scope

GET  /v1/candidate-edge-pools
POST /v1/candidate-edge-pools/rebuild
POST /v1/candidate-edge-pools/refresh-key

GET  /v1/candidate-exchange-pools
POST /v1/candidate-exchange-pools/rebuild

GET  /v1/closed-loops
POST /v1/closed-loops/rebuild

GET  /v1/reviews
POST /v1/reviews/run

GET  /v1/broadcasts
POST /v1/broadcasts/submit

GET  /v1/pipeline/status
POST /v1/pipeline/refresh
```

### 3.2 统一 DTO

所有接口返回：

```json
{
  "ok": true,
  "version": "manager_dto_v1",
  "generatedAt": "2026-11-03T00:00:00Z",
  "chainId": 4663,
  "data": {},
  "meta": {},
  "upstreamSource": {
    "manager": "pool-manager",
    "fingerprint": "sha256:..."
  }
}
```

### 3.3 UI 契约

- UI 只与 Rust `RuntimeCore` 交换数据；资金池、候选池、市场观察/Y-Z、闭环、
  复核和广播页面不直接调用各自的 Manager 或 `manager_api`。compare-only 双读诊断、
  显式命令和兼容代理不属于页面业务读取路径。
- `RuntimeCore` 向对应 Pool/Candidate/Closed-loop/Review/Broadcast Manager 查询
  DTO；跨模块刷新和版本门禁由 Composite Manager 编排。
- 显式用户命令由 `RuntimeCore` 校验 request/idempotency/dryRun 后，调用所属
  Rust Manager 的公共写接口；尚未迁移的命令才由 `manager_api` 作为兼容入口。
- UI 页面保持不变，只替换后端实现和数据来源。
- 不清空、不直接修改 SQLite 数据。

## 4. 数据所有权

```text
Token Manager       -> token_core + token_stocks + token_extension + token_test
                       + token_library_state + token_admission_events
                       (+ chain_tokens 仅作兼容投影)
Pool Manager        -> v4_exchange_pools_* + v4_exchange_pool_liquidity_snapshots
Candidate Edge Pool Manager     -> dex_candidate_*
Candidate Exchange Pool Manager -> market_observation_*
Closed-loop Manager              -> v4_closed_loops*
Review Manager                   -> cow_execution_reviews + review evidence
Broadcast Manager                -> cow_execution_attempts + broadcast state
Composite Manager                -> manager_pipeline_* + manager_events
```

禁止：

- Candidate Edge Pool Manager 写 Token Manager 表或 Y-Z 池表。
- Candidate Exchange Pool Manager 写候选边池表或闭环表。
- Closed-loop Manager 生成 PnL 或发送交易。
- Review Manager 写广播 attempt 或修改闭环拓扑。
- Broadcast Manager 重新计算 PnL 或修改闭环拓扑。
- Composite Manager 绕过各模块直接写任意业务表。
- UI 绕过接口写任意业务表。

## 5. 迁移阶段

### 阶段 0（M0）：契约与基线

- 冻结各模块 DTO、错误码和版本号。
- 在 Rust workspace 中建立共享 crate：`manager_contracts`、`manager_db`、
  `manager_api`。
- 保留现有 Python 路径，只读运行。

里程碑编号对应实施手册中的 `M0-M9`。开发计划、提交说明和验收记录应使用同一
编号，不再使用另一套阶段名称。

### 阶段 1（M1）：Token Manager

- Rust Token Manager 落地到 `flashloan/tools/token_manager`。
- 新增 `token_core`、`token_stocks`、`token_extension`、`token_test` 四张表并
  实现迁移。
- 初始导入核心库 `core`、股票库 `stocks`、扩展库 `extension` 和测试库 `test`。
- 扩展库从现有池数据中筛选“与 core、stock 或 TZ 有直接 V4 池且池通过流动性
  门槛”的代币，并逐项确认 metadata 来源和 decimals。
- 首期使用现有 SQLite 基线增量导入，不做全量区块重放。
- 生成稳定的 `token_key_id` 和 `v4_currency_key`，禁止 symbol 作为外键。
- 封锁 Python 对 `chain_tokens` 的直接权威写入，只保留兼容投影。
- 以 `TOKEN_MANAGER_AUTHORITY` 完成 `legacy -> manager -> rollback -> manager`
  切换演练；回滚保留新表审计证据，生产切换只在独立维护窗口执行。
- UI 的代币下拉和符号解析改为 Token Manager 接口。

### 阶段 2（M2）：Pool Manager

- 实现启动模式与配置表映射。
- 先接入 `core4`，从 `token_core` 加载四币并生成独立池表，便于主网快速收敛。
- Pool Manager 只从 Token Manager 已准入代币构建池。
- 使用 `v4_currency_key` 组合 V4 `PoolKey`，由 `manager_contracts` 的唯一标准
  ABI 编码实现计算 `v4_pool_id`；`rs-src` 消费共享实现，不再保留本地第二份
  PoolId 算法。KEY ID、pool_key_json 和独立 scope 池表由 Pool Manager 管理。
- `core4` 使用 `edge_any`：池任一端命中 Token Manager 核心库 admitted 锚点
  即可进入 scope，两端都必须可解析为 admitted `token_key_id`。
- 候选池和下游 YZ 交换池不以美元深度、美元 TVL 或 USDG/USDE 关系为准入条件。
- scope apply 只允许在 `manager` authority 下执行；池行、`pool_scope_config` 与
  通用控制表 `manager_write_requests` 在同一个 `IMMEDIATE` 事务内提交。
- 同一幂等键和请求摘要重放首次结果，摘要变化拒绝为
  `IDEMPOTENCY_KEY_REUSED`；dry-run 不创建幂等或业务记录。
- Python 初始化入口改为调用 Rust Pool Manager 或停止直接写池。

### 阶段 3（M3）：Candidate Edge Pool Manager

- 从 `Pool Manager` 合格池构建 YC/ZC。
- 迁移 `dex_candidate_*` 写入。
- 旧 Python builder 转为只读兼容。

### 阶段 4（M4）：Candidate Exchange Pool Manager

- 从候选池 YC/ZC 构建有序 Y-Z 对和中间池。
- 迁移 `market_observation_*` 写入。
- 输出 clear fingerprint 供闭环校验。
- 市场观察刷新通过 RuntimeCore `rebuild_candidate_exchange_pools` 调用本模块，
  operation 固定为 `candidate-exchange.rebuild`；RuntimeCore 不复制观察池算法。
- 候选池和 YZ 交换池不要求美元深度、美元 TVL 或 USDG/USDE 关系；只按已准入
  token、合格直接池、固定费/零 hook/流动性等 manager 质量条件处理。
- M4 UI 路径和临时库验证完成不改变生产 authority；主网只有
  `CANDIDATE_EXCHANGE_MANAGER_AUTHORITY=manager` 且真实副本双读、P0 和维护窗口
  门禁通过后才允许生产 apply。

### 阶段 5（M5）：Closed-loop Manager

- 从 YZ 和候选池构建闭环。
- 只维护闭环定义、路径和状态。

### 阶段 6（M6）：Review Manager

- 从闭环构建复核任务。
- 接入具体 signed PnL、单位、币种、来源、可执行性复核和结构化诊断。
- 缺少具体 PnL 的任务保持 `data_incomplete` 并修复重试，不能计为完成。
- 只有 PnL 有具体正数值且链上可执行时才生成广播请求。

### 阶段 7（M7）：Broadcast Manager

- 接受已确认的广播请求。
- 接入 signer、nonce、gas、RPC failover、广播回执和终态落库。

### 阶段 8（M8）：Composite Manager

- 接入跨模块刷新、版本一致性、重试点和 pipeline 审计。
- 不复制业务逻辑，不直接写业务表。

### 阶段 9（M9）：UI 兼容与 Python 退役

- UI 页面保持不变；Flask 兼容层通过 Rust `RuntimeCore.manager_view` 调用对应管理
  模块只读接口，通过 RuntimeCore command surface 调用已迁移显式命令，只有比较诊断
  和未迁移命令才使用 `manager_api`。
- quote 页面的旧 `/api/dex/quote-route/test` 只允许作为诊断夹具存在；活动路线
  按钮必须通过 RuntimeCore `manual_review` 或 `manual_broadcast` command 进入
  异步状态迁移，非四段闭环在客户端返回 `closed_loop_route_unsupported`，不得借
  route-test 恢复估算、报价或复核投递。
- Flask 页面读取不得直读 UI evidence JSON 或按文件补算交易盈亏；提交记录、
  复核和广播只消费 RuntimeCore/Manager DTO。实际 receipt 盈亏若需展示，由
  Broadcast Manager 发布结构化接口，不能恢复 Flask 对 `latest.json` 的路径。
- Python 直查 SQLite 的 API 逐步退役。
- 主网运行只依赖 Rust 管理模块和只读 SQLite 快照。

## 6. 验收标准

1. Token Manager 是唯一代币准入入口。
2. 任何未准入代币不会出现在池、候选池、YZ、闭环或 UI。
3. Candidate Edge Pool Manager 和 Candidate Exchange Pool Manager 的库表写入边界独立。
4. `core4` 只由 Token Manager 维护；Pool Manager 仅引用其库版本，并按显式池
   筛选模式生成独立池表。
5. 候选池更新后 YZ 和闭环必须重建到一致版本，不能出现跨版本混合。
6. Closed-loop Manager 只维护闭环，Review Manager 必须给出具体 signed PnL，
   缺值不得完成，Broadcast Manager 只广播已确认请求。
7. 有正收益且在链上可执行时，由 Review Manager 生成请求、Broadcast Manager 广播；失败可重试并留痕。
8. Composite Manager 能输出跨模块刷新和版本一致性结果，但不复制业务逻辑。
9. UI 页面保持不变且不再直查 SQLite。
10. Rust 单测和端到端集成测试通过，主网观察阶段可用当前 DB 并行比对。

### 6.1 阶段交付表

| 阶段 | 代码落点 | 完成标准 |
|---|---|---|
| 0 契约与基线 | `manager_contracts`、`manager_db`、`manager_api` | DTO、错误码、版本号冻结，Python 路径保持只读运行 |
| 1 Token Manager | `tools/token_manager` | 四张库表迁移、导入、KEY ID、API 和单测通过，symbol 不进入下游主键 |
| 2 Pool Manager | `tools/pool_manager` | `core4` 通过 `token_core` 生成 `v4_exchange_pools_core4`，PoolKey/PoolId 一致 |
| 3 Candidate Edge | `tools/candidate_edge_pool_manager` | Rust 唯一写 `dex_candidate_*`，候选代币全部 admitted |
| 4 Candidate Exchange | `tools/candidate_exchange_pool_manager` | Rust 唯一写 `market_observation_*`，Y-Z 版本摘要可校验 |
| 5 Closed-loop | `tools/closed_loop_manager` | 闭环只依赖上游版本，无跨版本混合 |
| 6 Review | `tools/review_manager` | 每个完成复核有具体 signed PnL，缺值进入 `data_incomplete` |
| 7 Broadcast | `tools/broadcast_manager` | 只处理已确认请求，attempt 均有 success/failed 终态 |
| 8 Composite | `tools/composite_manager` | pipeline checkpoint、幂等重试和审计表可用 |
| 9 UI 兼容 | 现有 Flask API 兼容层 | 页面不变，所有业务读写转交对应 Rust 模块 |

## 7. 与当前改造的衔接

- 已存在的 `flashloan/tools/token_manager` 从框架升级为阶段 1 实现。
- 已存在的 `flashloan/tools/pool_manager` 从只读审计升级为阶段 2 实现。
- `flashloan/src/dex/intent_trade/chains/robinhood_common/v4_pool_scope.py`
  保留启动模式解析，不再返回硬编码地址，而是读取 Token Manager 输出。
- 运行中的 Python 服务保留到 Rust 模块完成同口径验收，不主动删除。

## 8. 实施入口

编码、迁移和验收不得只依据本节概览，必须同时遵循
[Rust管理模块实施手册_20261103.md](Rust管理模块实施手册_20261103.md)。实施手册
中的四库 DDL、固定 scope 表、KEY ID 解析链和测试矩阵是阶段交付物的一部分。

## 9. D9.38 Python 兼容门面重组边界

Python 过渡层现在采用“兼容入口 + 领域实现模块”的结构，兼容入口只 re-export
既有公开 API，不新增第二套业务权威：

- COW 执行仓储保留 `storage_cow_execution.py` 兼容入口，schema/query/reader/
  writer 分别承担迁移、范围查询、读取和写入。
- 扫描器保留 `dex_opportunity_scanner_engine.py` 兼容入口，具体线程、状态、
  记录和单次扫描流程由 mixin 子模块组合。
- Manager 页面适配保留 `manager_page_compare.py` 兼容 barrel，候选池、闭环、
  市场观察、提交记录、Overview、Quote 和通用 envelope 按领域拆分。
- 兼容入口必须继续支持旧的 monkeypatch 点，例如 `ManagerApiClient`、
  `runtime_core_manager_overview_envelope` 和扫描器门面上的原生池函数。
- 该重组只服务 M9 过渡与 Python 退役，不改变 Rust Manager 的唯一业务权威，
  也不改变 M7 真实广播所需的 P0 门禁。

## 10. D9.39 广播失败记录的 Manager 权威投影

D9.39 是 M9 页面读取边界的增量收口，不是新的广播执行实现：

- `manager_broadcast_failure_payload.py` 固定从 Broadcast Manager 的失败 attempt
  快照读取，并将 `attempts` 与 `diagnostics` 映射为现有提交记录页 DTO；页面继续
  保留原 URL、结构和刷新节奏。
- `broadcast_failure_history/*.jsonl` 和 `core.broadcast_failure_audit` 只允许
  作为历史诊断/测试工具，不得再作为 UI 业务数据源；Flask 路由不允许在 Manager
  不可用时回退到它们。Manager 读取失败必须 fail-closed 并返回 503。
- 失败 DTO 必须保留旧页面依赖的 `groupKey`、`routePath`、`broadcastError`、
  `selectedProfitRaw`，同时携带 Manager attempt 的终态、交易哈希、nonce、RPC
  和结构化诊断；Flask 不补算交易 PnL。
- `limit=0` 保留旧接口空结果语义。由于 `/v1/broadcasts` 的公开契约要求
  `limit` 在 1..1000，投影层可用 `limit=1` 探测 Manager 后裁剪 records，不能
  放宽或修改 Manager API 的限制。
- 证据：`runtime_evidence/reports/d9_39_broadcast_failure_manager_projection_20261106.json`。

## 11. D9.40 迁移页面死旧 SQLite 路径退役边界

M9 已切换页面模块不允许继续保留“看起来像 UI 数据源”的死旧 SQLite 包装：

- `dex_surface_routes_ops.py` 删除提交记录响应缓存、旧 review group loader、
  旧 record API payload 和旧分页/chain filter；页面路由继续只调用 manager payload
  或 RuntimeCore command。
- `dex_surface_helpers.py` 删除无活动调用的
  `_merge_recent_attempt_evidence_into_routes` 及其 SQLite/RPC/链名依赖，
  `dex_surfaces.py` 同步移除导入。
- 存储层 Python 函数仍可保留在 `core/db/storage_cow_execution*.py`，作测试和
  独立诊断工具；任何 DEX 已切换页面不得恢复直读 SQLite 或 evidence JSONL。
- 架构守卫和证据：`d9_40_dead_legacy_sqlite_page_paths_20261106.json`。

## 12. D9.41 控制面板 scanner 生命周期只读边界

控制面板是最后一个仍把 Python scanner 内存态作为正式 UI 入口的位置。D9.41
将其收口到 Rust RuntimeCore/ClosedLoopWorkerPool 只读 DTO：

- `/api/status` 的 `dex_opportunity_scanner` 保留兼容字段名，但内容改为
  RuntimeCore 状态、闭环复核队列、执行队列、maintenance、worker 概要和
  广播门禁；不再调用 `get_dex_opportunity_scanner()`，也不再生成
  `running/scan_count/interval_seconds/auto_execute_count` 等 Python scanner
  session 字段。
- 控制面板删除“自动扫描复核并链上广播”checkbox 和 localStorage 开关；页面
  只读展示 RuntimeCore/Manager 可用性、队列状态和 `broadcastGate`。待 M8
  pipeline command 接口确定后再考虑 UI 写入口。
- `opportunity-scanner` 的 start/snapshot/scan-now/auto-execute/stats/reset
  路由继续保留为维护诊断入口，不由正式页面重新绑定。
- 架构守卫检查控制面板源码/模板和 DEX 正式页面不恢复旧 scanner 生命周期接入；
  证据为 `runtime_evidence/reports/d9_41_control_panel_scanner_lifecycle_boundary_20261107.json`。
