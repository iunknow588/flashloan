from page_state.models import (
    AccountPoolResult,
    AccountScanStatus,
    DebtPoolScanResult,
    DebtPoolStatus,
    ExecutionStatus,
    MarketObservationStatus,
    PageName,
    RouteIntent,
    normalize_execution_phase,
    normalize_tx_hash,
    receipt_status,
)
from page_state.store import PAGE_STATE_STORE, PageState, PageStateStore

_SERVICE_EXPORTS = {
    "account_scan_state_payload",
    "debt_pool_state_payload",
    "execution_state_payload",
    "market_observation_state_payload",
    "store_page_state",
}

__all__ = [
    "AccountPoolResult",
    "AccountScanStatus",
    "DebtPoolScanResult",
    "DebtPoolStatus",
    "ExecutionStatus",
    "MarketObservationStatus",
    "PAGE_STATE_STORE",
    "PageName",
    "PageState",
    "PageStateStore",
    "RouteIntent",
    "account_scan_state_payload",
    "debt_pool_state_payload",
    "execution_state_payload",
    "market_observation_state_payload",
    "normalize_execution_phase",
    "normalize_tx_hash",
    "receipt_status",
    "store_page_state",
]


def __getattr__(name: str):
    if name in _SERVICE_EXPORTS:
        from page_state import service

        return getattr(service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
