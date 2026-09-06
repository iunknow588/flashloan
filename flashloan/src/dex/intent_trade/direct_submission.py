"""Submission orchestration for unified direct on-chain execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.sensitive_data import redact_sensitive_text
from dex.intent_trade.direct_submission_preflight import prepare_submission_inputs
from dex.intent_trade.direct_submission_context import submission_deps


def submit_direct_onchain_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    deps = submission_deps()
    preflight = prepare_submission_inputs(
        deps,
        quote_payload=quote_payload,
        opportunity=opportunity,
    )
    if preflight.get("blocked") is not None:
        return preflight["blocked"]
    protocol = preflight["protocol"]
    network = preflight["network"]
    expected_chain_id = preflight["expected_chain_id"]
    executor_address = preflight["executor_address"]
    opportunity_context = preflight["opportunity_context"]
    usdc_address = preflight["usdc_address"]
    broadcast_enabled = preflight["broadcast_enabled"]
    explicit_route_groups = preflight["explicit_route_groups"]
    prepared_groups = preflight["prepared_groups"]
    rejected_groups = preflight["rejected_groups"]
    rpc_url = preflight["rpc_url"]

    from web3 import Web3
    from eth_account import Account

    request_timeout = max(1, int(timeout_seconds or 20))
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout}))
        executor = w3.eth.contract(
            address=Web3.to_checksum_address(executor_address),
            abi=deps.load_unified_executor_abi(),
        )
        chain_id = int(w3.eth.chain_id)
        if chain_id != expected_chain_id:
            return {
                "ok": False,
                "submitted": False,
                "status": "network_mismatch",
                "blocked_reason": "network_mismatch",
                "error": f"chain id is {chain_id}, expected {expected_chain_id}",
            }
        current_block = int(w3.eth.block_number)
        max_cache_age_blocks = deps.max_runtime_cache_age_blocks(protocol)
        cache_reports: dict[str, dict[str, Any]] = {}
        pool_cache = deps.load_runtime_pool_cache()
        pool_cache_report = deps.runtime_cache_validation(
            pool_cache,
            network=network,
            current_block=current_block,
            max_age_blocks=max_cache_age_blocks,
            required_factory=deps.unified_required_factory(protocol, pool_cache),
        )
        pool_cache_report["explicitRouteGroups"] = str(bool(explicit_route_groups))
        cache_reports["runtimePoolCache"] = pool_cache_report
        if not pool_cache_report.get("ok"):
            return {
                "ok": False,
                "submitted": False,
                "status": "runtime_cache_unverified",
                "blocked_reason": pool_cache_report.get("reason") or "runtime_cache_unverified",
                "error": pool_cache_report.get("reason") or "runtime cache verification failed",
                "network": network,
                "chain_id": chain_id,
                "executor_address": executor_address,
                "cacheValidation": cache_reports,
                "runtimePoolCacheFile": str(deps.runtime_pool_cache_path()),
            }

        signer_key = deps.env(
            "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "COW_ORDER_SIGNER_PRIVATE_KEY",
            "DEPLOYER_PRIVATE_KEY",
        )
        if not signer_key:
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_private_key_missing",
                "blocked_reason": "signer_private_key_missing",
                "error": "signer private key is required",
            }
        signer = Account.from_key(signer_key)
        signer_address = Web3.to_checksum_address(signer.address)
        onchain_owner = Web3.to_checksum_address(executor.functions.owner().call())
        if signer_address.lower() != onchain_owner.lower():
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_not_owner",
                "blocked_reason": "signer_not_owner",
                "error": "signer does not match unified executor owner",
                "owner": onchain_owner,
                "signer": signer_address,
                "executor_address": executor_address,
            }
        if broadcast_enabled:
            pre_pause = deps.direct_pre_pause_status()
            if pre_pause.get("prePause"):
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "direct_pre_pause_active",
                    "blocked_reason": "direct_pre_pause_active",
                    "error": pre_pause.get("reason") or "local pre-pause blocks new signatures",
                    "network": network,
                    "chain_id": chain_id,
                    "owner": onchain_owner,
                    "signer": signer_address,
                    "executor_address": executor_address,
                    "prePause": pre_pause,
                }

        execute_runtime_trade = deps.direct_execution_enabled(protocol, opportunity_context)
        usdc_params = deps.runtime_execution_params(protocol, opportunity_context)
        token_params = deps.runtime_cross_pool_execution_params(
            protocol,
            opportunity_context,
            usdc_params,
        )
        usdc_params_arg = deps.unified_execution_params(usdc_params)
        token_params_arg = deps.unified_cross_pool_params(token_params)
        allow_non_usdc_cross_pool = deps.non_usdc_cross_pool_enabled(protocol, opportunity_context)
        reserve_cache = deps.load_aave_reserve_cache()
        reserve_cache_report = deps.reserve_cache_validation(
            reserve_cache,
            network=network,
            current_block=current_block,
            max_age_blocks=max_cache_age_blocks,
        )
        cache_reports["aaveReserveCache"] = reserve_cache_report
        if not reserve_cache_report.get("ok"):
            return {
                "ok": False,
                "submitted": False,
                "status": "runtime_cache_unverified",
                "blocked_reason": reserve_cache_report.get("reason") or "reserve_cache_unverified",
                "error": reserve_cache_report.get("reason") or "Aave reserve cache verification failed",
                "network": network,
                "chain_id": chain_id,
                "executor_address": executor_address,
                "cacheValidation": cache_reports,
                "reserveCacheFile": str(deps.aave_reserve_cache_path()),
            }
        borrowable_addresses = deps.aave_borrowable_token_addresses(
            reserve_cache,
            min_liquidity=token_params_arg["amount"],
            network=network,
        )
        gas_estimate_info = None
        gas_price_for_selection = 0
        relay_cost_for_selection = 0
        cache_penalty_for_selection = 0
        public_mempool_penalty_for_selection = 0
        public_mempool_penalty_bps_for_selection = 0
        avax_usdc_price_for_selection = 0
        gas_token_price_report: dict[str, Any] = {}
        if broadcast_enabled:
            gas_estimate_info = deps.estimate_gas_price(
                w3,
                max_gas_price_gwei=(
                    deps.max_broadcast_gas_price_wei() / 1_000_000_000
                    if deps.max_broadcast_gas_price_wei()
                    else None
                ),
            )
            gas_price_for_selection = deps.positive_int_value(getattr(gas_estimate_info, "max_fee", 0))
            relay_cost_for_selection = deps.relay_cost_wei(protocol, opportunity_context)
            cache_penalty_for_selection = deps.cache_risk_penalty_usdc_base_units(
                protocol,
                opportunity_context,
                cache_reports,
            )
            public_mempool_penalty_for_selection = deps.public_mempool_risk_penalty_usdc_base_units(
                protocol,
                opportunity_context,
            )
            public_mempool_penalty_bps_for_selection = deps.public_mempool_risk_penalty_bps(
                protocol,
                opportunity_context,
            )
            gas_token_price_report = deps.gas_token_usdc_price_report(
                protocol,
                opportunity_context,
                require_production_sources=True,
            )
            avax_usdc_price_for_selection = deps.positive_int_value(gas_token_price_report.get("priceMicro"))
            if not gas_token_price_report.get("ok"):
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "net_profit_model_incomplete",
                    "blocked_reason": gas_token_price_report.get("reason") or "gas_token_usdc_price_unverified",
                    "error": "fresh AVAX_USDC_PRICE/gasTokenUsdcPrice with updated_at is required before broadcast",
                    "network": network,
                    "chain_id": chain_id,
                    "executor_address": executor_address,
                    "cacheValidation": cache_reports,
                    "gasTokenPrice": gas_token_price_report,
                }

        def preview_group(group: dict[str, Any]) -> Any:
            runtime_trade_args = [deps.runtime_trade_abi_arg(trade, Web3) for trade in group["trades"]]
            return executor.functions.previewOrderedRuntimeAutoExecution(
                runtime_trade_args,
                usdc_params_arg,
                token_params_arg,
                allow_non_usdc_cross_pool,
            ).call({"from": signer_address})

        def estimate_group_gas(group: dict[str, Any]) -> int:
            runtime_trade_args = [deps.runtime_trade_abi_arg(trade, Web3) for trade in group["trades"]]
            return int(
                executor.functions.runOrderedRuntimeTradesAndExecuteAuto(
                    runtime_trade_args,
                    usdc_params_arg,
                    token_params_arg,
                    allow_non_usdc_cross_pool,
                ).estimate_gas({"from": signer_address})
            )

        selected_group, preview_report, route_evaluations = deps.select_best_unified_route_group(
            prepared_groups,
            usdc_address=usdc_address,
            preview_group=preview_group,
            estimate_group_gas=estimate_group_gas if broadcast_enabled else None,
            gas_price_wei=gas_price_for_selection,
            relay_cost_wei=relay_cost_for_selection,
            cache_risk_penalty_usdc=cache_penalty_for_selection,
            public_mempool_risk_penalty_usdc=public_mempool_penalty_for_selection,
            public_mempool_risk_penalty_bps=public_mempool_penalty_bps_for_selection,
            avax_usdc_price_micro=avax_usdc_price_for_selection,
        )
        if selected_group is None or preview_report is None:
            return {
                "ok": False,
                "submitted": False,
                "status": "no_profitable_execution",
                "blocked_reason": "no_profitable_execution",
                "error": "no USDC route group passed the on-chain profit preview",
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "executor_address": executor_address,
                "routeGroupCount": len(prepared_groups),
                "rejectedRouteGroups": rejected_groups,
                "routeEvaluations": route_evaluations,
            }
        runtime_trades = selected_group["trades"]
        runtime_trade_args = [deps.runtime_trade_abi_arg(trade, Web3) for trade in runtime_trades]
        route_plan = selected_group["routePlan"]
        route_check = selected_group["routeCheck"]
        token_x_for_cross_pool = (
            deps.normalize_pair_address(runtime_trades[2].get("tokenX"))
            if len(runtime_trades) > 2
            else ""
        )
        if (
            allow_non_usdc_cross_pool
            and preview_report["executionKind"] == "2"
            and token_x_for_cross_pool != usdc_address
            and token_x_for_cross_pool not in borrowable_addresses
        ):
            return {
                "ok": False,
                "submitted": False,
                "status": "aave_non_usdc_borrow_unverified",
                "blocked_reason": "aave_non_usdc_borrow_unverified",
                "error": "selected tokenX is not present in a current-network Aave reserve cache with enough liquidity",
                "network": network,
                "chain_id": chain_id,
                "executor_address": executor_address,
                "tokenX": token_x_for_cross_pool,
                "borrowableAddressCount": len(borrowable_addresses),
                "routeEvaluations": route_evaluations,
                "reserveCacheFile": str(deps.aave_reserve_cache_path()),
            }
        request_payload = {
            "runtimeTrades": runtime_trade_args,
            "runtimeTradePlan": deps.runtime_trade_plan_report(runtime_trades),
            "routePlan": route_plan,
            "routeCheck": route_check,
            "routeGroupCount": len(prepared_groups),
            "rejectedRouteGroups": rejected_groups,
            "routeEvaluations": route_evaluations,
            "cacheValidation": cache_reports,
            "selectedRouteKey": selected_group["routeKey"],
            "selectedRouteKind": selected_group["routeKind"],
            "selectedRouteDirection": route_check.get("routeDirection"),
            "executeRuntimeTrade": execute_runtime_trade,
            "executionMode": "ordered_auto",
            "executionPhase": "unified_ordered_state_machine",
            "selectionStrategy": (
                "net_profit_after_gas_delivery_cache_mempool_slippage_correlation_penalties"
                if broadcast_enabled
                else "expected_profit_preview_only"
            ),
            "netProfitModel": {
                "enabled": str(bool(broadcast_enabled)),
                "gasPriceWei": str(gas_price_for_selection),
                # Deprecated compatibility alias; use deliveryCostWei in new reports.
                "relayCostWei": str(relay_cost_for_selection),
                "deliveryCostWei": str(relay_cost_for_selection),
                "cacheRiskPenaltyUsdc": str(cache_penalty_for_selection),
                "publicMempoolRiskPenaltyUsdc": str(public_mempool_penalty_for_selection),
                "publicMempoolRiskPenaltyBps": str(public_mempool_penalty_bps_for_selection),
                "slippageRiskPenaltyUsdc": str(selected_group.get("slippageRiskPenaltyUsdc") or 0),
                "routeCorrelationPenaltyUsdc": str(selected_group.get("routeCorrelationPenaltyUsdc") or 0),
                "avaxUsdcPriceMicro": str(avax_usdc_price_for_selection),
                "gasTokenPrice": gas_token_price_report,
            },
            "selectedTokenRisk": selected_group.get("tokenRisk"),
            "executionParams": {key: str(value) for key, value in usdc_params_arg.items()},
            "crossPoolExecutionParams": {key: str(value) for key, value in token_params_arg.items()},
            "crossPoolFilter": {
                "usdcAddress": usdc_address,
                "nonUsdcCrossPoolEnabled": str(bool(allow_non_usdc_cross_pool)),
                "borrowableAddressCount": str(len(borrowable_addresses)),
                "borrowableTokenX": str(token_x_for_cross_pool in borrowable_addresses),
            },
            "selectedStrategyStatus": preview_report["strategyStatus"],
            "selectedExecutionKind": preview_report["executionKind"],
            "selectedTradeArrayIndex": preview_report["selectedTradeArrayIndex"],
            "deliveryPolicy": deps.delivery_policy_report(protocol, opportunity_context),
        }
        if broadcast_enabled:
            circuit_status = deps.direct_circuit_status(network=network, route_key=selected_group["routeKey"])
            request_payload["circuitBreaker"] = circuit_status
            if circuit_status.get("paused"):
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "direct_circuit_breaker_paused",
                    "blocked_reason": "direct_circuit_breaker_paused",
                    "error": circuit_status.get("reason") or "direct on-chain circuit breaker is paused",
                    "network": network,
                    "chain_id": chain_id,
                    "owner": onchain_owner,
                    "signer": signer_address,
                    "executor_address": executor_address,
                    "preflight": preview_report,
                    "request": request_payload,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            selected_evaluation = next(
                (item for item in route_evaluations if item.get("selected")),
                {},
            )
            selected_net_profit = int((selected_evaluation.get("netProfit") or {}).get("netProfit") or 0)
            if selected_net_profit <= 0:
                return deps.with_direct_circuit_record({
                    "ok": False,
                    "submitted": False,
                    "status": "net_profit_not_positive",
                    "blocked_reason": "net_profit_not_positive",
                    "error": "selected route is not profitable after gas, relay, and cache-risk costs",
                    "network": network,
                    "chain_id": chain_id,
                    "owner": onchain_owner,
                    "signer": signer_address,
                    "executor_address": executor_address,
                    "preflight": preview_report,
                    "request": request_payload,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }, network=network, route_key=selected_group["routeKey"])
        return deps.broadcast_unified_direct_trade(
            {
                "w3": w3,
                "executor": executor,
                "signer_address": signer_address,
                "signer_key": signer_key,
                "chain_id": chain_id,
                "network": network,
                "onchain_owner": onchain_owner,
                "executor_address": executor_address,
                "preview_report": preview_report,
                "request_payload": request_payload,
                "selected_group": selected_group,
                "runtime_trade_args": runtime_trade_args,
                "usdc_params_arg": usdc_params_arg,
                "token_params_arg": token_params_arg,
                "broadcast_enabled": broadcast_enabled,
                "execute_runtime_trade": execute_runtime_trade,
                "allow_non_usdc_cross_pool": allow_non_usdc_cross_pool,
                "gas_estimate_info": gas_estimate_info,
                "gas_price_for_selection": gas_price_for_selection,
                "relay_cost_for_selection": relay_cost_for_selection,
                "cache_penalty_for_selection": cache_penalty_for_selection,
                "public_mempool_penalty_for_selection": public_mempool_penalty_for_selection,
                "public_mempool_penalty_bps_for_selection": public_mempool_penalty_bps_for_selection,
                "avax_usdc_price_for_selection": avax_usdc_price_for_selection,
                "current_block": current_block,
                "timeout_seconds": timeout_seconds,
                "protocol": protocol,
                "opportunity_context": opportunity_context,
            }
        )
    except Exception as exc:
        execution_error = deps.execution_failure_report(exc)
        return {
            "ok": False,
            "submitted": False,
            "status": "execution_preflight_failed" if execution_error else "submission_failed",
            "blocked_reason": execution_error.get("failureReason") if execution_error else "submission_failed",
            "error": execution_error.get("failureReason") if execution_error else redact_sensitive_text(exc),
            "execution_error": execution_error,
        }
