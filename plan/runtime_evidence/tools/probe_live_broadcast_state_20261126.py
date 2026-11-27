"""Read-only probe of the live store: what the broadcast chain holds now.

Prints the review rows, the published broadcast requests, the execution
attempts and the broadcast library state, newest first, so a deployment
can be judged by what the store holds rather than by what it should hold.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


DB = Path("flashloan/db/local_store_4663.sqlite3").resolve()


def dump(connection: sqlite3.Connection, table: str, columns: str, limit: int = 12) -> list[dict]:
    try:
        rows = connection.execute(
            f"SELECT {columns} FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.Error as exc:
        return [{"error": str(exc)}]
    return [dict(row) for row in rows]


def main() -> int:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    out: dict[str, object] = {}
    for table in (
        "manager_v4_reviews",
        "manager_v4_review_broadcast_requests",
        "manager_cow_execution_attempts",
        "manager_broadcast_library_state",
    ):
        try:
            total = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error as exc:
            out[table] = {"error": str(exc)}
            continue
        out[f"{table}__count"] = total
    out["requests"] = dump(
        connection,
        "manager_v4_review_broadcast_requests",
        "loop_key, request_status, signed_pnl_raw, signed_pnl_amount, created_at, network, chain_id",
    )
    out["attempts"] = dump(
        connection,
        "manager_cow_execution_attempts",
        "attempt_id, loop_key, status, signed_pnl_raw, nonce, rpc_url, created_at",
    )
    out["library_state"] = dump(
        connection,
        "manager_broadcast_library_state",
        "*",
        limit=6,
    )
    try:
        out["reviews_newest"] = dump(
            connection,
            "manager_v4_reviews",
            "review_id, loop_key, review_status, created_at, network, chain_id",
        )
    except sqlite3.Error:
        out["reviews_newest"] = []
    connection.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
