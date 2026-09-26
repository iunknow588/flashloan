"""Central policy for API endpoints that require an explicit market scope."""

from __future__ import annotations

from functools import lru_cache
from collections.abc import Mapping


SCOPE_FREE_API_PATHS = frozenset(
    {
        "/api/status",
        "/api/system-info",
        "/api/workflow-context",
        "/api/chain-selection",
        "/api/markets",
        "/api/metrics/summary",
        "/api/metrics/deliver-alert",
    }
)

SCOPE_REQUIRED_API_PATHS = frozenset(
    {
        "/api/db-summary",
        "/api/opportunity-health",
        "/api/velocity-timepoints",
        "/api/velocity-summary",
        "/api/strategy-config",
        "/api/trade-stats",
        "/api/testnet-trade-stats",
        "/api/aave-pair-prices",
        "/api/binance-pair-prices",
        "/api/pair-route-profits",
        "/api/chart-symbols",
        "/api/binance-extremes/latest",
        "/api/executable-signal/latest",
        "/api/dex-costs",
        "/api/liquidation-health",
        "/api/liquidation-settings",
        "/api/start",
        "/api/stop",
        "/api/clear",
        "/api/clear-files",
        "/api/liquidation-discovery",
        "/api/trigger/latest",
    }
)

API_RESPONSE_REQUIRED_KEYS = {
    "/api/status": ("running", "control_status"),
    "/api/debt-pool/state": ("status",),
    "/api/account-pool/state": ("result",),
    "/api/account-scan/state": ("status",),
    "/api/market-observation/state": ("status",),
    "/api/execution/state": ("status",),
    "/api/flash-loan/pool-discovery": ("protocols",),
    "/api/flash-loan/pools": ("providers",),
    "/api/dex/overview": ("summary",),
    "/api/dex/market-observation": ("summary",),
    "/api/dex/quote-routes": ("routes",),
    "/api/dex/quote-routes/static": ("routes",),
    "/api/dex/closed-loop-groups": ("ok", "groups", "summary"),
    "/api/dex/quote-route/test": ("ok", "routeSymbols", "amount", "record"),
    "/api/dex-pools/candidates": ("pairs",),
    "/api/opportunity-health": ("rows",),
    "/api/velocity-summary": ("points",),
    "/api/binance-market/state": ("market_state",),
    "/api/metrics/deliver-alert": ("ok", "alert", "delivery"),
    "/api/binance-market/states": ("states",),
    "/api/liquidation/market": ("market_id", "chain_id"),
    "/api/liquidation/config-health": ("market_id", "chain_id"),
    "/api/execution-plan/quote": ("quote",),
    "/api/execution-plan/payload": ("execution_plan",),
    "/api/binance-market/cow-tokens": ("tokens",),
    "/api/binance-market/cow-support": ("cow_network", "market_state", "routes", "supported_route_count"),
    "/api/binance-market/cow-quotes": ("quotes",),
    "/api/robinhood-dex/observation": ("summary",),
    "/api/robinhood-dex/pools": ("pools",),
    "/api/robinhood-dex/readiness": ("market", "chainStatus", "evidence", "deploymentStatus", "executionEnabled", "broadcastEnabled"),
    "/api/liquidation/execution-attempts": ("attempts",),
    "/api/liquidation/failure-samples": ("samples",),
    "/api/liquidation/pause-guard": ("paused",),
    "/api/liquidation/discovery-coverage": ("pool_address", "has_gap", "progress"),
    "/api/liquidation/samples": ("samples",),
    "/api/liquidation/accounts": ("accounts",),
    "/api/trade-stats": ("stats",),
    "/api/testnet-trade-stats": ("stats",),
    "/api/observations": ("symbol", "rows"),
    "/api/aave-pair-prices": ("x_symbol", "y_symbol", "rows"),
    "/api/binance-pair-prices": ("x_symbol", "y_symbol", "rows"),
    "/api/pair-route-profits": ("x_symbol", "y_symbol", "routes"),
    "/api/chart-symbols": ("symbols",),
    "/api/binance-extremes/latest": ("extremes",),
    "/api/binance-velocity/candidates": ("candidate_count", "pairs", "side_limit"),
    "/api/arbitrage/latest": ("simulation",),
    "/api/trigger/latest": ("trigger",),
    "/api/executable-signal/latest": ("executable_signal",),
    "/api/db-summary": ("rows", "db_counts", "liquidation"),
    "/api/dex/strategy": ("selected_strategy", "dex_strategy"),
    "/api/strategy-config": ("config",),
    "/api/velocity-timepoints": ("points",),
    "/api/debt-pool/decision": ("status", "result", "route_intent"),
    "/api/start": ("running", "starting"),
    "/api/stop": ("running",),
    "/api/clear": ("cleared",),
    "/api/clear-files": ("deleted", "errors"),
    "/api/dex/market-observation/refresh": ("refresh_state",),
    "/api/dex/opportunity-scanner/start": ("chain_selection",),
    "/api/dex/opportunity-scanner/snapshot": ("chain_selection",),
    "/api/dex/opportunity-scanner/scan-now": ("chain_selection",),
    "/api/dex/quote-request": ("chain_selection",),
    "/api/dex-costs": ("symbol",),
    "/api/execution-plan/quote": ("quote",),
    "/api/execution-plan/payload": ("payload",),
    # Both `pairs` and the legacy `candidates` alias are emitted; refresh_state
    # is the stable discriminator shared by both response generations.
    "/api/dex-pools/candidates/refresh": ("refresh_state",),
    "/api/dex/submissions": ("mode", "summary", "attempts"),
    "/api/dex/submissions/clear": ("ok", "mode", "cleared", "results"),
    "/api/dex/submissions/check-onchain": ("ok", "tx_hash", "status"),
    "/api/flash-loan/pool-discovery/refresh": ("protocol_count", "protocols"),
    "/api/flash-loan/pools/refresh": ("providers", "refresh_state"),
    "/api/binance-market/cow-execution-attempts": ("attempts",),
    "/api/binance-market/cow-candidate-queue": ("queue",),
    "/api/binance-market/cow-submission-pause": ("paused",),
    "/api/binance-market/cow-submission-pause/clear": ("paused",),
    "/api/binance-market/cow-tokens": ("tokens",),
    "/api/binance-market/cow-tokens/refresh": ("tokens",),
    "/api/binance-market/cow-config": ("default_network", "networks"),
    "/api/liquidation/control-summary": ("configured",),
    "/api/liquidation/borrow-pool": ("rows",),
    "/api/liquidation/borrow-pool/batches": ("configured", "batches"),
    "/api/liquidation/borrow-pool/latest-batch": ("configured", "batch"),
    "/api/liquidation/high-frequency-pool": ("configured", "count", "rows"),
    "/api/liquidation/core-opportunities": ("configured", "count", "rows"),
    "/api/liquidation-discovery": ("saved", "count"),
    "/api/liquidation-health": ("rows", "summary"),
    "/api/liquidation/borrow-pool/scan": ("rows", "summary"),
    "/api/liquidation/account": ("account", "summary"),
    "/api/liquidation/account/<account>/attempts": ("configured", "attempts"),
    "/api/liquidation/account/<account>/samples": ("configured", "samples"),
    "/api/liquidation/account/<account>/static-call-and-save": ("account",),
    "/api/liquidation/account/cached": ("account", "cached"),
    "/api/liquidation/account/payload": ("request",),
    "/api/liquidation/account/preflight": ("account",),
    "/api/liquidation/account/flashloan": ("mode",),
    "/api/liquidation/account-backfill": ("running", "stage", "stop_requested"),
    "/api/liquidation/account-backfill/start": ("started", "running"),
    "/api/liquidation/account-backfill/stop": ("running", "stop_requested"),
    "/api/liquidation/daemon/status": ("state", "running"),
    "/api/liquidation/preflight/<account>": ("account",),
    "/api/liquidation/reports/daily": ("configured", "report"),
    "/api/liquidation-settings": ("refresh_profile", "retention_days", "scan_interval_seconds", "discovery_interval_seconds", "raw"),
    "/api/liquidation/pause-guard/clear": ("paused",),
    "/api/liquidation/account/execute": ("account", "error", "state", "execution_phase", "blocked_reasons"),
    "/api/metrics/summary": ("ok", "event_count", "latency_percentiles"),
}


@lru_cache(maxsize=1)
def scope_required_api_prefixes() -> tuple[str, ...]:
    return (
        "/api/liquidation/",
        "/api/dex/",
        "/api/dex-pools/",
        "/api/binance-market/",
        "/api/flash-loan/",
        "/api/execution-plan/",
        "/api/observations",
        "/api/velocity-",
        "/api/arbitrage/",
        "/api/account-pool/",
        "/api/account-scan/",
        "/api/debt-pool/",
        "/api/execution/",
        "/api/market-observation/",
        "/api/robinhood-dex/",
        "/api/binance-velocity/",
    )


def api_requires_scope(path: str) -> bool:
    normalized = str(path or "").split("?", 1)[0].rstrip("/") or "/"
    if normalized in SCOPE_FREE_API_PATHS:
        return False
    if normalized in SCOPE_REQUIRED_API_PATHS:
        return True
    return normalized.startswith(scope_required_api_prefixes())


def validate_api_scope_policy(paths: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Return registered API paths that have no explicit scope policy."""
    uncovered: list[str] = []
    for path in sorted({str(item).split("?", 1)[0].rstrip("/") or "/" for item in paths}):
        if not path.startswith("/api/") or path in SCOPE_FREE_API_PATHS:
            continue
        if not api_requires_scope(path):
            uncovered.append(path)
    return uncovered


def api_scope_contract(path: str, method: str = "GET") -> dict[str, object]:
    """Return machine-readable contract metadata for a registered API route."""
    normalized_method = str(method or "GET").strip().upper()
    if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        raise ValueError(f"unsupported API method: {method}")
    return {
        "path": str(path).split("?", 1)[0].rstrip("/") or "/",
        "method": normalized_method,
        "scope_required": api_requires_scope(path),
        "required_scope_fields": ["market_id", "chain_id", "network", "protocol"]
        if api_requires_scope(path)
        else [],
    }


def validate_api_response_contract(payload: object, *, required_keys: tuple[str, ...] = ()) -> bool:
    """Validate the common JSON envelope used by successful API responses."""
    if not isinstance(payload, Mapping):
        raise ValueError("API response must be a JSON object")
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise ValueError(f"API response missing keys: {', '.join(missing)}")
    return True


def response_required_keys(path: str) -> tuple[str, ...]:
    normalized = str(path or "").split("?", 1)[0].rstrip("/") or "/"
    return tuple(API_RESPONSE_REQUIRED_KEYS.get(normalized, ()))


def validate_named_response_contract(path: str, payload: object) -> bool:
    """Validate a response against the registered minimal schema, when present."""
    return validate_api_response_contract(payload, required_keys=response_required_keys(path))


def validate_registered_response_contracts(samples: Mapping[str, object]) -> list[str]:
    """Validate a collection of sampled route payloads and return checked paths."""
    checked: list[str] = []
    for path, payload in samples.items():
        validate_named_response_contract(path, payload)
        checked.append(str(path).split("?", 1)[0].rstrip("/") or "/")
    return sorted(checked)


def validate_response_schema_registry(paths: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Return schema entries that do not correspond to a registered API path."""
    registered = {str(path).split("?", 1)[0].rstrip("/") or "/" for path in paths}
    return sorted(path for path in API_RESPONSE_REQUIRED_KEYS if path not in registered)


def missing_response_schema_paths(paths: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Return API routes that still need a successful-response schema entry."""
    registered = {str(path).split("?", 1)[0].rstrip("/") or "/" for path in paths}
    return sorted(
        path for path in registered
        if path.startswith("/api/")
        and path not in SCOPE_FREE_API_PATHS
        and path not in API_RESPONSE_REQUIRED_KEYS
    )
