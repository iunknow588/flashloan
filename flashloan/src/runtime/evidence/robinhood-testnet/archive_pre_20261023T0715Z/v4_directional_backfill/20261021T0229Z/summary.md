# Robinhood Testnet V4 Directional Backfill Blocked Attempt

- attemptedAt: 2026-10-21T02:29:00Z
- chainId: 46630
- status: blocked
- blockedReason: rpc_http_429
- probe: `eth_chainId` + `eth_blockNumber` batch passed at `https://rpc.testnet.chain.robinhood.com/rpc`, block `139789621`
- failure: `collect_robinhood_v4_backfill_evidence.py` live confirmation hit `HTTP Error 429: Too Many Requests` while confirming indexed direction candidates
- retry: one retry after ~20s returned the same 429; no further retries in this window
- safety: `writesSqlite=false`, `broadcastsTransaction=false`
- nextAction: wait for RPC stability, rerun without `--sqlite-only`, then run `refresh_robinhood_v4_live.py --candidate-backfill`
