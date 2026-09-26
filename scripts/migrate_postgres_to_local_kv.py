#!/usr/bin/env python3
"""Optional exporter from a legacy Postgres database into the local SQLite KV store.

Run this before deleting DATABASE_URL if the old database still exists:

    python scripts/migrate_postgres_to_local_kv.py [--kv-path PATH]

The script reads the configured local KV path (FLASHLOAN_KV_PATH) when
--kv-path is not supplied, connects to DATABASE_URL if psycopg is installed,
and copies each known Postgres table into its matching KV collection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
APP_RUNTIME_DIR = REPO_ROOT / "flashloan" / "app_runtime"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(APP_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(APP_RUNTIME_DIR))


PREFERRED_COLLECTION_MAP = {
    "control_panel_parameters": "parameters",
    "page_state": "page_state",
    "observations": "observations",
    "binance_price_history": "binance_price_history",
    "binance_candidate_price_history": "binance_candidate_price_history",
    "binance_pair_price_history": "binance_pair_price_history",
    "binance_window_extremes": "binance_window_extremes",
    "arbitrage_simulations": "arbitrage_simulations",
    "liquidation_accounts": "liquidation_accounts",
    "liquidation_execution_attempts": "liquidation_execution_attempts",
    "liquidation_failure_samples": "liquidation_failure_samples",
    "liquidation_borrow_health_pool": "liquidation_borrow_health_pool",
    "liquidation_high_frequency_pool": "liquidation_high_frequency_pool",
    "liquidation_core_opportunity_pool": "liquidation_core_opportunity_pool",
    "liquidation_borrow_health_scan_batches": "liquidation_borrow_health_scan_batches",
    "liquidation_scan_config_library": "liquidation_scan_config_library",
    "chain_markets": "chain_markets",
    "cow_supported_tokens": "cow_supported_tokens",
    "cow_execution_attempts": "cow_execution_attempts",
}


def _kv_path_from_args(args: argparse.Namespace) -> Path:
    from core.local_kv import store_path

    return store_path(args.kv_path)


def _row_key(row: dict[str, Any], table_name: str, index: int) -> str:
    for key in ("id", "account", "market_id", "key", "page_key", "observed_at"):
        if key in row and row[key] not in (None, ""):
            return f"{table_name}|{row[key]}"
    return f"{table_name}|{index}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _export_table(psycopg: Any, connection: Any, kv_path: Path, table_name: str) -> int:
    from core.local_kv import put

    collection = PREFERRED_COLLECTION_MAP.get(table_name, f"table:{table_name}")
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM "{table_name}"')
        columns = [column.name for column in cursor.description]
        written = 0
        for index, raw_row in enumerate(cursor.fetchall()):
            row = {column: _json_safe(value) for column, value in zip(columns, raw_row)}
            put(kv_path, collection, _row_key(row, table_name, index), row)
            written += 1
    return written


def _table_names(connection: Any) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )
        return [str(row[0]) for row in cursor.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-path", type=str, default=None, help="Local SQLite KV path override.")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set. Skipping legacy Postgres export.")
        return

    try:
        import psycopg
    except Exception:
        try:
            import psycopg2 as psycopg
        except Exception as exc:
            raise SystemExit(
                "DATABASE_URL is set but psycopg/psycopg2 is not installed. "
                "Install it temporarily if you need to export the legacy database."
            ) from exc

    kv_path = _kv_path_from_args(args)
    from core.local_kv import ensure_store

    ensure_store(kv_path)
    connection = psycopg.connect(database_url)
    try:
        total_written = 0
        for table_name in _table_names(connection):
            written = _export_table(psycopg, connection, kv_path, table_name)
            total_written += written
            print(f"{table_name}: {written} rows")
        print(f"Exported {total_written} rows to {kv_path}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
