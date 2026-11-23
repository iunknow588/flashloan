"""Direction-aware static pool scoring for DEX candidate selection.

The **admission** decision belongs to the Rust kernel. The kernel reads the
shared depth policy document and publishes one verdict per measured pool;
this module only consumes that verdict and never carries a private copy of
an admission threshold.

Python therefore has no depth policy of its own. When the Rust kernel is
unavailable, the admission question stays unresolved instead of falling back
to a Python-local rule that could disagree with the kernel.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from core.db import pool_depth_keys as _pool_depth_keys


Q96 = 1 << 96
DEFAULT_DECIMALS = 18
# These two constants are retained only as call-site defaults for the
# diagnostic helpers that report a USD and a token-base reading. They are
# **not** admission thresholds: the admission floor lives in the shared
# depth policy and is applied by the Rust kernel.
DEFAULT_MIN_POOL_DEPTH_USD = 1000.0
DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE = 1.0
# A direction is usable as soon as one confirmed-liquidity candidate exists.
# This is the completion floor for backfill/readiness gates.
MIN_DIRECTION_CANDIDATE_POOLS = 1
# The directional candidate capacity is a **capacity knob**, not an admission
# threshold: it answers "how many pools may one direction keep", never
# "is this pool qualified".  Changing it changes what the system does, so it
# belongs in the environment instead of in the source text.  It must never be
# used as a gate -- a direction with fewer qualified pools keeps fewer pools.
MAX_DIRECTION_CANDIDATE_POOLS_ENV = "FLASHLOAN_MAX_DIRECTION_CANDIDATE_POOLS"
DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS = 5


def resolve_max_direction_candidate_pools(
    env: Mapping[str, str] | None = None,
) -> int:
    """Resolve the directional candidate capacity from the environment.

    A malformed or non-positive value falls back to the default instead of
    raising: a capacity knob must never be able to stop the scanner.  The
    value is read at call time so an operator can turn the knob without a
    code change; the module level constant below is only the import-time
    snapshot of it.
    """
    source = os.environ if env is None else env
    raw = source.get(MAX_DIRECTION_CANDIDATE_POOLS_ENV)
    if raw is None:
        return DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS
    if value <= 0:
        return DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS
    return value


# Import-time snapshot of the capacity knob.  Kept as a module constant so
# the existing call sites and their defaults keep working unchanged.
MAX_DIRECTION_CANDIDATE_POOLS = resolve_max_direction_candidate_pools()
LIVE_STATE_EVIDENCE_SOURCES = {
    "pool_manager_extsload_live_state_batch",
    "pool_manager_extsload_live_state_batch_rust",
}

# Legacy USD depth labels remain available for display and diagnostics. They
# are not candidate or YZ admission gates: a live V4 pool may be executable
# without any derived USD valuation.


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


# ARCH-1a: the on-chain binding capacity is the primary recommendation sort
# key. It is shared with the candidate builders so both surfaces observe one
# definition. See :mod:`core.db.pool_depth_keys`.
capacity_binding_raw = _pool_depth_keys.capacity_binding_raw


# The Rust kernel owns the candidate admission rule and publishes its
# verdict on every pool row it hands to Python. The verdict travels as an
# explicit field, so Python never derives an admission from a local
# threshold and never has to search for the right table.
#
# The field names are stable:
#   * ``kernel_admitted`` / ``kernelAdmitted`` -> the verdict itself
#   * ``kernel_admission_reason`` -> why the verdict was reached
#   * ``kernel_depth_band`` -> the depth band the kernel derived
ADMISSION_ADMITTED_KEYS = ("kernel_admitted", "kernelAdmitted")
ADMISSION_REASON_KEYS = ("kernel_admission_reason", "kernelAdmissionReason")
ADMISSION_BAND_KEYS = ("kernel_depth_band", "kernelDepthBand")


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _decode_depth_band_document(value: Any) -> dict[str, Any]:
    """Decode a stored depth-band document into a mapping.

    The SQLite store keeps the document in a JSON column, so the value can
    arrive as a string. A malformed or empty document is reported as an
    empty mapping rather than raising, because a missing document is a
    coverage gap and not a reason to abort the surrounding read.
    """
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def kernel_admission_verdict(pool: dict[str, Any]) -> dict[str, Any]:
    """Return the admission verdict published by the Rust kernel.

    The kernel owns the candidate admission rule and reads the shared depth
    policy document. It publishes one verdict per pool row; Python consumes
    that verdict and never derives it from a private threshold.

    A pool row without a published verdict is a coverage gap of the store.
    It is reported as unresolved instead of being admitted by a Python-local
    rule.
    """
    # A pool row identifies itself with any of the stable aliases. The
    # admission verdict is attached to the physical pool, so the lookup must
    # accept the same alias set the rest of the store uses.
    pool_id = str(
        pool.get("pool_id")
        or pool.get("poolId")
        or pool.get("pool")
        or pool.get("id")
        or ""
    ).strip()
    if not pool_id:
        return {"admitted": False, "reason": "missing_pool_id"}

    # The verdict may sit directly on the row or inside the depth-band
    # document the candidate surface carries. The store keeps the document
    # as a JSON column, so the raw string form must be decoded too.
    document = pool.get("depth_band") or pool.get("depthBand")
    if not isinstance(document, dict):
        document = _decode_depth_band_document(
            pool.get("depth_band_json")
            or pool.get("depthBandJson")
            or document
        )

    admitted = _first_present(pool, ADMISSION_ADMITTED_KEYS)
    if admitted is None:
        admitted = _first_present(document, ADMISSION_ADMITTED_KEYS)
    if admitted is None:
        return {"admitted": False, "reason": "kernel_verdict_missing"}

    reason = _first_present(pool, ADMISSION_REASON_KEYS)
    if reason is None:
        reason = _first_present(document, ADMISSION_REASON_KEYS)
    band = _first_present(pool, ADMISSION_BAND_KEYS)
    if band is None:
        band = _first_present(document, ADMISSION_BAND_KEYS)
    return {
        "admitted": bool(admitted),
        "reason": str(reason or ""),
        "depthBand": band,
    }


def depth_band_document(pool: dict[str, Any]) -> dict[str, Any]:
    """Return the depth-band document of one pool row, verdict included.

    The Rust kernel publishes its admission verdict on the pool row and
    stores it inside the depth-band document. When the row is written back to
    SQLite the verdict must survive the round trip, otherwise a later read
    cannot tell an admitted pool from a rejected one and the candidate
    surface degrades into a coverage gap.

    This helper is the single place that serializes the document. Every
    writer uses it so the verdict is never dropped on the way to the store.
    """
    document = pool.get("depth_band") or pool.get("depthBand")
    if not isinstance(document, dict):
        document = _decode_depth_band_document(
            pool.get("depth_band_json")
            or pool.get("depthBandJson")
            or document
        )
    else:
        document = dict(document)

    # The verdict may live directly on the row instead of inside the
    # document. Promote it into the document so a single read is enough.
    for keys, target in (
        (ADMISSION_ADMITTED_KEYS, "kernelAdmitted"),
        (ADMISSION_REASON_KEYS, "kernelAdmissionReason"),
        (ADMISSION_BAND_KEYS, "kernelDepthBand"),
        (("kernel_capacity_binding_raw", "kernelCapacityBindingRaw"), "kernelCapacityBindingRaw"),
    ):
        value = _first_present(pool, keys)
        if value is None:
            value = _first_present(document, keys)
        if value is not None and target not in document:
            document[target] = value

    return document


def has_confirmed_liquidity(
    pool: dict[str, Any],
    *,
    min_usd: float = DEFAULT_MIN_POOL_DEPTH_USD,
    fallback_token_base: float = DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE,
) -> bool:
    """Return the kernel-owned admission verdict of one physical pool.

    The decision is published by the Rust kernel, which reads the shared
    depth policy document. Depth is a two-sided market fact, so this gate
    deliberately has no ``token_in`` parameter: direction may change the
    score and the preferred pool, but it must never change whether a pool
    is included.

    The ``min_usd`` and ``fallback_token_base`` arguments are retained for
    call-site compatibility only. They are diagnostic readings, not
    admission thresholds, and they never decide the verdict.
    """
    return kernel_admission_verdict(pool)["admitted"]


def has_confirmed_depth(
    pool: dict[str, Any],
    *,
    min_usd: float = DEFAULT_MIN_POOL_DEPTH_USD,
    fallback_token_base: float = DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE,
) -> bool:
    """Return whether a pool is eligible for candidate/YZ construction.

    The historical USD depth status is intentionally ignored. Candidate and
    YZ eligibility follows confirmed on-chain liquidity only.
    """
    return has_confirmed_liquidity(pool, min_usd=min_usd, fallback_token_base=fallback_token_base)


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
    limit: int | None = None,
    min_usd: float = DEFAULT_MIN_POOL_DEPTH_USD,
    fallback_token_base: float = DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE,
    decimals_by_address: dict[str, object] | None = None,
) -> list[str]:
    """Return the best directed pool ids that still have confirmed liquidity.

    ARCH-1a: the order is the on-chain capacity key, not the static
    direction score. The first id is the replacement for the stored primary
    pool when the original recommendation has lost liquidity. The caller
    decides whether to keep the pool id as a backup; this helper never
    returns an id that fails the liquidity gate.

    Pool inclusion is deliberately direction-independent.  A physical V4 pool
    either exists as a two-sided market or it does not; direction may change
    the score/order, but it must never make one side of the pair look
    "has pools" while the mirror side looks "no pools".
    """
    if limit is None:
        limit = resolve_max_direction_candidate_pools()
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
        if not has_confirmed_depth(
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
    # ARCH-1a: the recommendation order is decided by the on-chain capacity
    # evidence (binding capacity DESC, realised price impact ASC). The
    # directional score only breaks ties between equally deep pools, and the
    # pool id closes the order so the result is deterministic.
    scored_pools.sort(
        key=lambda item: _pool_depth_keys.pool_depth_sort_key(
            item[1],
            _pool_id(item[1]),
            direction_score=item[0],
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
    "DEFAULT_MAX_DIRECTION_CANDIDATE_POOLS",
    "DEFAULT_MIN_POOL_DEPTH_TOKEN_BASE",
    "DEFAULT_MIN_POOL_DEPTH_USD",
    "MAX_DIRECTION_CANDIDATE_POOLS_ENV",
    "MIN_DIRECTION_CANDIDATE_POOLS",
    "MAX_DIRECTION_CANDIDATE_POOLS",
    "capacity_binding_raw",
    "depth_band_document",
    "has_confirmed_depth",
    "kernel_admission_verdict",
    "has_confirmed_liquidity",
    "pool_depth_usd",
    "resolve_max_direction_candidate_pools",
    "score_pool_by_direction",
    "select_directional_pool_ids",
]
