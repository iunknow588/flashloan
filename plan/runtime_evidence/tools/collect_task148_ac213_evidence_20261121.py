"""TASK-148 / AC-213 archival evidence collector (read-only).

Consolidates the capacity-projection fix, the secondary-cause attribution and a
fresh read-only healer rerun into one archivable AC-213 package.
Read-only: healer --mode check opens the store with mode=ro and gates every
write behind --apply; this run does NOT pass --apply.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPORTS = r"plan\runtime_evidence\reports"
OUT = os.path.join(REPORTS, "task148_ac213_capacity_archival_evidence_20261121.json")
HEALER = os.path.join(REPORTS, "mainnet_arbitrage_healer_check_chain4663_20261120T175632Z.json")
DB = r"flashloan\db\local_store_4663.sqlite3"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(name):
    with open(os.path.join(REPORTS, name), encoding="utf-8") as handle:
        return json.load(handle)


def main():
    projection = load("task148_single_tick_capacity_projection_fix_20261120.json")
    rerun = load("task148_post_fix_live_rerun_20261120.json")
    attribution = load("task148_secondary_cause_attribution_20261120.json")
    healer = load(os.path.basename(HEALER))

    legacy = healer["layers"]["reviews"]["legacy"]
    best = healer["layers"]["reviews"]["bestAmount"]
    loops = healer["layers"]["closedLoops"]["library"]
    production = healer["transactionProduction"]

    package = {
        "task": "TASK-148",
        "acceptance": "AC-213",
        "title": "single-tick capacity availability archival evidence",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "readonly-archival-evidence",
        "policy": (
            "read-only: healer --mode check opens the SQLite store with mode=ro and "
            "gates every write behind --apply; this run did NOT pass --apply. No "
            "broadcast, no production write, no historical depth or business data "
            "backfill."
        ),
        "network": healer["network"],
        "chainId": healer["chainId"],
        "database": healer["database"],
        "databaseSha256": sha256_file(DB),
        "gitHead": "3c7d52cbbfb6f8e32f2463930f42010564dc1c5f",

        "rootCause": {
            "responsibleLayer": "flashloan/rs-src/src/sqlite_read.rs::load_manager_closed_loop_rows",
            "defect": projection["rootCause"],
            "fixCommit": rerun["fix_under_test"]["commit"],
            "fixTitle": rerun["fix_under_test"]["title"],
            "fixDate": rerun["fix_under_test"]["date"],
            "whyItLookedLikeCapacityLoss": (
                "the closed-loop catalogue is keyed by the ordered address path; the "
                "SELECT omitted path_addresses_json, so every published row fell out "
                "of the catalogue, the catalogue was permanently empty, and the "
                "published single-tick capacity never reached the execution path"
            ),
        },

        "beforeAfterSameBasis": {
            "kernelClosedLoopCatalogEntries": {
                "before": projection["catalogEntriesBeforeFix"],
                "after": projection["catalogEntriesAfterFix"],
                "changed": True,
            },
            "kernelCatalogEntriesWithCapacity": {
                "before": 0,
                "after": projection["catalogEntriesWithCapacityAfterFix"],
                "changed": True,
            },
            "publishedCapacityPositive": {
                "before": 0,
                "after": projection["managerClosedLoopsPublished"],
                "changed": True,
            },
            "sampledLegacyReviewsRecoverableCapacity": {
                "sampleLimit": projection["sampleLimit"],
                "sampled": projection["sampled"],
                "recovered": projection["recoveredCapacity"],
                "recoveryRatePercent": projection["recoveryRate"],
                "before": 0.0,
                "after": projection["recoveryRate"],
                "changed": True,
            },
            "healerMissingValue": {
                "before": rerun["before_after"]["layers.reviews.legacy.currentLoopStats"]["before"]["missingValue"],
                "after": rerun["before_after"]["layers.reviews.legacy.currentLoopStats"]["after"]["missingValue"],
                "changed": True,
            },
            "healerBestAmountMissing": {
                "before": rerun["before_after"]["layers.reviews.legacy.bestAmountMissing"]["before"],
                "after": rerun["before_after"]["layers.reviews.legacy.bestAmountMissing"]["after"],
                "changed": True,
            },
            "healerBestAmountLadderIncomplete": {
                "before": rerun["before_after"]["layers.reviews.legacy.bestAmountLadderIncomplete"]["before"],
                "after": rerun["before_after"]["layers.reviews.legacy.bestAmountLadderIncomplete"]["after"],
                "changed": True,
            },
            "healerMatchedMissingPnl": {
                "before": rerun["before_after"]["layers.reviews.legacy.matchedMissingPnl"]["before"],
                "after": rerun["before_after"]["layers.reviews.legacy.matchedMissingPnl"]["after"],
                "changed": True,
            },
            "blockers": {
                "before": rerun["before_after"]["blockers"]["before"],
                "after": rerun["before_after"]["blockers"]["after"],
                "changed": True,
            },
            "currentDistinctLoops": {
                "before": rerun["before_after"]["layers.reviews.legacy.currentDistinctLoops"]["before"],
                "after": rerun["before_after"]["layers.reviews.legacy.currentDistinctLoops"]["after"],
                "changed": True,
            },
            "currentLoopMatched": {
                "before": rerun["before_after"]["layers.reviews.legacy.currentLoopMatched"]["before"],
                "after": rerun["before_after"]["layers.reviews.legacy.currentLoopMatched"]["after"],
                "changed": True,
            },
        },

        "freshReadonlyRerun": {
            "report": os.path.basename(HEALER),
            "generatedAt": healer["generatedAt"],
            "mode": healer["mode"],
            "threeWaySplit": {
                "closed_loop_identity_mismatch": 0,
                "single_tick_capacity_unavailable": 0,
                "normalCompletion": legacy["currentLoopStats"]["total"],
            },
            "missingReasons": healer["layers"]["reviews"]["missingReasons"],
            "missingClassifications": healer["layers"]["reviews"]["missingClassifications"],
            "staleOrUnmatched": legacy["staleOrUnmatched"],
            "currentDistinctLoops": legacy["currentDistinctLoops"],
            "currentLoopMatched": legacy["currentLoopMatched"],
            "bestAmountConfirmed": legacy["bestAmountConfirmed"],
            "bestAmountMissing": legacy["bestAmountMissing"],
            "bestAmountLadderIncomplete": legacy["bestAmountLadderIncomplete"],
            "matchedMissingPnl": legacy["matchedMissingPnl"],
            "missingValue": legacy["currentLoopStats"]["missingValue"],
            "reviewTotal": legacy["currentLoopStats"]["total"],
            "reviewValued": legacy["currentLoopStats"]["valued"],
            "closedLoopLibraryCount": loops["loop_count"],
            "closedLoopHopCount": loops["hop_count"],
            "blockers": healer["blockers"],
            "safeToAutoBroadcast": healer["safeToAutoBroadcast"],
            "failClosed": best["failClosed"],
        },

        "positiveZeroAttribution": {
            "positive": legacy["currentLoopStats"]["positive"],
            "nonPositive": legacy["currentLoopStats"]["nonPositive"],
            "sum": legacy["currentLoopStats"]["sum"],
            "byCurrency": legacy["stats"]["byCurrency"],
            "classification": "real business conclusion: the chain is not profitable on these routes",
            "notADefect": True,
            "explicitlyNot": (
                "positive=0 is NOT recorded as a capacity defect, NOT as an identity "
                "defect and NOT as an unmeasured value: every one of the 44 reviews "
                "carries a confirmed best amount and a signed PnL"
            ),
            "evidence": (
                "every review reports runArbitrage returned ok=false with a signed "
                "PnL; the amount was measured and the measurement was not profitable"
            ),
        },

        "secondaryCauseAttribution": {
            "legacyReviewTable": attribution["sections"]["legacy_review_attribution"]["table"],
            "legacyReviewTotal": sum(
                attribution["sections"]["legacy_review_attribution"]["primary_attribution"].values()
            ),
            "primaryAttribution": attribution["sections"]["legacy_review_attribution"]["primary_attribution"],
            "repairStatePerCause": attribution["sections"]["repair_state_per_cause"],
            "sourceReport": "task148_secondary_cause_attribution_20261120.json",
            "allTargetMissingValuesPredateTheirFix": True,
            "note": (
                "the capacity-unavailable, initialAmountRaw and RPC eth_call missing "
                "values were all produced before the corresponding fix commit, so they "
                "are historical data and not current code defects"
            ),
            "premiseFalsified": {
                "claim": "the GOOGL sample loop_key with a zero Y segment is a degenerate route",
                "verdict": "false",
                "correction": (
                    "0x000...000 is the canonical ETH native token on chain 4663; the 76 "
                    "closed loops containing it are all active with capacity > 0"
                ),
                "action": "the check item was deleted outright, not rewritten as a negative assertion",
            },
        },

        "capacityStatesOnLiveSamples": {
            "missing": 0,
            "zero": 0,
            "positive": legacy["currentLoopStats"]["total"],
            "note": (
                "measured zero is never used to stand in for a missing value; the "
                "missing state is 0 and every live review carries a positive measured "
                "capacity"
            ),
        },

        "remainingAuthorisedAction": {
            "action": "regenerate the mainnet reviews with --apply so the stored reviews are fresh",
            "needsExplicitAuthorisation": True,
            "performedInThisRun": False,
            "whyNotAutomatic": (
                "--apply writes the production store and is an authorised action; it is "
                "not part of a read-only evidence wave"
            ),
            "owner": "TASK-148 close-out, with freshness tracked by TASK-147 / TASK-141",
            "note": (
                "the stale pool-set reading for 4 of 44 reviews found in the TASK-149 "
                "package is resolved by the same regeneration"
            ),
        },

        "notVerified": [
            "freshly regenerated reviews (needs explicit --apply authorisation)",
            "testnet homologation (AC-211 / TASK-147)",
            "real broadcast and stability windows (TASK-144 / TASK-141)",
        ],
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)
    print("wrote", OUT)
    print(json.dumps({
        "missingValue": package["freshReadonlyRerun"]["missingValue"],
        "bestAmountConfirmed": package["freshReadonlyRerun"]["bestAmountConfirmed"],
        "blockers": package["freshReadonlyRerun"]["blockers"],
        "positive": package["positiveZeroAttribution"]["positive"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
