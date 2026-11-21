#!/usr/bin/env python3
"""TASK-146 / 基础框架库写入（迁移 011..013）—— 双链证据采集（只读）。

用途：
  证明"基础框架库写入"已落到两条链的 DB 实物：
    - 主网 4663   ：迁移 001..014 早已全部应用（本脚本只做只读核验）
    - 测试网 46630：迁移 011/012/013 已于第 55 轮应用

本脚本**只读**，不改任何库；写入动作由 `manager-db migrate` 单独执行。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB_DIR = REPO / "flashloan" / "db"
OUT = REPO / "plan" / "runtime_evidence" / "reports" / "task146_baseline_framework_write_20261121.json"

EXPECTED_LEDGER = [
    "20261103_001_manager_schema",
    "20261104_001_candidate_edge_schema",
    "20261104_002_candidate_exchange_schema",
    "20261104_003_closed_loop_schema",
    "20261104_004_review_schema",
    "20261104_005_broadcast_schema",
    "20261104_006_composite_schema",
    "20261112_007_usdg_valuation_schema",
    "20261114_008_single_tick_capacity_schema",
    "20261114_009_hop_price_limit_schema",
    "20261118_010_cross_tick_seed_schema",
    "20261119_011_skipped_token_reason_schema",
    "20261119_012_skipped_token_pool_localization",
    "20261119_013_pool_status_reason",
]

PARAMETERIZED_REASONS = [
    "path_single_tick_bound_unusable:%",
    "path_capacity_below_minimum:%",
    "yz_direction_missing:%",
    "yz_pool_missing:%",
]

POOL_TABLES = ["v4_exchange_pools_core4", "v4_exchange_pools_candidate_exchange"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    try:
        ledger = [r[0] for r in cur.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
        tables = {}
        for table in POOL_TABLES:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
            tables[table] = {
                "column_count": len(cols),
                "has_status_reason": "status_reason" in cols,
                "row_count": cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
            }
        skipped_sql = cur.execute(
            "SELECT sql FROM sqlite_master WHERE name='manager_v4_closed_loop_skipped_tokens'"
        ).fetchone()[0]
        skipped_rows = cur.execute(
            "SELECT network, chain_id, protocol, scope_key, anchor_token_key_id,"
            " token_key_id, token_symbol, candidate_role, reason"
            " FROM manager_v4_closed_loop_skipped_tokens"
        ).fetchall()
        quick = cur.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()

    missing = [m for m in EXPECTED_LEDGER if m not in ledger]
    unexpected = [m for m in ledger if m not in EXPECTED_LEDGER]
    return {
        "db_path": str(db_path),
        "db_filename": db_path.name,
        "file_size_bytes": db_path.stat().st_size,
        "sha256": sha256_of(db_path),
        "quick_check": quick,
        "ledger_table": "schema_migrations",
        "ledger_count": len(ledger),
        "ledger": ledger,
        "ledger_missing": missing,
        "ledger_unexpected": unexpected,
        "ledger_complete": not missing and not unexpected,
        "pool_tables": tables,
        "all_pool_tables_have_status_reason": all(
            t["has_status_reason"] for t in tables.values()),
        "skipped_tokens": {
            "parameterized_reasons_present": all(
                r in skipped_sql for r in PARAMETERIZED_REASONS),
            "candidate_role_allows_middle": "'middle'" in skipped_sql,
            "row_count": len(skipped_rows),
            "rows": [list(r) for r in skipped_rows],
        },
    }


def main() -> int:
    targets = [
        ("mainnet-4663", DB_DIR / "local_store_4663.sqlite3"),
        ("testnet-46630", DB_DIR / "local_store_46630.sqlite3"),
    ]
    chains = {}
    for label, path in targets:
        if not path.exists():
            chains[label] = {"error": f"missing database: {path}"}
            continue
        chains[label] = inspect(path)

    backups = sorted(
        p.name for p in (DB_DIR / "backups").glob("local_store_46630.sqlite3.bak_*")
    ) if (DB_DIR / "backups").exists() else []

    report = {
        "report": "TASK-146 baseline framework write (migrations 011..013)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classification": {
            "kind": "baseline_framework_write",
            "contains_dynamic_measurements": False,
            "idempotent": True,
            "data_preserving": True,
            "requires_separate_authorization": False,
            "rule": "基础框架库验证通过后必须实盘写入；动态记录可以不写。",
        },
        "expected_ledger": EXPECTED_LEDGER,
        "parameterized_reasons": PARAMETERIZED_REASONS,
        "chains": chains,
        "testnet_backups": backups,
        "verdict": {
            "mainnet_4663_ledger_complete": chains.get("mainnet-4663", {}).get("ledger_complete"),
            "testnet_46630_ledger_complete": chains.get("testnet-46630", {}).get("ledger_complete"),
            "both_chains_ledger_complete": bool(
                chains.get("mainnet-4663", {}).get("ledger_complete")
                and chains.get("testnet-46630", {}).get("ledger_complete")),
            "both_chains_have_status_reason": bool(
                chains.get("mainnet-4663", {}).get("all_pool_tables_have_status_reason")
                and chains.get("testnet-46630", {}).get("all_pool_tables_have_status_reason")),
            "skipped_token_rows_preserved": (
                chains.get("testnet-46630", {})
                     .get("skipped_tokens", {}).get("row_count") == 2),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
