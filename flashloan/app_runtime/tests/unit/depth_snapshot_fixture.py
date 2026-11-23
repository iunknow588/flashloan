# -*- coding: utf-8 -*-
"""A test fixture that publishes a depth snapshot row the way the pool manager does.

TASK-172 / AC-221.  The depth snapshot table
``v4_exchange_pool_liquidity_snapshots`` has exactly **one** writer: the
Rust pool manager.  It writes the price columns (``sqrt_price_x96`` /
``tick`` / ``liquidity``) **and** the capacity columns **and** the
single-tick bounds in one statement, so a row always describes exactly one
on-chain moment.

The evidence channel used to be a second writer.  It wrote the price
columns only, which left the bounds of an older moment next to a newer
price, and the row then carried two different observations at once.  The
channel no longer writes the table, so ``upsert_v4_liquidity_snapshots``
is inert.

A fixture therefore cannot use the evidence channel to arrange a measured
row.  It has to write the row the way the pool manager writes it, because
the state under test *is* the pool manager's published state.  This module
is that one way, shared by the fixtures that need it.

The merge rules below reproduce the pool manager's write semantics
exactly, because those rules are the contract under test:

* a row that is **not newer** never overwrites a newer one;
* an **unknown** value (``None`` / empty string) never erases a known one;
* only an explicit live-state read may prove a pool is empty;
* a positive raw liquidity word stays authoritative over a stale derived
  index or a stale ``no_liquidity`` label.
"""

from __future__ import annotations

import json
from typing import Any

from core.db.storage_business_io import execute, query_one


__all__ = [
    "materialise_depth_snapshot",
    "FIXTURE_MATERIALIZATION_BATCH",
    "FIXTURE_OBSERVED_AT",
    "POOL_MANAGER_EVIDENCE_SOURCE",
]


#: The batch id a fixture uses.  The real pool manager mints a fresh one
#: per run; a fixture needs a stable one so a test can assert on it.
FIXTURE_MATERIALIZATION_BATCH = "pool-manager-fixture-batch"

#: The moment a fixture claims the row was measured at.
FIXTURE_OBSERVED_AT = "2026-11-23T00:00:00Z"

#: The evidence source the pool manager stamps on a live measurement.
POOL_MANAGER_EVIDENCE_SOURCE = "pool_manager_extsload_live_state_batch_rust"

#: The status labels that mean "we could not measure liquidity".
_UNPRICED_STATUSES = {
    "",
    "no_liquidity",
    "empty",
    "liquidity_read_failed",
    "liquidity_math_failed",
    "live_liquidity_unavailable",
}

#: The columns the fixture reads back when it merges with the stored row.
_MERGE_COLUMNS = (
    "has_liquidity",
    "liquidity",
    "liquidity_index",
    "estimated_tvl_usd",
    "balances_json",
    "depth_status",
    "depth_band_json",
    "depth_score_usd",
    "sqrt_price_x96",
    "price",
    "tick",
    "liquidity_read_at",
    "liquidity_block_number",
    "recent_swap_active",
    "observed_at",
    "refreshed_at",
    "evidence_source",
)


def _first_present(*values: Any) -> Any:
    """Return the first value that is not None/empty while preserving 0/bool."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _float_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_liquidity_tri(value: Any) -> int | None:
    """Tri-state: 1 true, 0 false, None unknown."""
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


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_liquidity_fields(
    *,
    has_liquidity: int | None,
    liquidity: Any,
    liquidity_index: float | None,
    estimated_tvl_usd: float | None,
    depth_status: Any,
) -> tuple[int | None, float | None, Any]:
    """Keep positive raw liquidity authoritative over derived display fields."""
    raw_liquidity = _float_none(liquidity)
    if has_liquidity != 1 or raw_liquidity is None or raw_liquidity <= 0:
        return has_liquidity, liquidity_index, depth_status
    normalized_index = liquidity_index
    if normalized_index is None or normalized_index <= 0:
        normalized_index = raw_liquidity / 1e18
    normalized_status = str(depth_status or "").strip()
    if normalized_status.lower() in _UNPRICED_STATUSES:
        normalized_status = "unpriced" if not estimated_tvl_usd or estimated_tvl_usd <= 0 else "liquid"
    return has_liquidity, normalized_index, normalized_status


def _is_newer_snapshot(
    *,
    excluded_block: int | None,
    existing_block: int | None,
    excluded_observed_at: str | None,
    existing_observed_at: str | None,
) -> bool:
    """Whether the incoming row describes a moment at least as late as the stored one."""
    if excluded_block is not None:
        return existing_block is None or excluded_block >= existing_block
    if existing_block is None and excluded_observed_at is not None and existing_observed_at is not None:
        return excluded_observed_at >= existing_observed_at
    return False


def _coalesce(incoming: Any, existing: Any) -> Any:
    """An unknown incoming value never erases a known stored one."""
    return existing if incoming is None else incoming


def _stored_row(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    pool_id: str,
) -> dict[str, Any] | None:
    return query_one(
        database_url,
        f"""
        SELECT {", ".join(_MERGE_COLUMNS)}
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (network, chain_id, protocol, pool_id),
    )


def materialise_depth_snapshot(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    rows: list[dict[str, Any]],
) -> None:
    """Write depth snapshot rows the way the Rust pool manager writes them.

    One statement, whole row, one on-chain moment -- the same shape the
    pool manager uses, so a fixture cannot accidentally arrange a row that
    the single-writer rule forbids in production.

    The parameters mirror the pool manager's columns.  A parameter the
    caller does not set stays NULL, because a value nobody measured must
    not be invented.
    """
    for row in rows:
        pool_id = _first_present(row.get("pool_id"), row.get("poolId"), row.get("pool"))
        if not pool_id:
            continue

        raw_has_liquidity = _first_present(
            row.get("has_liquidity"),
            row.get("hasLiquidity"),
        )
        raw_balances = _first_present(
            row.get("balances"),
            row.get("balances_json"),
        )
        raw_depth_band = _first_present(
            row.get("depth_band"),
            row.get("depthBand"),
        )
        raw_depth_status = _first_present(
            row.get("depth_status"),
            row.get("depthStatus"),
        )

        has_liquidity = _has_liquidity_tri(raw_has_liquidity)
        liquidity = _first_present(row.get("liquidity"))
        liquidity_index = _float_none(
            _first_present(row.get("liquidity_index"), row.get("liquidityIndex"))
        )
        estimated_tvl_usd = _float_none(
            _first_present(row.get("estimated_tvl_usd"), row.get("estimatedTvlUsd"))
        )
        depth_status = raw_depth_status
        has_liquidity, liquidity_index, depth_status = _normalized_liquidity_fields(
            has_liquidity=has_liquidity,
            liquidity=liquidity,
            liquidity_index=liquidity_index,
            estimated_tvl_usd=estimated_tvl_usd,
            depth_status=depth_status,
        )

        balances_json = _json_dumps(raw_balances) if raw_balances is not None else None
        depth_band_json = _json_dumps(raw_depth_band) if raw_depth_band is not None else None
        sqrt_price_x96 = _first_present(row.get("sqrt_price_x96"), row.get("sqrtPriceX96"))
        price = _float_none(_first_present(row.get("price")))
        tick = _int_none(_first_present(row.get("tick")))
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
        raw_swap_active = _first_present(
            row.get("recent_swap_active"),
            row.get("recentSwapActive"),
        )
        recent_swap_active = (1 if bool(raw_swap_active) else 0) if raw_swap_active is not None else None
        depth_score_usd = _float_none(
            _first_present(row.get("depth_score_usd"), row.get("depthScoreUsd"))
        )
        # An absent evidence source stays absent.  The pool manager always
        # names the run that measured the row, but a fixture that does not
        # say so has measured nothing, and "no source" is exactly what makes
        # a weak row unable to overwrite confirmed evidence.
        evidence_source = (
            str(
                _first_present(
                    row.get("evidence_source"),
                    row.get("liquidity_source"),
                    row.get("liquiditySource"),
                    row.get("source"),
                    row.get("evidenceSource"),
                )
                or ""
            ).strip()
            or None
        )
        observed_at = row.get("observed_at") or FIXTURE_OBSERVED_AT
        refreshed_at = row.get("refreshed_at") or FIXTURE_OBSERVED_AT
        materialization_batch = row.get("materialization_batch") or FIXTURE_MATERIALIZATION_BATCH

        # A row that carries no dynamic measurement claims no block: the
        # block number alone is not evidence that anything was measured.
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

        stored = _stored_row(
            database_url,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            pool_id=pool_id,
        )

        if stored is not None:
            existing_block = _int_none(stored.get("liquidity_block_number"))
            existing_observed_at = stored.get("observed_at")
            newer = _is_newer_snapshot(
                excluded_block=liquidity_block_number,
                existing_block=existing_block,
                excluded_observed_at=observed_at,
                existing_observed_at=existing_observed_at,
            )
            if not newer:
                # A stale measurement never overwrites a newer one.
                continue

            existing_has_liquidity = _int_none(stored.get("has_liquidity"))
            authoritative = (
                (not evidence_source)
                and liquidity_block_number is None
                and existing_has_liquidity is not None
            )

            if has_liquidity is None or authoritative:
                has_liquidity = existing_has_liquidity
            if has_liquidity == 0 and (
                evidence_source or liquidity_block_number is not None
            ):
                liquidity = ""
            elif liquidity is None or liquidity == "":
                liquidity = stored.get("liquidity")
            elif authoritative:
                liquidity = stored.get("liquidity")

            if authoritative:
                liquidity_index = stored.get("liquidity_index")
                estimated_tvl_usd = stored.get("estimated_tvl_usd")
            else:
                liquidity_index = _coalesce(liquidity_index, stored.get("liquidity_index"))
                estimated_tvl_usd = _coalesce(
                    estimated_tvl_usd, stored.get("estimated_tvl_usd")
                )

            balances_json = _coalesce(balances_json, stored.get("balances_json"))
            depth_status = _coalesce(depth_status, stored.get("depth_status"))
            depth_band_json = _coalesce(depth_band_json, stored.get("depth_band_json"))
            depth_score_usd = _coalesce(depth_score_usd, stored.get("depth_score_usd"))
            sqrt_price_x96 = _coalesce(sqrt_price_x96, stored.get("sqrt_price_x96"))
            price = _coalesce(price, stored.get("price"))
            tick = _coalesce(tick, stored.get("tick"))
            liquidity_read_at = _coalesce(liquidity_read_at, stored.get("liquidity_read_at"))
            observed_at = _coalesce(observed_at, stored.get("observed_at"))
            evidence_source = _coalesce(evidence_source, stored.get("evidence_source"))

        execute(
            database_url,
            """
            INSERT INTO v4_exchange_pool_liquidity_snapshots (
                network, chain_id, protocol, pool_id,
                has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
                depth_status, depth_band_json, depth_score_usd,
                evidence_source, sqrt_price_x96, price, tick,
                liquidity_read_at, liquidity_block_number, recent_swap_active,
                balances_json, observed_at, refreshed_at, materialization_batch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
                has_liquidity = excluded.has_liquidity,
                liquidity = excluded.liquidity,
                liquidity_index = excluded.liquidity_index,
                estimated_tvl_usd = excluded.estimated_tvl_usd,
                depth_status = excluded.depth_status,
                depth_band_json = excluded.depth_band_json,
                depth_score_usd = excluded.depth_score_usd,
                evidence_source = excluded.evidence_source,
                sqrt_price_x96 = excluded.sqrt_price_x96,
                price = excluded.price,
                tick = excluded.tick,
                liquidity_read_at = excluded.liquidity_read_at,
                liquidity_block_number = excluded.liquidity_block_number,
                recent_swap_active = excluded.recent_swap_active,
                balances_json = excluded.balances_json,
                observed_at = excluded.observed_at,
                refreshed_at = excluded.refreshed_at,
                materialization_batch = excluded.materialization_batch
            """,
            (
                network,
                chain_id,
                protocol,
                pool_id,
                has_liquidity,
                liquidity,
                liquidity_index,
                estimated_tvl_usd,
                depth_status,
                depth_band_json,
                depth_score_usd,
                evidence_source,
                sqrt_price_x96,
                price,
                tick,
                liquidity_read_at,
                liquidity_block_number,
                recent_swap_active,
                balances_json,
                observed_at,
                refreshed_at,
                materialization_batch,
            ),
        )
