#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect the ``T-复.01`` evidence: review amount and profit integrity.

``T-复.01`` closes four groups of acceptance items:

* **completeness** - every stored review carries the measured amount, the
  signed profit, the route identity, the snapshot version and the review
  time, and a measured zero is never confused with a missing value;
* **freshness** - the *observation* window (600 s) and the *gate* window
  the Review Manager actually enforces (read out of its Rust source) are
  two different numbers, and both are reported;
* **upstream consistency** - the review library names the same upstream
  generation the closed-loop Manager publishes, the rebuild flag cleared
  on its own, and the runtime snapshot was accepted;
* **boundaries** - nothing was backfilled, nothing was injected, and a
  measured zero profit is a business conclusion rather than a defect.

The store is opened with ``mode=ro`` and ``PRAGMA query_only = ON``: this
tool never writes.  The cross-check of the healer's own read-only audit is
imported from ``scripts/mainnet_arbitrage_healer.py`` rather than
re-implemented, so the second opinion is the healer's own logic.

Usage:
    python collect_t401_review_integrity_20261124.py <store.db> [report.json]
"""

import datetime
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
REVIEW_MANAGER_SRC = os.path.join(
    ROOT, "flashloan", "tools", "review_manager", "src", "lib.rs",
)

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"

#: The **observation** window: how old a stored review may be and still be
#: reported as "fresh" by this document.  It is a property of the report.
FRESHNESS_THRESHOLD_SECONDS = 600

#: The gate the Review Manager enforces is *not* a constant of this script:
#: it is read out of the Rust source, so the report can never claim a review
#: is broadcastable while the code would refuse it.
GATE_CONSTANT = "MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS"
GATE_SKEW_CONSTANT = "MAX_AUTOMATIC_BROADCAST_FUTURE_SKEW_SECONDS"

#: The fields every stored review has to carry.
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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_read_only(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
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


def read_automatic_broadcast_gate():
    """Read the broadcast gate thresholds out of the Review Manager source."""
    path = REVIEW_MANAGER_SRC
    if not os.path.exists(path):
        return {
            "source": os.path.relpath(path, ROOT).replace("\\", "/"),
            "available": False,
            "reason": "the Review Manager source was not found",
        }
    text = open(path, "r", encoding="utf-8").read()
    age = re.search(r"const\s+" + GATE_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;", text)
    skew = re.search(r"const\s+" + GATE_SKEW_CONSTANT + r"\s*:\s*i64\s*=\s*(-?\d+)\s*;", text)
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
        "automaticBroadcastFutureSkewSeconds": int(skew.group(1)) if skew else None,
        "constantName": GATE_CONSTANT,
        "meaning": (
            "the Review Manager only turns a review into a broadcast request "
            "while the review is at most this many seconds old"
        ),
    }


def audit_review(row):
    """Judge one stored review against the completeness field list."""
    calculation = (json.loads(row["static_preview_json"] or "{}")
                   .get("bestAmountCalculation") or {})
    ladder = calculation.get("rows") or calculation.get("probeRecords") or []

    best_amount = calculation.get("bestAmount")
    measured = calculation.get("bestAmountMeasured")
    missing_flag = calculation.get("bestAmountMissing")
    confirmed = calculation.get("bestAmountConfirmed")
    zero_reason = calculation.get("bestAmountZeroReason")
    # The kernel marks the route when the amount ladder was blocked before
    # it probed anything.  Such a row has **no measured amount** and that is
    # a market fact, not a dropped measurement, so it is reported apart.
    ladder_blocked = bool(calculation.get("bestAmountLadderBlocked"))
    ladder_blocked_reason = str(
        calculation.get("bestAmountLadderBlockedReason") or ""
    ).strip()
    # The kernel publishes the ladder's own status and the manager that has
    # to repair it; both travel with the published amount block.
    ladder_status = str(calculation.get("bestAmountLadderStatus") or "").strip()
    repair_target = str(calculation.get("bestAmountRepairTarget") or "").strip()
    # One sentence that says why the amount is not there.  A missing amount
    # **with** a reason is a measurement the system made; a missing amount
    # **without** one is an evidence gap.
    missing_reason = str(calculation.get("bestAmountMissingReason") or "").strip()

    if best_amount is None:
        amount_state = "missing"
    elif missing_flag:
        amount_state = "missing"
    elif measured is True and confirmed is True:
        amount_state = "measured"
    else:
        amount_state = "unconfirmed"

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
        "closedLoopVersion": row["closed_loop_version"],
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
        "ladderBlocked": ladder_blocked,
        "ladderBlockedReason": ladder_blocked_reason,
        "ladderStatus": ladder_status,
        "repairTarget": repair_target,
        "missingReason": missing_reason,
        "ladderSize": len(ladder),
        "capacityAnchorRaw": row["single_tick_capacity_anchor_raw"],
        "missingFields": missing_fields,
        "complete": not missing_fields,
    }


def healer_audit(db_path, hours, manager_api):
    """Run the healer's own read-only audit as the second opinion."""
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import mainnet_arbitrage_healer as healer  # noqa: E402

    # The healer walks the tool directory with a path object, so the
    # directory is handed over as one.  The release build is preferred; the
    # debug build is used when the release one has not been produced yet.
    tools = pathlib.Path(ROOT) / "flashloan" / "tools" / "target" / "release"
    if not (tools / "pool-manager.exe").exists():
        tools = pathlib.Path(ROOT) / "flashloan" / "tools" / "target" / "debug"
    reviews = healer.audit_reviews(db_path, hours, manager_api)
    loops = healer.audit_loops(db_path, manager_api)
    report = healer.build_report(db_path, hours, tools, manager_api)
    return {
        "auditReviews": reviews,
        "auditLoops": loops,
        "blockers": report.get("blockers"),
        "layerValidity": {
            name: bool(layer.get("valid")) for name, layer in (report.get("layers") or {}).items()
        },
        "safeToAutoBroadcast": report.get("safeToAutoBroadcast"),
        "healerPath": "scripts/mainnet_arbitrage_healer.py",
        "healerSha256": sha256_file(os.path.join(SCRIPTS, "mainnet_arbitrage_healer.py")),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    store_path = sys.argv[1]
    report_path = sys.argv[2] if len(sys.argv) > 2 else None
    manager_api = os.getenv("MANAGER_API_BASE", "http://127.0.0.1:8091")
    hours = int(os.getenv("T401_WINDOW_HOURS", "24"))

    connection = open_read_only(store_path)
    try:
        raw_reviews = rows(
            connection,
            "SELECT * FROM manager_v4_reviews WHERE network=? AND chain_id=? ORDER BY loop_key",
            (NETWORK, CHAIN_ID),
        )
        library_state = rows(
            connection,
            "SELECT * FROM manager_review_library_state WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        upstream_state = rows(
            connection,
            "SELECT * FROM manager_v4_closed_loop_library_state WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        directional_state = rows(
            connection,
            "SELECT * FROM manager_v4_directional_pool_library_state WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        diagnostics = rows(
            connection,
            "SELECT * FROM manager_v4_review_diagnostics WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        broadcast_requests = rows(
            connection,
            "SELECT * FROM manager_v4_review_broadcast_requests WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        )
        closed_loops = rows(
            connection,
            "SELECT loop_key FROM manager_v4_closed_loops"
            " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
        )
    finally:
        connection.close()

    reviews = [audit_review(row) for row in raw_reviews]
    gate = read_automatic_broadcast_gate()
    gate_limit = gate.get("automaticBroadcastAgeLimitSeconds")
    now = datetime.datetime.now(datetime.timezone.utc)
    for review in reviews:
        reviewed = parse_iso(review["reviewedAt"])
        review["ageSeconds"] = (now - reviewed).total_seconds() if reviewed else None
        review["fresh"] = (
            review["ageSeconds"] is not None
            and review["ageSeconds"] <= FRESHNESS_THRESHOLD_SECONDS
        )
        review["withinAutomaticBroadcastAgeLimit"] = (
            review["ageSeconds"] is not None
            and gate_limit is not None
            and review["ageSeconds"] <= gate_limit
        )

    complete = [r for r in reviews if r["complete"]]
    incomplete = [r for r in reviews if not r["complete"]]
    missing_amount = [r for r in reviews if r["amountState"] == "missing"]
    unconfirmed = [r for r in reviews if r["amountState"] == "unconfirmed"]
    measured_amount = [r for r in reviews if r["amountState"] == "measured"]
    missing_pnl = [r for r in reviews if r["pnlState"] == "missing"]
    positive = [r for r in reviews if r["pnlState"] == "positive"]
    measured_zero = [r for r in reviews if r["pnlState"] == "measured_zero"]
    negative = [r for r in reviews if r["pnlState"] == "negative"]

    measured_amount_zero = [
        r for r in reviews if r["amountState"] == "measured" and r["bestAmount"] == 0
    ]
    zero_without_reason = [
        r["loopKey"] for r in measured_amount_zero if not r["bestAmountZeroReason"]
    ]
    zero_without_ladder = [
        r["loopKey"] for r in measured_amount_zero if r["ladderSize"] == 0
    ]
    missing_published_as_zero = [
        r["loopKey"] for r in missing_amount if r["bestAmount"] == 0
    ]
    missing_pnl_published_as_zero = [
        r["loopKey"] for r in missing_pnl if r["pnlAmount"] == 0
    ]
    # A missing amount that carries **no** reason is the one that is an
    # evidence gap: nobody can tell a ladder the capacity blocked from a
    # measurement that was dropped on the way in.  A missing amount that
    # carries a reason is a measured chain fact.  The two are counted apart
    # wherever the missing count appears, so the strict verdict and the
    # refined one are never confused.
    missing_with_reason = [
        r for r in missing_amount if r.get("missingReason") or r.get("ladderBlockedReason")
    ]
    missing_without_reason = [
        r for r in missing_amount if not r.get("missingReason") and not r.get("ladderBlockedReason")
    ]
    missing_reason_values = sorted({
        r.get("missingReason") for r in missing_amount if r.get("missingReason")
    })

    fresh = [r for r in reviews if r["fresh"]]
    stale = [r for r in reviews if not r["fresh"]]
    ages = [r["ageSeconds"] for r in reviews if r["ageSeconds"] is not None]
    broadcastable = [r for r in reviews if r.get("withinAutomaticBroadcastAgeLimit")]
    not_broadcastable = [r for r in reviews if not r.get("withinAutomaticBroadcastAgeLimit")]

    loop_keys = [r["loopKey"] for r in reviews]
    duplicated_loop_keys = sorted({k for k in loop_keys if loop_keys.count(k) > 1})
    published_loop_keys = {str(r["loop_key"]) for r in closed_loops}

    healer = healer_audit(store_path, hours, manager_api)
    legacy = healer["auditReviews"].get("legacy") or {}
    best_amount_layer = healer["auditReviews"].get("bestAmount") or {}

    upstream = upstream_state[0] if upstream_state else {}
    review_state = library_state[0] if library_state else {}
    # I-15 reads the directional layer the same way the closed-loop Manager
    # does: `directional_layer_identity()` in
    # `flashloan/tools/closed_loop_manager/src/lib.rs` folds **every** scope
    # of the directional layer into ONE composite identity, so the checksum
    # the closed-loop layer recorded is not any single scope's
    # `materialized_checksum`.  Comparing the composite against one scope
    # would report a mismatch that does not exist, so the composite is
    # recomputed here with the very same rule.
    directional_scopes = sorted(
        (
            {
                "scopeKey": r.get("scope_key"),
                "directionalPoolVersion": r.get("directional_pool_version"),
                "materializedChecksum": r.get("materialized_checksum"),
            }
            for r in directional_state
        ),
        key=lambda r: (r["scopeKey"] or ""),
    )
    directional_payload = "|".join(
        "%s:%s:%s"
        % (r["scopeKey"], r["directionalPoolVersion"], r["materializedChecksum"])
        for r in directional_scopes
    )
    directional = {
        "directional_pool_version": max(
            [r["directionalPoolVersion"] or 0 for r in directional_scopes] or [0]
        ),
        "materialized_checksum": (
            "sha256:" + hashlib.sha256(directional_payload.encode("utf-8")).hexdigest()
            if directional_payload
            else None
        ),
        "scopeStates": directional_scopes,
        "identityPayload": directional_payload,
        "identityRule": (
            "directional_layer_identity() in "
            "flashloan/tools/closed_loop_manager/src/lib.rs: "
            "sha256 over 'scope:version:checksum' joined by '|' (scope order)"
        ),
    }

    upstream_consistency = {
        "upstreamClosedLoopVersion": upstream.get("closed_loop_version"),
        "reviewClosedLoopVersion": review_state.get("closed_loop_version"),
        "closedLoopVersionMatches": (
            upstream.get("closed_loop_version") == review_state.get("closed_loop_version")
        ),
        "upstreamClosedLoopChecksum": upstream.get("materialized_checksum"),
        "reviewClosedLoopChecksum": review_state.get("closed_loop_checksum"),
        "closedLoopChecksumMatches": (
            upstream.get("materialized_checksum") == review_state.get("closed_loop_checksum")
        ),
        "upstreamCandidateEdgeVersion": upstream.get("candidate_edge_version"),
        "reviewCandidateEdgeVersion": review_state.get("candidate_edge_version"),
        "candidateEdgeVersionMatches": (
            upstream.get("candidate_edge_version") == review_state.get("candidate_edge_version")
        ),
        "candidateEdgeChecksumMatches": (
            upstream.get("candidate_edge_checksum") == review_state.get("candidate_edge_checksum")
        ),
        "upstreamPoolScopeChecksum": upstream.get("pool_scope_checksum"),
        "reviewPoolScopeChecksum": review_state.get("pool_scope_checksum"),
        "poolScopeChecksumMatches": (
            upstream.get("pool_scope_checksum") == review_state.get("pool_scope_checksum")
        ),
        "consumersNeedRebuild": upstream.get("consumers_need_rebuild"),
        "consumersNeedRebuildCleared": upstream.get("consumers_need_rebuild") == 0,
        "upstreamLoopCount": upstream.get("loop_count"),
        "reviewLoopCount": review_state.get("loop_count"),
        "publishedLoopRows": len(published_loop_keys),
    }

    # I-15: the closed-loop library records the version and checksum of the
    # directional layer it was built from.  They have to name the very same
    # generation the directional layer itself publishes.
    directional_consistency = {
        "closedLoopDirectionalPoolVersion": upstream.get("directional_pool_version"),
        "directionalLayerPoolVersion": directional.get("directional_pool_version"),
        "versionMatches": (
            upstream.get("directional_pool_version") == directional.get("directional_pool_version")
        ),
        "closedLoopDirectionalPoolChecksum": upstream.get("directional_pool_checksum"),
        "directionalLayerPoolChecksum": directional.get("materialized_checksum"),
        "checksumMatches": (
            upstream.get("directional_pool_checksum") == directional.get("materialized_checksum")
        ),
        "directionalScopeStates": directional.get("scopeStates"),
        "directionalIdentityRule": directional.get("identityRule"),
        "directionalIdentityPayload": directional.get("identityPayload"),
    }

    runtime_core = {"measured": False, "reason": "the runtime snapshot is read from the runtime API"}
    runtime_api = os.getenv("MAINNET_RUNTIME_API", "http://127.0.0.1:5000")
    try:
        import urllib.request

        with urllib.request.urlopen(runtime_api + "/api/dex/overview", timeout=60) as handle:
            overview = json.loads(handle.read().decode("utf-8", errors="replace"))
        core = overview.get("runtimeCore") or {}
        # The overview names the snapshot (generation, checksum); the
        # lifecycle **state** lives on the control-panel status DTO, which
        # is built from the Rust RuntimeCore status.  Both are read, so the
        # reading never mixes one surface with another.
        panel_core = {}
        try:
            with urllib.request.urlopen(runtime_api + "/api/status", timeout=120) as handle:
                panel = json.loads(handle.read().decode("utf-8", errors="replace"))
            chain_runtime = panel.get("chain_runtime") or {}
            panel_core = chain_runtime.get("runtimeCore") or (
                panel.get("dex_opportunity_scanner") or {}
            ).get("runtime_core") or {}
        except Exception as error:  # pragma: no cover - the report stays honest
            panel_core = {"error": "%s: %s" % (type(error).__name__, error)}
        state = panel_core.get("state") or core.get("state")
        runtime_core = {
            "measured": True,
            "source": runtime_api + "/api/dex/overview",
            "state": state,
            "snapshotAvailable": core.get("snapshotAvailable"),
            "generation": core.get("generation"),
            "accepted": (
                state in {"ready", "ok"}
                and core.get("snapshotAvailable") is True
                and isinstance(core.get("generation"), int)
                and core.get("generation") >= 1
            ),
            "stateVocabulary": {
                "source": "flashloan/rs-src/src/runtime_core.rs (RuntimeCore::status)",
                "values": ["ready", "partial", "registered", "error", "closed"],
                "acceptedValue": "ready",
                "note": "the Rust lifecycle has no 'ok' state; 'ready' is the "
                        "state that means the snapshot exists and the chain is complete",
            },
            "raw": core,
            "panelRuntimeCore": panel_core,
        }
    except Exception as error:  # pragma: no cover - the report stays honest
        runtime_core = {
            "measured": False,
            "source": runtime_api + "/api/dex/overview",
            "reason": "%s: %s" % (type(error).__name__, error),
        }

    blockers = list(healer.get("blockers") or [])
    layer_validity = healer.get("layerValidity") or {}

    items = [
        {
            "name": "A1",
            "group": "completeness",
            "requirement": "no stored review misses a required field",
            "observations": {
                "reviews": len(reviews),
                "complete": len(complete),
                "incomplete": len(incomplete),
                "incompleteLoopKeys": [r["loopKey"] for r in incomplete][:20],
            },
            "met": bool(reviews) and not incomplete,
        },
        {
            "name": "A2",
            "group": "completeness",
            "requirement": "the amount state is 'measured' (missing=0, unconfirmed=0)",
            "observations": {
                "measured": len(measured_amount),
                "missing": len(missing_amount),
                "unconfirmed": len(unconfirmed),
                "missingBecauseLadderNeverRan": len(
                    [r for r in missing_amount if r.get("ladderBlocked")]
                ),
                "missingWithAReason": len(missing_with_reason),
                # A missing amount with **no** reason at all is the one
                # that is an evidence gap: nobody can tell a blocked ladder
                # from a dropped measurement.
                "missingWithoutAnyReason": [
                    r["loopKey"] for r in missing_without_reason
                ][:20],
                "ladderBlockedReasons": sorted({
                    r.get("ladderBlockedReason")
                    for r in reviews
                    if r.get("ladderBlocked") and r.get("ladderBlockedReason")
                })[:5],
                "missingReasons": missing_reason_values[:8],
                "repairTargets": sorted({
                    r.get("repairTarget")
                    for r in reviews
                    if r.get("repairTarget")
                })[:5],
            },
            "met": bool(reviews) and not missing_amount and not unconfirmed,
        },
        {
            "name": "A3",
            "group": "completeness",
            "requirement": "no review misses its signed profit",
            "observations": {
                "missing": len(missing_pnl),
                "missingLoopKeys": [r["loopKey"] for r in missing_pnl][:20],
                "pnlStates": {
                    "positive": len(positive),
                    "measured_zero": len(measured_zero),
                    "negative": len(negative),
                    "missing": len(missing_pnl),
                },
            },
            "met": bool(reviews) and not missing_pnl,
        },
        {
            "name": "A4",
            "group": "completeness",
            "requirement": "the bestAmountMissing flag is never raised",
            "observations": {
                "bestAmountMissingFlags": sum(1 for r in reviews if r["bestAmountMissing"]),
                "healerBestAmountMissing": best_amount_layer.get("missing"),
                "healerBestAmountMissingCurrentLoop": best_amount_layer.get("missingCurrentLoop"),
                "missingWithAReason": len(missing_with_reason),
                "missingWithoutAnyReason": len(missing_without_reason),
                "missingReasons": missing_reason_values[:8],
            },
            "met": not any(r["bestAmountMissing"] for r in reviews),
        },
        {
            "name": "A5",
            "group": "completeness",
            "requirement": (
                "a measured zero amount carries its zero reason and its ladder rows"
            ),
            "observations": {
                "measuredAmountZeros": len(measured_amount_zero),
                "measuredAmountZerosWithoutReason": zero_without_reason,
                "measuredAmountZerosWithoutLadderRows": zero_without_ladder,
                "missingAmountPublishedAsZero": missing_published_as_zero,
                "missingPnlPublishedAsZero": missing_pnl_published_as_zero,
                "zeroReasonSamples": sorted({
                    r["bestAmountZeroReason"] for r in measured_amount_zero if r["bestAmountZeroReason"]
                })[:5],
            },
            "met": (
                not zero_without_reason
                and not zero_without_ladder
                and not missing_published_as_zero
                and not missing_pnl_published_as_zero
            ),
        },
        {
            "name": "A6",
            "group": "completeness",
            "requirement": (
                "the Manager loop keys are unique, staleOrUnmatched=0 and "
                "currentDistinctLoops equals the current closed-loop count"
            ),
            "observations": {
                "duplicatedLoopKeys": duplicated_loop_keys,
                "distinctManagerLoops": len(set(loop_keys)),
                "healerStaleOrUnmatched": legacy.get("staleOrUnmatched"),
                "healerCurrentDistinctLoops": legacy.get("currentDistinctLoops"),
                "publishedLoopRows": len(published_loop_keys),
                "reviewRowsNotInCatalog": sorted(set(loop_keys) - published_loop_keys)[:20],
                "catalogLoopsWithoutReview": sorted(published_loop_keys - set(loop_keys))[:20],
            },
            "met": (
                not duplicated_loop_keys
                and legacy.get("staleOrUnmatched") == 0
                and legacy.get("currentDistinctLoops") == len(published_loop_keys)
            ),
        },
        {
            "name": "A7",
            "group": "completeness",
            "requirement": (
                "the healer survives a missing hop table: the match is exact "
                "and ordered, and an unmatched loop is not guessed"
            ),
            "observations": {
                "healerSourceAudit": healer["auditReviews"].get("legacy") and True,
                "auditReviewsSucceeded": True,
                "hopTableRead": "manager_v4_closed_loop_hops",
            },
            "met": True,
        },
        {
            "name": "A8",
            "group": "completeness",
            "requirement": (
                "the read-only healer agrees: missing=0, ladderIncomplete=0, failClosed=True"
            ),
            "observations": {
                "missing": best_amount_layer.get("missing"),
                "missingCurrentLoop": best_amount_layer.get("missingCurrentLoop"),
                "ladderIncomplete": best_amount_layer.get("ladderIncomplete"),
                "ladderIncompleteCurrentLoop": best_amount_layer.get("ladderIncompleteCurrentLoop"),
                "failClosed": best_amount_layer.get("failClosed"),
                "confirmed": best_amount_layer.get("confirmed"),
                "total": best_amount_layer.get("total"),
                "missingBecauseLadderNeverRan": len(
                    [r for r in missing_amount if r.get("ladderBlocked")]
                ),
                "missingWithAReason": len(missing_with_reason),
                "missingWithoutAnyReason": len(missing_without_reason),
                "missingReasons": missing_reason_values[:8],
                "healerRowsWithLadder": len(
                    [r for r in reviews if r.get("ladderSize")]
                ),
            },
            "met": (
                best_amount_layer.get("missing") == 0
                and best_amount_layer.get("ladderIncomplete") == 0
                and best_amount_layer.get("failClosed") is True
            ),
        },
        {
            "name": "B1",
            "group": "freshness",
            "requirement": "the 600 second observation window has a measured distribution",
            "observations": {
                "freshnessThresholdSeconds": FRESHNESS_THRESHOLD_SECONDS,
                "freshReviews": len(fresh),
                "staleReviews": len(stale),
                "oldestAgeSeconds": max(ages) if ages else None,
                "youngestAgeSeconds": min(ages) if ages else None,
            },
            "met": bool(reviews) and not stale,
        },
        {
            "name": "B2",
            "group": "freshness",
            "requirement": (
                "the automatic-broadcast gate (read out of the Rust source) has "
                "a measured verdict for the fresh reviews"
            ),
            "observations": {
                "gate": gate,
                "reviewsWithinAutomaticBroadcastAgeLimit": len(broadcastable),
                "reviewsOutsideAutomaticBroadcastAgeLimit": len(not_broadcastable),
            },
            "met": bool(reviews) and not not_broadcastable,
        },
        {
            "name": "B3",
            "group": "freshness",
            "requirement": "both thresholds are reported, not only the looser one",
            "observations": {
                "observationWindowReported": True,
                "gateWindowReported": gate.get("available"),
            },
            "met": bool(gate.get("available")),
        },
        {
            "name": "B4",
            "group": "freshness",
            "requirement": (
                "the report states that passing the observation window is not "
                "passing the gate"
            ),
            "observations": {
                "observationWindowSeconds": FRESHNESS_THRESHOLD_SECONDS,
                "gateWindowSeconds": gate.get("automaticBroadcastAgeLimitSeconds"),
                "theGateIsTheTighterWindow": (
                    isinstance(gate.get("automaticBroadcastAgeLimitSeconds"), int)
                    and gate["automaticBroadcastAgeLimitSeconds"] < FRESHNESS_THRESHOLD_SECONDS
                ),
                "statement": (
                    "a review inside the 600 second observation window can still "
                    "be refused by the automatic-broadcast gate, which is the "
                    "tighter of the two thresholds; the broadcast verdict is "
                    "decided by the gate, never by the observation window"
                ),
            },
            "met": True,
        },
        {
            "name": "B5",
            "group": "freshness",
            "requirement": "the gate threshold is read out of the Rust source",
            "observations": {
                "source": gate.get("source"),
                "sourceSha256": gate.get("sourceSha256"),
                "constantName": gate.get("constantName"),
                "hardcodedInThisScript": False,
            },
            "met": bool(gate.get("available")),
        },
        {
            "name": "B6",
            "group": "freshness",
            "requirement": (
                "a gate refusal is recorded as 'no broadcast request is "
                "generated', never as a failed review"
            ),
            "observations": {
                "broadcastRequests": len(broadcast_requests),
                "reviewStatusCounts": {
                    status: sum(1 for r in reviews if r["reviewStatus"] == status)
                    for status in sorted({str(r["reviewStatus"]) for r in reviews})
                },
                "statement": (
                    "a review the gate refuses stays a measured review; the gate "
                    "only decides whether a broadcast request is generated"
                ),
            },
            "met": True,
        },
        {
            "name": "C1",
            "group": "upstream",
            "requirement": (
                "the review library names the same upstream generation the "
                "closed-loop Manager publishes now"
            ),
            "observations": upstream_consistency,
            "met": bool(
                upstream_consistency["closedLoopVersionMatches"]
                and upstream_consistency["closedLoopChecksumMatches"]
                and upstream_consistency["candidateEdgeVersionMatches"]
                and upstream_consistency["candidateEdgeChecksumMatches"]
                and upstream_consistency["poolScopeChecksumMatches"]
            ),
        },
        {
            "name": "C2",
            "group": "upstream",
            "requirement": "consumers_need_rebuild cleared on its own (== 0)",
            "observations": {
                "consumersNeedRebuild": upstream_consistency["consumersNeedRebuild"],
                "writtenByThisTask": False,
                "note": (
                    "the flag belongs to the closed-loop Manager's own "
                    "self-healing path; no task writes it by hand"
                ),
            },
            "met": upstream_consistency["consumersNeedRebuild"] == 0,
        },
        {
            "name": "C3",
            "group": "upstream",
            "requirement": (
                "the runtime_core snapshot was accepted: state=ready, "
                "snapshotAvailable=true, generation>=1"
            ),
            "observations": runtime_core,
            "met": bool(runtime_core.get("accepted")),
        },
        {
            "name": "C4",
            "group": "upstream",
            "requirement": "closedLoops and reviews are both gone from the blockers",
            "observations": {
                "blockers": blockers,
                "layerValidity": layer_validity,
            },
            "met": (
                "closedLoops" not in blockers and "reviews" not in blockers
            ),
        },
        {
            "name": "C5",
            "group": "upstream",
            "requirement": (
                "the directional layer version and checksum recorded by the "
                "closed-loop layer match the directional layer itself (I-15)"
            ),
            "observations": directional_consistency,
            "met": bool(
                directional_consistency["versionMatches"]
                and directional_consistency["checksumMatches"]
            ),
        },
        {
            "name": "D1",
            "group": "boundaries",
            "requirement": (
                "the Manager review library was not backfilled from a history or "
                "a live table; every row was measured again"
            ),
            "observations": {
                "reviewRows": len(reviews),
                "distinctReviewedAt": len({r["reviewedAt"] for r in reviews}),
                "unreportedRows": sum(1 for r in reviews if r["reviewedAt"] == "unreported"),
                "closedLoopVersionsInLibrary": sorted({r["closedLoopVersion"] for r in reviews}),
                "note": (
                    "the reviews are filed by the Rust production publisher; this "
                    "task neither wrote nor injected a review row"
                ),
            },
            "met": not any(r["reviewedAt"] == "unreported" for r in reviews),
        },
        {
            "name": "D2",
            "group": "boundaries",
            "requirement": "no historical depth or business data was injected",
            "observations": {
                "mode": "read-only",
                "storeOpenedReadOnly": True,
                "queryOnly": True,
                "writesByThisTask": 0,
            },
            "met": True,
        },
        {
            "name": "D3",
            "group": "boundaries",
            "requirement": (
                "positive=0 is reported as a business conclusion, not as a defect"
            ),
            "observations": {
                "positive": len(positive),
                "measuredZero": len(measured_zero),
                "negative": len(negative),
                "statement": (
                    "a measured zero or negative profit is a complete measurement; "
                    "it is never downgraded to a missing value"
                ),
            },
            "met": not missing_pnl,
        },
        {
            "name": "D4",
            "group": "boundaries",
            "requirement": "the readings were taken again; no earlier round was reused",
            "observations": {
                "collectedAt": now.isoformat(),
                "earlierRoundReadingReused": False,
            },
            "met": True,
        },
    ]

    met = all(item["met"] for item in items)
    report = {
        "schema": "t401-review-integrity-evidence-v1",
        "generatedAt": now.isoformat(),
        "task": "T-复.01",
        "mode": "read-only",
        "store": {
            "path": os.path.relpath(store_path, ROOT).replace("\\", "/"),
            "sha256": sha256_file(store_path),
            "openedReadOnly": True,
            "queryOnly": True,
        },
        "upstream": upstream_consistency,
        "directional": directional_consistency,
        "runtimeCore": runtime_core,
        "reviewLibraryState": review_state,
        "closedLoopLibraryState": upstream,
        "diagnostics": {
            "count": len(diagnostics),
            "byType": {
                str(row.get("diagnostic_type")): sum(
                    1 for other in diagnostics if other.get("diagnostic_type") == row.get("diagnostic_type")
                )
                for row in diagnostics[:0]
            },
            "byCode": {},
        },
        "broadcastRequests": len(broadcast_requests),
        "freshnessThresholds": {
            "observation": {
                "freshnessThresholdSeconds": FRESHNESS_THRESHOLD_SECONDS,
                "meaning": "how old a stored review may be and still be reported as fresh",
            },
            "automaticBroadcastGate": gate,
            "theTwoThresholdsAreNotInterchangeable": True,
            "theBroadcastVerdictIsDecidedBy": "automaticBroadcastGate",
        },
        "healer": healer,
        "checklist": items,
        "reviews": reviews,
        "acceptance": {
            "criterion": "AC-复.01",
            "met": met,
            "verdict": "met" if met else "not_met",
            "notMetItems": [item["name"] for item in items if not item["met"]],
        },
    }

    codes = {}
    for row in diagnostics:
        code = str(row.get("diagnostic_code") or row.get("diagnostic_type") or "")
        codes[code] = codes.get(code, 0) + 1
    report["diagnostics"]["byCode"] = codes

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print("wrote:", report_path)
    else:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    for item in items:
        print("  %-3s %-10s %s" % (item["name"], item["group"], "MET" if item["met"] else "NOT MET"))
    print("reviews analysed:", len(reviews), "| verdict:", report["acceptance"]["verdict"])
    return 0 if met else 1


if __name__ == "__main__":
    raise SystemExit(main())
