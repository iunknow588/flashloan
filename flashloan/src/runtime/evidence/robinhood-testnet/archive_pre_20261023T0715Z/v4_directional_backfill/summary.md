# Robinhood Testnet V4 Directional Backfill Evidence

- generatedAt: 2026-10-20T02:50:46.353499Z
- chainId: 46630
- neededDirectionCount: 2
- satisfiedDirectionCount: 0
- stoppedReason: floor_reached_or_range_exhausted
- scannedSegmentCount: 363
- initializeLogCount: 0
- selectedPoolCount: 2
- sqliteSnapshotCount: None
- sqliteLiveConfirmedPoolCount: None
- sqliteZeroLiquidityPoolCount: None
- sqliteMissingSnapshotPoolCount: None
- verifiedSnapshotRefreshedAtRange: []
- fromBlock: 120947927
- toBlock: 139120365
- directionCounts: {"0x0000000000000000000000000000000000000000:0x0045e403e2d1153ad9185b4baa93a5ee5965a156": 1, "0x0000000000000000000000000000000000000000:0x00a3bdf72d0376a8e784cc854724de1a4e673195": 1}

- scanDirection: newest_to_oldest
- onchainRpcScan: None

SQLite-only mode reuses verified pool and liquidity snapshot rows and
records any shortfall without opening an RPC connection.
