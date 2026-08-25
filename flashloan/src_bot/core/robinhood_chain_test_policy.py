from __future__ import annotations

from typing import Any


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_CHAIN_ID = 46630

_ROBINHOOD_ALIASES = {
    "robinhood": ROBINHOOD_NETWORK,
    "robinhood-chain": ROBINHOOD_NETWORK,
    "robinhood-mainnet": ROBINHOOD_NETWORK,
    "4663": ROBINHOOD_NETWORK,
    "robinhood-testnet": ROBINHOOD_TESTNET_NETWORK,
    "robinhood-sepolia": ROBINHOOD_TESTNET_NETWORK,
    "46630": ROBINHOOD_TESTNET_NETWORK,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
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
            "canRunForkStaticcall": False,
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


__all__ = [
    "ROBINHOOD_CHAIN_ID",
    "ROBINHOOD_NETWORK",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    "ROBINHOOD_TESTNET_NETWORK",
    "normalize_robinhood_chain_test_network",
    "robinhood_chain_test_scope",
]
