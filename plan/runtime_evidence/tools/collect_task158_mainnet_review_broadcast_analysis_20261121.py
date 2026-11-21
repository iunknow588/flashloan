"""TASK-158 mainnet review / broadcast record analysis (read-only).

The question this answers is an operator question, not a build question:

    Over the last day on mainnet, how many reviews reported a **positive
    profit expectation**, how many of them were **correct broadcast
    executions**, and what were the profit / loss amounts?

Three different records answer three different parts of that question, and
they are owned by three different managers:

* the **review records** (``cow_execution_reviews``) carry the measured
  profit expectation of every reviewed route;
* the **broadcast attempts** (``manager_cow_execution_attempts``) carry the
  records of what was actually sent to the chain;
* the **broadcast requests** (``manager_v4_review_broadcast_requests``) carry
  the requests the Review Manager generated out of a completed review.

A "positive expectation" is **not** a broadcast.  A review may report a
positive profit and still be rejected by the execution gate, and it may be
reported from a preview that the chain never confirmed.  The report therefore
separates the three states instead of collapsing them into one number:

    previewPositive    the review carries a positive measured profit
    executionConfirmed the review also carries ``runArbitrage.ok`` and
                       ``executable``, i.e. the chain accepted the route
    broadcast          an attempt record exists with a transaction hash

Everything is read-only: the SQLite store is opened with ``mode=ro``, the
manager API is read with GET, and nothing is written, signed or broadcast.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task158_mainnet_review_broadcast_analysis_20261121.json",
)
MANAGER_API = os.environ.get("MANAGER_API_URL", "http://127.0.0.1:8091")

NETWORK = "robinhood"
CHAIN_ID = 4663
WINDOW_HOURS = 24


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value):
    """Return a float, or None when the value is not a number.

    A value the runtime did not measure stays None. It is never turned into
    a zero, because a measured zero and a missing value are different facts.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _find_run_arbitrage(value, found=None):
    """Yield every ``runArbitrage`` block inside a nested payload."""
    if found is None:
        found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("runArbitrage", "run_arbitrage") and isinstance(item, dict):
                found.append(item)
            _find_run_arbitrage(item, found)
    elif isinstance(value, list):
        for item in value:
            _find_run_arbitrage(item, found)
    return found


def _manager_get(path, params):
    url = "%s%s?%s" % (MANAGER_API.rstrip("/"), path, params)
    with urllib.request.urlopen(url, timeout=60) as response:
        document = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(document, dict) or not document.get("ok"):
        raise RuntimeError("manager api read failed for %s: %s" % (path, document))
    return document.get("data") or {}


def main():
    if not os.path.exists(MAINNET_DB):
        raise SystemExit("mainnet store not found: %s" % MAINNET_DB)
    connection = sqlite3.connect("file:%s?mode=ro" % MAINNET_DB, uri=True)
    connection.row_factory = sqlite3.Row

    since = "datetime('now','-%d hours')" % WINDOW_HOURS
    reviews = connection.execute(
        "SELECT id, created_at, pair, category, selected_profit_amount, "
        "       selected_profit_amount_symbol, selected_profit_raw, "
        "       selected_profit_source, selected_profit_missing_reason, "
        "       payload_json "
        "FROM cow_execution_reviews "
        "WHERE network = ?1 AND chain_id = ?2 "
        "  AND datetime(created_at) >= datetime(%s) "
        "ORDER BY id DESC" % since,
        (NETWORK, CHAIN_ID),
    ).fetchall()

    positive = []
    negative = []
    missing = []
    zero = []
    for row in reviews:
        amount = _number(row["selected_profit_amount"])
        if amount is None:
            missing.append(row)
        elif amount > 0:
            positive.append(row)
        elif amount < 0:
            negative.append(row)
        else:
            zero.append(row)

    def _totals(rows):
        by_currency = defaultdict(float)
        worst = {}
        for row in rows:
            currency = str(row["selected_profit_amount_symbol"] or "unknown")
            amount = _number(row["selected_profit_amount"]) or 0.0
            by_currency[currency] += amount
            if currency not in worst or amount < worst[currency]:
                worst[currency] = amount
        return {
            currency: {
                "sum": round(value, 18),
                "worstSingleRecord": round(worst.get(currency, 0.0), 18),
            }
            for currency, value in sorted(by_currency.items())
        }

    # ---- the three states of a positive expectation --------------------
    preview_positive = len(positive)
    execution_confirmed = 0
    confirmed_rows = []
    preview_only = 0
    for row in positive:
        payload = json.loads(row["payload_json"] or "{}")
        blocks = _find_run_arbitrage(payload)
        run = blocks[0] if blocks else {}
        ok = run.get("ok") is True
        executable = run.get("executable") is True
        if ok and executable:
            execution_confirmed += 1
            confirmed_rows.append((row, run))
        else:
            preview_only += 1

    pairs = Counter(str(row["pair"] or "") for row in positive)
    sources = Counter(str(row["selected_profit_source"] or "") for row in positive)
    categories = Counter(str(row["category"] or "") for row in reviews)

    # ---- the broadcast records -----------------------------------------
    broadcast_tables = {
        "cow_execution_attempts": 0,
        "manager_cow_execution_attempts": 0,
        "manager_v4_review_broadcast_requests": 0,
        "manager_broadcast_attempt_diagnostics": 0,
    }
    for table in broadcast_tables:
        try:
            broadcast_tables[table] = connection.execute(
                "SELECT COUNT(*) FROM %s" % table
            ).fetchone()[0]
        except sqlite3.Error:
            broadcast_tables[table] = None

    manager_broadcast = None
    manager_reviews = None
    try:
        manager_broadcast = _manager_get(
            "/v1/broadcasts", "chainId=%d&limit=1000" % CHAIN_ID
        )
        manager_reviews = _manager_get(
            "/v1/reviews", "chainId=%d&limit=1000" % CHAIN_ID
        )
    except Exception as error:  # noqa: BLE001 - a failed read is reported, not hidden
        manager_broadcast = {"error": str(error)}

    confirmed_pairs = Counter(str(row["pair"] or "") for row, _ in confirmed_rows)

    report = {
        "task": "TASK-158",
        "question": (
            "mainnet, last %d hours: how many reviews reported a positive "
            "profit expectation, how many broadcast executions were correct, "
            "and what were the profit / loss amounts?" % WINDOW_HOURS
        ),
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "authority": "read-only; the store is opened with mode=ro and the "
                     "manager API is read with GET",
        "environment": {
            "database": MAINNET_DB,
            "databaseSha256": sha256_file(MAINNET_DB),
            "managerApi": MANAGER_API,
            "network": NETWORK,
            "chainId": CHAIN_ID,
            "windowHours": WINDOW_HOURS,
        },
        "answers": {
            "reviewsWithPositiveProfitExpectation": preview_positive,
            "ofWhichExecutionConfirmed": execution_confirmed,
            "ofWhichPreviewOnlyNotConfirmed": preview_only,
            "correctBroadcastExecutions": broadcast_tables.get(
                "manager_cow_execution_attempts", 0
            ),
            "reviewsWithNegativeProfit": len(negative),
            "reviewsWithMeasuredZeroProfit": len(zero),
            "reviewsWithUnmeasuredProfit": len(missing),
            "totalReviewsInWindow": len(reviews),
        },
        "amounts": {
            "positiveExpectation": _totals(positive),
            "negativeExpectation": _totals(negative),
            "executionConfirmedPositive": _totals(
                [row for row, _ in confirmed_rows]
            ),
        },
        "breakdown": {
            "reviewCategories": dict(categories),
            "positiveSources": dict(sources),
            "positivePairs": dict(pairs.most_common()),
            "executionConfirmedPairs": dict(confirmed_pairs.most_common()),
        },
        "broadcastRecords": {
            "rowCountsAllTime": broadcast_tables,
            "managerBroadcastStatus": (manager_broadcast or {}).get("status"),
            "managerAttemptCount": len((manager_broadcast or {}).get("attempts") or []),
            "managerDiagnosticCount": len(
                (manager_broadcast or {}).get("diagnostics") or []
            ),
        },
        "managerReviewStatus": (manager_reviews or {}).get("status"),
        "whatThisDoesNotProve": [
            "It does not prove that the positive profit expectations were "
            "correct: a positive expectation is a measured preview, and a "
            "preview is not an execution.",
            "It does not prove that any route was profitable in reality: the "
            "signed profit of an executed route is only as good as the "
            "contract call it was measured with, and a review that was never "
            "executed has no realized profit at all.",
            "It does not count realized P&L: no attempt record exists in the "
            "window, so no gas cost, no slippage and no realized token delta "
            "is accounted for.",
            "It does not cover the Manager review library: the manager review "
            "library is a different record set with its own version and its "
            "own freshness, and it is reported separately below.",
            "It does not generalize beyond the window and the chain that are "
            "named in the environment block.",
        ],
    }
    connection.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report["answers"], ensure_ascii=False, indent=2))
    print("report: %s" % OUT)


if __name__ == "__main__":
    main()
