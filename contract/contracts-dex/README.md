# DEX 合约工程

当前工程使用单合约入口：

```text
src/UnifiedFlashLoanMevExecutor.sol
```

它在同一个合约内完成：

- 有序状态 1 到 5 的候选扫描；
- V3 pool 的 factory、流动性和 tick 风险检查；
- `U-X-Y-U` / `U-Y-X-U` 三跳逐跳报价；
- Aave `flashLoanSimple` 回调、swap 和还款余额校验；
- 状态化返回值、方向字段、逐跳事件和利润归集。

## 测试

```powershell
npx hardhat compile
npx hardhat test test\UnifiedFlashLoanMevExecutor.test.js
```

## Robinhood 链测状态

Hardhat 已配置 Robinhood 主网与 Robinhood testnet 网络别名：

```text
robinhood        chainId 4663
robinhoodTestnet chainId 46630
```

当前只用于 Robinhood-only 链测准备：

- Robinhood 主网：机会捕获、只读 Quoter、fork/static-call 预检。
- Robinhood testnet：部署骨架、读回、暂停门禁和回款约束演练。
- 其他链：暂不进入链上测试。

注意：现有 `UnifiedFlashLoanMevExecutor.sol` 仍是 Aave + V3 风格执行器。Robinhood 当前测试网链路使用专用 `RobinhoodPausedFlashLoanExecutor`，不能直接把 Avalanche 执行器脚本视为 Robinhood 可执行部署脚本。

Robinhood testnet 暂停态部署准备使用专用合约与脚本：

```bash
npm run test:robinhood
npm run preflight:robinhood:testnet
npm run ready:robinhood:testnet
npm run deploy:robinhood:testnet:paused
npm run readback:robinhood:testnet
```

`RobinhoodPausedFlashLoanExecutor.sol` 在 Robinhood testnet 上使用 Uniswap V4 PoolManager kind `4` 作为 flash accounting 来源，并配置 Uniswap V4 Universal Router calldata adapter；Morpho 风格 adapter 只作为兼容/fork scaffold 保留，当前没有公开证据表明 Morpho Blue 已部署在 chain ID `46630`。预检脚本会检查 RPC chain ID 与 adapter code status；`ready:robinhood:testnet` 是严格部署门禁；部署脚本也会在交易前强制运行 preflight。暂停态部署已写入 `deployments/robinhood-paused-flashloan-robinhoodTestnet.json` 和 `../robinhood/testnet/contracts.json`，readback 证据包含 owner、pause、adapter、adapter code status、repayment guard、chain ID 和 code hash；它不会启用生产 broadcast。

剩余核心开发任务是实现真实 V4 PoolManager `unlock` / `take` / `settle` flash accounting 执行路径，并在新闭环机会上通过 fork/static-call。

部署前设置 `UNIFIED_AAVE_POOL_ADDRESS`、`UNIFIED_USDC_ADDRESS`，以及可选的
`UNIFIED_V3_FACTORY`、`UNIFIED_V3_ROUTER`、`UNIFIED_V3_QUOTER`，然后执行：

```powershell
npm run deploy:unified:fuji
```

部署结果写入 `deployments/unified-flashloan-<network>.json`。

默认测试覆盖：

- 状态4 `U -> X -> Y -> U` 的选择和逐跳返回值；
- packed path 执行、Aave premium、真实利润和利润归集；
- 三跳报价不盈利时的 `RuntimeProgress` 与结构化错误；
- 开启 token 借款后状态3的直接跨池路径。

## 历史实现

旧的多合约实现和测试保留在：

```text
back/src/
back/test/
back/scripts/
```

其中包括旧的 route controller、USDC 三角执行器、token 跨池执行器及其部署/执行脚本。
`back/` 只用于历史对照和迁移复盘，不属于当前默认编译入口。

更完整的策略、返回值和方向约定见：

```text
../../docs/闪电贷防MEV/单合约版本/01_单合约接口设计.md
../../docs/闪电贷防MEV/单合约版本/02_单合约实现算法.md
```
