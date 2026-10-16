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
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db import builders  # noqa: E402
from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from core.db.v4_scan_state import (  # noqa: E402
    load_v4_scan_state,
    update_v4_scan_state,
)
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
from dex.tools.v4_rust_refresh import (  # noqa: E402
    import_v4_evidence_rows,
    prune_v4_evidence_rows,
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


def _remove_v4_json_evidence(
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Remove the optional V4 evidence JSON audit files after a DB rebuild.

    SQLite is the only formal source.  Deleting these files prevents an
    accidental later ``latest.json -> import_existing_json`` refresh from
    overwriting live chain evidence with stale placeholder/zero data.
    """
    from core.chain_data_registry import chain_data_file_path

    removed: list[str] = []
    for key in ("v4_pool_index", "v4_pool_liquidity", "v4_pool_snapshot"):
        path = chain_data_file_path(network, key, chain_id=chain_id)
        if path is None:
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(str(path))
        elif path.is_dir():
            for file_path in sorted(path.glob("*.json")):
                file_path.unlink(missing_ok=True)
                removed.append(str(file_path))
    return removed


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
    from_block: int = 0,
    merge_existing: bool = True,
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
            from_block=from_block,
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
            from_block=from_block,
            to_block=None,
            include_metadata=False,
            mark_recent_swaps=mark_recent_swaps,
        )
    if not payload.get("pools") and not merge_existing:
        raise RuntimeError(f"{network} V4 Initialize scan returned no pools")
    if merge_existing:
        payload = _merge_index_with_sqlite_pools(
            payload,
            database=database,
            network=network,
            chain_id=chain_id,
        )
    return {"payload": payload, "source": "onchain_initialize_logs"}


def _merge_index_with_sqlite_pools(
    onchain_payload: dict,
    *,
    database: str,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
) -> dict:
    """Merge on-chain Initialize rows into the SQLite pool index.

    An incremental Initialize scan only discovers newly created pools.  The
    existing SQLite pool rows are the index authority, so the merged payload
    always contains every known pool before liquidity is read.
    """
    from core.rs_bridge import merge_v4_pool_index as _rust_merge_v4_pool_index
    from core.rs_bridge import rust_available

    if rust_available():
        return _rust_merge_v4_pool_index(
            network,
            chain_id,
            onchain_payload,
            database_url=database,
            protocol=protocol,
        )
    from dex.intent_trade.chains.robinhood_common.v4_sqlite_view import (
        load_v4_pool_index_payload,
    )

    existing = load_v4_pool_index_payload(
        network,
        chain_id,
        database_url=database,
        protocol=protocol,
    )
    existing_payload = existing.get("payload") or {}
    existing_pools = (existing_payload.get("pools") or []) if existing.get("ok") else []
    merged_by_id: dict[str, dict] = {}
    for pool in existing_pools:
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("pool") or "").strip().lower()
        if pool_id:
            merged_by_id.setdefault(pool_id, dict(pool))
    for pool in onchain_payload.get("pools") or []:
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("pool") or "").strip().lower()
        if not pool_id:
            continue
        merged_by_id[pool_id] = dict(pool)
    merged = list(merged_by_id.values())
    max_block = 0
    for pool in merged:
        block = pool.get("generatedBlockNumber") or pool.get("initializeBlockNumber")
        try:
            max_block = max(max_block, int(block))
        except (TypeError, ValueError):
            pass
    merged_payload = {
        **onchain_payload,
        "pools": merged,
        "poolCount": len(merged),
        "generatedBlockNumber": (
            int(onchain_payload.get("generatedBlockNumber") or max_block)
            if onchain_payload.get("generatedBlockNumber") is not None
            else max_block
        ),
        "generatedAt": onchain_payload.get("generatedAt")
        or existing_payload.get("generatedAt"),
        "source": "onchain_initialize_logs_plus_sqlite_pools",
    }
    return merged_payload


def _connect_rpc(network: str, chain_id: int, rpc_url: str | None) -> tuple[object, str]:
    from web3 import Web3

    if rpc_url:
        w3 = Web3(
            Web3.HTTPProvider(
                rpc_url,
                request_kwargs={
                    "timeout": 25,
                    "verify": False,
                    "headers": {
                        "Content-Type": "application/json",
                        "User-Agent": "flashloan-robinhood-mainnet-v4/1.1",
                        "Connection": "close",
                    },
                },
            )
        )
        actual_chain_id = int(w3.eth.chain_id)
        if actual_chain_id != int(chain_id):
            raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {chain_id}")
        return w3, rpc_url
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
    batch_liquidity: bool = False,
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
    elif batch_liquidity:
        return _apply_batch_live_liquidity(
            index_payload=index_payload,
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


def _apply_batch_live_liquidity(
    *,
    index_payload: dict,
    w3: object,
    rpc_url: str,
    pool_manager: str,
    pool_ids: list[str] | None,
    batch_size: int,
    block_number: int,
) -> dict:
    """Read every indexed V4 pool via batched PoolManager extsloads.

    This is the fast generic mainnet path. Unknown read failures stay unknown
    and only an explicit ``liquidity is zero`` response proves a pool is empty.
    """
    from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
        _batch_read_v4_pool_states,
    )
    from dex.intent_trade.chains.robinhood.v4_pool_index import (
        _liquidity_depth_status,
        _liquidity_reserve_metrics,
    )

    pools = [pool for pool in index_payload.get("pools") or [] if isinstance(pool, dict)]
    wanted = (
        {
            str(pool_id).strip().lower()
            for pool_id in pool_ids
            if str(pool_id or "").strip().startswith("0x")
        }
        if pool_ids
        else None
    )
    selected = [
        pool
        for pool in pools
        if (
            str(pool.get("poolId") or "").startswith("0x")
            and (wanted is None or str(pool.get("poolId") or "").lower() in wanted)
        )
    ]
    all_ids = sorted({str(pool.get("poolId") or "").lower() for pool in selected})
    states: dict[str, dict] = {}
    errors: list[str] = []
    urls = tuple(
        dict.fromkeys(
            (
                *(str(url).strip() for url in (rpc_url,) if str(url or "").strip()),
                *_mainnet_rpc_urls_from_env(),
            )
        )
    )
    for start in range(0, len(all_ids), max(1, int(batch_size))):
        chunk = all_ids[start : start + max(1, int(batch_size))]
        chunk_states: dict[str, dict] = {}
        for candidate_url in urls:
            try:
                chunk_states = _batch_read_v4_pool_states(
                    rpc_url=candidate_url,
                    pool_manager=pool_manager,
                    pool_ids=chunk,
                )
            except Exception as exc:
                errors.append(str(exc)[:240])
                continue
            if chunk_states and any(not state.get("error") for state in chunk_states.values()):
                break
        if chunk_states and any(not state.get("error") for state in chunk_states.values()):
            states.update(chunk_states)
        else:
            errors.append(f"{chunk[0]}..{chunk[-1]}: no_state_success")

    liquid_count = 0
    zero_count = 0
    unavailable_count = 0
    stamped_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for pool in selected:
        pool_id = str(pool.get("poolId") or "").lower()
        state = states.get(pool_id)
        pool["liquiditySource"] = "pool_manager_extsload_live_state_batch"
        pool["liquidityReadAt"] = stamped_at
        pool["liquidityBlockNumber"] = block_number
        if state is None or state.get("error"):
            error_text = str((state or {}).get("error") or "live_state_missing").lower()
            if "liquidity is zero" in error_text:
                pool["hasLiquidity"] = False
                pool["liquidity"] = ""
                pool["liquidityIndex"] = 0
                pool["estimatedTvlUsd"] = 0
                pool["depthStatus"] = "live_liquidity_zero"
                pool["liquidityError"] = None
                zero_count += 1
            else:
                pool["hasLiquidity"] = None
                pool["liquidity"] = ""
                pool["liquidityIndex"] = 0
                pool["estimatedTvlUsd"] = 0
                pool["depthStatus"] = "live_liquidity_unavailable"
                pool["liquidityError"] = (state or {}).get("error") or "live_state_missing"
                unavailable_count += 1
            continue
        liquidity = int(str(state.get("liquidity") or "0"))
        if liquidity <= 0:
            pool["hasLiquidity"] = False
            pool["liquidity"] = ""
            pool["liquidityIndex"] = 0
            pool["estimatedTvlUsd"] = 0
            pool["depthStatus"] = "live_liquidity_zero"
            pool["liquidityError"] = None
            zero_count += 1
            continue
        metrics = _liquidity_reserve_metrics(pool, state)
        pool.update(metrics)
        pool["hasLiquidity"] = True
        pool["liquidity"] = str(liquidity)
        pool["liquiditySource"] = state.get("source") or "pool_manager_extsload_live_state_batch"
        pool["liquidityError"] = None
        pool["depthStatus"] = _liquidity_depth_status(pool)["label"]
        pool["status"] = "initialized_pool_with_liquidity"
        liquid_count += 1

    return {
        "liquidityMarkerActive": True,
        "liquidityMarkedPoolCount": liquid_count,
        "liquidityZeroPoolCount": zero_count,
        "liquidityUnavailablePoolCount": unavailable_count,
        "liquidityErrorCount": len(errors),
        "liquidityErrors": errors[:20],
        "liquidityScanAt": stamped_at,
        "liquidityPoolIds": sorted(
            str(pool.get("poolId") or "").lower()
            for pool in selected
            if pool.get("hasLiquidity") is True
        ),
        "liquiditySource": "pool_manager_extsload_live_state_batch",
        "scannedPoolCount": len(selected),
        "blockNumber": block_number,
    }


def _mainnet_rpc_urls_from_env() -> tuple[str, ...]:
    from dex.intent_trade.chains.robinhood import config as robinhood_config

    return robinhood_config.rpc_urls_from_env()


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
    batch_liquidity: bool = False,
    anchors: str = "",
    verify_only: bool = False,
    clear_cache: bool = True,
    prune_zero_liquidity: bool = True,
    prune_unknown_liquidity: bool = False,
    remove_json_evidence: bool = True,
    full_index_scan: bool = False,
    from_block_override: int | None = None,
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
        scan_state = load_v4_scan_state(
            str(database),
            network=network,
            chain_id=chain_id,
        )
        if from_block_override is not None:
            resolved_from_block = max(0, int(from_block_override))
        elif full_index_scan:
            resolved_from_block = 0
        else:
            last_scanned = int(scan_state.get("last_scanned_block") or 0)
            resolved_from_block = last_scanned + 1 if last_scanned > 0 else 0
        index_result = _load_index_payload(
            database=str(database),
            network=network,
            chain_id=chain_id,
            rpc_url=rpc_url or None,
            pool_manager=pool_manager or None,
            skip_index=skip_index,
            mark_recent_swaps=mark_recent_swaps,
            from_block=resolved_from_block,
            merge_existing=not skip_index,
        )
        index_payload = index_result["payload"]
        steps.append(
            {
                "step": "v4_pool_index",
                "ok": True,
                "source": index_result["source"],
                "poolCount": len(index_payload.get("pools") or []),
                "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
                "fromBlock": resolved_from_block if not skip_index else None,
                "scanMode": (
                    "full"
                    if full_index_scan or (resolved_from_block <= 0 and not skip_index)
                    else "incremental"
                ),
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
                batch_liquidity=batch_liquidity,
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
                import_result = import_v4_evidence_rows(
                    database=str(database),
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

    prune_result: dict = {}
    if not errors and not skip_build and not verify_only:
        try:
            if prune_zero_liquidity:
                prune_result = prune_v4_evidence_rows(
                    database=str(database),
                    network=network,
                    chain_id=chain_id,
                    protocol="uniswap_v4",
                    delete_unknown=prune_unknown_liquidity,
                )
                steps.append(
                    {
                        "step": "prune_non_executable_pools",
                        "ok": True,
                        "deletedZeroLiquidityCount": prune_result.get("deletedZeroLiquidityCount"),
                        "deletedUnknownLiquidityCount": prune_result.get("deletedUnknownLiquidityCount"),
                        "deletedPoolCount": prune_result.get("deletedPoolCount"),
                        "afterPoolCount": prune_result.get("afterPoolCount"),
                    }
                )
        except Exception as exc:
            errors.append(f"prune_non_executable_pools: {exc}")
            steps.append({"step": "prune_non_executable_pools", "ok": False, "error": str(exc)})

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

    scan_state_result: dict = {}
    if not errors and not skip_build and not verify_only:
        try:
            liquidity_block = int(
                liquidity_activity.get("blockNumber")
                or index_payload.get("generatedBlockNumber")
                or 0
            )
            confirmed_count = len(liquidity_activity.get("liquidityPoolIds") or [])
            explicit_zero_count = int(
                liquidity_activity.get("liquidityZeroPoolCount")
                or liquidity_activity.get("liquidityNoLiquidityPoolCount")
                or 0
            )
            pool_count = len(index_payload.get("pools") or [])
            scan_state_result = update_v4_scan_state(
                str(database),
                network=network,
                chain_id=chain_id,
                last_scanned_block=int(index_payload.get("generatedBlockNumber") or liquidity_block),
                index_from_block=resolved_from_block if not skip_index else None,
                index_to_block=int(index_payload.get("generatedBlockNumber") or liquidity_block),
                liquidity_scan_block=liquidity_block,
                scanned_pool_count=int(liquidity_activity.get("scannedPoolCount") or 0),
                pool_count=pool_count,
                confirmed_liquidity_pool_count=confirmed_count,
                explicit_zero_liquidity_pool_count=explicit_zero_count,
                unscanned_pool_count=max(0, pool_count - confirmed_count - explicit_zero_count),
                direction_candidate_limit=int(builders.MAX_CANDIDATE_POOLS_PER_PAIR),
                scan_mode=(
                    "full"
                    if full_index_scan or (resolved_from_block <= 0 and not skip_index)
                    else "incremental"
                ),
            )
            steps.append(
                {
                    "step": "v4_scan_state",
                    "ok": True,
                    "lastScannedBlock": scan_state_result.get("last_scanned_block"),
                    "liquidityScanBlock": scan_state_result.get("liquidity_scan_block"),
                    "scanMode": scan_state_result.get("scan_mode"),
                    "directionCandidateLimit": scan_state_result.get("direction_candidate_limit"),
                }
            )
        except Exception as exc:
            errors.append(f"v4_scan_state: {exc}")
            steps.append({"step": "v4_scan_state", "ok": False, "error": str(exc)})

    json_evidence_removed: list[str] = []
    if not errors and not skip_build and not verify_only:
        try:
            if remove_json_evidence:
                json_evidence_removed = _remove_v4_json_evidence(
                    network=network,
                    chain_id=chain_id,
                )
                if json_evidence_removed:
                    steps.append(
                        {
                            "step": "remove_v4_json_evidence",
                            "ok": True,
                            "removedFiles": json_evidence_removed,
                        }
                    )
        except Exception as exc:
            errors.append(f"remove_v4_json_evidence: {exc}")
            steps.append({"step": "remove_v4_json_evidence", "ok": False, "error": str(exc)})

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
        "prune": prune_result,
        "jsonEvidenceRemoved": json_evidence_removed,
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
    parser.add_argument("--batch-liquidity", action="store_true")
    parser.add_argument("--anchors", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--no-clear-cache", dest="clear_cache", action="store_false")
    parser.add_argument(
        "--no-prune-zero-liquidity",
        dest="prune_zero_liquidity",
        action="store_false",
    )
    parser.add_argument(
        "--prune-unknown-liquidity",
        dest="prune_unknown_liquidity",
        action="store_true",
        help="Also delete unscanned has_liquidity=NULL rows; default keeps them for a later live scan.",
    )
    parser.add_argument(
        "--keep-json-evidence",
        dest="remove_json_evidence",
        action="store_false",
    )
    parser.add_argument(
        "--full-index-scan",
        dest="full_index_scan",
        action="store_true",
        help=(
            "Scan Initialize logs from block 0. Default reads the persisted "
            "last scanned block and only fetches newly created pools."
        ),
    )
    parser.add_argument(
        "--from-block",
        dest="from_block_override",
        type=int,
        default=None,
        help="Explicit Initialize scan start block; overrides persisted progress.",
    )
    parser.set_defaults(clear_cache=True)
    parser.set_defaults(prune_zero_liquidity=True)
    parser.set_defaults(prune_unknown_liquidity=False)
    parser.set_defaults(remove_json_evidence=True)
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
        batch_liquidity=args.batch_liquidity,
        anchors=args.anchors,
        verify_only=args.verify_only,
        clear_cache=args.clear_cache,
        prune_zero_liquidity=args.prune_zero_liquidity,
        prune_unknown_liquidity=args.prune_unknown_liquidity,
        remove_json_evidence=args.remove_json_evidence,
        full_index_scan=args.full_index_scan,
        from_block_override=args.from_block_override,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
