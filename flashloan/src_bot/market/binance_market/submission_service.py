"""CoW order submission orchestration independent from Flask routes."""

from __future__ import annotations

from typing import Any, Callable


def submit_ready_quote_orders(
    payload: dict[str, Any],
    *,
    market_state: dict[str, Any],
    submitter: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Submit only quote rows that passed the precheck and summarize outcomes."""

    submitted = 0
    failed = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    for item in payload.get("ranking") or []:
        if not isinstance(item, dict):
            continue
        precheck = dict(item.get("execution_precheck") or {})
        if not precheck.get("can_submit_order"):
            skipped += 1
            continue
        quote_payload = {
            **item,
            "cow_network": payload.get("cow_network"),
            "cow_chain_id": payload.get("cow_chain_id"),
            "owner": payload.get("owner"),
            "owner_source": payload.get("owner_source"),
            "cow_testnet": payload.get("cow_testnet"),
        }
        submission = submitter(
            quote_payload=quote_payload,
            opportunity={
                "source": "web_cow_quotes",
                "market_state": market_state,
                "pair": item.get("pair"),
                "pair_rank": item.get("pair_rank"),
                "priority_reason": item.get("priority_reason"),
            },
        )
        item["cow_submission_result"] = submission
        sdk_plan = item.get("cow_flashloan_sdk_plan") if isinstance(item.get("cow_flashloan_sdk_plan"), dict) else {}
        if sdk_plan:
            sdk_plan["submission_status"] = submission.get("status")
            sdk_plan["order_id"] = submission.get("order_id")
            sdk_plan["tx_hash"] = submission.get("tx_hash")
        reasons = list(precheck.get("reasons") or [])
        precheck.update(
            {
                "execution_phase": "order_submission",
                "submission_attempted": True,
                "submission_status": submission.get("status"),
                "submission_order_id": submission.get("order_id"),
                "submission_tx_hash": submission.get("tx_hash"),
            }
        )
        if submission.get("submitted"):
            submitted += 1
            precheck["status"] = "submitted_success"
            precheck["can_submit_order"] = False
            if "cow_order_submitted_successfully" not in reasons:
                reasons.append("cow_order_submitted_successfully")
        else:
            failed += 1
            precheck["status"] = "submission_failed"
            precheck["can_submit_order"] = False
            error = submission.get("error") or submission.get("blocked_reason") or "cow_order_submission_failed"
            if str(error) not in reasons:
                reasons.append(str(error))
            item["error"] = str(error)
        precheck["reasons"] = reasons
        item["execution_precheck"] = precheck
        sdk_result = dict(item.get("cow_sdk_result") or {})
        sdk_result.update(
            {
                "status": precheck["status"],
                "ready": bool(precheck.get("checks_passed")),
                "submission_attempted": True,
                "submission_status": submission.get("status"),
                "submission_order_id": submission.get("order_id"),
                "submission_tx_hash": submission.get("tx_hash"),
                "submission_error": submission.get("error"),
                "submission_blocked_reason": submission.get("blocked_reason"),
                "submission_preflight": submission.get("preflight"),
                "execution_error": submission.get("execution_error"),
                "submitted": bool(submission.get("submitted")),
            }
        )
        item["cow_sdk_result"] = sdk_result
        results.append(
            {
                "pair": item.get("pair"),
                "priority_reason": item.get("priority_reason"),
                "status": submission.get("status"),
                "submitted": bool(submission.get("submitted")),
                "order_id": submission.get("order_id"),
                "tx_hash": submission.get("tx_hash"),
                "blocked_reason": submission.get("blocked_reason"),
                "error": submission.get("error"),
            }
        )
    opportunities = [
        item for item in payload.get("ranking") or []
        if (item.get("execution_precheck") or {}).get("checks_passed")
    ]
    payload["opportunities"] = opportunities
    payload["opportunity_count"] = len(opportunities)
    payload["best_opportunity"] = opportunities[0] if opportunities else None
    payload["best"] = opportunities[0] if opportunities else ((payload.get("ranking") or [None])[0])
    payload["submission_summary"] = {
        "attempted": submitted + failed,
        "submitted": submitted,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
    return payload
