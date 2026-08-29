from __future__ import annotations

from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from intent_trade.chains.robinhood_testnet.flash_pool_view import robinhood_testnet_flash_protocol_view
from intent_trade.flows.flash_loan_pools.presenter import _empty_protocol_view


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    robinhood_testnet_v4_pool_liquidity_loader=None,
    robinhood_testnet_v4_pool_index_loader=None,
    **_: Any,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if robinhood_testnet_v4_pool_liquidity_loader is not None:
        options["pool_liquidity_loader"] = robinhood_testnet_v4_pool_liquidity_loader
    if robinhood_testnet_v4_pool_index_loader is not None:
        options["pool_index_loader"] = robinhood_testnet_v4_pool_index_loader
    return robinhood_testnet_flash_protocol_view(
        request.protocol,
        empty_protocol_view=_empty_protocol_view,
        **options,
    )


__all__ = ["flash_loan_pool_protocol_view"]
