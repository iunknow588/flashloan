from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from core.market_identity import (
    FEATURE_DEX_ARBITRAGE,
    FEATURE_LIQUIDATION,
    MarketKey,
    canonical_market_id,
    canonical_market_id_from_legacy,
    legacy_market_aliases,
    market_key_from_summary,
    normalize_feature,
    normalize_protocol_id,
    resolve_market_key,
    validate_market_identity_registry,
)
from core.market_profiles import (
    BUILTIN_MARKET_SUMMARIES,
    DEFAULT_AVALANCHE_CHAIN_ID,
    DEFAULT_AVALANCHE_DEX_MARKET_ID,
    DEFAULT_AVALANCHE_MARKET_ID,
    DEFAULT_AVALANCHE_ROUTER_ADDRESS,
    DEFAULT_BNB_CHAIN_ID,
    DEFAULT_BNB_DEX_MARKET_ID,
    DEFAULT_BNB_MARKET_ID,
    DEFAULT_BNB_PANCAKE_ROUTER_ADDRESS,
    DEFAULT_COW_DEX_MARKET_ID,
    DEFAULT_COW_VIRTUAL_CHAIN_ID,
    DEFAULT_ETHEREUM_CHAIN_ID,
    DEFAULT_ETHEREUM_DEX_MARKET_ID,
    DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS,
    DEFAULT_POLYGON_CHAIN_ID,
    DEFAULT_POLYGON_DEX_MARKET_ID,
    DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS,
    DEFAULT_ROBINHOOD_BOT_MARKET_ID,
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    DEFAULT_ROBINHOOD_DEX_MORPHO_MARKET_ID,
    DEFAULT_ROBINHOOD_EXPLORER_URL,
    DEFAULT_ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS,
    DEFAULT_ROBINHOOD_MORPHO_MARKET_ID,
    DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
    DEFAULT_ROBINHOOD_RPC_URL,
    DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID,
    DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_EXPLORER_URL,
    DEFAULT_ROBINHOOD_TESTNET_RPC_URL,
    DEFAULT_ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS,
    DEFAULT_ROBINHOOD_WETH_ADDRESS,
    KNOWN_CHAIN_FEATURE_MARKETS,
    KNOWN_CHAIN_ID_MARKETS,
    KNOWN_EVM_MARKETS,
    LEGACY_AVALANCHE_DEX_MARKET_ID,
    LEGACY_AVALANCHE_MARKET_ID,
    LEGACY_BNB_DEX_MARKET_ID,
    LEGACY_BNB_MARKET_ID,
    LEGACY_COW_DEX_MARKET_ID,
    LEGACY_ETHEREUM_DEX_MARKET_ID,
    LEGACY_POLYGON_DEX_MARKET_ID,
    LEGACY_ROBINHOOD_BOT_MARKET_ID,
    LEGACY_ROBINHOOD_DEX_MARKET_ID,
    LEGACY_ROBINHOOD_DEX_MORPHO_MARKET_ID,
    LEGACY_ROBINHOOD_MORPHO_MARKET_ID,
    LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID,
    LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID,
    MARKET_TYPE_EXECUTION_RAIL,
    MARKET_TYPE_NATIVE_CHAIN,
    MARKET_TYPE_SHADOW_CHAIN,
    PROTOCOL_CAPABILITIES,
)

@dataclass(frozen=True)
class ChainMarketConfig:
    market_id: str
    feature: str
    display_name: str
    network: str
    market_type: str
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
    wrapped_native_address: str = ""
    explorer_url: str = ""
    observation_only: bool = False
    enabled: bool = True
    is_default: bool = False
    legacy_market_id: str = ""

    @property
    def namespace(self) -> str:
        return market_namespace(self.market_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "legacy_market_id": self.legacy_market_id or None,
            "namespace": self.namespace,
            "feature": self.feature,
            "display_name": self.display_name,
            "network": self.network,
            "market_type": self.market_type,
            "protocol": self.protocol,
            "protocol_id": self.protocol,
            "chain_id": self.chain_id,
            "native_symbol": self.native_symbol,
            "wrapped_native_address": self.wrapped_native_address,
            "explorer_url": self.explorer_url,
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
            "observation_only": self.observation_only,
            "enabled": self.enabled,
            "is_default": self.is_default,
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


def _profile_for_market_key(key: MarketKey) -> dict[str, Any]:
    summary = next(
        (dict(profile) for profile in BUILTIN_MARKET_SUMMARIES.values() if str(profile.get("market_id") or "") == key.market_id),
        None,
    )
    if summary is not None:
        summary["market_id"] = key.market_id
        summary["canonical_market_id"] = key.market_id
        summary["legacy_market_id"] = key.legacy_market_id or summary.get("legacy_market_id") or summary.get("market_id")
        summary["feature"] = key.feature
        summary["protocol"] = key.protocol_id
        summary["protocol_id"] = key.protocol_id
        return summary
    network = _env("LIQUIDATION_NETWORK") or "custom"
    return {
        "market_id": key.market_id,
        "canonical_market_id": key.market_id,
        "legacy_market_id": key.legacy_market_id,
        "feature": key.feature,
        "display_name": key.market_id,
        "network": network,
        "chain_id": key.chain_id,
        "native_symbol": _env("LIQUIDATION_NATIVE_SYMBOL") or "ETH",
        "protocol": key.protocol_id,
        "protocol_id": key.protocol_id,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "rpc_env_prefixes": (network.upper().replace("-", "_"),),
        "observation_only": True,
        "protocol_supported": False,
        "enabled": True,
    }


def _known_profile(
    market_id: str,
    *,
    feature: str | None = None,
    chain_id: int | None = None,
    protocol_id: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_market_key(
        market_id=market_id,
        feature=feature,
        chain_id=chain_id,
        protocol_id=protocol_id,
        summaries=list(BUILTIN_MARKET_SUMMARIES.values()),
        network=_env("LIQUIDATION_NETWORK") or None,
    )
    return _profile_for_market_key(resolved)


def _configured_default_market_id(feature: str) -> str:
    try:
        from core.chain_data_registry import default_market_id_for_feature

        return default_market_id_for_feature(feature)
    except Exception:
        return ""


def market_id_for_chain_id(chain_id: int | None) -> str | None:
    if chain_id is None:
        return None
    return KNOWN_CHAIN_ID_MARKETS.get(int(chain_id))


def market_id_for_scope(chain_id: int | None = None, feature: str | None = None) -> str | None:
    if chain_id is None:
        return None
    normalized_feature = normalize_feature(feature) if feature else ""
    if normalized_feature:
        scoped = KNOWN_CHAIN_FEATURE_MARKETS.get((int(chain_id), normalized_feature))
        if scoped:
            return scoped
    return market_id_for_chain_id(chain_id)


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
    if profile.get("network") == "bnb":
        candidates.extend(
            [
                "https://bsc-dataseed.binance.org",
                "https://rpc.ankr.com/bsc",
                "https://bsc.publicnode.com",
            ]
        )
    if profile.get("network") == "ethereum":
        candidates.extend(
            [
                "https://cloudflare-eth.com",
                "https://rpc.ankr.com/eth",
                "https://ethereum.publicnode.com",
            ]
        )
    if profile.get("network") == "polygon":
        candidates.extend(
            [
                "https://polygon-rpc.com",
                "https://rpc.ankr.com/polygon",
                "https://polygon-bor.publicnode.com",
            ]
        )
    if profile.get("network") == "robinhood":
        candidates.extend([DEFAULT_ROBINHOOD_RPC_URL])
    if profile.get("network") == "robinhood-testnet":
        candidates.extend([DEFAULT_ROBINHOOD_TESTNET_RPC_URL])
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _dex_router_for(profile: dict[str, Any]) -> str:
    explicit = _first_env("LIQUIDATION_DEX_ROUTER_ADDRESS")
    if explicit:
        return explicit
    network = str(profile.get("network") or "").strip().lower()
    if network == "bnb":
        return _first_env("BNB_DEX_ROUTER_ADDRESS", "PANCAKE_ROUTER_ADDRESS", "PANCAKESWAP_V2_ROUTER_ADDRESS") or DEFAULT_BNB_PANCAKE_ROUTER_ADDRESS
    if network == "ethereum":
        return _first_env("ETHEREUM_DEX_ROUTER_ADDRESS", "UNISWAP_DEX_ROUTER_ADDRESS", "UNISWAP_V2_ROUTER_ADDRESS") or DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS
    if network == "polygon":
        return _first_env("POLYGON_DEX_ROUTER_ADDRESS", "QUICKSWAP_DEX_ROUTER_ADDRESS", "QUICKSWAP_V2_ROUTER_ADDRESS") or DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS
    if network == "robinhood":
        return _first_env("ROBINHOOD_DEX_ROUTER_ADDRESS")
    if network == "robinhood-testnet":
        return _first_env("ROBINHOOD_TESTNET_DEX_ROUTER_ADDRESS", "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER")
    return _first_env("DEX_ROUTER_ADDRESS") or DEFAULT_AVALANCHE_ROUTER_ADDRESS


def liquidation_market_config(*, chain_id: int | None = None) -> ChainMarketConfig:
    market_id = _env("LIQUIDATION_MARKET_ID") or None
    protocol_id = _env("LIQUIDATION_PROTOCOL") or None
    if chain_id is not None:
        return liquidation_market_config_for(
            market_id=market_id,
            chain_id=chain_id,
            protocol_id=protocol_id,
        )
    raw_chain_id = _first_env("LIQUIDATION_CHAIN_ID", "CHAIN_ID")
    if raw_chain_id:
        return liquidation_market_config_for(
            market_id=market_id,
            chain_id=_parse_int(raw_chain_id, DEFAULT_AVALANCHE_CHAIN_ID),
            protocol_id=protocol_id,
        )
    return liquidation_market_config_for(
        market_id=market_id or _configured_default_market_id("liquidation") or DEFAULT_AVALANCHE_MARKET_ID,
        chain_id=None,
        feature=FEATURE_LIQUIDATION if not market_id else None,
        protocol_id=protocol_id,
    )


def liquidation_market_scope_is_explicit() -> bool:
    return any(
        _env(name)
        for name in (
            "LIQUIDATION_MARKET_ID",
            "LIQUIDATION_CHAIN_ID",
            "CHAIN_ID",
            "LIQUIDATION_PROTOCOL",
        )
    )


def liquidation_market_config_for(
    market_id: str | None = None,
    *,
    chain_id: int | None = None,
    feature: str | None = None,
    protocol_id: str | None = None,
) -> ChainMarketConfig:
    resolved_feature = normalize_feature(feature) if feature is not None else None
    if resolved_feature is None and market_id is None:
        resolved_feature = FEATURE_LIQUIDATION
    resolved_protocol_id = (
        normalize_protocol_id(protocol_id)
        if protocol_id is not None and str(protocol_id).strip()
        else None
    )
    summary_rows = list(BUILTIN_MARKET_SUMMARIES.values())
    resolved_key = resolve_market_key(
        market_id=market_id,
        feature=resolved_feature,
        chain_id=chain_id,
        protocol_id=resolved_protocol_id,
        summaries=summary_rows,
        network=_env("LIQUIDATION_NETWORK") or None,
    )
    profile = _profile_for_market_key(resolved_key)
    profile_chain_value = profile.get("chain_id")
    profile_chain_id = int(
        DEFAULT_AVALANCHE_CHAIN_ID
        if profile_chain_value is None
        else profile_chain_value
    )
    profile_feature = str(profile.get("feature") or FEATURE_LIQUIDATION).strip().lower()
    if chain_id is not None and int(chain_id) != profile_chain_id:
        raise ValueError(
            f"market_id {resolved_key.market_id} belongs to chain_id {profile_chain_id}, not {int(chain_id)}"
        )
    if resolved_feature is not None and profile_feature != resolved_feature:
        raise ValueError(
            f"market_id {resolved_key.market_id} belongs to feature {profile_feature}, not {resolved_feature}"
        )
    protocol = str(profile.get("protocol_id") or profile.get("protocol") or resolved_protocol_id or _env("LIQUIDATION_PROTOCOL") or "aave_v3").strip().lower()
    protocol_supported = bool(profile.get("protocol_supported", PROTOCOL_CAPABILITIES.get(protocol, {}).get("supported")))
    resolved_chain_id = profile_chain_id
    native_symbol = (_env("LIQUIDATION_NATIVE_SYMBOL") or str(profile.get("native_symbol") or "ETH")).upper()
    capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
    configured_default_market_id = canonical_market_id_from_legacy(_configured_default_market_id(profile_feature)) or _configured_default_market_id(profile_feature)
    return ChainMarketConfig(
        market_id=str(profile.get("market_id") or resolved_key.market_id),
        feature=str(profile.get("feature") or resolved_feature or FEATURE_LIQUIDATION),
        display_name=str(profile.get("display_name") or resolved_key.market_id),
        network=(_env("LIQUIDATION_NETWORK") or str(profile.get("network") or "custom")).strip().lower(),
        market_type=str(profile.get("market_type") or MARKET_TYPE_NATIVE_CHAIN),
        protocol=protocol,
        chain_id=resolved_chain_id,
        native_symbol=native_symbol,
        rpc_urls=_rpc_urls_for(profile),
        pool_address=_first_env("LIQUIDATION_POOL_ADDRESS")
        or str(profile.get("pool_address") or "")
        or (_first_env("AAVE_POOL_ADDRESS") if protocol in {"aave_v3", "hyper_evm_aave_v3"} else ""),
        protocol_data_provider_address=_first_env("LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS", "AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS"),
        liquidation_data_provider_address=_first_env("LIQUIDATION_DATA_PROVIDER_ADDRESS", "AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS"),
        dex_router_address=_dex_router_for(profile),
        executor_address=_first_env("LIQUIDATION_EXECUTOR_ADDRESS"),
        executor_owner_address=_first_env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS"),
        multicall3_address=_first_env(
            "LIQUIDATION_MULTICALL3_ADDRESS",
        )
        or str(profile.get("multicall3_address") or "0xcA11bde05977b3631167028862bE2a173976CA11"),
        protocol_supported=protocol_supported,
        evm_compatible=bool(capabilities.get("evm")),
        protocol_adapter=str(capabilities.get("adapter") or "ProtocolAdapter"),
        executor_adapter=str(capabilities.get("executor") or "LiquidationExecutor"),
        wrapped_native_address=str(profile.get("wrapped_native_address") or ""),
        explorer_url=str(profile.get("explorer_url") or ""),
        observation_only=bool(profile.get("observation_only")) or not protocol_supported,
        enabled=bool(profile.get("enabled", True)),
        is_default=resolved_key.market_id == configured_default_market_id
        or (not configured_default_market_id and bool(profile.get("is_default")))
        or (not configured_default_market_id and resolved_key.market_id == DEFAULT_AVALANCHE_MARKET_ID),
        legacy_market_id=str(profile.get("legacy_market_id") or resolved_key.legacy_market_id or profile.get("market_id") or ""),
    )


def _normalize_supported_market_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    try:
        key = market_key_from_summary(row)
    except Exception:
        return dict(row)
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    market_type = row.get("market_type") or metadata.get("market_type") or MARKET_TYPE_NATIVE_CHAIN
    protocol = str(row.get("protocol_id") or row.get("protocol") or key.protocol_id).strip().lower()
    summary = {
        **dict(row),
        "market_id": key.market_id,
        "canonical_market_id": key.market_id,
        "legacy_market_id": row.get("legacy_market_id") or key.legacy_market_id or row.get("market_id"),
        "feature": key.feature,
        "protocol": protocol,
        "protocol_id": protocol,
        "network": str(row.get("network") or "").strip().lower(),
        "chain_id": key.chain_id,
        "market_type": str(market_type or MARKET_TYPE_NATIVE_CHAIN),
    }
    return summary


def _builtin_supported_market_summaries() -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    default_market_ids: dict[str, str] = {}
    for market_id, profile in BUILTIN_MARKET_SUMMARIES.items():
        feature = str(profile.get("feature") or FEATURE_LIQUIDATION)
        if feature not in default_market_ids:
            default_market_ids[feature] = canonical_market_id_from_legacy(_configured_default_market_id(feature)) or _configured_default_market_id(feature)
        protocol = str(profile.get("protocol_id") or profile.get("protocol") or "")
        capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
        summary = {
            **dict(profile),
            "market_id": str(profile.get("market_id") or market_id),
            "canonical_market_id": str(profile.get("market_id") or market_id),
            "legacy_market_id": str(profile.get("legacy_market_id") or market_id),
            "feature": feature,
            "protocol": protocol,
            "protocol_id": protocol,
            "protocol_supported": bool(profile.get("protocol_supported", capabilities.get("supported"))),
            "observation_only": bool(profile.get("observation_only"))
            or not bool(profile.get("protocol_supported", capabilities.get("supported"))),
            "is_default": str(profile.get("market_id") or market_id) == default_market_ids.get(feature)
            or (not default_market_ids.get(feature) and bool(profile.get("is_default")))
            or (not default_market_ids.get(feature) and str(profile.get("market_id") or market_id) == DEFAULT_AVALANCHE_MARKET_ID),
            "migration_type": profile.get("migration_type") or "config_and_new_executor_deployment",
            "pool_address": profile.get("pool_address") or "",
        }
        summaries.append(summary)
    validate_market_identity_registry(summaries)
    return summaries


def supported_market_summaries(database_url: str | None = None) -> list[dict[str, Any]]:
    if database_url:
        from core.db.storage_common import (
            database_unavailable_reason,
            is_database_unavailable_error,
            mark_database_unavailable,
        )

        if database_unavailable_reason(database_url):
            return _builtin_supported_market_summaries()
        try:
            from core.db.storage_chain_markets import load_chain_market_summaries

            rows = load_chain_market_summaries(database_url)
            if rows:
                return [_normalize_supported_market_row(row) for row in rows]
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
    return _builtin_supported_market_summaries()
