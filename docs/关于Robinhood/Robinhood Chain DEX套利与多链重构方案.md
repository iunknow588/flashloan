# Robinhood Chain DEX套利与多链重构方案

更新时间：2026-08-18

## 一、目标边界

本次重构的目标不是为 Robinhood Chain 单独另起一套系统，而是把现有框架稳定下来：所有链都走同一条业务流程，不同链只通过不同的市场参数、协议适配器、执行适配器完成各自功能。

优先级如下：

1. 先完成 DEX 套利相关能力：链配置、RPC、Token/DEX 注册、池发现、报价、机会分层、影子预演、执行准入。
2. 再实现自动清算相关能力：借贷协议发现、账户池、健康度扫描、核心/高频/普通分池、清算预演、闪电贷执行。
3. Robinhood Chain 在没有完成合约执行器和协议适配验收前，必须保持 observation/shadow 模式，不能展示为“可自动执行”。

## 二、官方资料结论

Robinhood Chain 是 Arbitrum Layer-2，EVM 兼容，主网 Chain ID 为 `4663`，测试网 Chain ID 为 `46630`，原生 gas token 为 `ETH`。官方推荐 Alchemy RPC，同时提供公共 RPC 和 Blockscout 浏览器。

官方部署文档说明 Solidity/Vyper 合约可以按标准 EVM 工具链部署，但部署前仍需要 Robinhood Chain 上的 `ETH` 支付 gas。因此系统不能按“链级免 gas”设计执行器。

Account Abstraction 文档说明 Robinhood Chain 支持 ERC-4337 和 EIP-7702，Alchemy/ZeroDev 等服务支持 gas sponsorship、批量交易、session key 等能力。这里的“gasless”应理解为 AA/paymaster 赞助路径，而不是任意机器人交易天然免 gas。

官方生态页显示 Robinhood Chain 生态包含 Uniswap、Rialto、Morpho 等方向，这意味着 DEX 套利和清算都具备调研价值；但每个协议是否能接入，必须以合约地址、ABI、池数据、报价路径、执行预演结果为准。

参考链接：

- 连接网络/RPC：https://docs.robinhood.com/chain/connecting
- 部署智能合约：https://docs.robinhood.com/chain/deploy-smart-contracts
- 协议合约地址：https://docs.robinhood.com/chain/protocol-contracts
- Account Abstraction：https://docs.robinhood.com/chain/account-abstraction
- 运行节点：https://docs.robinhood.com/chain/run-a-full-node

## 三、统一框架原则

### 1. 流程统一

所有链都应保持同一条高层流程：

`market registry -> chain client -> protocol adapter -> data scanner -> current pool -> tier classifier -> quote/simulation -> execution gate -> execution adapter -> evidence/log`

不能出现以下情况：

- 页面选择 BNB 后，后台接口仍读取 Avalanche 默认链。
- DEX 套利走一套链配置，清算走另一套链配置。
- 扫描批次被当成当前账户池展示。
- 某条链使用硬编码全局变量绕过 `market_id/chain_id`。
- 因 Robinhood 支持 AA，就跳过普通 gas、利润、滑点、失败回滚检查。

### 2. 参数分链

链差异只允许进入以下参数层：

- `chain_id`
- `network`
- `native_symbol`
- `rpc_urls`
- `ws_urls`
- `explorer_url`
- `multicall_address`
- `wrapped_native_address`
- `dex_factories`
- `dex_routers`
- `quoters`
- `lending_protocol_addresses`
- `flashloan_provider`
- `executor_address`
- `gas_policy`
- `aa_policy`
- `scan_policy`
- `risk_thresholds`

业务流程、页面接口、机会分池、证据记录和执行保护必须保持一致。

### 3. 市场唯一性

不能只用 `chain_id` 判断市场，因为同一条链可能有多个协议市场。例如 BNB Chain 上可以同时存在 Venus、PancakeSwap、其他借贷协议或 DEX 市场。

建议统一使用：

- `market_id`：业务市场唯一键，例如 `bnb-venus-core`、`avalanche-aave-v3`、`robinhood-uniswap-v3`、`robinhood-morpho`
- `chain_id`：链 ID，例如 `56`、`43114`、`4663`
- `protocol`：协议类型，例如 `venus`、`aave_v3`、`uniswap_v3`、`morpho`
- `feature`：功能类型，例如 `dex_arbitrage`、`liquidation`

## 四、数据库重构方案

### 1. 市场注册表

新增统一市场注册表 `liquidation_markets` 或更通用的 `chain_markets`。如果后续 DEX 与清算共用多链配置，推荐使用更通用命名 `chain_markets`。

建议字段：

| 字段 | 说明 |
|---|---|
| `market_id` | 市场唯一键 |
| `chain_id` | 链 ID |
| `network` | 网络名 |
| `feature` | `dex_arbitrage` / `liquidation` / `both` |
| `protocol` | 协议名 |
| `native_symbol` | 原生 gas token |
| `wrapped_native_address` | WETH/WBNB/WAVAX 等 |
| `rpc_urls_json` | RPC 列表 |
| `ws_urls_json` | WebSocket 列表 |
| `explorer_url` | 浏览器地址 |
| `multicall_address` | Multicall 地址 |
| `protocol_addresses_json` | 协议合约地址 |
| `dex_addresses_json` | DEX factory/router/quoter 地址 |
| `flashloan_provider` | 闪电贷来源 |
| `executor_address` | 当前执行器地址 |
| `protocol_adapter` | 数据/健康度适配器 |
| `execution_adapter` | 执行适配器 |
| `gas_policy_json` | gas 和利润保护 |
| `aa_policy_json` | AA/paymaster 策略 |
| `observation_only` | 是否只观察 |
| `enabled` | 是否启用 |
| `is_default` | 是否默认市场 |
| `metadata_json` | 额外信息 |

### 2. 扫描策略表

新增 `market_scan_policies`：

| 字段 | 说明 |
|---|---|
| `market_id` | 关联市场 |
| `pool_tier` | `ordinary` / `high_frequency` / `core` |
| `hf_min` | 健康度下界 |
| `hf_max` | 健康度上界 |
| `refresh_interval_seconds` | 扫描间隔 |
| `page_size` | 页面默认分页 |
| `enabled` | 是否启用 |
| `metadata_json` | 链/协议特殊参数 |

这样 BNB、Avalanche、Robinhood 都可以保留同一个“三池结构”，差异只在策略参数。

### 3. DEX 市场表

DEX 套利建议新增或统一以下表：

- `dex_markets`：DEX 协议和链级配置。
- `dex_tokens`：Token 注册表，按 `chain_id + address` 唯一。
- `dex_pools`：池子注册表，按 `chain_id + pool_address` 唯一。
- `dex_pool_snapshots`：池状态快照，例如 liquidity、tick、reserve、tvl。
- `dex_arbitrage_candidates`：当前候选机会池。
- `dex_arbitrage_attempts`：预演和执行证据。

候选机会也应区分当前池和历史批次。页面永远展示当前候选机会；扫描批次只用于审计、恢复和历史回放。

## 五、Robinhood DEX 套利开发方案

### 阶段 1：只读链配置

新增 Robinhood Chain 配置：

- `network = robinhood`
- `chain_id = 4663`
- `testnet_network = robinhood-testnet`
- `testnet_chain_id = 46630`
- `native_symbol = ETH`
- `rpc_env = ROBINHOOD_RPC_URL, ROBINHOOD_RPC, ROBINHOOD_RPCS`
- `ws_env = ROBINHOOD_WS_URL, ROBINHOOD_WS, ROBINHOOD_WSS`
- `explorer = https://robinhoodchain.blockscout.com`
- `weth = 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73`
- `multicall = 0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1`

先接入 `eth_chainId`、`eth_blockNumber`、`getCode`、`multicall` 可用性检查。

### 阶段 2：DEX 发现

不要手工猜测 DEX 地址。必须从以下来源确认：

- Robinhood 官方生态页和协议合约文档。
- Uniswap 官方部署地址或合约注册信息。
- Blockscout 已验证合约。
- 协议官方文档。
- 链上 factory 事件。

确认后写入 `dex_addresses_json`，并记录来源 URL、确认时间、确认方法。

### 阶段 3：静态池表

复用 BNB 的静态池表思路：

- `network` 必须是 `robinhood`
- `chainId` 必须是 `4663`
- 每个池必须包含 `dex`、`protocol`、`factory`、`pool`、`symbolPair`、`token0`、`token1`、`fee`、`status`、`tvlUsd`
- 加载时必须校验 network/chainId，防止 Robinhood 页面读到 Avalanche 或 BNB 池表

初期 `status` 只允许：

- `recommended`：地址、流动性、报价、代码都已验证
- `observable`：可观察但未达到执行质量
- `rejected`：明确不用于套利

### 阶段 4：影子报价

Robinhood 第一版只做：

- 三角路径生成
- 最优边选择
- router/quoter 静态调用
- gas 估算
- ETH/USDC gas 成本换算
- 滑点保护
- 机会证据记录

输出必须标记：

- `mode = shadow_quote_only`
- `executionEnabled = false`
- `broadcastEnabled = false`
- `nextStage = live_router_quote_then_fork_simulation`

### 阶段 5：执行准入

只有满足以下条件后，才允许从 shadow 转为 execution：

1. Robinhood 上的 DEX 地址、池地址、quoter/router 都通过链上验证。
2. executor 合约已部署，并在测试网和主网上完成静态调用、fork 测试、小额测试。
3. flashloan provider 已确认，且 token 流动性覆盖目标资产。
4. gas 成本、L1 data fee、滑点、失败回滚、MEV/排序风险都进入净利润模型。
5. AA/paymaster 如果启用，必须作为 `aa_policy` 独立开关，失败时不能自动绕过执行保护。

## 六、Robinhood 自动清算开发方案

自动清算晚于 DEX 套利开发。清算接入前必须先回答三个问题：

1. Robinhood 上的目标借贷协议是什么：Morpho、Aave-like，还是其他协议？
2. 账户健康度如何读取：是否存在 user account data、market position、oracle price、liquidation threshold？
3. 清算如何执行：是否有 flashloan、是否需要 collateral unwrap/swap、是否有协议级限制？

清算仍保留原系统框架：

- 当前账户池是页面展示权威来源。
- 扫描批次是临时过程和历史记录。
- 普通账号、高频账号、核心账号按健康度和策略参数分开展示。
- 每个池有自己的扫描频率。
- 小于 1 的账户不是核心机会的主要展示意义，核心池应关注即将进入清算且存在可执行利润窗口的账户。

Robinhood 清算初期必须为：

- `observation_only = true`
- `execution_adapter = unverified`
- `flashloan_provider = unverified`
- 页面不显示“可执行清算”

## 七、页面和接口重构要求

所有页面接口必须从请求上下文提取：

- `market_id`
- `chain_id`
- `feature`

并向下传递到：

- market payload
- config health
- scan status
- account pool
- opportunity pool
- execution preview
- execution action

禁止接口内部回退到 Avalanche 默认值，除非请求没有任何市场参数且系统默认市场就是 Avalanche。

页面展示应分为：

- 链/市场选择区
- 当前市场状态
- 当前账户池或当前 DEX 候选池
- 三池分布
- 最近扫描批次，仅作为审计信息
- 执行保护状态

## 八、执行安全原则

Robinhood DEX 和清算都必须遵守：

- 默认只读。
- 无 executor 不执行。
- 无 flashloan provider 不执行。
- 无链上静态调用成功不执行。
- 无 fork simulation 不执行。
- 无净利润模型不执行。
- 无私钥环境配置不执行。
- AA/paymaster 不是利润来源，只是支付路径优化。
- 任何链切换都必须重新加载 market config，不能沿用上一条链的缓存。

## 九、验收标准

DEX 套利第一阶段验收：

- 能选择 Robinhood Chain 市场。
- 能显示 chain id、RPC、WETH、Multicall、explorer。
- 能加载 Robinhood 静态池表并拒绝错误链表。
- 能生成 `shadow_quote_only` 意图。
- 能证明 direct execution 对 Robinhood 是禁用状态。
- 不影响 BNB/Venus 和 Avalanche 原有页面。

DEX 套利第二阶段验收：

- 能发现并验证真实 Robinhood DEX 池。
- 能完成 live quote。
- 能记录候选机会和失败原因。
- 能展示当前 DEX 候选机会，而不是历史扫描批次。

清算阶段验收：

- 能确认 Robinhood 借贷协议。
- 能建立当前健康度账户池。
- 能按普通/高频/核心三池展示。
- 能完成清算预演但默认不广播。
- 只有 executor、flashloan、利润、gas、回滚保护全部通过后才允许广播。

## 十、当前代码落地状态

截至 2026-08-17，Robinhood DEX 套利已完成第一层代码骨架，但仍处于影子模式：

- 已新增 `intent_trade/chains/robinhood` 链模块。
- 已加入 Robinhood 主网 `4663` 和测试网 `46630` 的 direct network alias。
- 已配置官方链级地址：WETH、Multicall、Permit2。
- 已保留 DEX router/factory/quoter 为空，等待官方、Blockscout 或协议文档验证后再写入。
- 已实现 Robinhood 静态池表校验：只接受 `network=robinhood` 且 `chainId=4663`。
- 已实现 Robinhood `shadow_quote_only` intent draft。
- 已明确禁用 direct execution，返回 `robinhood_executor_adapter_unverified`。
- 已完成聚焦测试：Robinhood intent、BNB intent、direct builder 共 `114 passed`。

这意味着当前系统可以安全地继续做 Robinhood DEX 观察、静态表验证、候选机会建模，但不能执行真实套利交易。

## 十一、统一状态机

为了保证多链流程稳定，建议所有市场都使用同一个状态机，而不是每条链自己定义状态。

### 1. 市场状态

| 状态 | 含义 | 是否允许执行 |
|---|---|---:|
| `disabled` | 市场未启用 | 否 |
| `configured` | 链参数存在，但未完成 RPC/合约检查 | 否 |
| `readonly_ready` | RPC、chainId、基础合约可读 | 否 |
| `observing` | 可扫描池、账户或候选机会 | 否 |
| `shadow_quoting` | 可做报价和预演，不广播 | 否 |
| `simulation_ready` | fork/staticcall 预演通过 | 否 |
| `execution_ready` | executor、flashloan、利润、gas、风控全通过 | 是 |
| `paused` | 手动或自动暂停 | 否 |

Robinhood 当前应停留在 `shadow_quoting` 之前，即 `readonly_ready` 到 `observing` 之间。只有完成真实 DEX 地址发现、live quote 和 fork simulation 后，才能进入 `shadow_quoting`。

### 2. 候选机会状态

DEX 和清算可以共用一套候选状态：

| 状态 | 说明 |
|---|---|
| `discovered` | 从扫描器发现 |
| `validated_static` | 地址、链 ID、表结构通过校验 |
| `quoted` | 完成 live quote |
| `simulated` | 完成 staticcall/fork simulation |
| `profitable_after_costs` | 扣除 gas、滑点、费用后仍盈利 |
| `blocked` | 被执行保护阻断 |
| `expired` | 状态过期 |
| `executed` | 已提交并成功 |
| `failed` | 提交或执行失败 |

页面展示“核心机会”时，不应只看 `health_factor < 1` 或单个收益字段，而应显示 `profitable_after_costs` 且未过期、未阻断的候选。

## 十二、DB DDL 草案

以下是建议 DDL 草案，实际落地时可以按当前数据库迁移框架调整命名。

```sql
create table if not exists chain_markets (
    market_id text primary key,
    chain_id bigint not null,
    network text not null,
    feature text not null,
    protocol text not null,
    display_name text not null,
    native_symbol text not null,
    wrapped_native_address text,
    rpc_urls_json jsonb not null default '[]'::jsonb,
    ws_urls_json jsonb not null default '[]'::jsonb,
    explorer_url text,
    multicall_address text,
    protocol_addresses_json jsonb not null default '{}'::jsonb,
    dex_addresses_json jsonb not null default '{}'::jsonb,
    flashloan_provider text,
    executor_address text,
    protocol_adapter text not null,
    execution_adapter text not null,
    gas_policy_json jsonb not null default '{}'::jsonb,
    aa_policy_json jsonb not null default '{}'::jsonb,
    observation_only boolean not null default true,
    enabled boolean not null default true,
    is_default boolean not null default false,
    metadata_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists chain_markets_default_feature_idx
on chain_markets(feature)
where is_default;

create index if not exists chain_markets_chain_feature_idx
on chain_markets(chain_id, feature);
```

```sql
create table if not exists market_scan_policies (
    id bigserial primary key,
    market_id text not null references chain_markets(market_id),
    feature text not null,
    pool_tier text not null,
    metric_name text not null,
    metric_min numeric,
    metric_max numeric,
    refresh_interval_seconds integer not null,
    page_size integer not null default 20,
    enabled boolean not null default true,
    metadata_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (market_id, feature, pool_tier)
);
```

DEX 当前候选池建议独立于清算账户池：

```sql
create table if not exists dex_arbitrage_candidates (
    id bigserial primary key,
    market_id text not null references chain_markets(market_id),
    chain_id bigint not null,
    network text not null,
    route_key text not null,
    route_symbols_json jsonb not null,
    pool_addresses_json jsonb not null,
    status text not null,
    gross_profit_usd numeric,
    estimated_cost_usd numeric,
    net_profit_usd numeric,
    evidence_json jsonb not null default '{}'::jsonb,
    observed_block bigint,
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists dex_arbitrage_candidates_current_idx
on dex_arbitrage_candidates(market_id, status, expires_at);
```

## 十三、Market Config 解析顺序

建议所有功能统一采用以下解析顺序：

1. 请求参数：`market_id`、`chain_id`、`feature`。
2. 数据库 `chain_markets`。
3. 代码内置 fallback。
4. 环境变量覆盖敏感字段，例如 RPC、私钥、executor。
5. 运行时健康检查结果。

任何接口只要收到明确 `market_id`，就不能再使用全局默认市场。只有请求完全没有市场参数时，才允许使用 feature 默认市场。

解析结果应输出一个统一对象：

```json
{
  "marketId": "robinhood-uniswap-v3",
  "chainId": 4663,
  "network": "robinhood",
  "feature": "dex_arbitrage",
  "protocol": "uniswap_v3",
  "observationOnly": true,
  "executionAdapter": "unverified",
  "rpcHealth": "unknown",
  "executionStatus": "blocked"
}
```

页面、扫描器、报价器、执行器都只读取这个解析对象，不再各自拼链参数。

## 十四、Robinhood DEX 发现证据包

Robinhood DEX 地址和池子上线前，必须为每个 DEX 生成证据包，避免把第三方文章或未验证地址直接写入执行配置。

建议目录：

`runtime/evidence/robinhood/dex_discovery/<run_id>/`

建议文件：

- `source_urls.json`：官方文档、协议文档、Blockscout 链接。
- `contracts.json`：factory、router、quoter、permit2、multicall 地址。
- `code_hashes.json`：`eth_getCode` hash。
- `factory_events.json`：`PoolCreated` 或等价事件样本。
- `token_allowlist.json`：允许参与报价的 token。
- `token_risk_report.json`：黑名单、转账限制、税费 token、RWA/股票 token 限制。
- `pool_static_table.json`：最终静态池表。
- `quote_smoke_report.json`：live quote 结果。
- `summary.md`：人工可读结论。

证据包必须能回答：

1. 地址来源是什么？
2. 合约代码是否存在？
3. 是否为目标链 `4663`？
4. 池是否有足够流动性？
5. token 是否可自由转账和兑换？
6. 是否适合进入 shadow quote？
7. 为什么还不能执行，或为什么可以进入下一阶段？

## 十五、AA / Paymaster 接入边界

Robinhood 支持 ERC-4337 和 EIP-7702，但系统应把 AA/paymaster 当作支付策略，不当作盈利策略。

AA 接入必须满足：

- `aa_policy.enabled = true` 才启用。
- `paymaster_provider` 明确，例如 Alchemy 或 ZeroDev。
- `sponsor_scope` 明确，例如只读测试、测试网、小额主网。
- `allowed_targets` 明确，不能赞助任意合约调用。
- `max_sponsored_gas_usd` 明确。
- `fallback_to_eoa_gas` 明确。
- paymaster 失败时，交易必须重新走普通 gas 净利润检查。

执行决策顺序：

1. 先按普通 ETH gas 计算净利润。
2. 再计算 paymaster 可用时的优化成本。
3. 如果 paymaster 不可用，普通净利润不通过，则不执行。
4. 如果普通净利润通过，paymaster 只是降低成本，不改变安全门槛。

## 十六、迁移节奏

建议分四个 PR 或四个本地开发阶段推进：

### 阶段 A：配置统一

- 引入 `chain_markets` fallback 模型。
- 页面和 API 全部走 `market_id/chain_id/feature`。
- 保证 BNB 不再切回 Avalanche。
- 保证 Neon 数据库不可用时仍可显示只读状态。

### 阶段 B：Robinhood DEX shadow

- 使用当前 Robinhood chain module。
- 建立静态池表生成器。
- 发现 DEX 地址和池。
- 完成 live quote 但禁止广播。

### 阶段 C：执行器验收

- 部署 Robinhood DEX executor。
- 验证 flashloan provider。
- 完成测试网、小额主网、fork simulation。
- 通过净利润模型和执行保护。

### 阶段 D：清算后置

- 调研 Robinhood 上借贷协议。
- 建立健康度 current pool。
- 沿用普通/高频/核心三池。
- 清算先预演，后执行。

## 十七、当前不应做的事

- 不应把 Robinhood Wallet gas sponsorship 当作机器人免 gas。
- 不应在没有协议地址和 ABI 时声称 Robinhood 可以自动清算。
- 不应把 `chain_id=4663` 等同于某个 DEX 或借贷协议。
- 不应把历史扫描批次数量作为当前机会数量。
- 不应让页面、扫描器、执行器各自维护链配置。
- 不应复用 BNB/Venus 或 Avalanche/Aave 的协议适配器去解释 Robinhood 未验证协议。

## 十八、2026-08-18 重构实施进展

本轮已经把文档中的第一批重构任务落入系统：

1. 市场注册 fallback 已完善。
   - 新增内置市场 `robinhood-dex-shadow`。
   - `feature=dex_arbitrage` + `chain_id=4663` 会解析到 Robinhood DEX shadow market。
   - BNB `chain_id=56` 仍优先保留为 liquidation 场景的 `bnb-venus-core`，避免同链多市场互相覆盖。

2. 数据库市场注册 schema 已加入。
   - 新增 `chain_markets`。
   - 新增 `market_scan_policies`。
   - 新增 `dex_arbitrage_candidates`。
   - 新增 migration id：`20260818_chain_market_registry`。
   - Schema 初始化会 seed Avalanche、BNB Venus、Robinhood DEX shadow。

3. DB 优先、内置兜底已实现。
   - `supported_market_summaries(database_url)` 在 DB 可读时读取 `chain_markets`。
   - DB 不可用时自动回退到内置市场，不阻断页面。
   - 页面 market payload 已接入这个入口。

4. Robinhood RPC 只读探针已实现。
   - 工具：`flashloan/src/market/tools/probe_robinhood_chain_health.py`
   - 检查项：`eth_chainId`、`eth_blockNumber`、WETH `eth_getCode`、Multicall `eth_getCode`。
   - 实测公共 RPC 通过：chain id 为 `4663`，最新区块可读，WETH/Multicall 均有合约代码。

5. 执行边界保持不变。
   - Robinhood 仍为 shadow/readonly。
   - 未接入真实 DEX factory/router/quoter。
   - 未部署 Robinhood executor。
   - 未启用 Robinhood 自动清算。

本轮测试：

```powershell
# 当前单元测试入口；原 src_bot/tests 已迁移到 app_runtime/tests
python -m pytest flashloan/app_runtime/tests/unit -q
```

结果：历史 `143 passed`；最新 `app_runtime/tests/unit` 为 `1621 passed`。

## 十九、部署状态判定（2026-08-18）

Robinhood 当前属于“框架接入完成、观察 shadow 可用、可执行部署未完成”：

- 链配置、market registry、共享链选择、RPC/Blockscout 只读探针已完成。
- DEX intent 和候选页面已完成，但明确保持 `executionEnabled=false`、`broadcastEnabled=false`。
- 真实 DEX factory/router/quoter、池状态、live quote、executor、flashloan provider 和 fork simulation 尚未形成完整证据链。
- Robinhood 清算协议尚未确认，因此清算 adapter 和健康度 current pool 不进入本轮执行。

代码统一状态入口：

`flashloan/src/dex/intent_trade/chains/robinhood/readiness.py`

部署状态详细记录：

`docs/关于Robinhood/Robinhood Chain 部署状态.md`

在 DEX live quote 和执行验收完成前，Robinhood 只能作为 observation/shadow 市场运行；不能因为 RPC 正常或页面可访问，就判定 Robinhood 已完成生产部署。

## 二十、2026-10-17 当前落点

- Rust 执行原语与 Pyo3 注册已完成：`flashloan/rs-src/src/execution.rs`、`flashloan/rs-src/src/lib.rs`。
- Python 薄封装已接入：`flashloan/src/core/rs_bridge.py`，`rust_available()` 为真时正式路径优先 Rust。
- Rust-first 页面回执、统一广播和 V4 报价 `eth_call` 已完成接线：
  - `flashloan/src/dex/runtime/dex_route_rpc.py`
  - `flashloan/src/dex/intent_trade/direct_unified_broadcast.py`
  - `flashloan/src/dex/intent_trade/direct_unified_broadcast_py_fallback.py`
  - `flashloan/src/dex/intent_trade/chains/robinhood/v4_quote_adapter.py`
  - `flashloan/src/dex/intent_trade/chains/robinhood_testnet/v4_quote_adapter.py`
- P3-5 架构守卫与 fake `flashloan_rs` bridge 单测已加入 `flashloan/app_runtime/tests/unit`。
- `4663` / `46630` SQLite 重建 integrity 通过；验收记录见 `flashloan/docs/p3_4_p3_5_p4_p5_acceptance.md`。
- 主网生产边界不变：`executionEnabled=false`、`broadcastEnabled=false`、`productionReady=false`。
