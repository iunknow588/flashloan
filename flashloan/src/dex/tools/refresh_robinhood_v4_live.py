"""One-command Robinhood V4 live rebuild from in-memory on-chain evidence.

The command owns the complete raw V4 surface for the requested network.  It
builds the pool index from PoolManager Initialize logs (or reuses the SQLite
index when ``--skip-index`` is passed), reads live PoolManager liquidity,
clears the old raw pool/liquidity rows for that scope, imports the in-memory
payloads, and rebuilds candidates, market observation, closed loops and DEX
surfaces from SQLite only.

JSON evidence files are never read by this command.  The old
``latest.json -> import_existing_json`` path is deliberately not part of the
normal refresh flow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db import builders  # noqa: E402
from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.import_v4_evidence import import_v4_evidence_payloads  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from dex.intent_trade.chains.robinhood.flash_loan_pool_views import (  # noqa: E402
    clear_robinhood_v4_pool_cache,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (  # noqa: E402
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    all_anchor_symbols,
)
from market.binance_market.dex_surface_cache import _clear_dex_surface_cache  # noqa: E402


def _network_scope(network: str, chain_id: int | None) -> tuple[str, int, str]:
    normalized = str(network or "").strip().lower()
    if normalized in {ROBINHOOD_TESTNET_NETWORK, "testnet"} or chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_MARKET_ID
    return ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID, ROBINHOOD_MARKET_ID


_STALE_LIQUIDITY_KEYS = (
    "hasLiquidity",
    "has_liquidity",
    "liquidity",
    "liquidityIndex",
    "liquidity_index",
    "estimatedTvlUsd",
    "estimated_tvl_usd",
    "balances",
    "balances_json",
    "depthStatus",
    "depth_status",
    "depthBand",
    "depth_band",
    "recentSwapActive",
    "recent_swap_active",
    "status",
    "liquidityReadAt",
    "liquidity_read_at",
    "liquidityBlockNumber",
    "liquidity_block_number",
    "observedAt",
    "refreshedAt",
    "liquidityError",
    "liquiditySource",
)


def _strip_stale_liquidity_from_sqlite_pools(pools: list[dict]) -> None:
    """Drop old liquidity markers carried by SQLite-derived pool rows.

    ``--skip-index`` reuses the pool table as the pool-key source only.  Old
    has_liquidity/depth values must not be re-imported after the raw tables
    are cleared, otherwise a previous bad JSON import survives the refresh.
    """
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for key in _STALE_LIQUIDITY_KEYS:
            pool.pop(key, None)
        pool["status"] = "initialized_pool"


def _load_index_payload(
    *,
    database: str,
    network: str,
    chain_id: int,
    rpc_url: str | None,
    pool_manager: str | None,
    skip_index: bool,
    mark_recent_swaps: bool,
) -> dict:
    if skip_index:
        from dex.intent_trade.chains.robinhood_common.v4_sqlite_view import (
            load_v4_pool_index_payload,
        )

        payload = load_v4_pool_index_payload(
            network,
            chain_id,
            database_url=database,
        )
        if not payload.get("ok") or not payload.get("pools"):
            raise RuntimeError(
                f"{network} SQLite V4 pool index is empty; run without --skip-index"
            )
        _strip_stale_liquidity_from_sqlite_pools(payload["pools"])
        return {
            "payload": payload.get("payload") or {},
            "source": "sqlite_db",
        }

    if network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import (
            build_v4_pool_index_from_initialize_logs,
        )

        payload = build_v4_pool_index_from_initialize_logs(
            rpc_url=rpc_url,
            pool_manager=pool_manager,
            from_block=0,
            to_block=None,
            include_metadata=False,
            mark_recent_swaps=mark_recent_swaps,
        )
    else:
        from dex.intent_trade.chains.robinhood.v4_pool_index import (
            build_v4_pool_index_from_initialize_logs,
        )

        payload = build_v4_pool_index_from_initialize_logs(
            rpc_url=rpc_url,
            pool_manager=pool_manager,
            from_block=0,
            to_block=None,
            include_metadata=False,
            mark_recent_swaps=mark_recent_swaps,
        )
    if not payload.get("pools"):
        raise RuntimeError(f"{network} V4 Initialize scan returned no pools")
    return {"payload": payload, "source": "onchain_initialize_logs"}


def _connect_rpc(network: str, chain_id: int, rpc_url: str | None) -> tuple[object, str]:
    if network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (
            _connect_chain_rpc,
        )

        w3, resolved = _connect_chain_rpc(network, chain_id)
        return w3, rpc_url or resolved
    from dex.intent_trade.chains.robinhood.v4_quote_adapter import _connect_chain_rpc

    w3, resolved = _connect_chain_rpc(network, chain_id)
    return w3, rpc_url or resolved


def _apply_live_liquidity(
    *,
    network: str,
    chain_id: int,
    index_payload: dict,
    rpc_url: str | None,
    pool_manager: str | None,
    pool_ids: list[str] | None,
    batch_size: int,
    block_number: int,
) -> dict:
    w3, resolved_rpc_url = _connect_rpc(network, chain_id, rpc_url)
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != chain_id:
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {chain_id}")
    resolved_pool_manager = pool_manager or str(index_payload.get("poolManager") or "")
    pools = [pool for pool in index_payload.get("pools") or [] if isinstance(pool, dict)]
    if not pools:
        raise RuntimeError(f"{network} V4 pool index has no pools to enrich")
    if network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
            _apply_extsload_liquidity,
        )

        activity = _apply_extsload_liquidity(
            pools,
            w3=w3,
            rpc_url=resolved_rpc_url,
            pool_manager=resolved_pool_manager,
            pool_ids=pool_ids,
            batch_size=batch_size,
            block_number=block_number,
        )
    else:
        from dex.intent_trade.chains.robinhood.v4_pool_index import (
            _enrich_pool_liquidity,
        )

        activity = _enrich_pool_liquidity(
            w3,
            pools,
            pool_manager=resolved_pool_manager,
            rpc_url=resolved_rpc_url,
            pool_ids=pool_ids,
            block_number=block_number,
        )
    return {
        **activity,
        "scannedPoolCount": activity.get("scannedPoolCount") or len(pools),
        "rpcUrl": resolved_rpc_url,
        "blockNumber": block_number,
    }


def _make_liquidity_payload(index_payload: dict) -> dict:
    return {
        "schemaVersion": "robinhood-v4-pool-liquidity-v1",
        "network": index_payload.get("network"),
        "chainId": index_payload.get("chainId"),
        "poolManager": index_payload.get("poolManager"),
        "generatedAt": index_payload.get("generatedAt"),
        "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
        "source": "pool_manager_extsload_live_state_batch",
        "pools": index_payload.get("pools") or [],
    }


def _pool_ids_arg(pool_ids: str | list[str] | None) -> list[str]:
    if isinstance(pool_ids, list):
        items = [str(item) for item in pool_ids]
    else:
        items = str(pool_ids or "").split(",")
    return [
        str(item).strip().lower()
        for item in items
        if str(item).strip().startswith("0x")
    ]


def refresh_v4_live(
    *,
    database: str = "",
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int | None = None,
    rpc_url: str | None = "",
    pool_manager: str = "",
    skip_index: bool = False,
    skip_liquidity: bool = False,
    skip_build: bool = False,
    mark_recent_swaps: bool = False,
    pool_ids: str | list[str] | None = "",
    batch_size: int = 100,
    anchors: str = "",
    verify_only: bool = False,
    clear_cache: bool = True,
) -> dict:
    """Rebuild Robinhood V4 SQLite tables from live/in-memory evidence.

    This is the shared entry point for CLI, page refresh and integration
    callers. JSON evidence files are never read or written here.
    """
    network, chain_id, market_id = _network_scope(network, chain_id)
    database = absolute_database_path(database)
    ensure_database_schema(str(database))
    explicit_anchors = anchors

    steps: list[dict] = []
    errors: list[str] = []

    try:
        index_result = _load_index_payload(
            database=str(database),
            network=network,
            chain_id=chain_id,
            rpc_url=rpc_url or None,
            pool_manager=pool_manager or None,
            skip_index=skip_index,
            mark_recent_swaps=mark_recent_swaps,
        )
        index_payload = index_result["payload"]
        steps.append(
            {
                "step": "v4_pool_index",
                "ok": True,
                "source": index_result["source"],
                "poolCount": len(index_payload.get("pools") or []),
                "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
            }
        )
    except Exception as exc:
        errors.append(f"v4_pool_index: {exc}")
        steps.append({"step": "v4_pool_index", "ok": False, "error": str(exc)})

    liquidity_activity: dict = {}
    if not errors and not skip_liquidity:
        selected_pool_ids = _pool_ids_arg(pool_ids)
        try:
            block_number = int(index_payload.get("generatedBlockNumber") or 0)
            if block_number <= 0:
                w3, _ = _connect_rpc(network, chain_id, rpc_url or None)
                block_number = int(w3.eth.block_number)
            liquidity_activity = _apply_live_liquidity(
                network=network,
                chain_id=chain_id,
                index_payload=index_payload,
                rpc_url=rpc_url or None,
                pool_manager=pool_manager or None,
                pool_ids=selected_pool_ids or None,
                batch_size=batch_size,
                block_number=block_number,
            )
            steps.append(
                {
                    "step": "v4_pool_liquidity",
                    "ok": True,
                    "source": liquidity_activity.get("liquiditySource"),
                    "markedPoolCount": liquidity_activity.get("liquidityMarkedPoolCount"),
                    "scannedPoolCount": liquidity_activity.get("scannedPoolCount"),
                    "errorCount": liquidity_activity.get("liquidityErrorCount"),
                    "blockNumber": liquidity_activity.get("blockNumber"),
                }
            )
        except Exception as exc:
            errors.append(f"v4_pool_liquidity: {exc}")
            steps.append({"step": "v4_pool_liquidity", "ok": False, "error": str(exc)})

    import_result: dict = {}
    if not errors:
        try:
            if skip_index and skip_liquidity:
                import_result = {
                    "ok": True,
                    "skipped": True,
                    "source": "sqlite_only",
                    "detail": "raw V4 tables already present in SQLite; business tables rebuilt only",
                }
            else:
                import_result = import_v4_evidence_payloads(
                    str(database),
                    network=network,
                    chain_id=chain_id,
                    index_payload=index_payload,
                    liquidity_payload=_make_liquidity_payload(index_payload),
                    replace=True,
                )
            steps.append(
                {
                    "step": "import_v4_evidence_payloads",
                    "ok": True,
                    "v4PoolCount": import_result.get("v4PoolCount"),
                    "tokenCount": import_result.get("tokenCount"),
                    "skipped": import_result.get("skipped", False),
                }
            )
        except Exception as exc:
            errors.append(f"import_v4_evidence_payloads: {exc}")
            steps.append({"step": "import_v4_evidence_payloads", "ok": False, "error": str(exc)})

    build_result: dict = {}
    if not errors and not skip_build:
        anchors = tuple(
            all_anchor_symbols(
                network,
                database_url=str(database),
            )
        )
        if explicit_anchors:
            anchors = tuple(
                str(item).strip().upper()
                for item in explicit_anchors.split(",")
                if str(item).strip()
            )
        if not anchors:
            errors.append("no anchors resolved from SQLite; pass --anchors explicitly")
        else:
            try:
                if verify_only:
                    build_result = builders.verify_anchor_integrity(
                        str(database),
                        network=network,
                        chain_id=chain_id,
                        market_id=market_id,
                        anchors=anchors,
                    )
                else:
                    build_result = builders.build_all_anchor_data(
                        str(database),
                        network=network,
                        chain_id=chain_id,
                        market_id=market_id,
                        anchors=anchors,
                    )
                steps.append(
                    {
                        "step": "build_all_anchor_data" if not verify_only else "verify_anchor_integrity",
                        "ok": True,
                        "anchorCount": len(anchors),
                    }
                )
            except Exception as exc:
                errors.append(f"build_all_anchor_data: {exc}")
                steps.append({"step": "build_all_anchor_data", "ok": False, "error": str(exc)})

    snapshot_result: dict = {}
    if not errors and not skip_build and not verify_only:
        try:
            from market.binance_market.dex_surfaces import database_overview_snapshot

            snapshot_result = database_overview_snapshot(
                chain_id=chain_id,
                protocols=["uniswap_v4"],
            )
            steps.append(
                {
                    "step": "dex_overview_snapshot",
                    "ok": True,
                    "routeCount": len(snapshot_result.get("closedLoopRouteSummaries") or []),
                }
            )
        except Exception as exc:
            errors.append(f"dex_overview_snapshot: {exc}")
            steps.append({"step": "dex_overview_snapshot", "ok": False, "error": str(exc)})

    cleared = 0
    if clear_cache:
        try:
            cleared = _clear_dex_surface_cache(
                chain_id=chain_id,
                protocols=["uniswap_v4"],
            )
        except Exception as exc:
            errors.append(f"dex_surface_cache: {exc}")
        try:
            cleared += clear_robinhood_v4_pool_cache(
                database_url=str(database),
                network=network,
                chain_id=chain_id,
            )
        except Exception as exc:
            errors.append(f"robinhood_v4_pool_cache: {exc}")

    return {
        "ok": not errors,
        "database": str(database),
        "network": network,
        "chainId": chain_id,
        "marketId": market_id,
        "steps": steps,
        "liquidity": liquidity_activity,
        "import": import_result,
        "build": {
            "ok": bool(build_result.get("ok", True)),
            "anchorCount": len(build_result.get("anchors") or []),
        },
        "errors": errors,
        "cacheEntriesCleared": cleared,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="")
    parser.add_argument(
        "--network",
        default=ROBINHOOD_TESTNET_NETWORK,
        choices=(ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_NETWORK),
    )
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--pool-manager", default="")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--skip-liquidity", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--mark-recent-swaps", action="store_true")
    parser.add_argument("--pool-ids", default="", help="Comma-separated V4 pool ids to scan for live liquidity; empty means all.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--anchors", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--no-clear-cache", dest="clear_cache", action="store_false")
    parser.set_defaults(clear_cache=True)
    args = parser.parse_args()

    report = refresh_v4_live(
        database=args.database,
        network=args.network,
        chain_id=args.chain_id,
        rpc_url=args.rpc_url,
        pool_manager=args.pool_manager,
        skip_index=args.skip_index,
        skip_liquidity=args.skip_liquidity,
        skip_build=args.skip_build,
        mark_recent_swaps=args.mark_recent_swaps,
        pool_ids=args.pool_ids,
        batch_size=args.batch_size,
        anchors=args.anchors,
        verify_only=args.verify_only,
        clear_cache=args.clear_cache,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
