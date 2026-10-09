"""V4 closed loop, hop and optimization storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import executemany, query


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _int_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def replace_v4_closed_loop_tables(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loops: list[dict[str, Any]],
    hops: list[dict[str, Any]],
    optimizations: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    now = _stamp()
    loop_rows = []
    for loop in loops:
        loop_key = _text(loop.get("loop_key") or loop.get("loopKey"))
        anchor = _text(loop.get("anchor_address") or loop.get("anchorAddress"))
        if not loop_key or not anchor:
            continue
        loop_rows.append(
            (
                network, chain_id, protocol, market_id, loop_key, anchor,
                loop.get("anchor_symbol") or "",
                loop.get("path_text"),
                _json_dumps(loop.get("path_addresses") or loop.get("path_addresses_json") or []),
                _json_dumps(loop.get("path_symbols") or loop.get("path_symbols_json") or []),
                loop.get("pool_path_key"),
                _json_dumps(loop.get("pool_ids") or loop.get("pool_ids_json") or []),
                _json_dumps(loop.get("hop_candidate_counts") or loop.get("hop_candidate_counts_json") or {}),
                _int_none(loop.get("hop_candidate_combination_count")),
                loop.get("recommendation_basis"),
                _bool(loop.get("recommended_first_priority")),
                loop.get("status") or "active",
                loop.get("source"),
                now, now,
            )
        )
    hop_rows = []
    for hop in hops:
        loop_key = _text(hop.get("loop_key") or hop.get("loopKey"))
        pool_id = _text(hop.get("pool_id") or hop.get("poolId"))
        base_pool_id = _text(hop.get("base_pool_id") or hop.get("basePoolId"))
        if not loop_key or not pool_id:
            continue
        hop_rows.append(
            (
                network, chain_id, protocol, market_id, loop_key,
                _int_none(hop.get("hop_index") or hop.get("hopIndex")),
                hop.get("hop_name") or "",
                _text(hop.get("input_address") or hop.get("inputAddress")),
                _text(hop.get("output_address") or hop.get("outputAddress")),
                pool_id, base_pool_id or pool_id,
                hop.get("pool_selection_basis") or hop.get("poolSelectionBasis"),
                hop.get("optimization_id"),
                _bool(hop.get("temporary_override")),
                now, now,
            )
        )
    optimization_rows = []
    for opt in optimizations or []:
        loop_key = _text(opt.get("loop_key") or opt.get("loopKey"))
        optimized_pool = _text(opt.get("optimized_pool_id") or opt.get("optimizedPoolId"))
        if not loop_key or not optimized_pool:
            continue
        optimization_rows.append(
            (
                network, chain_id, protocol, market_id, loop_key,
                _int_none(opt.get("hop_index") or opt.get("hopIndex")),
                _text(opt.get("optimization_id")),
                _text(opt.get("original_pool_id")),
                optimized_pool,
                opt.get("optimization_basis"),
                _float_none(opt.get("expected_price_improvement")),
                opt.get("status") or "temporary",
                now, opt.get("expires_at"), now,
            )
        )
    if loop_rows:
        executemany(
            database_url,
            """
            INSERT INTO v4_closed_loops (
                network, chain_id, protocol, market_id, loop_key,
                anchor_address, anchor_symbol, path_text, path_addresses_json,
                path_symbols_json, pool_path_key, pool_ids_json,
                hop_candidate_counts_json, hop_candidate_combination_count,
                recommendation_basis, recommended_first_priority, status,
                source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, market_id, loop_key)
            DO UPDATE SET anchor_address = excluded.anchor_address,
                anchor_symbol = excluded.anchor_symbol,
                path_text = excluded.path_text,
                path_addresses_json = excluded.path_addresses_json,
                path_symbols_json = excluded.path_symbols_json,
                pool_path_key = excluded.pool_path_key,
                pool_ids_json = excluded.pool_ids_json,
                hop_candidate_counts_json = excluded.hop_candidate_counts_json,
                hop_candidate_combination_count = excluded.hop_candidate_combination_count,
                recommendation_basis = excluded.recommendation_basis,
                recommended_first_priority = excluded.recommended_first_priority,
                status = excluded.status,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            loop_rows,
        )
    if hop_rows:
        executemany(
            database_url,
            """
            INSERT INTO v4_closed_loop_hops (
                network, chain_id, protocol, market_id, loop_key, hop_index,
                hop_name, input_address, output_address, pool_id, base_pool_id,
                pool_selection_basis, optimization_id, temporary_override,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, market_id, loop_key, hop_index)
            DO UPDATE SET hop_name = excluded.hop_name,
                input_address = excluded.input_address,
                output_address = excluded.output_address,
                pool_id = excluded.pool_id,
                base_pool_id = excluded.base_pool_id,
                pool_selection_basis = excluded.pool_selection_basis,
                optimization_id = excluded.optimization_id,
                temporary_override = excluded.temporary_override,
                updated_at = excluded.updated_at
            """,
            hop_rows,
        )
    if optimization_rows:
        executemany(
            database_url,
            """
            INSERT INTO v4_closed_loop_optimizations (
                network, chain_id, protocol, market_id, loop_key, hop_index,
                optimization_id, original_pool_id, optimized_pool_id,
                optimization_basis, expected_price_improvement, status,
                created_at, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, market_id, loop_key, hop_index, optimization_id)
            DO UPDATE SET original_pool_id = excluded.original_pool_id,
                optimized_pool_id = excluded.optimized_pool_id,
                optimization_basis = excluded.optimization_basis,
                expected_price_improvement = excluded.expected_price_improvement,
                status = excluded.status,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            optimization_rows,
        )
    return {"loops": len(loop_rows), "hops": len(hop_rows), "optimizations": len(optimization_rows)}


def load_v4_closed_loop(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
) -> dict[str, Any]:
    loops = query(
        database_url,
        """
        SELECT network, chain_id, protocol, market_id, loop_key,
               anchor_address, anchor_symbol, path_text, path_addresses_json,
               path_symbols_json, pool_path_key, pool_ids_json,
               hop_candidate_counts_json, hop_candidate_combination_count,
               recommendation_basis, recommended_first_priority, status,
               source, created_at, updated_at
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
        ORDER BY loop_key
        """,
        (network, chain_id, protocol, market_id),
    )
    hops = query(
        database_url,
        """
        SELECT network, chain_id, protocol, market_id, loop_key, hop_index,
               hop_name, input_address, output_address, pool_id, base_pool_id,
               pool_selection_basis, optimization_id, temporary_override,
               created_at, updated_at
        FROM v4_closed_loop_hops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
        ORDER BY loop_key, hop_index
        """,
        (network, chain_id, protocol, market_id),
    )
    optimizations = query(
        database_url,
        """
        SELECT network, chain_id, protocol, market_id, loop_key, hop_index,
               optimization_id, original_pool_id, optimized_pool_id,
               optimization_basis, expected_price_improvement, status,
               created_at, expires_at, updated_at
        FROM v4_closed_loop_optimizations
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
        ORDER BY loop_key, hop_index, optimization_id
        """,
        (network, chain_id, protocol, market_id),
    )
    return {"loops": loops, "hops": hops, "optimizations": optimizations}


__all__ = [
    "load_v4_closed_loop",
    "replace_v4_closed_loop_tables",
]
