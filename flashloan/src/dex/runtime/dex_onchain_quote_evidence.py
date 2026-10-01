from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import json
import os
from pathlib import Path
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

from dex.runtime.dex_onchain_quote_evidence_official_v4 import official_v4_arb_static_hop_rows
from dex.runtime.dex_quote_evidence_helpers import (
    DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
    DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS,
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
    _has_not_enough_liquidity_blocker,
    _int_or_none,
    _is_missing_historical_state_error,
    _is_missing_v4_adapter_error,
    _is_not_enough_liquidity_error,
    _is_pool_not_initialized_error,
    _is_rpc_verification_error,
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
    _robinhood_network_for_chain_id,
    _text,
    _token_continuity_issues,
    _token_decimals,
    _transaction_development,
    _trigger_block_number,
    _trigger_context_from_route,
    _unsupported_step_reason,
)

_STABLE_ANCHOR_SYMBOLS = {
    "AUSD", "BUSD", "DAI", "EURC", "FDUSD", "FRAX", "PYUSD", "RLUSD", "USDC",
    "USDD", "USDE", "USDG", "USDT",
}


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
    status: str | None = None,
) -> dict[str, Any]:
    trigger = _trigger_context_from_route(route, trigger_context)
    joined = " ".join(reasons)
    lower = joined.lower()
    if status in {
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "pool_not_initialized",
        "not_enough_liquidity",
        "live_quote_failed",
        "adapter_required",
        "missing_trigger_block",
    }:
        pass
    elif _is_rpc_verification_error(ValueError(joined)):
        status = _is_rpc_verification_error(ValueError(joined))
    elif _is_not_enough_liquidity_error(ValueError(joined)):
        status = "not_enough_liquidity"
    elif _is_pool_not_initialized_error(ValueError(joined)):
        status = "pool_not_initialized"
    elif "adapter" in lower and "required" in lower:
        status = "adapter_required"
    elif "trigger block" in lower or "block context" in lower:
        status = "missing_trigger_block"
    else:
        status = "not_live_verified"
    execution_trust = (
        status
        if status in {"rpc_verification_failed", "rpc_verification_timeout", "missing_trigger_block", "not_enough_liquidity", "pool_not_initialized", "live_quote_failed"}
        else "static_reference_pending_live_review"
    )
    return {
        "evidenceVersion": 1,
        "observedAt": observed_at,
        "network": network,
        "chainId": chain_id,
        "status": status,
        "executionTrust": execution_trust,
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
        "errorStack": _dex_error_stack_from_status(
            status,
            message=reasons[0] if reasons else "OK",
            raw_error=reasons[0] if reasons else None,
            details={"reasons": reasons, "status": status},
        ),
    }


def _first_pool_for_edge(
    *,
    pools: dict[str, dict[str, Any]],
    from_symbol: str,
    to_symbol: str,
    from_address: str = "",
    to_address: str = "",
) -> dict[str, Any]:
    candidates = list(pools.values())
    for pool in candidates:
        from_side = _pool_side(pool, from_symbol)
        to_side = _pool_side(pool, to_symbol)
        if not from_side[0] or not to_side[0]:
            continue
        if from_address and from_side[0].lower() != from_address.lower():
            continue
        if to_address and to_side[0].lower() != to_address.lower():
            continue
        if from_side[0] and to_side[0]:
            return pool
    return {}


def _complete_route_steps(
    route_symbols: list[str],
    route_addresses: list[str],
    steps: list[dict[str, Any]],
    pools: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(route_symbols) < 2:
        return steps
    # Only synthesize omitted hops when the route supplies a complete pool
    # set. A partial fixture may intentionally describe only the verified hop.
    if len(pools) < len(route_symbols) - 1:
        return steps
    by_hop: dict[int, dict[str, Any]] = {}
    for index, step in enumerate(steps):
        hop_number = int(step.get("hop") or index + 1)
        by_hop[hop_number] = step
    completed: list[dict[str, Any]] = []
    for index, (from_symbol, to_symbol) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
        step = by_hop.get(index)
        if not isinstance(step, dict):
            from_address = route_addresses[index - 1] if index - 1 < len(route_addresses) else ""
            to_address = route_addresses[index] if index < len(route_addresses) else ""
            pool = _first_pool_for_edge(
                pools=pools,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
            )
            step = {
                "hop": index,
                "fromSymbol": from_symbol,
                "toSymbol": to_symbol,
                "tokenIn": from_address,
                "tokenOut": to_address,
                "pool": pool.get("pool") or "",
                "protocol": pool.get("protocol") or pool.get("dex") or "uniswap_v4",
                "status": "quoted",
                "quoteSource": "route_step_expanded_for_onchain_review",
                "inputAmount": None,
            }
            by_hop[index] = step
        completed.append(by_hop[index])
    return completed


def _missing_effective_pool_reasons(steps: list[dict[str, Any]], pools: dict[str, dict[str, Any]]) -> list[str]:
    """Return terminal blockers when a step has no effective V4 pool identity.

    A route may be assembled from symbols only or from stale route fixtures.
    Without a pool id we cannot quote on-chain, and waiting on an RPC request
    only produces a misleading timeout when the pool simply does not exist.
    """
    reasons: list[str] = []
    for step in steps:
        pool = _pool_for_step(step, pools)
        pool_id = str(step.get("pool") or pool.get("pool") or pool.get("poolId") or "").strip()
        if pool_id:
            continue
        hop_label = f"hop {step.get('hop') or '?'} {step.get('fromSymbol') or ''} -> {step.get('toSymbol') or ''}"
        reasons.append(f"{hop_label} V4 pool not initialized or missing effective pool for on-chain quote")
    return reasons


def _pool_manager_for_route(chain_id: int | None, network: str, pool: dict[str, Any]) -> str:
    for key in ("poolManager", "pool_manager"):
        candidate = str(pool.get(key) or "").strip()
        if candidate.startswith("0x") and len(candidate) == 42:
            return candidate
    normalized = _robinhood_network_for_chain_id(chain_id, network)
    if normalized == ROBINHOOD_TESTNET_NETWORK and robinhood.TESTNET_V4_POOL_MANAGER_ADDRESS:
        return robinhood.TESTNET_V4_POOL_MANAGER_ADDRESS
    return robinhood.V4_POOL_MANAGER_ADDRESS or ""


def _live_pool_state_precheck_failures(
    *,
    chain_id: int | None,
    network: str,
    w3: Any,
    rpc_url: str,
    steps: list[dict[str, Any]],
    pools: dict[str, dict[str, Any]],
    block_number: int,
    live_pool_states: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Return terminal failures from a realtime V4 pool-state precheck.

    The route may look tradable from static fixture data even though the
    actual on-chain pool is not initialized or has no LP liquidity. Reading
    PoolManager slot0/liquidity up front keeps those cases from falling into
    the generic RPC timeout path.
    """
    if _robinhood_network_for_chain_id(chain_id, network) != ROBINHOOD_TESTNET_NETWORK:
        return []
    if not hasattr(w3, "eth") or not hasattr(w3.eth, "contract") or not callable(getattr(w3.eth, "contract", None)):
        return []
    failures: list[str] = []
    from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (
        _batch_read_live_pool_states,
    )

    v4_entries: dict[str, list[tuple[dict[str, Any], str, str]]] = {}
    for step in steps:
        if _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee")) != "uniswap_v4":
            continue
        pool = _pool_for_step(step, pools)
        pool_id = str(step.get("pool") or pool.get("pool") or pool.get("poolId") or "").strip()
        pool_manager = str(step.get("poolManager") or pool.get("poolManager") or pool.get("pool_manager") or "").strip()
        if not pool_manager:
            pool_manager = _pool_manager_for_route(chain_id, network, pool)
        if not pool_manager:
            continue
        if not pool_id:
            continue
        if not (pool_id.startswith("0x") and len(pool_id) == 66):
            continue
        hop_label = f"hop {step.get('hop') or '?'} {step.get('fromSymbol') or ''} -> {step.get('toSymbol') or ''}"
        v4_entries.setdefault(pool_manager, []).append((step, pool_id, hop_label))

    for pool_manager, entries in v4_entries.items():
        pool_ids = [pool_id for _, pool_id, _ in entries]
        try:
            states = _batch_read_live_pool_states(
                w3=w3,
                rpc_url=rpc_url,
                pool_manager=pool_manager,
                pool_ids=pool_ids,
                block_number=block_number,
                network=network,
                chain_id=chain_id,
            )
            if live_pool_states is not None:
                live_pool_states.update(states)
        except Exception as exc:
            for _, _, hop_label in entries:
                failures.append(f"on-chain live pool state verification failed for {hop_label}: {exc}")
            continue
        for _, pool_id, hop_label in entries:
            state = states.get(pool_id.lower())
            if not state:
                failures.append(f"on-chain live pool state verification failed for {hop_label}: batch response missing pool state")
                continue
            message = _text(state.get("error")).lower()
            if not message:
                continue
            if "live liquidity is zero" in message or _is_not_enough_liquidity_error(ValueError(message)):
                failures.append(f"{hop_label} has no LP liquidity on chain: pool manager live liquidity is zero")
            elif "slot0 missing" in message or "missing sqrtpricex96" in message or _is_pool_not_initialized_error(ValueError(message)):
                failures.append(f"{hop_label} pool is not initialized: live slot0 is missing")
            else:
                failures.append(f"on-chain live pool state verification failed for {hop_label}: {state.get('error')}")
    return failures


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
    closed_loop_routes_group: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    table = payload.get("poolStaticTable") if isinstance(payload, dict) and isinstance(payload.get("poolStaticTable"), dict) else {}
    raw_network = _text(table.get("network") or route.get("network") or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    network = _robinhood_network_for_chain_id(chain_id, raw_network)
    trigger = _trigger_context_from_route(route, trigger_context)
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    route_addresses = [str(item) for item in route.get("routeAddresses") or [] if str(item).strip()]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    steps = [step for step in analysis.get("steps") or [] if isinstance(step, dict)]
    pools = _pool_map(route)
    steps = _complete_route_steps(route_symbols, route_addresses, steps, pools)
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
        for reason in [_unsupported_step_reason(step, _pool_for_step(step, pools))]
        if reason
    ]
    if unsupported:
        return _unsupported_evidence(chain_id=chain_id, network=network, route=route, observed_at=observed, reasons=unsupported, trigger_context=trigger)

    missing_pool_reasons = _missing_effective_pool_reasons(steps, pools)
    if missing_pool_reasons:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=missing_pool_reasons,
            trigger_context=trigger,
        )

    terminal_quote_failures: list[str] = []
    for step in steps:
        trust = str(step.get("executionTrust") or "").strip().lower()
        live_error = str(step.get("liveQuoteError") or "").strip()
        lowered_error = live_error.lower()
        hop_label = f"hop {step.get('hop') or '?'} {step.get('fromSymbol') or ''} -> {step.get('toSymbol') or ''}"
        if trust == "not_enough_liquidity" or any(marker in lowered_error for marker in (
            "liquidity is zero",
            "live liquidity is zero",
            "not enough liquidity",
            "insufficient liquidity",
            "notenoughliquidity",
        )):
            terminal_quote_failures.append(
                f"{hop_label} has no LP liquidity on chain: {live_error or 'liquidity is zero'}"
            )
        elif trust == "pool_not_initialized":
            terminal_quote_failures.append(f"{hop_label} pool is not initialized")
    warnings = [str(item) for item in analysis.get("warnings") or [] if isinstance(item, str)]
    if any("not_enough_liquidity" in warning or "liquidity is zero" in warning for warning in warnings):
        terminal_quote_failures.append("route quote already reports no LP liquidity")
    if terminal_quote_failures:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=terminal_quote_failures,
            trigger_context=trigger,
        )

    trigger_block_number = _trigger_block_number(trigger)

    quoter = _text(table.get("quoter") or robinhood.default_v3_quoter_address(ROBINHOOD_DEFAULT_V3_QUOTER))
    token_cache: dict[str, int] = {}
    for _pool in pools.values():
        for side_index in ("0", "1"):
            if not isinstance(_pool, dict):
                continue
            _address = _text(_pool.get(f"token{side_index}"))
            _symbol = _text(_pool.get(f"token{side_index}Symbol"))
            _decimals_raw = _pool.get(f"token{side_index}Decimals") or _pool.get(f"token{side_index}_decimals")
            if _decimals_raw in (None, ""):
                continue
            try:
                _decimals = int(_decimals_raw)
            except (TypeError, ValueError):
                continue
            if _address:
                token_cache[_address.lower()] = _decimals
            if _symbol:
                token_cache[_symbol.lower()] = _decimals
    try:
        w3, rpc_url = _connect_robinhood_family(chain_id, network)
        block_number = int(w3.eth.block_number)
        quote_block_source = "latest_onchain_review"
    except Exception as exc:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=[f"on-chain RPC verification failed: {exc}"],
            trigger_context=trigger,
        )

    live_pool_states: dict[str, dict[str, Any]] = {}
    live_pool_failures = _live_pool_state_precheck_failures(
        chain_id=chain_id,
        network=network,
        w3=w3,
        rpc_url=rpc_url,
        steps=steps,
        pools=pools,
        block_number=block_number,
        live_pool_states=live_pool_states,
    )
    if live_pool_failures:
        generic_failures = [
            blocker
            for blocker in live_pool_failures
            if blocker.startswith("on-chain live pool state verification failed")
        ]
        pool_failure_status = None
        if generic_failures and len(generic_failures) == len(live_pool_failures):
            joined_generic = " ".join(generic_failures)
            pool_failure_status = (
                _is_rpc_verification_error(ValueError(joined_generic))
                or "live_quote_failed"
            )
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=live_pool_failures,
            trigger_context=trigger,
            status=pool_failure_status,
        )

    input_symbol = _norm_symbol(route_symbols[0] if route_symbols else analysis.get("inputSymbol"))
    first_pool = _pool_for_step(steps[0], pools) if steps else {}
    input_address, _, input_decimals = _pool_side(first_pool, input_symbol)
    if not input_address and steps:
        input_address = _text(steps[0].get("tokenIn") or (route_addresses[0] if route_addresses else "")) or None
    if input_decimals is None:
        try:
            input_decimals = _token_decimals(w3, input_address, input_symbol, token_cache)
        except Exception as exc:
            return _unsupported_evidence(
                chain_id=chain_id,
                network=network,
                route=route,
                observed_at=observed,
                reasons=[f"missing exact token decimals for {input_symbol}: {exc}"],
                trigger_context=trigger,
            )
    route_closed = bool(route_symbols and route_symbols[0] == route_symbols[-1])
    route_size_execution_probe, route_max_executable_raw, route_first_failure_raw, route_recommended_raw = _route_size_advice(route)
    reference_input_amount = _positive_decimal_or_none(analysis.get("referenceInputAmount"))
    offchain_initial_probe_amount_raw, offchain_initial_probe_basis, offchain_initial_probe_reason, _ = (
        _offchain_initial_probe_amount_raw(
            route,
            input_decimals=input_decimals,
            analysis=analysis,
            pools=pools,
        )
    )
    if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0:
        requested_borrow_input_amount_raw = int(borrow_input_amount_override_raw)
        offchain_initial_probe_amount_raw = int(borrow_input_amount_override_raw)
        offchain_initial_probe_basis = "borrow_token"
        offchain_initial_probe_reason = "requested_borrow_amount_fallback"
    elif offchain_initial_probe_amount_raw and int(offchain_initial_probe_amount_raw) > 0:
        # 初始请求量的默认值就是链下初始探测起点；只有用户显式传入金额时才不同。
        requested_borrow_input_amount_raw = int(offchain_initial_probe_amount_raw)
    elif route_closed and reference_input_amount is not None:
        requested_borrow_input_amount_raw = _raw_from_human(reference_input_amount, input_decimals)
    else:
        requested_borrow_input_amount_raw = _raw_from_human(Decimal("1"), input_decimals)
    probe_requested_amount_raw = (
        int(offchain_initial_probe_amount_raw)
        if offchain_initial_probe_amount_raw and int(offchain_initial_probe_amount_raw) > 0
        else requested_borrow_input_amount_raw
    )
    if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0:
        selected_borrow_input_amount_raw = int(borrow_input_amount_override_raw)
    else:
        selected_borrow_input_amount_raw = int(probe_requested_amount_raw)
    borrow_input_amount = Decimal(int(selected_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))
    execution_size_probe: dict[str, Any] | None = route_size_execution_probe or None
    route_has_size_metadata = bool(route_max_executable_raw or route_first_failure_raw or route_size_execution_probe)
    all_v4_route = bool(steps) and all(
        str(step.get("protocol") or step.get("dex") or "").strip().lower() == "uniswap_v4"
        for step in steps
    )
    robinhood_closed_loop_v4_route = bool(
        chain_id in {robinhood.CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID} and route_closed and all_v4_route
    )
    if (
        not skip_execution_size_probe
        and chain_id in {robinhood.CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}
        and selected_borrow_input_amount_raw > 0
        and (route_has_size_metadata or robinhood_closed_loop_v4_route)
    ):
        probe_amount_raw = (
            int(borrow_input_amount_override_raw)
            if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0
            else offchain_initial_probe_amount_raw
        )
        # 链下初始探测只负责给出起点；链上执行规模探测再围绕绝对利润峰值
        # 搜索最佳量，不能把离线 sizeAnalysis 的 maxExecutable 当作执行量。
        execution_size_probe = _probe_robinhood_testnet_route_execution_size(
            route,
            payload=payload,
            chain_id=chain_id,
            observed_at=observed,
            trigger_context=trigger,
            quote_hop_provider=quote_hop_provider,
            requested_amount_raw=probe_amount_raw,
            initial_probe_amount_raw=probe_amount_raw,
        )
        try:
            probed_amount_raw = int(
                execution_size_probe.get("bestProfitAmountRaw")
                or execution_size_probe.get("maxExecutableAmountRaw")
                or 0
            )
        except (TypeError, ValueError):
            probed_amount_raw = None
        if probed_amount_raw is not None and probed_amount_raw > 0:
            selected_borrow_input_amount_raw = probed_amount_raw
            borrow_input_amount = Decimal(probed_amount_raw) / (Decimal(10) ** int(input_decimals))

    hop_rows, blockers, amount_in_raw, route_symbols, route_addresses, steps = _quote_route_hops(
        route=route,
        pools=pools,
        steps=steps,
        w3=w3,
        quoter=quoter,
        chain_id=chain_id,
        network=network,
        block_number=block_number,
        token_cache=token_cache,
        quote_hop_provider=quote_hop_provider,
        closed_loop_routes_group=closed_loop_routes_group,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        selected_borrow_input_amount_raw=selected_borrow_input_amount_raw,
        rpc_url=rpc_url,
        live_pool_states=live_pool_states,
    )

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
        blockers.append("V4 路线使用参考报价适配器；必须先用真实 V4 Quoter/SDK 或链上复核确认报价后再进入链上执行")
    all_hops_verified = bool(steps) and len(hop_rows) == len(steps) and token_continuity_ok and not any(
        _text(hop.get("status")).lower() in {"live_quote_failed", "quote_failed", "not_enough_liquidity", "adapter_required", "pool_not_initialized", "rpc_verification_failed", "rpc_verification_timeout", "live_evidence_deferred"}
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
    for issue in price_impact_issues:
        hop_no = int(issue.get("hop") or 0)
        from_symbol = _norm_symbol(issue.get("fromSymbol"))
        to_symbol = _norm_symbol(issue.get("toSymbol"))
        impact_bps = issue.get("priceImpactBps")
        limit_bps = int(issue.get("limitBps") or DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS)
        if impact_bps is None:
            blockers.append(
                f"hop {hop_no} {from_symbol}->{to_symbol} price impact cannot be confirmed; "
                f"must be at or below {limit_bps} bps before transaction preflight"
            )
        else:
            blockers.append(
                f"hop {hop_no} {from_symbol}->{to_symbol} price impact {impact_bps} bps "
                f"exceeds {limit_bps} bps limit"
            )
    adapter_required = bool(
        any(_text(hop.get("status")).lower() == "adapter_required" for hop in hop_rows)
    )
    final_symbol = _norm_symbol(route_symbols[-1] if route_symbols else analysis.get("outputSymbol"))
    final_hop = hop_rows[-1] if all_hops_verified and hop_rows else None
    final_decimals = int(final_hop["outputDecimals"]) if final_hop and final_hop.get("outputDecimals") is not None else None
    final_amount = Decimal(final_hop["amountOut"]) if final_hop and final_hop.get("amountOut") is not None else None
    borrow_asset_is_stable = bool(
        analysis.get("borrowAssetIsStable")
        if analysis.get("borrowAssetIsStable") is not None
        else input_symbol in _STABLE_ANCHOR_SYMBOLS
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
    final_output_usd = final_amount if borrow_asset_is_stable and final_symbol in _STABLE_ANCHOR_SYMBOLS and final_amount is not None else None
    gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
    flash_fee = notional * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    gas_reserve = Decimal(str(DEFAULT_GAS_RESERVE_USD))
    submission_net_profit_usd = gross_profit_usd - flash_fee if gross_profit_usd is not None else None
    net_profit_usd = submission_net_profit_usd - gas_reserve if submission_net_profit_usd is not None else None
    net_profit_bps = net_profit_usd / notional * Decimal(10_000) if net_profit_usd is not None and notional > 0 else None
    min_profit_usd = Decimal(str(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD))
    net_positive_after_costs = bool(
        all_hops_verified
        and (
            (borrow_asset_is_stable and submission_net_profit_usd is not None and submission_net_profit_usd > 0)
            or (not borrow_asset_is_stable and token_submission_net_profit is not None and token_submission_net_profit > 0)
        )
    )
    meets_submission_profit_threshold = bool(
        all_hops_verified
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
    not_enough_liquidity = bool(
        any(_text(hop.get("status")).lower() == "not_enough_liquidity" for hop in hop_rows)
        or bool(
            execution_size_probe
            and execution_size_probe.get("searchStatus") == "not_enough_liquidity"
        )
    )
    best_amount_confirmed = bool(
        isinstance(execution_size_probe, dict)
        and execution_size_probe.get("maxExecutableAmountConfirmed") is True
        and execution_size_probe.get("maxExecutableAmountRaw") is not None
        and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
    )
    best_amount_unconfirmed = bool(all_hops_verified and not best_amount_confirmed)
    if best_amount_unconfirmed:
        blockers.append("chain hops passed for current amount, but best amount is not confirmed; cannot enter transaction preflight")
    if pool_insufficient_for_profitable_execution:
        blockers.append("current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee")
    if pool_not_initialized:
        blockers.append("V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate")
    if not_enough_liquidity:
        blockers.append("current borrow size exceeds available liquidity for at least one hop; reduce borrow size and retry")
    if adapter_required:
        blockers.append("V4 quote adapter is required; pool interpretation deferred until the mainnet V4 adapter is configured")
    ready_for_transaction_preflight = bool(
        meets_submission_profit_threshold
        and all_hops_within_price_impact_limit
        and best_amount_confirmed
    )
    evidence_status = (
        "adapter_required"
        if adapter_required
        else (
        "pool_not_initialized"
        if pool_not_initialized
        else (
            "not_enough_liquidity"
        if not_enough_liquidity
        else (
            "pool_insufficient_for_profitable_execution"
        if pool_insufficient_for_profitable_execution
        else (
            "route_unprofitable"
            if all_hops_verified and not net_positive_after_costs
            else (
                "slippage_limit_exceeded"
                if all_hops_verified and not all_hops_within_price_impact_limit
                else ("live_verified" if all_hops_verified and best_amount_confirmed else ("static_reference_pending_live_review" if reference_quote_only or best_amount_unconfirmed else "live_quote_failed"))
            )
        )
        )
        )
        )
    )
    evidence_trust = (
        "live_quoter_verified_readonly"
        if all_hops_verified and best_amount_confirmed
        else (
            evidence_status
            if evidence_status in {"not_enough_liquidity", "pool_not_initialized", "live_quote_failed", "rpc_verification_failed", "rpc_verification_timeout"}
            else ("static_reference_pending_live_review" if reference_quote_only else "static_reference_pending_live_review")
        )
    )
    if ready_for_transaction_preflight:
        evidence_conclusion = "链上报价已验证为正收益，本次复核完成"
        evidence_action = "on-chain verified profitable; review complete"
    elif all_hops_verified and not net_positive_after_costs:
        evidence_conclusion = "链上报价不盈利，不建议进入链上执行"
        evidence_action = "on-chain verified but net profit is not positive after flash loan fees; do not execute"
    elif all_hops_verified and not all_hops_within_price_impact_limit:
        evidence_conclusion = "链上报价已验证存在利润，但至少一个单跳价格影响超过 10 bps；不建议按当前金额进入链上执行"
        evidence_action = "on-chain verified but at least one hop price impact exceeds the 10 bps limit; reduce size or do not execute"
    elif best_amount_unconfirmed:
        evidence_conclusion = "链上报价已验证当前金额，但最佳量未计算成功；复核未通过"
        evidence_action = "on-chain best amount unconfirmed; cannot enter transaction preflight"
    elif not all_hops_verified:
        evidence_conclusion = "链上报价失败或未完成，当前无法给出可执行套利结论"
        evidence_action = "on-chain quote failed; route cannot enter transaction preflight"
    else:
        evidence_conclusion = "链上复核完成，但当前状态不满足交易提交条件"
        evidence_action = "on-chain reverify complete; current state does not satisfy transaction submission conditions"
    return _build_onchain_quote_evidence_payload(locals())



def _build_onchain_quote_evidence_payload(c: dict[str, Any]) -> dict[str, Any]:
    adapter_required = c["adapter_required"]
    all_hops_verified = c["all_hops_verified"]
    all_hops_within_price_impact_limit = c["all_hops_within_price_impact_limit"]
    block_number = c["block_number"]
    blockers = c["blockers"]
    borrow_asset_is_stable = c["borrow_asset_is_stable"]
    borrow_input_amount = c["borrow_input_amount"]
    chain_id = c["chain_id"]
    evidence_action = c["evidence_action"]
    evidence_conclusion = c["evidence_conclusion"]
    evidence_status = c["evidence_status"]
    evidence_trust = c["evidence_trust"]
    execution_size_probe = c["execution_size_probe"]
    final_amount = c["final_amount"]
    final_decimals = c["final_decimals"]
    final_output_usd = c["final_output_usd"]
    final_symbol = c["final_symbol"]
    flash_fee = c["flash_fee"]
    gas_reserve = c["gas_reserve"]
    gross_profit_usd = c["gross_profit_usd"]
    hop_rows = c["hop_rows"]
    input_decimals = c["input_decimals"]
    input_symbol = c["input_symbol"]
    meets_submission_profit_threshold = c["meets_submission_profit_threshold"]
    min_profit_usd = c["min_profit_usd"]
    net_positive_after_costs = c["net_positive_after_costs"]
    net_profit_bps = c["net_profit_bps"]
    net_profit_usd = c["net_profit_usd"]
    network = c["network"]
    not_enough_liquidity = c["not_enough_liquidity"]
    notional = c["notional"]
    observed = c["observed"]
    offchain_initial_probe_amount_raw = c["offchain_initial_probe_amount_raw"]
    offchain_initial_probe_basis = c["offchain_initial_probe_basis"]
    offchain_initial_probe_reason = c["offchain_initial_probe_reason"]
    pool_insufficient_for_profitable_execution = c["pool_insufficient_for_profitable_execution"]
    pool_not_initialized = c["pool_not_initialized"]
    price_impact_issues = c["price_impact_issues"]
    quote_block_source = c["quote_block_source"]
    quoter = c["quoter"]
    ready_for_transaction_preflight = c["ready_for_transaction_preflight"]
    requested_borrow_input_amount_raw = c["requested_borrow_input_amount_raw"]
    route_addresses = c["route_addresses"]
    route_closed = c["route_closed"]
    route_symbols = c["route_symbols"]
    rpc_url = c["rpc_url"]
    selected_borrow_input_amount_raw = c["selected_borrow_input_amount_raw"]
    submission_net_profit_usd = c["submission_net_profit_usd"]
    token_continuity_issues = c["token_continuity_issues"]
    token_continuity_ok = c["token_continuity_ok"]
    token_flash_fee = c["token_flash_fee"]
    token_gross_profit = c["token_gross_profit"]
    token_net_profit = c["token_net_profit"]
    token_net_profit_bps = c["token_net_profit_bps"]
    token_submission_net_profit = c["token_submission_net_profit"]
    trigger = c["trigger"]
    trigger_block_number = c["trigger_block_number"]
    w3 = c["w3"]

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
        "quoteBlockSource": quote_block_source,
        "quoter": quoter,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "requestedBorrowInputAmountRaw": str(requested_borrow_input_amount_raw),
        "offchainInitialProbeAmountRaw": str(offchain_initial_probe_amount_raw),
        "offchainInitialProbeBasis": offchain_initial_probe_basis,
        "offchainInitialProbeReason": offchain_initial_probe_reason,
        "selectedBorrowInputAmountRaw": str(selected_borrow_input_amount_raw),
        "executionSizeProbe": execution_size_probe,
        "status": evidence_status,
        "executionTrust": evidence_trust,
        "trustedForExecution": False,
        "priceEvidenceMode": "live_verified" if all_hops_verified else "live_required",
        "priceEvidenceSource": "chain_live_quote" if all_hops_verified else "chain_live_quote_required",
        "livePriceVerified": bool(all_hops_verified),
        "readyForTransactionPreflight": ready_for_transaction_preflight,
        "profitable": bool(all_hops_verified and net_positive_after_costs),
        "conclusion": evidence_conclusion,
        "recommendation": {
            "level": evidence_status,
            "action": evidence_action,
            "conclusion": evidence_conclusion,
            "profitable": bool(all_hops_verified and net_positive_after_costs),
            "readyForTransactionPreflight": ready_for_transaction_preflight,
            "blocked": not ready_for_transaction_preflight,
            "reasons": blockers,
        },
        "hops": hop_rows,
        "costModel": {
            "notionalUsd": float(notional),
            "requestedBorrowInputAmountRaw": int(requested_borrow_input_amount_raw),
            "requestedBorrowInputAmount": float(Decimal(int(requested_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))),
            "selectedBorrowInputAmountRaw": int(selected_borrow_input_amount_raw),
            "selectedBorrowInputAmount": float(borrow_input_amount),
            "selectedBorrowInputAmountBasis": "borrow_token",
            "selectedBorrowInputAmountReason": (
                "onchain_execution_size_probe_max_absolute_profit_amount"
                if (
                    isinstance(execution_size_probe, dict)
                    and execution_size_probe.get("maxExecutableAmountRaw") is not None
                    and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
                    and execution_size_probe.get("maxExecutableAmountConfirmed") is True
                )
                else "onchain_execution_size_probe_confirmed_profitable_lower_bound"
                if (
                    isinstance(execution_size_probe, dict)
                    and execution_size_probe.get("maxExecutableAmountRaw") is not None
                    and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
                    and execution_size_probe.get("maxExecutableAmountConfirmed") is False
                )
                else "requested_amount_fallback"
            ),
            "selectedBorrowInputAmountConfirmedMax": bool(
                isinstance(execution_size_probe, dict)
                and execution_size_probe.get("maxExecutableAmountConfirmed") is True
                and execution_size_probe.get("maxExecutableAmountRaw") is not None
                and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
            ),
            "bestAmountConfirmed": bool(
                isinstance(execution_size_probe, dict)
                and execution_size_probe.get("maxExecutableAmountConfirmed") is True
                and execution_size_probe.get("maxExecutableAmountRaw") is not None
                and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
            ),
            "bestAmountMissing": not bool(
                isinstance(execution_size_probe, dict)
                and execution_size_probe.get("maxExecutableAmountConfirmed") is True
                and execution_size_probe.get("maxExecutableAmountRaw") is not None
                and int(execution_size_probe.get("maxExecutableAmountRaw") or 0) == int(selected_borrow_input_amount_raw)
            ),
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
            "notEnoughLiquidity": not_enough_liquidity,
            "adapterRequired": adapter_required,
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
        "transactionDevelopment": _transaction_development(ready_for_transaction_preflight, blockers),
    }


def _quote_route_hops(
    *,
    route: dict[str, Any],
    pools: dict[str, dict[str, Any]],
    steps: list[dict[str, Any]],
    w3: Any,
    quoter: str,
    chain_id: int | None,
    network: str,
    block_number: int,
    token_cache: dict[str, int],
    quote_hop_provider: QuoteHopProvider | None,
    closed_loop_routes_group: list[dict[str, Any]] | None,
    route_symbols: list[str],
    route_addresses: list[str],
    selected_borrow_input_amount_raw: int,
    rpc_url: str | None = None,
    live_pool_states: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str], int | None, list[str], list[str], list[dict[str, Any]]]:
    hop_provider = quote_hop_provider
    hop_rows: list[dict[str, Any]] = []
    amount_in_raw: int | None = None
    blockers: list[str] = []
    amount_in_raw = selected_borrow_input_amount_raw
    official_fast = official_v4_arb_static_hop_rows(
        w3=w3,
        route=route,
        pools=pools,
        steps=steps,
        amount_in_raw=amount_in_raw,
        chain_id=chain_id,
        routes_group=closed_loop_routes_group,
    )
    official_fast_rows = None
    if official_fast is not None:
        official_fast_rows, selected_symbols, selected_addresses, selected_steps = official_fast
        route_symbols = selected_symbols or route_symbols
        route_addresses = selected_addresses or route_addresses
        steps = selected_steps or steps
    for step in steps:
        if official_fast_rows is not None:
            hop_rows = official_fast_rows
            break
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
                    rpc_url=rpc_url,
                    live_pool_state=(
                        live_pool_states.get(str(step.get("pool") or pool.get("pool") or "").lower())
                        if live_pool_states
                        else None
                    ),
                )
            else:
                hop = _quote_v3_hop(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
        except Exception as exc:
            if _is_missing_historical_state_error(exc):
                # Live verification always runs against the latest block.
                # A pruned state response is a real RPC/evidence failure; do
                # not fall back to any historical block or reinterpret it as
                # missing liquidity.
                hop_rows.append(
                    {
                        "hop": hop_number,
                        "status": "live_quote_failed",
                        "executionTrust": "static_reference_pending_live_review",
                        "quoteSource": "chain_live_quote_failed",
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
                                "status": "live_quote_failed",
                                "liveQuoteError": str(exc),
                                "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                                "toSymbol": _norm_symbol(step.get("toSymbol")),
                            },
                            index=hop_number,
                        ),
                    }
                )
                blockers.append(
                    f"on-chain verification failed at hop {hop_number}: {exc}"
                )
                break
            if _is_missing_v4_adapter_error(exc):
                hop_status = "adapter_required"
                quote_source = "chain_adapter_required"
            elif _is_pool_not_initialized_error(exc):
                hop_status = "pool_not_initialized"
                quote_source = "chain_pool_not_initialized"
            elif _is_not_enough_liquidity_error(exc):
                hop_status = "not_enough_liquidity"
                quote_source = "chain_live_quote_liquidity_exceeded"
            else:
                hop_status = "live_quote_failed"
                quote_source = "chain_live_quote_failed"
            from_symbol = _norm_symbol(step.get("fromSymbol"))
            to_symbol = _norm_symbol(step.get("toSymbol"))
            _, _, hop_in_decimals = _pool_side(pool, from_symbol)
            _, _, hop_out_decimals = _pool_side(pool, to_symbol)
            if hop_in_decimals is None:
                hop_in_decimals = _int_or_none(step.get("inputDecimals"))
            if hop_out_decimals is None:
                hop_out_decimals = _int_or_none(step.get("outputDecimals"))
            if hop_in_decimals is None:
                try:
                    hop_in_decimals = _token_decimals(
                        w3,
                        _text(step.get("tokenIn") or _pool_side(pool, from_symbol)[0]) or None,
                        from_symbol,
                        token_cache,
                    )
                except Exception as exc:
                    blockers.append(
                        f"missing exact token decimals at hop {hop_number} {from_symbol}: {exc}"
                    )
                    break
            if hop_out_decimals is None:
                try:
                    hop_out_decimals = _token_decimals(
                        w3,
                        _text(step.get("tokenOut") or _pool_side(pool, to_symbol)[0]) or None,
                        to_symbol,
                        token_cache,
                    )
                except Exception as exc:
                    blockers.append(
                        f"missing exact token decimals at hop {hop_number} {to_symbol}: {exc}"
                    )
                    break
            hop_rows.append(
                {
                    "hop": hop_number,
                    "status": hop_status,
                    "executionTrust": hop_status,
                    "quoteSource": quote_source,
                    "quoteMethod": f"{protocol}_exact_input",
                    "protocol": protocol,
                    "pool": step.get("pool") or pool.get("pool"),
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "tokenIn": step.get("tokenIn") or _pool_side(pool, from_symbol)[0],
                    "tokenOut": step.get("tokenOut") or _pool_side(pool, to_symbol)[0],
                    "amountInRaw": str(amount_in_raw),
                    "amountIn": _human_from_raw(amount_in_raw, hop_in_decimals),
                    "inputDecimals": hop_in_decimals,
                    "outputDecimals": hop_out_decimals,
                    "amountOutRaw": None,
                    "liveQuoteError": str(exc),
                    "errorStack": _dex_error_stack_from_hop(
                        {
                            "hop": hop_number,
                            "status": hop_status,
                            "liveQuoteError": str(exc),
                            "fromSymbol": from_symbol,
                            "toSymbol": to_symbol,
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
            del from_symbol, to_symbol, hop_in_decimals, hop_out_decimals
            if hop_status == "adapter_required":
                blockers.append(f"V4 quote adapter is required at hop {hop_number} {_norm_symbol(step.get('fromSymbol'))}->{_norm_symbol(step.get('toSymbol'))}: {exc}")
            elif hop_status == "pool_not_initialized":
                blockers.append(f"V4 pool is not initialized or cannot be quoted at hop {hop_number} {_norm_symbol(step.get('fromSymbol'))}->{_norm_symbol(step.get('toSymbol'))}: {exc}")
            elif hop_status == "not_enough_liquidity":
                blockers.append(f"hop {hop_number} {_norm_symbol(step.get('fromSymbol'))}->{_norm_symbol(step.get('toSymbol'))} has not enough liquidity for requested amount: {exc}")
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
    return hop_rows, blockers, amount_in_raw, route_symbols, route_addresses, steps

__all__ = ["build_route_onchain_evidence", "capture_onchain_trigger_context"]
