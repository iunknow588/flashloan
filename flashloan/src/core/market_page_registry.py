from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from core.chain_data_registry import default_chain_scope
from core.chain_selection import ChainSelection, resolve_chain_selection
from core.market_config import (
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    MARKET_TYPE_EXECUTION_RAIL,
    MARKET_TYPE_SHADOW_CHAIN,
    MARKET_TYPE_NATIVE_CHAIN,
)


@dataclass(frozen=True)
class MarketPageVariant:
    page_key: str
    workflow: str
    feature: str
    selection: ChainSelection
    template_kind: str
    surface: str

    @property
    def market_id(self) -> str:
        return self.selection.market_id

    @property
    def chain_id(self) -> int | None:
        return self.selection.chain_id

    @property
    def is_robinhood_shadow(self) -> bool:
        return self.selection.market_type == MARKET_TYPE_SHADOW_CHAIN

    @property
    def is_cow_execution_rail(self) -> bool:
        return self.selection.market_type == MARKET_TYPE_EXECUTION_RAIL

    @property
    def is_native_chain(self) -> bool:
        return self.selection.market_type == MARKET_TYPE_NATIVE_CHAIN

    @property
    def observation_only(self) -> bool:
        return bool(self.selection.observation_only)


def _path_key(path: str) -> str:
    return urlsplit(str(path or "/")).path or "/"


def _page_definition(path: str) -> tuple[str, str, str, str, str] | None:
    pathname = _path_key(path)
    if pathname in {
        "/",
        "/bot",
        "/liquidation",
        "/bot/borrow-pool",
        "/account-scan",
        "/bot/account-scan",
        "/audit",
        "/bot/audit",
        "/execution",
        "/bot/execution",
        "/liquidation/account",
    }:
        return "liquidation", "liquidation", "liquidation", "liquidation", "liquidation"
    dex_surfaces = {
        "/dex": "quotes",
        "/dex/overview": "overview",
        "/dex/market-observation": "observation",
        "/market-observation": "observation",
        "/dex/candidates": "candidates",
        "/dex-pools": "candidates",
        "/candidate-pools": "candidates",
        "/dex/quotes": "quotes",
        "/binance-market": "quotes",
        "/dex-arbitrage": "quotes",
        "/dex/submissions": "submissions",
        "/dex/submissions/success": "submissions",
        "/dex/submissions/failed": "submissions_failed",
    }
    if pathname in dex_surfaces:
        return "dex_arbitrage", "dex_arbitrage", "dex_arbitrage", "dex", dex_surfaces[pathname]
    if pathname.startswith("/dex/submissions/failed/"):
        return "dex_arbitrage", "dex_arbitrage", "dex_arbitrage", "dex", "submissions_failed"
    if pathname in {
        "/dex/robinhood-shadow",
        "/robinhood-dex",
        "/robinhood-dex/candidates",
    } or pathname.startswith("/api/robinhood-dex"):
        return "robinhood_dex", "dex_arbitrage", "dex_arbitrage", "robinhood_shadow", "observation"
    return None


def resolve_market_page_variant(
    path: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
    protocol_id: str | None = None,
    database_url: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> MarketPageVariant | None:
    pathname = _path_key(path)
    definition = _page_definition(path)
    if definition is None:
        return None
    page_key, workflow, feature, template_kind, surface = definition
    if default_market_id is None or default_chain_id is None:
        if template_kind == "robinhood_shadow":
            default_market_id = default_market_id or DEFAULT_ROBINHOOD_DEX_MARKET_ID
            default_chain_id = default_chain_id or DEFAULT_ROBINHOOD_CHAIN_ID
        else:
            scope = default_chain_scope(feature)
            default_market_id = default_market_id or str(scope["market_id"])
            default_chain_id = default_chain_id or int(scope["chain_id"])
    selection = resolve_chain_selection(
        market_id=market_id,
        chain_id=chain_id,
        feature=feature,
        protocol_id=protocol_id,
        database_url=database_url,
        default_market_id=default_market_id,
    )
    return MarketPageVariant(
        page_key=page_key,
        workflow=workflow,
        feature=feature,
        selection=selection,
        template_kind=(
            "robinhood_shadow"
            if selection.market_type == MARKET_TYPE_SHADOW_CHAIN and feature == "dex_arbitrage"
            else "cow_execution"
            if selection.market_type == MARKET_TYPE_EXECUTION_RAIL
            else "single_chain"
            if selection.market_type == MARKET_TYPE_NATIVE_CHAIN and feature == "dex_arbitrage"
            else template_kind
        ),
        surface=surface,
    )
