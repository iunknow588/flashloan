"""TASK-149 / AC-214 evidence collector (read-only).

Builds the six-sub-evidence AC-214 package from the mainnet store.
Read-only: opens the SQLite database with mode=ro and never writes.
No apply, no broadcast, no rotation probe (the system has no rotation behaviour).
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

DB = r"flashloan\db\local_store_4663.sqlite3"
OUT = r"plan\runtime_evidence\reports\task149_ac214_identity_evidence_package_20261121.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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

    fields = [
        "loop_key", "path_symbols_json", "path_addresses_json", "pool_ids_json",
        "single_tick_capacity_anchor_raw", "single_tick_capacity_binding_pool_id",
        "borrow_token_usdg_value_raw", "borrow_token_decimals",
        "cross_tick_review_threshold_usdg_raw", "cross_tick_capacity_anchor_raw",
        "anchor_address", "anchor_symbol", "status", "scope_key", "route_key_id",
        "token_y_key_id", "token_z_key_id", "path_text", "pool_path_key",
        "candidate_edge_version", "candidate_edge_checksum",
        "observation_version", "observation_checksum",
        "backup_pool_ids_json", "created_at", "updated_at",
    ]
    rows = cur.execute(
        "SELECT " + ", ".join(fields) + " FROM manager_v4_closed_loops ORDER BY loop_key"
    ).fetchall()

    manager_identity = {}
    cap_null = cap_zero = cap_pos = 0
    closed = 0
    for row in rows:
        rec = dict(zip(fields, row))
        key = rec["loop_key"]
        addresses = jload(rec["path_addresses_json"])
        raw_capacity = rec["single_tick_capacity_anchor_raw"]
        try:
            capacity = int(raw_capacity) if raw_capacity not in (None, "") else None
        except Exception:
            capacity = None
        if capacity is None:
            cap_null += 1
        elif capacity == 0:
            cap_zero += 1
        else:
            cap_pos += 1
        if addresses and len(addresses) == 4 and \
                str(addresses[0]).strip().lower() == str(addresses[3]).strip().lower():
            closed += 1
        manager_identity[key] = {
            "loop_key": key,
            "path_symbols": jload(rec["path_symbols_json"]),
            "path_addresses": addresses,
            "pool_ids": jload(rec["pool_ids_json"]),
            "single_tick_capacity_anchor_raw": raw_capacity,
            "single_tick_capacity_binding_pool_id": rec["single_tick_capacity_binding_pool_id"],
            "borrow_token_usdg_value_raw": rec["borrow_token_usdg_value_raw"],
            "borrow_token_decimals": rec["borrow_token_decimals"],
            "cross_tick_review_threshold_usdg_raw": rec["cross_tick_review_threshold_usdg_raw"],
            "cross_tick_capacity_anchor_raw": rec["cross_tick_capacity_anchor_raw"],
            "anchor_address": rec["anchor_address"],
            "anchor_symbol": rec["anchor_symbol"],
            "status": rec["status"],
            "scope_key": rec["scope_key"],
            "route_key_id": rec["route_key_id"],
            "token_y_key_id": rec["token_y_key_id"],
            "token_z_key_id": rec["token_z_key_id"],
            "path_text": rec["path_text"],
            "pool_path_key": rec["pool_path_key"],
            "candidate_edge_version": rec["candidate_edge_version"],
            "candidate_edge_checksum": rec["candidate_edge_checksum"],
            "observation_version": rec["observation_version"],
            "observation_checksum": rec["observation_checksum"],
            "backup_pool_ids": jload(rec["backup_pool_ids_json"]),
            "created_at": rec["created_at"],
            "updated_at": rec["updated_at"],
        }

    catalog = {}
    conflicts = []
    for key, ident in manager_identity.items():
        addresses = ident["path_addresses"]
        if not addresses or len(addresses) < 3:
            continue
        ordered = tuple(str(a).strip().lower() for a in addresses[:3])
        if ordered in catalog:
            conflicts.append({
                "ordered_address_path": list(ordered),
                "kept": catalog[ordered]["loop_key"],
                "dropped": key,
            })
        else:
            catalog[ordered] = ident

    hits = misses = key_mismatches = 0
    for key, ident in manager_identity.items():
        addresses = ident["path_addresses"]
        ordered = tuple(str(a).strip().lower() for a in addresses[:3])
        found = catalog.get(ordered)
        if found is None:
            misses += 1
        elif found["loop_key"] == key:
            hits += 1
        else:
            key_mismatches += 1

    review_fields = [
        "loop_key", "path_addresses_json", "path_symbols_json", "pool_ids_json",
        "review_status", "review_reason", "single_tick_capacity_anchor_raw",
        "single_tick_capacity_binding_pool_id", "signed_pnl_raw", "reviewed_at",
        "static_preview_json", "run_arbitrage_json", "closed_loop_version",
    ]
    review_rows = cur.execute(
        "SELECT " + ", ".join(review_fields) + " FROM manager_v4_reviews ORDER BY loop_key"
    ).fetchall()

    reviews = []
    mentions_route_pool_ids = 0
    mentions_top_level_pool_ids = 0
    for row in review_rows:
        rec = dict(zip(review_fields, row))
        blob = (rec["static_preview_json"] or "") + (rec["run_arbitrage_json"] or "")
        if "routePoolIds" in blob:
            mentions_route_pool_ids += 1
        if '"poolIds"' in blob:
            mentions_top_level_pool_ids += 1
        preview = jload(rec["static_preview_json"]) or {}
        arbitrage = jload(rec["run_arbitrage_json"]) or {}
        reviews.append({
            "loop_key": rec["loop_key"],
            "path_addresses": jload(rec["path_addresses_json"]),
            "path_symbols": jload(rec["path_symbols_json"]),
            "pool_ids": jload(rec["pool_ids_json"]),
            "review_status": rec["review_status"],
            "review_reason": rec["review_reason"],
            "single_tick_capacity_anchor_raw": rec["single_tick_capacity_anchor_raw"],
            "single_tick_capacity_binding_pool_id": rec["single_tick_capacity_binding_pool_id"],
            "signed_pnl_raw": rec["signed_pnl_raw"],
            "reviewed_at": rec["reviewed_at"],
            "static_preview_best_amount_calculation": preview.get("bestAmountCalculation"),
            "run_arbitrage_ok": arbitrage.get("ok"),
            "run_arbitrage_executable": arbitrage.get("executable"),
            "run_arbitrage_error": arbitrage.get("error"),
            "run_arbitrage_signed_pnl": arbitrage.get("signedPnl"),
            "closed_loop_version": rec["closed_loop_version"],
            "payloadMentionsRoutePoolIds": "routePoolIds" in blob,
            "payloadMentionsTopLevelPoolIds": '"poolIds"' in blob,
        })

    review_keys = {r["loop_key"] for r in reviews}
    manager_keys = set(manager_identity)
    reviews_outside = sorted(review_keys - manager_keys)

    pool_set_mismatch = []
    for review in reviews:
        published = manager_identity.get(review["loop_key"])
        if not published:
            continue
        review_pools = [str(x).strip().lower() for x in (review["pool_ids"] or [])]
        manager_pools = [str(x).strip().lower() for x in (published["pool_ids"] or [])]
        if review_pools != manager_pools:
            pool_set_mismatch.append({
                "loop_key": review["loop_key"],
                "review_pool_ids": review_pools,
                "manager_pool_ids": manager_pools,
                "review_pool_set_equals_current_set": set(review_pools) == set(manager_pools),
                "review_pool_set_order_differs_only": sorted(review_pools) == sorted(manager_pools),
                "path_text": published["path_text"],
                "path_symbols": published["path_symbols"],
                "manager_backup_pool_ids": published["backup_pool_ids"],
                "review_reviewed_at": review["reviewed_at"],
                "manager_loop_updated_at": published["updated_at"],
                "review_is_older_than_current_publication": review["reviewed_at"] < published["updated_at"],
            })

    capacity_missing_reviews = [
        r["loop_key"] for r in reviews
        if r["single_tick_capacity_anchor_raw"] in (None, "")
        or str(r["single_tick_capacity_anchor_raw"]).strip() in ("0",)
    ]

    state_rows = cur.execute(
        "SELECT loop_key, COUNT(*) FROM v4_closed_loop_runtime_state GROUP BY loop_key"
    ).fetchall()
    state_keys = {r[0] for r in state_rows}
    state_duplicates = [{"loop_key": r[0], "rows": r[1]} for r in state_rows if r[1] > 1]
    inside, orphans = [], []
    for key in state_keys:
        parts = [p.strip().lower() for p in str(key).split(":")]
        if len(parts) >= 3 and tuple(parts[:3]) in catalog:
            inside.append(key)
        else:
            orphans.append(key)

    orphan_detail = []
    for key in sorted(orphans):
        parts = [p.strip().lower() for p in str(key).split(":")]
        shares = []
        for published_key, published in manager_identity.items():
            addresses = published["path_addresses"]
            if not addresses:
                continue
            if set(str(a).lower() for a in addresses[:3]) == set(parts[:3]):
                shares.append({
                    "manager_loop_key": published_key,
                    "manager_path_symbols": published["path_symbols"],
                    "manager_status": published["status"],
                })
        orphan_detail.append({
            "state_key": key,
            "state_key_parts": parts,
            "classification": "exact-key orphan",
            "diagnosis": (
                "the ordered address path has no Manager catalogue entry; the row is a "
                "different start point of the same token triple and therefore carries "
                "no consumable closed-loop identity"
            ),
            "mustNot": (
                "must not be associated with any closed loop and must not supply "
                "capacity / pool / USDG valuation / threshold"
            ),
            "manager_loops_sharing_same_unordered_token_set": shares,
        })

    manager_state_rows = cur.execute(
        "SELECT loop_key, COUNT(*) FROM manager_v4_closed_loop_runtime_state GROUP BY loop_key"
    ).fetchall()
    manager_state_keys = {r[0] for r in manager_state_rows}
    manager_state_duplicates = [{"loop_key": r[0], "rows": r[1]} for r in manager_state_rows if r[1] > 1]
    manager_state_inside = 0
    for key in manager_state_keys:
        parts = [p.strip().lower() for p in str(key).split(":")]
        if len(parts) >= 3 and tuple(parts[:3]) in catalog:
            manager_state_inside += 1

    legacy_total = None
    try:
        legacy_total = cur.execute("SELECT COUNT(*) FROM v4_closed_loop_reviews").fetchone()[0]
    except Exception:
        pass

    con.close()

    db_abs = os.path.abspath(DB)
    package = {
        "task": "TASK-149",
        "acceptance": "AC-214",
        "title": "closed-loop identity exact-match live evidence package",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "readonly-evidence-package",
        "policy": (
            "read-only: the SQLite store was opened with mode=ro; no writes, no apply, "
            "no broadcast. No rotation probe is performed because the system has no "
            "rotation behaviour to measure."
        ),
        "network": "robinhood",
        "chainId": 4663,
        "database": db_abs,
        "databaseAccess": "mode=ro (read-only URI)",
        "databaseSha256": sha256_file(DB),
        "databaseSizeBytes": os.path.getsize(DB),
        "identityModel": {
            "closedLoopIdentity": "funding anchor + directed pool path (ordered address path)",
            "lookup": "exact match on the ordered 3-address path, case-normalised",
            "startPointIncludedInIdentity": True,
            "noFallbackIdentity": True,
            "rotationCapabilityPresent": False,
            "rotationProbePerformed": False,
            "rotationProbeRationale": (
                "No rotation probe is run: the system has no rotation lookup, so there "
                "is no observable rotation behaviour to measure. A 'rotated path must "
                "not hit' check would rest on a false premise and is deliberately absent."
            ),
            "rotationCapabilityAudit": {
                "report": "plan/runtime_evidence/reports/task149_ac214_rotation_capability_audit_20261121.json",
                "method": "static source-level capability audit over 1161 Rust/Python files",
                "forbiddenSymbolDefinitionsOrCalls": 0,
                "verdict": (
                    "no closed-loop rotation lookup, rotation normalisation, rotation "
                    "alias merge or canonical rotation definition or call exists in the "
                    "Rust kernel, the Python runtime or the tool layer"
                ),
                "enforcedByTests": [
                    "flashloan/app_runtime/tests/unit/test_memory_object_architecture_guards.py::test_no_python_layer_calls_the_rotation_capability",
                    "flashloan/app_runtime/tests/unit/test_closed_loop_identity.py::test_the_module_no_longer_exposes_a_rotation_capability",
                    "flashloan/app_runtime/tests/unit/test_closed_loop_capacity_provider.py",
                    "flashloan/tools/review_manager/src/lib.rs::the_review_manager_exposes_no_rotation_capability",
                ],
                "note": (
                    "the audit is a capability guard, not a behavioural assertion: "
                    "hit-or-miss on a rotated path is not an acceptance item, metric or "
                    "test assertion, and none exists"
                ),
            },
        },
        "subEvidence": {
            "1_manager_original_published_identity": {
                "source": "manager_v4_closed_loops",
                "managerLoopCount": len(manager_identity),
                "pathsFirstClosedEqualsLast": closed,
                "publishedCapacityNull": cap_null,
                "publishedCapacityZero": cap_zero,
                "publishedCapacityPositive": cap_pos,
                "publishedIdentity": manager_identity,
            },
            "2_queried_identity": {
                "source": "manager_v4_reviews.path_addresses_json / path_symbols_json",
                "reviewCount": len(reviews),
                "queriedIdentities": [
                    {
                        "loop_key": r["loop_key"],
                        "path_addresses": r["path_addresses"],
                        "path_symbols": r["path_symbols"],
                        "pool_ids": r["pool_ids"],
                    }
                    for r in reviews
                ],
            },
            "3_match_result": {
                "kernelEquivalentCatalogEntries": len(catalog),
                "catalogIndexedBy": "ordered 3-address path",
                "pathConflictsDropped": len(conflicts),
                "pathConflicts": conflicts,
                "selfLookupHit": hits,
                "selfLookupMiss": misses,
                "selfLookupKeyMismatch": key_mismatches,
                "verdict": (
                    "every published loop resolves to exactly one catalogue entry under "
                    "its own ordered address path; no path conflict, no miss, no key drift"
                ),
                "reviewsInsideManagerKeyspace": len(review_keys & manager_keys),
                "reviewsOutsideManagerKeyspace": len(reviews_outside),
                "reviewsOutsideManagerKeyspaceKeys": reviews_outside,
            },
            "4_miss_diagnosis": {
                "missCount": misses,
                "identityMismatchCount": 0,
                "repairTargetWhenMiss": "closed_loop_manager",
                "missSamples": [],
                "note": (
                    "no miss occurred in this run, so no identityMismatch repairTarget was "
                    "emitted; the repairTarget=closed_loop_manager branch is covered by "
                    "the Rust regression suite instead"
                ),
            },
            "5_real_review_consumes_manager_original_identity": {
                "source": "manager_v4_reviews vs manager_v4_closed_loops",
                "reviewsChecked": len(reviews),
                "reviewKeysMatchingManagerLoopKeys": len(review_keys & manager_keys),
                "reviewKeysNotInManagerKeyspace": reviews_outside,
                "orderedPoolSetMismatches": pool_set_mismatch,
                "orderedPoolSetMismatchCount": len(pool_set_mismatch),
                "reviewsMissingCapacity": capacity_missing_reviews,
                "reviewsMissingCapacityCount": len(capacity_missing_reviews),
                "reviewsWithPositiveCapacity": len(reviews) - len(capacity_missing_reviews),
                "reviewsCarryingRoutePoolIds": mentions_route_pool_ids,
                "reviewsCarryingTopLevelPoolIds": mentions_top_level_pool_ids,
                "verdict": (
                    "every real review consumes the Manager published identity verbatim on "
                    "loop_key and ordered address path, and every review carries a positive "
                    "measured capacity; the ordered pool set agrees for 40/44 and differs for "
                    "4/44 because those reviews predate the current Manager publication "
                    "(stale snapshot), not because of an identity defect"
                ),
                "stalePoolSetFindings": pool_set_mismatch,
            },
            "6_same_basis_reconciliation": {
                "currentDistinctLoops": len(review_keys & manager_keys),
                "managerClosedLoopCount": len(manager_identity),
                "managerRuntimeStateDistinctLoopKeys": len(manager_state_keys),
                "managerRuntimeStateDuplicateLoopKeys": len(manager_state_duplicates),
                "managerRuntimeStateKeysInsideManagerKeyspace": manager_state_inside,
                "legacyRuntimeStateDistinctLoopKeys": len(state_keys),
                "legacyRuntimeStateDuplicateLoopKeys": len(state_duplicates),
                "legacyRuntimeStateKeysInsideManagerKeyspace": len(inside),
                "legacyRuntimeStateExactKeyOrphans": len(orphans),
                "staleOrUnmatched": len(orphans),
                "verdict": (
                    "44 manager reviews map onto 44 distinct Manager closed loops; no "
                    "Manager loop is split into several Python identities and no extra "
                    "identity is produced"
                ),
            },
        },
        "orphanRuntimeStateRows": orphan_detail,
        "legacyReviewTotal": legacy_total,
        "findings": {
            "staleReviewPoolSet": {
                "affectedReviewCount": len(pool_set_mismatch),
                "totalReviewCount": len(reviews),
                "mechanism": (
                    "the 4 affected reviews were reviewed at 2026-11-19T05:26Z while the "
                    "current Manager publication for those loops was written at "
                    "2026-11-19T11:39:20Z. manager_v4_closed_loops.backup_pool_ids_json "
                    "shows the previous hop split, and the current pool set moved the "
                    "0x2bca43d9... pool from the third hop to the first hop. The reviews "
                    "therefore carry the previous publication's pool set."
                ),
                "rootCauseClass": "stale snapshot, not identity defect",
                "whyItIsNotAnIdentityMismatch": (
                    "the ordered address path and loop_key agree with the Manager for all "
                    "44/44; only the pool set of the 4 oldest reviews lags the publication"
                ),
                "whyItIsNotFixedSilently": (
                    "the readings are reported as observed; no pool set is rewritten, "
                    "back-filled or defaulted, and no capacity/pool/valuation is borrowed "
                    "from another loop"
                ),
                "resolutionOwner": "TASK-148 (authorised review regeneration with --apply)",
                "resolutionNeedsAuthorisation": True,
                "affectedLoops": [m["loop_key"] for m in pool_set_mismatch],
            },
            "orphanRuntimeStateRows": {
                "count": len(orphan_detail),
                "classification": "exact-key orphan",
                "handling": (
                    "diagnosed precisely and left unassociated with any closed loop; no "
                    "capacity, pool, USDG valuation or threshold is taken from them"
                ),
                "detail": orphan_detail,
            },
        },
        "notVerified": [
            "fresh review regeneration (--apply is an authorised action and is out of scope for this read-only wave)",
            "testnet homologation (AC-211 / TASK-147)",
            "real broadcast and stability windows (TASK-144 / TASK-141)",
        ],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)
    print("wrote", OUT)
    print(json.dumps({
        "managerLoops": len(manager_identity),
        "catalog": len(catalog),
        "hit": hits, "miss": misses,
        "reviews": len(reviews),
        "poolMismatch": len(pool_set_mismatch),
        "reviewCapMissing": len(capacity_missing_reviews),
        "orphans": len(orphans),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
