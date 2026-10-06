from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import json
import os
from pathlib import Path
import time
from typing import Any

from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path
from dex.runtime.dex_opportunity_helpers import (
    _merge_onchain_evidence_into_route as _merge_onchain_evidence_into_route,
)


logger = logging.getLogger(__name__)


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _token_address(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if not lowered.startswith("0x"):
        return ""
    try:
        int(lowered, 16)
    except ValueError:
        return ""
    return lowered


def _pool_token_address_for_symbol(pool: dict[str, Any], symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized or not isinstance(pool, dict):
        return ""
    for index in (0, 1):
        token_symbol = str(pool.get(f"token{index}Symbol") or "").strip().upper()
        if token_symbol == normalized:
            return _token_address(pool.get(f"token{index}") or "")
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    for index in (0, 1):
        token_symbol = str(pool_key.get(f"token{index}Symbol") or "").strip().upper()
        if token_symbol == normalized:
            return _token_address(pool_key.get(f"token{index}") or "")
    for token in pool.get("tokens") or []:
        if not isinstance(token, dict):
            continue
        if str(token.get("symbol") or "").strip().upper() == normalized:
            return _token_address(token.get("address") or token.get("token") or "")
    return ""


def _infer_route_addresses_from_pools(route: dict[str, Any]) -> dict[str, Any]:
    """Fill route token addresses from V4 pool keys when route metadata lost them.

    Mainnet pool index entries frequently include `poolKey.token0/token1` with
    symbols while the market-observation route path only carried symbols.  The
    quote/route surface needs concrete addresses to run the live V4 Quoter and
    to keep evidence merging stable, so we repair the path from the route pools
    before on-chain review is attempted.
    """
    if not isinstance(route, dict):
        return route
    symbols = [str(item) for item in (route.get("routeSymbols") or []) if str(item).strip()]
    if len(symbols) < 4:
        return route
    existing_addresses = _route_addresses_from_route(route)
    if len(existing_addresses) == len(symbols) and all(existing_addresses):
        return route
    pools = [pool for pool in (route.get("routePools") or []) if isinstance(pool, dict)]
    if len(pools) < len(symbols) - 1:
        return route
    inferred: list[str] = []
    for index, symbol in enumerate(symbols):
        existing = existing_addresses[index] if index < len(existing_addresses) else ""
        if existing:
            inferred.append(existing)
            continue
        hop_index = min(index, len(pools) - 1)
        address = _pool_token_address_for_symbol(pools[hop_index], symbol)
        if not address and index > 0:
            address = _pool_token_address_for_symbol(pools[index - 1], symbol)
        inferred.append(address)
    if not all(inferred):
        return route
    route_tokens = [
        {"symbol": symbol, "address": inferred[index]}
        for index, symbol in enumerate(symbols)
    ]
    route["routeAddresses"] = inferred
    route["addressPair"] = " -> ".join(inferred)
    route["routeTokenPath"] = route_tokens
    overview_route = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
    overview_route = {**overview_route, "pathAddresses": inferred, "pathTokens": route_tokens, "pathAddressText": " -> ".join(inferred)}
    route["overviewRoute"] = overview_route
    linkage = route.get("flashLoanLinkage") if isinstance(route.get("flashLoanLinkage"), dict) else {}
    if linkage:
        route["flashLoanLinkage"] = {
            **linkage,
            "borrow_tokens": [inferred[0]],
            "route_addresses": inferred,
        }
    return route


def _has_live_verified_evidence(route: dict[str, Any]) -> bool:
    if not isinstance(route, dict):
        return False
    evidence = route.get("live_onchain_evidence") if isinstance(route.get("live_onchain_evidence"), dict) else {}
    if not evidence:
        analysis = route.get("quoteAnalysis")
        evidence = analysis.get("live_onchain_evidence") if isinstance(analysis, dict) and isinstance(analysis.get("live_onchain_evidence"), dict) else {}
    if str(evidence.get("status") or "").strip().lower() != "live_verified":
        return False
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    try:
        profit = float(costs.get("netProfitAmount") or 0)
        amount = float(costs.get("selectedBorrowInputAmount") or costs.get("borrowInputAmount") or 0)
    except (TypeError, ValueError):
        return False
    return bool(evidence.get("hops")) and amount > 0


def _has_preserved_manifest_evidence(route: dict[str, Any]) -> bool:
    if not isinstance(route, dict):
        return False
    evidence = route.get("live_onchain_evidence") if isinstance(route.get("live_onchain_evidence"), dict) else {}
    if not evidence:
        analysis = route.get("quoteAnalysis")
        evidence = analysis.get("live_onchain_evidence") if isinstance(analysis, dict) and isinstance(analysis.get("live_onchain_evidence"), dict) else {}
    status = str(evidence.get("status") or "").strip().lower()
    source = str(evidence.get("quoteBlockSource") or "").strip().lower()
    if status not in {"live_verified", "static_reference_pending_live_review"}:
        return False
    if status == "static_reference_pending_live_review" and "mainnet_v4_manifest_initial_probe" not in source:
        return False
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    return bool(evidence.get("hops")) and costs.get("profitable") in (True, None)


def _mainnet_last_arb_live_test(chain_id: int | None) -> dict[str, Any] | None:
    if int(chain_id or 0) != 4663:
        return None
    manifest_path = _workspace_root() / "test-token_create" / "config" / "mainnet-v4-amm" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    official = manifest.get("officialV4") if isinstance(manifest.get("officialV4"), dict) else {}
    record = official.get("lastArbLiveTest")
    return record if isinstance(record, dict) else None


def _manifest_live_evidence_for_route(chain_id: int | None) -> dict[str, Any] | None:
    record = _mainnet_last_arb_live_test(chain_id)
    if not record:
        return None
    route_symbols = [str(s).upper() for s in (record.get("routeSymbols") or record.get("selectedRouteSymbols") or []) if str(s).strip()]
    if len(route_symbols) < 4:
        return None
    raw_amount_in = int(record.get("amountInRaw") or record.get("amountIn") or 0)
    raw_final = int(record.get("finalAmountRaw") or 0)
    raw_profit = int(record.get("profitRaw") or 0)
    amount_human = float(record.get("selectedAmount") or (raw_amount_in / 10**18 if raw_amount_in else 0))
    final_human = float(raw_final) / 10**18 if raw_final else float(raw_profit) / 10**18
    profit_human = float(raw_profit) / 10**18 if raw_profit else 0.0
    if amount_human <= 0:
        amount_human = 1.0
    bps = round(profit_human / amount_human * 10000, 2) if amount_human else 0
    hops: list[dict[str, Any]] = []
    for step in record.get("steps") or []:
        if not isinstance(step, dict):
            continue
        hop_index = int(step.get("hop") or len(hops) + 1)
        if hop_index < 1 or hop_index >= len(route_symbols):
            continue
        hops.append(
            {
                "hop": hop_index,
                "fromSymbol": route_symbols[hop_index - 1],
                "toSymbol": route_symbols[hop_index],
                "poolKey": step.get("poolKey") or {},
                "pool": step.get("pool") or "",
                "poolId": step.get("poolId") or step.get("pool") or "",
                "amountIn": str(step.get("amountInRaw") or ""),
                "amountOut": str(step.get("amountOutRaw") or ""),
                "amountInRaw": str(step.get("amountInRaw") or ""),
                "amountOutRaw": str(step.get("amountOutRaw") or ""),
                "gasEstimateRaw": str(step.get("gasEstimateRaw") or ""),
                "zeroForOne": bool(step.get("zeroForOne")),
            }
        )
    return {
        "evidenceVersion": 1,
        "observedAt": record.get("runAt") or "",
        "network": "robinhood",
        "chainId": 4663,
        "status": "static_reference_pending_live_review",
        "executionTrust": "initial_probe_onchain_reference",
        "trustedForExecution": False,
        "readyForTransactionPreflight": False,
        "profitable": bool(record.get("profitable")) or raw_profit > 0,
        "livePriceVerified": True,
        "bestAmountConfirmed": False,
        "bestAmountMissing": True,
        "blockNumber": None,
        "quoteBlockSource": "mainnet_v4_manifest_initial_probe",
        "routeSymbols": route_symbols,
        "hops": hops,
        "costModel": {
            "borrowInputSymbol": route_symbols[0],
            "finalOutputSymbol": route_symbols[-1],
            "requestedBorrowInputAmount": amount_human,
            "selectedBorrowInputAmountReason": "chain_onchain_initial_probe_reference_only",
            "bestAmountConfirmed": False,
            "bestAmountMissing": True,
            "finalOutputAmount": final_human,
            "netProfitAmount": profit_human,
            "netProfitBps": bps,
            "netProfitAmountBps": bps,
            "profitable": bool(record.get("profitable")) or raw_profit > 0,
            "readyForTransactionPreflight": False,
        },
        "blockers": [],
    }


def _merge_manifest_live_evidence_into_routes(
    routes: list[dict],
    *,
    chain_id: int,
) -> list[dict]:
    if int(chain_id or 0) != 4663:
        return routes
    evidence = _manifest_live_evidence_for_route(chain_id)
    if not isinstance(evidence, dict) or not evidence.get("hops"):
        return routes
    for route in routes:
        if not isinstance(route, dict):
            continue
        current = route.get("live_onchain_evidence") if isinstance(route.get("live_onchain_evidence"), dict) else {}
        current_status = str(current.get("status") or "").strip().lower()
        if current_status in {
            "live_verified",
            "not_enough_liquidity",
            "pool_not_initialized",
            "pool_insufficient_for_profitable_execution",
            "route_unprofitable",
        }:
            continue
        symbols = [str(item) for item in (route.get("routeSymbols") or []) if str(item).strip()]
        if [str(s).upper() for s in symbols] != evidence.get("routeSymbols"):
            continue
        _merge_onchain_evidence_into_route(route, evidence)
    return routes


def _number_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


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
        route_addresses = (
            evidence.get("routeAddresses")
            or evidence.get("route_addresses")
            or quote.get("routeAddresses")
            or quote.get("route_addresses")
            or attempt.get("route_addresses")
            or attempt.get("routeAddresses")
            or []
        )
        route_symbols = raw_path or evidence.get("routeSymbols") or evidence.get("route_symbols") or []
        route_key = _route_dedup_key(
            {
                "routeAddresses": route_addresses,
                "routeSymbols": route_symbols,
            }
        )
        if not route_key:
            continue
        symbol_key = "symbol|" + _route_symbol_key(route_symbols)
        latest_evidence_by_route.setdefault(route_key, evidence)
        if symbol_key and symbol_key != route_key:
            # Older on-chain previews may not carry addresses in the persisted
            # evidence. Keep a symbol-only key as a fallback so the quote-routes
            # page can still merge route_unprofitable/live_verified rows.
            latest_evidence_by_route.setdefault(symbol_key, evidence)
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_key = _route_dedup_key(route)
        evidence = latest_evidence_by_route.get(route_key)
        if not isinstance(evidence, dict) or not evidence:
            symbol_key = "symbol|" + _route_symbol_key(route)
            evidence = latest_evidence_by_route.get(symbol_key)
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
        elif existing_status == "static_reference_pending_live_review" and attempt_status in terminal_replace_statuses:
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
    live_calc = live_payload.get("bestAmountCalculation")
    if not isinstance(live_calc, dict):
        live_calc = {}
    best_amount_missing = (
        live_payload.get("bestAmountMissing") is True
        or live_analysis.get("bestAmountMissing") is True
        or live_calc.get("bestAmountMissing") is True
    )
    live_best_amount_confirmed = bool(
        live_payload.get("bestAmountConfirmed")
        or live_analysis.get("bestAmountConfirmed")
        or live_calc.get("bestAmountConfirmed")
    )
    live_best_amount = (
        next(
            (
                candidate
                for candidate in (
                    live_payload.get("bestAmount"),
                    live_analysis.get("bestAmount"),
                    live_analysis.get("best_amount"),
                    live_calc.get("bestAmount"),
                    live_calc.get("recommendedBorrowInputAmount"),
                    live_calc.get("selectedBorrowInputAmount"),
                    analysis.get("bestAmount"),
                    analysis.get("best_amount"),
                )
                if _number_or_none(candidate) is not None
            ),
            None,
        )
        if not best_amount_missing and live_best_amount_confirmed
        else None
    )
    if live_best_amount is not None:
        live_best_amount = _number_or_none(live_best_amount)
    if live_best_amount is not None:
        analysis["referenceInputAmount"] = live_best_amount
        analysis["bestAmount"] = live_best_amount
    live_recommendation = live_payload.get("recommendation") or live_analysis.get("recommendation")
    if isinstance(live_recommendation, dict):
        analysis["recommendation"] = live_recommendation
    status = str(live_payload.get("quoteStatus") or "").strip().lower()
    if status == "requires_live_quoter" and (live_payload.get("steps") or []):
        status = "static_reference_pending_live_review"
    evidence = {
        "evidenceVersion": 1,
        "observedAt": live_payload.get("observedAt"),
        "network": live_payload.get("network"),
        "chainId": live_payload.get("chain_id"),
        "status": status or ("live_verified" if live_payload.get("liveVerified") else "static_reference_pending_live_review"),
        "executionTrust": live_payload.get("executionTrust") or "static_reference_pending_live_review",
        "trustedForExecution": False,
        "readyForTransactionPreflight": bool(
            live_payload.get("readyForTransactionPreflight")
            or live_analysis.get("readyForTransactionPreflight")
            or (live_recommendation.get("readyForTransactionPreflight") if isinstance(live_recommendation, dict) else False)
        ),
        "profitable": bool(live_payload.get("profitable") or live_analysis.get("profitable")),
        "bestAmountConfirmed": live_best_amount_confirmed,
        "blockNumber": live_payload.get("blockNumber"),
        "quoteBlockSource": live_payload.get("quoteBlockSource") or "route_live_estimate",
        "routeSymbols": live_payload.get("routeSymbols") or route.get("routeSymbols") or [],
        "hops": steps,
        "costModel": {
            "borrowInputSymbol": analysis.get("referenceInputSymbol") or (live_payload.get("routeSymbols") or [""])[0],
            "borrowInputAmount": live_best_amount,
            "selectedBorrowInputAmount": live_best_amount,
            "bestAmountComputed": live_best_amount is not None,
            "bestAmountConfirmed": live_best_amount_confirmed,
            "bestAmountMissing": live_best_amount is None or not live_best_amount_confirmed,
            "requestedBorrowInputAmount": (
                live_payload.get("requestedAmount")
                or live_analysis.get("requestedAmount")
                or live_payload.get("initialProbeAmount")
                or live_analysis.get("initialProbeAmount")
                or live_payload.get("amount")
            ),
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
    existing_evidence_mode = str(analysis.get("priceEvidenceMode") or "").strip().lower()
    existing_evidence_source = str(analysis.get("priceEvidenceSource") or "").strip().lower()
    static_evidence_modes = {
        "static_reference_pending_live_review",
        "static_reference_only",
        "static_live_quoter_estimate",
        "live_quote_reference_only",
        "reference_only",
    }
    price_evidence_mode = (
        "live_verified"
        if live_verified
        else "static_reference_pending_live_review"
        if existing_evidence_mode in static_evidence_modes or not live_verified
        else "live_required"
    )
    price_evidence_source = (
        "chain_live_quote"
        if live_verified
        else "static_reference_pending_live_review"
        if existing_evidence_source in static_evidence_modes or not live_verified
        else "chain_live_quote_required"
    )
    analysis.update(
        {
            "live_onchain_evidence": evidence,
            "onchainEvidenceStatus": status,
            "livePriceVerified": live_verified,
            "priceEvidenceMode": price_evidence_mode,
            "priceEvidenceSource": price_evidence_source,
            "requiresLiveQuoter": not live_verified,
            "quote_verified": live_verified,
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "bestAmountMissing": live_best_amount is None or not live_best_amount_confirmed,
            "bestAmountConfirmed": live_best_amount_confirmed,
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
            or 1.0
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
    "_infer_route_addresses_from_pools",
    "_has_live_verified_evidence",
    "_has_preserved_manifest_evidence",
    "_manifest_live_evidence_for_route",
    "_merge_manifest_live_evidence_into_routes",
    "_live_quote_deferred_route",
    "_merge_live_route_estimate",
    "_merge_recent_attempt_evidence_into_routes",
    "_reference_only_live_quote_context",
    "_route_dedup_key",
    "_route_symbol_key",
    "_scanner_snapshot_is_stale",
]
