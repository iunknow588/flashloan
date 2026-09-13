from importlib import import_module
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID as _ROBINHOOD_TESTNET_CHAIN_ID


ROBINHOOD_TESTNET_CHAIN_ID = _ROBINHOOD_TESTNET_CHAIN_ID


def _chain_observation_module(chain_id: int | None):
    if chain_id is None:
        return None
    row = chain_data_by_chain_id(chain_id)
    network = str((row or {}).get("network") or "").strip().lower()
    if not network:
        return None
    module_name = f"dex.intent_trade.chains.{network.replace('-', '_')}.market_observation"
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


def _chain_observation_function(chain_id: int | None, name: str):
    module = _chain_observation_module(chain_id)
    if module is None:
        return None
    function = getattr(module, name, None)
    return function if callable(function) else None


def _chain_network_name(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    row = chain_data_by_chain_id(chain_id) or {}
    return str(row.get("network") or "").strip().lower()
