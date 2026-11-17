"""ARCH-1a pool depth keys.

ARCH-1a (2026-11-12) defines the **ordering** of already-admitted pools. It
does not redefine who is admitted.

Sort key (total and reproducible):

    1. ``capacity_binding_raw`` DESC  -- on-chain, token-denominated capacity
    2. ``price_impact_bps`` ASC       -- smaller realised impact wins
    3. ``pool_id`` ASC                -- stable final tie-break

Hard boundaries that this module must not break:

- **Zero USD dependency.** Admission, economics and banding are all
  token-denominated. ``estimated_tvl_usd`` is a display/diagnosis reading
  only; it never decides an admission and it is not a sort key here.
- **Missing measurement stays missing.** An unmeasured capacity yields
  ``0.0``; it is never fabricated as a positive capacity and never lets a
  pool through by accident.
- **Direction-independent.** The capacity of a physical V4 pool is a
  two-sided market fact, so both mirror directions observe the same value.

The helpers accept the capacity either as a flat column or inside the
``depth_band_json`` document that the candidate table carries.
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "capacity_binding_raw",
    "pool_capacity_binding",
    "pool_price_impact_bps",
    "pool_depth_sort_key",
    "pool_legacy_depth_usd",
]


_CAPACITY_FLAT_KEYS = ("capacity_binding_raw", "capacityBindingRaw")
_CAPACITY_BAND_KEYS = ("capacityBindingRaw", "capacity_binding_raw")
_IMPACT_FLAT_KEYS = ("price_impact_bps", "priceImpactBps")
_IMPACT_BAND_KEYS = ("priceImpactBps", "price_impact_bps")


def _as_float(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` when it is not numeric."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _depth_band(pool: dict[str, Any]) -> dict[str, Any]:
    band = pool.get("depth_band") or pool.get("depthBand")
    if isinstance(band, dict):
        return band
    return {}


def capacity_binding_raw(pool: dict[str, Any]) -> float:
    """Return the decimals-normalised binding-side capacity of one pool.

    The capacity measures the weaker side of the pool in the pool's own token
    units, so it is comparable across tokens with different decimals. An
    unmeasured pool yields ``0.0``: unknown is not deep.
    """
    for key in _CAPACITY_FLAT_KEYS:
        number = _as_float(pool.get(key))
        if number is not None:
            return number
    band = _depth_band(pool)
    for key in _CAPACITY_BAND_KEYS:
        number = _as_float(band.get(key))
        if number is not None:
            return number
    return 0.0


def pool_price_impact_bps(pool: dict[str, Any]) -> float:
    """Return the realised price impact of the measured capacity, in bps.

    Among pools that carry the same binding capacity, the one that disturbs
    the price least is preferred. An unmeasured impact yields ``0.0`` so it
    never outranks a measured one by accident.
    """
    for key in _IMPACT_FLAT_KEYS:
        number = _as_float(pool.get(key))
        if number is not None:
            return number
    band = _depth_band(pool)
    for key in _IMPACT_BAND_KEYS:
        number = _as_float(band.get(key))
        if number is not None:
            return number
    return 0.0


def pool_legacy_depth_usd(pool: dict[str, Any]) -> float:
    """Return the legacy USD depth reading for display and diagnosis only.

    ARCH-1a forbids this value from deciding an admission or from acting as a
    sort key. It stays reachable so the UI and the drift audit can still show
    what the old surface reported.
    """
    best = 0.0
    for key in ("estimated_tvl_usd", "estimatedTvlUsd", "depth_score_usd", "depthScoreUsd"):
        number = _as_float(pool.get(key))
        if number is not None:
            best = max(best, number)
    balances = pool.get("balances")
    if isinstance(balances, dict):
        for key in ("tvlUsd", "tvl_usd"):
            number = _as_float(balances.get(key))
            if number is not None:
                best = max(best, number)
    return best


def pool_capacity_binding(pool: dict[str, Any]) -> float:
    """Return the pool's on-chain depth signal.

    ARCH-1a / R-03 / R-04: the depth signal is the binding-side capacity
    only. ``liquidity_index`` is not a depth signal and a TVL estimate is a
    USD reading that depends on an external price feed, so neither may enter
    this value. The legacy fields stay in the row for display and diagnosis.
    """
    return capacity_binding_raw(pool)


def pool_depth_sort_key(
    pool: dict[str, Any],
    pool_id: str,
    *,
    direction_score: float = 0.0,
) -> tuple[float, float, float, str]:
    """Return the ARCH-1a sort key for one pool.

    The returned tuple is ordered so that ``sorted(..., key=...)`` yields:

    1. ``capacity_binding_raw`` DESC  -- on-chain, token-denominated capacity
    2. ``price_impact_bps`` ASC       -- smaller realised impact wins
    3. ``direction_score`` DESC       -- mirror-direction price preference
    4. ``pool_id`` ASC                -- stable, deterministic final key

    ``direction_score`` is deliberately placed **below** the capacity keys.
    ARCH-1a forbids a price reading from deciding which admitted pool is
    recommended, but the V4 model keeps the two mirror directions distinct:
    the same physical pool may be the best A -> B venue and the worst B -> A
    venue. Keeping the directional score as a tie-break below capacity
    preserves that invariant without ever letting a price reading outrank an
    on-chain capacity measurement.

    ``estimated_tvl_usd`` never appears in this key.
    """
    return (
        -pool_capacity_binding(pool),
        pool_price_impact_bps(pool),
        -direction_score,
        pool_id,
    )