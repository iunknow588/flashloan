"""DEX candidate anchor, pair, pool and recommendation storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import executemany, query


MAX_USABLE_CANDIDATE_FIXED_FEE = 3000


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _bool_tri(value: Any) -> int | None:
    """Return 1/0 for explicit evidence and None when the row has no signal."""
    if value is None:
        return None
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes"} else 0
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


def _candidate_fixed_fee_too_high(fee: int | None) -> bool:
    return bool(fee is not None and fee > MAX_USABLE_CANDIDATE_FIXED_FEE)


def _float_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_loads(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _coalesce_candidate_pool_dicts(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge direction-flavoured pool payloads into one physical pool row."""
    merged: dict[str, dict[str, Any]] = {}
    for pool in pools:
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        if not pool_id:
            continue
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        existing = merged.get(pool_id)
        if existing is None:
            existing = dict(pool)
            existing["pool_id"] = pool_id
            if token0 and token1:
                canonical = f"{token0}:{token1}"
                existing["address_pair"] = canonical
                existing["direction_key"] = canonical
                existing["direction_address_pair"] = canonical
            merged[pool_id] = existing
            continue
        existing["is_first_hop_recommended"] = _bool(
            existing.get("is_first_hop_recommended")
            or existing.get("isFirstHopRecommended")
            or pool.get("is_first_hop_recommended")
            or pool.get("isFirstHopRecommended")
        )
        existing["is_third_hop_recommended"] = _bool(
            existing.get("is_third_hop_recommended")
            or existing.get("isThirdHopRecommended")
            or pool.get("is_third_hop_recommended")
            or pool.get("isThirdHopRecommended")
        )
        hop_recommendation = _json_loads(
            existing.get("hop_recommendation")
            or existing.get("hop_recommendation_json")
            or {}
        )
        other_hop_recommendation = _json_loads(
            pool.get("hop_recommendation")
            or pool.get("hop_recommendation_json")
            or {}
        )
        if isinstance(hop_recommendation, dict) and isinstance(other_hop_recommendation, dict):
            merged_hop = {
                "first": bool(hop_recommendation.get("first") or other_hop_recommendation.get("first")),
                "third": bool(hop_recommendation.get("third") or other_hop_recommendation.get("third")),
            }
            existing["hop_recommendation"] = merged_hop
            existing["hop_recommendation_json"] = merged_hop
        optimal_roles = _json_loads(
            existing.get("optimal_roles")
            or existing.get("optimal_roles_json")
            or {}
        )
        other_optimal_roles = _json_loads(
            pool.get("optimal_roles")
            or pool.get("optimal_roles_json")
            or {}
        )
        if isinstance(optimal_roles, dict) and isinstance(other_optimal_roles, dict):
            merged_roles = {
                "yc": bool(optimal_roles.get("yc") or other_optimal_roles.get("yc")),
                "zc": bool(optimal_roles.get("zc") or other_optimal_roles.get("zc")),
            }
            existing["optimal_roles"] = merged_roles
            existing["optimal_roles_json"] = merged_roles
        if token0 and token1:
            canonical = f"{token0}:{token1}"
            existing["address_pair"] = canonical
            existing["direction_key"] = canonical
            existing["direction_address_pair"] = canonical
    return list(merged.values())


def replace_dex_candidate_tables(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
    anchor_symbol: str,
    tokens: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
) -> dict[str, int]:
    now = _stamp()
    token_rows = []
    for token in tokens:
        address = _text(token.get("token_address") or token.get("address"))
        if not address:
            continue
        token_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                token.get("token_role") or "yc_zc", address,
                _int_none(token.get("pool_count") or 0) or 0,
                _bool(token.get("has_yc_optimal_pool")),
                _bool(token.get("has_zc_optimal_pool")),
                _text(token.get("yc_optimal_pool_id")),
                _text(token.get("zc_optimal_pool_id")),
                _json_dumps(token.get("yc_backup_pool_ids") or []),
                _json_dumps(token.get("zc_backup_pool_ids") or []),
                _bool(token.get("yc_zc_same_set", True)),
                now, now,
            )
        )
    pair_rows = []
    for pair in pairs:
        anchor = _text(pair.get("anchor_address") or pair.get("anchorAddress"))
        counter = _text(pair.get("counter_address") or pair.get("counterAddress"))
        if not anchor or not counter:
            continue
        direction_key = _text(pair.get("direction_key") or pair.get("directionKey"))
        direction_key = direction_key or f"{anchor}:{counter}"
        address_pair = direction_key
        pair_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor, counter, address_pair, direction_key,
                _int_none(pair.get("pool_count") or 0) or 0,
                _int_none(pair.get("fixed_fee_liquid_pool_count") or 0) or 0,
                _int_none(pair.get("priced_pool_count") or 0) or 0,
                _text(pair.get("yc_optimal_pool_id")),
                _text(pair.get("zc_optimal_pool_id")),
                _json_dumps(pair.get("price_reference") or pair.get("price_reference_json") or {}),
                _json_dumps(pair.get("yc_backup_pool_ids") or []),
                _json_dumps(pair.get("zc_backup_pool_ids") or []),
                pair.get("price_outlier_filter"),
                now, now,
            )
        )
    pool_rows = []
    for pool in _coalesce_candidate_pool_dicts(pools):
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        direction_key = _text(pool.get("direction_key") or pool.get("directionKey"))
        direction_key = direction_key or f"{token0}:{token1}"
        address_pair = direction_key
        direction_address_pair = _text(
            pool.get("direction_address_pair") or pool.get("directionAddressPair")
        ) or direction_key
        fee = _int_none(pool.get("fee"))
        dynamic_fee = bool(fee is not None and fee & 8388608)
        special_fee = bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        if _candidate_fixed_fee_too_high(fee):
            continue
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        pool_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id,
                _json_dumps(pool.get("pool_key") or pool.get("poolKey") or {}),
                _text(pool.get("pool_manager")),
                token0, token1, fee, fee_label,
                _bool(dynamic_fee), _bool(special_fee),
                _int_none(pool.get("tick_spacing") or pool.get("tickSpacing")),
                _text(pool.get("hooks")),
                pool.get("sqrt_price_x96") or pool.get("sqrtPriceX96") or "",
                _int_none(pool.get("tick")),
                _bool_tri(
                    pool.get("has_liquidity")
                    if pool.get("has_liquidity") is not None
                    else pool.get("hasLiquidity")
                ),
                pool.get("liquidity") or "",
                _float_none(pool.get("liquidity_index") or pool.get("liquidityIndex")),
                _float_none(pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd")),
                _json_dumps(pool.get("balances") or pool.get("balances_json") or {}),
                pool.get("depth_status") or pool.get("depthStatus") or "",
                _json_dumps(pool.get("depth_band") or pool.get("depthBand") or {}),
                _bool_tri(
                    pool.get("usable_for_observation")
                    if pool.get("usable_for_observation") is not None
                    else pool.get("usableForObservation")
                ),
                _bool(pool.get("is_first_hop_recommended") or pool.get("isFirstHopRecommended")),
                _bool(pool.get("is_third_hop_recommended") or pool.get("isThirdHopRecommended")),
                _json_dumps(pool.get("hop_recommendation") or pool.get("hop_recommendation_json") or {}),
                _json_dumps(pool.get("optimal_roles") or pool.get("optimal_roles_json") or {}),
                pool.get("liquidity_read_at") or pool.get("liquidityReadAt"),
                _int_none(pool.get("liquidity_block_number") or pool.get("liquidityBlockNumber")),
                now, now,
            )
        )
    recommendation_rows = []
    for rec in recommendations:
        counter = _text(rec.get("counter_address") or rec.get("counterAddress"))
        pool_id = _text(rec.get("pool_id") or rec.get("poolId") or rec.get("pool"))
        hop = _text(rec.get("hop"))
        direction_key = _text(rec.get("direction_key") or rec.get("directionKey"))
        if not counter or not pool_id or not hop:
            continue
        if not direction_key:
            continue
        recommendation_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                counter, direction_key, hop, _int_none(rec.get("rank") or 0) or 0, pool_id,
                _float_none(rec.get("depth_score") or rec.get("depthScore")),
                _float_none(rec.get("price")),
                _int_none(rec.get("fee")),
                rec.get("selection_basis") or rec.get("selectionBasis"),
                now, now,
            )
        )
    if token_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_anchor_tokens (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                token_role, token_address, pool_count, has_yc_optimal_pool,
                has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                yc_zc_same_set, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address)
            DO UPDATE SET pool_count = excluded.pool_count,
                has_yc_optimal_pool = excluded.has_yc_optimal_pool,
                has_zc_optimal_pool = excluded.has_zc_optimal_pool,
                yc_optimal_pool_id = excluded.yc_optimal_pool_id,
                zc_optimal_pool_id = excluded.zc_optimal_pool_id,
                yc_backup_pool_ids_json = excluded.yc_backup_pool_ids_json,
                zc_backup_pool_ids_json = excluded.zc_backup_pool_ids_json,
                yc_zc_same_set = excluded.yc_zc_same_set,
                updated_at = excluded.updated_at
            """,
            token_rows,
        )
    if pair_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_pool_pairs (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor_address, counter_address, address_pair, direction_key, pool_count,
                fixed_fee_liquid_pool_count, priced_pool_count,
                yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                price_outlier_filter, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
            DO UPDATE SET pool_count = excluded.pool_count,
                fixed_fee_liquid_pool_count = excluded.fixed_fee_liquid_pool_count,
                priced_pool_count = excluded.priced_pool_count,
                yc_optimal_pool_id = excluded.yc_optimal_pool_id,
                zc_optimal_pool_id = excluded.zc_optimal_pool_id,
                yc_backup_pool_ids_json = excluded.yc_backup_pool_ids_json,
                zc_backup_pool_ids_json = excluded.zc_backup_pool_ids_json,
                price_reference_json = excluded.price_reference_json,
                price_outlier_filter = excluded.price_outlier_filter,
                updated_at = excluded.updated_at
            """,
            pair_rows,
        )
    if pool_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
                token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                liquidity_index, estimated_tvl_usd, balances_json, depth_status,
                depth_band_json, usable_for_observation, is_first_hop_recommended,
                is_third_hop_recommended, hop_recommendation_json,
                optimal_roles_json, liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET pool_key_json = excluded.pool_key_json,
                pool_manager = excluded.pool_manager,
                token0 = excluded.token0,
                token1 = excluded.token1,
                fee = excluded.fee,
                fee_label = excluded.fee_label,
                dynamic_fee = excluded.dynamic_fee,
                special_fee = excluded.special_fee,
                tick_spacing = excluded.tick_spacing,
                hooks = excluded.hooks,
                sqrt_price_x96 = excluded.sqrt_price_x96,
                tick = excluded.tick,
                has_liquidity = CASE
                    WHEN excluded.has_liquidity IS NULL
                    THEN dex_candidate_pools.has_liquidity
                    ELSE excluded.has_liquidity
                END,
                liquidity = CASE
                    WHEN excluded.liquidity IS NULL OR excluded.liquidity = ''
                    THEN dex_candidate_pools.liquidity
                    ELSE excluded.liquidity
                END,
                liquidity_index = COALESCE(
                    excluded.liquidity_index,
                    dex_candidate_pools.liquidity_index
                ),
                estimated_tvl_usd = COALESCE(
                    excluded.estimated_tvl_usd,
                    dex_candidate_pools.estimated_tvl_usd
                ),
                balances_json = COALESCE(
                    excluded.balances_json,
                    dex_candidate_pools.balances_json
                ),
                depth_status = CASE
                    WHEN excluded.depth_status IS NULL OR excluded.depth_status = ''
                    THEN dex_candidate_pools.depth_status
                    ELSE excluded.depth_status
                END,
                depth_band_json = COALESCE(
                    excluded.depth_band_json,
                    dex_candidate_pools.depth_band_json
                ),
                usable_for_observation = CASE
                    WHEN excluded.usable_for_observation IS NULL
                    THEN dex_candidate_pools.usable_for_observation
                    ELSE excluded.usable_for_observation
                END,
                is_first_hop_recommended = excluded.is_first_hop_recommended,
                is_third_hop_recommended = excluded.is_third_hop_recommended,
                hop_recommendation_json = excluded.hop_recommendation_json,
                optimal_roles_json = excluded.optimal_roles_json,
                liquidity_read_at = COALESCE(
                    excluded.liquidity_read_at,
                    dex_candidate_pools.liquidity_read_at
                ),
                liquidity_block_number = COALESCE(
                    excluded.liquidity_block_number,
                    dex_candidate_pools.liquidity_block_number
                ),
                updated_at = excluded.updated_at
            """,
            pool_rows,
        )
    if recommendation_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_pool_recommendations (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                counter_address, direction_key, hop, rank, pool_id, depth_score, price, fee,
                selection_basis, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
            DO UPDATE SET rank = excluded.rank,
                depth_score = excluded.depth_score,
                price = excluded.price,
                fee = excluded.fee,
                selection_basis = excluded.selection_basis,
                updated_at = excluded.updated_at
            """,
            recommendation_rows,
        )
    return {
        "tokens": len(token_rows),
        "pairs": len(pair_rows),
        "pools": len(pool_rows),
        "recommendations": len(recommendation_rows),
    }


def replace_dex_candidate_abnormal_tables(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_scope: str,
    anchor_symbol: str,
    tokens: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    pools: list[dict[str, Any]],
) -> dict[str, int]:
    """Write pools that do not qualify for the normal YC/ZC candidate set.

    Abnormal here means dynamic fee, > 3000 fixed fee, no fee, or a non-empty
    hook.  These rows are stored for candidate-page diagnostics only; later DEX
    overview and closed-loop construction must keep using the normal tables.
    """
    now = _stamp()
    token_rows = []
    for token in tokens:
        address = _text(token.get("token_address") or token.get("address"))
        if not address:
            continue
        token_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                token.get("token_role") or "yc_zc_abnormal", address,
                _int_none(token.get("pool_count") or 0) or 0,
                _bool(token.get("has_yc_optimal_pool")),
                _bool(token.get("has_zc_optimal_pool")),
                _text(token.get("yc_optimal_pool_id")),
                _text(token.get("zc_optimal_pool_id")),
                _bool(token.get("yc_zc_same_set", True)),
                now, now,
            )
        )
    pair_rows = []
    for pair in pairs:
        anchor = _text(pair.get("anchor_address") or pair.get("anchorAddress"))
        counter = _text(pair.get("counter_address") or pair.get("counterAddress"))
        if not anchor or not counter:
            continue
        direction_key = _text(pair.get("direction_key") or pair.get("directionKey"))
        direction_key = direction_key or f"{anchor}:{counter}"
        address_pair = direction_key
        pair_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor, counter, address_pair, direction_key,
                _int_none(pair.get("pool_count") or 0) or 0,
                _int_none(pair.get("fixed_fee_liquid_pool_count") or 0) or 0,
                _int_none(pair.get("priced_pool_count") or 0) or 0,
                _text(pair.get("yc_optimal_pool_id")),
                _text(pair.get("zc_optimal_pool_id")),
                _json_dumps(pair.get("price_reference") or pair.get("price_reference_json") or {}),
                pair.get("price_outlier_filter") or "abnormal_pool",
                now, now,
            )
        )
    pool_rows = []
    for pool in _coalesce_candidate_pool_dicts(pools):
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        direction_key = _text(pool.get("direction_key") or pool.get("directionKey"))
        direction_key = direction_key or f"{token0}:{token1}"
        address_pair = direction_key
        direction_address_pair = _text(
            pool.get("direction_address_pair") or pool.get("directionAddressPair")
        ) or direction_key
        fee = _int_none(pool.get("fee"))
        dynamic_fee = bool(fee is not None and fee & 8388608)
        special_fee = bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        pool_rows.append(
            (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id,
                _json_dumps(pool.get("pool_key") or pool.get("poolKey") or {}),
                _text(pool.get("pool_manager")),
                token0, token1, fee, fee_label,
                _bool(dynamic_fee), _bool(special_fee),
                _int_none(pool.get("tick_spacing") or pool.get("tickSpacing")),
                _text(pool.get("hooks")),
                pool.get("sqrt_price_x96") or pool.get("sqrtPriceX96") or "",
                _int_none(pool.get("tick")),
                _bool_tri(
                    pool.get("has_liquidity")
                    if pool.get("has_liquidity") is not None
                    else pool.get("hasLiquidity")
                ),
                pool.get("liquidity") or "",
                _float_none(pool.get("liquidity_index") or pool.get("liquidityIndex")),
                _float_none(pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd")),
                _json_dumps(pool.get("balances") or pool.get("balances_json") or {}),
                pool.get("depth_status") or pool.get("depthStatus") or "",
                _json_dumps(pool.get("depth_band") or pool.get("depthBand") or {}),
                _bool_tri(
                    pool.get("usable_for_observation")
                    if pool.get("usable_for_observation") is not None
                    else pool.get("usableForObservation")
                ),
                _bool(pool.get("is_first_hop_recommended") or pool.get("isFirstHopRecommended")),
                _bool(pool.get("is_third_hop_recommended") or pool.get("isThirdHopRecommended")),
                _json_dumps(pool.get("hop_recommendation") or pool.get("hop_recommendation_json") or {}),
                _json_dumps(pool.get("optimal_roles") or pool.get("optimal_roles_json") or {}),
                pool.get("liquidity_read_at") or pool.get("liquidityReadAt"),
                _int_none(pool.get("liquidity_block_number") or pool.get("liquidityBlockNumber")),
                now, now,
            )
        )
    if token_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_abnormal_anchor_tokens (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                token_role, token_address, pool_count, has_yc_optimal_pool,
                has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
                yc_zc_same_set, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address)
            DO UPDATE SET pool_count = excluded.pool_count,
                has_yc_optimal_pool = excluded.has_yc_optimal_pool,
                has_zc_optimal_pool = excluded.has_zc_optimal_pool,
                yc_optimal_pool_id = excluded.yc_optimal_pool_id,
                zc_optimal_pool_id = excluded.zc_optimal_pool_id,
                yc_zc_same_set = excluded.yc_zc_same_set,
                updated_at = excluded.updated_at
            """,
            token_rows,
        )
    if pair_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_abnormal_pool_pairs (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor_address, counter_address, address_pair, direction_key, pool_count,
                fixed_fee_liquid_pool_count, priced_pool_count,
                yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
                price_outlier_filter, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
            DO UPDATE SET pool_count = excluded.pool_count,
                fixed_fee_liquid_pool_count = excluded.fixed_fee_liquid_pool_count,
                priced_pool_count = excluded.priced_pool_count,
                yc_optimal_pool_id = excluded.yc_optimal_pool_id,
                zc_optimal_pool_id = excluded.zc_optimal_pool_id,
                price_reference_json = excluded.price_reference_json,
                price_outlier_filter = excluded.price_outlier_filter,
                updated_at = excluded.updated_at
            """,
            pair_rows,
        )
    if pool_rows:
        executemany(
            database_url,
            """
            INSERT INTO dex_candidate_abnormal_pools (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
                token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                liquidity_index, estimated_tvl_usd, balances_json, depth_status,
                depth_band_json, usable_for_observation, is_first_hop_recommended,
                is_third_hop_recommended, hop_recommendation_json,
                optimal_roles_json, liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            DO UPDATE SET pool_key_json = excluded.pool_key_json,
                pool_manager = excluded.pool_manager,
                token0 = excluded.token0,
                token1 = excluded.token1,
                fee = excluded.fee,
                fee_label = excluded.fee_label,
                dynamic_fee = excluded.dynamic_fee,
                special_fee = excluded.special_fee,
                tick_spacing = excluded.tick_spacing,
                hooks = excluded.hooks,
                sqrt_price_x96 = excluded.sqrt_price_x96,
                tick = excluded.tick,
                has_liquidity = CASE
                    WHEN excluded.has_liquidity IS NULL
                    THEN dex_candidate_abnormal_pools.has_liquidity
                    ELSE excluded.has_liquidity
                END,
                liquidity = CASE
                    WHEN excluded.liquidity IS NULL OR excluded.liquidity = ''
                    THEN dex_candidate_abnormal_pools.liquidity
                    ELSE excluded.liquidity
                END,
                liquidity_index = COALESCE(
                    excluded.liquidity_index,
                    dex_candidate_abnormal_pools.liquidity_index
                ),
                estimated_tvl_usd = COALESCE(
                    excluded.estimated_tvl_usd,
                    dex_candidate_abnormal_pools.estimated_tvl_usd
                ),
                balances_json = COALESCE(
                    excluded.balances_json,
                    dex_candidate_abnormal_pools.balances_json
                ),
                depth_status = CASE
                    WHEN excluded.depth_status IS NULL OR excluded.depth_status = ''
                    THEN dex_candidate_abnormal_pools.depth_status
                    ELSE excluded.depth_status
                END,
                depth_band_json = COALESCE(
                    excluded.depth_band_json,
                    dex_candidate_abnormal_pools.depth_band_json
                ),
                usable_for_observation = CASE
                    WHEN excluded.usable_for_observation IS NULL
                    THEN dex_candidate_abnormal_pools.usable_for_observation
                    ELSE excluded.usable_for_observation
                END,
                is_first_hop_recommended = excluded.is_first_hop_recommended,
                is_third_hop_recommended = excluded.is_third_hop_recommended,
                hop_recommendation_json = excluded.hop_recommendation_json,
                optimal_roles_json = excluded.optimal_roles_json,
                liquidity_read_at = COALESCE(
                    excluded.liquidity_read_at,
                    dex_candidate_abnormal_pools.liquidity_read_at
                ),
                liquidity_block_number = COALESCE(
                    excluded.liquidity_block_number,
                    dex_candidate_abnormal_pools.liquidity_block_number
                ),
                updated_at = excluded.updated_at
            """,
            pool_rows,
        )
    return {"tokens": len(token_rows), "pairs": len(pair_rows), "pools": len(pool_rows)}


def load_dex_candidate_pools(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbol: str,
) -> dict[str, Any]:
    tokens = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               token_role, token_address, pool_count, has_yc_optimal_pool,
               has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
               yc_backup_pool_ids_json, zc_backup_pool_ids_json,
               yc_zc_same_set, created_at, updated_at
        FROM dex_candidate_anchor_tokens
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY token_address
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    pairs = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               anchor_address, counter_address, address_pair, direction_key, pool_count,
               fixed_fee_liquid_pool_count, priced_pool_count,
               yc_optimal_pool_id, zc_optimal_pool_id,
               yc_backup_pool_ids_json, zc_backup_pool_ids_json,
               price_reference_json, price_outlier_filter, created_at, updated_at
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY counter_address
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    pools = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
               token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
               hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
               liquidity_index, estimated_tvl_usd, balances_json, depth_status,
               depth_band_json, usable_for_observation, is_first_hop_recommended,
               is_third_hop_recommended, hop_recommendation_json,
               optimal_roles_json, liquidity_read_at, liquidity_block_number,
               created_at, updated_at
        FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY address_pair, pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    recommendations = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               counter_address, direction_key, hop, rank, pool_id, depth_score, price, fee,
               selection_basis, created_at, updated_at
        FROM dex_candidate_pool_recommendations
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY counter_address, hop, rank
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    abnormal_tokens = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               token_role, token_address, pool_count, has_yc_optimal_pool,
               has_zc_optimal_pool, yc_optimal_pool_id, zc_optimal_pool_id,
               yc_zc_same_set, created_at, updated_at
        FROM dex_candidate_abnormal_anchor_tokens
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY token_address
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    abnormal_pairs = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               anchor_address, counter_address, address_pair, direction_key, pool_count,
               fixed_fee_liquid_pool_count, priced_pool_count,
               yc_optimal_pool_id, zc_optimal_pool_id,
               price_reference_json, price_outlier_filter, created_at, updated_at
        FROM dex_candidate_abnormal_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY counter_address
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    abnormal_pools = query(
        database_url,
        """
        SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
               address_pair, direction_key, direction_address_pair, pool_id, pool_key_json, pool_manager, token0,
               token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
               hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
               liquidity_index, estimated_tvl_usd, balances_json, depth_status,
               depth_band_json, usable_for_observation, is_first_hop_recommended,
               is_third_hop_recommended, hop_recommendation_json,
               optimal_roles_json, liquidity_read_at, liquidity_block_number,
               created_at, updated_at
        FROM dex_candidate_abnormal_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY address_pair, pool_id
        """,
        (network, chain_id, protocol, anchor_symbol),
    )
    return {
        "anchorTokens": [
            {
                **row,
                "yc_backup_pool_ids": _json_loads(row.get("yc_backup_pool_ids_json") or "[]"),
                "zc_backup_pool_ids": _json_loads(row.get("zc_backup_pool_ids_json") or "[]"),
            }
            for row in tokens
        ],
        "pairs": [
            {
                **row,
                "yc_backup_pool_ids": _json_loads(row.get("yc_backup_pool_ids_json") or "[]"),
                "zc_backup_pool_ids": _json_loads(row.get("zc_backup_pool_ids_json") or "[]"),
            }
            for row in pairs
        ],
        "pools": pools,
        "recommendations": recommendations,
        "abnormalAnchorTokens": abnormal_tokens,
        "abnormalPairs": abnormal_pairs,
        "abnormalPools": abnormal_pools,
    }


__all__ = [
    "load_dex_candidate_pools",
    "replace_dex_candidate_abnormal_tables",
    "replace_dex_candidate_tables",
]
