# 14 Aave V3 Origin 对齐修正

本文根据 Aave DAO `aave-v3-origin` README 及其 v3.3、v3.7 文档，对当前 AVAX 清算机器人设计和合约实现做修正。

## 1. 参考基线

- Aave v3 origin 当前文档入口明确列出 v3.3、v3.7 等升级说明和审计记录。
- v3.3 重点影响清算的 close factor、dust debt、bad debt cleanup。
- v3.7 重点影响 liquidation rounding，rounding loss 明确由 liquidator 吸收。
- 集成合约应使用稳定 ERC20 处理方式和 mainnet fork 测试，而不能只依赖 mock 测试。

## 2. 设计修正

### 2.1 合约只负责闭环，不负责判断机会

执行合约保持单一职责：

1. 接收链下已校验的清算参数。
2. 通过 Aave `flashLoanSimple` 借入债务资产。
3. 在回调里执行 `liquidationCall`。
4. 将获得的抵押资产兑换回债务资产。
5. 检查能否归还本金、premium，并满足最小利润。

账户发现、HF 判断、close factor 估算、DEX 报价和滑点预算全部由链下机器人完成。

### 2.2 close factor 不能硬编码 50%

Aave v3.3 后，清算 close factor 会受全仓位、特定债务规模、dust leftover 规则影响。机器人不得简单使用 `debt * 0.5` 作为 `debtToCover`。

执行前必须至少满足一个条件：

- 从 Aave 数据提供方读取并使用 `maxDebtToLiquidate`。
- 或在 fork/callStatic 中验证当前 `debtToCover` 不会因 close factor、dust leftover、bad debt cleanup 规则回滚。

### 2.3 liquidation rounding 要留利润缓冲

Aave v3.7 liquidation rounding 将 `maxCollateralToLiquidate`、bonus split、protocol fee 的 rounding 方向改成对 liquidator 保守。链下利润模型必须增加 rounding buffer。

建议初始规则：

- `minProfitAmount` 必须大于链下预计净利润扣除 gas、premium、滑点、MEV buffer 后的保守值。
- 对每个 token 根据 decimals 至少预留 1-10 wei 的 rounding buffer。
- 不允许提交贴边利润交易。
- `minCollateralSwapOut` 不允许在生产路径中为 0；只有本地 mock/调试可显式开启零最小输出。

### 2.4 执行前必须静态模拟

提交真实交易前必须执行：

```text
callStatic / staticCall requestLiquidation(request)
```

只有静态模拟成功且利润达标，才允许进入真实发送队列。

### 2.5 生产合约使用 SafeERC20

执行合约应使用成熟 ERC20 处理库，避免遇到非标准 ERC20 时出现返回值兼容问题。MVP 合约应迁移到 OpenZeppelin `SafeERC20`。

## 3. 测试修正

测试分层调整为：

| 层级 | 必须覆盖 |
|---|---|
| Mock 单元测试 | 闪电贷回调、清算、兑换、还款、低利润回滚、权限回滚 |
| Fork 静态模拟 | 真实 Aave Pool、真实 token、真实 router 接口兼容 |
| 历史回放 | 最近 7 天快速扫描、一周前分段回补、连续区块记录 |
| 失败样本 | close factor 超限、dust leftover、低利润、高滑点、RPC 失败 |

## 4. 上线门槛修正

上线前新增 Go / No-Go 条件：

- [ ] 合约使用 SafeERC20。
- [ ] `debtToCover` 不硬编码 50%，由链下 maxDebt 估算或静态模拟确认。
- [ ] v3.7 rounding buffer 已进入利润模型。
- [ ] mainnet fork 静态模拟通过。
- [ ] 每次真实提交前执行 callStatic。
- [ ] 低利润、低滑点输出、高 premium、dust leftover 均有测试样本。

## 5. 当前实现状态

截至 2026-07-30：

- `contracts-bot` 已有第一版 `AaveV3LiquidationExecutor`。
- mock 清算闭环测试已通过。
- 已补齐 SafeERC20、fork 测试脚手架、静态模拟脚本和机器人侧 payload builder。
- 已新增 `/api/liquidation/account/payload`，用于从账户报告生成合约 `requestLiquidation` 参数。
- payload builder 默认拒绝没有 `min_collateral_swap_out` 的执行参数。
- 待后续使用真实 `AVALANCHE_RPC_URL` 和真实清算样本执行 mainnet fork 验证。
