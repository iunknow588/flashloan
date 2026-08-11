from __future__ import annotations

import os
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timezone
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
MAX_RUNTIME_POOL_SCAN = 10
MAX_RUNTIME_TRADE_SCAN = 16
DEFAULT_RUNTIME_TRADE_SCAN = 5
RUNTIME_TOP_BOTTOM_LIMIT = 5
MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN = RUNTIME_TOP_BOTTOM_LIMIT * RUNTIME_TOP_BOTTOM_LIMIT
SRC_ROOT = Path(__file__).resolve().parents[1]
AVALANCHE_USDC_ADDRESS = "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"
FUJI_USDC_ADDRESS = "0x5425890298aed601595a70ab815c96711a31bc65"
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


def _load_aave_reserve_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or _aave_reserve_cache_path()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _usdc_address_for_network(network: str | None = None, cache: dict[str, Any] | None = None) -> str:
    explicit = _normalize_pair_address(_env("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS"))
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


def _aave_borrowable_token_addresses(cache: dict[str, Any] | None = None, *, min_liquidity: int = 0) -> set[str]:
    addresses: set[str] = set()
    for asset in _aave_reserve_assets(cache):
        if not isinstance(asset, dict):
            continue
        address = _normalize_pair_address(asset.get("token_address") or asset.get("address"))
        if not address:
            continue
        liquidity = _positive_int_value(
            asset.get("available_liquidity")
            or asset.get("reserve_data_liquidity")
            or asset.get("a_token_total_supply")
            or 0
        )
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
    trade["spreadScore"] = spread
    return trade


def _runtime_trades_from_market_state(
    market_state: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
    side_limit: int = RUNTIME_TOP_BOTTOM_LIMIT,
    trade_limit: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(market_state, dict):
        return []
    pool_cache = cache if isinstance(cache, dict) else _load_runtime_pool_cache()
    top_rows = list(market_state.get("cow_top") or market_state.get("top") or [])[: max(1, int(side_limit))]
    bottom_rows = list(market_state.get("cow_bottom") or market_state.get("bottom") or [])[: max(1, int(side_limit))]
    network = str(market_state.get("network") or "").strip().lower()
    usdc_address = _usdc_address_for_network(network, pool_cache)
    strategy = _runtime_strategy_from_env()
    candidates: list[tuple[int, float, str, str, str, str, list[dict[str, Any]], str, list[str]]] = []
    seen_single_pairs: set[tuple[str, str]] = set()
    for top in top_rows:
        x_symbol = _base_symbol_from_row(top)
        x_change = _float_or_none(top.get("change_percent")) if isinstance(top, dict) else None
        if not x_symbol:
            continue
        if strategy == "expanded" and usdc_address:
            token_x, token_y, pools = _cache_pool_candidates_for_pair(pool_cache, "USDC", x_symbol)
            key = _trade_key(token_x, token_y)
            if len(pools) >= 2 and token_x and token_y and key not in seen_single_pairs:
                seen_single_pairs.add(key)
                candidates.append((1, float(x_change or 0.0), "USDC", x_symbol, token_x, token_y, pools, "status1_usdc_to_x_cross_pool", ["USDC", x_symbol, "USDC"]))
        for bottom in bottom_rows:
            y_symbol = _base_symbol_from_row(bottom)
            y_change = _float_or_none(bottom.get("change_percent")) if isinstance(bottom, dict) else None
            if not y_symbol or x_symbol == y_symbol:
                continue
            if strategy == "expanded" and usdc_address:
                token_x, token_y, pools = _cache_pool_candidates_for_pair(pool_cache, "USDC", y_symbol)
                key = _trade_key(token_x, token_y)
                if len(pools) >= 2 and token_x and token_y and key not in seen_single_pairs:
                    seen_single_pairs.add(key)
                    candidates.append((2, abs(float(y_change or 0.0)), "USDC", y_symbol, token_x, token_y, pools, "status2_usdc_to_y_cross_pool", ["USDC", y_symbol, "USDC"]))
            token_x, token_y, pools = _cache_pool_candidates_for_pair(pool_cache, x_symbol, y_symbol)
            if len(pools) < 2 or not token_x or not token_y:
                continue
            spread = (x_change - y_change) if x_change is not None and y_change is not None else 0.0
            candidates.append((3, spread, x_symbol, y_symbol, token_x, token_y, pools, "status3_4_xy_auto_or_triangular", ["USDC", x_symbol, y_symbol, "USDC"]))
            if strategy == "expanded":
                candidates.append((5, spread, y_symbol, x_symbol, token_y, token_x, pools, "status5_reverse_two_hop_triangular", ["USDC", y_symbol, x_symbol, "USDC"]))
    candidates.sort(key=lambda item: (item[0], -item[1]))
    limit = DEFAULT_RUNTIME_TRADE_SCAN if trade_limit is None else int(trade_limit)
    limit = max(1, min(limit, MAX_RUNTIME_TRADE_SCAN))
    trades = []
    for trade_index, (strategy_status, spread, x_symbol, y_symbol, token_x, token_y, pools, stage, route_symbols) in enumerate(candidates[:limit]):
        trades.append(
            _runtime_trade_with_metadata(
                trade_index=trade_index,
                token_x=token_x,
                token_y=token_y,
                pools=pools,
                strategy_status=strategy_status,
                strategy_stage=stage,
                route_symbols=route_symbols,
                spread=spread,
            )
        )
    return trades


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
            return _runtime_trades_from_market_state(market_state, trade_limit=limit) if market_state else []
        source = json.loads(env_value)
    if not isinstance(source, list):
        raise ValueError("runtime trades must be a list")
    return [
        _normalize_runtime_trade(trade, index)
        for index, trade in enumerate(source[:limit])
    ]


def _runtime_trade_decision_report(result: Any) -> dict[str, Any]:
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
    }


def _runtime_execution_preview_report(result: Any) -> dict[str, Any]:
    return {
        "router": result[0],
        "swapPath": _bytes_to_hex(result[1]),
        "quotedFinalUsdc": str(result[2]),
        "premiumUsdc": str(result[3]),
        "requiredFinalUsdc": str(result[4]),
        "protectedAmountOutMinUsdc": str(result[5]),
        "minProfitUsdc": str(result[6]),
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
        net_profit_usdc = (
            int(preview["quotedFinalUsdc"])
            - int(preview["requiredFinalUsdc"])
            + int(preview["minProfitUsdc"])
        )
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
    runtime_trades = _runtime_trade_candidates(
        {"market_state": signal_market_state},
        None,
        limit=MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
    )
    network, chain_id, testnet = _resolve_direct_onchain_network()
    protocol = {
        "kind": "triangular_route_controller_runtime_auto_v3",
        "enabled": True,
        "network": network,
        "chain_id": chain_id,
        "testnet": testnet,
        "owner_address": _env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "TRIANGULAR_CONTROLLER_OWNER_ADDRESS"),
        "controller_address": _env("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"),
        "executor_address": _env("AAVE_TRIANGULAR_EXECUTOR_ADDRESS", "TRIANGULAR_EXECUTOR_ADDRESS"),
        "runtime_trades": runtime_trades,
        "runtime_trade_limit": DEFAULT_RUNTIME_TRADE_SCAN,
        "runtime_candidate_limit": MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
        "execution_mode": _env("TRIANGULAR_DIRECT_EXECUTION_MODE", default="auto") or "auto",
        "candidate_strategy": _runtime_strategy_from_env(),
        "selection_strategy": "expanded_status_order_safe_auto_then_triangular_fallback",
        "borrow_symbol": intent.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL,
        "route_path": route_path,
        "route_direction": intent.get("route_direction"),
        "expected_profit_usdc": intent.get("expected_profit_amount"),
    }
    intent["direct_onchain_protocol"] = protocol
    intent["intent_protocol"] = "direct_onchain"
    intent["submission_protocol"] = "direct_onchain"
    intent["submission_mode"] = "direct_onchain"
    intent["direct_onchain_ready"] = bool(protocol["controller_address"] and protocol["runtime_trades"])
    return intent


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
        gas_estimate_info = estimate_gas_price(w3)
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
