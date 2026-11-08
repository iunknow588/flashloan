"""Idle SQLite maintenance consumer for the Rust closed-loop worker pool.

The Rust pool only produces maintenance submissions; Python is the single
consumer thread that performs RPC backfill, SQLite writes, candidate/anchor
rebuild and evidence output.  Mainnet stays fail-closed: this module refuses
everything except Robinhood testnet 46630.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.direction_pool_score import (
    MAX_DIRECTION_CANDIDATE_POOLS,
    MIN_DIRECTION_CANDIDATE_POOLS,
)
from core.db.storage_business_schema import ensure_business_schema
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    all_anchor_symbols,
)
from dex.tools.refresh_robinhood_v4_live import _confirm_backfill_pool_liquidity


SRC_ROOT = Path(__file__).resolve().parents[2] / "runtime"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
PROTOCOL = "uniswap_v4"
STALE_LIQUIDITY_TTL_SECONDS = 900
PRICE_CHANGE_TRIGGER_BPS = 50


def _normalize_robinhood_network(network: str, chain_id: int) -> tuple[str, int, str]:
    """Return the canonical Robinhood scope for a network/chain pair."""
    normalized = str(network or "").strip().lower()
    if normalized == ROBINHOOD_TESTNET_NETWORK or int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_MARKET_ID
    return ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID, ROBINHOOD_MARKET_ID


def _robinhood_market_id(network: str, chain_id: int) -> str:
    return _normalize_robinhood_network(network, chain_id)[2]


def _robinhood_pool_manager(network: str, chain_id: int) -> str:
    if _normalize_robinhood_network(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
            ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        )

        return ROBINHOOD_TESTNET_V4_POOL_MANAGER
    from dex.intent_trade.chains.robinhood.config import V4_POOL_MANAGER_ADDRESS

    from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
        ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    )

    return V4_POOL_MANAGER_ADDRESS or ROBINHOOD_TESTNET_V4_POOL_MANAGER


def _robinhood_rpc_url(network: str, chain_id: int) -> str:
    if _normalize_robinhood_network(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
            _rpc_url_from_env_or_default,
        )

        return _rpc_url_from_env_or_default()
    from dex.intent_trade.chains.robinhood.v4_pool_rpc import (
        _rpc_url_from_env_or_default,
    )

    return _rpc_url_from_env_or_default()


def _robinhood_v4_pool_index_module(network: str, chain_id: int):
    if _normalize_robinhood_network(network, chain_id)[1] == ROBINHOOD_TESTNET_CHAIN_ID:
        return __import__(
            "dex.intent_trade.chains.robinhood_testnet.v4_pool_index",
            fromlist=["build_v4_pool_index_from_initialize_logs", "build_v4_directional_pool_backfill"],
        )
    return __import__(
        "dex.intent_trade.chains.robinhood.v4_pool_index",
        fromlist=["build_v4_pool_index_from_initialize_logs", "build_v4_directional_pool_backfill"],
    )


def _robinhood_scope_allowed(network: str, chain_id: int) -> bool:
    normalized = _text(network)
    try:
        cid = int(chain_id)
    except (TypeError, ValueError):
        return False
    return (
        normalized == ROBINHOOD_TESTNET_NETWORK and cid == ROBINHOOD_TESTNET_CHAIN_ID
    ) or (
        normalized == ROBINHOOD_NETWORK and cid == ROBINHOOD_CHAIN_ID
    )

# Single source of truth for the YC/ZC candidate-eligible pool scope.  The
# CLI completeness guard and the maintenance workers must agree on this
# filter so generic unknown pools never force a full block-0 rescan.
_CANDIDATE_ELIGIBLE_WHERE_SQL = """
    p.network = ? AND p.chain_id = ? AND p.protocol = ?
    AND p.fee > 0 AND p.fee <= 3000 AND (p.fee & 8388608) = 0
    AND (COALESCE(p.hooks, '') = '' OR lower(p.hooks) = ?)
"""


def _eligible_token_placeholders(addresses: list[str]) -> tuple[str, list[str]]:
    placeholders = ",".join("?" for _ in addresses)
    condition = (
        f" AND (lower(p.token0) IN ({placeholders})"
        f" OR lower(p.token1) IN ({placeholders}))"
    )
    params = list(addresses) + list(addresses)
    return condition, params


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


_SQLITE_CONSUMER_LOCK = __import__("threading").RLock()
_LIQUIDITY_WORKER_INTERVAL_SECONDS = 900
_LIQUIDITY_WORKER_BATCH_SIZE = 512
_LIQUIDITY_WORKER_RPC_BATCH_CAP = 4096


def _write_lock(database: str):
    """Serialise SQLite writes from the single maintenance consumer."""
    return _SQLITE_CONSUMER_LOCK


def _liquidity_worker_interval_seconds() -> int:
    raw = os.getenv("V4_POOL_LIQUIDITY_WORKER_INTERVAL_SECONDS", "")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _LIQUIDITY_WORKER_INTERVAL_SECONDS


def _liquidity_worker_batch_size() -> int:
    raw = os.getenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _LIQUIDITY_WORKER_BATCH_SIZE


def _liquidity_worker_rpc_batch_size() -> int:
    raw = os.getenv("V4_POOL_LIQUIDITY_WORKER_RPC_BATCH_SIZE", "")
    try:
        return max(1, min(_LIQUIDITY_WORKER_RPC_BATCH_CAP, int(raw)))
    except (TypeError, ValueError):
        return _LIQUIDITY_WORKER_RPC_BATCH_CAP


def _liquidity_worker_dynamic_index_scan_enabled() -> bool:
    raw = os.getenv("V4_POOL_LIQUIDITY_WORKER_DYNAMIC_INDEX_SCAN_ENABLED", "")
    if not raw:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _dynamic_pool_discovery_enabled() -> bool:
    raw = os.getenv("V4_POOL_LIQUIDITY_WORKER_DYNAMIC_DISCOVERY_ENABLED", "")
    if not raw:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _route_addresses(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("route_addresses")
    if not isinstance(raw, list):
        raw = payload.get("routeAddresses")
    return [
        _text(address)
        for address in (raw or [])
        if _text(address).startswith("0x") and len(_text(address)) == 42
    ]


def _directed_pairs(route_addresses: list[str]) -> tuple[str, ...]:
    """Return the six directed edges of the A->B->C->A closed loop."""
    if len(route_addresses) < 4:
        return ()
    anchor = route_addresses[0]
    y = route_addresses[1]
    z = route_addresses[2]
    if anchor != route_addresses[3]:
        return ()
    pairs = [
        f"{anchor}:{y}",
        f"{y}:{anchor}",
        f"{y}:{z}",
        f"{z}:{y}",
        f"{z}:{anchor}",
        f"{anchor}:{z}",
    ]
    return tuple(dict.fromkeys(pairs))


def _route_direction_plan(
    database: str,
    *,
    network: str,
    chain_id: int,
    directed_pairs: tuple[str, ...],
    direction_target: int | None = None,
) -> dict[str, Any]:
    """Count live usable candidates per route direction from SQLite only."""
    from core.db.storage_business_io import query

    candidate_target = max(
        MIN_DIRECTION_CANDIDATE_POOLS,
        min(
            MIN_DIRECTION_CANDIDATE_POOLS if direction_target is None else int(direction_target),
            MAX_DIRECTION_CANDIDATE_POOLS * 10,
        ),
    )

    pairs: list[tuple[str, str]] = []
    for direction in directed_pairs:
        left, separator, right = str(direction).partition(":")
        if (
            separator
            and _text(left).startswith("0x")
            and len(_text(left)) == 42
            and _text(right).startswith("0x")
            and len(_text(right)) == 42
        ):
            pairs.append((_text(left), _text(right)))
    pairs = list(dict.fromkeys(pairs))
    counts: dict[str, int] = {direction: 0 for direction in directed_pairs}
    selected: dict[str, list[dict[str, Any]]] = {
        direction: [] for direction in directed_pairs
    }
    if not pairs:
        return {
            "targets": {},
            "counts": counts,
            "selectedPools": {},
            "neededDirectionCount": 0,
            "satisfiedDirectionCount": 0,
            "floorBlock": 0,
            "basis": "route_addresses_invalid",
        }
    pair_placeholders = ",".join("(?, ?)" for _ in pairs)
    pair_params = [part for pair in pairs for part in pair]
    rows = query(
        database,
        f"""
        SELECT p.pool_id, p.pool_key_json, p.token0, p.token1, p.fee,
               p.tick_spacing, p.hooks,
               CAST(json_extract(p.pool_key_json, '$.initializeBlockNumber') AS INTEGER)
                 AS initialize_block_number,
               s.has_liquidity, s.liquidity, s.liquidity_index,
               s.estimated_tvl_usd, s.sqrt_price_x96, s.tick,
               s.liquidity_read_at, s.liquidity_block_number, s.evidence_source
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
            network,
            chain_id,
            PROTOCOL,
            *pair_params,
            *pair_params,
        ),
    )
    now = _utc_now_iso()
    seen: dict[str, set[str]] = {direction: set() for direction in directed_pairs}
    floor_blocks: list[int] = []
    for row in rows:
        pool_id = _text(row.get("pool_id"))
        if not pool_id.startswith("0x"):
            continue
        token0 = _text(row.get("token0"))
        token1 = _text(row.get("token1"))
        if (
            token0 not in {pair[0] for pair in pairs}
            and token1 not in {pair[0] for pair in pairs}
        ):
            continue
        floor_block = int(row.get("initialize_block_number") or 0)
        if floor_block > 0:
            floor_blocks.append(floor_block)
        if (
            int(row.get("has_liquidity") or 0) != 1
            or not _usable_candidate_pool(row)
        ):
            continue
        direction, reverse = _directional_keys(token0, token1)
        for key in (direction, reverse):
            if key not in counts or pool_id in seen[key]:
                continue
            seen[key].add(pool_id)
            counts[key] += 1
            selected[key].append(
                {
                    "poolId": pool_id,
                    "token0": token0,
                    "token1": token1,
                    "fee": row.get("fee"),
                    "tickSpacing": row.get("tick_spacing"),
                    "hooks": row.get("hooks"),
                    "initializeBlockNumber": floor_block,
                    "hasLiquidity": row.get("has_liquidity"),
                    "liquidity": row.get("liquidity"),
                    "liquidityIndex": row.get("liquidity_index"),
                    "estimatedTvlUsd": row.get("estimated_tvl_usd"),
                    "liquidityReadAt": row.get("liquidity_read_at"),
                    "liquidityBlockNumber": row.get("liquidity_block_number"),
                    "evidenceSource": row.get("evidence_source"),
                }
            )
    targets = {
        direction: candidate_target
        for direction, count in counts.items()
        if count < candidate_target
    }
    return {
        "targets": targets,
        "counts": counts,
        "selectedPools": selected,
        "minimumDirectionTarget": int(MIN_DIRECTION_CANDIDATE_POOLS),
        "candidateDirectionTarget": int(candidate_target),
        "maximumDirectionTarget": int(MAX_DIRECTION_CANDIDATE_POOLS),
        "neededDirectionCount": len(targets),
        "satisfiedDirectionCount": sum(
            1
            for direction in directed_pairs
            if counts.get(direction, 0) >= min(
                MIN_DIRECTION_CANDIDATE_POOLS,
                candidate_target,
            )
        ),
        "floorBlock": min(floor_blocks) if floor_blocks else 0,
        "basis": "route_edges_sqlite_live_candidates",
        "observedAt": now,
    }


def _route_pool_price_snapshots(
    database: str,
    *,
    network: str,
    chain_id: int,
    route_addresses: list[str],
) -> dict[str, dict[str, Any]]:
    """Return pool price evidence for the route's directed pairs."""
    from core.db.storage_business_io import query

    directed_pairs = _directed_pairs(route_addresses)
    pairs: list[tuple[str, str]] = []
    for direction in directed_pairs:
        left, separator, right = str(direction).partition(":")
        if (
            separator
            and _text(left).startswith("0x")
            and len(_text(left)) == 42
            and _text(right).startswith("0x")
            and len(_text(right)) == 42
        ):
            normalized = (sorted((_text(left), _text(right)))[0], sorted((_text(left), _text(right)))[1])
            if normalized not in pairs:
                pairs.append(normalized)
    if not pairs:
        return {}
    pair_placeholders = ",".join("(?, ?)" for _ in pairs)
    pair_params = [part for pair in pairs for part in pair]
    rows = query(
        database,
        f"""
        SELECT p.pool_id, p.token0, p.token1,
               s.price AS price, s.sqrt_price_x96 AS sqrt_price_x96,
               s.tick AS tick
        FROM v4_exchange_pools p
        JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = p.network AND s.chain_id = p.chain_id
         AND s.protocol = p.protocol AND s.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND s.has_liquidity = 1
          AND (
            (lower(p.token0), lower(p.token1)) IN ({pair_placeholders})
            OR (lower(p.token1), lower(p.token0)) IN ({pair_placeholders})
          )
        """,
        (network, chain_id, PROTOCOL, *pair_params, *pair_params),
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        pool_id = _text(row.get("pool_id"))
        if not pool_id.startswith("0x"):
            continue
        result[pool_id] = {
            "token0": _text(row.get("token0")),
            "token1": _text(row.get("token1")),
            "price": row.get("price"),
            "sqrtPriceX96": _text(row.get("sqrt_price_x96")),
            "tick": row.get("tick"),
        }
    return result


def _decimal_or_none(value: object) -> Decimal | None:
    from decimal import Decimal, InvalidOperation

    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def _price_relative_change(
    before: dict[str, Any],
    after: dict[str, Any],
) -> Decimal | None:
    from decimal import Decimal

    before_price = _decimal_or_none(before.get("price"))
    after_price = _decimal_or_none(after.get("price"))
    if before_price is not None and after_price is not None:
        return abs((after_price - before_price) / before_price)
    before_sqrt = _decimal_or_none(before.get("sqrtPriceX96"))
    after_sqrt = _decimal_or_none(after.get("sqrtPriceX96"))
    if before_sqrt is not None and after_sqrt is not None:
        price_ratio = (after_sqrt / before_sqrt) ** 2
        return abs(price_ratio - Decimal("1"))
    return None


def _price_change_trigger(
    database: str,
    *,
    network: str,
    chain_id: int,
    route_addresses: list[str],
    before_snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return whether a pool price moved more than the trigger threshold."""
    from decimal import Decimal

    after_snapshots = _route_pool_price_snapshots(
        database,
        network=network,
        chain_id=chain_id,
        route_addresses=route_addresses,
    )
    changes: list[dict[str, Any]] = []
    for pool_id, before in before_snapshots.items():
        after = after_snapshots.get(pool_id)
        if not after:
            continue
        relative = _price_relative_change(before, after)
        if relative is None:
            continue
        change_bps = float(relative * 10000)
        parsed_before = _decimal_or_none(before.get("price"))
        parsed_after = _decimal_or_none(after.get("price"))
        changes.append(
            {
                "poolId": pool_id,
                "token0": before.get("token0"),
                "token1": before.get("token1"),
                "beforePrice": (
                    str(parsed_before) if parsed_before is not None else before.get("price")
                ),
                "afterPrice": (
                    str(parsed_after) if parsed_after is not None else after.get("price")
                ),
                "changeBps": round(change_bps, 4),
                "basis": "price" if parsed_before is not None and parsed_after is not None else "sqrt_price_x96",
            }
        )
    max_change = max(changes, key=lambda item: float(item["changeBps"])) if changes else None
    changed_count = sum(1 for item in changes if float(item["changeBps"]) >= PRICE_CHANGE_TRIGGER_BPS)
    return {
        "basis": "route_direction_pool_snapshots",
        "compareCount": len(changes),
        "changedPoolCount": changed_count,
        "maxChangeBps": float(max_change["changeBps"]) if max_change else 0.0,
        "maxChangePoolId": max_change["poolId"] if max_change else None,
        "maxChangeToken0": max_change["token0"] if max_change else None,
        "maxChangeToken1": max_change["token1"] if max_change else None,
        "thresholdBps": PRICE_CHANGE_TRIGGER_BPS,
        "triggered": bool(max_change and float(max_change["changeBps"]) >= PRICE_CHANGE_TRIGGER_BPS),
        "changes": changes[:20],
    }


def _directional_keys(token0: object, token1: object) -> tuple[str, str]:
    left = _text(token0)
    right = _text(token1)
    if not (left.startswith("0x") and right.startswith("0x")):
        return f"{left}:{right}", f"{right}:{left}"
    small, large = sorted((left, right))
    return f"{small}:{large}", f"{large}:{small}"


def _usable_candidate_pool(row: dict[str, Any]) -> bool:
    fee = row.get("fee")
    if fee is None:
        return False
    try:
        fee = int(fee)
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > 3000 or bool(fee & 8388608):
        return False
    hooks = _text(row.get("hooks"))
    return hooks in {"", ZERO_ADDRESS}


def _candidate_pool_ids(
    *,
    planned: dict[str, Any],
    pool_candidates: list[dict[str, Any]],
    backfill_payload: dict[str, Any] | None = None,
) -> list[str]:
    """Return pool ids this maintenance run should read live."""
    pool_ids: set[str] = set()
    for selected in (planned.get("selectedPools") or {}).values():
        for pool in selected or []:
            if isinstance(pool, dict):
                pool_id = _text(pool.get("poolId") or pool.get("pool_id") or pool.get("pool"))
                if pool_id.startswith("0x"):
                    pool_ids.add(pool_id)
    for item in pool_candidates or []:
        if not isinstance(item, dict):
            continue
        pool_id = _text(item.get("poolId") or item.get("pool_id") or item.get("pool"))
        if pool_id.startswith("0x"):
            pool_ids.add(pool_id)
    for pool in (backfill_payload or {}).get("pools") or []:
        if not isinstance(pool, dict):
            continue
        pool_id = _text(pool.get("poolId") or pool.get("pool_id") or pool.get("pool"))
        if pool_id.startswith("0x"):
            pool_ids.add(pool_id)
    return sorted(pool_ids)


def _candidate_eligible_pool_ids(
    database: str,
    *,
    network: str,
    chain_id: int,
    known_token_addresses: list[str] | None = None,
) -> list[str]:
    """Return fixed-fee/no-hook pools that still need liquidity maintenance.

    Explicit ``has_liquidity=0`` pools are terminal for the incremental sweep:
    they are excluded from YC/ZC candidates by the liquidity gate, and a full
    backfill/reindex is responsible for re-evaluating them later.  Unknown and
    confirmed pools continue to be refreshed so prices and liquidity stay
    fresh.  When ``known_token_addresses`` is supplied, only pools touching
    that fixed token universe are returned, keeping the sweep proportional to
    the configurable pool set instead of every Initialize event on chain.
    """
    from core.db.storage_business_io import query

    where = _CANDIDATE_ELIGIBLE_WHERE_SQL + "\n    AND COALESCE(l.has_liquidity, 1) = 1"
    params: list[Any] = [network, chain_id, PROTOCOL, ZERO_ADDRESS]
    if known_token_addresses is not None:
        addresses = sorted(
            {
                str(address).strip().lower()
                for address in known_token_addresses
                if str(address or "").strip().lower().startswith("0x")
            }
        )
        if not addresses:
            return []
        condition, token_params = _eligible_token_placeholders(addresses)
        where += condition
        params.extend(token_params)
    rows = query(
        database,
        f"""
        SELECT lower(p.pool_id) AS pool_id
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE {where}
        ORDER BY lower(p.pool_id)
        """,
        tuple(params),
    )
    return [
        str(row.get("pool_id") or "").strip().lower()
        for row in rows
        if str(row.get("pool_id") or "").strip().lower().startswith("0x")
    ]


def _known_pool_token_addresses(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Return the fixed token universe persisted in ``chain_tokens``.

    Only tokens whose metadata carries a real category (stock, stablecoin,
    native, wrapped, test fixture, ...) are part of the Robinhood V4 pool
    universe.  Tokens with no category or ``unknown`` are excluded so the
    incremental worker never walks every Initialize event on the testnet.
    """
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT DISTINCT lower(token_address) AS token_address
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
          AND lower(COALESCE(json_extract(token_metadata_json, '$.category'), ''))
              NOT IN ('', 'unknown')
        ORDER BY token_address
        """,
        (network, chain_id),
    )
    addresses = [
        str(row.get("token_address") or "").strip().lower()
        for row in rows
        if str(row.get("token_address") or "").strip().lower().startswith("0x")
    ]
    if addresses:
        return sorted(set(addresses))
    # A database that has not seeded token metadata yet still needs an
    # incremental sweep; fall back to the token universe present in the raw
    # pool rows so a fresh chain is not treated as an empty pool set.
    rows = query(
        database,
        """
        SELECT DISTINCT lower(token0) AS token_address
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        UNION
        SELECT DISTINCT lower(token1) AS token_address
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, PROTOCOL, network, chain_id, PROTOCOL),
    )
    return sorted(
        {
            str(row.get("token_address") or "").strip().lower()
            for row in rows
            if str(row.get("token_address") or "").strip().lower().startswith("0x")
        }
    )


def _candidate_eligible_missing_liquidity_count(
    database: str,
    *,
    network: str,
    chain_id: int,
    known_token_addresses: list[str] | None = None,
) -> int:
    """Return the count of eligible pools lacking an explicit verdict."""
    counts = _candidate_eligible_liquidity_counts(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    return int(counts["eligibleUnknownCount"])


def _candidate_eligible_liquidity_counts(
    database: str,
    *,
    network: str,
    chain_id: int,
    known_token_addresses: list[str] | None = None,
) -> dict[str, int]:
    """Return YC/ZC eligible pool counts by liquidity verdict.

    ``known_token_addresses=None`` means every pool in the fixed-fee/no-hook
    scope counts as eligible (used by the maintenance sweep when the caller
    already constrained the scope).  The CLI completeness guard always passes
    the known token universe explicitly.
    """
    from core.db.storage_business_io import query

    where = _CANDIDATE_ELIGIBLE_WHERE_SQL
    params: list[Any] = [network, chain_id, PROTOCOL, ZERO_ADDRESS]
    if known_token_addresses is not None:
        addresses = sorted(
            {
                str(address).strip().lower()
                for address in known_token_addresses
                if str(address or "").strip().lower().startswith("0x")
            }
        )
        if not addresses:
            return {
                "eligiblePoolCount": 0,
                "eligibleConfirmedCount": 0,
                "eligibleZeroCount": 0,
                "eligibleUnknownCount": 0,
            }
        condition, token_params = _eligible_token_placeholders(addresses)
        where += condition
        params.extend(token_params)
    rows = query(
        database,
        f"""
        SELECT COUNT(*) AS pool_count,
               COALESCE(SUM(CASE WHEN l.has_liquidity = 1 THEN 1 ELSE 0 END), 0)
                 AS confirmed_count,
               COALESCE(SUM(CASE WHEN l.has_liquidity = 0 THEN 1 ELSE 0 END), 0)
                 AS zero_count,
               COALESCE(SUM(CASE WHEN l.pool_id IS NULL OR l.has_liquidity IS NULL
                          THEN 1 ELSE 0 END), 0)
                 AS unknown_count
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE {where}
        """,
        tuple(params),
    )
    row = rows[0] if rows else {}
    return {
        "eligiblePoolCount": int(row.get("pool_count") or 0),
        "eligibleConfirmedCount": int(row.get("confirmed_count") or 0),
        "eligibleZeroCount": int(row.get("zero_count") or 0),
        "eligibleUnknownCount": int(row.get("unknown_count") or 0),
    }


def _non_eligible_unknown_liquidity_count(
    database: str,
    *,
    network: str,
    chain_id: int,
    known_token_addresses: list[str] | None = None,
) -> int:
    """Count raw pools outside the eligible scope that lack a verdict."""
    from core.db.storage_business_io import query

    params: list[Any] = [network, chain_id, PROTOCOL, ZERO_ADDRESS]
    token_condition = ""
    if known_token_addresses is not None:
        addresses = sorted(
            {
                str(address).strip().lower()
                for address in known_token_addresses
                if str(address or "").strip().lower().startswith("0x")
            }
        )
        if not addresses:
            return 0
        token_condition, token_params = _eligible_token_placeholders(addresses)
        params.extend(token_params)
    rows = query(
        database,
        f"""
        SELECT COUNT(*) AS count
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
          AND (l.pool_id IS NULL OR l.has_liquidity IS NULL)
          AND NOT (
            p.fee > 0 AND p.fee <= 3000 AND (p.fee & 8388608) = 0
            AND (COALESCE(p.hooks, '') = '' OR lower(p.hooks) = ?)
            {token_condition}
          )
        """,
        tuple(params),
    )
    return int((rows[0] or {}).get("count") or 0)


def _explicit_zero_liquidity_pool_ids(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Return every raw pool with an explicit zero-liquidity verdict."""
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT DISTINCT lower(pool_id) AS pool_id
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND has_liquidity = 0
        """,
        (network, chain_id, PROTOCOL),
    )
    return sorted(
        {
            str(row.get("pool_id") or "").strip().lower()
            for row in rows
            if str(row.get("pool_id") or "").strip().lower().startswith("0x")
        }
    )


def _cleanup_zero_liquidity_pool_refs(
    database: str,
    *,
    network: str,
    chain_id: int,
    zero_pool_ids: list[str],
) -> dict[str, Any]:
    """Remove candidate/observation/loop references to explicitly empty pools.

    Raw ``v4_exchange_pools`` rows and liquidity snapshots are retained so a
    later positive snapshot makes the same physical pool eligible again without
    a full on-chain Initialize rescan.  The caller must already hold the
    single SQLite consumer lock when running from the maintenance manager.
    """
    from core.db.storage_business_io import delete_rows, query

    zero_pool_ids = sorted(
        {
            str(pool_id).strip().lower()
            for pool_id in zero_pool_ids
            if str(pool_id or "").strip().lower().startswith("0x")
        }
    )
    cleaned = {
        "v4_exchange_pool_liquidity_snapshots": 0,
        "v4_exchange_pools": 0,
        "dex_candidate_pool_recommendations": 0,
        "dex_candidate_pools": 0,
        "v4_closed_loop_hops": 0,
        "v4_closed_loop_optimizations": 0,
        "v4_closed_loops": 0,
    }
    if not zero_pool_ids:
        return cleaned
    affected_symbols: set[str] = set()
    affected_loop_keys: set[str] = set()
    placeholders = ",".join("?" for _ in zero_pool_ids)
    rows = query(
        database,
        f"""
        SELECT DISTINCT anchor_symbol
        FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND pool_id IN ({placeholders})
        """,
        (network, chain_id, PROTOCOL, *zero_pool_ids),
    )
    affected_symbols.update(
        str(row.get("anchor_symbol") or "").strip().upper()
        for row in rows
        if str(row.get("anchor_symbol") or "").strip()
    )
    rows = query(
        database,
        f"""
        SELECT DISTINCT loop_key
        FROM v4_closed_loop_hops
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND pool_id IN ({placeholders})
        """,
        (network, chain_id, PROTOCOL, *zero_pool_ids),
    )
    affected_loop_keys.update(
        str(row.get("loop_key") or "").strip()
        for row in rows
        if str(row.get("loop_key") or "").strip()
    )
    rows = query(
        database,
        f"""
        SELECT DISTINCT loop_key
        FROM v4_closed_loop_optimizations
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND optimized_pool_id IN ({placeholders})
        """,
        (network, chain_id, PROTOCOL, *zero_pool_ids),
    )
    affected_loop_keys.update(
        str(row.get("loop_key") or "").strip()
        for row in rows
        if str(row.get("loop_key") or "").strip()
    )
    affected_loop_keys = {key for key in affected_loop_keys if key}
    if affected_loop_keys:
        loop_placeholders = ",".join("?" for _ in affected_loop_keys)
        cleaned["v4_closed_loop_optimizations"] += delete_rows(
            database,
            "v4_closed_loop_optimizations",
            (
                "network = ? AND chain_id = ? AND protocol = ? "
                f"AND loop_key IN ({loop_placeholders})"
            ),
            (network, chain_id, PROTOCOL, *affected_loop_keys),
        )
        cleaned["v4_closed_loop_hops"] += delete_rows(
            database,
            "v4_closed_loop_hops",
            (
                "network = ? AND chain_id = ? AND protocol = ? "
                f"AND loop_key IN ({loop_placeholders})"
            ),
            (network, chain_id, PROTOCOL, *affected_loop_keys),
        )
        cleaned["v4_closed_loops"] += delete_rows(
            database,
            "v4_closed_loops",
            (
                "network = ? AND chain_id = ? AND protocol = ? "
                f"AND loop_key IN ({loop_placeholders})"
            ),
            (network, chain_id, PROTOCOL, *affected_loop_keys),
        )
    for table, column in (
        ("dex_candidate_pool_recommendations", "pool_id"),
        ("dex_candidate_pools", "pool_id"),
    ):
        for symbol in affected_symbols:
            cleaned[table] += delete_rows(
                database,
                table,
                (
                    "network = ? AND chain_id = ? AND protocol = ? "
                    f"AND anchor_symbol = ? AND {column} IN (" + placeholders + ")"
                ),
                (network, chain_id, PROTOCOL, symbol, *zero_pool_ids),
            )
    for symbol in affected_symbols:
        try:
            _rebuild_anchor(
                database,
                network=network,
                chain_id=chain_id,
                market_id=_robinhood_market_id(network, chain_id),
                anchor_symbol=symbol,
            )
        except Exception as exc:
            cleaned.setdefault("anchorRebuildErrors", []).append(
                {"anchorSymbol": symbol, "error": str(exc)[:300]}
            )
    return cleaned


def _run_pool_liquidity_tick(
    *,
    database: str,
    network: str,
    chain_id: int,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Run one incremental liquidity slice under the single write lock."""
    with _write_lock(database):
        return _run_pool_liquidity_tick_locked(
            database=database,
            network=network,
            chain_id=chain_id,
            batch_size=batch_size,
        )


def _run_pool_liquidity_tick_locked(
    *,
    database: str,
    network: str,
    chain_id: int,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Refresh the next incremental slice of candidate-eligible pools.

    This is the fixed liquidity worker's single unit of work.  The SQLite
    cursor is updated only after the refresh succeeds, so an RPC failure
    retries the same slice on the next tick.
    """
    from core.db.v4_pool_liquidity_worker import (
        load_v4_pool_liquidity_cursor,
        save_v4_pool_liquidity_cursor,
    )
    from dex.tools.refresh_robinhood_v4_live import refresh_v4_live

    if batch_size is None:
        batch_size = _liquidity_worker_batch_size()
    else:
        batch_size = max(1, int(batch_size))
    rpc_batch_size = _liquidity_worker_rpc_batch_size()
    dynamic_index_scan_enabled = _liquidity_worker_dynamic_index_scan_enabled()
    cursor_state = load_v4_pool_liquidity_cursor(
        database,
        network=network,
        chain_id=chain_id,
    )
    cursor_pool_id_before = str(
        cursor_state.get("cursor_pool_id") or ""
    ).strip().lower()
    known_token_addresses = _known_pool_token_addresses(
        database,
        network=network,
        chain_id=chain_id,
    )
    dynamic_discovery: dict[str, Any] = {"ok": True, "poolIds": [], "poolCount": 0}
    discovery_pool_ids: list[str] = []
    discovery_slice_pool_ids: list[str] = []
    discovery_slice: dict[str, Any] = {
        "ok": True,
        "phase": "skipped_no_short_direction",
        "discoveredPoolIds": [],
    }
    if _dynamic_pool_discovery_enabled():
        try:
            from dex.runtime.v4_dynamic_pool_discovery import (
                run_dynamic_pool_index_slice,
                dynamic_discovery_eligible_pool_ids,
                dynamic_pool_discovery_plan,
            )

            discovery_plan = dynamic_pool_discovery_plan(
                database=database,
                network=network,
                chain_id=chain_id,
            )
            discovery_pool_ids = dynamic_discovery_eligible_pool_ids(
                database=database,
                network=network,
                chain_id=chain_id,
            )
            if (
                not dynamic_index_scan_enabled
                and int(discovery_plan.get("shortDirectionCount") or 0) > 0
                and not discovery_pool_ids
            ):
                discovery_slice = {
                    "ok": True,
                    "phase": "dynamic_index_scan_disabled",
                    "discoveredPoolIds": [],
                    "poolIds": discovery_pool_ids,
                    "writesSqlite": False,
                }
            elif (
                int(discovery_plan.get("shortDirectionCount") or 0) > 0
                and not discovery_pool_ids
            ):
                discovery_slice = run_dynamic_pool_index_slice(
                    database=database,
                    network=network,
                    chain_id=chain_id,
                    dry_run=not dynamic_index_scan_enabled,
                )
            elif int(discovery_plan.get("shortDirectionCount") or 0) > 0:
                discovery_slice = {
                    "ok": True,
                    "phase": "targeted_verification_planned",
                    "discoveredPoolIds": [],
                    "poolIds": discovery_pool_ids,
                    "writesSqlite": False,
                }
            else:
                discovery_slice = {
                    "ok": True,
                    "phase": "directions_satisfied",
                    "discoveredPoolIds": [],
                    "writesSqlite": False,
                }
            discovery_slice_pool_ids = sorted(
                {
                    str(pool_id).strip().lower()
                    for pool_id in (discovery_slice.get("discoveredPoolIds") or [])
                    if str(pool_id or "").strip().lower().startswith("0x")
                }
            )
            dynamic_discovery = {
                "ok": True,
                "poolIds": discovery_pool_ids,
                "poolCount": len(discovery_pool_ids),
                "rpcBatchSize": rpc_batch_size,
                "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
                "incrementalSlice": discovery_slice,
                "incrementalPoolIds": discovery_slice_pool_ids,
                "directionCount": int(discovery_plan.get("directionCount") or 0),
                "shortDirectionCount": int(discovery_plan.get("shortDirectionCount") or 0),
                "shortDirections": discovery_plan.get("shortDirections") or [],
                "recoverablePoolCount": int(discovery_plan.get("recoverablePoolCount") or 0),
                "recoverablePoolIds": discovery_plan.get("recoverablePoolIds") or [],
                "newEligiblePoolCount": int(discovery_plan.get("newEligiblePoolCount") or 0),
                "newEligiblePoolIds": discovery_plan.get("newEligiblePoolIds") or [],
                "directionTarget": int(discovery_plan.get("directionTarget") or 5),
            }
        except Exception as exc:
            discovery_pool_ids = []
            dynamic_discovery = {
                "ok": False,
                "poolIds": [],
                "poolCount": 0,
                "error": str(exc)[:400],
            }
    else:
        dynamic_discovery = {
            "ok": True,
            "poolIds": [],
            "poolCount": 0,
            "skipped": True,
            "reason": "worker_disabled_by_env",
        }
    pool_ids = _candidate_eligible_pool_ids(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    if discovery_pool_ids or discovery_slice_pool_ids:
        pool_ids = sorted(
            set(pool_ids)
            | set(discovery_pool_ids)
            | set(discovery_slice_pool_ids)
        )
    scoped_pool_ids = pool_ids[: int(rpc_batch_size)]
    total = len(scoped_pool_ids)
    missing_before = _candidate_eligible_missing_liquidity_count(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    if total == 0:
        zero_liquidity_pool_ids = _explicit_zero_liquidity_pool_ids(
            database,
            network=network,
            chain_id=chain_id,
        )
        zero_cleanup = {}
        if zero_liquidity_pool_ids:
            try:
                zero_cleanup = _cleanup_zero_liquidity_pool_refs(
                    database,
                    network=network,
                    chain_id=chain_id,
                    zero_pool_ids=zero_liquidity_pool_ids,
                )
            except Exception as exc:
                save_v4_pool_liquidity_cursor(
                    database,
                    network=network,
                    chain_id=chain_id,
                    cursor=0,
                    pool_count=0,
                    scanned_pool_count=0,
                    remaining_pool_count=0,
                    batch_size=batch_size,
                    last_scanned_at=_utc_now_iso(),
                    mode="error",
                    error=f"zero_liquidity_cleanup_failed: {exc}",
                )
                return {
                    "ok": False,
                    "mode": "pool_liquidity_worker",
                    "phase": "zero_liquidity_cleanup_failed",
                    "error": str(exc),
                    "poolCount": 0,
                    "batchSize": 0,
                    "cursor": 0,
                    "cursorBefore": 0,
                    "missingBefore": missing_before,
                    "missingAfter": 0,
                    "scannedPoolCount": 0,
                    "remainingPoolCount": 0,
                    "refreshOk": True,
                    "refreshErrors": [],
                    "zeroLiquidityPoolCount": len(zero_liquidity_pool_ids),
                    "zeroLiquidityCleanup": {},
                    "zeroLiquidityCleanupError": str(exc),
                    "safety": {
                        "writesSqlite": True,
                        "buildsTransaction": False,
                        "broadcastsTransaction": False,
                        "broadcastEnabled": False,
                    },
                }
        save_v4_pool_liquidity_cursor(
            database,
            network=network,
            chain_id=chain_id,
            cursor=0,
            pool_count=0,
            scanned_pool_count=0,
            remaining_pool_count=0,
            batch_size=batch_size,
            last_scanned_at=_utc_now_iso(),
            mode="complete",
        )
        return {
            "ok": True,
            "mode": "pool_liquidity_worker",
            "phase": "no_eligible_pools",
            "poolCount": 0,
            "batchSize": 0,
            "cursor": 0,
            "cursorBefore": 0,
            "missingBefore": missing_before,
            "missingAfter": 0,
            "scannedPoolCount": 0,
            "remainingPoolCount": 0,
            "refreshOk": True,
            "refreshErrors": [],
            "zeroLiquidityPoolCount": len(zero_liquidity_pool_ids),
            "zeroLiquidityCleanup": zero_cleanup,
            "dynamicDiscovery": dynamic_discovery,
            "safety": {
                "writesSqlite": bool(zero_cleanup),
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    cursor = max(0, int(cursor_state.get("cursor") or 0))
    if cursor_pool_id_before in scoped_pool_ids:
        cursor = scoped_pool_ids.index(cursor_pool_id_before) + 1
    elif cursor_pool_id_before:
        cursor = bisect.bisect_right(scoped_pool_ids, cursor_pool_id_before)
    if cursor >= total:
        cursor = 0
    chunk = scoped_pool_ids[cursor : cursor + batch_size]
    cursor_before = cursor
    rpc_url = _robinhood_rpc_url(network, chain_id)
    if not rpc_url:
        return {
            "ok": False,
            "mode": "pool_liquidity_worker",
            "phase": "rpc_unavailable",
            "stoppedReason": "no_rpc_url",
            "poolCount": total,
            "batchSize": len(chunk),
            "cursor": cursor_before,
            "cursorBefore": cursor_before,
            "cursorPoolId": cursor_pool_id_before,
            "cursorPoolIdBefore": cursor_pool_id_before,
            "missingBefore": missing_before,
            "missingAfter": missing_before,
            "scannedPoolCount": cursor_before,
            "remainingPoolCount": max(0, total - cursor_before),
            "scopedPoolIds": scoped_pool_ids,
            "scopedPoolCount": total,
            "rpcBatchSize": rpc_batch_size,
            "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
            "refreshOk": False,
            "refreshErrors": ["no_rpc_url"],
            "dynamicDiscovery": dynamic_discovery,
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    try:
        refresh_result = refresh_v4_live(
            database=database,
            network=network,
            chain_id=chain_id,
            rpc_url=rpc_url,
            pool_manager=_robinhood_pool_manager(network, chain_id),
            candidate_backfill=False,
            skip_index=True,
            skip_liquidity=False,
            skip_build=True,
            pool_ids=chunk,
            import_pool_ids=chunk,
            batch_size=batch_size,
            batch_liquidity=True,
            prune_zero_liquidity=False,
            prune_unknown_liquidity=False,
            remove_json_evidence=False,
            replace_evidence=False,
        )
    except Exception as exc:
        save_v4_pool_liquidity_cursor(
            database,
            network=network,
            chain_id=chain_id,
            cursor=cursor_before,
            cursor_pool_id=cursor_pool_id_before,
            pool_count=total,
            scanned_pool_count=cursor_before,
            remaining_pool_count=total - cursor_before,
            batch_size=batch_size,
            last_scanned_at=_utc_now_iso(),
            mode="error",
            error=str(exc),
        )
        return {
            "ok": False,
            "mode": "pool_liquidity_worker",
            "phase": "tick_failed",
            "error": str(exc),
            "poolCount": total,
            "batchSize": len(chunk),
            "cursor": cursor_before,
            "cursorBefore": cursor_before,
            "cursorPoolId": cursor_pool_id_before,
            "cursorPoolIdBefore": cursor_pool_id_before,
            "missingBefore": missing_before,
            "missingAfter": missing_before,
            "scannedPoolCount": cursor_before,
            "remainingPoolCount": max(0, total - cursor_before),
            "scopedPoolIds": scoped_pool_ids,
            "scopedPoolCount": total,
            "rpcBatchSize": rpc_batch_size,
            "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
            "refreshOk": False,
            "refreshErrors": [str(exc)],
            "dynamicDiscovery": dynamic_discovery,
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    refresh_errors = refresh_result.get("errors") or []
    refresh_ok = bool(refresh_result.get("ok"))
    missing_after = _candidate_eligible_missing_liquidity_count(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    if not refresh_ok:
        save_v4_pool_liquidity_cursor(
            database,
            network=network,
            chain_id=chain_id,
            cursor=cursor_before,
            cursor_pool_id=cursor_pool_id_before,
            pool_count=total,
            scanned_pool_count=cursor_before,
            remaining_pool_count=total - cursor_before,
            batch_size=batch_size,
            last_scanned_at=_utc_now_iso(),
            mode="error",
            error="; ".join(str(item) for item in refresh_errors[:3]),
        )
        return {
            "ok": False,
            "mode": "pool_liquidity_worker",
            "phase": "tick_failed",
            "error": "pool_liquidity_refresh_failed",
            "poolCount": total,
            "batchSize": len(chunk),
            "cursor": cursor_before,
            "cursorBefore": cursor_before,
            "cursorPoolId": cursor_pool_id_before,
            "cursorPoolIdBefore": cursor_pool_id_before,
            "missingBefore": missing_before,
            "missingAfter": missing_after,
            "scannedPoolCount": cursor_before,
            "remainingPoolCount": max(0, total - cursor_before),
            "scopedPoolIds": scoped_pool_ids,
            "scopedPoolCount": total,
            "rpcBatchSize": rpc_batch_size,
            "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
            "refreshOk": False,
            "refreshErrors": refresh_errors,
            "dynamicDiscovery": dynamic_discovery,
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    zero_liquidity_pool_ids = _explicit_zero_liquidity_pool_ids(
        database,
        network=network,
        chain_id=chain_id,
    )
    zero_cleanup: dict[str, Any] = {}
    if zero_liquidity_pool_ids:
        try:
            zero_cleanup = _cleanup_zero_liquidity_pool_refs(
                database,
                network=network,
                chain_id=chain_id,
                zero_pool_ids=zero_liquidity_pool_ids,
            )
        except Exception as exc:
            save_v4_pool_liquidity_cursor(
                database,
                network=network,
                chain_id=chain_id,
                cursor=cursor_before,
                cursor_pool_id=cursor_pool_id_before,
                pool_count=total,
                scanned_pool_count=cursor_before,
                remaining_pool_count=max(0, total - cursor_before),
                batch_size=batch_size,
                last_scanned_at=_utc_now_iso(),
                mode="error",
                error=f"zero_liquidity_cleanup_failed: {exc}",
            )
            return {
                "ok": False,
                "mode": "pool_liquidity_worker",
                "phase": "zero_liquidity_cleanup_failed",
                "error": str(exc),
                "poolCount": total,
                "batchSize": len(chunk),
                "cursor": cursor_before,
                "cursorBefore": cursor_before,
                "cursorPoolId": cursor_pool_id_before,
                "cursorPoolIdBefore": cursor_pool_id_before,
                "missingBefore": missing_before,
                "missingAfter": missing_after,
                "scannedPoolCount": cursor_before,
                "remainingPoolCount": max(0, total - cursor_before),
                "scopedPoolIds": scoped_pool_ids,
                "scopedPoolCount": total,
                "rpcBatchSize": rpc_batch_size,
                "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
                "refreshOk": True,
                "refreshErrors": [],
                "zeroLiquidityPoolCount": len(zero_liquidity_pool_ids),
                "zeroLiquidityCleanup": {},
                "zeroLiquidityCleanupError": str(exc),
                "dynamicDiscovery": dynamic_discovery,
                "safety": {
                    "writesSqlite": True,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                    "broadcastEnabled": False,
                },
            }
    next_cursor = cursor_before + len(chunk)
    wrapped = next_cursor >= total
    if wrapped:
        next_cursor = 0
    next_cursor_pool_id = (
        ""
        if wrapped
        else (str(chunk[-1]).strip().lower() if chunk else "")
    )
    scanned_pool_count = total if wrapped else next_cursor
    remaining_pool_count = max(0, total - next_cursor)
    last_block_number = int(
        (refresh_result.get("liquidity") or {}).get("blockNumber") or 0
    )
    mode = "complete" if missing_after == 0 and wrapped else "scanning"
    save_v4_pool_liquidity_cursor(
        database,
        network=network,
        chain_id=chain_id,
        cursor=next_cursor,
        cursor_pool_id=next_cursor_pool_id,
        pool_count=total,
        scanned_pool_count=scanned_pool_count,
        remaining_pool_count=remaining_pool_count,
        batch_size=batch_size,
        last_scanned_at=_utc_now_iso(),
        last_block_number=last_block_number,
        mode=mode,
    )
    return {
        "ok": True,
        "mode": "pool_liquidity_worker",
        "phase": "cycle_complete" if wrapped else "incremental",
        "poolCount": total,
        "batchSize": len(chunk),
        "rpcBatchSize": rpc_batch_size,
        "dynamicIndexScanEnabled": dynamic_index_scan_enabled,
        "scopedPoolIds": scoped_pool_ids,
        "scopedPoolCount": total,
        "cursor": next_cursor,
        "cursorBefore": cursor_before,
        "cursorPoolId": next_cursor_pool_id,
        "cursorPoolIdBefore": cursor_pool_id_before,
        "missingBefore": missing_before,
        "missingAfter": missing_after,
        "scannedPoolCount": scanned_pool_count,
        "remainingPoolCount": remaining_pool_count,
        "lastBlockNumber": last_block_number,
        "refreshOk": True,
        "refreshErrors": [],
        "dynamicDiscovery": dynamic_discovery,
        "zeroLiquidityPoolCount": len(zero_liquidity_pool_ids),
        "zeroLiquidityCleanup": zero_cleanup,
        "safety": {
            "writesSqlite": True,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "broadcastEnabled": False,
        },
    }


def _pool_surface_freshness(
    database: str,
    *,
    network: str,
    chain_id: int,
    pool_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return the newest liquidity snapshot age for the requested scope.

    The worker pool only refreshes SQLite through this maintenance consumer,
    so the snapshot timestamps are the single source of truth for deciding
    whether an idle refresh is required even when route directions are already
    satisfied.
    """
    from core.db.storage_business_io import query

    selected_pool_ids = [str(pool_id).strip().lower() for pool_id in (pool_ids or []) if str(pool_id or "").strip()]
    where_clause = "WHERE network = ? AND chain_id = ? AND protocol = ?"
    params: list[object] = [network, chain_id, PROTOCOL]
    if selected_pool_ids:
        placeholders = ",".join("?" for _ in selected_pool_ids)
        where_clause += f" AND lower(pool_id) IN ({placeholders})"
        params.extend(selected_pool_ids)
    rows = query(
        database,
        f"""
        SELECT COUNT(*) AS snapshot_count,
               MAX(refreshed_at) AS refreshed_at,
               MAX(liquidity_read_at) AS liquidity_read_at,
               MAX(observed_at) AS observed_at
        FROM v4_exchange_pool_liquidity_snapshots
        {where_clause}
        """,
        tuple(params),
    )
    row = rows[0] if rows else {}
    refreshed_at = str(row.get("refreshed_at") or "").strip() or None
    liquidity_read_at = str(row.get("liquidity_read_at") or "").strip() or None
    observed_at = str(row.get("observed_at") or "").strip() or None
    latest = None
    for value in (refreshed_at, liquidity_read_at, observed_at):
        if not value:
            continue
        parsed = _parse_utc_timestamp(value)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    if latest is None:
        age_seconds = None
        stale = True
    else:
        age_seconds = max(0.0, (datetime.now(timezone.utc) - latest).total_seconds())
        stale = age_seconds > STALE_LIQUIDITY_TTL_SECONDS
    return {
        "snapshotCount": int(row.get("snapshot_count") or 0),
        "refreshedAt": refreshed_at,
        "liquidityReadAt": liquidity_read_at,
        "observedAt": observed_at,
        "latestSnapshotAt": latest.isoformat().replace("+00:00", "Z") if latest else None,
        "ageSeconds": age_seconds,
        "stale": stale,
        "ttlSeconds": STALE_LIQUIDITY_TTL_SECONDS,
    }


def _parse_utc_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_affected_anchors(
    database: str,
    *,
    network: str,
    chain_id: int,
    route_addresses: list[str],
) -> tuple[str, ...]:
    from core.db.storage_business_io import query

    addresses = {route_addresses[0]} if route_addresses else set()
    if not addresses:
        return ()
    placeholders = ",".join("?" for _ in addresses)
    rows = query(
        database,
        f"""
        SELECT token_symbol FROM chain_tokens
        WHERE network = ? AND chain_id = ? AND lower(token_address) IN ({placeholders})
        ORDER BY length(token_symbol) ASC, token_symbol ASC
        """,
        (network, chain_id, *addresses),
    )
    symbols = [
        str(row.get("token_symbol") or "").strip().upper()
        for row in rows
        if str(row.get("token_symbol") or "").strip()
    ]
    if symbols:
        return tuple(dict.fromkeys(symbols))
    return all_anchor_symbols(network, database_url=database)


def _run_route_maintenance(
    *,
    database: str,
    network: str,
    chain_id: int,
    route_addresses: list[str],
    pool_candidates: list[dict[str, Any]],
    direction_target: int,
) -> dict[str, Any]:
    """Backfill a route's six directions, then rebuild recommendations."""
    planned = _route_direction_plan(
        database,
        network=network,
        chain_id=chain_id,
        directed_pairs=_directed_pairs(route_addresses),
        direction_target=direction_target,
    )
    freshness = _pool_surface_freshness(
        database,
        network=network,
        chain_id=chain_id,
        pool_ids=_candidate_pool_ids(
            planned=planned,
            pool_candidates=pool_candidates,
        ),
    )
    before_snapshots = _route_pool_price_snapshots(
        database,
        network=network,
        chain_id=chain_id,
        route_addresses=route_addresses,
    )
    directions_satisfied = not planned["targets"]
    if directions_satisfied and not freshness["stale"]:
        return {
            "ok": True,
            "mode": "idle_maintenance",
            "phase": "directions_satisfied",
            "plan": {
                **planned,
                "directionTarget": int(direction_target),
                "satisfiedDirectionCount": planned["satisfiedDirectionCount"],
            },
            "freshness": freshness,
            "priceChange": {
                "basis": "no_pool_refresh",
                "triggered": False,
            },
            "triggerImmediateReview": False,
            "backfill": None,
            "import": None,
            "prune": None,
            "rebuildAnchors": [],
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    targets = planned["targets"]
    rpc_url = _robinhood_rpc_url(network, chain_id)
    if not rpc_url:
        return {
            "ok": False,
            "mode": "idle_maintenance",
            "phase": "rpc_unavailable",
            "stoppedReason": "no_rpc_url",
            "plan": {
                **planned,
                "directionTarget": int(direction_target),
            },
            "freshness": freshness,
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
            },
        }
    backfill_payload: dict[str, Any] | None = None
    import_result: dict[str, Any] | None = None
    pool_manager = _robinhood_pool_manager(network, chain_id)
    if targets:
        try:
            pool_index_module = _robinhood_v4_pool_index_module(network, chain_id)
            build_v4_directional_pool_backfill = (
                pool_index_module.build_v4_directional_pool_backfill
            )

            confirm = _confirm_backfill_pool_liquidity(
                network=network,
                rpc_url=rpc_url,
                pool_manager=pool_manager,
            )
            backfill_payload = build_v4_directional_pool_backfill(
                rpc_url=rpc_url,
                pool_manager=pool_manager,
                direction_targets=targets,
                direction_floor_block=int(planned.get("floorBlock") or 0),
                confirm_pool_liquidity=confirm,
                exclude_pool_ids=(
                    _candidate_pool_ids(
                        planned=planned,
                        pool_candidates=pool_candidates,
                    )
                ),
                chunk_size=100_000,
            )
        except Exception as exc:
            error_text = str(exc)
            lowered = error_text.lower()
            stopped_reason = (
                "rpc_rate_limited"
                if ("429" in error_text or "too many requests" in lowered or "rate limit" in lowered)
                else (
                    "rpc_block_range_limited"
                    if "exceeds limit" in lowered or "range" in lowered
                    else "rpc_backfill_failed"
                )
            )
            return {
                "ok": False,
                "mode": "idle_maintenance",
                "phase": "backfill_failed",
                "stoppedReason": stopped_reason,
                "error": error_text,
                "plan": {
                    **planned,
                    "directionTarget": int(direction_target),
                },
                "freshness": freshness,
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            }
        index_payload = {
            "schemaVersion": "robinhood-v4-idle-maintenance-index-v1",
            "network": network,
            "chainId": int(chain_id),
            "poolManager": pool_manager,
            "generatedAt": backfill_payload.get("generatedAt"),
            "generatedBlockNumber": backfill_payload.get("generatedBlockNumber"),
            "pools": backfill_payload.get("pools") or [],
            "poolKeys": backfill_payload.get("poolKeys") or [],
            "source": "idle_maintenance_reverse_directional_backfill",
        }
        try:
            from dex.tools.v4_rust_refresh import import_v4_evidence_rows

            import_result = import_v4_evidence_rows(
                database=database,
                network=network,
                chain_id=chain_id,
                index_payload=index_payload,
                liquidity_payload=None,
                protocol=PROTOCOL,
                replace=False,
            )
        except Exception as exc:
            return {
                "ok": False,
                "mode": "idle_maintenance",
                "phase": "import_failed",
                "stoppedReason": "v4_evidence_import_failed",
                "error": str(exc),
                "plan": {
                    **planned,
                    "directionTarget": int(direction_target),
                },
                "freshness": freshness,
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            }
    refresh_pool_ids = _candidate_pool_ids(
        planned=planned,
        pool_candidates=pool_candidates,
        backfill_payload=backfill_payload,
    )
    from dex.tools.refresh_robinhood_v4_live import refresh_v4_live

    try:
        refresh_result = refresh_v4_live(
            database=str(database),
            network=network,
            chain_id=chain_id,
            rpc_url=rpc_url or "",
            pool_manager=pool_manager,
            candidate_backfill=False,
            skip_index=True,
            skip_liquidity=False,
            skip_build=False,
            batch_size=100,
            pool_ids=refresh_pool_ids or None,
            prune_zero_liquidity=True,
            prune_unknown_liquidity=False,
            remove_json_evidence=False,
            replace_evidence=False,
        )
    except Exception as exc:
        error_text = str(exc)
        return {
            "ok": False,
            "mode": "idle_maintenance",
            "phase": "refresh_failed",
            "stoppedReason": "live_refresh_failed",
            "error": error_text,
            "import": import_result,
            "freshness": freshness,
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
            },
        }
    anchors = _resolve_affected_anchors(
        database,
        network=network,
        chain_id=chain_id,
        route_addresses=route_addresses,
    )
    rebuild_anchors: list[dict[str, Any]] = []
    for anchor_symbol in anchors:
        anchor_result = _rebuild_anchor(
            database,
            network=network,
            chain_id=chain_id,
            market_id=_robinhood_market_id(network, chain_id),
            anchor_symbol=anchor_symbol,
        )
        rebuild_anchors.append(anchor_result)
    after = _route_direction_plan(
        database,
        network=network,
        chain_id=chain_id,
        directed_pairs=_directed_pairs(route_addresses),
    )
    price_change = _price_change_trigger(
        database,
        network=network,
        chain_id=chain_id,
        route_addresses=route_addresses,
        before_snapshots=before_snapshots,
    )
    return {
        "ok": True,
        "mode": "idle_maintenance",
        "phase": "completed" if targets else "directions_satisfied_refreshed",
        "directionTarget": int(direction_target),
        "plan": planned,
        "freshness": freshness,
        "priceChange": price_change,
        "triggerImmediateReview": bool(price_change.get("triggered")),
        "afterPlan": {
            **after,
            "directionTarget": int(direction_target),
        },
        "backfill": (
            {
                "ok": bool(backfill_payload.get("pools")),
                "targets": backfill_payload.get("directionTargets"),
                "directionCounts": backfill_payload.get("directionCounts"),
                "neededDirectionCount": backfill_payload.get("neededDirectionCount"),
                "satisfiedDirectionCount": backfill_payload.get("satisfiedDirectionCount"),
                "stoppedReason": backfill_payload.get("stoppedReason"),
                "scannedSegmentCount": backfill_payload.get("scannedSegmentCount"),
                "initializeLogCount": backfill_payload.get("initializeLogCount"),
                "selectedPoolCount": backfill_payload.get("selectedPoolCount"),
            }
            if backfill_payload
            else None
        ),
        "import": import_result or refresh_result.get("import"),
        "refresh": {
            "ok": bool(refresh_result.get("ok", True)),
            "steps": refresh_result.get("steps") or [],
            "errors": refresh_result.get("errors") or [],
            "poolCount": refresh_result.get("poolCount"),
            "liquidityPoolCount": refresh_result.get("liquidityPoolCount"),
        },
        "refreshPoolIds": refresh_pool_ids,
        "refreshPoolCount": len(refresh_pool_ids),
        "rebuildAnchors": rebuild_anchors,
        "prune": (
            refresh_result.get("prune") or {}
        ),
        "safety": {
            "writesSqlite": True,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "broadcastEnabled": False,
        },
    }


def _full_scope_rebuild_if_empty(
    database: str,
    *,
    network: str,
    chain_id: int,
    market_id: str,
) -> dict[str, Any]:
    """Rebuild candidate/closed-loop rows when the scope has raw pools only.

    The Rust timer emits full-scope payloads without route addresses during
    idle periods.  If candidate/closed-loop tables are empty but raw
    ``v4_exchange_pools`` rows still exist, the full SQLite rebuild must run
    here instead of returning ``route_addresses_invalid``.
    """
    from core.db import builders
    from core.db.storage_business_io import query

    loop_row = query(
        database,
        """
        SELECT COUNT(*) AS count FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
        """,
        (network, chain_id, PROTOCOL, market_id),
    )
    if int((loop_row[0] or {}).get("count") or 0) > 0:
        return {
            "ok": True,
            "phase": "existing_closed_loops",
            "rebuilt": False,
            "result": None,
        }
    pool_row = query(
        database,
        """
        SELECT COUNT(*) AS count FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, PROTOCOL),
    )
    pool_count = int((pool_row[0] or {}).get("count") or 0)
    if pool_count == 0:
        return {
            "ok": False,
            "phase": "no_raw_pools",
            "rebuilt": False,
            "result": None,
        }
    try:
        result = builders.build_all_anchor_data(
            database,
            network=network,
            chain_id=chain_id,
            protocol=PROTOCOL,
            anchor_scope=market_id,
            market_id=market_id,
        )
    except Exception as exc:
        return {
            "ok": False,
            "phase": "rebuild_failed",
            "rebuilt": False,
            "result": None,
            "error": str(exc),
        }
    return {
        "ok": bool(result.get("ok", False)),
        "phase": "rebuild_completed" if result.get("ok") else "rebuild_returned_failure",
        "rebuilt": True,
        "result": result,
    }


def _full_scope_route(
    database: str,
    *,
    network: str,
    chain_id: int,
    market_id: str,
) -> tuple[list[str], dict[str, Any]]:
    """Pick one active closed loop from SQLite and return its address route."""
    from core.db.storage_business_io import query

    rows = query(
        database,
        """
        SELECT loop_key, anchor_symbol, path_addresses_json,
               recommended_first_priority
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
          AND status = 'active'
        ORDER BY recommended_first_priority DESC, updated_at ASC
        """,
        (network, chain_id, PROTOCOL, market_id),
    )
    for row in rows:
        raw = row.get("path_addresses_json")
        if raw:
            try:
                parsed = json.loads(str(raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, list):
                addresses = [
                    _text(address)
                    for address in parsed
                    if _text(address).startswith("0x") and len(_text(address)) == 42
                ]
                if _directed_pairs(addresses):
                    return addresses, row
        hop_rows = query(
            database,
            """
            SELECT hop_index, input_address, output_address
            FROM v4_closed_loop_hops
            WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
              AND loop_key = ?
            ORDER BY hop_index ASC
            """,
            (network, chain_id, PROTOCOL, market_id, row.get("loop_key")),
        )
        addresses: list[str] = []
        for hop in hop_rows:
            input_address = _text(hop.get("input_address"))
            output_address = _text(hop.get("output_address"))
            if (
                not (input_address.startswith("0x") and len(input_address) == 42)
                or not (output_address.startswith("0x") and len(output_address) == 42)
            ):
                addresses = []
                break
            if addresses and input_address != addresses[-1]:
                addresses = []
                break
            if not addresses:
                addresses.append(input_address)
            addresses.append(output_address)
        if _directed_pairs(addresses):
            return addresses, row
    return [], {}


def _run_full_scope_refresh(
    *,
    database: str,
    network: str,
    chain_id: int,
    market_id: str,
    direction_target: int,
    pool_liquidity_tick: bool = True,
) -> dict[str, Any]:
    """Maintain the whole chain scope when the timer payload has no route."""
    progress: list[dict[str, Any]] = []
    rebuild = _full_scope_rebuild_if_empty(
        database,
        network=network,
        chain_id=chain_id,
        market_id=market_id,
    )
    progress.append(
        {
            "step": "scope_rebuild",
            "phase": rebuild.get("phase"),
            "ok": bool(rebuild.get("ok")),
        }
    )
    if not rebuild.get("ok"):
        return {
            "ok": False,
            "mode": "full_scope_refresh",
            "phase": str(rebuild.get("phase") or "scope_rebuild_failed"),
            "error": rebuild.get("error"),
            "scopeRebuild": rebuild,
            "poolLiquidityWorker": None,
            "progress": progress,
            "routeAddresses": [],
            "safety": {
                "writesSqlite": bool(rebuild.get("rebuilt")),
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    route_addresses, loop_row = _full_scope_route(
        database,
        network=network,
        chain_id=chain_id,
        market_id=market_id,
    )
    if not route_addresses:
        liquidity_tick: dict[str, Any] | None = None
        known_token_addresses = _known_pool_token_addresses(
            database,
            network=network,
            chain_id=chain_id,
        )
        if pool_liquidity_tick and _candidate_eligible_missing_liquidity_count(
            database,
            network=network,
            chain_id=chain_id,
            known_token_addresses=known_token_addresses,
        ) > 0:
            liquidity_tick = _run_pool_liquidity_tick(
                database=database,
                network=network,
                chain_id=chain_id,
            )
            progress.append(
                {
                    "step": "pool_liquidity_worker",
                    "phase": liquidity_tick.get("phase"),
                    "ok": bool(liquidity_tick.get("ok")),
                    "cursorBefore": liquidity_tick.get("cursorBefore"),
                    "cursor": liquidity_tick.get("cursor"),
                    "missingBefore": liquidity_tick.get("missingBefore"),
                    "missingAfter": liquidity_tick.get("missingAfter"),
                }
            )
            after_tick_rebuild = _full_scope_rebuild_if_empty(
                database,
                network=network,
                chain_id=chain_id,
                market_id=market_id,
            )
            progress.append(
                {
                    "step": "scope_rebuild_after_liquidity",
                    "phase": after_tick_rebuild.get("phase"),
                    "ok": bool(after_tick_rebuild.get("ok")),
                }
            )
            if after_tick_rebuild.get("ok"):
                rebuild = {
                    **rebuild,
                    "afterLiquidityTick": after_tick_rebuild,
                }
                route_addresses, loop_row = _full_scope_route(
                    database,
                    network=network,
                    chain_id=chain_id,
                    market_id=market_id,
                )
        if not route_addresses:
            return {
                "ok": False,
                "mode": "full_scope_refresh",
                "phase": "no_closed_loop_route",
                "error": "no_active_closed_loop_after_scope_rebuild",
                "scopeRebuild": rebuild,
                "poolLiquidityWorker": liquidity_tick,
                "progress": progress,
                "routeAddresses": [],
                "safety": {
                    "writesSqlite": bool(
                        rebuild.get("rebuilt")
                        or (liquidity_tick or {}).get("safety", {}).get("writesSqlite")
                    ),
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                    "broadcastEnabled": False,
                },
            }
    try:
        result = _run_route_maintenance(
            database=database,
            network=network,
            chain_id=chain_id,
            route_addresses=route_addresses,
            pool_candidates=[],
            direction_target=direction_target,
        )
    except Exception as exc:
        return {
            "ok": False,
            "mode": "full_scope_refresh",
            "phase": "handler_error",
            "error": str(exc),
            "scopeRebuild": rebuild,
            "routeAddresses": route_addresses,
            "safety": {
                "writesSqlite": bool(rebuild.get("rebuilt")),
                "buildsTransaction": False,
                "broadcastsTransaction": False,
                "broadcastEnabled": False,
            },
        }
    result["mode"] = "full_scope_refresh"
    result["scopeRebuild"] = rebuild
    result["poolLiquidityWorker"] = liquidity_tick if "liquidity_tick" in locals() else None
    result["progress"] = progress
    result["scopeRoute"] = {
        "loopKey": loop_row.get("loop_key"),
        "anchorSymbol": loop_row.get("anchor_symbol"),
        "routeAddresses": route_addresses,
        "basis": "sqlite_closed_loops",
    }
    result.setdefault("routeAddresses", route_addresses)
    return result


def _rebuild_anchor(
    database: str,
    *,
    network: str,
    chain_id: int,
    market_id: str,
    anchor_symbol: str,
) -> dict[str, Any]:
    from core.db import builders

    candidate = builders.build_candidate_anchor(
        str(database),
        network=network,
        chain_id=chain_id,
        protocol=PROTOCOL,
        anchor_symbol=anchor_symbol,
    )
    observation = builders.build_market_observation_anchor(
        str(database),
        network=network,
        chain_id=chain_id,
        protocol=PROTOCOL,
        anchor_symbol=anchor_symbol,
    )
    closed = builders.build_closed_loops_anchor(
        str(database),
        network=network,
        chain_id=chain_id,
        protocol=PROTOCOL,
        market_id=market_id,
        anchor_symbol=anchor_symbol,
    )
    return {
        "anchorSymbol": anchor_symbol,
        "candidate": candidate,
        "observation": observation,
        "closedLoops": closed,
    }


def run_zero_liquidity_pool_cleanup(payload_json: str) -> str:
    """Rust maintenance callback entry point for dynamic-scan zero-liquidity pools.

    The consumer removes only the candidate/observation/loop references to
    explicitly empty pools.  Raw ``v4_exchange_pools`` rows are retained so a
    later positive liquidity snapshot makes the same physical pool eligible
    again without a full on-chain Initialize rescan.
    """
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return json.dumps(
            {
                "ok": False,
                "mode": "zero_liquidity_pool_cleanup",
                "error": f"invalid_json_payload: {exc}",
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    if not isinstance(payload, dict):
        return json.dumps(
            {
                "ok": False,
                "mode": "zero_liquidity_pool_cleanup",
                "error": "payload_must_be_json_object",
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    network = _text(payload.get("network") or ROBINHOOD_TESTNET_NETWORK)
    try:
        chain_id = int(payload.get("chain_id") or ROBINHOOD_TESTNET_CHAIN_ID)
    except (TypeError, ValueError):
        chain_id = 0
    if not _robinhood_scope_allowed(network, chain_id):
        return json.dumps(
            {
                "ok": False,
                "mode": "zero_liquidity_pool_cleanup",
                "chainId": chain_id,
                "network": network,
                "error": (
                    "unsupported_scope: zero-liquidity cleanup only accepts "
                    "robinhood 4663 or robinhood-testnet 46630"
                ),
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    database = str(absolute_database_path(payload.get("database_path") or ""))
    ensure_business_schema(database)
    zero_pool_ids = sorted(
        {
            _text(item)
            for item in (payload.get("zero_liquidity_pool_ids") or [])
            if _text(item).startswith("0x")
        }
    )
    if not zero_pool_ids:
        return json.dumps(
            {
                "ok": True,
                "mode": "zero_liquidity_pool_cleanup",
                "phase": "no_pools_to_clean",
                "cleanedPoolCount": 0,
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    cleaned: dict[str, Any] = {}
    try:
        with _write_lock(database):
            cleaned = _cleanup_zero_liquidity_pool_refs(
                database,
                network=network,
                chain_id=chain_id,
                zero_pool_ids=zero_pool_ids,
            )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "mode": "zero_liquidity_pool_cleanup",
                "phase": "cleanup_failed",
                "error": str(exc)[:500],
                "cleaned": cleaned,
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    report = {
        "schemaVersion": "robinhood-testnet-v4-zero-liquidity-cleanup-evidence-v1",
        "ok": True,
        "generatedAt": _utc_now_iso(),
        "network": network,
        "chainId": chain_id,
        "database": database,
        "mode": "zero_liquidity_pool_cleanup",
        "phase": "completed",
        "poolIds": zero_pool_ids,
        "cleanedPoolCount": len(zero_pool_ids),
        "cleaned": cleaned,
        "retained": {
            "v4_exchange_pools": len(zero_pool_ids),
            "v4_exchange_pool_liquidity_snapshots": (
                "retained_for_liquidity_recovery; raw pool rows and snapshots "
                "are never deleted by dynamic-scan cleanup"
            ),
        },
        "safety": {
            "writesSqlite": True,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "broadcastEnabled": False,
        },
    }
    _write_evidence(report)
    return json.dumps(report, ensure_ascii=False)


def run_idle_maintenance(payload_json: str) -> str:
    """Rust callback entry point; returns a JSON evidence string.

    ``mode=zero_liquidity_cleanup`` is delegated to
    :func:`run_zero_liquidity_pool_cleanup` so the same single maintenance
    consumer thread handles both SQLite write families.
    """
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return json.dumps(
            {
                "ok": False,
                "mode": "idle_maintenance",
                "error": f"invalid_json_payload: {exc}",
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    if (
        isinstance(payload, dict)
        and str(payload.get("mode") or "").lower() == "zero_liquidity_cleanup"
    ):
        return run_zero_liquidity_pool_cleanup(payload_json)
    if not isinstance(payload, dict):
        return json.dumps(
            {
                "ok": False,
                "mode": "idle_maintenance",
                "error": "payload_must_be_json_object",
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    network = _text(payload.get("network") or ROBINHOOD_TESTNET_NETWORK)
    try:
        chain_id = int(payload.get("chain_id") or ROBINHOOD_TESTNET_CHAIN_ID)
    except (TypeError, ValueError):
        chain_id = 0
    if not _robinhood_scope_allowed(network, chain_id):
        return json.dumps(
            {
                "ok": False,
                "mode": "idle_maintenance",
                "chainId": chain_id,
                "network": network,
                "error": (
                    "unsupported_scope: idle maintenance only accepts "
                    "robinhood 4663 or robinhood-testnet 46630"
                ),
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                },
            },
            ensure_ascii=False,
        )
    database = str(
        absolute_database_path(payload.get("database_path") or "")
    )
    ensure_business_schema(database)
    try:
        from dex.runtime.v4_maintenance_workers import (
            worker_stats,
        )

        maintenance_workers = worker_stats(
            database_url=database,
            network=network,
            chain_id=chain_id,
        )
    except Exception as exc:
        maintenance_workers = {
            "ok": False,
            "error": str(exc)[:200],
        }
    route_addresses = _route_addresses(payload)
    directed_pairs = _directed_pairs(route_addresses)
    report = {
        "schemaVersion": "robinhood-testnet-v4-idle-maintenance-evidence-v1",
        "generatedAt": _utc_now_iso(),
        "network": network,
        "chainId": chain_id,
        "database": database,
        "scope": {
            "routeAddresses": route_addresses,
            "directions": list(directed_pairs),
            "directionTarget": int(
                payload.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS
            ),
        },
        "source": "rust_closed_loop_sqlite_maintenance_consumer",
        "maintenanceWorkers": maintenance_workers,
    }
    if not directed_pairs:
        try:
            result = _run_full_scope_refresh(
                database=database,
                network=network,
                chain_id=chain_id,
                market_id=_robinhood_market_id(network, chain_id),
                direction_target=int(
                    payload.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS
                ),
            )
        except Exception as exc:
            result = {
                "ok": False,
                "mode": "full_scope_refresh",
                "phase": "handler_error",
                "error": str(exc),
                "safety": {
                    "writesSqlite": False,
                    "buildsTransaction": False,
                    "broadcastsTransaction": False,
                    "broadcastEnabled": False,
                },
            }
        report.update(result)
        _write_evidence(report)
        return json.dumps(report, ensure_ascii=False)
    try:
        result = _run_route_maintenance(
            database=database,
            network=network,
            chain_id=chain_id,
            route_addresses=route_addresses,
            pool_candidates=payload.get("pool_candidates") or [],
            direction_target=int(
                payload.get("direction_target") or MAX_DIRECTION_CANDIDATE_POOLS
            ),
        )
    except Exception as exc:
        result = {
            "ok": False,
            "mode": "idle_maintenance",
            "phase": "handler_error",
            "error": str(exc),
            "safety": {
                "writesSqlite": False,
                "buildsTransaction": False,
                "broadcastsTransaction": False,
            },
        }
    report.update(result)
    _write_evidence(report)
    return json.dumps(report, ensure_ascii=False)


def _write_evidence(report: dict[str, Any]) -> Path:
    network = str(report.get("network") or ROBINHOOD_TESTNET_NETWORK).strip().lower()
    evidence_root = SRC_ROOT / "evidence" / network / "idle_maintenance"
    report_path = evidence_root / _stamp() / "report.json"
    _write_json(report_path, report)
    return report_path


__all__ = [
    "run_idle_maintenance",
    "run_zero_liquidity_pool_cleanup",
]
