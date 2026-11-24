#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the closed loops of two stores: the baseline build and the new build.

Usage:
    python compare_closed_loop_directional_20261124.py <baseline.db> <new.db> [report.json]
"""

import json
import os
import sqlite3
import statistics
import sys

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def read_loops(path):
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    connection.row_factory = sqlite3.Row
    loops = {}
    for row in connection.execute(
        "SELECT loop_key, anchor_token_key_id, token_y_key_id, token_z_key_id,"
        "       pool_ids_json, single_tick_capacity_anchor_raw,"
        "       single_tick_capacity_binding_pool_id, cross_tick_capacity_anchor_raw,"
        "       borrow_token_usdg_value_raw, borrow_token_decimals,"
        "       cross_tick_review_threshold_usdg_raw, status, source"
        "  FROM manager_v4_closed_loops"
        " WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        " ORDER BY loop_key",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ):
        loops[row["loop_key"]] = dict(row)
    hops = {}
    for row in connection.execute(
        "SELECT h.loop_key, h.hop_index, h.hop_name, h.pool_id, h.base_pool_id,"
        "       h.input_token_key_id, h.output_token_key_id,"
        "       h.directional_pool_id, h.hop_direction, h.directional_scope_key,"
        "       h.directional_pool_rank, h.pool_selection_basis"
        "  FROM manager_v4_closed_loop_hops h"
        " WHERE h.network = ?1 AND h.chain_id = ?2 AND h.protocol = ?3"
        "   AND h.scope_key = ?4"
        " ORDER BY h.loop_key, h.hop_index",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ):
        hops.setdefault(row["loop_key"], []).append(dict(row))
    connection.close()
    return loops, hops


def to_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def relative(before, after):
    if before in (None, 0) or after is None:
        return None
    return (after - before) / abs(before)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    baseline_path, new_path = sys.argv[1], sys.argv[2]
    report_path = sys.argv[3] if len(sys.argv) > 3 else None

    baseline_loops, baseline_hops = read_loops(baseline_path)
    new_loops, new_hops = read_loops(new_path)

    baseline_keys = set(baseline_loops)
    new_keys = set(new_loops)
    removed = sorted(baseline_keys - new_keys)
    added = sorted(new_keys - baseline_keys)
    common = sorted(baseline_keys & new_keys)

    capacity_changes = []
    identical_capacity = 0
    changed_capacity = []
    changed_usdg = []
    changed_binding = []
    changed_pool_hops = 0
    changed_hop_count = 0
    direction_missing = 0
    for key in common:
        before = baseline_loops[key]
        after = new_loops[key]
        before_capacity = to_int(before["single_tick_capacity_anchor_raw"])
        after_capacity = to_int(after["single_tick_capacity_anchor_raw"])
        if before_capacity != after_capacity:
            changed_capacity.append(key)
            change = relative(before_capacity, after_capacity)
            if change is not None:
                capacity_changes.append(change)
        else:
            identical_capacity += 1
        if to_int(before["borrow_token_usdg_value_raw"]) != to_int(after["borrow_token_usdg_value_raw"]):
            changed_usdg.append(key)
        if before["single_tick_capacity_binding_pool_id"] != after["single_tick_capacity_binding_pool_id"]:
            changed_binding.append(key)
        before_hops = baseline_hops.get(key, [])
        after_hops = new_hops.get(key, [])
        if len(before_hops) != len(after_hops):
            changed_hop_count += 1
            continue
        for hop_before, hop_after in zip(before_hops, after_hops):
            if hop_before["pool_id"] != hop_after["pool_id"]:
                changed_pool_hops += 1
            if hop_after["directional_pool_id"] is None or hop_after["hop_direction"] is None:
                direction_missing += 1

    report = {
        "baseline": os.path.abspath(baseline_path),
        "new": os.path.abspath(new_path),
        "loop_count": {"baseline": len(baseline_loops), "new": len(new_loops)},
        "hop_count": {
            "baseline": sum(len(rows) for rows in baseline_hops.values()),
            "new": sum(len(rows) for rows in new_hops.values()),
        },
        "removed_loops": removed,
        "added_loops": added,
        "common_loops": len(common),
        "identical_capacity": identical_capacity,
        "changed_capacity": len(changed_capacity),
        "capacity_relative_change": {
            "count": len(capacity_changes),
            "median": statistics.median(capacity_changes) if capacity_changes else None,
            "mean": statistics.fmean(capacity_changes) if capacity_changes else None,
            "min": min(capacity_changes) if capacity_changes else None,
            "max": max(capacity_changes) if capacity_changes else None,
        },
        "changed_borrow_token_usdg_value": len(changed_usdg),
        "changed_binding_pool": len(changed_binding),
        "changed_hop_pool": changed_pool_hops,
        "changed_hop_count": changed_hop_count,
        "hops_without_direction": direction_missing,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
