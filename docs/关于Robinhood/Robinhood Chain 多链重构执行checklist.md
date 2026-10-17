# Robinhood Chain 多链重构执行 Checklist

更新时间：2026-08-18

## A. 框架稳定性

- [ ] 所有页面 API 都接收并传递 `market_id`、`chain_id`、`feature`。
- [ ] 禁止在请求已有市场参数时回退到 Avalanche 默认市场。
- [ ] 当前池与扫描批次分离：页面展示当前池，批次只做历史审计。
- [ ] 三池模型保持一致：普通账号、高频账号、核心账号。
- [ ] 每个市场的三池阈值和扫描频率来自配置或数据库，不写死在页面。
- [ ] 同一 `chain_id` 下允许多个 `market_id`，例如 BNB Venus 和 BNB DEX。
- [ ] 链切换后清空上一条链的页面缓存、执行缓存和报价缓存。
- [ ] 所有执行动作必须二次校验当前 market scope。

## B. 数据库重构

- [x] 设计 `chain_markets` 或 `liquidation_markets` 表。
- [x] 字段覆盖 `market_id`、`chain_id`、`network`、`feature`、`protocol`。
- [x] 字段覆盖 RPC、WS、explorer、native token、wrapped native、multicall。
- [x] 字段覆盖协议合约、DEX 合约、flashloan provider、executor。
- [x] 字段覆盖 `protocol_adapter`、`execution_adapter`。
- [x] 字段覆盖 `gas_policy_json` 和 `aa_policy_json`。
- [x] 字段覆盖 `observation_only`、`enabled`、`is_default`。
- [x] 设计 `market_scan_policies` 表。
- [x] 普通/高频/核心三池阈值迁移到 `market_scan_policies`。
- [x] 保留代码内置 fallback，避免数据库不可用时页面完全失效。
- [x] 写 migration。
- [x] 写 seed 数据：Avalanche、BNB Venus、Robinhood DEX shadow。
- [x] 写单元测试验证 DB 配置优先、内置配置兜底。

## C. Robinhood 链配置

- [x] 新增 Robinhood 主网配置：`network=robinhood`、`chain_id=4663`。
- [x] 新增 Robinhood 测试网配置：`network=robinhood-testnet`、`chain_id=46630`。
- [x] 配置原生 gas token：`ETH`。
- [x] 配置主网公共 RPC：`https://rpc.mainnet.chain.robinhood.com`。
- [x] 配置测试网公共 RPC：`https://rpc.testnet.chain.robinhood.com`。
- [x] 配置 Alchemy RPC 环境变量，不把 API key 写入代码。
- [x] 配置主网 explorer：`https://robinhoodchain.blockscout.com`。
- [x] 配置测试网 explorer：`https://explorer.testnet.chain.robinhood.com`。
- [x] 配置 L2 WETH：`0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73`。
- [x] 配置 L2 Multicall：`0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1`。
- [x] 写 `eth_chainId` 健康检查。
- [x] 写 `eth_blockNumber` 健康检查。
- [x] 写 `getCode` 检查 WETH/Multicall。
- [x] 页面显示 Robinhood RPC/链状态时，不依赖 Neon 数据库。

## D. DEX 套利优先开发

- [x] 新增 Robinhood DEX chain module。
- [x] 新增 Robinhood Token 配置，先只放官方确认的 WETH。
- [x] 不手工猜测 DEX router/factory 地址。
- [ ] 从官方文档、Blockscout、协议文档确认 Uniswap/Rialto 地址。
- [x] 新增 Robinhood 静态池表路径。
- [x] 静态池表强制校验 `network=robinhood` 和 `chainId=4663`。
- [x] 错链静态池表必须抛错。
- [x] 实现 Robinhood `shadow_quote_only` intent draft。
- [x] intent 输出 `executionEnabled=false`。
- [x] intent 输出 `broadcastEnabled=false`。
- [x] direct execution 对 Robinhood 返回 `robinhood_executor_adapter_unverified`。
- [x] 增加三角路径生成测试。
- [x] 增加静态表校验测试。
- [x] 增加 Robinhood alias 测试：`robinhood`、`4663`、`robinhood-testnet`、`46630`。
- [x] 增加 “不支持 unified executor” 测试。
- [x] 完成 live quote 前，页面只展示观察/候选，不展示可执行利润。
- [x] 聚焦测试通过：Robinhood intent、BNB intent、direct builder 共 `114 passed`。

## E. DEX 池发现和报价

- [ ] 建立 Robinhood DEX factory 地址来源记录。
- [x] 扫描 factory `PoolCreated` 或等价事件。
- [x] 建立 token allowlist。
- [x] 建立高风险 token blacklist。
- [ ] 记录池 TVL、liquidity、fee tier、最近交易活跃度。
- [ ] v2 池读取 reserves。
- [ ] v3 池读取 slot0、liquidity、tick spacing。
- [ ] 使用 quoter/staticcall 报价。
- [ ] 估算 gas 和 L1 data fee。
- [ ] ETH gas 成本换算成 USDC 或基准稳定币。
- [x] 写入 `dex_arbitrage_candidates` 当前候选池。
- [x] 写入 `dex_arbitrage_attempts` 预演证据。
- [x] 页面按当前候选池展示，不展示旧批次作为机会数量。

## F. 执行准入

- [ ] Robinhood executor 合约未部署前，禁止执行。
- [ ] Robinhood flashloan provider 未确认前，禁止执行。
- [ ] Robinhood live quote 未通过前，禁止执行。
- [ ] fork simulation 未通过前，禁止执行。
- [ ] 净利润扣除 gas、滑点、flashloan fee、失败保护成本。
- [ ] 交易广播前重新读取最新池状态。
- [ ] 交易广播前重新确认 `market_id/chain_id`。
- [ ] AA/paymaster 仅作为可选支付策略。
- [ ] AA/paymaster 失败时不能绕过普通 gas 保护。
- [ ] 所有执行结果写入 evidence。

## G. Robinhood 清算后续开发

- [ ] 确认 Robinhood 上目标借贷协议，优先调研 Morpho。
- [ ] 确认协议合约地址和 ABI。
- [ ] 确认账户健康度读取方式。
- [ ] 确认 oracle 和价格精度。
- [ ] 确认可清算条件。
- [ ] 确认 repay asset / collateral asset / liquidation incentive。
- [ ] 确认是否能使用 flashloan。
- [ ] 确认清算后 collateral 退出 DEX 路径。
- [ ] 建立 Robinhood 当前健康度账户池。
- [ ] 按普通/高频/核心三池展示。
- [ ] 保留小于 1 账户为历史/已可清算观察，不作为核心机会主要来源。
- [ ] 清算预演通过前保持 `observation_only=true`。
- [ ] executor、flashloan、利润、gas 全部通过后，才允许自动执行。

## H. 回归测试

- [ ] BNB Venus 页面仍显示 BNB 当前账户池。
- [ ] BNB Venus 不再自动切回 Avalanche。
- [ ] BNB 健康度 status 和 pool tier 分开显示。
- [ ] Avalanche 原有 direct on-chain 流程不受影响。
- [x] Robinhood DEX 页面可以在无数据库时显示链配置状态。
- [ ] Neon 数据库不可用时，页面显示降级状态而不是卡死。
- [ ] 错链缓存、错链静态表、错 market_id 都被拒绝。
- [ ] 所有新增测试通过。

## I. 当前建议执行顺序

- [x] 先补 Robinhood chain config 和 direct network alias。
- [x] 再补 Robinhood shadow intent draft。
- [x] 再补静态池表校验和测试。
- [x] 再补 DB market registry migration。
- [x] 再补页面 market selector 读取 DB/fallback。
- [ ] 再做 Robinhood DEX 真实地址和池发现。
- [ ] 最后进入 Robinhood 清算协议调研和适配。

## J. 文档和证据包

- [x] 建立 Robinhood 可行性与免 Gas 政策调研计划。
- [x] 建立 Robinhood DEX 套利与多链重构方案。
- [x] 建立 Robinhood 多链重构执行 checklist。
- [ ] 为每个 Robinhood DEX 地址建立来源证据。
- [ ] 为每个 Robinhood token 建立来源证据和风险标签。
- [x] 为每个候选池建立 `eth_getCode` 和事件扫描证据。
- [ ] 为每轮 live quote 建立 evidence 文件。
- [ ] 为 AA/paymaster PoC 建立独立报告。

## K. 下一轮开发建议

- [x] 新增 `chain_markets` migration。
- [x] 新增内置 market registry fallback。
- [x] Seed `robinhood-dex-shadow`。
- [x] 新增 Robinhood RPC health probe。
- [x] 新增 Robinhood Blockscout contract verification probe。
- [x] 新增 DEX 地址 discovery tool。
- [x] 新增 Robinhood DEX current candidate pool 页面。
- [ ] 清算功能继续保持后置，不进入执行开发。

## L. 2026-08-18 实施记录

- [x] `core.market_config` 增加 `robinhood-dex-shadow`，按 `feature=dex_arbitrage` 解析到 Robinhood Chain `4663`。
- [x] `chain_markets`、`market_scan_policies`、`dex_arbitrage_candidates` 已加入 schema 初始化。
- [x] `20260818_chain_market_registry` 已加入 schema migration 记录。
- [x] `supported_market_summaries(database_url)` 支持 DB 优先、内置 fallback。
- [x] 页面市场 payload 已接入 DB/fallback 市场列表。
- [x] 新增 `probe_robinhood_chain_health.py` 只读探针。
- [x] 实测 Robinhood 公共 RPC：`eth_chainId=4663`，`eth_blockNumber` 可读，WETH/Multicall 合约代码存在。
- [x] 聚焦回归测试通过：`143 passed`。
- [x] 本轮（2026-08-17）：`market_scan_policies` 现已 seed 三池阈值与刷新频率；运行时优先读取 DB，失败时退回内置策略。
- [x] 本轮（2026-08-17）：新增 `probe_robinhood_contract_verification.py`，通过 Blockscout v2 只读核验 WETH/Multicall 或指定合约。
- [x] 本轮（2026-08-17）：新增 `/robinhood-dex` 与 `/api/robinhood-dex/observation`；链状态不依赖数据库，候选池固定为观察态并带执行阻断原因。

## M. 2026-08-17 本轮代码重构

- [x] 新增 `robinhood_dex_evidence`：校验目标链、factory 代码证据、来源 URL 和池级 `eth_getCode` 结果。
- [x] 新增 token registry、allowlist、blacklist 过滤；allowlist 与 blacklist 重叠时直接拒绝策略。
- [x] 新增三角候选生成：只从 `observable` 池构造当前观察候选，不生成可执行利润。
- [x] 新增证据包生成器：输出 `source_urls.json`、`contracts.json`、`code_hashes.json`、`factory_events.json`、`token_allowlist.json`、`token_risk_report.json`、`pool_static_table.json`、`quote_smoke_report.json` 和 `summary.md`。
- [x] 新增当前候选池替换写入和 `dex_arbitrage_attempts` 记录接口。
- [x] 本轮定向回归：`45 passed`。
- [ ] 尚未填入未经独立来源确认的真实 Robinhood factory/router/quoter 地址；尚未执行真实 factory 扫描和 live quote。

## N. 2026-08-18 部署状态复核

### 结论

- [x] Robinhood 链配置、market alias、RPC 只读探针和 Blockscout 只读核验已完成。
- [x] Robinhood DEX 已进入 `observation_shadow_only`，页面和 API 可展示观察候选。
- [x] 新增 `dex/intent_trade/chains/robinhood/readiness.py::robinhood_deployment_status()`，统一输出部署层级、执行状态和阻断原因。
- [x] 新增 `/api/robinhood-dex/readiness`，独立输出链状态、证据包来源和部署阻断。
- [ ] Robinhood 可执行部署尚未完成。
- [ ] 主网自动广播仍然禁止；测试网已具备 Rust-first 广播路径，但仍受执行 gate 控制。
- [ ] Robinhood 清算工作流仍然未启动。

当前状态：

```text
deploymentLevel = framework_and_observation_shadow
scope            = observation_shadow_only
executionEnabled = false
broadcastEnabled = false
observationReady = true
dexExecutionReady = false
liquidationReady = false
productionReady = false
```

### 当前阻断原因

- [ ] `robinhood_dex_factory_source_unverified`
- [ ] `robinhood_pool_discovery_evidence_missing`
- [ ] `robinhood_live_quote_unverified`
- [ ] `robinhood_executor_unverified`
- [ ] `robinhood_flashloan_provider_unverified`
- [ ] `robinhood_fork_simulation_unverified`
- [ ] `robinhood_lending_protocol_unverified`
- [ ] `robinhood_liquidation_adapter_not_started`

### 复核证据

- [x] 主网只读 RPC 返回 `chainId=4663`。
- [x] `eth_blockNumber` 可读。
- [x] WETH 和 Multicall 的 `eth_getCode` 存在。
- [x] Blockscout 能返回 WETH 和 Multicall 的已验证合约信息。
- [ ] 真实 DEX factory/router/quoter 来源证据。
- [ ] 真实池流动性和报价证据。
- [ ] executor、flashloan、fork simulation 证据。

详细状态见：`Robinhood Chain 部署状态.md`。

## O. 2026-10-17 执行层与 Rust 接入

- [x] `flashloan/rs-src/src/execution.rs` 实现并注册 7 个 Pyo3 原语：
  `resolve_private_key_hex`、`sign_typed_transaction_json`、`build_v4_quote_calldata`、`quote_v4_exact_input_single`、`submit_v4_route`、`load_v4_receipt`、`get_v4_pending_nonce`。
- [x] Rust 回归基线覆盖 EIP-1559 raw tx、EIP-155 legacy raw tx、`quoteExactInputSingle` calldata。
- [x] Rust 单测覆盖私钥规范、RLP 编码、signature recovery id、relay 解析、redaction；`cargo test` 为 `27 passed`。
- [x] `flashloan/src/core/rs_bridge.py` 增加薄封装并纳入 `__all__`；`rust_available()` 为真时正式路径优先 Rust。
- [x] `fetch_dex_tx_receipt` 优先 `load_v4_receipt`，Python 只合并链元数据与页面字段。
- [x] `direct_unified_broadcast.py` 使用 Rust 签名、广播和回执；Python 兼容分支在 `direct_unified_broadcast_py_fallback.py`。
- [x] 正式广播文件与 fallback 模块均不出现顶层 `eth_account.Account`；`Account` 只在函数内 Rust 不可用分支出现。
- [x] 主网/测试网 `v4_quote_adapter.py` 使用 Rust calldata + `eth_call`，保留上下文解析、重试和错误归类。
- [x] P3-5 架构守卫覆盖 `rust_available()`、`submit_v4_route(`、`load_v4_receipt(` 静态断言。
- [x] 新增 fake `flashloan_rs` bridge 单测，网络类能力用 mock RPC 测 payload 形状。
- [x] V4 SQLite 重建：真实库 `4663` / `46630` integrity report 均为 `0 violations`。
- [ ] 主网真实 live quote、executor、flashloan provider、fork simulation 证据仍不足。
- [ ] 主网 `executionEnabled=false`、`broadcastEnabled=false`、`productionReady=false` 保持不变。

验收记录：`flashloan/docs/p3_4_p3_5_p4_p5_acceptance.md`。

## P. 2026-10-17 方向候选补池

- [x] `build_v4_directional_pool_backfill` 从最新区块反向扫描 `Initialize` 日志，方向级满 `5` 即停。
- [x] 方向统一为小地址 ID → 大地址 ID（正）、大地址 ID → 小地址 ID（反），不依赖合约 `token0/token1` 顺序。
- [x] 找不到流动性时最多回退到该边池子创建区块；只把实时确认有流动性的池计入方向候选。
- [x] `refresh_v4_live --candidate-backfill` 接入计划、反向扫描、实时流动性确认与 index merge。
- [x] 零流动性池继续由 `prune_v4_evidence_rows` 清理，市场总览只保留有流动性池。
- [x] 新增冷导入零 RPC 回归：`DEX_V4_LIVE_QUOTE_ENABLED=0` 且 `skip_index/skip_liquidity=True` 时 `_connect_rpc` 调用计数为 `0`。
- [x] 相关单测与架构守卫通过；全量单测除既有真实库断言（股票候选 9 vs 10）外全绿。

## Q. 2026-10-17 Morpho Blue 只读调研

- [x] 新增 `morpho_blue_readonly.py`：主网 Morpho Blue Core 只读探针、市场 view 与 position view 解码。
- [x] 新增 `probe_robinhood_morpho_blue.py` CLI，默认不写业务库、不广播，输出主网独立 evidence。
- [x] 真实主网只读探测：`chainId=4663`、`codeBytes=15582`、`codeHash=0x753b54fdb48c87c9c1ba1dcc3a330faac1a401a3bd23b43104b7b92409ecb424`。
- [x] `market(bytes32)`、`position(bytes32,address)`、`flashLoan(address,uint256,bytes)`、`owner()` 选择器在字节码中确认存在。
- [x] 近期事件扫描：区块 `90715221-90725220` 内 `47` 条日志，包含 `AccrueInterest 21`、`Supply 12`、`FlashLoan 9`、`SupplyCollateral 3`、`Borrow 2`。
- [ ] 独立官方来源 URL 与源码核验记录（当前地址来源仍为 `chains/robinhood/chain.json`，未独立核验）。
- [ ] 标准 Morpho Blue v1 ABI 等价性复核（`lltv/isDeprecated/CreateMarket` 等常量未在字节码静态探测中确认）。
- [ ] 主网 `CreateMarket` 事件签名、市场扫描、oracle/irm/lltv 记录。
- [ ] 真实账户清单与健康度、清算 incentive/close factor、fork/static-call、paused executor readback。
- [ ] 清算继续保持 `observation_only=true`，DEX 执行稳定前不进入清算 static-call 与自动执行。

调研记录：`Robinhood Morpho Blue 借贷清算调研.md`。
