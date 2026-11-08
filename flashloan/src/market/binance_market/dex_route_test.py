from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
from typing import Any

from web3 import Web3

from core.error_registry import ErrorCode
from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood_common.rpc_gate import (
    v4_live_quote_enabled,
)
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID
from dex.runtime.dex_route_rpc import _probe_robinhood_family_rpc, fetch_dex_tx_receipt
from market.binance_market.dex_route_helpers import _match_route
from market.binance_market.dex_route_helpers import _norm_symbols
from market.binance_market.dex_route_helpers import (
    _official_v4_route_pool_key_groups,
    _official_v4_route_pool_keys,
    _route_addresses_from_route,
)
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
# Compatibility aliases kept for tests that still reference the old module
# constants. The live helpers resolve script/dex dir from chain configuration.
OFFICIAL_ARB_SCRIPT = ROOT / "test-token_create" / "scripts" / "tool" / "testnet-v4-official-arb-live.js"
OFFICIAL_DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"


def _probe_robinhood_testnet_rpc() -> tuple[str, int]:
    """Compatibility wrapper used by older tests/scripts."""
    return _probe_robinhood_family_rpc(ROBINHOOD_TESTNET_CHAIN_ID)


def _robinhood_network_name(chain_id: int) -> str:
    row = chain_data_by_chain_id(int(chain_id)) or {}
    return str(row.get("network") or "robinhood").strip().lower()


def _chain_supports_official_v4_arb(chain_id: int | None) -> bool:
    if chain_id is None:
        return False
    row = chain_data_by_chain_id(int(chain_id)) or {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    dex = features.get("dex_arbitrage") if isinstance(features.get("dex_arbitrage"), dict) else {}
    flash_loan = row.get("flash_loan") if isinstance(row.get("flash_loan"), dict) else {}
    protocols = {
        str(protocol or "").strip().lower()
        for protocol in flash_loan.get("candidate_protocols") or []
    }
    return bool(dex.get("protocol_supported")) and "uniswap_v4" in protocols


def _official_arb_script_for_chain(chain_id: int) -> Path:
    network = _robinhood_network_name(chain_id)
    script_network = {
        "robinhood-testnet": "testnet",
        "robinhood": "mainnet",
    }.get(network, network)
    return ROOT / "test-token_create" / "scripts" / "tool" / f"{script_network}-v4-official-arb-live.js"


def _official_dex_dir_for_chain(chain_id: int) -> Path:
    network = _robinhood_network_name(chain_id)
    return ROOT / "contract" / network / "DEX"


def _hardhat_network_for_chain(chain_id: int) -> str:
    row = chain_data_by_chain_id(int(chain_id)) or {}
    tooling = row.get("tooling") if isinstance(row.get("tooling"), dict) else {}
    return str(tooling.get("hardhat_network") or "robinhoodTestnet").strip()


def _robinhood_env_prefix(chain_id: int) -> str:
    return "ROBINHOOD_MAINNET" if _robinhood_network_name(int(chain_id)) == "robinhood" else "ROBINHOOD_TESTNET"


def _structured_route_error(message: object, *, error_code: int, flow: str, node_id: str) -> list[dict[str, Any]]:
    return [
        {
            "module": "DEX",
            "code": int(error_code),
            "flow": flow,
            "nodeId": node_id,
            "message": str(message or "")[:500],
        }
    ]


def _steps_have_live_onchain_evidence(steps: list[dict[str, Any]], analysis: dict[str, Any]) -> bool:
    """Return True only when every hop has explicit chain_live_quote evidence.

    A step that only carries ``executionTrust == live_v4_adapter`` may still be
    an off-chain/static estimate from the surface facade. We therefore require
    the same evidence fields that the live adapter path actually emits.
    """
    if str(analysis.get("priceEvidenceMode") or "").strip().lower() == "live_verified":
        return True
    if not steps:
        return False
    return all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        and str(step.get("quoteSource") or "").strip().lower()
        in {
            "uniswap_v4_live_adapter_quote",
            "chain_live_quote",
        }
        and (
            step.get("priceEvidenceSource") is None
            or str(step.get("priceEvidenceSource") or "").strip().lower() == "chain_live_quote"
        )
        and step.get("livePriceVerified") is True
        for step in steps
    )


def _raw_amount_to_human(value: Any, decimals: int | None = None) -> float | None:
    """Convert a raw integer on-chain amount to a human-readable float.

    The official arb script now emits ``profitHuman`` / ``outAmountHuman``.
    This helper is only a fallback for older outputs that carried raw
    integers, and it requires the actual borrow-token decimals.  No
    18-decimal fallback is applied: without the decimals the raw amount
    cannot be converted into a verified human value.
    """
    if decimals is None:
        return None
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


def _load_route_context(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
    route: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    from market.binance_market.dex_surface_graph import _price_map_from_pairs
    from market.binance_market.pool_candidates import dex_pool_candidates_payload

    route_symbols = _norm_symbols(route_symbols)
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = route if isinstance(route, dict) else _match_route(routes_payload.get("routes") or [], route_symbols)
    if route is None:
        available = [_route_symbols(item) for item in (routes_payload.get("routes") or [])]
        return (
            route_symbols,
            routes_payload,
            None,
            None,
            {
                "ok": False,
                "mode": "dex_route_live_estimate",
                "error": "route_not_found",
                "routeSymbols": route_symbols,
                "availableRoutes": available,
                "errorStack": _structured_route_error(
                    "route_not_found",
                    error_code=ErrorCode.DEX_ROUTE_INVALID,
                    flow="dex_route_live_estimate",
                    node_id="resolve_route",
                ),
            },
        )
    pool_payload = dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    return route_symbols, routes_payload, route, price_map, None


def _choose_direction(
    *,
    quote_analysis_fn: Any,
    route: dict[str, Any],
    route_symbols: list[str],
    route_addresses: list[str],
    price_map: dict[str, Any],
    chain_id: int,
    amount: float,
) -> tuple[list[str], dict[str, Any], dict[str, Any], list[str]]:
    analysis = quote_analysis_fn(
        route,
        price_map=price_map,
        chain_id=chain_id,
        token_basis_input_amount=amount,
    )
    route_addresses = [str(item) for item in analysis.get("routeAddresses") or [] if str(item).strip()]
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols, chain_id=chain_id)
    return route_symbols, route, analysis, route_addresses


def estimate_route_live_quote(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
    amount: float,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from market.binance_market.dex_surface_quotes import _route_quote_analysis

    route_symbols, routes_payload, route, price_map, load_error = _load_route_context(
        chain_id=chain_id,
        protocols=protocols,
        route_symbols=route_symbols,
        route=route,
    )
    if load_error is not None:
        return load_error
    route_symbols, route, analysis, route_addresses = _choose_direction(
        quote_analysis_fn=_route_quote_analysis,
        route=route or {},
        route_symbols=route_symbols,
        route_addresses=[],
        price_map=price_map or {},
        chain_id=chain_id,
        amount=amount,
    )

    requested_amount = float(amount)
    amount_optimized_from = requested_amount
    amount_optimized = False
    probe_records: list[dict[str, Any]] = [_probe_record_item(analysis, amount, route_symbols)]

    # After direction is confirmed, optimize the borrow amount. We scan a small
    # ladder around the requested amount (plus any static size steps already
    # carried on the route) so "best amount" is a chosen point with adjacent
    # probe records, not just whatever amount the route scanner happened to use.
    max_attempts = max(1, int(os.getenv("DEX_V4_LIVE_QUOTE_AMOUNT_OPTIMIZE_ATTEMPTS", "5") or 5))
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
        route_addresses = _official_route_addresses(route_symbols, chain_id=chain_id)
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
    live_verified = _steps_have_live_onchain_evidence(steps, analysis)
    ref_final_output_amount = analysis.get("referenceFinalOutputAmount")
    ref_net_profit_amount = analysis.get("referenceNetProfitAmount")
    ref_net_profit_bps = analysis.get("referenceNetReturnBps")
    if (
        ref_final_output_amount is None
        and steps
        and route_symbols
        and route_symbols[0] == route_symbols[-1]
        and amount is not None
        and amount > 0
    ):
        last_step = steps[-1]
        last_output = last_step.get("outputAmount")
        if last_output is None:
            last_output = last_step.get("referenceOutputAmount")
        try:
            last_output = float(last_output)
            input_amount = float(amount)
            ref_final_output_amount = last_output
            ref_net_profit_amount = last_output - input_amount
            ref_net_profit_bps = ref_net_profit_amount / input_amount * 10_000.0
        except (TypeError, ValueError):
            pass
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
        else "static_reference_pending_live_review"
    )
    # If profit is negative on a live-verified route, mark unprofitable.
    # A static/reference route stays "pending live review" even when the
    # paper P&L is negative, because the chain has not been consulted yet.
    ref_net_profit = analysis.get("referenceNetProfitAmount")
    if live_verified and ref_net_profit is not None and float(ref_net_profit) < 0:
        quote_status = "route_unprofitable"
    execution_trust = (
        "live_v4_adapter"
        if quote_status == "live_verified" or quote_status == "route_unprofitable"
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
        else "static_reference_pending_live_review"
    )
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
            f"已从初始请求量 {amount_optimized_from} {route_symbols[0] if route_symbols else ''} 调整到 {amount} {route_symbols[0] if route_symbols else ''}；该值仅作为链上复核初始量，最佳交易量由链上复核确认"
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
                f"已从初始请求量 {amount_optimized_from} 调整到 {amount}；该值仅作为链上复核初始量，最佳交易量由链上复核确认"
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


def _fixed_amount_pnl_payload(
    *,
    chain_id: int,
    route_symbols: list[str],
    route_addresses: list[str],
    analysis: dict[str, Any],
    amount: float,
) -> dict[str, Any]:
    steps = [step for step in (analysis.get("steps") or []) if isinstance(step, dict)]
    ref_final_output_amount = analysis.get("referenceFinalOutputAmount")
    ref_net_profit_amount = analysis.get("referenceNetProfitAmount")
    ref_net_profit_bps = analysis.get("referenceNetReturnBps")
    profitable = bool(ref_net_profit_amount is not None and float(ref_net_profit_amount) > 0)
    # 固定金额估算只允许把“逐 hop 链上已验证”的结果标记为 live_verified。
    # 仅携带静态/参考步骤、或步骤缺少 chain_live_quote 证据时，必须保持待复核语义。
    live_verified = _steps_have_live_onchain_evidence(steps, analysis)
    quote_status = (
        "live_verified"
        if live_verified and profitable
        else "route_unprofitable"
        if live_verified and ref_net_profit_amount is not None and float(ref_net_profit_amount) <= 0
        else "static_reference_pending_live_review"
    )
    diagnostic_unprofitable = quote_status == "route_unprofitable"
    return {
        "ok": True,
        "mode": "dex_route_fixed_amount_pnl",
        "chain_id": chain_id,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "amount": float(amount),
        "bestAmount": float(amount) if diagnostic_unprofitable else None,
        "bestAmountConfirmed": False,
        "bestAmountComputed": diagnostic_unprofitable,
        "bestAmountMissing": not diagnostic_unprofitable,
        "bestAmountSelectionBasis": "requested_fixed_amount",
        "direction": " -> ".join(route_symbols),
        "directionConfirmed": True,
        "quoteStatus": quote_status,
        "executionTrust": quote_status,
        "liveVerified": live_verified,
        "profitable": profitable,
        "referenceInputSymbol": analysis.get("referenceInputSymbol") or (route_symbols[0] if route_symbols else None),
        "referenceFinalOutputAmount": ref_final_output_amount,
        "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
        "referenceNetProfitAmount": ref_net_profit_amount,
        "referenceNetReturnBps": ref_net_profit_bps,
        "steps": [_step_payload(step) for step in steps],
        "analysis": analysis,
    }


def estimate_fixed_amount_pnl(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
    amount: float,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate a single amount's P&L without running the best-amount ladder.

    This is the narrow public entry for external callers that already know the
    initial amount to test. Direction selection still happens, but no amount
    optimization is performed.
    """
    from market.binance_market.dex_surface_quotes import _route_quote_analysis

    route_symbols, routes_payload, route, price_map, load_error = _load_route_context(
        chain_id=chain_id,
        protocols=protocols,
        route_symbols=route_symbols,
        route=route,
    )
    if load_error is not None:
        return load_error
    route_symbols, route, analysis, route_addresses = _choose_direction(
        quote_analysis_fn=_route_quote_analysis,
        route=route or {},
        route_symbols=route_symbols,
        route_addresses=[],
        price_map=price_map or {},
        chain_id=chain_id,
        amount=amount,
    )
    return _fixed_amount_pnl_payload(
        chain_id=chain_id,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        analysis=analysis,
        amount=amount,
    )


def _observation_fixed_route(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
) -> dict[str, Any] | None:
    route_symbols = _norm_symbols(route_symbols)
    if not _chain_supports_official_v4_arb(chain_id) or "uniswap_v4" not in protocols:
        return None
    network_dir = _robinhood_network_name(chain_id)
    path = (
        ROOT
        / "flashloan"
        / "src"
        / "runtime"
        / "market_observation"
        / network_dir
        / str(chain_id)
        / "uniswap_v4"
        / "observation.json"
    )
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for sequence in data.get("observationSequences") or []:
        if _norm_symbols(sequence.get("pathSymbols") or []) != route_symbols:
            continue
        route_addresses: list[str] = []
        for hop in sequence.get("routePools") or []:
            hop_addresses = [str(item) for item in (hop.get("routeAddresses") or []) if str(item).strip()]
            if len(hop_addresses) < 2:
                continue
            if not route_addresses:
                route_addresses.append(hop_addresses[0])
            route_addresses.append(hop_addresses[1])
        if len(route_addresses) != len(route_symbols):
            continue
        return {
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "routePools": sequence.get("routePools") or [],
            "symbolPair": " -> ".join(route_symbols),
            "overviewRoute": {
                "pathSymbols": route_symbols,
                "pathAddresses": route_addresses,
                "pathTokens": [],
            },
        }
    return None


def _pool_token_pair_addresses(pool: dict[str, Any]) -> list[str]:
    """Return the two token addresses carried by one V4 pool row."""
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    candidates: list[list[str]] = []
    for pair_key in ("token0", "currency0"):
        first = str(pool.get(pair_key) or pool_key.get(pair_key) or "").strip()
        if not first:
            continue
        second_key = "token1" if pair_key == "token0" else "currency1"
        second = str(pool.get(second_key) or pool_key.get(second_key) or "").strip()
        if second:
            candidates.append([first.lower(), second.lower()])
    route_addresses = [
        str(item or "").strip().lower()
        for item in (pool.get("routeAddresses") or [])
        if str(item or "").strip()
    ]
    if len(route_addresses) >= 2:
        candidates.append(route_addresses[:2])
    for candidate in candidates:
        if len(candidate) == 2 and candidate[0] and candidate[1] and candidate[0] != candidate[1]:
            return candidate
    return []


def _pool_for_hop(
    pools: list[dict[str, Any]],
    from_address: str,
    to_address: str,
    *,
    hop_index: int | None = None,
) -> dict[str, Any] | None:
    from_addr = str(from_address or "").strip().lower()
    to_addr = str(to_address or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        pair = _pool_token_pair_addresses(pool)
        if len(pair) == 2 and from_addr in pair and to_addr in pair:
            matches.append(pool)
    if not matches:
        return None
    if hop_index is None:
        return matches[0]

    direction_key = f"{from_addr}:{to_addr}"

    def score(pool: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        recommendations = (
            pool.get("recommendationsByDirection")
            if isinstance(pool.get("recommendationsByDirection"), dict)
            else {}
        )
        recommendation = recommendations.get(direction_key) or {}
        try:
            recommendation_rank = int(recommendation.get("rank") or 0)
        except (TypeError, ValueError):
            recommendation_rank = 0
        preferred = (
            pool.get("is_first_hop_recommended")
            if hop_index == 0
            else pool.get("is_third_hop_recommended")
            if hop_index == 2
            else 0
        )
        try:
            liquidity = int(pool.get("liquidity") or 0)
        except (TypeError, ValueError):
            liquidity = 0
        return (
            1 if recommendation_rank > 0 else 0,
            -recommendation_rank,
            1 if bool(preferred) else 0,
            1 if bool(pool.get("usable_for_observation")) else 0,
            1 if bool(pool.get("has_liquidity")) else 0,
            liquidity,
        )

    return max(matches, key=score)


def _offline_v4_pool_exact_output_raw(
    *,
    pool: dict[str, Any],
    from_address: str,
    to_address: str,
    amount_in_raw: int,
) -> int | None:
    """Compute one Uniswap V4 exact-input output from the local pool snapshot.

    Constant-product math is done in raw token units. For token0 -> token1 the
    output is ``amount * L * sqrtPrice ** 2 / (L + amount * sqrtPrice)``; the
    reverse direction uses ``amount * L / (sqrtPrice * (L * sqrtPrice + amount))``.
    """
    pair = _pool_token_pair_addresses(pool)
    from_addr = str(from_address or "").strip().lower()
    to_addr = str(to_address or "").strip().lower()
    if len(pair) != 2 or from_addr not in pair or to_addr not in pair:
        return None
    from_side = 0 if from_addr == pair[0] else 1 if from_addr == pair[1] else None
    if from_side is None:
        return None
    liquidity = pool.get("liquidity") or pool.get("liquidity_index") or pool.get("liquidityIndex")
    sqrt_price_x96 = pool.get("sqrtPriceX96") or pool.get("sqrt_price_x96")
    pool_key_sqrt_price_x96 = pool.get("poolKeySqrtPriceX96")
    if liquidity in (None, "") or sqrt_price_x96 in (None, ""):
        return None
    try:
        liquidity_decimal = Decimal(str(liquidity))
        fee = int(pool.get("fee") or 0)
        amount_in = Decimal(int(amount_in_raw))
        if fee > 0:
            amount_in = amount_in * (Decimal(1_000_000) - Decimal(fee)) // Decimal(1_000_000)
        if liquidity_decimal <= 0 or amount_in <= 0:
            return None
        prices: list[Decimal] = []
        for raw_price in (sqrt_price_x96, pool_key_sqrt_price_x96):
            if raw_price in (None, ""):
                continue
            sqrt_price = Decimal(str(raw_price)) / (Decimal(2) ** 96)
            if sqrt_price > 0 and sqrt_price not in prices:
                prices.append(sqrt_price)
        if not prices:
            return None
        if len(prices) == 2:
            ratio = prices[0] / prices[1]
            # A candidate row whose live sqrt price diverges by six orders of
            # magnitude from its immutable PoolKey is corrupt/stale data, not
            # a market move. Use the PoolKey snapshot until pool-manager refresh
            # replaces the row.
            if ratio > Decimal(1_000_000) or ratio < Decimal("0.000001"):
                prices = [prices[1], prices[0]]
        for sqrt_price in prices:
            if from_side == 0:
                output_raw = (amount_in * liquidity_decimal * sqrt_price * sqrt_price) // (
                    liquidity_decimal + amount_in * sqrt_price
                )
            else:
                output_raw = (amount_in * liquidity_decimal) // (
                    sqrt_price * (liquidity_decimal * sqrt_price + amount_in)
                )
            if output_raw > 0:
                return int(output_raw)
        return None
    except (TypeError, ValueError, ArithmeticError):
        return None


def _offline_pool_token_decimals_map(chain_id: int, pool_manager: str) -> dict[str, int]:
    """Read token decimals from the SQLite chain token registry only.

    The local database is the only acceptable source for per-token decimals.
    No 18-decimal fallback is applied: a pool that lacks the registry row
    cannot form a closed-loop reference P&L.
    """
    try:
        from market.binance_market.dex_surface_v4 import _v4_token_metadata_cache

        metadata = _v4_token_metadata_cache(str(pool_manager or ""), chain_id=int(chain_id))
    except Exception:
        metadata = {}
    decimals: dict[str, int] = {}
    for address, row in metadata.items():
        if not isinstance(row, dict):
            continue
        raw = row.get("decimals")
        try:
            value = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        if value is not None:
            decimals[str(address or "").strip().lower()] = value
    return decimals


def _borrow_token_decimals_from_registry(*, chain_id: int, borrow_address: str) -> int | None:
    """Resolve borrow-token decimals from the SQLite chain token registry.

    No 18-decimal fallback is applied: a route whose borrow token lacks
    registry decimals cannot form a validated closed loop and must fail
    closed instead of guessing a decimal count.
    """
    normalized_address = str(borrow_address or "").strip().lower()
    resolved_chain_id = int(chain_id or 0)
    if not normalized_address or not resolved_chain_id:
        return None
    network = (
        "robinhood-testnet"
        if resolved_chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else "robinhood"
    )
    try:
        from core.db.storage_chain_core import load_chain_tokens

        database_path = ""
        try:
            from core.runtime.chain_runtime import chain_runtime_registry

            database_path = str(
                chain_runtime_registry().get(resolved_chain_id).database_url
            )
        except (
            AttributeError,
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            database_path = ""
        if not database_path:
            from core.db.chain_database_context import chain_database_path

            database_path = str(
                chain_database_path(resolved_chain_id, network).resolve()
            )
        for row in load_chain_tokens(
            database_path,
            network=network,
            chain_id=resolved_chain_id,
        ):
            if (
                str(row.get("token_address") or "").strip().lower()
                != normalized_address
            ):
                continue
            raw = row.get("token_decimals")
            if raw in (None, ""):
                return None
            return int(raw)
    except (
        ImportError,
        KeyError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ):
        database_path = ""
    try:
        from market.binance_market.dex_surface_v4 import (
            _pool_manager_address,
            _v4_token_metadata_cache,
        )

        pool_manager = _pool_manager_address({}, chain_id=resolved_chain_id)
        row = _v4_token_metadata_cache(pool_manager, chain_id=int(chain_id)).get(
            normalized_address
        )
        raw = (row or {}).get("decimals") if isinstance(row, dict) else None
        value = int(raw) if raw not in (None, "") else None
        return value
    except Exception:
        return None


def _offline_pool_token_pnl_reference(
    *,
    chain_id: int,
    route_symbols: list[str],
    route_addresses: list[str],
    amount: float,
    protocols: list[str] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a borrow-token P&L reference from the local V4 pool snapshot.

    The estimate is deliberately token-based: final output, net profit and
    return bps are all measured in the first-hop borrow token. It never
    converts through USD and never assumes a token decimal count.
    """
    from market.binance_market.dex_surface_graph import _route_pools
    from market.binance_market.dex_surface_route_quotes import (
        DEFAULT_FLASH_LOAN_FEE_BPS,
    )

    route_symbols = _norm_symbols(route_symbols)
    route_addresses = [
        str(item or "").strip()
        for item in route_addresses
        if str(item or "").strip()
    ]
    error_payload = {
        "ok": False,
        "mode": "dex_route_fixed_amount_pnl_offline_pool_snapshot",
        "error": "offline_pool_token_pnl_unavailable",
        "detail": (
            "local V4 pool snapshot lacks required token decimals, liquidity "
            "or sqrtPriceX96 for borrow-token P&L; no 18-decimal fallback applied"
        ),
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "amount": float(amount),
    }
    if not isinstance(route, dict):
        _, _, route, _, load_error = _load_route_context(
            chain_id=chain_id,
            protocols=protocols or ["uniswap_v4"],
            route_symbols=route_symbols,
            route=None,
        )
        if load_error is not None or not isinstance(route, dict):
            return error_payload
    if not route_addresses and isinstance(route, dict):
        route_addresses = [
            str(item or "").strip()
            for item in route.get("routeAddresses") or []
            if str(item or "").strip()
        ]
    if len(route_symbols) < 2 or len(route_addresses) != len(route_symbols):
        return error_payload
    pools = _route_pools(route)
    pool_manager = str(
        (pools[0].get("poolManager") if pools else None)
        or (route.get("poolManager") or "")
    ).strip()
    decimals_map = _offline_pool_token_decimals_map(chain_id, pool_manager)
    token_decimals: dict[str, int] = {}
    for address in route_addresses:
        value = decimals_map.get(str(address or "").strip().lower())
        if value is not None:
            token_decimals[str(address or "").strip().lower()] = value
    if any(
        str(address or "").strip().lower() not in token_decimals
        for address in route_addresses
    ):
        return error_payload
    current_amount = float(amount)
    steps: list[dict[str, Any]] = []
    for index in range(len(route_symbols) - 1):
        from_address = route_addresses[index]
        to_address = route_addresses[index + 1]
        pool = _pool_for_hop(pools, from_address, to_address)
        if pool is None:
            return error_payload
        decimals_in = token_decimals[str(from_address or "").strip().lower()]
        decimals_out = token_decimals[str(to_address or "").strip().lower()]
        amount_in_raw = int(Decimal(str(current_amount)) * (Decimal(10) ** decimals_in))
        output_raw = _offline_v4_pool_exact_output_raw(
            pool=pool,
            from_address=from_address,
            to_address=to_address,
            amount_in_raw=amount_in_raw,
        )
        if output_raw is None:
            return error_payload
        output_amount = float(Decimal(output_raw) / (Decimal(10) ** decimals_out))
        fee_bps = int(pool.get("fee") or 0) / 100.0
        steps.append(
            {
                "hop": index + 1,
                "fromSymbol": route_symbols[index],
                "toSymbol": route_symbols[index + 1],
                "tokenIn": from_address,
                "tokenOut": to_address,
                "pool": pool.get("pool") or pool.get("poolId"),
                "feeBps": fee_bps,
                "inputAmount": current_amount,
                "outputAmount": output_amount,
                "referenceOutputAmount": output_amount,
                "effectiveRate": output_amount / current_amount if current_amount > 0 else None,
                "referenceEffectiveRate": output_amount / current_amount if current_amount > 0 else None,
                "priceImpactBps": None,
                "executionTrust": "offline_pool_snapshot_reference",
                "quoteSource": "local_sqlite_v4_pool_snapshot",
                "liveQuoteError": None,
            }
        )
        current_amount = output_amount
    flash_loan_fee_amount = float(amount) * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    net_profit_amount = current_amount - float(amount) - flash_loan_fee_amount
    net_return_bps = (
        net_profit_amount / float(amount) * 10_000.0
        if float(amount) > 0
        else None
    )
    analysis = {
        "method": "offline_v4_pool_snapshot_token_basis",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "referenceInputSymbol": route_symbols[0],
        "referenceInputAmount": float(amount),
        "referenceFinalOutputAmount": current_amount,
        "referenceFinalOutputSymbol": route_symbols[0],
        "referenceNetProfitAmount": net_profit_amount,
        "referenceNetReturnBps": net_return_bps,
        "referenceReturnBasis": "borrow_token",
        "referenceReturnTrusted": True,
        "tokenDecimalsTrusted": True,
        "complete": True,
        "requiresLiveQuoter": True,
        "steps": steps,
        "offlinePoolSnapshotSource": "sqlite_db",
        "offlinePoolSnapshotBasis": "borrow_token",
    }
    return _fixed_amount_pnl_payload(
        chain_id=chain_id,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        analysis=analysis,
        amount=amount,
    )


def _offline_candidate_pool_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a pool-manager candidate row for offline route math."""
    raw_key = row.get("pool_key_json")
    if isinstance(raw_key, str):
        try:
            pool_key = json.loads(raw_key)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    elif isinstance(raw_key, dict):
        pool_key = raw_key
    else:
        return None
    if not isinstance(pool_key, dict):
        return None
    pool_id = str(row.get("pool_id") or pool_key.get("poolId") or "").strip()
    token0 = str(
        row.get("token0") or pool_key.get("token0") or pool_key.get("currency0") or ""
    ).strip().lower()
    token1 = str(
        row.get("token1") or pool_key.get("token1") or pool_key.get("currency1") or ""
    ).strip().lower()
    candidate_sqrt_price = row.get("sqrt_price_x96") or ""
    pool_key_sqrt_price = pool_key.get("sqrtPriceX96") or ""
    sqrt_price = candidate_sqrt_price or pool_key_sqrt_price
    liquidity = row.get("liquidity") or ""
    if not pool_id or not token0 or not token1 or token0 == token1:
        return None
    if not str(sqrt_price).strip() or not str(liquidity).strip():
        return None
    try:
        fee = int(row.get("fee") if row.get("fee") not in (None, "") else pool_key.get("fee"))
    except (TypeError, ValueError):
        return None
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolManager": row.get("pool_manager") or pool_key.get("poolManager"),
        "poolKey": pool_key,
        "token0": token0,
        "token1": token1,
        "currency0": token0,
        "currency1": token1,
        "fee": fee,
        "liquidity": str(liquidity),
        "liquidity_index": row.get("liquidity_index"),
        "sqrtPriceX96": str(sqrt_price),
        "poolKeySqrtPriceX96": str(pool_key_sqrt_price or ""),
        "has_liquidity": row.get("has_liquidity"),
        "usable_for_observation": row.get("usable_for_observation"),
        "is_first_hop_recommended": row.get("is_first_hop_recommended"),
        "is_third_hop_recommended": row.get("is_third_hop_recommended"),
    }


def _offline_candidate_pools_for_route(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    protocol: str,
    route_addresses: list[str],
) -> list[dict[str, Any]]:
    """Read only the pool-manager-owned candidate surface for a route."""
    from core.db.storage_business_io import query as _query

    placeholders = ",".join("?" for _ in route_addresses)
    rows = _query(
        database_url,
        f"""
        SELECT pool_id, pool_key_json, pool_manager, token0, token1, fee,
               sqrt_price_x96, has_liquidity, liquidity, liquidity_index,
               usable_for_observation, is_first_hop_recommended,
               is_third_hop_recommended
        FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND (token0 IN ({placeholders}) OR token1 IN ({placeholders}))
        ORDER BY updated_at DESC, pool_id
        """,
        (network, int(chain_id), protocol, *route_addresses, *route_addresses),
    )
    direction_keys = [
        f"{route_addresses[index].lower()}:{route_addresses[index + 1].lower()}"
        for index in range(len(route_addresses) - 1)
    ]
    recommendation_placeholders = ",".join("?" for _ in direction_keys)
    recommendation_rows = _query(
        database_url,
        f"""
        SELECT pool_id, direction_key, hop, rank
        FROM dex_candidate_pool_recommendations
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND direction_key IN ({recommendation_placeholders})
        ORDER BY direction_key, rank
        """,
        (network, int(chain_id), protocol, *direction_keys),
    )
    recommendations: dict[tuple[str, str], dict[str, Any]] = {}
    for row in recommendation_rows:
        pool_id = str(row.get("pool_id") or "").strip().lower()
        direction_key = str(row.get("direction_key") or "").strip().lower()
        if pool_id and direction_key:
            recommendations[(pool_id, direction_key)] = {
                "hop": row.get("hop"),
                "rank": row.get("rank"),
            }
    pools: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        pool = _offline_candidate_pool_payload(row)
        if pool is None:
            continue
        key = (
            str(pool["poolId"]).lower(),
            str(pool["token0"]).lower(),
            str(pool["token1"]).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        pool["recommendationsByDirection"] = {
            direction_key: recommendation
            for (pool_id, direction_key), recommendation in recommendations.items()
            if pool_id == str(pool["poolId"]).lower()
        }
        pools.append(pool)
    return pools


def _offline_v4_route_sqlite_pnl_reference(
    *,
    chain_id: int,
    route_symbols: list[str],
    route_addresses: list[str],
    amount: float,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Fast borrow-token P&L reference read directly from SQLite tables only.

    The full market payload path can take tens of seconds because it rebuilds
    the route framework and reads every candidate pool. This entry keeps the
    same token-basis math but only queries ``chain_tokens`` and
    ``dex_candidate_pools`` for the exact route addresses, so unprofitable
    routes still get concrete signed profit/final-output values without a slow
    observation reload.
    """
    from core.db.database_path import absolute_database_path
    from market.binance_market.dex_surface_route_quotes import (
        DEFAULT_FLASH_LOAN_FEE_BPS,
    )

    route_symbols = _norm_symbols(route_symbols)
    route_addresses = [
        str(item or "").strip().lower()
        for item in route_addresses
        if str(item or "").strip()
    ]
    network = _robinhood_network_name(int(chain_id or 0))
    protocol = "uniswap_v4"
    db = str(absolute_database_path(database_url))
    error_payload = {
        "ok": False,
        "mode": "dex_route_fixed_amount_pnl_offline_sqlite_snapshot",
        "error": "offline_sqlite_route_pnl_unavailable",
        "reason": "pool_manager_refresh_required",
        "detail": (
            "SQLite chain token decimals or an eligible pool-manager candidate "
            "pool is missing; raw pool-table fallback is disabled"
        ),
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "amount": float(amount),
    }
    if len(route_symbols) != 4 or len(route_addresses) != 4:
        return error_payload
    if not (float(amount) > 0):
        return error_payload
    placeholders = ",".join("?" for _ in route_addresses)
    try:
        from core.db.storage_business_io import query as _query

        token_rows = _query(
            db,
            f"""
            SELECT token_address, token_decimals
            FROM chain_tokens
            WHERE network = ? AND chain_id = ? AND token_address IN ({placeholders})
            """,
            (network, int(chain_id), *route_addresses),
        )
    except Exception:
        return error_payload
    token_decimals: dict[str, int] = {}
    for row in token_rows:
        address = str(row.get("token_address") or "").strip().lower()
        raw_decimals = row.get("token_decimals")
        if not address:
            continue
        try:
            value = int(raw_decimals) if raw_decimals not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        if value is not None:
            token_decimals[address] = value
    if any(
        str(address or "").strip().lower() not in token_decimals
        for address in route_addresses
    ):
        return error_payload
    try:
        pools = _offline_candidate_pools_for_route(
            database_url=db,
            network=network,
            chain_id=int(chain_id),
            protocol=protocol,
            route_addresses=route_addresses,
        )
    except Exception:
        return error_payload
    if not pools:
        return error_payload
    current_amount = float(amount)
    steps: list[dict[str, Any]] = []
    for index in range(len(route_symbols) - 1):
        from_address = route_symbols[index]
        to_address = route_symbols[index + 1]
        from_token = route_addresses[index]
        to_token = route_addresses[index + 1]
        hop_pool = _pool_for_hop(
            pools,
            from_token,
            to_token,
            hop_index=index,
        )
        if hop_pool is None:
            return error_payload
        amount_in_raw = int(
            Decimal(str(current_amount)) * (Decimal(10) ** token_decimals[from_token])
        )
        output_raw = _offline_v4_pool_exact_output_raw(
            pool=hop_pool,
            from_address=from_token,
            to_address=to_token,
            amount_in_raw=amount_in_raw,
        )
        if output_raw is None:
            return error_payload
        output_amount = float(
            Decimal(output_raw) / (Decimal(10) ** token_decimals[to_token])
        )
        fee_bps = int(hop_pool.get("fee") or 0) / 100.0
        steps.append(
            {
                "hop": index + 1,
                "fromSymbol": from_address,
                "toSymbol": to_address,
                "tokenIn": from_token,
                "tokenOut": to_token,
                "pool": hop_pool.get("pool") or hop_pool.get("poolId"),
                "feeBps": fee_bps,
                "inputAmount": current_amount,
                "outputAmount": output_amount,
                "referenceOutputAmount": output_amount,
                "effectiveRate": (
                    output_amount / current_amount if current_amount > 0 else None
                ),
                "referenceEffectiveRate": (
                    output_amount / current_amount if current_amount > 0 else None
                ),
                "priceImpactBps": None,
                "executionTrust": "offline_sqlite_pool_snapshot_reference",
                "quoteSource": "local_sqlite_v4_pool_snapshot",
                "liveQuoteError": None,
            }
        )
        current_amount = output_amount
    flash_loan_fee_amount = float(amount) * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    net_profit_amount = current_amount - float(amount) - flash_loan_fee_amount
    net_return_bps = (
        net_profit_amount / float(amount) * 10_000.0
        if float(amount) > 0
        else None
    )
    analysis = {
        "method": "offline_v4_sqlite_route_snapshot_token_basis",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "referenceInputSymbol": route_symbols[0],
        "referenceInputAmount": float(amount),
        "referenceFinalOutputAmount": current_amount,
        "referenceFinalOutputSymbol": route_symbols[0],
        "referenceNetProfitAmount": net_profit_amount,
        "referenceNetReturnBps": net_return_bps,
        "referenceReturnBasis": "borrow_token",
        "referenceReturnTrusted": True,
        "tokenDecimalsTrusted": True,
        "complete": True,
        "requiresLiveQuoter": True,
        "steps": steps,
        "offlinePoolSnapshotSource": "sqlite_db",
        "offlinePoolSnapshotBasis": "borrow_token",
    }
    return _fixed_amount_pnl_payload(
        chain_id=chain_id,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        analysis=analysis,
        amount=amount,
    )


def estimate_fixed_amount_pnl_quick(
    *,
    chain_id: int,
    route_symbols: list[str],
    amount: float,
    route_addresses: list[str] | None = None,
    protocols: list[str] | None = None,
    prefer_sqlite_snapshot: bool = False,
) -> dict[str, Any]:
    """Fast fixed-amount P&L estimate using the local observation snapshot.

    This entry avoids rebuilding the full route framework, which can be slow
    when the caller already knows the exact route and only needs a single
    amount's reference output/profit. It never broadcasts a transaction.
    """
    route_addresses = [
        str(item or "").strip()
        for item in (route_addresses or [])
        if str(item or "").strip()
    ]
    if len(route_addresses) != 4:
        route_addresses = _official_route_addresses(route_symbols, chain_id=int(chain_id or 0))
    if prefer_sqlite_snapshot and len(route_addresses) == 4:
        try:
            sqlite_pnl = _offline_v4_route_sqlite_pnl_reference(
                chain_id=chain_id,
                route_symbols=route_symbols,
                route_addresses=route_addresses,
                amount=amount,
            )
            if sqlite_pnl.get("referenceNetProfitAmount") is not None:
                return sqlite_pnl
        except (
            ArithmeticError,
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ):
            sqlite_pnl = {}
    route = _observation_fixed_route(
        chain_id=chain_id,
        protocols=protocols or ["uniswap_v4"],
        route_symbols=route_symbols,
    )
    if route is None:
        return _offline_pool_token_pnl_reference(
            chain_id=chain_id,
            route_symbols=route_symbols,
            route_addresses=[],
            amount=amount,
            protocols=protocols or ["uniswap_v4"],
        )
    from market.binance_market.dex_surface_quotes import _route_quote_analysis

    analysis = _route_quote_analysis(
        route,
        price_map={},
        chain_id=chain_id,
        token_basis_input_amount=amount,
        use_static_observation=True,
    )
    route_symbols = list(analysis.get("routeSymbols") or route["routeSymbols"])
    route_addresses = [
        str(item)
        for item in analysis.get("routeAddresses") or route.get("routeAddresses") or []
        if str(item).strip()
    ]
    if analysis.get("referenceNetProfitAmount") is None:
        return _offline_pool_token_pnl_reference(
            chain_id=chain_id,
            route_symbols=route_symbols,
            route_addresses=route_addresses,
            amount=amount,
            protocols=protocols or ["uniswap_v4"],
            route=route,
        )
    return _fixed_amount_pnl_payload(
        chain_id=chain_id,
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        analysis=analysis,
        amount=amount,
    )


def is_official_triangle_route(route_symbols: list[str]) -> bool:
    route_symbols = _norm_symbols(route_symbols)
    if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
        return False
    return len({symbol.upper() for symbol in route_symbols}) == 3


def _resolve_route_addresses_from_market_payload(
    *,
    route_symbols: list[str],
    chain_id: int,
    protocols: list[str],
) -> list[str]:
    """Resolve exact route addresses from the market observation payload.

    The static allowlist only knows curated token symbols. The market
    observation payload still carries full addresses for dynamically
    discovered V4 pool tokens, so route review should not give up just because
    a symbol like ``TSLA`` or ``0X102154`` is not in the curated allowlist.
    """
    if not _chain_supports_official_v4_arb(int(chain_id or 0)) or not route_symbols:
        return []
    try:
        routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
        route = _match_route(routes_payload.get("routes") or [], route_symbols)
    except Exception:
        return []
    if not isinstance(route, dict):
        address_symbols = [symbol for symbol in route_symbols if re.fullmatch(r"0x[0-9a-fA-F]{40}", symbol, flags=re.IGNORECASE)]
        if len(address_symbols) == len(route_symbols):
            normalized = [symbol.lower() for symbol in address_symbols]
            for observed_route in routes_payload.get("routes") or []:
                observed_addresses = _route_addresses_from_route(observed_route)
                if observed_addresses and [address.lower() for address in observed_addresses] == normalized:
                    return observed_addresses
        return []
    candidates: list[list[str]] = []
    for key in ("routeAddresses", "route_addresses", "pathAddresses", "path_addresses"):
        raw = route.get(key)
        if isinstance(raw, list):
            candidates.append([str(item or "").strip() for item in raw if str(item or "").strip()])
    overview = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
    for key in ("pathAddresses", "routeAddresses"):
        raw = overview.get(key)
        if isinstance(raw, list):
            candidates.append([str(item or "").strip() for item in raw if str(item or "").strip()])
    raw_tokens = route.get("routeTokenPath") or overview.get("pathTokens") or []
    if isinstance(raw_tokens, list):
        token_addresses: list[str] = []
        for item in raw_tokens:
            if isinstance(item, dict):
                address = str(item.get("address") or "").strip()
                if address:
                    token_addresses.append(address)
        if token_addresses:
            candidates.append(token_addresses)
    for addresses in candidates:
        if (
            len(addresses) == len(route_symbols)
            and addresses[0].lower() == addresses[-1].lower()
        ):
            return addresses
    return []


_OFFICIAL_V4_PREVIEW_ABI = [
    {
        "type": "function",
        "name": "previewArbitrageBestAmount",
        "stateMutability": "view",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "keysets",
                "type": "tuple[3][]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "directionsets", "type": "bool[3][]"},
            {"name": "initialAmount", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
            {"name": "isBestAmount", "type": "bool"},
        ],
        "outputs": [
            {"name": "selectedAmount", "type": "uint256"},
            {"name": "selectedRouteIndex", "type": "uint256"},
            {"name": "selectedProfit", "type": "uint256"},
            {"name": "attemptedCount", "type": "uint256"},
        ],
    }
]

_OFFICIAL_V4_RUN_ABI = [
    {
        "type": "function",
        "name": "runArbitrage",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "keys",
                "type": "tuple[3]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "zeroForOne", "type": "bool[3]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outTb", "type": "uint256"},
            {"name": "outTa", "type": "uint256"},
            {"name": "outTz", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
        ],
    }
]


_OFFICIAL_V4_RUN_BEST_ABI = [
    {
        "type": "function",
        "name": "runArbitrageBest",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "keysets",
                "type": "tuple[3][]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "directionsets", "type": "bool[3][]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outTb", "type": "uint256"},
            {"name": "outTa", "type": "uint256"},
            {"name": "outTz", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
            {"name": "routeIndex", "type": "uint256"},
        ],
    }
]


_OFFICIAL_V4_POOL_PREVIEW_ABI = [
    {
        "type": "function",
        "name": "previewBestPool",
        "stateMutability": "view",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "entryKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "entryDirections", "type": "bool[]"},
            {
                "name": "bridgeKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "bridgeDirections", "type": "bool[]"},
            {
                "name": "exitKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "exitDirections", "type": "bool[]"},
        ],
        "outputs": [
            {"name": "entryIndex", "type": "uint256"},
            {"name": "bridgeIndex", "type": "uint256"},
            {"name": "exitIndex", "type": "uint256"},
            {"name": "netProduct", "type": "uint256"},
        ],
    }
]

_OFFICIAL_V4_PREVIEW_BEST_POOL_SELECTOR = bytes.fromhex(
    Web3.keccak(
        text=(
            "previewBestPool(address,"
            "(address,address,uint24,int24,address)[],bool[],"
            "(address,address,uint24,int24,address)[],bool[],"
            "(address,address,uint24,int24,address)[],bool[])"
        )
    ).hex()[2:10]
)


def _official_v4_contract_has_selector(
    w3: Any,
    arb_address: str,
    selector: bytes,
) -> bool:
    """Return whether the deployed arb runtime contains the requested selector."""
    code = w3.eth.get_code(Web3.to_checksum_address(arb_address))
    return selector in bytes(code)


_OFFICIAL_V4_POOL_RUN_ABI = [
    {
        "type": "function",
        "name": "runArbitrageBestPoolsWithSelectedAmount",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "entryKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "entryDirections", "type": "bool[]"},
            {
                "name": "bridgeKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "bridgeDirections", "type": "bool[]"},
            {
                "name": "exitKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "exitDirections", "type": "bool[]"},
            {"name": "selectedAmount", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outTb", "type": "uint256"},
            {"name": "outTa", "type": "uint256"},
            {"name": "outTz", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
            {"name": "entryIndex", "type": "uint256"},
            {"name": "bridgeIndex", "type": "uint256"},
            {"name": "exitIndex", "type": "uint256"},
        ],
    }
]


def _single_direction_contract_sets(
    keys: list[list[Any]],
    directions: list[bool],
) -> tuple[list[list[list[Any]]], list[list[bool]]]:
    """Wrap one fixed direction set for the official V4 arb arrays."""
    return [list(keys)], [list(directions)]


def _official_v4_hop_candidate_sets(
    pool_key_groups: list[list[dict[str, Any]]],
    route_addresses: list[str],
) -> tuple[list[list[Any]], list[bool], list[list[Any]], list[bool], list[list[Any]], list[bool]]:
    """Flatten backup pool groups into one candidate array per hop.

    Every complete route in ``route_pool_key_groups`` contributes one
    entry/bridge/exit pool.  Duplicate pool+direction pairs are removed so the
    broadcast payload stays small while still carrying every backup pool.
    Directions are derived from the fixed token order of the closed triangle.
    """
    if len(route_addresses) != 4 or route_addresses[0].lower() != route_addresses[-1].lower():
        raise ValueError("official V4 route must be a closed 4-address triangle")
    hop_keys: list[list[list[Any]]] = [[], [], []]
    hop_directions: list[list[bool]] = [[], [], []]
    seen: set[tuple[Any, ...]] = set()
    for pool_keys in pool_key_groups:
        if len(pool_keys) != 3:
            raise ValueError("official V4 pool key group must contain exactly 3 PoolKeys")
        keys: list[list[Any]] = []
        for index in range(3):
            key = pool_keys[index]
            keys.append(
                [
                    str(key.get("currency0") or ""),
                    str(key.get("currency1") or ""),
                    int(key.get("fee") or 0),
                    int(key.get("tickSpacing") or 0),
                    str(key.get("hooks") or "0x0000000000000000000000000000000000000000"),
                ]
            )
        for hop_index in range(3):
            input_token = str(route_addresses[hop_index]).lower()
            output_token = str(route_addresses[hop_index + 1]).lower()
            key = keys[hop_index]
            direction = input_token == key[0].lower()
            key_in = key[0].lower() if direction else key[1].lower()
            key_out = key[1].lower() if direction else key[0].lower()
            if key_in != input_token or key_out != output_token:
                raise ValueError("official V4 pool key group hop does not match route token order")
            identity = (tuple(key), direction)
            if identity in seen:
                continue
            seen.add(identity)
            hop_keys[hop_index].append(list(key))
            hop_directions[hop_index].append(direction)
    if any(len(items) == 0 for items in hop_keys):
        raise ValueError("official V4 route needs at least one pool per hop")
    return (
        hop_keys[0],
        hop_directions[0],
        hop_keys[1],
        hop_directions[1],
        hop_keys[2],
        hop_directions[2],
    )


def _selected_route_symbols(route_symbols: list[str], route_index: int) -> list[str]:
    """Return forward or reverse symbols for the selected triangle route index."""
    if int(route_index or 0) == 0:
        return list(route_symbols)
    return [route_symbols[0], route_symbols[2], route_symbols[1], route_symbols[0]]


def _preview_official_v4_best_amount(
    contract: Any,
    *,
    token: str,
    keysets: list[list[list[Any]]],
    directionsets: list[list[bool]],
    amount_raw: int,
    min_profit: int,
    is_best_amount: bool,
    block_number: int | str,
) -> tuple[int, int, int, int]:
    """Call previewArbitrageBestAmount and return its four decoded outputs."""
    result = contract.functions.previewArbitrageBestAmount(
        Web3.to_checksum_address(token),
        keysets,
        directionsets,
        amount_raw,
        min_profit,
        is_best_amount,
    ).call(block_identifier=block_number, transaction={"gas": 50_000_000})
    return tuple(int(item) for item in result)


def _official_v4_probe_single_route(
    contract: Any,
    *,
    token: str,
    keysets: list[list[list[Any]]],
    directionsets: list[list[bool]],
    amount_raw: int,
    min_profit: int,
    block_number: int | str,
) -> tuple[bool, int, int, int]:
    """Probe one exact amount on one fixed route with isBestAmount=true."""
    try:
        selected_amount_raw, route_index, selected_profit_raw, attempted_count = (
            _preview_official_v4_best_amount(
                contract,
                token=token,
                keysets=keysets,
                directionsets=directionsets,
                amount_raw=amount_raw,
                min_profit=min_profit,
                is_best_amount=True,
                block_number=block_number,
            )
        )
        if selected_amount_raw <= 0 or selected_profit_raw <= 0 or int(route_index) != 0:
            return False, 0, 0, 1
        return True, amount_raw, selected_profit_raw, int(attempted_count)
    except Exception as exc:
        data = str(getattr(exc, "data", "") or "")
        message = str(exc)
        if "0x4f422847" in data or "NoProfitableRoute" in message:
            return False, 0, 0, 1
        lowered = message.lower()
        if data in ("", "0x") and (
            "execution reverted" in lowered
            or "revert" in lowered
            or "call exception" in lowered
        ):
            return False, 0, 0, 1
        raise


def _official_v4_preview_best_amount_single_call(
    contract: Any,
    *,
    token: str,
    keysets: list[list[list[Any]]],
    directionsets: list[list[bool]],
    amount_raw: int,
    min_profit: int,
    block_number: int | str,
) -> tuple[int, int, int, int]:
    """Run the contract-internal adaptive search in one eth_call."""
    return _preview_official_v4_best_amount(
        contract,
        token=token,
        keysets=keysets,
        directionsets=directionsets,
        amount_raw=amount_raw,
        min_profit=min_profit,
        is_best_amount=False,
        block_number=block_number,
    )


def _double_amount_python(amount: int) -> int:
    max_uint256 = (1 << 256) - 1
    if amount > max_uint256 // 2:
        return 0
    return amount * 2


def _official_v4_adaptive_best_amount_single_route(
    contract: Any,
    *,
    token: str,
    keysets: list[list[list[Any]]],
    directionsets: list[list[bool]],
    initial_amount_raw: int,
    min_profit: int,
    block_number: int | str,
    max_steps: int = 24,
) -> tuple[int, int, int]:
    """Run the same fixed-route probe ladder as the JS live script."""
    max_steps = max(1, int(max_steps))
    selected_amount = 0
    selected_profit = 0
    attempted_count = 0
    base_amount = 0
    lower_amount = 0
    upper_amount = 0
    has_upper_amount = False

    def probe(amount_raw: int) -> tuple[bool, int, int, int]:
        return _official_v4_probe_single_route(
            contract,
            token=token,
            keysets=keysets,
            directionsets=directionsets,
            amount_raw=amount_raw,
            min_profit=min_profit,
            block_number=block_number,
        )

    initial_ok, _, initial_profit, initial_attempts = probe(initial_amount_raw)
    attempted_count += initial_attempts
    if initial_ok:
        selected_amount = initial_amount_raw
        selected_profit = initial_profit
        base_amount = initial_amount_raw
        lower_amount = initial_amount_raw // 2
        candidate = _double_amount_python(base_amount)
        for _ in range(max_steps):
            if candidate <= 0:
                break
            ok, _, profit, attempts = probe(candidate)
            attempted_count += attempts
            if ok and profit > selected_profit:
                selected_amount = candidate
                selected_profit = profit
                base_amount = candidate
                lower_amount = candidate // 2
            else:
                upper_amount = candidate
                has_upper_amount = True
                break
            candidate = _double_amount_python(candidate)
        if not has_upper_amount and selected_amount > 0:
            upper_amount = _double_amount_python(selected_amount)
            base_amount = selected_amount
            lower_amount = selected_amount // 2
            has_upper_amount = upper_amount > 0
    else:
        upper_amount = initial_amount_raw
        candidate = initial_amount_raw // 2
        for _ in range(max_steps):
            if candidate <= 0:
                break
            ok, _, profit, attempts = probe(candidate)
            attempted_count += attempts
            if ok:
                selected_amount = candidate
                selected_profit = profit
                base_amount = candidate
                lower_amount = candidate // 2
                has_upper_amount = True
                break
            upper_amount = candidate
            candidate //= 2

    if has_upper_amount and selected_amount > 0:
        for _ in range(max_steps):
            if upper_amount <= base_amount + 1 or lower_amount >= base_amount:
                break
            if upper_amount <= lower_amount:
                break
            left_mid = lower_amount + (base_amount - lower_amount) // 2
            right_mid = base_amount + (upper_amount - base_amount) // 2
            if (
                left_mid <= lower_amount
                or left_mid >= base_amount
                or right_mid <= base_amount
                or right_mid >= upper_amount
            ):
                break
            left_ok, _, left_profit, left_attempts = probe(left_mid)
            right_ok, _, right_profit, right_attempts = probe(right_mid)
            attempted_count += left_attempts + right_attempts
            if not left_ok and not right_ok:
                break
            left_improves = left_ok and left_profit > selected_profit
            right_improves = right_ok and right_profit > selected_profit
            if not left_improves and not right_improves:
                break
            if left_ok and right_ok:
                if left_profit >= right_profit:
                    if left_improves:
                        selected_amount = left_mid
                        selected_profit = left_profit
                elif right_improves:
                    selected_amount = right_mid
                    selected_profit = right_profit
            elif left_ok and left_improves:
                selected_amount = left_mid
                selected_profit = left_profit
            elif right_ok and right_improves:
                selected_amount = right_mid
                selected_profit = right_profit

            old_base = base_amount
            if left_ok and right_ok:
                if left_profit >= right_profit:
                    upper_amount = old_base
                    base_amount = left_mid
                else:
                    lower_amount = old_base
                    base_amount = right_mid
            elif left_ok:
                upper_amount = old_base
                base_amount = left_mid
            else:
                lower_amount = old_base
                base_amount = right_mid

    return selected_amount, selected_profit, attempted_count


def _official_v4_preview_python(
    *,
    route_symbols: list[str],
    route_addresses: list[str],
    amount: float,
    route_pool_keys: list[dict[str, Any]] | None = None,
    route_pool_key_groups: list[list[dict[str, Any]]] | None = None,
    chain_id: int,
) -> tuple[dict[str, Any], str] | None:
    """Run the official V4 static preview from Python instead of Hardhat.

    Hardhat can fail with ``NoProfitableRoute`` even when the same static call
    succeeds through Web3, so non-broadcast review prefers this direct path.
    """
    if not v4_live_quote_enabled(chain_id=int(chain_id)):
        return None
    if len(route_addresses) != len(route_symbols):
        return None
    if route_pool_key_groups is None:
        route_pool_key_groups = [route_pool_keys] if route_pool_keys else []
    if not route_pool_key_groups:
        return None
    borrow_decimals = _borrow_token_decimals_from_registry(
        chain_id=int(chain_id),
        borrow_address=route_addresses[0],
    )
    if borrow_decimals is None:
        return (
            {
                "ok": False,
                "error": "borrow_token_decimals_missing",
                "detail": (
                    "SQLite chain_tokens 缺少借币代币 decimals；未采用 18 位假设，"
                    "该闭环无法形成可验证的链上金额换算，复核关闭"
                ),
                "quoteStatus": "missing_token_metadata",
                "evidenceStatus": "missing_token_metadata",
                "executionTrust": "missing_token_metadata",
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "amount": amount,
                "initialAmount": amount,
                "staticCall": "skipped",
                "method": "previewBestPool",
            },
            "borrow token decimals missing from SQLite chain_tokens; fail closed without 18-decimal assumption",
        )
    try:
        rpc_url, block_number = _probe_robinhood_family_rpc(chain_id)
    except Exception as exc:
        return None
    from dex.runtime.dex_onchain_quote_evidence_official_v4 import _official_v4_arb_address

    arb_address = _official_v4_arb_address(chain_id=chain_id)
    if not arb_address:
        return None
    try:
        (
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
        ) = _official_v4_hop_candidate_sets(
            route_pool_key_groups,
            route_addresses,
        )
    except ValueError:
        return None

    amount_raw = int(Decimal(str(amount)) * (Decimal(10) ** int(borrow_decimals)))
    search_mode = str(os.getenv("DEX_OFFICIAL_V4_SEARCH_MODE", "") or "").strip().lower()
    inner_single_call = search_mode in {
        "inner",
        "inner_single_call",
        "single_call",
        "contract_internal",
    }
    selected_amount_method = (
        "onchain_preview_best_amount_inner_single_call"
        if inner_single_call
        else "onchain_preview_adaptive_best_amount_single_route"
    )
    try:
        w3 = Web3(
            Web3.HTTPProvider(
                rpc_url,
                request_kwargs={
                    "timeout": 60,
                    "verify": False,
                    "headers": {
                        "Content-Type": "application/json",
                        "User-Agent": "flashloan-robinhood-testnet-route-preview/1.0",
                        "Connection": "close",
                    },
                },
            )
        )
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(arb_address),
            abi=(
                _OFFICIAL_V4_POOL_PREVIEW_ABI
                + _OFFICIAL_V4_PREVIEW_ABI
                + _OFFICIAL_V4_RUN_ABI
            ),
        )
        if not _official_v4_contract_has_selector(
            w3,
            arb_address,
            _OFFICIAL_V4_PREVIEW_BEST_POOL_SELECTOR,
        ):
            return (
                {
                    "ok": False,
                    "error": "official_v4_arb_contract_outdated",
                    "detail": (
                        "链上 OfficialV4FlashArb 缺少 previewBestPool，"
                        "请先部署与本地工件一致的官方 arb 合约"
                    ),
                    "errorDetail": "official_v4_arb_contract_outdated",
                    "method": "previewBestPool",
                    "arbAddress": arb_address,
                    "routeSymbols": route_symbols,
                    "amount": amount,
                    "initialAmount": amount,
                    "staticCall": "skipped",
                    "quoteStatus": "contract_outdated",
                    "evidenceStatus": "contract_outdated",
                    "executionTrust": "contract_outdated",
                },
                "official V4 on-chain arb contract is outdated; deploy latest artifact first",
            )
        pool_result = contract.functions.previewBestPool(
            Web3.to_checksum_address(route_addresses[0]),
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
        ).call(block_identifier=block_number, transaction={"gas": 50_000_000})
        entry_index, bridge_index, exit_index, net_product = (
            int(pool_result[0]),
            int(pool_result[1]),
            int(pool_result[2]),
            int(pool_result[3]),
        )
        if net_product <= 10**18:
            raise RuntimeError("NoProfitableRoute")
        selected_keys = [
            entry_keys[entry_index],
            bridge_keys[bridge_index],
            exit_keys[exit_index],
        ]
        selected_directions = [
            entry_directions[entry_index],
            bridge_directions[bridge_index],
            exit_directions[exit_index],
        ]
        # The best-amount preview receives exactly one pool group: the route
        # selected above from current on-chain prices.
        keysets, directionsets = _single_direction_contract_sets(selected_keys, selected_directions)
        selected_route_index = 0
        if inner_single_call:
            (
                selected_amount_raw,
                selected_route_index,
                selected_profit_raw,
                attempted_count,
            ) = _official_v4_preview_best_amount_single_call(
                contract,
                token=route_addresses[0],
                keysets=keysets,
                directionsets=directionsets,
                amount_raw=amount_raw,
                min_profit=1,
                block_number=block_number,
            )
            if (
                selected_amount_raw <= 0
                or selected_profit_raw <= 0
                or int(selected_route_index) >= len(keysets)
            ):
                raise RuntimeError("NoProfitableRoute")
            selected_keys = keysets[selected_route_index]
            selected_directions = directionsets[selected_route_index]
        else:
            # Every probe is one eth_call. The JS script keeps MAX_STEPS=24,
            # but the testnet RPC is slow enough that the full ladder can
            # exceed the web request budget. Keep the default aligned with
            # the script and let operators lower the budget for the
            # interactive page.
            try:
                max_probe_steps = max(1, int(os.getenv("DEX_OFFICIAL_V4_SEARCH_STEPS", "24") or 24))
            except (TypeError, ValueError):
                max_probe_steps = 24
            (
                selected_amount_raw,
                selected_profit_raw,
                attempted_count,
            ) = _official_v4_adaptive_best_amount_single_route(
                contract,
                token=route_addresses[0],
                keysets=keysets,
                directionsets=directionsets,
                initial_amount_raw=amount_raw,
                min_profit=1,
                block_number=block_number,
                max_steps=max_probe_steps,
            )
        if selected_amount_raw <= 0 or selected_profit_raw <= 0:
            raise RuntimeError("NoProfitableRoute")
        run_result = contract.functions.runArbitrage(
            Web3.to_checksum_address(route_addresses[0]),
            selected_keys,
            selected_directions,
            selected_amount_raw,
            1,
        ).call(block_identifier=block_number, transaction={"gas": 50_000_000})
        out_tb, out_ta, out_tz, profit_raw = (int(run_result[0]), int(run_result[1]), int(run_result[2]), int(run_result[3]))
    except Exception as exc:
        data = str(getattr(exc, "data", "") or "")
        if "0x4f422847" in data or "NoProfitableRoute" in str(exc):
            return (
                {
                    "ok": False,
                    "error": "static_arb_call_reverted",
                    "detail": "链上复核未通过：NoProfitableRoute()",
                    "errorDetail": "NoProfitableRoute()",
                    "errorData": "0x4f422847",
                    "errorSelector": "0x4f422847",
                    "routeSymbols": route_symbols,
                    "amount": amount,
                    "initialAmount": amount,
                    "staticCall": "reverted",
                "method": "previewBestPool",
                "bestAmountMethod": selected_amount_method,
                    "quoteStatus": "route_unprofitable",
                    "evidenceStatus": "route_unprofitable",
                    "executionTrust": "route_unprofitable",
                },
                "",
            )
        detail = str(exc)[:300]
        return (
            {
                "ok": False,
                "error": "static_arb_call_reverted",
                "detail": detail,
                "errorDetail": detail,
                "errorData": str(getattr(exc, "data", "") or "")[:300],
                "routeSymbols": route_symbols,
                "amount": amount,
                "initialAmount": amount,
                "staticCall": "reverted",
                "method": "previewBestPool",
                "bestAmountMethod": selected_amount_method,
                "quoteStatus": "route_unprofitable",
                "evidenceStatus": "route_unprofitable",
                "executionTrust": "route_unprofitable",
            },
            "",
        )
    return (
        {
            "ok": True,
            "routeSymbols": route_symbols,
            "selectedRouteSymbols": route_symbols,
            "selectedPool": {
                "entryIndex": entry_index,
                "bridgeIndex": bridge_index,
                "exitIndex": exit_index,
                "netProduct": str(net_product),
            },
            "amount": amount,
            "initialAmount": amount,
            "bestAmount": str(float(selected_amount_raw) / (10 ** int(borrow_decimals))),
            "bestAmountRaw": str(selected_amount_raw),
            "borrowTokenDecimals": int(borrow_decimals),
            "profit": str(profit_raw),
            "profitHuman": str(float(profit_raw) / (10 ** int(borrow_decimals))),
            "outTb": str(out_tb),
            "outTa": str(out_ta),
            "outTz": str(out_tz),
            "outAmount": str(float(out_tz) / (10 ** int(borrow_decimals))),
            "outAmountHuman": str(float(out_tz) / (10 ** int(borrow_decimals))),
            "selectedAmount": str(selected_amount_raw),
            "selectedAmountMethod": selected_amount_method,
            "selectedAmountAttemptedCount": str(attempted_count),
            "selectedRouteIndex": selected_route_index,
            "staticCall": "ok",
            "bestAmountMethod": selected_amount_method,
            "bestAmountAttemptedCount": str(attempted_count),
        },
        (
            "official V4 on-chain price-pool selection + contract-internal best amount"
            if inner_single_call
            else "official V4 on-chain price-pool selection + single-route best amount"
        ),
    )


def _env_flag(*names: str) -> bool:
    for name in names:
        if str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _official_v4_signer_for_chain(chain_id: int) -> tuple[str | None, str | None]:
    prefix = _robinhood_env_prefix(int(chain_id))
    names = [
        f"{prefix}_DEX_PRIVATE_KEY",
        f"{prefix}_EXECUTION_PRIVATE_KEY",
        "DEX_EXECUTION_PRIVATE_KEY",
        "OFFICIAL_V4_PRIVATE_KEY",
        "DEX_OFFICIAL_V4_PRIVATE_KEY",
    ]
    if int(chain_id) == ROBINHOOD_TESTNET_CHAIN_ID:
        names.extend(
            [
                "ROBINHOOD_TESTNET_DEX_PRIVATE_KEY",
                "ROBINHOOD_TESTNET_EXECUTION_PRIVATE_KEY",
            ]
        )
    if int(chain_id) == ROBINHOOD_CHAIN_ID:
        names.extend(
            [
                "ROBINHOOD_MAINNET_DEX_PRIVATE_KEY",
                "ROBINHOOD_MAINNET_EXECUTION_PRIVATE_KEY",
            ]
        )
    # Keep the liquidation signer as a compatibility fallback; the operator
    # has instructed that mainnet uses the same key as the testnet suite.
    names.append("LIQUIDATION_EXECUTION_PRIVATE_KEY")
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value, name
    return None, None


def _parse_official_v4_arb_result_json(output: str) -> dict[str, Any] | None:
    """Parse the complete official V4 result object from the script output.

    The live broadcast payload contains nested pool selections, so a naive
    non-greedy brace regex stops at the first nested closing brace and yields
    an incomplete JSON object. ``raw_decode`` consumes exactly one complete
    JSON value, which keeps earlier failure/preview objects and a later
    successful broadcast object all readable in turn.
    """
    if not isinstance(output, str):
        return None
    decoder = json.JSONDecoder()
    parsed: dict[str, Any] | None = None
    for marker in re.finditer(r"OFFICIAL_V4_ARB_RESULT_JSON", output):
        tail = output[marker.end():].lstrip(" \t\r\n")
        try:
            value, _end = decoder.raw_decode(tail)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed = value
    return parsed


def _official_v4_broadcast_python(
    *,
    route_symbols: list[str],
    route_addresses: list[str],
    amount: float,
    route_pool_keys: list[dict[str, Any]] | None = None,
    route_pool_key_groups: list[list[dict[str, Any]]] | None = None,
    chain_id: int,
) -> dict[str, Any] | None:
    """Broadcast through Python when the Hardhat JS live script is unavailable.

    This path keeps mainnet usable after the operator configures an official
    V4 arb/composer address plus a chain-scoped signer. The static preview still
    runs first and no transaction is sent unless it reports a positive profit.
    The broadcast payload is not a single fixed route: it carries entry, bridge,
    and exit candidate pools and lets the contract reselect by current price.
    """
    preview = _official_v4_preview_python(
        route_symbols=route_symbols,
        route_addresses=route_addresses,
        amount=amount,
        route_pool_keys=route_pool_keys,
        route_pool_key_groups=route_pool_key_groups,
        chain_id=int(chain_id),
    )
    if preview is None:
        return None
    parsed, appended = preview
    if parsed.get("ok") is not True:
        parsed["broadcast"] = False
        parsed["broadcastAttempted"] = False
        return parsed
    try:
        profit_human = float(parsed.get("profitHuman") or 0)
    except (TypeError, ValueError):
        profit_human = 0.0
    if profit_human <= 0:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "not_profitable",
            "detail": "on-chain preview did not return positive profit; broadcast skipped",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": False,
            "quoteStatus": "route_unprofitable",
            "evidenceStatus": "route_unprofitable",
            "executionTrust": "route_unprofitable",
            "staticCall": "ok",
            "profitHuman": profit_human,
            "profitable": False,
        }

    prefix = _robinhood_env_prefix(int(chain_id))
    if not _env_flag(
        f"{prefix}_ALLOW_OFFICIAL_V4_ARB_BROADCAST",
        "OFFICIAL_V4_ALLOW_ARB_BROADCAST",
    ):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_disabled",
            "detail": "Python broadcast gate is not enabled; set the chain broadcast switch before sending a transaction",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": False,
            "broadcastDisabled": True,
            "quoteStatus": "live_verified",
            "evidenceStatus": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "staticCall": "ok",
            "bestAmount": parsed.get("bestAmount"),
            "bestAmountConfirmed": True,
            "profitHuman": profit_human,
            "profitable": True,
        }

    private_key, key_name = _official_v4_signer_for_chain(int(chain_id))
    if not private_key:
        return None
    from dex.runtime.dex_onchain_quote_evidence_official_v4 import _official_v4_arb_address

    arb_address = _official_v4_arb_address(chain_id=int(chain_id))
    if not arb_address:
        return None
    try:
        rpc_url, _block_number = _probe_robinhood_family_rpc(int(chain_id))
    except Exception as exc:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_rpc_unavailable",
            "detail": f"Robinhood chain {chain_id} RPC preflight failed: {str(exc)[:300]}",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": False,
        }

    route_key_groups = route_pool_key_groups if route_pool_key_groups is not None else (
        [route_pool_keys] if route_pool_keys else []
    )
    if not route_key_groups:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_pool_identity_missing",
            "detail": "Python broadcast skipped because no complete per-hop pool candidate groups were provided",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": False,
            "broadcastFailed": False,
        }
    try:
        (
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
        ) = _official_v4_hop_candidate_sets(
            route_key_groups,
            route_addresses,
        )
    except Exception as exc:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_candidate_sets_invalid",
            "detail": f"Python broadcast hop candidate sets invalid: {str(exc)[:300]}",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": False,
            "broadcastFailed": False,
        }
    selected_amount_raw = int(parsed.get("selectedAmount") or parsed.get("bestAmountRaw") or 0)
    if selected_amount_raw <= 0:
        borrow_decimals = _borrow_token_decimals_from_registry(
            chain_id=int(chain_id),
            borrow_address=route_addresses[0],
        )
        if borrow_decimals is None:
            return {
                "ok": False,
                "mode": "dex_route_broadcast_test",
                "error": "official_arb_broadcast_borrow_token_decimals_missing",
                "detail": (
                    "SQLite chain_tokens 缺少借币代币 decimals；未采用 18 位假设，"
                    "广播金额无法从 human 值换算为 raw 值"
                ),
                "routeSymbols": parsed.get("routeSymbols") or route_symbols,
                "routeAddresses": route_addresses,
                "broadcast": True,
                "broadcastAttempted": False,
                "quoteStatus": "missing_token_metadata",
                "evidenceStatus": "missing_token_metadata",
                "executionTrust": "missing_token_metadata",
            }
        selected_amount_raw = int(
            Decimal(str(parsed.get("bestAmount") or amount)) * (Decimal(10) ** int(borrow_decimals))
        )

    try:
        w3 = Web3(
            Web3.HTTPProvider(
                rpc_url,
                request_kwargs={
                    "timeout": 60,
                    "verify": False,
                    "headers": {
                        "Content-Type": "application/json",
                        "User-Agent": "flashloan-robinhood-official-v4-broadcast/1.0",
                        "Connection": "close",
                    },
                },
            )
        )
        account = w3.eth.account.from_key(private_key)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(arb_address),
            abi=_OFFICIAL_V4_POOL_RUN_ABI,
        )
        tx_fn = contract.functions.runArbitrageBestPoolsWithSelectedAmount(
            Web3.to_checksum_address(route_addresses[0]),
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
            selected_amount_raw,
            1,
        )
        gas_estimate = int(tx_fn.estimate_gas({"from": account.address}))
        nonce = int(w3.eth.get_transaction_count(account.address))
        latest_block = w3.eth.get_block("latest")
        base_fee = int((latest_block or {}).get("baseFeePerGas") or w3.eth.gas_price)
        gas_price = int(w3.eth.gas_price)
        try:
            priority_fee = int(w3.eth.max_priority_fee)
        except Exception:
            priority_fee = 0
        priority_fee = max(priority_fee, 0)
        # Robinhood's RPC can reject a legacy gasPrice that is only a few wei
        # below the latest base fee. Keep EIP-1559 fields unambiguous and add a
        # buffer so a stale block/base-fee snapshot still clears the limit.
        max_fee = max(gas_price, base_fee) * 3 // 2 + priority_fee + 1_000_000
        tx = tx_fn.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "gas": int(gas_estimate * 1.15) + 50_000,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority_fee,
                "chainId": int(w3.eth.chain_id),
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
    except Exception as exc:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_failed",
            "detail": f"Python broadcast failed: {str(exc)[:500]}",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": True,
            "broadcastAttempted": True,
            "broadcastFailed": True,
        }

    return {
        "ok": True,
        "mode": "dex_route_broadcast_test",
        "actionStage": "onchain_execution",
        "routeSymbols": parsed.get("routeSymbols") or route_symbols,
        "routeAddresses": route_addresses,
        "amount": amount,
        "requestedAmount": amount,
        "initialProbeAmount": amount,
        "bestAmount": parsed.get("bestAmount"),
        "bestAmountConfirmed": True,
        "bestAmountMissing": False,
        "bestAmountMethod": parsed.get("bestAmountMethod") or "onchain_preview_adaptive_best_amount_single_route",
        "bestAmountAttemptedCount": parsed.get("bestAmountAttemptedCount"),
        "broadcast": True,
        "broadcastAttempted": True,
        "broadcastDisabled": False,
        "broadcastChannel": "public_rpc_python",
        "broadcastMethod": "runArbitrageBestPoolsWithSelectedAmount",
        "method": "runArbitrageBestPoolsWithSelectedAmount",
        "selectedPool": parsed.get("selectedPool"),
        "broadcastCandidatePoolCounts": {
            "entry": len(entry_keys),
            "bridge": len(bridge_keys),
            "exit": len(exit_keys),
        },
        "staticCall": "ok",
        "staticCallPassed": True,
        "quoteStatus": "live_verified",
        "evidenceStatus": "live_verified",
        "executionTrust": "live_quoter_verified_readonly",
        "profitable": True,
        "profit": parsed.get("profitHuman"),
        "profitHuman": parsed.get("profitHuman"),
        "profitRaw": parsed.get("profit"),
        "outAmount": parsed.get("outAmountHuman"),
        "outAmountHuman": parsed.get("outAmountHuman"),
        "outAmountRaw": parsed.get("outAmount"),
        "txHash": tx_hash,
        "detail": "broadcast_complete",
        "evidence": {
            "status": "live_verified",
            "executionTrust": "live_quoter_verified_readonly",
            "routeSymbols": parsed.get("routeSymbols") or route_symbols,
            "routeAddresses": route_addresses,
            "blockers": [],
            "bestAmountConfirmed": True,
            "bestAmountMissing": False,
            "profitable": True,
            "readyForTransactionPreflight": True,
            "costModel": {
                "borrowInputSymbol": route_symbols[0],
                "finalOutputSymbol": route_symbols[0],
                "borrowInputAmount": parsed.get("bestAmount"),
                "selectedBorrowInputAmount": parsed.get("bestAmount"),
                "requestedBorrowInputAmount": amount,
                "bestAmountConfirmed": True,
                "bestAmountMissing": False,
                "netProfitAmount": parsed.get("profitHuman"),
                "netProfitBps": parsed.get("referenceNetReturnBps"),
                "profitable": True,
                "readyForTransactionPreflight": True,
            },
        },
        "stdoutTail": appended,
        "stderrTail": "",
    }


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
    chain_id: int | None = None,
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
        user_requested_amount = requested_amount if requested_amount is not None else amount
        initial_amount = user_requested_amount
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
            chain_id=chain_id or ROBINHOOD_TESTNET_CHAIN_ID,
            route_symbols=route_symbols,
            amount=final_amount,
        )
        best_calculation.update(
            {
                "method": method_override or "route_execution_best_amount",
                "requestedAmount": user_requested_amount,
                "initialProbeAmount": initial_amount,
                "initialProbeSymbol": (route_symbols[0] if route_symbols else None),
                "bestAmount": final_amount,
                "bestAmountSymbol": (route_symbols[0] if route_symbols else None),
                "bestAmountConfirmed": confirmed,
                "bestAmountMissing": not confirmed,
                "bestAmountSelectionBasis": confirmation_basis,
                "bestAmountConfirmationReason": confirmation_reason,
                "selectionBasis": "borrow_token",
                "attemptedCount": attempted_count_int,
            }
        )
        probe_records = best_calculation.get("probe_records") or best_calculation.get("probeRecords") or []
        offchain_probe_reason = (
            payload.get("evidence", {}).get("offchainInitialProbeReason")
            if isinstance(payload.get("evidence"), dict)
            else None
        ) or best_calculation.get("offchainInitialProbeReason") or best_calculation.get("offchain_initial_probe_reason")
        if offchain_probe_reason is None and user_requested_amount is not None:
            offchain_probe_reason = "requested_borrow_amount_fallback"
        if isinstance(payload.get("evidence"), dict):
            payload["evidence"]["offchainInitialProbeReason"] = offchain_probe_reason
        payload["bestAmountCalculation"] = best_calculation
        payload["probeRecords"] = probe_records
        payload["probe_records"] = probe_records
        payload["bestAmount"] = final_amount
        payload["bestAmountConfirmed"] = confirmed
        payload["bestAmountMissing"] = not confirmed
        payload["bestAmountSelectionBasis"] = confirmation_basis
        payload["bestAmountConfirmationReason"] = confirmation_reason
        payload["requestedAmount"] = user_requested_amount
        payload["initialProbeAmount"] = best_calculation.get("initialProbeAmount") or initial_amount
        payload["initialProbeSymbol"] = (
            best_calculation.get("initialProbeSymbol")
            or (route_symbols[0] if route_symbols else None)
        )
        payload["offchainInitialProbeReason"] = offchain_probe_reason
        payload["analysis"] = {
            **payload.get("analysis", {}),
            "bestAmount": final_amount,
            "bestAmountSymbol": best_calculation.get("bestAmountSymbol")
            or (route_symbols[0] if route_symbols else None),
            "bestAmountConfirmed": confirmed,
            "bestAmountMissing": not confirmed,
            "bestAmountSelectionBasis": confirmation_basis,
            "bestAmountConfirmationReason": confirmation_reason,
            "referenceInputSymbol": best_calculation.get("bestAmountSymbol")
            or (route_symbols[0] if route_symbols else None),
            "referenceNetReturnBps": payload.get("referenceNetReturnBps"),
            "referenceFinalOutputAmount": (
                payload.get("outAmountHuman")
                or payload.get("outAmount")
                or payload.get("final_amount")
                or payload.get("outAmountRaw")
            ),
            "referenceFinalOutputSymbol": payload.get("final_symbol")
            or payload.get("amountSymbol")
            or (route_symbols[0] if route_symbols else None),
            "referenceNetProfitAmount": (
                payload.get("profitHuman")
                or payload.get("profit")
                or payload.get("profit_amount")
                or payload.get("final_delta_amount")
            ),
            "referenceNetReturnUsd": payload.get("profitUsd"),
            "offchainInitialProbeReason": offchain_probe_reason,
            "initialProbeAmount": payload["initialProbeAmount"],
            "initialProbeSymbol": payload["initialProbeSymbol"],
            "requestedAmount": user_requested_amount,
            "probeRecords": probe_records,
            "probe_records": probe_records,
            "bestAmountCalculation": best_calculation,
        }
    except Exception as exc:
        detail = str(exc)[:300]
        payload["bestAmountCalculationError"] = detail
        existing_stack = payload.get("errorStack")
        if not isinstance(existing_stack, list):
            existing_stack = []
        payload["errorStack"] = existing_stack + _structured_route_error(
            detail,
            error_code=ErrorCode.DEX_INTERNAL,
            flow="attach_best_amount_calculation",
            node_id="payload",
        )
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
            "errorStack": _structured_route_error(
                "official_arb_broadcast_route_unsupported",
                error_code=ErrorCode.DEX_ROUTE_INVALID,
                flow="dex_route_broadcast_test",
                node_id="validate_route",
            ),
        }
    if not _chain_supports_official_v4_arb(int(chain_id or 0)):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "official V4 triangle arbitrage is only supported on Robinhood chain 4663 or Robinhood Testnet 46630 when explicitly requested",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
            "errorStack": _structured_route_error(
                "official_arb_broadcast_route_unsupported",
                error_code=ErrorCode.DEX_ROUTE_INVALID,
                flow="dex_route_broadcast_test",
                node_id="validate_chain",
            ),
        }
    route_addresses = [
        str(address or "").strip()
        for address in (route_addresses or [])
        if str(address or "").strip()
    ]
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols, chain_id=int(chain_id or 0))
        if not route_addresses:
            route_addresses = _resolve_route_addresses_from_market_payload(
                route_symbols=route_symbols,
                chain_id=int(chain_id),
                protocols=["uniswap_v4"],
            )
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
            "errorStack": _structured_route_error(
                "official_arb_broadcast_route_unsupported",
                error_code=ErrorCode.DEX_ROUTE_INVALID,
                flow="dex_route_broadcast_test",
                node_id="validate_addresses",
            ),
        }
    official_script = _official_arb_script_for_chain(int(chain_id))
    official_dex_dir = _official_dex_dir_for_chain(int(chain_id))
    hardhat_network = _hardhat_network_for_chain(int(chain_id))
    env_prefix = _robinhood_env_prefix(int(chain_id))

    route_pool_key_groups = _official_v4_route_pool_key_groups(
        route_symbols=route_symbols,
        chain_id=int(chain_id or ROBINHOOD_TESTNET_CHAIN_ID),
        protocols=["uniswap_v4"],
        route_addresses=route_addresses or None,
    )
    if not route_pool_key_groups:
        route_pool_keys_fallback = _official_v4_route_pool_keys(
            route_symbols=route_symbols,
            chain_id=int(chain_id or ROBINHOOD_TESTNET_CHAIN_ID),
            protocols=["uniswap_v4"],
            route_addresses=route_addresses or None,
        )
        route_pool_key_groups = [route_pool_keys_fallback] if route_pool_keys_fallback else []
    route_pool_keys = route_pool_key_groups[0] if route_pool_key_groups else []
    if not route_pool_key_groups or any(len(group) != 3 for group in route_pool_key_groups):
        route_text = " -> ".join(str(item) for item in route_symbols) if isinstance(route_symbols, list) else str(route_symbols or "-")
        payload = {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_pool_identity_missing",
            "detail": f"链上复核失败：缺少中间桥池 PoolKey；当前市场观察没有返回 {route_text} 的路由池身份，已停止链上复核。请先补全市场观察中的中间桥池身份后重试；不会用全局默认 hook 猜测。",
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": broadcast,
            "quoteStatus": "missing_route_pool_identity",
            "evidenceStatus": "missing_route_pool_identity",
            "executionTrust": "missing_route_pool_identity",
            "errorStack": _structured_route_error(
                "missing route pool identity",
                error_code=ErrorCode.DEX_ROUTE_INVALID,
                flow="dex_route_broadcast_test",
                node_id="resolve_route_pool_keys",
            ),
        }
        _attach_best_amount_calculation(
            payload,
            route_symbols=route_symbols,
            amount=amount,
            chain_id=int(chain_id or 0),
        )
        return payload

    if not broadcast and not v4_live_quote_enabled(chain_id=int(chain_id)):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_v4_readonly_preview_rpc_unavailable",
            "detail": (
                "official V4 read-only preview is unavailable: RPC link is "
                "dynamically offline"
            ),
            "quoteStatus": "rpc_unavailable",
            "evidenceStatus": "rpc_unavailable",
            "executionTrust": "rpc_unavailable",
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": broadcast,
            "errorStack": _structured_route_error(
                "official_v4_readonly_preview_rpc_unavailable",
                error_code=ErrorCode.DEX_RPC_FAILED,
                flow="dex_route_broadcast_test",
                node_id="python_preview",
            ),
        }

    script_available = bool(official_script.exists() and official_dex_dir.exists())
    if broadcast and not script_available:
        python_broadcast = _official_v4_broadcast_python(
            route_symbols=route_symbols,
            route_addresses=route_addresses,
            amount=amount,
            route_pool_keys=route_pool_keys,
            route_pool_key_groups=route_pool_key_groups,
            chain_id=int(chain_id or 0),
        )
        if python_broadcast is not None:
            _attach_best_amount_calculation(
                python_broadcast,
                route_symbols=python_broadcast.get("routeSymbols") or route_symbols,
                amount=python_broadcast.get("bestAmount") or amount,
                chain_id=int(chain_id or 0),
                requested_amount=amount,
                selected_amount=python_broadcast.get("bestAmount") or amount,
                method_override=python_broadcast.get("bestAmountMethod"),
                attempted_count=python_broadcast.get("bestAmountAttemptedCount"),
                best_amount_confirmed=python_broadcast.get("bestAmountConfirmed"),
            )
            return python_broadcast
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_config_missing",
            "detail": (
                f"missing script or DEX workdir: {official_script}; "
                "configure officialV4 arb/composer address plus a chain-scoped "
                "signer private key, or provide the chain's v4-official-arb-live.js"
            ),
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "broadcast": broadcast,
            "broadcastAttempted": False,
            "errorStack": _structured_route_error(
                "official_arb_broadcast_config_missing",
                error_code=ErrorCode.DEX_INTERNAL,
                flow="dex_route_broadcast_test",
                node_id="script_resolve",
            ),
        }

    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = hardhat_network
    env[f"{env_prefix}_ALLOW_V4_AMM_LIVE_TEST"] = "true"
    env[f"{env_prefix}_ALLOW_OFFICIAL_V4_ARB_BROADCAST"] = "true" if broadcast else "false"
    chain_network = _robinhood_network_name(int(chain_id or 0))
    manifest_network = "mainnet" if chain_network == "robinhood" else "testnet"
    env["FLASHLOAN_MANIFEST_MIRROR_PATH"] = str(
        ROOT
        / "contract"
        / chain_network
        / "DEX"
        / "config"
        / f"{manifest_network}-v4-amm"
        / "manifest.json"
    )
    borrow_decimals = _borrow_token_decimals_from_registry(
        chain_id=int(chain_id or 0),
        borrow_address=route_addresses[0] if route_addresses else "",
    )
    if borrow_decimals is not None:
        env[f"{env_prefix}_OFFICIAL_ARB_BORROW_DECIMALS"] = str(borrow_decimals)
    # The official testnet endpoint currently serves a Let's Encrypt cert that
    # only pins an IP address, not rpc.testnet.chain.robinhood.com. Disabling
    # TLS hostname verification for this child Hardhat process keeps local
    # testnet route review working while the upstream cert issue exists.
    env.setdefault("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    node_options = str(env.get("NODE_OPTIONS") or "").strip()
    if "dns-result-order" not in node_options:
        env["NODE_OPTIONS"] = (node_options + " --dns-result-order=ipv4first").strip()
    # Prefer the official Robinhood testnet RPC for the live Hardhat script.
    # Some configured public endpoints are reachable over Python but time out
    # inside Node/undici, which makes the broadcast script fail before static
    # call or broadcast can run.
    try:
        probed_rpc_url, probed_block = _probe_robinhood_family_rpc(int(chain_id))
        env[f"{env_prefix}_RPC_URL"] = probed_rpc_url
        env[f"{env_prefix}_RPC"] = probed_rpc_url
        env["ROBINHOOD_RPC_URL"] = probed_rpc_url
        env["ROBINHOOD_RPC"] = probed_rpc_url
    except Exception as exc:
        rpc_detail = f"Robinhood chain {chain_id} RPC preflight failed: {str(exc)[:300]}"
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
            "errorStack": _structured_route_error(
                rpc_detail,
                error_code=(
                    ErrorCode.DEX_RPC_TIMEOUT
                    if rpc_status == "rpc_verification_timeout"
                    else ErrorCode.DEX_RPC_FAILED
                ),
                flow="dex_route_broadcast_test",
                node_id="rpc_preflight",
            ),
        }
        _attach_best_amount_calculation(
            payload,
            route_symbols=route_symbols,
            amount=amount,
            chain_id=int(chain_id or 0),
        )
        return payload

    env[f"{env_prefix}_OFFICIAL_ARB_ROUTE"] = " -> ".join(route_symbols)
    env[f"{env_prefix}_OFFICIAL_ARB_ADDRESSES"] = ",".join(route_addresses)
    env[f"{env_prefix}_OFFICIAL_ARB_POOL_KEYS_JSON"] = json.dumps(
        route_pool_keys,
        separators=(",", ":"),
    )
    env[f"{env_prefix}_OFFICIAL_ARB_POOL_KEY_GROUPS_JSON"] = json.dumps(
        route_pool_key_groups,
        separators=(",", ":"),
    )
    env[f"{env_prefix}_OFFICIAL_ARB_AMOUNT"] = str(amount)
    env[f"{env_prefix}_OFFICIAL_ARB_AMOUNT_IS_BEST"] = "true" if amount_is_best else "false"
    env[f"{env_prefix}_OFFICIAL_ARB_REUSE_AMOUNT"] = "true" if amount_is_best else "false"

    parsed: dict[str, Any] = {}
    appended = ""
    skip_subprocess = False
    if not broadcast:
        python_preview = _official_v4_preview_python(
            route_symbols=route_symbols,
            route_addresses=route_addresses,
            amount=amount,
            route_pool_keys=route_pool_keys,
            route_pool_key_groups=route_pool_key_groups,
            chain_id=int(chain_id),
        )
        if python_preview is not None:
            parsed, appended = python_preview
            result = SimpleNamespace(returncode=0, stdout=appended, stderr="")
            retry_error_detail = []
            skip_subprocess = True
        elif not official_script.exists():
            # Read-only review must not depend on a JS broadcast script. If the
            # Python preview cannot run (e.g. official V4 arb contract is not
            # configured for this chain), return a config error instead of
            # trying to launch a missing Hardhat script.
            return {
                "ok": False,
                "mode": "dex_route_broadcast_test",
                "error": "official_v4_readonly_preview_unavailable",
                "detail": (
                    "official V4 Python preview is not available for this chain; "
                    "check officialV4 arb/composer addresses and route pool keys; "
                    "set OFFICIAL_V4_ARB_ADDRESS / OFFICIAL_V4_COMPOSER_ADDRESS or "
                    "ROBINHOOD_MAINNET_OFFICIAL_V4_ARB_ADDRESS / "
                    "ROBINHOOD_MAINNET_OFFICIAL_V4_COMPOSER_ADDRESS for Robinhood mainnet"
                ),
                "quoteStatus": "missing_configuration",
                "evidenceStatus": "missing_configuration",
                "executionTrust": "missing_configuration",
                "routeSymbols": route_symbols,
                "broadcast": broadcast,
                "errorStack": _structured_route_error(
                    "official_v4_readonly_preview_unavailable",
                    error_code=ErrorCode.DEX_INTERNAL,
                    flow="dex_route_broadcast_test",
                    node_id="python_preview",
                ),
            }

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
        str(official_script),
        "--network",
        hardhat_network,
        "--no-compile",
    ]
    if _robinhood_network_name(int(chain_id)) == "robinhood":
        rpc_candidates = [probed_rpc_url]
    else:
        official_rpc = "https://rpc.testnet.chain.robinhood.com/rpc"
        rpc_candidates = [official_rpc]
        for candidate in (
            probed_rpc_url,
            "https://rpc.testnet.chain.robinhood.com/rpc",
            "https://robinhood-sepolia-rpc.publicnode.com",
        ):
            if candidate.lower() not in {url.lower() for url in rpc_candidates}:
                rpc_candidates.append(candidate)

    if skip_subprocess:
        rpc_candidates.clear()

    result = result if skip_subprocess else None
    appended = appended if skip_subprocess else ""
    parsed = parsed if skip_subprocess else {}
    retry_error_detail = retry_error_detail if skip_subprocess else []
    try:
        script_timeout_seconds = max(10, int(os.getenv("DEX_OFFICIAL_ARB_TIMEOUT_SECONDS", "60")))
    except (TypeError, ValueError):
        script_timeout_seconds = 60
    for rpc_index, rpc_url in enumerate(rpc_candidates):
        env[f"{env_prefix}_RPC_URL"] = rpc_url
        env[f"{env_prefix}_RPC"] = rpc_url
        env["ROBINHOOD_RPC_URL"] = rpc_url
        env["ROBINHOOD_RPC"] = rpc_url
        try:
            result = subprocess.run(
                command,
                cwd=str(official_dex_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=script_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            retry_error_detail.append(f"{rpc_url}: timeout")
            if rpc_index >= len(rpc_candidates) - 1:
                return {
                    "ok": False,
                    "mode": "dex_route_broadcast_test",
                    "error": "official_arb_broadcast_timeout",
                    "detail": "链上脚本超时，建议降低金额或检查 RPC；已尝试 " + ", ".join(retry_error_detail) + "。",
                    "quoteStatus": "rpc_verification_timeout",
                    "evidenceStatus": "rpc_verification_timeout",
                    "executionTrust": "rpc_verification_timeout",
                    "routeSymbols": route_symbols,
                    "broadcast": broadcast,
                    "errorStack": _structured_route_error(
                        "official_arb_broadcast_timeout",
                        error_code=ErrorCode.DEX_RPC_TIMEOUT,
                        flow="dex_route_broadcast_test",
                        node_id="script_timeout",
                    ),
                }
            continue

        appended = result.stdout + "\n" + result.stderr
        parsed = _parse_official_v4_arb_result_json(appended) or {}

        lowered = appended.lower()
        retryable = any(
            marker in lowered
            for marker in (
                "und_err_connect_timeout",
                "und_err_headers_timeout",
                "connecttimeouterror",
                "connect timeout error",
                "headers timeout error",
                "request timeout",
                "socket hang up",
                "econnreset",
                "cert_altname_invalid",
                "tls_cert_altname_invalid",
            )
        )
        if parsed and not retryable:
            break
        if retryable:
            retry_error_detail.append(f"{rpc_url}: rpc_connection_issue")
            if rpc_index >= len(rpc_candidates) - 1:
                break

    if not parsed.get("selectedRouteSymbols"):
        parsed["selectedRouteSymbols"] = parsed.get("routeSymbols") or route_symbols

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
            else "onchain_preview_adaptive_best_amount_single_route"
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
        profit_human = _raw_amount_to_human(
            raw_profit,
            decimals=parsed.get("borrowTokenDecimals"),
        )
    raw_out_amount = parsed.get("outAmount")
    out_amount_human = parsed.get("outAmountHuman")
    if out_amount_human is None:
        out_amount_human = _raw_amount_to_human(
            raw_out_amount,
            decimals=parsed.get("borrowTokenDecimals"),
        )
    static_call_ok = str(parsed.get("staticCall") or "").strip().lower() in {"ok", "passed"}
    profitable = bool(profit_human is not None and float(profit_human) > 0)
    # The official V4 script executes all three swaps in one contract eth_call
    # and does not emit per-hop JSON rows. A successful static call with a
    # selected route and on-chain best amount is therefore the live hop proof.
    has_live_hops = bool(
        static_call_ok
        and best_amount_from_chain
        and parsed.get("selectedRouteSymbols")
    )
    no_profitable_route = "noprofitableroute" in str(parsed.get("errorDetail") or "").lower() and not profitable
    reference_net_return_bps = (
        float(profit_human) / float(selected_amount) * 10_000
        if profit_human is not None and selected_amount is not None and float(selected_amount) > 0
        else None
    )
    if not static_call_ok and parsed.get("staticCall") is None and parsed.get("ok") is True and profitable:
        static_call_ok = True
    ready_for_transaction_preflight = bool(
        static_call_ok
        and profitable
        and has_live_hops
        and best_amount_confirmed
    )
    quote_status = (
        "live_verified"
        if ready_for_transaction_preflight
        else "route_unprofitable"
        if no_profitable_route
        else "static_reference_pending_live_review"
        if static_call_ok and profitable
        else "live_quote_failed"
    )
    execution_trust = (
        "live_quoter_verified_readonly"
        if quote_status == "live_verified"
        else "route_unprofitable"
        if quote_status == "route_unprofitable"
        else "static_reference_pending_live_review"
        if quote_status == "static_reference_pending_live_review"
        else "live_quote_failed"
    )
    diagnostic_best_amount = bool(
        quote_status == "route_unprofitable"
        and selected_amount is not None
        and float(selected_amount) > 0
    )
    best_amount_missing = not best_amount_confirmed and not diagnostic_best_amount

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
        "bestAmountComputed": diagnostic_best_amount,
        "bestAmountMissing": best_amount_missing,
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
        "quoteStatus": quote_status,
        "evidenceStatus": quote_status,
        "executionTrust": execution_trust,
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
            "status": quote_status,
            "executionTrust": execution_trust,
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "hops": [],
            "blockers": (
                []
                if best_amount_confirmed
                else ["on-chain best amount is not confirmed; cannot enter transaction preflight"]
            ),
            "bestAmountConfirmed": best_amount_confirmed,
            "bestAmountComputed": diagnostic_best_amount,
            "bestAmountMissing": best_amount_missing,
            "bestAmountSelectionBasis": best_amount_selection_basis,
            "bestAmountConfirmationReason": best_amount_confirmation_reason,
            "profitable": profitable,
            "readyForTransactionPreflight": ready_for_transaction_preflight,
            "costModel": {
                "borrowInputSymbol": route_symbols[0] if route_symbols else None,
                "finalOutputSymbol": route_symbols[0] if route_symbols else None,
                "borrowInputAmount": selected_amount,
                "selectedBorrowInputAmount": selected_amount,
                "requestedBorrowInputAmount": amount,
                "bestAmountConfirmed": best_amount_confirmed,
                "bestAmountComputed": diagnostic_best_amount,
                "bestAmountMissing": best_amount_missing,
                "bestAmountSelectionBasis": best_amount_selection_basis,
                "bestAmountConfirmationReason": best_amount_confirmation_reason,
                "netProfitAmount": profit_human,
                "netProfitAmountBps": reference_net_return_bps,
                "netProfitBps": reference_net_return_bps,
                "finalOutputAmount": out_amount_human,
                "profitBasis": "borrow_token",
                "profitable": profitable,
                "readyForTransactionPreflight": ready_for_transaction_preflight,
            },
            "offchainInitialProbeReason": (
                "onchain_review_best_amount_reused"
                if best_amount_from_chain and bool(amount_is_best)
                else "onchain_preview_adaptive_best_amount_single_route"
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
        chain_id=int(chain_id or 0),
        requested_amount=amount,
        selected_amount=selected_amount,
        method_override=best_amount_method,
        attempted_count=best_amount_attempted,
        best_amount_confirmed=best_amount_confirmed,
        best_amount_selection_basis=best_amount_selection_basis,
        best_amount_confirmation_reason=best_amount_confirmation_reason,
    )
    if diagnostic_best_amount:
        payload["bestAmountComputed"] = True
        payload["bestAmountMissing"] = False
        if isinstance(payload.get("analysis"), dict):
            payload["analysis"]["bestAmountComputed"] = True
            payload["analysis"]["bestAmountMissing"] = False
        best_amount_calculation = payload.get("bestAmountCalculation")
        if isinstance(best_amount_calculation, dict):
            best_amount_calculation["bestAmountComputed"] = True
            best_amount_calculation["bestAmountMissing"] = False
        if isinstance(payload.get("evidence"), dict):
            evidence = payload["evidence"]
            evidence["bestAmountComputed"] = True
            evidence["bestAmountMissing"] = False
            if isinstance(evidence.get("costModel"), dict):
                evidence["costModel"]["bestAmountComputed"] = True
                evidence["costModel"]["bestAmountMissing"] = False
    # The official V4 contract can revert with NoProfitableRoute without
    # returning a numeric final output. Keep that on-chain conclusion, but
    # attach a negative borrow-token reference P&L from the local observation
    # or V4 pool snapshot so the UI never claims net profit is not positive
    # while showing a blank net-profit value.
    if quote_status == "route_unprofitable" and profit_human is None:
        try:
            fallback_pnl = estimate_fixed_amount_pnl_quick(
                chain_id=int(chain_id),
                route_symbols=route_symbols,
                amount=amount,
                route_addresses=route_addresses,
                protocols=["uniswap_v4"],
                prefer_sqlite_snapshot=True,
            )
        except Exception:
            fallback_pnl = {}
        if not isinstance(fallback_pnl, dict) or fallback_pnl.get("referenceNetProfitAmount") is None:
            try:
                fallback_pnl = _offline_pool_token_pnl_reference(
                    chain_id=int(chain_id),
                    route_symbols=route_symbols,
                    route_addresses=route_addresses,
                    amount=amount,
                    protocols=["uniswap_v4"],
                )
            except Exception:
                fallback_pnl = {}
        fallback_net = fallback_pnl.get("referenceNetProfitAmount")
        fallback_bps = fallback_pnl.get("referenceNetReturnBps")
        fallback_out = fallback_pnl.get("referenceFinalOutputAmount")
        fallback_symbol = route_symbols[0] if route_symbols else None
        if fallback_net is not None:
            payload["referenceNetProfitAmount"] = fallback_net
            payload["referenceNetReturnBps"] = fallback_bps
            payload["referenceFinalOutputAmount"] = fallback_out
            payload["referenceFinalOutputSymbol"] = fallback_symbol
            if isinstance(payload.get("evidence"), dict):
                cost_model = payload["evidence"].get("costModel")
                if isinstance(cost_model, dict):
                    cost_model["netProfitAmount"] = fallback_net
                    cost_model["netProfitAmountBps"] = fallback_bps
                    cost_model["netProfitBps"] = fallback_bps
                    cost_model["finalOutputAmount"] = fallback_out
                    cost_model["finalOutputSymbol"] = fallback_symbol
                    cost_model["finalOutputUsd"] = None
                    cost_model["netProfitUsd"] = None
                    cost_model["grossProfitUsd"] = None
                payload["evidence"]["referenceNetProfitAmount"] = fallback_net
                payload["evidence"]["referenceNetReturnBps"] = fallback_bps
                payload["evidence"]["referenceFinalOutputAmount"] = fallback_out
            if not isinstance(payload.get("analysis"), dict):
                payload["analysis"] = {}
            payload["analysis"]["referenceNetProfitAmount"] = fallback_net
            payload["analysis"]["referenceNetReturnBps"] = fallback_bps
            payload["analysis"]["referenceFinalOutputAmount"] = fallback_out
            payload["analysis"]["referenceFinalOutputSymbol"] = fallback_symbol
            payload["analysis"]["profitBasis"] = "borrow_token"
    if quote_status != "live_verified" or not best_amount_confirmed:
        reason = (
            payload.get("detail")
            or payload.get("error")
            or "on-chain review did not complete"
        )
        error_code = (
            ErrorCode.DEX_RPC_TIMEOUT
            if quote_status == "rpc_verification_timeout"
            else ErrorCode.DEX_RPC_FAILED
            if quote_status == "rpc_verification_failed"
            else ErrorCode.DEX_ROUTE_INVALID
            if quote_status in {"route_unprofitable", "slippage_limit_exceeded"}
            else ErrorCode.DEX_GATE_NOT_READY
            if not best_amount_confirmed
            else ErrorCode.DEX_LIVE_QUOTE_FAILED
        )
        stack = _structured_route_error(
            reason,
            error_code=error_code,
            flow="dex_route_broadcast_test",
            node_id="preflight_result",
        )
        payload["errorStack"] = list(payload.get("errorStack") or []) + stack
        if isinstance(payload.get("evidence"), dict):
            payload["evidence"]["errorStack"] = stack
    return payload


__all__ = [
    "best_amount_calculation_from_route",
    "estimate_route_live_quote",
    "estimate_fixed_amount_pnl",
    "estimate_fixed_amount_pnl_quick",
    "fetch_dex_tx_receipt",
    "is_official_triangle_route",
    "normalize_route_symbols",
    "recommended_route_test_amount",
    "run_official_v4_arb_route_test",
]
