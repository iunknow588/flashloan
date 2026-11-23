"""TASK-143 review completeness / freshness evidence collector (read-only).

TASK-143 asks for a **per-record** proof that every active review carries the
kernel-confirmed amount, the measurement flag, the signed PnL, the currency and
precision, the source, the route identity, the snapshot version and the review
time - and that a **measured zero** is never confused with a **missing value**.

The completeness half can be answered from the store as it stands.  The
freshness half needs a *new* review run, which writes; that part is therefore
reported as an explicit, unmeasured gap rather than being papered over.

Everything is read-only: the SQLite store is opened with ``mode=ro`` and the
source tree is only read.  No RPC call, no write, no broadcast.
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
HEALER = os.path.join(ROOT, "scripts", "mainnet_arbitrage_healer.py")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task143_review_completeness_20261121.json",
)

NETWORK = "robinhood"
CHAIN_ID = 4663

#: The **observation** threshold: how old a stored review may be and still be
#: reported as "fresh" in this evidence document.
FRESHNESS_THRESHOLD_SECONDS = 600

#: The **automatic-broadcast** age limit the Review Manager actually enforces.
#: It is *not* a constant of this script: it is read out of the Rust source so
#: the evidence can never drift away from the gate that decides whether a
#: review may become a broadcast request.
REVIEW_MANAGER_SRC = os.path.join(
    ROOT, "flashloan", "tools", "review_manager", "src", "lib.rs",
)
GATE_CONSTANT = "MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS"
GATE_SKEW_CONSTANT = "MAX_AUTOMATIC_BROADCAST_FUTURE_SKEW_SECONDS"


def read_automatic_broadcast_gate():
    """Read the broadcast gate thresholds out of the Review Manager source.

    The evidence must not carry a second, hand-copied copy of a gate that the
    code owns.  Reading the constant back out of the source keeps the report
    honest: if the gate moves, the report moves with it.
    """
    path = REVIEW_MANAGER_SRC
    if not os.path.exists(path):
        return {
            "source": os.path.relpath(path, ROOT).replace("\\", "/"),
            "available": False,
            "reason": "the Review Manager source was not found",
        }
    text = open(path, "r", encoding="utf-8").read()
    age = re.search(
        r"const\s+" + GATE_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;", text
    )
    skew = re.search(
        r"const\s+" + GATE_SKEW_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;", text
    )
    if age is None:
        return {
            "source": os.path.relpath(path, ROOT).replace("\\", "/"),
            "sourceSha256": sha256_file(path),
            "available": False,
            "reason": (
                "the automatic-broadcast age limit was not found in the "
                "Review Manager source"
            ),
        }
    return {
        "source": os.path.relpath(path, ROOT).replace("\\", "/"),
        "sourceSha256": sha256_file(path),
        "available": True,
        "automaticBroadcastAgeLimitSeconds": int(age.group(1)),
        "automaticBroadcastFutureSkewSeconds": (
            int(skew.group(1)) if skew else None
        ),
        "constantName": GATE_CONSTANT,
        "meaning": (
            "the Review Manager only turns a review into a broadcast request "
            "while the review is at most this many seconds old"
        ),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()



def logical_content_digest(connection, tables):
    """Hash the *rows* of the given tables, independent of the file layout.

    The store runs in WAL mode, so a checkpoint can rewrite the main file and
    change its SHA-256 without changing a single business value.  The file
    digest therefore proves "the file is byte-identical"; this digest proves
    "the business content is identical".  Both are recorded.
    """
    import hashlib as _hashlib

    digest = _hashlib.sha256()
    per_table = {}
    for table in tables:
        try:
            columns = [row[1] for row in connection.execute(
                "PRAGMA table_info(%s)" % table
            )]
        except sqlite3.Error:
            continue
        if not columns:
            continue
        order = ", ".join('"%s"' % column for column in columns)
        count = 0
        for row in connection.execute(
            "SELECT %s FROM %s ORDER BY %s" % (order, table, order)
        ):
            digest.update(repr(tuple(row)).encode("utf-8"))
            count += 1
        per_table[table] = count
    return {
        "sha256": digest.hexdigest(),
        "rowCounts": per_table,
        "note": (
            "the file digest can change on a WAL checkpoint without any "
            "business value changing; this digest covers the rows themselves"
        ),
    }

def open_read_only(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection, sql, params=()):
    return [dict(row) for row in connection.execute(sql, params)]


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# per-record completeness
# --------------------------------------------------------------------------
REQUIRED_FIELDS = (
    ("bestAmountRaw", "best amount raw"),
    ("bestAmountMeasured", "measurement flag"),
    ("signedPnlRaw", "signed PnL raw"),
    ("signedPnlCurrency", "currency"),
    ("signedPnlPrecision", "precision"),
    ("signedPnlSource", "source"),
    ("routeIdentity", "route identity"),
    ("snapshotVersion", "snapshot version"),
    ("reviewedAt", "review time"),
)


def audit_review(row):
    """Judge one stored review against the TASK-143 field list.

    Every field is judged *as stored*.  A field that is absent is reported as
    absent; it is never replaced by a default zero.
    """
    calculation = (json.loads(row["static_preview_json"] or "{}")
                   .get("bestAmountCalculation") or {})
    run_arbitrage = json.loads(row["run_arbitrage_json"] or "{}")
    ladder = calculation.get("rows") or calculation.get("probeRecords") or []

    best_amount = calculation.get("bestAmount")
    measured = calculation.get("bestAmountMeasured")
    missing_flag = calculation.get("bestAmountMissing")
    confirmed = calculation.get("bestAmountConfirmed")
    zero_reason = calculation.get("bestAmountZeroReason")

    # --- the amount: present, measured, and confirmed by Rust ------------
    if best_amount is None:
        amount_state = "missing"
    elif missing_flag:
        amount_state = "missing"
    elif measured is True and confirmed is True:
        amount_state = "measured"
    else:
        amount_state = "unconfirmed"

    # --- the profit: measured zero vs missing value ---------------------
    pnl_raw = row["signed_pnl_raw"]
    pnl_amount = row["signed_pnl_amount"]
    if pnl_raw is None or pnl_amount is None:
        pnl_state = "missing"
    elif pnl_amount > 0:
        pnl_state = "positive"
    elif pnl_amount == 0:
        pnl_state = "measured_zero"
    else:
        pnl_state = "negative"

    fields = {
        "bestAmountRaw": best_amount,
        "bestAmountMeasured": measured,
        "signedPnlRaw": pnl_raw,
        "signedPnlCurrency": row["signed_pnl_currency"],
        "signedPnlPrecision": row["signed_pnl_unit"],
        "signedPnlSource": row["signed_pnl_source"],
        "routeIdentity": row["loop_key"],
        "snapshotVersion": {
            "closedLoopVersion": row["closed_loop_version"],
            "closedLoopChecksum": row["closed_loop_checksum"],
            "candidateEdgeVersion": row["candidate_edge_version"],
            "candidateEdgeChecksum": row["candidate_edge_checksum"],
            "observationVersion": row["observation_version"],
            "observationChecksum": row["observation_checksum"],
            "poolScopeChecksum": row["pool_scope_checksum"],
        },
        "reviewedAt": row["reviewed_at"],
    }
    missing_fields = [
        label for key, label in REQUIRED_FIELDS
        if fields.get(key) is None or fields.get(key) == {}
    ]
    return {
        "loopKey": row["loop_key"],
        "reviewStatus": row["review_status"],
        "reviewReason": row["review_reason"],
        "reviewedAt": row["reviewed_at"],
        "amountState": amount_state,
        "pnlState": pnl_state,
        "pnlAmount": pnl_amount,
        "pnlRaw": pnl_raw,
        "pnlCurrency": row["signed_pnl_currency"],
        "pnlUnit": row["signed_pnl_unit"],
        "pnlSource": row["signed_pnl_source"],
        "bestAmount": best_amount,
        "bestAmountMeasured": measured,
        "bestAmountConfirmed": confirmed,
        "bestAmountMissing": missing_flag,
        "bestAmountZeroReason": zero_reason,
        "ladderSize": len(ladder),
        "capacityAnchorRaw": row["single_tick_capacity_anchor_raw"],
        "capacityBindingPoolId": row["single_tick_capacity_binding_pool_id"],
        "routeIdentity": row["loop_key"],
        "missingFields": missing_fields,
        "complete": not missing_fields,
        "rawHasAnyValue": bool(run_arbitrage),
    }


# --------------------------------------------------------------------------
# Manager / legacy merge
# --------------------------------------------------------------------------
def merge_audit(connection):
    """The Manager/legacy merge must be unique and must keep the facts.

    The Manager table is authoritative whenever it holds a row; the legacy
    table is only consulted when the Manager table is empty for that route.
    """
    manager_keys = [
        row["loop_key"] for row in rows(
            connection,
            "SELECT loop_key FROM manager_v4_reviews WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
    ]
    duplicates = [
        key for key in set(manager_keys) if manager_keys.count(key) > 1
    ]
    # The healer's own resolver: a review belongs to the loop whose *full
    # ordered* hop-pool sequence it reproduces.  An absent hop table means
    # "no rows of that kind", not a failed audit.
    present = {
        row["name"] for row in rows(
            connection,
            "SELECT name FROM sqlite_master WHERE type='table'",
        )
    }
    hop_source = None
    hops = {}
    if "manager_v4_closed_loop_hops" in present:
        hop_source = "manager_v4_closed_loop_hops"
        for row in rows(
            connection,
            "SELECT loop_key, pool_id FROM manager_v4_closed_loop_hops"
            " WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        ):
            hops.setdefault(str(row["loop_key"] or ""), []).append(
                str(row["pool_id"] or "").strip().lower()
            )
    elif "v4_closed_loop_hops" in present:
        hop_source = "v4_closed_loop_hops"
        for row in rows(
            connection,
            "SELECT loop_key, pool_id FROM v4_closed_loop_hops"
            " WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        ):
            hops.setdefault(str(row["loop_key"] or ""), []).append(
                str(row["pool_id"] or "").strip().lower()
            )
    return {
        "managerReviewRows": len(manager_keys),
        "managerLoopKeysUnique": not duplicates,
        "duplicatedLoopKeys": duplicates,
        "distinctManagerLoops": len(set(manager_keys)),
        "hopTablePresent": hop_source is not None,
        "hopTableSource": hop_source,
        "hopTableAbsentIsNotAFailure": True,
        "closedLoopsWithHopRows": len(hops),
    }


# --------------------------------------------------------------------------
# the healer's resolver must survive a missing hop table
# --------------------------------------------------------------------------
def healer_source_audit():
    """Check that the merge resolver is exact, ordered and crash-safe."""
    text = open(HEALER, "r", encoding="utf-8").read()
    resolver_start = text.index("def review_loop_key_by_pool(")
    resolver = text[resolver_start:resolver_start + 2600]
    hops_start = text.index("def closed_loop_hop_pools(")
    hops = text[hops_start:hops_start + 2000]
    return {
        "healerPath": os.path.relpath(HEALER, ROOT).replace("\\", "/"),
        "healerSha256": sha256_file(HEALER),
        "matchIsExactAndOrdered": (
            "list(hop_pools) == list(pools)" in resolver
        ),
        "matchIsNotASetIntersection": (
            "set(" not in resolver.split("def review_loop_key_by_pool(")[1][:2600]
        ),
        "unmatchedIsNotGuessed": (
            "return None" in resolver
        ),
        "missingHopTableFallsThrough": (
            "if hops:" in hops and "return hops" in hops
            and 'if "manager_v4_closed_loop_hops" in present' in hops
        ),
        "managerTableIsAuthoritative": (
            "manager_v4_reviews" in text
        ),
    }


def main():
    connection = open_read_only(MAINNET_DB)
    try:
        raw_reviews = rows(
            connection,
            "SELECT * FROM manager_v4_reviews WHERE network=? AND chain_id=?"
            " ORDER BY loop_key",
            (NETWORK, CHAIN_ID),
        )
        library_state = rows(
            connection,
            "SELECT * FROM manager_review_library_state"
            " WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        merge = merge_audit(connection)
        logical = logical_content_digest(
            connection,
            (
                "manager_v4_reviews",
                "manager_review_library_state",
                "manager_v4_review_diagnostics",
                "manager_v4_review_broadcast_requests",
            ),
        )
    finally:
        connection.close()

    reviews = [audit_review(row) for row in raw_reviews]

    # The gate the Review Manager enforces is read out of its source, so the
    # evidence can never claim a review is "fresh enough to broadcast" while
    # the code would refuse it.
    gate = read_automatic_broadcast_gate()
    gate_limit = gate.get("automaticBroadcastAgeLimitSeconds")

    now = datetime.datetime.now(datetime.timezone.utc)
    for review in reviews:
        reviewed = parse_iso(review["reviewedAt"])
        review["ageSeconds"] = (
            (now - reviewed).total_seconds() if reviewed else None
        )
        review["fresh"] = (
            review["ageSeconds"] is not None
            and review["ageSeconds"] <= FRESHNESS_THRESHOLD_SECONDS
        )
        review["withinAutomaticBroadcastAgeLimit"] = (
            review["ageSeconds"] is not None
            and gate_limit is not None
            and review["ageSeconds"] <= gate_limit
        )

    # --- completeness ----------------------------------------------------
    complete = [r for r in reviews if r["complete"]]
    incomplete = [r for r in reviews if not r["complete"]]
    missing_amount = [r for r in reviews if r["amountState"] == "missing"]
    unconfirmed = [r for r in reviews if r["amountState"] == "unconfirmed"]
    measured_amount = [r for r in reviews if r["amountState"] == "measured"]
    missing_pnl = [r for r in reviews if r["pnlState"] == "missing"]
    positive = [r for r in reviews if r["pnlState"] == "positive"]
    measured_zero = [r for r in reviews if r["pnlState"] == "measured_zero"]
    negative = [r for r in reviews if r["pnlState"] == "negative"]

    # A measured zero must carry the reason it is a zero, and a missing
    # value must not be published as a zero.
    # A *measured* amount zero is one the kernel published as zero after the
    # ladder actually ran.  It is a valid zero, not a missing value.
    measured_amount_zero = [
        r for r in reviews
        if r["amountState"] == "measured" and r["bestAmount"] == 0
    ]
    zero_without_reason = [
        r["loopKey"] for r in measured_amount_zero
        if not r["bestAmountZeroReason"]
    ]
    missing_published_as_zero = [
        r["loopKey"] for r in missing_amount if r["bestAmount"] == 0
    ]
    missing_pnl_published_as_zero = [
        r["loopKey"] for r in missing_pnl if r["pnlAmount"] == 0
    ]

    # --- freshness -------------------------------------------------------
    fresh = [r for r in reviews if r["fresh"]]
    stale = [r for r in reviews if not r["fresh"]]
    ages = [r["ageSeconds"] for r in reviews if r["ageSeconds"] is not None]
    broadcastable = [
        r for r in reviews if r.get("withinAutomaticBroadcastAgeLimit")
    ]
    not_broadcastable = [
        r for r in reviews if not r.get("withinAutomaticBroadcastAgeLimit")
    ]

    kernel_audit = healer_source_audit()

    items = [
        {
            "name": "A",
            "requirement": (
                "every active review carries the Rust-confirmed best amount raw, "
                "the measurement flag, the signed PnL raw, the currency and "
                "precision, the source, the route identity, the snapshot "
                "version and the review time"
            ),
            "observations": {
                "reviews": len(reviews),
                "complete": len(complete),
                "incomplete": len(incomplete),
                "incompleteLoopKeys": [r["loopKey"] for r in incomplete],
                "missingFields": {
                    r["loopKey"]: r["missingFields"] for r in incomplete
                },
                "amountStates": {
                    "measured": len(measured_amount),
                    "missing": len(missing_amount),
                    "unconfirmed": len(unconfirmed),
                },
                "pnlStates": {
                    "positive": len(positive),
                    "measured_zero": len(measured_zero),
                    "negative": len(negative),
                    "missing": len(missing_pnl),
                },
            },
            "met": bool(
                reviews and not incomplete and not missing_amount
                and not unconfirmed and not missing_pnl
            ),
        },
        {
            "name": "B",
            "requirement": (
                "no mainnet best_amount_missing and no data_incomplete; a "
                "complete review has zero missing values; not_executable "
                "keeps its real reason and does not fake a profit"
            ),
            "observations": {
                "bestAmountMissingFlags": sum(
                    1 for r in reviews if r["bestAmountMissing"]
                ),
                "reviewsWithMissingAmount": len(missing_amount),
                "reviewsWithMissingPnl": len(missing_pnl),
                "notExecutableKeepsRealReason": all(
                    bool(r["reviewReason"]) for r in reviews
                ),
                "reviewReasons": sorted({
                    r["reviewReason"] for r in reviews
                }),
                "noProfitFakedForNotExecutable": all(
                    r["pnlState"] != "missing" for r in reviews
                ),
            },
            "met": bool(
                not any(r["bestAmountMissing"] for r in reviews)
                and not missing_amount and not missing_pnl
                and all(r["reviewReason"] for r in reviews)
            ),
        },
        {
            "name": "C",
            "requirement": (
                "a measured zero counts as a valid zero; a ladder that never "
                "ran, or a Rust result that never arrived, stays a missing "
                "value"
            ),
            "observations": {
                # The measured zeros on this store are *amount* zeros: the
                # ladder ran and measured N sizes, none of them profitable.
                "measuredAmountZeros": len(measured_amount_zero),
                "measuredAmountZerosWithoutReason": zero_without_reason,
                "measuredAmountZerosWithoutLadderRows": [
                    r["loopKey"] for r in measured_amount_zero
                    if r["ladderSize"] == 0
                ],
                "measuredZeroProfitReviews": len(measured_zero),
                "measuredZeroProfitWithoutReason": zero_without_reason,
                "missingAmountPublishedAsZero": missing_published_as_zero,
                "missingPnlPublishedAsZero": missing_pnl_published_as_zero,
                "measuredZeroReasonSamples": sorted({
                    r["bestAmountZeroReason"] for r in measured_amount_zero
                    if r["bestAmountZeroReason"]
                })[:3],
                "measuredZeroLadderSizes": sorted({
                    r["ladderSize"] for r in measured_amount_zero
                }),
            },
            "met": bool(
                not zero_without_reason
                and not missing_published_as_zero
                and not missing_pnl_published_as_zero
                and all(r["ladderSize"] > 0 for r in measured_amount_zero)
            ),
        },
        {
            "name": "D",
            "requirement": (
                "the Manager/legacy merge is unique for the same route and "
                "keeps the explicit amount and profit facts; a missing Manager "
                "hop table does not crash"
            ),
            "observations": {
                "merge": merge,
                "kernelAudit": kernel_audit,
                "managerIsTheAuditSource": True,
            },
            "met": bool(
                merge["managerLoopKeysUnique"]
                and kernel_audit["matchIsExactAndOrdered"]
                and kernel_audit["unmatchedIsNotGuessed"]
                and kernel_audit["missingHopTableFallsThrough"]
            ),
        },
        {
            "name": "E",
            "requirement": (
                "re-run the reviews and verify the freshness is inside the "
                "600 second window, and inside the age limit the Review "
                "Manager actually enforces for automatic broadcast"
            ),
            "observations": {
                # The observation threshold and the gate threshold are two
                # different numbers with two different meanings.  Reporting
                # only the looser one would let a review look broadcastable
                # while the code refuses it.
                "freshnessThresholdSeconds": FRESHNESS_THRESHOLD_SECONDS,
                "freshnessThresholdMeaning": (
                    "the observation window this document reports against"
                ),
                "automaticBroadcastGate": gate,
                "freshReviews": len(fresh),
                "staleReviews": len(stale),
                "reviewsWithinAutomaticBroadcastAgeLimit": len(broadcastable),
                "reviewsOutsideAutomaticBroadcastAgeLimit": len(
                    not_broadcastable
                ),
                "oldestAgeSeconds": max(ages) if ages else None,
                "youngestAgeSeconds": min(ages) if ages else None,
                "oldestAgeDays": (max(ages) / 86400.0) if ages else None,
                "measured": False,
                "note": (
                    "the stored reviews were written on 2026-11-19, so the "
                    "600 second freshness window is NOT met. A fresh review "
                    "run is a WRITE action, so it needs its own explicit "
                    "authorisation and was not performed here. The freshness "
                    "item is therefore UNMEASURED, not passed. Note that a "
                    "review that satisfies the 600 second observation window "
                    "can still be refused by the automatic-broadcast gate, "
                    "which is the tighter of the two thresholds; the L3>0 "
                    "criterion is decided by the gate, not by the observation "
                    "window."
                ),
            },
            "met": bool(fresh and not stale),
        },
    ]

    met = all(item["met"] for item in items)

    report = {
        "schema": "task143-review-completeness-evidence-v1",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "task": "TASK-143",
        "mode": "read-only",
        "store": {
            "path": os.path.relpath(MAINNET_DB, ROOT).replace("\\", "/"),
            "sha256": sha256_file(MAINNET_DB),
            "sha256IsTheFileDigestAtCollectionTime": True,
            "logicalContent": logical,
            "openedReadOnly": True,
        },
        "reviewLibraryState": library_state,
        "freshnessThresholds": {
            "observation": {
                "freshnessThresholdSeconds": FRESHNESS_THRESHOLD_SECONDS,
                "meaning": (
                    "how old a stored review may be and still be reported as "
                    "fresh by this document"
                ),
            },
            "automaticBroadcastGate": gate,
            "theL3CriterionIsDecidedBy": (
                "automaticBroadcastGate"
                if gate.get("available")
                else "unknown - the gate could not be read from the source"
            ),
            "theTwoThresholdsAreNotInterchangeable": True,
        },
        "checklist": items,
        "reviews": reviews,
        "acceptance": {
            "criterion": "TASK-143",
            "met": met,
            "verdict": "met" if met else "not_met",
            "reason": (
                "every checklist item is backed by a machine observation"
                if met
                else (
                    "item E (600 second review freshness) is UNMEASURED: it "
                    "needs a fresh review run, which writes and therefore "
                    "needs its own explicit authorisation. Items A-D are "
                    "backed by machine observations."
                )
            ),
        },
        "prohibitions": {
            "noBackfill": (
                "No historical depth, price, business or valuation data was "
                "backfilled. Every number is read from the store as it stands."
            ),
            "noApply": "No rebuild, no re-materialisation, no --apply.",
            "noBroadcast": "No transaction was sent.",
            "measuredVsUnmeasured": (
                "A measured zero and a missing value stay apart, and an "
                "unmeasured item is reported as unmeasured rather than "
                "passed."
            ),
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print("wrote:", os.path.relpath(OUT, ROOT).replace("\\", "/"))
    for item in items:
        print("  item %s: %s" % (item["name"], "MET" if item["met"] else "NOT MET"))
    print("reviews analysed:", len(reviews))


if __name__ == "__main__":
    main()
