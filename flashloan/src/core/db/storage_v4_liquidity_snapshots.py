"""V4 liquidity snapshot persistence (ARCH-1a / R-05).

The snapshot table is the **only** measured-capacity source for the Robinhood
Chain V4 pools. This module owns writing it; :mod:`core.db.storage_v4_exchange`
owns the pool metadata and the read helpers.

Two rules govern the write path:

- **A newer snapshot never overwrites a newer one.** The upsert compares the
  liquidity block number first and falls back to the observation timestamp.
  A late-arriving older snapshot must not roll the measured capacity back.
- **Derived fields never contradict the raw on-chain liquidity word.** When
  the PoolManager reports a positive liquidity word, a stale zero index or a
  stale ``no_liquidity`` label is normalised away so every downstream gate
  observes the same fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import execute, executemany


__all__ = ["upsert_v4_liquidity_snapshots"]


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    import json

    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _bool(value: Any) -> int:
    return 1 if value else 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _first_present(*values: Any) -> Any:
    """Return the first value that is not None/empty while preserving 0/bool."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _int_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_liquidity_tri(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return 1
        if normalized in {"0", "false", "no"}:
            return 0
        return None
    return 1 if bool(value) else 0


def _normalized_liquidity_fields(
    *,
    has_liquidity: int | None,
    liquidity: Any,
    liquidity_index: float | None,
    estimated_tvl_usd: float | None,
    depth_status: Any,
) -> tuple[int | None, float | None, Any]:
    """Keep positive raw liquidity authoritative over derived display fields.

    The V4 PoolManager liquidity word is the primary on-chain evidence.  Older
    import paths occasionally wrote a positive raw word together with a zero
    derived index or a stale ``no_liquidity`` label.  Normalize those
    contradictions at the storage boundary so every downstream candidate and
    token-manager gate sees the same fact.
    """
    raw_liquidity = _float_none(liquidity)
    if has_liquidity != 1 or raw_liquidity is None or raw_liquidity <= 0:
        return has_liquidity, liquidity_index, depth_status
    normalized_index = liquidity_index
    if normalized_index is None or normalized_index <= 0:
        normalized_index = raw_liquidity / 1e18
    normalized_status = str(depth_status or "").strip()
    if normalized_status.lower() in {
        "",
        "no_liquidity",
        "empty",
        "liquidity_read_failed",
        "liquidity_math_failed",
        "live_liquidity_unavailable",
    }:
        normalized_status = "unpriced" if not estimated_tvl_usd or estimated_tvl_usd <= 0 else "liquid"
    return has_liquidity, normalized_index, normalized_status


_IS_NEWER_SNAPSHOT_SQL = """
(
  (
    excluded.liquidity_block_number IS NOT NULL
    AND (
      v4_exchange_pool_liquidity_snapshots.liquidity_block_number IS NULL
      OR excluded.liquidity_block_number >= v4_exchange_pool_liquidity_snapshots.liquidity_block_number
    )
  )
  OR
  (
    excluded.liquidity_block_number IS NULL
    AND v4_exchange_pool_liquidity_snapshots.liquidity_block_number IS NULL
    AND excluded.observed_at IS NOT NULL
    AND v4_exchange_pool_liquidity_snapshots.observed_at IS NOT NULL
    AND excluded.observed_at >= v4_exchange_pool_liquidity_snapshots.observed_at
  )
)
"""

def upsert_v4_liquidity_snapshots(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    rows: list[dict[str, Any]],
) -> int:
    now = _stamp()
    params = []
    for row in rows:
        pool_id = _text(row.get("pool_id") or row.get("poolId") or row.get("pool"))
        if not pool_id:
            continue
        raw_has_liquidity = _first_present(
            row.get("has_liquidity"),
            row.get("hasLiquidity"),
        )
        raw_swap_active = _first_present(
            row.get("recent_swap_active"),
            row.get("recentSwapActive"),
        )
        raw_balances = _first_present(
            row.get("balances"),
            row.get("balances_json"),
        )
        raw_depth_band = _first_present(
            row.get("depth_band"),
            row.get("depthBand"),
        )
        has_liquidity = _has_liquidity_tri(raw_has_liquidity)
        liquidity = _first_present(row.get("liquidity"))
        liquidity_index = _float_none(
            _first_present(
                row.get("liquidity_index"),
                row.get("liquidityIndex"),
            )
        )
        estimated_tvl_usd = _float_none(
            _first_present(
                row.get("estimated_tvl_usd"),
                row.get("estimatedTvlUsd"),
                row.get("tvl_usd"),
            )
        )
        balances_json = (
            _json_dumps(raw_balances) if raw_balances is not None else None
        )
        depth_status = _first_present(
            row.get("depth_status"),
            row.get("depthStatus"),
        )
        has_liquidity, liquidity_index, depth_status = _normalized_liquidity_fields(
            has_liquidity=has_liquidity,
            liquidity=liquidity,
            liquidity_index=liquidity_index,
            estimated_tvl_usd=estimated_tvl_usd,
            depth_status=depth_status,
        )
        depth_band_json = (
            _json_dumps(raw_depth_band)
            if raw_depth_band is not None
            else None
        )
        depth_score_usd = _float_none(
            _first_present(
                row.get("depth_score_usd"),
                row.get("depthScoreUsd"),
            )
        )
        sqrt_price_x96 = _first_present(
            row.get("sqrt_price_x96"),
            row.get("sqrtPriceX96"),
        )
        price = _float_none(_first_present(row.get("price")))
        tick = _int_none(row.get("tick"))
        liquidity_read_at = _first_present(
            row.get("liquidity_read_at"),
            row.get("liquidityReadAt"),
        )
        liquidity_block_number = _int_none(
            _first_present(
                row.get("liquidity_block_number"),
                row.get("liquidityBlockNumber"),
            )
        )
        recent_swap_active = (
            (1 if bool(raw_swap_active) else 0)
            if raw_swap_active is not None
            else None
        )
        observed_at = _first_present(
            row.get("observed_at"),
            row.get("observedAt"),
        ) or now
        refreshed_at = _first_present(
            row.get("refreshed_at"),
            row.get("refreshedAt"),
        ) or now
        evidence_source = str(
            _first_present(
                row.get("evidence_source"),
                row.get("liquidity_source"),
                row.get("liquiditySource"),
                row.get("source"),
            )
            or ""
        ).strip() or None
        has_dynamic_value = any(
            value is not None
            for value in (
                has_liquidity,
                liquidity,
                liquidity_index,
                estimated_tvl_usd,
                depth_status,
                depth_score_usd,
                sqrt_price_x96,
                price,
                tick,
                recent_swap_active,
            )
        )
        if not has_dynamic_value:
            liquidity_block_number = None
        params.append(
            (
                network,
                chain_id,
                protocol,
                pool_id,
                has_liquidity,
                liquidity,
                liquidity_index,
                estimated_tvl_usd,
                balances_json,
                depth_status,
                depth_band_json,
                depth_score_usd,
                sqrt_price_x96,
                price,
                tick,
                liquidity_read_at,
                liquidity_block_number,
                recent_swap_active,
                observed_at,
                refreshed_at,
                evidence_source,
            )
        )
    if not params:
        return 0
    return executemany(
        database_url,
        f"""
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            liquidity_index, estimated_tvl_usd, balances_json, depth_status,
            depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
            liquidity_read_at, liquidity_block_number, recent_swap_active,
            observed_at, refreshed_at, evidence_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            has_liquidity = CASE
                WHEN excluded.has_liquidity IS NULL OR NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.has_liquidity
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.has_liquidity
                ELSE excluded.has_liquidity
            END,
            liquidity = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.liquidity
                WHEN excluded.has_liquidity = 0 AND (
                    (excluded.evidence_source IS NOT NULL AND excluded.evidence_source <> '')
                    OR excluded.liquidity_block_number IS NOT NULL
                )
                THEN ''
                WHEN excluded.liquidity IS NULL OR excluded.liquidity = ''
                THEN v4_exchange_pool_liquidity_snapshots.liquidity
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.liquidity
                ELSE excluded.liquidity
            END,
            liquidity_index = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.liquidity_index
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.liquidity_index
                ELSE COALESCE(
                    excluded.liquidity_index,
                    v4_exchange_pool_liquidity_snapshots.liquidity_index
                )
            END,
            estimated_tvl_usd = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.estimated_tvl_usd
                WHEN (
                    (excluded.evidence_source IS NULL OR excluded.evidence_source = '')
                    AND excluded.liquidity_block_number IS NULL
                    AND v4_exchange_pool_liquidity_snapshots.has_liquidity IS NOT NULL
                )
                THEN v4_exchange_pool_liquidity_snapshots.estimated_tvl_usd
                ELSE COALESCE(
                    excluded.estimated_tvl_usd,
                    v4_exchange_pool_liquidity_snapshots.estimated_tvl_usd
                )
            END,
            balances_json = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.balances_json
                ELSE COALESCE(
                    excluded.balances_json,
                    v4_exchange_pool_liquidity_snapshots.balances_json
                )
            END,
            depth_status = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.depth_status
                WHEN excluded.depth_status IS NULL OR excluded.depth_status = ''
                THEN v4_exchange_pool_liquidity_snapshots.depth_status
                ELSE excluded.depth_status
            END,
            depth_band_json = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.depth_band_json
                ELSE COALESCE(
                    excluded.depth_band_json,
                    v4_exchange_pool_liquidity_snapshots.depth_band_json
                )
            END,
            depth_score_usd = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.depth_score_usd
                ELSE COALESCE(
                    excluded.depth_score_usd,
                    v4_exchange_pool_liquidity_snapshots.depth_score_usd
                )
            END,
            sqrt_price_x96 = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.sqrt_price_x96
                WHEN excluded.sqrt_price_x96 IS NULL OR excluded.sqrt_price_x96 = ''
                THEN v4_exchange_pool_liquidity_snapshots.sqrt_price_x96
                ELSE excluded.sqrt_price_x96
            END,
            price = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.price
                ELSE COALESCE(
                    excluded.price,
                    v4_exchange_pool_liquidity_snapshots.price
                )
            END,
            tick = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.tick
                ELSE COALESCE(
                    excluded.tick,
                    v4_exchange_pool_liquidity_snapshots.tick
                )
            END,
            liquidity_read_at = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.liquidity_read_at
                ELSE COALESCE(
                    excluded.liquidity_read_at,
                    v4_exchange_pool_liquidity_snapshots.liquidity_read_at
                )
            END,
            liquidity_block_number = CASE
                WHEN excluded.liquidity_block_number IS NULL
                THEN v4_exchange_pool_liquidity_snapshots.liquidity_block_number
                WHEN (
                    v4_exchange_pool_liquidity_snapshots.liquidity_block_number IS NULL
                    OR excluded.liquidity_block_number
                       >= v4_exchange_pool_liquidity_snapshots.liquidity_block_number
                )
                THEN excluded.liquidity_block_number
                ELSE v4_exchange_pool_liquidity_snapshots.liquidity_block_number
            END,
            recent_swap_active = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.recent_swap_active
                WHEN excluded.recent_swap_active IS NULL
                THEN v4_exchange_pool_liquidity_snapshots.recent_swap_active
                ELSE excluded.recent_swap_active
            END,
            observed_at = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.observed_at
                ELSE COALESCE(
                    excluded.observed_at,
                    v4_exchange_pool_liquidity_snapshots.observed_at
                )
            END,
            refreshed_at = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.refreshed_at
                ELSE excluded.refreshed_at
            END,
            evidence_source = CASE
                WHEN NOT {_IS_NEWER_SNAPSHOT_SQL}
                THEN v4_exchange_pool_liquidity_snapshots.evidence_source
                ELSE COALESCE(
                    excluded.evidence_source,
                    v4_exchange_pool_liquidity_snapshots.evidence_source
                )
            END
        """,
        params,
    )


