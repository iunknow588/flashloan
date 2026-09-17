"""DEX-owned adapters for the reusable module error stack.

The reusable stack only stores ``module/code/message``.  This module owns the
DEX interpretation of route/evidence statuses and is the boundary used by
DEX surfaces before data is sent to an API or page.
"""

from __future__ import annotations

from typing import Any, Mapping

from core.error_registry import ErrorCode
from core.error_stack import ErrorStack, ModuleError


_STATUS_CODES = {
    "live_quote_failed": ErrorCode.DEX_LIVE_QUOTE_FAILED,
    "quote_failed": ErrorCode.DEX_LIVE_QUOTE_FAILED,
    "rpc_verification_failed": ErrorCode.DEX_RPC_FAILED,
    "rpc_verification_timeout": ErrorCode.DEX_RPC_FAILED,
    "adapter_required": ErrorCode.DEX_GATE_NOT_READY,
    "missing_trigger_block": ErrorCode.DEX_GATE_NOT_READY,
    "pool_not_initialized": ErrorCode.DEX_POOL_NOT_INITIALIZED,
    "not_enough_liquidity": ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY,
    "route_unprofitable": ErrorCode.DEX_SIZE_NOT_PROFITABLE,
    "not_profitable_after_costs": ErrorCode.DEX_SIZE_NOT_PROFITABLE,
    "pool_insufficient_for_profitable_execution": ErrorCode.DEX_GATE_NOT_READY,
    "slippage_limit_exceeded": ErrorCode.DEX_GATE_NOT_READY,
    "live_evidence_deferred": ErrorCode.DEX_GATE_NOT_READY,
    "live_quote_reference_only": ErrorCode.DEX_GATE_NOT_READY,
    "requires_live_quoter": ErrorCode.DEX_GATE_NOT_READY,
    "sdk_adapter_required": ErrorCode.DEX_GATE_NOT_READY,
    "candidate_pair_not_found": ErrorCode.DEX_ROUTE_INVALID,
    "price_impact_limit_exceeded": ErrorCode.DEX_GATE_NOT_READY,
    "not_all_hops_quoted": ErrorCode.DEX_GATE_NOT_READY,
    "route_not_closed": ErrorCode.DEX_GATE_NOT_READY,
    "token_continuity_failed": ErrorCode.DEX_GATE_NOT_READY,
}


def _rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, ErrorStack):
        return value.as_list()
    if isinstance(value, Mapping):
        raw = value.get("errorStack")
        return list(raw) if isinstance(raw, list) else []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _error_rows(value: object) -> list[dict[str, Any]]:
    return [row for row in _rows(value) if int(row.get("code") or 0) != 0]


def _first_child_error(evidence: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(evidence, Mapping):
        return []
    for hop in evidence.get("hops") or []:
        if isinstance(hop, Mapping):
            if hop.get("liveQuoteError") or str(hop.get("status") or "").strip().lower() in {
                "live_quote_failed",
                "quote_failed",
                "pool_not_initialized",
                "not_enough_liquidity",
                "adapter_required",
                "live_evidence_deferred",
            }:
                errors = _error_rows(hop.get("errorStack"))
                if errors:
                    return errors
    direct = _error_rows(evidence.get("errorStack"))
    if direct:
        return direct
    for hop in evidence.get("hops") or []:
        if isinstance(hop, Mapping):
            errors = _error_rows(hop.get("errorStack"))
            if errors:
                return errors
    return []


def route_error_stack(
    *,
    status: object,
    message: object = None,
    evidence: Mapping[str, Any] | None = None,
    blockers: list[object] | None = None,
    existing: object = None,
) -> list[dict[str, Any]]:
    """Return the DEX route boundary stack.

    Child evidence errors are reused verbatim.  Otherwise the DEX route
    module creates one body from its own status.  A successful route exposes a
    single DEX OK element; internal successful hops are never appended.
    """

    status_text = str(status or "").strip().lower()
    stack = ErrorStack()
    if status_text in {"", "live_verified", "quoted", "complete", "profitable_amount_found"}:
        stack.mark_ok(ModuleError(module="DEX", code=0, message="OK"))
        return stack.as_list()

    failed_hop = None
    if isinstance(evidence, Mapping):
        failed_hop = next(
            (
                hop
                for hop in evidence.get("hops") or []
                if isinstance(hop, Mapping)
                and (
                    hop.get("liveQuoteError")
                    or str(hop.get("status") or "").strip().lower()
                    in {
                        "live_quote_failed",
                        "quote_failed",
                        "pool_not_initialized",
                        "live_evidence_deferred",
                        "not_enough_liquidity",
                        "adapter_required",
                    }
                )
            ),
            None,
        )
    if isinstance(failed_hop, Mapping):
        hop = failed_hop.get("hop") or "?"
        pair = f"{failed_hop.get('fromSymbol') or '?'}->{failed_hop.get('toSymbol') or '?'}"
        reason = next((item for item in blockers or [] if str(item).strip()), None)
        if not reason:
            reason = str(message or status_text or "DEX route failed")
        text = reason
    else:
        reason = message
        if reason in (None, "") and blockers:
            reason = next((item for item in blockers if str(item).strip()), None)
        text = str(reason or status_text or "DEX route failed")

    stack.push(
        ModuleError(
            module="DEX",
            code=int(_STATUS_CODES.get(status_text, ErrorCode.DEX_GATE_NOT_READY)),
            message=text,
        )
    )

    child_errors = _first_child_error(evidence)
    if child_errors:
        for row in child_errors:
            stack.push(
                ModuleError(
                    module=str(row.get("module") or "DEX"),
                    code=int(row.get("code") or ErrorCode.DEX_INTERNAL),
                    message=str(row.get("message") or "DEX child error"),
                )
            )
        return stack.as_list()

    inherited_errors = _error_rows(existing)
    if inherited_errors:
        for row in inherited_errors:
            stack.push(
                ModuleError(
                    module=str(row.get("module") or "DEX"),
                    code=int(row.get("code") or ErrorCode.DEX_INTERNAL),
                    message=str(row.get("message") or "DEX existing error"),
                )
            )
    return stack.as_list()


def stack_from_dex_payload(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize one route/evidence payload for page/API consumers."""

    payload = payload if isinstance(payload, Mapping) else {}
    evidence = payload.get("live_onchain_evidence") or payload.get("liveOnchainEvidence")
    return route_error_stack(
        status=payload.get("status") or payload.get("quoteStatus") or payload.get("evidenceStatus"),
        message=payload.get("failureReason") or payload.get("error"),
        evidence=evidence if isinstance(evidence, Mapping) else None,
        blockers=list(payload.get("blockers") or payload.get("blocked_reasons") or []),
        existing=payload.get("errorStack"),
    )


__all__ = ["route_error_stack", "stack_from_dex_payload"]
