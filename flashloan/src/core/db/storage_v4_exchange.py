"""V4 exchange pool and liquidity snapshot storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db.storage_business_io import execute, query
from core.db.storage_dex_surface_snapshot import clear_dex_surface_snapshots


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


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def _json_loads(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0


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
    """Return 1/0 for explicit evidence and None when the row has no depth evidence."""
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


def clear_v4_exchange_evidence(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> dict[str, int]:
    """Delete raw V4 pool/liquidity rows for one network scope.

    A live refresh owns the complete raw V4 surface for the requested
    network/chain/protocol.  Clearing before import prevents stale rows from
    a previous bad JSON import (empty liquidity, placeholder symbols, pools
    that no longer exist on chain) from surviving into candidate/observation
    rebuilds.
    """
    loop_optimization_count = execute(
        database_url,
        """
        DELETE FROM v4_closed_loop_optimizations
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    loop_hop_count = execute(
        database_url,
        """
        DELETE FROM v4_closed_loop_hops
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    loop_count = execute(
        database_url,
        """
        DELETE FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    # TASK-172 / AC-221: the depth snapshot table is **not** cleared here.
    #
    # The table has a single writer -- the Rust pool manager -- and that
    # writer materialises a complete row (price columns *and* single-tick
    # bounds) in one statement.  Clearing the table from the evidence
    # channel deleted the pool manager's rows and then wrote nothing back,
    # because the evidence channel no longer materialises rows at all.
    # The scope therefore ended up empty until the next pool-manager
    # refresh, and every consumer of the boundary read "no depth" instead
    # of the measured depth.
    #
    # The pool rows are still cleared: the evidence channel owns the pool
    # index.
    #
    # A pool row that still carries a depth snapshot row cannot go.  The
    # snapshot table carries a foreign key onto the pool table, and the pool
    # manager -- the only writer of that table -- only ever UPDATEs a
    # snapshot row, never inserts one.  Without its pool row the manager
    # could never materialise a measurement for that pool again, so the
    # pool row has to outlive the clear.  The pool rows that no snapshot
    # references are removed as before, so the pool index this channel owns
    # is still reset.
    liquidity_count = 0
    pool_count = execute(
        database_url,
        """
        DELETE FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND NOT EXISTS (
            SELECT 1 FROM v4_exchange_pool_liquidity_snapshots s
             WHERE s.network = v4_exchange_pools.network
               AND s.chain_id = v4_exchange_pools.chain_id
               AND s.protocol = v4_exchange_pools.protocol
               AND s.pool_id = v4_exchange_pools.pool_id
          )
        """,
        (network, chain_id, protocol),
    )
    counts = {
        "closed_loop_optimizations": loop_optimization_count,
        "closed_loop_hops": loop_hop_count,
        "closed_loops": loop_count,
        "liquidity_snapshots": liquidity_count,
        "pools": pool_count,
    }
    counts["dex_surface_snapshots"] = clear_dex_surface_snapshots(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        database_url=database_url,
    )
    return counts


def prune_v4_non_executable_evidence(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    delete_unknown: bool = False,
) -> dict[str, int]:
    """Keep only executable pools plus pools that still need liquidity evidence.

    The raw tables are the lineage source for executable candidates and
    loops, not a bucket for historical zero-liquidity diagnostics.  Only an
    explicit chain read that returned ``has_liquidity=0`` proves a pool is
    empty, so those rows are removed.  Unknown/missing rows are preserved by
    default because they mean the pool has not been scanned, not that it has
    no liquidity.
    """
    def _count(sql: str) -> int:
        rows = query(database_url, sql, (network, chain_id, protocol))
        return int(rows[0]["c"]) if rows else 0

    before_liquidity = _count(
        """
        SELECT COUNT(*) AS c FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """
    )
    before_pools = _count(
        """
        SELECT COUNT(*) AS c FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """
    )
    zero_pool_ids = {
        str(row["pool_id"])
        for row in query(
            database_url,
            """
            SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
            WHERE network = ? AND chain_id = ? AND protocol = ?
              AND has_liquidity = 0
            """,
            (network, chain_id, protocol),
        )
        if str(row.get("pool_id") or "").strip()
    }
    deleted_zero = execute(
        database_url,
        """
        DELETE FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND has_liquidity = 0
        """,
        (network, chain_id, protocol),
    )
    deleted_unknown = 0
    removable_pool_ids = set(zero_pool_ids)
    if delete_unknown:
        removable_pool_ids.update(
            str(row["pool_id"])
            for row in query(
                database_url,
                """
                SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND has_liquidity IS NULL
                """,
                (network, chain_id, protocol),
            )
            if str(row.get("pool_id") or "").strip()
        )
        snapshot_pool_ids = {
            str(row["pool_id"])
            for row in query(
                database_url,
                """
                SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots
                WHERE network = ? AND chain_id = ? AND protocol = ?
                """,
                (network, chain_id, protocol),
            )
            if str(row.get("pool_id") or "").strip()
        }
        removable_pool_ids.update(
            str(row["pool_id"])
            for row in query(
                database_url,
                """
                SELECT pool_id FROM v4_exchange_pools
                WHERE network = ? AND chain_id = ? AND protocol = ?
                """,
                (network, chain_id, protocol),
            )
            if str(row.get("pool_id") or "").strip()
            and str(row.get("pool_id") or "").strip() not in snapshot_pool_ids
        )
        deleted_unknown = execute(
            database_url,
            """
            DELETE FROM v4_exchange_pool_liquidity_snapshots
            WHERE network = ? AND chain_id = ? AND protocol = ?
              AND has_liquidity IS NULL
            """,
            (network, chain_id, protocol),
        )
    # Unknown/missing pools are kept as pending evidence so a later full
    # liquidity scan can resolve them.  Only pools with explicit zero evidence
    # (or forced unknown cleanup) are removed from the raw lineage tables.
    deleted_pools = 0
    if removable_pool_ids:
        placeholders = ",".join("?" for _ in removable_pool_ids)
        deleted_pools = execute(
            database_url,
            f"""
            DELETE FROM v4_exchange_pools
            WHERE network = ? AND chain_id = ? AND protocol = ?
              AND pool_id IN ({placeholders})
            """,
            (network, chain_id, protocol, *sorted(removable_pool_ids)),
        )
    after_liquidity = _count(
        """
        SELECT COUNT(*) AS c FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """
    )
    after_pools = _count(
        """
        SELECT COUNT(*) AS c FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """
    )
    return {
        "beforeLiquidityCount": before_liquidity,
        "beforePoolCount": before_pools,
        "deletedZeroLiquidityCount": deleted_zero,
        "deletedUnknownLiquidityCount": deleted_unknown,
        "deletedPoolCount": deleted_pools,
        "afterLiquidityCount": after_liquidity,
        "afterPoolCount": after_pools,
    }


def upsert_v4_exchange_pools(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    pool_rows: list[dict[str, Any]],
) -> int:
    now = _stamp()
    rows = []
    for pool in pool_rows:
        pool_id = _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))
        token0 = _text(pool.get("token0") or pool.get("currency0"))
        token1 = _text(pool.get("token1") or pool.get("currency1"))
        if not pool_id or not token0 or not token1:
            continue
        fee = _int_none(_first_present(pool.get("fee")))
        dynamic_fee = _bool(fee is not None and fee & 8388608)
        special_fee = _bool(fee is None or fee == 0 or dynamic_fee or (fee or 0) > 100_000)
        fee_label = "dynamic" if dynamic_fee else ("special" if special_fee else fee)
        pool_key = _first_present(pool.get("pool_key"), pool.get("poolKey"))
        pool_manager = _text(pool.get("pool_manager") or pool.get("poolManager"))
        tick_spacing = _int_none(
            _first_present(pool.get("tick_spacing"), pool.get("tickSpacing"))
        )
        hooks = _text(pool.get("hooks"))
        rows.append(
            (
                network,
                chain_id,
                protocol,
                pool_id,
                pool.get("pool_identity_mode") or "v4_pool_id",
                pool_manager or None,
                _json_dumps(pool_key) if pool_key is not None else None,
                token0,
                token1,
                fee,
                fee_label,
                dynamic_fee,
                special_fee,
                tick_spacing,
                hooks or None,
                f"{token0}:{token1}",
                now,
                now,
            )
        )
    if not rows:
        return 0
    return execute_many_upsert(
        database_url,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, pool_id) DO UPDATE SET
            pool_identity_mode = CASE
                WHEN excluded.pool_identity_mode IS NULL
                     OR excluded.pool_identity_mode = ''
                THEN v4_exchange_pools.pool_identity_mode
                ELSE excluded.pool_identity_mode
            END,
            pool_manager = CASE
                WHEN excluded.pool_manager IS NULL OR excluded.pool_manager = ''
                THEN v4_exchange_pools.pool_manager
                ELSE excluded.pool_manager
            END,
            pool_key_json = CASE
                WHEN excluded.pool_key_json IS NULL
                     OR excluded.pool_key_json = ''
                     OR excluded.pool_key_json = 'null'
                     OR excluded.pool_key_json = '{}'
                THEN v4_exchange_pools.pool_key_json
                ELSE excluded.pool_key_json
            END,
            token0 = CASE
                WHEN excluded.token0 IS NULL OR excluded.token0 = ''
                THEN v4_exchange_pools.token0
                ELSE excluded.token0
            END,
            token1 = CASE
                WHEN excluded.token1 IS NULL OR excluded.token1 = ''
                THEN v4_exchange_pools.token1
                ELSE excluded.token1
            END,
            fee = CASE
                WHEN excluded.fee IS NULL
                THEN v4_exchange_pools.fee
                ELSE excluded.fee
            END,
            fee_label = CASE
                WHEN excluded.fee IS NULL
                THEN v4_exchange_pools.fee_label
                ELSE excluded.fee_label
            END,
            dynamic_fee = CASE
                WHEN excluded.fee IS NULL
                THEN v4_exchange_pools.dynamic_fee
                ELSE excluded.dynamic_fee
            END,
            special_fee = CASE
                WHEN excluded.fee IS NULL
                THEN v4_exchange_pools.special_fee
                ELSE excluded.special_fee
            END,
            tick_spacing = CASE
                WHEN excluded.tick_spacing IS NULL
                THEN v4_exchange_pools.tick_spacing
                ELSE excluded.tick_spacing
            END,
            hooks = CASE
                WHEN excluded.hooks IS NULL OR excluded.hooks = ''
                THEN v4_exchange_pools.hooks
                ELSE excluded.hooks
            END,
            address_pair = CASE
                WHEN excluded.address_pair IS NULL OR excluded.address_pair = ''
                THEN v4_exchange_pools.address_pair
                ELSE excluded.address_pair
            END,
            updated_at = CASE
                WHEN (
                    COALESCE(excluded.pool_manager, '') = COALESCE(v4_exchange_pools.pool_manager, '')
                    AND COALESCE(excluded.pool_key_json, '') = COALESCE(v4_exchange_pools.pool_key_json, '')
                    AND excluded.token0 = v4_exchange_pools.token0
                    AND excluded.token1 = v4_exchange_pools.token1
                    AND COALESCE(excluded.fee, -1) = COALESCE(v4_exchange_pools.fee, -1)
                    AND COALESCE(excluded.tick_spacing, -1) = COALESCE(v4_exchange_pools.tick_spacing, -1)
                    AND COALESCE(excluded.hooks, '') = COALESCE(v4_exchange_pools.hooks, '')
                )
                THEN v4_exchange_pools.updated_at
                ELSE excluded.updated_at
            END
        """,
        rows,
    )


def execute_many_upsert(database_url: str, sql: str, rows: list[tuple[Any, ...]]) -> int:
    from core.db.storage_business_io import executemany

    return executemany(database_url, sql, rows)


def upsert_v4_liquidity_snapshots(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    rows: list[dict[str, Any]],
) -> int:
    """Write measured V4 liquidity snapshots.

    The implementation lives in
    :mod:`core.db.storage_v4_liquidity_snapshots`; it is re-exported here so
    existing import sites keep working.
    """
    from core.db.storage_v4_liquidity_snapshots import (
        upsert_v4_liquidity_snapshots as _upsert,
    )

    return _upsert(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        rows=rows,
    )

def load_v4_exchange_pools(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, pool_identity_mode,
               pool_manager, pool_key_json, token0, token1, fee, fee_label,
               dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
               created_at, updated_at
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def load_v4_exchange_pool_rows_for_view(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, pool_manager, token0, token1,
               fee, hooks, tick_spacing
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def load_v4_exchange_pool_pair_counts(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT token0, token1,
               COUNT(DISTINCT CASE
                   WHEN COALESCE(pool_id, '') <> '' THEN pool_id
                   ELSE token0 || ':' || token1 || ':' || COALESCE(CAST(fee AS TEXT), '')
               END) AS pool_count
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        GROUP BY token0, token1
        ORDER BY token0, token1
        """,
        (network, chain_id, protocol),
    )


def load_v4_exchange_pool_manager(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> dict[str, Any]:
    rows = query(
        database_url,
        """
        SELECT pool_manager
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        LIMIT 1
        """,
        (network, chain_id, protocol),
    )
    return rows[0] if rows else {}


def load_v4_pool_liquidity(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, has_liquidity, liquidity,
               liquidity_index, estimated_tvl_usd, balances_json, depth_status,
               depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
               liquidity_read_at, liquidity_block_number, recent_swap_active,
               observed_at, refreshed_at, evidence_source
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def load_v4_pool_liquidity_rows_for_view(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT pool_id, has_liquidity, liquidity, liquidity_index,
               estimated_tvl_usd, depth_status, sqrt_price_x96, tick,
               liquidity_read_at, liquidity_block_number, recent_swap_active,
               observed_at, refreshed_at, evidence_source
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        (network, chain_id, protocol),
    )


def load_v4_pool_liquidity_refreshed_at(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> str | None:
    rows = query(
        database_url,
        """
        SELECT MAX(COALESCE(observed_at, refreshed_at)) AS refreshed_at
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    value = rows[0].get("refreshed_at") if rows else None
    return str(value) if value not in (None, "") else None


def find_v4_pools_between_tokens(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    token_a: str,
    token_b: str,
) -> list[dict[str, Any]]:
    first = _text(token_a)
    second = _text(token_b)
    if first == second:
        return []
    if first > second:
        first, second = second, first
    return query(
        database_url,
        """
        SELECT network, chain_id, protocol, pool_id, pool_identity_mode,
               pool_manager, pool_key_json, token0, token1, fee, fee_label,
               dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
               created_at, updated_at
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND token0 = ? AND token1 = ?
        ORDER BY fee
        """,
        (network, chain_id, protocol, first, second),
    )


__all__ = [
    "clear_v4_exchange_evidence",
    "find_v4_pools_between_tokens",
    "load_v4_exchange_pools",
    "load_v4_exchange_pool_manager",
    "load_v4_exchange_pool_pair_counts",
    "load_v4_exchange_pool_rows_for_view",
    "load_v4_pool_liquidity",
    "load_v4_pool_liquidity_rows_for_view",
    "prune_v4_non_executable_evidence",
    "upsert_v4_exchange_pools",
    "upsert_v4_liquidity_snapshots",
]
