# Robinhood 链上测试包

- 网络：`robinhood-testnet`
- 链 ID：`46630`
- 路径：`TZ -> TB -> TA -> TZ`
- Execution mode: `pool_manager_direct_swap_settle_take`
- 触发区块：`118022639`
- 复核区块：`118022639`
- 证据状态：`live_verified`
- Trusted for execution: `no`
- Ready for preflight: `yes`

## Size Gate

- Requested borrow raw: `100000000000000`
- Max executable raw: ``

## 结论

Current evidence can enter fork/static-call preflight, but it is not broadcast-ready.

## 关键阻断
- none

## Next Steps

1. Pin the route and block snapshot.
2. Generate calldata and minOut constraints.
3. Run static-call or transaction simulation on a fork.
4. Consider signing and broadcast only after preflight passes.
