"""Pure review-summary mapping helpers for CoW execution storage."""

from __future__ import annotations

from typing import Any


def _market_state_summary(market_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": market_state.get("observed_at"),
        "window_seconds": market_state.get("window_seconds"),
        "price_source": market_state.get("price_source"),
        "market_state_source": market_state.get("market_state_source"),
        "fallback_reason": market_state.get("fallback_reason"),
        "cow_filter": market_state.get("cow_filter"),
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _review_decimal(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _review_change_percent(item: dict[str, Any]) -> Any:
    raw = item.get("change_percent")
    start = _review_decimal(item.get("start_price"))
    current = _review_decimal(item.get("current_price"))
    if (raw is None or raw == "" or str(raw) == "0" or str(raw) == "0.0") and start and current:
        return (current - start) / start * 100
    return raw


def _review_error_summary(error: Any) -> dict[str, Any]:
    text = str(error or "").strip()
    lowered = text.lower()
    if not text:
        return {"type": None, "display": None, "raw": None}
    if "403 error" in lowered and ("cloudfront" in lowered or "request blocked" in lowered):
        return {
            "type": "quote_api_http_403_cloudfront_request_blocked",
            "display": "CoW quote API HTTP 403: CloudFront request blocked",
            "raw": text,
        }
    if "timed out" in lowered or "timeout" in lowered:
        return {"type": "quote_api_timeout", "display": "CoW quote API timeout", "raw": text}
    if "ssl" in lowered:
        return {"type": "quote_api_ssl_error", "display": "CoW quote API SSL error", "raw": text}
    if "http" in lowered or "<html" in lowered or "<!doctype" in lowered:
        return {"type": "quote_api_http_error", "display": "CoW quote API HTTP error", "raw": text}
    return {"type": "quote_api_error", "display": text[:240], "raw": text}


def _review_market_prices(quote: dict[str, Any]) -> list[dict[str, Any]]:
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    prices = plan.get("market_prices") if isinstance(plan, dict) else None
    if isinstance(prices, list) and prices:
        return [{**item, "change_percent": _review_change_percent(item)} for item in prices if isinstance(item, dict)]
    rows = []
    for prefix in ("x", "y"):
        symbol = quote.get(f"{prefix}_symbol") or quote.get(f"{prefix}_base_symbol")
        if symbol:
            rows.append(
                {
                    "symbol": symbol,
                    "base_symbol": quote.get(f"{prefix}_base_symbol"),
                    "start_price": quote.get(f"{prefix}_start_price"),
                    "current_price": quote.get(f"{prefix}_current_price"),
                    "change_percent": _review_change_percent(
                        {
                            "start_price": quote.get(f"{prefix}_start_price"),
                            "current_price": quote.get(f"{prefix}_current_price"),
                            "change_percent": quote.get(f"{prefix}_change_percent"),
                        }
                    ),
                }
            )
    return rows


def _review_plan_steps(quote: dict[str, Any]) -> list[dict[str, Any]]:
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return []
    summaries = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        summaries.append(
            {
                "step": step.get("step"),
                "from_symbol": step.get("from_symbol"),
                "to_symbol": step.get("to_symbol"),
                "input_amount": _first_present(step.get("query_sell_amount_before_fee"), step.get("input_amount")),
                "query_output_amount": step.get("query_buy_amount_after_fee"),
                "target_output_amount": step.get("target_output_amount"),
                "min_output_amount": step.get("min_output_amount"),
                "price_candidates": step.get("price_candidates") or [],
                "rate_candidates": step.get("rate_candidates") or [],
                "query_price_usd_per_token": step.get("query_price_usd_per_token"),
                "query_exchange_rate": step.get("query_exchange_rate"),
                "query_price_position": step.get("query_price_position"),
                "query_rate_position": step.get("query_rate_position"),
                "query_window_timing": step.get("query_window_timing"),
                "query_guard_analysis": step.get("query_guard_analysis"),
                "selected_target_source": step.get("selected_target_source"),
                "selected_target_price_usd_per_token": step.get("selected_target_price_usd_per_token"),
                "selected_target_exchange_rate": step.get("selected_target_exchange_rate"),
                "acceptable_slippage_price_usd_per_token": step.get("acceptable_slippage_price_usd_per_token"),
                "acceptable_slippage_exchange_rate": step.get("acceptable_slippage_exchange_rate"),
                "slippage_bps": step.get("slippage_bps"),
                "selection_rule": step.get("selection_rule") or step.get("price_compare_rule"),
            }
        )
    return summaries


def _review_address(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 42 and text.lower().startswith("0x"):
        return text
    return ""


def _review_hop_token_in(step: dict[str, Any]) -> str:
    return _review_address(
        _first_present(
            step.get("tokenIn"),
            step.get("token_in"),
            step.get("tokenInAddress"),
            step.get("token_in_address"),
            step.get("fromAddress"),
            step.get("from_address"),
        )
    )


def _review_hop_token_out(step: dict[str, Any]) -> str:
    return _review_address(
        _first_present(
            step.get("tokenOut"),
            step.get("token_out"),
            step.get("tokenOutAddress"),
            step.get("token_out_address"),
            step.get("toAddress"),
            step.get("to_address"),
        )
    )


def _review_route_addresses_from_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    addresses = [_review_address(item.get("address") if isinstance(item, dict) else item) for item in value]
    return [address for address in addresses if address]


def _review_route_addresses_from_hops(hops: list[dict[str, Any]]) -> list[str]:
    if not hops:
        return []
    first = _review_hop_token_in(hops[0])
    if not first:
        return []
    addresses = [first]
    for step in hops:
        token_out = _review_hop_token_out(step)
        if not token_out:
            return []
        addresses.append(token_out)
    return addresses


def _review_hops_from_attempt(row: dict[str, Any], quote: dict[str, Any], live_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = quote.get("quote_analysis") if isinstance(quote.get("quote_analysis"), dict) else {}
    for value in (
        live_evidence.get("hops"),
        quote.get("hops"),
        analysis.get("steps"),
        row.get("hops"),
    ):
        if isinstance(value, list) and any(isinstance(step, dict) for step in value):
            return [step for step in value if isinstance(step, dict)]
    return []


def _review_route_addresses_from_attempt(
    row: dict[str, Any],
    quote: dict[str, Any],
    live_evidence: dict[str, Any],
    hops: list[dict[str, Any]],
) -> tuple[list[str], str]:
    analysis = quote.get("quote_analysis") if isinstance(quote.get("quote_analysis"), dict) else {}
    route = quote.get("route") if isinstance(quote.get("route"), dict) else {}
    for source, value in (
        ("row.route_addresses", row.get("route_addresses")),
        ("row.routeAddresses", row.get("routeAddresses")),
        ("quote.route_addresses", quote.get("route_addresses")),
        ("quote.routeAddresses", quote.get("routeAddresses")),
        ("quote.route_token_path", quote.get("route_token_path")),
        ("quote.routeTokenPath", quote.get("routeTokenPath")),
        ("quote.route.pathAddresses", route.get("pathAddresses")),
        ("quote.route.path_addresses", route.get("path_addresses")),
        ("quote_analysis.routeAddresses", analysis.get("routeAddresses")),
        ("live_onchain_evidence.routeAddresses", live_evidence.get("routeAddresses")),
        ("live_onchain_evidence.route_addresses", live_evidence.get("route_addresses")),
    ):
        addresses = _review_route_addresses_from_list(value)
        if addresses:
            return addresses, source
    addresses = _review_route_addresses_from_hops(hops)
    if addresses:
        return addresses, "hops.tokenInOut"
    return [], ""


def _review_route_identity(row: dict[str, Any], quote: dict[str, Any], live_evidence: dict[str, Any]) -> dict[str, Any]:
    route_symbols = list(
        live_evidence.get("routeSymbols")
        or quote.get("routeSymbols")
        or quote.get("path")
        or row.get("route_path")
        or []
    )
    hops = _review_hops_from_attempt(row, quote, live_evidence)
    route_addresses, address_source = _review_route_addresses_from_attempt(row, quote, live_evidence, hops)
    issues = []
    for index in range(1, len(hops)):
        previous = hops[index - 1]
        current = hops[index]
        previous_token_out = _review_hop_token_out(previous)
        current_token_in = _review_hop_token_in(current)
        if previous_token_out and current_token_in and previous_token_out.lower() != current_token_in.lower():
            issues.append(
                {
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromSymbol": previous.get("toSymbol") or previous.get("to_symbol"),
                    "toSymbol": current.get("fromSymbol") or current.get("from_symbol"),
                    "previousTokenOut": previous_token_out,
                    "currentTokenIn": current_token_in,
                }
            )
    expected_nodes = len(route_symbols) if route_symbols else (len(hops) + 1 if hops else 0)
    if issues:
        status = "token_discontinuity"
    elif expected_nodes and len(route_addresses) >= expected_nodes:
        status = "ok"
    elif route_addresses:
        status = "address_partial"
    else:
        status = "symbol_only"
    return {
        "status": status,
        "identity_mode": "address" if route_addresses else "symbol_fallback",
        "address_source": address_source or None,
        "route_symbols": route_symbols,
        "route_addresses": route_addresses,
        "routeAddresses": route_addresses,
        "hop_count": len(hops),
        "token_continuity": not issues,
        "tokenContinuity": not issues,
        "token_continuity_issues": issues,
        "tokenContinuityIssues": issues,
    }


def _review_net_positive_after_costs(live_validation: dict[str, Any], live_costs: dict[str, Any]) -> bool:
    if live_validation.get("netPositiveAfterCosts") is not None:
        return bool(live_validation.get("netPositiveAfterCosts"))
    net_profit = _review_decimal(live_costs.get("netProfitUsd"))
    if net_profit is not None:
        return net_profit > 0
    return bool(live_validation.get("profitableAfterCosts"))


def _review_meets_min_profit_threshold(live_validation: dict[str, Any], live_costs: dict[str, Any], live_evidence: dict[str, Any]) -> bool:
    if live_validation.get("meetsMinProfitThreshold") is not None:
        return bool(live_validation.get("meetsMinProfitThreshold"))
    net_profit = _review_decimal(live_costs.get("netProfitUsd"))
    min_profit = _review_decimal(live_costs.get("minProfitUsd"))
    if net_profit is not None and min_profit is not None:
        return net_profit >= min_profit
    return bool(live_evidence.get("readyForTransactionPreflight"))


def _attempt_route_address_identity(item: dict[str, Any]) -> list[str]:
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    live_evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    return [address.lower() for address in _review_route_identity(item, quote, live_evidence).get("route_addresses") or []]


def _build_review_summary(row: dict[str, Any]) -> dict[str, Any]:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    market_state = row.get("market_state") if isinstance(row.get("market_state"), dict) else {}
    cow_filter = market_state.get("cow_filter") if isinstance(market_state.get("cow_filter"), dict) else {}
    threshold = cow_filter.get("threshold_detail") if isinstance(cow_filter.get("threshold_detail"), dict) else {}
    costs = quote.get("costs") if isinstance(quote.get("costs"), dict) else {}
    live_evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    live_costs = live_evidence.get("costModel") if isinstance(live_evidence.get("costModel"), dict) else {}
    live_validation = live_evidence.get("validation") if isinstance(live_evidence.get("validation"), dict) else {}
    trigger_context = live_evidence.get("triggerContext") if isinstance(live_evidence.get("triggerContext"), dict) else {}
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    error_info = _review_error_summary(quote.get("error") or row.get("error"))
    route_identity = _review_route_identity(row, quote, live_evidence)
    identity_blockers = []
    if route_identity["status"] == "token_discontinuity":
        identity_blockers.append("链上复核跳间 token 地址不连续：存在同名不同合约地址，必须按地址拆分路线后重新复核。")
    elif route_identity["status"] in {"symbol_only", "address_partial"}:
        identity_blockers.append("路线缺少完整 token 地址身份：symbol 只能用于展示，不能作为链上测试或交易依据。")
    live_blockers = [
        str(item)
        for item in (live_evidence.get("blockers") or [])
        if str(item or "").strip()
    ]
    blockers = [*live_blockers, *[item for item in identity_blockers if item not in live_blockers]]
    identity_ok = route_identity["status"] == "ok"
    net_positive_after_costs = _review_net_positive_after_costs(live_validation, live_costs)
    meets_min_profit_threshold = _review_meets_min_profit_threshold(live_validation, live_costs, live_evidence)
    return {
        "phase": row.get("execution_phase"),
        "control_mode": row.get("control_mode") or precheck.get("control_mode") or (quote.get("cow_flashloan_intent") or {}).get("control_mode"),
        "route_hop_constraints_enforced": bool(row.get("route_hop_constraints_enforced") or precheck.get("route_hop_constraints_enforced")),
        "candidate_basis": quote.get("candidate_basis"),
        "trigger_source": quote.get("trigger_source"),
        "window_seconds": market_state.get("window_seconds"),
        "price_source": market_state.get("price_source"),
        "window_spread_percent": quote.get("window_spread_percent"),
        "edge_hint_percent": quote.get("edge_hint_percent"),
        "market_prices": _review_market_prices(quote),
        "plan": {
            "available": plan.get("available"),
            "initial_amount": plan.get("initial_amount") or quote.get("input_amount"),
            "initial_symbol": plan.get("initial_symbol") or quote.get("input_symbol"),
            "final_target_amount": plan.get("final_target_amount"),
            "final_symbol": plan.get("final_symbol") or quote.get("final_symbol"),
            "profit_amount": plan.get("profit_amount"),
            "profit_percent": plan.get("profit_percent"),
            "slippage_bps": plan.get("slippage_bps"),
            "steps": _review_plan_steps(quote),
        },
        "cow_quote": {
            "quote_verified": bool(quote.get("quote_verified")),
            "viable": quote.get("viable"),
            "input_amount": quote.get("input_amount"),
            "input_symbol": quote.get("input_symbol"),
            "final_amount": quote.get("final_amount"),
            "final_symbol": quote.get("final_symbol"),
            "final_delta_amount": quote.get("final_delta_amount") or row.get("final_delta_amount"),
            "hops": quote.get("hops") or [],
            "error": error_info.get("display") or quote.get("error") or row.get("error"),
            "error_type": error_info.get("type"),
            "error_raw": error_info.get("raw"),
        },
        "onchain_evidence": {
            "available": bool(live_evidence),
            "status": live_evidence.get("status"),
            "execution_trust": live_evidence.get("executionTrust"),
            "executionTrust": live_evidence.get("executionTrust"),
            "trusted_for_execution": bool(live_evidence.get("trustedForExecution")) and identity_ok,
            "trustedForExecution": bool(live_evidence.get("trustedForExecution")) and identity_ok,
            "ready_for_transaction_preflight": bool(live_evidence.get("readyForTransactionPreflight")) and identity_ok,
            "readyForTransactionPreflight": bool(live_evidence.get("readyForTransactionPreflight")) and identity_ok,
            "network": live_evidence.get("network"),
            "chain_id": live_evidence.get("chainId"),
            "chainId": live_evidence.get("chainId"),
            "block_number": live_evidence.get("blockNumber"),
            "blockNumber": live_evidence.get("blockNumber"),
            "trigger_block_number": live_evidence.get("triggerBlockNumber") or trigger_context.get("triggerBlockNumber") or trigger_context.get("blockNumber"),
            "triggerBlockNumber": live_evidence.get("triggerBlockNumber") or trigger_context.get("triggerBlockNumber") or trigger_context.get("blockNumber"),
            "quote_block_source": live_evidence.get("quoteBlockSource"),
            "quoteBlockSource": live_evidence.get("quoteBlockSource"),
            "block_timestamp": live_evidence.get("blockTimestamp"),
            "blockTimestamp": live_evidence.get("blockTimestamp"),
            "observed_at": live_evidence.get("observedAt"),
            "observedAt": live_evidence.get("observedAt"),
            "trigger_context": trigger_context,
            "triggerContext": trigger_context,
            "route_symbols": route_identity["route_symbols"],
            "routeSymbols": route_identity["route_symbols"],
            "route_addresses": route_identity["route_addresses"],
            "routeAddresses": route_identity["route_addresses"],
            "route_identity_status": route_identity["status"],
            "routeIdentityStatus": route_identity["status"],
            "route_identity_mode": route_identity["identity_mode"],
            "routeIdentityMode": route_identity["identity_mode"],
            "route_identity": route_identity,
            "routeIdentity": route_identity,
            "hop_count": route_identity["hop_count"],
            "hops": live_evidence.get("hops") or [],
            "all_hops_quoted": bool(live_validation.get("allHopsQuoted")),
            "profitable_after_costs": bool(live_validation.get("profitableAfterCosts")),
            "net_positive_after_costs": net_positive_after_costs,
            "netPositiveAfterCosts": net_positive_after_costs,
            "meets_min_profit_threshold": meets_min_profit_threshold,
            "meetsMinProfitThreshold": meets_min_profit_threshold,
            "token_continuity": route_identity["token_continuity"],
            "tokenContinuity": route_identity["tokenContinuity"],
            "token_continuity_issues": route_identity["token_continuity_issues"],
            "tokenContinuityIssues": route_identity["tokenContinuityIssues"],
            "final_output_usd": live_costs.get("finalOutputUsd"),
            "net_profit_usd": live_costs.get("netProfitUsd"),
            "net_profit_bps": live_costs.get("netProfitBps"),
            "netProfitBps": live_costs.get("netProfitBps"),
            "blockers": blockers,
            "transaction_development": live_evidence.get("transactionDevelopment") or {},
            "transactionDevelopment": live_evidence.get("transactionDevelopment") or {},
        },
        "cow_sdk": {
            "result": row.get("cow_sdk_result") or quote.get("cow_sdk_result") or {},
            "intent": row.get("cow_flashloan_intent") or quote.get("cow_flashloan_intent") or {},
        },
        "timing": {
            "quote": quote.get("quote_timing") or {},
            "signal": quote.get("signal_timing") or market_state.get("signal_timing") or {},
            "binance_window": quote.get("binance_window") or {},
            "three_hop_window_analysis": [
                {
                    "step": step.get("step"),
                    "from_symbol": step.get("from_symbol"),
                    "to_symbol": step.get("to_symbol"),
                    "query_window_timing": step.get("query_window_timing") or {},
                }
                for step in _review_plan_steps(quote)
                if isinstance(step, dict)
            ],
        },
        "costs": {
            "cow_fee_amounts": costs.get("cow_fee_amounts") or [],
            "quote_api_gas_used": costs.get("quote_api_gas_used"),
            "user_order_submission_gas_used": costs.get("user_order_submission_gas_used"),
            "settlement_gas_payer": costs.get("settlement_gas_payer"),
            "approval_gas_status": costs.get("approval_gas_status"),
            "native_balance_source": costs.get("native_balance_source"),
        },
        "profit_guard": {
            "status": precheck.get("status") or row.get("state"),
            "checks_passed": row.get("checks_passed"),
            "can_submit_order": row.get("can_submit_order"),
            "price_guards_passed": precheck.get("price_guards_passed"),
            "profit_above_auto_threshold": precheck.get("profit_above_auto_threshold"),
            "local_profit_gate_enforced": precheck.get("local_profit_gate_enforced"),
            "local_profit_diagnostic_reasons": precheck.get("local_profit_diagnostic_reasons") or [],
            "pure_profit_amount": precheck.get("pure_profit_amount") or precheck.get("final_delta_amount") or row.get("final_delta_amount"),
            "final_symbol": precheck.get("final_symbol") or row.get("final_symbol"),
            "auto_execute_min_profit_usd": precheck.get("auto_execute_min_profit_usd") or threshold.get("min_profit_usd"),
            "auto_execute_min_profit_percent": precheck.get("auto_execute_min_profit_percent") or threshold.get("min_profit_percent"),
            "reasons": row.get("blocked_reasons") or precheck.get("reasons") or [],
            "hop_checks": precheck.get("hop_checks") or [],
            "blocking_cause_counts": precheck.get("blocking_cause_counts") or {},
            "quote_error_type": precheck.get("quote_error_type") or error_info.get("type"),
            "quote_error_display": precheck.get("quote_error_display") or error_info.get("display"),
            "drawdown_amount": precheck.get("drawdown_amount"),
            "drawdown_percent": precheck.get("drawdown_percent"),
        },
        "threshold": threshold,
    }


__all__ = [
    "_attempt_route_address_identity",
    "_build_review_summary",
    "_market_state_summary",
]
