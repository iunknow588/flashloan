# Robinhood 链上测试包

- 网络：`robinhood-testnet`
- 链 ID：`46630`
- 路径：`ETH -> 0XC9F9C8 -> 0X71178B -> ETH`
- Execution mode: `pool_manager_direct_swap_settle_take`
- 触发区块：`113728916`
- 复核区块：`113728916`
- 证据状态：`pool_insufficient_for_profitable_execution`
- Trusted for execution: `no`
- Ready for preflight: `no`

## Size Gate

- Requested borrow raw: `10000000000000000`
- Max executable raw: ``

## 结论

Current evidence is blocked by preflight prerequisites and cannot enter calldata or fork/static-call.

## 关键阻断
- 至少一跳价格影响无法确认或超过 10 bps，不能进入交易预检。
- 链上核算扣除闪电贷费用后仍未转正，不能进入预检。
- 当前池子深度不足以支持满足单跳 10 bps 限制且扣除闪电贷费用后盈利的借贷规模。
- latest_snapshot_route_not_executable_candidate

## Next Steps

1. Rebuild the real closed route from candidate pools and token addresses.
2. Re-run read-only on-chain quoter validation.
3. Generate calldata and fork/static-call tasks after audit passes.
4. Keep the broadcast gate closed.
