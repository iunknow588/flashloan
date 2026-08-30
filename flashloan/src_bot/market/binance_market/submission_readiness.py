"""Pure CoW submission gate shared by quote and submission adapters."""

from __future__ import annotations

from typing import Any, Iterable


def _dedupe(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def build_submission_readiness(
    *,
    checks_passed: bool,
    route_supported: bool,
    quote_available: bool,
    quote_verified: bool = False,
    submission_requested: bool,
    adapter_available: bool,
    sdk_ready: bool,
    network_supported: bool,
    signer_ready: bool,
    quote_submission_blocked: bool = False,
    quote_error_type: str | None = None,
    existing_reasons: Iterable[object] = (),
) -> dict[str, Any]:
    """Return a stable gate result without performing submission side effects."""

    reasons = list(existing_reasons)
    if not route_supported:
        reasons.append("cow_route_has_unsupported_token")
    if not checks_passed:
        reasons.append("quote_checks_not_passed")
    if not quote_available:
        reasons.append(quote_error_type or "cow_quote_unavailable")
    if not quote_verified:
        reasons.append("quote_not_verified")
    if not submission_requested:
        reasons.append("order_submission_switch_off")
    if not adapter_available:
        reasons.append("order_submission_adapter_unavailable")
    if not sdk_ready:
        reasons.append("cow_flashloan_sdk_install_required")
    if not network_supported:
        reasons.append("order_submission_network_unsupported")
    if not signer_ready:
        reasons.append("order_submission_signer_not_ready")
    if quote_submission_blocked:
        reasons.append("quote_submission_blocked")

    blocked_reasons = _dedupe(reasons)
    can_submit = (
        bool(checks_passed)
        and bool(route_supported)
        and bool(quote_available)
        and bool(quote_verified)
        and bool(submission_requested)
        and bool(adapter_available)
        and bool(sdk_ready)
        and bool(network_supported)
        and bool(signer_ready)
        and not bool(quote_submission_blocked)
    )
    return {
        "ready": can_submit,
        "execution_enabled": bool(checks_passed),
        "submission_requested": bool(submission_requested),
        "can_submit_order": can_submit,
        "blocked_reasons": [] if can_submit else blocked_reasons,
        "quote_available": bool(quote_available),
        "quote_verified": bool(quote_verified),
        "submission_rule_verified": bool(quote_verified),
        "route_supported": bool(route_supported),
        "quote_error_type": quote_error_type,
    }
