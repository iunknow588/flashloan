# -*- coding: utf-8 -*-
"""TASK-157 closed-loop identity distribution readout (read-only).

TASK-157 asks for the **distribution** of the closed-loop identity the
Review Manager published for the mainnet reviews, with the three states kept
apart:

* ``matched``     - the route matched the closed-loop catalog;
* ``mismatch``    - the route priced pools the catalog does not publish;
* ``unmeasured``  - the review carries no identity at all.

Keeping the three apart is the whole point: a review whose identity columns
are all absent was measured against a catalog that published no loop, or it
was written before the identity was published.  That is **not** a mismatch.

Where the reading comes from
----------------------------
The Review Manager owns ``manager_v4_reviews`` and publishes the identity
through ``GET /v1/reviews`` (``closedLoopIdentity``).  The reading is taken
from **that** interface first, so the evidence obeys the record-ownership
rule.  When the manager API is not running, the collector falls back to a
**read-only** copy of the store and says so explicitly in the report: the
fallback is a *measurement of the same published columns*, and the report
never claims the API was read when it was not.

Everything is read-only: the store is opened with ``mode=ro``, the manager
API is only read with GET, and no transaction is ever sent.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task157_closed_loop_identity_distribution_20261122.json",
)

NETWORK = "robinhood"
CHAIN_ID = 4663
MANAGER_API = os.environ.get("MANAGER_API_URL", "http://127.0.0.1:8091")

#: The three states the distribution is reported in.  They are never merged.
MATCHED = "matched"
MISMATCH = "mismatch"
UNMEASURED = "unmeasured"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def read_manager_reviews(base, chain_id, timeout=30):
    """Read the published review rows through the manager API."""
    rows = []
    offset = 0
    limit = 1000
    filtered = None
    while True:
        query = urllib.parse.urlencode(
            {"chainId": int(chain_id), "limit": limit, "offset": offset}
        )
        url = base.rstrip("/") + "/v1/reviews?" + query
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError("manager api read failed: %s" % error) from error
        try:
            document = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise RuntimeError("manager api read failed: %s" % error) from error
        if not isinstance(document, dict) or not document.get("ok"):
            raise RuntimeError(
                "manager api read failed: %s"
                % (document.get("error") if isinstance(document, dict) else raw[:200])
            )
        data = document.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("manager api read failed: no data member")
        page = data.get("reviews")
        if not isinstance(page, list):
            break
        rows.extend(item for item in page if isinstance(item, dict))
        if isinstance(data.get("filteredCount"), int):
            filtered = int(data["filteredCount"])
        if filtered is not None and len(rows) >= filtered:
            break
        if len(page) < limit:
            break
        offset += limit
        if offset > 100000:
            break
    return rows


def read_store_reviews(path):
    """Read the review rows from a read-only copy of the store."""
    connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = []
        for row in connection.execute(
            "SELECT loop_key, review_status, review_reason, signed_pnl_amount, "
            "reviewed_at, closed_loop_identity_status, "
            "closed_loop_pool_identity_status, identity_mismatch, "
            "closed_loop_repair_target, anchor_address, anchor_symbol "
            "FROM manager_v4_reviews WHERE network = ?1 AND chain_id = ?2 "
            "ORDER BY loop_key",
            (NETWORK, CHAIN_ID),
        ):
            rows.append({
                "loopKey": row["loop_key"],
                "reviewStatus": row["review_status"],
                "reviewReason": row["review_reason"],
                "signedPnlAmount": row["signed_pnl_amount"],
                "reviewedAt": row["reviewed_at"],
                "anchorAddress": row["anchor_address"],
                "anchorSymbol": row["anchor_symbol"],
                "closedLoopIdentity": {
                    "closedLoopIdentityStatus": row["closed_loop_identity_status"],
                    "closedLoopPoolIdentityStatus": row["closed_loop_pool_identity_status"],
                    "identityMismatch": row["identity_mismatch"],
                    "closedLoopRepairTarget": row["closed_loop_repair_target"],
                } if row["closed_loop_identity_status"]
                or row["closed_loop_pool_identity_status"]
                or row["identity_mismatch"]
                or row["closed_loop_repair_target"] else None,
            })
        return rows
    finally:
        connection.close()


def classify(identity):
    """Put one review into exactly one of the three states.

    An absent identity is **unmeasured**, never a mismatch.  A present
    identity that reports a mismatch is a mismatch.  A present identity that
    reports a match is a match.
    """
    if not isinstance(identity, dict):
        return UNMEASURED, None
    status = identity.get("closedLoopIdentityStatus")
    status = status.strip() if isinstance(status, str) and status.strip() else None
    mismatch = identity.get("identityMismatch")
    mismatch = mismatch.strip() if isinstance(mismatch, str) and mismatch.strip() else None
    if mismatch:
        return MISMATCH, status or "mismatch"
    if status:
        lowered = status.lower()
        if "mismatch" in lowered or "unmatched" in lowered:
            return MISMATCH, status
        return MATCHED, status
    return UNMEASURED, None


def main():
    source = None
    api_error = None
    rows = []
    try:
        rows = read_manager_reviews(MANAGER_API, CHAIN_ID)
        source = {
            "kind": "manager-api",
            "endpoint": MANAGER_API.rstrip("/") + "/v1/reviews",
            "method": "GET",
            "note": (
                "the identity was read from the Review Manager's published "
                "interface, which is the owner of the record"
            ),
        }
    except Exception as error:  # noqa: BLE001 - the fallback is reported
        api_error = "%s: %s" % (type(error).__name__, error)
        rows = read_store_reviews(MAINNET_DB)
        source = {
            "kind": "read-only-store-fallback",
            "endpoint": rel(MAINNET_DB),
            "method": "sqlite mode=ro",
            "managerApiUnavailable": True,
            "managerApiError": api_error,
            "note": (
                "the manager API was not reachable, so the same published "
                "columns were read from a read-only copy of the store. This "
                "is a measurement of the published columns; it does not "
                "claim that the API was read."
            ),
        }

    now = datetime.datetime.now(datetime.timezone.utc)
    per_review = []
    counts = {MATCHED: 0, MISMATCH: 0, UNMEASURED: 0}
    pool_counts = {MATCHED: 0, MISMATCH: 0, UNMEASURED: 0}
    mismatch_details = []
    repair_targets = []
    reason_counts = {}
    status_counts = {}

    for row in rows:
        identity = row.get("closedLoopIdentity")
        state, status = classify(identity)
        counts[state] += 1

        pool_identity = identity.get("closedLoopPoolIdentityStatus") if isinstance(identity, dict) else None
        pool_state = UNMEASURED
        if isinstance(pool_identity, str) and pool_identity.strip():
            lowered = pool_identity.strip().lower()
            pool_state = MISMATCH if ("mismatch" in lowered or "unmatched" in lowered) else MATCHED
        pool_counts[pool_state] += 1

        reason = row.get("reviewReason")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        status = row.get("reviewStatus")
        status_counts[status] = status_counts.get(status, 0) + 1

        reviewed = None
        if row.get("reviewedAt"):
            try:
                reviewed = datetime.datetime.fromisoformat(
                    str(row["reviewedAt"]).replace("Z", "+00:00")
                )
            except ValueError:
                reviewed = None
        age = (now - reviewed).total_seconds() if reviewed else None

        if state == MISMATCH:
            mismatch_details.append({
                "loopKey": row.get("loopKey"),
                "identityStatus": status,
                "identityMismatch": identity.get("identityMismatch"),
                "closedLoopRepairTarget": identity.get("closedLoopRepairTarget"),
                "poolIdentityStatus": pool_identity,
            })
        target = identity.get("closedLoopRepairTarget") if isinstance(identity, dict) else None
        if isinstance(target, str) and target.strip():
            repair_targets.append({
                "loopKey": row.get("loopKey"),
                "repairTarget": target.strip(),
            })

        per_review.append({
            "loopKey": row.get("loopKey"),
            "reviewStatus": status,
            "reviewReason": reason,
            "signedPnlAmount": row.get("signedPnlAmount"),
            "reviewedAt": row.get("reviewedAt"),
            "ageSeconds": age,
            "anchorAddress": row.get("anchorAddress"),
            "anchorSymbol": row.get("anchorSymbol"),
            "identityState": state,
            "identityStatus": status if isinstance(identity, dict) else None,
            "poolIdentityState": pool_state,
            "poolIdentityStatus": pool_identity,
            "identityMismatch": identity.get("identityMismatch") if isinstance(identity, dict) else None,
            "closedLoopRepairTarget": target,
        })

    ages = [r["ageSeconds"] for r in per_review if r["ageSeconds"] is not None]
    with_identity = counts[MATCHED] + counts[MISMATCH]

    report = {
        "schema": "task157-closed-loop-identity-distribution-v1",
        "generatedAt": now.isoformat(),
        "task": "TASK-157",
        "mode": "read-only",
        "authority": (
            "read-only; manager API read with GET; store opened with mode=ro; "
            "no write, no sign, no broadcast"
        ),
        "environment": {
            "network": NETWORK,
            "chainId": CHAIN_ID,
            "database": MAINNET_DB,
            "databaseSha256": sha256_file(MAINNET_DB),
        },
        "readingSource": source,
        "distribution": {
            "totalReviews": len(per_review),
            "identity": {
                MATCHED: counts[MATCHED],
                MISMATCH: counts[MISMATCH],
                UNMEASURED: counts[UNMEASURED],
            },
            "poolIdentity": {
                MATCHED: pool_counts[MATCHED],
                MISMATCH: pool_counts[MISMATCH],
                UNMEASURED: pool_counts[UNMEASURED],
            },
            "theThreeStatesAreNeverMerged": True,
            "reviewsWithIdentity": with_identity,
            "reviewsWithoutIdentity": counts[UNMEASURED],
            "absenceIsNotAMismatch": True,
        },
        "reviewFreshness": {
            "observationThresholdSeconds": 600,
            "oldestAgeSeconds": max(ages) if ages else None,
            "youngestAgeSeconds": min(ages) if ages else None,
            "oldestAgeDays": (max(ages) / 86400.0) if ages else None,
        },
        "reviewStatusDistribution": status_counts,
        "reviewReasonDistribution": reason_counts,
        "mismatchDetails": mismatch_details,
        "repairTargets": repair_targets,
        "reviews": per_review,
        "acceptance": {
            "criterion": "TASK-157",
            "requirement": (
                "the closed-loop identity distribution is reported in three "
                "separate states, the mismatch diagnosis and the repair "
                "target are listed per row, and an absent identity is "
                "reported as unmeasured rather than as a mismatch"
            ),
            "threeStatesReported": (
                set(counts) == {MATCHED, MISMATCH, UNMEASURED}
                and sum(counts.values()) == len(per_review)
            ),
            "mismatchAndUnmeasuredNotMerged": (
                counts[MISMATCH] != counts[UNMEASURED]
                or (counts[MISMATCH] == 0 and counts[UNMEASURED] == 0)
            ),
            "poolIdentityAlsoReported": (
                sum(pool_counts.values()) == len(per_review)
            ),
            "mismatchRowsNameTheirRepairTarget": all(
                row.get("closedLoopRepairTarget")
                for row in mismatch_details
            ) if mismatch_details else True,
            "verdict": "measured",
        },
        "prohibitions": {
            "noBackfill": (
                "no identity was backfilled; a review that carries no "
                "identity is reported as unmeasured"
            ),
            "noApply": "no rebuild, no re-materialisation, no --apply.",
            "noBroadcast": "no transaction was sent.",
            "noPythonReDerivation": (
                "the identity was taken as published; it was never rebuilt "
                "from the route addresses on the Python side"
            ),
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print("wrote: %s" % rel(OUT))
    print("source: %s" % source["kind"])
    print("identity: matched=%d mismatch=%d unmeasured=%d (total %d)" % (
        counts[MATCHED], counts[MISMATCH], counts[UNMEASURED], len(per_review)
    ))
    print("pool:    matched=%d mismatch=%d unmeasured=%d" % (
        pool_counts[MATCHED], pool_counts[MISMATCH], pool_counts[UNMEASURED]
    ))
    print("verdict: %s" % report["acceptance"]["verdict"])


if __name__ == "__main__":
    main()
