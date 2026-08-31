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

from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
)
from intent_trade.chains.robinhood.chain_test_policy import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    robinhood_chain_test_scope,
)
from strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD


DEFAULT_ROBINHOOD_CHAIN_ID = ROBINHOOD_CHAIN_ID
DEFAULT_ROBINHOOD_NETWORK = ROBINHOOD_NETWORK
DEFAULT_ROBINHOOD_MARKET_ID = "robinhood-dex-shadow"
DEFAULT_ROBINHOOD_TESTNET_MARKET_ID = "robinhood-testnet-dex-paused"
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
        payload = json.loads(path.read_text(encoding="utf-8"))
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
    snapshot = _read_json(ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT)
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


def build_robinhood_chain_test_plan(attempt: dict[str, Any], *, contract_dir: Path | None = None) -> dict[str, Any]:
    evidence = _attempt_evidence(attempt)
    quote = _as_dict(attempt.get("quote"))
    precheck = _as_dict(attempt.get("precheck"))
    network = _text(evidence.get("network") or attempt.get("network") or DEFAULT_ROBINHOOD_NETWORK).lower()
    chain_id = int(evidence.get("chainId") or attempt.get("chain_id") or DEFAULT_ROBINHOOD_CHAIN_ID)
    default_market_id = (
        DEFAULT_ROBINHOOD_TESTNET_MARKET_ID
        if network == ROBINHOOD_TESTNET_NETWORK or chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else DEFAULT_ROBINHOOD_MARKET_ID
    )
    route_path = _route_path(attempt)
    blockers = _unique(
        [
            *(str(item) for item in evidence.get("blockers") or []),
            *(str(item) for item in precheck.get("reasons") or []),
            *(str(item) for item in attempt.get("blocked_reasons") or []),
        ]
    )
    route_closed = bool(route_path and route_path[0] == route_path[-1])
    latest_snapshot_route, latest_snapshot = _latest_snapshot_route(
        route_path,
        chain_id=chain_id,
        network=network,
    )
    requested_ready_for_preflight = bool(
        evidence.get("readyForTransactionPreflight")
        or evidence.get("ready_for_transaction_preflight")
        or precheck.get("ready_for_transaction_preflight")
    )
    trusted_for_execution = bool(
        evidence.get("trustedForExecution")
        or evidence.get("trusted_for_execution")
        or precheck.get("trustedForExecution")
    )
    slippage_bps = 50
    normalized_hops = _normalized_hop_rows(quote=quote, evidence=evidence, network=network)
    hop_summaries = _hop_summaries(normalized_hops, slippage_bps=slippage_bps)
    token_continuity_issues = _token_continuity_issues(hop_summaries)
    token_continuity_ok = not token_continuity_issues
    validation = _as_dict(evidence.get("validation"))
    cost_model = dict(_as_dict(evidence.get("costModel")))
    requested_borrow_amount_raw = _int_or_none(evidence.get("requestedBorrowInputAmountRaw"))
    selected_borrow_amount_raw = _int_or_none(evidence.get("selectedBorrowInputAmountRaw"))
    original_requested_borrow_amount_raw = requested_borrow_amount_raw
    repayment_borrow_amount_raw = None
    latest_snapshot_route_size_advice = {
        "latestSnapshotUpdatedAt": _text(latest_snapshot.get("updated_at")),
        "routeMaxExecutableAmountRaw": None,
        "routeExecutionSizeProbe": None,
        "routeExecutionSizeProbeStatus": "",
        "routeExecutableCandidate": None,
        "routeChainSubmissionCandidate": None,
        "routeRequestedBorrowAmountRaw": None,
        "routeExecutionSizeProbeExceeded": False,
        "routeExecutionSizeShortfallRaw": None,
    }
    latest_snapshot_route_max_executable_raw = None
    latest_snapshot_route_execution_probe = {}
    latest_snapshot_route_updated_at = _text(latest_snapshot.get("updated_at"))
    if hop_summaries:
        latest_snapshot_route_size_advice = _latest_snapshot_route_size_advice(
            latest_snapshot_route,
            latest_snapshot,
            requested_borrow_amount_raw=requested_borrow_amount_raw,
        )
        latest_snapshot_route_max_executable_raw = _int_or_none(
            latest_snapshot_route_size_advice.get("routeMaxExecutableAmountRaw")
        )
        latest_snapshot_route_execution_probe = _as_dict(
            latest_snapshot_route_size_advice.get("routeExecutionSizeProbe")
        )
        latest_snapshot_route_updated_at = _text(latest_snapshot_route_size_advice.get("latestSnapshotUpdatedAt"))
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = latest_snapshot_route_max_executable_raw
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = _int_or_none(hop_summaries[0].get("amountInRaw"))
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = requested_borrow_amount_raw
        repayment_borrow_amount_raw = selected_borrow_amount_raw
        if (
            repayment_borrow_amount_raw is not None
            and latest_snapshot_route_max_executable_raw is not None
            and repayment_borrow_amount_raw <= latest_snapshot_route_max_executable_raw
            and latest_snapshot_route_size_advice.get("routeExecutableCandidate") is False
        ):
            latest_snapshot_route_size_advice["routeExecutableCandidate"] = True
        if not cost_model:
            notional = _number(
                quote.get("input_amount")
                or quote.get("inputAmount")
                or _as_dict(quote.get("analysis")).get("notionalUsd")
                or hop_summaries[0].get("inputUsd")
            )
            if notional is None:
                notional = 1000.0
            final_hop = hop_summaries[-1]
            final_output_usd = _number(
                final_hop.get("referenceOutputUsd")
                or final_hop.get("outputUsd")
                or final_hop.get("amountOut")
            )
            flash_fee_bps = float(DEFAULT_FLASH_LOAN_FEE_BPS)
            flash_fee_usd = notional * flash_fee_bps / 10_000
            gas_reserve_usd = float(DEFAULT_GAS_RESERVE_USD)
            gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
            net_profit_usd = gross_profit_usd - flash_fee_usd - gas_reserve_usd if gross_profit_usd is not None else None
            min_profit_usd = max(float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD), 0.0)
            cost_model = {
                "notionalUsd": notional,
                "finalOutputUsd": final_output_usd,
                "grossProfitUsd": gross_profit_usd,
                "flashLoanFeeBps": flash_fee_bps,
                "flashLoanFeeUsd": flash_fee_usd,
                "gasReserveUsd": gas_reserve_usd,
                "offchainMinProfitUsd": min_profit_usd,
                "minProfitUsd": min_profit_usd,
                "submissionMinProfitUsd": flash_fee_usd,
                "submissionNetProfitUsd": gross_profit_usd - flash_fee_usd if gross_profit_usd is not None else None,
                "offchainNetProfitUsd": net_profit_usd,
                "netProfitUsd": net_profit_usd,
                "netProfitBps": (net_profit_usd / notional * 10_000) if net_profit_usd is not None and notional else None,
            }
        else:
            cost_model.setdefault("notionalUsd", _number(quote.get("input_amount") or quote.get("inputAmount")))
            if cost_model.get("finalOutputUsd") is None:
                cost_model["finalOutputUsd"] = _number(
                    hop_summaries[-1].get("referenceOutputUsd")
                    or hop_summaries[-1].get("outputUsd")
                    or hop_summaries[-1].get("amountOut")
                )
    if token_continuity_issues:
        blockers = _unique(
            [
                *blockers,
                "On-chain reviewed route has token discontinuity; calldata and fork/static-call preflight are blocked.",
            ]
        )
    route_size_probe_blocked = False
    if latest_snapshot_route and latest_snapshot_route_max_executable_raw is not None:
        if repayment_borrow_amount_raw is not None and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw:
            route_size_probe_blocked = True
            blockers = _unique(
                [
                    *blockers,
                    "latest_snapshot_route_execution_size_probe_exceeded",
                ]
            )
    if latest_snapshot_route and latest_snapshot_route_size_advice.get("routeExecutableCandidate") is False:
        route_size_probe_blocked = True
        blockers = _unique(
            [
                *blockers,
                "latest_snapshot_route_not_executable_candidate",
            ]
        )
    evidence_has_hops = bool(_as_list(evidence.get("hops")))
    all_hops_quoted = bool(validation.get("allHopsQuoted") if evidence_has_hops else hop_summaries) and token_continuity_ok
    profit_basis = _profit_basis(cost_model, route_path[0] if route_path else "")
    inferred_net_positive = _positive_number(
        cost_model.get("netProfitAmount") if profit_basis == "borrow_token" else cost_model.get("netProfitUsd")
    )
    net_positive_after_costs = bool(
        validation.get("netPositiveAfterCosts")
        if validation.get("netPositiveAfterCosts") is not None
        else (inferred_net_positive if inferred_net_positive is not None else validation.get("profitableAfterCosts"))
    )
    net_profit = _number(cost_model.get("netProfitUsd"))
    token_net_profit = _number(cost_model.get("netProfitAmount"))
    submission_net_profit = _number(cost_model.get("submissionNetProfitUsd"))
    token_submission_net_profit = _number(cost_model.get("submissionNetProfitAmount"))
    submission_min_profit = _number(cost_model.get("submissionMinProfitUsd"))
    offchain_min_profit = _number(cost_model.get("offchainMinProfitUsd"))
    inferred_meets_threshold = (
        token_submission_net_profit is not None and token_submission_net_profit > 0
        if profit_basis == "borrow_token"
        else submission_net_profit is not None and submission_net_profit > 0
    )
    validation_meets_submission = validation.get("meetsSubmissionProfitThreshold")
    if validation_meets_submission is None:
        validation_meets_submission = validation.get("meetsMinProfitThreshold")
    meets_min_profit_threshold = bool(
        validation_meets_submission
        if validation_meets_submission is not None
        else (inferred_meets_threshold if submission_min_profit is not None else requested_ready_for_preflight)
    )
    meets_offchain_profit_threshold = bool(
        validation.get("meetsOffchainProfitThreshold")
        if validation.get("meetsOffchainProfitThreshold") is not None
        else (net_profit is not None and offchain_min_profit is not None and net_profit >= offchain_min_profit)
    )
    ready_for_preflight = (
        requested_ready_for_preflight
        and meets_min_profit_threshold
        and route_closed
        and token_continuity_ok
        and all_hops_quoted
        and not route_size_probe_blocked
    )
    chain_scope = robinhood_chain_test_scope(
        network,
        chain_id,
    )
    if token_continuity_issues:
        preflight_status = "blocked_token_discontinuity"
    elif route_size_probe_blocked:
        preflight_status = "blocked_route_execution_size_probe"
    elif ready_for_preflight and not trusted_for_execution:
        preflight_status = "ready_for_fork_staticcall"
    else:
        preflight_status = "not_ready"
    repayment_constraint = _repayment_constraint(
        hop_summaries,
        flash_fee_bps=float(cost_model.get("flashLoanFeeBps") or DEFAULT_FLASH_LOAN_FEE_BPS),
        cost_model=cost_model,
    )
    execution_model = _execution_model(hop_summaries, repayment_constraint)
    execution_request = _execution_request(hop_summaries, repayment_constraint, execution_model=execution_model)
    plan = {
        "generatedAt": _utc_now_iso(),
        "network": network,
        "chainId": chain_id,
        "marketId": _text(attempt.get("market_id") or default_market_id),
        "chainTestScope": chain_scope,
        "attempt": {
            "state": _attempt_status(attempt),
            "pair": _text(attempt.get("pair") or quote.get("pair")),
            "pathText": " -> ".join(route_path) if route_path else _text(attempt.get("pair")),
            "routePath": route_path,
            "observedAt": _text(attempt.get("observed_at") or attempt.get("observedAt") or evidence.get("observedAt")),
            "createdAt": _text(attempt.get("created_at") or attempt.get("createdAt")),
            "triggerBlockNumber": evidence.get("triggerBlockNumber") or attempt.get("trigger_block_number"),
            "blockNumber": evidence.get("blockNumber"),
            "reviewBlockNumber": evidence.get("blockNumber"),
            "status": _text(evidence.get("status") or precheck.get("status") or attempt.get("state")),
        },
        "evidence": {
            "status": _text(evidence.get("status")),
            "executionTrust": _text(evidence.get("executionTrust")),
            "triggerBlockNumber": evidence.get("triggerBlockNumber")
            or _as_dict(evidence.get("triggerContext")).get("triggerBlockNumber")
            or _as_dict(evidence.get("triggerContext")).get("blockNumber")
            or attempt.get("trigger_block_number"),
            "blockNumber": evidence.get("blockNumber"),
            "trustedForExecution": trusted_for_execution,
            "readyForTransactionPreflight": ready_for_preflight,
            "readyForTransactionPreflightRaw": requested_ready_for_preflight,
            "routeClosed": route_closed,
            "allHopsQuoted": all_hops_quoted,
            "tokenContinuity": token_continuity_ok,
            "tokenContinuityIssues": token_continuity_issues,
            "profitableAfterCosts": net_positive_after_costs,
            "netPositiveAfterCosts": net_positive_after_costs,
            "meetsMinProfitThreshold": meets_min_profit_threshold,
            "meetsSubmissionProfitThreshold": meets_min_profit_threshold,
            "meetsOffchainProfitThreshold": meets_offchain_profit_threshold,
            "finalOutputSymbol": _text(validation.get("finalOutputSymbol") or (hop_summaries[-1].get("toSymbol") if hop_summaries else "")),
            "finalOutputUsd": cost_model.get("finalOutputUsd"),
            "grossProfitUsd": cost_model.get("grossProfitUsd"),
            "netProfitUsd": cost_model.get("netProfitUsd"),
            "netProfitBps": cost_model.get("netProfitBps"),
            "minProfitUsd": cost_model.get("minProfitUsd"),
            "latestSnapshotUpdatedAt": latest_snapshot_route_updated_at or _text(latest_snapshot.get("updated_at")),
            "routeOriginalRequestedBorrowAmountRaw": original_requested_borrow_amount_raw,
            "routeRequestedBorrowAmountRaw": repayment_borrow_amount_raw or requested_borrow_amount_raw,
            "routeSelectedBorrowAmountRaw": selected_borrow_amount_raw or repayment_borrow_amount_raw,
            "routeMaxExecutableAmountRaw": latest_snapshot_route_max_executable_raw,
            "routeExecutionSizeProbeStatus": latest_snapshot_route_size_advice.get("routeExecutionSizeProbeStatus"),
            "routeExecutableCandidate": latest_snapshot_route_size_advice.get("routeExecutableCandidate"),
            "routeChainSubmissionCandidate": latest_snapshot_route_size_advice.get("routeChainSubmissionCandidate"),
            "routeExecutionSizeProbe": latest_snapshot_route_execution_probe or None,
            "routeExecutionSizeProbeExceeded": bool(
                repayment_borrow_amount_raw is not None
                and latest_snapshot_route_max_executable_raw is not None
                and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw
            ),
            "routeExecutionSizeShortfallRaw": (
                repayment_borrow_amount_raw - latest_snapshot_route_max_executable_raw
                if repayment_borrow_amount_raw is not None
                and latest_snapshot_route_max_executable_raw is not None
                and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw
                else None
            ),
            "routeOriginalExecutionSizeProbeExceeded": latest_snapshot_route_size_advice.get("routeExecutionSizeProbeExceeded"),
            "routeOriginalExecutionSizeShortfallRaw": latest_snapshot_route_size_advice.get("routeExecutionSizeShortfallRaw"),
            "profitBasis": profit_basis,
            "borrowInputAmount": cost_model.get("borrowInputAmount"),
            "borrowInputSymbol": cost_model.get("borrowInputSymbol"),
            "finalOutputAmount": cost_model.get("finalOutputAmount"),
            "grossProfitAmount": cost_model.get("grossProfitAmount"),
            "submissionNetProfitAmount": cost_model.get("submissionNetProfitAmount"),
            "netProfitAmount": token_net_profit,
            "netProfitAmountSymbol": cost_model.get("netProfitAmountSymbol") or (route_path[0] if route_path else ""),
            "netProfitAmountBps": cost_model.get("netProfitAmountBps"),
            "liveProfitPercent": precheck.get("live_profit_percent"),
            "referenceNetProfitUsd": precheck.get("reference_profit_amount"),
            "blockers": blockers,
        },
        "route": {
            "path": route_path,
            "pathText": " -> ".join(route_path),
            "hopCount": len(hop_summaries) or max(0, len(route_path) - 1),
            "closed": route_closed,
            "tokenContinuous": token_continuity_ok,
            "analysisStage": _text(
                _as_dict(attempt.get("route")).get("analysisStage")
                or _as_dict(quote.get("route")).get("analysisStage")
            ),
        },
        "decision": {
            "canBroadcast": False,
            "canSubmitOrder": bool(attempt.get("can_submit_order")),
            "canRunForkPreflight": bool(chain_scope.get("canRunForkStaticcall")) and ready_for_preflight and not trusted_for_execution,
            "canDeployTestnet": bool(chain_scope.get("canDeployTestnet")),
            "trustedForExecution": trusted_for_execution,
            "readyForTransactionPreflight": ready_for_preflight,
            "requiresTransactionDevelopment": not trusted_for_execution,
            "needsLiveQuoter": bool(precheck.get("requires_live_quoter")),
            "blockedReasons": blockers,
        },
        "preflightSpec": {
            "status": preflight_status,
            "slippageBps": slippage_bps,
            "routeHops": hop_summaries,
            "repaymentConstraint": repayment_constraint,
            "executionModel": execution_model,
            "executionRequest": execution_request,
            "requiredChecks": [
                "calldata_matches_verified_hops",
                "each_hop_min_amount_out_enforced",
                "flashloan_repay_amount_enforced",
                "final_token_balance_covers_repay_and_min_profit",
                "fork_staticcall_or_fork_transaction_simulation_passed",
                "broadcast_gate_remains_disabled_until_trusted_for_execution",
            ],
            "broadcastEnabled": False,
        },
    }
    plan["checklist"] = _checklist_items(
        {
            "evidence": plan["evidence"],
            "decision": plan["decision"],
            "route": plan["route"],
            "contractHandoff": {"contractDir": str(contract_dir) if contract_dir else ""},
        }
    )
    plan["contractHandoff"] = {
        "contractDir": str(contract_dir) if contract_dir else "",
        "expectedFiles": ["README.md", "contracts.json"] if contract_dir else [],
        "executionModel": execution_model,
    }
    return plan


def render_robinhood_chain_test_summary(plan: dict[str, Any]) -> str:
    attempt = _as_dict(plan.get("attempt"))
    evidence = _as_dict(plan.get("evidence"))
    route = _as_dict(plan.get("route"))
    decision = _as_dict(plan.get("decision"))
    if decision.get("readyForTransactionPreflight"):
        conclusion = "Current evidence can enter fork/static-call preflight, but it is not broadcast-ready."
        next_steps = [
            "1. Pin the route and block snapshot.",
            "2. Generate calldata and minOut constraints.",
            "3. Run static-call or transaction simulation on a fork.",
            "4. Consider signing and broadcast only after preflight passes.",
        ]
    else:
        conclusion = "Current evidence is blocked by preflight prerequisites and cannot enter calldata or fork/static-call."
        next_steps = [
            "1. Rebuild the real closed route from candidate pools and token addresses.",
            "2. Re-run read-only on-chain quoter validation.",
            "3. Generate calldata and fork/static-call tasks after audit passes.",
            "4. Keep the broadcast gate closed.",
        ]
    lines = [
        "# Robinhood 閾句笂娆℃祴璇曞寘",
        "",
        f"- 缃戠粶锛歚{plan.get('network')}`",
        f"- 閾?ID锛歚{plan.get('chainId')}`",
        f"- 璺緞锛歚{route.get('pathText')}`",
        f"- Execution mode: `{_text(_as_dict(plan.get('preflightSpec')).get('executionModel', {}).get('routeExecutionMode'))}`",
        f"- 瑙﹀彂鍖哄潡锛歚{attempt.get('triggerBlockNumber')}`",
        f"- 澶嶆牳鍖哄潡锛歚{attempt.get('reviewBlockNumber')}`",
        f"- 璇佹嵁鐘舵€侊細`{evidence.get('status')}`",
        f"- Trusted for execution: `{'yes' if decision.get('trustedForExecution') else 'no'}`",
        f"- Ready for preflight: `{'yes' if decision.get('readyForTransactionPreflight') else 'no'}`",
        "",
        "## Size Gate",
        "",
        f"- Requested borrow raw: `{_text(evidence.get('routeRequestedBorrowAmountRaw'))}`",
        f"- Max executable raw: `{_text(evidence.get('routeMaxExecutableAmountRaw'))}`",
    ]
    if evidence.get("routeExecutionSizeProbeExceeded"):
        lines.append(f"- Shortfall raw: `{_text(evidence.get('routeExecutionSizeShortfallRaw'))}`")
    lines.extend(
        [
            "",
        "## 缁撹",
        "",
        conclusion,
        "",
        "## 鍏抽敭闃绘柇",
        ]
    )
    blockers = evidence.get("blockers") or decision.get("blockedReasons") or []
    if blockers:
        lines.extend([f"- {item}" for item in blockers])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            *next_steps,
        ]
    )
    return "\n".join(lines) + "\n"


def render_robinhood_chain_test_checklist(plan: dict[str, Any]) -> str:
    lines = ["# Robinhood 閾句笂娆℃祴璇?checklist", ""]
    for item in plan.get("checklist") or []:
        if not isinstance(item, dict):
            continue
        mark = "x" if item.get("done") else " "
        lines.append(f"- [{mark}] {item.get('title')}: {item.get('detail')}")
    return "\n".join(lines) + "\n"


def _chain_contract_handoff_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    network = _text(plan.get("network")).lower()
    execution_model = _as_dict(_as_dict(plan.get("preflightSpec")).get("executionModel"))
    common = {
        "network": plan["network"],
        "chainId": plan["chainId"],
        "marketId": plan["marketId"],
        "routePreflightStatus": plan["preflightSpec"]["status"],
        "repaymentConstraint": _as_dict(plan["preflightSpec"].get("repaymentConstraint")),
        "executionRequest": _as_dict(plan["preflightSpec"].get("executionRequest")),
        "executionModel": execution_model,
        "deploymentGate": "blocked_until_token_continuity_calldata_repayment_and_fork_staticcall_pass",
        "broadcastEnabled": False,
        "note": "read-only evidence package; no broadcast-ready executor has been deployed for this chain test",
    }
    if network == ROBINHOOD_TESTNET_NETWORK or plan.get("chainId") == ROBINHOOD_TESTNET_CHAIN_ID:
        return {
            **common,
            "scope": "robinhood_testnet_46630_only",
            "flashSourceKind": "uniswap_v4_poolmanager_flash_accounting",
            "v4PoolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "defaultV4Quoter": "0x8dc178efb8111bb0973dd9d722ebeff267c98f94",
            "v4UniversalRouter": "0x8876789976dEcBfCbBbe364623C63652db8C0904",
            "permit2": "0x000000000022D473030F116dDEE9F6B43aC78BA3",
            "candidatePoolSupplyEvidence": None,
            "morphoBlueStatus": "not_publicly_verified_on_testnet_46630",
            "aaveStatus": "not_publicly_verified_on_testnet_46630",
        }
    return {
        **common,
        "scope": "robinhood_mainnet_4663_only",
        "wrappedNative": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        "multicall3": "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1",
        "uniswapV2Pool": "0x8803c117ccae7B5146297876c2A25DF135141C4d",
        "v3Quoter": "0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7",
    }


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_robinhood_chain_test_package(
    output_dir: Path,
    attempt: dict[str, Any],
    *,
    contract_dir: Path | None = None,
) -> dict[str, Any]:
    plan = build_robinhood_chain_test_plan(attempt, contract_dir=contract_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(output_dir / "attempt.json", attempt)
    _json_dump(output_dir / "plan.json", plan)
    _json_dump(output_dir / "checklist.json", plan.get("checklist") or [])
    _json_dump(
        output_dir / "manifest.json",
        {
            "schemaVersion": "robinhood-chain-test-package-v1",
            "generatedAt": plan["generatedAt"],
            "network": plan["network"],
            "chainId": plan["chainId"],
            "files": ["attempt.json", "plan.json", "checklist.json", "summary.md"],
            "contractDir": str(contract_dir) if contract_dir else None,
        },
    )
    (output_dir / "summary.md").write_text(render_robinhood_chain_test_summary(plan), encoding="utf-8")
    if contract_dir is not None:
        contract_dir.mkdir(parents=True, exist_ok=True)
        _json_dump(contract_dir / "contracts.json", _chain_contract_handoff_metadata(plan))
        (contract_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Robinhood Contract Notes",
                    "",
                    f"- Chain: `{plan['network']}`",
                    f"- Chain ID: `{plan['chainId']}`",
                    f"- Market: `{plan['marketId']}`",
                    "- Current state: read-only evidence and preflight preparation only",
                    f"- Current route preflight status: `{plan['preflightSpec']['status']}`",
                    f"- Route execution mode: `{_text(plan['preflightSpec'].get('executionModel', {}).get('routeExecutionMode'))}`",
                    "- Default V4 quoter: `0x8dc178efb8111bb0973dd9d722ebeff267c98f94`",
                    "- Deployment gate: blocked until token continuity, calldata, repayment, and fork/static-call pass",
                    "- Deployment: no dedicated broadcast executor is added by this package",
                    "- Route test scope: calldata, slippage, repayment, and fork/static-call verification",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return plan
