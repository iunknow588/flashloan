"""Read-only probe of the review -> broadcast gap on the mainnet catalog.

The supervisor keeps reporting ``positive=<n>`` reviews while the broadcast
layer records zero attempts.  This probe prints, straight from the store:

* every closed loop runtime row (the two review moments + the runtime status);
* every review row whose signed PnL is positive, together with the loop it
  belongs to and whether that loop is still current;
* every broadcast attempt row.

Usage::

    python probe_broadcast_gap_20261127.py --database flashloan/db/local_store_4663.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _tables(connection: sqlite3.Connection) -> list[str]:
    return sorted(
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))


def probe(database: Path, network: str, chain_id: int) -> None:
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        names = _tables(connection)
        print("== tables of interest ==")
        for name in names:
            if any(key in name for key in
                   ("closed_loop", "review", "broadcast", "candidate_pool",
                    "pool", "token")):
                print("  ", name)

        print("== closed loop runtime state ==")
        for row in connection.execute(
                """SELECT loop_key, runtime_status, last_reviewed_at,
                          next_review_at, review_cycle_started_at
                     FROM manager_v4_closed_loop_runtime_state
                    WHERE network = ?1 AND chain_id = ?2
                    ORDER BY loop_key""", (network, chain_id)):
            print("  ", row)

        for table in ("manager_v4_reviews", "manager_v4_review_broadcast_requests",
                      "manager_v4_review_diagnostics"):
            print("== %s ==" % table)
            columns = [str(row[1]) for row in connection.execute(
                "PRAGMA table_info(%s)" % table)]
            print("   columns:", ",".join(columns))
            try:
                for row in connection.execute(
                        "SELECT * FROM %s ORDER BY rowid DESC LIMIT 8" % table):
                    print("   ", dict(zip(columns, row)))
            except sqlite3.OperationalError as error:
                print("   unavailable:", error)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    arguments = parser.parse_args()
    probe(arguments.database, arguments.network, arguments.chain_id)


if __name__ == "__main__":
    main()
