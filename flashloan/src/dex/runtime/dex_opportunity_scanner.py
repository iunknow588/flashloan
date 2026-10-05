from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from market.binance_market.dex_surfaces import dex_overview_payload
from dex.runtime.dex_opportunity_auto_execute import (
    auto_execute_callback_from_env as default_auto_execute_callback,
    auto_execute_env_enabled as default_auto_execute_env_enabled,
)
from dex.runtime.dex_opportunity_helpers import (
    _env_float,
    _network_for_chain_id,
    _now_iso,
    _snapshot_summary,
    _structured_dex_error,
)
from dex.runtime.dex_opportunity_scanner_engine import CACHE_PATH, DexOpportunityScanner
from dex.runtime.dex_opportunity_route_runner import (
    _attempt_route_identity,
    _broadcast_profit_value,
    _closed_loop_workers,
    _evidence_provider_with_timeout,
    _is_robinhood_family_network,
    _quote_routes_payload_from_facade,
    _reference_only_quote_payload,
    _route_closed_loop_group_key,
    _route_is_closed_loop_quad,
    _scanner_evidence_timeout_seconds,
    _scanner_evidence_worker_count,
    _scanner_onchain_evidence_provider,
    build_dex_opportunity_attempts,
    default_quote_payload_provider,
    default_record_attempts,
)
from dex.runtime.dex_opportunity_snapshot import (
    _auto_execute_log_stats,
    _chain_scope_key,
    _dedupe_payload_routes,
    _route_evidence_richness,
    _route_simple_key,
    _scanner_snapshot_ttl_seconds,
)


# Legacy tests and callers monkeypatch ``dex_quote_routes_payload`` on the
# scanner facade. Keep that seam pointing at the facade-aware resolver instead
# of importing the raw market surface function here.
dex_quote_routes_payload = _quote_routes_payload_from_facade


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]

_SCANNER: DexOpportunityScanner | None = None
_SCANNER_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def get_dex_opportunity_scanner() -> DexOpportunityScanner:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is None:
            _SCANNER = DexOpportunityScanner(
                interval_seconds=_env_float("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", 180.0, minimum=1.0, maximum=3600.0),
                record_cooldown_seconds=_env_float(
                    "DEX_OPPORTUNITY_RECORD_COOLDOWN_SECONDS",
                    60.0,
                    minimum=0.0,
                    maximum=3600.0,
                ),
                auto_execute_cooldown_seconds=_env_float(
                    "DEX_AUTO_EXECUTE_COOLDOWN_SECONDS",
                    600.0,
                    minimum=0.0,
                    maximum=86400.0,
                ),
                auto_execute_callback=default_auto_execute_callback(),
                auto_execute_enabled=default_auto_execute_env_enabled(),
                record_opportunities=True,
                evidence_workers=_scanner_evidence_worker_count(),
                testnet_max_onchain_evidence_routes=int(
                    _env_float("DEX_OPPORTUNITY_TESTNET_MAX_ONCHAIN_EVIDENCE_ROUTES", 10.0, minimum=1.0, maximum=1000.0)
                ),
                periodic_scan_enabled=str(
                    os.getenv("DEX_OPPORTUNITY_PERIODIC_SCAN_ENABLED", "true")
                ).strip().lower()
                in {"1", "true", "yes", "on"},
            )
        return _SCANNER


def ensure_dex_opportunity_scanner_running(
    *, chain_id: int | None = None, protocols: list[str] | None = None
) -> DexOpportunityScanner:
    scanner = get_dex_opportunity_scanner()
    scanner.start(chain_id=chain_id, protocols=protocols)
    snapshot = scanner.snapshot(chain_id=chain_id, protocols=protocols)
    if not (snapshot.get("snapshot") or {}).get("payload"):
        try:
            dex_overview_payload(
                chain_id=chain_id,
                protocols=protocols or [],
                use_static_observation=_is_robinhood_family_network(chain_id),
            )
            payload = scanner.quote_payload_provider(chain_id=chain_id, protocols=protocols or [])
            if isinstance(payload, dict):
                warmed = {
                    "chain_id": chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "trigger_context": scanner.trigger_context_provider(
                        chain_id=chain_id,
                        network=_network_for_chain_id(chain_id),
                        observed_at=_now_iso(),
                    ),
                    "updated_at": _now_iso(),
                    "error": None,
                    "recording": {"candidate_count": 0, "recorded": 0, "source": "warmup", "error": None},
                }
                with scanner._lock:
                    scanner._snapshots[_chain_scope_key(chain_id, protocols)] = warmed
        except Exception:
            logger.warning("failed to warm DEX opportunity scanner snapshot", exc_info=True)
    return scanner


def run_dex_opportunity_scan_now(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    return scanner.scan_async(chain_id=chain_id, protocols=protocols)


def dex_opportunity_snapshot(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    scanner.start(chain_id=chain_id, protocols=protocols)
    return scanner.snapshot(chain_id=chain_id, protocols=protocols)


def dex_opportunity_scanner_status(*, chain_id: int | None = None) -> dict[str, Any]:
    return get_dex_opportunity_scanner().status(chain_id=chain_id)


def _reset_dex_opportunity_scanner_for_tests() -> None:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is not None:
            _SCANNER.stop()
        _SCANNER = None


__all__ = [
    "DexOpportunityScanner",
    "build_dex_opportunity_attempts",
    "dex_opportunity_scanner_status",
    "dex_opportunity_snapshot",
    "ensure_dex_opportunity_scanner_running",
    "run_dex_opportunity_scan_now",
]
