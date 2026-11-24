#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory the consumers of the legacy pool ordering.

The direction layer is the authority of the primary order since
``T-闭.01``.  The legacy order — the ``recommend_direction`` sort of the
candidate edge and the ``pool_score`` sort of the market observation — is
downgraded to "the source of the candidate members" and no longer decides
which pool of a hop is the primary.

Downgrading a legacy column is only safe once **every** consumer of it is
known.  This tool produces that inventory: it scans the repository for the
two tables and for the two legacy ordering functions, classifies every hit
by module and by role, and reads the write timestamps of the producers and
the publish timestamps of the consumers from the live chain store.

The chain store is opened read-only (``mode=ro`` plus ``PRAGMA query_only =
ON``), so the tool never writes to it.

Usage:
    python collect_legacy_order_consumers_20261125.py [repo] [store] [report.json]
"""

import json
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

NETWORK = "robinhood"
CHAIN_ID = 4663

# The two legacy tables whose ordering is now decided by the direction layer.
LEGACY_TABLES = (
    "manager_dex_candidate_pool_recommendations",
    "manager_market_observation_yz_pairs",
)

# The two legacy ordering functions and the columns they decide.
LEGACY_SYMBOLS = (
    "recommend_direction",
    "pool_score",
    "optimal_pool_id",
    "pool_ids_json",
)

# The module a file belongs to.  The order matters: the first match wins.
MODULE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("flashloan/tools/closed_loop_manager/", "M-4"),
    ("flashloan/tools/candidate_edge_pool_manager/", "M-5"),
    ("flashloan/tools/candidate_exchange_pool_manager/", "M-5"),
    ("flashloan/tools/directional_pool_manager/", "M-3"),
    ("flashloan/tools/manager_db/", "M-10"),
    ("flashloan/tools/pool_manager/", "M-1"),
    ("flashloan/tools/broadcast_manager/", "M-7"),
    ("flashloan/tools/review_manager/", "M-6"),
    ("flashloan/tools/composite_manager/", "M-8"),
    ("flashloan/tools/manager_api/", "M-8"),
    ("flashloan/rs-src/", "M-2"),
    ("flashloan/src/core/db/", "M-10"),
    ("flashloan/src/dex/", "M-5"),
    ("flashloan/src/market/", "M-5"),
    ("flashloan/app_runtime/tests/", "M-9"),
    ("flashloan/app_runtime/", "M-8"),
    ("scripts/", "M-5"),
    ("plan/runtime_evidence/tools/", "M-5"),
    ("plan/", "M-10"),
)

# A file that only mentions a table is not a consumer of its ordering; the
# role says what the file does with it.
ROLE_PRODUCER = "producer"
ROLE_CONSUMER = "consumer"
ROLE_SCHEMA = "schema"
ROLE_TEST = "test"
ROLE_TOOL = "tool"


def module_of(relative: str) -> str:
    for prefix, module in MODULE_PREFIXES:
        if relative.startswith(prefix):
            return module
    return "-"


def role_of(relative: str, line: str) -> str:
    upper = line.upper()
    if "INSERT INTO" in upper or "REPLACE INTO" in upper:
        return ROLE_PRODUCER
    if relative.startswith("flashloan/tools/manager_db/migrations/") or "migrate.rs" in relative:
        return ROLE_SCHEMA
    if "/tests/" in relative or relative.startswith("scripts/"):
        return ROLE_TOOL if relative.startswith("scripts/") else ROLE_TEST
    if relative.startswith("plan/runtime_evidence/tools/"):
        return ROLE_TOOL
    return ROLE_CONSUMER


def scan(repo: str) -> Dict[str, List[Dict[str, object]]]:
    """Find every file that mentions a legacy table or a legacy symbol."""
    hits: Dict[str, List[Dict[str, object]]] = {}
    needles = LEGACY_TABLES + LEGACY_SYMBOLS
    skipped_dirs = {".git", "target", "__pycache__", "node_modules", ".venv"}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skipped_dirs]
        for name in files:
            if not name.endswith((".rs", ".py", ".sql", ".toml")):
                continue
            path = os.path.join(root, name)
            relative = os.path.relpath(path, repo).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
            except OSError:
                continue
            matched = [needle for needle in needles if needle in content]
            if not matched:
                continue
            # The directory itself may carry the symbols (a schema folder).
            roles = set()
            for number, line in enumerate(content.splitlines(), start=1):
                if any(needle in line for needle in matched):
                    roles.add(role_of(relative, line))
            hits[relative] = {
                "module": module_of(relative),
                "symbols": sorted(matched),
                "roles": sorted(roles) or [ROLE_CONSUMER],
            }
    return hits


def read_timestamps(store: str) -> Dict[str, object]:
    """Read the write and publish timestamps of the producers and consumers."""
    uri = "file:%s?mode=ro" % store.replace("\\", "/")
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        readings: Dict[str, object] = {}
        for table in LEGACY_TABLES + (
            "manager_v4_directional_exchange_pools",
            "manager_v4_closed_loops",
            "manager_v4_closed_loop_hops",
            "manager_v4_closed_loop_optimizations",
        ):
            row = connection.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM %s" % table
            ).fetchone()
            readings[table] = {"rows": row[0], "maxUpdatedAt": row[1]}
        for table in LEGACY_TABLES:
            scopes = connection.execute(
                "SELECT scope_key, COUNT(*) FROM %s GROUP BY scope_key" % table
            ).fetchall()
            readings[table]["scopes"] = {scope: count for scope, count in scopes}
        ranks = connection.execute(
            "SELECT MIN(rank), MAX(rank) FROM manager_dex_candidate_pool_recommendations"
        ).fetchone()
        readings["manager_dex_candidate_pool_recommendations"]["rankRange"] = list(ranks)
        return readings
    finally:
        connection.close()


def column_inventory(store: str) -> Dict[str, object]:
    """List every table that carries a legacy ordering column.

    The names ``pool_ids_json`` and ``optimal_pool_id`` are shared by
    several tables, and only the ones of the market observation hold the
    **legacy order**.  The inventory therefore names the owner of every
    column, so a decision can be taken per column and not per name.
    """
    uri = "file:%s?mode=ro" % store.replace("\\", "/")
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        inventory: Dict[str, List[Dict[str, object]]] = {}
        for column in ("pool_ids_json", "optimal_pool_id", "rank"):
            owners: List[Dict[str, object]] = []
            for table in tables:
                columns = [
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(%s)" % table
                    ).fetchall()
                ]
                if column not in columns:
                    continue
                rows = connection.execute(
                    "SELECT COUNT(*) FROM %s" % table
                ).fetchone()[0]
                owners.append({"table": table, "rows": rows})
            inventory[column] = owners
        return inventory
    finally:
        connection.close()


def main(argv: List[str]) -> int:
    repo = argv[1] if len(argv) > 1 else os.getcwd()
    store = argv[2] if len(argv) > 2 else os.path.join(
        repo, "flashloan", "db", "local_store_4663.sqlite3"
    )
    report_path: Optional[str] = argv[3] if len(argv) > 3 else None

    hits = scan(repo)
    by_module: Dict[str, List[str]] = {}
    for relative in sorted(hits):
        by_module.setdefault(str(hits[relative]["module"]), []).append(relative)

    report = {
        "repo": repo,
        "store": store,
        "legacyTables": list(LEGACY_TABLES),
        "legacySymbols": list(LEGACY_SYMBOLS),
        "fileCount": len(hits),
        "byModule": {module: len(files) for module, files in sorted(by_module.items())},
        "files": hits,
    }
    if os.path.isfile(store):
        report["storeReadings"] = read_timestamps(store)
        report["columnInventory"] = column_inventory(store)
    else:
        report["storeReadings"] = {"error": "store not found: %s" % store}
        report["columnInventory"] = {}

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if report_path:
        with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    print(
        "--CONSUMER-SCAN-DONE files=%d modules=%s"
        % (len(hits), ",".join("%s:%d" % item for item in sorted(report["byModule"].items())))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
