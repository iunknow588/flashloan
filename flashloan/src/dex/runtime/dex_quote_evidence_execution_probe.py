from __future__ import annotations

import os
from typing import Any, Callable

from dex.runtime.dex_quote_evidence_common import (
    _as_dict,
    _dex_error_stack_from_status,
    _evidence_module_or_none,
    _has_not_enough_liquidity_blocker,
    _is_not_enough_liquidity_error,
    _is_pool_not_initialized_error,
    _is_rpc_verification_error,
    _text,
)

DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS = 24

QuoteHopProvider = Callable[..., dict[str, Any]]

def _probe_robinhood_testnet_route_execution_size(
    route: dict[str, Any],
    *,
    payload: dict[str, Any] | None,
    chain_id: int | None,
    observed_at: str,
    trigger_context: dict[str, Any],
    quote_hop_provider: QuoteHopProvider | None,
    requested_amount_raw: int,
    initial_probe_amount_raw: int | None = None,
    max_attempts: int | None = None,
    min_probe_amount_raw: int | None = None,
) -> dict[str, Any]:
    max_attempts = max(1, int(max_attempts or os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_ATTEMPTS", str(DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS)) or DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS))
    min_probe_amount_raw = max(1, int(min_probe_amount_raw or os.getenv("DEX_V4_EXECUTION_SIZE_MIN_PROBE_AMOUNT_RAW", "1") or 1))
    max_probe_multiplier = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_MULTIPLIER", "64") or 64))
    target_amount_raw = max(1, int(requested_amount_raw))
    initial_probe_amount_raw = max(1, int(initial_probe_amount_raw or target_amount_raw))
    max_probe_amount_raw = min((1 << 128) - 1, max(target_amount_raw, min_probe_amount_raw) * max_probe_multiplier)
    attempts = 0
    candidate_rows: list[dict[str, Any]] = []

    def _all_hop_gates_ok(evidence: dict[str, Any]) -> bool:
        validation = _as_dict(evidence.get("validation"))
        return bool(
            validation.get("allHopsQuoted", True) is True
            and validation.get("routeClosed", True) is True
            and validation.get("tokenContinuity", True) is True
            and validation.get("profitableAfterCosts", True) is True
        )

    def _passes(evidence: dict[str, Any]) -> bool:
        status = _text(evidence.get("status")).lower()
        # During a size search each candidate is built with
        # skip_execution_size_probe=True, so a fully-quoted candidate can
        # legitimately arrive as static_reference_pending_live_review (the
        # best amount confirmation is what this outer probe is trying to
        # produce). Treat that as a usable lower-bound candidate.
        return bool(
            (
                status
                in {
                    "live_verified",
                    "slippage_limit_exceeded",
                    "route_unprofitable",
                    "static_reference_pending_live_review",
                }
            )
            and _all_hop_gates_ok(evidence)
        )

    def _failure_reason(evidence: dict[str, Any]) -> str | None:
        validation = _as_dict(evidence.get("validation"))
        status = _text(evidence.get("status")).lower()
        if _all_hop_gates_ok(evidence):
            return None
        if validation.get("profitableAfterCosts") is False and validation.get("allHopsQuoted") is True:
            return "not_profitable_after_costs"
        if status not in {
            "live_verified",
            "slippage_limit_exceeded",
            "route_unprofitable",
            "static_reference_pending_live_review",
        }:
            return status or "live_quote_failed"
        if validation.get("allHopsQuoted") is not True:
            return "not_all_hops_quoted"
        if validation.get("routeClosed") is not True:
            return "route_not_closed"
        if validation.get("tokenContinuity") is not True:
            return "token_continuity_failed"
        if validation.get("profitableAfterCosts") is not True:
            return "not_profitable_after_costs"
        return None

    def _profit_amount(evidence: dict[str, Any]) -> float | None:
        costs = _as_dict(evidence.get("costModel"))
        try:
            profit = float(costs.get("netProfitAmount"))
        except (TypeError, ValueError):
            return None
        return profit

    def _evaluate(amount_raw: int) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        module = _evidence_module_or_none()
        build_route_onchain_evidence = (
            getattr(module, "build_route_onchain_evidence", None)
            if module is not None
            else None
        )
        if build_route_onchain_evidence is None:
            from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence

        return build_route_onchain_evidence(
            route,
            payload=payload,
            chain_id=chain_id,
            observed_at=observed_at,
            trigger_context=trigger_context,
            quote_hop_provider=quote_hop_provider,
            borrow_input_amount_override_raw=int(amount_raw),
            skip_execution_size_probe=True,
        )

    def _record(amount_raw: int, evidence: dict[str, Any]) -> tuple[bool, str | None, float | None]:
        validation = _as_dict(evidence.get("validation"))
        costs = _as_dict(evidence.get("costModel"))
        passed = _passes(evidence)
        reason = None if passed else _failure_reason(evidence)
        profit = _profit_amount(evidence)
        candidate_rows.append(
            {
                "attempt": attempts,
                "amountRaw": str(amount_raw),
                "status": "profitable_and_within_limits" if passed else "quoted_but_not_eligible",
                "allHopsQuoted": validation.get("allHopsQuoted") is True,
                "allHopsWithinPriceImpactLimit": validation.get("allHopsWithinPriceImpactLimit") is True,
                "profitableAfterCosts": validation.get("profitableAfterCosts") is True,
                "netProfitAmount": costs.get("netProfitAmount"),
                "profitAmount": profit,
                "netProfitAmountBps": costs.get("netProfitAmountBps"),
                "blockers": list(evidence.get("blockers") or []),
                "failureReason": reason,
                "errorStack": evidence.get("errorStack") or _dex_error_stack_from_status(
                    "live_verified" if passed else (reason or "not_profitable_after_costs"),
                    message=reason or "OK",
                    details={"attempt": attempts, "amountRaw": str(amount_raw)},
                    flow="size_probe",
                    node_id="probe_attempt",
                ),
            }
        )
        return passed, reason, profit

    def _stop_without_size(reason: str, *, raw_error: str | None = None) -> dict[str, Any]:
        search_status = (
            "not_profitable_after_costs"
            if reason == "not_profitable_after_costs"
            else reason
        )
        failure_reason = reason
        if raw_error:
            failure_reason = f"{reason}: {raw_error}"
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": search_status,
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "initialProbeAmountRaw": str(initial_probe_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": failure_reason,
            "errorStack": _dex_error_stack_from_status(
                search_status,
                message=failure_reason,
                raw_error=raw_error,
                details={"reason": reason, "searchStatus": search_status},
                flow="size_probe",
                node_id="probe_quote",
            ),
        }

    lower_bound: int | None = None
    upper_bound: int | None = None
    first_ineligible_amount_raw: int | None = None
    expansion_stop_reason: str | None = None
    last_failure_reason: str | None = None
    selected_amount_raw: int | None = None
    selected_profit_amount: float | None = None
    probe = initial_probe_amount_raw
    while attempts < max_attempts and probe >= min_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason, profit = _record(probe, evidence)
            if passed:
                lower_bound = probe
                selected_amount_raw = probe
                selected_profit_amount = profit
                break
            last_failure_reason = reason
            if reason not in {"not_profitable_after_costs"}:
                if reason == "not_enough_liquidity" or (reason == "live_quote_failed" and _has_not_enough_liquidity_blocker(evidence)):
                    first_ineligible_amount_raw = probe
                    last_failure_reason = "not_enough_liquidity"
                else:
                    return _stop_without_size(reason or "not_eligible")
            else:
                first_ineligible_amount_raw = probe
        except Exception as exc:
            quote_failure_reason = _is_rpc_verification_error(exc) or ("pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed"))
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": quote_failure_reason,
                    "error": str(exc),
                    "errorStack": _dex_error_stack_from_status(
                        quote_failure_reason,
                        message=str(exc),
                        raw_error=str(exc),
                        details={"attempt": attempts, "amountRaw": str(probe)},
                        flow="size_probe",
                        node_id="probe_attempt",
                    ),
                }
            )
            if quote_failure_reason in {"live_quote_failed", "not_enough_liquidity"} and _is_not_enough_liquidity_error(exc):
                first_ineligible_amount_raw = probe
                last_failure_reason = quote_failure_reason
                next_probe = max(min_probe_amount_raw, probe // 2)
                if next_probe == probe or next_probe < min_probe_amount_raw:
                    return _stop_without_size("not_enough_liquidity", raw_error=str(exc))
                probe = next_probe
                continue
            else:
                return _stop_without_size(quote_failure_reason, raw_error=str(exc))
        if lower_bound is not None:
            break
        next_probe = max(min_probe_amount_raw, probe // 2)
        if next_probe == probe:
            break
        probe = next_probe

    if lower_bound is None or selected_amount_raw is None:
        search_status = (
            "not_profitable_after_costs"
            if last_failure_reason == "not_profitable_after_costs"
            else "rpc_verification_timeout"
            if last_failure_reason == "rpc_verification_timeout"
            else "rpc_verification_failed"
            if last_failure_reason == "rpc_verification_failed"
            else "pool_not_initialized"
            if last_failure_reason == "pool_not_initialized"
            else "not_enough_liquidity"
            if last_failure_reason == "not_enough_liquidity"
            else "live_quote_failed"
        )
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": search_status,
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "initialProbeAmountRaw": str(initial_probe_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": f"after up to {max_attempts} attempts, no amount passed live quote and positive net-profit gates",
            "errorStack": _dex_error_stack_from_status(
                search_status,
                message=search_status,
                details={"lastFailureReason": last_failure_reason, "searchStatus": search_status},
                flow="size_probe",
                node_id="probe_quote",
            ),
        }

    if upper_bound is None and first_ineligible_amount_raw is not None and first_ineligible_amount_raw > lower_bound:
        upper_bound = first_ineligible_amount_raw

    if upper_bound is None:
        probe = min(max_probe_amount_raw, max(selected_amount_raw * 2, selected_amount_raw + 1))
        while attempts < max_attempts and probe <= max_probe_amount_raw:
            try:
                evidence = _evaluate(probe)
                passed, reason, profit = _record(probe, evidence)
                if passed and profit is not None and profit > selected_profit_amount:
                    lower_bound = probe
                    selected_amount_raw = probe
                    selected_profit_amount = profit
                    upper_bound = None
                    expansion_stop_reason = None
                    first_ineligible_amount_raw = None
                    if probe >= max_probe_amount_raw:
                        break
                    next_probe = min(max_probe_amount_raw, max(probe * 2, probe + 1))
                    if next_probe == probe:
                        break
                    probe = next_probe
                    continue
                expansion_stop_reason = reason or "not_improving_profit"
                first_ineligible_amount_raw = probe
                upper_bound = probe
                break
            except Exception as exc:
                quote_failure_reason = _is_rpc_verification_error(exc) or ("pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed"))
                candidate_rows.append(
                    {
                        "attempt": attempts,
                        "amountRaw": str(probe),
                        "status": quote_failure_reason,
                        "error": str(exc),
                        "errorStack": _dex_error_stack_from_status(
                            quote_failure_reason,
                            message=str(exc),
                            raw_error=str(exc),
                            details={"attempt": attempts, "amountRaw": str(probe)},
                            flow="size_probe",
                            node_id="probe_attempt",
                        ),
                    }
                )
                first_ineligible_amount_raw = probe
                expansion_stop_reason = quote_failure_reason
                upper_bound = probe
                break

    if upper_bound is not None and lower_bound is not None:
        while upper_bound - lower_bound > 1 and attempts < max_attempts:
            mid = (upper_bound + lower_bound) // 2
            try:
                evidence = _evaluate(mid)
                passed, _, profit = _record(mid, evidence)
                if passed and profit is not None and profit > selected_profit_amount:
                    selected_amount_raw = mid
                    selected_profit_amount = profit
                    lower_bound = mid
                    break
                else:
                    upper_bound = mid
            except Exception as exc:
                quote_failure_reason = _is_rpc_verification_error(exc) or ("pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed"))
                candidate_rows.append(
                    {
                        "attempt": attempts,
                        "amountRaw": str(mid),
                        "status": quote_failure_reason,
                        "error": str(exc),
                        "errorStack": _dex_error_stack_from_status(
                            quote_failure_reason,
                            message=str(exc),
                            raw_error=str(exc),
                            details={"attempt": attempts, "amountRaw": str(mid)},
                            flow="size_probe",
                            node_id="probe_attempt",
                        ),
                    }
                )
                upper_bound = mid

    max_executable_confirmed = bool(
        upper_bound is not None
        and lower_bound is not None
        and upper_bound > lower_bound
        and selected_amount_raw is not None
        and selected_amount_raw < max_probe_amount_raw
    )
    search_status = (
        "profitable_amount_found"
        if max_executable_confirmed
        else "profitable_amount_found_upper_bound_unconfirmed"
    )
    return {
        "status": "route_execution_size_probe_complete",
        "searchStatus": search_status,
        "probeAttempts": attempts,
        "maxProbeAttempts": max_attempts,
        "requestedAmountRaw": str(target_amount_raw),
        "initialProbeAmountRaw": str(initial_probe_amount_raw),
        "selectedProfitableAmountRaw": str(selected_amount_raw),
        "maxExecutableAmountRaw": str(selected_amount_raw),
        "bestProfitAmountRaw": str(selected_amount_raw),
        "bestProfitAmount": selected_profit_amount,
        "selectionBasis": "max_absolute_net_profit_amount",
        "selectionReason": expansion_stop_reason or "not_improving_profit",
        "maxExecutableAmountConfirmed": max_executable_confirmed,
        "maxExecutableUpperBoundRaw": str(upper_bound) if upper_bound is not None else None,
        "firstFailureAmountRaw": str(upper_bound or first_ineligible_amount_raw) if (upper_bound or first_ineligible_amount_raw) is not None else (str(target_amount_raw) if lower_bound != target_amount_raw else None),
        "attempts": candidate_rows,
        "failureReason": None,
        "expansionStopReason": expansion_stop_reason,
        "errorStack": _dex_error_stack_from_status(
            "live_verified",
            message="OK",
            flow="size_probe",
            node_id="complete",
        ),
    }
