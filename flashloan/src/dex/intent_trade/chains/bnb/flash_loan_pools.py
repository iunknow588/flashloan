from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.bnb.flash_loan_pool_views import bnb_dex_protocol_view, venus_protocol_view
from dex.intent_trade.flows.flash_loan_pools.aave import aave_protocol_view
from dex.intent_trade.flows.flash_loan_pools.presenter import _empty_protocol_view


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    aave_cache_path: Path | None = None,
    stable_target_cache_path: Path | None = None,
    venus_market_path: Path | None = None,
    dex_pool_static_path: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol = request.protocol
    if protocol == "aave_v3":
        return aave_protocol_view(
            aave_cache_path=aave_cache_path,
            stable_target_cache_path=stable_target_cache_path,
            chain_id=request.chain_id,
        )
    if protocol == "venus_core":
        return venus_protocol_view(chain_id=request.chain_id, venus_market_path=venus_market_path)
    if protocol in {"pancakeswap_v2", "pancakeswap_v3", "biswap_v2"}:
        return bnb_dex_protocol_view(
            protocol,
            chain_id=request.chain_id,
            dex_pool_static_path=dex_pool_static_path,
        )
    return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")


__all__ = ["flash_loan_pool_protocol_view"]
