from __future__ import annotations

import os
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text
from execution.gas_estimator import build_gas_params, estimate_gas_price
from execution.private_tx import send_raw_transaction_private_first
from intent_trade.builder import (
    DEFAULT_INTENT_BORROW_SYMBOL,
    build_cow_intent_trade,
)
from intent_trade.direct_utils import (
    _bool_value,
    _execution_failure_report,
    _raw_signed_transaction,
    _route_decision_report,
)


_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$", re.IGNORECASE)
_USDC_PAIR_MEMORY_TABLE: list[dict[str, str]] = []
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
SRC_ROOT = Path(__file__).resolve().parents[1]
AVALANCHE_USDC_ADDRESS = "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"
FUJI_USDC_ADDRESS = "0x5425890298aed601595a70ab815c96711a31bc65"
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
    "avalanche": {"chain_id": 43114, "testnet": False, "rpc_env": ("AVALANCHE_RPC_URL", "AVALANCHE_RPC")},
    "fuji": {"chain_id": 43113, "testnet": True, "rpc_env": ("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL")},
}
DIRECT_ONCHAIN_NETWORK_ALIASES = {
    "avax": "avalanche",
    "avalanche-c": "avalanche",
    "avalanche-c-chain": "avalanche",
    "43114": "avalanche",
    "avalanche-fuji": "fuji",
    "fuji": "fuji",
    "43113": "fuji",
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

_UNIFIED_EXECUTOR_ABI_CACHE: list[dict[str, Any]] | None = None


def _unified_executor_artifact_path() -> Path:
    configured = _env(
        "UNIFIED_EXECUTOR_ARTIFACT",
        "TRIANGULAR_UNIFIED_EXECUTOR_ARTIFACT",
        default="",
    )
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else SRC_ROOT / path
    return (
        SRC_ROOT.parents[1]
        / "contract"
        / "contracts-dex"
        / "artifacts"
        / "src"
        / "UnifiedFlashLoanMevExecutor.sol"
        / "UnifiedFlashLoanMevExecutor.json"
    )


def _load_unified_executor_abi() -> list[dict[str, Any]]:
    global _UNIFIED_EXECUTOR_ABI_CACHE
    if _UNIFIED_EXECUTOR_ABI_CACHE is not None:
        return _UNIFIED_EXECUTOR_ABI_CACHE
    path = _unified_executor_artifact_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        abi = payload.get("abi")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"UnifiedFlashLoanMevExecutor ABI artifact is unavailable: {path}") from exc
    if not isinstance(abi, list) or not abi:
        raise RuntimeError(f"UnifiedFlashLoanMevExecutor ABI artifact is invalid: {path}")
    _UNIFIED_EXECUTOR_ABI_CACHE = abi
    return abi

def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "0x...":
            return value
    return default


def _env_bool(*names: str) -> bool:
    for name in names:
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return False


def _max_broadcast_gas_price_wei() -> int:
    return _positive_int_value(
        _env(
            "TRIANGULAR_MAX_GAS_PRICE_WEI",
            "UNIFIED_EXECUTOR_MAX_GAS_PRICE_WEI",
            default="0",
        )
    )


def _broadcast_gas_guard(gas_units: Any, gas_estimate: Any) -> dict[str, Any]:
    gas = _positive_int_value(gas_units)
    max_fee = _positive_int_value(getattr(gas_estimate, "max_fee", 0))
    strategy = str(getattr(gas_estimate, "strategy", "") or "")
    cap = _max_broadcast_gas_price_wei()
    report = {
        "gasUnits": str(gas),
        "gasPriceWei": str(max_fee),
        "estimatedCostWei": str(gas * max_fee),
        "strategy": strategy,
        "configuredMaxGasPriceWei": str(cap),
    }
    if strategy == "blocked" or max_fee == 0:
        return {
            "ok": False,
            "status": "gas_price_unavailable",
            "blocked_reason": "gas_price_unavailable",
            "report": report,
        }
    if cap and max_fee > cap:
        return {
            "ok": False,
            "status": "gas_price_cap_exceeded",
            "blocked_reason": "gas_price_cap_exceeded",
            "report": report,
        }
    return {
        "ok": True,
        "status": "gas_price_accepted",
        "blocked_reason": None,
        "report": report,
    }


def _normalize_direct_onchain_network(value: Any) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    if not key:
        return ""
    return DIRECT_ONCHAIN_NETWORK_ALIASES.get(key, key)


def _chain_id_value(value: Any) -> int | None:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def _resolve_direct_onchain_network(protocol: dict[str, Any] | None = None) -> tuple[str, int, bool]:
    protocol = protocol if isinstance(protocol, dict) else {}
    explicit_chain_id = _chain_id_value(
        _first_value(protocol, names=("direct_chain_id", "onchain_chain_id", "chain_id", "chainId"))
    )
    if explicit_chain_id is not None:
        by_chain = _normalize_direct_onchain_network(str(explicit_chain_id))
        if by_chain in DIRECT_ONCHAIN_NETWORKS:
            config = DIRECT_ONCHAIN_NETWORKS[by_chain]
            return by_chain, int(config["chain_id"]), bool(config["testnet"])

    configured = (
        _env("TRIANGULAR_DIRECT_NETWORK", "TRIANGULAR_ONCHAIN_NETWORK")
        or _env("TRIANGULAR_TESTNET_NAME", "TRIANGULAR_NETWORK")
        or _first_value(protocol, names=("direct_network", "onchain_network", "network"))
        or "avalanche"
    )
    network = _normalize_direct_onchain_network(configured)
    if network not in DIRECT_ONCHAIN_NETWORKS:
        supported = ", ".join(sorted(DIRECT_ONCHAIN_NETWORKS))
        raise ValueError(f"unsupported direct on-chain network: {network}; supported networks: {supported}")
    config = DIRECT_ONCHAIN_NETWORKS[network]
    return network, int(config["chain_id"]), bool(config["testnet"])


def _direct_rpc_url(network: str) -> tuple[str, tuple[str, ...]]:
    config = DIRECT_ONCHAIN_NETWORKS.get(network, DIRECT_ONCHAIN_NETWORKS["avalanche"])
    names = tuple(config["rpc_env"])
    return _env(*names), names


def _positive_int_value(value: Any, *, default: int = 0) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw, 10)
    except ValueError as exc:
        raise ValueError(f"expected a positive integer, got {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"expected a positive integer, got {parsed}")
    return parsed


def _usdc_decimal_to_base_units(value: Any) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return 0
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected a USDC decimal amount, got {value!r}") from exc
    if amount < 0:
        raise ValueError(f"expected a non-negative USDC amount, got {value!r}")
    return int((amount * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING))


def _normalize_pair_address(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() == "0x..." or not _ADDRESS_RE.match(text):
        return ""
    return "0x" + text[2:].lower()


def _parse_pair_id(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        pair_id = int(str(raw).strip(), 10)
    except ValueError as exc:
        raise ValueError(f"pair_id must be a non-negative integer, got {raw!r}") from exc
    if pair_id < 0:
        raise ValueError(f"pair_id must be a non-negative integer, got {pair_id}")
    return pair_id


def _explicit_pair_id(protocol: dict[str, Any]) -> int | None:
    raw = protocol.get("pair_id")
    if raw is None:
        raw = protocol.get("usdc_pair_id")
    return _parse_pair_id(raw)


def _env_pair_id() -> int | None:
    return _parse_pair_id(_env("TRIANGULAR_USDC_PAIR_ID", "TRIANGULAR_PAIR_ID"))


def set_usdc_pair_memory_table(pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, str]]:
    global _USDC_PAIR_MEMORY_TABLE
    _USDC_PAIR_MEMORY_TABLE = _normalize_usdc_pair_table(pairs)
    return list(_USDC_PAIR_MEMORY_TABLE)


def _normalize_usdc_pair_table(pairs: Any) -> list[dict[str, str]]:
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("USDC pair table must be a list")
    normalized = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"USDC pair table entry {index} must be an object")
        token_x = _normalize_pair_address(pair.get("tokenX") or pair.get("token_x") or pair.get("tokex"))
        token_y = _normalize_pair_address(pair.get("tokenY") or pair.get("token_y") or pair.get("tokey"))
        router = _normalize_pair_address(pair.get("router"))
        if not token_x:
            raise ValueError(f"USDC pair table entry {index} tokenX must be a valid address")
        if not token_y:
            raise ValueError(f"USDC pair table entry {index} tokenY must be a valid address")
        if token_x == token_y:
            raise ValueError(f"USDC pair table entry {index} tokenX and tokenY must be different")
        entry = {"tokenX": token_x, "tokenY": token_y}
        if router:
            entry["router"] = router
        normalized.append(entry)
    return normalized


def _env_usdc_pair_table() -> list[dict[str, str]]:
    value = _env("TRIANGULAR_USDC_PAIRS_JSON")
    if value:
        return _normalize_usdc_pair_table(json.loads(value))
    token_x = _env("TRIANGULAR_TOKEN_X")
    token_y = _env("TRIANGULAR_TOKEN_Y")
    router = _env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    if token_x and token_y:
        return _normalize_usdc_pair_table([{"tokenX": token_x, "tokenY": token_y, "router": router}])
    return []


def local_usdc_pair_memory_table(protocol: dict[str, Any] | None = None) -> list[dict[str, str]]:
    if protocol and isinstance(protocol.get("usdc_pairs"), list):
        return _normalize_usdc_pair_table(protocol["usdc_pairs"])
    if _USDC_PAIR_MEMORY_TABLE:
        return list(_USDC_PAIR_MEMORY_TABLE)
    return _env_usdc_pair_table()


def _runtime_candidate_pairs(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> list[dict[str, str]]:
    pairs_source = None
    for source in (protocol, opportunity):
        if not isinstance(source, dict):
            continue
        for key in ("candidate_pairs", "usdc_candidate_pairs", "usdc_pairs"):
            if isinstance(source.get(key), list):
                pairs_source = source[key]
                break
        if pairs_source is not None:
            break
    if pairs_source is None:
        return []

    default_router = _normalize_pair_address(
        _first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
        or _env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    )
    candidates = []
    for index, pair in enumerate(_normalize_usdc_pair_table(pairs_source)):
        router = _normalize_pair_address(pair.get("router")) or default_router
        if not router:
            raise ValueError(f"runtime candidate pair {index} router must be a valid address")
        candidates.append({"tokenX": pair["tokenX"], "tokenY": pair["tokenY"], "router": router})
    return candidates


def _normalize_runtime_pool(pool: Any) -> dict[str, Any]:
    if not isinstance(pool, dict):
        raise ValueError("runtime pool must be an object")
    adapter_kind = int(pool.get("adapterKind", pool.get("adapter_kind", 0)) or 0)
    pool_address = _normalize_pair_address(pool.get("pool"))
    return {"adapterKind": adapter_kind, "pool": pool_address}


def _normalize_runtime_trade(trade: Any, trade_index: int) -> dict[str, Any]:
    if not isinstance(trade, dict):
        raise ValueError("runtime trade must be an object")
    token_x = _normalize_pair_address(trade.get("tokenX") or trade.get("token_x"))
    token_y = _normalize_pair_address(trade.get("tokenY") or trade.get("token_y"))
    if not token_x or not token_y:
        raise ValueError(f"runtime trade {trade_index} tokenX/tokenY must be valid addresses")
    pools = trade.get("pools") or trade.get("candidatePools") or trade.get("candidate_pools")
    if not isinstance(pools, list):
        raise ValueError(f"runtime trade {trade_index} pools must be a list")
    normalized_pools = [{"adapterKind": 0, "pool": ""} for _ in range(MAX_RUNTIME_POOL_SCAN)]
    for index, pool in enumerate(pools[:MAX_RUNTIME_POOL_SCAN]):
        normalized_pools[index] = _normalize_runtime_pool(pool)
    normalized = {
        "tradeIndex": int(trade.get("tradeIndex", trade.get("trade_index", trade_index))),
        "tokenX": token_x,
        "tokenY": token_y,
        "pools": normalized_pools,
    }
    for key in (
        "strategyStatus",
        "strategy_status",
        "strategyStage",
        "strategy_stage",
        "routeSymbols",
        "route_symbols",
        "routeKey",
        "route_key",
        "spreadScore",
    ):
        if key in trade:
            normalized[key] = trade[key]
    return normalized


def _runtime_pool_cache_path() -> Path:
    raw = _env(
        "TRIANGULAR_RUNTIME_POOL_CACHE_FILE",
        "PINAX_POOL_DISCOVERY_CACHE_FILE",
        default="runtime/cache/avalanche_v3_pools.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def _load_runtime_pool_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or _runtime_pool_cache_path()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _aave_reserve_cache_path() -> Path:
    raw = _env("AAVE_RESERVE_CACHE_FILE", default="runtime/cache/aave_reserve_assets.json")
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def _direct_circuit_breaker_path() -> Path:
    raw = _env(
        "DIRECT_ONCHAIN_CIRCUIT_BREAKER_FILE",
        "UNIFIED_EXECUTOR_CIRCUIT_BREAKER_FILE",
        default="runtime/cache/direct_onchain_circuit_breaker.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def _load_aave_reserve_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or _aave_reserve_cache_path()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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
    if not _direct_circuit_enabled():
        return {"enabled": False, "paused": False, "level": "green"}
    state_path = path or _direct_circuit_breaker_path()
    payload = _read_json_file(state_path)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    key = _direct_circuit_group_key(network, route_key)
    group = groups.get(key) if isinstance(groups.get(key), dict) else {}
    paused_until = _parse_utc_datetime(group.get("pausedUntil"))
    current = now or _utc_now()
    paused = bool(paused_until and paused_until > current)
    return {
        "enabled": True,
        "paused": paused,
        "level": str(group.get("level") or "green"),
        "reason": str(group.get("reason") or ""),
        "groupKey": key,
        "pausedUntil": paused_until.isoformat() if paused_until else "",
        "state": group,
        "file": str(state_path),
    }


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
    if not _direct_circuit_enabled():
        return {"enabled": False, "recorded": False}
    state_path = path or _direct_circuit_breaker_path()
    payload = _read_json_file(state_path)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    key = _direct_circuit_group_key(network, route_key)
    group = groups.get(key) if isinstance(groups.get(key), dict) else {}
    thresholds = _direct_circuit_thresholds()
    current = now or _utc_now()
    ok = bool(result.get("ok"))
    status = str(result.get("status") or "")

    if ok:
        group = {
            "level": "green",
            "reason": "last_result_success",
            "offchainFailures": 0,
            "onchainFailures": 0,
            "updatedAt": current.isoformat(),
            "lastSuccessAt": current.isoformat(),
        }
    else:
        bucket = _direct_failure_bucket(status, bool(result.get("submitted")))
        offchain_failures = _positive_int_value(group.get("offchainFailures"))
        onchain_failures = _positive_int_value(group.get("onchainFailures"))
        if bucket == "onchain":
            onchain_failures += 1
        elif bucket == "offchain":
            offchain_failures += 1

        loss_usdc = _direct_failure_loss_usdc(result)
        level = "green"
        reason = status or "direct_onchain_failure"
        paused_until = ""
        if loss_usdc >= thresholds["redLossUsdc"] > 0:
            level = "red"
            reason = "red_large_loss"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        elif onchain_failures >= thresholds["onchain"]:
            level = "orange"
            reason = "orange_onchain_failure_threshold"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        elif offchain_failures >= thresholds["offchain"]:
            level = "yellow"
            reason = "yellow_offchain_failure_threshold"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        group = {
            **group,
            "level": level,
            "reason": reason,
            "lastStatus": status,
            "lastFailureBucket": bucket,
            "lastFailureAt": current.isoformat(),
            "updatedAt": current.isoformat(),
            "offchainFailures": offchain_failures,
            "onchainFailures": onchain_failures,
            "lastEstimatedLossUsdc": str(loss_usdc),
        }
        if paused_until:
            group["pausedUntil"] = paused_until

    groups[key] = group
    payload = {"schemaVersion": 1, "updatedAt": current.isoformat(), "groups": groups}
    _write_json_file(state_path, payload)
    return {"enabled": True, "recorded": True, "groupKey": key, "state": group, "file": str(state_path)}


def _with_direct_circuit_record(
    result: dict[str, Any],
    *,
    network: str,
    route_key: Any = "",
) -> dict[str, Any]:
    circuit = _record_direct_circuit_result(result, network=network, route_key=route_key)
    if circuit.get("recorded"):
        result["circuitBreaker"] = circuit
    return result


def _cache_observed_block(cache: dict[str, Any]) -> int:
    for key in (
        "block_number",
        "blockNumber",
        "observed_block",
        "observedBlock",
        "latest_block",
        "latestBlock",
    ):
        try:
            value = _positive_int_value(cache.get(key))
        except ValueError:
            value = 0
        if value:
            return value
    return 0


def _max_runtime_cache_age_blocks(protocol: dict[str, Any] | None = None) -> int:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        names=("max_cache_age_blocks", "maxCacheAgeBlocks", "runtime_cache_max_age_blocks"),
    )
    if raw in (None, ""):
        raw = _env("TRIANGULAR_RUNTIME_CACHE_MAX_AGE_BLOCKS", "UNIFIED_EXECUTOR_CACHE_MAX_AGE_BLOCKS")
    try:
        return _positive_int_value(raw, default=DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS)
    except ValueError:
        return DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS


def _runtime_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
    required_factory: str = "",
) -> dict[str, Any]:
    if not isinstance(cache, dict) or not cache:
        return {"ok": False, "reason": "cache_missing"}
    normalized_network = _normalize_direct_onchain_network(network)
    expected_chain_id = DIRECT_ONCHAIN_NETWORKS.get(normalized_network, {}).get("chain_id")
    cache_chain_id = cache.get("chain_id", cache.get("chainId"))
    if expected_chain_id is not None:
        try:
            if int(cache_chain_id) != int(expected_chain_id):
                return {
                    "ok": False,
                    "reason": "cache_chain_id_mismatch",
                    "expectedChainId": str(expected_chain_id),
                    "cacheChainId": str(cache_chain_id),
                }
        except (TypeError, ValueError):
            return {
                "ok": False,
                "reason": "cache_chain_id_missing",
                "expectedChainId": str(expected_chain_id),
            }
    observed_block = _cache_observed_block(cache)
    max_age = DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS if max_age_blocks is None else int(max_age_blocks)
    if current_block is not None and max_age > 0:
        if observed_block <= 0:
            return {"ok": False, "reason": "cache_block_missing", "currentBlock": str(current_block)}
        age = int(current_block) - observed_block
        if age < 0:
            return {
                "ok": False,
                "reason": "cache_block_ahead",
                "currentBlock": str(current_block),
                "cacheBlock": str(observed_block),
            }
        if age > max_age:
            return {
                "ok": False,
                "reason": "cache_stale",
                "currentBlock": str(current_block),
                "cacheBlock": str(observed_block),
                "maxAgeBlocks": str(max_age),
                "ageBlocks": str(age),
            }
    expected_factory = _normalize_pair_address(required_factory)
    if expected_factory:
        cache_factory = _normalize_pair_address(cache.get("factory") or cache.get("factory_address"))
        if cache_factory and cache_factory != expected_factory:
            return {
                "ok": False,
                "reason": "cache_factory_mismatch",
                "expectedFactory": expected_factory,
                "cacheFactory": cache_factory,
            }
        checked = 0
        mismatches: list[str] = []
        for entry in cache.get("pools") or []:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
            for pool in nested:
                if not isinstance(pool, dict):
                    continue
                pool_address = _normalize_pair_address(pool.get("pool") or pool.get("pool_address") or pool.get("address"))
                entry_factory = _normalize_pair_address(
                    pool.get("factory")
                    or pool.get("factory_address")
                    or entry.get("factory")
                    or entry.get("factory_address")
                )
                if not pool_address and not entry_factory:
                    continue
                checked += 1
                if entry_factory and entry_factory != expected_factory:
                    mismatches.append(pool_address or f"entry:{checked}")
                    if len(mismatches) >= 3:
                        break
            if len(mismatches) >= 3:
                break
        if mismatches:
            return {
                "ok": False,
                "reason": "cache_factory_mismatch",
                "expectedFactory": expected_factory,
                "samplePools": mismatches,
            }
    return {
        "ok": True,
        "reason": "cache_verified",
        "cacheBlock": str(observed_block),
        "maxAgeBlocks": str(max_age),
        "currentBlock": str(current_block or ""),
        "factoryCheckedCount": str(checked) if expected_factory else "0",
    }


def _unified_required_factory(protocol: dict[str, Any] | None, cache: dict[str, Any] | None = None) -> str:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        names=("factory", "factory_address", "v3_factory", "v3Factory", "unified_v3_factory"),
    )
    if raw in (None, ""):
        raw = _env("UNIFIED_V3_FACTORY", "TRIANGULAR_V3_FACTORY")
    if raw in (None, "") and isinstance(cache, dict):
        raw = cache.get("factory") or cache.get("factory_address")
    return _normalize_pair_address(raw)


def _reserve_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
) -> dict[str, Any]:
    result = _runtime_cache_validation(
        cache,
        network=network,
        current_block=current_block,
        max_age_blocks=max_age_blocks,
    )
    if not result.get("ok"):
        result["cacheKind"] = "aave_reserve"
        return result
    assets = _aave_reserve_assets(cache)
    if not assets:
        return {"ok": False, "reason": "reserve_cache_assets_missing", "cacheKind": "aave_reserve"}
    result["cacheKind"] = "aave_reserve"
    result["assetCount"] = str(len(assets))
    return result


def _public_fallback_enabled(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_public_fallback", "allowPublicFallback", "public_fallback_enabled", "publicFallbackEnabled"),
    )
    if raw is not None:
        return _bool_value(raw)
    return _env_bool(
        "UNIFIED_EXECUTOR_ALLOW_PUBLIC_FALLBACK",
        "TRIANGULAR_ALLOW_PUBLIC_FALLBACK",
    )


def _relay_cost_wei(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("relay_cost_wei", "relayCostWei"),
    )
    if raw in (None, ""):
        raw = _env("UNIFIED_EXECUTOR_RELAY_COST_WEI", "TRIANGULAR_RELAY_COST_WEI")
    try:
        return _positive_int_value(raw)
    except ValueError:
        return 0


def _cache_risk_penalty_usdc_base_units(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    cache_reports: dict[str, dict[str, Any]] | None = None,
) -> int:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("cache_risk_penalty_usdc", "cacheRiskPenaltyUsdc"),
    )
    if raw in (None, ""):
        raw = _env("UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_USDC", "TRIANGULAR_CACHE_RISK_PENALTY_USDC")
    try:
        static_penalty = _usdc_decimal_to_base_units(raw)
    except ValueError:
        static_penalty = 0
    per_block = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("cache_risk_penalty_per_block_usdc", "cacheRiskPenaltyPerBlockUsdc"),
    )
    if per_block in (None, ""):
        per_block = _env(
            "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
            "TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        )
    try:
        per_block_penalty = _usdc_decimal_to_base_units(per_block)
    except ValueError:
        per_block_penalty = 0
    age_blocks = 0
    for report in (cache_reports or {}).values():
        if not isinstance(report, dict) or not report.get("ok"):
            continue
        try:
            age = _positive_int_value(report.get("ageBlocks"))
        except ValueError:
            age = 0
        if age == 0:
            try:
                current = _positive_int_value(report.get("currentBlock"))
                cache_block = _positive_int_value(report.get("cacheBlock"))
                age = max(0, current - cache_block) if current and cache_block else 0
            except ValueError:
                age = 0
        age_blocks = max(age_blocks, age)
    dynamic_penalty = per_block_penalty * age_blocks
    return static_penalty + dynamic_penalty


def _gas_token_price_max_age_seconds(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("gas_token_price_max_age_seconds", "gasTokenPriceMaxAgeSeconds", "avax_usdc_price_max_age_seconds"),
    )
    if raw in (None, ""):
        raw = _env("AVAX_USDC_PRICE_MAX_AGE_SECONDS", "GAS_TOKEN_USDC_PRICE_MAX_AGE_SECONDS")
    try:
        return _positive_int_value(raw, default=DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS)
    except ValueError:
        return DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS


def _gas_token_price_updated_at(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> datetime | None:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=(
            "avax_usdc_price_updated_at",
            "avaxUsdcPriceUpdatedAt",
            "gas_token_usdc_price_updated_at",
            "gasTokenUsdcPriceUpdatedAt",
        ),
    )
    if raw in (None, ""):
        raw = _env("AVAX_USDC_PRICE_UPDATED_AT", "GAS_TOKEN_USDC_PRICE_UPDATED_AT")
    if raw in (None, ""):
        return None
    try:
        numeric = Decimal(str(raw))
        if numeric > 0:
            return datetime.fromtimestamp(float(numeric), tz=timezone.utc)
    except (InvalidOperation, ValueError, OSError, OverflowError):
        pass
    return _parse_utc_datetime(raw)


def _avax_usdc_price_micro(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> int:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("avax_usdc_price", "avaxUsdcPrice", "gas_token_usdc_price", "gasTokenUsdcPrice"),
    )
    if raw in (None, ""):
        raw = _env("AVAX_USDC_PRICE", "GAS_TOKEN_USDC_PRICE")
    try:
        return _usdc_decimal_to_base_units(raw)
    except ValueError:
        return 0


def _gas_token_usdc_price_report(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    price = _avax_usdc_price_micro(protocol, opportunity)
    updated_at = _gas_token_price_updated_at(protocol, opportunity)
    max_age = _gas_token_price_max_age_seconds(protocol, opportunity)
    current = now or _utc_now()
    report: dict[str, Any] = {
        "ok": False,
        "priceMicro": str(price),
        "updatedAt": updated_at.isoformat() if updated_at else "",
        "maxAgeSeconds": str(max_age),
    }
    if price <= 0:
        report["reason"] = "gas_token_usdc_price_missing"
        return report
    if updated_at is None:
        report["reason"] = "gas_token_usdc_price_timestamp_missing"
        return report
    age = max(0, int((current - updated_at).total_seconds()))
    report["ageSeconds"] = str(age)
    if max_age > 0 and age > max_age:
        report["reason"] = "gas_token_usdc_price_stale"
        return report
    report["ok"] = True
    report["reason"] = "gas_token_usdc_price_fresh"
    return report


def _wei_cost_to_usdc_base_units(cost_wei: int, avax_usdc_price_micro: int) -> int:
    if cost_wei <= 0 or avax_usdc_price_micro <= 0:
        return 0
    return (int(cost_wei) * int(avax_usdc_price_micro) + 10**18 - 1) // 10**18


def _unified_net_profit_report(
    *,
    expected_profit: int,
    gas_units: int,
    gas_price_wei: int,
    relay_cost_wei: int,
    cache_risk_penalty_usdc: int,
    avax_usdc_price_micro: int,
) -> dict[str, str]:
    gas_cost_wei = max(0, int(gas_units)) * max(0, int(gas_price_wei))
    relay_cost_usdc = _wei_cost_to_usdc_base_units(max(0, int(relay_cost_wei)), avax_usdc_price_micro)
    gas_cost_usdc = _wei_cost_to_usdc_base_units(gas_cost_wei, avax_usdc_price_micro)
    net_profit = int(expected_profit) - gas_cost_usdc - relay_cost_usdc - max(0, int(cache_risk_penalty_usdc))
    return {
        "expectedProfit": str(expected_profit),
        "gasUnits": str(gas_units),
        "gasPriceWei": str(gas_price_wei),
        "gasCostWei": str(gas_cost_wei),
        "gasCostUsdc": str(gas_cost_usdc),
        "relayCostWei": str(relay_cost_wei),
        "relayCostUsdc": str(relay_cost_usdc),
        "cacheRiskPenaltyUsdc": str(max(0, int(cache_risk_penalty_usdc))),
        "avaxUsdcPriceMicro": str(avax_usdc_price_micro),
        "netProfit": str(net_profit),
    }


def _usdc_address_for_network(network: str | None = None, cache: dict[str, Any] | None = None) -> str:
    explicit = _normalize_pair_address(
        _env("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS")
    )
    if explicit:
        return explicit
    for entry in (cache or {}).get("pools") or []:
        if not isinstance(entry, dict):
            continue
        left_symbols, right_symbols = _cache_entry_symbols(entry)
        entry_x, entry_y = _cache_entry_tokens(entry)
        if "USDC" in left_symbols and entry_x:
            return entry_x
        if "USDC" in right_symbols and entry_y:
            return entry_y
    normalized_network = _normalize_direct_onchain_network(network)
    if normalized_network == "fuji":
        return FUJI_USDC_ADDRESS
    return AVALANCHE_USDC_ADDRESS


def _aave_reserve_assets(cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = cache if isinstance(cache, dict) else _load_aave_reserve_cache()
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else payload.get("selected")
    return list(assets or []) if isinstance(assets, list) else []


def _aave_borrowable_token_addresses(
    cache: dict[str, Any] | None = None,
    *,
    min_liquidity: int = 0,
    network: str | None = None,
) -> set[str]:
    payload = cache if isinstance(cache, dict) else _load_aave_reserve_cache()
    normalized_network = _normalize_direct_onchain_network(network)
    cache_chain_id = payload.get("chain_id")
    expected_chain_id = DIRECT_ONCHAIN_NETWORKS.get(normalized_network, {}).get("chain_id")
    if cache_chain_id is not None and expected_chain_id is not None:
        try:
            if int(cache_chain_id) != int(expected_chain_id):
                return set()
        except (TypeError, ValueError):
            return set()
    cache_rpc = str(payload.get("rpc_url") or "").lower()
    if normalized_network == "fuji" and "avax-test" not in cache_rpc:
        return set()
    if normalized_network == "avalanche" and "avax-test" in cache_rpc:
        return set()
    addresses: set[str] = set()
    for asset in _aave_reserve_assets(payload):
        if not isinstance(asset, dict):
            continue
        address = _normalize_pair_address(asset.get("token_address") or asset.get("address"))
        if not address:
            continue
        if asset.get("active") is False or asset.get("paused") is True:
            continue
        if asset.get("borrowing_enabled") is False:
            continue
        if "available_liquidity" in asset:
            raw_liquidity = asset.get("available_liquidity")
        elif "reserve_data_liquidity" in asset:
            raw_liquidity = asset.get("reserve_data_liquidity")
        else:
            raw_liquidity = asset.get("a_token_total_supply") or 0
        liquidity = _positive_int_value(raw_liquidity)
        if min_liquidity and liquidity < min_liquidity:
            continue
        addresses.add(address)
    return addresses


def _base_symbol_from_row(row: Any) -> str:
    if isinstance(row, str):
        value = row.strip().upper()
        if not value:
            return ""
        for suffix in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD"):
            if value.endswith(suffix) and len(value) > len(suffix):
                return value[: -len(suffix)]
        return value
    if not isinstance(row, dict):
        return ""
    for key in ("base_symbol", "token_symbol", "symbol"):
        value = str(row.get(key) or "").strip().upper()
        if value:
            for suffix in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD"):
                if value.endswith(suffix) and len(value) > len(suffix):
                    return value[: -len(suffix)]
            return value
    return ""


def _normalized_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_stable_exercise_symbol(value: Any) -> bool:
    return _normalized_symbol(value) in STABLE_EXERCISE_SYMBOLS


def _allow_stable_exercise_targets(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_stable_exercise_targets", "allowStableExerciseTargets"),
    )
    if raw is not None:
        return _bool_value(raw, False)
    return _env_bool("UNIFIED_EXECUTOR_ALLOW_STABLE_TARGETS", "TRIANGULAR_ALLOW_STABLE_TARGETS")


def _allow_single_pair_diagnostic(protocol: dict[str, Any] | None = None, opportunity: dict[str, Any] | None = None) -> bool:
    raw = _first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_single_pair_diagnostic", "allowSinglePairDiagnostic"),
    )
    if raw is not None:
        return _bool_value(raw, False)
    return _env_bool(
        "UNIFIED_EXECUTOR_ALLOW_SINGLE_PAIR_DIAGNOSTIC",
        "TRIANGULAR_ALLOW_SINGLE_PAIR_DIAGNOSTIC",
    )


def _symbol_aliases(symbol: str) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    wrapped = {
        "AVAX": {"WAVAX"},
        "WAVAX": {"AVAX"},
        "ETH": {"WETH", "WETH.E"},
        "WETH": {"ETH", "WETH.E"},
        "BTC": {"WBTC", "WBTC.E", "BTC.B"},
        "WBTC": {"BTC", "WBTC.E", "BTC.B"},
        "AAVE": {"AAVE.E"},
    }
    aliases.update(wrapped.get(value, set()))
    return {item for item in aliases if item}


def _cache_entry_symbols(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    left = entry.get("tokenX_symbol") or entry.get("token_x_symbol") or entry.get("token0_symbol") or entry.get("base_symbol")
    right = entry.get("tokenY_symbol") or entry.get("token_y_symbol") or entry.get("token1_symbol") or entry.get("quote_symbol")
    return _symbol_aliases(str(left or "")), _symbol_aliases(str(right or ""))


def _cache_entry_tokens(entry: dict[str, Any]) -> tuple[str, str]:
    token_x = _normalize_pair_address(entry.get("tokenX") or entry.get("token_x") or entry.get("token0") or entry.get("base_token"))
    token_y = _normalize_pair_address(entry.get("tokenY") or entry.get("token_y") or entry.get("token1") or entry.get("quote_token"))
    return token_x, token_y


def _cache_pool_candidates_for_pair(cache: dict[str, Any], x_symbol: str, y_symbol: str) -> tuple[str, str, list[dict[str, Any]]]:
    wanted_x = _symbol_aliases(x_symbol)
    wanted_y = _symbol_aliases(y_symbol)
    token_x = ""
    token_y = ""
    pools: list[dict[str, Any]] = []
    entries = cache.get("pools") if isinstance(cache.get("pools"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        left_symbols, right_symbols = _cache_entry_symbols(entry)
        entry_x, entry_y = _cache_entry_tokens(entry)
        if not entry_x or not entry_y:
            continue
        same_order = bool(wanted_x & left_symbols and wanted_y & right_symbols)
        reverse_order = bool(wanted_x & right_symbols and wanted_y & left_symbols)
        if not same_order and not reverse_order:
            continue
        token_x = entry_x if same_order else entry_y
        token_y = entry_y if same_order else entry_x
        nested = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
        for pool in nested:
            if not isinstance(pool, dict):
                continue
            adapter_kind = int(
                pool.get("adapterKind", pool.get("adapter_kind", entry.get("adapterKind", entry.get("adapter_kind", 0)))) or 0
            )
            pool_address = _normalize_pair_address(pool.get("pool") or pool.get("pool_address") or pool.get("address"))
            if adapter_kind != 1 or not pool_address:
                continue
            candidate = {"adapterKind": 1, "pool": pool_address}
            if candidate not in pools:
                pools.append(candidate)
            if len(pools) >= MAX_RUNTIME_POOL_SCAN:
                return token_x, token_y, pools
    return token_x, token_y, pools


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _runtime_strategy_from_env() -> str:
    strategy = _env("TRIANGULAR_DIRECT_CANDIDATE_STRATEGY", default="expanded").strip().lower().replace("-", "_")
    aliases = {
        "abc": "expanded",
        "abc_auto": "expanded",
        "top_bottom": "expanded",
        "xy_only": "pair_only",
        "legacy": "pair_only",
    }
    strategy = aliases.get(strategy, strategy)
    return strategy if strategy in {"expanded", "pair_only"} else "expanded"


def _trade_key(token_x: str, token_y: str) -> tuple[str, str]:
    return token_x.lower(), token_y.lower()


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
    trade = _normalize_runtime_trade(
        {
            "tradeIndex": trade_index,
            "tokenX": token_x,
            "tokenY": token_y,
            "pools": pools,
        },
        trade_index,
    )
    trade["strategyStatus"] = int(strategy_status)
    trade["strategy_status"] = int(strategy_status)
    trade["strategyStage"] = strategy_stage
    trade["strategy_stage"] = strategy_stage
    trade["routeSymbols"] = route_symbols
    trade["route_symbols"] = route_symbols
    trade["routeKey"] = route_key or f"{token_x.lower()}:{token_y.lower()}"
    trade["route_key"] = trade["routeKey"]
    trade["spreadScore"] = spread
    return trade


def _runtime_trade_status(trade: dict[str, Any] | None) -> int | None:
    if not isinstance(trade, dict):
        return None
    raw = trade.get("strategyStatus", trade.get("strategy_status"))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _runtime_trade_route_key(trade: dict[str, Any] | None) -> str:
    if not isinstance(trade, dict):
        return ""
    value = trade.get("routeKey", trade.get("route_key"))
    if value:
        return str(value).strip().lower()
    route = trade.get("routeSymbols", trade.get("route_symbols"))
    if isinstance(route, (list, tuple)) and len(route) >= 3:
        return "->".join(str(item or "").strip().upper() for item in route)
    return ""


def _ordered_runtime_trade_plan(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select one coherent route group for the unified executor's fixed slots."""
    if not trades:
        return [], {"ok": False, "reason": "runtime_trades_empty"}

    groups: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for trade in trades:
        key = _runtime_trade_route_key(trade)
        if key:
            groups.setdefault(key, []).append(trade)
        else:
            ungrouped.append(trade)

    if not groups:
        ordered = list(trades[:MAX_UNIFIED_ROUTE_TRADE_INPUTS])
        for index, trade in enumerate(ordered):
            if _runtime_trade_status(trade) is None and index < 4:
                trade["strategyStatus"] = index + 1
                trade["strategy_status"] = index + 1
        return ordered, {
            "ok": len(ordered) >= 3,
            "reason": "input_order_preserved",
            "groupKey": None,
        }

    ranked_groups: list[tuple[float, str, list[dict[str, Any]]]] = []
    for key, group in groups.items():
        by_status: dict[int, dict[str, Any]] = {}
        for trade in group:
            status = _runtime_trade_status(trade)
            if status is not None and status not in by_status:
                by_status[status] = trade
        if not by_status:
            by_status = {
                index + 1: trade
                for index, trade in enumerate(group[:MAX_RUNTIME_TRADE_SCAN])
            }
        score = max(
            float(trade.get("spreadScore") or 0.0)
            for trade in group
            if isinstance(trade, dict)
        )
        ordered: list[dict[str, Any]] = []
        if 1 in by_status:
            ordered.append(by_status[1])
        if 2 in by_status:
            ordered.append(by_status[2])
        if 3 in by_status:
            ordered.append(by_status[3])
        if 5 in by_status:
            ordered.append(by_status[5])
        if ordered:
            ranked_groups.append((score, key, ordered))

    if not ranked_groups:
        return [], {
            "ok": False,
            "reason": "no_complete_unified_route_group",
            "groupKey": None,
        }

    _, selected_key, ordered = max(ranked_groups, key=lambda item: (item[0], item[1]))
    for trade in ordered:
        trade["routeKey"] = selected_key
        trade["route_key"] = selected_key

    return ordered[:MAX_UNIFIED_ROUTE_TRADE_INPUTS], {
        "ok": True,
        "reason": "coherent_route_group_selected",
        "groupKey": selected_key,
        "strategyStatuses": [_runtime_trade_status(trade) for trade in ordered],
    }


def _runtime_trades_from_market_state(
    market_state: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
    side_limit: int = RUNTIME_TOP_BOTTOM_LIMIT,
    trade_limit: int | None = None,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(market_state, dict):
        return []
    pool_cache = cache if isinstance(cache, dict) else _load_runtime_pool_cache()
    top_rows = list(market_state.get("cow_top") or market_state.get("top") or [])[: max(1, int(side_limit))]
    bottom_rows = list(market_state.get("cow_bottom") or market_state.get("bottom") or [])[: max(1, int(side_limit))]
    network = str(market_state.get("network") or "").strip().lower()
    usdc_address = _usdc_address_for_network(network, pool_cache)
    candidates: list[tuple[float, str, list[dict[str, Any]]]] = []
    seen_route_keys: set[str] = set()
    for top in top_rows:
        x_symbol = _base_symbol_from_row(top)
        x_change = _float_or_none(top.get("change_percent")) if isinstance(top, dict) else None
        if not x_symbol or (_is_stable_exercise_symbol(x_symbol) and not include_stable_targets):
            continue
        for bottom in bottom_rows:
            y_symbol = _base_symbol_from_row(bottom)
            y_change = _float_or_none(bottom.get("change_percent")) if isinstance(bottom, dict) else None
            if (
                not y_symbol
                or x_symbol == y_symbol
                or (_is_stable_exercise_symbol(y_symbol) and not include_stable_targets)
            ):
                continue
            ux_x, ux_y, ux_pools = _cache_pool_candidates_for_pair(pool_cache, "USDC", x_symbol)
            uy_x, uy_y, uy_pools = _cache_pool_candidates_for_pair(pool_cache, "USDC", y_symbol)
            xy_x, xy_y, xy_pools = _cache_pool_candidates_for_pair(pool_cache, x_symbol, y_symbol)
            yx_x, yx_y, yx_pools = _cache_pool_candidates_for_pair(pool_cache, y_symbol, x_symbol)
            if (
                not usdc_address
                or len(ux_pools) < 1
                or len(uy_pools) < 1
                or len(xy_pools) < 1
                or not ux_x
                or not ux_y
                or not uy_x
                or not uy_y
                or not xy_x
                or not xy_y
                or not yx_x
                or not yx_y
            ):
                continue
            spread = (x_change - y_change) if x_change is not None and y_change is not None else 0.0
            route_key = f"{x_symbol.upper()}:{y_symbol.upper()}"
            if route_key in seen_route_keys:
                continue
            seen_route_keys.add(route_key)
            candidates.append(
                (
                    abs(spread),
                    route_key,
                    [
                        _runtime_trade_with_metadata(
                            trade_index=0,
                            token_x=ux_x,
                            token_y=ux_y,
                            pools=ux_pools,
                            strategy_status=1,
                            strategy_stage="status1_usdc_to_x_cross_pool",
                            route_symbols=["USDC", x_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        _runtime_trade_with_metadata(
                            trade_index=1,
                            token_x=uy_x,
                            token_y=uy_y,
                            pools=uy_pools,
                            strategy_status=2,
                            strategy_stage="status2_usdc_to_y_cross_pool",
                            route_symbols=["USDC", y_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        _runtime_trade_with_metadata(
                            trade_index=2,
                            token_x=xy_x,
                            token_y=xy_y,
                            pools=xy_pools,
                            strategy_status=3,
                            strategy_stage="status3_xy_cross_pool_or_triangular",
                            route_symbols=["USDC", x_symbol, y_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        _runtime_trade_with_metadata(
                            trade_index=3,
                            token_x=yx_x,
                            token_y=yx_y,
                            pools=yx_pools,
                            strategy_status=5,
                            strategy_stage="status5_reverse_triangular_fallback",
                            route_symbols=["USDC", y_symbol, x_symbol, "USDC"],
                            route_key=route_key,
                            spread=-spread,
                        ),
                    ],
                )
            )
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if not candidates:
        return []
    limit = DEFAULT_RUNTIME_TRADE_SCAN if trade_limit is None else int(trade_limit)
    limit = max(1, min(limit, MAX_RUNTIME_TRADE_SCAN))
    return candidates[0][2][:min(limit, MAX_UNIFIED_ROUTE_TRADE_INPUTS)]


def _cache_usdc_supported_symbols(cache: dict[str, Any]) -> list[str]:
    symbols: set[str] = set()
    entries = cache.get("pools") if isinstance(cache.get("pools"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        left = str(
            entry.get("tokenX_symbol")
            or entry.get("token_x_symbol")
            or entry.get("token0_symbol")
            or entry.get("base_symbol")
            or ""
        )
        right = str(
            entry.get("tokenY_symbol")
            or entry.get("token_y_symbol")
            or entry.get("token1_symbol")
            or entry.get("quote_symbol")
            or ""
        )
        left_symbols, right_symbols = _cache_entry_symbols(entry)
        if "USDC" in left_symbols:
            symbol = _base_symbol_from_row(right)
            if symbol and "USDC" not in _symbol_aliases(symbol):
                symbols.add(symbol)
        if "USDC" in right_symbols:
            symbol = _base_symbol_from_row(left)
            if symbol and "USDC" not in _symbol_aliases(symbol):
                symbols.add(symbol)
    return sorted(symbols)


def _market_change_by_symbol(market_state: dict[str, Any]) -> dict[str, float]:
    changes: dict[str, float] = {}
    for key in ("cow_top", "top", "cow_bottom", "bottom"):
        rows = market_state.get(key) if isinstance(market_state, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            symbol = _base_symbol_from_row(row)
            change = _float_or_none(row.get("change_percent")) if isinstance(row, dict) else None
            if symbol and change is not None:
                changes[symbol] = change
    return changes


def _runtime_route_groups_from_market_state(
    market_state: dict[str, Any] | None,
    *,
    cache: dict[str, Any] | None = None,
    group_limit: int | None = None,
    include_single_pair_diagnostic: bool = False,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    """Build direct and triangular route groups for every cache-discovered USDC token."""
    context = market_state if isinstance(market_state, dict) else {}
    pool_cache = cache if isinstance(cache, dict) else _load_runtime_pool_cache()
    usdc_address = _usdc_address_for_network(str(context.get("network") or ""), pool_cache)
    changes = _market_change_by_symbol(context)
    usdc_pairs: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}

    for symbol in _cache_usdc_supported_symbols(pool_cache):
        token_x, token_y, pools = _cache_pool_candidates_for_pair(pool_cache, "USDC", symbol)
        if (
            not token_x
            or not token_y
            or len(pools) < 1
            or usdc_address.lower() not in {token_x.lower(), token_y.lower()}
        ):
            continue
        usdc_pairs[symbol] = (token_x, token_y, pools)

    groups: list[dict[str, Any]] = []
    if include_single_pair_diagnostic:
        for symbol, (token_x, token_y, pools) in usdc_pairs.items():
            if _is_stable_exercise_symbol(symbol) and not include_stable_targets:
                continue
            score = abs(changes.get(symbol, 0.0))
            groups.append(
                {
                    "routeKey": f"USDC:{symbol}",
                    "routeKind": "usdc_cross_pool_diagnostic",
                    "exerciseTargetPolicy": "single-pair-diagnostic",
                    "score": score,
                    "trades": [
                        _runtime_trade_with_metadata(
                            trade_index=0,
                            token_x=token_x,
                            token_y=token_y,
                            pools=pools,
                            strategy_status=1,
                            strategy_stage="status1_usdc_cross_pool_diagnostic",
                            route_symbols=["USDC", symbol, "USDC"],
                            route_key=f"USDC:{symbol}",
                            spread=score,
                        )
                    ],
                }
            )

    symbols = sorted(usdc_pairs)
    for left_index, x_symbol in enumerate(symbols):
        if _is_stable_exercise_symbol(x_symbol) and not include_stable_targets:
            continue
        for y_symbol in symbols[left_index + 1 :]:
            if _is_stable_exercise_symbol(y_symbol) and not include_stable_targets:
                continue
            ux_x, ux_y, ux_pools = usdc_pairs[x_symbol]
            uy_x, uy_y, uy_pools = usdc_pairs[y_symbol]
            xy_x, xy_y, xy_pools = _cache_pool_candidates_for_pair(pool_cache, x_symbol, y_symbol)
            if not xy_x or not xy_y or len(xy_pools) < 1:
                continue

            route_key = f"{x_symbol}:{y_symbol}"
            score = abs(changes.get(x_symbol, 0.0) - changes.get(y_symbol, 0.0))
            trades = [
                _runtime_trade_with_metadata(
                    trade_index=0,
                    token_x=ux_x,
                    token_y=ux_y,
                    pools=ux_pools,
                    strategy_status=1,
                    strategy_stage="status1_usdc_to_x_cross_pool",
                    route_symbols=["USDC", x_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
                _runtime_trade_with_metadata(
                    trade_index=1,
                    token_x=uy_x,
                    token_y=uy_y,
                    pools=uy_pools,
                    strategy_status=2,
                    strategy_stage="status2_usdc_to_y_cross_pool",
                    route_symbols=["USDC", y_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
                _runtime_trade_with_metadata(
                    trade_index=2,
                    token_x=xy_x,
                    token_y=xy_y,
                    pools=xy_pools,
                    strategy_status=3,
                    strategy_stage="status3_xy_cross_pool_or_triangular",
                    route_symbols=["USDC", x_symbol, y_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
            ]
            yx_x, yx_y, yx_pools = _cache_pool_candidates_for_pair(pool_cache, y_symbol, x_symbol)
            if yx_x and yx_y and len(yx_pools) >= 1:
                trades.append(
                    _runtime_trade_with_metadata(
                        trade_index=3,
                        token_x=yx_x,
                        token_y=yx_y,
                        pools=yx_pools,
                        strategy_status=5,
                        strategy_stage="status5_reverse_triangular_fallback",
                        route_symbols=["USDC", y_symbol, x_symbol, "USDC"],
                        route_key=route_key,
                        spread=-score,
                    )
                )
            groups.append(
                {
                    "routeKey": route_key,
                    "routeKind": "usdc_triangular",
                    "exerciseTargetPolicy": (
                        "stable-target-diagnostic"
                        if _is_stable_exercise_symbol(x_symbol) or _is_stable_exercise_symbol(y_symbol)
                        else "non-stable-pair"
                    ),
                    "score": score,
                    "trades": trades,
                }
            )

    groups.sort(key=lambda item: (-float(item["score"]), item["routeKind"], item["routeKey"]))
    if group_limit is None or int(group_limit) <= 0:
        return groups
    return groups[: max(1, int(group_limit))]


def _runtime_route_group_limit(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
) -> int:
    configured = _first_value(
        protocol,
        opportunity,
        names=("runtime_route_group_limit", "route_group_scan_limit", "max_route_groups"),
    ) or _env("TRIANGULAR_RUNTIME_ROUTE_GROUP_LIMIT", default=str(DEFAULT_RUNTIME_ROUTE_GROUP_SCAN))
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = DEFAULT_RUNTIME_ROUTE_GROUP_SCAN
    if value <= 0:
        return 0
    return min(max(1, value), MAX_RUNTIME_ROUTE_GROUP_SCAN)


def _runtime_route_group_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    limit = _runtime_route_group_limit(protocol, opportunity)
    for payload in (protocol, opportunity):
        if not isinstance(payload, dict) or not isinstance(payload.get("runtime_route_groups"), list):
            continue
        groups: list[dict[str, Any]] = []
        for index, source in enumerate(payload["runtime_route_groups"]):
            trades_source = source.get("trades") if isinstance(source, dict) else source
            if not isinstance(trades_source, list):
                continue
            trades = [_normalize_runtime_trade(trade, trade_index) for trade_index, trade in enumerate(trades_source)]
            if trades:
                groups.append(
                    {
                        "routeKey": str(source.get("routeKey") or f"group:{index}") if isinstance(source, dict) else f"group:{index}",
                        "routeKind": str(source.get("routeKind") or "provided") if isinstance(source, dict) else "provided",
                        "score": float(source.get("score") or 0.0) if isinstance(source, dict) else 0.0,
                        "trades": trades,
                    }
                )
        return groups if limit == 0 else groups[:limit]

    for payload in (protocol, opportunity):
        if not isinstance(payload, dict):
            continue
        for key in ("runtime_trades", "candidate_trades", "trades"):
            if isinstance(payload.get(key), list):
                trades = [_normalize_runtime_trade(trade, index) for index, trade in enumerate(payload[key])]
                return [{"routeKey": "provided", "routeKind": "provided", "score": 0.0, "trades": trades}]

    env_value = _env("TRIANGULAR_RUNTIME_TRADES_JSON")
    if env_value:
        source = json.loads(env_value)
        if not isinstance(source, list):
            raise ValueError("runtime trades must be a list")
        return [{
            "routeKey": "environment",
            "routeKind": "provided",
            "score": 0.0,
            "trades": [_normalize_runtime_trade(trade, index) for index, trade in enumerate(source)],
        }]

    market_state = _market_state_from_context(protocol, opportunity)
    return _runtime_route_groups_from_market_state(
        market_state,
        group_limit=limit,
        include_single_pair_diagnostic=_allow_single_pair_diagnostic(protocol, opportunity),
        include_stable_targets=_allow_stable_exercise_targets(protocol, opportunity),
    )


def _market_state_from_context(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, Any] | None:
    for source in (opportunity, protocol):
        if not isinstance(source, dict):
            continue
        market_state = source.get("market_state")
        if isinstance(market_state, dict):
            return market_state
        if isinstance(source.get("top"), list) or isinstance(source.get("bottom"), list):
            return source
    return None


def _runtime_trade_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
    *,
    limit: int = DEFAULT_RUNTIME_TRADE_SCAN,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN))
    source = None
    for payload in (protocol, opportunity):
        if not isinstance(payload, dict):
            continue
        for key in ("runtime_trades", "candidate_trades", "trades"):
            if isinstance(payload.get(key), list):
                source = payload[key]
                break
        if source is not None:
            break
    if source is None:
        env_value = _env("TRIANGULAR_RUNTIME_TRADES_JSON")
        if not env_value:
            market_state = _market_state_from_context(protocol, opportunity)
            return (
                _runtime_trades_from_market_state(
                    market_state,
                    trade_limit=limit,
                    include_stable_targets=_allow_stable_exercise_targets(protocol, opportunity),
                )
                if market_state
                else []
            )
        source = json.loads(env_value)
    if not isinstance(source, list):
        raise ValueError("runtime trades must be a list")
    return [
        _normalize_runtime_trade(trade, index)
        for index, trade in enumerate(source[:limit])
    ]


def _runtime_trade_decision_report(result: Any) -> dict[str, Any]:
    if len(result) >= 17:
        failure_code = int(result[16]) if result[16] else 0
        return {
            "ok": bool(result[0]),
            "viable": bool(result[0]),
            "tradeIndex": str(result[1]),
            "tokenX": result[2],
            "tokenY": result[3],
            "lowPool": result[4],
            "highPool": result[5],
            "adapterKind": str(result[6]),
            "lowFee": str(result[7]),
            "highFee": str(result[8]),
            "lowLiquidity": str(result[9]),
            "highLiquidity": str(result[10]),
            "lowNormalizedTick": str(result[11]),
            "highNormalizedTick": str(result[12]),
            "tickDelta": str(result[13]),
            "scannedPoolCount": str(result[14]),
            "validPoolCount": str(result[15]),
            "failureCode": str(failure_code),
            "failureReason": runtimeFailureReason(failure_code),
            "abiVersion": "legacy",
        }
    failure_code = int(result[14]) if result[14] else 0
    return {
        "ok": bool(result[0]),
        "viable": bool(result[0]),
        "tradeIndex": str(result[1]),
        "tokenX": result[2],
        "tokenY": result[3],
        "lowPool": result[4],
        "highPool": result[5],
        "lowFee": str(result[6]),
        "highFee": str(result[7]),
        "lowLiquidity": str(result[8]),
        "highLiquidity": str(result[9]),
        "lowNormalizedTick": str(result[10]),
        "highNormalizedTick": str(result[11]),
        "tickDelta": str(result[12]),
        "validPoolCount": str(result[13]),
        "failureCode": str(failure_code),
        "failureReason": runtimeFailureReason(failure_code),
        "abiVersion": "unified",
    }


def _runtime_execution_preview_report(result: Any) -> dict[str, Any]:
    if len(result) < 12:
        return {
            "router": result[0],
            "swapPath": _bytes_to_hex(result[1]),
            "quotedFinal": str(result[2]),
            "premium": str(result[3]),
            "requiredFinal": str(result[4]),
            "protectedMinFinal": str(result[5]),
            "minProfit": str(result[6]),
            "quotedFinalUsdc": str(result[2]),
            "premiumUsdc": str(result[3]),
            "requiredFinalUsdc": str(result[4]),
            "protectedAmountOutMinUsdc": str(result[5]),
            "abiVersion": "legacy",
        }
    return {
        "borrowedAsset": result[0],
        "profitAsset": result[1],
        "borrowedAmount": str(result[2]),
        "routeDirection": str(result[3]),
        "failedHopIndex": str(result[4]),
        "swapPath": _bytes_to_hex(result[5]),
        "quotedFinal": str(result[6]),
        "premium": str(result[7]),
        "requiredFinal": str(result[8]),
        "protectedMinFinal": str(result[9]),
        "minProfit": str(result[10]),
        "expectedProfit": str(result[11]),
        "quotedFinalUsdc": str(result[6]),
        "premiumUsdc": str(result[7]),
        "requiredFinalUsdc": str(result[8]),
        "protectedAmountOutMinUsdc": str(result[9]),
        "abiVersion": "unified",
    }


def _runtime_hop_preview_report(result: Any) -> dict[str, Any]:
    return {
        "hopIndex": str(result[0]),
        "tokenIn": result[1],
        "tokenOut": result[2],
        "pool": result[3],
        "fee": str(result[4]),
        "amountIn": str(result[5]),
        "quotedAmountOut": str(result[6]),
        "amountOutMin": str(result[7]),
    }


def _runtime_order_preview_report(result: Any) -> dict[str, Any]:
    triangular_route = result[6]
    progress = result[7]
    return {
        "found": bool(result[0]),
        "strategyStatus": str(result[1]),
        "executionKind": str(result[2]),
        "selectedTradeArrayIndex": str(result[3]),
        "decision": _runtime_trade_decision_report(result[4]),
        "executionPreview": _runtime_execution_preview_report(result[5]),
        "triangularRoute": {
            "routeDirection": str(triangular_route[0]),
            "failedHopIndex": str(triangular_route[1]),
            "executionMode": str(triangular_route[2]),
            "hops": [_runtime_hop_preview_report(hop) for hop in triangular_route[3]],
            "quotedFinalUsdc": str(triangular_route[4]),
            "premiumUsdc": str(triangular_route[5]),
            "requiredFinalUsdc": str(triangular_route[6]),
            "expectedProfitUsdc": str(triangular_route[7]),
        },
        "progress": {
            "finalResultCode": str(progress[0]),
            "selectedStatus": str(progress[1]),
            "lastCheckedStatus": str(progress[2]),
            "attemptedStatusMask": str(progress[3]),
            "selectedStatusMask": str(progress[4]),
            "remainingStatusMask": str(progress[5]),
            "remainingStepCount": str(progress[6]),
            "steps": [
                {
                    "strategyStatus": str(step[0]),
                    "phase": str(step[1]),
                    "routeDirection": str(step[2]),
                    "failedHopIndex": str(step[3]),
                    "resultCode": str(step[4]),
                    "detailCode": str(step[5]),
                    "tradeArrayIndex": str(step[6]),
                    "tradeIndex": str(step[7]),
                    "profitAsset": step[8],
                    "expectedProfit": str(step[9]),
                    "quotedFinal": str(step[10]),
                    "requiredFinal": str(step[11]),
                }
                for step in progress[7]
            ],
        },
    }


def _runtime_run_result_report(result: Any) -> dict[str, Any]:
    return {
        "resultCode": str(result[0]),
        "strategyStatus": str(result[1]),
        "executionKind": str(result[2]),
        "routeDirection": str(result[3]),
        "selectedTradeArrayIndex": str(result[4]),
        "profitAsset": result[5],
        "profitAmount": str(result[6]),
        "profitSwept": str(result[7]),
        "attemptedStatusMask": str(result[8]),
        "remainingStatusMask": str(result[9]),
        "remainingStepCount": str(result[10]),
    }


def _bytes_to_hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if hasattr(value, "hex"):
        text = value.hex()
        return text if str(text).startswith("0x") else f"0x{text}"
    return str(value)


def _runtime_trade_plan_report(trades: list[dict[str, Any]]) -> list[dict[str, str]]:
    report = []
    for array_index, trade in enumerate(trades):
        report.append(
            {
                "arrayIndex": str(array_index),
                "tradeIndex": str(trade.get("tradeIndex", "")),
                "strategyStatus": str(trade.get("strategyStatus", trade.get("strategy_status", ""))),
                "strategyStage": str(trade.get("strategyStage", trade.get("strategy_stage", ""))),
                "routeSymbols": "->".join(trade.get("routeSymbols") or trade.get("route_symbols") or []),
                "tokenX": str(trade.get("tokenX", "")),
                "tokenY": str(trade.get("tokenY", "")),
            }
        )
    return report


def _runtime_trade_metadata_by_index(trades: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    metadata: dict[int, dict[str, Any]] = {}
    for trade in trades:
        try:
            metadata[int(trade.get("tradeIndex"))] = trade
        except (TypeError, ValueError):
            continue
    return metadata


def _selected_strategy_status(
    selected_trade: dict[str, Any] | None,
    *,
    execution_mode: str,
    execution_kind: int | None = None,
) -> int:
    if not selected_trade:
        return 55555
    raw_status = selected_trade.get("strategyStatus", selected_trade.get("strategy_status", 55555))
    try:
        strategy_status = int(raw_status)
    except (TypeError, ValueError):
        strategy_status = 55555
    if strategy_status in {1, 2, 5}:
        return strategy_status
    if strategy_status == 3:
        if execution_kind == 2:
            return 3
        if execution_kind == 1 or execution_mode == "triangular":
            return 4
        return 3
    return strategy_status if strategy_status in {4, 55555} else 55555


def _runtime_trade_abi_arg(trade: dict[str, Any], Web3: Any) -> dict[str, Any]:
    pools = []
    for pool in trade["pools"]:
        pool_address = pool["pool"] or "0x0000000000000000000000000000000000000000"
        pools.append({"adapterKind": int(pool["adapterKind"]), "pool": Web3.to_checksum_address(pool_address)})
    return {
        "tradeIndex": int(trade["tradeIndex"]),
        "tokenX": Web3.to_checksum_address(trade["tokenX"]),
        "tokenY": Web3.to_checksum_address(trade["tokenY"]),
        "pools": pools,
    }


def _triangular_trade_allowed(trade: dict[str, Any], usdc_address: str) -> bool:
    token_x = _normalize_pair_address(trade.get("tokenX"))
    token_y = _normalize_pair_address(trade.get("tokenY"))
    return bool(token_x and token_y and token_x != token_y and token_x != usdc_address and token_y != usdc_address)


def _non_usdc_cross_pool_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    configured = _first_value(
        protocol,
        opportunity,
        names=("enable_non_usdc_cross_pool", "non_usdc_cross_pool_enabled", "allow_token_x_flashloan_cross_pool"),
    )
    if configured is not None:
        return _bool_value(configured, False)
    return _env_bool("TRIANGULAR_ENABLE_NON_USDC_CROSS_POOL", "TRIANGULAR_DIRECT_ENABLE_TOKEN_X_CROSS_POOL")


def _cross_pool_trade_allowed(
    trade: dict[str, Any],
    *,
    usdc_address: str,
    borrowable_addresses: set[str],
    allow_non_usdc: bool,
) -> bool:
    token_x = _normalize_pair_address(trade.get("tokenX"))
    token_y = _normalize_pair_address(trade.get("tokenY"))
    if not token_x or not token_y or token_x == token_y:
        return False
    if token_x == usdc_address:
        return True
    if not allow_non_usdc:
        return False
    if borrowable_addresses and token_x not in borrowable_addresses:
        return False
    return True


def _rank_runtime_trades_by_profit(
    candidate_trade_args: list[dict[str, Any]],
    preview_trade: Callable[[dict[str, Any]], Any],
    *,
    limit: int = DEFAULT_RUNTIME_TRADE_SCAN,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ranked: list[tuple[int, int, dict[str, Any], dict[str, str]]] = []
    for input_index, trade in enumerate(candidate_trade_args):
        try:
            result = preview_trade(trade)
        except Exception:
            continue
        if not isinstance(result, (list, tuple)) or len(result) < 4 or not bool(result[0]):
            continue

        is_auto_preview = len(result) >= 5
        preview = _runtime_execution_preview_report(result[4] if is_auto_preview else result[3])
        quoted_final = preview.get("quotedFinalUsdc") or preview["quotedFinal"]
        required_final = preview.get("requiredFinalUsdc") or preview["requiredFinal"]
        min_profit = preview.get("minProfitUsdc") or preview["minProfit"]
        net_profit_usdc = int(quoted_final) - int(required_final) + int(min_profit)
        report = {
            "inputIndex": str(input_index),
            "tradeIndex": str(trade["tradeIndex"]),
            "quotedFinalUsdc": preview["quotedFinalUsdc"],
            "netProfitUsdc": str(net_profit_usdc),
        }
        if is_auto_preview:
            report["executionKind"] = str(result[1])
        ranked.append(
            (
                net_profit_usdc,
                input_index,
                trade,
                report,
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]))
    selected = ranked[: max(1, int(limit))]
    return [item[2] for item in selected], [item[3] for item in selected]


def runtimeFailureReason(code: int) -> str:
    return {
        0: "none",
        101: "not_enough_valid_pools",
        102: "no_price_spread",
    }.get(int(code), f"unknown_failure_{code}")


def pair_index_from_tokenx_tokeny(
    token_x: Any,
    token_y: Any,
    pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    router: Any = None,
) -> int | None:
    normalized_x = _normalize_pair_address(token_x)
    normalized_y = _normalize_pair_address(token_y)
    normalized_router = _normalize_pair_address(router)
    if not normalized_x or not normalized_y:
        return None
    table = _normalize_usdc_pair_table(pairs) if pairs is not None else local_usdc_pair_memory_table()
    for index, pair in enumerate(table):
        same_order = pair["tokenX"] == normalized_x and pair["tokenY"] == normalized_y
        reverse_order = pair["tokenX"] == normalized_y and pair["tokenY"] == normalized_x
        if not same_order and not reverse_order:
            continue
        if normalized_router and pair.get("router") and pair["router"] != normalized_router:
            continue
        return index
    return None


def _first_value(*objects: Any, names: tuple[str, ...]) -> Any:
    for source in objects:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return value
    return None


def _direct_execution_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    configured = _first_value(
        protocol,
        opportunity,
        names=("execute_runtime_trade", "execute_selected_runtime_trade", "call_executor", "execute_with_aave"),
    )
    if configured is not None:
        return _bool_value(configured, False)
    return _env_bool(
        "TRIANGULAR_DIRECT_EXECUTE_RUNTIME_TRADE",
        "TRIANGULAR_DIRECT_EXECUTOR_ENABLED",
        "TRIANGULAR_AB_EXECUTOR_ENABLED",
    )


def _direct_execution_mode(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> str:
    configured = _first_value(
        protocol,
        opportunity,
        names=("execution_mode", "direct_execution_mode", "runtime_execution_mode"),
    ) or _env("TRIANGULAR_DIRECT_EXECUTION_MODE", default="auto")
    mode = str(configured or "auto").strip().lower().replace("-", "_")
    aliases = {
        "a": "auto",
        "abc": "auto",
        "ordered": "ordered_auto",
        "ordered_status": "ordered_auto",
        "status_order": "ordered_auto",
        "auto_select": "auto",
        "two_or_three_pool": "auto",
        "b": "triangular",
        "three_pool": "triangular",
        "triangular_only": "triangular",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"auto", "ordered_auto", "triangular"}:
        raise ValueError("TRIANGULAR_DIRECT_EXECUTION_MODE must be auto, ordered_auto, or triangular")
    return mode


def _runtime_execution_params(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, int]:
    amount = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("execution_amount_usdc", "execution_amount", "borrow_amount", "amount"),
        )
        or _env(
            "TRIANGULAR_BORROW_AMOUNT_UNITS",
            "TRIANGULAR_EXECUTION_AMOUNT_USDC_BASE_UNITS",
            "TRIANGULAR_EXECUTION_AMOUNT",
            "TRIANGULAR_FLASHLOAN_AMOUNT_USDC",
        )
    )
    amount_out_min = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("amountOutMinUsdc", "amount_out_min_usdc", "min_final_usdc"),
        )
        or _env(
            "TRIANGULAR_AMOUNT_OUT_MIN_USDC",
            "TRIANGULAR_EXECUTION_AMOUNT_OUT_MIN_USDC",
            default=str(amount),
        )
    )
    explicit_min_profit = _first_value(
        protocol,
        opportunity,
        names=("minProfitUsdc", "min_profit_usdc", "execution_min_profit_usdc"),
    )
    expected_profit_usdc = _first_value(
        protocol,
        opportunity,
        names=("expected_profit_usdc", "expected_profit_amount"),
    )
    if explicit_min_profit is not None:
        min_profit = _positive_int_value(explicit_min_profit, default=1)
    elif expected_profit_usdc is not None:
        min_profit = _usdc_decimal_to_base_units(expected_profit_usdc)
    else:
        min_profit = _positive_int_value(
            _env(
                "TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS",
                "TRIANGULAR_MIN_PROFIT_USDC",
                "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
                default="1",
            ),
            default=1,
        )
    min_net_profit = _positive_int_value(
        _env(
            "TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS",
            "TRIANGULAR_MIN_NET_PROFIT_USDC",
            default="1",
        ),
        default=1,
    )
    min_profit = max(min_profit, min_net_profit)
    deadline = _positive_int_value(
        _first_value(protocol, opportunity, names=("deadline", "execution_deadline"))
        or _env("TRIANGULAR_EXECUTION_DEADLINE")
    )
    if deadline == 0:
        ttl = _positive_int_value(_env("TRIANGULAR_EXECUTION_DEADLINE_SECONDS", default="300"), default=300)
        deadline = int(datetime.now(timezone.utc).timestamp()) + max(1, ttl)
    usdc_to_token_x_fee = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("usdcToTokenXFee", "usdc_to_token_x_fee", "usdc_to_x_fee"),
        )
        or _env("TRIANGULAR_USDC_TO_TOKEN_X_FEE", default="3000"),
        default=3000,
    )
    token_y_to_usdc_fee = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("tokenYToUsdcFee", "token_y_to_usdc_fee", "y_to_usdc_fee"),
        )
        or _env("TRIANGULAR_TOKEN_Y_TO_USDC_FEE", default="3000"),
        default=3000,
    )
    if amount == 0:
        raise ValueError("execution amount is required when executor mode is enabled")
    if min_profit == 0:
        raise ValueError("min profit is required when executor mode is enabled")
    if usdc_to_token_x_fee <= 0 or usdc_to_token_x_fee > 16_777_215:
        raise ValueError("TRIANGULAR_USDC_TO_TOKEN_X_FEE must be between 1 and 16777215")
    if token_y_to_usdc_fee <= 0 or token_y_to_usdc_fee > 16_777_215:
        raise ValueError("TRIANGULAR_TOKEN_Y_TO_USDC_FEE must be between 1 and 16777215")
    return {
        "amount": amount,
        "deadline": deadline,
        "amountOutMinUsdc": amount_out_min,
        "minProfitUsdc": min_profit,
        "usdcToTokenXFee": usdc_to_token_x_fee,
        "tokenYToUsdcFee": token_y_to_usdc_fee,
    }


def _runtime_cross_pool_execution_params(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
    triangular_params: dict[str, int] | None = None,
) -> dict[str, int]:
    fallback_amount = str((triangular_params or {}).get("amount") or "")
    amount = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("cross_pool_amount", "crossPoolAmount", "cross_pool_borrow_amount", "token_x_borrow_amount"),
        )
        or _env(
            "TRIANGULAR_CROSS_POOL_BORROW_AMOUNT_UNITS",
            "TRIANGULAR_CROSS_POOL_AMOUNT_UNITS",
            default=fallback_amount,
        )
    )
    min_final = _positive_int_value(
        _first_value(protocol, opportunity, names=("minFinalTokenX", "min_final_token_x", "cross_pool_min_final"))
        or _env("TRIANGULAR_CROSS_POOL_MIN_FINAL_TOKEN_X", default=str(amount))
    )
    min_profit = _positive_int_value(
        _first_value(
            protocol,
            opportunity,
            names=("minProfitTokenX", "min_profit_token_x", "cross_pool_min_profit"),
        )
        or _env("TRIANGULAR_CROSS_POOL_MIN_PROFIT_TOKEN_X_BASE_UNITS", default="1"),
        default=1,
    )
    deadline = _positive_int_value(
        _first_value(protocol, opportunity, names=("cross_pool_deadline", "crossPoolDeadline"))
        or _env("TRIANGULAR_CROSS_POOL_DEADLINE")
    )
    if deadline == 0:
        deadline = int((triangular_params or {}).get("deadline") or 0)
    if deadline == 0:
        ttl = _positive_int_value(_env("TRIANGULAR_EXECUTION_DEADLINE_SECONDS", default="300"), default=300)
        deadline = int(datetime.now(timezone.utc).timestamp()) + max(1, ttl)
    if amount == 0:
        raise ValueError("cross-pool amount is required when auto executor mode is enabled")
    if min_profit == 0:
        raise ValueError("cross-pool min profit is required when auto executor mode is enabled")
    return {
        "amount": amount,
        "deadline": deadline,
        "minFinalTokenX": min_final,
        "minProfitTokenX": min_profit,
    }


def _route_symbols_match(route_symbols: Any, route_path: list[Any]) -> bool:
    if not isinstance(route_symbols, (list, tuple)) or not route_path:
        return False
    left = [str(item or "").strip().upper() for item in route_symbols]
    right = [str(item or "").strip().upper() for item in route_path]
    return bool(left and left == right)


def _runtime_group_intent_score(group: dict[str, Any], route_path: list[Any]) -> int:
    if not isinstance(group, dict) or len(route_path) < 3:
        return 0
    normalized_path = [str(item or "").strip().upper() for item in route_path if str(item or "").strip()]
    route_key = str(group.get("routeKey") or "").strip().upper()
    route_kind = str(group.get("routeKind") or "").strip().lower()
    trades = group.get("trades") if isinstance(group.get("trades"), list) else []

    if len(normalized_path) >= 4:
        forward_key = f"{normalized_path[1]}:{normalized_path[2]}"
        reverse_key = f"{normalized_path[2]}:{normalized_path[1]}"
        if any(_route_symbols_match(trade.get("routeSymbols") or trade.get("route_symbols"), normalized_path) for trade in trades):
            return 300
        if route_kind == "usdc_triangular" and route_key == forward_key:
            return 250
        if route_kind == "usdc_triangular" and route_key == reverse_key:
            return 200
    if len(normalized_path) >= 3 and normalized_path[0] == "USDC":
        direct_key = f"USDC:{normalized_path[1]}"
        if route_kind == "usdc_cross_pool" and route_key == direct_key:
            return 150
    return 0


def _select_runtime_group_for_intent(
    route_groups: list[dict[str, Any]],
    route_path: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    normalized_path = [str(item or "").strip().upper() for item in route_path if str(item or "").strip()]
    scored: list[tuple[int, float, int, dict[str, Any]]] = []
    for index, group in enumerate(route_groups):
        score = _runtime_group_intent_score(group, route_path)
        if score <= 0:
            continue
        scored.append((score, float(group.get("score") or 0.0), -index, group))
    if scored:
        selected = max(scored, key=lambda item: (item[0], item[1], item[2]))
        return selected[3], {
            "matched": True,
            "matchScore": str(selected[0]),
            "routeKey": str(selected[3].get("routeKey") or ""),
            "routeKind": str(selected[3].get("routeKind") or ""),
            "routePath": normalized_path,
        }

    preferred_groups = [
        group for group in route_groups
        if group.get("routeKind") == "usdc_triangular" and len(group.get("trades") or []) >= 3
    ]
    if preferred_groups:
        return preferred_groups[0], {
            "matched": False,
            "matchScore": "0",
            "routeKey": str(preferred_groups[0].get("routeKey") or ""),
            "routeKind": str(preferred_groups[0].get("routeKind") or ""),
            "routePath": normalized_path,
            "fallback": "first_triangular_group",
        }
    if route_groups:
        return route_groups[0], {
            "matched": False,
            "matchScore": "0",
            "routeKey": str(route_groups[0].get("routeKey") or ""),
            "routeKind": str(route_groups[0].get("routeKind") or ""),
            "routePath": normalized_path,
            "fallback": "first_route_group",
        }
    return None, {"matched": False, "matchScore": "0", "routeKey": "", "routeKind": "", "routePath": normalized_path}


def _pair_tokens_from_path(path: Any) -> tuple[Any, Any]:
    if isinstance(path, (list, tuple)) and len(path) >= 4:
        return path[1], path[2]
    return None, None


def _resolve_pair_id(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> int | None:
    explicit = _explicit_pair_id(protocol)
    if explicit is not None:
        return explicit

    token_x = _first_value(protocol, opportunity, names=("tokenX", "token_x", "tokex"))
    token_y = _first_value(protocol, opportunity, names=("tokenY", "token_y", "tokey"))
    router = _first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
    if token_x is None or token_y is None:
        path_x, path_y = _pair_tokens_from_path(protocol.get("route_path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    if (token_x is None or token_y is None) and isinstance(opportunity, dict):
        path_x, path_y = _pair_tokens_from_path(opportunity.get("route_path") or opportunity.get("path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    inferred = pair_index_from_tokenx_tokeny(token_x, token_y, local_usdc_pair_memory_table(protocol), router=router)
    return inferred if inferred is not None else _env_pair_id()


def build_triangular_onchain_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    intent = build_cow_intent_trade(link_name, expected_profit, rising_tokens, falling_tokens)
    route_path = list(intent.get("route_path") or [])
    signal_market_state = {
        "top": list(rising_tokens or []) if isinstance(rising_tokens, (list, tuple)) else [],
        "bottom": list(falling_tokens or []) if isinstance(falling_tokens, (list, tuple)) else [],
    }
    route_groups = _runtime_route_group_candidates({"market_state": signal_market_state}, None)
    selected_group, intent_route_match = _select_runtime_group_for_intent(route_groups, route_path)
    runtime_trades = list(selected_group["trades"]) if selected_group else []
    network, chain_id, testnet = _resolve_direct_onchain_network()
    unified_executor_address = _env(
        "UNIFIED_EXECUTOR_ADDRESS",
        "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
        "AAVE_TRIANGULAR_EXECUTOR_ADDRESS",
    )
    protocol = {
        "kind": "unified_flashloan_mev_executor_runtime_v1",
        "enabled": True,
        "network": network,
        "chain_id": chain_id,
        "testnet": testnet,
        "owner_address": _env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "TRIANGULAR_CONTROLLER_OWNER_ADDRESS"),
        "unified_executor_address": unified_executor_address,
        "executor_address": unified_executor_address,
        "runtime_trades": runtime_trades,
        "runtime_route_groups": route_groups,
        "runtime_trade_limit": MAX_RUNTIME_TRADE_SCAN,
        "runtime_candidate_limit": MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
        "runtime_route_group_limit": _runtime_route_group_limit({}, None),
        "intent_route_match": intent_route_match,
        "execution_mode": "ordered_auto",
        "candidate_strategy": _runtime_strategy_from_env(),
        "selection_strategy": "all_usdc_route_groups_preview_then_best",
        "borrow_symbol": intent.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL,
        "route_path": route_path,
        "route_direction": intent.get("route_direction"),
        "expected_profit_usdc": intent.get("expected_profit_amount"),
    }
    intent["direct_onchain_protocol"] = protocol
    intent["intent_protocol"] = "direct_onchain"
    intent["submission_protocol"] = "direct_onchain"
    intent["submission_mode"] = "direct_onchain"
    intent["direct_onchain_ready"] = bool(
        protocol["unified_executor_address"] and (protocol["runtime_trades"] or protocol["runtime_route_groups"])
    )
    return intent


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

    intent = quote_payload.get("cow_flashloan_intent") if isinstance(quote_payload, dict) else None
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent, dict) else None
    if not isinstance(protocol, dict) or not protocol.get("enabled", True):
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_missing",
            "blocked_reason": "direct_protocol_missing",
            "error": "direct_onchain_protocol is required",
        }

    try:
        network, expected_chain_id, _expected_testnet = _resolve_direct_onchain_network(protocol)
    except ValueError as exc:
        return {
            "ok": False,
            "submitted": False,
            "status": "order_submission_network_unsupported",
            "blocked_reason": "order_submission_network_unsupported",
            "error": str(exc),
        }

    controller_address = str(protocol.get("controller_address") or "").strip()
    owner_address = str(protocol.get("owner_address") or "").strip()
    if not controller_address:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "controller address is required",
        }

    opportunity_context = opportunity if isinstance(opportunity, dict) else None
    runtime_trades = _runtime_trade_candidates(
        protocol,
        opportunity_context,
        limit=MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
    )
    use_runtime_trades = bool(runtime_trades)
    if not use_runtime_trades:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "runtime_trades is required",
        }

    rpc_url, rpc_env_names = _direct_rpc_url(network)
    if not rpc_url:
        return {
            "ok": False,
            "submitted": False,
            "status": "network_config_missing",
            "blocked_reason": "network_config_missing",
            "error": f"{' or '.join(rpc_env_names)} is required",
        }

    from web3 import Web3
    from eth_account import Account

    request_timeout = max(1, int(timeout_seconds or 20))

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout}))
        controller = w3.eth.contract(address=Web3.to_checksum_address(controller_address), abi=TRIANGULAR_CONTROLLER_ABI)
        chain_id = int(w3.eth.chain_id)
        if chain_id != expected_chain_id:
            return {
                "ok": False,
                "submitted": False,
                "status": "network_mismatch",
                "blocked_reason": "network_mismatch",
                "error": f"chain id is {chain_id}, expected {expected_chain_id}",
            }
        signer_key = _env("LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY", "DEPLOYER_PRIVATE_KEY")
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
        onchain_owner = Web3.to_checksum_address(controller.functions.owner().call())
        configured_owner = Web3.to_checksum_address(owner_address) if owner_address else onchain_owner
        if signer_address.lower() != onchain_owner.lower():
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_not_owner",
                "blocked_reason": "signer_not_owner",
                "error": "signer does not match controller owner",
                "owner": onchain_owner,
                "configured_owner": configured_owner,
                "signer": signer_address,
            }
        all_candidate_trade_args = [_runtime_trade_abi_arg(trade, Web3) for trade in runtime_trades]
        runtime_trade_plan = _runtime_trade_plan_report(runtime_trades)
        runtime_trade_metadata = _runtime_trade_metadata_by_index(runtime_trades)
        execute_runtime_trade = _direct_execution_enabled(protocol, opportunity_context)
        execution_mode = _direct_execution_mode(protocol, opportunity_context) if execute_runtime_trade else "preview"
        execution_params = _runtime_execution_params(protocol, opportunity_context) if execute_runtime_trade else None
        cross_pool_params = (
            _runtime_cross_pool_execution_params(protocol, opportunity_context, execution_params)
            if execute_runtime_trade and execution_mode in {"auto", "ordered_auto"}
            else None
        )

        execution_preview = None
        execution_phase = "preview"
        if execute_runtime_trade:
            usdc_address = _usdc_address_for_network(network)
            borrowable_addresses = _aave_borrowable_token_addresses(
                min_liquidity=int((cross_pool_params or {}).get("amount") or 0)
            )
            allow_non_usdc_cross_pool = _non_usdc_cross_pool_enabled(protocol, opportunity_context)
            if execution_mode == "ordered_auto":
                candidate_trade_args = all_candidate_trade_args[:DEFAULT_RUNTIME_TRADE_SCAN]
                profit_ranked_trades = []
                execution_phase = "ordered_status_auto"
            elif execution_mode == "auto":
                auto_pairs = [
                    (trade, arg)
                    for trade, arg in zip(runtime_trades, all_candidate_trade_args)
                    if _cross_pool_trade_allowed(
                        trade,
                        usdc_address=usdc_address,
                        borrowable_addresses=borrowable_addresses,
                        allow_non_usdc=allow_non_usdc_cross_pool,
                    )
                ]
                triangular_pairs = [
                    (trade, arg)
                    for trade, arg in zip(runtime_trades, all_candidate_trade_args)
                    if _triangular_trade_allowed(trade, usdc_address)
                ]
                auto_trade_args = [arg for _trade, arg in auto_pairs]
                triangular_trade_args = [arg for _trade, arg in triangular_pairs]

                preview_fn = lambda trade: controller.functions.previewFirstProfitableRuntimeAutoExecution(
                    [trade],
                    execution_params,
                    cross_pool_params,
                ).call({"from": signer_address})
                candidate_trade_args, profit_ranked_trades = _rank_runtime_trades_by_profit(auto_trade_args, preview_fn)
                execution_phase = "auto_cross_pool_then_triangular"
                if not candidate_trade_args and triangular_trade_args:
                    triangular_preview_fn = lambda trade: controller.functions.previewFirstProfitableRuntimeExecution(
                        [trade],
                        execution_params,
                    ).call({"from": signer_address})
                    candidate_trade_args, profit_ranked_trades = _rank_runtime_trades_by_profit(
                        triangular_trade_args,
                        triangular_preview_fn,
                    )
                    execution_mode = "triangular"
                    cross_pool_params = None
                    execution_phase = "triangular_fallback_after_cross_pool_filter"
            else:
                preview_fn = lambda trade: controller.functions.previewFirstProfitableRuntimeExecution(
                    [trade],
                    execution_params,
                ).call({"from": signer_address})
                triangular_trade_args = [
                    arg
                    for trade, arg in zip(runtime_trades, all_candidate_trade_args)
                    if _triangular_trade_allowed(trade, usdc_address)
                ]
                candidate_trade_args, profit_ranked_trades = _rank_runtime_trades_by_profit(triangular_trade_args, preview_fn)
                execution_phase = "triangular_only"
            request_payload = {
                "runtimeTrades": candidate_trade_args,
                "candidateTradeCount": str(len(all_candidate_trade_args)),
                "runtimeTradePlan": runtime_trade_plan,
                "profitRankedTrades": profit_ranked_trades,
                "executeRuntimeTrade": True,
                "executionMode": execution_mode,
                "executionPhase": execution_phase,
                "selectionStrategy": (
                    "ordered_status_first_profitable"
                    if execution_mode == "ordered_auto"
                    else "first_profitable_in_ascending_profit_order"
                ),
                "executionParams": {key: str(value) for key, value in execution_params.items()},
            }
            if cross_pool_params is not None:
                request_payload["crossPoolExecutionParams"] = {
                    key: str(value) for key, value in cross_pool_params.items()
                }
            request_payload["crossPoolFilter"] = {
                "usdcAddress": usdc_address,
                "nonUsdcCrossPoolEnabled": str(bool(allow_non_usdc_cross_pool)),
                "borrowableAddressCount": str(len(borrowable_addresses)),
            }
            if not candidate_trade_args:
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "no_profitable_execution",
                    "blocked_reason": "no_profitable_execution",
                    "error": "no runtime candidate passed the on-chain profit preview",
                    "network": network,
                    "chain_id": chain_id,
                    "controller_address": controller_address,
                    "request": request_payload,
                }

            if execution_mode == "ordered_auto":
                ordered_preview = controller.functions.previewOrderedRuntimeAutoExecution(
                    candidate_trade_args,
                    execution_params,
                    cross_pool_params,
                    allow_non_usdc_cross_pool,
                ).call({"from": signer_address})
            elif execution_mode == "auto":
                ordered_preview = controller.functions.previewFirstProfitableRuntimeAutoExecution(
                    candidate_trade_args,
                    execution_params,
                    cross_pool_params,
                ).call({"from": signer_address})
            else:
                ordered_preview = controller.functions.previewFirstProfitableRuntimeExecution(
                    candidate_trade_args,
                    execution_params,
                ).call({"from": signer_address})
            found = bool(ordered_preview[0])
            if execution_mode == "ordered_auto":
                selected_strategy_status = int(ordered_preview[1])
                selected_execution_kind = int(ordered_preview[2])
                selected_trade_array_index = int(ordered_preview[3])
                preflight = ordered_preview[4]
                execution_preview = _runtime_execution_preview_report(ordered_preview[5])
                request_payload["selectedExecutionKind"] = str(selected_execution_kind)
            elif execution_mode == "auto":
                selected_execution_kind = int(ordered_preview[1])
                selected_trade_array_index = int(ordered_preview[2])
                preflight = ordered_preview[3]
                execution_preview = _runtime_execution_preview_report(ordered_preview[4])
                request_payload["selectedExecutionKind"] = str(selected_execution_kind)
            else:
                selected_execution_kind = 1
                selected_trade_array_index = int(ordered_preview[1])
                preflight = ordered_preview[2]
                execution_preview = _runtime_execution_preview_report(ordered_preview[3])
                selected_strategy_status = _selected_strategy_status(
                    runtime_trade_metadata.get(
                        int((candidate_trade_args[selected_trade_array_index] if selected_trade_array_index < len(candidate_trade_args) else {}).get("tradeIndex", -1))
                    ),
                    execution_mode=execution_mode,
                    execution_kind=selected_execution_kind,
                )
            request_payload["selectedTradeArrayIndex"] = str(selected_trade_array_index)
            if execution_mode == "auto":
                selected_trade_arg = candidate_trade_args[selected_trade_array_index] if selected_trade_array_index < len(candidate_trade_args) else None
                selected_trade_meta = runtime_trade_metadata.get(int((selected_trade_arg or {}).get("tradeIndex", -1)))
                selected_strategy_status = _selected_strategy_status(
                    selected_trade_meta,
                    execution_mode=execution_mode,
                    execution_kind=selected_execution_kind,
                )
            request_payload["selectedStrategyStatus"] = str(selected_strategy_status)

            if not found:
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "no_profitable_execution",
                    "blocked_reason": "no_profitable_execution",
                    "error": "controller preview found no profitable runtime trade in supplied order",
                    "network": network,
                    "chain_id": chain_id,
                    "controller_address": controller_address,
                    "request": request_payload,
                }
            if execution_mode == "ordered_auto":
                tx_builder = controller.functions.runOrderedRuntimeTradesAndExecuteAuto(
                    candidate_trade_args,
                    execution_params,
                    cross_pool_params,
                    allow_non_usdc_cross_pool,
                )
            elif execution_mode == "auto":
                tx_builder = controller.functions.runFirstProfitableRuntimeTradesAndExecuteAuto(
                    candidate_trade_args,
                    execution_params,
                    cross_pool_params,
                )
            else:
                tx_builder = controller.functions.runFirstProfitableRuntimeTradesAndExecute(
                    candidate_trade_args,
                    execution_params,
                )
        else:
            candidate_trade_args = all_candidate_trade_args[:MAX_RUNTIME_TRADE_SCAN]
            request_payload = {
                "runtimeTrades": candidate_trade_args,
                "candidateTradeCount": str(len(all_candidate_trade_args)),
                "runtimeTradePlan": runtime_trade_plan,
                "executeRuntimeTrade": False,
            }
            best_trade_index, preflight = controller.functions.previewBestRuntimeTrades(candidate_trade_args).call(
                {"from": signer_address}
            )
            request_payload["bestTradeArrayIndex"] = str(best_trade_index)
            if not preflight[0]:
                return {
                    "ok": False,
                    "submitted": False,
                    "status": "no_viable_route",
                    "blocked_reason": "no_viable_route",
                    "error": "controller preview found no viable route",
                    "network": network,
                    "chain_id": chain_id,
                    "controller_address": controller_address,
                    "preflight": _runtime_trade_decision_report(preflight),
                    "request": request_payload,
                }
            tx_builder = controller.functions.runBestRuntimeTrades(candidate_trade_args)

        static_return = tx_builder.call({"from": signer_address})
        gas_estimate = tx_builder.estimate_gas({"from": signer_address})
        static_report = {
            "ok": True,
            "gasEstimate": str(gas_estimate),
        }
        if execute_runtime_trade and execution_mode == "ordered_auto":
            static_report["strategyStatusReturned"] = str(static_return[0])
            static_report["executionKindReturned"] = str(static_return[1])
            static_report["bestTradeArrayIndexReturned"] = str(static_return[2])
            static_report["decisionReturned"] = _runtime_trade_decision_report(static_return[3])
        elif execute_runtime_trade and execution_mode == "auto":
            static_report["executionKindReturned"] = str(static_return[0])
            static_report["bestTradeArrayIndexReturned"] = str(static_return[1])
            static_report["decisionReturned"] = _runtime_trade_decision_report(static_return[2])
        else:
            static_report["bestTradeArrayIndexReturned"] = str(static_return[0])
            static_report["decisionReturned"] = _runtime_trade_decision_report(static_return[1])
        if execute_runtime_trade:
            static_report["profitSwept"] = str(
                static_return[4]
                if execution_mode == "ordered_auto"
                else static_return[3]
                if execution_mode == "auto"
                else static_return[2]
            )
            static_report["executionPreview"] = execution_preview
        broadcast_enabled = _env_bool("TRIANGULAR_DIRECT_BROADCAST_ENABLED", "TRIANGULAR_AB_BROADCAST_ENABLED")
        if not broadcast_enabled:
            result = {
                "ok": True,
                "submitted": False,
                "status": "static_call_passed",
                "blocked_reason": "broadcast_disabled",
                "error": None,
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "controller_address": controller_address,
                "tx_hash": None,
                "preflight": _runtime_trade_decision_report(preflight),
                "static_call": static_report,
                "strategy_status": str(request_payload.get("selectedStrategyStatus", "55555")) if execute_runtime_trade else None,
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            return result
        gas_estimate_info = estimate_gas_price(
            w3,
            max_gas_price_gwei=(
                _max_broadcast_gas_price_wei() / 1_000_000_000
                if _max_broadcast_gas_price_wei()
                else None
            ),
        )
        gas_guard = _broadcast_gas_guard(gas_estimate, gas_estimate_info)
        static_report["gasPricing"] = gas_guard["report"]
        if not gas_guard["ok"]:
            return _with_direct_circuit_record({
                "ok": False,
                "submitted": False,
                "status": gas_guard["status"],
                "blocked_reason": gas_guard["blocked_reason"],
                "error": gas_guard["blocked_reason"],
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "controller_address": controller_address,
                "preflight": _runtime_trade_decision_report(preflight),
                "static_call": static_report,
                "strategy_status": str(request_payload.get("selectedStrategyStatus", "55555")) if execute_runtime_trade else None,
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, network=network, route_key="legacy")
        tx_params = {
            "from": signer_address,
            "nonce": w3.eth.get_transaction_count(signer_address, "pending"),
            "chainId": chain_id,
            "gas": gas_estimate,
            **build_gas_params(gas_estimate_info),
        }
        built_tx = tx_builder.build_transaction(tx_params)
        signed_tx = Account.sign_transaction(built_tx, signer_key)
        broadcast = send_raw_transaction_private_first(_raw_signed_transaction(signed_tx), public_w3=w3)
        tx_hash = broadcast.get("tx_hash")
        receipt_hash = tx_hash.hex() if hasattr(tx_hash, "hex") else tx_hash
        receipt = w3.eth.wait_for_transaction_receipt(receipt_hash, timeout=max(1, int(timeout_seconds or 180)))
        status = "submitted_success" if receipt and int(receipt.status or 0) == 1 else "submitted_failed"
        result = {
            "ok": bool(receipt and int(receipt.status or 0) == 1),
            "submitted": True,
            "status": status,
            "blocked_reason": None if status == "submitted_success" else "submission_failed",
            "error": None if status == "submitted_success" else "transaction reverted",
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "controller_address": controller_address,
            "tx_hash": broadcast.get("tx_hash"),
            "broadcast_channel": broadcast.get("broadcast_channel"),
            "relay": broadcast.get("relay"),
            "preflight": _runtime_trade_decision_report(preflight),
            "strategy_status": str(request_payload.get("selectedStrategyStatus", "55555")) if execute_runtime_trade else None,
            "request": request_payload,
            "receipt": {
                "hash": receipt.hash.hex() if hasattr(receipt.hash, "hex") else str(receipt.hash),
                "status": receipt.status,
                "gasUsed": str(receipt.gasUsed) if receipt and receipt.gasUsed is not None else None,
            },
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        return result
    except Exception as exc:
        execution_error = _execution_failure_report(exc)
        return {
            "ok": False,
            "submitted": False,
            "status": "execution_preflight_failed" if execution_error else "submission_failed",
            "blocked_reason": execution_error.get("failureReason") if execution_error else "submission_failed",
            "error": execution_error.get("failureReason") if execution_error else redact_sensitive_text(exc),
            "execution_error": execution_error,
        }


def _unified_executor_route_order_check(
    trades: list[dict[str, Any]],
    *,
    usdc_address: str,
) -> dict[str, Any]:
    if not trades:
        return {"ok": False, "reason": "unified_executor_requires_at_least_one_trade"}

    def other_token(trade: dict[str, Any]) -> str:
        token_x = _normalize_pair_address(trade.get("tokenX"))
        token_y = _normalize_pair_address(trade.get("tokenY"))
        if token_x == usdc_address:
            return token_y
        if token_y == usdc_address:
            return token_x
        return ""

    x_token = other_token(trades[0])
    if not x_token:
        return {"ok": False, "reason": "unified_executor_first_trade_must_include_usdc"}
    if len(trades) == 1:
        return {
            "ok": True,
            "routeDirection": "U-X-U",
            "tokenX": x_token,
            "tokenY": "",
            "statuses": [_runtime_trade_status(trades[0])],
        }

    y_token = other_token(trades[1])
    if not y_token:
        return {"ok": False, "reason": "unified_executor_second_trade_must_include_usdc"}
    if len(trades) == 2:
        return {
            "ok": True,
            "routeDirection": "U-X-U|U-Y-U",
            "tokenX": x_token,
            "tokenY": y_token,
            "statuses": [_runtime_trade_status(trade) for trade in trades],
        }

    xy_x = _normalize_pair_address(trades[2].get("tokenX"))
    xy_y = _normalize_pair_address(trades[2].get("tokenY"))
    if not x_token or not y_token or not xy_x or not xy_y:
        return {"ok": False, "reason": "unified_executor_route_tokens_are_incomplete"}
    if x_token != xy_x or y_token != xy_y:
        return {
            "ok": False,
            "reason": "unified_executor_route_token_order_mismatch",
            "usdcXToken": x_token,
            "usdcYToken": y_token,
            "xyTokenX": xy_x,
            "xyTokenY": xy_y,
        }
    if len(trades) >= 4:
        yx_x = _normalize_pair_address(trades[3].get("tokenX"))
        yx_y = _normalize_pair_address(trades[3].get("tokenY"))
        if yx_x and yx_y and (yx_x != y_token or yx_y != x_token):
            return {
                "ok": False,
                "reason": "unified_executor_reverse_route_token_order_mismatch",
            }
    return {
        "ok": True,
        "routeDirection": "U-X-Y-U",
        "tokenX": x_token,
        "tokenY": y_token,
        "statuses": [_runtime_trade_status(trade) for trade in trades[:4]],
    }


def _route_group_target_symbols(group: dict[str, Any], trades: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    for trade in trades:
        route_symbols = trade.get("routeSymbols") or trade.get("route_symbols")
        if isinstance(route_symbols, (list, tuple)):
            for item in route_symbols:
                symbol = _normalized_symbol(item)
                if symbol and symbol != "USDC" and symbol not in symbols:
                    symbols.append(symbol)
    if not symbols:
        route_key = str(group.get("routeKey") or group.get("route_key") or "")
        for item in re.split(r"[:>\-/,\s]+", route_key):
            symbol = _normalized_symbol(item)
            if symbol and symbol != "USDC" and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _prepare_unified_route_groups(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    allow_single_pair_diagnostic: bool = False,
    allow_stable_targets: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        raw_trades = group.get("trades") if isinstance(group, dict) else None
        if not isinstance(raw_trades, list):
            rejected.append({"groupIndex": index, "reason": "route_group_trades_missing"})
            continue
        ordered, route_plan = _ordered_runtime_trade_plan(raw_trades, usdc_address=usdc_address)
        route_check = _unified_executor_route_order_check(ordered, usdc_address=usdc_address)
        if not route_plan.get("ok") or not route_check.get("ok"):
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": route_plan.get("reason") if not route_plan.get("ok") else route_check.get("reason"),
                }
            )
            continue
        if len(ordered) == 1 and not allow_single_pair_diagnostic:
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": "single_pair_diagnostic_disabled",
                }
            )
            continue
        target_symbols = _route_group_target_symbols(group, ordered)
        stable_targets = [symbol for symbol in target_symbols if _is_stable_exercise_symbol(symbol)]
        if stable_targets and not allow_stable_targets:
            rejected.append(
                {
                    "groupIndex": index,
                    "routeKey": str(group.get("routeKey") or f"group:{index}"),
                    "reason": "stable_exercise_target_disabled",
                    "stableTargets": stable_targets,
                }
            )
            continue
        prepared.append(
            {
                "groupIndex": index,
                "routeKey": str(group.get("routeKey") or route_plan.get("groupKey") or f"group:{index}"),
                "routeKind": str(group.get("routeKind") or "provided"),
                "exerciseTargetPolicy": str(group.get("exerciseTargetPolicy") or "provided"),
                "score": float(group.get("score") or 0.0),
                "trades": ordered,
                "routePlan": route_plan,
                "routeCheck": route_check,
                "targetSymbols": target_symbols,
            }
        )
    return prepared, rejected


def _select_best_unified_route_group(
    groups: list[dict[str, Any]],
    *,
    usdc_address: str,
    preview_group: Callable[[dict[str, Any]], Any],
    estimate_group_gas: Callable[[dict[str, Any]], int] | None = None,
    gas_price_wei: int = 0,
    relay_cost_wei: int = 0,
    cache_risk_penalty_usdc: int = 0,
    avax_usdc_price_micro: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    selected: tuple[int, int, int, dict[str, Any], dict[str, Any]] | None = None
    for group_index, group in enumerate(groups):
        try:
            raw_preview = preview_group(group)
            report = _runtime_order_preview_report(raw_preview)
        except Exception as exc:
            evaluations.append(
                {
                    "groupIndex": str(group.get("groupIndex", group_index)),
                    "routeKey": str(group.get("routeKey") or ""),
                    "found": False,
                    "status": "preview_error",
                    "error": redact_sensitive_text(exc),
                }
            )
            continue

        expected_profit = int(report["executionPreview"].get("expectedProfit") or 0)
        profit_asset = _normalize_pair_address(report["executionPreview"].get("profitAsset"))
        found = bool(report["found"]) and profit_asset == usdc_address and expected_profit > 0
        gas_units = 0
        gas_error = ""
        if found and estimate_group_gas is not None:
            try:
                gas_units = int(estimate_group_gas(group))
            except Exception as exc:
                found = False
                gas_error = redact_sensitive_text(exc)
        net_profit = _unified_net_profit_report(
            expected_profit=expected_profit,
            gas_units=gas_units,
            gas_price_wei=gas_price_wei,
            relay_cost_wei=relay_cost_wei,
            cache_risk_penalty_usdc=cache_risk_penalty_usdc,
            avax_usdc_price_micro=avax_usdc_price_micro,
        )
        evaluation = {
            "groupIndex": str(group.get("groupIndex", group_index)),
            "routeKey": str(group.get("routeKey") or ""),
            "routeKind": str(group.get("routeKind") or ""),
            "found": bool(report["found"]),
            "profitableCandidate": found,
            "selected": False,
            "strategyStatus": report["strategyStatus"],
            "executionKind": report["executionKind"],
            "expectedProfit": str(expected_profit),
            "netProfit": net_profit,
            "profitAsset": report["executionPreview"].get("profitAsset"),
            "preview": report,
        }
        if gas_error:
            evaluation["status"] = "gas_estimate_error"
            evaluation["error"] = gas_error
        evaluations.append(evaluation)
        if not found:
            continue
        tie_index = int(group.get("groupIndex", group_index))
        candidate = (int(net_profit["netProfit"]), expected_profit, -tie_index, group, report)
        if selected is None or candidate[:3] > selected[:3]:
            selected = candidate

    if selected is None:
        return None, None, evaluations
    selected_group = selected[3]
    selected_index = str(selected_group.get("groupIndex", ""))
    selected_key = str(selected_group.get("routeKey") or "")
    for evaluation in evaluations:
        if evaluation.get("groupIndex") == selected_index and evaluation.get("routeKey") == selected_key:
            evaluation["selected"] = True
            break
    return selected[3], selected[4], evaluations


def _unified_execution_params(params: dict[str, int]) -> dict[str, int]:
    return {
        "amount": int(params["amount"]),
        "deadline": int(params["deadline"]),
        "amountOutMinUsdc": int(params["amountOutMinUsdc"]),
        "minProfitUsdc": int(params["minProfitUsdc"]),
    }


def _unified_cross_pool_params(params: dict[str, int]) -> dict[str, int]:
    return {
        "amount": int(params["amount"]),
        "deadline": int(params["deadline"]),
        "minFinalToken": int(params["minFinalTokenX"]),
        "minProfitToken": int(params["minProfitTokenX"]),
    }


def _unified_receipt_events(contract: Any, receipt: Any) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for event_name in (
        "OrderedRuntimePreviewSelected",
        "RuntimeProfitChecked",
        "RuntimeTriangularHopQuoted",
        "RuntimeStepEvaluated",
        "FlashLoanRouteExecuted",
        "ProfitSwept",
        "RuntimeWorkflowFinished",
    ):
        try:
            event_factory = getattr(contract.events, event_name)
            decoded = event_factory().process_receipt(receipt)
        except Exception:
            continue
        if decoded:
            events[event_name] = [
                dict(item.get("args") or {})
                for item in decoded
                if isinstance(item, dict)
            ]
    return events


def submit_direct_onchain_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    intent = quote_payload.get("cow_flashloan_intent") if isinstance(quote_payload, dict) else None
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent, dict) else None
    if not isinstance(protocol, dict) or not protocol.get("enabled", True):
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_missing",
            "blocked_reason": "direct_protocol_missing",
            "error": "direct_onchain_protocol is required",
        }

    try:
        network, expected_chain_id, _expected_testnet = _resolve_direct_onchain_network(protocol)
    except ValueError as exc:
        return {
            "ok": False,
            "submitted": False,
            "status": "order_submission_network_unsupported",
            "blocked_reason": "order_submission_network_unsupported",
            "error": str(exc),
        }

    executor_address = str(
        protocol.get("unified_executor_address")
        or protocol.get("executor_address")
        or _env("UNIFIED_EXECUTOR_ADDRESS", "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS")
    ).strip()
    if not executor_address:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "unified executor address is required",
        }

    opportunity_context = opportunity if isinstance(opportunity, dict) else None
    usdc_address = _usdc_address_for_network(network)
    explicit_route_groups = any(
        isinstance(payload, dict)
        and any(isinstance(payload.get(key), list) for key in ("runtime_route_groups", "runtime_trades", "candidate_trades", "trades"))
        for payload in (protocol, opportunity_context)
    ) or bool(_env("TRIANGULAR_RUNTIME_TRADES_JSON"))
    route_groups = _runtime_route_group_candidates(protocol, opportunity_context)
    prepared_groups, rejected_groups = _prepare_unified_route_groups(
        route_groups,
        usdc_address=usdc_address,
        allow_single_pair_diagnostic=_allow_single_pair_diagnostic(protocol, opportunity_context),
        allow_stable_targets=_allow_stable_exercise_targets(protocol, opportunity_context),
    )
    if not prepared_groups:
        error = "runtime_trades_empty" if not route_groups else "no_valid_unified_route_group"
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": error,
            "routeGroupCount": len(route_groups),
            "rejectedRouteGroups": rejected_groups,
        }

    rpc_url, rpc_env_names = _direct_rpc_url(network)
    if not rpc_url:
        return {
            "ok": False,
            "submitted": False,
            "status": "network_config_missing",
            "blocked_reason": "network_config_missing",
            "error": f"{' or '.join(rpc_env_names)} is required",
        }

    from web3 import Web3
    from eth_account import Account

    request_timeout = max(1, int(timeout_seconds or 20))
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout}))
        executor = w3.eth.contract(
            address=Web3.to_checksum_address(executor_address),
            abi=_load_unified_executor_abi(),
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
        max_cache_age_blocks = _max_runtime_cache_age_blocks(protocol)
        cache_reports: dict[str, dict[str, Any]] = {}
        pool_cache = _load_runtime_pool_cache()
        pool_cache_report = _runtime_cache_validation(
            pool_cache,
            network=network,
            current_block=current_block,
            max_age_blocks=max_cache_age_blocks,
            required_factory=_unified_required_factory(protocol, pool_cache),
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
                "runtimePoolCacheFile": str(_runtime_pool_cache_path()),
            }

        signer_key = _env(
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

        execute_runtime_trade = _direct_execution_enabled(protocol, opportunity_context)
        usdc_params = _runtime_execution_params(protocol, opportunity_context)
        token_params = _runtime_cross_pool_execution_params(
            protocol,
            opportunity_context,
            usdc_params,
        )
        usdc_params_arg = _unified_execution_params(usdc_params)
        token_params_arg = _unified_cross_pool_params(token_params)
        allow_non_usdc_cross_pool = _non_usdc_cross_pool_enabled(protocol, opportunity_context)
        reserve_cache = _load_aave_reserve_cache()
        reserve_cache_report = _reserve_cache_validation(
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
                "reserveCacheFile": str(_aave_reserve_cache_path()),
            }
        borrowable_addresses = _aave_borrowable_token_addresses(
            reserve_cache,
            min_liquidity=token_params_arg["amount"],
            network=network,
        )
        broadcast_enabled = _env_bool(
            "TRIANGULAR_DIRECT_BROADCAST_ENABLED",
            "TRIANGULAR_AB_BROADCAST_ENABLED",
            "UNIFIED_EXECUTOR_BROADCAST_ENABLED",
        )
        gas_estimate_info = None
        gas_price_for_selection = 0
        relay_cost_for_selection = 0
        cache_penalty_for_selection = 0
        avax_usdc_price_for_selection = 0
        gas_token_price_report: dict[str, Any] = {}
        if broadcast_enabled:
            gas_estimate_info = estimate_gas_price(
                w3,
                max_gas_price_gwei=(
                    _max_broadcast_gas_price_wei() / 1_000_000_000
                    if _max_broadcast_gas_price_wei()
                    else None
                ),
            )
            gas_price_for_selection = _positive_int_value(getattr(gas_estimate_info, "max_fee", 0))
            relay_cost_for_selection = _relay_cost_wei(protocol, opportunity_context)
            cache_penalty_for_selection = _cache_risk_penalty_usdc_base_units(
                protocol,
                opportunity_context,
                cache_reports,
            )
            gas_token_price_report = _gas_token_usdc_price_report(protocol, opportunity_context)
            avax_usdc_price_for_selection = _positive_int_value(gas_token_price_report.get("priceMicro"))
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
            runtime_trade_args = [_runtime_trade_abi_arg(trade, Web3) for trade in group["trades"]]
            return executor.functions.previewOrderedRuntimeAutoExecution(
                runtime_trade_args,
                usdc_params_arg,
                token_params_arg,
                allow_non_usdc_cross_pool,
            ).call({"from": signer_address})

        def estimate_group_gas(group: dict[str, Any]) -> int:
            runtime_trade_args = [_runtime_trade_abi_arg(trade, Web3) for trade in group["trades"]]
            return int(
                executor.functions.runOrderedRuntimeTradesAndExecuteAuto(
                    runtime_trade_args,
                    usdc_params_arg,
                    token_params_arg,
                    allow_non_usdc_cross_pool,
                ).estimate_gas({"from": signer_address})
            )

        selected_group, preview_report, route_evaluations = _select_best_unified_route_group(
            prepared_groups,
            usdc_address=usdc_address,
            preview_group=preview_group,
            estimate_group_gas=estimate_group_gas if broadcast_enabled else None,
            gas_price_wei=gas_price_for_selection,
            relay_cost_wei=relay_cost_for_selection,
            cache_risk_penalty_usdc=cache_penalty_for_selection,
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
        runtime_trade_args = [_runtime_trade_abi_arg(trade, Web3) for trade in runtime_trades]
        route_plan = selected_group["routePlan"]
        route_check = selected_group["routeCheck"]
        token_x_for_cross_pool = (
            _normalize_pair_address(runtime_trades[2].get("tokenX"))
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
                "reserveCacheFile": str(_aave_reserve_cache_path()),
            }
        request_payload = {
            "runtimeTrades": runtime_trade_args,
            "runtimeTradePlan": _runtime_trade_plan_report(runtime_trades),
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
                "net_profit_after_gas_relay_cache_penalty"
                if broadcast_enabled
                else "expected_profit_preview_only"
            ),
            "netProfitModel": {
                "enabled": str(bool(broadcast_enabled)),
                "gasPriceWei": str(gas_price_for_selection),
                "relayCostWei": str(relay_cost_for_selection),
                "cacheRiskPenaltyUsdc": str(cache_penalty_for_selection),
                "avaxUsdcPriceMicro": str(avax_usdc_price_for_selection),
                "gasTokenPrice": gas_token_price_report,
            },
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
        }
        if broadcast_enabled:
            circuit_status = _direct_circuit_status(network=network, route_key=selected_group["routeKey"])
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
                return _with_direct_circuit_record({
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
        if not execute_runtime_trade:
            return {
                "ok": True,
                "submitted": False,
                "status": "preview_passed",
                "blocked_reason": "execution_disabled",
                "error": None,
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "executor_address": executor_address,
                "preflight": preview_report,
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

        tx_builder = executor.functions.runOrderedRuntimeTradesAndExecuteAuto(
            runtime_trade_args,
            usdc_params_arg,
            token_params_arg,
            allow_non_usdc_cross_pool,
        )
        static_return = tx_builder.call({"from": signer_address})
        gas_estimate = tx_builder.estimate_gas({"from": signer_address})
        static_report = {
            "ok": True,
            "gasEstimate": str(gas_estimate),
            "runResult": _runtime_run_result_report(static_return),
            "executionPreview": preview_report["executionPreview"],
        }
        if not broadcast_enabled:
            return {
                "ok": True,
                "submitted": False,
                "status": "static_call_passed",
                "blocked_reason": "broadcast_disabled",
                "error": None,
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "executor_address": executor_address,
                "preflight": preview_report,
                "static_call": static_report,
                "strategy_status": preview_report["strategyStatus"],
                "route_direction": preview_report["executionPreview"]["routeDirection"],
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

        if gas_estimate_info is None:
            gas_estimate_info = estimate_gas_price(
                w3,
                max_gas_price_gwei=(
                    _max_broadcast_gas_price_wei() / 1_000_000_000
                    if _max_broadcast_gas_price_wei()
                    else None
                ),
            )
        gas_guard = _broadcast_gas_guard(gas_estimate, gas_estimate_info)
        static_report["gasPricing"] = gas_guard["report"]
        if not gas_guard["ok"]:
            return {
                "ok": False,
                "submitted": False,
                "status": gas_guard["status"],
                "blocked_reason": gas_guard["blocked_reason"],
                "error": gas_guard["blocked_reason"],
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "executor_address": executor_address,
                "preflight": preview_report,
                "static_call": static_report,
                "strategy_status": preview_report["strategyStatus"],
                "route_direction": preview_report["executionPreview"]["routeDirection"],
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        tx_params = {
            "from": signer_address,
            "nonce": w3.eth.get_transaction_count(signer_address, "pending"),
            "chainId": chain_id,
            "gas": gas_estimate,
            **build_gas_params(gas_estimate_info),
        }
        built_tx = tx_builder.build_transaction(tx_params)
        signed_tx = Account.sign_transaction(built_tx, signer_key)
        broadcast = send_raw_transaction_private_first(
            _raw_signed_transaction(signed_tx),
            public_w3=w3,
            allow_public_fallback=_public_fallback_enabled(protocol, opportunity_context),
        )
        tx_hash = broadcast.get("tx_hash")
        if not tx_hash:
            return _with_direct_circuit_record({
                "ok": False,
                "submitted": False,
                "status": broadcast.get("status") or "private_relay_failed_public_fallback_disabled",
                "blocked_reason": broadcast.get("status") or "private_relay_failed_public_fallback_disabled",
                "error": "private relay failed and public fallback is disabled; discard calldata and re-preview",
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "executor_address": executor_address,
                "broadcast_channel": broadcast.get("broadcast_channel"),
                "relay_errors": broadcast.get("relay_errors"),
                "preflight": preview_report,
                "static_call": static_report,
                "strategy_status": preview_report["strategyStatus"],
                "route_direction": preview_report["executionPreview"]["routeDirection"],
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, network=network, route_key=selected_group["routeKey"])
        receipt_hash = tx_hash.hex() if hasattr(tx_hash, "hex") else tx_hash
        receipt = w3.eth.wait_for_transaction_receipt(
            receipt_hash,
            timeout=max(1, int(timeout_seconds or 180)),
        )
        status = "submitted_success" if receipt and int(receipt.status or 0) == 1 else "submitted_failed"
        events = _unified_receipt_events(executor, receipt) if receipt else {}
        return _with_direct_circuit_record({
            "ok": status == "submitted_success",
            "submitted": True,
            "status": status,
            "blocked_reason": None if status == "submitted_success" else "submission_failed",
            "error": None if status == "submitted_success" else "transaction reverted",
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "executor_address": executor_address,
            "tx_hash": broadcast.get("tx_hash"),
            "broadcast_channel": broadcast.get("broadcast_channel"),
            "relay": broadcast.get("relay"),
            "preflight": preview_report,
            "static_call": static_report,
            "strategy_status": preview_report["strategyStatus"],
            "route_direction": preview_report["executionPreview"]["routeDirection"],
            "request": request_payload,
            "receipt": {
                "hash": receipt.hash.hex() if hasattr(receipt.hash, "hex") else str(receipt.hash),
                "status": receipt.status,
                "gasUsed": str(receipt.gasUsed) if receipt and receipt.gasUsed is not None else None,
                "events": events,
            },
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }, network=network, route_key=selected_group["routeKey"])
    except Exception as exc:
        execution_error = _execution_failure_report(exc)
        return {
            "ok": False,
            "submitted": False,
            "status": "execution_preflight_failed" if execution_error else "submission_failed",
            "blocked_reason": execution_error.get("failureReason") if execution_error else "submission_failed",
            "error": execution_error.get("failureReason") if execution_error else redact_sensitive_text(exc),
            "execution_error": execution_error,
        }
