# Robinhood Testnet V4 Directional Backfill Evidence

- generatedAt: 2026-10-19T10:47:28.264344Z
- chainId: 46630
- neededDirectionCount: 4
- satisfiedDirectionCount: 0
- stoppedReason: floor_reached_or_range_exhausted
- scannedSegmentCount: 953
- initializeLogCount: 0
- selectedPoolCount: 5
- sqliteSnapshotCount: None
- sqliteLiveConfirmedPoolCount: None
- sqliteZeroLiquidityPoolCount: None
- sqliteMissingSnapshotPoolCount: None
- verifiedSnapshotRefreshedAtRange: []
- fromBlock: 91000224
- toBlock: 138668283
- directionCounts: {"0x0000000000000000000000000000000000000000:0x3b8262a63d25f0477c4dde23f83cfe22cb768c93": 2, "0x0000000000000000000000000000000000000000:0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02": 3, "0x3b8262a63d25f0477c4dde23f83cfe22cb768c93:0x0000000000000000000000000000000000000000": 2, "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02:0x0000000000000000000000000000000000000000": 3}

- scanDirection: newest_to_oldest
- onchainRpcScan: None

SQLite-only mode reuses verified pool and liquidity snapshot rows and
records any shortfall without opening an RPC connection.
