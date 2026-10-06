from __future__ import annotations

import os

from typing import Any

from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path
from dex.runtime.dex_opportunity_helpers import _chain_key
def _route_simple_key(route: dict[str, Any]) -> tuple[Any, ...]:
    addresses = route.get("routeAddresses") if isinstance(route.get("routeAddresses"), list) else route.get("addresses")
    if isinstance(addresses, list):
        selected = [str(item or "").strip().lower() for item in addresses if str(item or "").strip()]
        if selected:
            return tuple(selected)
    symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else route.get("symbols")
    if isinstance(symbols, list):
        selected = [str(item or "").strip().upper() for item in symbols if str(item or "").strip()]
        if selected:
            return tuple(selected)
    return (str(route.get("symbolPair") or "").strip().upper(),)


def _route_evidence_richness(route: dict[str, Any]) -> int:
    score = 0
    evidence = route.get("live_onchain_evidence")
    if isinstance(evidence, dict):
        score += 2
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    steps = analysis.get("steps")
    if isinstance(steps, list):
        score += len(steps)
    if route.get("quoteStatus"):
        score += 1
    return score


def _dedupe_payload_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        key = _route_simple_key(route)
        if key not in best_by_key:
            order.append(key)
        existing = best_by_key.get(key)
        if existing is None or _route_evidence_richness(route) > _route_evidence_richness(existing):
            best_by_key[key] = route
    return [best_by_key[key] for key in order if key in best_by_key]


def _scanner_snapshot_ttl_seconds() -> float:
    raw = os.getenv("DEX_OPPORTUNITY_SNAPSHOT_MAX_AGE_SECONDS", "600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 600.0


def _chain_scope_key(chain_id: int | None, protocols: list[str] | None = None) -> str:
    """Return the snapshot cache key for a chain plus its requested protocols.

    A chain can be watched with different protocol scopes.  Keying only by chain
    lets a ``protocols=[]`` scan overwrite a ``protocols=['uniswap_v4']`` scan,
    which drops route evidence from the quote-routes page.
    """
    base = _chain_key(chain_id)
    normalized = tuple(
        sorted(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
    )
    if not normalized:
        return base
    return f"{base}|protocols:{','.join(normalized)}"


def _scanner_evidence_worker_count() -> int:
    saved = read_json_parameter(STRATEGY_CONFIG_PATH, legacy_paths=LEGACY_STRATEGY_CONFIG_PATHS) or {}
    raw = saved.get("DEX_SCAN_WORKERS", 1)
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 1


def _scanner_evidence_timeout_seconds() -> float:
    saved = read_json_parameter(STRATEGY_CONFIG_PATH, legacy_paths=LEGACY_STRATEGY_CONFIG_PATHS) or {}
    raw = saved.get("DEX_EVIDENCE_TIMEOUT_SECONDS", os.getenv("DEX_OPPORTUNITY_SCANNER_EVIDENCE_TIMEOUT_SECONDS", "120"))
    try:
        return max(60.0, float(raw))
    except (TypeError, ValueError):
        return 120.0


def _closed_loop_workers() -> int:
    try:
        return max(1, min(8, int(os.getenv("DEX_CLOSED_LOOP_WORKERS", "3") or "3")))
    except (TypeError, ValueError):
        return 3


def _auto_execute_log_stats(chain_id: int | None, since: str | None = None) -> dict[str, Any]:
    """Return cumulative auto-execute stats persisted in execution attempts."""
    if chain_id is None:
        return {"auto_execute_count": 0, "last_auto_execute_at": None}
    try:
        log_path = cow_execution_attempt_log_path(chain_id=int(chain_id))
        attempts = load_recent_cow_execution_attempts_jsonl(
            log_path,
            limit=10000,
            chain_ids=[int(chain_id)],
        )
    except Exception:
        return {"auto_execute_count": 0, "last_auto_execute_at": None}
    auto_execute_count = 0
    last_auto_execute_at = None
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
        if not bool(auto.get("executed")):
            continue
        result = auto.get("result") if isinstance(auto.get("result"), dict) else {}
        tx_hash = str(result.get("txHash") or result.get("tx_hash") or "").strip()
        if not tx_hash:
            continue
        ts = str(auto.get("observed_at") or attempt.get("created_at") or attempt.get("observed_at") or "").strip()
        if since and ts and str(ts) < str(since):
            continue
        auto_execute_count += 1
        if ts and (last_auto_execute_at is None or ts > last_auto_execute_at):
            last_auto_execute_at = ts
    return {
        "auto_execute_count": auto_execute_count,
        "last_auto_execute_at": last_auto_execute_at,
    }
