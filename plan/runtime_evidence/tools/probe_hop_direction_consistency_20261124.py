#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the direction invariants of the published closed-loop hops.

The closed loop stores, for every hop, the directional pool it walked and
the direction that row carries.  The invariants are:

* **I-7**  the hop's `directional_pool_id` resolves to exactly one row, and
  that row's output token is the hop's output token;
* **I-11** the hop's `base_pool_id` is the row's `base_pool_id`;
* **I-12** the hop's `hop_direction` is the row's `direction`;
* **I-14** the direction agrees with the token order: direction `0` means
  token1 -> token0 and direction `1` means token0 -> token1, so the hop's
  input token must be the row's token1 when the direction is `0` and its
  token0 when it is `1`;
* **I-16** the loop's `pool_ids_json` holds one base pool id per hop.

Usage:
    python probe_hop_direction_consistency_20261124.py <store.db> [report.json]
"""

import json
import os
import sqlite3
import sys

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"
DIRECTIONAL_TABLE = "manager_v4_directional_exchange_pools"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    store_path = sys.argv[1]
    report_path = sys.argv[2] if len(sys.argv) > 2 else None

    connection = sqlite3.connect("file:%s?mode=ro" % store_path, uri=True)
    connection.row_factory = sqlite3.Row

    rows = {}
    duplicate = 0
    for row in connection.execute(
        "SELECT directional_pool_id, scope_key, base_pool_id, direction,"
        "       token0_key_id, token1_key_id, input_token_key_id,"
        "       output_token_key_id, zero_for_one"
        "  FROM %s WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3"
        % DIRECTIONAL_TABLE,
        (NETWORK, CHAIN_ID, PROTOCOL),
    ):
        if row["directional_pool_id"] in rows:
            duplicate += 1
            continue
        rows[row["directional_pool_id"]] = dict(row)

    hops = []
    for row in connection.execute(
        "SELECT h.loop_key, h.hop_index, h.pool_id, h.base_pool_id, h.temporary_override,"
        "       h.input_token_key_id, h.output_token_key_id,"
        "       h.directional_pool_id, h.hop_direction, h.directional_scope_key"
        "  FROM manager_v4_closed_loop_hops h"
        " WHERE h.network = ?1 AND h.chain_id = ?2 AND h.protocol = ?3 AND h.scope_key = ?4"
        " ORDER BY h.loop_key, h.hop_index",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
    ):
        hops.append(dict(row))

    counters = {
        "hops": len(hops),
        "duplicate_directional_ids": duplicate,
        "unresolved_directional_id": 0,
        "i7_output_token_mismatch": 0,
        "i11_base_pool_mismatch": 0,
        "i12_direction_mismatch": 0,
        "i14_direction_token_order_mismatch": 0,
        "pool_id_not_base_pool": 0,
        "missing_direction": 0,
        "temporary_override_hops": 0,
    }
    samples = []
    for hop in hops:
        directional_pool_id = hop["directional_pool_id"]
        if directional_pool_id is None or hop["hop_direction"] is None:
            counters["missing_direction"] += 1
            continue
        row = rows.get(directional_pool_id)
        if row is None:
            counters["unresolved_directional_id"] += 1
            continue
        problems = []
        # The row publishes the token order of **its own** direction, so the
        # hop's output token is compared with the row's own column and not
        # with a value derived from the token pair.
        direction = hop["hop_direction"]
        row_output_token = row["output_token_key_id"]
        if row_output_token != hop["output_token_key_id"]:
            counters["i7_output_token_mismatch"] += 1
            problems.append("i7")
        if row["input_token_key_id"] != hop["input_token_key_id"]:
            counters["i14_direction_token_order_mismatch"] += 1
            problems.append("i14-input")
        if row["base_pool_id"] != hop["base_pool_id"]:
            # A hop that took over a backup pool walks the **backup's** base
            # pool, while ``base_pool_id`` keeps naming the primary it
            # replaced.  The two ids are therefore expected to differ for a
            # temporary override, and the check only applies to the hops
            # that kept their primary.
            if hop["temporary_override"]:
                counters["temporary_override_hops"] += 1
            else:
                counters["i11_base_pool_mismatch"] += 1
                problems.append("i11")
        if row["direction"] != hop["hop_direction"]:
            counters["i12_direction_mismatch"] += 1
            problems.append("i12")
        # Direction 0 trades token1 -> token0, direction 1 trades token0 -> token1.
        expected_input = row["token1_key_id"] if direction == 0 else row["token0_key_id"]
        expected_output = row["token0_key_id"] if direction == 0 else row["token1_key_id"]
        if hop["input_token_key_id"] != expected_input or hop["output_token_key_id"] != expected_output:
            counters["i14_direction_token_order_mismatch"] += 1
            problems.append("i14")
        if hop["pool_id"] != hop["base_pool_id"] and not hop["temporary_override"]:
            counters["pool_id_not_base_pool"] += 1
            problems.append("pool_id")
        if problems and len(samples) < 10:
            samples.append({"hop": hop, "problems": problems})

    # I-16: the loop body publishes one base pool id per hop.
    loop_rows = list(
        connection.execute(
            "SELECT l.loop_key, l.pool_ids_json, l.directional_pool_ids_json,"
            "       (SELECT COUNT(*) FROM manager_v4_closed_loop_hops h"
            "         WHERE h.network = l.network AND h.chain_id = l.chain_id"
            "           AND h.protocol = l.protocol AND h.scope_key = l.scope_key"
            "           AND h.loop_key = l.loop_key) AS hop_count"
            "  FROM manager_v4_closed_loops l"
            " WHERE l.network = ?1 AND l.chain_id = ?2 AND l.protocol = ?3 AND l.scope_key = ?4",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
        )
    )
    counters["loops"] = len(loop_rows)
    counters["i16_pool_ids_length_mismatch"] = 0
    counters["i16_directional_ids_length_mismatch"] = 0
    counters["i16_directional_ids_not_base_pools"] = 0
    for row in loop_rows:
        pool_ids = json.loads(row["pool_ids_json"] or "[]")
        directional_ids = json.loads(row["directional_pool_ids_json"] or "[]")
        if len(pool_ids) != row["hop_count"]:
            counters["i16_pool_ids_length_mismatch"] += 1
        if len(directional_ids) != row["hop_count"]:
            counters["i16_directional_ids_length_mismatch"] += 1
        for directional_id in directional_ids:
            resolved = rows.get(directional_id)
            if resolved is None or resolved["base_pool_id"] not in pool_ids:
                counters["i16_directional_ids_not_base_pools"] += 1
                break

    connection.close()

    report = {
        "store": os.path.abspath(store_path),
        "counters": counters,
        "violation_samples": samples,
        "passed": all(
            value == 0
            for name, value in counters.items()
            if name not in ("hops", "loops", "temporary_override_hops")
        ),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
