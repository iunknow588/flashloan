from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import os
import time
from typing import Any

from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path
from dex.runtime.dex_opportunity_helpers import (
    _merge_onchain_evidence_into_route as _merge_onchain_evidence_into_route,
)


logger = logging.getLogger(__name__)


def _route_symbol_key(items: object) -> str:
    if isinstance(items, str):
        items = [part for part in items.split(" -> ") if str(part).strip()]
    parts = []
    for item in (items or []):
        s = str(item).strip().upper()
        if not s:
            continue
        # 过滤地址碎片，避免展示 0x... 这类非符号内容
        if s.startswith("0X") and len(s) < 20:
            continue
        parts.append(s)
    return " -> ".join(parts)


def _route_addresses_from_route(route: dict) -> list[str]:
    for key in ("routeAddresses", "route_addresses", "addresses"):
        value = route.get(key)
        if isinstance(value, list):
            addresses = [str(item or "").strip().lower() for item in value if str(item or "").strip()]
            if addresses:
                return addresses
    return []


def _route_dedup_key(route: dict) -> str:
    """Return a stable route key that prefers exact token addresses.

    Symbols are display-only and can collide (for example multiple WETH
    addresses). When addresses are available they become the internal key so
    scanner/evidence merging does not combine two different token routes that
    happen to share the same symbol path.
    """
    if isinstance(route, dict):
        addresses = _route_addresses_from_route(route)
        if addresses:
            return "addr|" + "|".join(addresses)
        raw_path = route.get("routeSymbols") or route.get("symbolPair") or route.get("path") or []
        if not raw_path:
            return ""
        return "symbol|" + _route_symbol_key(raw_path)
    return "symbol|" + _route_symbol_key(route)


def _scanner_snapshot_is_stale(scanner_payload: dict, *, max_age_seconds: float = 600.0) -> bool:
    if not isinstance(scanner_payload, dict):
        return True
    snapshot = scanner_payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return True
    if not snapshot.get("payload"):
        return True
    updated_at = str(snapshot.get("updated_at") or "")
    if not updated_at:
        return True
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_seconds = time.time() - parsed.timestamp()
    return age_seconds > max_age_seconds


def _recent_attempt_max_age_seconds() -> float:
    raw = os.getenv("DEX_RECENT_ATTEMPT_MAX_AGE_SECONDS", "600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 600.0


def _attempt_timestamp(attempt: dict) -> float | None:
    raw = str(attempt.get("created_at") or attempt.get("observed_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _attempt_route_evidence(attempt: dict) -> dict:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    candidates = (
        quote.get("live_onchain_evidence"),
        attempt.get("live_onchain_evidence"),
        attempt.get("evidence"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _merge_recent_attempt_evidence_into_routes(
    routes: list[dict],
    *,
    chain_id: int,
    recent_attempt_limit: int = 500,
    max_age_seconds: float | None = None,
) -> list[dict]:
    """Fold recent JSONL quote-route tests into the quote-routes page."""
    if not routes or chain_id is None:
        return routes
    try:
        log_path = cow_execution_attempt_log_path(chain_id=int(chain_id))
        attempts = load_recent_cow_execution_attempts_jsonl(
            log_path,
            limit=max(1, int(recent_attempt_limit)),
            chain_ids=[int(chain_id)],
        )
    except Exception as exc:
        logger.warning("failed to load recent cow execution attempts for quote-routes page: %s", exc)
        return routes
    latest_evidence_by_route = {}
    max_age = _recent_attempt_max_age_seconds() if max_age_seconds is None else float(max_age_seconds)
    now = time.time()
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_ts = _attempt_timestamp(attempt)
        if attempt_ts is None or now - attempt_ts > max_age:
            continue
        quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
        raw_path = attempt.get("route_path") or quote.get("path") or attempt.get("path") or []
        evidence = _attempt_route_evidence(attempt)
        if not isinstance(evidence, dict) or not evidence.get("status"):
            continue
        route_key = _route_dedup_key(
            {
                "routeAddresses": evidence.get("routeAddresses") or evidence.get("route_addresses") or [],
                "routeSymbols": raw_path,
            }
        )
        if not route_key:
            continue
        latest_evidence_by_route.setdefault(route_key, evidence)
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_key = _route_dedup_key(route)
        evidence = latest_evidence_by_route.get(route_key)
        if not isinstance(evidence, dict) or not evidence:
            continue
        existing_evidence = route.get("live_onchain_evidence")
        existing_status = (
            str(existing_evidence.get("status") or "")
            if isinstance(existing_evidence, dict)
            else ""
        ).strip().lower()
        attempt_status = str(evidence.get("status") or "").strip().lower()
        if not attempt_status:
            continue
        terminal_replace_statuses = {
            "live_verified",
            "not_enough_liquidity",
            "pool_not_initialized",
            "pool_insufficient_for_profitable_execution",
            "route_unprofitable",
            "slippage_limit_exceeded",
        }
        if existing_status == attempt_status and existing_status in terminal_replace_statuses:
            continue
        if existing_status in {"requires_live_quoter", "not_live_verified", "", "rpc_verification_failed", "rpc_verification_timeout"}:
            _merge_onchain_evidence_into_route(route, evidence)
        elif existing_status in terminal_replace_statuses and attempt_status in terminal_replace_statuses:
            _merge_onchain_evidence_into_route(route, evidence)
    return routes


def evict_stale_evidence_from_routes(routes: list[dict]) -> list[dict]:
    """Remove old on-chain evidence before a realtime refresh can replace it.

    When the scanner snapshot is stale, the quote-routes page must not present
    `live_verified` data from the old snapshot as if it were current. This
    helper clears the evidence fields and marks the route as pending refresh.
    """
    for route in routes:
        if not isinstance(route, dict):
            continue
        route["quoteStatus"] = "stale_refresh_pending"
        route["onchainEvidenceStatus"] = "stale_refresh_pending"
        route.pop("live_onchain_evidence", None)
        route.pop("evidence", None)
        stale_analysis = route.get("quoteAnalysis")
        if isinstance(stale_analysis, dict):
            stale_analysis.pop("live_onchain_evidence", None)
            stale_analysis.pop("evidence", None)
            stale_analysis["onchainEvidenceStatus"] = "stale_refresh_pending"
            stale_analysis["requiresLiveQuoter"] = True
    return routes


def _merge_live_route_estimate(
    route: dict,
    live_payload: dict,
) -> dict:
    """Merge a live estimate result into a scanner route."""
    if not isinstance(route, dict) or not isinstance(live_payload, dict):
        return route
    steps = [step for step in (live_payload.get("steps") or []) if isinstance(step, dict)]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    live_analysis = {}
    if steps:
        analysis = {**analysis, "steps": steps}
    if live_payload.get("analysis") and isinstance(live_payload["analysis"], dict):
        live_analysis = live_payload["analysis"]
        for key in (
            "referenceInputAmount",
            "referenceInputSymbol",
            "referenceFinalOutputAmount",
            "referenceFinalOutputSymbol",
            "referenceGrossProfitAmount",
            "referenceNetProfitAmount",
            "referenceNetReturnBps",
            "maxStaticDepthImpactBps",
            "warnings",
            "profitable",
            "readyForTransactionPreflight",
        ):
            if live_analysis.get(key) is not None:
                analysis[key] = live_analysis[key]
    live_recommendation = live_payload.get("recommendation") or live_analysis.get("recommendation")
    if isinstance(live_recommendation, dict):
        analysis["recommendation"] = live_recommendation
    status = str(live_payload.get("quoteStatus") or "").strip().lower()
    if status == "requires_live_quoter" and (live_payload.get("steps") or []):
        status = "live_quote_reference_only"
    evidence = {
        "evidenceVersion": 1,
        "observedAt": live_payload.get("observedAt"),
        "network": live_payload.get("network"),
        "chainId": live_payload.get("chain_id"),
        "status": status or ("live_verified" if live_payload.get("liveVerified") else "live_quote_reference_only"),
        "executionTrust": live_payload.get("executionTrust") or "static_reference_only",
        "trustedForExecution": False,
        "readyForTransactionPreflight": bool(
            live_payload.get("readyForTransactionPreflight")
            or live_analysis.get("readyForTransactionPreflight")
            or (live_recommendation.get("readyForTransactionPreflight") if isinstance(live_recommendation, dict) else False)
        ),
        "profitable": bool(live_payload.get("profitable") or live_analysis.get("profitable")),
        "blockNumber": live_payload.get("blockNumber"),
        "quoteBlockSource": live_payload.get("quoteBlockSource") or "route_live_estimate",
        "routeSymbols": live_payload.get("routeSymbols") or route.get("routeSymbols") or [],
        "hops": steps,
        "costModel": {
            "borrowInputSymbol": analysis.get("referenceInputSymbol") or (live_payload.get("routeSymbols") or [""])[0],
            "borrowInputAmount": analysis.get("referenceInputAmount") or live_payload.get("amount"),
            "selectedBorrowInputAmount": live_payload.get("bestAmount") or analysis.get("bestAmount") or analysis.get("referenceInputAmount") or live_payload.get("amount"),
            "netProfitAmount": analysis.get("referenceNetProfitAmount"),
            "netProfitBps": analysis.get("referenceNetProfitBps"),
            "finalOutputAmount": analysis.get("referenceFinalOutputAmount"),
            "finalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
            "finalOutputUsd": analysis.get("referenceFinalOutputUsd"),
            "netProfitUsd": analysis.get("referenceNetProfitUsd"),
            "netProfitAmountBps": analysis.get("referenceNetReturnBps"),
            "profitable": bool(live_payload.get("profitable") or live_analysis.get("profitable")),
            "readyForTransactionPreflight": bool(
                live_payload.get("readyForTransactionPreflight")
                or live_analysis.get("readyForTransactionPreflight")
                or (live_recommendation.get("readyForTransactionPreflight") if isinstance(live_recommendation, dict) else False)
            ),
        },
        "blockers": [],
        "transactionDevelopment": {
            "status": "blocked_before_transaction_development",
            "executionEnabled": False,
            "blockers": [],
        },
    }
    if not steps:
        evidence["blockers"] = [str(live_payload.get("detail") or live_payload.get("error") or "链上报价未完成")]
        evidence["errorStack"] = [
            {
                "module": "DEX",
                "code": status or "live_quote_failed",
                "message": str(live_payload.get("detail") or live_payload.get("error") or "链上报价未完成"),
            }
        ]
    live_verified = bool(live_payload.get("liveVerified"))
    analysis.update(
        {
            "live_onchain_evidence": evidence,
            "onchainEvidenceStatus": status,
            "livePriceVerified": live_verified,
            "priceEvidenceMode": "live_verified" if live_verified else "live_required",
            "priceEvidenceSource": "chain_live_quote" if live_verified else "chain_live_quote_required",
            "requiresLiveQuoter": not live_verified,
            "quote_verified": live_verified,
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
        }
    )
    route["quoteAnalysis"] = analysis
    route["live_onchain_evidence"] = evidence
    route["onchainEvidenceStatus"] = status
    route["quoteStatus"] = status
    route["quoteVerification"] = {
        "status": status,
        "livePriceVerified": live_verified,
        "requiresLiveQuoter": not live_verified,
        "executableCandidate": False,
        "chainSubmissionCandidate": False,
        "trustedForExecution": False,
        "readyForTransactionPreflight": False,
        "blockers": evidence["blockers"],
    }
    return route


def _live_quote_deferred_route(
    route: dict,
    *,
    chain_id: int,
    protocols: list[str],
) -> dict | None:
    """Try an immediate live estimate for a route the scanner deferred."""
    try:
        from market.binance_market.dex_route_test import estimate_route_live_quote

        symbols = [str(item) for item in (route.get("routeSymbols") or []) if str(item).strip()]
        if len(symbols) < 3:
            return None
        if symbols[0].upper() != symbols[-1].upper():
            return None
        quote_analysis = route.get("quoteAnalysis") or {}
        size_analysis = route.get("sizeAnalysis") or {}
        amount = float(
            quote_analysis.get("requestedAmount")
            or quote_analysis.get("initialProbeAmount")
            or quote_analysis.get("amountOptimizedFrom")
            or quote_analysis.get("inputAmount")
            or quote_analysis.get("referenceInputAmount")
            or size_analysis.get("recommendedBorrowInputAmount")
            or size_analysis.get("tokenAmountSteps", [None])[0]
            or 1000
        )
        if amount <= 0:
            amount = 1.0
        live: dict | None = None
        try:
            live = estimate_route_live_quote(
                chain_id=chain_id,
                protocols=protocols,
                route_symbols=symbols,
                amount=amount,
            )
        except Exception as exc:
            return _merge_live_route_estimate(
                route,
                {
                    "quoteStatus": "live_quote_failed",
                    "ok": False,
                    "error": "on-chain estimate raised",
                    "detail": str(exc)[:300] or "on-chain estimate raised",
                    "steps": [],
                },
            )
        if not isinstance(live, dict):
            return _merge_live_route_estimate(
                route,
                {
                    "quoteStatus": "live_quote_failed",
                    "ok": False,
                    "error": "on-chain estimate returned no payload",
                    "detail": "on-chain estimate returned no payload",
                    "steps": [],
                },
            )
        if live.get("ok") is False or not (live.get("steps") or []):
            return _merge_live_route_estimate(route, live)
        return _merge_live_route_estimate(route, live)
    except Exception as exc:
        logger.warning("live route estimate failed for chain_id=%s protocols=%s: %s", chain_id, protocols, exc)
        return _merge_live_route_estimate(
            route,
            {
                "quoteStatus": "live_quote_failed",
                "ok": False,
                "error": "on-chain estimate failed",
                "detail": "on-chain estimate failed",
                "steps": [],
            },
        )


@contextmanager
def _reference_only_live_quote_context(*, disable_live: bool):
    key = "DEX_V4_LIVE_QUOTE_ENABLED"
    previous = os.environ.get(key)
    if disable_live:
        os.environ[key] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


__all__ = [
    "_live_quote_deferred_route",
    "_merge_live_route_estimate",
    "_merge_recent_attempt_evidence_into_routes",
    "_reference_only_live_quote_context",
    "_route_dedup_key",
    "_route_symbol_key",
    "_scanner_snapshot_is_stale",
]
