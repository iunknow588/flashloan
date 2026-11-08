"""Read-only V4 scope index for candidate/observation/closed-loop lookup.

The maintenance pipeline already persists its own incremental cursors in
``chain_business_initialization``.  This module adds a read-only projection on
top of those rows: given a pool id, it reports which candidate anchors,
observation rows and closed loops touch that pool without materialising a
second table.  A CLI can verify the projection against the live rows; it never
rewrites the business tables.
"""

from __future__ import annotations

import json
from typing import Any

from core.db.storage_business_io import query
from core.db.storage_chain_core import load_initialization
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)

SCOPE_INDEXER_DOMAIN = "v4_scope_indexer"


def _norm_pool_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _json_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_norm_pool_id(item) for item in value if str(item or "").strip()]
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_norm_pool_id(item) for item in parsed if str(item or "").strip()]


def scope_params(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
) -> tuple[str, list[Any]]:
    return (
        "network = ? AND chain_id = ? AND protocol = ? AND anchor_scope = ?",
        [
            str(network).strip().lower(),
            int(chain_id),
            str(protocol).strip().lower(),
            str(anchor_scope).strip().lower(),
        ],
    )


def query_pool_scope(
    database_url: str,
    *,
    pool_id: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    protocol: str = "uniswap_v4",
    market_id: str = ROBINHOOD_TESTNET_MARKET_ID,
) -> dict[str, Any]:
    """Return candidate/observation/closed-loop ownership for one pool id."""
    normalized = _norm_pool_id(pool_id)
    if not normalized:
        return {
            "ok": False,
            "error": "pool_id_required",
            "poolId": None,
        }
    where, params = scope_params(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_scope=market_id,
    )
    # Candidate pools store one row per anchor; a pool id can therefore appear
    # under multiple anchors.  Observation rows keep pool ids in a JSON list.
    candidate_rows = query(
        database_url,
        f"""
        SELECT anchor_symbol, address_pair, direction_key, pool_id, token0, token1,
               is_first_hop_recommended, is_third_hop_recommended
        FROM dex_candidate_pools
        WHERE {where} AND pool_id = ?
        """,
        [*params, normalized],
    )
    observation_rows = query(
        database_url,
        f"""
        SELECT anchor_symbol, direction_key, token_x_address, token_y_address,
               address_pair, pool_ids_json, optimal_pool_id
        FROM market_observation_yz_pairs
        WHERE {where} AND (optimal_pool_id = ? OR pool_ids_json LIKE ?)
        """,
        [*params, normalized, f'%"{normalized}"%'],
    )
    loop_rows = query(
        database_url,
        f"""
        SELECT market_id, loop_key, anchor_symbol, path_text, pool_ids_json
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND market_id = ? AND (pool_ids_json LIKE ? OR EXISTS (
            SELECT 1 FROM v4_closed_loop_hops h
            WHERE h.network = v4_closed_loops.network
              AND h.chain_id = v4_closed_loops.chain_id
              AND h.protocol = v4_closed_loops.protocol
              AND h.market_id = v4_closed_loops.market_id
              AND h.loop_key = v4_closed_loops.loop_key
              AND h.pool_id = ?
          ))
        """,
        [str(network).strip().lower(), int(chain_id), str(protocol).strip().lower(), str(market_id).strip().lower(), f'%"{normalized}"%', normalized],
    )
    observation_rows = [
        {
            **row,
            "poolIds": _json_list(row.get("pool_ids_json")),
        }
        for row in observation_rows
    ]
    loop_rows = [
        {
            **row,
            "poolIds": _json_list(row.get("pool_ids_json")),
        }
        for row in loop_rows
    ]
    return {
        "ok": True,
        "poolId": normalized,
        "network": str(network).strip().lower(),
        "chainId": int(chain_id),
        "protocol": str(protocol).strip().lower(),
        "candidateRows": candidate_rows,
        "observationRows": observation_rows,
        "closedLoops": loop_rows,
        "counts": {
            "candidate": len(candidate_rows),
            "observation": len(observation_rows),
            "closedLoop": len(loop_rows),
        },
    }


def verify_pool_scope(
    database_url: str,
    *,
    pool_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the read-only scope query and report a deterministic result."""
    scope = query_pool_scope(database_url, pool_id=pool_id, **kwargs)
    scope["verifiedAt"] = None
    return scope


def load_scope_indexer_cursor(
    database_url: str,
    *,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    row = load_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=SCOPE_INDEXER_DOMAIN,
    )
    if not isinstance(row, dict):
        return {
            "mode": "idle",
            "refreshed_at": None,
            "error": None,
        }
    try:
        completeness = json.loads(str(row.get("completeness_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        completeness = {}
    return {
        "mode": str(completeness.get("mode") or "idle"),
        "cursor": int(completeness.get("cursor") or 0),
        "pool_count": int(completeness.get("pool_count") or 0),
        "verified_pool_count": int(completeness.get("verified_pool_count") or 0),
        "refreshed_at": row.get("refreshed_at"),
        "error": (row.get("error_json") or {}).get("message") if isinstance(row.get("error_json"), dict) else None,
    }


__all__ = [
    "SCOPE_INDEXER_DOMAIN",
    "load_scope_indexer_cursor",
    "query_pool_scope",
    "scope_params",
    "verify_pool_scope",
]
