from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


WORKFLOW_LIQUIDATION = "liquidation"
WORKFLOW_DEX_ARBITRAGE = "dex_arbitrage"
WORKFLOW_CHAIN_NEUTRAL = "chain_neutral"
SCOPE_SINGLE_CHAIN = "single_chain"
SCOPE_CHAIN_NEUTRAL = "chain_neutral"
EXECUTION_MODE_DIRECT = "direct"
EXECUTION_MODE_COW = "cow"


@dataclass(frozen=True)
class PageScopeContract:
    page_key: str
    workflow: str
    selection_required: bool
    scope_type: str
    default_execution_mode: str | None
    allowed_execution_modes: tuple[str, ...]
    data_filter_mode: str
    notes: str

    def as_dict(self) -> dict[str, object]:
        return {
            "page_key": self.page_key,
            "workflow": self.workflow,
            "selection_required": self.selection_required,
            "scope_type": self.scope_type,
            "default_execution_mode": self.default_execution_mode,
            "allowed_execution_modes": list(self.allowed_execution_modes),
            "data_filter_mode": self.data_filter_mode,
            "notes": self.notes,
        }


_LIQUIDATION_PATHS = {
    "/", "/bot", "/liquidation", "/bot/borrow-pool", "/account-scan",
    "/bot/account-scan", "/audit", "/bot/audit", "/execution", "/bot/execution",
    "/liquidation/account",
}
_DEX_PATHS = {
    "/dex", "/dex/market-observation", "/dex/candidates", "/dex/quotes",
    "/dex/submissions", "/dex/robinhood-shadow", "/binance-market", "/dex-arbitrage",
    "/dex-pools", "/candidate-pools", "/robinhood-dex", "/robinhood-dex/candidates",
    "/market-observation",
}
_CHAIN_NEUTRAL_PATHS = {
    "/exchange-matrix", "/analytics/exchange-matrix", "/opportunity-health",
    "/analytics/opportunity-health", "/api/exchange-matrix", "/api/opportunity-health",
}
_ROBINHOOD_PATHS = {"/robinhood-dex", "/robinhood-dex/candidates", "/dex/robinhood-shadow"}


def page_scope_contract(path: str) -> PageScopeContract | None:
    pathname = urlsplit(str(path or "")).path or "/"
    if pathname in _LIQUIDATION_PATHS or pathname.startswith("/api/liquidation"):
        return PageScopeContract(
            "liquidation", WORKFLOW_LIQUIDATION, True, SCOPE_SINGLE_CHAIN,
            EXECUTION_MODE_DIRECT, (EXECUTION_MODE_DIRECT, EXECUTION_MODE_COW), "strict",
            "Liquidation discovery, preflight, and submission must stay within one market and chain.",
        )
    if (
        pathname in _DEX_PATHS
        or pathname.startswith("/api/binance-market")
        or pathname.startswith("/api/dex-pools")
        or pathname.startswith("/api/robinhood-dex")
    ):
        page_key = "robinhood_dex" if pathname in _ROBINHOOD_PATHS or pathname.startswith("/api/robinhood-dex") else "dex_arbitrage"
        return PageScopeContract(
            page_key, WORKFLOW_DEX_ARBITRAGE, True, "mode_dependent", EXECUTION_MODE_COW,
            (EXECUTION_MODE_COW, EXECUTION_MODE_DIRECT), "strict",
            "Direct DEX execution is single-chain; current CoW multi-network mode settles each selected chain independently.",
        )
    if pathname in _CHAIN_NEUTRAL_PATHS:
        return PageScopeContract(
            pathname.lstrip("/").replace("-", "_") or "analytics", WORKFLOW_CHAIN_NEUTRAL,
            False, SCOPE_CHAIN_NEUTRAL, None, (), "aggregate",
            "This page is an aggregate analytics surface and is not an execution scope.",
        )
    return None
