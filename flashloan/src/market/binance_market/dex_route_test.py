from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID
from dex.runtime.dex_route_rpc import _probe_robinhood_testnet_rpc, fetch_dex_tx_receipt
from market.binance_market.dex_route_helpers import _match_route
from market.binance_market.dex_route_helpers import _norm_symbols
from market.binance_market.dex_route_helpers import _official_route_addresses
from market.binance_market.dex_route_helpers import _optimize_amount_ladder
from market.binance_market.dex_route_helpers import _probe_record_item
from market.binance_market.dex_route_helpers import _recommended_route_test_amount
from market.binance_market.dex_route_helpers import _routes_payload
from market.binance_market.dex_route_helpers import _route_symbols
from market.binance_market.dex_route_helpers import _quote_failure_status
from market.binance_market.dex_route_helpers import _static_call_revert_detail
from market.binance_market.dex_route_helpers import _step_failure_status
from market.binance_market.dex_route_helpers import _step_payload
from market.binance_market.dex_route_helpers import _tail
from market.binance_market.dex_route_helpers import best_amount_calculation_from_route
from market.binance_market.dex_route_helpers import normalize_route_symbols


ROOT = Path(__file__).resolve().parents[4]
OFFICIAL_ARB_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-arb-live.js"
OFFICIAL_DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"


def _raw_amount_to_human(value: Any, decimals: int = 18) -> float | None:
    """Convert a raw integer on-chain amount to a human-readable float.

    The official arb script now emits ``profitHuman`` / ``outAmountHuman``.
    This helper is only a fallback for older outputs that carried raw wei.
    """
    try:
        raw = int(str(value or ""))
    except (TypeError, ValueError):
        return None
    return float(raw) / (10**decimals)


def recommended_route_test_amount(
    *,
    route_symbols: list[str],
    chain_id: int,
    protocols: list[str],
) -> float | None:
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = _match_route(routes_payload.get("routes") or [], route_symbols)
    return _recommended_route_test_amount(route)


def estimate_route_live_quote(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
    amount: float,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from market.binance_market.dex_surface_graph import _price_map_from_pairs
    from market.binance_market.dex_surface_quotes import _route_quote_analysis
    from market.binance_market.pool_candidates import dex_pool_candidates_payload

    route_symbols = _norm_symbols(route_symbols)
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = route if isinstance(route, dict) else _match_route(routes_payload.get("routes") or [], route_symbols)
    if route is None and route_symbols and route_symbols[0] == route_symbols[-1]:
        reversed_symbols = list(reversed(route_symbols))
        reversed_route = _match_route(routes_payload.get("routes") or [], reversed_symbols)
        if reversed_route:
            reversed_addresses = [str(item) for item in (reversed_route.get("routeAddresses") or []) if str(item).strip()]
            route = {
                **reversed_route,
                "routeSymbols": route_symbols,
                "symbolPair": " -> ".join(route_symbols),
                "routeAddresses": list(reversed(reversed_addresses)) if len(reversed_addresses) == len(route_symbols) else reversed_addresses,
                "routeTokenPath": [],
                "overviewRoute": {
                    **((reversed_route.get("overviewRoute") or {}) if isinstance(reversed_route.get("overviewRoute"), dict) else {}),
                    "pathSymbols": route_symbols,
                    "pathTokens": [],
                },
            }
    if route is None:
        available = [_route_symbols(item) for item in (routes_payload.get("routes") or [])]
        return {
            "ok": False,
            "mode": "dex_route_live_estimate",
            "error": "route_not_found",
            "routeSymbols": route_symbols,
            "availableRoutes": available,
        }

    pool_payload = dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    analysis = _route_quote_analysis(
        route,
        price_map=price_map,
        chain_id=chain_id,
        token_basis_input_amount=amount,
    )
    requested_amount = float(amount)
    amount_optimized_from = requested_amount
    amount_optimized = False
    probe_records: list[dict[str, Any]] = []

    probe_records.append(_probe_record_item(analysis, amount, route_symbols))
    route_addresses = [str(item) for item in analysis.get("routeAddresses") or [] if str(item).strip()]
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols)
    # Direction is the first optimization: for a closed loop, both clockwise
    # and counter-clockwise paths are valid candidates. Pick whichever direction
    # has the higher token-basis net profit.
    ref_net_profit = analysis.get("referenceNetProfitAmount")
    if ref_net_profit is not None and float(ref_net_profit) < 0:
        reversed_symbols = list(reversed(route_symbols))
        if reversed_symbols[0] == reversed_symbols[-1]:
            rev_route = _match_route(routes_payload.get("routes") or [], reversed_symbols)
            if rev_route:
                rev_analysis = _route_quote_analysis(
                    rev_route,
                    price_map=price_map,
                    chain_id=chain_id,
                    token_basis_input_amount=amount,
                )
                rev_profit = rev_analysis.get("referenceNetProfitAmount")
                if rev_profit is not None and float(rev_profit) > float(ref_net_profit):
                    analysis = rev_analysis
                    route_symbols = reversed_symbols
                    if route_addresses and len(route_addresses) == len(reversed_symbols):
                        route_addresses = list(reversed(route_addresses))
                    route = rev_route

    # After direction is confirmed, optimize the borrow amount. We scan a small
    # ladder around the requested amount (plus any static size steps already
    # carried on the route) so "best amount" is a chosen point with adjacent
    # probe records, not just whatever amount the route scanner happened to use.
    max_attempts = max(1, int(os.getenv("DEX_V4_LIVE_QUOTE_AMOUNT_OPTIMIZE_ATTEMPTS", "18") or 18))
    best_analysis, best_amount, best_profit, probe_records, selection_meta = _optimize_amount_ladder(
        route=route,
        route_symbols=route_symbols,
        price_map=price_map,
        chain_id=chain_id,
        amount=amount,
        max_attempts=max_attempts,
        analysis=analysis,
        probe_records=probe_records,
        quote_analysis_fn=_route_quote_analysis,
    )
    if best_analysis is not analysis:
        analysis = best_analysis
        amount = best_amount
        amount_optimized_from = requested_amount
        amount_optimized = bool(abs(float(best_amount) - requested_amount) > 1e-12)
    best_amount_confirmed = bool(selection_meta.get("selectionConfirmed"))
    best_amount_selection_basis = str(selection_meta.get("selectionBasis") or "initial_probe_unconfirmed")
    best_amount_confirmation_reason = str(
        selection_meta.get("selectionReason") or "no confirmed best amount"
    )
    amount_optimization_method = "live_route_amount_ladder"

    route_addresses = [str(item) for item in analysis.get("routeAddresses") or [] if str(item).strip()] or route_addresses
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols)
    if analysis.get("terminalStatus") == "missing_route_identity" and not route_addresses:
        return {
            "ok": False,
            "mode": "dex_route_live_estimate",
            "error": "route_address_identity_incomplete",
            "detail": "路线缺少完整 token 地址身份：symbol 只能用于展示，不能作为链上复核或执行依据",
            "quoteStatus": "missing_route_identity",
            "executionTrust": "missing_route_identity",
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "amount": amount,
            "analysis": {
                "routeAddresses": route_addresses,
                "referenceInputSymbol": route_symbols[0] if route_symbols else None,
                "warnings": analysis.get("warnings") or [],
            },
        }
    steps = [step for step in (analysis.get("steps") or []) if isinstance(step, dict)]
    full_steps = [int(step.get("hop") or 0) for step in steps]
    complete = bool(steps) and full_steps == list(range(1, max(1, len(route_symbols))))
    live_verified = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    failure_statuses = [_step_failure_status(step) for step in steps]
    liquidity_failed = "not_enough_liquidity" in failure_statuses
    rpc_failed = "rpc_verification_failed" in failure_statuses
    rpc_timeout = "rpc_verification_timeout" in failure_statuses
    pool_not_initialized = "pool_not_initialized" in failure_statuses
    live_failed = any(
        status
        in {"live_quote_failed", "not_enough_liquidity", "sdk_adapter_required", "adapter_required", "rpc_verification_failed", "rpc_verification_timeout", "pool_not_initialized"}
        for status in failure_statuses
    ) or any(
        bool(step.get("liveQuoteError"))
        for step in steps
    )
    quote_status = (
        "live_verified"
        if live_verified
        else "not_enough_liquidity"
        if liquidity_failed
        else "pool_not_initialized"
        if pool_not_initialized
        else "rpc_verification_timeout"
        if rpc_timeout
        else "rpc_verification_failed"
        if rpc_failed
        else "live_quote_failed"
        if live_failed
        else "missing_route_identity"
        if analysis.get("terminalStatus") == "missing_route_identity"
        else "requires_live_quoter"
        if bool(analysis.get("requiresLiveQuoter"))
        else "live_quote_reference_only"
    )
    # If profit is negative, suggest reverse direction or amount reduction
    ref_net_profit = analysis.get("referenceNetProfitAmount")
    if ref_net_profit is not None and float(ref_net_profit) < 0:
        # Mark as unprofitable and keep suggestion for halving/reverse
        quote_status = "route_unprofitable"
        execution_trust = "static_live_quoter_estimate"
    execution_trust = (
        "live_v4_adapter"
        if live_verified
        else "not_enough_liquidity"
        if quote_status == "not_enough_liquidity"
        else "pool_not_initialized"
        if quote_status == "pool_not_initialized"
        else "rpc_verification_timeout"
        if quote_status == "rpc_verification_timeout"
        else "rpc_verification_failed"
        if quote_status == "rpc_verification_failed"
        else "live_quote_failed"
        if quote_status == "live_quote_failed"
        else "static_live_quoter_estimate"
    )
    ref_final_output_amount = analysis.get("referenceFinalOutputAmount")
    ref_net_profit_amount = analysis.get("referenceNetProfitAmount")
    ref_net_profit_bps = analysis.get("referenceNetReturnBps")
    profitable = bool(ref_net_profit_amount is not None and float(ref_net_profit_amount) > 0)
    ready_for_transaction_preflight = bool(
        live_verified
        and quote_status == "live_verified"
        and profitable
    )
    unique_probe_records: dict[float, dict[str, Any]] = {}
    for record in probe_records:
        key = round(float(record.get("amount") or 0), 12)
        unique_probe_records[key] = record
    for record in unique_probe_records.values():
        record["isBest"] = bool(abs(float(record.get("amount") or 0) - float(amount)) < 1e-12)
    final_probe_records = [unique_probe_records[key] for key in sorted(unique_probe_records)]
    if quote_status == "route_unprofitable":
        recommendation_action = "on-chain verified but net profit is not positive after flash loan fees; do not execute"
    elif ready_for_transaction_preflight:
        recommendation_action = "on-chain verified profitable; review complete"
    else:
        recommendation_action = "on-chain quote failed; route cannot enter transaction preflight"
    if best_amount_confirmed:
        optimization_hint = (
            f"Offchain candidate selected from {amount_optimized_from} to {amount}; "
            "not a confirmed global executable maximum until on-chain preflight checks larger amounts"
            if amount_optimized
            else "Offchain candidate confirmed at initial probe; larger amounts are not confirmed"
        )
    else:
        optimization_hint = (
            "Best amount is not confirmed; current value is initial probe/scan limit. "
            "Use on-chain preflight to confirm a larger executable peak"
        )
    best_amount_calculation = best_amount_calculation_from_route(route, amount)
    best_amount_calculation.update(
        {
            "method": "live_route_amount_ladder",
            "requestedAmount": requested_amount,
            "bestAmount": amount,
            "bestAmountSymbol": route_symbols[0] if route_symbols else None,
            "initialProbeAmount": requested_amount,
            "initialProbeSymbol": route_symbols[0] if route_symbols else None,
            "amountOptimizedFrom": amount_optimized_from,
            "amountOptimized": amount_optimized,
            "amountOptimizationHint": optimization_hint,
            "bestAmountConfirmed": best_amount_confirmed,
            "bestAmountSelectionBasis": best_amount_selection_basis,
            "bestAmountConfirmationReason": best_amount_confirmation_reason,
            "amountOptimizationMethod": amount_optimization_method,
            "scannedCandidateCount": len(final_probe_records),
            "probeRecords": final_probe_records,
            "probe_records": final_probe_records,
        }
    )
    return {
        "ok": True,
        "mode": "dex_route_live_estimate",
        "chain_id": chain_id,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "amount": amount,
        "requestedAmount": requested_amount,
        "initialProbeAmount": requested_amount,
        "initialProbeSymbol": route_symbols[0] if route_symbols else None,
        "amountOptimizedFrom": amount_optimized_from,
        "amountOptimized": amount_optimized,
        "bestAmountConfirmed": best_amount_confirmed,
        "bestAmountSelectionBasis": best_amount_selection_basis,
        "bestAmountConfirmationReason": best_amount_confirmation_reason,
        "amountOptimizationMethod": amount_optimization_method,
        "amountOptimizationHint": (
            f"已从初始请求量 {amount_optimized_from} {route_symbols[0] if route_symbols else ''} 调整到 {amount} {route_symbols[0] if route_symbols else ''}；该值仅作为链上复核初始量，最佳量由链上复核确认"
            if amount_optimized
            else None
        ),
        "probeRecords": final_probe_records,
        "probe_records": final_probe_records,
        "bestAmountCalculation": best_amount_calculation,
        "bestAmount": amount,
        "amountSymbol": route_symbols[0] if route_symbols else None,
        "directionConfirmed": True,
        "direction": " -> ".join(route_symbols),
        "complete": complete,
        "liveVerified": live_verified,
        "profitable": profitable,
        "readyForTransactionPreflight": ready_for_transaction_preflight,
        "notEnoughLiquidity": liquidity_failed,
        "quoteStatus": quote_status,
        "executionTrust": execution_trust,
        "steps": [_step_payload(step) for step in steps],
        "analysis": {
            "routeAddresses": route_addresses,
            "direction": " -> ".join(route_symbols),
            "directionConfirmed": True,
            "bestAmount": amount,
            "bestAmountSymbol": route_symbols[0] if route_symbols else None,
            "referenceInputSymbol": analysis.get("referenceInputSymbol") or (route_symbols[0] if route_symbols else None),
            "referenceFinalOutputAmount": ref_final_output_amount,
            "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
            "referenceGrossProfitAmount": analysis.get("referenceGrossProfitAmount"),
            "referenceNetProfitAmount": ref_net_profit_amount,
            "referenceNetReturnBps": ref_net_profit_bps,
            "maxStaticDepthImpactBps": analysis.get("maxStaticDepthImpactBps"),
            "warnings": list((analysis.get("warnings") or [])),
            "requestedAmount": requested_amount,
            "initialProbeAmount": requested_amount,
            "initialProbeSymbol": route_symbols[0] if route_symbols else None,
            "amountOptimizedFrom": amount_optimized_from,
            "amountOptimized": amount_optimized,
            "bestAmountConfirmed": best_amount_confirmed,
            "bestAmountSelectionBasis": best_amount_selection_basis,
            "bestAmountConfirmationReason": best_amount_confirmation_reason,
            "amountOptimizationMethod": amount_optimization_method,
            "amountOptimizationHint": (
                f"已从初始请求量 {amount_optimized_from} 调整到 {amount}；该值仅作为链上复核初始量，最佳量由链上复核确认"
                if amount_optimized
                else None
            ),
            "probeRecords": final_probe_records,
            "probe_records": final_probe_records,
            "bestAmountCalculation": best_amount_calculation,
            "profitable": profitable,
            "readyForTransactionPreflight": ready_for_transaction_preflight,
            "recommendation": {
                "level": quote_status,
                "action": recommendation_action,
                "reasons": [],
                "profitable": profitable,
                "readyForTransactionPreflight": ready_for_transaction_preflight,
                "blocked": not ready_for_transaction_preflight,
            },
        },
    }


def is_official_triangle_route(route_symbols: list[str]) -> bool:
    route_symbols = _norm_symbols(route_symbols)
    if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
        return False
    return len({symbol.upper() for symbol in route_symbols}) == 3


def _best_amount_calculation_for_route(
    *,
    chain_id: int,
    route_symbols: list[str],
    amount: float,
) -> dict[str, Any]:
    try:
        routes_payload = _routes_payload(chain_id=chain_id, protocols=["uniswap_v4"])
        route = _match_route(routes_payload.get("routes") or [], route_symbols)
    except Exception:
        route = None
    return best_amount_calculation_from_route(route, amount)


def _attach_best_amount_calculation(
    payload: dict[str, Any],
    *,
    route_symbols: list[str],
    amount: float,
    requested_amount: float | None = None,
    selected_amount: float | None = None,
    method_override: str | None = None,
    attempted_count: int | None = None,
    best_amount_confirmed: bool | None = None,
    best_amount_selection_basis: str | None = None,
    best_amount_confirmation_reason: str | None = None,
) -> dict[str, Any]:
    try:
        final_amount = selected_amount if selected_amount is not None else amount
        initial_amount = requested_amount if requested_amount is not None else amount
        confirmed = bool(best_amount_confirmed) if best_amount_confirmed is not None else False
        confirmation_basis = best_amount_selection_basis or "initial_probe_unconfirmed"
        confirmation_reason = (
            best_amount_confirmation_reason
            or ("best amount returned by on-chain preview" if confirmed else "best amount not confirmed; current value is initial probe")
        )
        try:
            attempted_count_int = int(attempted_count) if attempted_count is not None else None
        except (TypeError, ValueError):
            attempted_count_int = None
        best_calculation = _best_amount_calculation_for_route(
            chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
            route_symbols=route_symbols,
            amount=final_amount,
        )
        best_calculation.update(
            {
                "method": method_override or "route_execution_best_amount",
                "requestedAmount": initial_amount,
                "initialProbeAmount": initial_amount,
                "initialProbeSymbol": (route_symbols[0] if route_symbols else None),
                "bestAmount": final_amount,
                "bestAmountSymbol": (route_symbols[0] if route_symbols else None),
                "bestAmountConfirmed": confirmed,
                "bestAmountSelectionBasis": confirmation_basis,
                "bestAmountConfirmationReason": confirmation_reason,
                "selectionBasis": "borrow_token",
                "attemptedCount": attempted_count_int,
            }
        )
        probe_records = best_calculation.get("probe_records") or best_calculation.get("probeRecords") or []
        payload["bestAmountCalculation"] = best_calculation
        payload["probeRecords"] = probe_records
        payload["probe_records"] = probe_records
        payload["bestAmount"] = final_amount
        payload["bestAmountConfirmed"] = confirmed
        payload["bestAmountSelectionBasis"] = confirmation_basis
        payload["bestAmountConfirmationReason"] = confirmation_reason
        payload["requestedAmount"] = initial_amount
        payload["initialProbeAmount"] = best_calculation.get("initialProbeAmount") or initial_amount
        payload["initialProbeSymbol"] = (
            best_calculation.get("initialProbeSymbol")
            or (route_symbols[0] if route_symbols else None)
        )
        payload["analysis"] = {
            **payload.get("analysis", {}),
            "bestAmount": final_amount,
            "bestAmountSymbol": best_calculation.get("bestAmountSymbol")
            or (route_symbols[0] if route_symbols else None),
            "bestAmountConfirmed": confirmed,
            "bestAmountSelectionBasis": confirmation_basis,
            "bestAmountConfirmationReason": confirmation_reason,
            "referenceInputSymbol": best_calculation.get("bestAmountSymbol")
            or (route_symbols[0] if route_symbols else None),
            "referenceNetReturnBps": payload.get("referenceNetReturnBps"),
            "offchainInitialProbeReason": (
                payload.get("evidence", {}).get("offchainInitialProbeReason")
                if isinstance(payload.get("evidence"), dict)
                else None
            ),
            "initialProbeAmount": payload["initialProbeAmount"],
            "initialProbeSymbol": payload["initialProbeSymbol"],
            "requestedAmount": initial_amount,
            "probeRecords": probe_records,
            "probe_records": probe_records,
            "bestAmountCalculation": best_calculation,
        }
    except Exception as exc:
        payload["bestAmountCalculationError"] = str(exc)[:300]
    return payload


def run_official_v4_arb_route_test(
    *,
    route_symbols: list[str],
    amount: float,
    broadcast: bool,
    chain_id: int | None = None,
    route_addresses: list[str] | None = None,
    amount_is_best: bool = False,
) -> dict[str, Any]:
    route_symbols = _norm_symbols(route_symbols)
    if not is_official_triangle_route(route_symbols):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "当前链上执行器只支持三币 V4 三角闭环：路线必须是由 3 个不同代币组成的 4 段闭环（A -> B -> C -> A）。",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }
    if int(chain_id or 0) != ROBINHOOD_TESTNET_CHAIN_ID:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "官方 V4 三角套利脚本仅绑定 Robinhood Testnet chain 46630，未显式指定该链时不允许调用",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }
    route_addresses = [
        str(address or "").strip()
        for address in (route_addresses or [])
        if str(address or "").strip()
    ]
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols)
    if (
        len(route_addresses) != len(route_symbols)
        or route_addresses[0].lower() != route_addresses[-1].lower()
    ):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "当前链上执行器要求该三币闭环能解析出完整 token 地址，并用 3 次 V4 swap 闭环执行；缺少地址或闭环尾部地址不一致时不允许链上执行。",
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": broadcast,
        }
    if not OFFICIAL_ARB_SCRIPT.exists() or not OFFICIAL_DEX_DIR.exists():
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_script_missing",
            "detail": f"missing script or DEX workdir: {OFFICIAL_ARB_SCRIPT}",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }

    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = "robinhoodTestnet"
    env["ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST"] = "true"
    env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"] = "true" if broadcast else "false"
    # Prefer the official Robinhood testnet RPC for the live Hardhat script.
    # Some configured public endpoints are reachable over Python but time out
    # inside Node/undici, which makes the broadcast script fail before static
    # call or broadcast can run.
    try:
        probed_rpc_url, probed_block = _probe_robinhood_testnet_rpc()
        env["ROBINHOOD_TESTNET_RPC_URL"] = probed_rpc_url
        env["ROBINHOOD_TESTNET_RPC"] = probed_rpc_url
    except Exception as exc:
        rpc_detail = f"Robinhood Testnet RPC preflight failed: {str(exc)[:300]}"
        lowered = rpc_detail.lower()
        rpc_status = (
            "rpc_verification_timeout"
            if "timeout" in lowered or "timed out" in lowered
            else "rpc_verification_failed"
        )
        payload = {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_rpc_unavailable",
            "detail": rpc_detail,
            "quoteStatus": rpc_status,
            "evidenceStatus": rpc_status,
            "executionTrust": rpc_status,
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }
        _attach_best_amount_calculation(payload, route_symbols=route_symbols, amount=amount)
        return payload

    env["ROBINHOOD_TESTNET_OFFICIAL_ARB_ROUTE"] = " -> ".join(route_symbols)
    env["ROBINHOOD_TESTNET_OFFICIAL_ARB_ADDRESSES"] = ",".join(route_addresses)
    env["ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT"] = str(amount)
    env["ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT_IS_BEST"] = "true" if amount_is_best else "false"
    env["ROBINHOOD_TESTNET_OFFICIAL_ARB_REUSE_AMOUNT"] = "true" if amount_is_best else "false"

    # The live script must run through Hardhat's runner. Running it directly with
    # `node` does not initialize the configured network/provider properly, so
    # static calls and broadcasts fail with `UND_ERR_CONNECT_TIMEOUT` even when
    # the RPC URL itself is reachable.
    npx_executable = str(os.getenv("NPX", "") or shutil.which("npx") or "npx")
    if os.name == "nt" and not npx_executable.lower().endswith(".cmd"):
        npx_cmd = shutil.which("npx.cmd")
        if npx_cmd:
            npx_executable = npx_cmd
    command = [
        npx_executable,
        "--no-install",
        "hardhat",
        "run",
        str(OFFICIAL_ARB_SCRIPT),
        "--network",
        "robinhoodTestnet",
        "--no-compile",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(OFFICIAL_DEX_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_timeout",
            "detail": "链上脚本超时，建议降低金额或检查 RPC。",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }

    appended = result.stdout + "\n" + result.stderr
    parsed: dict[str, Any] = {}
    for json_match in re.finditer(r"OFFICIAL_V4_ARB_RESULT_JSON\s+(\{.*?\})", appended, flags=re.DOTALL):
        try:
            parsed = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            continue

    broadcast_disabled = bool(parsed.get("broadcastDisabled")) and not parsed.get("txHash")
    try:
        parsed_best_amount = float(parsed.get("bestAmount") or "")
    except (TypeError, ValueError):
        parsed_best_amount = None
    best_amount_from_chain = bool(parsed_best_amount is not None and parsed_best_amount > 0)
    selected_amount = parsed_best_amount if best_amount_from_chain else amount
    best_amount_method = (
        parsed.get("bestAmountMethod")
        or (
            "preselected_best_amount"
            if best_amount_from_chain and bool(amount_is_best)
            else "onchain_preview_adaptive_best_amount"
            if best_amount_from_chain
            else "requested_borrow_amount_unconfirmed"
        )
    )
    best_amount_confirmed = bool(best_amount_from_chain)
    best_amount_selection_basis = (
        best_amount_method
        if best_amount_from_chain
        else "initial_probe_unconfirmed"
    )
    best_amount_confirmation_reason = (
        "preselected amount passed through without re-searching"
        if best_amount_from_chain and bool(amount_is_best)
        else "contract preview returned a selected amount"
        if best_amount_from_chain
        else "contract preview did not return a confirmed best amount; current value is initial probe"
    )
    best_amount_attempted = parsed.get("bestAmountAttemptedCount")
    raw_profit = parsed.get("profit")
    profit_human = parsed.get("profitHuman")
    if profit_human is None:
        profit_human = _raw_amount_to_human(raw_profit)
    raw_out_amount = parsed.get("outAmount")
    out_amount_human = parsed.get("outAmountHuman")
    if out_amount_human is None:
        out_amount_human = _raw_amount_to_human(raw_out_amount)
    static_call_ok = str(parsed.get("staticCall") or "").strip().lower() in {"ok", "passed"}
    profitable = bool(profit_human is not None and float(profit_human) > 0)
    reference_net_return_bps = (
        float(profit_human) / float(selected_amount) * 10_000
        if profit_human is not None and selected_amount is not None and float(selected_amount) > 0
        else None
    )
    if not static_call_ok and parsed.get("staticCall") is None and parsed.get("ok") is True and profitable:
        static_call_ok = True
    ready_for_transaction_preflight = bool(static_call_ok and profitable)

    payload = {
        "ok": result.returncode == 0 and parsed.get("ok") is not False,
        "mode": "dex_route_broadcast_test",
        "actionStage": "onchain_execution" if broadcast else "onchain_preflight",
        "routeSymbols": parsed.get("selectedRouteSymbols") or route_symbols,
        "routeAddresses": route_addresses,
        "amount": amount,
        "amountIsBest": bool(amount_is_best),
        "requestedAmount": amount,
        "initialProbeAmount": amount,
        "bestAmount": selected_amount,
        "bestAmountSource": "onchain_contract_preview" if best_amount_from_chain else "requested_initial_amount",
        "bestAmountConfirmed": best_amount_confirmed,
        "bestAmountSelectionBasis": best_amount_selection_basis,
        "bestAmountConfirmationReason": best_amount_confirmation_reason,
        "amountSymbol": route_symbols[0] if route_symbols else None,
        "bestAmountMethod": best_amount_method,
        "bestAmountAttemptedCount": best_amount_attempted,
        "broadcast": broadcast,
        "exitCode": result.returncode,
        "detail": parsed.get("detail") or parsed.get("error") or (result.stderr.strip() or result.stdout.strip()),
        "errorDetail": parsed.get("errorDetail"),
        "errorData": parsed.get("errorData"),
        "errorSelector": parsed.get("errorSelector"),
        "staticCall": parsed.get("staticCall"),
        "staticCallPassed": bool(str(parsed.get("staticCall") or "").strip().lower() in {"ok", "passed"}),
        "onchainPreviewAttempted": bool(parsed.get("bestAmountAttemptedCount") is not None),
        "quoteStatus": "live_verified" if ready_for_transaction_preflight else "live_quote_failed",
        "evidenceStatus": "live_verified" if ready_for_transaction_preflight else "live_quote_failed",
        "executionTrust": "live_quoter_verified_readonly" if ready_for_transaction_preflight else "live_quote_failed",
        "profitable": profitable,
        "readyForTransactionPreflight": ready_for_transaction_preflight,
        "profit": profit_human,
        "profitHuman": profit_human,
        "profitRaw": raw_profit,
        "referenceNetReturnBps": reference_net_return_bps,
        "outAmount": out_amount_human,
        "outAmountHuman": out_amount_human,
        "outAmountRaw": raw_out_amount,
        "txHash": parsed.get("txHash") if broadcast else None,
        "evidence": {
            "status": "live_verified" if ready_for_transaction_preflight else "live_quote_failed",
            "executionTrust": "live_quoter_verified_readonly" if ready_for_transaction_preflight else "live_quote_failed",
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "hops": [],
            "offchainInitialProbeReason": (
                "onchain_review_best_amount_reused"
                if best_amount_from_chain
                else "requested_borrow_amount_fallback"
            ),
        },
        "stdoutTail": _tail(result.stdout),
        "stderrTail": _tail(result.stderr),
        "broadcastDisabled": broadcast_disabled,
    }
    if broadcast and broadcast_disabled:
        payload["ok"] = False
        payload["error"] = "official_arb_broadcast_disabled"
        payload["detail"] = (
            str(parsed.get("detail") or "")
            or "broadcast disabled; static preflight passed"
        )
    if parsed.get("staticCall") == "reverted" and parsed.get("error") == "static_arb_call_reverted":
        payload["detail"] = _static_call_revert_detail(parsed)
    if "already broadcast-disabled" not in payload["detail"] and result.returncode != 0:
        payload["error"] = parsed.get("error") or "official_arb_broadcast_failed"
    _attach_best_amount_calculation(
        payload,
        route_symbols=route_symbols,
        amount=selected_amount,
        requested_amount=amount,
        selected_amount=selected_amount,
        method_override=best_amount_method,
        attempted_count=best_amount_attempted,
        best_amount_confirmed=best_amount_confirmed,
        best_amount_selection_basis=best_amount_selection_basis,
        best_amount_confirmation_reason=best_amount_confirmation_reason,
    )
    return payload


__all__ = [
    "best_amount_calculation_from_route",
    "estimate_route_live_quote",
    "fetch_dex_tx_receipt",
    "is_official_triangle_route",
    "normalize_route_symbols",
    "recommended_route_test_amount",
    "run_official_v4_arb_route_test",
]
