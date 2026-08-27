from __future__ import annotations

from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from intent_trade.flows.flash_loan_pools.presenter import _empty_protocol_view


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    **_: Any,
) -> dict[str, Any]:
    return _empty_protocol_view(request.protocol, status="protocol_pool_cache_not_available")


__all__ = ["flash_loan_pool_protocol_view"]
