#!/usr/bin/env python3
"""Split the legacy single SQLite store into per-chain SQLite files.

This tool is intentionally read-only against the source database.  For every
chain registered in ``flashloan/src/chains/registry.json`` it creates
``<sqlite_root>/<database.sqlite_file>`` (or the default
``local_store_<chain_id>.sqlite3`` when the chain JSON does not configure a
file).  Global tables plus per-chain rows are copied into each new database;
the source file is never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "flashloan" / "src"
CHAIN_DATA_ROOT = SRC_ROOT / "chains"
DB_ROOT = SRC_ROOT.parent / "db"

# Tables that are shared across chains and copied verbatim into every new file.
GLOBAL_TABLES = {
    "chain_registry_meta",
    "chain_registry",
    "chain_configs",
    "chain_info",
    "chain_contracts",
    "chain_data_files",
    "chain_flows",
    "chain_markets",
}

# Kv tables are global too: they hold chain-neutral parameters and page state.
KV_TABLES = {"kv_entries", "kv_meta"}

# BNB is designed as a shared market quote source consumed by other chains.
# These tables live in the dedicated BNB shared database
# (``local_store_bnb.sqlite3``) and are not copied into per-chain files.
SHARED_MARKET_TABLES = {"bnb_market_observations", "bnb_market_snapshots"}

# Tables whose rows are scoped by (network, chain_id).  Tables not listed here
# are copied only if they have no chain columns and are not engine internals.
_CHAIN_UNIVERSE = {
    "chain_business_initialization",
    "chain_tokens",
    "v4_exchange_pools",
    "v4_exchange_pool_liquidity_snapshots",
    "v4_closed_loops",
    "v4_closed_loop_hops",
    "v4_closed_loop_optimizations",
    "dex_surface_snapshots",
    "dex_candidate_anchor_tokens",
    "dex_candidate_pool_pairs",
    "dex_candidate_pools",
    "dex_candidate_pool_recommendations",
    "dex_candidate_abnormal_anchor_tokens",
    "dex_candidate_abnormal_pool_pairs",
    "dex_candidate_abnormal_pools",
    "market_observation_yz_pairs",
    "market_observation_intermediate_pools",
    "market_observation_abnormal_yz_pairs",
    "market_observation_abnormal_intermediate_pools",
    "market_observation_liquidity_snapshots",
    "cow_execution_attempts",
    "cow_execution_reviews",
}

SKIP_TABLES = {"sqlite_sequence"}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _chain_rows() -> list[dict[str, Any]]:
    registry = _load_json(CHAIN_DATA_ROOT / "registry.json")
    chains = registry.get("chains") if isinstance(registry, dict) else {}
    rows: list[dict[str, Any]] = []
    for raw_chain_id, item in chains.items() if isinstance(chains, dict) else []:
        try:
            chain_id = int(str(raw_chain_id).strip())
        except (TypeError, ValueError):
            continue
        row = item if isinstance(item, dict) else {}
        network = str(row.get("network") or "").strip().lower()
        directory = str(row.get("directory") or "").strip()
        if not network or not directory:
            continue
        chain_json_path = CHAIN_DATA_ROOT / directory / "chain.json"
        database = {}
        if chain_json_path.exists():
            try:
                payload = _load_json(chain_json_path)
                database = payload.get("database") if isinstance(payload.get("database"), dict) else {}
            except (OSError, ValueError):
                database = {}
        sqlite_file = str(database.get("sqlite_file") or "").strip()
        if not sqlite_file:
            sqlite_file = f"local_store_{chain_id}.sqlite3"
        rows.append(
            {
                "chain_id": chain_id,
                "network": network,
                "directory": directory,
                "sqlite_file": sqlite_file,
            }
        )
    return rows


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info('{table}')")
    ]


def _row_values(row: sqlite3.Row, columns: list[str]) -> tuple[Any, ...]:
    return tuple(row[name] for name in columns)


def _copy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    *,
    where: str | None = None,
    params: Iterable[Any] = (),
) -> int:
    columns = _columns(source, table)
    if not columns:
        return 0
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{name}"' for name in columns)
    sql = f"SELECT {quoted} FROM \"{table}\""
    if where:
        sql += f" WHERE {where}"
    rows = source.execute(sql, tuple(params)).fetchall()
    if not rows:
        return 0
    target.executemany(
        f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})',
        [_row_values(row, columns) for row in rows],
    )
    return len(rows)


def _copy_chain_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
    *,
    chain_id: int,
    network: str,
) -> int:
    columns = _columns(source, table)
    if "network" in columns and "chain_id" in columns:
        return _copy_table(
            source,
            target,
            table,
            where="chain_id = ? AND network = ?",
            params=(int(chain_id), network),
        )
    return 0


def _table_is_scoped_by_chain(table: str, columns: list[str]) -> bool:
    return "network" in columns and "chain_id" in columns


def _copy_unknown_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
) -> int:
    """Copy non-engine tables that are neither global nor chain-universe."""

    columns = _columns(source, table)
    if not columns:
        return 0
    if _table_is_scoped_by_chain(table, columns):
        # Unknown chain-scoped tables cannot be split safely without an
        # explicit universe entry; keep the new database empty for them.
        return 0
    return _copy_table(source, target, table)


def _copy_indexes_and_triggers(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for row in source.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') AND sql IS NOT NULL"
    ):
        sql = str(row[0])
        try:
            target.execute(sql)
        except sqlite3.OperationalError:
            pass


def split_database(
    source_path: Path,
    output_dir: Path,
    *,
    chains: list[dict[str, Any]] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Split ``source_path`` into per-chain files under ``output_dir``."""

    chains = chains if chains is not None else _chain_rows()
    if not chains:
        raise ValueError("no chains found in chain registry")
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        source_tables = _table_names(source)
        report: dict[str, Any] = {"source": str(source_path), "chains": []}
        for chain in chains:
            chain_id = int(chain["chain_id"])
            network = str(chain["network"])
            filename = str(chain["sqlite_file"])
            target_path = output_dir / filename
            chain_report: dict[str, Any] = {
                "chain_id": chain_id,
                "network": network,
                "target": str(target_path),
                "exists": target_path.exists(),
                "tables": {},
                "rows": {},
            }
            if target_path.exists() and not force and not dry_run:
                chain_report["skipped"] = "target_exists_without_force"
                report["chains"].append(chain_report)
                continue
            if dry_run:
                for table in sorted(source_tables):
                    columns = _columns(source, table)
                    if table in SHARED_MARKET_TABLES:
                        count = 0
                        chain_report.setdefault("bnb_shared_tables", []).append(table)
                    elif table in GLOBAL_TABLES or table in KV_TABLES:
                        count = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    elif table in _CHAIN_UNIVERSE and _table_is_scoped_by_chain(
                        table, columns
                    ):
                        count = source.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE chain_id=? AND network=?',
                            (chain_id, network),
                        ).fetchone()[0]
                    elif table in _CHAIN_UNIVERSE:
                        count = source.execute(
                            f'SELECT COUNT(*) FROM "{table}"'
                        ).fetchone()[0]
                        chain_report.setdefault("universe_fallback_tables", []).append(
                            table
                        )
                    elif _table_is_scoped_by_chain(table, columns):
                        count = 0
                        chain_report.setdefault(
                            "unsupported_chain_scoped_tables", []
                        ).append(table)
                    else:
                        count = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    chain_report["tables"][table] = count
                chain_report["dry_run"] = True
                report["chains"].append(chain_report)
                continue
            tmp_path = target_path.with_suffix(".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            target = sqlite3.connect(tmp_path)
            try:
                target.executescript(
                    "PRAGMA journal_mode=WAL;"
                    "PRAGMA foreign_keys=OFF;"
                    "PRAGMA synchronous=NORMAL;"
                )
                for statement in source.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
                ):
                    statement_sql = str(statement[0])
                    match = re.match(
                        r'\s*CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+"?([^"\s]+)"?',
                        statement_sql,
                        flags=re.IGNORECASE,
                    )
                    if match and str(match.group(1)).lower().startswith("sqlite_"):
                        continue
                    target.execute(
                        statement_sql.replace(
                            "CREATE TABLE",
                            "CREATE TABLE IF NOT EXISTS",
                        )
                    )
                target.commit()
                for table in sorted(source_tables):
                    if table.startswith("sqlite_"):
                        continue
                    columns = _columns(source, table)
                    if table in SHARED_MARKET_TABLES:
                        chain_report.setdefault("bnb_shared_tables", []).append(table)
                        chain_report["tables"][table] = 0
                        continue
                    elif table in GLOBAL_TABLES or table in KV_TABLES:
                        copied = _copy_table(source, target, table)
                    elif table in _CHAIN_UNIVERSE and _table_is_scoped_by_chain(
                        table, columns
                    ):
                        copied = _copy_chain_table(
                            source,
                            target,
                            table,
                            chain_id=chain_id,
                            network=network,
                        )
                    elif table in _CHAIN_UNIVERSE:
                        copied = _copy_table(source, target, table)
                        chain_report.setdefault("universe_fallback_tables", []).append(
                            table
                        )
                    elif _table_is_scoped_by_chain(table, columns):
                        chain_report.setdefault(
                            "unsupported_chain_scoped_tables", []
                        ).append(table)
                        chain_report["tables"][table] = 0
                        continue
                    else:
                        copied = _copy_unknown_table(source, target, table)
                    chain_report["tables"][table] = copied
                    if table in GLOBAL_TABLES or table in KV_TABLES:
                        chain_report["rows"].setdefault("global", 0)
                        chain_report["rows"]["global"] += copied
                    elif table in _CHAIN_UNIVERSE or _table_is_scoped_by_chain(table, columns):
                        chain_report["rows"].setdefault("chain_scoped", 0)
                        chain_report["rows"]["chain_scoped"] += copied
                    else:
                        chain_report["rows"].setdefault("other", 0)
                        chain_report["rows"]["other"] += copied
                _copy_indexes_and_triggers(source, target)
                meta = {
                    "split_at": "utc",
                    "source": str(source_path),
                    "chain_id": chain_id,
                    "network": network,
                }
                target.execute(
                    "INSERT OR REPLACE INTO kv_meta (meta_key, value_json) VALUES (?, ?)",
                    ("chain_database_split", json.dumps(meta, ensure_ascii=False)),
                )
                target.commit()
                try:
                    target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    target.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.OperationalError:
                    pass
            finally:
                target.close()
            tmp_path.replace(target_path)
            chain_report["created"] = True
            report["chains"].append(chain_report)
        return report
    finally:
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DB_ROOT / "local_store.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=DB_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--chains", nargs="*", type=int, default=None)
    args = parser.parse_args()

    chains = _chain_rows()
    if args.chains:
        wanted = {int(item) for item in args.chains}
        chains = [row for row in chains if int(row["chain_id"]) in wanted]
    print(
        json.dumps(
            split_database(
                args.source,
                args.output_dir,
                chains=chains,
                force=args.force,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
