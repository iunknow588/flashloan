# Robinhood 链上测试包

- 网络：`robinhood-testnet`
- 链 ID：`46630`
- 路径：`ETH -> NFLX -> PLTR -> ETH`
- Execution mode: `mixed_protocol_executor`
- 触发区块：`None`
- 复核区块：`None`
- 证据状态：`live_verified`
- Trusted for execution: `yes`
- Ready for preflight: `no`

## Size Gate

- Requested borrow raw: ``
- Max executable raw: ``

## 结论

Current evidence is blocked by preflight prerequisites and cannot enter calldata or fork/static-call.

## 关键阻断
- none

## Next Steps

1. Rebuild the real closed route from candidate pools and token addresses.
2. Re-run read-only on-chain quoter validation.
3. Generate calldata and fork/static-call tasks after audit passes.
4. Keep the broadcast gate closed.
