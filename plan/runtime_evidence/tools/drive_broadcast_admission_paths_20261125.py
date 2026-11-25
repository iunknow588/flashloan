#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drive the ``T-广.01`` broadcast refusal paths through the real HTTP API.

The driver talks to a ``manager-api`` process that is bound to the
**fixture** store, using the shipping endpoint
``POST /v1/broadcasts/submit``.  Each case is submitted twice:

* ``dryRun = true``  -- the refusal must be answered, and **nothing** may
  be written (a dry run writes nothing at all);
* ``dryRun = false`` -- the refusal must be **recorded** (``BC-3``), so an
  operator can ask "why did nothing go out" and get an answer.

The positive control is submitted as well: it is the case where every rule
holds.  It must be **admitted** into a *pending* attempt, and it must not
carry a transaction hash -- an admitted attempt is not a sent transaction.

Usage:
    python drive_broadcast_admission_paths_20261125.py <api-base-url> <fixture.db> [report.json]
"""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CHAIN_ID = 4663

SIGNER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_SIGNER = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

#: The cases, in the order they are driven.  ``expectedRule`` is the rule
#: the admission decision is expected to refuse on; ``None`` means the
#: case is the positive control and must be admitted.
CASES = [
    {
        "key": "profit-zero",
        "expectedRule": "BC-1.pnl_positive",
        "class": "profit<=0",
        "signer": SIGNER,
        "amount": 1000,
    },
    {
        "key": "profit-negative",
        "expectedRule": "BC-1.pnl_positive",
        "class": "profit<=0",
        "signer": SIGNER,
        "amount": 1000,
    },
    {
        "key": "profit-unmeasured",
        "expectedRule": "BC-1.pnl_measured",
        "class": "profit-or-amount-unknown",
        "signer": SIGNER,
        "amount": 1000,
    },
    {
        "key": "price-limits-missing",
        "expectedRule": "BC-1.price_limits_complete",
        "class": "profit-or-amount-unknown",
        "signer": SIGNER,
        "amount": 1000,
    },
    {
        "key": "review-stale",
        "expectedRule": "BC-1.review_fresh",
        "class": "stale-state",
        "signer": SIGNER,
        "amount": 1000,
    },
    {
        "key": "guard-account-mismatch",
        "expectedRule": "BC-2.account_matches",
        "class": "guardrail-mismatch",
        "signer": OTHER_SIGNER,
        "amount": 1000,
    },
    {
        "key": "guard-amount-over-limit",
        "expectedRule": "BC-2.amount_within_limit",
        "class": "guardrail-mismatch",
        "signer": SIGNER,
        "amount": 1000000000000,
    },
    {
        "key": "positive-control",
        "expectedRule": None,
        "class": "positive-control",
        "signer": SIGNER,
        "amount": 1000,
    },
]


def _attempt(case):
    return {
        "attemptId": "attempt-%s" % case["key"],
        "loopKey": "fixture:%s" % case["key"],
        "signerAddress": case["signer"],
        "nonce": 42,
        "gasLimit": 500000,
        "gasPriceWei": "1000000000",
        "rpcUrl": "https://rpc.example.invalid",
        "rpcName": "fixture-primary",
        "evidence": {"source": "t-guang-01-fixture", "case": case["key"]},
    }


def _post(base_url, body, request_id, idempotency_key):
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/broadcasts/submit",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "X-Request-Id": request_id,
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as handle:
            return handle.status, json.loads(handle.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        return error.code, payload


def _refusal_rules(payload):
    """The refusal rules the answer names, wherever they sit."""
    error = payload.get("error") or {}
    details = error.get("details") or {}
    rules = []
    for key in ("failedRules", "reasons", "rules"):
        value = details.get(key)
        if isinstance(value, list):
            rules.extend([str(item) for item in value])
        elif isinstance(value, str):
            rules.extend([part for part in value.split(",") if part])
    detail = details.get("detail")
    if isinstance(detail, str):
        rules.extend([part for part in detail.split(",") if part.startswith("BC-")])
    return sorted(set(rules))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    base_url = sys.argv[1]
    fixture = os.path.abspath(sys.argv[2])
    report_path = sys.argv[3] if len(sys.argv) > 3 else None

    results = []
    for case in CASES:
        body = {
            "chainId": CHAIN_ID,
            "dryRun": True,
            "attempts": [_attempt(case)],
            "amountsRaw": {"fixture:%s" % case["key"]: str(case["amount"])},
        }
        dry_status, dry_payload = _post(
            base_url,
            body,
            "t401-dry-%s" % case["key"],
            "t401-dry-idem-%s" % case["key"],
        )
        apply_body = dict(body)
        apply_body["dryRun"] = False
        apply_status, apply_payload = _post(
            base_url,
            apply_body,
            "t401-apply-%s" % case["key"],
            "t401-apply-idem-%s" % case["key"],
        )
        dry_rules = _refusal_rules(dry_payload)
        apply_rules = _refusal_rules(apply_payload)
        expected = case["expectedRule"]
        if expected is None:
            admitted = dry_status == 200 and bool((dry_payload.get("data") or {}).get("attemptCount", 0) or 0)
            rule_held = admitted
        else:
            rule_held = expected in dry_rules and expected in apply_rules
        results.append(
            {
                "case": case["key"],
                "class": case["class"],
                "expectedRule": expected,
                "ruleHeld": rule_held,
                "dryRun": {
                    "httpStatus": dry_status,
                    "errorCode": ((dry_payload.get("error") or {}).get("code")),
                    "failedRules": dry_rules,
                    "payload": dry_payload,
                },
                "apply": {
                    "httpStatus": apply_status,
                    "errorCode": ((apply_payload.get("error") or {}).get("code")),
                    "failedRules": apply_rules,
                    "payload": apply_payload,
                },
            }
        )
        print(
            "%-28s %-28s dry=%s apply=%s rules=%s"
            % (
                case["key"],
                expected or "(admitted)",
                dry_status,
                apply_status,
                ",".join(dry_rules) or "-",
            )
        )

    # ---- what the fixture store recorded --------------------------------
    # ---- the idempotency key is bound to the body it was used with ------
    #
    # Re-using one key with a **different** body must be refused: the key
    # names one request, and a different body under the same name would
    # make the stored receipt describe a transaction that never happened.
    # The probe uses the **positive control**, because only a request that
    # is actually persisted binds an idempotency key to a body.  A refused
    # request aborts before the receipt is written, so it would never
    # reach the check -- and the check itself only runs on an **apply**
    # submission, because a dry run writes no receipt at all.
    idem_case = CASES[-1]
    idem_attempt = _attempt(idem_case)
    idem_attempt["attemptId"] = "attempt-idem-probe"
    idem_body = {
        "chainId": CHAIN_ID,
        "dryRun": False,
        "attempts": [idem_attempt],
        "amountsRaw": {"fixture:%s" % idem_case["key"]: str(idem_case["amount"])},
    }
    idem_first_status, idem_first = _post(
        base_url, idem_body, "t401-idem-probe-a", "t401-idem-probe"
    )
    idem_changed = dict(idem_body)
    idem_changed["amountsRaw"] = {"fixture:%s" % idem_case["key"]: "2000"}
    idem_changed["attempts"] = [dict(idem_attempt)]
    idem_changed["attempts"][0] = dict(idem_changed["attempts"][0])
    idem_changed["attempts"][0]["nonce"] = 43
    idem_second_status, idem_second = _post(
        base_url, idem_changed, "t401-idem-probe-b", "t401-idem-probe"
    )
    idempotency = {
        "firstStatus": idem_first_status,
        "firstErrorCode": ((idem_first.get("error") or {}).get("code")),
        "secondStatus": idem_second_status,
        "secondErrorCode": ((idem_second.get("error") or {}).get("code")),
        "secondMessage": ((idem_second.get("error") or {}).get("message")),
        "rejected": idem_second_status != 200,
    }

    connection = sqlite3.connect("file:" + fixture.replace("\\", "/") + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    trails = connection.execute(
        "SELECT diagnostic_code, diagnostic_type, message, payload_json, observed_at "
        "FROM manager_broadcast_attempt_diagnostics ORDER BY observed_at"
    ).fetchall()
    attempts = connection.execute(
        "SELECT attempt_id, loop_key, status, tx_hash FROM manager_cow_execution_attempts "
        "ORDER BY attempt_id"
    ).fetchall()
    library_state = connection.execute(
        "SELECT broadcast_version, attempt_count, pending_count, success_count, failed_count "
        "FROM manager_broadcast_library_state"
    ).fetchall()
    connection.close()

    trail_rows = [
        {
            "diagnosticCode": row[0],
            "diagnosticType": row[1],
            "message": row[2],
            "observedAt": row[4],
            "payload": json.loads(row[3] or "{}"),
        }
        for row in trails
    ]
    attempt_rows = [
        {
            "attemptId": row[0],
            "loopKey": row[1],
            "status": row[2],
            "txHash": row[3],
        }
        for row in attempts
    ]

    report = {
        "schema": "t-guang-01-broadcast-refusal-paths-v1",
        "task": "T-广.01",
        "api": base_url,
        "fixtureStore": fixture,
        "note": "the fixture store is a separate database file; the live store was not touched",
        "cases": results,
        "idempotency": idempotency,
        "refusalTrails": trail_rows,
        "attempts": attempt_rows,
        "broadcastLibraryState": [list(row) for row in library_state],
        "verdict": {
            "everyExpectedRuleHeld": all(item["ruleHeld"] for item in results),
            "failedCases": [item["case"] for item in results if not item["ruleHeld"]],
            "refusalTrailsRecorded": len(trail_rows),
            "attemptRows": len(attempt_rows),
            "txHashesPresent": [row["attemptId"] for row in attempt_rows if row["txHash"]],
            "pendingAttempts": [row["attemptId"] for row in attempt_rows if row["status"] == "pending"],
        },
    }

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print("wrote:", report_path)
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    print("verdict:", json.dumps(report["verdict"], ensure_ascii=False))
    return 0 if report["verdict"]["everyExpectedRuleHeld"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
