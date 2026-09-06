from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.robinhood.flash_loan_pool_views import (
    robinhood_dex_protocol_view,
    robinhood_flash_protocol_view,
)


def flash_loan_pool_protocol_view(
    *,
    request: ChainFlowRequest,
    robinhood_evidence_root: Path | None = None,
    robinhood_flash_liquidity_loader: Callable[[], dict[str, Any]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol = request.protocol
    if protocol in {"morpho_blue", "uniswap_v2"}:
        return robinhood_flash_protocol_view(
            protocol,
            snapshot_loader=robinhood_flash_liquidity_loader,
        )
    return robinhood_dex_protocol_view(protocol, evidence_root=robinhood_evidence_root)


__all__ = ["flash_loan_pool_protocol_view"]
