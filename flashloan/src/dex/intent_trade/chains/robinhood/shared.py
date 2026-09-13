from __future__ import annotations

import os
from typing import Any

from core.chain_data_registry import chain_contracts, chain_data_by_network


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"


def _configured_chain_id(network: str, env_name: str, fallback: int) -> int:
    raw = str(os.getenv(env_name, "")).strip()
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{env_name} must be an integer") from exc
    try:
        row = chain_data_by_network(network) or {}
        value = int(row.get("chain_id"))
        if value > 0:
            return value
    except (TypeError, ValueError, KeyError):
        pass
    return int(fallback)


ROBINHOOD_CHAIN_ID = _configured_chain_id("robinhood", "ROBINHOOD_CHAIN_ID", 4663)
ROBINHOOD_TESTNET_CHAIN_ID = _configured_chain_id("robinhood-testnet", "ROBINHOOD_TESTNET_CHAIN_ID", 46630)

_ROBINHOOD_ALIASES = {
    "robinhood": ROBINHOOD_NETWORK,
    "robinhood-chain": ROBINHOOD_NETWORK,
    "robinhood-mainnet": ROBINHOOD_NETWORK,
    str(ROBINHOOD_CHAIN_ID): ROBINHOOD_NETWORK,
    "robinhood-testnet": ROBINHOOD_TESTNET_NETWORK,
    "robinhood-sepolia": ROBINHOOD_TESTNET_NETWORK,
    str(ROBINHOOD_TESTNET_CHAIN_ID): ROBINHOOD_TESTNET_NETWORK,
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_robinhood_chain_test_network(network: Any = None, chain_id: Any = None) -> str:
    raw_network = _text(network).lower()
    raw_chain = _text(chain_id)
    if raw_network in _ROBINHOOD_ALIASES:
        return _ROBINHOOD_ALIASES[raw_network]
    if raw_chain in _ROBINHOOD_ALIASES:
        return _ROBINHOOD_ALIASES[raw_chain]
    return raw_network


def robinhood_chain_test_scope(network: Any = None, chain_id: Any = None) -> dict[str, Any]:
    normalized = normalize_robinhood_chain_test_network(network, chain_id)
    parsed_chain_id = _int_or_none(chain_id)
    if normalized == ROBINHOOD_NETWORK and parsed_chain_id in (None, ROBINHOOD_CHAIN_ID):
        return {
            "enabled": True,
            "network": ROBINHOOD_NETWORK,
            "chainId": ROBINHOOD_CHAIN_ID,
            "mode": "mainnet_fork_staticcall_only",
            "canCaptureLiveOpportunity": True,
            "canRunForkStaticcall": True,
            "canDeployTestnet": False,
            "canBroadcast": False,
            "disabledReason": None,
        }
    if normalized == ROBINHOOD_TESTNET_NETWORK and parsed_chain_id in (None, ROBINHOOD_TESTNET_CHAIN_ID):
        return {
            "enabled": True,
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
            "mode": "testnet_deploy_readback_only",
            "canCaptureLiveOpportunity": False,
            "canRunForkStaticcall": True,
            "canDeployTestnet": True,
            "canBroadcast": False,
            "disabledReason": None,
        }
    return {
        "enabled": False,
        "network": normalized or _text(network).lower() or "unknown",
        "chainId": parsed_chain_id,
        "mode": "disabled",
        "canCaptureLiveOpportunity": False,
        "canRunForkStaticcall": False,
        "canDeployTestnet": False,
        "canBroadcast": False,
        "disabledReason": "disabled_non_robinhood_chain_test_scope",
    }


def robinhood_chain_config(
    network: str,
    *,
    default_chain_id: int,
    default_market_id: str,
    default_pool_manager: str = "",
) -> dict[str, Any]:
    normalized_network = _text(network).lower()
    try:
        row = chain_data_by_network(normalized_network) or {}
    except Exception:
        row = {}
    features = _as_dict(row.get("features"))
    dex = _as_dict(features.get("dex_arbitrage"))
    try:
        contracts = chain_contracts(normalized_network) if row else {}
    except Exception:
        contracts = {}
    return {
        "network": _text(row.get("network") or normalized_network),
        "chain_id": _int_or_none(row.get("chain_id")) or int(default_chain_id),
        "market_id": _text(dex.get("default_market_id") or default_market_id),
        "pool_manager": _text(contracts.get("v4_pool_manager") or default_pool_manager),
    }


__all__ = [
    "ROBINHOOD_CHAIN_ID",
    "ROBINHOOD_NETWORK",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    "ROBINHOOD_TESTNET_NETWORK",
    "normalize_robinhood_chain_test_network",
    "robinhood_chain_config",
    "robinhood_chain_test_scope",
]
