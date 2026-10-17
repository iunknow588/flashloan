"""Central live-quote RPC gate for Robinhood V4 automatic paths.

``DEX_V4_LIVE_QUOTE_ENABLED`` keeps its documented semantics: an explicit
``1/true/yes/on`` enables automatic live quoting, an explicit ``0`` disables it,
and Robinhood ``4663``/``46630`` default to enabled when the variable is unset.
Explicit maintenance/refresh tools may bypass this gate by invoking their own
RPC entry points directly with the variable enabled.
"""

from __future__ import annotations

import os

from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID,
)

_ENABLED_VALUES = {"1", "true", "yes", "on"}
_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}


def v4_live_quote_enabled(chain_id: int | None = None) -> bool:
    raw = os.getenv("DEX_V4_LIVE_QUOTE_ENABLED")
    if raw is not None:
        return str(raw).strip().lower() in _ENABLED_VALUES
    return int(chain_id or 0) in _ROBINHOOD_CHAIN_IDS


def require_v4_live_quote(chain_id: int | None = None, *, operation: str = "V4 live quote") -> None:
    if not v4_live_quote_enabled(chain_id=chain_id):
        raise RuntimeError(
            f"{operation} disabled: set DEX_V4_LIVE_QUOTE_ENABLED=1 for chain_id={chain_id}"
        )


__all__ = ["require_v4_live_quote", "v4_live_quote_enabled"]
