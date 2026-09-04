from __future__ import annotations

from core.market_identity import canonical_market_id_from_legacy
from core.market_config import DEFAULT_ROBINHOOD_DEX_MARKET_ID


def validate_robinhood_market_network(market_id: str, selected_networks: tuple[str, ...]) -> None:
    normalized_market_id = canonical_market_id_from_legacy(market_id) or str(market_id or "").strip().lower()
    robinhood_market_id = canonical_market_id_from_legacy(DEFAULT_ROBINHOOD_DEX_MARKET_ID) or DEFAULT_ROBINHOOD_DEX_MARKET_ID
    if normalized_market_id != robinhood_market_id:
        return
    if selected_networks:
        from core.workflow import WorkflowContextError

        raise WorkflowContextError(
            "market_network_mismatch",
            "Robinhood DEX shadow is a market observation variant and does not accept CoW network selection",
        )
