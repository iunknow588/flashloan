# Robinhood 閾句笂娆℃祴璇曞寘

- 缃戠粶锛歚robinhood-testnet`
- 閾?ID锛歚46630`
- 璺緞锛歚ETH -> 0X71178B -> 0XC9F9C8 -> ETH`
- Execution mode: `pool_manager_direct_swap_settle_take`
- 瑙﹀彂鍖哄潡锛歚109454378`
- 澶嶆牳鍖哄潡锛歚109454378`
- 璇佹嵁鐘舵€侊細`live_verified`
- Trusted for execution: `no`
- Ready for preflight: `yes`

## Size Gate

- Requested borrow raw: `715255737304`
- Max executable raw: `498365714991916341`

## 缁撹

Current evidence can enter fork/static-call preflight, but it is not broadcast-ready.

## 鍏抽敭闃绘柇
- none

## Next Steps

1. Pin the route and block snapshot.
2. Generate calldata and minOut constraints.
3. Run static-call or transaction simulation on a fork.
4. Consider signing and broadcast only after preflight passes.
