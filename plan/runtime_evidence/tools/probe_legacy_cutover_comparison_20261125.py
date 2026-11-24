#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the legacy cut-over comparison of the closed-loop layer.

The closed-loop layer publishes its own tables and compares them with the
legacy tables (``v4_closed_loops`` / ``v4_closed_loop_hops``).  The
comparison is a **diagnostic**: it is not a gate of the materialization, but
it is surfaced by the control panel, so a change of the published columns has
to be measured against it.

The probe replicates the comparison of
``flashloan/tools/closed_loop_manager/src/lib.rs`` field by field, so the
reading shows **which** column differs and not only how many rows differ.

The store is opened read-only (``mode=ro`` plus ``PRAGMA query_only = ON``),
so the probe never writes to the live chain store.

Usage:
    python probe_legacy_cutover_comparison_20261125.py [store] [report.json]
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
DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "flashloan",
    "db",
    "local_store_4663.sqlite3",
)
DEFAULT_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"

LOOP_FIELDS = (
    "path_text",
    "path_addresses_json",
    "path_symbols_json",
    "pool_path_key",
    "pool_ids_json",
    "hop_candidate_counts_json",
    "hop_candidate_combination_count",
    "recommendation_basis",
    "recommended_first_priority",
    "status",
)
HOP_FIELDS = (
    "hop_name",
    "input_address",
    "output_address",
    "pool_id",
    "base_pool_id",
    "pool_selection_basis",
    "temporary_override",
)


def open_readonly(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f")
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1", (table,)
    ).fetchone()
    return row is not None


def json_or_none(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def read_manager_loops(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    columns = ", ".join(LOOP_FIELDS)
    sql = (
        "SELECT anchor_symbol, loop_key, %s FROM manager_v4_closed_loops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        % columns
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        record = dict(zip(LOOP_FIELDS, raw[2:]))
        record["path_addresses_json"] = json_or_none(record["path_addresses_json"])
        record["path_symbols_json"] = json_or_none(record["path_symbols_json"])
        record["pool_ids_json"] = json_or_none(record["pool_ids_json"])
        record["hop_candidate_counts_json"] = json_or_none(
            record["hop_candidate_counts_json"]
        )
        record["recommended_first_priority"] = bool(record["recommended_first_priority"])
        rows["%s|%s" % (raw[0], raw[1])] = record
    return rows


def read_legacy_loops(
    connection: sqlite3.Connection, market_id: str
) -> Dict[str, Dict[str, Any]]:
    columns = ", ".join(LOOP_FIELDS)
    sql = (
        "SELECT anchor_symbol, loop_key, %s FROM v4_closed_loops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4"
        % columns
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, market_id)):
        record = dict(zip(LOOP_FIELDS, raw[2:]))
        record["path_addresses_json"] = json_or_none(record["path_addresses_json"])
        record["path_symbols_json"] = json_or_none(record["path_symbols_json"])
        record["pool_ids_json"] = json_or_none(record["pool_ids_json"])
        record["hop_candidate_counts_json"] = json_or_none(
            record["hop_candidate_counts_json"]
        )
        record["recommended_first_priority"] = bool(record["recommended_first_priority"])
        rows["%s|%s" % (raw[0], raw[1])] = record
    return rows


def read_manager_hops(connection: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    columns = ", ".join(HOP_FIELDS)
    sql = (
        "SELECT loop_key, hop_index, %s FROM manager_v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4"
        % columns
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, SCOPE)):
        record = dict(zip(HOP_FIELDS, raw[2:]))
        record["temporary_override"] = bool(record["temporary_override"])
        rows["%s|%s" % (raw[0], raw[1])] = record
    return rows


def read_legacy_hops(
    connection: sqlite3.Connection, market_id: str
) -> Dict[str, Dict[str, Any]]:
    columns = ", ".join(HOP_FIELDS)
    sql = (
        "SELECT loop_key, hop_index, %s FROM v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND market_id = ?4"
        % columns
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in connection.execute(sql, (NETWORK, CHAIN_ID, PROTOCOL, market_id)):
        record = dict(zip(HOP_FIELDS, raw[2:]))
        record["temporary_override"] = bool(record["temporary_override"])
        rows["%s|%s" % (raw[0], raw[1])] = record
    return rows


def compare(
    manager: Dict[str, Dict[str, Any]],
    legacy: Dict[str, Dict[str, Any]],
    fields: Tuple[str, ...],
) -> Dict[str, Any]:
    manager_only = sorted(set(manager) - set(legacy))
    legacy_only = sorted(set(legacy) - set(manager))
    shared = sorted(set(manager) & set(legacy))
    column_mismatch: Counter = Counter()
    mismatching_rows = 0
    samples: List[Dict[str, Any]] = []
    for key in shared:
        left = manager[key]
        right = legacy[key]
        differing = [field for field in fields if left.get(field) != right.get(field)]
        if not differing:
            continue
        mismatching_rows += 1
        for field in differing:
            column_mismatch[field] += 1
        if len(samples) < 10:
            samples.append(
                {
                    "key": key,
                    "fields": {
                        field: {"manager": left.get(field), "legacy": right.get(field)}
                        for field in differing
                    },
                }
            )
    return {
        "managerRows": len(manager),
        "legacyRows": len(legacy),
        "shared": len(shared),
        "managerOnly": len(manager_only),
        "legacyOnly": len(legacy_only),
        "fieldMismatchRows": mismatching_rows,
        "fieldMismatchByColumn": dict(column_mismatch.most_common()),
        "samples": samples,
    }


def main(argv: Optional[List[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    store = arguments[0] if arguments else DEFAULT_STORE
    report_path = arguments[1] if len(arguments) > 1 else None
    market_id = DEFAULT_MARKET_ID
    if not os.path.isfile(store):
        print(json.dumps({"ok": False, "error": "store not found", "store": store}))
        return 2
    connection = open_readonly(store)
    try:
        legacy_tables = table_exists(connection, "v4_closed_loops") and table_exists(
            connection, "v4_closed_loop_hops"
        )
        payload: Dict[str, Any] = {
            "ok": True,
            "store": store,
            "marketId": market_id,
            "legacyTablesAvailable": legacy_tables,
        }
        if legacy_tables:
            loops = compare(
                read_manager_loops(connection),
                read_legacy_loops(connection, market_id),
                LOOP_FIELDS,
            )
            hops = compare(
                read_manager_hops(connection),
                read_legacy_hops(connection, market_id),
                HOP_FIELDS,
            )
            payload["loops"] = loops
            payload["hops"] = hops
            payload["readyForCutover"] = (
                loops["managerOnly"] == 0
                and loops["legacyOnly"] == 0
                and loops["fieldMismatchRows"] == 0
                and hops["managerOnly"] == 0
                and hops["legacyOnly"] == 0
                and hops["fieldMismatchRows"] == 0
            )
        else:
            payload["readyForCutover"] = False
    finally:
        connection.close()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if report_path:
        with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
