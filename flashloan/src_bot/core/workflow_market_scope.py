from __future__ import annotations

from core.market_config import DEFAULT_ROBINHOOD_DEX_MARKET_ID


def validate_robinhood_market_network(market_id: str, selected_networks: tuple[str, ...]) -> None:
    if market_id != DEFAULT_ROBINHOOD_DEX_MARKET_ID:
        return
    if selected_networks:
        from core.workflow import WorkflowContextError

        raise WorkflowContextError(
            "market_network_mismatch",
            "Robinhood DEX shadow is a market observation variant and does not accept CoW network selection",
        )
