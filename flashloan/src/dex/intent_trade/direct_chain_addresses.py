"""Chain-level address resolution for direct on-chain execution."""

from __future__ import annotations

import os
from typing import Any


def _impl():
    from dex.intent_trade import direct_impl

    return direct_impl


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "0x...":
            return value
    return default


def _normalize_network(value: Any) -> str:
    from dex.intent_trade.direct_onchain_config import normalize_direct_onchain_network

    return normalize_direct_onchain_network(value)


def _normalize_pair_address(value: Any) -> str:
    from dex.intent_trade.direct_onchain_config import normalize_pair_address

    return normalize_pair_address(value)


def chain_contracts_for_network(network: str) -> dict[str, str]:
    from core.chain_data_registry import chain_contracts

    normalized = _normalize_network(network)
    config = _impl().DIRECT_ONCHAIN_NETWORKS.get(normalized)
    if not config:
        return {}
    return chain_contracts(chain_id=int(config["chain_id"])) or {}


def unified_executor_address_for_network(network: str) -> str:
    normalized = _normalize_network(network)
    contracts = chain_contracts_for_network(normalized)
    configured = _normalize_pair_address(
        contracts.get("unified_executor") or contracts.get("unified_executor_address")
    )
    if configured:
        return configured
    # Keep the old env keys as a migration fallback. Chain-specific addresses
    # should now live in chains/<network>/chain.json under contracts.
    return _normalize_pair_address(
        _env(
            "UNIFIED_EXECUTOR_ADDRESS",
            "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
            "AAVE_TRIANGULAR_EXECUTOR_ADDRESS",
        )
    )


def owner_address_for_network(network: str) -> str:
    normalized = _normalize_network(network)
    contracts = chain_contracts_for_network(normalized)
    configured = _normalize_pair_address(contracts.get("owner") or contracts.get("owner_address"))
    if configured:
        return configured
    return _normalize_pair_address(
        _env(
            "LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
            "TRIANGULAR_CONTROLLER_OWNER_ADDRESS",
        )
    )
