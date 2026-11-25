#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the acceptance criteria of every item that is still on the board.

The remaining list is split into four classes.  Only the *blocked* class is
real work; this probe measures every criterion of that class in one pass so
that a checklist box is only ever ticked on a measured reading.

Read-only by construction:

* the live store is opened with ``mode=ro`` **and** ``PRAGMA query_only = ON``;
* the Manager API and the runtime API are only ever read over HTTP.

Usage:
    python probe_remaining_closure_20261125.py [--out report.json]
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
MANAGER_API = "http://127.0.0.1:8091"
RUNTIME_API = "http://127.0.0.1:5000"
CHAIN_ID = 4663
NETWORK = "robinhood"
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def get_json(url: str, timeout: int = 240):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def open_read_only(path: Path) -> sqlite3.Connection:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def reason_of(review: dict) -> str:
    return str(review.get("reviewReason") or "")


def measure(database_path: Path) -> dict:
    now = datetime.datetime.now().astimezone()
    reviews_payload = get_json(f"{MANAGER_API}/v1/reviews?chainId={CHAIN_ID}")
    data = reviews_payload.get("data", {})
    status = data.get("status", {})
    reviews = data.get("reviews", [])

    connection = open_read_only(database_path)
    try:
        broadcast_rows = connection.execute(
            "SELECT COUNT(*) FROM manager_broadcast_library_state"
        ).fetchone()[0]
        broadcast_requests = connection.execute(
            "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
        ).fetchone()[0]
        runtime_state_rows = connection.execute(
            "SELECT COUNT(*) FROM v4_closed_loop_runtime_state"
        ).fetchone()[0]
        runtime_state_priced = connection.execute(
            "SELECT COUNT(*) FROM v4_closed_loop_runtime_state"
            " WHERE state NOT IN ('preflight_running', 'claimed')"
        ).fetchone()[0]
    finally:
        connection.close()

    status_counts: dict[str, int] = {}
    for review in reviews:
        key = str(review.get("reviewStatus") or "")
        status_counts[key] = status_counts.get(key, 0) + 1

    drift = [
        review for review in reviews
        if "pool_state_version_drift" in reason_of(review)
    ]
    single_tick_gate = [
        review for review in reviews
        if "single_tick_gate_failed" in reason_of(review)
    ]
    other_gate = [
        review for review in single_tick_gate
        if "pool_state_version_drift" not in reason_of(review)
    ]

    signed_pnl_missing = []
    best_amount_missing = []
    incomplete_fields = []
    missing_field_names: dict[str, int] = {}
    pnl_signs = {"positive": 0, "zero": 0, "negative": 0, "missing": 0}
    for review in reviews:
        signed = review.get("signedPnl") or {}
        raw = signed.get("raw")
        missing_fields = []
        for field in ("raw", "currency", "unit", "source"):
            if signed.get(field) in (None, ""):
                missing_fields.append(f"signedPnl.{field}")
        preview = review.get("staticPreview") or {}
        calculation = preview.get("bestAmountCalculation") or {}
        if calculation.get("bestAmountRaw") in (None, ""):
            missing_fields.append("staticPreview.bestAmountCalculation.bestAmountRaw")
        if calculation.get("bestAmountMeasured") is not True:
            missing_fields.append("staticPreview.bestAmountCalculation.bestAmountMeasured")
        for field in ("closedLoopChecksum", "poolScopeChecksum", "reviewedAt"):
            if review.get(field) in (None, ""):
                missing_fields.append(field)
        if calculation.get("bestAmountMissing") is True:
            best_amount_missing.append(review)
        if raw in (None, ""):
            signed_pnl_missing.append(review)
        if missing_fields:
            incomplete_fields.append((review, missing_fields))
            for name in missing_fields:
                missing_field_names[name] = missing_field_names.get(name, 0) + 1
        if raw in (None, ""):
            pnl_signs["missing"] += 1
        else:
            try:
                value = int(str(raw), 0) if isinstance(raw, str) else int(raw)
            except (TypeError, ValueError):
                value = None
            if value is None:
                pnl_signs["missing"] += 1
            elif value > 0:
                pnl_signs["positive"] += 1
            elif value == 0:
                pnl_signs["zero"] += 1
            else:
                pnl_signs["negative"] += 1

    completed = int(status.get("completedCount") or 0)
    report = {
        "schema": "remaining-closure-readings-v1",
        "generatedAt": now.isoformat(),
        "mode": "read-only",
        "store": {
            "path": str(database_path).replace("\\", "/"),
            "openedReadOnly": True,
            "queryOnly": True,
            "writesByThisProbe": 0,
        },
        "reviewGeneration": {
            "reviewVersion": status.get("reviewVersion"),
            "closedLoopVersion": status.get("closedLoopVersion"),
            "candidateEdgeVersion": status.get("candidateEdgeVersion"),
            "observationVersion": status.get("observationVersion"),
            "loopCount": status.get("loopCount"),
            "completedCount": completed,
            "dataIncompleteCount": status.get("dataIncompleteCount"),
            "notExecutableCount": status.get("notExecutableCount"),
            "failedCount": status.get("failedCount"),
            "broadcastRequestCount": status.get("broadcastRequestCount"),
            "updatedAt": status.get("updatedAt"),
            "reviewStatusCounts": status_counts,
        },
        "poolScope": {
            "driftRejections": len(drift),
            "singleTickGateFailures": len(single_tick_gate),
            "singleTickGateFailuresOtherThanDrift": len(other_gate),
            "otherGateReasons": sorted({reason_of(review) for review in other_gate}),
            "driftExamples": [
                {"loopKey": review.get("loopKey"), "reason": reason_of(review)}
                for review in drift[:5]
            ],
        },
        "reviewIntegrity": {
            "reviews": len(reviews),
            "incompleteFieldReviews": len(incomplete_fields),
            "signedPnlMissing": len(signed_pnl_missing),
            "bestAmountMissing": len(best_amount_missing),
            "missingFieldHistogram": missing_field_names,
            "pnlSigns": pnl_signs,
            "missingWithoutReason": [
                review.get("loopKey")
                for review in signed_pnl_missing
                if not reason_of(review)
            ],
        },
        "broadcast": {
            "managerBroadcastLibraryStateRows": broadcast_rows,
            "managerBroadcastRequestRows": broadcast_requests,
        },
        "runtimeState": {
            "rows": runtime_state_rows,
            "priced": runtime_state_priced,
        },
    }

    try:
        overview = get_json(f"{RUNTIME_API}/api/dex/overview?chainId={CHAIN_ID}")
        versions = overview.get("versions", {})
        upstream = (overview.get("pipelineStatus", {}) or {}).get("upstream", {})
        report["overview"] = {
            "reviewVersion": versions.get("reviewVersion"),
            "versionsConsistent": versions.get("versionsConsistent"),
            "consistencyIssues": versions.get("consistencyIssues"),
            "broadcastVersion": versions.get("broadcastVersion"),
            "broadcastEnabled": (overview.get("summary") or {}).get("broadcastEnabled"),
            "broadcastRequestCount": (overview.get("summary") or {}).get("broadcastRequestCount"),
            "candidateExchangePoolScopeChecksum": (
                upstream.get("candidateExchange") or {}
            ).get("poolScopeChecksum"),
            "closedLoopPoolScopeChecksum": (
                upstream.get("closedLoop") or {}
            ).get("poolScopeChecksum"),
            "runtimeCoreGeneration": (overview.get("runtimeCore") or {}).get("generation"),
            "runtimeCoreSnapshotAvailable": (
                overview.get("runtimeCore") or {}
            ).get("snapshotAvailable"),
            "runtimeCoreLastError": (overview.get("runtimeCore") or {}).get("lastError"),
        }
    except Exception as error:  # pragma: no cover - the runtime may be busy
        report["overview"] = {"error": f"{type(error).__name__}: {error}"}

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    arguments = parser.parse_args()
    report = measure(Path(arguments.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
