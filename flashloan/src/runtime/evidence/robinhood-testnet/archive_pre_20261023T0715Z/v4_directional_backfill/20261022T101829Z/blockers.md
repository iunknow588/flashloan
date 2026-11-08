# Robinhood Testnet V4 Directional Backfill Blockers

- generatedAt: 2026-10-22T10:18:29Z
- network: robinhood-testnet
- chainId: 46630
- scope: 2 required directions
- target: 5 live candidate pools per direction
- observedCounts: ETH/Z 2, ETH/Y 2
- satisfiedDirectionCount: 0

## Blocker 1: RPC rate limiting

The one-command refresh path
`refresh_robinhood_v4_live.py --candidate-backfill` first rescans the full
V4 pool index and then reads live liquidity for the whole index. The primary
testnet RPC rejected the liquidity batch with:

```text
HTTP Error 429: Too Many Requests
```

This is an external provider limit. The run stopped fail-closed and did not
mark liquidity as complete. It was not hidden with infinite retries.

## Blocker 2: directional backfill reaches the pool creation floor

A read-only reverse Initialize-log scan for the two directions was run
against the publicnode provider with fallback RPCs. It scanned 1590 segments
from block 140685017 back to the stored pool creation floor 91000224 and
found 0 new live pools for the two directions.

The existing SQLite index contains 5 pools total for the two directions, of
which 4 have verified live liquidity snapshots. The direction counts remain:

- ETH/Z direction: 2 live candidates
- ETH/Y direction: 2 live candidates

No additional live pools exist in the scanned range, so the 5-per-direction
target cannot be satisfied by inventing or duplicating pool rows.

## Conclusion

The task stays fail-closed: `satisfiedDirectionCount=0`, no broadcast is
opened, and no candidate count is promoted. Any later completion requires
either a new stable RPC window that can read liquidity for the missing
indexed pools, or a chain-side liquidity/token setup that creates live pools
in the two directions.
