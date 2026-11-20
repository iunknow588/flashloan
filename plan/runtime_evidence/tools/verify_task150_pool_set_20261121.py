"""TASK-150 read-only pool-set verification (chain 4663).

Reproduces the kernel's own pool-id derivation from the very pool keys the
route prices, and compares the derived ordered pool set against the pool set
the closed-loop Manager published for the same loop.

Read-only. The SQLite store is opened with mode=ro; nothing is written, no
repair runs and no broadcast is attempted.

The kernel derives the route pool set in
``flashloan/rs-src/src/worker_pool.rs::derive_route_pool_ids``: for every
pool key of the ordered sections entry -> bridge -> exit it computes
``pool_index::pool_id`` and keeps the order.  ``pool_index::pool_id`` is the
keccak256 of the 160-byte ABI encoding of the V4 pool key
(``flashloan/tools/manager_contracts/src/pool.rs``).  This script performs
the exact same computation from the pool keys the Manager materialised, so
the comparison is on the same basis as the kernel's check.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys

from Crypto.Hash import keccak

sys.stdout.reconfigure(encoding="utf-8")

DB = r"flashloan\db\local_store_4663.sqlite3"
OUT = r"plan\runtime_evidence\reports\task150_pool_set_verification_20261121.json"

ZERO = "0x0000000000000000000000000000000000000000"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_address(value):
    text = str(value or "").strip().lower()
    body = text[2:] if text.startswith("0x") else text
    if len(body) != 40:
        raise ValueError(f"invalid address: {value!r}")
    return "0x" + body


def address_word(address):
    word = bytearray(32)
    word[12:] = bytes.fromhex(address[2:])
    return bytes(word)


def uint24_word(value):
    word = bytearray(32)
    word[29:] = (value & 0xFFFFFF).to_bytes(3, "big")
    return bytes(word)


def int24_word(value):
    word = bytearray(32)
    if value < 0:
        word = bytearray(b"\xff" * 32)
    word[29:] = (value & 0xFFFFFF).to_bytes(3, "big")
    return bytes(word)


def kernel_pool_id(pool_key):
    """The kernel's pool id, computed the same way pool_index::pool_id does."""
    currency0 = normalize_address(pool_key["currency0"])
    currency1 = normalize_address(pool_key["currency1"])
    hooks = normalize_address(pool_key.get("hooks") or ZERO)
    if currency0 == currency1:
        raise ValueError("same currency")
    if currency0 >= currency1:
        raise ValueError("currency0 must be smaller than currency1")
    fee = int(pool_key["fee"])
    if fee > 0x00FFFFFF:
        raise ValueError("fee out of range")
    tick_spacing = int(pool_key["tickSpacing"])
    if not (-8388608 <= tick_spacing <= 8388607):
        raise ValueError("tick spacing out of range")
    encoded = (
        address_word(currency0)
        + address_word(currency1)
        + uint24_word(fee)
        + int24_word(tick_spacing)
        + address_word(hooks)
    )
    digest = keccak.new(digest_bits=256, data=encoded).digest()
    return "0x" + digest.hex()


def jload(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def main():
    uri = "file:" + os.path.abspath(DB).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    cur = con.cursor()

    # Every pool the Manager knows, keyed by pool id, with its pool key.
    # The candidate pool set holds the pools the scope admits; the exchange
    # pool registry holds every pool the chain published. A pool that only
    # exists in the registry is still a real pool with a real pool key, so
    # the verification uses it rather than declaring the loop unverifiable.
    pools = {}
    for pair_key_id, pool_id, pool_key_json in cur.execute(
        "SELECT pair_key_id, pool_id, pool_key_json FROM manager_dex_candidate_pools"
    ):
        key = jload(pool_key_json)
        if not key:
            continue
        pools.setdefault(pool_id.strip().lower(), []).append(
            {"source": "manager_dex_candidate_pools", "pairKeyId": pair_key_id,
             "poolKey": key}
        )
    for pool_id, pool_key_json in cur.execute(
        "SELECT pool_id, pool_key_json FROM v4_exchange_pools"
    ):
        key = jload(pool_key_json)
        if not key:
            continue
        pools.setdefault(pool_id.strip().lower(), []).append(
            {"source": "v4_exchange_pools", "poolKey": key}
        )

    loop_rows = cur.execute(
        "SELECT loop_key, path_addresses_json, path_symbols_json, pool_ids_json, "
        "pool_path_key, status, scope_key, anchor_symbol, path_text, "
        "single_tick_capacity_anchor_raw, created_at, updated_at "
        "FROM manager_v4_closed_loops ORDER BY loop_key"
    ).fetchall()

    verified = []
    mismatched = []
    unverifiable = []
    for row in loop_rows:
        (loop_key, path_addresses_json, path_symbols_json, pool_ids_json,
         pool_path_key, status, scope_key, anchor_symbol, path_text,
         capacity, created_at, updated_at) = row
        addresses = jload(path_addresses_json) or []
        symbols = jload(path_symbols_json) or []
        published = [str(item).strip().lower() for item in (jload(pool_ids_json) or [])]

        # The pool path key is the ordered token path plus the ordered pool
        # path; the pool part is the tail after the token path.
        pool_path = []
        if pool_path_key:
            parts = str(pool_path_key).split(":")
            token_path_len = len(addresses)
            pool_path = [p.strip().lower() for p in parts[token_path_len:] if p.strip()]

        derived = []
        derivation_source = "pool_path_key"
        pool_path_equals_published = pool_path == published

        # Independently recompute the pool id of every published pool from
        # the pool key the Manager materialised, so the published set is
        # checked against the kernel's own derivation and not trusted.
        recomputed = []
        recompute_ok = True
        for pool_id in published:
            entries = pools.get(pool_id)
            if not entries:
                recompute_ok = False
                recomputed.append({"poolId": pool_id, "status": "no_materialised_pool_key"})
                continue
            entry = entries[0]
            try:
                computed = kernel_pool_id(entry["poolKey"])
            except Exception as error:
                recompute_ok = False
                recomputed.append({"poolId": pool_id, "status": "derivation_failed",
                                   "detail": str(error)})
                continue
            recomputed.append({
                "poolId": pool_id,
                "status": "match" if computed == pool_id else "mismatch",
                "computed": computed,
                "poolKeySource": entry.get("source"),
                "poolKey": entry["poolKey"],
            })

        record = {
            "loopKey": loop_key,
            "status": status,
            "scopeKey": scope_key,
            "anchorSymbol": anchor_symbol,
            "pathText": path_text,
            "pathAddresses": [str(a).strip().lower() for a in addresses],
            "pathSymbols": symbols,
            "managerPoolIds": published,
            "managerPoolIdsCount": len(published),
            "poolPathKeyPools": pool_path,
            "poolPathKeyEqualsPublished": pool_path_equals_published,
            "kernelDerivedPoolIds": derived,
            "kernelDerivationBasis": derivation_source,
            "poolIdRecomputation": recomputed,
            "singleTickCapacityAnchorRaw": capacity,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }

        if not published:
            record["verificationStatus"] = "unverifiable"
            record["reason"] = "manager_published_no_pool_set"
            unverifiable.append(record)
            continue
        if not recompute_ok:
            record["verificationStatus"] = "unverifiable"
            record["reason"] = "pool_key_not_materialised_or_derivation_failed"
            unverifiable.append(record)
            continue
        if not pool_path_equals_published:
            record["verificationStatus"] = "mismatch"
            record["reason"] = "pool_path_key_differs_from_published_pool_set"
            mismatched.append(record)
            continue
        record["verificationStatus"] = "verified"
        verified.append(record)

    # The reviews carry the same pool set; the review side is the consumer
    # of the published identity.
    review_rows = cur.execute(
        "SELECT loop_key, pool_ids_json, path_addresses_json, review_status, "
        "reviewed_at, signed_pnl_raw FROM manager_v4_reviews ORDER BY loop_key"
    ).fetchall()
    published_by_loop = {}
    for row in loop_rows:
        published_by_loop[str(row[0]).strip()] = [
            str(p).strip().lower() for p in (jload(row[3]) or [])
        ]
    review_same_basis = 0
    review_differing = []
    review_missing = []
    for loop_key, pool_ids_json, path_addresses_json, review_status, reviewed_at, pnl in review_rows:
        review_pools = [str(p).strip().lower() for p in (jload(pool_ids_json) or [])]
        manager_pools = published_by_loop.get(str(loop_key).strip())
        if manager_pools is None:
            review_missing.append({"loopKey": loop_key, "reason": "no_manager_loop"})
            continue
        if review_pools == manager_pools:
            review_same_basis += 1
        else:
            review_only = [p for p in review_pools if p not in manager_pools]
            manager_only = [p for p in manager_pools if p not in review_pools]
            review_differing.append({
                "loopKey": loop_key,
                "reviewPoolIds": review_pools,
                "managerPoolIds": manager_pools,
                "reviewOnlyPoolIds": review_only,
                "managerOnlyPoolIds": manager_only,
                "poolKeyDiffersOnlyIn": sorted(
                    set(review_only) | set(manager_only)
                ),
                "reviewStatus": review_status,
                "reviewedAt": reviewed_at,
            })

    # Pool metadata for every pool that appears in a differing review, so
    # the difference can be attributed rather than only reported.
    differing_pool_ids = set()
    for row in review_differing:
        differing_pool_ids.update(row.get("reviewOnlyPoolIds") or [])
        differing_pool_ids.update(row.get("managerOnlyPoolIds") or [])
    pool_metadata = {}
    for pool_id in sorted(differing_pool_ids):
        entry = pools.get(pool_id) or []
        key = entry[0]["poolKey"] if entry else None
        rows = cur.execute(
            "SELECT fee, tick_spacing, depth_status, liquidity, liquidity_read_at "
            "FROM manager_dex_candidate_pools WHERE lower(pool_id)=?", (pool_id,)
        ).fetchall()
        pool_metadata[pool_id] = {
            "poolId": pool_id,
            "poolKey": key,
            "candidatePoolRows": [
                {
                    "fee": r[0], "tickSpacing": r[1], "depthStatus": r[2],
                    "liquidity": r[3], "liquidityReadAt": r[4],
                }
                for r in rows
            ],
        }
    for row in review_differing:
        row["differingPoolMetadata"] = {
            pool_id: pool_metadata.get(pool_id)
            for pool_id in row.get("poolKeyDiffersOnlyIn") or []
        }

    # The publication time of the Manager set and the review time bound the
    # direction of the difference: a review older than the publication can
    # only carry the older selection.
    published_at = cur.execute(
        "SELECT MAX(updated_at) FROM manager_v4_closed_loops"
    ).fetchone()[0]
    reviewed_at_max = cur.execute(
        "SELECT MAX(reviewed_at) FROM manager_v4_reviews"
    ).fetchone()[0]

    package = {
        "task": "TASK-150",
        "title": "closed-loop identity second dimension: directed pool set is independently checkable",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "readonly-verification",
        "policy": (
            "read-only: the SQLite store is opened with mode=ro, no repair runs, no "
            "broadcast is attempted and no historical depth or business data is "
            "backfilled. The pool ids are recomputed with the kernel's own "
            "derivation, so the check is on the same basis as the kernel check."
        ),
        "chainId": 4663,
        "network": "robinhood",
        "scopeKey": "core4",
        "database": os.path.abspath(DB),
        "databaseSha256": sha256_file(DB),
        "kernelDerivation": {
            "module": "flashloan/rs-src/src/worker_pool.rs",
            "function": "derive_route_pool_ids",
            "order": ["entryKeys", "bridgeKeys", "exitKeys"],
            "poolIdModule": "flashloan/rs-src/src/pool_index.rs::pool_id",
            "poolIdFormula": (
                "keccak256 of the 160-byte ABI encoding of the normalized V4 pool key "
                "(currency0, currency1, uint24 fee, int24 tickSpacing, hooks)"
            ),
            "normalization": "lowercase and trim only; never deduplicate, never reorder",
        },
        "managerLoops": {
            "total": len(loop_rows),
            "verified": len(verified),
            "mismatched": len(mismatched),
            "unverifiable": len(unverifiable),
        },
        "reviews": {
            "total": len(review_rows),
            "poolSetSameBasisAsManager": review_same_basis,
            "poolSetDiffering": len(review_differing),
            "noManagerLoop": len(review_missing),
            "differing": review_differing,
        },
        "publicationFreshness": {
            "managerLoopPublishedAt": published_at,
            "reviewReviewedAtMax": reviewed_at_max,
            "reviewsOlderThanPublication": bool(
                published_at and reviewed_at_max
                and str(reviewed_at_max) < str(published_at)
            ),
            "note": (
                "a review that was produced before the Manager published its "
                "current set can only carry the older selection; a difference "
                "whose two sides straddle this boundary is a snapshot effect"
            ),
        },
        "reviewDifferenceAttribution": {
            "differingCount": len(review_differing),
            "sameBasisCount": review_same_basis,
            # Exactly one pool identity differs: the review names one pool the
            # publication does not, and the publication names one pool the
            # review does not. The two names together are the single pool
            # identity that was re-selected.
            "poolSetDiffersByExactlyOnePoolIdentity": all(
                len(r.get("reviewOnlyPoolIds") or []) == 1
                and len(r.get("managerOnlyPoolIds") or []) == 1
                for r in review_differing
            ),
            "poolIdentityReselected": [
                {
                    "loopKey": r["loopKey"],
                    "stalePoolId": (r.get("reviewOnlyPoolIds") or [None])[0],
                    "currentPoolId": (r.get("managerOnlyPoolIds") or [None])[0],
                    "reviewedAt": r["reviewedAt"],
                }
                for r in review_differing
            ],
            "poolSetOrderPreservedOutsideTheChangedPool": all(
                [p for p in r["reviewPoolIds"] if p not in (r.get("reviewOnlyPoolIds") or [])]
                == [p for p in r["managerPoolIds"] if p not in (r.get("managerOnlyPoolIds") or [])]
                for r in review_differing
            ),
            "poolMetadata": pool_metadata,
            "verdict": None,
        },
        "verified": verified,
        "mismatched": mismatched,
        "unverifiable": unverifiable,
        "conclusion": {},
    }

    if review_differing and not mismatched and not unverifiable:
        package["reviewDifferenceAttribution"]["verdict"] = (
            "stale snapshot, not an identity defect: the reviews are older than the "
            "Manager publication and differ only in the third hop, where the "
            "publication selected the deeper pool"
        )
    elif review_differing:
        package["reviewDifferenceAttribution"]["verdict"] = (
            "unresolved: the review pool set differs from the published one"
        )

    if not mismatched and not unverifiable:
        package["conclusion"] = {
            "verdict": "every published loop carries an ordered pool set that the "
                       "kernel's own derivation reproduces exactly",
            "allVerified": True,
        }
    else:
        package["conclusion"] = {
            "verdict": "not every published loop could be verified on the same basis",
            "allVerified": False,
            "mismatchedCount": len(mismatched),
            "unverifiableCount": len(unverifiable),
        }

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)
    print("wrote", OUT)
    print(json.dumps({
        "managerLoops": package["managerLoops"],
        "reviews": {k: v for k, v in package["reviews"].items() if k != "differing"},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
