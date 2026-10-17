"""Read-only Robinhood Testnet directional candidate backfill evidence.

The tool computes the direction shortfall from the SQLite candidate pair
tables and can either reuse already-verified SQLite pools and liquidity
snapshots (``--sqlite-only``) or scan PoolManager Initialize logs
newest-to-oldest with live liquidity confirmation.  It never writes SQLite
business tables and never broadcasts anything.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.direction_pool_score import MAX_DIRECTION_CANDIDATE_POOLS  # noqa: E402
from dex.intent_trade.chains.robinhood_common.chain_config import (  # noqa: E402
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalized_direction(value: object) -> str:
    text = str(value or "").strip().lower()
    if ":" not in text:
        return ""
    left, _, right = text.partition(":")
    if not (left.startswith("0x") and right.startswith("0x")):
        return ""
    return f"{left}:{right}"


def _direction_plan(
    *,
    database: str,
    network: str,
    chain_id: int,
    directions: tuple[str, ...] = (),
    limit_directions: int = 0,
    direction_limit: int = MAX_DIRECTION_CANDIDATE_POOLS,
) -> dict[str, Any]:
    from dex.tools.refresh_robinhood_v4_live import _direction_backfill_plan

    plan = _direction_backfill_plan(
        database=database,
        network=network,
        chain_id=chain_id,
        direction_limit=direction_limit,
    )
    targets = dict(plan.get("targets") or {})
    if directions:
        wanted = {
            _normalized_direction(item)
            for item in directions
            if _normalized_direction(item)
        }
        targets = {
            key: value
            for key, value in targets.items()
            if key in wanted
        }
    if limit_directions > 0:
        targets = dict(sorted(targets.items())[: int(limit_directions)])
    if not targets:
        return {
            **plan,
            "targets": {},
            "neededDirectionCount": 0,
            "basis": (
                "explicit_direction_subset_empty"
                if directions
                else plan.get("basis") or "all_directions_satisfied"
            ),
        }

    address_pairs = list(dict.fromkeys(targets.keys()))
    placeholders = ",".join("?" for _ in address_pairs)
    from core.db.storage_business_io import query

    pool_rows = query(
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
            "uniswap_v4",
            *address_pairs,
        ),
    )
    floors: list[int] = []
    for row in pool_rows:
        pair = str(row.get("address_pair") or "").strip().lower()
        if pair not in set(address_pairs):
            continue
        try:
            floors.append(int(row.get("floor_block") or 0))
        except (TypeError, ValueError):
            continue
    return {
        **plan,
        "targets": targets,
        "neededDirectionCount": len(targets),
        "floor_block": min(floors) if floors else int(plan.get("floor_block") or 0),
        "basis": "direction_candidate_shortfall",
    }


def _confirm_pool_liquidity(
    *,
    rpc_url: str,
    pool_manager: str,
    batch_size: int,
) -> object:
    from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (
        _batch_read_live_pool_states,
    )

    def _confirm(pools: list[dict[str, Any]]) -> set[str]:
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
                rpc_url=rpc_url,
                pool_manager=pool_manager,
                pool_ids=chunk,
                network=ROBINHOOD_TESTNET_NETWORK,
                chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
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


def _confirm_indexed_direction_candidates(
    *,
    database: str,
    network: str,
    chain_id: int,
    rpc_url: str,
    pool_manager: str,
    direction_targets: dict[str, int],
    batch_size: int,
) -> dict[str, Any]:
    """Confirm liquidity for already-indexed pools and count per direction.

    This lets an incremental run prefer the existing pool index instead of
    rescannning old blocks when the shortfall is only liquidity evidence.
    """
    from core.db.storage_business_io import query
    from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
        _directional_keys,
    )

    if not direction_targets:
        return {"counts": {}, "remaining": {}, "pools": []}
    address_pairs = list(direction_targets)
    placeholders = ",".join("?" for _ in address_pairs)
    rows = query(
        database,
        f"""
        SELECT pool_id, token0, token1, fee, tick_spacing, hooks,
               CAST(json_extract(pool_key_json, '$.initializeBlockNumber') AS INTEGER)
                 AS initialize_block_number
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND LOWER(address_pair) IN ({placeholders})
        ORDER BY initialize_block_number DESC
        """,
        (
            network,
            chain_id,
            "uniswap_v4",
            *address_pairs,
        ),
    )
    pools = [
        {
            "poolId": str(row.get("pool_id") or "").strip(),
            "token0": str(row.get("token0") or "").strip(),
            "token1": str(row.get("token1") or "").strip(),
            "fee": int(row.get("fee") or 0),
            "tickSpacing": int(row.get("tick_spacing") or 0),
            "hooks": str(row.get("hooks") or "").strip(),
            "initializeBlockNumber": int(row.get("initialize_block_number") or 0),
        }
        for row in rows
        if str(row.get("pool_id") or "").strip().startswith("0x")
    ]
    if not pools:
        return {"counts": {}, "remaining": dict(direction_targets), "pools": []}
    confirm = _confirm_pool_liquidity(
        rpc_url=rpc_url,
        pool_manager=pool_manager,
        batch_size=batch_size,
    )
    confirmed_ids = confirm(pools)
    remaining = dict(direction_targets)
    counts: dict[str, int] = {}
    selected: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        pool_id = str(pool.get("poolId") or "").strip().lower()
        if not pool_id or pool_id not in confirmed_ids:
            continue
        direction, reverse = _directional_keys(
            pool.get("token0"),
            pool.get("token1"),
        )
        for key in (direction, reverse):
            if key not in remaining:
                continue
            counts[key] = counts.get(key, 0) + 1
            selected.setdefault(key, []).append(pool)
            if counts[key] >= remaining[key]:
                counts[key] = remaining[key]
                del remaining[key]
    return {
        "counts": counts,
        "remaining": remaining,
        "pools": pools,
        "selectedPools": selected,
        "confirmedPoolCount": len(confirmed_ids),
        "indexedPoolCount": len(pools),
        "allPoolIds": [
            str(pool.get("poolId") or "").strip().lower()
            for pool in pools
            if str(pool.get("poolId") or "").strip().lower().startswith("0x")
        ],
    }


def _merge_direction_selections(
    *,
    targets: dict[str, int],
    indexed_selected: dict[str, list[dict[str, Any]]],
    scanned_pools: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
        _directional_keys,
    )

    selected: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    for key in targets:
        selected[key] = []
        seen[key] = set()
    candidates = [
        pool
        for pools in indexed_selected.values()
        for pool in pools
    ]
    candidates.extend(scanned_pools)
    for pool in sorted(
        candidates,
        key=lambda item: int(item.get("initializeBlockNumber") or 0),
        reverse=True,
    ):
        pool_id = str(pool.get("poolId") or "").strip().lower()
        direction, reverse = _directional_keys(
            pool.get("token0"),
            pool.get("token1"),
        )
        for key in (direction, reverse):
            if key not in targets or pool_id in seen[key]:
                continue
            if len(selected[key]) >= targets[key]:
                continue
            seen[key].add(pool_id)
            selected[key].append(pool)
    counts = {key: len(selected.get(key, [])) for key in targets}
    return selected, counts


def collect_report(
    *,
    database: str = "",
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int | None = None,
    rpc_url: str = "",
    pool_manager: str = "",
    directions: tuple[str, ...] = (),
    limit_directions: int = 0,
    chunk_size: int = 500_000,
    min_chunk_size: int = 10_000,
    batch_size: int = 300,
) -> dict[str, Any]:
    if str(network or "").strip().lower() != ROBINHOOD_TESTNET_NETWORK:
        raise ValueError("this evidence tool is testnet-only; mainnet stays fail-closed")
    resolved_chain_id = int(chain_id or ROBINHOOD_TESTNET_CHAIN_ID)
    if resolved_chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"chainId {resolved_chain_id} is not Robinhood testnet 46630")
    resolved_database = str(absolute_database_path(database))

    from dex.intent_trade.chains.robinhood.config import (
        testnet_rpc_urls_from_env,
    )
    from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
        build_v4_directional_pool_backfill as _backfill_core,
    )
    from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
        ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        _rpc_url_from_env_or_default,
    )

    resolved_pool_manager = pool_manager or ROBINHOOD_TESTNET_V4_POOL_MANAGER
    resolved_rpc_url = rpc_url or _rpc_url_from_env_or_default()
    if not resolved_rpc_url:
        raise RuntimeError("no Robinhood testnet RPC URL configured")
    providers = testnet_rpc_urls_from_env()
    fallback_urls = tuple(
        url
        for url in providers
        if url and url != resolved_rpc_url
    )

    plan = _direction_plan(
        database=resolved_database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=resolved_chain_id,
        directions=directions,
        limit_directions=limit_directions,
        direction_limit=MAX_DIRECTION_CANDIDATE_POOLS,
    )
    targets = plan.get("targets") or {}
    if not targets:
        return {
            "ok": True,
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chainId": resolved_chain_id,
            "database": resolved_database,
            "plan": plan,
            "scanned": False,
            "reason": "no_direction_shortfall",
        }

    target_tokens = list(dict.fromkeys(
        part
        for key in targets
        for part in str(key or "").split(":", 1)
        if str(part or "").strip().lower().startswith("0x")
    ))

    indexed = _confirm_indexed_direction_candidates(
        database=resolved_database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=resolved_chain_id,
        rpc_url=resolved_rpc_url,
        pool_manager=resolved_pool_manager,
        direction_targets=targets,
        batch_size=batch_size,
    )
    remaining = dict(indexed.get("remaining") or {})
    payload = None
    if remaining:
        confirm = _confirm_pool_liquidity(
            rpc_url=resolved_rpc_url,
            pool_manager=resolved_pool_manager,
            batch_size=batch_size,
        )
        payload = _backfill_core(
            network=ROBINHOOD_TESTNET_NETWORK,
            chain_id=resolved_chain_id,
            pool_manager=resolved_pool_manager,
            rpc_url=resolved_rpc_url,
            schema_version="robinhood-testnet-v4-pool-index-v1",
            direction_targets=remaining,
            direction_floor_block=int(plan.get("floor_block") or 0),
            include_currency_metadata=False,
            confirm_pool_liquidity=confirm,
            rpc_urls=fallback_urls,
            prefer_hyperrpc=False,
            topic_filter_addresses=target_tokens,
            exclude_pool_ids=indexed.get("allPoolIds") or [],
            chunk_size=max(1, int(chunk_size)),
            min_chunk_size=max(1, int(min_chunk_size)),
            rpc_user_agent="flashloan-robinhood-testnet-v4/1.0",
        )
    selected_by_direction, direction_counts = _merge_direction_selections(
        targets=targets,
        indexed_selected=indexed.get("selectedPools") or {},
        scanned_pools=payload.get("pools") if payload is not None else [],
    )
    satisfied_count = sum(
        1
        for key, target in targets.items()
        if direction_counts.get(key, 0) >= target
    )
    all_selected_pools: list[dict[str, Any]] = []
    seen_selected_ids: set[str] = set()
    for key in sorted(targets):
        for pool in selected_by_direction.get(key, []):
            pool_id = str(pool.get("poolId") or "").strip().lower()
            if pool_id in seen_selected_ids:
                continue
            seen_selected_ids.add(pool_id)
            all_selected_pools.append(pool)
    all_selected_pools.sort(
        key=lambda item: int(item.get("initializeBlockNumber") or 0),
        reverse=True,
    )
    payload_scan = payload or {}
    scan = {
        key: payload_scan.get(key)
        for key in (
            "fromBlock",
            "toBlock",
            "scanChunkSize",
            "providerCount",
            "providerUrls",
            "scannedSegmentCount",
            "initializeLogCount",
            "discoveryMethod",
        )
        if key in payload_scan
    }
    scan.update({
        "scanDirection": (
            "newest_to_oldest"
            if payload is not None
            else "existing_pool_index_liquidity_confirmation"
        ),
        "directionTargets": targets,
        "directionCounts": direction_counts,
        "neededDirectionCount": len(targets),
        "satisfiedDirectionCount": satisfied_count,
        "stoppedReason": (
            "existing_pool_index_plus_incremental_scan_satisfied"
            if payload is not None and satisfied_count == len(targets)
            else (
                "existing_pool_index_liquidity_satisfied"
                if payload is None
                else payload_scan.get("stoppedReason")
            )
        ),
        "selectedPoolCount": len(all_selected_pools),
        "existingIndexPoolCount": int(indexed.get("indexedPoolCount") or 0),
        "existingIndexLiveConfirmedCount": int(
            indexed.get("confirmedPoolCount") or 0
        ),
    })
    report = {
        "schemaVersion": "robinhood-testnet-v4-directional-backfill-evidence-v1",
        "generatedAt": _utc_now_iso(),
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": resolved_chain_id,
        "database": resolved_database,
        "scope": {
            "directionsRequested": list(directions) if directions else "all",
            "limitDirections": int(limit_directions or 0),
            "candidateLimit": MAX_DIRECTION_CANDIDATE_POOLS,
            "forwardDirection": "lower_address_to_higher_address",
            "reverseDirection": "higher_address_to_lower_address",
            "searchOrder": "newest_to_oldest",
            "stopCondition": "five_live_candidates_per_direction_or_pool_creation_floor",
        },
        "plan": plan,
        "scan": scan,
        "indexedLiquidity": {
            "poolCount": int(indexed.get("indexedPoolCount") or 0),
            "liveConfirmedPoolCount": int(indexed.get("confirmedPoolCount") or 0),
        },
        "selectedPools": [
            {
                "poolId": pool.get("poolId"),
                "initializeBlockNumber": pool.get("initializeBlockNumber"),
                "token0": pool.get("token0"),
                "token1": pool.get("token1"),
                "fee": pool.get("fee"),
                "tickSpacing": pool.get("tickSpacing"),
                "hooks": pool.get("hooks"),
            }
            for pool in all_selected_pools
        ],
        "safety": {
            "writesSqlite": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "observationOnly": True,
        },
    }
    return report


def collect_report_from_sqlite(
    *,
    database: str = "",
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int | None = None,
    directions: tuple[str, ...] = (),
    limit_directions: int = 0,
    direction_limit: int = MAX_DIRECTION_CANDIDATE_POOLS,
) -> dict[str, Any]:
    """Build directional backfill evidence from existing SQLite rows only.

    This path never opens an RPC connection.  It reads the pool index and the
    verified liquidity snapshots already stored by previous live refreshes,
    counts live candidates per direction, and reports the exact shortfall if
    the existing index does not have enough pools.  This makes verified
    on-chain conclusions reusable without re-running the same scan.
    """
    if str(network or "").strip().lower() != ROBINHOOD_TESTNET_NETWORK:
        raise ValueError("this evidence tool is testnet-only; mainnet stays fail-closed")
    resolved_chain_id = int(chain_id or ROBINHOOD_TESTNET_CHAIN_ID)
    if resolved_chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"chainId {resolved_chain_id} is not Robinhood testnet 46630")
    resolved_database = str(absolute_database_path(database))

    plan = _direction_plan(
        database=resolved_database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=resolved_chain_id,
        directions=directions,
        limit_directions=limit_directions,
        direction_limit=direction_limit,
    )
    targets = plan.get("targets") or {}
    if not targets:
        return {
            "ok": True,
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chainId": resolved_chain_id,
            "database": resolved_database,
            "plan": plan,
            "scanned": False,
            "reason": "no_direction_shortfall",
            "source": "sqlite_only",
        }

    from core.db.storage_business_io import query
    from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
        _directional_keys,
    )

    target_pairs = sorted(
        {
            tuple(
                str(part).strip().lower()
                for part in str(key or "").split(":", 1)
                if str(part or "").strip().lower().startswith("0x")
            )
            for key in targets
            if len(str(key or "").split(":", 1)) == 2
        }
    )
    pair_placeholders = ",".join("(?, ?)" for _ in target_pairs)
    pair_params = [part for pair in target_pairs for part in pair]
    rows = query(
        resolved_database,
        f"""
        SELECT p.pool_id, p.token0, p.token1, p.fee, p.tick_spacing, p.hooks,
               CAST(json_extract(p.pool_key_json, '$.initializeBlockNumber') AS INTEGER)
                 AS initialize_block_number,
               CASE WHEN s.pool_id IS NULL THEN 0 ELSE 1 END AS has_snapshot,
               s.has_liquidity, s.liquidity, s.estimated_tvl_usd, s.depth_status,
               s.tick, s.sqrt_price_x96, s.liquidity_block_number,
               s.evidence_source, s.refreshed_at
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = p.network AND s.chain_id = p.chain_id
         AND s.protocol = p.protocol AND s.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND (
            (lower(p.token0), lower(p.token1)) IN ({pair_placeholders})
            OR (lower(p.token1), lower(p.token0)) IN ({pair_placeholders})
          )
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            resolved_chain_id,
            "uniswap_v4",
            *pair_params,
            *pair_params,
        ),
    )

    snapshot_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        pool_id = str(row.get("pool_id") or "").strip().lower()
        if not pool_id.startswith("0x"):
            continue
        pool_record = {
            "poolId": pool_id,
            "initializeBlockNumber": int(row.get("initialize_block_number") or 0),
            "token0": str(row.get("token0") or "").strip().lower(),
            "token1": str(row.get("token1") or "").strip().lower(),
            "fee": int(row.get("fee") or 0),
            "tickSpacing": int(row.get("tick_spacing") or 0),
            "hooks": str(row.get("hooks") or "").strip().lower(),
            "hasSnapshot": int(row.get("has_snapshot") or 0),
            "hasLiquidity": row.get("has_liquidity"),
            "liquidity": row.get("liquidity"),
            "estimatedTvlUsd": row.get("estimated_tvl_usd"),
            "depthStatus": row.get("depth_status"),
            "tick": row.get("tick"),
            "liquidityBlockNumber": row.get("liquidity_block_number"),
            "evidenceSource": row.get("evidence_source"),
            "refreshedAt": row.get("refreshed_at"),
        }
        snapshot_rows.append(pool_record)
        if int(row.get("has_liquidity") or 0) != 1:
            continue
        candidates.append(pool_record)
    candidates.sort(
        key=lambda item: int(item.get("initializeBlockNumber") or 0),
        reverse=True,
    )

    verified_snapshots = [
        row
        for row in snapshot_rows
        if row.get("evidenceSource") or row.get("refreshedAt")
    ]
    refreshed_times = sorted(
        {
            str(row.get("refreshedAt") or "")
            for row in verified_snapshots
            if str(row.get("refreshedAt") or "").strip()
        }
    )
    has_liquidity_rows = [
        row
        for row in snapshot_rows
        if int(row.get("hasSnapshot") or 0) == 1
    ]
    zero_liquidity_rows = [
        row
        for row in has_liquidity_rows
        if row.get("hasLiquidity") is not None
        and int(row.get("hasLiquidity") or 0) == 0
    ]
    missing_snapshot_rows = [
        row
        for row in snapshot_rows
        if int(row.get("hasSnapshot") or 0) == 0
    ]

    selected: dict[str, list[dict[str, Any]]] = {key: [] for key in targets}
    counts: dict[str, int] = {key: 0 for key in targets}
    seen: dict[str, set[str]] = {key: set() for key in targets}
    for candidate in candidates:
        direction, reverse = _directional_keys(
            candidate["token0"],
            candidate["token1"],
        )
        for key in (direction, reverse):
            if (
                key not in targets
                or candidate["poolId"] in seen[key]
                or len(selected[key]) >= targets[key]
            ):
                continue
            seen[key].add(candidate["poolId"])
            selected[key].append(candidate)
            counts[key] = len(selected[key])

    all_selected_pools: list[dict[str, Any]] = []
    seen_selected_ids: set[str] = set()
    for key in sorted(targets):
        for pool in selected.get(key, []):
            pool_id = str(pool.get("poolId") or "").strip().lower()
            if pool_id in seen_selected_ids:
                continue
            seen_selected_ids.add(pool_id)
            all_selected_pools.append(pool)
    satisfied_count = sum(
        1
        for key, target in targets.items()
        if counts.get(key, 0) >= target
    )
    remaining_directions = {
        key: max(0, int(target) - int(counts.get(key, 0)))
        for key, target in targets.items()
        if int(counts.get(key, 0)) < int(target)
    }
    stopped_reason = (
        "sqlite_liquidity_snapshot_satisfied"
        if satisfied_count == len(targets)
        else "sqlite_liquidity_snapshot_shortfall_pending_rpc_backfill"
    )
    scan = {
        "scanDirection": "existing_sqlite_liquidity_snapshot",
        "onchainRpcScan": False,
        "sqliteEvidenceSource": (
            "v4_exchange_pools + v4_exchange_pool_liquidity_snapshots"
        ),
        "sqlitePoolCount": len(snapshot_rows),
        "sqliteSnapshotCount": len(has_liquidity_rows),
        "sqliteLiveConfirmedPoolCount": len(candidates),
        "sqliteZeroLiquidityPoolCount": len(zero_liquidity_rows),
        "sqliteMissingSnapshotPoolCount": len(missing_snapshot_rows),
        "verifiedSnapshotRefreshedAtRange": (
            [refreshed_times[0], refreshed_times[-1]]
            if refreshed_times
            else []
        ),
        "directionTargets": targets,
        "directionCounts": counts,
        "neededDirectionCount": len(targets),
        "satisfiedDirectionCount": satisfied_count,
        "remainingDirections": remaining_directions,
        "stoppedReason": stopped_reason,
        "selectedPoolCount": len(all_selected_pools),
    }
    return {
        "schemaVersion": "robinhood-testnet-v4-directional-backfill-evidence-v1",
        "generatedAt": _utc_now_iso(),
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": resolved_chain_id,
        "database": resolved_database,
        "scope": {
            "directionsRequested": list(directions) if directions else "all",
            "limitDirections": int(limit_directions or 0),
            "candidateLimit": MAX_DIRECTION_CANDIDATE_POOLS,
            "forwardDirection": "lower_address_to_higher_address",
            "reverseDirection": "higher_address_to_lower_address",
            "searchOrder": "existing_sqlite_snapshot_only",
            "stopCondition": "reuse_verified_liquidity_snapshot_or_record_shortfall",
        },
        "plan": plan,
        "scan": scan,
        "indexedLiquidity": {
            "poolCount": len(snapshot_rows),
            "snapshotCount": len(has_liquidity_rows),
            "liveConfirmedPoolCount": len(candidates),
            "zeroLiquidityPoolCount": len(zero_liquidity_rows),
            "missingSnapshotPoolCount": len(missing_snapshot_rows),
        },
        "selectedPools": all_selected_pools,
        "safety": {
            "writesSqlite": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "observationOnly": True,
        },
    }


def _assert_evidence_scope(output_dir: Path, network: str) -> Path:
    evidence_root = SRC_ROOT / "runtime" / "evidence" / network
    resolved_output = output_dir.resolve()
    resolved_root = evidence_root.resolve()
    if not str(resolved_output).lower().startswith(str(resolved_root).lower()):
        raise ValueError(
            f"evidence output must stay under {evidence_root}, got {resolved_output}"
        )
    return resolved_output


def write_evidence_bundle(
    report: dict[str, Any],
    *,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    evidence_root = SRC_ROOT / "runtime" / "evidence" / network / "v4_directional_backfill"
    resolved_output = _assert_evidence_scope(
        output_dir or (evidence_root / _stamp()),
        network=network,
    )
    report_path = resolved_output / "report.json"
    summary_path = resolved_output / "summary.md"
    _write_json(report_path, report)
    scan = report.get("scan") or {}
    plan = report.get("plan") or {}
    summary = [
        "# Robinhood Testnet V4 Directional Backfill Evidence",
        "",
        f"- generatedAt: {report.get('generatedAt')}",
        f"- chainId: {report.get('chainId')}",
        f"- neededDirectionCount: {scan.get('neededDirectionCount', plan.get('neededDirectionCount'))}",
        f"- satisfiedDirectionCount: {scan.get('satisfiedDirectionCount')}",
        f"- stoppedReason: {scan.get('stoppedReason')}",
        f"- scannedSegmentCount: {scan.get('scannedSegmentCount')}",
        f"- initializeLogCount: {scan.get('initializeLogCount')}",
        f"- selectedPoolCount: {scan.get('selectedPoolCount')}",
        f"- sqliteSnapshotCount: {scan.get('sqliteSnapshotCount')}",
        f"- sqliteLiveConfirmedPoolCount: {scan.get('sqliteLiveConfirmedPoolCount')}",
        f"- sqliteZeroLiquidityPoolCount: {scan.get('sqliteZeroLiquidityPoolCount')}",
        f"- sqliteMissingSnapshotPoolCount: {scan.get('sqliteMissingSnapshotPoolCount')}",
        f"- verifiedSnapshotRefreshedAtRange: {json.dumps(scan.get('verifiedSnapshotRefreshedAtRange') or [])}",
        f"- fromBlock: {scan.get('fromBlock')}",
        f"- toBlock: {scan.get('toBlock')}",
        f"- directionCounts: {json.dumps(scan.get('directionCounts') or {}, ensure_ascii=False)}",
        "",
        f"- scanDirection: {scan.get('scanDirection')}",
        f"- onchainRpcScan: {scan.get('onchainRpcScan')}",
        "",
        "SQLite-only mode reuses verified pool and liquidity snapshot rows and",
        "records any shortfall without opening an RPC connection.",
        "",
    ]
    summary_path.write_text("\n".join(summary), encoding="utf-8")
    return {
        "ok": bool(report.get("ok", True)),
        "reportPath": str(report_path),
        "summaryPath": str(summary_path),
        "network": network,
        "chainId": report.get("chainId"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--pool-manager", default="")
    parser.add_argument(
        "--directions",
        default="",
        help="Comma-separated canonical token:token direction keys; default is all shortfall directions.",
    )
    parser.add_argument("--limit-directions", type=int, default=0)
    parser.add_argument("--chunk-size", type=int, default=500_000)
    parser.add_argument("--min-chunk-size", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=300)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument(
        "--sqlite-only",
        action="store_true",
        help=(
            "Reuse verified SQLite pools and liquidity snapshots only; "
            "never scan RPC and record any shortfall."
        ),
    )
    args = parser.parse_args()

    parsed_directions = tuple(
        item.strip()
        for item in args.directions.split(",")
        if item.strip()
    )
    if args.sqlite_only:
        report = collect_report_from_sqlite(
            database=args.database,
            directions=parsed_directions,
            limit_directions=args.limit_directions,
        )
    else:
        report = collect_report(
            database=args.database,
            rpc_url=args.rpc_url,
            pool_manager=args.pool_manager,
            directions=parsed_directions,
            limit_directions=args.limit_directions,
            chunk_size=args.chunk_size,
            min_chunk_size=args.min_chunk_size,
            batch_size=args.batch_size,
        )
    if args.print_only:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    written = write_evidence_bundle(
        report,
        output_dir=args.output_dir,
    )
    print(json.dumps(written, ensure_ascii=False, indent=2, sort_keys=True))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "collect_report",
    "collect_report_from_sqlite",
    "write_evidence_bundle",
]
