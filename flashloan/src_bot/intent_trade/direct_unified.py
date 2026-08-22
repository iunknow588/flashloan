"""Shared helpers for the unified direct execution path."""

from __future__ import annotations

import re
from typing import Any, Callable


def unified_executor_route_order_check(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
    normalize_pair_address: Callable[[Any], str],
    runtime_trade_status: Callable[[dict[str, Any]], int | None],
) -> dict[str, Any]:
    if not trades:
        return {"ok": False, "reason": "unified_executor_requires_at_least_one_trade"}

    def other_token(trade: dict[str, Any]) -> str:
        token_x = normalize_pair_address(trade.get("tokenX"))
        token_y = normalize_pair_address(trade.get("tokenY"))
        if token_x == usdc_address:
            return token_y
        if token_y == usdc_address:
            return token_x
        return ""

    x_token = other_token(trades[0])
    if not x_token:
        return {"ok": False, "reason": "unified_executor_first_trade_must_include_usdc"}
    if len(trades) == 1:
        return {
            "ok": True,
            "routeDirection": "U-X-U",
            "tokenX": x_token,
            "tokenY": "",
            "statuses": [runtime_trade_status(trades[0])],
        }

    y_token = other_token(trades[1])
    if not y_token:
        return {"ok": False, "reason": "unified_executor_second_trade_must_include_usdc"}
    if len(trades) == 2:
        return {
            "ok": True,
            "routeDirection": "U-X-U|U-Y-U",
            "tokenX": x_token,
            "tokenY": y_token,
            "statuses": [runtime_trade_status(trade) for trade in trades],
        }

    xy_x = normalize_pair_address(trades[2].get("tokenX"))
    xy_y = normalize_pair_address(trades[2].get("tokenY"))
    if not x_token or not y_token or not xy_x or not xy_y:
        return {"ok": False, "reason": "unified_executor_route_tokens_are_incomplete"}
    if x_token != xy_x or y_token != xy_y:
        return {
            "ok": False,
            "reason": "unified_executor_route_token_order_mismatch",
            "usdcXToken": x_token,
            "usdcYToken": y_token,
            "xyTokenX": xy_x,
            "xyTokenY": xy_y,
        }
    if len(trades) >= 4:
        yx_x = normalize_pair_address(trades[3].get("tokenX"))
        yx_y = normalize_pair_address(trades[3].get("tokenY"))
        if yx_x and yx_y and (yx_x != y_token or yx_y != x_token):
            return {
                "ok": False,
                "reason": "unified_executor_reverse_route_token_order_mismatch",
            }
    return {
        "ok": True,
        "routeDirection": "U-X-Y-U",
        "tokenX": x_token,
        "tokenY": y_token,
        "statuses": [runtime_trade_status(trade) for trade in trades[:4]],
    }


def route_group_target_symbols(
    group: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    normalized_symbol: Callable[[Any], str],
) -> list[str]:
    symbols: list[str] = []
    for trade in trades:
        route_symbols = trade.get("routeSymbols") or trade.get("route_symbols")
        if isinstance(route_symbols, (list, tuple)):
            for item in route_symbols:
                symbol = normalized_symbol(item)
                if symbol and symbol != "USDC" and symbol not in symbols:
                    symbols.append(symbol)
    if not symbols:
        route_key = str(group.get("routeKey") or group.get("route_key") or "")
        for item in re.split(r"[:>\-/,\s]+", route_key):
            symbol = normalized_symbol(item)
            if symbol and symbol != "USDC" and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def ordered_runtime_trade_plan(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
    runtime_trade_route_key: Callable[[dict[str, Any]], str],
    runtime_trade_status: Callable[[dict[str, Any]], int | None],
    max_unified_route_trade_inputs: int,
    max_runtime_trade_scan: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not trades:
        return [], {"ok": False, "reason": "runtime_trades_empty"}

    groups: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for trade in trades:
        key = runtime_trade_route_key(trade)
        if key:
            groups.setdefault(key, []).append(trade)
        else:
            ungrouped.append(trade)

    if not groups:
        ordered = list(trades[:max_unified_route_trade_inputs])
        for index, trade in enumerate(ordered):
            if runtime_trade_status(trade) is None and index < 4:
                trade["strategyStatus"] = index + 1
                trade["strategy_status"] = index + 1
        return ordered, {
            "ok": len(ordered) >= 3,
            "reason": "input_order_preserved",
            "groupKey": None,
        }

    ranked_groups: list[tuple[float, str, list[dict[str, Any]]]] = []
    for key, group in groups.items():
        by_status: dict[int, dict[str, Any]] = {}
        for trade in group:
            status = runtime_trade_status(trade)
            if status is not None and status not in by_status:
                by_status[status] = trade
        if not by_status:
            by_status = {
                index + 1: trade
                for index, trade in enumerate(group[:max_runtime_trade_scan])
            }
        score = max(
            float(trade.get("spreadScore") or 0.0)
            for trade in group
            if isinstance(trade, dict)
        )
        ordered: list[dict[str, Any]] = []
        if 1 in by_status:
            ordered.append(by_status[1])
        if 2 in by_status:
            ordered.append(by_status[2])
        if 3 in by_status:
            ordered.append(by_status[3])
        if 5 in by_status:
            ordered.append(by_status[5])
        if ordered:
            ranked_groups.append((score, key, ordered))

    if not ranked_groups:
        return [], {
            "ok": False,
            "reason": "no_complete_unified_route_group",
            "groupKey": None,
        }
    _, selected_key, ordered = max(ranked_groups, key=lambda item: (item[0], item[1]))
    for trade in ordered:
        trade["routeKey"] = selected_key
        trade["route_key"] = selected_key

    return ordered[:max_unified_route_trade_inputs], {
        "ok": True,
        "reason": "coherent_route_group_selected",
        "groupKey": selected_key,
        "strategyStatuses": [runtime_trade_status(trade) for trade in ordered],
    }


def runtime_trade_abi_arg(trade: dict[str, Any], Web3: Any) -> dict[str, Any]:
    pools = []
    for pool in trade["pools"]:
        pool_address = pool["pool"] or "0x0000000000000000000000000000000000000000"
        pools.append({"adapterKind": int(pool["adapterKind"]), "pool": Web3.to_checksum_address(pool_address)})
    return {
        "tradeIndex": int(trade["tradeIndex"]),
        "tokenX": Web3.to_checksum_address(trade["tokenX"]),
        "tokenY": Web3.to_checksum_address(trade["tokenY"]),
        "pools": pools,
    }


def prepare_unified_route_groups(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    ordered_runtime_trade_plan: Callable[[list[dict[str, Any]], str], tuple[list[dict[str, Any]], dict[str, Any]]],
    unified_executor_route_order_check: Callable[[list[dict[str, Any]], str], dict[str, Any]],
    route_group_target_symbols: Callable[[dict[str, Any], list[dict[str, Any]]], list[str]],
    route_group_token_risk_report: Callable[[dict[str, Any], list[dict[str, Any]], bool], dict[str, Any]],
    route_group_usdc_penalty: Callable[[dict[str, Any], str, str], int],
    is_stable_exercise_symbol: Callable[[str], bool],
    allow_single_pair_diagnostic: bool = False,
    allow_stable_targets: bool = False,
    require_token_risk_metadata: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        raw_trades = group.get("trades") if isinstance(group, dict) else None
        if not isinstance(raw_trades, list):
            rejected.append({"groupIndex": index, "reason": "route_group_trades_missing"})
            continue
        ordered, route_plan = ordered_runtime_trade_plan(raw_trades, usdc_address=usdc_address)
        route_check = unified_executor_route_order_check(ordered, usdc_address=usdc_address)
        if not route_plan.get("ok") or not route_check.get("ok"):
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": route_plan.get("reason") if not route_plan.get("ok") else route_check.get("reason"),
                }
            )
            continue
        if len(ordered) == 1 and not allow_single_pair_diagnostic:
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": "single_pair_diagnostic_disabled",
                }
            )
            continue
        target_symbols = route_group_target_symbols(group, ordered)
        stable_targets = [symbol for symbol in target_symbols if is_stable_exercise_symbol(symbol)]
        if stable_targets and not allow_stable_targets:
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": "stable_exercise_target_disabled",
                    "stableTargets": stable_targets,
                }
            )
            continue
        token_risk = route_group_token_risk_report(
            group,
            ordered,
            require_token_risk_metadata,
        )
        if not token_risk["broadcastable"]:
            reason = "token_risk_broadcast_disabled" if token_risk["blocked"] else "token_risk_metadata_missing"
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or route_plan.get("groupKey") or f"group:{index}"),
                    "reason": reason,
                    "tokenRisk": token_risk,
                }
            )
            continue
        prepared.append(
            {
                "groupIndex": index,
                "routeKey": str(group.get("routeKey") or route_plan.get("groupKey") or f"group:{index}"),
                "routeKind": str(group.get("routeKind") or "provided"),
                "exerciseTargetPolicy": str(group.get("exerciseTargetPolicy") or "provided"),
                "score": float(group.get("score") or 0.0),
                "trades": ordered,
                "routePlan": route_plan,
                "routeCheck": route_check,
                "targetSymbols": target_symbols,
                "tokenRisk": token_risk,
                "slippageRiskPenaltyUsdc": route_group_usdc_penalty(
                    group,
                    "slippageRiskPenaltyUsdc",
                    "slippage_risk_penalty_usdc",
                ),
                "routeCorrelationPenaltyUsdc": route_group_usdc_penalty(
                    group,
                    "routeCorrelationPenaltyUsdc",
                    "route_correlation_penalty_usdc",
                ),
            }
        )
    return prepared, rejected


def select_best_unified_route_group(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    preview_group: Callable[[dict[str, Any]], Any],
    runtime_order_preview_report: Callable[[Any], dict[str, Any]],
    unified_net_profit_report: Callable[..., dict[str, Any]],
    normalize_pair_address: Callable[[Any], str],
    estimate_group_gas: Callable[[dict[str, Any]], int] | None = None,
    gas_price_wei: int = 0,
    relay_cost_wei: int = 0,
    cache_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_bps: int = 0,
    avax_usdc_price_micro: int = 0,
    redact_sensitive_text: Callable[[Any], Any] = lambda value: value,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    selected: tuple[int, int, int, dict[str, Any], dict[str, Any]] | None = None
    for group_index, group in enumerate(groups):
        try:
            raw_preview = preview_group(group)
            report = runtime_order_preview_report(raw_preview)
        except Exception as exc:
            evaluations.append(
                {
                    "groupIndex": str(group.get("groupIndex", group_index)),
                    "routeKey": str(group.get("routeKey") or ""),
                    "found": False,
                    "status": "preview_error",
                    "error": redact_sensitive_text(exc),
                }
            )
            continue

        expected_profit = int(report["executionPreview"].get("expectedProfit") or 0)
        profit_asset = normalize_pair_address(report["executionPreview"].get("profitAsset"))
        found = bool(report["found"]) and profit_asset == usdc_address and expected_profit > 0
        gas_units = 0
        gas_error = ""
        if found and estimate_group_gas is not None:
            try:
                gas_units = int(estimate_group_gas(group))
            except Exception as exc:
                found = False
                gas_error = redact_sensitive_text(exc)
        net_profit = unified_net_profit_report(
            expected_profit=expected_profit,
            gas_units=gas_units,
            gas_price_wei=gas_price_wei,
            relay_cost_wei=relay_cost_wei,
            cache_risk_penalty_usdc=cache_risk_penalty_usdc,
            public_mempool_risk_penalty_usdc=public_mempool_risk_penalty_usdc,
            public_mempool_risk_penalty_bps=public_mempool_risk_penalty_bps,
            slippage_risk_penalty_usdc=int(group.get("slippageRiskPenaltyUsdc") or 0),
            route_correlation_penalty_usdc=int(group.get("routeCorrelationPenaltyUsdc") or 0),
            avax_usdc_price_micro=avax_usdc_price_micro,
        )
        evaluation = {
            "groupIndex": str(group.get("groupIndex", group_index)),
            "routeKey": str(group.get("routeKey") or ""),
            "routeKind": str(group.get("routeKind") or ""),
            "found": bool(report["found"]),
            "profitableCandidate": found,
            "selected": False,
            "strategyStatus": report["strategyStatus"],
            "executionKind": report["executionKind"],
            "expectedProfit": str(expected_profit),
            "netProfit": net_profit,
            "profitAsset": report["executionPreview"].get("profitAsset"),
            "tokenRisk": group.get("tokenRisk"),
            "preview": report,
        }
        if gas_error:
            evaluation["status"] = "gas_estimate_error"
            evaluation["error"] = gas_error
        evaluations.append(evaluation)
        if not found:
            continue
        tie_index = int(group.get("groupIndex", group_index))
        candidate = (int(net_profit["netProfit"]), expected_profit, -tie_index, group, report)
        if selected is None or candidate[:3] > selected[:3]:
            selected = candidate

    if selected is None:
        return None, None, evaluations
    selected_group = selected[3]
    selected_index = str(selected_group.get("groupIndex", ""))
    selected_key = str(selected_group.get("routeKey") or "")
    for evaluation in evaluations:
        if evaluation.get("groupIndex") == selected_index and evaluation.get("routeKey") == selected_key:
            evaluation["selected"] = True
            break
    return selected[3], selected[4], evaluations


def unified_execution_params(params: dict[str, int]) -> dict[str, int]:
    return {
        "amount": int(params["amount"]),
        "deadline": int(params["deadline"]),
        "amountOutMinUsdc": int(params["amountOutMinUsdc"]),
        "minProfitUsdc": int(params["minProfitUsdc"]),
    }


def unified_cross_pool_params(params: dict[str, int]) -> dict[str, int]:
    return {
        "amount": int(params["amount"]),
        "deadline": int(params["deadline"]),
        "minFinalToken": int(params["minFinalTokenX"]),
        "minProfitToken": int(params["minProfitTokenX"]),
    }


def unified_receipt_events(contract: Any, receipt: Any) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for event_name in (
        "OrderedRuntimePreviewSelected",
        "RuntimeProfitChecked",
        "RuntimeTriangularHopQuoted",
        "RuntimeStepEvaluated",
        "FlashLoanRouteExecuted",
        "ProfitSwept",
        "RuntimeWorkflowFinished",
    ):
        try:
            event_factory = getattr(contract.events, event_name)
            decoded = event_factory().process_receipt(receipt)
        except Exception:
            continue
        if decoded:
            events[event_name] = [
                dict(item.get("args") or {})
                for item in decoded
                if isinstance(item, dict)
            ]
    return events
