#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure what the direction-layer primary order changes on real data.

The two stores handed to the tool are the **same** chain-store slice
materialized twice: once with the legacy order of the candidate tier and
once with the direction layer as the authority of the primary pool.  The tool
reports the difference of the two publications, hop by hop and loop by loop.

Nothing is written: both stores are opened read-only.

Usage:
    python compare_directional_primary_order_effect_20261125.py <before.db> <after.db> [report.json]
"""

import json
import os
import sqlite3
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def open_readonly(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f")
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def read_loops(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    sql = (
        "SELECT loop_key, anchor_symbol, token_y_key_id, token_z_key_id, "
        "       pool_ids_json, backup_pool_ids_json, status, "
        "       single_tick_capacity_anchor_raw, single_tick_capacity_binding_pool_id, "
        "       borrow_token_usdg_value_raw, cross_tick_capacity_anchor_raw "
        "FROM manager_v4_closed_loops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        rows[raw[0]] = {
            "loopKey": raw[0],
            "anchorSymbol": raw[1],
            "tokenY": raw[2],
            "tokenZ": raw[3],
            "poolIds": json.loads(raw[4] or "[]"),
            "backupPoolIds": json.loads(raw[5] or "[]"),
            "status": raw[6],
            "capacityRaw": raw[7],
            "capacityBindingPoolId": raw[8],
            "borrowTokenUsdgValueRaw": raw[9],
            "crossTickCapacityRaw": raw[10],
        }
    return rows


def read_hops(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    sql = (
        "SELECT loop_key, hop_index, hop_name, pool_id, base_pool_id, "
        "       pool_selection_basis, directional_pool_id, hop_direction, "
        "       directional_pool_rank, optimization_id, temporary_override "
        "FROM manager_v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        rows["%s|%s" % (raw[0], raw[1])] = {
            "loopKey": raw[0],
            "hopIndex": raw[1],
            "hopName": raw[2],
            "poolId": raw[3],
            "basePoolId": raw[4],
            "poolSelectionBasis": raw[5],
            "directionalPoolId": raw[6],
            "hopDirection": raw[7],
            "directionalPoolRank": raw[8],
            "optimizationId": raw[9],
            "temporaryOverride": bool(raw[10]),
        }
    return rows


def read_optimizations(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    sql = (
        "SELECT loop_key, hop_index, optimization_id, original_pool_id, "
        "       optimized_pool_id, optimization_basis, status "
        "FROM manager_v4_closed_loop_optimizations "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        rows["%s|%s|%s" % (raw[0], raw[1], raw[2])] = {
            "loopKey": raw[0],
            "hopIndex": raw[1],
            "optimizationId": raw[2],
            "originalPoolId": raw[3],
            "optimizedPoolId": raw[4],
            "optimizationBasis": raw[5],
            "status": raw[6],
        }
    return rows


def read_skipped(connection: sqlite3.Connection) -> List[Tuple[Any, ...]]:
    sql = (
        "SELECT anchor_token_key_id, token_key_id, candidate_role, reason "
        "FROM manager_v4_closed_loop_skipped_tokens "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
    )
    return sorted(connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)))


def read_directional_rates(connection: sqlite3.Connection) -> Dict[str, float]:
    """The output rate of every published directional pool.

    The rate is the output per unit of input, normalised to decimal units,
    so the product of the three hops of a loop is the gross round-trip
    multiplier of that loop.
    """
    sql = (
        "SELECT directional_pool_id, directional_output "
        "FROM manager_v4_directional_exchange_pools "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND directional_output IS NOT NULL"
    )
    rates: Dict[str, float] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL)):
        try:
            rates[raw[0]] = float(raw[1])
        except (TypeError, ValueError):
            continue
    return rates


def as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 2:
        print(json.dumps({"ok": False, "error": "usage: <before.db> <after.db> [report.json]"}))
        return 2
    before_path, after_path = arguments[0], arguments[1]
    report_path = arguments[2] if len(arguments) > 2 else None
    before = open_readonly(before_path)
    after = open_readonly(after_path)
    try:
        before_loops = read_loops(before)
        after_loops = read_loops(after)
        before_hops = read_hops(before)
        after_hops = read_hops(after)
        before_optimizations = read_optimizations(before)
        after_optimizations = read_optimizations(after)
        before_skipped = read_skipped(before)
        after_skipped = read_skipped(after)
        before_rates = read_directional_rates(before)
        after_rates = read_directional_rates(after)

        loop_keys = sorted(set(before_loops) | set(after_loops))
        hop_keys = sorted(set(before_hops) | set(after_hops))

        hop_pool_changed: List[Dict[str, Any]] = []
        hop_rank_distribution_before: Counter = Counter()
        hop_rank_distribution_after: Counter = Counter()
        by_hop_name: Dict[str, Counter] = {}
        for key in hop_keys:
            left = before_hops.get(key)
            right = after_hops.get(key)
            if left is None or right is None:
                continue
            name = left["hopName"]
            counter = by_hop_name.setdefault(name, Counter())
            counter["hops"] += 1
            hop_rank_distribution_before[str(left["directionalPoolRank"])] += 1
            hop_rank_distribution_after[str(right["directionalPoolRank"])] += 1
            if left["poolId"] != right["poolId"]:
                counter["poolChanged"] += 1
                hop_pool_changed.append(
                    {
                        "loopKey": left["loopKey"],
                        "hopIndex": left["hopIndex"],
                        "hopName": name,
                        "beforePoolId": left["poolId"],
                        "afterPoolId": right["poolId"],
                        "beforeRank": left["directionalPoolRank"],
                        "afterRank": right["directionalPoolRank"],
                        "afterBasis": right["poolSelectionBasis"],
                        "afterTemporaryOverride": right["temporaryOverride"],
                    }
                )
            else:
                counter["poolUnchanged"] += 1

        capacity_changed: List[Dict[str, Any]] = []
        capacity_better = 0
        capacity_worse = 0
        capacity_same = 0
        capacity_total_before = 0
        capacity_total_after = 0
        capacity_known = 0
        for key in loop_keys:
            left = before_loops.get(key)
            right = after_loops.get(key)
            if left is None or right is None:
                continue
            before_capacity = as_int(left["capacityRaw"])
            after_capacity = as_int(right["capacityRaw"])
            if before_capacity is not None and after_capacity is not None:
                capacity_known += 1
                capacity_total_before += before_capacity
                capacity_total_after += after_capacity
                if after_capacity > before_capacity:
                    capacity_better += 1
                elif after_capacity < before_capacity:
                    capacity_worse += 1
                else:
                    capacity_same += 1
                if before_capacity != after_capacity:
                    capacity_changed.append(
                        {
                            "loopKey": key,
                            "anchorSymbol": left["anchorSymbol"],
                            "beforeRaw": str(before_capacity),
                            "afterRaw": str(after_capacity),
                            "deltaRaw": str(after_capacity - before_capacity),
                            "deltaPercent": (
                                None
                                if before_capacity == 0
                                else round(
                                    (after_capacity - before_capacity) * 100.0 / before_capacity, 6
                                )
                            ),
                            "beforeBindingPoolId": left["capacityBindingPoolId"],
                            "afterBindingPoolId": right["capacityBindingPoolId"],
                        }
                    )

        # The price quality of a loop: the product of the output rates of the
        # three hops it walks.  It is the gross round-trip multiplier, so it
        # is the profit proxy the closed-loop layer can measure without a
        # live price probe.
        def loop_rate(
            hops: Dict[str, Dict[str, Any]], rates: Dict[str, float], loop_key: str
        ) -> Optional[float]:
            product = 1.0
            seen = 0
            for hop_index in (1, 2, 3):
                hop = hops.get("%s|%s" % (loop_key, hop_index))
                if hop is None:
                    return None
                rate = rates.get(hop["directionalPoolId"] or "")
                if rate is None:
                    return None
                product *= rate
                seen += 1
            return product if seen == 3 else None

        rate_rows: List[Dict[str, Any]] = []
        rate_better = 0
        rate_worse = 0
        rate_same = 0
        rate_known = 0
        rate_total_before = 0.0
        rate_total_after = 0.0
        for key in loop_keys:
            before_rate = loop_rate(before_hops, before_rates, key)
            after_rate = loop_rate(after_hops, after_rates, key)
            if before_rate is None or after_rate is None:
                continue
            rate_known += 1
            rate_total_before += before_rate
            rate_total_after += after_rate
            if after_rate > before_rate:
                rate_better += 1
            elif after_rate < before_rate:
                rate_worse += 1
            else:
                rate_same += 1
            if before_rate != after_rate:
                rate_rows.append(
                    {
                        "loopKey": key,
                        "anchorSymbol": (before_loops.get(key) or {}).get("anchorSymbol"),
                        "before": before_rate,
                        "after": after_rate,
                        "delta": after_rate - before_rate,
                        "deltaPercent": (
                            None
                            if before_rate == 0
                            else round((after_rate - before_rate) * 100.0 / before_rate, 6)
                        ),
                    }
                )

        affected_loops = sorted({row["loopKey"] for row in hop_pool_changed})
        payload: Dict[str, Any] = {
            "ok": True,
            "before": before_path,
            "after": after_path,
            "loops": {
                "before": len(before_loops),
                "after": len(after_loops),
                "added": sorted(set(after_loops) - set(before_loops)),
                "removed": sorted(set(before_loops) - set(after_loops)),
                "poolSetChanged": sorted(
                    key
                    for key in loop_keys
                    if key in before_loops
                    and key in after_loops
                    and before_loops[key]["poolIds"] != after_loops[key]["poolIds"]
                ),
            },
            "hops": {
                "before": len(before_hops),
                "after": len(after_hops),
                "added": sorted(set(after_hops) - set(before_hops)),
                "removed": sorted(set(before_hops) - set(after_hops)),
                "poolChanged": len(hop_pool_changed),
                "poolChangedPercent": round(
                    100.0 * len(hop_pool_changed) / max(1, len(hop_keys)), 4
                ),
                "byHopName": {name: dict(counter) for name, counter in sorted(by_hop_name.items())},
                "rankDistributionBefore": dict(hop_rank_distribution_before.most_common()),
                "rankDistributionAfter": dict(hop_rank_distribution_after.most_common()),
                "changed": hop_pool_changed,
            },
            "capacity": {
                "loopsWithCapacity": capacity_known,
                "better": capacity_better,
                "worse": capacity_worse,
                "same": capacity_same,
                "totalBeforeRaw": str(capacity_total_before),
                "totalAfterRaw": str(capacity_total_after),
                "totalDeltaRaw": str(capacity_total_after - capacity_total_before),
                "totalDeltaPercent": (
                    None
                    if capacity_total_before == 0
                    else round(
                        (capacity_total_after - capacity_total_before)
                        * 100.0
                        / capacity_total_before,
                        6,
                    )
                ),
                "changed": capacity_changed,
            },
            "affectedLoops": {
                "count": len(affected_loops),
                "percentOfLoops": round(100.0 * len(affected_loops) / max(1, len(loop_keys)), 4),
                "loopKeys": affected_loops,
            },
            "skippedTokens": {
                "before": len(before_skipped),
                "after": len(after_skipped),
                "identical": before_skipped == after_skipped,
                "onlyBefore": [list(row) for row in sorted(set(before_skipped) - set(after_skipped))],
                "onlyAfter": [list(row) for row in sorted(set(after_skipped) - set(before_skipped))],
            },
            "optimizations": {
                "before": len(before_optimizations),
                "after": len(after_optimizations),
                "statusBefore": dict(
                    Counter(row["status"] for row in before_optimizations.values()).most_common()
                ),
                "statusAfter": dict(
                    Counter(row["status"] for row in after_optimizations.values()).most_common()
                ),
                # The candidate list of a hop is its primary plus its
                # optimizations, so the membership is compared over that
                # union: the primary of the hop moves, the list does not.
                "candidateMembershipPerHopIdentical": all(
                    set(
                        row["optimizedPoolId"]
                        for row in before_optimizations.values()
                        if row["loopKey"] == loop_key and row["hopIndex"] == hop_index
                    )
                    | {
                        row["basePoolId"]
                        for row in before_hops.values()
                        if row["loopKey"] == loop_key and row["hopIndex"] == hop_index
                    }
                    == set(
                        row["optimizedPoolId"]
                        for row in after_optimizations.values()
                        if row["loopKey"] == loop_key and row["hopIndex"] == hop_index
                    )
                    | {
                        row["basePoolId"]
                        for row in after_hops.values()
                        if row["loopKey"] == loop_key and row["hopIndex"] == hop_index
                    }
                    for loop_key, hop_index in {
                        (row["loopKey"], row["hopIndex"]) for row in before_optimizations.values()
                    }
                ),
            },
            "priceQuality": {
                "loopsWithRate": rate_known,
                "better": rate_better,
                "worse": rate_worse,
                "same": rate_same,
                "totalBefore": rate_total_before,
                "totalAfter": rate_total_after,
                "totalDelta": rate_total_after - rate_total_before,
                "totalDeltaPercent": (
                    None
                    if rate_total_before == 0
                    else round(
                        (rate_total_after - rate_total_before) * 100.0 / rate_total_before, 6
                    )
                ),
                "changed": rate_rows,
            },
        }
    finally:
        before.close()
        after.close()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if report_path:
        with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
