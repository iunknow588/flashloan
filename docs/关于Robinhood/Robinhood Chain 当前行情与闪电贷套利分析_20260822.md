# Robinhood Chain 当前行情与闪电贷套利分析

## 1. 本次读取范围

本次只读检查于 **2026-08-22** 在 Robinhood Chain 主网 RPC 执行，使用区块固定在本次读取的最新区块：

```text
RPC: https://rpc.mainnet.chain.robinhood.com
Chain ID: 4663
Block: 42951349
Mode: readonly_market_snapshot
Signer loaded: false
Transaction built: false
Broadcast: false
```

代码入口：

- `flashloan/src/dex/intent_trade/chains/robinhood/market_snapshot.py`
- `flashloan/src/market/tools/probe_robinhood_market_snapshot.py`
- `flashloan/src/runtime/evidence/robinhood/`（历史快照目录）

合约地址目前只作为研究输入和链上只读观察结果使用，不写入生产执行市场配置。

## 2. 链上行情快照

本次观察到的研究对象是 WETH/USDG 的 Uniswap V3 池：

| Fee tier | Pool | Liquidity | Tick |
|---:|---|---:|---:|
| 0.01% | `0x52e65B17fB6E5BA00Ed806f37Afcd2DaA50271Ca` | `940097216554650001` | `-198296` |
| 0.05% | `0x69BfaF19C9f377BB306a89aEd9F6B07e2c1a8d9a` | `238626770519729632` | `-198300` |
| 0.30% | `0xa9188730Fe85Be88ad499D7d52B099e800fB0334` | `37595346130177785` | `-198326` |
| 1.00% | `0x5f009E071F07e92B6C624e83F52F17bBDa34680D` | `1594066154817` | `-198267` |

读取到的研究合约地址：

```text
Factory: 0x1f7d7550b1b028f7571e69a784071F0205FD2EfA
QuoterV2: 0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7
SwapRouter02: 0xCaf681a66D020601342297493863E78C959E5cb2
WETH: 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73
USDG: 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168
```

四个池均通过 `eth_getCode`，并读取到正流动性。地址的生产使用仍需要独立来源、Blockscout 验证和证据包复核。

## 3. 动态报价结果

输入金额为 `1000 USDG`，每一跳均通过 QuoterV2 的 `quoteExactInputSingle` 进行 `eth_call`。共完成 12 条不同 fee tier 的双池有序往返报价。

本次最优路线：

```text
1000 USDG
  -> 0.05% pool: USDG -> WETH
  -> 0.01% pool: WETH -> USDG
  -> 999.755947 USDG
```

结果：

```text
毛收益 = -0.244053 USDG
毛收益率约 = -0.0244053%
eth_gasPrice = 20,000,000 wei = 0.02 gwei
Quoter gas estimate 合计 = 199,789
```

因此当前观察区块下，没有发现正毛收益的双池往返套利路线。1.00% 池的流动性显著低于其他池，不应因为单次异常报价而进入候选。

## 4. 套利算法结论

当前算法应按以下顺序执行：

1. 先按 `chain_id + market_id + protocol` 隔离市场。
2. 从 factory/event 或受信静态表发现池，校验池代码、token、fee 和流动性。
3. 不再按每个交易对只保留一个池；同一交易对的不同 fee tier 必须保留为独立边。
4. 对每条有序 hop 逐跳调用 Quoter，使用前一跳真实输出作为下一跳输入。
5. 计算毛收益，再扣除闪电贷费用、gas、滑点保护、MEV/失败缓冲。
6. 只有 `net_profit_after_costs > minimum_profit` 且通过 fork/static call，才允许进入执行候选。

旧的 `best_edges_by_pair()` 适合生成观察路线，但不适合作为真实套利判定的唯一算法，因为它会把同一交易对的多个 fee tier 压缩成一个池，丢失跨 fee tier 的价差组合。

## 5. 闪电贷可行性

### 理论可行

同链 DEX 套利在原理上可以使用闪电贷：借入 USDG，完成多池 swap，归还本金和费用，并将正净收益留在 executor 中。

### 当前不可执行

本次结果不能证明 Robinhood 已具备闪电贷交易条件，原因如下：

```text
robinhood_flashloan_provider_unverified
robinhood_executor_unverified
robinhood_fork_simulation_unverified
broadcast_disabled_by_design
```

同时，本次净收益模型没有将闪电贷费用视为已验证输入，因此 `netProfitVerified=false`。即使暂时使用假设的 fee bps，也不能绕过 provider、executor、fork simulation 和 token 风险门禁。

## 6. 当前页面应如何展示

Robinhood 页面现在应区分三种状态：

- `market_snapshot_available`：RPC、池状态和 Quoter 只读数据已读取，但还没有合格候选。
- `candidates_available`：有经过 factory/pool 证据包验证的观察候选。
- `awaiting_discovery_evidence`：没有候选缓存，也没有快照或发现证据。

本次页面属于 `market_snapshot_available`，不是“可执行套利”，也不是“已部署完成”。执行和广播继续保持关闭。

## 7. 下一步门禁

- [ ] 独立确认 Factory、Quoter、Router 的官方/协议来源并归档 URL。
- [ ] 将 USDG 纳入受信 token registry，并完成 token 风险复核。
- [ ] 记录 PoolCreated 事件证据和池级状态快照。
- [ ] 继续按多个 fee tier 保留候选边并执行动态报价。
- [ ] 确认 Robinhood 闪电贷 provider、资产覆盖和费用。
- [ ] 部署并验证 Robinhood 专属 executor。
- [ ] 完成 fork simulation、static call、滑点和失败回滚测试。
- [ ] 只有净收益、执行器、闪电贷和仿真全部通过后，才考虑开启执行；本轮不改变执行开关。
