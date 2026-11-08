# Robinhood Testnet V4 Directional Backfill Evidence

- generatedAt: 2026-10-17T20:08:00.124782Z
- chainId: 46630
- neededDirectionCount: 4
- satisfiedDirectionCount: 0
- stoppedReason: sqlite_liquidity_snapshot_shortfall_pending_rpc_backfill
- scannedSegmentCount: None
- initializeLogCount: None
- selectedPoolCount: 5
- sqliteSnapshotCount: 5
- sqliteLiveConfirmedPoolCount: 5
- sqliteZeroLiquidityPoolCount: 0
- sqliteMissingSnapshotPoolCount: 0
- verifiedSnapshotRefreshedAtRange: ["2026-10-15T13:11:50.813833Z", "2026-10-15T13:11:50.813833Z"]
- fromBlock: None
- toBlock: None
- directionCounts: {"0x0000000000000000000000000000000000000000:0x3b8262a63d25f0477c4dde23f83cfe22cb768c93": 2, "0x3b8262a63d25f0477c4dde23f83cfe22cb768c93:0x0000000000000000000000000000000000000000": 2, "0x0000000000000000000000000000000000000000:0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02": 3, "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02:0x0000000000000000000000000000000000000000": 3}

- scanDirection: existing_sqlite_liquidity_snapshot
- onchainRpcScan: False

SQLite-only mode reuses verified pool and liquidity snapshot rows and
records any shortfall without opening an RPC connection.
