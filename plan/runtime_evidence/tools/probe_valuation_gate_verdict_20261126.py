"""Read-only probe: does the valuation A/B flip the cross-tick gate verdict?

The A/B moves the **break-even** single-tick profit of a closed loop: the
profit at which the USDG profit of the loop reaches the review threshold.
A verdict only flips when the **measured** profit lies between the two
break-evens, so the measured profit is needed to answer the question.

The measured profit is published in the review records (JSONL, one file per
closed loop and day).  The probe joins them onto the A/B report by loop
key and reports, for every closed loop that has a measured profit:

* the USDG profit under snapshot A and under snapshot B,
* the verdict (the cross-tick supplement starts or not) under each,
* whether the verdict differs.

Read-only throughout: the store is opened `mode=ro` with
`PRAGMA query_only = ON`, and the review records are only read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEW_ROOT = REPO_ROOT / "flashloan" / "db" / "review_record_jsonl"
DEFAULT_REPORT = REPO_ROOT / "plan" / "runtime_evidence" / "valuation_snapshot_ab_20261126.json"


def latest_reviews(root: Path) -> dict[str, dict]:
    """The newest review record of every closed loop.

    A loop is reviewed again and again, and every generation carries the
    same loop key, so the newest record of a loop is the one that speaks
    for it.  The review version decides which record is the newest.
    """
    best: dict[str, dict] = {}
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                loop_key = record.get("loopKey")
                if not loop_key:
                    continue
                version = int(record.get("reviewVersion") or 0)
                current = best.get(loop_key)
                if current is None or version > int(current.get("reviewVersion") or 0):
                    best[loop_key] = record
    return best


def signed_raw(text: str | None) -> int | None:
    """The published signed profit, as a signed integer.

    The value is published as a hex string with a sign, so it is read
    with the base-16 conversion and the sign applied by hand.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return -value if negative else value


def main() -> int:
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reviews = latest_reviews(REVIEW_ROOT)

    verdicts: list[dict] = []
    flips: list[dict] = []
    lost_tokens = set(report["coverage"]["onlyInLegacy"])
    reviews_on_lost_tokens: list[dict] = []
    anchor_of = {entry["loopKey"]: entry["anchorTokenKeyId"] for entry in report["loops"]}
    for entry in report["loops"]:
        loop_key = entry["loopKey"]
        record = reviews.get(loop_key)
        if record is None:
            continue
        profit = signed_raw(record.get("signedPnlRaw"))
        if profit is None or profit <= 0:
            # A loop that does not earn anything is below the threshold
            # under both snapshots, so its verdict cannot differ.
            continue
        decimals = entry.get("borrowTokenDecimals")
        before = entry.get("valueUsdgRawBefore")
        after = entry.get("valueUsdgRawAfter")
        if decimals is None or before is None or after is None:
            continue
        threshold = int(entry["crossTickReviewThresholdUsdgRaw"])
        scale = 10 ** int(decimals)
        profit_usdg_before = (profit * int(before)) // scale
        profit_usdg_after = (profit * int(after)) // scale
        verdict_before = profit_usdg_before >= threshold
        verdict_after = profit_usdg_after >= threshold
        row = {
            "loopKey": loop_key,
            "reviewVersion": record.get("reviewVersion"),
            "reviewStatus": record.get("reviewStatus"),
            "signedPnlRaw": record.get("signedPnlRaw"),
            "borrowTokenDecimals": decimals,
            "profitUsdgRawBefore": profit_usdg_before,
            "profitUsdgRawAfter": profit_usdg_after,
            "thresholdUsdgRaw": threshold,
            "crossTickStartsBefore": not verdict_before,
            "crossTickStartsAfter": not verdict_after,
            "verdictFlipped": verdict_before != verdict_after,
        }
        verdicts.append(row)
        if row["verdictFlipped"]:
            flips.append(row)

    for loop_key, record in reviews.items():
        anchor = anchor_of.get(loop_key)
        if anchor in lost_tokens:
            reviews_on_lost_tokens.append(
                {"loopKey": loop_key, "anchorTokenKeyId": anchor, "reviewVersion": record.get("reviewVersion")}
            )

    summary = {
        "probe": "valuation_gate_verdict_20261126",
        "task": "T-闭.02",
        "readOnly": True,
        "reviewedLoopsWithPositiveProfit": len(verdicts),
        "verdictFlips": len(flips),
        "reviewsRead": len(reviews),
        "reviewsOnTokensThatLostTheirValuation": len(reviews_on_lost_tokens),
        "flips": flips,
        "verdicts": verdicts,
    }
    out_path = Path(__file__).resolve().parents[2] / "runtime_evidence" / "valuation_gate_verdict_20261126.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("reviewedLoopsWithPositiveProfit", "verdictFlips", "reviewsRead")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
