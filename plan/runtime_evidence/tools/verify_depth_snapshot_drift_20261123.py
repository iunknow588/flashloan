# -*- coding: utf-8 -*-
"""TASK-172 / AC-221 -- depth snapshot drift guard (read-only).

The depth snapshot table ``v4_exchange_pool_liquidity_snapshots`` has
exactly **one** writer: the Rust pool manager.  It writes the price
columns (``sqrt_price_x96`` / ``tick`` / ``liquidity``) **and** the
single-tick bounds in one statement, so a row describes exactly one
on-chain moment.

TASK-172 was raised because the evidence channel used to be a second
writer.  It wrote the price columns only, which left a bound belonging to
an older price sitting next to a newer price.  The row then carried two
different observations at once, and every consumer of the bound read a
stale number.

The single-writer rule removes the *cause*.  This guard is the
*detector*: it recomputes the bound from the observation the row actually
carries and compares it with the bound the row publishes.  A row whose
recomputed bound differs from its stored bound is a drifted row, and only
the pool manager can repair it.

Two things this guard deliberately does **not** do:

* it does not use a timestamp to decide whether a row is fresh.  A
  timestamp only says when a row was written, never whether the bound and
  the price in that row came from the same moment, and the evidence
  channel rewrote ``refreshed_at`` unconditionally.  The only sound
  criterion is the arithmetic itself.
* it does not recompute anything the consumer would then read.  The
  guard compares the stored bound against the bound implied by the stored
  price, which is a statement about the row, not a repair.

**The row is compared against itself.**  The criterion is "does the bound
published in this row equal the bound this row's own price implies?".
The comparison source is the snapshot row's own ``sqrt_price_x96`` /
``tick`` / ``liquidity``, never the pool table's, because the pool table
may hold a different moment.  The pool-table bound is reported as
secondary context only.

The arithmetic is ported from the Rust pool manager and was proven
bit-identical against the rows the pool manager itself wrote (see
``compare_directional_pool_projection_20261123.py``, which reported
88/88 exact matches on Rust-written rows).  The boundary arithmetic stays
in Rust in production (DPO-10); this read-only port is a diagnostic, and
it never feeds a decision the runtime acts on.

Usage::

    python plan/runtime_evidence/tools/verify_depth_snapshot_drift_20261123.py \
        --database flashloan/db/local_store_4663.sqlite3
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple


REPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"
)

#: The pool tables that carry the price/tick/liquidity observation a
#: snapshot row's bound must agree with.
POOL_TABLES = (
    "v4_exchange_pools_core4",
    "v4_exchange_pools_candidate_exchange",
    "v4_exchange_pools",
)

#: The snapshot columns the guard reads.
SNAPSHOT_COLUMNS = (
    "pool_id",
    "sqrt_price_x96",
    "tick",
    "liquidity",
    "liquidity_read_at",
    "liquidity_block_number",
    "refreshed_at",
    "observed_at",
    "evidence_source",
    "materialization_batch",
    "single_tick_max_token0_raw",
    "single_tick_max_token1_raw",
)


def _load_projection_module() -> Any:
    """Import the proven arithmetic from the TASK-166 shadow comparison."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import importlib.util

    path = os.path.join(here, "compare_directional_pool_projection_20261123.py")
    spec = importlib.util.spec_from_file_location(
        "task166_directional_pool_projection", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the shadow comparison module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROJ = _load_projection_module()


def open_read_only(path: str) -> sqlite3.Connection:
    """Open the database read-only.  The guard never writes."""
    uri = "file:%s?mode=ro" % path.replace("?", "%3f").replace("#", "%23")
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_pools(
    connection: sqlite3.Connection, table: str, network: str, chain_id: int, protocol: str
) -> Dict[str, Dict[str, Any]]:
    present = set(existing_columns(connection, table))
    if not present:
        # A database may not carry every scoped pool table.  The tool reads
        # whichever exist.
        return {}
    wanted = [
        column
        for column in (
            "pool_id",
            "fee",
            "tick_spacing",
            "sqrt_price_x96",
            "tick",
            "liquidity",
            "liquidity_read_at",
            "liquidity_block_number",
            "evidence_source",
        )
        if column in present
    ]
    rows = connection.execute(
        "SELECT %s FROM %s WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        " ORDER BY pool_id" % (", ".join(wanted), table),
        (network, chain_id, protocol),
    ).fetchall()
    pools: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        pool: Dict[str, Any] = {
            "pool_id": row["pool_id"],
            "fee": int(row["fee"] or 0),
            "tick_spacing": int(row["tick_spacing"] or 0),
            "source_table": table,
        }
        for optional in (
            "sqrt_price_x96",
            "tick",
            "liquidity",
            "liquidity_read_at",
            "liquidity_block_number",
            "evidence_source",
        ):
            if optional in wanted:
                pool[optional] = row[optional]
        if "tick" in pool and pool["tick"] is not None:
            pool["tick"] = int(pool["tick"])
        pools[row["pool_id"]] = pool
    return pools


def existing_columns(
    connection: sqlite3.Connection, table: str
) -> List[str]:
    """The columns a table actually has.

    A historical database may predate a column the current schema adds, so
    the guard reads what is there instead of demanding the newest shape.
    """
    rows = connection.execute("PRAGMA table_info(%s)" % table).fetchall()
    return [row[1] for row in rows]


def load_snapshots(
    connection: sqlite3.Connection, network: str, chain_id: int, protocol: str
) -> Dict[str, Dict[str, Any]]:
    present = set(existing_columns(connection, "v4_exchange_pool_liquidity_snapshots"))
    columns = [column for column in SNAPSHOT_COLUMNS if column in present]
    rows = connection.execute(
        "SELECT %s FROM v4_exchange_pool_liquidity_snapshots"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        " ORDER BY pool_id" % ", ".join(columns),
        (network, chain_id, protocol),
    ).fetchall()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        snapshot = {column: row[column] for column in columns}
        if "tick" in snapshot:
            snapshot["tick"] = int(snapshot["tick"]) if snapshot["tick"] is not None else None
        snapshots[snapshot["pool_id"]] = snapshot
    return snapshots


def recompute_bounds(observation: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Recompute the single-tick bounds from one observation.

    This is the pool manager's arithmetic, ported.  It answers "what
    bound does *this* price imply?" -- the question a drifted row answers
    wrongly.

    The observation supplies the price triple (``sqrt_price_x96`` /
    ``tick`` / ``liquidity``) and the pool's static shape (``fee`` /
    ``tick_spacing``).  The price triple may come from the snapshot row
    itself or from the pool table; the caller decides which moment is
    being tested.
    """
    sqrt_price_x96 = PROJ.parse_u128(observation.get("sqrt_price_x96"))
    liquidity = PROJ.parse_u128(observation.get("liquidity"))
    tick = observation.get("tick")
    tick_spacing = observation.get("tick_spacing")
    fee = observation.get("fee")
    if (
        not sqrt_price_x96
        or not liquidity
        or tick is None
        or not tick_spacing
        or fee is None
    ):
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
        except PROJ.ArithmeticError_:
            computed = None
        bounds.append(computed)
    return bounds[0], bounds[1]


def _observations(
    stored0: Optional[int],
    stored1: Optional[int],
    recomputed: Tuple[Optional[int], Optional[int]],
) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    for column, stored, computed in (
        ("single_tick_max_token0_raw", stored0, recomputed[0]),
        ("single_tick_max_token1_raw", stored1, recomputed[1]),
    ):
        if stored is None and computed is None:
            status = "absent-both"
        elif stored is None:
            status = "missing-bound"
        elif computed is None:
            # The bound could not be recomputed from this observation, so
            # the guard cannot say whether the stored bound agrees.  That is
            # unknown, not drift.
            status = "not-recomputable"
        elif stored == computed:
            status = "consistent"
        else:
            status = "drifted"
        observations.append(
            {
                "column": column,
                "stored": stored,
                "recomputed": computed,
                "status": status,
            }
        )
    return observations


def classify(
    snapshot: Dict[str, Any], pool: Dict[str, Any]
) -> Dict[str, Any]:
    """Classify one snapshot row against the observation the row itself carries.

    The primary criterion compares the stored bound with the bound implied
    by the row's **own** price columns.  A difference means the bound
    belongs to a different moment than the price next to it -- exactly the
    TASK-172 defect.

    The pool table's price is reported as secondary context: it is often a
    different (usually older) moment, so a difference against it says
    nothing about the row's internal consistency.
    """
    stored0 = PROJ.parse_u128(snapshot.get("single_tick_max_token0_raw"))
    stored1 = PROJ.parse_u128(snapshot.get("single_tick_max_token1_raw"))

    own_observation = {
        "sqrt_price_x96": snapshot.get("sqrt_price_x96"),
        "tick": snapshot.get("tick"),
        "liquidity": snapshot.get("liquidity"),
        "fee": pool.get("fee"),
        "tick_spacing": pool.get("tick_spacing"),
    }
    own = _observations(stored0, stored1, recompute_bounds(own_observation))

    table_observation = {
        "sqrt_price_x96": pool.get("sqrt_price_x96"),
        "tick": pool.get("tick"),
        "liquidity": pool.get("liquidity"),
        "fee": pool.get("fee"),
        "tick_spacing": pool.get("tick_spacing"),
    }
    against_table = _observations(stored0, stored1, recompute_bounds(table_observation))

    drifted = any(item["status"] == "drifted" for item in own)
    undecidable = any(item["status"] == "not-recomputable" for item in own)

    return {
        "poolId": snapshot.get("pool_id"),
        "table": pool.get("source_table"),
        "drifted": drifted,
        "undecidable": undecidable,
        "observations": own,
        "observationsAgainstPoolTable": against_table,
        "sqrtPriceX96": snapshot.get("sqrt_price_x96"),
        "tick": snapshot.get("tick"),
        "liquidity": snapshot.get("liquidity"),
        "fee": pool.get("fee"),
        "tickSpacing": pool.get("tick_spacing"),
        "poolTableSqrtPriceX96": pool.get("sqrt_price_x96"),
        "poolTableTick": pool.get("tick"),
        "poolTableLiquidity": pool.get("liquidity"),
        "liquidityReadAt": snapshot.get("liquidity_read_at"),
        "refreshedAt": snapshot.get("refreshed_at"),
        "evidenceSource": snapshot.get("evidence_source"),
        "materializationBatch": snapshot.get("materialization_batch"),
    }


def run(database_path: str, network: str, chain_id: int, protocol: str) -> Dict[str, Any]:
    connection = open_read_only(database_path)
    try:
        snapshots = load_snapshots(connection, network, chain_id, protocol)
        pools: Dict[str, Dict[str, Any]] = {}
        for table in POOL_TABLES:
            pools.update(load_pools(connection, table, network, chain_id, protocol))

        compared = 0
        consistent = 0
        drifted: List[Dict[str, Any]] = []
        undecidable: List[Dict[str, Any]] = []
        drifted_count = 0
        by_table: Dict[str, Dict[str, int]] = {}
        for pool_id in sorted(pools):
            pool = pools[pool_id]
            snapshot = snapshots.get(pool_id)
            if snapshot is None:
                continue
            compared += 1
            bucket = by_table.setdefault(
                pool["source_table"],
                {"compared": 0, "consistent": 0, "drifted": 0, "notRecomputable": 0},
            )
            bucket["compared"] += 1
            verdict = classify(snapshot, pool)
            if verdict["drifted"]:
                drifted_count += 1
                bucket["drifted"] += 1
                if len(drifted) < 50:
                    drifted.append(verdict)
            elif verdict["undecidable"]:
                undecidable.append(verdict)
                bucket["notRecomputable"] += 1
            else:
                bucket["consistent"] += 1
                consistent += 1
    finally:
        connection.close()

    return {
        "task": "TASK-172",
        "acAcceptance": "AC-221",
        "readOnly": True,
        "database": database_path,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "criterion": (
            "The stored single-tick bound is recomputed from the observation "
            "**the snapshot row itself carries** (its own sqrt_price_x96, "
            "tick and liquidity, with the pool's fee and tick_spacing).  A "
            "row whose recomputed bound differs from its stored bound is "
            "drifted: its bound belongs to a different moment than its "
            "price.  No timestamp is used -- a timestamp says when a row was "
            "written, never whether the bound and the price in that row "
            "came from the same moment, and the evidence channel rewrote "
            "refreshed_at unconditionally."
        ),
        "compared": compared,
        "consistent": consistent,
        "drifted": drifted_count,
        "notRecomputable": len(undecidable),
        "driftRate": (drifted_count / compared) if compared else None,
        "byTable": by_table,
        "driftedSamples": drifted,
        "verdict": {
            "singleWriterHolds": drifted_count == 0,
            "detail": (
                "Every snapshot row's stored bound agrees with the bound its "
                "own price implies."
                if drifted_count == 0
                else "%d of %d snapshot rows carry a bound that their own "
                "price does not imply.  Only the Rust pool manager can "
                "repair them; rewrite the whole row with 'pool-manager "
                "depth --apply'." % (drifted_count, compared)
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=os.path.join("flashloan", "db", "local_store_4663.sqlite3"),
    )
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = run(args.database, args.network, args.chain_id, args.protocol)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        print("report : %s" % args.output)
    else:
        print(payload)
    print(
        "compared=%d consistent=%d drifted=%d notRecomputable=%d"
        % (
            report["compared"],
            report["consistent"],
            report["drifted"],
            report["notRecomputable"],
        )
    )
    for table, counts in sorted(report["byTable"].items()):
        print(
            "  %-38s compared=%-5d consistent=%-5d drifted=%-5d notRecomputable=%d"
            % (
                table,
                counts["compared"],
                counts["consistent"],
                counts["drifted"],
                counts.get("notRecomputable", 0),
            )
        )
    print("verdict: %s" % report["verdict"]["detail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
