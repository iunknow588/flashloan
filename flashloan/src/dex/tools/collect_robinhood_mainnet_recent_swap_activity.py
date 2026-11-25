"""Measure and publish the mainnet ``recent_swap_active`` markers.

The marker answers one question per pool: did this pool produce a ``Swap``
event inside the lookback window.  The window is pinned to one chain tip,
so every answer belongs to the same moment.

The tool is read-only unless ``--write`` is given.  ``--write`` publishes the
markers into ``v4_exchange_pool_recent_swap_activity``, the table the Rust
pool manager reads when it materialises the marker into the liquidity
snapshot row.  Unmeasured pools get no row at all: a pool that was not
measured and a pool that was measured without trades are different facts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.chain_database_context import chain_database_path  # noqa: E402
from core.db.storage_v4_exchange import (  # noqa: E402
    upsert_v4_pool_recent_swap_activity,
)
from core.env_loader import load_chain_env_files  # noqa: E402
from dex.intent_trade.chains.robinhood.v4_pool_index import (  # noqa: E402
    RECENT_SWAP_LOOKBACK_DAYS,
    RECENT_SWAP_SCAN_MIN_REQUEST_INTERVAL_SECONDS,
    RecentSwapScanError,
    collect_recent_swap_activity_markers,
)


DEFAULT_NETWORK = "robinhood"
DEFAULT_CHAIN_ID = 4663
DEFAULT_PROTOCOL = "uniswap_v4"


def _read_pool_ids(database: Path, network: str, chain_id: int, protocol: str) -> list[str]:
    """Read the pool library of one chain scope, read-only."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT pool_id FROM v4_exchange_pools "
            "WHERE network = ? AND chain_id = ? AND protocol = ? ORDER BY pool_id",
            (network, int(chain_id), protocol),
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]


def _read_pool_manager(database: Path, network: str, chain_id: int) -> str:
    """Read the PoolManager address the chain scope was indexed against."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT pool_manager FROM v4_exchange_pools_core4 "
            "WHERE network = ? AND chain_id = ? AND pool_manager IS NOT NULL LIMIT 1",
            (network, int(chain_id)),
        ).fetchone()
    finally:
        connection.close()
    if not row or not str(row[0] or "").strip():
        raise SystemExit("no PoolManager address is recorded for this chain scope")
    return str(row[0]).strip()


def _rpc_candidates(database: Path, network: str) -> list[str]:
    """List the chain's RPC endpoints, most preferred first."""

    candidates: list[str] = []
    override = os.getenv("ROBINHOOD_MAINNET_RPC_URL", "").strip()
    if override:
        candidates.append(override)
    registry = Path(__file__).resolve().parents[3] / "chains" / network / "chain.json"
    if registry.is_file():
        payload = json.loads(registry.read_text(encoding="utf-8-sig"))
        rpc = payload.get("rpc") if isinstance(payload, dict) else None
        if isinstance(rpc, dict):
            primary = str(rpc.get("primary") or "").strip()
            if primary:
                candidates.append(primary)
            for fallback in rpc.get("fallbacks") or []:
                value = str(fallback or "").strip()
                if value:
                    candidates.append(value)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def main() -> int:
    load_chain_env_files(Path(__file__).resolve())
    parser = argparse.ArgumentParser(
        description="Measure recent Swap activity for the mainnet pool library."
    )
    parser.add_argument("--database-url", default="", help="Chain database path; defaults to the chain scope database.")
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--protocol", default=DEFAULT_PROTOCOL)
    parser.add_argument("--rpc-url", default="", help="RPC endpoint override.")
    parser.add_argument("--pool-manager", default="", help="PoolManager address override.")
    parser.add_argument("--lookback-days", type=int, default=RECENT_SWAP_LOOKBACK_DAYS)
    parser.add_argument("--block-span", type=int, default=100_000, help="Blocks per request; split automatically when the provider cap is hit.")
    parser.add_argument("--pool-batch-size", type=int, default=900, help="Pool ids per request; the endpoint accepts fewer than 1000 topic selectors.")
    parser.add_argument("--concurrency", type=int, default=3, help="Parallel requests against one endpoint. The endpoint answers a burst with HTTP 429, so this stays low.")
    parser.add_argument(
        "--min-interval",
        type=float,
        default=RECENT_SWAP_SCAN_MIN_REQUEST_INTERVAL_SECONDS,
        help="Smallest gap between two requests, in seconds. The scan paces itself with this.",
    )
    parser.add_argument("--checkpoint", default="", help="Checkpoint file; enables resuming an interrupted pass.")
    parser.add_argument("--resume", action="store_true", help="Resume from the checkpoint file.")
    parser.add_argument("--write", action="store_true", help="Publish the markers into the activity table.")
    parser.add_argument("--report", default="", help="Optional JSON report path.")
    args = parser.parse_args()

    database = (
        Path(args.database_url).expanduser().resolve()
        if args.database_url
        else chain_database_path(int(args.chain_id), args.network).resolve()
    )
    if not database.is_file():
        raise SystemExit(f"chain database is missing: {database}")

    pool_ids = _read_pool_ids(database, args.network, args.chain_id, args.protocol)
    if not pool_ids:
        raise SystemExit("the pool library of this chain scope is empty")
    pool_manager = args.pool_manager.strip() or _read_pool_manager(database, args.network, args.chain_id)

    endpoints = [args.rpc_url.strip()] if args.rpc_url.strip() else _rpc_candidates(database, args.network)
    if not endpoints:
        raise SystemExit("no RPC endpoint is configured for this chain")

    checkpoint = Path(args.checkpoint).expanduser() if args.checkpoint else None

    def _progress(state: dict) -> None:
        print(
            "scan {completedChunks}/{totalChunks} chunks, "
            "logs={matchedLogCount}, pools_seen={distinctPoolIds}, "
            "requests={requestCount}, rate_limited={rateLimitedCount}".format(**state),
            flush=True,
        )

    started = time.time()
    print(
        f"scanning {len(pool_ids)} library pools over {args.lookback_days} days "
        f"against {endpoints[0]}",
        flush=True,
    )
    result: dict | None = None
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            result = collect_recent_swap_activity_markers(
                rpc_url=endpoint,
                pool_manager=pool_manager,
                pool_ids=pool_ids,
                lookback_days=int(args.lookback_days),
                block_span=int(args.block_span),
                pool_batch_size=int(args.pool_batch_size),
                concurrency=int(args.concurrency),
                min_request_interval=float(args.min_interval),
                checkpoint_path=checkpoint,
                resume=bool(args.resume),
                progress=_progress,
            )
            result["rpcUrl"] = endpoint
            break
        except RecentSwapScanError as error:
            errors.append(f"{endpoint}: {error}")
    if result is None:
        raise SystemExit("every endpoint failed:\n" + "\n".join(errors))

    rows = [
        {
            "pool_id": pool_id,
            "recent_swap_active": 1 if pool_id in set(result["activePoolIds"]) else 0,
            "recent_swap_lookback_days": int(result["lookbackDays"]),
            "recent_swap_scan_block_number": int(result["scanBlockNumber"]),
        }
        for pool_id in pool_ids
    ]
    published = 0
    if args.write:
        published = upsert_v4_pool_recent_swap_activity(
            str(database),
            network=args.network,
            chain_id=int(args.chain_id),
            protocol=args.protocol,
            rows=rows,
        )

    report = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database": str(database),
        "network": args.network,
        "chainId": int(args.chain_id),
        "protocol": args.protocol,
        "poolManager": pool_manager,
        "rpcUrl": result.get("rpcUrl", ""),
        "lookbackDays": int(result["lookbackDays"]),
        "scanBlockNumber": int(result["scanBlockNumber"]),
        "startBlock": int(result["startBlock"]),
        "blockSpan": int(result["blockSpan"]),
        "poolBatchCount": int(result["poolBatchCount"]),
        "poolBatchSize": int(result["poolBatchSize"]),
        "chunkCount": int(result["chunkCount"]),
        "scannedChunkCount": int(result["scannedChunkCount"]),
        "matchedLogCount": int(result["matchedLogCount"]),
        "distinctPoolIdsSeenInWindow": int(result["distinctPoolIds"]),
        "libraryPoolCount": len(pool_ids),
        "activePoolCount": int(result["activePoolCount"]),
        "inactivePoolCount": int(result["inactivePoolCount"]),
        "activeRatio": round(int(result["activePoolCount"]) / len(pool_ids), 6),
        "requestCount": int(result.get("requestCount") or 0),
        "rateLimitedCount": int(result.get("rateLimitedCount") or 0),
        "failedRequestCount": int(result.get("failedRequestCount") or 0),
        "splitCount": int(result.get("splitCount") or 0),
        "publishedRows": int(published),
        "written": bool(args.write),
        "elapsedSeconds": round(time.time() - started, 3),
        "activePoolIds": result["activePoolIds"],
        "inactivePoolIds": result["inactivePoolIds"],
    }
    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())