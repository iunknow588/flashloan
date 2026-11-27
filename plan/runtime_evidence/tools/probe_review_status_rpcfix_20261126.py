#!/usr/bin/env python3
"""Read the live store and report how the closed-loop reviews fare now.

The probe opens the store **read-only** (`mode=ro` plus
`PRAGMA query_only = ON`): the question is "does the review path price the
route now that the pricing path knows every endpoint of the chain?", and
answering it must not change the thing being measured.

Usage:

    python plan/runtime_evidence/tools/probe_review_status_rpcfix_20261126.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
JSONL_ROOT = REPO / "flashloan" / "db" / "review_record_jsonl"
TRADE_ROOT = REPO / "flashloan" / "db" / "broadcast_transaction_db"


def main() -> None:
    uri = "file:" + STORE.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    report: dict[str, object] = {"store": STORE.as_posix()}

    report["review_status_counts"] = dict(
        connection.execute(
            "SELECT review_status, COUNT(*) FROM manager_v4_reviews"
            " GROUP BY review_status ORDER BY review_status"
        ).fetchall()
    )
    report["review_rows"] = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_reviews"
    ).fetchone()[0]
    report["review_updated_at_max"] = connection.execute(
        "SELECT MAX(updated_at) FROM manager_v4_reviews"
    ).fetchone()[0]

    try:
        report["diagnostic_type_counts"] = dict(
            connection.execute(
                "SELECT diagnostic_type, COUNT(*) FROM manager_v4_review_diagnostics"
                " GROUP BY diagnostic_type ORDER BY diagnostic_type"
            ).fetchall()
        )
    except sqlite3.Error as error:
        report["diagnostic_type_counts"] = "ERR %s" % error

    try:
        report["diagnostic_code_counts"] = dict(
            connection.execute(
                "SELECT diagnostic_code, COUNT(*) FROM manager_v4_review_diagnostics"
                " GROUP BY diagnostic_code ORDER BY diagnostic_code"
            ).fetchall()
        )
    except sqlite3.Error as error:
        report["diagnostic_code_counts"] = "ERR %s" % error

    try:
        report["diagnostic_observed_at_max"] = connection.execute(
            "SELECT MAX(observed_at) FROM manager_v4_review_diagnostics"
        ).fetchone()[0]
    except sqlite3.Error as error:
        report["diagnostic_observed_at_max"] = "ERR %s" % error

    try:
        rows = connection.execute(
            "SELECT observed_at, diagnostic_type, diagnostic_code, message,"
            " payload_json FROM manager_v4_review_diagnostics"
            " ORDER BY observed_at DESC LIMIT 5"
        ).fetchall()
        errors = []
        for observed_at, dtype, code, message, payload in rows:
            detail = None
            try:
                detail = json.loads(payload).get("error")
            except (TypeError, ValueError):
                detail = str(payload)[:200]
            errors.append(
                {
                    "observed_at": observed_at,
                    "type": dtype,
                    "code": code,
                    "message": (message or "")[:160],
                    "error": (detail or "")[:260],
                }
            )
        report["recent_diagnostics"] = errors
    except sqlite3.Error as error:
        report["recent_diagnostics"] = "ERR %s" % error

    for table in (
        "manager_cow_execution_attempts",
        "manager_broadcast_attempt_diagnostics",
        "manager_v4_review_broadcast_requests",
    ):
        try:
            report[table] = connection.execute(
                "SELECT COUNT(*) FROM %s" % table
            ).fetchone()[0]
        except sqlite3.Error as error:
            report[table] = "ERR %s" % error

    def tree(root: Path) -> dict[str, object]:
        if not root.exists():
            return {"exists": False}
        files = [p for p in root.rglob("*") if p.is_file()]
        return {
            "exists": True,
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "newest": max((p.stat().st_mtime for p in files), default=0),
            "sample": [str(p.relative_to(REPO)) for p in files[:6]],
        }

    report["review_record_jsonl"] = tree(JSONL_ROOT)
    report["broadcast_transaction_db"] = tree(TRADE_ROOT)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
