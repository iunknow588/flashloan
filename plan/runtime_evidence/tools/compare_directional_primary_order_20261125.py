#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shadow comparison: the direction layer's own order vs the legacy order.

The closed loop switched to the direction table for the **direction** and
for the **backup tier**, but the **primary** of every hop still comes from
the legacy candidate tables:

* the first and the third hop from
  `manager_dex_candidate_pool_recommendations` (ranked by
  `recommend_direction`), and
* the middle hop from `manager_market_observation_yz_pairs` (ranked by
  `pool_score`, i.e. by `liquidity_index`).

This probe measures, **read only**, how much the closed loop would change
if the primary of every hop were taken from the direction layer instead
(the pool with `pair_direction_rank = 1` inside the hop's
`(scope, pair, direction)` group).

It answers three questions with numbers:

1. **Pool level** - for every group, does the legacy rank-1 pool equal the
   direction layer's rank-1 pool?
2. **Hop level** - for every published hop, is the pool that was actually
   selected the direction layer's rank-1 pool of its group?
3. **Loop impact** - how many published loops carry at least one hop that
   would change?

What it deliberately does **not** claim: the new folded capacity and the
new profit.  Those need the kernel to fold the ticks again, which is a
write-side measurement; this probe reports them as **not measured**.

The store is opened with `mode=ro` plus `PRAGMA query_only = ON`, so the
probe can never write - not even a WAL header.

Usage:
    python compare_directional_primary_order_20261125.py --db <path>
                                                         [--network robinhood]
                                                         [--chain-id 4663]
                                                         [--out-json <path>]
                                                         [--out-md <path>]
                                                         [--quiet]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


NL = chr(10)
BT = chr(96)


def _open_readonly(path: str) -> sqlite3.Connection:
    """Open the store read only, with a second guard on top of the URI."""
    uri = "file:{}?mode=ro".format(path.replace("\\", "/"))
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _rows(
    connection: sqlite3.Connection, sql: str, params: Tuple[Any, ...]
) -> List[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(connection.execute(sql, params))


def _rate(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole * 100.0, 4)


class DirectionIndex:
    """The direction layer, indexed the way the closed loop reads it."""

    def __init__(self) -> None:
        # (scope, pair_key_id, direction) -> [(rank, base_pool_id), ...]
        self.groups: Dict[Tuple[str, str, int], List[Tuple[int, str]]] = defaultdict(list)
        # (scope, input_token_key_id, output_token_key_id) -> (pair_key_id, direction)
        self.group_of_tokens: Dict[Tuple[str, str, str], Tuple[str, int]] = {}
        self.row_count = 0
        self.ranked_row_count = 0
        self.status_counts: Dict[str, int] = defaultdict(int)

    def load(self, connection: sqlite3.Connection, network: str, chain_id: int) -> None:
        rows = _rows(
            connection,
            "SELECT scope_key, pair_key_id, direction, base_pool_id, "
            "       directional_pool_id, input_token_key_id, output_token_key_id, "
            "       pair_direction_rank, pool_status, directional_output_measured "
            "FROM manager_v4_directional_exchange_pools "
            "WHERE network = ?1 AND chain_id = ?2 "
            "ORDER BY scope_key, pair_key_id, direction, pair_direction_rank, base_pool_id",
            (network, chain_id),
        )
        self.row_count = len(rows)
        for row in rows:
            scope = row["scope_key"]
            direction = int(row["direction"])
            base_pool_id = row["base_pool_id"]
            pair_key_id = row["pair_key_id"]
            self.status_counts[row["pool_status"] or "(null)"] += 1
            self.group_of_tokens.setdefault(
                (scope, row["input_token_key_id"], row["output_token_key_id"]),
                (pair_key_id, direction),
            )
            rank = row["pair_direction_rank"]
            if rank is None:
                continue
            self.ranked_row_count += 1
            self.groups[(scope, pair_key_id, direction)].append((int(rank), base_pool_id))
        for members in self.groups.values():
            members.sort()

    def group_for_tokens(
        self, scope: str, token_in: str, token_out: str
    ) -> Optional[Tuple[str, int]]:
        return self.group_of_tokens.get((scope, token_in, token_out))

    def rank1(self, scope: str, pair_key_id: str, direction: int) -> Optional[str]:
        members = self.groups.get((scope, pair_key_id, direction))
        if not members:
            return None
        return members[0][1]

    def group_size(self, scope: str, pair_key_id: str, direction: int) -> int:
        return len(self.groups.get((scope, pair_key_id, direction), ()))


def _compare_groups(
    index: DirectionIndex,
    scope: str,
    token_in: str,
    token_out: str,
    legacy_pool_id: Optional[str],
) -> Dict[str, Any]:
    """Compare one legacy rank-1 pool with the direction layer's rank-1."""
    group = index.group_for_tokens(scope, token_in, token_out)
    if group is None:
        return {
            "scope": scope,
            "tokenIn": token_in,
            "tokenOut": token_out,
            "groupFound": False,
            "legacyPoolId": legacy_pool_id,
            "directionPoolId": None,
            "changed": None,
        }
    pair_key_id, direction = group
    new_pool_id = index.rank1(scope, pair_key_id, direction)
    return {
        "scope": scope,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "pairKeyId": pair_key_id,
        "direction": direction,
        "groupFound": True,
        "groupSize": index.group_size(scope, pair_key_id, direction),
        "legacyPoolId": legacy_pool_id,
        "directionPoolId": new_pool_id,
        "changed": (
            None
            if (legacy_pool_id is None or new_pool_id is None)
            else legacy_pool_id != new_pool_id
        ),
    }


def _probe_candidate_edge(
    connection: sqlite3.Connection, index: DirectionIndex, network: str, chain_id: int
) -> Dict[str, Any]:
    """The first and the third hop: the recommend_direction order."""
    rows = _rows(
        connection,
        "SELECT anchor_token_key_id, candidate_token_key_id, hop, rank, pool_id, "
        "       direction_key, selection_basis, scope_key "
        "FROM manager_dex_candidate_pool_recommendations "
        "WHERE network = ?1 AND chain_id = ?2 "
        "ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank",
        (network, chain_id),
    )
    per_hop: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if int(row["rank"]) != 1:
            continue
        hop = row["hop"]
        if hop == "first":
            token_in = row["anchor_token_key_id"]
            token_out = row["candidate_token_key_id"]
        else:
            token_in = row["candidate_token_key_id"]
            token_out = row["anchor_token_key_id"]
        per_hop[hop].append(
            _compare_groups(index, row["scope_key"], token_in, token_out, row["pool_id"])
        )
    summary: Dict[str, Any] = {}
    for hop, entries in per_hop.items():
        comparable = [entry for entry in entries if entry["changed"] is not None]
        changed = [entry for entry in comparable if entry["changed"]]
        summary[hop] = {
            "legacyRank1Count": len(entries),
            "comparable": len(comparable),
            "groupMissing": len(entries) - len(comparable),
            "changed": len(changed),
            "changeRatePercent": _rate(len(changed), len(comparable)),
            "examples": changed[:20],
        }
    return summary


def _probe_yz_observation(
    connection: sqlite3.Connection, index: DirectionIndex, network: str, chain_id: int
) -> Dict[str, Any]:
    """The middle hop: the pool_score order (liquidity index)."""
    rows = _rows(
        connection,
        "SELECT anchor_token_key_id, direction_key, token_x_key_id, token_y_key_id, "
        "       optimal_pool_id, pool_ids_json, scope_key "
        "FROM manager_market_observation_yz_pairs "
        "WHERE network = ?1 AND chain_id = ?2 "
        "ORDER BY anchor_token_key_id, direction_key",
        (network, chain_id),
    )
    entries = [
        _compare_groups(
            index,
            row["scope_key"],
            row["token_x_key_id"],
            row["token_y_key_id"],
            row["optimal_pool_id"],
        )
        for row in rows
    ]
    comparable = [entry for entry in entries if entry["changed"] is not None]
    changed = [entry for entry in comparable if entry["changed"]]
    return {
        "legacyOptimalCount": len(entries),
        "comparable": len(comparable),
        "groupMissing": len(entries) - len(comparable),
        "changed": len(changed),
        "changeRatePercent": _rate(len(changed), len(comparable)),
        "examples": changed[:20],
    }


def _probe_tier_position(
    connection: sqlite3.Connection, index: DirectionIndex, network: str, chain_id: int
) -> Dict[str, Any]:
    """Where does the direction layer's rank-1 pool sit in the legacy tier?

    The legacy candidate tier is truncated to five rows.  When the
    direction layer's own rank-1 pool is **inside** that window, a pure
    re-ordering of the legacy tier is enough to make the direction layer
    the authority: no pool that the legacy code would not have used can
    reach a hop.

    When the direction rank-1 pool is **outside** the window, the switch
    has to promote it from the backup tier, which is a membership change
    and therefore a different question.
    """
    recommendations = _rows(
        connection,
        "SELECT anchor_token_key_id, candidate_token_key_id, hop, rank, pool_id "
        "FROM manager_dex_candidate_pool_recommendations "
        "WHERE network = ?1 AND chain_id = ?2 "
        "ORDER BY anchor_token_key_id, candidate_token_key_id, hop, rank",
        (network, chain_id),
    )
    tiers: Dict[Tuple[str, str, str], List[str]] = defaultdict(list)
    for row in recommendations:
        tiers[
            (
                row["anchor_token_key_id"],
                row["candidate_token_key_id"],
                row["hop"],
            )
        ].append(row["pool_id"])

    positions: Dict[str, int] = defaultdict(int)
    outside_window = 0
    comparable = 0
    missing_from_tier = 0
    for (anchor, candidate, hop), pool_ids in tiers.items():
        if hop == "first":
            token_in, token_out = anchor, candidate
        else:
            token_in, token_out = candidate, anchor
        group = index.group_for_tokens("core4", token_in, token_out)
        if group is None:
            continue
        pair_key_id, direction = group
        rank1 = index.rank1("core4", pair_key_id, direction)
        if rank1 is None:
            continue
        comparable += 1
        if rank1 in pool_ids:
            positions[str(pool_ids.index(rank1) + 1)] += 1
        else:
            missing_from_tier += 1
            outside_window += 1

    observations = _rows(
        connection,
        "SELECT anchor_token_key_id, token_x_key_id, token_y_key_id, "
        "       optimal_pool_id, pool_ids_json "
        "FROM manager_market_observation_yz_pairs "
        "WHERE network = ?1 AND chain_id = ?2",
        (network, chain_id),
    )
    yz_positions: Dict[str, int] = defaultdict(int)
    yz_outside = 0
    yz_comparable = 0
    yz_missing = 0
    for row in observations:
        try:
            pool_ids = json.loads(row["pool_ids_json"] or "[]")
        except json.JSONDecodeError:
            pool_ids = []
        group = index.group_for_tokens(
            "candidate_exchange", row["token_x_key_id"], row["token_y_key_id"]
        )
        if group is None:
            continue
        pair_key_id, direction = group
        rank1 = index.rank1("candidate_exchange", pair_key_id, direction)
        if rank1 is None:
            continue
        yz_comparable += 1
        if rank1 in pool_ids:
            yz_positions[str(pool_ids.index(rank1) + 1)] += 1
        else:
            yz_missing += 1
            yz_outside += 1

    return {
        "candidateTier": {
            "comparable": comparable,
            "positionHistogram": dict(sorted(positions.items(), key=lambda kv: int(kv[0]))),
            "notInTier": missing_from_tier,
            "notInTierPercent": _rate(missing_from_tier, comparable),
        },
        "yzTier": {
            "comparable": yz_comparable,
            "positionHistogram": dict(sorted(yz_positions.items(), key=lambda kv: int(kv[0]))),
            "notInTier": yz_missing,
            "notInTierPercent": _rate(yz_missing, yz_comparable),
        },
        "legacyTruncationWindow": 5,
    }


def _probe_hops(
    connection: sqlite3.Connection, index: DirectionIndex, network: str, chain_id: int
) -> Dict[str, Any]:
    """The published hops: which pool was selected, and would it change?"""
    rows = _rows(
        connection,
        "SELECT loop_key, hop_index, hop_name, input_token_key_id, output_token_key_id, "
        "       pool_id, base_pool_id, pool_selection_basis, optimization_id, "
        "       directional_pool_id, hop_direction, directional_scope_key, "
        "       directional_pool_rank "
        "FROM manager_v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 "
        "ORDER BY loop_key, hop_index",
        (network, chain_id),
    )
    per_hop_name: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "hops": 0,
            "primary": 0,
            "primaryChanged": 0,
            "changed": 0,
            "comparable": 0,
        }
    )
    changed_loops: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rank_histogram: Dict[str, int] = defaultdict(int)
    basis_histogram: Dict[str, int] = defaultdict(int)
    changed_hops = 0
    comparable_hops = 0
    for row in rows:
        scope = row["directional_scope_key"] or "core4"
        group = index.group_for_tokens(
            scope, row["input_token_key_id"], row["output_token_key_id"]
        )
        new_pool_id = None
        if group is not None:
            pair_key_id, direction = group
            new_pool_id = index.rank1(scope, pair_key_id, direction)
        is_primary = row["optimization_id"] is None
        bucket = per_hop_name[row["hop_name"]]
        bucket["hops"] += 1
        if is_primary:
            bucket["primary"] += 1
        basis_histogram[row["pool_selection_basis"] or "(null)"] += 1
        rank_histogram[str(row["directional_pool_rank"])] += 1
        if new_pool_id is None:
            continue
        comparable_hops += 1
        bucket["comparable"] += 1
        if new_pool_id != row["base_pool_id"]:
            changed_hops += 1
            bucket["changed"] += 1
            if is_primary:
                bucket["primaryChanged"] += 1
            changed_loops[row["loop_key"]].append(
                {
                    "hopIndex": row["hop_index"],
                    "hopName": row["hop_name"],
                    "currentPoolId": row["base_pool_id"],
                    "directionPoolId": new_pool_id,
                    "isPrimary": is_primary,
                }
            )
    return {
        "hopCount": len(rows),
        "comparable": comparable_hops,
        "changed": changed_hops,
        "changeRatePercent": _rate(changed_hops, comparable_hops),
        "affectedLoopCount": len(changed_loops),
        "perHopName": {name: dict(values) for name, values in sorted(per_hop_name.items())},
        "rankHistogram": dict(sorted(rank_histogram.items())),
        "selectionBasisHistogram": dict(sorted(basis_histogram.items())),
        "affectedLoops": [
            {"loopKey": loop_key, "hops": hops}
            for loop_key, hops in sorted(changed_loops.items())
        ],
    }


def _probe_loops(connection: sqlite3.Connection, network: str, chain_id: int) -> Dict[str, Any]:
    """The published loops: identity and the readings a switch would move."""
    rows = _rows(
        connection,
        "SELECT loop_key, anchor_token_key_id, token_y_key_id, token_z_key_id, status, "
        "       single_tick_capacity_anchor_raw, single_tick_capacity_binding_pool_id, "
        "       borrow_token_usdg_value_raw, recommendation_basis, pool_ids_json, "
        "       directional_pool_ids_json, created_at, updated_at "
        "FROM manager_v4_closed_loops "
        "WHERE network = ?1 AND chain_id = ?2 ORDER BY loop_key",
        (network, chain_id),
    )
    status_counts: Dict[str, int] = defaultdict(int)
    capacity_present = 0
    for row in rows:
        status_counts[row["status"] or "(null)"] += 1
        if row["single_tick_capacity_anchor_raw"] is not None:
            capacity_present += 1
    return {
        "loopCount": len(rows),
        "statusCounts": dict(sorted(status_counts.items())),
        "capacityMeasured": capacity_present,
        "identityRule": "loop_key = anchor:tokenY:tokenZ (the token triple, not the pool set)",
        "identityStability": (
            "a different primary pool does not change the token triple, so the loop "
            "identity is unchanged by construction; the folded capacity and the "
            "profit are NOT measured here (they need a kernel re-fold)"
        ),
    }


def _legacy_table_timestamps(
    connection: sqlite3.Connection, network: str, chain_id: int
) -> Dict[str, Any]:
    """When the legacy tables were last written (the consumer inventory anchor)."""
    result: Dict[str, Any] = {}
    for table in (
        "manager_dex_candidate_pool_recommendations",
        "manager_market_observation_yz_pairs",
        "manager_market_observation_intermediate_pools",
        "manager_v4_directional_exchange_pools",
        "manager_v4_closed_loop_hops",
    ):
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info({})".format(table))
        }
        written = "updated_at" if "updated_at" in columns else None
        created = "created_at" if "created_at" in columns else None
        if written is None and created is None:
            result[table] = {
                "rows": connection.execute(
                    "SELECT COUNT(*) FROM {} WHERE network = ?1 AND chain_id = ?2".format(table),
                    (network, chain_id),
                ).fetchone()[0],
                "lastWrittenAt": None,
                "firstWrittenAt": None,
            }
            continue
        projection = "COUNT(*), "
        projection += "MAX({})".format(written) if written else "NULL"
        projection += ", "
        projection += "MIN({})".format(created) if created else "NULL"
        row = connection.execute(
            "SELECT {} FROM {} WHERE network = ?1 AND chain_id = ?2".format(
                projection, table
            ),
            (network, chain_id),
        ).fetchone()
        result[table] = {
            "rows": row[0],
            "lastWrittenAt": row[1],
            "firstWrittenAt": row[2],
        }
    return result


def _render_markdown(payload: Dict[str, Any]) -> str:
    hops = payload["hops"]
    candidate = payload["candidateEdge"]
    yz = payload["yzObservation"]
    lines: List[str] = []
    lines.append("# 方向层主序 vs 旧口径主序 · 影子比对（只读）")
    lines.append("")
    lines.append(
        "> 生成工具：" + BT
        + "plan/runtime_evidence/tools/compare_directional_primary_order_20261125.py"
        + BT
    )
    lines.append("")
    lines.append(
        "> 库：" + BT + payload["database"] + BT
        + "（mode=ro + PRAGMA query_only = ON，全程零写入）"
    )
    lines.append("")
    lines.append("> 执行时刻：" + BT + payload["measuredAt"] + BT)
    lines.append("")
    lines.append("## 1. 结论摘要")
    lines.append("")
    lines.append("| 观测 | 读数 |")
    lines.append("|---|---|")
    lines.append(
        "| 方向池行数 | **{}**（有 pair_direction_rank 的 **{}**） |".format(
            payload["directionLayer"]["rows"], payload["directionLayer"]["rankedRows"]
        )
    )
    lines.append(
        "| 已发布闭环跳 | **{}**（可比 **{}**） |".format(
            hops["hopCount"], hops["comparable"]
        )
    )
    lines.append(
        "| 跳维度换池率 | **{} %**（{} / {}） |".format(
            hops["changeRatePercent"], hops["changed"], hops["comparable"]
        )
    )
    lines.append(
        "| 受影响闭环 | **{}** / **{}** |".format(
            hops["affectedLoopCount"], payload["loops"]["loopCount"]
        )
    )
    for hop in ("first", "third"):
        bucket = candidate.get(hop, {})
        lines.append(
            "| 候选边主序（{}）换池率 | **{} %**（{} / {}） |".format(
                hop,
                bucket.get("changeRatePercent", "n/a"),
                bucket.get("changed", "n/a"),
                bucket.get("comparable", "n/a"),
            )
        )
    lines.append(
        "| 中间跳主序换池率 | **{} %**（{} / {}） |".format(
            yz["changeRatePercent"], yz["changed"], yz["comparable"]
        )
    )
    lines.append("")
    lines.append("## 2. 未测量项（不得用零冒充）")
    lines.append("")
    lines.append("- **折叠容量差异**：**未测量** —— 需要内核按新主序重折 tick 区间（写侧测量）。")
    lines.append("- **利润差异**：**未测量** —— 依赖上面的容量复算。")
    lines.append("- **闭环集合差异**：按构造为 **0** —— 闭环身份是代币三元组，与选中的池无关。")
    lines.append("")
    lines.append("## 3. 分跳明细")
    lines.append("")
    lines.append("| 跳 | 跳数 | 主序跳数 | 可比 | 换池 | 主序换池 | 换池率 % |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, values in hops["perHopName"].items():
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                name,
                values["hops"],
                values["primary"],
                values["comparable"],
                values["changed"],
                values["primaryChanged"],
                _rate(values["changed"], values["comparable"]),
            )
        )
    lines.append("")
    lines.append("## 4. 当前选中池的方向序位分布")
    lines.append("")
    lines.append("| directional_pool_rank | 跳数 |")
    lines.append("|---|---|")
    for rank, count in hops["rankHistogram"].items():
        lines.append("| {} | {} |".format(rank, count))
    lines.append("")
    lines.append("## 5. 选池依据分布（pool_selection_basis）")
    lines.append("")
    lines.append("| 依据 | 跳数 |")
    lines.append("|---|---|")
    for basis, count in hops["selectionBasisHistogram"].items():
        lines.append("| " + BT + basis + BT + " | {} |".format(count))
    lines.append("")
    lines.append("## 6. 受影响闭环（前 20 条）")
    lines.append("")
    if not hops["affectedLoops"]:
        lines.append("（无）")
    else:
        lines.append("| 闭环 | 跳 | 现用池 | 方向层主序池 | 主序 |")
        lines.append("|---|---|---|---|---|")
        for entry in hops["affectedLoops"][:20]:
            for hop in entry["hops"]:
                lines.append(
                    "| " + BT + entry["loopKey"][:26] + "…" + BT
                    + " | {} | ".format(hop["hopIndex"])
                    + BT + hop["currentPoolId"][:14] + "…" + BT
                    + " | " + BT + hop["directionPoolId"][:14] + "…" + BT
                    + " | " + ("是" if hop["isPrimary"] else "备")
                )
    lines.append("")
    lines.append("## 7. 旧口径表的最后写入时间")
    lines.append("")
    lines.append("| 表 | 行数 | 最后写入 |")
    lines.append("|---|---|---|")
    for table, values in payload["legacyTables"].items():
        lines.append(
            "| " + BT + table + BT + " | {} | {} |".format(
                values["rows"], values["lastWrittenAt"]
            )
        )
    lines.append("")
    return NL.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="directional primary order shadow")
    parser.add_argument("--db", default="flashloan/db/local_store_4663.sqlite3")
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--out-json", default=None)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print("database not found: {}".format(args.db), file=sys.stderr)
        return 2

    connection = _open_readonly(args.db)
    try:
        index = DirectionIndex()
        index.load(connection, args.network, args.chain_id)
        payload: Dict[str, Any] = {
            "tool": "compare_directional_primary_order_20261125.py",
            "measuredAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "database": os.path.abspath(args.db),
            "network": args.network,
            "chainId": args.chain_id,
            "readOnly": True,
            "directionLayer": {
                "rows": index.row_count,
                "rankedRows": index.ranked_row_count,
                "groups": len(index.groups),
                "statusCounts": dict(sorted(index.status_counts.items())),
            },
            "candidateEdge": _probe_candidate_edge(
                connection, index, args.network, args.chain_id
            ),
            "yzObservation": _probe_yz_observation(
                connection, index, args.network, args.chain_id
            ),
            "tierPosition": _probe_tier_position(
                connection, index, args.network, args.chain_id
            ),
            "hops": _probe_hops(connection, index, args.network, args.chain_id),
            "loops": _probe_loops(connection, args.network, args.chain_id),
            "legacyTables": _legacy_table_timestamps(connection, args.network, args.chain_id),
        }
    finally:
        connection.close()

    out_json = (
        args.out_json
        or "plan/runtime_evidence/reports/directional_primary_order_shadow_20261125.json"
    )
    out_md = (
        args.out_md
        or "plan/runtime_evidence/reports/directional_primary_order_shadow_20261125.md"
    )
    for path in (out_json, out_md):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
    with open(out_json, "w", encoding="utf-8", newline=NL) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write(NL)
    with open(out_md, "w", encoding="utf-8", newline=NL) as handle:
        handle.write(_render_markdown(payload))

    if not args.quiet:
        hops = payload["hops"]
        print("direction rows         : {}".format(payload["directionLayer"]["rows"]))
        print(
            "published hops         : {} (comparable {})".format(
                hops["hopCount"], hops["comparable"]
            )
        )
        print("hop-level change rate  : {} %".format(hops["changeRatePercent"]))
        print(
            "affected loops         : {} / {}".format(
                hops["affectedLoopCount"], payload["loops"]["loopCount"]
            )
        )
        print(
            "candidate first change : {} %".format(
                payload["candidateEdge"].get("first", {}).get("changeRatePercent", "n/a")
            )
        )
        print(
            "candidate third change : {} %".format(
                payload["candidateEdge"].get("third", {}).get("changeRatePercent", "n/a")
            )
        )
        print("yz middle change       : {} %".format(payload["yzObservation"]["changeRatePercent"]))
        print("json -> {}".format(out_json))
        print("md   -> {}".format(out_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
