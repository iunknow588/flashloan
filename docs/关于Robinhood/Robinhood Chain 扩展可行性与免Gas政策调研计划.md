# Robinhood Chain 扩展可行性与免 Gas 政策调研计划

更新时间：2026-08-18

## 初步结论

Robinhood Chain 可以作为现有多链框架的候选扩展链调研。它是 EVM 兼容的 Arbitrum Layer-2，主网 Chain ID 为 `4663`，官方 RPC 为 `https://rpc.mainnet.chain.robinhood.com`，原生 Gas Token 为 `ETH`。因此，现有框架中“链配置、RPC、区块浏览器、EVM 合约调用、Token/DEX 静态表、账户健康度展示”的通用部分可以复用。

但当前不能直接判断“现有清算策略可无改动运行”。原因是清算策略依赖具体借贷协议适配器，例如 Aave/Venus 的账户健康度、抵押/债务资产、清算入口、预演和执行参数。Robinhood Chain 需要先确认是否存在可清算的借贷协议、协议合约 ABI、价格源、可交易退出路径和执行限制。

关于免 Gas：官方文档没有显示 Robinhood Chain 是全链免 Gas。官方 Gas 文档说明交易仍使用 `ETH` 支付费用，并包含 L2 执行费和 L1 数据费。近期市场消息和 Robinhood Crypto 官方 X 信息显示，Robinhood Wallet 在 Robinhood Chain 上有 Gas Sponsorship/补贴活动，2026-08-08 左右将 swap 的 Gas 补贴门槛从 `$5` 降到 `$0.50`，有效期报道为至 2026-09-29。这个政策应理解为“Robinhood Wallet 特定 swap 场景的 Gas 补贴”，不是任意合约调用、机器人交易或清算交易的全链免 Gas。

## 已确认基础资料

| 项目 | 当前信息 | 调研处理 |
|---|---:|---|
| 主网 Chain ID | `4663` | 以官方文档为准，并用 `eth_chainId` 实测 |
| 测试网 Chain ID | `46630` | 以官方文档为准，注意第三方页面可能存在不一致 |
| 主网 RPC | `https://rpc.mainnet.chain.robinhood.com` | 需要压测限流、延迟、归档能力 |
| 测试网 RPC | `https://rpc.testnet.chain.robinhood.com` | 先做只读 PoC |
| 原生 Gas | `ETH` | 不按免 Gas 设计执行器 |
| 浏览器 | `robinhoodchain.blockscout.com` | 验证 API、合约源码、事件查询 |
| 技术栈 | Arbitrum L2 / EVM | 可复用 Web3/EVM 调用框架 |

## 关键判断：当前框架如何扩展

1. 链配置层可以扩展。
   - 新增 `robinhood` chain config：chain id、RPC 列表、explorer、native token、gas 参数。
   - 复用现有多链 `market_id/chain_id` 页面选择逻辑。

2. 展示层可以扩展。
   - 页面已经支持按链/协议选择，并按普通账号、高频账号、核心账号分池展示。
   - Robinhood 需要接入一个 `robinhood_current_health_pool` 或数据库池，避免把历史扫描批次当作当前账户池。

3. 扫描层需要新增协议适配。
   - 如果 Robinhood Chain 上存在 Aave-like 协议，可复用 Aave 健康度模型并改配置。
   - 如果是自有协议或 tokenized-stock 相关协议，需要新增 `protocol adapter`。
   - 没有借贷协议时，只能先做账户/Token/DEX 观察，不能称为清算系统可运行。

4. 执行层需要重新验收。
   - Gas 补贴不能假设适用于机器人地址或合约清算交易。
   - 必须按普通 ETH Gas 估算成本。
   - 需要验证 Robinhood Chain 的 sequencer、finality、reorg、nonce、EIP-1559/Arbitrum fee 行为。

## 免 Gas / Gas Sponsorship 调研项

目标：确认“是否可以把清算机器人当作免 Gas 运行”。

结论假设：不能默认免 Gas，必须按付费 Gas 设计。

### OneBullEx 页面口径分析

参考页面：https://www.onebullex.com/explore/robinhood-chain-explorer-track-transactions

OneBullEx 的文章多次使用 `gasless transaction infrastructure`、`without gas fees`、`gas fees are typically zero or subsidized` 等表述。该页面对免 Gas 的描述可以作为市场侧观点，但不能作为执行系统的费用假设，原因如下：

1. 文章主题是 Robinhood Chain Explorer。
   - Explorer 查询地址、交易、区块、Token 本身不需要链上交易，自然不消耗 Gas。
   - 这不能推导出清算机器人提交合约交易也免 Gas。

2. 文章说的是“typically zero or subsidized”。
   - `zero` 和 `subsidized` 是两种不同模型：前者表示用户看不到费用，后者表示有人代付。
   - 如果是代付，需要确认 paymaster、钱包、交易类型、活动期限、额度和风控策略。

3. 官方 Gas 文档仍然定义 Robinhood Chain 使用 ETH 支付交易费。
   - 官方文档说明费用由 L2 execution fee 和 L1 data fee 组成。
   - 因此普通 EOA 或普通合约执行不能按全链免 Gas 建模。

4. 官方 Account Abstraction 文档支持 gas sponsorship。
   - Robinhood Chain 支持 ERC-4337/EIP-7702，并可通过 Alchemy/ZeroDev 等服务配置 gas sponsorship。
   - 这说明“可被赞助”是工程能力，不等于“任意机器人自动免 Gas”。

5. 近期促销更像 Robinhood Wallet swap 补贴。
   - 2026-08-08 附近的公开报道显示，Robinhood Wallet 在 Robinhood Chain 上把 Gas sponsorship minimum 从 `$5` 降到 `$0.50`。
   - 相关报道强调适用对象是 Robinhood Wallet 的 swap 交易，并且活动持续到 2026-09-29。
   - 这不能直接覆盖清算合约、机器人地址、批量套利、外部执行器或 flashloan 调用。

工程结论：Robinhood 扩链时，费用模型必须默认 `ETH Gas = 真实成本`。Gas sponsorship 只能作为可选优化路径，必须通过 PoC 验证 paymaster 是否允许我们的执行账户、执行合约和交易类型使用。

### 免 Gas 概念拆分

| 概念 | 是否可视为免 Gas | 对清算机器人的影响 |
|---|---:|---|
| Explorer 查询免费 | 是，但只是查询免费 | 无法降低交易成本 |
| Robinhood Wallet swap 补贴 | 可能，取决于活动条款 | 大概率不覆盖机器人清算 |
| ERC-4337 paymaster 赞助 | 技术上可行 | 需要自建/配置 sponsor policy，并承担费用或额度限制 |
| 普通 EOA 合约交易 | 否 | 必须准备 ETH 并计入利润模型 |
| 清算/flashloan 合约调用 | 未确认 | 必须按付费 Gas 做预演和成本估算 |

需要核验：

1. Robinhood Wallet Gas Sponsorship 的适用主体。
   - 是否只适用于 Robinhood Wallet 用户。
   - 是否只适用于 wallet 内 swap。
   - 是否排除合约交互、机器人地址、多签、合约钱包、批量调用。

2. 补贴规则。
   - 门槛从 `$5` 降到 `$0.50` 的含义是“超过门槛部分补贴”还是“符合条件即补贴”。
   - 每地址、每日、每交易类型是否有限额。
   - 有效期是否确认为 2026-09-29。

3. 工程验证。
   - 使用普通 EOA 对 RPC 发一笔测试网交易，确认仍需 ETH。
   - 如果 Robinhood Wallet 支持 AA/paymaster，确认其 paymaster 是否开放给外部调用。
   - 如果有 paymaster API，确认 ToS 是否允许自动化套利/清算机器人使用。

## 调研阶段计划

### 阶段 1：链基础接入验证

交付物：
- `robinhood_chain_probe.json`
- `robinhood_rpc_health.md`

任务：
- 调用 `eth_chainId`、`eth_blockNumber`、`eth_getBlockByNumber`。
- 验证主网 `4663`、测试网 `46630`。
- 记录 RPC 延迟、错误率、批量请求限制、WebSocket 可用性。
- 验证 Blockscout API 是否可查交易、合约、日志。

验收：
- 连续 30 分钟只读请求成功率 > 99%。
- 能读取最新区块、交易收据、日志事件。

### 阶段 2：生态与协议发现

交付物：
- `robinhood_protocol_inventory.md`
- `robinhood_token_static_table.json`
- `robinhood_dex_static_table.json`

任务：
- 拉取官方 Token Contracts 和 Protocol Contracts。
- 识别 WETH、USDG、股票 Token、稳定币、主流兑换池。
- 确认是否存在借贷协议：Aave-like、Compound-like、自研协议或 RWA 抵押市场。
- 确认是否存在清算入口、健康度接口、oracle、close factor、liquidation bonus。

验收：
- 至少明确一个可扫描账户健康度的协议，或明确 Robinhood 当前不适合清算策略。

### 阶段 3：健康度模型适配

交付物：
- `robinhood_health_model.md`
- `robinhood_current_health_pool.json`

任务：
- 若协议兼容 Aave/Venus：映射账户健康度字段到现有普通/高频/核心池。
- 若协议不兼容：设计新的 `robinhood_*_adapter`，输出统一字段：
  - `account`
  - `health_factor` / `health_display`
  - `status`
  - `positions`
  - `liquidation_candidates`
  - `recommended_candidate`
- current pool 必须表示系统当前账户健康度，历史批次只作为过程记录。

验收：
- 页面账户池显示当前全量账户数。
- 普通账号、高频账号、核心账号分开展示。
- 单轮扫描不会覆盖全量 current pool。

### 阶段 4：Gas 与执行成本验证

交付物：
- `robinhood_gas_policy_report.md`
- `robinhood_execution_cost_model.json`

任务：
- 实测普通 EOA 交易 Gas。
- 实测合约调用 Gas。
- 查询 Arbitrum fee 组件：L2 execution + L1 data fee。
- 验证 Robinhood Wallet Gas Sponsorship 是否能用于外部自动化地址。
- 将补贴作为可选优化，不作为盈利模型默认项。

验收：
- 成本模型能给出 ETH Gas、USD Gas、L1 data fee 估算。
- 清算利润判断不依赖 Gas 免费假设。

### 阶段 5：最小 PoC

交付物：
- Robinhood 链只读页面入口。
- Robinhood current health pool。
- 账户详情页。
- 一份“不提交交易”的 observation-only 报告。

任务：
- 新增 `market_id=robinhood-<protocol>`、`chain_id=4663`。
- 接入页面市场选择器。
- 加入只读扫描按钮。
- 输出普通/高频/核心三池。
- 阻断真实提交，直到完成执行安全验收。

验收：
- 页面可切到 Robinhood。
- 能看到具体账户健康度或明确“待扫描/不支持健康度”。
- 所有 Robinhood 执行接口默认 observation-only。

## 主要风险

1. Gas 补贴并非全链免 Gas，机器人可能不能使用。
2. Robinhood Chain 生态可能偏向 Robinhood Wallet 和 tokenized stocks，未必有开放清算协议。
3. 股票 Token/RWA 可能有转让限制、地区限制、合规限制或黑名单机制。
4. RPC 公共端点可能有限流，不适合高频扫描。
5. Arbitrum L2 fee 模型和 finality 需要单独处理，不能完全套用 Avalanche/BNB。

## 推荐决策

短期建议只做 observation-only 调研，不做真实交易执行。

优先级：
1. 确认链和 RPC 可用性。
2. 找到可清算协议和健康度接口。
3. 验证 Gas Sponsorship 是否适用于机器人。
4. 完成页面 current pool 展示。
5. 再评估是否进入执行链路。

## 当前落地进展

截至 2026-08-17，已经完成第一步安全骨架：

- Robinhood 主网 `4663` 和测试网 `46630` 已加入 direct network 解析。
- Robinhood 链模块已建立，配置了官方公共 RPC、explorer、WETH、Multicall、Permit2。
- DEX router/factory/quoter 暂不写死，等待官方、Blockscout 或协议文档验证。
- Robinhood DEX intent 已实现为 `shadow_quote_only`。
- direct execution 已明确阻断，状态为 `robinhood_executor_adapter_unverified`。
- 聚焦测试通过：Robinhood intent、BNB intent、direct builder 共 `114 passed`。

因此下一步不是执行交易，而是建立 `chain_markets` 数据库市场注册、Robinhood RPC health probe、Blockscout 合约验证 probe，以及 DEX 地址 discovery tool。

2026-08-18 更新：

- `chain_markets`、`market_scan_policies`、`dex_arbitrage_candidates` 已加入系统 schema。
- `robinhood-dex-shadow` 已作为内置 fallback market，并会被 seed 到 DB registry。
- 页面市场列表已支持 DB 优先、内置 fallback。
- Robinhood RPC health probe 已实现并实测通过：公共 RPC 返回 `eth_chainId=4663`，WETH/Multicall 合约代码存在。
- 当前测试扩大到 `143 passed`。
- 下一步应进入 Blockscout 合约验证 probe 和 DEX 地址 discovery tool，而不是直接进入执行。

## 文档拆分

本目录目前建议按三份文档协同维护：

- `Robinhood Chain 扩展可行性与免Gas政策调研计划.md`：回答能不能做、免 Gas 口径是什么、风险在哪里。
- `Robinhood Chain DEX套利与多链重构方案.md`：回答系统如何重构、流程如何统一、DB 和状态机如何设计。
- `Robinhood Chain 多链重构执行checklist.md`：回答当前完成了什么、下一步做什么、哪些条件没达标不能执行。

## 参考资料

- Robinhood Chain 官方连接文档：https://docs.robinhood.com/chain/connecting/
- Robinhood Chain 添加网络参数：https://docs.robinhood.com/chain/add-network-to-wallet/
- Robinhood Chain Gas & Fees：https://docs.robinhood.com/chain/gas-and-fees/
- Robinhood Chain Account Abstraction / Gas Sponsorship：https://docs.robinhood.com/chain/account-abstraction/
- Robinhood Chain 部署合约文档：https://docs.robinhood.com/chain/deploy-smart-contracts/
- Robinhood Chain Token Contracts：https://docs.robinhood.com/chain/contracts/
- OneBullEx Robinhood Chain Explorer 说明：https://www.onebullex.com/explore/robinhood-chain-explorer-track-transactions
- Robinhood Crypto X 关于 Gas Sponsorship 门槛调整：https://x.com/RobinhoodCrypto/status/2085832774460596477
- Odaily 关于 2026-08-08 Gas 补贴门槛调整报道：https://www.odaily.news/en/newsflash/507876
- KuCoin 关于 2026-08-08 Gas 补贴门槛调整报道：https://www.kucoin.com/news/flash/robinhood-lowers-gas-fee-subsidy-threshold-to-0-5-on-robinhood-chain
