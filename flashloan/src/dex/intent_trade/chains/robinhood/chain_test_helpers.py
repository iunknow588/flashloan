from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.market_identity import canonical_market_id_from_legacy
from dex.intent_trade.chains.robinhood.chain_test_policy import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from market.binance_market.dex_surfaces import DEFAULT_GAS_RESERVE_USD
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD

ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT = SRC_ROOT / "runtime" / "cache" / "dex_opportunity_snapshot.json"
_TOKEN_DECIMALS_FALLBACK = {
    "ETH": 18,
    "WETH": 18,
    "BTC": 8,
    "WBTC": 8,
}
_STABLE_SYMBOLS = {"AUSD", "BUSD", "DAI", "FDUSD", "FRAX", "PYUSD", "RLUSD", "USDC", "USDD", "USDE", "USDG", "USDT"}
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_market_id(value: object) -> str:
    text = _text(value).lower()
    return canonical_market_id_from_legacy(text) or text


def _is_zero_address(value: object) -> bool:
    return _text(value).lower() == _ZERO_ADDRESS


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value: object) -> bool | None:
    parsed = _number(value)
    return None if parsed is None else parsed > 0


def _raw_from_human(value: object, decimals: int | None) -> int | None:
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return None
    scale = Decimal(10) ** int(decimals if decimals is not None else 18)
    return int((parsed * scale).to_integral_value())


def _human_from_raw(value: int | None, decimals: int | None) -> str:
    if value is None:
        return ""
    scale = Decimal(10) ** int(decimals if decimals is not None else 18)
    return format((Decimal(int(value)) / scale).normalize(), "f")


def _quote_pool_index(quote: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for key in ("candidate_pools", "candidatePools", "routePools", "executionPools", "preferredPools", "strictQualityPools"):
        for pool in _as_list(quote.get(key)):
            if not isinstance(pool, dict):
                continue
            for address_key in ("pool", "poolId", "pool_id"):
                address = _text(pool.get(address_key))
                if address:
                    mapping[address.lower()] = pool
    return mapping


def _symbol_decimals(symbol: object, *, network: str) -> int:
    normalized = _text(symbol).upper()
    if normalized in _TOKEN_DECIMALS_FALLBACK:
        return _TOKEN_DECIMALS_FALLBACK[normalized]
    if normalized in _STABLE_SYMBOLS:
        return 18 if network == ROBINHOOD_TESTNET_NETWORK else 6
    return 18


def _hop_decimals(hop: dict[str, Any], pool: dict[str, Any], *, side: str, network: str) -> int:
    decimals_key = "inputDecimals" if side == "in" else "outputDecimals"
    direct = hop.get(decimals_key)
    try:
        parsed = int(direct)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass

    token_key = "tokenIn" if side == "in" else "tokenOut"
    symbol_key = "fromSymbol" if side == "in" else "toSymbol"
    token = _text(hop.get(token_key)).lower()
    symbol = _text(hop.get(symbol_key)).upper()
    for index in (0, 1):
        token_address = _text(pool.get(f"token{index}")).lower()
        token_symbol = _text(pool.get(f"token{index}Symbol")).upper()
        if token and token == token_address:
            candidate = pool.get(f"token{index}Decimals") or _as_dict(pool.get(f"token{index}Metadata")).get("decimals")
            try:
                parsed = int(candidate)
                if parsed > 0:
                    return parsed
            except (TypeError, ValueError):
                pass
        if symbol and symbol == token_symbol:
            candidate = pool.get(f"token{index}Decimals") or _as_dict(pool.get(f"token{index}Metadata")).get("decimals")
            try:
                parsed = int(candidate)
                if parsed > 0:
                    return parsed
            except (TypeError, ValueError):
                pass
    return _symbol_decimals(symbol, network=network)


def _normalized_hop_rows(*, quote: dict[str, Any], evidence: dict[str, Any], network: str) -> list[dict[str, Any]]:
    source_hops = _as_list(evidence.get("hops")) or _as_list(quote.get("hops"))
    if not source_hops:
        return []
    pools = _quote_pool_index(quote)
    rows: list[dict[str, Any]] = []
    for index, hop in enumerate(source_hops, start=1):
        if not isinstance(hop, dict):
            continue
        pool = pools.get(_text(hop.get("pool")).lower(), {})
        in_decimals = _hop_decimals(hop, pool, side="in", network=network)
        out_decimals = _hop_decimals(hop, pool, side="out", network=network)
        amount_in_raw = _int_or_none(hop.get("amountInRaw"))
        if amount_in_raw is None:
            amount_in_raw = _raw_from_human(hop.get("inputAmount") or hop.get("amountIn") or hop.get("referenceInputAmount"), in_decimals)
        amount_out_raw = _int_or_none(hop.get("amountOutRaw"))
        if amount_out_raw is None:
            amount_out_raw = _raw_from_human(
                hop.get("outputAmount")
                or hop.get("referenceOutputAmount")
                or hop.get("amountOut")
                or hop.get("referenceAmountOut"),
                out_decimals,
            )
        if amount_in_raw is None or amount_out_raw is None:
            continue
        rows.append(
            {
                "hop": hop.get("hop") or index,
                "protocol": _text(hop.get("protocol") or hop.get("dex")),
                "quoteMethod": _text(hop.get("quoteMethod") or hop.get("quoteMode")),
                "pool": _text(hop.get("pool")),
                "fromSymbol": _text(hop.get("fromSymbol")),
                "toSymbol": _text(hop.get("toSymbol")),
                "tokenIn": _text(hop.get("tokenIn")),
                "tokenOut": _text(hop.get("tokenOut")),
                "amountInRaw": str(amount_in_raw),
                "amountOutRaw": str(amount_out_raw),
                "minAmountOutRaw": str(amount_out_raw * max(0, 10_000 - 50) // 10_000),
                "amountIn": _text(hop.get("amountIn") or hop.get("inputAmount") or _human_from_raw(amount_in_raw, in_decimals)),
                "amountOut": _text(
                    hop.get("amountOut")
                    or hop.get("referenceOutputAmount")
                    or hop.get("outputAmount")
                    or _human_from_raw(amount_out_raw, out_decimals)
                ),
                "inputDecimals": in_decimals,
                "outputDecimals": out_decimals,
                "fee": hop.get("fee") or hop.get("feeBps"),
                "quoterGasEstimate": hop.get("quoterGasEstimate"),
                "quoteSource": hop.get("quoteSource"),
                "executionTrust": hop.get("executionTrust"),
                "referenceOutputUsd": _number(hop.get("referenceOutputUsd") or hop.get("outputUsd")),
                "inputUsd": _number(hop.get("inputUsd")),
                "adapterRequired": hop.get("adapterRequired"),
                "status": _text(hop.get("status") or "quoted"),
            }
        )
    return rows


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _route_path(attempt: dict[str, Any]) -> list[str]:
    quote = _as_dict(attempt.get("quote"))
    precheck = _as_dict(attempt.get("precheck"))
    evidence = _as_dict(quote.get("live_onchain_evidence"))
    candidates = [
        attempt.get("route_path"),
        attempt.get("route_path_symbols"),
        attempt.get("path"),
        attempt.get("pathSymbols"),
        attempt.get("route_symbols"),
        attempt.get("routeSymbols"),
        quote.get("route_path"),
        quote.get("route_symbols"),
        evidence.get("routeSymbols"),
        precheck.get("route_symbols"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            route = [_text(item) for item in candidate if _text(item)]
            if route:
                return route
    pair = _text(attempt.get("pair") or quote.get("pair"))
    if pair:
        return [part.strip() for part in pair.split("->") if part.strip()]
    return []


def _route_path_text(route_path: list[str]) -> str:
    return " -> ".join(item for item in (_text(value) for value in route_path) if item)


def _latest_snapshot_route(route_path: list[str], *, chain_id: int, network: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if network != ROBINHOOD_TESTNET_NETWORK or chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        return {}, {}
    # Tests patch the path on chain_test_pack. Read that module attribute so the
    # extracted helper still honors monkeypatched snapshot locations.
    import sys

    from dex.intent_trade.chains.robinhood import chain_test_pack as _chain_test_pack

    snapshot = _read_json(getattr(_chain_test_pack, "ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT", ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT))
    snapshots = _as_dict(snapshot.get("snapshots"))
    chain_snapshot = _as_dict(snapshots.get(str(chain_id)))
    payload = _as_dict(chain_snapshot.get("payload"))
    wanted = _route_path_text(route_path).lower()
    if not wanted:
        return {}, {}
    for route in _as_list(payload.get("routes")):
        if not isinstance(route, dict):
            continue
        overview = _as_dict(route.get("overviewRoute"))
        candidates = [
            _text(route.get("pathText")),
            _text(route.get("routePathText")),
            _text(overview.get("pathText")),
            _text(route.get("symbolPair")),
            _route_path_text(_as_list(route.get("routeSymbols"))),
            _route_path_text(_as_list(overview.get("pathSymbols"))),
        ]
        for candidate in candidates:
            if candidate and candidate.lower() == wanted:
                return route, snapshot
    return {}, snapshot


def _latest_snapshot_route_size_advice(
    latest_snapshot_route: dict[str, Any],
    latest_snapshot: dict[str, Any],
    *,
    requested_borrow_amount_raw: int | None,
) -> dict[str, Any]:
    size_analysis = _as_dict(latest_snapshot_route.get("sizeAnalysis"))
    quote_analysis = _as_dict(latest_snapshot_route.get("quoteAnalysis"))
    execution_probe = _as_dict(
        size_analysis.get("executionSizeProbe")
        or size_analysis.get("routeExecutionSizeProbe")
        or quote_analysis.get("executionSizeProbe")
        or quote_analysis.get("routeExecutionSizeProbe")
    )
    max_executable_raw = _int_or_none(
        size_analysis.get("maxExecutableAmountRaw")
        or quote_analysis.get("maxExecutableAmountRaw")
        or execution_probe.get("maxExecutableAmountRaw")
    )
    route_executable_candidate = quote_analysis.get("executableCandidate")
    route_chain_submission_candidate = quote_analysis.get("chainSubmissionCandidate")
    route_execution_size_probe_status = _text(
        execution_probe.get("status")
        or size_analysis.get("executionSizeProbeStatus")
        or quote_analysis.get("executionSizeProbeStatus")
    )
    route_recommended_borrow_amount_raw = _int_or_none(
        size_analysis.get("recommendedBorrowInputAmountRaw")
        or quote_analysis.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("recommendedBorrowInputAmountRaw")
    )
    route_execution_size_probe_exceeded = bool(
        requested_borrow_amount_raw is not None
        and max_executable_raw is not None
        and requested_borrow_amount_raw > max_executable_raw
    )
    route_execution_size_shortfall_raw = (
        requested_borrow_amount_raw - max_executable_raw
        if route_execution_size_probe_exceeded
        else None
    )
    return {
        "latestSnapshotUpdatedAt": _text(latest_snapshot.get("updated_at")),
        "routeMaxExecutableAmountRaw": max_executable_raw,
        "routeExecutionSizeProbe": execution_probe or None,
        "routeExecutionSizeProbeStatus": route_execution_size_probe_status,
        "routeExecutableCandidate": None if route_executable_candidate is None else bool(route_executable_candidate),
        "routeChainSubmissionCandidate": None if route_chain_submission_candidate is None else bool(route_chain_submission_candidate),
        "routeRecommendedBorrowAmountRaw": route_recommended_borrow_amount_raw,
        "routeRequestedBorrowAmountRaw": requested_borrow_amount_raw,
        "routeExecutionSizeProbeExceeded": route_execution_size_probe_exceeded,
        "routeExecutionSizeShortfallRaw": route_execution_size_shortfall_raw,
    }


def _hop_summaries(hops: list[dict[str, Any]], *, slippage_bps: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hop in hops:
        amount_out_raw = _int_or_none(hop.get("amountOutRaw"))
        amount_in_raw = _int_or_none(hop.get("amountInRaw"))
        min_amount_out_raw = (
            amount_out_raw * max(0, 10_000 - int(slippage_bps)) // 10_000
            if amount_out_raw is not None
            else None
        )
        rows.append(
            {
                "hop": hop.get("hop"),
                "protocol": _text(hop.get("protocol")),
                "quoteMethod": _text(hop.get("quoteMethod")),
                "pool": _text(hop.get("pool")),
                "fromSymbol": _text(hop.get("fromSymbol")),
                "toSymbol": _text(hop.get("toSymbol")),
                "tokenIn": _text(hop.get("tokenIn")),
                "tokenOut": _text(hop.get("tokenOut")),
                "amountInRaw": str(amount_in_raw) if amount_in_raw is not None else _text(hop.get("amountInRaw")),
                "amountOutRaw": str(amount_out_raw) if amount_out_raw is not None else _text(hop.get("amountOutRaw")),
                "minAmountOutRaw": str(min_amount_out_raw) if min_amount_out_raw is not None else None,
                "amountIn": _text(hop.get("amountIn")),
                "amountOut": _text(hop.get("amountOut")),
                "inputDecimals": hop.get("inputDecimals"),
                "outputDecimals": hop.get("outputDecimals"),
                "fee": hop.get("fee") or hop.get("feeBps"),
                "quoterGasEstimate": hop.get("quoterGasEstimate"),
            }
        )
    return rows


def _profit_basis(cost_model: dict[str, Any], borrow_symbol: str) -> str:
    basis = _text(cost_model.get("profitBasis")).lower()
    if basis in {"borrow_token", "usd"}:
        return basis
    return "usd" if borrow_symbol.upper() in _STABLE_SYMBOLS else "borrow_token"


def _repayment_constraint(
    hops: list[dict[str, Any]],
    *,
    flash_fee_bps: float,
    cost_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cost_model = cost_model or {}
    first_hop = hops[0] if hops and isinstance(hops[0], dict) else {}
    last_hop = hops[-1] if hops and isinstance(hops[-1], dict) else {}
    principal_raw = _int_or_none(first_hop.get("amountInRaw"))
    decimals = _int_or_none(first_hop.get("inputDecimals")) or 18
    borrow_symbol = _text(first_hop.get("fromSymbol"))
    basis = _profit_basis(cost_model, borrow_symbol)
    repay_raw = None
    if principal_raw is not None:
        repay_raw = principal_raw + int(principal_raw * flash_fee_bps // 10_000)
    if basis == "borrow_token":
        min_profit_raw = 1
        gas_reserve_raw = 0
        min_profit_rule = "positive_x_profit_min_raw_unit"
    else:
        min_profit_raw = _raw_from_human(
            cost_model.get("minProfitUsd") or DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            decimals,
        ) or 0
        gas_reserve_raw = _raw_from_human(cost_model.get("gasReserveUsd") or DEFAULT_GAS_RESERVE_USD, decimals) or 0
        min_profit_rule = "stable_x_usd_threshold_in_borrow_token_units"
    return {
        "borrowToken": _text(first_hop.get("tokenIn")),
        "borrowSymbol": _text(first_hop.get("fromSymbol")),
        "borrowAmountRaw": str(principal_raw) if principal_raw is not None else None,
        "flashLoanFeeBps": flash_fee_bps,
        "repayAmountRaw": str(repay_raw) if repay_raw is not None else None,
        "finalToken": _text(last_hop.get("tokenOut")),
        "finalSymbol": _text(last_hop.get("toSymbol")),
        "finalAmountRaw": _text(last_hop.get("amountOutRaw")),
        "profitBasis": basis,
        "minProfitRaw": str(min_profit_raw),
        "gasReserveRaw": str(gas_reserve_raw),
        "minProfitRule": min_profit_rule,
    }


def _execution_model(hops: list[dict[str, Any]], repayment: dict[str, Any]) -> dict[str, Any]:
    hop_protocols = [_text(hop.get("protocol")).lower() for hop in hops if _text(hop.get("protocol"))]
    all_v4_hops = bool(hops) and all(protocol == "uniswap_v4" for protocol in hop_protocols)
    mixed_protocol_route = len(set(hop_protocols)) > 1
    uses_native_borrow = _is_zero_address(repayment.get("borrowToken"))
    needs_native_value_forwarding = (
        False
        if all_v4_hops
        else any(int(value) > 0 for value in (_text(hop.get("amountInRaw")) for hop in hops) if value.isdigit())
    )
    route_execution_mode = "pool_manager_direct_swap_settle_take" if all_v4_hops else "mixed_protocol_executor"
    return {
        "routeExecutionMode": route_execution_mode,
        "routeExecutionModeLabel": route_execution_mode,
        "allV4Hops": all_v4_hops,
        "mixedProtocolRoute": mixed_protocol_route,
        "requiresUniversalRouter": not all_v4_hops,
        "requiresDirectPoolManagerFlow": all_v4_hops,
        "usesNativeBorrow": uses_native_borrow,
        "requiresNativeValueForwarding": needs_native_value_forwarding,
        "nestedUnlockAvoidance": all_v4_hops,
        "status": "planned",
    }


def _execution_request(
    hops: list[dict[str, Any]],
    repayment: dict[str, Any],
    *,
    execution_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_model = execution_model or _execution_model(hops, repayment)
    swap_values: list[str] = []
    if execution_model.get("requiresDirectPoolManagerFlow"):
        swap_values = ["0" for _ in hops]
    else:
        for hop in hops:
            amount_in_raw = _text(hop.get("amountInRaw"))
            swap_values.append(amount_in_raw if _is_zero_address(hop.get("tokenIn")) else "0")
    requires_native_value_forwarding = bool(execution_model.get("requiresNativeValueForwarding"))
    uses_native_borrow = _is_zero_address(repayment.get("borrowToken"))
    function_name = "requestV4FlashLoanWithValues" if requires_native_value_forwarding else "requestV4FlashLoan"
    return {
        "executorContract": "RobinhoodPausedFlashLoanExecutor",
        "function": function_name,
        "struct": "V4FlashAccountingValueRequest" if function_name == "requestV4FlashLoanWithValues" else "V4FlashAccountingRequest",
        "token": _text(repayment.get("borrowToken")),
        "amount": _text(repayment.get("borrowAmountRaw")),
        "finalToken": _text(repayment.get("finalToken")),
        "minProfit": _text(repayment.get("minProfitRaw")),
        "gasReserve": _text(repayment.get("gasReserveRaw")),
        "transactionValueRaw": "0",
        "borrowTokenIsNative": uses_native_borrow,
        "requiresNativeValueForwarding": requires_native_value_forwarding,
        "nativeValueForwardingPolicy": "forward_amountInRaw_on_hops_where_tokenIn_is_zero_address",
        "routeExecutionMode": execution_model.get("routeExecutionMode"),
        "routeExecutionModeLabel": execution_model.get("routeExecutionModeLabel"),
        "routeExecutionModel": execution_model,
        "swapValues": swap_values,
        "calldataStatus": "pending",
    }


def _token_continuity_issues(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index in range(1, len(hops)):
        previous = hops[index - 1]
        current = hops[index]
        previous_token_out = _text(previous.get("tokenOut"))
        current_token_in = _text(current.get("tokenIn"))
        if previous_token_out.lower() != current_token_in.lower():
            issues.append(
                {
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromSymbol": previous.get("toSymbol"),
                    "toSymbol": current.get("fromSymbol"),
                    "previousTokenOut": previous_token_out,
                    "currentTokenIn": current_token_in,
                }
            )
    return issues


def _attempt_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    quote = _as_dict(attempt.get("quote"))
    review_summary = _as_dict(attempt.get("review_summary"))
    precheck = _as_dict(attempt.get("precheck"))
    return (
        _as_dict(quote.get("live_onchain_evidence"))
        or _as_dict(review_summary.get("onchain_evidence"))
        or _as_dict(precheck.get("onchain_evidence"))
    )


def _attempt_status(attempt: dict[str, Any]) -> str:
    for key in ("state", "priority_reason", "execution_phase"):
        text = _text(attempt.get(key))
        if text:
            return text
    evidence = _attempt_evidence(attempt)
    return _text(evidence.get("status")) or "unknown"


def _checklist_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = plan["evidence"]
    decision = plan["decision"]
    route = plan["route"]
    contract_dir = plan["contractHandoff"]["contractDir"]
    route_closed = bool(evidence.get("routeClosed"))
    ready_for_preflight = bool(decision.get("readyForTransactionPreflight"))
    trust_gap = not bool(decision.get("trustedForExecution"))
    return [
        {
            "id": "freeze_snapshot",
            "title": "鍐荤粨閾句笂澶嶆牳蹇収",
            "status": "done",
            "done": True,
            "detail": f"閿佸畾 {route['pathText']} / 瑙﹀彂鍖哄潡 {evidence.get('triggerBlockNumber')}",
        },
        {
            "id": "build_contract_dir",
            "title": "鍑嗗閾惧埆鍚堢害淇℃伅鐩綍",
            "status": "done" if contract_dir else "pending",
            "done": bool(contract_dir),
            "detail": "鍦?contract/robinhood 涓嬩繚鐣欓摼鍒俊鎭笌鍚庣画閮ㄧ讲鍗犱綅",
        },
        {
            "id": "calldata_builder",
            "title": "鐢熸垚浜ゆ槗 calldata",
            "status": "pending",
            "done": False,
            "detail": "鎸夐摼涓婂鏍歌矾寰勭敓鎴?flashloan / AMM 浜ゆ崲鍙傛暟",
        },
        {
            "id": "risk_guard",
            "title": "Add slippage and repayment constraints",
            "status": "pending" if route_closed else "blocked",
            "done": False,
            "detail": "Add minOut, slippage guard, repayment balance, and minimum-profit threshold",
        },
        {
            "id": "fork_staticcall",
            "title": "鎵ц fork/static-call 浠跨湡",
            "status": "pending" if ready_for_preflight else "blocked",
            "done": False,
            "detail": "鍙 Quoter 宸插鏍稿悗浠嶉渶鏈湴浠跨湡",
        },
        {
            "id": "broadcast_gate",
            "title": "淇濇寔骞挎挱闂搁棬鍏抽棴",
            "status": "blocked" if trust_gap else "pending",
            "done": False,
            "detail": "trustedForExecution is still false, so signing and broadcast remain disabled.",
        },
        {
            "id": "archive_outputs",
            "title": "褰掓。瀹炴柦鏂囨。涓庢祴璇曞寘",
            "status": "pending",
            "done": False,
            "detail": "瀹屾垚鍚庢妸鏂规銆佹竻鍗曘€佹祴璇曞寘褰掓。鍒?back",
        },
    ]

