from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text
from bot.execution.gas_estimator import estimate_gas_price
from dex.intent_trade.chains.avalanche.config import (
    AVALANCHE_CHAIN_ID,
    AVALANCHE_RPC_ENV,
    AVALANCHE_USDC_ADDRESS,
    FUJI_CHAIN_ID,
    FUJI_RPC_ENV,
    FUJI_USDC_ADDRESS,
)
from dex.intent_trade.direct_utils import (
    _bool_value,
    _execution_failure_report,
)
from dex.intent_trade.direct_intent import (
    build_triangular_onchain_intent_trade as _build_triangular_onchain_intent_trade_impl,
    pair_index_from_tokenx_tokeny as _pair_index_from_tokenx_tokeny_impl,
    pair_tokens_from_path as _pair_tokens_from_path_impl,
    resolve_pair_id as _resolve_pair_id_impl,
)
from dex.intent_trade.direct_symbols import (
    allow_single_pair_diagnostic as _allow_single_pair_diagnostic_impl,
    allow_stable_exercise_targets as _allow_stable_exercise_targets_impl,
    base_symbol_from_row as _base_symbol_from_row_impl,
    float_or_none as _float_or_none_impl,
    is_stable_exercise_symbol as _is_stable_exercise_symbol_impl,
    normalized_symbol as _normalized_symbol_impl,
    symbol_aliases as _symbol_aliases_impl,
)
from dex.intent_trade.direct_submission import submit_direct_onchain_trade as _submit_direct_onchain_trade_impl
from dex.intent_trade import direct_unified_cache as _direct_unified_cache
from dex.intent_trade.direct_onchain_config import (
    broadcast_gas_guard as _broadcast_gas_guard_impl,
    chain_id_value as _chain_id_value_impl,
    direct_rpc_url as _direct_rpc_url_impl,
    env as _env_impl,
    env_bool as _env_bool_impl,
    env_pair_id as _env_pair_id_impl,
    env_usdc_pair_table as _env_usdc_pair_table_impl,
    executor_adapter_unverified_error as _executor_adapter_unverified_error_impl,
    executor_adapter_unverified_status as _executor_adapter_unverified_status_impl,
    explicit_pair_id as _explicit_pair_id_impl,
    first_value as _first_value_impl,
    local_usdc_pair_memory_table as _local_usdc_pair_memory_table_impl,
    max_broadcast_gas_price_wei as _max_broadcast_gas_price_wei_impl,
    normalize_direct_onchain_network as _normalize_direct_onchain_network_impl,
    normalize_pair_address as _normalize_pair_address_impl,
    normalize_usdc_pair_table as _normalize_usdc_pair_table_impl,
    parse_pair_id as _parse_pair_id_impl,
    positive_int_value as _positive_int_value_impl,
    resolve_direct_onchain_network as _resolve_direct_onchain_network_impl,
    runtime_candidate_pairs as _runtime_candidate_pairs_impl,
    unified_executor_supported_for_network as _unified_executor_supported_for_network_impl,
    usdc_decimal_to_base_units as _usdc_decimal_to_base_units_impl,
)
from dex.intent_trade.direct_unified import (
    ordered_runtime_trade_plan as _ordered_runtime_trade_plan_impl,
    prepare_unified_route_groups as _prepare_unified_route_groups_impl,
    route_group_target_symbols as _route_group_target_symbols_impl,
    runtime_trade_abi_arg as _runtime_trade_abi_arg_impl,
    select_best_unified_route_group as _select_best_unified_route_group_impl,
    unified_executor_route_order_check as _unified_executor_route_order_check_impl,
    unified_cross_pool_params as _unified_cross_pool_params_impl,
    unified_execution_params as _unified_execution_params_impl,
    unified_receipt_events as _unified_receipt_events_impl,
)
from dex.intent_trade.direct_unified_broadcast import (
    broadcast_unified_direct_trade as _broadcast_unified_direct_trade_impl,
)
from dex.intent_trade.direct_unified_cache import (
    aave_reserve_cache_path as _aave_reserve_cache_path_impl,
    cache_observed_block as _cache_observed_block_impl,
    canonical_json_hash as _canonical_json_hash_impl,
    direct_circuit_breaker_path as _direct_circuit_breaker_path_impl,
    direct_pre_pause_path as _direct_pre_pause_path_impl,
    load_aave_reserve_cache as _load_aave_reserve_cache_impl,
    load_runtime_pool_cache as _load_runtime_pool_cache_impl,
    load_unified_executor_abi as _load_unified_executor_abi_impl,
    load_unified_token_registry as _load_unified_token_registry_impl,
    max_runtime_cache_age_blocks as _max_runtime_cache_age_blocks_impl,
    parse_utc_datetime as _parse_utc_datetime_impl,
    read_json_file as _read_json_file_impl,
    reserve_cache_validation as _reserve_cache_validation_impl,
    runtime_cache_validation as _runtime_cache_validation_impl,
    runtime_pool_cache_path as _runtime_pool_cache_path_impl,
    unified_executor_artifact_path as _unified_executor_artifact_path_impl,
    unified_required_factory as _unified_required_factory_impl,
    unified_token_registry_path as _unified_token_registry_path_impl,
    utc_now as _utc_now_impl,
    write_json_file as _write_json_file_impl,
)
from dex.intent_trade.direct_unified_candidates import (
    cache_entry_symbols as _cache_entry_symbols_impl,
    cache_entry_tokens as _cache_entry_tokens_impl,
    cache_pool_candidates_for_pair as _cache_pool_candidates_for_pair_impl,
    cache_usdc_supported_symbols as _cache_usdc_supported_symbols_impl,
    market_change_by_symbol as _market_change_by_symbol_impl,
    market_state_from_context as _market_state_from_context_impl,
    normalize_runtime_pool as _normalize_runtime_pool_impl,
    normalize_runtime_trade as _normalize_runtime_trade_impl,
    runtime_route_group_candidates as _runtime_route_group_candidates_impl,
    runtime_route_group_limit as _runtime_route_group_limit_impl,
    runtime_route_groups_from_market_state as _runtime_route_groups_from_market_state_impl,
    runtime_strategy_from_env as _runtime_strategy_from_env_impl,
    runtime_trade_candidates as _runtime_trade_candidates_impl,
    runtime_trade_route_key as _runtime_trade_route_key_impl,
    runtime_trade_status as _runtime_trade_status_impl,
    runtime_trade_with_metadata as _runtime_trade_with_metadata_impl,
    runtime_trades_from_market_state as _runtime_trades_from_market_state_impl,
    trade_key as _trade_key_impl,
)
from dex.intent_trade.direct_unified_cost import (
    avax_usdc_price_micro as _avax_usdc_price_micro_impl,
    cache_risk_penalty_usdc_base_units as _cache_risk_penalty_usdc_base_units_impl,
    gas_token_price_max_age_seconds as _gas_token_price_max_age_seconds_impl,
    gas_token_price_updated_at as _gas_token_price_updated_at_impl,
    gas_token_usdc_price_report as _gas_token_usdc_price_report_impl,
    public_mempool_risk_penalty_bps as _public_mempool_risk_penalty_bps_impl,
    public_mempool_risk_penalty_usdc_base_units as _public_mempool_risk_penalty_usdc_base_units_impl,
    relay_cost_wei as _relay_cost_wei_impl,
    unified_net_profit_report as _unified_net_profit_report_impl,
    wei_cost_to_usdc_base_units as _wei_cost_to_usdc_base_units_impl,
)
from dex.intent_trade.direct_unified_evidence import (
    _record_direct_circuit_result as _record_direct_circuit_result_impl,
    _with_direct_circuit_record as _with_direct_circuit_record_impl,
    direct_circuit_status as _direct_circuit_status_impl,
    direct_pre_pause_status as _direct_pre_pause_status_impl,
    set_direct_pre_pause as _set_direct_pre_pause_impl,
)
from dex.intent_trade.direct_unified_preflight import (
    _cross_pool_trade_allowed as _cross_pool_trade_allowed_impl,
    _delivery_policy_report as _delivery_policy_report_impl,
    _direct_execution_enabled as _direct_execution_enabled_impl,
    _direct_execution_mode as _direct_execution_mode_impl,
    _non_usdc_cross_pool_enabled as _non_usdc_cross_pool_enabled_impl,
    _private_relay_research_enabled as _private_relay_research_enabled_impl,
    _public_fallback_enabled as _public_fallback_enabled_impl,
    _route_group_token_risk_report as _route_group_token_risk_report_impl,
    _route_group_usdc_penalty as _route_group_usdc_penalty_impl,
    _route_symbols_match as _route_symbols_match_impl,
    _runtime_cross_pool_execution_params as _runtime_cross_pool_execution_params_impl,
    _runtime_execution_params as _runtime_execution_params_impl,
    _runtime_group_intent_score as _runtime_group_intent_score_impl,
    _select_runtime_group_for_intent as _select_runtime_group_for_intent_impl,
    _triangular_trade_allowed as _triangular_trade_allowed_impl,
)
from dex.intent_trade.direct_unified_reports import (
    bytes_to_hex as _bytes_to_hex_impl,
    rank_runtime_trades_by_profit as _rank_runtime_trades_by_profit_impl,
    runtime_execution_preview_report as _runtime_execution_preview_report_impl,
    runtime_failure_reason as _runtime_failure_reason_impl,
    runtime_hop_preview_report as _runtime_hop_preview_report_impl,
    runtime_order_preview_report as _runtime_order_preview_report_impl,
    runtime_run_result_report as _runtime_run_result_report_impl,
    runtime_trade_decision_report as _runtime_trade_decision_report_impl,
    runtime_trade_metadata_by_index as _runtime_trade_metadata_by_index_impl,
    runtime_trade_plan_report as _runtime_trade_plan_report_impl,
    selected_strategy_status as _selected_strategy_status_impl,
)
from dex.intent_trade.direct_unified_reserve import (
    aave_borrowable_token_addresses as _aave_borrowable_token_addresses_impl,
    aave_reserve_assets as _aave_reserve_assets_impl,
    usdc_address_for_network as _usdc_address_for_network_impl,
)
from dex.intent_trade.direct_constants import (
    BNB_USDC_ADDRESS,
    DEFAULT_DIRECT_CIRCUIT_COOLDOWN_SECONDS,
    DEFAULT_DIRECT_CIRCUIT_LOSS_THRESHOLD_USDC,
    DEFAULT_DIRECT_CIRCUIT_OFFCHAIN_THRESHOLD,
    DEFAULT_DIRECT_CIRCUIT_ONCHAIN_THRESHOLD,
    DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS,
    DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS,
    DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS,
    DEFAULT_RUNTIME_ROUTE_GROUP_SCAN,
    DEFAULT_RUNTIME_TRADE_SCAN,
    DIRECT_ONCHAIN_NETWORK_ALIASES,
    DIRECT_ONCHAIN_NETWORKS,
    MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
    MAX_RUNTIME_POOL_SCAN,
    MAX_RUNTIME_ROUTE_GROUP_SCAN,
    MAX_RUNTIME_TRADE_SCAN,
    MAX_UNIFIED_ROUTE_TRADE_INPUTS,
    ROBINHOOD_WETH_ADDRESS,
    ROUTE_DECISION_COMPONENTS,
    RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
    RUNTIME_DECISION_COMPONENTS,
    RUNTIME_EXECUTION_PARAMS_COMPONENTS,
    RUNTIME_EXECUTION_PREVIEW_COMPONENTS,
    RUNTIME_POOL_COMPONENTS,
    RUNTIME_TOP_BOTTOM_LIMIT,
    RUNTIME_TRADE_COMPONENTS,
    SRC_ROOT,
    STABLE_EXERCISE_SYMBOLS,
    _SELECTED_STRATEGY_STATUS_TRANSLATION,
    TRIANGULAR_CONTROLLER_ABI,
    USDC_PAIR_COMPONENTS,
)
from dex.intent_trade.direct_token_registry import (
    _attach_registry_token_risk_metadata,
    _registry_token_risks_for_trades,
)


_USDC_PAIR_MEMORY_TABLE: list[dict[str, str]] = []
_UNIFIED_EXECUTOR_ABI_CACHE: list[dict[str, Any]] | None = None

from dex.intent_trade import direct_runtime_helpers as _direct_runtime_helpers

# Keep the historical direct_impl import surface while helper implementations
# live in a focused runtime module.
for _helper_name in _direct_runtime_helpers.__all__:
    globals()[_helper_name] = getattr(_direct_runtime_helpers, _helper_name)
del _helper_name

def build_triangular_onchain_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    return _build_triangular_onchain_intent_trade_impl(
        link_name,
        expected_profit,
        rising_tokens,
        falling_tokens,
    )


def _submit_legacy_direct_onchain_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "submitted": False,
        "status": "legacy_direct_path_disabled",
        "blocked_reason": "legacy_direct_path_disabled",
        "error": (
            "legacy TriangularRouteController direct-onchain submission is disabled; "
            "use UnifiedFlashLoanMevExecutor via submit_direct_onchain_trade"
        ),
    }



def _unified_executor_route_order_check(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
) -> dict[str, Any]:
    return _unified_executor_route_order_check_impl(
        trades,
        usdc_address=usdc_address,
        normalize_pair_address=_normalize_pair_address,
        runtime_trade_status=_runtime_trade_status,
    )


def _route_group_target_symbols(group: dict[str, Any], trades: list[dict[str, Any]]) -> list[str]:
    return _route_group_target_symbols_impl(group, trades, normalized_symbol=_normalized_symbol)


def _prepare_unified_route_groups(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    allow_single_pair_diagnostic: bool = False,
    allow_stable_targets: bool = False,
    require_token_risk_metadata: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _prepare_unified_route_groups_impl(
        groups,
        usdc_address=usdc_address,
        ordered_runtime_trade_plan=_ordered_runtime_trade_plan,
        unified_executor_route_order_check=_unified_executor_route_order_check,
        route_group_target_symbols=_route_group_target_symbols,
        route_group_token_risk_report=lambda group, trades, require_metadata: _route_group_token_risk_report(
            group,
            trades,
            require_metadata=require_metadata,
        ),
        route_group_usdc_penalty=lambda group, *names: _route_group_usdc_penalty(group, *names),
        is_stable_exercise_symbol=_is_stable_exercise_symbol,
        allow_single_pair_diagnostic=allow_single_pair_diagnostic,
        allow_stable_targets=allow_stable_targets,
        require_token_risk_metadata=require_token_risk_metadata,
    )


def _select_best_unified_route_group(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    preview_group: Callable[[dict[str, Any]], Any],
    estimate_group_gas: Callable[[dict[str, Any]], int] | None = None,
    gas_price_wei: int = 0,
    relay_cost_wei: int = 0,
    cache_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_bps: int = 0,
    avax_usdc_price_micro: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    return _select_best_unified_route_group_impl(
        groups,
        usdc_address=usdc_address,
        preview_group=preview_group,
        runtime_order_preview_report=_runtime_order_preview_report,
        unified_net_profit_report=_unified_net_profit_report,
        normalize_pair_address=_normalize_pair_address,
        estimate_group_gas=estimate_group_gas,
        gas_price_wei=gas_price_wei,
        relay_cost_wei=relay_cost_wei,
        cache_risk_penalty_usdc=cache_risk_penalty_usdc,
        public_mempool_risk_penalty_usdc=public_mempool_risk_penalty_usdc,
        public_mempool_risk_penalty_bps=public_mempool_risk_penalty_bps,
        avax_usdc_price_micro=avax_usdc_price_micro,
        redact_sensitive_text=redact_sensitive_text,
    )


def _unified_execution_params(params: dict[str, int]) -> dict[str, int]:
    return _unified_execution_params_impl(params)


def _unified_cross_pool_params(params: dict[str, int]) -> dict[str, int]:
    return _unified_cross_pool_params_impl(params)


def _unified_receipt_events(contract: Any, receipt: Any) -> dict[str, list[dict[str, Any]]]:
    return _unified_receipt_events_impl(contract, receipt)


def submit_direct_onchain_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    return _submit_direct_onchain_trade_impl(
        quote_payload=quote_payload,
        opportunity=opportunity,
        timeout_seconds=timeout_seconds,
    )
