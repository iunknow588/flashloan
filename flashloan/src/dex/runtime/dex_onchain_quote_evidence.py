from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import os
from typing import Any, Callable

from web3 import Web3

from bot.execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from bot.execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK
from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
)
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD

from dex.runtime.dex_quote_evidence_helpers import (
    DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
    DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS,
    _STABLE_DECIMALS,
    _TOKEN_DECIMALS_FALLBACK,
    _annotate_hop_price_impact,
    _as_dict,
    _block_timestamp,
    _checksum,
    _connect_robinhood,
    _connect_robinhood_family,
    _dex_error_stack_from_hop,
    _dex_error_stack_from_status,
    _decimal,
    _fee_bps,
    _hop_fee_bps,
    _human_from_raw,
    _int_or_none,
    _is_missing_historical_state_error,
    _is_pool_not_initialized_error,
    _is_robinhood_family,
    _load_v4_quote_adapter,
    _norm_symbol,
    _now_iso,
    _number,
    _offchain_initial_probe_amount_raw,
    _pool_for_step,
    _pool_map,
    _pool_side,
    _pool_side_by_address,
    _pool_state_v2,
    _positive_decimal_or_none,
    _probe_robinhood_testnet_route_execution_size,
    _protocol_key,
    _quote_v2_hop,
    _quote_v3_hop,
    _quote_v4_hop,
    _raw_from_human,
    _route_size_advice,
    _spot_amount_out_raw,
    _text,
    _token_continuity_issues,
    _token_decimals,
    _transaction_development,
    _trigger_block_number,
    _trigger_context_from_route,
    _unsupported_step_reason,
)


ERC20_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]

V2_PAIR_ABI = [
    {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "reserve0", "type": "uint112"},
            {"name": "reserve1", "type": "uint112"},
            {"name": "blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

ROBINHOOD_DEFAULT_V3_QUOTER = "0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"
def capture_onchain_trigger_context(
    *,
    chain_id: int | None,
    network: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    normalized_network = _text(network or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    context: dict[str, Any] = {
        "source": "scan_trigger_chain_head",
        "observedAt": observed,
        "network": normalized_network,
        "chainId": chain_id,
        "available": False,
        "blockNumber": None,
        "triggerBlockNumber": None,
        "blockTimestamp": None,
        "rpcUrl": None,
        "error": None,
    }
    if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID or normalized_network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.trigger_context import capture_robinhood_testnet_trigger_context

        return capture_robinhood_testnet_trigger_context(
            chain_id=chain_id,
            network=ROBINHOOD_TESTNET_NETWORK,
            observed_at=observed,
        )
    if chain_id != robinhood.CHAIN_ID and normalized_network != robinhood.NETWORK:
        context["error"] = "trigger block capture adapter is not configured for this chain"
        return context
    try:
        w3, rpc_url = _connect_robinhood()
        block_number = int(w3.eth.block_number)
        context.update(
            {
                "available": True,
                "blockNumber": block_number,
                "triggerBlockNumber": block_number,
                "blockTimestamp": _block_timestamp(w3, block_number),
                "rpcUrl": rpc_url,
            }
        )
    except Exception as exc:
        context["error"] = str(exc)
    return context


def _unsupported_evidence(
    *,
    chain_id: int | None,
    network: str,
    route: dict[str, Any],
    observed_at: str,
    reasons: list[str],
    trigger_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trigger = _trigger_context_from_route(route, trigger_context)
    return {
        "evidenceVersion": 1,
        "observedAt": observed_at,
        "network": network,
        "chainId": chain_id,
        "status": "not_live_verified",
        "executionTrust": "static_reference_only",
        "trustedForExecution": False,
        "blockNumber": None,
        "triggerContext": trigger,
        "triggerBlockNumber": _trigger_block_number(trigger),
        "quoteBlockSource": "unavailable",
        "routeSymbols": route.get("routeSymbols") or [],
        "routeAddresses": route.get("routeAddresses") or [],
        "hops": [],
        "costModel": {},
        "blockers": reasons,
        "transactionDevelopment": _transaction_development(False, reasons),
    }




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

    def _passes(evidence: dict[str, Any]) -> bool:
        validation = _as_dict(evidence.get("validation"))
        price_impact_gate = validation.get("allHopsWithinPriceImpactLimit", True)
        profit_gate = validation.get("profitableAfterCosts", True)
        return bool(
            _text(evidence.get("status")).lower() == "live_verified"
            and validation.get("allHopsQuoted", True) is True
            and validation.get("routeClosed", True) is True
            and validation.get("tokenContinuity", True) is True
            and profit_gate is True
            and price_impact_gate is True
        )

    def _failure_reason(evidence: dict[str, Any]) -> str | None:
        validation = _as_dict(evidence.get("validation"))
        status = _text(evidence.get("status")).lower()
        if validation.get("allHopsWithinPriceImpactLimit") is False and validation.get("allHopsQuoted") is True:
            return "price_impact_limit_exceeded"
        if validation.get("profitableAfterCosts") is False and validation.get("allHopsQuoted") is True:
            return "not_profitable_after_costs"
        if status == "slippage_limit_exceeded":
            return "price_impact_limit_exceeded"
        if status != "live_verified":
            return status or "live_quote_failed"
        if validation.get("allHopsQuoted") is not True:
            return "not_all_hops_quoted"
        if validation.get("routeClosed") is not True:
            return "route_not_closed"
        if validation.get("tokenContinuity") is not True:
            return "token_continuity_failed"
        if validation.get("allHopsWithinPriceImpactLimit") is not True:
            return "price_impact_limit_exceeded"
        if validation.get("profitableAfterCosts") is not True:
            return "not_profitable_after_costs"
        return None

    def _evaluate(amount_raw: int) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
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

    def _record(amount_raw: int, evidence: dict[str, Any]) -> tuple[bool, str | None]:
        validation = _as_dict(evidence.get("validation"))
        costs = _as_dict(evidence.get("costModel"))
        passed = _passes(evidence)
        reason = None if passed else _failure_reason(evidence)
        candidate_rows.append(
            {
                "attempt": attempts,
                "amountRaw": str(amount_raw),
                "status": "profitable_and_within_limits" if passed else "quoted_but_not_eligible",
                "allHopsQuoted": validation.get("allHopsQuoted") is True,
                "allHopsWithinPriceImpactLimit": validation.get("allHopsWithinPriceImpactLimit") is True,
                "profitableAfterCosts": validation.get("profitableAfterCosts") is True,
                "netProfitAmount": costs.get("netProfitAmount"),
                "netProfitAmountBps": costs.get("netProfitAmountBps"),
                "blockers": list(evidence.get("blockers") or []),
                "failureReason": reason,
            }
        )
        return passed, reason

    def _stop_without_size(reason: str) -> dict[str, Any]:
        search_status = (
            "pool_insufficient_for_profitable_execution"
            if reason == "price_impact_limit_exceeded"
            else "not_profitable_at_low_slippage"
            if reason == "not_profitable_after_costs"
            else reason
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
            "failureReason": reason,
        }

    lower_bound: int | None = None
    upper_bound: int | None = None
    first_ineligible_amount_raw: int | None = None
    expansion_stop_reason: str | None = None
    last_failure_reason: str | None = None
    probe = initial_probe_amount_raw
    while attempts < max_attempts and probe >= min_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason = _record(probe, evidence)
            if passed:
                lower_bound = probe
                break
            last_failure_reason = reason
            if reason not in {"price_impact_limit_exceeded", "not_profitable_after_costs"}:
                return _stop_without_size(reason or "not_eligible")
            if reason == "not_profitable_after_costs":
                return _stop_without_size(reason)
            upper_bound = probe
            first_ineligible_amount_raw = probe
        except Exception as exc:
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": "quote_failed",
                    "error": str(exc),
                }
            )
            return _stop_without_size("live_quote_failed")
        if lower_bound is not None:
            break
        next_probe = max(min_probe_amount_raw, probe // 2)
        if next_probe == probe:
            break
        probe = next_probe

    if lower_bound is None:
        search_status = (
            "not_profitable_at_low_slippage"
            if last_failure_reason == "not_profitable_after_costs"
            else "pool_insufficient_for_profitable_execution"
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
            "failureReason": f"after up to {max_attempts} half-size attempts, no amount passed live quote, 10 bps per-hop impact, and positive net-profit gates",
        }

    probe = min(max_probe_amount_raw, max(lower_bound * 2, lower_bound + 1))
    while attempts < max_attempts and probe <= max_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason = _record(probe, evidence)
            if passed:
                lower_bound = probe
                if probe >= max_probe_amount_raw:
                    break
                next_probe = min(max_probe_amount_raw, max(probe * 2, probe + 1))
                if next_probe == probe:
                    break
                probe = next_probe
                continue
            first_ineligible_amount_raw = probe
            expansion_stop_reason = reason or "not_eligible"
            upper_bound = probe if reason == "price_impact_limit_exceeded" else None
            break
        except Exception as exc:
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": "quote_failed",
                    "error": str(exc),
                }
            )
            first_ineligible_amount_raw = probe
            expansion_stop_reason = "live_quote_failed"
            upper_bound = None
            break

    if upper_bound is not None and lower_bound is not None:
        while upper_bound - lower_bound > 1 and attempts < max_attempts:
            mid = (upper_bound + lower_bound) // 2
            try:
                evidence = _evaluate(mid)
                passed, _ = _record(mid, evidence)
                if passed:
                    lower_bound = mid
                else:
                    upper_bound = mid
            except Exception as exc:
                candidate_rows.append(
                    {
                        "attempt": attempts,
                        "amountRaw": str(mid),
                        "status": "quote_failed",
                        "error": str(exc),
                    }
                )
                upper_bound = mid

    return {
        "status": "route_execution_size_probe_complete",
        "searchStatus": "profitable_amount_found",
        "probeAttempts": attempts,
        "maxProbeAttempts": max_attempts,
        "requestedAmountRaw": str(target_amount_raw),
        "initialProbeAmountRaw": str(initial_probe_amount_raw),
        "selectedProfitableAmountRaw": str(lower_bound),
        "maxExecutableAmountRaw": str(lower_bound),
        "firstFailureAmountRaw": str(upper_bound or first_ineligible_amount_raw) if (upper_bound or first_ineligible_amount_raw) is not None else (str(target_amount_raw) if lower_bound != target_amount_raw else None),
        "attempts": candidate_rows,
        "failureReason": None,
        "expansionStopReason": expansion_stop_reason,
    }

def build_route_onchain_evidence(
    route: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    chain_id: int | None = None,
    observed_at: str | None = None,
    trigger_context: dict[str, Any] | None = None,
    quote_hop_provider: QuoteHopProvider | None = None,
    borrow_input_amount_override_raw: int | None = None,
    skip_execution_size_probe: bool = False,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    table = payload.get("poolStaticTable") if isinstance(payload, dict) and isinstance(payload.get("poolStaticTable"), dict) else {}
    network = _text(table.get("network") or route.get("network") or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    trigger = _trigger_context_from_route(route, trigger_context)
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    steps = [step for step in analysis.get("steps") or [] if isinstance(step, dict)]
    if not _is_robinhood_family(chain_id, network):
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=["current chain quoter review adapter is not connected; record is static observation only."],
            trigger_context=trigger,
        )
    unsupported = [
        reason
        for step in steps
        for reason in [_unsupported_step_reason(step, _pool_for_step(step, _pool_map(route)))]
        if reason
    ]
    if unsupported:
        return _unsupported_evidence(chain_id=chain_id, network=network, route=route, observed_at=observed, reasons=unsupported, trigger_context=trigger)

    trigger_block_number = _trigger_block_number(trigger)
    if trigger_block_number is None:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=["missing trigger block context; later latest price cannot replace the trigger-time price."],
            trigger_context=trigger,
        )

    pools = _pool_map(route)
    quoter = _text(table.get("quoter") or robinhood.default_v3_quoter_address(ROBINHOOD_DEFAULT_V3_QUOTER))
    token_cache: dict[str, int] = {}
    try:
        w3, rpc_url = _connect_robinhood_family(chain_id, network)
        block_number = trigger_block_number
    except Exception as exc:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=[f"on-chain RPC verification failed: {exc}"],
            trigger_context=trigger,
        )

    input_symbol = _norm_symbol(route_symbols[0] if route_symbols else analysis.get("inputSymbol"))
    first_pool = _pool_for_step(steps[0], pools) if steps else {}
    input_address, _, input_decimals = _pool_side(first_pool, input_symbol)
    input_decimals = input_decimals if input_decimals is not None else _token_decimals(w3, input_address, input_symbol, token_cache)
    route_closed = bool(route_symbols and route_symbols[0] == route_symbols[-1])
    route_size_execution_probe, route_max_executable_raw, route_first_failure_raw, route_recommended_raw = _route_size_advice(route)
    reference_input_amount = _positive_decimal_or_none(analysis.get("referenceInputAmount"))
    borrow_input_amount = (
        reference_input_amount
        if route_closed and reference_input_amount is not None
        else _positive_decimal_or_none(analysis.get("notionalUsd"))
        or Decimal("1000")
    )
    requested_borrow_input_amount_raw = (
        int(borrow_input_amount_override_raw)
        if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0
        else _raw_from_human(borrow_input_amount, input_decimals)
    )
    offchain_initial_probe_amount_raw, offchain_initial_probe_basis, offchain_initial_probe_reason, _ = (
        _offchain_initial_probe_amount_raw(
            route,
            requested_amount_raw=requested_borrow_input_amount_raw,
            input_decimals=input_decimals,
            analysis=analysis,
            pools=pools,
        )
    )
    if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0:
        selected_borrow_input_amount_raw = int(borrow_input_amount_override_raw)
    elif route_recommended_raw is not None and route_recommended_raw > 0:
        selected_borrow_input_amount_raw = int(route_recommended_raw)
    elif route_max_executable_raw is not None and route_max_executable_raw > 0:
        selected_borrow_input_amount_raw = int(route_max_executable_raw)
    else:
        selected_borrow_input_amount_raw = int(requested_borrow_input_amount_raw)
    borrow_input_amount = Decimal(int(selected_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))
    execution_size_probe: dict[str, Any] | None = route_size_execution_probe or None
    route_has_size_metadata = bool(route_max_executable_raw or route_first_failure_raw or route_size_execution_probe)
    all_v4_route = bool(steps) and all(
        str(step.get("protocol") or step.get("dex") or "").strip().lower() == "uniswap_v4"
        for step in steps
    )
    testnet_closed_loop_v4_route = bool(
        chain_id == ROBINHOOD_TESTNET_CHAIN_ID and route_closed and all_v4_route
    )
    if (
        not skip_execution_size_probe
        and chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        and selected_borrow_input_amount_raw > 0
        and (route_has_size_metadata or testnet_closed_loop_v4_route)
    ):
        probe_amount_raw = offchain_initial_probe_amount_raw
        # Off-chain sizing chooses the starting point and passes it to the
        # on-chain probe as an explicit parameter. The probe halves on failed
        # attempts and doubles on successful attempts from that starting point.
        execution_size_probe = _probe_robinhood_testnet_route_execution_size(
            route,
            payload=payload,
            chain_id=chain_id,
            observed_at=observed,
            trigger_context=trigger,
            quote_hop_provider=quote_hop_provider,
            requested_amount_raw=probe_amount_raw,
            initial_probe_amount_raw=offchain_initial_probe_amount_raw,
        )
        try:
            probed_amount_raw = int(execution_size_probe.get("maxExecutableAmountRaw") or 0)
        except (TypeError, ValueError):
            probed_amount_raw = None
        if probed_amount_raw is not None and probed_amount_raw > 0:
            selected_borrow_input_amount_raw = probed_amount_raw
            borrow_input_amount = Decimal(probed_amount_raw) / (Decimal(10) ** int(input_decimals))

    hop_provider = quote_hop_provider
    hop_rows: list[dict[str, Any]] = []
    amount_in_raw: int | None = None
    blockers: list[str] = []
    amount_in_raw = selected_borrow_input_amount_raw
    for step in steps:
        pool = _pool_for_step(step, pools)
        protocol = _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee"), pool.get("protocol"), pool.get("dex"))
        hop_number = int(step.get("hop") or len(hop_rows) + 1)
        try:
            if hop_provider is not None:
                hop = hop_provider(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v2":
                hop = _quote_v2_hop(w3=w3, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v4":
                hop = _quote_v4_hop(
                    w3=w3,
                    step=step,
                    pool=pool,
                    amount_in_raw=amount_in_raw,
                    block_number=block_number,
                    token_cache=token_cache,
                    chain_id=chain_id,
                    network=network,
                )
            else:
                hop = _quote_v3_hop(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
        except Exception as exc:
            if _is_missing_historical_state_error(exc):
                hop_rows.append(
                    {
                        "hop": hop_number,
                        "status": "live_evidence_deferred",
                        "executionTrust": "static_reference_only",
                        "quoteSource": "historical_state_unavailable",
                        "quoteMethod": f"{protocol}_exact_input",
                        "protocol": protocol,
                        "pool": step.get("pool") or pool.get("pool"),
                        "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                        "toSymbol": _norm_symbol(step.get("toSymbol")),
                        "amountInRaw": str(amount_in_raw),
                        "amountOutRaw": None,
                        "liveQuoteError": str(exc),
                        "errorStack": _dex_error_stack_from_hop(
                            {
                                "hop": hop_number,
                                "status": "live_evidence_deferred",
                                "liveQuoteError": str(exc),
                                "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                                "toSymbol": _norm_symbol(step.get("toSymbol")),
                            },
                            index=hop_number,
                        ),
                    }
                )
                blockers.append(
                    f"绗?{hop_number} 璺冲巻鍙插尯鍧?{block_number} 鐨?trie 鐘舵€佷笉鍙敤锛屽凡寤跺悗澶嶆牳锛屼笉鍒ゅ畾涓鸿矾绾垮け璐ワ細{exc}"
                )
                break
            hop_status = "pool_not_initialized" if _is_pool_not_initialized_error(exc) else "live_quote_failed"
            quote_source = "chain_pool_not_initialized" if hop_status == "pool_not_initialized" else "chain_live_quote_failed"
            hop_rows.append(
                {
                    "hop": hop_number,
                    "status": hop_status,
                    "executionTrust": hop_status,
                    "quoteSource": quote_source,
                    "quoteMethod": f"{protocol}_exact_input",
                    "protocol": protocol,
                    "pool": step.get("pool") or pool.get("pool"),
                    "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                    "toSymbol": _norm_symbol(step.get("toSymbol")),
                    "tokenIn": step.get("tokenIn") or _pool_side(pool, _norm_symbol(step.get("fromSymbol")))[0],
                    "tokenOut": step.get("tokenOut") or _pool_side(pool, _norm_symbol(step.get("toSymbol")))[0],
                    "amountInRaw": str(amount_in_raw),
                    "amountOutRaw": None,
                    "liveQuoteError": str(exc),
                    "errorStack": _dex_error_stack_from_hop(
                        {
                            "hop": hop_number,
                            "status": hop_status,
                            "liveQuoteError": str(exc),
                            "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                            "toSymbol": _norm_symbol(step.get("toSymbol")),
                        },
                        index=hop_number,
                    ),
                    "staticQuote": {
                        "outputAmount": step.get("outputAmount"),
                        "outputUsd": step.get("outputUsd"),
                        "quoteSource": step.get("quoteSource"),
                        "executionTrust": step.get("executionTrust"),
                    },
                }
            )
            if hop_status == "pool_not_initialized":
                blockers.append(f"V4 pool is not initialized or cannot be quoted at hop {hop_number} {_norm_symbol(step.get('fromSymbol'))}->{_norm_symbol(step.get('toSymbol'))}: {exc}")
            else:
                blockers.append(f"hop {hop_number} {_norm_symbol(step.get('fromSymbol'))}->{_norm_symbol(step.get('toSymbol'))} verification failed: {exc}")
            break
        hop["hop"] = hop_number
        hop["fromSymbol"] = _norm_symbol(step.get("fromSymbol"))
        hop["toSymbol"] = _norm_symbol(step.get("toSymbol"))
        hop["staticQuote"] = {
            "outputAmount": step.get("outputAmount"),
            "outputUsd": step.get("outputUsd"),
            "quoteSource": step.get("quoteSource"),
            "executionTrust": step.get("executionTrust"),
        }
        hop = _annotate_hop_price_impact(
            hop,
            step=step,
            pool=pool,
            limit_bps=DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
        )
        hop_rows.append(hop)
        amount_in_raw = int(hop["amountOutRaw"])

    token_continuity_issues = _token_continuity_issues(hop_rows)
    token_continuity_ok = not token_continuity_issues
    if token_continuity_issues:
        blockers.append("on-chain verified token addresses are inconsistent; cannot enter transaction preflight")
    reference_quote_only = any(
        _text(hop.get("sdkAdapter") or hop.get("quoteAdapter")).lower() == "reference_quote_adapter"
        or _text(hop.get("quoteMethod")).lower() == "uniswap_v4_reference_quote"
        for hop in hop_rows
    )
    if reference_quote_only:
        blockers.append("V4 route uses reference_quote_adapter; must connect real V4 Quoter/SDK or swap static-call before transaction preflight")
    all_hops_verified = bool(steps) and len(hop_rows) == len(steps) and token_continuity_ok and not any(
        _text(hop.get("status")).lower() in {"live_quote_failed", "quote_failed"}
        for hop in hop_rows
    ) and not reference_quote_only
    hop_price_impact_rows = [
        hop for hop in hop_rows if isinstance(hop, dict) and hop.get("amountOutRaw") is not None
    ]
    all_hops_within_price_impact_limit = bool(hop_price_impact_rows) and len(hop_price_impact_rows) == len(steps) and all(
        hop.get("priceImpactWithinLimit") is True for hop in hop_price_impact_rows
    )
    price_impact_issues = [
        {
            "hop": hop.get("hop"),
            "fromSymbol": hop.get("fromSymbol"),
            "toSymbol": hop.get("toSymbol"),
            "priceImpactBps": hop.get("priceImpactBps"),
            "limitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "error": hop.get("priceImpactError"),
        }
        for hop in hop_rows
        if hop.get("priceImpactWithinLimit") is not True
    ]
    historical_state_unavailable = any(
        _text(hop.get("status")).lower() == "live_evidence_deferred"
        for hop in hop_rows
    )
    if all_hops_verified and not all_hops_within_price_impact_limit:
        blockers.append(
            f"at least one hop price impact cannot be confirmed or exceeds {DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS} bps",
        )
    final_symbol = _norm_symbol(route_symbols[-1] if route_symbols else analysis.get("outputSymbol"))
    final_hop = hop_rows[-1] if all_hops_verified and hop_rows else None
    final_decimals = int(final_hop["outputDecimals"]) if final_hop and final_hop.get("outputDecimals") is not None else None
    final_amount = Decimal(final_hop["amountOut"]) if final_hop and final_hop.get("amountOut") is not None else None
    borrow_asset_is_stable = bool(
        analysis.get("borrowAssetIsStable")
        if analysis.get("borrowAssetIsStable") is not None
        else input_symbol in _STABLE_DECIMALS
    )
    notional = (
        borrow_input_amount
        if borrow_asset_is_stable
        else (_positive_decimal_or_none(analysis.get("notionalUsd")) or Decimal("1000"))
    )
    token_gross_profit = (
        final_amount - borrow_input_amount
        if all_hops_verified and route_closed and final_symbol == input_symbol and final_amount is not None
        else None
    )
    token_flash_fee = borrow_input_amount * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    token_submission_net_profit = token_gross_profit - token_flash_fee if token_gross_profit is not None else None
    token_net_profit = token_submission_net_profit
    token_net_profit_bps = (
        token_net_profit / borrow_input_amount * Decimal(10_000)
        if token_net_profit is not None and borrow_input_amount > 0
        else None
    )
    final_output_usd = final_amount if borrow_asset_is_stable and final_symbol in _STABLE_DECIMALS and final_amount is not None else None
    gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
    flash_fee = notional * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    gas_reserve = Decimal(str(DEFAULT_GAS_RESERVE_USD))
    submission_net_profit_usd = gross_profit_usd - flash_fee if gross_profit_usd is not None else None
    net_profit_usd = submission_net_profit_usd - gas_reserve if submission_net_profit_usd is not None else None
    net_profit_bps = net_profit_usd / notional * Decimal(10_000) if net_profit_usd is not None and notional > 0 else None
    min_profit_usd = Decimal(str(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD))
    net_positive_after_costs = bool(
        all_hops_verified
        and all_hops_within_price_impact_limit
        and (
            (borrow_asset_is_stable and submission_net_profit_usd is not None and submission_net_profit_usd > 0)
            or (not borrow_asset_is_stable and token_submission_net_profit is not None and token_submission_net_profit > 0)
        )
    )
    meets_submission_profit_threshold = bool(
        all_hops_verified
        and all_hops_within_price_impact_limit
        and (
            (borrow_asset_is_stable and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
            or (not borrow_asset_is_stable and token_submission_net_profit is not None and token_submission_net_profit > 0)
        )
    )
    if not all_hops_verified and not blockers:
        blockers.append("on-chain verification did not cover all hops; cannot enter preflight")
    if all_hops_verified and not net_positive_after_costs:
        blockers.append("on-chain computed profit is not positive after flash loan fees; cannot enter preflight")
    elif all_hops_verified and borrow_asset_is_stable and not meets_submission_profit_threshold:
        blockers.append("stable-coin route on-chain net profit is below the $6 gate; cannot enter preflight")
    if borrow_asset_is_stable and final_output_usd is None:
        blockers.append("final output is not stable coin or lacks reliable USD valuation; cannot enter automatic transaction preflight")
    pool_insufficient_for_profitable_execution = bool(
        execution_size_probe
        and execution_size_probe.get("searchStatus") == "pool_insufficient_for_profitable_execution"
    )
    pool_not_initialized = bool(
        any(_text(hop.get("status")).lower() == "pool_not_initialized" for hop in hop_rows)
        or bool(
            execution_size_probe
            and execution_size_probe.get("searchStatus") == "pool_not_initialized"
        )
    )
    if pool_insufficient_for_profitable_execution:
        blockers.append("current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee")
    if pool_not_initialized:
        blockers.append("V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate")
    evidence_status = (
        "pool_not_initialized"
        if pool_not_initialized
        else (
            "live_evidence_deferred"
        if historical_state_unavailable
        else (
            "pool_insufficient_for_profitable_execution"
        if pool_insufficient_for_profitable_execution
        else (
            "slippage_limit_exceeded"
            if all_hops_verified and not all_hops_within_price_impact_limit
            else (
                "route_unprofitable"
                if all_hops_verified and all_hops_within_price_impact_limit and not net_positive_after_costs
                else ("live_verified" if all_hops_verified else ("live_quote_reference_only" if reference_quote_only else "live_quote_failed"))
            )
        )
        )
        )
    )
    return {
        "evidenceVersion": 1,
        "observedAt": observed,
        "network": network,
        "chainId": chain_id,
        "rpcUrl": rpc_url,
        "blockNumber": block_number,
        "blockTimestamp": _block_timestamp(w3, block_number),
        "triggerContext": trigger,
        "triggerBlockNumber": trigger_block_number,
        "quoteBlockSource": "trigger_block",
        "quoter": quoter,
        "routeSymbols": route_symbols,
        "routeAddresses": route.get("routeAddresses") or [],
        "requestedBorrowInputAmountRaw": str(requested_borrow_input_amount_raw),
        "offchainInitialProbeAmountRaw": str(offchain_initial_probe_amount_raw),
        "offchainInitialProbeBasis": offchain_initial_probe_basis,
        "offchainInitialProbeReason": offchain_initial_probe_reason,
        "selectedBorrowInputAmountRaw": str(selected_borrow_input_amount_raw),
        "executionSizeProbe": execution_size_probe,
        "status": evidence_status,
        "executionTrust": "live_quoter_verified_readonly" if all_hops_verified else "static_reference_only",
        "trustedForExecution": False,
        "priceEvidenceMode": "live_verified" if all_hops_verified else "live_required",
        "priceEvidenceSource": "chain_live_quote" if all_hops_verified else "chain_live_quote_required",
        "livePriceVerified": bool(all_hops_verified),
        "readyForTransactionPreflight": meets_submission_profit_threshold,
        "hops": hop_rows,
        "costModel": {
            "notionalUsd": float(notional),
            "requestedBorrowInputAmountRaw": int(requested_borrow_input_amount_raw),
            "requestedBorrowInputAmount": float(Decimal(int(requested_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))),
            "selectedBorrowInputAmountRaw": int(selected_borrow_input_amount_raw),
            "selectedBorrowInputAmount": float(borrow_input_amount),
            "borrowInputAmount": float(borrow_input_amount),
            "borrowInputSymbol": input_symbol,
            "finalOutputAmount": float(final_amount) if final_amount is not None else None,
            "finalOutputSymbol": final_symbol,
            "grossProfitAmount": float(token_gross_profit) if token_gross_profit is not None else None,
            "flashLoanFeeAmount": float(token_flash_fee),
            "submissionNetProfitAmount": float(token_submission_net_profit) if token_submission_net_profit is not None else None,
            "netProfitAmount": float(token_net_profit) if token_net_profit is not None else None,
            "netProfitAmountSymbol": input_symbol,
            "netProfitAmountBps": float(token_net_profit_bps) if token_net_profit_bps is not None else None,
            "profitBasis": "usd" if borrow_asset_is_stable else "borrow_token",
            "tokenGasExcluded": not borrow_asset_is_stable,
            "finalOutputUsd": float(final_output_usd) if final_output_usd is not None else None,
            "grossProfitUsd": float(gross_profit_usd) if gross_profit_usd is not None else None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": float(flash_fee),
            "gasReserveUsd": float(gas_reserve),
            "offchainMinProfitUsd": float(min_profit_usd),
            "minProfitUsd": float(min_profit_usd),
            "submissionMinProfitUsd": float(flash_fee),
            "submissionNetProfitUsd": float(submission_net_profit_usd) if submission_net_profit_usd is not None else None,
            "offchainNetProfitUsd": float(net_profit_usd) if net_profit_usd is not None else None,
            "netProfitUsd": float(net_profit_usd) if net_profit_usd is not None else None,
            "netProfitBps": float(net_profit_bps if borrow_asset_is_stable else token_net_profit_bps)
            if (net_profit_bps if borrow_asset_is_stable else token_net_profit_bps) is not None
            else None,
            "hopPriceImpactLimitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "maxHopPriceImpactBps": max(
                (float(hop.get("priceImpactBps")) for hop in hop_rows if _number(hop.get("priceImpactBps")) is not None),
                default=None,
            ),
            "priceImpactIssues": price_impact_issues,
        },
        "validation": {
            "allHopsQuoted": all_hops_verified,
            "allHopsWithinPriceImpactLimit": all_hops_within_price_impact_limit,
            "hopPriceImpactLimitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "priceImpactIssues": price_impact_issues,
            "routeClosed": route_closed,
            "tokenContinuity": token_continuity_ok,
            "tokenContinuityIssues": token_continuity_issues,
            "finalOutputSymbol": final_symbol,
            "finalOutputDecimals": final_decimals,
            "profitableAfterCosts": net_positive_after_costs,
            "netPositiveAfterCosts": net_positive_after_costs,
            "meetsMinProfitThreshold": meets_submission_profit_threshold,
            "meetsSubmissionProfitThreshold": meets_submission_profit_threshold,
            "meetsOffchainProfitThreshold": bool(
                all_hops_verified and net_profit_usd is not None and net_profit_usd >= min_profit_usd
            ),
            "borrowAssetClass": "stable" if borrow_asset_is_stable else "non_stable",
            "borrowAssetIsStable": borrow_asset_is_stable,
            "borrowInputAmount": float(borrow_input_amount),
            "requestedBorrowInputAmount": float(Decimal(int(requested_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))),
            "borrowInputSymbol": input_symbol,
            "finalOutputAmount": float(final_amount) if final_amount is not None else None,
            "netProfitAmount": float(token_net_profit) if token_net_profit is not None else None,
            "netProfitAmountSymbol": input_symbol,
            "nonStablePositiveXProfitPolicy": bool(not borrow_asset_is_stable),
            "executionSizeSearchStatus": execution_size_probe.get("searchStatus") if execution_size_probe else None,
            "poolInsufficientForProfitableExecution": pool_insufficient_for_profitable_execution,
            "poolNotInitialized": pool_not_initialized,
        },
        "blockers": blockers,
        "errorStack": _dex_error_stack_from_status(
            evidence_status,
            message=blockers[0] if blockers else "OK",
            raw_error=blockers[0] if blockers else None,
            details={
                "status": evidence_status,
                "blockers": blockers,
                "executionSizeProbe": execution_size_probe,
            },
        ),
        "transactionDevelopment": _transaction_development(meets_submission_profit_threshold, blockers),
    }


__all__ = ["build_route_onchain_evidence", "capture_onchain_trigger_context"]
