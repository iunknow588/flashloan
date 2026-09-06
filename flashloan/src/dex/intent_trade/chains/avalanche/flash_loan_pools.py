from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.avalanche.flash_loan_pool_views import avalanche_dex_protocol_view
from dex.intent_trade.flows.flash_loan_pools.aave import aave_protocol_view
from dex.intent_trade.flows.flash_loan_pools.presenter import _empty_protocol_view


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    aave_cache_path: Path | None = None,
    stable_target_cache_path: Path | None = None,
    avalanche_v3_pool_static_path: Path | None = None,
    avalanche_v2_pool_discovery_path: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol = request.protocol
    if protocol == "aave_v3":
        return aave_protocol_view(
            aave_cache_path=aave_cache_path,
            stable_target_cache_path=stable_target_cache_path,
            chain_id=request.chain_id,
        )
    if protocol in {"uniswap_v3", "traderjoe_v2"}:
        return avalanche_dex_protocol_view(
            protocol,
            avalanche_v3_pool_static_path=avalanche_v3_pool_static_path,
            avalanche_v2_pool_discovery_path=avalanche_v2_pool_discovery_path,
        )
    return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")


__all__ = ["flash_loan_pool_protocol_view"]
