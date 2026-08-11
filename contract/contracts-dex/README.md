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
