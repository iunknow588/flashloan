from __future__ import annotations

from typing import Any

from cow_flashloan import order_submission
from intent_trade.direct import submit_direct_onchain_trade


def submit_cow_intent_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    intent = quote_payload.get("cow_flashloan_intent") if isinstance(quote_payload, dict) else None
    if not isinstance(intent, dict):
        return {
            "ok": False,
            "submitted": False,
            "status": "intent_missing",
            "blocked_reason": "intent_missing",
            "error": "cow_flashloan_intent is required",
        }
    if not intent.get("ready"):
        return {
            "ok": False,
            "submitted": False,
            "status": "intent_not_ready",
            "blocked_reason": "intent_not_ready",
            "error": "cow_flashloan_intent is not ready",
        }
    direct_protocol = intent.get("direct_onchain_protocol")
    if isinstance(direct_protocol, dict):
        protocol_name = str(
            intent.get("submission_protocol")
            or intent.get("intent_protocol")
            or direct_protocol.get("kind")
            or ""
        ).strip().lower()
        direct_requested = direct_protocol.get("enabled", True) and (
            protocol_name in {"direct_onchain", "unified_flashloan_mev_executor_runtime_v1"}
            or protocol_name.startswith("unified_flashloan")
        )
        if direct_requested:
            return submit_direct_onchain_trade(
                quote_payload=quote_payload,
                opportunity=opportunity,
                timeout_seconds=timeout_seconds,
            )
    return order_submission.submit_cow_flashloan_order(
        quote_payload=quote_payload,
        opportunity={**(opportunity or {}), "intent_trade_submission": True},
        timeout_seconds=timeout_seconds,
    )
