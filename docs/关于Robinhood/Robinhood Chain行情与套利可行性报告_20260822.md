# Robinhood Chain 行情与套利可行性报告

更新时间：2026-08-22

## 结论

本轮已完成 Robinhood Chain 主网只读行情采样、Uniswap v3 池状态读取和 Quoter 实时报价。

当前结论：

- Robinhood Chain 主网可读，`chainId=4663`。
- 当前区块约为 `42,963,901`，RPC `eth_gasPrice` 约为 `0.020032 gwei`。
- Robinhood 官方股票代币接口返回了 194 个主网资产；本轮检查到 `WETH/股票代币` 非零流动性池，但没有检查到非零流动性的 `USDG/股票代币` 池。
- `USDG/WETH` 的 0.01%、0.05%、0.30%、1.00% 四个 Uniswap v3 池均有代码和流动性，四池之间的 12 条有序双池往返报价均成功。
- 以 `1,000 USDG` 为输入，当前最佳路径为 `0.01% USDG->WETH` 再经 `0.05% WETH->USDG`，最终得到 `999.471697 USDG`，毛损失 `0.528303 USDG`。
- 按示例 `5 bps` 闪电贷费和 `250,000` gas 估算，净结果约为 `-1.040508 USDG`。
- 因此当前没有可确认的正利润套利机会，不能开启真实交易或广播。

## 本轮数据

行情和证据文件：

- [历史 Robinhood 市场快照目录](../../flashloan/src/runtime/evidence/robinhood/)
- [Robinhood 市场快照读取工具](../../flashloan/src/market/tools/probe_robinhood_market_snapshot.py)
- [市场快照核心实现](../../flashloan/src/dex/intent_trade/chains/robinhood/market_snapshot.py)

链级只读结果：

| 项目 | 结果 |
|---|---:|
| network | `robinhood` |
| chainId | `4663` |
| snapshot block | `42963901` |
| 有效 USDG/WETH 池 | `4` |
| 成功往返报价 | `12` |
| 当前 gas price | `0.020032 gwei` |
| executionEnabled | `false` |
| broadcastEnabled | `false` |

USDG/WETH 池状态如下。`liquidity` 是 Uniswap v3 原始 liquidity 单位，不等于 USD TVL，后续仍应补充 token balance、价格区间和可成交深度。

| Fee | Pool | Liquidity | Tick |
|---:|---|---:|---:|
| 0.01% | `0x52e65B17fB6E5BA00Ed806f37Afcd2DaA50271Ca` | `965821657373725543` | `-198331` |
| 0.05% | `0x69BfaF19C9f377BB306a89aEd9F6B07e2c1a8d9a` | `238627884508208301` | `-198329` |
| 0.30% | `0xa9188730Fe85Be88ad499D7d52B099e800fB0334` | `37595346130177785` | `-198351` |
| 1.00% | `0x5f009E071F07e92B6C624e83F52F17bBDa34680D` | `1594066154817` | `-198267` |

官方 Uniswap v3 部署资料中的 Robinhood Chain 地址与本轮链上 `eth_getCode` 结果一致：Factory、QuoterV2、SwapRouter02 均有代码。本报告仍把它们作为研究输入，未写入生产执行配置。

## 外部股票行情

Robinhood 股票代币价格接口本轮返回了以下样本。它们是 bid/ask 参考行情，不等同于可在同一笔 EVM 交易中执行的 AMM swap 报价。

| Token | Bid | Ask | 观察时间（UTC） |
|---|---:|---:|---|
| AAPL | `309.6` | `312.2` | `2026-08-22T07:06:54Z` 附近 |
| NVDA | `213` | `217.55` | `2026-08-22T07:08:40Z` |
| CRM | `209.17` | `220` | `2026-08-22T07:08:34Z` |
| COIN | `189.2` | `189.31` | `2026-08-22T07:08:37Z` |
| TSLA | `255` | `364` | `2026-08-22T07:19:41Z` |

TSLA 的 bid/ask 极宽，不能直接作为套利利润使用；需要先确认接口语义、交易时段、报价有效期和是否允许机器人地址进行相同方向的 mint/burn 或交易结算。

## 套利算法

### 1. 当前可验证算法：同交易对跨 fee tier

当前最可靠的闭环是：

```text
flash borrow USDG
  -> pool fee=0.01%: USDG -> WETH
  -> pool fee=0.05%: WETH -> USDG
  -> repay USDG + flashloan fee
```

扫描器应对每个 token pair 的所有有效池执行以下组合：

1. 按 `chainId + token0 + token1 + fee` 读取池。
2. 过滤无代码、`liquidity=0`、过期或无法 Quoter 报价的池。
3. 对每两个不同 fee tier 生成两个方向。
4. 用第一跳真实 `amountOut` 作为第二跳 `amountIn`，禁止用独立中间价相乘。
5. 计算：

```text
gross = finalAmount - initialAmount
net = gross - flashloanFee - gasCost - slippageBuffer - MEVBuffer
```

6. 只有 `net > 0`、报价区块新鲜、池状态再次复核且执行器门禁全部通过，才允许进入候选机会；当前仍只能记录为 observation。

### 2. 三角套利当前不成立

本轮对官方资产列表的 `USDG/股票代币` 四个常见 fee tier 做了只读查询，共检查 `194 * 4 = 776` 个组合，未发现非零流动性池。因此当前没有证据支持以下路径：

```text
USDG -> STOCK_TOKEN -> WETH -> USDG
```

虽然链上存在若干 `WETH/股票代币` 池，但缺少有流动性的 `USDG/股票代币` 连接边，不能仅凭股票代币的外部 bid/ask 把它们拼成原子三角套利。

### 3. 价格方向和数据质量要求

Uniswap v3 的 `sqrtPriceX96` 必须结合 `token0/token1` 和 decimals 计算，不能看到 `WETH/股票代币` 就直接解释为“每个股票代币值多少 WETH”。生产算法必须：

- 先按地址确认 token0/token1。
- 再按 decimals 转换价格。
- 最后用 Quoter 的 exact-input 结果作为成交估计。
- spot price、外部 bid/ask、Quoter amountOut 三者分别存储，不互相替代。

## 闪电贷可行性

### 技术上可行的路径

理论上有两类路径：

1. 使用已验证的借贷协议 flash loan，例如确认 Robinhood Chain 上存在并开放的 Morpho/其他借贷池。
2. 使用 Uniswap v3 池的 pool-level `flash` 回调，在同一交易内借出 token、完成其他池 swap、归还本金和费用。

但第二种方式有一个重要限制：被用于 flash 的 Uniswap v3 池在回调期间处于 lock 状态，不能同时把同一个池当作 swap 池。因此当前最佳路径使用 0.01% 和 0.05% 两个池时，不能简单地从 0.01% 池借款后又在 0.01% 池完成第一跳。若从 0.30% 或 1.00% 池借款，费用和流动性又必须重新加入模型。

### 当前不能开启的原因

项目 readiness 当前仍阻断：

```text
robinhood_flashloan_provider_unverified
robinhood_executor_unverified
robinhood_fork_simulation_unverified
broadcast_disabled_by_design
```

所以当前答案是：

> 可以继续做闪电贷套利的只读建模和 fork 预演；目前不能把 Robinhood Chain 的闪电贷套利作为可执行生产策略。

要进入下一阶段，至少需要：

1. 确认一个 Robinhood Chain 上真实可调用的 flash loan provider、地址、ABI、费用和资产覆盖。
2. 如果使用 Uniswap v3 pool flash，验证 `flash` 回调、还款金额、费用计算和池 lock 约束。
3. 部署 Robinhood 专属 executor，并完成 staticcall、fork simulation 和小额测试。
4. 在真实 gas、L1 data fee、滑点、失败回滚和排序风险下重新计算净利润。
5. 增加实时快照过期时间和广播前的二次池状态复核。

## 当前判定

```text
marketStatus       = observing / shadow_quote_only
grossOpportunity   = none in sampled USDG/WETH routes
netOpportunity     = none
flashloanModel     = possible in theory, provider unverified
executionEnabled   = false
broadcastEnabled   = false
productionReady    = false
```

本轮最合理的下一步不是直接部署，而是继续收集多 DEX 或 proprietary AMM 的官方地址与池数据，并把 `USDG/WETH` 跨 fee-tier 报价做成持续观察窗口，确认是否存在可重复、扣除全部成本后仍为正的价差。
