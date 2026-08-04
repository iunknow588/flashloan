from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


DEFAULT_AVALANCHE_MARKET_ID = "avalanche-aave-v3"
DEFAULT_AVALANCHE_CHAIN_ID = 43114


KNOWN_EVM_MARKETS: dict[str, dict[str, Any]] = {
    DEFAULT_AVALANCHE_MARKET_ID: {
        "network": "avalanche",
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": "AVAX",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("AVALANCHE",),
    },
    "bnb-aave-v3": {
        "network": "bnb",
        "chain_id": 56,
        "native_symbol": "BNB",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("BNB", "BSC"),
    },
}

KNOWN_CHAIN_ID_MARKETS: dict[int, str] = {
    int(profile["chain_id"]): market_id
    for market_id, profile in KNOWN_EVM_MARKETS.items()
    if isinstance(profile.get("chain_id"), int)
}


PROTOCOL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "aave_v3": {
        "evm": True,
        "adapter": "AaveV3Adapter",
        "executor": "EvmLiquidationExecutor",
        "supported": True,
        "notes": "EVM Aave V3 style health factor, reserve data, liquidationCall, and executor payloads.",
    },
    "hyperliquid_native": {
        "evm": False,
        "adapter": "HyperliquidNativeAdapter",
        "executor": "HyperliquidLiquidationExecutor",
        "supported": False,
        "notes": "Native margin/position liquidation needs a separate protocol adapter; Aave executor contracts do not apply.",
    },
    "hyper_evm_aave_v3": {
        "evm": True,
        "adapter": "AaveV3Adapter",
        "executor": "EvmLiquidationExecutor",
        "supported": True,
        "notes": "Only valid when the market exposes Aave V3 compatible contracts on HyperEVM.",
    },
}


@dataclass(frozen=True)
class ChainMarketConfig:
    market_id: str
    network: str
    protocol: str
    chain_id: int
    native_symbol: str
    rpc_urls: tuple[str, ...]
    pool_address: str
    protocol_data_provider_address: str
    liquidation_data_provider_address: str
    dex_router_address: str
    executor_address: str
    executor_owner_address: str
    multicall3_address: str
    protocol_supported: bool
    evm_compatible: bool
    protocol_adapter: str
    executor_adapter: str

    @property
    def namespace(self) -> str:
        return market_namespace(self.market_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "namespace": self.namespace,
            "network": self.network,
            "protocol": self.protocol,
            "chain_id": self.chain_id,
            "native_symbol": self.native_symbol,
            "rpc_urls": list(self.rpc_urls),
            "pool_address": self.pool_address,
            "protocol_data_provider_address": self.protocol_data_provider_address,
            "liquidation_data_provider_address": self.liquidation_data_provider_address,
            "dex_router_address": self.dex_router_address,
            "executor_address": self.executor_address,
            "executor_owner_address": self.executor_owner_address,
            "multicall3_address": self.multicall3_address,
            "protocol_supported": self.protocol_supported,
            "evm_compatible": self.evm_compatible,
            "protocol_adapter": self.protocol_adapter,
            "executor_adapter": self.executor_adapter,
        }


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def _parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)


def _split_urls(value: str) -> list[str]:
    urls: list[str] = []
    for item in str(value or "").replace("\n", ",").replace(";", ",").split(","):
        candidate = item.strip().rstrip("/")
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def market_namespace(market_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(market_id or "").lower()).strip("-")
    return slug or DEFAULT_AVALANCHE_MARKET_ID


def _known_profile(market_id: str) -> dict[str, Any]:
    normalized = market_namespace(market_id)
    if normalized in KNOWN_EVM_MARKETS:
        return dict(KNOWN_EVM_MARKETS[normalized])
    network = _env("LIQUIDATION_NETWORK") or normalized.split("-")[0] or "custom"
    protocol = _env("LIQUIDATION_PROTOCOL") or ("aave_v3" if "aave" in normalized else "custom")
    return {
        "network": network,
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": _env("LIQUIDATION_NATIVE_SYMBOL") or "ETH",
        "protocol": protocol,
        "rpc_env_prefixes": (network.upper().replace("-", "_"),),
    }


def market_id_for_chain_id(chain_id: int | None) -> str | None:
    if chain_id is None:
        return None
    return KNOWN_CHAIN_ID_MARKETS.get(int(chain_id))


def _rpc_urls_for(profile: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    for raw in (
        _env("LIQUIDATION_RPCS"),
        _env("LIQUIDATION_RPC_URLS"),
        _env("LIQUIDATION_RPC"),
        _env("LIQUIDATION_RPC_URL"),
    ):
        candidates.extend(_split_urls(raw))
    for prefix in profile.get("rpc_env_prefixes") or ():
        candidates.extend(_split_urls(_env(f"{prefix}_RPCS")))
        candidates.extend(_split_urls(_env(f"{prefix}_RPC")))
        candidates.extend(_split_urls(_env(f"{prefix}_RPC_URL")))
    if profile.get("network") == "avalanche":
        candidates.extend(
            [
                "https://api.avax.network/ext/bc/C/rpc",
                "https://rpc.ankr.com/avalanche",
                "https://avalanche-c-chain-rpc.publicnode.com",
            ]
        )
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def liquidation_market_config() -> ChainMarketConfig:
    raw_chain_id = _first_env("LIQUIDATION_CHAIN_ID", "CHAIN_ID")
    return liquidation_market_config_for(
        market_id=_env("LIQUIDATION_MARKET_ID") or DEFAULT_AVALANCHE_MARKET_ID,
        chain_id=_parse_int(raw_chain_id, DEFAULT_AVALANCHE_CHAIN_ID) if raw_chain_id else None,
    )


def liquidation_market_config_for(
    market_id: str | None = None,
    *,
    chain_id: int | None = None,
) -> ChainMarketConfig:
    resolved_market_id = market_namespace(market_id or market_id_for_chain_id(chain_id) or DEFAULT_AVALANCHE_MARKET_ID)
    profile = _known_profile(resolved_market_id)
    protocol = (_env("LIQUIDATION_PROTOCOL") or str(profile.get("protocol") or "aave_v3")).strip().lower()
    resolved_chain_id = int(chain_id if chain_id is not None else profile.get("chain_id") or DEFAULT_AVALANCHE_CHAIN_ID)
    native_symbol = (_env("LIQUIDATION_NATIVE_SYMBOL") or str(profile.get("native_symbol") or "ETH")).upper()
    capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
    return ChainMarketConfig(
        market_id=resolved_market_id,
        network=(_env("LIQUIDATION_NETWORK") or str(profile.get("network") or "custom")).strip().lower(),
        protocol=protocol,
        chain_id=resolved_chain_id,
        native_symbol=native_symbol,
        rpc_urls=_rpc_urls_for(profile),
        pool_address=_first_env("LIQUIDATION_POOL_ADDRESS", "AAVE_POOL_ADDRESS"),
        protocol_data_provider_address=_first_env("LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS", "AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS"),
        liquidation_data_provider_address=_first_env("LIQUIDATION_DATA_PROVIDER_ADDRESS", "AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS"),
        dex_router_address=_first_env("LIQUIDATION_DEX_ROUTER_ADDRESS", "DEX_ROUTER_ADDRESS"),
        executor_address=_first_env("LIQUIDATION_EXECUTOR_ADDRESS"),
        executor_owner_address=_first_env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS"),
        multicall3_address=_first_env(
            "LIQUIDATION_MULTICALL3_ADDRESS",
        )
        or "0xcA11bde05977b3631167028862bE2a173976CA11",
        protocol_supported=bool(capabilities.get("supported")),
        evm_compatible=bool(capabilities.get("evm")),
        protocol_adapter=str(capabilities.get("adapter") or "ProtocolAdapter"),
        executor_adapter=str(capabilities.get("executor") or "LiquidationExecutor"),
    )


def supported_market_summaries() -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for market_id, profile in KNOWN_EVM_MARKETS.items():
        protocol = str(profile["protocol"])
        capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
        summaries.append(
            {
                "market_id": market_id,
                "network": profile["network"],
                "chain_id": profile["chain_id"],
                "native_symbol": profile["native_symbol"],
                "protocol": protocol,
                "protocol_supported": bool(capabilities.get("supported")),
                "migration_type": "config_and_new_executor_deployment",
            }
        )
    summaries.append(
        {
            "market_id": "hyperliquid-native",
            "network": "hyperliquid",
            "chain_id": None,
            "native_symbol": "HYPE",
            "protocol": "hyperliquid_native",
            "protocol_supported": False,
            "migration_type": "new_protocol_adapter_required",
        }
    )
    return summaries
