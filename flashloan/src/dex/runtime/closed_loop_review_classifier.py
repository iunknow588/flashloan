"""Shared review classification for closed-loop worker results and pages.

The worker pool and the Web surface must agree on whether the newest review of
one closed loop is a completed review, a failed review, or an RPC-deferred
review.  Keeping the classifier here prevents two independent implementations
from drifting.
"""

from __future__ import annotations

from typing import Any

from dex.runtime.closed_loop_identity import (
    physical_loop_key_from_addresses,
    physical_loop_key_from_route,
)


RPC_BUSY_MARKERS = (
    "network is busy",
    "please try again in a moment",
    "too many requests",
    "rate limit",
    "rate limited",
    "request rate",
    "429",
    "service unavailable",
    "temporarily unavailable",
    "overloaded",
    "server busy",
    "busy, please",
)

RPC_OFFLINE_MARKERS = (
    "rpc is disabled because the chain link is dynamically offline",
    "automatic review cannot run",
    "chain link is dynamically offline",
)


def is_rpc_busy_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in RPC_BUSY_MARKERS)


def row_failure_reason_text(row: dict[str, Any]) -> str:
    """Collect human-readable failure/blocker text from a submission row."""
    parts: list[str] = []
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
    auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
    broadcast_gate = auto_execute.get("broadcastGate") if isinstance(auto_execute.get("broadcastGate"), dict) else {}
    for value in (
        row.get("blocked_reasons"),
        row.get("reasons"),
        row.get("error"),
        row.get("detail"),
        row.get("errorDetail"),
        auto_execute.get("reason"),
        auto_result.get("error"),
        auto_result.get("detail"),
        precheck.get("reasons"),
        evidence.get("blockers"),
        evidence.get("blocked_reasons"),
        evidence.get("reasons"),
        evidence.get("reviewFailureKind"),
        evidence.get("reviewFailureReasons"),
        evidence.get("reviewFailureDetail"),
        evidence.get("failureReason"),
        evidence.get("error"),
        evidence.get("detail"),
    ):
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if str(item).strip())
        else:
            parts.append(str(value))
    if bool(auto_execute.get("broadcastResultMissing")):
        gate_reasons = " | ".join(str(item) for item in (broadcast_gate.get("reasons") or []) if str(item).strip())
        parts.append(
            "broadcast_result_missing: broadcastGate open "
            "(broadcastEnabled=true), no broadcast result in worker result"
        )
        if gate_reasons:
            parts.append(f"broadcastGate reasons: {gate_reasons}")
    for stack in (evidence.get("errorStack") or []):
        if isinstance(stack, dict) and stack.get("message"):
            parts.append(str(stack["message"]))
    for hop in evidence.get("hops") or []:
        if not isinstance(hop, dict):
            continue
        for key in ("liveQuoteError", "error", "blockers"):
            value = hop.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple)):
                parts.extend(str(item) for item in value if str(item).strip())
            else:
                parts.append(str(value))
    return " | ".join(part for part in parts if part)


def is_review_deferred_row(row: dict[str, Any]) -> bool:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    status = str(evidence.get("status") or evidence.get("executionTrust") or "").strip().lower()
    kind = str(
        evidence.get("errorKind")
        or evidence.get("reviewFailureKind")
        or ""
    ).strip().lower()
    auto_reason = str(
        (row.get("auto_execute") or {}).get("reason")
        or (row.get("auto_execute") or {}).get("auto_execute", {}).get("reason")
        or ""
    ).strip().lower()
    row_status = str(
        row.get("state")
        or row.get("status")
        or row.get("submission_status")
        or ""
    ).strip().lower()
    if status in {"rpc_busy", "live_evidence_deferred"}:
        return True
    if kind == "rpc_busy" or auto_reason == "rpc_busy" or row_status in {"rpc_busy", "live_evidence_deferred"}:
        return True
    reason_text = row_failure_reason_text(row)
    if is_rpc_busy_text(reason_text):
        return True
    lowered = reason_text.lower()
    return any(marker in lowered for marker in RPC_OFFLINE_MARKERS)


def review_row_kind(row: dict[str, Any]) -> str:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    status = str(evidence.get("status") or evidence.get("executionTrust") or row.get("status") or "").strip().lower()
    state_text = str(
        row.get("state")
        or row.get("status")
        or row.get("submission_status")
        or ""
    ).strip().lower()
    reason_text = row_failure_reason_text(row).lower()
    if bool(evidence.get("readyForTransactionPreflight")) or state_text in {
        "ready_not_submitted",
        "ready_to_submit",
        "checks_passed_order_disabled",
        "limit_order_ready_not_submitted",
        "limit_order_ready_to_submit",
    }:
        return "ready_not_submitted"
    if is_review_deferred_row(row):
        return "rpc_busy_deferred"
    if status in {
        "route_unprofitable",
        "no_profitable_route",
        "not_profitable_after_costs",
    } or any(marker in reason_text for marker in (
        "noprofitable",
        "not profitable",
        "unprofitable",
        "no_profitable_route",
        "route_unprofitable",
    )):
        return "confirmed_no_profit"
    if status in {
        "rpc_unavailable",
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "live_quote_failed",
        "quote_failed",
        "not_live_verified",
        "review_failed",
        "quote_unavailable",
    } or any(marker in reason_text for marker in (
        "previewbestpooldetailed unavailable",
        "previewbestpool unavailable",
        "numeric values unavailable",
        "quote_unavailable",
        "review_failed",
        "rpc unavailable",
        "rpc failed",
        "quote failed",
        "live quote failed",
        "not live verified",
    )):
        return "review_failed"
    return "other"


def loop_key_from_pair_text(pair_text: str) -> str | None:
    """Return the legacy unordered symbol group for migration diagnostics.

    Native Rust identities are ``anchor|bridge_a|bridge_b`` with the two bridge
    tokens sorted. This value must not be written to directional runtime state.
    """
    tokens = [
        str(item).strip().lower()
        for item in str(pair_text or "").split("->")
        if str(item).strip()
    ]
    if len(tokens) == 4 and tokens[0] == tokens[-1]:
        return "|".join([tokens[0], *sorted(tokens[1:3])])
    return None


def _row_route_address_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("address") or value.get("token") or "").strip()
    return str(value or "").strip()


def _row_route_addresses(row: dict[str, Any]) -> list[str]:
    for key in ("routeAddresses", "route_addresses"):
        value = row.get(key)
        if isinstance(value, list):
            return [_row_route_address_text(item) for item in value if _row_route_address_text(item)]
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    for key in ("routeAddresses", "route_addresses", "routeTokenPath", "route_token_path"):
        value = quote.get(key)
        if isinstance(value, list):
            return [_row_route_address_text(item) for item in value if _row_route_address_text(item)]
    analysis = quote.get("quote_analysis") if isinstance(quote.get("quote_analysis"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    for container in (analysis, evidence):
        for key in ("routeAddresses", "route_addresses"):
            value = container.get(key)
            if isinstance(value, list):
                return [_row_route_address_text(item) for item in value if _row_route_address_text(item)]
    return []


def loop_key_from_review_row(row: dict[str, Any]) -> str | None:
    """Build the durable directional key from trusted route addresses.

    A third-party token can return any ``symbol()`` it likes.  When the review
    row has a real token path, the physical address path is the identity and
    preserves the Y -> Z direction. Legacy unresolved symbol groups are only
    returned when no physical path is available, for migration diagnostics.
    """
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    loop_key = physical_loop_key_from_route(row)
    if not loop_key:
        loop_key = physical_loop_key_from_route(quote)
    if not loop_key:
        loop_key = physical_loop_key_from_addresses(_row_route_addresses(row))
    if loop_key:
        return loop_key
    route_symbols = [
        str(item).strip().upper()
        for item in (
            row.get("routeSymbols")
            or row.get("route_path")
            or quote.get("routeSymbols")
            or quote.get("path")
            or []
        )
        if str(item).strip()
    ]
    if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
        tokens = [
            str(item).strip().upper()
            for item in str(row.get("pair") or "").split("->")
            if str(item).strip()
        ]
        if len(tokens) == 4 and tokens[0] == tokens[-1]:
            route_symbols = tokens
        else:
            return None
    route_addresses = _row_route_addresses(row)
    if len(route_addresses) != len(route_symbols):
        try:
            chain_id = int(
                row.get("chain_id")
                or quote.get("chain_id")
                or (quote.get("live_onchain_evidence") or {}).get("chainId")
                or 0
            )
        except (TypeError, ValueError):
            chain_id = 0
        from market.binance_market.dex_route_helpers import _official_route_addresses

        route_addresses = _official_route_addresses(route_symbols, chain_id=chain_id)
        if len(route_addresses) != len(route_symbols):
            return None
    try:
        chain_id = int(row.get("chain_id") or quote.get("chain_id") or 0)
    except (TypeError, ValueError):
        chain_id = 0
    from market.binance_market.dex_route_helpers import trusted_symbol_from_address

    normalized: list[str] = []
    for symbol, address in zip(route_symbols, route_addresses):
        trusted = trusted_symbol_from_address(address, chain_id=chain_id)
        normalized.append(str(trusted or f"unresolved:{str(address).strip().lower()}").upper())
    return loop_key_from_pair_text(" -> ".join(normalized))


__all__ = [
    "RPC_BUSY_MARKERS",
    "RPC_OFFLINE_MARKERS",
    "is_review_deferred_row",
    "is_rpc_busy_text",
    "loop_key_from_pair_text",
    "loop_key_from_review_row",
    "review_row_kind",
    "row_failure_reason_text",
]
