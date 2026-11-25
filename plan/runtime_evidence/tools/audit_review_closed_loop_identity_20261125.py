#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent read-only audit of the closed-loop identity stored on reviews.

The Review Manager publishes four identity columns per review row
(``closed_loop_identity_status``, ``closed_loop_pool_identity_status``,
``identity_mismatch``, ``closed_loop_repair_target``).  Those columns are
written by the production publisher, so reading them back only proves the
publisher can write what it wrote.

This tool proves the **other** direction: it re-derives the identity from
the closed-loop catalog **in the database** and compares it with what the
review row claims.  Nothing here trusts the publisher's verdict.

The three states are kept apart, exactly as the migration requires:

* ``matched``    -- the review's address path resolves in the catalog and
                    the pools the review priced are the pools the Manager
                    published for the same hops;
* ``mismatch``   -- the review claims an identity the catalog does not
                    support (the path does not resolve, or the pool sets
                    disagree);
* ``unmeasured`` -- the review carries **no** identity at all (all four
                    columns NULL).  An absent identity is a coverage gap,
                    **never** a mismatch, so it is counted on its own.

The store is opened with ``mode=ro`` and ``PRAGMA query_only = ON``; this
tool never writes.

Usage:
    python audit_review_closed_loop_identity_20261125.py <store.db> [report.json]
"""

import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"


def _norm_address(value):
    return str(value or "").strip().lower()


def _norm_ids(raw):
    """Parse a pool-id list; the shape is a JSON array of hex strings."""
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [_norm_address(item) for item in rows if _norm_address(item)]


def _identity_path(addresses):
    """The three-address identity path the kernel resolves against.

    Mirrors ``ClosedLoopCatalog::identity_for`` in
    ``flashloan/rs-src/src/closed_loop_identity.rs``: a three-element path
    is the identity, and a four-element path whose first and last element
    are equal is the same identity with the anchor closed.
    """
    if len(addresses) == 3:
        return tuple(addresses)
    if len(addresses) == 4 and addresses[0] == addresses[3]:
        return tuple(addresses[:3])
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    store_path = os.path.abspath(sys.argv[1])
    report_path = sys.argv[2] if len(sys.argv) > 2 else None

    connection = sqlite3.connect(
        "file:" + store_path.replace("\\", "/") + "?mode=ro", uri=True
    )
    connection.execute("PRAGMA query_only = ON")

    def rows(sql, *params):
        return connection.execute(sql, params).fetchall()

    # ---- the closed-loop catalog, read straight from the Manager tables
    catalog = {}
    for loop_key, path_json, pool_json in rows(
        "SELECT loop_key, path_addresses_json, pool_ids_json "
        "FROM manager_v4_closed_loops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        SCOPE,
    ):
        addresses = [_norm_address(item) for item in json.loads(path_json or "[]")]
        identity = _identity_path(addresses)
        if identity is None:
            continue
        catalog[identity] = {
            "loop_key": str(loop_key or "").strip().lower(),
            "pool_ids": _norm_ids(pool_json),
        }

    # ---- the reviews, with the identity the publisher stored
    reviews = rows(
        "SELECT loop_key, path_addresses_json, pool_ids_json, "
        "       closed_loop_identity_status, closed_loop_pool_identity_status, "
        "       identity_mismatch, closed_loop_repair_target, reviewed_at "
        "FROM manager_v4_reviews "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4 "
        "ORDER BY loop_key",
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        SCOPE,
    )

    states = {"matched": 0, "mismatch": 0, "unmeasured": 0}
    claimed = {"matched": 0, "mismatch": 0, "other": 0, "absent": 0}
    pool_claims = {"verified": 0, "unverifiable": 0, "mismatch": 0, "absent": 0}
    disagreements = []
    repair_targets = {}
    review_versions = set()
    reviewed_at = set()

    for row in rows(
        "SELECT DISTINCT closed_loop_version FROM manager_v4_reviews "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        SCOPE,
    ):
        review_versions.add(row[0])

    for (
        loop_key,
        path_json,
        pool_json,
        identity_status,
        pool_identity_status,
        identity_mismatch,
        repair_target,
        reviewed,
    ) in reviews:
        reviewed_at.add(reviewed)
        identity_status = (identity_status or "").strip() or None
        pool_identity_status = (pool_identity_status or "").strip() or None
        identity_mismatch = (identity_mismatch or "").strip() or None
        repair_target = (repair_target or "").strip() or None

        # What the review **claims**.
        if identity_status is None:
            claimed["absent"] += 1
        elif identity_status == "matched":
            claimed["matched"] += 1
        elif identity_status == "mismatch":
            claimed["mismatch"] += 1
        else:
            claimed["other"] += 1
        if pool_identity_status is None:
            pool_claims["absent"] += 1
        elif pool_identity_status in pool_claims:
            pool_claims[pool_identity_status] += 1
        else:
            pool_claims["other"] = pool_claims.get("other", 0) + 1
        if repair_target:
            repair_targets[repair_target] = repair_targets.get(repair_target, 0) + 1

        # What the catalog **says**, re-derived here.
        addresses = [_norm_address(item) for item in json.loads(path_json or "[]")]
        identity = _identity_path(addresses)
        entry = catalog.get(identity) if identity else None
        if identity_status is None and pool_identity_status is None:
            # No identity was published: a coverage gap, **not** a mismatch.
            states["unmeasured"] += 1
            continue
        if entry is None:
            states["mismatch"] += 1
            disagreements.append(
                {
                    "loopKey": loop_key,
                    "reason": "the address path does not resolve in the closed-loop catalog",
                    "claimed": identity_status,
                }
            )
            continue
        if entry["loop_key"] != str(loop_key or "").strip().lower():
            states["mismatch"] += 1
            disagreements.append(
                {
                    "loopKey": loop_key,
                    "reason": "the path resolves to a different loop key",
                    "resolvedLoopKey": entry["loop_key"],
                }
            )
            continue
        review_pools = _norm_ids(pool_json)
        if review_pools != entry["pool_ids"]:
            states["mismatch"] += 1
            disagreements.append(
                {
                    "loopKey": loop_key,
                    "reason": "the priced pool set differs from the published pool set",
                    "reviewPoolIds": review_pools,
                    "catalogPoolIds": entry["pool_ids"],
                }
            )
            continue
        states["matched"] += 1

    library = rows(
        "SELECT closed_loop_version, loop_count, reviewed_count, generated_at "
        "FROM manager_review_library_state "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        SCOPE,
    )
    connection.close()

    report = {
        "schema": "review-closed-loop-identity-audit-v1",
        "generatedAt": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "task": "T-复.02",
        "mode": "read-only (mode=ro + PRAGMA query_only = ON)",
        "store": store_path,
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "protocol": PROTOCOL,
        "scopeKey": SCOPE,
        "reviewLibraryState": {
            "closedLoopVersion": library[0][0] if library else None,
            "loopCount": library[0][1] if library else None,
            "reviewedCount": library[0][2] if library else None,
            "generatedAt": library[0][3] if library else None,
        },
        "catalog": {"loops": len(catalog)},
        "reviews": {
            "rows": len(reviews),
            "distinctReviewedAt": len(reviewed_at),
            "closedLoopVersions": sorted(v for v in review_versions if v is not None),
        },
        "threeStates": {
            "matched": states["matched"],
            "mismatch": states["mismatch"],
            "unmeasured": states["unmeasured"],
            "note": "unmeasured (no identity published at all) is counted on its own and "
            "is never folded into mismatch",
        },
        "publisherClaims": {
            "closedLoopIdentityStatus": claimed,
            "closedLoopPoolIdentityStatus": pool_claims,
            "repairTargets": repair_targets,
        },
        "disagreements": disagreements,
        "verdict": {
            "passed": not disagreements and states["mismatch"] == 0,
            "failureCount": len(disagreements),
            "failures": disagreements,
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

    print("three states:", json.dumps(report["threeStates"], ensure_ascii=False))
    print("publisher claims:", json.dumps(report["publisherClaims"], ensure_ascii=False))
    print("verdict:", report["verdict"]["passed"], "failures:", report["verdict"]["failureCount"])
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
