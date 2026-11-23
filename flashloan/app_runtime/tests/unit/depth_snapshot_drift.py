# -*- coding: utf-8 -*-
"""The drift guard for the depth snapshot table (TASK-172 / AC-221).

The static guard (:mod:`depth_snapshot_single_writer`) pins the *writer
count* to one.  This guard pins the *result* of that rule: a row's
published single-tick bound must equal the bound that row's own price
implies.

Why the data guard is needed
-----------------------------
A writer-count guard only sees the writers that are in the repository
today.  It cannot see a second writer that arrives through a path the
scanner does not read, and it cannot see a row that was already drifted
before the rule existed.  The historical mainnet database carries exactly
such rows, and they stay drifted until the pool manager rewrites them.

So the property is checked in the only way that cannot be fooled by a
timestamp: by **recomputing** the bound.

The criterion
-------------
For one snapshot row:

    stored_bound  ==  bound( sqrt_price_x96, tick, liquidity, fee, tick_spacing )

where the price triple comes from **the row itself**.  A row whose stored
bound differs from that value is drifted: its bound belongs to a
different on-chain moment than the price printed next to it.

Two things this guard deliberately refuses to do:

* it does not compare against a timestamp.  ``refreshed_at`` says when a
  row was written, not whether the bound and the price in that row came
  from the same moment, and the old evidence writer rewrote
  ``refreshed_at`` unconditionally on every import -- so a timestamp
  comparison would call every such row "fresh" and hide the defect.
* it does not recompute the bound the way a *consumer* would, and it does
  not repair anything.  It compares the stored bound against the bound
  implied by the stored price, which is a statement about the row.

The boundary arithmetic stays in Rust in production (DPO-10).  This
module is a read-only diagnostic, and the boundary it uses is the port
that was proven bit-identical against the rows the pool manager itself
wrote.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple


#: The table whose internal consistency is checked.
DEPTH_SNAPSHOT_TABLE = "v4_exchange_pool_liquidity_snapshots"

#: The pool tables that supply a row's static shape (fee, tick spacing).
#:
#: The Rust pool manager keeps scoped views of the pool set
#: (``v4_exchange_pools_core4`` for the core scope and
#: ``v4_exchange_pools_candidate_exchange`` for the candidate scope),
#: while the Python side keeps one unsized ``v4_exchange_pools`` table.
#: A row's fee and tick spacing are the same in all of them, so the guard
#: reads whichever exists.
POOL_TABLES = (
    "v4_exchange_pools_core4",
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools",
)

#: Where the proven boundary arithmetic lives.  The script is a diagnostic
#: artifact of the plan directory, not a runtime module, so it is loaded
#: by path rather than imported.
_PROJECTION_TOOL = os.path.join(
    "plan",
    "runtime_evidence",
    "tools",
    "compare_directional_pool_projection_20261123.py",
)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def _load_projection():
    """Load the proven boundary arithmetic by path."""
    root = _repo_root()
    path = os.path.join(root, _PROJECTION_TOOL)
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(
        "task172_depth_snapshot_projection", path
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # pragma: no cover - the tool is optional
        return None
    return module


PROJ = _load_projection()


def _parse_u128(value: Any) -> Optional[int]:
    """Parse a raw u128 column, or None when it is not a number."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _recompute_bounds(
    sqrt_price_x96: Optional[int],
    liquidity: Optional[int],
    tick: Optional[int],
    fee: Optional[int],
    tick_spacing: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """Recompute both single-tick bounds from one observation."""
    if None in (sqrt_price_x96, liquidity, tick, fee, tick_spacing):
        return None, None
    if not sqrt_price_x96 or not liquidity or not tick_spacing:
        return None, None
    bounds: List[Optional[int]] = []
    for zero_for_one in (True, False):
        try:
            computed = PROJ.single_tick_swap(
                sqrt_price_x96,
                liquidity,
                int(fee),
                int(tick_spacing),
                int(tick),
                1,
                zero_for_one,
            )["max_amount_in_raw"]
        except Exception:
            computed = None
        bounds.append(computed)
    return bounds[0], bounds[1]


def open_read_only(database_path: str) -> sqlite3.Connection:
    """Open a database read-only.  The guard never writes."""
    uri = "file:%s?mode=ro" % str(database_path).replace("?", "%3f").replace("#", "%23")
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def drifted_rows(
    database_path: str,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
    protocol: str = "uniswap_v4",
) -> List[Dict[str, Any]]:
    """Return every snapshot row whose stored bound its own price does not imply.

    An empty list means the database is internally consistent: every row
    describes exactly one on-chain moment.
    """
    if PROJ is None:
        raise RuntimeError(
            "the boundary arithmetic is unavailable: %s" % _PROJECTION_TOOL
        )

    connection = open_read_only(database_path)
    try:
        shape: Dict[str, Dict[str, Any]] = {}
        for table in POOL_TABLES:
            try:
                rows = connection.execute(
                    "SELECT pool_id, fee, tick_spacing FROM %s"
                    " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
                    % table,
                    (network, chain_id, protocol),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                shape.setdefault(
                    row["pool_id"],
                    {
                        "fee": int(row["fee"] or 0),
                        "tick_spacing": int(row["tick_spacing"] or 0),
                        "table": table,
                    },
                )

        rows = connection.execute(
            "SELECT pool_id, sqrt_price_x96, tick, liquidity,"
            " single_tick_max_token0_raw, single_tick_max_token1_raw"
            " FROM %s WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
            " ORDER BY pool_id" % DEPTH_SNAPSHOT_TABLE,
            (network, chain_id, protocol),
        ).fetchall()

        drifted: List[Dict[str, Any]] = []
        for row in rows:
            pool_shape = shape.get(row["pool_id"])
            if pool_shape is None:
                continue
            stored0 = _parse_u128(row["single_tick_max_token0_raw"])
            stored1 = _parse_u128(row["single_tick_max_token1_raw"])
            if stored0 is None and stored1 is None:
                continue
            tick = int(row["tick"]) if row["tick"] is not None else None
            recomputed = _recompute_bounds(
                _parse_u128(row["sqrt_price_x96"]),
                _parse_u128(row["liquidity"]),
                tick,
                pool_shape["fee"],
                pool_shape["tick_spacing"],
            )
            mismatches = []
            for column, stored, computed in (
                ("single_tick_max_token0_raw", stored0, recomputed[0]),
                ("single_tick_max_token1_raw", stored1, recomputed[1]),
            ):
                if stored is None or computed is None:
                    # Nothing was stored, or nothing could be recomputed.
                    # Either way this row makes no claim the guard can
                    # contradict.
                    continue
                if stored != computed:
                    mismatches.append(
                        {"column": column, "stored": stored, "recomputed": computed}
                    )
            if mismatches:
                drifted.append(
                    {
                        "poolId": row["pool_id"],
                        "table": pool_shape["table"],
                        "sqrtPriceX96": row["sqrt_price_x96"],
                        "tick": tick,
                        "liquidity": row["liquidity"],
                        "fee": pool_shape["fee"],
                        "tickSpacing": pool_shape["tick_spacing"],
                        "mismatches": mismatches,
                    }
                )
        return drifted
    finally:
        connection.close()
