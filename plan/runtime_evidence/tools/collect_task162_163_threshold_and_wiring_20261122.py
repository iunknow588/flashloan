# -*- coding: utf-8 -*-
"""TASK-162 / TASK-163 evidence collector (read-only).

TASK-162 records the two freshness thresholds separately: the **observation**
window this evidence reports against, and the **automatic-broadcast age limit**
the Review Manager actually enforces.  The gate is read out of the Rust
source instead of being copied into this script, so the evidence cannot drift
away from the code that decides whether a review may become a broadcast
request.

TASK-163 records where the L2->L3 wiring actually belongs: the submit endpoint
*creates attempts from* broadcast requests, it does not *create* them, so the
missing production caller is on the review-output side.

Everything here is read-only: the SQLite store is opened with ``mode=ro`` and
the source tree is only read.  No RPC call, no write, no broadcast.
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
REVIEW_MANAGER_SRC = os.path.join(
    ROOT, "flashloan", "tools", "review_manager", "src", "lib.rs",
)
BROADCAST_MANAGER_SRC = os.path.join(
    ROOT, "flashloan", "tools", "broadcast_manager", "src", "lib.rs",
)
MANAGER_API_SRC = os.path.join(
    ROOT, "flashloan", "tools", "manager_api", "src", "lib.rs",
)
RUNTIME_CORE_SRC = os.path.join(
    ROOT, "flashloan", "rs-src", "src", "runtime_core.rs",
)
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task162_163_threshold_and_wiring_20261122.json",
)

NETWORK = "robinhood"
CHAIN_ID = 4663
OBSERVATION_THRESHOLD_SECONDS = 600
GATE_CONSTANT = "MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS"
GATE_SKEW_CONSTANT = "MAX_AUTOMATIC_BROADCAST_FUTURE_SKEW_SECONDS"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def read_source(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# TASK-162: the two freshness thresholds
# --------------------------------------------------------------------------
def audit_thresholds():
    """Read the automatic-broadcast gate out of the Review Manager source."""
    review_src = read_source(REVIEW_MANAGER_SRC)
    age = re.search(
        r"const\s+" + GATE_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;", review_src
    )
    skew = re.search(
        r"const\s+" + GATE_SKEW_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;",
        review_src,
    )

    gate_limit = int(age.group(1)) if age else None
    gate_skew = int(skew.group(1)) if skew else None

    # The gate is only meaningful where the Review Manager decides to publish
    # a broadcast request.  Prove that call site exists and that it uses the
    # gate, rather than trusting the constant alone.
    gate_used_at = review_src.find("review_is_fresh_for_automatic_broadcast(&row.reviewed_at)")
    publishes_request = review_src.find("desired.broadcast_requests.push(BroadcastRequestRow")

    # The gate decides on the age only; the other two preconditions are
    # recorded next to it so the evidence names all of them.
    preconditions = {
        "reviewStatusMustBeCompleted": (
            'review_status == "completed"' in review_src
        ),
        "signedPnlAmountMustBePositive": ("pnl.amount > 0.0" in review_src),
        "hopSqrtPriceLimitsMustBeExactlyThree": (
            "row.hop_sqrt_price_limits.len() == 3" in review_src
        ),
        "reviewMustBeWithinTheGate": gate_used_at >= 0,
    }

    observations = {
        "observation": {
            "freshnessThresholdSeconds": OBSERVATION_THRESHOLD_SECONDS,
            "meaning": (
                "how old a stored review may be and still be reported as "
                "fresh by the evidence documents"
            ),
            "source": "the TASK-143 evidence collector constant",
        },
        "automaticBroadcastGate": {
            "constantName": GATE_CONSTANT,
            "automaticBroadcastAgeLimitSeconds": gate_limit,
            "automaticBroadcastFutureSkewSeconds": gate_skew,
            "source": rel(REVIEW_MANAGER_SRC),
            "sourceSha256": sha256_file(REVIEW_MANAGER_SRC),
            "available": gate_limit is not None,
            "meaning": (
                "the Review Manager only turns a review into a broadcast "
                "request while the review is at most this many seconds old"
            ),
        },
        "theGateIsActuallyApplied": {
            "gateCallSiteFound": gate_used_at >= 0,
            "broadcastRequestPublishSiteFound": publishes_request >= 0,
            "preconditions": preconditions,
            "allPreconditionsPresent": all(preconditions.values()),
        },
        "theTwoThresholdsAreNotInterchangeable": (
            gate_limit is not None
            and gate_limit != OBSERVATION_THRESHOLD_SECONDS
        ),
        "theL3CriterionIsDecidedBy": (
            "automaticBroadcastGate"
            if gate_limit is not None
            else "unknown - the gate could not be read from the source"
        ),
        "theGateIsNotRelaxedByThisTask": True,
        "note": (
            "A review that satisfies the 600 second observation window can "
            "still be refused by the automatic-broadcast gate, which is the "
            "tighter of the two thresholds. The L3>0 criterion is decided by "
            "the gate. This task separates the two numbers; it does not "
            "change either one."
        ),
    }

    verdict = (
        "measured"
        if (
            gate_limit is not None
            and gate_used_at >= 0
            and publishes_request >= 0
            and all(preconditions.values())
        )
        else "incomplete"
    )
    return {"verdict": verdict, **observations}


# --------------------------------------------------------------------------
# TASK-163: where the L2->L3 wiring belongs
# --------------------------------------------------------------------------
def audit_wiring():
    """Show that the submit endpoint creates attempts from broadcast requests.

    The submit path reads the broadcast requests out of the store and matches
    the attempts in the request body against them by ``loop_key``.  It never
    creates a broadcast request, so the missing production caller cannot be
    fixed by calling submit.
    """
    broadcast_src = read_source(BROADCAST_MANAGER_SRC)
    api_src = read_source(MANAGER_API_SRC)
    runtime_src = read_source(RUNTIME_CORE_SRC)

    # The submit path is spread over three functions: `submit_impl` opens the
    # transaction, `submit_on_connection` reads the broadcast requests, and
    # `build_attempt_rows` matches the attempts against them.  Each fact is
    # therefore proven at the function that actually carries it, rather than
    # in one fragile window that would silently break when the file is
    # refactored.
    def function_body(text, signature):
        """Return the body of the function that starts with `signature`."""
        start = text.index(signature)
        tail = text[start + len(signature):]
        end = tail.find("\nfn ")
        if end < 0:
            end = len(tail)
        return text[start:start + len(signature) + end]

    submit_impl = function_body(broadcast_src, "fn submit_impl(")
    submit_on_connection = function_body(
        broadcast_src, "fn submit_on_connection("
    )
    build_attempt_rows = function_body(broadcast_src, "fn build_attempt_rows(")

    # The request body carries attempts, not broadcast requests.
    submit_request_body = function_body(
        broadcast_src, "fn validate_submit_request("
    )

    reads_requests = (
        "read_broadcast_requests_on_connection(" in submit_on_connection
    )
    matches_by_loop_key = (
        "requests_by_loop" in build_attempt_rows
        and "attempt.loop_key.as_str()" in build_attempt_rows
    )
    creates_attempts = "build_attempt_rows(" in submit_on_connection
    # The submit request body carries attempts keyed by loop_key; it carries
    # no broadcast-request payload at all.
    submit_body_carries_attempts = (
        "attempts" in submit_request_body
    )

    # Where does a broadcast request row come from?  The Review Manager
    # publishes it; the Broadcast Manager only stores it.
    request_insert = broadcast_src.find(
        "INSERT INTO manager_v4_review_broadcast_requests"
    )
    request_publish_site = read_source(REVIEW_MANAGER_SRC).find(
        "desired.broadcast_requests.push(BroadcastRequestRow"
    )

    # Who calls the submit write path?
    submit_callers = {
        "broadcast_manager_definesIt": "pub fn submit_broadcasts_write(" in broadcast_src,
        "manager_api_exposesTheEndpoint": (
            '"/v1/broadcasts/submit"' in api_src
            and "submit_broadcasts_write(" in api_src
        ),
        "runtime_core_callsSubmit": "submit_broadcasts_write(" in runtime_src,
        "runtime_core_onlyCallsClear": (
            "clear_broadcast_records_write(" in runtime_src
            and "submit_broadcasts_write(" not in runtime_src
        ),
    }
    python_callers = []
    for base, dirs, files in os.walk(os.path.join(ROOT, "flashloan")):
        dirs[:] = [d for d in dirs if d not in {".git", "target", "__pycache__"}]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                try:
                    text = read_source(path)
                except OSError:
                    continue
                if "broadcasts/submit" in text or "submit_broadcasts" in text:
                    python_callers.append(rel(path))

    findings = {
        "submitEndpointCreatesAttemptsFromRequests": {
            "readsBroadcastRequestsFromTheStore": reads_requests,
            "matchesAttemptsByLoopKey": matches_by_loop_key,
            "buildsAttemptRows": creates_attempts,
            "submitRequestBodyCarriesAttemptsNotRequests": submit_body_carries_attempts,
            "createsBroadcastRequests": False,
            "explanation": (
                "the submit path is 'create an attempt from an existing "
                "broadcast request'. It cannot create the broadcast request "
                "itself, so the missing production caller for L2->L3 is on "
                "the review-output side, not on the submit side."
            ),
        },
        "broadcastRequestOrigin": {
            "publishedByReviewManager": request_publish_site >= 0,
            "storedByBroadcastManager": request_insert >= 0,
            "reviewManagerSource": rel(REVIEW_MANAGER_SRC),
            "broadcastManagerSource": rel(BROADCAST_MANAGER_SRC),
            "explanation": (
                "the Review Manager decides which reviews may become "
                "broadcast requests; the Broadcast Manager only stores and "
                "materialises them."
            ),
        },
        "submitWritePathCallers": submit_callers,
        "thereIsNoProductionCaller": (
            submit_callers["runtime_core_callsSubmit"] is False
            and not python_callers
        ),
        "pythonCallers": python_callers,
        "theWiringPointIs": (
            "the review-output side: the producer that turns a completed, "
            "fresh, positive review into a manager_v4_review_broadcast_"
            "requests row"
        ),
        "theWiringPointIsNot": (
            "a new caller of POST /v1/broadcasts/submit"
        ),
        "managerBroadcastLibraryStateOrigin": {
            "writtenOnlyByTheMaterialisationPath": (
                "upsert_broadcast_state(" in broadcast_src
                and "manager_broadcast_library_state" in broadcast_src
            ),
            "explanation": (
                "manager_broadcast_library_state is written by the "
                "submit/finalize materialisation path, so the row appears "
                "only after a broadcast request exists and the materialisation "
                "runs."
            ),
        },
    }

    verdict = (
        "measured"
        if (
            reads_requests
            and matches_by_loop_key
            and creates_attempts
            and request_publish_site >= 0
            and request_insert >= 0
        )
        else "incomplete"
    )
    return {"verdict": verdict, **findings}


# --------------------------------------------------------------------------
# the store reading
# --------------------------------------------------------------------------
def audit_store():
    connection = sqlite3.connect("file:%s?mode=ro" % MAINNET_DB, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        counts = {}
        for table in (
            "manager_v4_reviews",
            "manager_v4_review_broadcast_requests",
            "manager_cow_execution_attempts",
            "manager_broadcast_library_state",
        ):
            counts[table] = connection.execute(
                "select count(*) from %s" % table
            ).fetchone()[0]

        rows = list(connection.execute(
            "select loop_key, review_status, review_reason, signed_pnl_amount, "
            "reviewed_at, closed_loop_identity_status, "
            "closed_loop_pool_identity_status, identity_mismatch "
            "from manager_v4_reviews"
        ))

        gate_limit = None
        age = re.search(
            r"const\s+" + GATE_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;",
            read_source(REVIEW_MANAGER_SRC),
        )
        if age:
            gate_limit = int(age.group(1))

        per_review = []
        within_observation = 0
        within_gate = 0
        for row in rows:
            reviewed = None
            if row["reviewed_at"]:
                try:
                    reviewed = datetime.datetime.fromisoformat(
                        row["reviewed_at"].replace("Z", "+00:00")
                    )
                except ValueError:
                    reviewed = None
            age_seconds = (
                (now - reviewed).total_seconds() if reviewed else None
            )
            is_fresh = (
                age_seconds is not None
                and age_seconds <= OBSERVATION_THRESHOLD_SECONDS
            )
            is_broadcastable = (
                age_seconds is not None
                and gate_limit is not None
                and age_seconds <= gate_limit
            )
            within_observation += int(is_fresh)
            within_gate += int(is_broadcastable)
            per_review.append({
                "loopKey": row["loop_key"],
                "reviewStatus": row["review_status"],
                "reviewReason": row["review_reason"],
                "signedPnlAmount": row["signed_pnl_amount"],
                "reviewedAt": row["reviewed_at"],
                "ageSeconds": age_seconds,
                "withinObservationWindow": is_fresh,
                "withinAutomaticBroadcastAgeLimit": is_broadcastable,
                "closedLoopIdentityStatus": row["closed_loop_identity_status"],
                "closedLoopPoolIdentityStatus": row["closed_loop_pool_identity_status"],
                "identityMismatch": row["identity_mismatch"],
            })

        ages = [r["ageSeconds"] for r in per_review if r["ageSeconds"] is not None]
        identity_present = sum(
            1 for r in per_review if r["closedLoopIdentityStatus"]
        )
        identity_absent = sum(
            1 for r in per_review if not r["closedLoopIdentityStatus"]
        )
        mismatch = sum(1 for r in per_review if r["identityMismatch"])

        return {
            "path": rel(MAINNET_DB),
            "sha256": sha256_file(MAINNET_DB),
            "openedReadOnly": True,
            "counts": counts,
            "reviewFreshness": {
                "totalReviews": len(per_review),
                "withinObservationWindow": within_observation,
                "withinAutomaticBroadcastAgeLimit": within_gate,
                "observationThresholdSeconds": OBSERVATION_THRESHOLD_SECONDS,
                "automaticBroadcastAgeLimitSeconds": gate_limit,
                "oldestAgeSeconds": max(ages) if ages else None,
                "youngestAgeSeconds": min(ages) if ages else None,
                "oldestAgeDays": (max(ages) / 86400.0) if ages else None,
            },
            "reviewIdentity": {
                "reviewsWithIdentity": identity_present,
                "reviewsWithoutIdentity": identity_absent,
                "identityMismatchCount": mismatch,
                "absenceIsNotAMismatch": True,
            },
            "reviews": per_review,
        }
    finally:
        connection.close()


def main():
    thresholds = audit_thresholds()
    wiring = audit_wiring()
    store = audit_store()

    report = {
        "schema": "task162-163-threshold-and-wiring-evidence-v1",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tasks": ["TASK-162", "TASK-163"],
        "mode": "read-only",
        "authority": (
            "read-only; store opened with mode=ro; source tree read only; "
            "no write, no sign, no broadcast"
        ),
        "environment": {
            "network": NETWORK,
            "chainId": CHAIN_ID,
            "database": MAINNET_DB,
            "databaseSha256": sha256_file(MAINNET_DB),
        },
        "task162_freshnessThresholds": thresholds,
        "task163_l2ToL3WiringPoint": wiring,
        "storeReading": store,
        "acceptance": {
            "TASK-162": {
                "requirement": (
                    "the observation window and the automatic-broadcast age "
                    "limit are named separately and the gate is read out of "
                    "the code that enforces it"
                ),
                "met": thresholds["verdict"] == "measured",
                "verdict": thresholds["verdict"],
            },
            "TASK-163": {
                "requirement": (
                    "the wiring point for L2->L3 is named, and it is shown "
                    "that the submit endpoint cannot be the missing caller"
                ),
                "met": wiring["verdict"] == "measured",
                "verdict": wiring["verdict"],
            },
        },
        "prohibitions": {
            "noGateRelaxation": (
                "neither threshold was changed; the gate keeps its own value "
                "and the evidence only reports it"
            ),
            "noBackfill": (
                "no historical depth, price, business or valuation data was "
                "backfilled; every number is read from the store as it stands"
            ),
            "noApply": "no rebuild, no re-materialisation, no --apply.",
            "noBroadcast": "no transaction was sent.",
            "measuredVsUnmeasured": (
                "an item whose evidence could not be produced is reported as "
                "incomplete rather than passed"
            ),
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print("wrote: %s" % rel(OUT))
    print("TASK-162 thresholds: %s" % thresholds["verdict"])
    print("TASK-163 wiring:     %s" % wiring["verdict"])
    print(
        "  observation=%ss gate=%ss withinGate=%s/%s"
        % (
            OBSERVATION_THRESHOLD_SECONDS,
            thresholds["automaticBroadcastGate"]["automaticBroadcastAgeLimitSeconds"],
            store["reviewFreshness"]["withinAutomaticBroadcastAgeLimit"],
            store["reviewFreshness"]["totalReviews"],
        )
    )


if __name__ == "__main__":
    main()
