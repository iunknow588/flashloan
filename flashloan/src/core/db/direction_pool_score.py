"""Direction-aware static pool scoring for DEX candidate selection."""

from __future__ import annotations

from typing import Any


Q96 = 1 << 96
DEFAULT_DECIMALS = 18
DEFAULT_MIN_POOL_DEPTH_USD = 1000.0
DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE = 1.0
LIVE_STATE_EVIDENCE_SOURCES = {
    "pool_manager_extsload_live_state_batch",
}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _token_decimals(
    token_address: str,
    decimals_by_address: dict[str, int] | None,
) -> int:
    if not decimals_by_address:
        return DEFAULT_DECIMALS
    decimals = decimals_by_address.get(_text(token_address))
    try:
        return int(decimals) if decimals not in (None, "") else DEFAULT_DECIMALS
    except (TypeError, ValueError):
        return DEFAULT_DECIMALS


def _spot_sqrt_price(pool: dict[str, Any]) -> float:
    raw = pool.get("sqrt_price_x96") or pool.get("sqrtPriceX96") or pool.get("sqrtPrice")
    if raw in (None, ""):
        return 0.0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        try:
            value = int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
        except (TypeError, ValueError):
            return 0.0
    if value <= 0:
        return 0.0
    return value / Q96


def _fee_factor(pool: dict[str, Any]) -> float:
    try:
        fee = int(pool.get("fee") or 0)
    except (TypeError, ValueError):
        fee = 0
    return max(0.0, 1.0 - (fee / 1_000_000))


def _depth_score(pool: dict[str, Any]) -> float:
    return max(
        _number(pool.get("liquidity_index") or pool.get("liquidityIndex")),
        _number(pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd")),
        _number(pool.get("depth_score_usd") or pool.get("depthScoreUsd")),
    )


def pool_depth_usd(pool: dict[str, Any]) -> float:
    """Return the best available USD depth evidence for one physical pool."""
    depth_usd = max(
        _number(pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd")),
        _number(pool.get("depth_score_usd") or pool.get("depthScoreUsd")),
    )
    balances = pool.get("balances")
    if isinstance(balances, dict):
        depth_usd = max(
            depth_usd,
            _number(balances.get("tvlUsd") or balances.get("tvl_usd")),
        )
    return depth_usd


def has_confirmed_liquidity(
    pool: dict[str, Any],
    *,
    min_usd: float = DEFAULT_MIN_POOL_DEPTH_USD,
    fallback_token_base: float = DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE,
) -> bool:
    """Confirm a physical pool has enough depth.

    Depth is a two-sided market fact. Direction may change the score and the
    preferred pool, but it must never change whether a pool is included, so
    this gate deliberately has no ``token_in`` parameter.

    Confirmed liquidity is a two-sided market fact and the observation layer
    treats ``has_liquidity=1`` as liquid regardless of the evidence source.
    The candidate layer must use the same gate, so a confirmed row is never
    rejected just because a derived USD depth is tiny.  USD evidence is the
    primary depth gate; when USD depth is small or missing, the fallback is
    the published liquidity index, or one whole token unit from raw liquidity.
    """
    evidence_source = _text(pool.get("evidence_source") or pool.get("evidenceSource"))
    has_liquidity = pool.get("has_liquidity")
    if has_liquidity is None:
        has_liquidity = pool.get("hasLiquidity")
    if evidence_source in LIVE_STATE_EVIDENCE_SOURCES and _truthy(has_liquidity):
        raw_liquidity = _number(pool.get("liquidity"))
        liquidity_index = _number(pool.get("liquidity_index") or pool.get("liquidityIndex"))
        estimated_tvl_usd = _number(pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd"))
        if raw_liquidity > 0 or liquidity_index > 0 or estimated_tvl_usd > 0:
            return True
    depth_usd = pool_depth_usd(pool)
    if depth_usd >= min_usd:
        return True
    if fallback_token_base <= 0:
        return False
    liquidity_index = _number(pool.get("liquidity_index") or pool.get("liquidityIndex"))
    raw_liquidity = _number(pool.get("liquidity"))
    if raw_liquidity > 0:
        liquidity_index = max(liquidity_index, raw_liquidity / (10 ** DEFAULT_DECIMALS))
    if liquidity_index >= fallback_token_base:
        return True
    return False


def _liquidity_capacity(
    pool: dict[str, Any],
    *,
    token_in: str,
    sqrt_price: float,
) -> float | None:
    liquidity = _number(pool.get("liquidity"))
    if liquidity <= 0 or sqrt_price <= 0:
        return None
    # L/sqrt(P) is the raw token0 depth and L*sqrt(P) the raw token1 depth.
    if _text(pool.get("token0")) == token_in:
        return liquidity / sqrt_price
    if _text(pool.get("token1")) == token_in:
        return liquidity * sqrt_price
    return None


def score_pool_by_direction(
    pool: dict[str, Any],
    token_in: object,
    token_out: object,
    *,
    decimals_by_address: dict[str, object] | None = None,
) -> float:
    """Return a deterministic directional output score for one V4 pool.

    The score favours a high effective output per fixed input in the requested
    direction.  When sqrt price or ``price`` evidence is unavailable it falls
    back to depth, which keeps candidate/observation ordering stable.
    """

    token_in = _text(token_in)
    token_out = _text(token_out)
    token0 = _text(pool.get("token0") or pool.get("currency0"))
    token1 = _text(pool.get("token1") or pool.get("currency1"))
    if not token_in or not token_out or {token_in, token_out} != {token0, token1}:
        return 0.0
    decimals = {
        token0: _token_decimals(token0, decimals_by_address),
        token1: _token_decimals(token1, decimals_by_address),
    }
    sqrt_price = _spot_sqrt_price(pool)
    fee_factor = _fee_factor(pool)
    if sqrt_price > 0:
        price = sqrt_price * sqrt_price
        if token_in == token0:
            rate = price
        else:
            rate = 1.0 / price
        # Convert token1/token0 price into raw output units per raw input unit.
        rate *= 10 ** (decimals[token_out] - decimals[token_in])
        capacity = _liquidity_capacity(pool, token_in=token_in, sqrt_price=sqrt_price)
        if capacity is not None and capacity > 0:
            input_raw = 10 ** decimals[token_in]
            rate = min(rate, capacity / input_raw)
        return rate * fee_factor
    reference_price = _number(pool.get("price"))
    if reference_price > 0:
        if token_in == token1:
            reference_price = 1.0 / reference_price
        return reference_price * fee_factor
    return _depth_score(pool)


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(
        pool.get("pool_id")
        or pool.get("poolId")
        or pool.get("pool")
        or pool.get("id")
    )


def select_directional_pool_ids(
    pools: list[dict[str, Any]],
    token_in: object,
    token_out: object,
    *,
    limit: int = 3,
    min_usd: float = DEFAULT_MIN_POOL_DEPTH_USD,
    fallback_token_base: float = DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE,
    decimals_by_address: dict[str, object] | None = None,
) -> list[str]:
    """Return the best directed pool ids that still have confirmed depth.

    Order is the current static direction score, so the first id is the
    replacement for the stored primary pool when the original recommendation
    has lost liquidity.  The caller decides whether to keep the pool id as a
    backup; this helper never returns an id that fails the depth gate.

    Pool inclusion is deliberately direction-independent.  A physical V4 pool
    either exists as a two-sided market or it does not; direction may change
    the score/order, but it must never make one side of the pair look
    "has pools" while the mirror side looks "no pools".
    """
    if limit <= 0:
        return []
    token_in_norm = _text(token_in)
    token_out_norm = _text(token_out)
    if not token_in_norm or not token_out_norm:
        return []
    selected: list[str] = []
    selected_ids: set[str] = set()
    scored_pools: list[tuple[float, dict[str, object]]] = []
    for pool in pools:
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if {token_in_norm, token_out_norm} != {token0, token1}:
            continue
        if not has_confirmed_liquidity(
            pool,
            min_usd=min_usd,
            fallback_token_base=fallback_token_base,
        ):
            continue
        score = score_pool_by_direction(
            pool,
            token_in,
            token_out,
            decimals_by_address=decimals_by_address,
        )
        if score <= 0:
            continue
        scored_pools.append((score, pool))
    scored_pools.sort(
        key=lambda item: (
            -item[0],
            _number(item[1].get("fee")),
            _pool_id(item[1]),
        )
    )
    for _, pool in scored_pools:
        pool_id = _pool_id(pool)
        if not pool_id:
            continue
        if pool_id in selected_ids:
            continue
        selected_ids.add(pool_id)
        selected.append(pool_id)
        if len(selected) >= limit:
            break
    return selected


__all__ = [
    "DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE",
    "DEFAULT_MIN_POOL_DEPTH_USD",
    "has_confirmed_liquidity",
    "pool_depth_usd",
    "score_pool_by_direction",
    "select_directional_pool_ids",
]
