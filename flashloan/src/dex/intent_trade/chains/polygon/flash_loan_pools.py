from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.flows.flash_loan_pools.aave import aave_protocol_view
from dex.intent_trade.flows.flash_loan_pools.presenter import _empty_protocol_view


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    aave_cache_path: Path | None = None,
    stable_target_cache_path: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    if request.protocol == "aave_v3":
        return aave_protocol_view(
            aave_cache_path=aave_cache_path,
            stable_target_cache_path=stable_target_cache_path,
            chain_id=request.chain_id,
        )
    return _empty_protocol_view(request.protocol, status="protocol_pool_cache_not_available")


__all__ = ["flash_loan_pool_protocol_view"]
