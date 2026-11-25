#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the ``T-广.01`` fixture store and print the admission audit.

The fixture store is a **separate** database file that the probe suite
built from the shipping migrations.  This reader never writes: it opens the
file with ``mode=ro`` and turns on ``PRAGMA query_only`` so a mistake in the
script cannot touch the live store either.

It answers the ``D-070`` question -- *"is every field of the request
consistent with what the guard policy says?"* -- by printing, for every
persisted attempt, the field that the admission decision checked it against.

Usage:
    python read_fixture_broadcast_state_20261125.py [fixture.db]
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\2026OPC大赛\flashLoan"
DEFAULT_FIXTURE = os.path.join(
    ROOT, "plan", "runtime_evidence", "broadcast_admission_fixture_20261125.sqlite3"
)


def connect(path):
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_names(connection, like):
    return [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?", (like,)
        )
    ]


def main():
    fixture = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FIXTURE)
    connection = connect(fixture)

    print("fixture: %s" % fixture)
    print("broadcast tables: %s" % table_names(connection, "%broadcast%"))

    requests = []
    if "manager_v4_review_broadcast_requests" in table_names(connection, "%broadcast_request%"):
        for row in connection.execute(
            "SELECT * FROM manager_v4_review_broadcast_requests ORDER BY rowid"
        ):
            item = dict(row)
            requests.append(item)
    print("\n--- broadcast requests (%d) ---" % len(requests))
    for item in requests:
        print(
            json.dumps(
                {
                    key: item.get(key)
                    for key in (
                        "request_id",
                        "loop_key",
                        "status",
                        "idempotency_key",
                        "chain_id",
                        "amount_raw",
                        "tx_hash",
                    )
                    if key in item
                },
                ensure_ascii=False,
            )
        )

    print("\n--- attempts (field-by-field vs the guard policy) ---")
    for row in connection.execute("SELECT * FROM manager_cow_execution_attempts ORDER BY attempt_id"):
        item = dict(row)
        evidence = json.loads(item.get("evidence_json") or "{}")
        audit = (evidence.get("broadcastAudit") or {})
        admission = (audit.get("admission") or {})
        checks = {check.get("rule"): check for check in (admission.get("checks") or [])}
        print(
            json.dumps(
                {
                    "attempt_id": item.get("attempt_id"),
                    "chain_id": item.get("chain_id"),
                    "signer_address": item.get("signer_address"),
                    "nonce": item.get("nonce"),
                    "gas_limit": item.get("gas_limit"),
                    "gas_price_wei": item.get("gas_price_wei"),
                    "target_contract_column": "<absent from manager_cow_execution_attempts>",
                    "amount_raw": admission.get("amountRaw"),
                    "snapshot": {
                        "closed_loop_version": item.get("closed_loop_version"),
                        "closed_loop_checksum": item.get("closed_loop_checksum"),
                        "review_version": item.get("review_version"),
                        "review_checksum": item.get("review_checksum"),
                    },
                    "idempotency_key": audit.get("idempotencyKey"),
                    "tx_hash": item.get("tx_hash"),
                    "status": item.get("status"),
                    "outcome": audit.get("outcome"),
                    "failed_rules": admission.get("failedRules"),
                    "rule_verdicts": {
                        rule: check.get("satisfied") for rule, check in sorted(checks.items())
                    },
                },
                ensure_ascii=False,
            )
        )

    print("\n--- refusal trails (BC-3) ---")
    for row in connection.execute(
        "SELECT diagnostic_code, diagnostic_type, message, payload_json, observed_at "
        "FROM manager_broadcast_attempt_diagnostics ORDER BY observed_at"
    ):
        payload = json.loads(row[3] or "{}")
        print(
            json.dumps(
                {
                    "diagnostic_code": row[0],
                    "diagnostic_type": row[1],
                    "message": row[2],
                    "observed_at": row[4],
                    "broadcasted": payload.get("broadcasted"),
                    "outcome": payload.get("outcome"),
                    "unsatisfied_rules": payload.get("unsatisfiedRules"),
                },
                ensure_ascii=False,
            )
        )

    print("\n--- broadcast library state ---")
    for row in connection.execute("SELECT * FROM manager_broadcast_library_state"):
        print(json.dumps(dict(row), ensure_ascii=False))

    connection.close()


if __name__ == "__main__":
    main()
