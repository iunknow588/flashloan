#!/usr/bin/env python3
"""Sample closed-loop runtime evidence within one explicit DEX scope.

This script is read-only: it never writes SQLite. It emits one JSON line per
sample. Rows outside the requested scope are counted separately and never enter
the active runtime or broadcast metrics.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "closed-loop-runtime-watch-v2"
TABLES = (
    "cow_execution_attempts",
    "cow_execution_reviews",
    "v4_closed_loop_runtime_state",
)


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, separator: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", separator, text)
    return re.sub(rf"{re.escape(separator)}+", separator, text).strip(separator)


def _scope(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
) -> dict[str, Any]:
    try:
        parsed_chain_id = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("chain_id must be a positive integer") from exc
    if parsed_chain_id <= 0:
        raise ValueError("chain_id must be a positive integer")
    scope = {
        "network": _slug(network, "-"),
        "chainId": parsed_chain_id,
        "protocol": _slug(protocol, "_"),
        "marketId": _slug(market_id, "-"),
    }
    if not all(scope.values()):
        raise ValueError("network, chain_id, protocol, and market_id are required")
    return scope


def _selected_clause(*, include_protocol: bool) -> str:
    clause = (
        "lower(network) = ? AND chain_id = ? "
        "AND replace(lower(market_id), '_', '-') = ?"
    )
    if include_protocol:
        clause += " AND lower(protocol) = ?"
    return clause


def _selected_values(scope: dict[str, Any], *, include_protocol: bool) -> tuple[Any, ...]:
    values: list[Any] = [
        str(scope["network"]),
        int(scope["chainId"]),
        str(scope["marketId"]),
    ]
    if include_protocol:
        values.append(str(scope["protocol"]))
    return tuple(values)


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _out_of_scope_rows(
    con: sqlite3.Connection,
    *,
    table: str,
    scope: dict[str, Any],
    include_protocol: bool,
) -> list[dict[str, Any]]:
    selected = _selected_clause(include_protocol=include_protocol)
    params = _selected_values(scope, include_protocol=include_protocol)
    protocol_sql = "lower(protocol)" if include_protocol else "NULL"
    rows = con.execute(
        f"""
        SELECT lower(network), chain_id, {protocol_sql},
               replace(lower(market_id), '_', '-'), COUNT(*)
        FROM {table}
        WHERE NOT ({selected})
        GROUP BY lower(network), chain_id, {protocol_sql},
                 replace(lower(market_id), '_', '-')
        ORDER BY 1, 2, 3, 4
        """,
        params,
    ).fetchall()
    return [
        {
            "table": table,
            "network": row[0],
            "chainId": int(row[1] or 0),
            "protocol": row[2],
            "marketId": row[3],
            "count": int(row[4] or 0),
        }
        for row in rows
    ]


def _sample(database: str, scope: dict[str, Any]) -> dict[str, Any]:
    con = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro",
        uri=True,
        isolation_level=None,
    )
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        cur.execute("PRAGMA query_only = ON")
        cur.execute("BEGIN")
        missing = [table for table in TABLES if not _table_exists(con, table)]
        if missing:
            return {
                "schema": SCHEMA,
                "utc": _iso_now(),
                "scope": scope,
                "error": "missing_tables",
                "missingTables": missing,
            }

        attempts_where = _selected_clause(include_protocol=False)
        attempts_params = _selected_values(scope, include_protocol=False)
        runtime_where = _selected_clause(include_protocol=True)
        runtime_params = _selected_values(scope, include_protocol=True)

        attempts_total, attempts_max = cur.execute(
            f"""
            SELECT COUNT(*), MAX(created_at)
            FROM cow_execution_attempts
            WHERE {attempts_where}
            """,
            attempts_params,
        ).fetchone()
        reviews_total, reviews_max = cur.execute(
            f"""
            SELECT COUNT(*), MAX(created_at)
            FROM cow_execution_reviews
            WHERE {attempts_where}
            """,
            attempts_params,
        ).fetchone()
        runtime_states = cur.execute(
            f"""
            SELECT state, COUNT(*)
            FROM v4_closed_loop_runtime_state
            WHERE {runtime_where}
            GROUP BY state
            ORDER BY 2 DESC, 1
            """,
            runtime_params,
        ).fetchall()
        preflight_failures = cur.execute(
            f"""
            SELECT COALESCE(last_error, ''), COUNT(*)
            FROM v4_closed_loop_runtime_state
            WHERE {runtime_where} AND state = 'preflight_failed'
            GROUP BY last_error
            ORDER BY 2 DESC, 1
            LIMIT 20
            """,
            runtime_params,
        ).fetchall()

        out_of_scope: list[dict[str, Any]] = []
        for table in ("cow_execution_attempts", "cow_execution_reviews"):
            out_of_scope.extend(
                _out_of_scope_rows(
                    con,
                    table=table,
                    scope=scope,
                    include_protocol=False,
                )
            )
        out_of_scope.extend(
            _out_of_scope_rows(
                con,
                table="v4_closed_loop_runtime_state",
                scope=scope,
                include_protocol=True,
            )
        )
        cur.execute("COMMIT")
    except sqlite3.Error as exc:
        return {
            "schema": SCHEMA,
            "utc": _iso_now(),
            "scope": scope,
            "error": f"sqlite_error: {exc}",
        }
    finally:
        con.close()

    states = {str(row[0]): int(row[1]) for row in runtime_states}
    return {
        "schema": SCHEMA,
        "utc": _iso_now(),
        "scope": scope,
        "scopeApplied": True,
        "attemptsTotal": int(attempts_total or 0),
        "reviewsTotal": int(reviews_total or 0),
        "attemptsMaxAt": attempts_max,
        "reviewsMaxAt": reviews_max,
        "runtimeStates": states,
        "preflightFailedLastErrors": {
            str(row[0]): int(row[1]) for row in preflight_failures
        },
        "stalePreflightRunning": int(states.get("preflight_running", 0)),
        "missingBroadcast": sum(
            count
            for state, count in states.items()
            if state in {"execution_pending", "execution_submitted"}
        ),
        "outOfScope": {
            "total": sum(row["count"] for row in out_of_scope),
            "rows": out_of_scope,
        },
    }


def main() -> int:
    parser = ContractArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()

    try:
        scope = _scope(
            network=args.network,
            chain_id=args.chain_id,
            protocol=args.protocol,
            market_id=args.market_id,
        )
    except ValueError as exc:
        parser.error(str(exc))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    started_at = _iso_now()
    sample_index = 0
    first = True
    while time.monotonic() - started < max(1, args.duration_seconds):
        row = _sample(args.database, scope)
        row.update(
            {
                "sampleIndex": sample_index,
                "startedAt": started_at,
                "elapsedSeconds": round(time.monotonic() - started, 1),
            }
        )
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if first:
            print(json.dumps(row, ensure_ascii=False), flush=True)
            first = False
        sample_index += 1
        time.sleep(max(1, args.interval_seconds))
    row = _sample(args.database, scope)
    row.update(
        {
            "sampleIndex": sample_index,
            "startedAt": started_at,
            "completedAt": _iso_now(),
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "windowComplete": True,
        }
    )
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
