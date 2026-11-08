"""Pure mapping and validation helpers for CoW execution persistence."""

from __future__ import annotations

import json
from typing import Any

from core.evidence_scope import infer_evidence_scope


SELECTED_PROFIT_CAMEL_KEYS = (
    "selectedProfitRaw",
    "selectedProfitAmount",
    "selectedProfitAmountSymbol",
    "selectedProfitSource",
    "selectedProfitMissingReason",
)

SELECTED_PROFIT_SNAKE_KEYS = (
    "selected_profit_raw",
    "selected_profit_amount",
    "selected_profit_amount_symbol",
    "selected_profit_source",
    "selected_profit_missing_reason",
)


def _not_executable_reason_sources(payload: dict[str, Any]) -> list[str]:
    """Collect machine-readable reasons for a not-executable review."""
    if not isinstance(payload, dict):
        return []
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    live = (
        quote.get("live_onchain_evidence")
        or payload.get("live_onchain_evidence")
        or payload.get("review_summary", {}).get("onchain_evidence")
    )
    if not isinstance(live, dict):
        live = {}
    sources: list[str] = []
    failure_kind = str(payload.get("reviewFailureKind") or payload.get("errorKey") or "").strip()
    if failure_kind:
        sources.append(failure_kind)
    for key in ("reviewFailureReasons", "failureReason", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().startswith(("[", "{")):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if isinstance(value, dict):
            for text_key in ("reason", "message", "error", "detail"):
                text = str(value.get(text_key) or "").strip()
                if text:
                    sources.append(text)
                    break
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    reason = str(
                        item.get("reason") or item.get("message") or item.get("error") or ""
                    ).strip()
                    if reason:
                        sources.append(reason)
                elif str(item or "").strip():
                    sources.append(str(item).strip())
        elif str(value or "").strip():
            sources.append(str(value).strip())
    for value in (
        payload.get("blocked_reasons"),
        live.get("blockers"),
        live.get("conclusion"),
        payload.get("conclusion"),
    ):
        if isinstance(value, list):
            sources.extend(str(item).strip() for item in value if str(item or "").strip())
        elif str(value or "").strip():
            sources.append(str(value).strip())
    error_stack = payload.get("errorStack") or payload.get("error_stack") or []
    for item in error_stack if isinstance(error_stack, list) else []:
        if isinstance(item, dict):
            message = str(item.get("message") or "").strip()
            if message:
                sources.append(f"{item.get('module') or 'DEX'}:{item.get('code') or 'unknown'}:{message}")
    return sources


def _enrich_missing_reason(payload: dict[str, Any], reason: Any) -> str | None:
    sources = _not_executable_reason_sources(payload)
    original = str(reason).strip() if reason and str(reason).strip() else None
    if not sources:
        return original
    specific = "; ".join(dict.fromkeys(sources))
    if not original:
        return specific
    generic_markers = (
        "no_native_signed_profit",
        "native result has no signed borrow-token profit value",
        "zero placeholder does not prove",
    )
    if any(marker in original for marker in generic_markers):
        return specific
    if specific == original:
        return original
    return f"{specific} | {original}"


def _has_review_evidence(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    for key in (
        "live_onchain_evidence",
        "quote_analysis",
        "quoteAnalysis",
        "analysis",
    ):
        if isinstance(quote.get(key), dict):
            return True
    if isinstance(payload.get("live_onchain_evidence"), dict):
        return True
    if payload.get("blocked_reasons") or payload.get("errorStack"):
        return True
    if payload.get("reviewFailureKind") is not None or payload.get("reviewFailureReasons"):
        return True
    return bool(payload.get("review_failure") or payload.get("reviewFailure"))


def selected_profit_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted selected-profit view for a CoW attempt/review row.

    The native Rust preview writes camelCase fields on the attempt and nested
    evidence.  The storage layer persists the snake_case column view. A
    numeric ``0.0`` is not accepted as a review conclusion because it means the
    native/offline P&L was not resolved; it is surfaced read-only as
    ``concrete_pnl_unavailable`` without changing the persisted row.
    """
    if not isinstance(payload, dict):
        return {}
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    analysis = (
        quote.get("quote_analysis")
        or quote.get("quoteAnalysis")
        or quote.get("analysis")
    )
    if not isinstance(analysis, dict):
        analysis = {}
    live = (
        quote.get("live_onchain_evidence")
        or payload.get("live_onchain_evidence")
        or payload.get("review_summary", {}).get("onchain_evidence")
    )
    if not isinstance(live, dict):
        live = {}
    seen: dict[str, Any] = {}

    def merge(**values: Any) -> None:
        for key, value in values.items():
            if value is None or value == "":
                continue
            if key not in seen:
                seen[key] = value

    merge(**{
        "selectedProfitRaw": payload.get("selectedProfitRaw"),
        "selectedProfitAmount": payload.get("selectedProfitAmount"),
        "selectedProfitAmountSymbol": payload.get("selectedProfitAmountSymbol"),
        "selectedProfitSource": payload.get("selectedProfitSource"),
        "selectedProfitMissingReason": payload.get("selectedProfitMissingReason"),
    })
    merge(**{
        "selectedProfitRaw": live.get("selectedProfitRaw"),
        "selectedProfitAmount": live.get("selectedProfitAmount"),
        "selectedProfitAmountSymbol": live.get("selectedProfitAmountSymbol"),
        "selectedProfitSource": live.get("selectedProfitSource"),
        "selectedProfitMissingReason": live.get("selectedProfitMissingReason"),
    })
    merge(**{
        "selectedProfitRaw": analysis.get("selectedProfitRaw"),
        "selectedProfitAmount": analysis.get("selectedProfitAmount"),
        "selectedProfitAmountSymbol": analysis.get("selectedProfitAmountSymbol"),
        "selectedProfitSource": analysis.get("selectedProfitSource"),
        "selectedProfitMissingReason": analysis.get("selectedProfitMissingReason"),
    })
    size_analysis = (
        analysis.get("sizeAnalysis")
        or analysis.get("size_analysis")
        or result_size_analysis(payload)
    )
    if isinstance(size_analysis, dict):
        merge(
            selectedProfitRaw=size_analysis.get("selectedProfitRaw"),
        )
    cost_model = live.get("costModel") if isinstance(live.get("costModel"), dict) else {}
    if seen.get("selectedProfitAmount") is None:
        net_profit = cost_model.get("netProfitAmount")
        if net_profit is not None:
            merge(
                selectedProfitAmount=net_profit,
                selectedProfitAmountSymbol=(
                    cost_model.get("netProfitAmountSymbol")
                    or cost_model.get("borrowInputSymbol")
                ),
                selectedProfitSource=(
                    cost_model.get("netProfitReferenceSource")
                    or "native_contract_conservative_zero"
                ),
            )
    # A missing amount must stay missing.  A literal 0.0 is not a valid
    # review conclusion: it means the native/offline P&L was not resolved.
    if seen.get("selectedProfitAmount") is None:
        merge(selectedProfitSource="concrete_pnl_unavailable")
    if seen.get("selectedProfitAmount") is None:
        seen["selectedProfitSource"] = "concrete_pnl_unavailable"
    elif not seen.get("selectedProfitSource") and (
        seen.get("selectedProfitRaw") or seen.get("selectedProfitAmount") is not None
    ):
        seen["selectedProfitSource"] = "native_rust_contract_preview"
    amount_missing = seen.get("selectedProfitAmount") is None
    existing_reason = str(seen.get("selectedProfitMissingReason") or "").strip()
    generic_markers = (
        "no_native_signed_profit",
        "native result has no signed borrow-token profit value",
        "zero placeholder does not prove",
    )
    if not existing_reason or any(marker in existing_reason for marker in generic_markers):
        enriched = _enrich_missing_reason(payload, existing_reason or None)
        if enriched:
            seen["selectedProfitMissingReason"] = enriched
        elif amount_missing:
            seen["selectedProfitMissingReason"] = "no_native_signed_profit"
    amount = seen.get("selectedProfitAmount")
    zero_amount = False
    if amount is not None:
        try:
            zero_amount = float(amount) == 0.0
        except (TypeError, ValueError):
            zero_amount = False
    if zero_amount:
        seen["selectedProfitAmount"] = None
        seen["selectedProfitSource"] = "concrete_pnl_unavailable"
        reason = str(seen.get("selectedProfitMissingReason") or "").strip()
        zero_marker = (
            "selected profit amount resolved to zero; zero is not accepted "
            "as a valid review conclusion"
        )
        if not reason:
            seen["selectedProfitMissingReason"] = zero_marker
        elif zero_marker not in reason:
            seen["selectedProfitMissingReason"] = f"{reason} | {zero_marker}"
    return {key: seen.get(key) for key in SELECTED_PROFIT_CAMEL_KEYS}


def result_size_analysis(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for candidate in (
        payload.get("quote_analysis"),
        payload.get("quoteAnalysis"),
        payload.get("analysis"),
    ):
        if not isinstance(candidate, dict):
            continue
        size = candidate.get("sizeAnalysis") or candidate.get("size_analysis")
        if isinstance(size, dict):
            return size
    return None


def scope_consistency_reasons(row: dict[str, Any]) -> list[str]:
    """Return contradictions between top-level and nested evidence scope."""
    scope = infer_evidence_scope(row)
    reasons: list[str] = []

    def as_int(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    for key, nested_key, reason in (
        ("network", "network", "network_scope_mismatch"),
        ("chain_id", "chainId", "chain_id_scope_mismatch"),
    ):
        top = row.get(key)
        nested = evidence.get(nested_key)
        if top not in (None, "") and nested not in (None, ""):
            top_value = str(top).strip().lower() if key == "network" else as_int(top)
            nested_value = str(nested).strip().lower() if key == "network" else as_int(nested)
            if top_value != nested_value:
                reasons.append(reason)

    top_market = row.get("market_id")
    nested_market = evidence.get("market_id")
    if top_market and nested_market and str(top_market).strip().lower() != str(nested_market).strip().lower():
        reasons.append("market_id_scope_mismatch")
    if scope.network == "" and (row.get("network") or evidence.get("network")):
        reasons.append("network_scope_unreadable")
    return reasons


__all__ = ["scope_consistency_reasons"]
