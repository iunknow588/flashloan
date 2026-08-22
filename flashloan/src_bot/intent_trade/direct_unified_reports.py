"""Report formatting helpers for unified direct runtime execution."""

from __future__ import annotations

from typing import Any, Callable


def runtime_failure_reason(code: int) -> str:
    return {
        0: "none",
        101: "not_enough_valid_pools",
        102: "no_price_spread",
    }.get(int(code), f"unknown_failure_{code}")


def bytes_to_hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if hasattr(value, "hex"):
        text = value.hex()
        return text if str(text).startswith("0x") else f"0x{text}"
    return str(value)


def runtime_trade_decision_report(result: Any) -> dict[str, Any]:
    if len(result) >= 17:
        failure_code = int(result[16]) if result[16] else 0
        return {
            "ok": bool(result[0]),
            "viable": bool(result[0]),
            "tradeIndex": str(result[1]),
            "tokenX": result[2],
            "tokenY": result[3],
            "lowPool": result[4],
            "highPool": result[5],
            "adapterKind": str(result[6]),
            "lowFee": str(result[7]),
            "highFee": str(result[8]),
            "lowLiquidity": str(result[9]),
            "highLiquidity": str(result[10]),
            "lowNormalizedTick": str(result[11]),
            "highNormalizedTick": str(result[12]),
            "tickDelta": str(result[13]),
            "scannedPoolCount": str(result[14]),
            "validPoolCount": str(result[15]),
            "failureCode": str(failure_code),
            "failureReason": runtime_failure_reason(failure_code),
            "abiVersion": "legacy",
        }
    failure_code = int(result[14]) if result[14] else 0
    return {
        "ok": bool(result[0]),
        "viable": bool(result[0]),
        "tradeIndex": str(result[1]),
        "tokenX": result[2],
        "tokenY": result[3],
        "lowPool": result[4],
        "highPool": result[5],
        "lowFee": str(result[6]),
        "highFee": str(result[7]),
        "lowLiquidity": str(result[8]),
        "highLiquidity": str(result[9]),
        "lowNormalizedTick": str(result[10]),
        "highNormalizedTick": str(result[11]),
        "tickDelta": str(result[12]),
        "validPoolCount": str(result[13]),
        "failureCode": str(failure_code),
        "failureReason": runtime_failure_reason(failure_code),
        "abiVersion": "unified",
    }


def runtime_execution_preview_report(result: Any) -> dict[str, Any]:
    if len(result) < 12:
        return {
            "router": result[0],
            "swapPath": bytes_to_hex(result[1]),
            "quotedFinal": str(result[2]),
            "premium": str(result[3]),
            "requiredFinal": str(result[4]),
            "protectedMinFinal": str(result[5]),
            "minProfit": str(result[6]),
            "quotedFinalUsdc": str(result[2]),
            "premiumUsdc": str(result[3]),
            "requiredFinalUsdc": str(result[4]),
            "protectedAmountOutMinUsdc": str(result[5]),
            "abiVersion": "legacy",
        }
    return {
        "borrowedAsset": result[0],
        "profitAsset": result[1],
        "borrowedAmount": str(result[2]),
        "routeDirection": str(result[3]),
        "failedHopIndex": str(result[4]),
        "swapPath": bytes_to_hex(result[5]),
        "quotedFinal": str(result[6]),
        "premium": str(result[7]),
        "requiredFinal": str(result[8]),
        "protectedMinFinal": str(result[9]),
        "minProfit": str(result[10]),
        "expectedProfit": str(result[11]),
        "quotedFinalUsdc": str(result[6]),
        "premiumUsdc": str(result[7]),
        "requiredFinalUsdc": str(result[8]),
        "protectedAmountOutMinUsdc": str(result[9]),
        "abiVersion": "unified",
    }


def runtime_hop_preview_report(result: Any) -> dict[str, Any]:
    return {
        "hopIndex": str(result[0]),
        "tokenIn": result[1],
        "tokenOut": result[2],
        "pool": result[3],
        "fee": str(result[4]),
        "amountIn": str(result[5]),
        "quotedAmountOut": str(result[6]),
        "amountOutMin": str(result[7]),
    }


def runtime_order_preview_report(result: Any) -> dict[str, Any]:
    triangular_route = result[6]
    progress = result[7]
    return {
        "found": bool(result[0]),
        "strategyStatus": str(result[1]),
        "executionKind": str(result[2]),
        "selectedTradeArrayIndex": str(result[3]),
        "decision": runtime_trade_decision_report(result[4]),
        "executionPreview": runtime_execution_preview_report(result[5]),
        "triangularRoute": {
            "routeDirection": str(triangular_route[0]),
            "failedHopIndex": str(triangular_route[1]),
            "executionMode": str(triangular_route[2]),
            "hops": [runtime_hop_preview_report(hop) for hop in triangular_route[3]],
            "quotedFinalUsdc": str(triangular_route[4]),
            "premiumUsdc": str(triangular_route[5]),
            "requiredFinalUsdc": str(triangular_route[6]),
            "expectedProfitUsdc": str(triangular_route[7]),
        },
        "progress": {
            "finalResultCode": str(progress[0]),
            "selectedStatus": str(progress[1]),
            "lastCheckedStatus": str(progress[2]),
            "attemptedStatusMask": str(progress[3]),
            "selectedStatusMask": str(progress[4]),
            "remainingStatusMask": str(progress[5]),
            "remainingStepCount": str(progress[6]),
            "steps": [
                {
                    "strategyStatus": str(step[0]),
                    "phase": str(step[1]),
                    "routeDirection": str(step[2]),
                    "failedHopIndex": str(step[3]),
                    "resultCode": str(step[4]),
                    "detailCode": str(step[5]),
                    "tradeArrayIndex": str(step[6]),
                    "tradeIndex": str(step[7]),
                    "profitAsset": step[8],
                    "expectedProfit": str(step[9]),
                    "quotedFinal": str(step[10]),
                    "requiredFinal": str(step[11]),
                }
                for step in progress[7]
            ],
        },
    }


def runtime_run_result_report(result: Any) -> dict[str, Any]:
    return {
        "resultCode": str(result[0]),
        "strategyStatus": str(result[1]),
        "executionKind": str(result[2]),
        "routeDirection": str(result[3]),
        "selectedTradeArrayIndex": str(result[4]),
        "profitAsset": result[5],
        "profitAmount": str(result[6]),
        "profitSwept": str(result[7]),
        "attemptedStatusMask": str(result[8]),
        "remainingStatusMask": str(result[9]),
        "remainingStepCount": str(result[10]),
    }


def runtime_trade_plan_report(trades: list[dict[str, Any]]) -> list[dict[str, str]]:
    report = []
    for array_index, trade in enumerate(trades):
        report.append(
            {
                "arrayIndex": str(array_index),
                "tradeIndex": str(trade.get("tradeIndex", "")),
                "strategyStatus": str(trade.get("strategyStatus", trade.get("strategy_status", ""))),
                "strategyStage": str(trade.get("strategyStage", trade.get("strategy_stage", ""))),
                "routeSymbols": "->".join(trade.get("routeSymbols") or trade.get("route_symbols") or []),
                "tokenX": str(trade.get("tokenX", "")),
                "tokenY": str(trade.get("tokenY", "")),
            }
        )
    return report


def runtime_trade_metadata_by_index(trades: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    metadata: dict[int, dict[str, Any]] = {}
    for trade in trades:
        try:
            metadata[int(trade.get("tradeIndex"))] = trade
        except (TypeError, ValueError):
            continue
    return metadata


def selected_strategy_status(
    selected_trade: dict[str, Any] | None,
    *,
    execution_mode: str,
    execution_kind: int | None = None,
    status_translation: dict[tuple[str, int | None, int], int] | None = None,
) -> int:
    if not selected_trade:
        return 55555
    raw_status = selected_trade.get("strategyStatus", selected_trade.get("strategy_status", 55555))
    try:
        strategy_status = int(raw_status)
    except (TypeError, ValueError):
        strategy_status = 55555
    if strategy_status in {1, 2, 5}:
        return strategy_status
    mapped_status = (status_translation or {}).get((execution_mode, execution_kind, strategy_status))
    if mapped_status is not None:
        return mapped_status
    if strategy_status == 3:
        return 3
    return strategy_status if strategy_status in {4, 55555} else 55555


def rank_runtime_trades_by_profit(
    candidate_trade_args: list[dict[str, Any]],
    preview_trade: Callable[[dict[str, Any]], Any],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ranked: list[tuple[int, int, dict[str, Any], dict[str, str]]] = []
    for input_index, trade in enumerate(candidate_trade_args):
        try:
            result = preview_trade(trade)
        except Exception:
            continue
        if not isinstance(result, (list, tuple)) or len(result) < 4 or not bool(result[0]):
            continue

        is_auto_preview = len(result) >= 5
        preview = runtime_execution_preview_report(result[4] if is_auto_preview else result[3])
        quoted_final = preview.get("quotedFinalUsdc") or preview["quotedFinal"]
        required_final = preview.get("requiredFinalUsdc") or preview["requiredFinal"]
        min_profit = preview.get("minProfitUsdc") or preview["minProfit"]
        net_profit_usdc = int(quoted_final) - int(required_final) + int(min_profit)
        report = {
            "inputIndex": str(input_index),
            "tradeIndex": str(trade["tradeIndex"]),
            "quotedFinalUsdc": preview["quotedFinalUsdc"],
            "netProfitUsdc": str(net_profit_usdc),
        }
        if is_auto_preview:
            report["executionKind"] = str(result[1])
        ranked.append((net_profit_usdc, input_index, trade, report))

    ranked.sort(key=lambda item: (item[0], item[1]))
    selected = ranked[: max(1, int(limit))]
    return [item[2] for item in selected], [item[3] for item in selected]
