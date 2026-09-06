# Robinhood 链上次测试包

- 网络：`robinhood`
- 链 ID：`4663`
- 路径：`USDG -> WETH -> CATS -> WETH -> USDG`
- 触发区块：`45618391`
- 复核区块：`45618391`
- 证据状态：`live_verified`
- 执行可信：`否`
- 预检就绪：`否`

## 结论

当前记录已被预检前置校验阻断，不能进入 calldata 或 fork/static-call。

## 关键阻断
- 链上复核后未覆盖闪电贷费用、gas 储备和最小利润阈值。

## 下一步

1. 回到候选池构建层，按 token 地址重建真实闭环。
2. 重新执行链上 Quoter 复核。
3. 审计通过后再生成 calldata 与 fork/static-call 任务。
4. 广播闸门继续保持关闭。
