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
import os
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


def _with_database_path(database: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a default-database reader against an explicit SQLite file."""
    key = "FLASHLOAN_KV_PATH"
    previous = os.environ.get(key)
    os.environ[key] = str(database)
    try:
        return fn(*args, **kwargs)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _v4_incremental_database_state(
    *,
    database: str,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    """Return V4 pool/liquidity completeness for the incremental guard.

    Completeness is scoped to YC/ZC eligible pools (known token universe,
    fixed fee <= 3000, no hook).  Generic unknown pools outside that scope are
    counted for reporting but never decide the scan mode; explicit
    zero-liquidity verdicts are terminal and do not force a full rescan.
    """
    from core.db.storage_business_io import query

    # Import lazily: robinhood_v4_idle_maintenance imports this module at
    # module scope, so a top-level import here would form a cycle.
    from dex.runtime.robinhood_v4_idle_maintenance import (
        _candidate_eligible_liquidity_counts,
        _known_pool_token_addresses,
        _non_eligible_unknown_liquidity_count,
    )

    rows = query(
        database,
        """
        SELECT COUNT(*) AS pool_count,
               COALESCE(SUM(CASE WHEN l.pool_id IS NOT NULL THEN 1 ELSE 0 END), 0)
                 AS snapshot_count,
               COALESCE(SUM(CASE WHEN l.has_liquidity = 1 THEN 1 ELSE 0 END), 0)
                 AS confirmed_count,
               COALESCE(SUM(CASE WHEN l.has_liquidity = 0 THEN 1 ELSE 0 END), 0)
                 AS zero_count
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
        """,
        (network, chain_id, protocol),
    )
    row = rows[0] if rows else {}
    pool_count = int(row.get("pool_count") or 0)
    snapshot_count = int(row.get("snapshot_count") or 0)
    confirmed_count = int(row.get("confirmed_count") or 0)
    zero_count = int(row.get("zero_count") or 0)
    known_token_addresses = _known_pool_token_addresses(
        database,
        network=network,
        chain_id=chain_id,
    )
    eligible = _candidate_eligible_liquidity_counts(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    generic_unknown_count = _non_eligible_unknown_liquidity_count(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    eligible_pool_count = int(eligible["eligiblePoolCount"])
    eligible_unknown_count = int(eligible["eligibleUnknownCount"])
    return {
        "poolCount": pool_count,
        "snapshotCount": snapshot_count,
        "confirmedLiquidityPoolCount": confirmed_count,
        "zeroLiquidityPoolCount": zero_count,
        "missingLiquiditySnapshotCount": max(0, pool_count - snapshot_count),
        "unknownLiquidityCount": max(0, snapshot_count - confirmed_count - zero_count),
        "totalPoolCount": pool_count,
        "eligiblePoolCount": eligible_pool_count,
        "eligibleConfirmedCount": int(eligible["eligibleConfirmedCount"]),
        "eligibleZeroCount": int(eligible["eligibleZeroCount"]),
        "eligibleUnknownCount": eligible_unknown_count,
        "genericUnknownCount": generic_unknown_count,
        "complete": eligible_pool_count > 0 and eligible_unknown_count == 0,
    }


def _resolve_v4_scan_decision(
    *,
    database_state: dict[str, Any],
    last_scanned_block: int,
    full_index_scan: bool,
    from_block_override: int | None,
) -> dict[str, Any]:
    """Decide the Initialize scan start block before any RPC connection."""
    database_complete = bool(database_state.get("complete"))
    if from_block_override is not None:
        return {
            "fromBlock": max(0, int(from_block_override)),
            "scanMode": "full" if int(from_block_override) <= 0 else "explicit_override",
            "forcedFullScan": False,
            "reason": None,
        }
    if full_index_scan:
        return {
            "fromBlock": 0,
            "scanMode": "full",
            "forcedFullScan": True,
            "reason": "full_index_scan_requested",
        }
    if not database_complete:
        return {
            "fromBlock": 0,
            "scanMode": "full_forced_db_incomplete",
            "forcedFullScan": True,
            "reason": "db_pool_liquidity_incomplete",
        }
    return {
        "fromBlock": max(0, last_scanned_block + 1),
        "scanMode": "incremental",
        "forcedFullScan": False,
        "reason": None,
    }


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
        merged = _rust_merge_v4_pool_index(
            network,
            chain_id,
            onchain_payload,
            database_url=database,
            protocol=protocol,
        )
        if not isinstance(merged, dict):
            return merged
        # Rust merge currently returns only pool rows/count metadata. Keep the
        # on-chain top-level fields (poolManager etc.) so later liquidity steps
        # can resolve the contract address without re-reading chain config.
        return {
            **onchain_payload,
            **merged,
            "pools": merged.get("pools") or [],
        }
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
        _is_live_pool_state_completed,
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
            if chunk_states and any(
                _is_live_pool_state_completed(state)
                for state in chunk_states.values()
            ):
                break
        if chunk_states and any(
            _is_live_pool_state_completed(state)
            for state in chunk_states.values()
        ):
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


def _pool_payload_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("poolId")
        or value.get("pool_id")
        or value.get("pool")
        or ""
    ).strip().lower()


def _filter_index_payload_to_pool_ids(
    index_payload: dict[str, Any],
    pool_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Keep pool rows and pool keys aligned for incremental imports."""
    wanted_ids = {
        str(pool_id).strip().lower()
        for pool_id in pool_ids
        if str(pool_id).strip().lower().startswith("0x")
    }
    pools = [
        pool
        for pool in (index_payload.get("pools") or [])
        if _pool_payload_id(pool) in wanted_ids
    ]
    kept_ids = {_pool_payload_id(pool) for pool in pools}
    pool_keys = [
        key
        for key in (index_payload.get("poolKeys") or [])
        if _pool_payload_id(key) in kept_ids
    ]
    return {
        **index_payload,
        "pools": pools,
        "poolKeys": pool_keys,
        "poolCount": len(pools),
    }


def _directed_pool_count(
    row: dict[str, Any],
    direction_key: str,
    direction: str,
) -> int:
    """Count YC/ZC candidate pools for one directed candidate pair row."""
    if direction not in {"forward", "reverse"}:
        return 0
    yc_opt = 1 if str(row.get("yc_optimal") or "").strip().lower().startswith("0x") else 0
    zc_opt = 1 if str(row.get("zc_optimal") or "").strip().lower().startswith("0x") else 0
    try:
        yc_backup = json.loads(row.get("yc_backup") or "[]")
        zc_backup = json.loads(row.get("zc_backup") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        yc_backup, zc_backup = [], []
    yc_total = yc_opt + sum(
        1
        for item in (yc_backup if isinstance(yc_backup, list) else [])
        if str(item or "").strip().lower().startswith("0x")
    )
    zc_total = zc_opt + sum(
        1
        for item in (zc_backup if isinstance(zc_backup, list) else [])
        if str(item or "").strip().lower().startswith("0x")
    )
    left, _, right = direction_key.partition(":")
    small, large = sorted((left, right))
    forward_key = f"{small}:{large}"
    if direction_key == forward_key:
        return yc_total if direction == "forward" else zc_total
    return zc_total if direction == "forward" else yc_total


def _direction_backfill_plan(
    *,
    database: str,
    network: str,
    chain_id: int,
    direction_target: int = builders.MAX_CANDIDATE_POOLS_PER_PAIR,
    protocol: str = "uniswap_v4",
    reader: Any | None = None,
    anchors: Iterable[str] | None = None,
    anchor_addresses: dict[str, str] | None = None,
) -> dict:
    """Return canonical token directions that have fewer than ``direction_target`` pools.

    Forward is the lower-address token to the higher-address token and reverse
    is the mirror, regardless of the anchor order stored in
    ``dex_candidate_pool_pairs``.  The floor for each edge is the earliest
    Initialize block already stored in SQLite for that edge; the reverse scan
    never goes earlier than the pool creation block.

    ``anchors``/``anchor_addresses`` may provide the official anchor universe
    so completely missing directions still appear in the plan.  Without them,
    only directions already present in ``dex_candidate_pool_pairs`` are
    considered and a pair with zero candidate pools is invisible.
    """
    from core.db.storage_business_io import query, query_read_only

    reader = reader or query
    address_by_symbol = {str(k).upper(): str(v).strip().lower() for k, v in (anchor_addresses or {}).items()}
    anchor_symbols = tuple(str(item).strip().upper() for item in anchors or ())
    if anchor_symbols and not address_by_symbol:
        from dex.intent_trade.chains.robinhood_common.chain_config import (
            all_anchor_addresses_by_symbol,
        )

        resolved = all_anchor_addresses_by_symbol(
            network,
            database_url=database,
        )
        address_by_symbol = {str(k).upper(): str(v).strip().lower() for k, v in resolved.items()}

    rows = reader(
        database,
        """
        SELECT direction_key,
               COALESCE(yc_optimal_pool_id, '') AS yc_optimal,
               COALESCE(zc_optimal_pool_id, '') AS zc_optimal,
               COALESCE(yc_backup_pool_ids_json, '[]') AS yc_backup,
               COALESCE(zc_backup_pool_ids_json, '[]') AS zc_backup
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    totals_by_pair: dict[str, dict[str, int]] = {}
    for row in rows:
        direction_key = str(row.get("direction_key") or "").strip().lower()
        if ":" not in direction_key:
            continue
        left, right = direction_key.split(":", 1)
        if not left.startswith("0x") or not right.startswith("0x"):
            continue
        small, large = sorted((left, right))
        totals = totals_by_pair.setdefault(
            f"{small}:{large}",
            {"forward": 0, "reverse": 0},
        )
        totals["forward"] += _directed_pool_count(row, direction_key, "forward")
        totals["reverse"] += _directed_pool_count(row, direction_key, "reverse")
    for address in sorted(
        {
            address
            for address in address_by_symbol.values()
            if str(address or "").strip().lower().startswith("0x")
        }
    ):
        if not totals_by_pair:
            totals_by_pair.setdefault(f"{address}:{address}", {"forward": 0, "reverse": 0})
    direction_target = max(1, int(direction_target))
    targets: dict[str, int] = {}
    address_pairs: list[str] = []
    anchor_addresses_list = sorted(
        {
            address
            for address in address_by_symbol.values()
            if str(address or "").strip().lower().startswith("0x")
        }
    )
    pair_keys_to_check = [
        f"{left}:{right}"
        for left in anchor_addresses_list
        for right in anchor_addresses_list
        if left != right
    ]
    if not pair_keys_to_check:
        pair_keys_to_check = list(totals_by_pair.keys())
    for forward_key in pair_keys_to_check:
        totals = totals_by_pair.setdefault(
            forward_key,
            {"forward": 0, "reverse": 0},
        )
        left, _, right = forward_key.partition(":")
        reverse_key = f"{right}:{left}"
        if totals["forward"] < direction_target:
            targets[forward_key] = direction_target
            address_pairs.append(forward_key)
        if totals["reverse"] < direction_target:
            targets[reverse_key] = direction_target
            address_pairs.append(reverse_key)
    if not targets:
        return {
            "targets": {},
            "floor_block": 0,
            "neededDirectionCount": 0,
            "basis": "all_directions_satisfied",
        }
    address_pairs = list(dict.fromkeys(address_pairs))
    placeholders = ",".join("?" for _ in address_pairs)
    pool_rows = reader(
        database,
        f"""
        SELECT p.address_pair,
               MIN(CAST(json_extract(p.pool_key_json, '$.initializeBlockNumber') AS INTEGER))
                 AS floor_block
        FROM v4_exchange_pools p
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND p.pool_key_json LIKE '%"initializeBlockNumber"%'
          AND LOWER(p.address_pair) IN ({placeholders})
        GROUP BY p.address_pair
        """,
        (
            network,
            chain_id,
            protocol,
            *address_pairs,
        ),
    )
    floors: list[int] = []
    for row in pool_rows:
        pair = str(row.get("address_pair") or "").strip().lower()
        if pair not in address_pairs:
            continue
        try:
            floors.append(int(row.get("floor_block") or 0))
        except (TypeError, ValueError):
            continue
    return {
        "targets": targets,
        "floor_block": min(floors) if floors else 0,
        "neededDirectionCount": len(targets),
        "basis": "direction_candidate_shortfall",
    }


def _dry_run_candidate_backfill_report(
    *,
    database: str,
    network: str,
    chain_id: int,
    requested: dict[str, Any],
) -> dict:
    """Build a strict read-only candidate-backfill plan against the SQLite file.

    The report reads only through ``query_read_only`` and never opens a
    writable connection, so it is safe to run while the production process is
    live and before a maintenance window is confirmed.
    """
    from core.db.storage_business_io import query_read_only

    plan = _direction_backfill_plan(
        database=database,
        network=network,
        chain_id=chain_id,
        reader=query_read_only,
    )
    zero = "0x0000000000000000000000000000000000000000"
    counts = {
        "poolCount": query_read_only(
            database,
            """
            SELECT COUNT(*) AS count FROM v4_exchange_pools
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (network, chain_id, "uniswap_v4"),
        ),
        "confirmedLiquidityPoolCount": query_read_only(
            database,
            """
            SELECT COUNT(*) AS count
            FROM v4_exchange_pools p
            LEFT JOIN v4_exchange_pool_liquidity_snapshots l
              ON l.network = p.network AND l.chain_id = p.chain_id
             AND l.protocol = p.protocol AND l.pool_id = p.pool_id
            WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
              AND p.fee > 0 AND p.fee <= 3000 AND (p.fee & 8388608) = 0
              AND (COALESCE(p.hooks, '') = '' OR lower(p.hooks) = ?)
              AND l.has_liquidity = 1
            """,
            (network, chain_id, "uniswap_v4", zero),
        ),
        "candidatePoolCount": query_read_only(
            database,
            """
            SELECT COUNT(*) AS count FROM dex_candidate_pools
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (network, chain_id, "uniswap_v4"),
        ),
        "candidatePoolPairCount": query_read_only(
            database,
            """
            SELECT COUNT(*) AS count FROM dex_candidate_pool_pairs
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (network, chain_id, "uniswap_v4"),
        ),
    }
    counts = {key: int((rows[0] or {}).get("count") or 0) for key, rows in counts.items()}
    return {
        "ok": True,
        "dryRun": True,
        "readOnly": True,
        "database": str(database),
        "network": network,
        "chainId": chain_id,
        "steps": [
            {
                "step": "v4_candidate_backfill_dry_run",
                "ok": True,
                "connection": "sqlite_mode_ro",
                "writesSkipped": True,
                "neededDirectionCount": plan["neededDirectionCount"],
                "floorBlock": plan["floor_block"],
                "basis": plan["basis"],
            }
        ],
        "plan": plan,
        "scope": counts,
        "requested": requested,
        "errors": [],
    }


def _merge_directional_backfill_into_index(
    index_payload: dict,
    backfill_payload: dict,
) -> dict:
    pools = list(index_payload.get("pools") or [])
    merged_by_id: dict[str, dict] = {}
    for pool in pools:
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("pool") or "").strip().lower()
        if pool_id:
            merged_by_id.setdefault(pool_id, dict(pool))
    for pool in backfill_payload.get("pools") or []:
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("pool") or "").strip().lower()
        if not pool_id:
            continue
        merged_by_id[pool_id] = dict(pool)
    merged = list(merged_by_id.values())
    return {
        **index_payload,
        "pools": merged,
        "poolCount": len(merged),
        "generatedBlockNumber": (
            int(backfill_payload.get("generatedBlockNumber") or index_payload.get("generatedBlockNumber") or 0)
        ),
        "source": "onchain_initialize_logs_reverse_directional_backfill",
    }


def _confirm_backfill_pool_liquidity(
    *,
    network: str,
    rpc_url: str | None,
    pool_manager: str | None,
    batch_size: int = 100,
) -> object:
    """Return a callback that marks live candidate pools as liquid or not."""
    if network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (
            _batch_read_live_pool_states,
        )

        def _confirm(pools: list[dict]) -> set[str]:
            pool_ids = sorted(
                {
                    str(pool.get("poolId") or "").strip().lower()
                    for pool in pools
                    if str(pool.get("poolId") or "").strip().startswith("0x")
                }
            )
            if not pool_ids:
                return set()
            confirmed: set[str] = set()
            for start in range(0, len(pool_ids), max(1, int(batch_size))):
                chunk = pool_ids[start : start + max(1, int(batch_size))]
                states = _batch_read_live_pool_states(
                    rpc_url=rpc_url or "",
                    pool_manager=pool_manager or "",
                    pool_ids=chunk,
                )
                for pool_id, state in (states or {}).items():
                    if not isinstance(state, dict) or state.get("error"):
                        continue
                    try:
                        liquidity = int(str(state.get("liquidity") or "0"))
                    except (TypeError, ValueError):
                        liquidity = 0
                    if liquidity > 0:
                        confirmed.add(str(pool_id).strip().lower())
            return confirmed

        return _confirm
    from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
        _batch_read_v4_pool_states,
    )

    def _confirm(pools: list[dict]) -> set[str]:
        pool_ids = sorted(
            {
                str(pool.get("poolId") or "").strip().lower()
                for pool in pools
                if str(pool.get("poolId") or "").strip().startswith("0x")
            }
        )
        if not pool_ids:
            return set()
        confirmed: set[str] = set()
        for start in range(0, len(pool_ids), max(1, int(batch_size))):
            chunk = pool_ids[start : start + max(1, int(batch_size))]
            states = _batch_read_v4_pool_states(
                rpc_url=rpc_url or "",
                pool_manager=pool_manager or "",
                pool_ids=chunk,
            )
            for pool_id, state in (states or {}).items():
                if not isinstance(state, dict) or state.get("error"):
                    continue
                try:
                    liquidity = int(str(state.get("liquidity") or "0"))
                except (TypeError, ValueError):
                    liquidity = 0
                if liquidity > 0:
                    confirmed.add(str(pool_id).strip().lower())
        return confirmed

    return _confirm


def refresh_v4_live(
    *,
    database: str = "",
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int | None = None,
    rpc_url: str | None = "",
    pool_manager: str = "",
    candidate_backfill: bool = False,
    skip_index: bool = False,
    skip_liquidity: bool = False,
    skip_build: bool = False,
    mark_recent_swaps: bool = False,
    pool_ids: str | list[str] | None = "",
    import_pool_ids: str | list[str] | None = None,
    batch_size: int = 100,
    batch_liquidity: bool = False,
    anchors: str = "",
    verify_only: bool = False,
    dry_run: bool = False,
    clear_cache: bool = True,
    prune_zero_liquidity: bool = True,
    prune_unknown_liquidity: bool = False,
    remove_json_evidence: bool = True,
    replace_evidence: bool = True,
    full_index_scan: bool = False,
    from_block_override: int | None = None,
    token_balance_scan_scope: str = "none",
) -> dict:
    """Rebuild Robinhood V4 SQLite tables from live/in-memory evidence.

    This is the shared entry point for CLI, page refresh and integration
    callers. JSON evidence files are never read or written here.  ``dry_run``
    returns a strict read-only candidate plan without opening a writable
    SQLite connection or touching RPC.
    """
    network, chain_id, market_id = _network_scope(network, chain_id)
    database = absolute_database_path(database)
    if dry_run:
        return _dry_run_candidate_backfill_report(
            database=str(database),
            network=network,
            chain_id=chain_id,
            requested={
                "candidateBackfill": bool(candidate_backfill),
                "skipIndex": bool(skip_index),
                "skipLiquidity": bool(skip_liquidity),
                "poolIds": _pool_ids_arg(pool_ids),
                "importPoolIds": _pool_ids_arg(import_pool_ids),
                "batchSize": max(1, int(batch_size)),
            },
        )
    ensure_database_schema(str(database))
    explicit_anchors = anchors

    steps: list[dict] = []
    errors: list[str] = []
    replace_evidence_guard: dict | None = None
    if skip_index and replace_evidence:
        # ``--skip-index`` reuses SQLite rows as the pool-key source.  A live
        # incremental refresh only scanned the requested batch, so clearing
        # all snapshots and importing placeholder rows for the untouched
        # universe would erase positive liquidity evidence from the chain.
        replace_evidence_guard = {
            "step": "replace_evidence_guard",
            "ok": True,
            "reason": "skip_index_incremental_preserves_liquidity",
            "replacedWith": False,
        }
        replace_evidence = False

    try:
        scan_state = load_v4_scan_state(
            str(database),
            network=network,
            chain_id=chain_id,
        )
        database_state = _v4_incremental_database_state(
            database=str(database),
            network=network,
            chain_id=chain_id,
        )
        scan_decision = _resolve_v4_scan_decision(
            database_state=database_state,
            last_scanned_block=int(scan_state.get("last_scanned_block") or 0),
            full_index_scan=full_index_scan,
            from_block_override=from_block_override,
        )
        resolved_from_block = int(scan_decision["fromBlock"])
        steps.append(
            {
                "step": "v4_incremental_guard",
                "ok": True,
                "scanMode": scan_decision["scanMode"],
                "forcedFullScan": scan_decision["forcedFullScan"],
                "reason": scan_decision["reason"],
                "databaseState": database_state,
            }
        )
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
                "scanMode": scan_decision["scanMode"],
            }
        )
        if replace_evidence_guard is not None:
            steps.append(replace_evidence_guard)

        backfill_activity: dict = {}
        if (
            candidate_backfill
            and not skip_index
            and not skip_liquidity
            and bool(database_state.get("complete"))
            and not index_result.get("skipped")
        ):
            try:
                plan = _direction_backfill_plan(
                    database=str(database),
                    network=network,
                    chain_id=chain_id,
                    direction_target=int(builders.MAX_CANDIDATE_POOLS_PER_PAIR),
                )
                if plan.get("neededDirectionCount"):
                    if network == ROBINHOOD_TESTNET_NETWORK:
                        from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import (
                            build_v4_directional_pool_backfill,
                        )
                    else:
                        from dex.intent_trade.chains.robinhood.v4_pool_index import (
                            build_v4_directional_pool_backfill,
                        )
                    backfill_payload = build_v4_directional_pool_backfill(
                        rpc_url=rpc_url or None,
                        pool_manager=pool_manager or None,
                        direction_targets=plan["targets"],
                        direction_floor_block=int(plan.get("floor_block") or 0),
                        confirm_pool_liquidity=_confirm_backfill_pool_liquidity(
                            network=network,
                            rpc_url=rpc_url or None,
                            pool_manager=pool_manager or None,
                        ),
                    )
                    index_payload = _merge_directional_backfill_into_index(
                        index_payload,
                        backfill_payload,
                    )
                    backfill_activity = {
                        "ok": True,
                        "neededDirectionCount": int(plan.get("neededDirectionCount") or 0),
                        "satisfiedDirectionCount": int(
                            backfill_payload.get("satisfiedDirectionCount") or 0
                        ),
                        "stoppedReason": backfill_payload.get("stoppedReason"),
                        "floorBlock": int(plan.get("floor_block") or 0),
                        "selectedPoolCount": int(backfill_payload.get("selectedPoolCount") or 0),
                        "directionCounts": backfill_payload.get("directionCounts") or {},
                    }
                else:
                    backfill_activity = {
                        "ok": True,
                        "neededDirectionCount": 0,
                        "basis": plan.get("basis"),
                    }
                steps.append(
                    {
                        "step": "v4_directional_candidate_backfill",
                        "ok": True,
                        **backfill_activity,
                    }
                )
            except Exception as exc:
                errors.append(f"v4_directional_candidate_backfill: {exc}")
                steps.append(
                    {
                        "step": "v4_directional_candidate_backfill",
                        "ok": False,
                        "error": str(exc),
                    }
                )
    except Exception as exc:
        errors.append(f"v4_pool_index: {exc}")
        steps.append({"step": "v4_pool_index", "ok": False, "error": str(exc)})

    index_checkpoint_result: dict = {}
    if (
        not errors
        and not skip_index
        and not skip_liquidity
        and not verify_only
        and bool(index_payload.get("pools"))
    ):
        try:
            checkpoint_payload = index_payload
            checkpoint_pool_ids = _pool_ids_arg(import_pool_ids)
            if checkpoint_pool_ids:
                checkpoint_payload = _filter_index_payload_to_pool_ids(
                    checkpoint_payload,
                    checkpoint_pool_ids,
                )
            index_checkpoint_result = import_v4_evidence_rows(
                database=str(database),
                network=network,
                chain_id=chain_id,
                index_payload=checkpoint_payload,
                liquidity_payload=None,
                replace=replace_evidence,
            )
            steps.append(
                {
                    "step": "v4_pool_index_checkpoint",
                    "ok": True,
                    "v4PoolCount": index_checkpoint_result.get("v4PoolCount"),
                    "tokenCount": index_checkpoint_result.get("tokenCount"),
                    "purpose": "recover_from_later_liquidity_or_build_failure",
                }
            )
        except Exception as exc:
            errors.append(f"v4_pool_index_checkpoint: {exc}")
            steps.append(
                {
                    "step": "v4_pool_index_checkpoint",
                    "ok": False,
                    "error": str(exc),
                }
            )

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

    accounting_activity: dict = {}
    if (
        not errors
        and not verify_only
        and network == ROBINHOOD_TESTNET_NETWORK
        and token_balance_scan_scope != "none"
    ):
        try:
            from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
                build_v4_pool_manager_accounting_payload,
            )

            accounting_payload = build_v4_pool_manager_accounting_payload(
                database=str(database),
                network=network,
                chain_id=chain_id,
                pool_manager=pool_manager or str(index_payload.get("poolManager") or ""),
                rpc_url=rpc_url or None,
                index_payload=index_payload,
                scan_scope=token_balance_scan_scope,
                multicall_chunk_size=batch_size,
            )
            from core.db.storage_v4_pool_manager_accounting import (
                save_v4_pool_manager_accounting,
            )

            save_v4_pool_manager_accounting(
                str(database),
                network=network,
                chain_id=chain_id,
                pool_manager=accounting_payload["poolManager"],
                block_number=accounting_payload["blockNumber"],
                generated_at=accounting_payload["generatedAt"],
                scan_scope=accounting_payload["scanScope"],
                source_label="refresh_v4_live_poolmanager_balance_of",
                native_balance_wei=accounting_payload["nativePoolManagerBalanceWei"],
                sources=accounting_payload["flashAccountingSources"],
                metadata=accounting_payload["metadata"],
                errors=accounting_payload["errors"],
            )
            accounting_activity = {
                "ok": True,
                "sourceCount": accounting_payload["flashAccountingSourceCount"],
                "blockNumber": accounting_payload["blockNumber"],
                "scanScope": accounting_payload["scanScope"],
                "errorCount": len(accounting_payload["errors"]),
            }
            steps.append(
                {
                    "step": "v4_pool_manager_accounting",
                    "ok": True,
                    **accounting_activity,
                }
            )
        except Exception as exc:
            errors.append(f"v4_pool_manager_accounting: {exc}")
            steps.append(
                {
                    "step": "v4_pool_manager_accounting",
                    "ok": False,
                    "error": str(exc),
                }
            )

    import_result: dict = {}
    if not errors:
        try:
            if verify_only:
                import_result = {
                    "ok": True,
                    "skipped": True,
                    "source": "verify_only_no_db_writes",
                    "detail": "read-only verification requested; import skipped",
                }
            elif skip_index and skip_liquidity:
                import_result = {
                    "ok": True,
                    "skipped": True,
                    "source": "sqlite_only",
                    "detail": "raw V4 tables already present in SQLite; business tables rebuilt only",
                }
            else:
                imported_pool_ids = _pool_ids_arg(import_pool_ids)
                if imported_pool_ids:
                    index_payload = _filter_index_payload_to_pool_ids(
                        index_payload,
                        imported_pool_ids,
                    )
                liquidity_payload = None
                if not skip_index:
                    liquidity_payload = _make_liquidity_payload(index_payload)
                import_result = import_v4_evidence_rows(
                    database=str(database),
                    network=network,
                    chain_id=chain_id,
                    index_payload=index_payload,
                    liquidity_payload=liquidity_payload,
                    replace=replace_evidence,
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
            from core.db.storage_dex_surface_snapshot import load_dex_overview_snapshot

            persisted = _with_database_path(
                str(database),
                load_dex_overview_snapshot,
                network=network,
                chain_id=int(chain_id),
                protocol="uniswap_v4",
            )
            if persisted is None:
                from market.binance_market.dex_surfaces import database_overview_snapshot

                snapshot_result = _with_database_path(
                    str(database),
                    database_overview_snapshot,
                    chain_id=chain_id,
                    protocols=["uniswap_v4"],
                )
            else:
                snapshot_result = persisted
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
                scan_mode=scan_decision["scanMode"],
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
    if clear_cache and not verify_only:
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
        "poolManagerAccounting": accounting_activity,
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
    parser.add_argument(
        "--candidate-backfill",
        action="store_true",
        help=(
            "Scan newest Initialize logs backward for directions with fewer "
            "than 5 active candidate pools, stopping at the pool creation "
            "block; the closed-loop route stays usable with 1 active pool "
            "and 5 is the current dynamic backfill target."
        ),
    )
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--skip-liquidity", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--mark-recent-swaps", action="store_true")
    parser.add_argument("--pool-ids", default="", help="Comma-separated V4 pool ids to scan for live liquidity; empty means all.")
    parser.add_argument(
        "--import-pool-ids",
        default="",
        help="Comma-separated V4 pool ids whose evidence rows should be imported; empty means all scanned pool ids.",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch-liquidity", action="store_true")
    parser.add_argument("--anchors", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Strict read-only candidate backfill plan. Opens SQLite with "
            "mode=ro only, skips RPC, import, schema migration and cache "
            "writes; safe to run before a maintenance window."
        ),
    )
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
    parser.add_argument(
        "--token-balance-scan-scope",
        default="none",
        choices=("none", "active_plus_known", "full_index", "full_index_recent_swaps"),
        help="Robinhood Testnet PoolManager balance scan scope persisted into SQLite.",
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
        candidate_backfill=args.candidate_backfill,
        skip_index=args.skip_index,
        skip_liquidity=args.skip_liquidity,
        skip_build=args.skip_build,
        mark_recent_swaps=args.mark_recent_swaps,
        pool_ids=args.pool_ids,
        import_pool_ids=args.import_pool_ids,
        batch_size=args.batch_size,
        batch_liquidity=args.batch_liquidity,
        anchors=args.anchors,
        verify_only=args.verify_only,
        dry_run=args.dry_run,
        clear_cache=args.clear_cache,
        prune_zero_liquidity=args.prune_zero_liquidity,
        prune_unknown_liquidity=args.prune_unknown_liquidity,
        remove_json_evidence=args.remove_json_evidence,
        full_index_scan=args.full_index_scan,
        from_block_override=args.from_block_override,
        token_balance_scan_scope=args.token_balance_scan_scope,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
