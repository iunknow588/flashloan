#!/usr/bin/env python3
"""Audit the directional pool layer against its base pool layer.

The directional layer is a **projection** of the base V4 pools: one base
pool becomes two directional rows, one per swap direction, and every fact
the row carries about the pool itself is copied from the base pool.  This
script proves that on a live database, read only.

It answers four questions that the runtime does not currently answer:

* ``I-9``  does every directional row still agree with its base pool row?
* ``I-7``  does every closed loop hop resolve to exactly one directional row?
* ``I-12`` does the hop's direction match the direction of that row?
* are the unmeasured quantities kept apart from the measured zero?

The database is opened read only (``mode=ro`` plus ``PRAGMA query_only``) so
the script can never write to a live store.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


DIRECTIONAL_TABLE = "manager_v4_directional_exchange_pools"
LOOP_TABLE = "manager_v4_closed_loops"
HOP_TABLE = "manager_v4_closed_loop_hops"
SCOPE_TABLE = "pool_scope_config"

# The base pool facts the directional layer copies.  Everything here is a
# fact of the pool, not of the direction, so the two rows of one base pool
# and the base pool row itself must all agree.
BASE_FACT_COLUMNS = (
    "pool_manager",
    "pool_key_json",
    "token0",
    "token1",
    "token0_key_id",
    "token1_key_id",
    "fee",
    "tick_spacing",
    "hooks",
)

SAMPLE_LIMIT = 5

# The zero address means "no hook".  The direction layer uses the same
# rule (directional_pool_manager::hooks_is_absent), so the audit must not
# report a pool with the zero address as a pool with a hook.
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def open_read_only(database_path: Path) -> sqlite3.Connection:
    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def column_names(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def read_scopes(connection: sqlite3.Connection) -> list[dict]:
    if not table_exists(connection, SCOPE_TABLE):
        return []
    rows = connection.execute(
        "SELECT network, chain_id, scope_key, pool_table FROM pool_scope_config"
        " WHERE enabled = 1 ORDER BY network, chain_id, scope_key"
    ).fetchall()
    return [dict(row) for row in rows]


def read_base_pools(connection: sqlite3.Connection, pool_table: str) -> dict:
    available = set(column_names(connection, pool_table))
    columns = [name for name in BASE_FACT_COLUMNS if name in available]
    extra = [
        name
        for name in ("dynamic_fee", "special_fee", "pool_status")
        if name in available
    ]
    selected = ", ".join(["pool_id", *columns, *extra])
    rows = connection.execute(f"SELECT {selected} FROM {pool_table}").fetchall()
    base = {}
    for row in rows:
        base[row["pool_id"]] = {name: row[name] for name in [*columns, *extra]}
    return base


def read_directional_rows(connection: sqlite3.Connection, scope_key: str) -> list[dict]:
    rows = connection.execute(
        f"SELECT base_pool_id, direction, directional_pool_id, {', '.join(BASE_FACT_COLUMNS)},"
        " pair_key_id, input_token_key_id, output_token_key_id, zero_for_one,"
        " input_capacity_raw, input_capacity_measured, directional_output,"
        " directional_output_measured, pair_direction_rank, pool_status"
        f" FROM {DIRECTIONAL_TABLE} WHERE scope_key = ? ORDER BY base_pool_id, direction",
        (scope_key,),
    ).fetchall()
    return [dict(row) for row in rows]


def audit_scope(connection: sqlite3.Connection, scope: dict) -> dict:
    scope_key = scope["scope_key"]
    pool_table = scope["pool_table"]
    result = {
        "network": scope["network"],
        "chainId": scope["chain_id"],
        "scopeKey": scope_key,
        "basePoolTable": pool_table,
    }

    base = read_base_pools(connection, pool_table)
    rows = read_directional_rows(connection, scope_key)
    result["basePoolCount"] = len(base)
    result["directionalRowCount"] = len(rows)
    result["projectedBasePoolCount"] = len({row["base_pool_id"] for row in rows})

    # A. Projection completeness: every base pool that is projected must
    #    appear exactly twice, once per direction.
    per_base: dict[str, list[dict]] = {}
    for row in rows:
        per_base.setdefault(row["base_pool_id"], []).append(row)
    not_two = sorted(key for key, value in per_base.items() if len(value) != 2)
    wrong_direction_set = sorted(
        key
        for key, value in per_base.items()
        if len(value) == 2 and sorted(row["direction"] for row in value) != [0, 1]
    )
    result["basePoolsWithoutTwoRows"] = len(not_two)
    result["basePoolsWithoutBothDirections"] = len(wrong_direction_set)

    # B. I-9: the directional row must still agree with its base pool row.
    orphan_rows = []
    mismatch_rows = []
    for row in rows:
        base_row = base.get(row["base_pool_id"])
        if base_row is None:
            orphan_rows.append(row["directional_pool_id"])
            continue
        differing = [
            name
            for name in BASE_FACT_COLUMNS
            if name in base_row and row[name] != base_row[name]
        ]
        if differing:
            mismatch_rows.append(
                {
                    "directionalPoolId": row["directional_pool_id"],
                    "basePoolId": row["base_pool_id"],
                    "direction": row["direction"],
                    "differingColumns": differing,
                    "directional": {name: row[name] for name in differing},
                    "base": {name: base_row.get(name) for name in differing},
                }
            )
    result["directionalRowsWithoutBasePool"] = len(orphan_rows)
    result["basePoolMismatches"] = len(mismatch_rows)
    result["basePoolMismatchSamples"] = mismatch_rows[:SAMPLE_LIMIT]
    result["directionalRowsWithoutBasePoolSamples"] = orphan_rows[:SAMPLE_LIMIT]

    # The base pools that are not projected are a market fact (the depth
    # judgement rejects them), not a defect.  They are counted so that the
    # coverage can be read, never as a failure.
    result["basePoolsWithoutProjection"] = len(set(base) - set(per_base))

    # C. Direction semantics: direction 0 spends the higher address token
    #    (token1) and receives token0; direction 1 is the mirror image.
    direction_mismatch = []
    for row in rows:
        if row["direction"] == 0:
            expected = (row["token1_key_id"], row["token0_key_id"], 0)
        elif row["direction"] == 1:
            expected = (row["token0_key_id"], row["token1_key_id"], 1)
        else:
            direction_mismatch.append(
                {"directionalPoolId": row["directional_pool_id"], "reason": "direction not 0 or 1"}
            )
            continue
        actual = (row["input_token_key_id"], row["output_token_key_id"], row["zero_for_one"])
        if actual != expected:
            direction_mismatch.append(
                {
                    "directionalPoolId": row["directional_pool_id"],
                    "basePoolId": row["base_pool_id"],
                    "direction": row["direction"],
                    "expected": list(expected),
                    "actual": list(actual),
                }
            )
    result["directionMismatches"] = len(direction_mismatch)
    result["directionMismatchSamples"] = direction_mismatch[:SAMPLE_LIMIT]

    # D. Identity and ordering: the directional identity is unique, and the
    #    rank inside (pair, direction) is unique and without gaps.
    identities = [row["directional_pool_id"] for row in rows]
    result["duplicateDirectionalPoolIds"] = len(identities) - len(set(identities))
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["pair_direction_rank"] is None:
            continue
        groups.setdefault((row["pair_key_id"], row["direction"]), []).append(row)
    duplicate_rank_groups = 0
    rank_gap_groups = 0
    for members in groups.values():
        ranks = sorted(member["pair_direction_rank"] for member in members)
        if len(ranks) != len(set(ranks)):
            duplicate_rank_groups += 1
        if ranks and ranks != list(range(1, len(ranks) + 1)):
            rank_gap_groups += 1
    result["rankedRowCount"] = sum(len(value) for value in groups.values())
    result["rankGroupCount"] = len(groups)
    result["groupsWithDuplicateRank"] = duplicate_rank_groups
    result["groupsWithRankGap"] = rank_gap_groups

    # E. Unmeasured is not zero: a null quantity must carry the "not
    #    measured" flag, and a measured value must not be null.
    capacity_flag_errors = []
    output_flag_errors = []
    for row in rows:
        if row["input_capacity_raw"] is None and row["input_capacity_measured"] != 0:
            capacity_flag_errors.append(row["directional_pool_id"])
        if row["input_capacity_raw"] is not None and row["input_capacity_measured"] != 1:
            capacity_flag_errors.append(row["directional_pool_id"])
        if row["directional_output"] is None and row["directional_output_measured"] != 0:
            output_flag_errors.append(row["directional_pool_id"])
        if row["directional_output"] is not None and row["directional_output_measured"] != 1:
            output_flag_errors.append(row["directional_pool_id"])
    result["capacityMeasuredFlagErrors"] = len(capacity_flag_errors)
    result["outputMeasuredFlagErrors"] = len(output_flag_errors)

    # H. The standing rules of the system: a dynamic fee pool and a pool
    #    with a hook are not pools the off-chain arithmetic may price, so
    #    the direction layer does not project them at all.  A projected
    #    pool that carries one of them would be a defect of the layer.
    #    A special (above threshold) fee is not part of that rule: it is a
    #    market fact the closed loop is free to weigh, so it is reported
    #    but never counted as a failure.
    dynamic_fee_rows = 0
    hook_rows = 0
    special_fee_rows = 0
    for base_id, base_row in base.items():
        if base_id not in per_base:
            continue
        if base_row.get("dynamic_fee") not in (None, 0):
            dynamic_fee_rows += 1
        if (base_row.get("hooks") or "").strip().lower() not in ("", ZERO_ADDRESS):
            hook_rows += 1
        if base_row.get("special_fee") not in (None, 0):
            special_fee_rows += 1
    result["projectedPoolsWithDynamicFee"] = dynamic_fee_rows
    result["projectedPoolsWithHooks"] = hook_rows
    result["projectedPoolsWithSpecialFee"] = special_fee_rows

    return result


def audit_closed_loop_wiring(connection: sqlite3.Connection) -> dict:
    result = {}
    if not table_exists(connection, DIRECTIONAL_TABLE):
        result["available"] = False
        return result
    result["available"] = True

    directional = {}
    for row in connection.execute(
        f"SELECT directional_pool_id, scope_key, direction, pair_direction_rank, base_pool_id"
        f" FROM {DIRECTIONAL_TABLE}"
    ):
        directional[row["directional_pool_id"]] = dict(row)

    hops = connection.execute(
        f"SELECT loop_key, hop_index, directional_pool_id, hop_direction,"
        f" directional_scope_key, directional_pool_rank FROM {HOP_TABLE}"
    ).fetchall()
    result["hopCount"] = len(hops)
    unresolved = 0
    direction_mismatch = 0
    scope_mismatch = 0
    rank_mismatch = 0
    missing_pointer = 0
    samples = []
    per_loop: dict[str, list] = {}
    for hop in hops:
        per_loop.setdefault(hop["loop_key"], []).append(hop)
        pointer = hop["directional_pool_id"]
        if not pointer:
            missing_pointer += 1
            continue
        target = directional.get(pointer)
        if target is None:
            unresolved += 1
            if len(samples) < SAMPLE_LIMIT:
                samples.append({"loopKey": hop["loop_key"], "reason": "unresolved", "directionalPoolId": pointer})
            continue
        if hop["hop_direction"] is None or hop["hop_direction"] != target["direction"]:
            direction_mismatch += 1
            if len(samples) < SAMPLE_LIMIT:
                samples.append({"loopKey": hop["loop_key"], "reason": "direction", "directionalPoolId": pointer})
        if hop["directional_scope_key"] and hop["directional_scope_key"] != target["scope_key"]:
            scope_mismatch += 1
        if (
            hop["directional_pool_rank"] is not None
            and target["pair_direction_rank"] is not None
            and hop["directional_pool_rank"] != target["pair_direction_rank"]
        ):
            rank_mismatch += 1
    result["hopsWithoutDirectionalPointer"] = missing_pointer
    result["unresolvedDirectionalPoolIds"] = unresolved
    result["hopDirectionMismatches"] = direction_mismatch
    result["hopScopeMismatches"] = scope_mismatch
    result["hopRankMismatches"] = rank_mismatch
    result["samples"] = samples

    # The loop body carries the same identities in hop order.
    body_mismatch = 0
    body_samples = []
    if table_exists(connection, LOOP_TABLE) and "directional_pool_ids_json" in column_names(connection, LOOP_TABLE):
        for row in connection.execute(
            f"SELECT loop_key, directional_pool_ids_json FROM {LOOP_TABLE}"
        ):
            raw = row["directional_pool_ids_json"]
            if not raw:
                continue
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body_mismatch += 1
                continue
            hops = sorted(per_loop.get(row["loop_key"], []), key=lambda hop: hop["hop_index"])
            expected = [hop["directional_pool_id"] for hop in hops]
            if list(body) != expected:
                body_mismatch += 1
                if len(body_samples) < SAMPLE_LIMIT:
                    body_samples.append({"loopKey": row["loop_key"], "body": list(body), "hops": expected})
    result["loopBodyMismatches"] = body_mismatch
    result["loopBodyMismatchSamples"] = body_samples
    return result


def verdict(scope_results: list[dict], loop_result: dict) -> dict:
    failures = []
    for scope in scope_results:
        name = f"{scope['scopeKey']}"
        for key, label in (
            ("basePoolsWithoutTwoRows", "a base pool is not projected into exactly two rows"),
            ("basePoolsWithoutBothDirections", "a projected base pool does not carry both directions"),
            ("directionalRowsWithoutBasePool", "a directional row has no base pool row (I-9)"),
            ("basePoolMismatches", "a directional row disagrees with its base pool row (I-9)"),
            ("directionMismatches", "a directional row disagrees with its direction"),
            ("duplicateDirectionalPoolIds", "a directional pool identity is not unique"),
            ("groupsWithDuplicateRank", "a (pair, direction) group has a duplicated rank"),
            ("groupsWithRankGap", "a (pair, direction) group has a gap in its rank sequence"),
            ("capacityMeasuredFlagErrors", "the capacity measured flag does not match the capacity value"),
            ("outputMeasuredFlagErrors", "the output measured flag does not match the output value"),
            ("projectedPoolsWithDynamicFee", "a projected pool carries a dynamic fee"),
            ("projectedPoolsWithHooks", "a projected pool carries a hook"),
        ):
            value = scope.get(key)
            if value:
                failures.append({"scope": name, "check": key, "count": value, "meaning": label})
    for key, label in (
        ("hopsWithoutDirectionalPointer", "a closed loop hop does not name a directional pool"),
        ("unresolvedDirectionalPoolIds", "a closed loop hop names an unknown directional pool (I-7)"),
        ("hopDirectionMismatches", "a closed loop hop disagrees with the direction of its pool (I-12)"),
        ("hopScopeMismatches", "a closed loop hop disagrees with the scope of its pool"),
        ("hopRankMismatches", "a closed loop hop disagrees with the rank of its pool"),
        ("loopBodyMismatches", "a closed loop body disagrees with its hops"),
    ):
        value = loop_result.get(key)
        if value:
            failures.append({"scope": "closedLoop", "check": key, "count": value, "meaning": label})
    return {"passed": not failures, "failureCount": len(failures), "failures": failures}


def render_markdown(report: dict) -> str:
    lines = [
        f"# 方向层投影不变式审计 · {report['generatedAt']}",
        "",
        f"- **实盘库**：`{report['databasePath']}`（**只读**：`mode=ro` ＋ `PRAGMA query_only = ON`）",
        f"- **总判定**：{'✅ 通过（零失败项）' if report['verdict']['passed'] else '❌ 失败项 ' + str(report['verdict']['failureCount']) + ' 类'}",
        f"- **实盘库写入**：**0**（本脚本只读）",
        "",
        "## 1. 逐作用域读数",
        "",
        "| 作用域 | 基础池 | 方向行 | 已投影基础池 | 两行齐备 | 与基础池不一致 (I-9) | 无基础池行 (I-9) | 方向语义错 | 序位重复组 | 序位缺口组 | 身份重复 | 容量标记错 | 输出标记错 | 动态费率池 | 带 hook 池 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scope in report["scopes"]:
        lines.append(
            "| `{scope}` | {base} | {rows} | {projected} | {two} | {mismatch} | {orphan} | {direction} | "
            "{dup} | {gap} | {iddup} | {cap} | {out} | {dyn} | {hook} |".format(
                scope=scope["scopeKey"],
                base=scope["basePoolCount"],
                rows=scope["directionalRowCount"],
                projected=scope["projectedBasePoolCount"],
                two=scope["basePoolsWithoutTwoRows"],
                mismatch=scope["basePoolMismatches"],
                orphan=scope["directionalRowsWithoutBasePool"],
                direction=scope["directionMismatches"],
                dup=scope["groupsWithDuplicateRank"],
                gap=scope["groupsWithRankGap"],
                iddup=scope["duplicateDirectionalPoolIds"],
                cap=scope["capacityMeasuredFlagErrors"],
                out=scope["outputMeasuredFlagErrors"],
                dyn=scope.get("projectedPoolsWithDynamicFee", 0),
                hook=scope.get("projectedPoolsWithHooks", 0),
            )
        )
    loop = report["closedLoopWiring"]
    lines += [
        "",
        "## 2. 闭环引用读数（I-7 / I-12）",
        "",
        "| 项 | 读数 |",
        "|---|---:|",
        f"| 跳数 | {loop.get('hopCount')} |",
        f"| 跳未指向方向池 | {loop.get('hopsWithoutDirectionalPointer')} |",
        f"| 方向池无法解析（I-7） | {loop.get('unresolvedDirectionalPoolIds')} |",
        f"| 方向与方向行不一致（I-12） | {loop.get('hopDirectionMismatches')} |",
        f"| 作用域与方向行不一致 | {loop.get('hopScopeMismatches')} |",
        f"| 序位与方向行不一致 | {loop.get('hopRankMismatches')} |",
        f"| 闭环体与跳序列不一致 | {loop.get('loopBodyMismatches')} |",
        "",
        "## 3. 失败项",
        "",
    ]
    if report["verdict"]["failures"]:
        for failure in report["verdict"]["failures"]:
            lines.append(
                f"- **{failure['scope']}** · `{failure['check']}` = **{failure['count']}** —— {failure['meaning']}"
            )
    else:
        lines.append("**零失败项。**")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--md-out", required=True, type=Path)
    args = parser.parse_args()

    database_path = args.database.resolve()
    connection = open_read_only(database_path)
    try:
        scopes = read_scopes(connection)
        scope_results = []
        for scope in scopes:
            if not table_exists(connection, scope["pool_table"]):
                scope_results.append(
                    {
                        "network": scope["network"],
                        "chainId": scope["chain_id"],
                        "scopeKey": scope["scope_key"],
                        "basePoolTable": scope["pool_table"],
                        "missingBasePoolTable": True,
                    }
                )
                continue
            scope_results.append(audit_scope(connection, scope))
        loop_result = audit_closed_loop_wiring(connection)
    finally:
        connection.close()

    import datetime as _datetime

    report = {
        "generatedAt": _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds"),
        "databasePath": str(database_path),
        "writeMode": "read-only (mode=ro + PRAGMA query_only = ON)",
        "scopes": scope_results,
        "closedLoopWiring": loop_result,
    }
    report["verdict"] = verdict(scope_results, loop_result)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps(report["verdict"], ensure_ascii=False))
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
