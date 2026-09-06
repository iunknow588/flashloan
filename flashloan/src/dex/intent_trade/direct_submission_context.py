"""Dependency binding for unified direct submission.

The submission orchestrator intentionally resolves these through
``direct_impl`` so existing tests and operator monkeypatch hooks keep working.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def submission_deps() -> Any:
    from dex.intent_trade import direct_impl as impl

    return SimpleNamespace(
        direct_onchain_networks=impl.DIRECT_ONCHAIN_NETWORKS,
        aave_borrowable_token_addresses=impl._aave_borrowable_token_addresses,
        aave_reserve_cache_path=impl._aave_reserve_cache_path,
        allow_single_pair_diagnostic=impl._allow_single_pair_diagnostic,
        allow_stable_exercise_targets=impl._allow_stable_exercise_targets,
        broadcast_unified_direct_trade=impl._broadcast_unified_direct_trade_impl,
        cache_risk_penalty_usdc_base_units=impl._cache_risk_penalty_usdc_base_units,
        delivery_policy_report=impl._delivery_policy_report,
        direct_circuit_status=impl._direct_circuit_status,
        direct_execution_enabled=impl._direct_execution_enabled,
        direct_rpc_url=impl._direct_rpc_url,
        env=impl._env,
        env_bool=impl._env_bool,
        execution_failure_report=impl._execution_failure_report,
        executor_adapter_unverified_error=impl._executor_adapter_unverified_error,
        executor_adapter_unverified_status=impl._executor_adapter_unverified_status,
        gas_token_usdc_price_report=impl._gas_token_usdc_price_report,
        load_aave_reserve_cache=impl._load_aave_reserve_cache,
        load_runtime_pool_cache=impl._load_runtime_pool_cache,
        load_unified_executor_abi=impl._load_unified_executor_abi,
        max_broadcast_gas_price_wei=impl._max_broadcast_gas_price_wei,
        max_runtime_cache_age_blocks=impl._max_runtime_cache_age_blocks,
        non_usdc_cross_pool_enabled=impl._non_usdc_cross_pool_enabled,
        normalize_direct_onchain_network=impl._normalize_direct_onchain_network,
        normalize_pair_address=impl._normalize_pair_address,
        positive_int_value=impl._positive_int_value,
        prepare_unified_route_groups=impl._prepare_unified_route_groups,
        public_mempool_risk_penalty_bps=impl._public_mempool_risk_penalty_bps,
        public_mempool_risk_penalty_usdc_base_units=impl._public_mempool_risk_penalty_usdc_base_units,
        relay_cost_wei=impl._relay_cost_wei,
        reserve_cache_validation=impl._reserve_cache_validation,
        resolve_direct_onchain_network=impl._resolve_direct_onchain_network,
        runtime_cache_validation=impl._runtime_cache_validation,
        runtime_cross_pool_execution_params=impl._runtime_cross_pool_execution_params,
        runtime_execution_params=impl._runtime_execution_params,
        runtime_pool_cache_path=impl._runtime_pool_cache_path,
        runtime_route_group_candidates=impl._runtime_route_group_candidates,
        runtime_trade_abi_arg=impl._runtime_trade_abi_arg,
        runtime_trade_plan_report=impl._runtime_trade_plan_report,
        select_best_unified_route_group=impl._select_best_unified_route_group,
        unified_cross_pool_params=impl._unified_cross_pool_params,
        unified_execution_params=impl._unified_execution_params,
        unified_executor_supported_for_network=impl._unified_executor_supported_for_network,
        unified_required_factory=impl._unified_required_factory,
        usdc_address_for_network=impl._usdc_address_for_network,
        with_direct_circuit_record=impl._with_direct_circuit_record,
        direct_pre_pause_status=impl.direct_pre_pause_status,
        estimate_gas_price=impl.estimate_gas_price,
    )
