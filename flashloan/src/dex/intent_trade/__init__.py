"""Lazy public boundary for the intent-trade package.

Importing any ``dex.intent_trade`` submodule used to eagerly load the whole
direct/CoW stack (and with it web3), which added several seconds to the first
page API request.  The public names are now resolved on demand so read-only
page handlers that only touch Robinhood pool views stay fast.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_FUNCTION_SOURCES = {
    "_bind_cow_intent_context": "dex.intent_trade.context",
    "bind_cow_intent_context": "dex.intent_trade.context",
    "build_cow_intent_trade": "dex.intent_trade.builder",
    "build_triangular_onchain_intent_trade": "dex.intent_trade.direct_impl",
    "pair_index_from_tokenx_tokeny": "dex.intent_trade.direct_impl",
    "set_usdc_pair_memory_table": "dex.intent_trade.direct_impl",
    "submit_direct_onchain_trade": "dex.intent_trade.direct_impl",
    "submit_cow_intent_trade": "dex.intent_trade.submission",
}

__all__ = [
    "_bind_cow_intent_context",
    "bind_cow_intent_context",
    "build_cow_intent_trade",
    "build_triangular_onchain_intent_trade",
    "pair_index_from_tokenx_tokeny",
    "set_usdc_pair_memory_table",
    "submit_direct_onchain_trade",
    "submit_cow_intent_trade",
]


def __getattr__(name: str) -> Any:
    source = _FUNCTION_SOURCES.get(name)
    if source is not None:
        value = getattr(import_module(source), name)
    else:
        try:
            value = import_module(f"{__name__}.{name}")
        except ImportError:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value
