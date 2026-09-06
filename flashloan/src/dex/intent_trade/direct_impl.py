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


_USDC_PAIR_MEMORY_TABLE: list[dict[str, str]] = []
_UNIFIED_EXECUTOR_ABI_CACHE: list[dict[str, Any]] | None = None
MAX_RUNTIME_POOL_SCAN = 5
MAX_RUNTIME_TRADE_SCAN = 5
MAX_UNIFIED_ROUTE_TRADE_INPUTS = 4
DEFAULT_RUNTIME_TRADE_SCAN = 5
RUNTIME_TOP_BOTTOM_LIMIT = 5
MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN = RUNTIME_TOP_BOTTOM_LIMIT * RUNTIME_TOP_BOTTOM_LIMIT
DEFAULT_RUNTIME_ROUTE_GROUP_SCAN = 25
MAX_RUNTIME_ROUTE_GROUP_SCAN = 250
DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS = 30
DEFAULT_DIRECT_CIRCUIT_OFFCHAIN_THRESHOLD = 5
DEFAULT_DIRECT_CIRCUIT_ONCHAIN_THRESHOLD = 2
DEFAULT_DIRECT_CIRCUIT_LOSS_THRESHOLD_USDC = "1"
DEFAULT_DIRECT_CIRCUIT_COOLDOWN_SECONDS = 3600
DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS = 120
DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS = 100
SRC_ROOT = Path(__file__).resolve().parents[1]
BNB_USDC_ADDRESS = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
ROBINHOOD_WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
STABLE_EXERCISE_SYMBOLS = {
    "USDC",
    "USDC.E",
    "USDT",
    "USDT.E",
    "USDT0",
    "USDT0.E",
    "DAI",
    "DAI.E",
    "FRAX",
    "EURC",
    "USDE",
    "SUSDE",
}
DIRECT_ONCHAIN_NETWORKS = {
    "avalanche": {
        "chain_id": AVALANCHE_CHAIN_ID,
        "testnet": False,
        "rpc_env": AVALANCHE_RPC_ENV,
        "unified_executor_supported": True,
    },
    "fuji": {
        "chain_id": FUJI_CHAIN_ID,
        "testnet": True,
        "rpc_env": FUJI_RPC_ENV,
        "unified_executor_supported": True,
    },
    "bnb": {
        "chain_id": 56,
        "testnet": False,
        "rpc_env": ("BNB_RPC_URL", "BSC_RPC_URL", "BNB_RPC", "BSC_RPC"),
        "unified_executor_supported": False,
    },
    "robinhood": {
        "chain_id": 4663,
        "testnet": False,
        "rpc_env": (
            "ROBINHOOD_RPC_URL",
            "ROBINHOOD_RPC",
            "ROBINHOOD_RPCS",
            "ROBINHOOD_ALCHEMY_RPC_URL",
            "ROBINHOOD_ALCHEMY_RPC",
            "ROBINHOOD_MAINNET_RPC_URL",
            "ROBINHOOD_MAINNET_RPC",
        ),
        "unified_executor_supported": False,
    },
    "robinhood-testnet": {
        "chain_id": 46630,
        "testnet": True,
        "rpc_env": (
            "ROBINHOOD_TESTNET_RPC_URL",
            "ROBINHOOD_TESTNET_RPC",
            "ROBINHOOD_TESTNET_RPCS",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC",
        ),
        "unified_executor_supported": False,
    },
}
DIRECT_ONCHAIN_NETWORK_ALIASES = {
    "avax": "avalanche",
    "avalanche-c": "avalanche",
    "avalanche-c-chain": "avalanche",
    "43114": "avalanche",
    "avalanche-fuji": "fuji",
    "fuji": "fuji",
    "43113": "fuji",
    "bnb": "bnb",
    "bsc": "bnb",
    "bnb-chain": "bnb",
    "binance-smart-chain": "bnb",
    "56": "bnb",
    "robinhood": "robinhood",
    "robinhood-chain": "robinhood",
    "robinhood-mainnet": "robinhood",
    "4663": "robinhood",
    "robinhood-testnet": "robinhood-testnet",
    "robinhood-sepolia": "robinhood-testnet",
    "46630": "robinhood-testnet",
}
ROUTE_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "reverse", "type": "bool"},
    {"name": "quotedFinalUsdc", "type": "uint256"},
    {"name": "profitUsdc", "type": "uint256"},
    {"name": "path", "type": "address[]"},
    {"name": "edgeBps", "type": "uint256"},
    {"name": "requiredEdgeBps", "type": "uint256"},
    {"name": "directComparableAmount", "type": "uint256"},
    {"name": "viaComparableAmount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
    {"name": "requiredFinalUsdc", "type": "uint256"},
    {"name": "minAfterSlippageUsdc", "type": "uint256"},
    {"name": "amountOutMinUsdc", "type": "uint256"},
    {"name": "selectedAmount", "type": "uint256"},
    {"name": "routeMaxBorrow", "type": "uint256"},
    {"name": "probeAmount", "type": "uint256"},
    {"name": "probeProfitUsdc", "type": "uint256"},
    {"name": "fundingCostUsdc", "type": "uint256"},
    {"name": "mBps", "type": "int256"},
]
USDC_PAIR_COMPONENTS = [
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "router", "type": "address"},
]
RUNTIME_POOL_COMPONENTS = [
    {"name": "adapterKind", "type": "uint8"},
    {"name": "pool", "type": "address"},
]
RUNTIME_TRADE_COMPONENTS = [
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "pools", "type": "tuple[10]", "components": RUNTIME_POOL_COMPONENTS},
]
RUNTIME_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "lowPool", "type": "address"},
    {"name": "highPool", "type": "address"},
    {"name": "adapterKind", "type": "uint8"},
    {"name": "lowFee", "type": "uint24"},
    {"name": "highFee", "type": "uint24"},
    {"name": "lowLiquidity", "type": "uint128"},
    {"name": "highLiquidity", "type": "uint128"},
    {"name": "lowNormalizedTick", "type": "int24"},
    {"name": "highNormalizedTick", "type": "int24"},
    {"name": "tickDelta", "type": "int256"},
    {"name": "scannedPoolCount", "type": "uint256"},
    {"name": "validPoolCount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
]
RUNTIME_EXECUTION_PARAMS_COMPONENTS = [
    {"name": "amount", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
    {"name": "amountOutMinUsdc", "type": "uint256"},
    {"name": "minProfitUsdc", "type": "uint256"},
    {"name": "usdcToTokenXFee", "type": "uint24"},
    {"name": "tokenYToUsdcFee", "type": "uint24"},
]
RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS = [
    {"name": "amount", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
    {"name": "minFinalTokenX", "type": "uint256"},
    {"name": "minProfitTokenX", "type": "uint256"},
]
RUNTIME_EXECUTION_PREVIEW_COMPONENTS = [
    {"name": "router", "type": "address"},
    {"name": "swapPath", "type": "bytes"},
    {"name": "quotedFinalUsdc", "type": "uint256"},
    {"name": "premiumUsdc", "type": "uint256"},
    {"name": "requiredFinalUsdc", "type": "uint256"},
    {"name": "protectedAmountOutMinUsdc", "type": "uint256"},
    {"name": "minProfitUsdc", "type": "uint256"},
]
# docs: docs/闂數璐烽槻MEV/鍗曞悎绾︾増鏈?01_鍗曞悎绾︽帴鍙ｈ璁?md
# Update this table, ABI docs, and tests together when executionKind/status changes.
_SELECTED_STRATEGY_STATUS_TRANSLATION: dict[tuple[str, int | None, int], int] = {
    ("auto", 1, 3): 4,
    ("auto", 2, 3): 3,
    ("triangular", 1, 3): 4,
}
TRIANGULAR_CONTROLLER_ABI = [
    {
        "type": "function",
        "name": "owner",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "previewBestRuntimeTrades",
        "stateMutability": "view",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runBestRuntimeTrades",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runBestRuntimeTradesAndExecute",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewBestRuntimeExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "previewFirstProfitableRuntimeExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runFirstProfitableRuntimeTradesAndExecute",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewFirstProfitableRuntimeAutoExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runFirstProfitableRuntimeTradesAndExecuteAuto",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
        ],
        "outputs": [
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewOrderedRuntimeAutoExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
            {"name": "enableNonUsdcCrossPool", "type": "bool"},
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "strategyStatus", "type": "uint256"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runOrderedRuntimeTradesAndExecuteAuto",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
            {"name": "enableNonUsdcCrossPool", "type": "bool"},
        ],
        "outputs": [
            {"name": "strategyStatus", "type": "uint256"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
]

def _unified_executor_artifact_path() -> Path:
    return _unified_executor_artifact_path_impl()


def _load_unified_executor_abi() -> list[dict[str, Any]]:
    global _UNIFIED_EXECUTOR_ABI_CACHE
    if _UNIFIED_EXECUTOR_ABI_CACHE is not None:
        return _UNIFIED_EXECUTOR_ABI_CACHE
    _direct_unified_cache._UNIFIED_EXECUTOR_ABI_CACHE = None
    _UNIFIED_EXECUTOR_ABI_CACHE = _load_unified_executor_abi_impl()
    return _UNIFIED_EXECUTOR_ABI_CACHE

def _env(*names: str, default: str = "") -> str:
    return _env_impl(*names, default=default)


def _env_bool(*names: str) -> bool:
    return _env_bool_impl(*names)


def _max_broadcast_gas_price_wei() -> int:
    return _max_broadcast_gas_price_wei_impl()


def _broadcast_gas_guard(gas_units: Any, gas_estimate: Any) -> dict[str, Any]:
    return _broadcast_gas_guard_impl(gas_units, gas_estimate)


def _normalize_direct_onchain_network(value: Any) -> str:
    return _normalize_direct_onchain_network_impl(value)


def _chain_id_value(value: Any) -> int | None:
    return _chain_id_value_impl(value)


def _resolve_direct_onchain_network(protocol: dict[str, Any] | None = None) -> tuple[str, int, bool]:
    return _resolve_direct_onchain_network_impl(protocol)


def _direct_rpc_url(network: str) -> tuple[str, tuple[str, ...]]:
    return _direct_rpc_url_impl(network)


def _unified_executor_supported_for_network(network: str) -> bool:
    return _unified_executor_supported_for_network_impl(network)


def _executor_adapter_unverified_status(network: str) -> str:
    return _executor_adapter_unverified_status_impl(network)


def _executor_adapter_unverified_error(network: str) -> str:
    return _executor_adapter_unverified_error_impl(network)


def _positive_int_value(value: Any, *, default: int = 0) -> int:
    return _positive_int_value_impl(value, default=default)


def _usdc_decimal_to_base_units(value: Any) -> int:
    return _usdc_decimal_to_base_units_impl(value)


def _normalize_pair_address(value: Any) -> str:
    return _normalize_pair_address_impl(value)


def _parse_pair_id(raw: Any) -> int | None:
    return _parse_pair_id_impl(raw)


def _explicit_pair_id(protocol: dict[str, Any]) -> int | None:
    return _explicit_pair_id_impl(protocol)


def _env_pair_id() -> int | None:
    return _env_pair_id_impl()


def set_usdc_pair_memory_table(pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, str]]:
    global _USDC_PAIR_MEMORY_TABLE
    _USDC_PAIR_MEMORY_TABLE = _normalize_usdc_pair_table(pairs)
    return list(_USDC_PAIR_MEMORY_TABLE)


def _normalize_usdc_pair_table(pairs: Any) -> list[dict[str, str]]:
    return _normalize_usdc_pair_table_impl(pairs)


def _env_usdc_pair_table() -> list[dict[str, str]]:
    return _env_usdc_pair_table_impl()


def local_usdc_pair_memory_table(protocol: dict[str, Any] | None = None) -> list[dict[str, str]]:
    return _local_usdc_pair_memory_table_impl(protocol)


def _runtime_candidate_pairs(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> list[dict[str, str]]:
    return _runtime_candidate_pairs_impl(protocol, opportunity)


def _normalize_runtime_pool(pool: Any) -> dict[str, Any]:
    return _normalize_runtime_pool_impl(pool)


def _normalize_runtime_trade(trade: Any, trade_index: int) -> dict[str, Any]:
    return _normalize_runtime_trade_impl(trade, trade_index)


def _route_group_token_risk_report(
    group: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    require_metadata: bool = False,
) -> dict[str, Any]:
    return _route_group_token_risk_report_impl(group, trades, require_metadata=require_metadata)


def _route_group_usdc_penalty(group: dict[str, Any], *names: str) -> int:
    return _route_group_usdc_penalty_impl(group, *names)


def _unified_token_registry_path() -> Path:
    return _unified_token_registry_path_impl()


def _load_unified_token_registry(path: Path | None = None) -> dict[str, Any]:
    return _load_unified_token_registry_impl(path)


def _registry_token_risks_for_trades(
    trades: list[dict[str, Any]],
    *,
    network: str,
    registry: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Resolve reviewed token metadata for one dynamically generated route group."""
    payload = registry if isinstance(registry, dict) else _load_unified_token_registry()
    expected_chain_id = DIRECT_ONCHAIN_NETWORKS.get(
        _normalize_direct_onchain_network(network),
        {},
    ).get("chain_id")
    try:
        registry_chain_id = int(payload.get("chainId") or payload.get("chain_id") or 0)
    except (TypeError, ValueError):
        registry_chain_id = 0
    if not payload or not expected_chain_id or registry_chain_id != expected_chain_id:
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_chain_mismatch_or_unavailable",
            "registryPath": str(_unified_token_registry_path()),
        }
    if payload.get("_integrityRequired") and not payload.get("_integrityOk"):
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_hash_mismatch",
            "registryVersion": str(payload.get("registryVersion") or ""),
            "registryPath": str(_unified_token_registry_path()),
            "canonicalHash": str(payload.get("_canonicalHash") or ""),
            "expectedCanonicalHash": str(payload.get("_expectedCanonicalHash") or ""),
        }

    now = _utc_now()
    by_address: dict[str, dict[str, Any]] = {}
    expired_by_address: dict[str, dict[str, Any]] = {}
    for item in payload.get("tokens") or []:
        if not isinstance(item, dict):
            continue
        token = _normalize_pair_address(item.get("token") or item.get("address"))
        if not token:
            continue
        expires_at = _parse_utc_datetime(item.get("expiresAt") or item.get("expires_at"))
        if expires_at and expires_at <= now:
            expired_by_address[token] = item
            continue
        by_address[token] = item

    route_tokens = sorted(
        {
            _normalize_pair_address(token)
            for trade in trades
            if isinstance(trade, dict)
            for token in (trade.get("tokenX"), trade.get("tokenY"))
            if _normalize_pair_address(token)
        }
    )
    missing = [token for token in route_tokens if token not in by_address]
    if missing:
        expired = [token for token in missing if token in expired_by_address]
        if expired:
            return None, {
                "source": "registry",
                "available": False,
                "reason": "token_registry_metadata_expired",
                "expiredTokens": expired,
                "missingTokens": missing,
                "registryVersion": str(payload.get("registryVersion") or ""),
                "registryPath": str(_unified_token_registry_path()),
                "automaticDowngrade": "route_group_diagnostic_only_until_registry_refresh",
            }
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_metadata_missing",
            "missingTokens": missing,
            "registryVersion": str(payload.get("registryVersion") or ""),
            "registryPath": str(_unified_token_registry_path()),
        }

    token_risks: list[dict[str, Any]] = []
    for token in route_tokens:
        source = by_address[token]
        token_risks.append(
            {
                "token": token,
                "symbol": str(source.get("symbol") or ""),
                "decimals": source.get("decimals"),
                "approveBehavior": str(source.get("approveBehavior") or source.get("approve_behavior") or ""),
                "transferBehavior": str(source.get("transferBehavior") or source.get("transfer_behavior") or ""),
                "aaveBorrowEnabled": source.get("aaveBorrowEnabled", source.get("aave_borrow_enabled")),
                "poolCacheVerifiedAt": str(
                    source.get("poolCacheVerifiedAt") or source.get("pool_cache_verified_at") or ""
                ),
                "role": str(source.get("role") or ""),
                "reentrancyRisk": str(
                    source.get("reentrancyRisk") or source.get("reentrancy_risk") or "none"
                ).lower(),
                "decimalTruncationRisk": str(
                    source.get("decimalTruncationRisk")
                    or source.get("decimal_truncation_risk")
                    or "none"
                ).lower(),
                "reviewedAt": str(source.get("reviewedAt") or source.get("reviewed_at") or ""),
                "expiresAt": str(source.get("expiresAt") or source.get("expires_at") or ""),
            }
        )
    return token_risks, {
        "source": "registry",
        "available": True,
        "registryVersion": str(payload.get("registryVersion") or ""),
        "registryPath": str(_unified_token_registry_path()),
        "chainId": str(registry_chain_id),
        "canonicalHash": str(payload.get("_canonicalHash") or ""),
        "integrityRequired": bool(payload.get("_integrityRequired")),
        "integrityOk": bool(payload.get("_integrityOk")),
    }


def _attach_registry_token_risk_metadata(
    groups: list[dict[str, Any]],
    *,
    network: str,
    registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    for group in groups:
        if not isinstance(group, dict) or group.get("tokenRisks") or group.get("token_risks"):
            continue
        trades = group.get("trades") if isinstance(group.get("trades"), list) else []
        token_risks, report = _registry_token_risks_for_trades(
            trades,
            network=network,
            registry=registry,
        )
        group["tokenRiskRegistry"] = report
        if token_risks:
            group["tokenRisks"] = token_risks
    return groups


def _runtime_pool_cache_path() -> Path:
    return _runtime_pool_cache_path_impl()


def _load_runtime_pool_cache(path: Path | None = None) -> dict[str, Any]:
    return _load_runtime_pool_cache_impl(path)


def _aave_reserve_cache_path() -> Path:
    return _aave_reserve_cache_path_impl()


def _direct_circuit_breaker_path() -> Path:
    return _direct_circuit_breaker_path_impl()


def _direct_pre_pause_path() -> Path:
    return _direct_pre_pause_path_impl()


def _load_aave_reserve_cache(path: Path | None = None) -> dict[str, Any]:
    return _load_aave_reserve_cache_impl(path)


def _read_json_file(path: Path) -> dict[str, Any]:
    return _read_json_file_impl(path)


def _canonical_json_hash(value: Any) -> str:
    return _canonical_json_hash_impl(value)


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    _write_json_file_impl(path, payload)


def _utc_now() -> datetime:
    return _utc_now_impl()


def _parse_utc_datetime(value: Any) -> datetime | None:
    return _parse_utc_datetime_impl(value)


def _direct_circuit_enabled() -> bool:
    raw = _env("DIRECT_ONCHAIN_CIRCUIT_BREAKER_ENABLED", "UNIFIED_EXECUTOR_CIRCUIT_BREAKER_ENABLED", default="true")
    return _bool_value(raw)


def _direct_circuit_thresholds() -> dict[str, int]:
    def configured_int(name: str, fallback: int) -> int:
        try:
            return max(1, _positive_int_value(_env(name), default=fallback))
        except ValueError:
            return fallback

    return {
        "offchain": configured_int(
            "DIRECT_ONCHAIN_CIRCUIT_OFFCHAIN_THRESHOLD",
            DEFAULT_DIRECT_CIRCUIT_OFFCHAIN_THRESHOLD,
        ),
        "onchain": configured_int(
            "DIRECT_ONCHAIN_CIRCUIT_ONCHAIN_THRESHOLD",
            DEFAULT_DIRECT_CIRCUIT_ONCHAIN_THRESHOLD,
        ),
        "cooldownSeconds": configured_int(
            "DIRECT_ONCHAIN_CIRCUIT_COOLDOWN_SECONDS",
            DEFAULT_DIRECT_CIRCUIT_COOLDOWN_SECONDS,
        ),
        "redLossUsdc": _usdc_decimal_to_base_units(
            _env(
                "DIRECT_ONCHAIN_CIRCUIT_RED_LOSS_USDC",
                default=DEFAULT_DIRECT_CIRCUIT_LOSS_THRESHOLD_USDC,
            )
        ),
    }


def _direct_circuit_group_key(network: str, route_key: Any = "") -> str:
    key = str(route_key or "global").strip() or "global"
    return f"{_normalize_direct_onchain_network(network)}:{key}"


def _direct_circuit_status(
    *,
    network: str,
    route_key: Any = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    return _direct_circuit_status_impl(network=network, route_key=route_key, now=now, path=path)


def direct_pre_pause_status(path: Path | None = None) -> dict[str, Any]:
    return _direct_pre_pause_status_impl(path)


def set_direct_pre_pause(
    paused: bool,
    *,
    reason: str,
    set_by: str = "operator",
    pause_trigger_source: str = "manual",
    pause_detected_at: Any = None,
    pause_tx_sent_at: Any = None,
    pause_tx_confirmed_at: Any = None,
    blocked_candidate_count: Any = 0,
    path: Path | None = None,
) -> dict[str, Any]:
    return _set_direct_pre_pause_impl(
        paused,
        reason=reason,
        set_by=set_by,
        pause_trigger_source=pause_trigger_source,
        pause_detected_at=pause_detected_at,
        pause_tx_sent_at=pause_tx_sent_at,
        pause_tx_confirmed_at=pause_tx_confirmed_at,
        blocked_candidate_count=blocked_candidate_count,
        path=path,
    )


def _direct_failure_bucket(status: Any, submitted: bool) -> str:
    text = str(status or "").lower()
    if submitted or text in {"submitted_failed", "confirmed_failed", "transaction_reverted"}:
        return "onchain"
    if text in {
        "private_relay_failed_public_fallback_disabled",
        "submission_failed",
        "gas_price_cap_exceeded",
        "gas_price_unavailable",
    }:
        return "submission"
    return "offchain"


def _direct_failure_loss_usdc(result: dict[str, Any]) -> int:
    receipt = result.get("receipt") if isinstance(result, dict) else None
    try:
        gas_used = _positive_int_value((receipt or {}).get("gasUsed")) if isinstance(receipt, dict) else 0
    except ValueError:
        gas_used = 0
    static_call = result.get("static_call") if isinstance(result, dict) else {}
    gas_pricing = static_call.get("gasPricing") if isinstance(static_call, dict) else {}
    try:
        gas_price = _positive_int_value((gas_pricing or {}).get("gasPriceWei")) if isinstance(gas_pricing, dict) else 0
    except ValueError:
        gas_price = 0
    request = result.get("request") if isinstance(result, dict) else {}
    model = request.get("netProfitModel") if isinstance(request, dict) else {}
    try:
        avax_price = _positive_int_value((model or {}).get("avaxUsdcPriceMicro")) if isinstance(model, dict) else 0
    except ValueError:
        avax_price = 0
    return _wei_cost_to_usdc_base_units(gas_used * gas_price, avax_price)


def _record_direct_circuit_result(
    result: dict[str, Any],
    *,
    network: str,
    route_key: Any = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    return _record_direct_circuit_result_impl(
        result,
        network=network,
        route_key=route_key,
        now=now,
        path=path,
    )


def _with_direct_circuit_record(
    result: dict[str, Any],
    *,
    network: str,
    route_key: Any = "",
) -> dict[str, Any]:
    return _with_direct_circuit_record_impl(result, network=network, route_key=route_key)


def _cache_observed_block(cache: dict[str, Any]) -> int:
    return _cache_observed_block_impl(cache)


def _max_runtime_cache_age_blocks(protocol: dict[str, Any] | None = None) -> int:
    return _max_runtime_cache_age_blocks_impl(protocol)


def _runtime_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
    required_factory: str = "",
) -> dict[str, Any]:
    return _runtime_cache_validation_impl(
        cache,
        network=network,
        current_block=current_block,
        max_age_blocks=max_age_blocks,
        required_factory=required_factory,
    )


def _unified_required_factory(protocol: dict[str, Any] | None, cache: dict[str, Any] | None = None) -> str:
    return _unified_required_factory_impl(protocol, cache)


def _reserve_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
) -> dict[str, Any]:
    return _reserve_cache_validation_impl(
        cache,
        network=network,
        current_block=current_block,
        max_age_blocks=max_age_blocks,
    )


def _public_fallback_enabled(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    return _public_fallback_enabled_impl(protocol, opportunity)


def _private_relay_research_enabled(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    return _private_relay_research_enabled_impl(protocol, opportunity)


def _delivery_policy_report(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> dict[str, Any]:
    return _delivery_policy_report_impl(protocol, opportunity)


def _relay_cost_wei(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    return _relay_cost_wei_impl(protocol, opportunity)


def _cache_risk_penalty_usdc_base_units(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    cache_reports: dict[str, dict[str, Any]] | None = None,
) -> int:
    return _cache_risk_penalty_usdc_base_units_impl(protocol, opportunity, cache_reports)


def _public_mempool_risk_penalty_usdc_base_units(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    return _public_mempool_risk_penalty_usdc_base_units_impl(protocol, opportunity)


def _public_mempool_risk_penalty_bps(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    return _public_mempool_risk_penalty_bps_impl(protocol, opportunity)


def _gas_token_price_max_age_seconds(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    return _gas_token_price_max_age_seconds_impl(protocol, opportunity)


def _gas_token_price_updated_at(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> datetime | None:
    return _gas_token_price_updated_at_impl(protocol, opportunity)


def _avax_usdc_price_micro(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    return _avax_usdc_price_micro_impl(protocol, opportunity)


def _gas_token_usdc_price_report(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    require_production_sources: bool = False,
) -> dict[str, Any]:
    return _gas_token_usdc_price_report_impl(
        protocol,
        opportunity,
        now=now,
        require_production_sources=require_production_sources,
    )


def _wei_cost_to_usdc_base_units(cost_wei: int, avax_usdc_price_micro: int) -> int:
    return _wei_cost_to_usdc_base_units_impl(cost_wei, avax_usdc_price_micro)


def _unified_net_profit_report(
    *,
    expected_profit: int,
    gas_units: int,
    gas_price_wei: int,
    relay_cost_wei: int,
    cache_risk_penalty_usdc: int,
    public_mempool_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_bps: int = 0,
    slippage_risk_penalty_usdc: int = 0,
    route_correlation_penalty_usdc: int = 0,
    avax_usdc_price_micro: int,
) -> dict[str, str]:
    return _unified_net_profit_report_impl(
        expected_profit=expected_profit,
        gas_units=gas_units,
        gas_price_wei=gas_price_wei,
        relay_cost_wei=relay_cost_wei,
        cache_risk_penalty_usdc=cache_risk_penalty_usdc,
        public_mempool_risk_penalty_usdc=public_mempool_risk_penalty_usdc,
        public_mempool_risk_penalty_bps=public_mempool_risk_penalty_bps,
        slippage_risk_penalty_usdc=slippage_risk_penalty_usdc,
        route_correlation_penalty_usdc=route_correlation_penalty_usdc,
        avax_usdc_price_micro=avax_usdc_price_micro,
    )


def _usdc_address_for_network(network: str | None = None, cache: dict[str, Any] | None = None) -> str:
    return _usdc_address_for_network_impl(network, cache)


def _aave_reserve_assets(cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return _aave_reserve_assets_impl(cache)


def _aave_borrowable_token_addresses(
    cache: dict[str, Any] | None = None,
    *,
    min_liquidity: int = 0,
    network: str | None = None,
) -> set[str]:
    return _aave_borrowable_token_addresses_impl(cache, min_liquidity=min_liquidity, network=network)


def _base_symbol_from_row(row: Any) -> str:
    return _base_symbol_from_row_impl(row)


def _normalized_symbol(value: Any) -> str:
    return _normalized_symbol_impl(value)


def _is_stable_exercise_symbol(value: Any) -> bool:
    return _is_stable_exercise_symbol_impl(value)


def _allow_stable_exercise_targets(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    return _allow_stable_exercise_targets_impl(protocol, opportunity)


def _allow_single_pair_diagnostic(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    return _allow_single_pair_diagnostic_impl(protocol, opportunity)


def _symbol_aliases(symbol: str) -> set[str]:
    return _symbol_aliases_impl(symbol)


def _cache_entry_symbols(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    return _cache_entry_symbols_impl(entry)


def _cache_entry_tokens(entry: dict[str, Any]) -> tuple[str, str]:
    return _cache_entry_tokens_impl(entry)


def _cache_pool_candidates_for_pair(cache: dict[str, Any], x_symbol: str, y_symbol: str) -> tuple[str, str, list[dict[str, Any]]]:
    return _cache_pool_candidates_for_pair_impl(cache, x_symbol, y_symbol)


def _float_or_none(value: Any) -> float | None:
    return _float_or_none_impl(value)


def _runtime_strategy_from_env() -> str:
    return _runtime_strategy_from_env_impl()


def _trade_key(token_x: str, token_y: str) -> tuple[str, str]:
    return _trade_key_impl(token_x, token_y)


def _runtime_trade_with_metadata(
    *,
    trade_index: int,
    token_x: str,
    token_y: str,
    pools: list[dict[str, Any]],
    strategy_status: int,
    strategy_stage: str,
    route_symbols: list[str],
    route_key: str = "",
    spread: float = 0.0,
) -> dict[str, Any]:
    return _runtime_trade_with_metadata_impl(
        trade_index=trade_index,
        token_x=token_x,
        token_y=token_y,
        pools=pools,
        strategy_status=strategy_status,
        strategy_stage=strategy_stage,
        route_symbols=route_symbols,
        route_key=route_key,
        spread=spread,
    )


def _runtime_trade_status(trade: dict[str, Any] | None) -> int | None:
    return _runtime_trade_status_impl(trade)


def _runtime_trade_route_key(trade: dict[str, Any] | None) -> str:
    return _runtime_trade_route_key_impl(trade)


def _ordered_runtime_trade_plan(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _ordered_runtime_trade_plan_impl(
        trades,
        usdc_address=usdc_address,
        runtime_trade_route_key=_runtime_trade_route_key,
        runtime_trade_status=_runtime_trade_status,
        max_unified_route_trade_inputs=MAX_UNIFIED_ROUTE_TRADE_INPUTS,
        max_runtime_trade_scan=MAX_RUNTIME_TRADE_SCAN,
    )


def _runtime_trades_from_market_state(
    market_state: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
    side_limit: int = RUNTIME_TOP_BOTTOM_LIMIT,
    trade_limit: int | None = None,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    return _runtime_trades_from_market_state_impl(
        market_state,
        cache=cache,
        side_limit=side_limit,
        trade_limit=trade_limit,
        include_stable_targets=include_stable_targets,
    )


def _cache_usdc_supported_symbols(cache: dict[str, Any]) -> list[str]:
    return _cache_usdc_supported_symbols_impl(cache)


def _market_change_by_symbol(market_state: dict[str, Any]) -> dict[str, float]:
    return _market_change_by_symbol_impl(market_state)


def _runtime_route_groups_from_market_state(
    market_state: dict[str, Any] | None,
    *,
    cache: dict[str, Any] | None = None,
    group_limit: int | None = None,
    include_single_pair_diagnostic: bool = False,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    return _runtime_route_groups_from_market_state_impl(
        market_state,
        cache=cache,
        group_limit=group_limit,
        include_single_pair_diagnostic=include_single_pair_diagnostic,
        include_stable_targets=include_stable_targets,
    )


def _runtime_route_group_limit(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
) -> int:
    return _runtime_route_group_limit_impl(protocol, opportunity)


def _runtime_route_group_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _runtime_route_group_candidates_impl(protocol, opportunity)


def _market_state_from_context(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, Any] | None:
    return _market_state_from_context_impl(protocol, opportunity)


def _runtime_trade_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
    *,
    limit: int = DEFAULT_RUNTIME_TRADE_SCAN,
) -> list[dict[str, Any]]:
    return _runtime_trade_candidates_impl(protocol, opportunity, limit=limit)


def _runtime_trade_decision_report(result: Any) -> dict[str, Any]:
    return _runtime_trade_decision_report_impl(result)


def _runtime_execution_preview_report(result: Any) -> dict[str, Any]:
    return _runtime_execution_preview_report_impl(result)


def _runtime_hop_preview_report(result: Any) -> dict[str, Any]:
    return _runtime_hop_preview_report_impl(result)


def _runtime_order_preview_report(result: Any) -> dict[str, Any]:
    return _runtime_order_preview_report_impl(result)


def _runtime_run_result_report(result: Any) -> dict[str, Any]:
    return _runtime_run_result_report_impl(result)


def _bytes_to_hex(value: Any) -> str:
    return _bytes_to_hex_impl(value)


def _runtime_trade_plan_report(trades: list[dict[str, Any]]) -> list[dict[str, str]]:
    return _runtime_trade_plan_report_impl(trades)


def _runtime_trade_metadata_by_index(trades: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return _runtime_trade_metadata_by_index_impl(trades)


def _selected_strategy_status(
    selected_trade: dict[str, Any] | None,
    *,
    execution_mode: str,
    execution_kind: int | None = None,
) -> int:
    return _selected_strategy_status_impl(
        selected_trade,
        execution_mode=execution_mode,
        execution_kind=execution_kind,
        status_translation=_SELECTED_STRATEGY_STATUS_TRANSLATION,
    )


def _runtime_trade_abi_arg(trade: dict[str, Any], Web3: Any) -> dict[str, Any]:
    return _runtime_trade_abi_arg_impl(trade, Web3)


def _triangular_trade_allowed(trade: dict[str, Any], usdc_address: str) -> bool:
    return _triangular_trade_allowed_impl(trade, usdc_address)


def _non_usdc_cross_pool_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    return _non_usdc_cross_pool_enabled_impl(protocol, opportunity)


def _cross_pool_trade_allowed(
    trade: dict[str, Any],
    *,
    usdc_address: str,
    borrowable_addresses: set[str],
    allow_non_usdc: bool,
) -> bool:
    return _cross_pool_trade_allowed_impl(
        trade,
        usdc_address=usdc_address,
        borrowable_addresses=borrowable_addresses,
        allow_non_usdc=allow_non_usdc,
    )


def _rank_runtime_trades_by_profit(
    candidate_trade_args: list[dict[str, Any]],
    preview_trade: Callable[[dict[str, Any]], Any],
    *,
    limit: int = DEFAULT_RUNTIME_TRADE_SCAN,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    return _rank_runtime_trades_by_profit_impl(candidate_trade_args, preview_trade, limit=limit)


def runtimeFailureReason(code: int) -> str:
    return _runtime_failure_reason_impl(code)


def pair_index_from_tokenx_tokeny(
    token_x: Any,
    token_y: Any,
    pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    router: Any = None,
) -> int | None:
    return _pair_index_from_tokenx_tokeny_impl(token_x, token_y, pairs, router=router)


def _first_value(*objects: Any, names: tuple[str, ...]) -> Any:
    return _first_value_impl(*objects, names=names)


def _direct_execution_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    return _direct_execution_enabled_impl(protocol, opportunity)


def _direct_execution_mode(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> str:
    return _direct_execution_mode_impl(protocol, opportunity)


def _runtime_execution_params(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, int]:
    return _runtime_execution_params_impl(protocol, opportunity)


def _runtime_cross_pool_execution_params(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
    triangular_params: dict[str, int] | None = None,
) -> dict[str, int]:
    return _runtime_cross_pool_execution_params_impl(protocol, opportunity, triangular_params)


def _route_symbols_match(route_symbols: Any, route_path: list[Any]) -> bool:
    return _route_symbols_match_impl(route_symbols, route_path)


def _runtime_group_intent_score(group: dict[str, Any], route_path: list[Any]) -> int:
    return _runtime_group_intent_score_impl(group, route_path)


def _select_runtime_group_for_intent(
    route_groups: list[dict[str, Any]],
    route_path: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    return _select_runtime_group_for_intent_impl(route_groups, route_path)


def _pair_tokens_from_path(path: Any) -> tuple[Any, Any]:
    return _pair_tokens_from_path_impl(path)


def _resolve_pair_id(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> int | None:
    return _resolve_pair_id_impl(protocol, opportunity)


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
