from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from core.chain_data_registry import chain_data_by_chain_id
from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl
from core.error_registry import ErrorCode
from core.parameter_config import LEGACY_STRATEGY_CONFIG_PATHS, STRATEGY_CONFIG_PATH, read_json_parameter
from market.binance_market.dex_surfaces import dex_overview_payload, dex_quote_routes_payload
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path
from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence, capture_onchain_trigger_context
from dex.runtime.dex_closed_loop_review_queue import ClosedLoopReviewQueue, FixedRefreshPipeline
from dex.runtime.dex_opportunity_auto_execute import (
    DexAutoExecuteRunner,
    auto_execute_callback_from_env as default_auto_execute_callback,
    auto_execute_cooldown_seconds as default_auto_execute_cooldown_seconds,
    auto_execute_env_enabled as default_auto_execute_env_enabled,
)
from dex.runtime.dex_opportunity_helpers import (
    _apply_onchain_evidence_to_attempt,
    _chain_key,
    _chain_network,
    _evidence_ready_for_auto_execute,
    _env_float,
    _live_profit_percent,
    _merge_onchain_evidence_into_route,
    _network_for_chain_id,
    _now_iso,
    _recordable_route,
    _refresh_payload_summary,
    _route_attempt,
    _route_blocked_reasons,
    _route_evidence,
    _route_priority_key,
    _select_recordable_routes,
    _snapshot_summary,
    _trigger_block_number,
    merge_recent_manual_review_evidence_into_routes,
)


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
_SCANNER: DexOpportunityScanner | None = None
_SCANNER_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _structured_dex_error(message: object, *, flow: str, node_id: str) -> list[dict[str, Any]]:
    return [
        {
            "module": "DEX",
            "code": int(ErrorCode.DEX_INTERNAL),
            "flow": flow,
            "nodeId": node_id,
            "message": str(message or "")[:500],
        }
    ]


def _route_closed_loop_group_key(route: dict[str, Any]) -> str:
    addresses = route.get("routeAddresses") if isinstance(route.get("routeAddresses"), list) else route.get("addresses")
    if isinstance(addresses, list):
        for item in addresses:
            text = str(item or "").strip().lower()
            if text:
                return text
    symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else route.get("symbols")
    if isinstance(symbols, list):
        for item in symbols:
            text = str(item or "").strip().upper()
            if text:
                return text
    return "fallback"


def _route_is_closed_loop_quad(route: dict[str, Any]) -> bool:
    symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else route.get("symbols")
    if not isinstance(symbols, list) or len(symbols) != 4:
        return False
    return str(symbols[0] or "").strip().upper() == str(symbols[-1] or "").strip().upper()


def _attempt_route_identity(attempt: dict[str, Any]) -> tuple[Any, ...]:
    for key in ("routeAddresses", "route_addresses"):
        value = attempt.get(key)
        if isinstance(value, list):
            addresses = tuple(str(item or "").strip().lower() for item in value if str(item or "").strip())
            if addresses:
                return addresses
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    for key in ("routeAddresses", "route_addresses"):
        value = quote.get(key)
        if isinstance(value, list):
            addresses = tuple(str(item or "").strip().lower() for item in value if str(item or "").strip())
            if addresses:
                return addresses
    return tuple(str(item or "").strip().upper() for item in (attempt.get("route_path") or []) if str(item or "").strip())


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
    raw = saved.get("DEX_EVIDENCE_TIMEOUT_SECONDS", os.getenv("DEX_OPPORTUNITY_SCANNER_EVIDENCE_TIMEOUT_SECONDS", "90"))
    try:
        return max(60.0, float(raw))
    except (TypeError, ValueError):
        return 90.0


def _auto_execute_log_stats(chain_id: int | None) -> dict[str, Any]:
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
        auto_execute_count += 1
        ts = str(auto.get("observed_at") or attempt.get("created_at") or attempt.get("observed_at") or "").strip()
        if ts and (last_auto_execute_at is None or ts > last_auto_execute_at):
            last_auto_execute_at = ts
    return {
        "auto_execute_count": auto_execute_count,
        "last_auto_execute_at": last_auto_execute_at,
    }


def _is_robinhood_family_network(chain_id: int | None) -> bool:
    return _network_for_chain_id(chain_id).lower() in {
        "robinhood",
        "robinhood-mainnet",
        "robinhood-testnet",
        "robinhood_testnet",
    }


def _scanner_onchain_evidence_provider(*args, **kwargs):
    # Robinhood closed-loop routes are used for live route review. Keep the
    # page scanner fast by skipping the full execution-size search on the
    # background scan; the on-chain quoter still verifies each hop with a
    # conservative 0.1-token starting amount when no reference amount exists.
    # Dedicated sizing flows can still opt in with an env flag.
    skip_size_probe = str(os.getenv("DEX_OPPORTUNITY_SCANNER_SKIP_EXECUTION_SIZE_PROBE", "1")).strip().lower() in {"1", "true", "yes", "on"}
    kwargs.setdefault("skip_execution_size_probe", skip_size_probe)
    evidence = build_route_onchain_evidence(*args, **kwargs)
    if str(evidence.get("status") or "").lower() == "rpc_verification_failed":
        # A testnet RPC can briefly refuse a new connection during parallel
        # review. Give that route one immediate retry before reporting failure.
        time.sleep(1.0)
        evidence = build_route_onchain_evidence(*args, **kwargs)
    if str(evidence.get("status") or "").lower() == "not_enough_liquidity":
        # The quick UI scan keeps full size probing off to stay responsive.
        # When the requested amount is not liquid enough, keep halving until a
        # smaller amount quotes or the retry budget is exhausted. This gives
        # operators concrete evidence about whether the pool has any usable
        # liquidity at all, instead of a bare "not enough liquidity".
        original_amount_raw = evidence.get("selectedBorrowInputAmountRaw") or evidence.get("requestedBorrowInputAmountRaw")
        liquidity_retry_attempts = max(0, int(os.getenv("DEX_OPPORTUNITY_SCANNER_LIQUIDITY_RETRY_ATTEMPTS", "3")))
        try:
            original_int_raw = int(original_amount_raw)
        except (TypeError, ValueError):
            original_int_raw = None
        if original_int_raw and original_int_raw > 0:
            retry_amount_raw = original_int_raw
            probe_attempts = [
                {
                    "attempt": 1,
                    "amountRaw": str(original_int_raw),
                    "status": "not_enough_liquidity",
                    "failureReason": "not_enough_liquidity",
                }
            ]
            succeeded_evidence = None
            for attempt_index in range(1, liquidity_retry_attempts + 1):
                retry_amount_raw = max(1, int(retry_amount_raw) // 2)
                if retry_amount_raw <= 0:
                    break
                retry_kwargs = {key: value for key, value in kwargs.items() if key not in {"skip_execution_size_probe", "borrow_input_amount_override_raw"}}
                retry_kwargs["skip_execution_size_probe"] = True
                retry_kwargs["borrow_input_amount_override_raw"] = retry_amount_raw
                halved_evidence = build_route_onchain_evidence(*args, **retry_kwargs)
                halved_status = str(halved_evidence.get("status") or "").lower()
                probe_attempts.append(
                    {
                        "attempt": attempt_index + 1,
                        "amountRaw": str(retry_amount_raw),
                        "status": halved_status,
                        "failureReason": None if halved_status != "not_enough_liquidity" else "not_enough_liquidity",
                    }
                )
                if halved_status != "not_enough_liquidity":
                    succeeded_evidence = deepcopy(halved_evidence)
                    break

            if succeeded_evidence is not None:
                evidence = succeeded_evidence
                try:
                    confirm_kwargs = {
                        key: value
                        for key, value in retry_kwargs.items()
                        if key not in {"skip_execution_size_probe"}
                    }
                    confirm_kwargs["skip_execution_size_probe"] = False
                    confirmed_evidence = build_route_onchain_evidence(*args, **confirm_kwargs)
                    if (
                        str(confirmed_evidence.get("status") or "").lower() == "live_verified"
                        and (
                            confirmed_evidence.get("bestAmountConfirmed") is True
                            or (confirmed_evidence.get("costModel") or {}).get("bestAmountConfirmed") is True
                        )
                    ):
                        evidence = confirmed_evidence
                except Exception:
                    # Keep the already-successful halved quote if the deeper
                    # size probe is not able to confirm a best amount right now.
                    evidence = succeeded_evidence
            else:
                evidence = deepcopy(evidence)
            last_attempt = probe_attempts[-1]
            evidence["executionSizeProbe"] = {
                "status": "route_execution_size_probe_liquidity_retry",
                "searchStatus": "halved_amount_quoted" if succeeded_evidence is not None else "not_enough_liquidity",
                "probeAttempts": len(probe_attempts),
                "maxProbeAttempts": liquidity_retry_attempts + 1,
                "requestedAmountRaw": str(original_int_raw),
                "initialProbeAmountRaw": str(original_int_raw),
                "selectedProfitableAmountRaw": str(retry_amount_raw) if succeeded_evidence is not None else None,
                "firstFailureAmountRaw": str(retry_amount_raw) if succeeded_evidence is None else str(original_int_raw),
                "attempts": probe_attempts,
            }
    return evidence


def _evidence_provider_with_timeout(evidence_provider, timeout_seconds: float, *args, **kwargs):
    """Run one on-chain evidence pass with a real watchdog timeout.

    The quoter already has its own internal RPC timeouts, but a stuck
    testnet RPC connection can still outlive the scanner budget.  When that
    happens we return a structured timeout evidence instead of letting the
    review hang until a lower layer gives up.
    """
    if timeout_seconds <= 0:
        return evidence_provider(*args, **kwargs)
    route = args[0] if args else kwargs.get("route") or {}
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(evidence_provider, *args, **kwargs)
    timed_out = False
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        timed_out = True
        return {
            "status": "rpc_verification_timeout",
            "executionTrust": "rpc_verification_timeout",
            "trustedForExecution": False,
            "blockers": [
                f"on-chain evidence timed out after {timeout_seconds:.1f}s"
            ],
            "routeSymbols": route.get("routeSymbols") or route.get("symbols") or [],
            "routeAddresses": route.get("routeAddresses") or route.get("addresses") or [],
            "hops": [],
            "costModel": {},
            "transactionDevelopment": {},
            "errorStack": [
                {
                    "module": "DEX",
                    "code": int(ErrorCode.DEX_RPC_TIMEOUT),
                    "flow": "onchain_evidence",
                    "nodeId": route.get("routeSymbols") or route.get("symbols") or "route",
                    "message": f"on-chain evidence timed out after {timeout_seconds:.1f}s",
                }
            ],
        }
    finally:
        executor.shutdown(wait=not timed_out)


def _reference_only_quote_payload(**kwargs):
    key = "DEX_V4_LIVE_QUOTE_ENABLED"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    try:
        return dex_quote_routes_payload(**kwargs)
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def default_quote_payload_provider(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    # Mainnet V4 is the production read-only verification target. Do not use
    # the testnet/reference-only compatibility path for it: that path disables
    # live quotes by design and would make a correctly configured mainnet
    # adapter look unavailable.
    if _network_for_chain_id(chain_id).lower() in {"robinhood", "robinhood-mainnet"}:
        return dex_quote_routes_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
    if _is_robinhood_family_network(chain_id):
        return _reference_only_quote_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
    return dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        # Robinhood mainnet/testnet route review is a closed-loop market observation
        # task, not a fresh computed route expansion. Keep the scanner aligned
        # with the DEX overview instead of surfacing every candidate loop.
        use_static_observation=_is_robinhood_family_network(chain_id),
    )


def build_dex_opportunity_attempts(
    payload: dict[str, Any],
    *,
    chain_id: int | None,
    observed_at: str,
    onchain_evidence_provider: OnchainEvidenceProvider | None = None,
    max_onchain_evidence_routes: int | None = None,
    max_recordable_routes: int | None = None,
    testnet_max_recordable_routes: int | None = None,
    onchain_evidence_offset: int = 0,
    evidence_workers: int = 1,
    trigger_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_attempts = []
    raw_routes = []
    recordable_routes: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, route in enumerate(payload.get("routes") or [], start=1):
        if isinstance(route, dict) and _recordable_route(route, chain_id=chain_id):
            route_with_trigger = deepcopy(route)
            quote_inputs = route_with_trigger.get("quoteInputs") if isinstance(route_with_trigger.get("quoteInputs"), dict) else {}
            route_with_trigger["quoteInputs"] = {**quote_inputs, "triggerContext": trigger_context or {}}
            recordable_routes.append(
                (
                    index,
                    route_with_trigger,
                    _route_attempt(
                        route_with_trigger,
                        payload=payload,
                        chain_id=chain_id,
                        pair_rank=index,
                        observed_at=observed_at,
                        trigger_context=trigger_context,
                    ),
                )
            )
    if not recordable_routes:
        return []
    # Every Robinhood-family network is a full-scan review target. The
    # deprecated DEX_OPPORTUNITY_TESTNET_FULL_SCAN switch no longer controls
    # scanner behavior; all eligible recordable closed-loop routes must be
    # chain-reviewed in the current scan.
    robinhood_full_scan = _chain_network(payload, chain_id) in {"robinhood", "robinhood-testnet"}
    selected_routes = _select_recordable_routes(
        recordable_routes,
        max_routes=max_recordable_routes,
        testnet_full_scan=robinhood_full_scan,
    )
    raw_routes = [route for _, route, _ in selected_routes]
    raw_attempts = [attempt for _, _, attempt in selected_routes]
    payload_routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    evidence_provider = onchain_evidence_provider or _scanner_onchain_evidence_provider
    # Every recordable closed-loop route is reviewed in this scan.  The old
    # rotating route budget silently converted ordinary candidates into
    # ``live_evidence_deferred`` records, although no product requirement
    # authorized postponing their chain review.
    selected_indexes: set[int] = set(range(len(raw_attempts)))
    attempts = []
    evidence_results: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]] = {}
    if selected_indexes:
        evidence_timeout_seconds = _scanner_evidence_timeout_seconds()
        requested_workers = max(1, int(evidence_workers))

        route_group_by_key: dict[str, list[dict[str, Any]]] = {}
        for _original_index, _route, _attempt in selected_routes:
            if not isinstance(_route, dict):
                continue
            group_key = _route_closed_loop_group_key(_route)
            route_group_by_key.setdefault(group_key, []).append(_route)

        group_evidence_cache: dict[str, dict[str, Any]] = {}
        group_evidence_lock = threading.Lock()

        def _review_one(item: tuple[int, dict[str, Any], dict[str, Any], int]) -> tuple[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]]:
            attempt_index, route, attempt, original_index = item
            group_key = _route_closed_loop_group_key(route)
            if _route_is_closed_loop_quad(route):
                with group_evidence_lock:
                    cached = group_evidence_cache.get(group_key)
                if cached is not None:
                    cached_evidence = deepcopy(cached)
                    if isinstance(cached_evidence, dict):
                        cached_evidence["closedLoopGroupEvidence"] = {"shared": True, "groupKey": group_key}
                    return attempt_index, (cached_evidence, route, attempt, original_index)
            recent_evidence = _route_evidence(route)
            if _evidence_ready_for_auto_execute(recent_evidence):
                reusable_evidence = deepcopy(recent_evidence)
                reusable_evidence["reusedRecentAttemptEvidence"] = True
                if _route_is_closed_loop_quad(route):
                    reusable_evidence["closedLoopGroupEvidence"] = {"shared": False, "groupKey": group_key}
                    with group_evidence_lock:
                        group_evidence_cache.setdefault(group_key, deepcopy(reusable_evidence))
                return attempt_index, (reusable_evidence, route, attempt, original_index)
            evidence = _evidence_provider_with_timeout(
                evidence_provider,
                evidence_timeout_seconds,
                route,
                payload=payload,
                chain_id=chain_id,
                observed_at=observed_at,
                trigger_context=trigger_context or {},
                closed_loop_routes_group=route_group_by_key.get(group_key) or [route],
            )
            if _route_is_closed_loop_quad(route):
                if isinstance(evidence, dict):
                    evidence["closedLoopGroupEvidence"] = {"shared": False, "groupKey": group_key}
                with group_evidence_lock:
                    group_evidence_cache.setdefault(group_key, deepcopy(evidence))
            return attempt_index, (evidence, route, attempt, original_index)

        review_items = [
            (attempt_index, route, attempt, original_index)
            for attempt_index, (original_index, route, attempt) in enumerate(selected_routes)
            if attempt_index in selected_indexes
        ]
        if requested_workers <= 1:
            # Default path: keep the current machine serial. This is a global,
            # user-controlled setting; DEX_SCAN_WORKERS / evidence_workers > 1
            # can be enabled later when the host is known to tolerate parallel
            # RPC/quoter review.
            for item in review_items:
                attempt_index, result = _review_one(item)
                evidence_results[attempt_index] = result
        else:
            with ThreadPoolExecutor(max_workers=requested_workers) as executor:
                futures = [executor.submit(_review_one, item) for item in review_items]
                for future in futures:
                    attempt_index, result = future.result()
                    evidence_results[attempt_index] = result
    for attempt_index, (original_index, route, attempt) in enumerate(selected_routes):
        if attempt_index in selected_indexes:
            if attempt_index in evidence_results:
                evidence, route, attempt, original_index = evidence_results[attempt_index]
            else:
                evidence = _evidence_provider_with_timeout(
                    evidence_provider,
                    evidence_timeout_seconds,
                    route,
                    payload=payload,
                    chain_id=chain_id,
                    observed_at=observed_at,
                    trigger_context=trigger_context or {},
                )
            _merge_onchain_evidence_into_route(route, evidence)
            attempts.append(_apply_onchain_evidence_to_attempt(attempt, evidence))
        if 0 < original_index <= len(payload_routes) and isinstance(payload_routes[original_index - 1], dict):
            payload_routes[original_index - 1].clear()
            payload_routes[original_index - 1].update(deepcopy(route))
    return attempts


def default_record_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    from dex.runtime.cow_attempt_log import record_cow_attempt_list_safely

    return record_cow_attempt_list_safely(
        attempts,
        database_url=None,
        dedupe_market_candidates=False,
    )


class DexOpportunityScanner:
    def __init__(
        self,
        *,
        interval_seconds: float = 180.0,
        record_cooldown_seconds: float = 60.0,
        auto_execute_cooldown_seconds: float | None = None,
        quote_payload_provider: QuotePayloadProvider | None = None,
        record_attempts: RecordAttempts | None = None,
        onchain_evidence_provider: OnchainEvidenceProvider | None = None,
        trigger_context_provider: TriggerContextProvider | None = None,
        auto_execute_callback: AutoExecuteCallback | None = None,
        auto_execute_enabled: bool | None = None,
        cache_path: Path | None = CACHE_PATH,
        record_opportunities: bool = True,
        max_onchain_evidence_routes: int = 5,
        max_recordable_routes: int | None = None,
        testnet_max_onchain_evidence_routes: int | None = None,
        evidence_workers: int = 1,
        periodic_scan_enabled: bool = True,
    ) -> None:
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.periodic_scan_enabled = bool(periodic_scan_enabled)
        self._queue_poll_seconds = max(0.25, min(1.0, self.interval_seconds / 4.0))
        self.record_cooldown_seconds = max(0.0, float(record_cooldown_seconds))
        self.auto_execute_cooldown_seconds = max(
            0.0,
            float(auto_execute_cooldown_seconds if auto_execute_cooldown_seconds is not None else default_auto_execute_cooldown_seconds()),
        )
        self.quote_payload_provider = quote_payload_provider or default_quote_payload_provider
        self.record_attempts = record_attempts or default_record_attempts
        self.auto_execute_enabled = bool(auto_execute_enabled if auto_execute_enabled is not None else default_auto_execute_env_enabled())
        base_evidence_provider = onchain_evidence_provider or _scanner_onchain_evidence_provider

        def evidence_provider(*args, **kwargs):
            kwargs["skip_execution_size_probe"] = not self.auto_execute_enabled
            return base_evidence_provider(*args, **kwargs)

        self.onchain_evidence_provider = evidence_provider
        self.trigger_context_provider = trigger_context_provider or capture_onchain_trigger_context
        self.auto_execute_callback = auto_execute_callback
        self._auto_execute_runner = DexAutoExecuteRunner(
            callback=self.auto_execute_callback,
            enabled=self.auto_execute_enabled,
            cooldown_seconds=self.auto_execute_cooldown_seconds,
        )
        self.cache_path = cache_path
        self.record_opportunities = bool(record_opportunities)
        self.max_onchain_evidence_routes = max(0, int(max_onchain_evidence_routes))
        self.max_recordable_routes = None if max_recordable_routes is None else max(0, int(max_recordable_routes))
        self.evidence_workers = max(1, int(evidence_workers))
        self.testnet_max_onchain_evidence_routes = (
            None
            if testnet_max_onchain_evidence_routes is None
            else max(0, int(testnet_max_onchain_evidence_routes))
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._watched: dict[str, tuple[int | None, tuple[str, ...]]] = {}
        self._queued_scans: deque[tuple[int | None, tuple[str, ...]]] = deque()
        self._last_periodic_scan_at: dict[str, float] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._recorded_signatures: dict[tuple[Any, ...], float] = {}
        self._last_recording: dict[str, Any] = {"recorded": 0, "source": "empty", "error": None}
        self._recorded_attempt_count = 0
        self._auto_execute_count = 0
        self._last_auto_execute_at: str | None = None
        self._last_error: str | None = None
        self._last_error_stack: list[dict[str, Any]] | None = None
        self._scan_count = 0
        self._last_scan_started_at: str | None = None
        self._last_scan_finished_at: str | None = None
        self._last_duration_ms: int | None = None
        self.closed_loop_review_queue = ClosedLoopReviewQueue()
        self.closed_loop_review_pipeline = FixedRefreshPipeline(
            queue=self.closed_loop_review_queue,
            refresh_fn=self._fixed_refresh_group,
        )
        self._load_cache()

    def _fixed_refresh_group(self, task) -> dict[str, Any]:
        anchor = str(task.group_key or "").strip().upper()
        payload = dex_quote_routes_payload(
            chain_id=task.chain_id,
            protocols=list(task.protocols),
            use_static_observation=True,
            anchor=anchor,
        )
        if task.mode in {"deep", "broadcast"}:
            from market.binance_market.dex_route_test import (
                estimate_route_live_quote,
                recommended_route_test_amount,
                run_official_v4_arb_route_test,
            )

            routes = payload.get("routes") or []
            refreshed = []
            refresh_attempt_pairs = []
            manual_review_count = 0
            auto_broadcast_count = 0
            auto_broadcast_failed = 0
            auto_broadcast_errors: list[str] = []
            for route in routes:
                route_symbols = [str(item) for item in (route.get("routeSymbols") or []) if str(item).strip()]
                if not route_symbols:
                    refreshed.append(route)
                    continue
                try:
                    amount = recommended_route_test_amount(
                        route_symbols=route_symbols,
                        chain_id=task.chain_id,
                        protocols=list(task.protocols),
                    )
                    if amount is None:
                        refreshed.append(route)
                        continue
                    if self.auto_execute_enabled:
                        manual_result = self._run_manual_route_review_and_broadcast(
                            route=route,
                            route_symbols=route_symbols,
                            amount=amount,
                            chain_id=task.chain_id,
                            protocols=list(task.protocols),
                            run_official_v4_arb_route_test=run_official_v4_arb_route_test,
                        )
                        result = manual_result["result"]
                        route["__manualPreflight"] = manual_result["preflight"]
                        route["__manualBroadcast"] = manual_result["broadcast"]
                        manual_review_count += 1
                        broadcast_payload = manual_result["broadcast"]
                        if broadcast_payload is not None:
                            if bool(broadcast_payload.get("ok")) and broadcast_payload.get("txHash"):
                                auto_broadcast_count += 1
                            else:
                                auto_broadcast_failed += 1
                                auto_broadcast_errors.append(
                                    str(broadcast_payload.get("error") or "official_arb_auto_broadcast_failed")[:300]
                                )
                    else:
                        result = estimate_route_live_quote(
                            chain_id=task.chain_id,
                            protocols=list(task.protocols),
                            route_symbols=route_symbols,
                            amount=amount,
                            route=route,
                        )
                    if isinstance(result, dict):
                        route["__liveEstimate"] = result
                        attempt = self._fixed_refresh_attempt(route, chain_id=task.chain_id, observed_at=_now_iso())
                        if attempt is not None:
                            refresh_attempt_pairs.append((route, attempt))
                    refreshed.append(route)
                except Exception as exc:
                    detail = str(exc)[:300]
                    route["__liveEstimate"] = {
                        "ok": False,
                        "error": detail,
                        "quoteStatus": "live_quote_failed",
                        "errorStack": _structured_dex_error(
                            detail,
                            flow="closed_loop_refresh",
                            node_id=anchor,
                        ),
                    }
                    refreshed.append(route)
            payload["routes"] = refreshed
            if self.auto_execute_enabled and manual_review_count:
                payload["auto_execute"] = {
                    "autoExecuteEnabled": True,
                    "manualReviewCount": manual_review_count,
                    "autoExecuted": auto_broadcast_count,
                    "autoExecuteSkipped": manual_review_count - auto_broadcast_count - auto_broadcast_failed,
                    "autoExecuteFailed": auto_broadcast_failed,
                    "autoExecuteErrors": auto_broadcast_errors,
                }
                if auto_broadcast_count:
                    with self._lock:
                        self._auto_execute_count += auto_broadcast_count
                        self._last_auto_execute_at = _now_iso()
                for route in refreshed:
                    broadcast_payload = route.get("__manualBroadcast")
                    auto_execute_payload = {
                        "observed_at": _now_iso(),
                        "route_symbols": route.get("routeSymbols") or [],
                        "broadcast": broadcast_payload is not None,
                        "attempted": isinstance(broadcast_payload, dict),
                    }
                    if isinstance(broadcast_payload, dict):
                        auto_execute_payload.update({
                            "amount": broadcast_payload.get("amount"),
                            "result": broadcast_payload,
                            "executed": bool(broadcast_payload.get("ok")) and bool(broadcast_payload.get("txHash")),
                        })
                    else:
                        auto_execute_payload["skipped"] = True
                        auto_execute_payload["reason"] = "manual_review_not_profitable"
                    route["auto_execute"] = auto_execute_payload
                    for _, attempt in refresh_attempt_pairs:
                        if attempt.get("route_path") == route.get("routeSymbols"):
                            attempt["auto_execute"] = auto_execute_payload
        self._merge_fixed_refresh_into_snapshot(task, payload)
        return {
            "ok": True,
            "mode": task.mode,
            "anchor": anchor,
            "routeCount": len(payload.get("routes") or []),
            "routes": payload.get("routes") or [],
            "auto_execute": payload.get("auto_execute"),
        }

    def _run_manual_route_review_and_broadcast(
        self,
        *,
        route: dict[str, Any],
        route_symbols: list[str],
        amount: float,
        chain_id: int | None,
        protocols: list[str],
        run_official_v4_arb_route_test: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        route_addresses = [
            str(item).strip()
            for item in (route.get("routeAddresses") or [])
            if str(item).strip()
        ]
        preflight = run_official_v4_arb_route_test(
            route_symbols=route_symbols,
            amount=amount,
            broadcast=False,
            chain_id=chain_id,
            route_addresses=route_addresses or None,
        )
        if (
            not isinstance(preflight, dict)
            or not preflight.get("ok")
            or not preflight.get("bestAmountConfirmed")
            or not self._manual_review_is_profitable(preflight)
        ):
            return {"preflight": preflight, "broadcast": None, "result": preflight}
        broadcast_amount = preflight.get("bestAmount") or amount
        amount_is_best = bool(preflight.get("bestAmountConfirmed"))
        broadcast = run_official_v4_arb_route_test(
            route_symbols=route_symbols,
            amount=broadcast_amount,
            broadcast=True,
            chain_id=chain_id,
            route_addresses=route_addresses or None,
            amount_is_best=amount_is_best,
        )
        if not isinstance(broadcast, dict):
            broadcast = {"ok": False, "error": "official_arb_auto_broadcast_failed"}
        return {"preflight": preflight, "broadcast": broadcast, "result": broadcast}

    @staticmethod
    def _manual_review_is_profitable(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        if bool(payload.get("profitable")):
            return True
        try:
            return float(payload.get("profit") or 0) > 0
        except (TypeError, ValueError):
            return False

    def _fixed_refresh_attempt(self, route: dict[str, Any], *, chain_id: int | None, observed_at: str) -> dict[str, Any] | None:
        estimate = route.get("__liveEstimate")
        if not isinstance(estimate, dict) or not estimate.get("ok"):
            return None
        route_symbols = [str(item) for item in (estimate.get("routeSymbols") or route.get("routeSymbols") or []) if str(item).strip()]
        if not route_symbols:
            return None
        route_addresses = [
            str(item).strip().lower()
            for item in (estimate.get("routeAddresses") or route.get("routeAddresses") or [])
            if str(item).strip()
        ]
        best_amount_confirmed = bool(
            estimate.get("bestAmountConfirmed")
            or (estimate.get("analysis") or {}).get("bestAmountConfirmed")
            or (estimate.get("bestAmountCalculation") or {}).get("bestAmountConfirmed")
        )
        best_amount_missing = bool(
            estimate.get("bestAmountMissing") is True
            or (estimate.get("analysis") or {}).get("bestAmountMissing") is True
            or (estimate.get("bestAmountCalculation") or {}).get("bestAmountMissing") is True
            or estimate.get("bestAmount") in (None, "")
            or not best_amount_confirmed
        )
        quote_status = str(estimate.get("quoteStatus") or "").strip().lower()
        execution_trust = str(estimate.get("executionTrust") or "").strip().lower()
        if best_amount_missing and (quote_status == "live_verified" or execution_trust == "live_quoter_verified_readonly"):
            quote_status = "static_reference_pending_live_review"
            execution_trust = "static_reference_pending_live_review"
        evidence = {
            "status": quote_status,
            "executionTrust": execution_trust,
            "livePriceVerified": bool(estimate.get("liveVerified")) and not best_amount_missing,
            "readyForTransactionPreflight": bool(estimate.get("readyForTransactionPreflight")) and not best_amount_missing,
            "profitable": bool(estimate.get("profitable")),
            "bestAmountConfirmed": best_amount_confirmed,
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "hops": list(estimate.get("steps") or []),
            "costModel": {
                "selectedBorrowInputAmount": estimate.get("bestAmount"),
                "borrowInputAmount": estimate.get("bestAmount"),
                "bestAmountConfirmed": best_amount_confirmed,
                "bestAmountMissing": best_amount_missing,
                "netProfitAmount": (estimate.get("analysis") or {}).get("referenceNetProfitAmount"),
                "netProfitBps": (estimate.get("analysis") or {}).get("referenceNetReturnBps"),
            },
            "validation": {
                "profitableAfterCosts": bool(estimate.get("profitable")),
            },
            "blockers": [
                "chain hops passed for current amount, but best amount is not confirmed; cannot enter transaction preflight"
            ] if best_amount_missing else [],
            "conclusion": "链上报价已验证当前金额，但最佳量未计算成功；复核未通过" if best_amount_missing else None,
            "errorStack": [
                {
                    "module": "DEX",
                    "code": 3006,
                    "message": "chain hops passed for current amount, but best amount is not confirmed; cannot enter transaction preflight",
                }
            ] if best_amount_missing else [],
        }
        quote = dict(route.get("quote") or {})
        quote["live_onchain_evidence"] = evidence
        quote["quote_analysis"] = estimate.get("analysis") or {}
        return {
            "network": _network_for_chain_id(chain_id),
            "chain_id": chain_id,
            "pair": " -> ".join(route_symbols),
            "route_path": route_symbols,
            "route_addresses": route_addresses,
            "routeAddresses": route_addresses,
            "priority_reason": "closed_loop_fixed_refresh",
            "quote": quote,
            "quoteAnalysis": estimate.get("analysis") or {},
            "observed_at": observed_at,
        }

    def _merge_fixed_refresh_into_snapshot(self, task, payload: dict[str, Any]) -> None:
        key = _chain_scope_key(task.chain_id, list(task.protocols))
        refreshed_routes = payload.get("routes") or []
        if not refreshed_routes:
            return
        with self._lock:
            existing = self._snapshots.get(key)
            if existing is None:
                existing = {
                    "chain_id": task.chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "updated_at": _now_iso(),
                    "error": None,
                }
            else:
                existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
                by_key = {
                    _route_simple_key(route): route
                    for route in existing_payload.get("routes") or []
                    if isinstance(route, dict)
                }
                for route in refreshed_routes:
                    by_key[_route_simple_key(route)] = route
                existing_payload = dict(existing_payload)
                existing_payload["routes"] = list(by_key.values())
                _refresh_payload_summary(existing_payload)
                existing["payload"] = existing_payload
                existing["summary"] = _snapshot_summary(existing_payload)
                existing["updated_at"] = _now_iso()
                existing["error"] = None
            self._snapshots[key] = existing

    def set_auto_execute_enabled(self, enabled: bool) -> None:
        self.auto_execute_enabled = bool(enabled)
        if self.auto_execute_enabled and self.auto_execute_callback is None:
            self.auto_execute_callback = default_auto_execute_callback()
        self._auto_execute_runner.enabled = self.auto_execute_enabled
        self._auto_execute_runner.callback = self.auto_execute_callback

    def _enqueue_closed_loop_groups_from_payload(
        self,
        payload: dict[str, Any],
        *,
        chain_id: int | None,
        protocols: list[str] | None,
    ) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        seen: set[str] = set()
        for route in payload.get("routes") or []:
            if not isinstance(route, dict) or not _route_is_closed_loop_quad(route):
                continue
            symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else route.get("symbols")
            if isinstance(symbols, list) and symbols and str(symbols[0] or "").strip():
                group_key = str(symbols[0]).strip().upper()
            else:
                group_key = _route_closed_loop_group_key(route)
            if group_key in seen:
                continue
            seen.add(group_key)
            current = self.closed_loop_review_queue.get(group_key)
            if current is None or current.state in {"done", "error"}:
                self.closed_loop_review_queue.submit_group(
                    group_key=group_key,
                    chain_id=chain_id,
                    protocols=protocols,
                    mode="scan",
                )

    def _load_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        snapshots = data.get("snapshots")
        if isinstance(snapshots, dict):
            ttl = _scanner_snapshot_ttl_seconds()
            with self._lock:
                now = time.time()
                for key, snapshot in snapshots.items():
                    if not isinstance(snapshot, dict):
                        continue
                    updated_at = str(snapshot.get("updated_at") or "")
                    if ttl > 0 and updated_at:
                        try:
                            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
                        except ValueError:
                            parsed = None
                        if parsed is not None and now - parsed > ttl:
                            continue
                    self._snapshots[key] = snapshot
        scanner = data.get("scanner")
        if isinstance(scanner, dict):
            with self._lock:
                self._scan_count = int(scanner.get("scan_count") or 0)
                self._recorded_attempt_count = int(scanner.get("recorded_attempt_count") or 0)
                self._last_error = scanner.get("last_error")
                raw_error_stack = scanner.get("last_error_stack")
                self._last_error_stack = (
                    list(raw_error_stack)
                    if isinstance(raw_error_stack, list)
                    else None
                )
                self._last_scan_started_at = scanner.get("last_scan_started_at")
                self._last_scan_finished_at = scanner.get("last_scan_finished_at")
                self._last_duration_ms = scanner.get("last_duration_ms")
                self._auto_execute_count = int(scanner.get("auto_execute_count") or 0)
                self._last_auto_execute_at = scanner.get("last_auto_execute_at")
                last_recording = scanner.get("last_recording")
                if isinstance(last_recording, dict):
                    self._last_recording = last_recording

    def start(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        self.watch(chain_id=chain_id, protocols=protocols)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self.closed_loop_review_pipeline.start()
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self.run_forever, name="dex-opportunity-scanner", daemon=True)
            self._thread.start()
            self.closed_loop_review_pipeline.start()

    def stop(self) -> None:
        self._stop.set()
        self.closed_loop_review_pipeline.stop()

    def watch(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        normalized_protocols = tuple(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
        with self._lock:
            self._watched[_chain_scope_key(chain_id, list(normalized_protocols))] = (chain_id, normalized_protocols)

    def _latest_chain_snapshot_for_status(self, chain_id: int | None) -> dict[str, Any] | None:
        if chain_id is None:
            return None
        base = _chain_key(chain_id)
        exact = self._snapshots.get(base)
        if exact is not None:
            return deepcopy(exact)
        candidates = [
            snapshot
            for key, snapshot in self._snapshots.items()
            if key == base or key.startswith(base + "|")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: str((item or {}).get("updated_at") or ""), reverse=True)
        return deepcopy(candidates[0])

    def status(self, *, chain_id: int | None = None) -> dict[str, Any]:
        log_stats = _auto_execute_log_stats(chain_id)
        thread = self._thread
        with self._lock:
            watched_keys = sorted(self._watched)
            queued_count = len(self._queued_scans)
            snapshot = self._latest_chain_snapshot_for_status(chain_id)
            scan_count = self._scan_count
            last_error = self._last_error
            last_error_stack = deepcopy(self._last_error_stack)
            last_started = self._last_scan_started_at
            last_finished = self._last_scan_finished_at
            last_duration = self._last_duration_ms
            last_recording = deepcopy(self._last_recording)
            recorded_attempt_count = self._recorded_attempt_count
            auto_execute_count = self._auto_execute_count
            last_auto_execute_at = self._last_auto_execute_at
        log_auto_execute_count = int(log_stats.get("auto_execute_count") or 0)
        if log_auto_execute_count > auto_execute_count:
            auto_execute_count = log_auto_execute_count
        log_last_auto_execute_at = log_stats.get("last_auto_execute_at")
        if log_last_auto_execute_at and (
            last_auto_execute_at is None or str(log_last_auto_execute_at) > str(last_auto_execute_at)
        ):
            last_auto_execute_at = log_last_auto_execute_at
        return {
            "running": bool(thread and thread.is_alive()),
            "interval_seconds": self.interval_seconds,
            "record_cooldown_seconds": self.record_cooldown_seconds,
            "auto_execute_cooldown_seconds": self.auto_execute_cooldown_seconds,
            "auto_execute_enabled": self.auto_execute_enabled,
            "auto_execute_callback_configured": self.auto_execute_callback is not None,
            "max_onchain_evidence_routes": self.max_onchain_evidence_routes,
            "max_recordable_routes": self.max_recordable_routes,
            "testnet_max_onchain_evidence_routes": self.testnet_max_onchain_evidence_routes,
            "evidence_workers": self.evidence_workers,
            "periodic_scan_enabled": self.periodic_scan_enabled,
            "watched_chain_keys": watched_keys,
            "queued_scan_count": queued_count,
            "chain_id": chain_id,
            "scan_count": scan_count,
            "recorded_attempt_count": recorded_attempt_count,
            "auto_execute_count": auto_execute_count,
            "auto_execute_log_count": log_auto_execute_count,
            "closed_loop_review_queue": self.closed_loop_review_queue.summary(),
            "last_auto_execute_at": last_auto_execute_at,
            "last_recording": last_recording,
            "last_error": last_error,
            "last_error_stack": last_error_stack,
            "last_scan_started_at": last_started,
            "last_scan_finished_at": last_finished,
            "last_duration_ms": last_duration,
            "snapshot_updated_at": snapshot.get("updated_at") if snapshot else None,
        }

    def snapshot(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
        key = _chain_scope_key(chain_id, protocols)
        with self._lock:
            snapshot = deepcopy(self._snapshots.get(key))
            if snapshot is None and protocols is None and chain_id is not None:
                snapshot = self._latest_chain_snapshot_for_status(chain_id)
        if snapshot is None:
            snapshot = {
                "chain_id": chain_id,
                "payload": None,
                "summary": _snapshot_summary(None),
                "updated_at": None,
                "error": None,
            }
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else None
        if payload is not None and isinstance(payload.get("routes"), list):
            payload["routes"] = _dedupe_payload_routes(payload["routes"])
            _refresh_payload_summary(payload)
        return {"ok": True, "scanner": self.status(chain_id=chain_id), "snapshot": snapshot}

    def run_forever(self) -> None:
        while not self._stop.is_set():
            queued = self._pop_queued_scan()
            if queued is not None:
                chain_id, protocols = queued
                self._scan_one(chain_id=chain_id, protocols=list(protocols))
                continue
            watched = self._watched_items()
            if not watched:
                self._stop.wait(self._queue_poll_seconds)
                continue
            if not self.periodic_scan_enabled:
                self._stop.wait(self._queue_poll_seconds)
                continue
            for chain_id, protocols in watched:
                if self._stop.is_set():
                    break
                key = _chain_scope_key(chain_id, list(protocols))
                now = time.monotonic()
                with self._lock:
                    last_periodic_at = self._last_periodic_scan_at.get(key)
                if last_periodic_at is not None and now - last_periodic_at < self.interval_seconds:
                    continue
                self._scan_one(chain_id=chain_id, protocols=list(protocols))
                with self._lock:
                    self._last_periodic_scan_at[key] = time.monotonic()
            if not self._stop.is_set():
                self._stop.wait(self._queue_poll_seconds)

    def scan_now(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
        self.watch(chain_id=chain_id, protocols=protocols)
        self._scan_one(chain_id=chain_id, protocols=protocols or [])
        return self.snapshot(chain_id=chain_id, protocols=protocols)

    def scan_async(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
        self.watch(chain_id=chain_id, protocols=protocols)
        with self._lock:
            scan_key = (chain_id, tuple(str(item).strip().lower() for item in (protocols or []) if str(item).strip()))
            if scan_key not in self._queued_scans:
                self._queued_scans.append(scan_key)
        self.start(chain_id=chain_id, protocols=protocols)
        snapshot = self.snapshot(chain_id=chain_id, protocols=protocols)
        snapshot["queued"] = True
        return snapshot

    def _pop_queued_scan(self) -> tuple[int | None, tuple[str, ...]] | None:
        with self._lock:
            if not self._queued_scans:
                return None
            return self._queued_scans.popleft()

    def _watched_items(self) -> list[tuple[int | None, tuple[str, ...]]]:
        with self._lock:
            return list(self._watched.values())

    def _scan_one(self, *, chain_id: int | None, protocols: list[str]) -> None:
        started_at = _now_iso()
        started = time.perf_counter()
        key = _chain_scope_key(chain_id, protocols)
        trigger_context = self.trigger_context_provider(
            chain_id=chain_id,
            network=_network_for_chain_id(chain_id),
            observed_at=started_at,
        )
        if _trigger_block_number(trigger_context) is None and _network_for_chain_id(chain_id) == "robinhood-testnet":
            try:
                from dex.intent_trade.chains.robinhood_testnet.trigger_context import capture_robinhood_testnet_trigger_context

                fallback_trigger_context = capture_robinhood_testnet_trigger_context(
                    chain_id=chain_id,
                    network="robinhood-testnet",
                    observed_at=started_at,
                )
                if _trigger_block_number(fallback_trigger_context) is not None:
                    trigger_context = fallback_trigger_context
            except Exception:
                logger.warning(
                    "robinhood trigger-context fallback failed for chain %s",
                    chain_id,
                    exc_info=True,
                )
        with self._lock:
            self._last_scan_started_at = started_at
        with self._scan_lock:
            try:
                payload = self.quote_payload_provider(chain_id=chain_id, protocols=protocols)
                if isinstance(payload.get("routes"), list):
                    payload["routes"] = _dedupe_payload_routes(payload["routes"])
                working_payload = deepcopy(payload)
                merge_recent_manual_review_evidence_into_routes(
                    working_payload.setdefault("routes", []) or [],
                    chain_id=chain_id,
                )
                recording = self._record_opportunities(working_payload, chain_id=chain_id, observed_at=started_at, trigger_context=trigger_context)
                self._enqueue_closed_loop_groups_from_payload(
                    working_payload,
                    chain_id=chain_id,
                    protocols=protocols,
                )
                _refresh_payload_summary(working_payload)
                snapshot = {
                    "chain_id": chain_id,
                    "payload": working_payload,
                    "summary": _snapshot_summary(working_payload),
                    "trigger_context": trigger_context,
                    "updated_at": _now_iso(),
                    "error": None,
                    "recording": recording,
                }
                error = None
                error_stack = None
            except Exception as exc:
                payload = None
                snapshot = None
                error = str(exc)
                error_stack = _structured_dex_error(
                    error,
                    flow="opportunity_scan",
                    node_id=key,
                )
            finished_at = _now_iso()
            duration_ms = int((time.perf_counter() - started) * 1000)
            with self._lock:
                self._scan_count += 1
                self._last_error = error
                self._last_error_stack = error_stack
                self._last_scan_finished_at = finished_at
                self._last_duration_ms = duration_ms
                if snapshot is not None:
                    self._snapshots[key] = snapshot
                elif key not in self._snapshots:
                    self._snapshots[key] = {
                        "chain_id": chain_id,
                        "payload": payload,
                        "summary": _snapshot_summary(None),
                        "updated_at": None,
                        "error": error,
                        "errorStack": error_stack,
                    }
                else:
                    self._snapshots[key]["error"] = error
                    self._snapshots[key]["errorStack"] = error_stack
            self._write_cache()

    def _write_cache(self) -> None:
        if self.cache_path is None:
            return
        with self._lock:
            thread = self._thread
            scanner = {
                "running": bool(thread and thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "record_cooldown_seconds": self.record_cooldown_seconds,
                "periodic_scan_enabled": self.periodic_scan_enabled,
                "watched_chain_keys": sorted(self._watched),
                "queued_scan_count": len(self._queued_scans),
                "chain_id": None,
                "scan_count": self._scan_count,
                "recorded_attempt_count": self._recorded_attempt_count,
                "auto_execute_count": self._auto_execute_count,
                "last_auto_execute_at": self._last_auto_execute_at,
                "last_recording": deepcopy(self._last_recording),
                "last_error": self._last_error,
                "last_error_stack": deepcopy(self._last_error_stack),
                "last_scan_started_at": self._last_scan_started_at,
                "last_scan_finished_at": self._last_scan_finished_at,
                "last_duration_ms": self._last_duration_ms,
                "snapshot_updated_at": None,
            }
            payload = {
                "updated_at": _now_iso(),
                "scanner": scanner,
                "snapshots": deepcopy(self._snapshots),
            }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.cache_path)
        except Exception:
            return

    def _record_opportunities(
        self,
        payload: dict[str, Any],
        *,
        chain_id: int | None,
        observed_at: str,
        trigger_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = build_dex_opportunity_attempts(
            payload,
            chain_id=chain_id,
            observed_at=observed_at,
        onchain_evidence_provider=self.onchain_evidence_provider,
        max_recordable_routes=self.max_recordable_routes,
        testnet_max_recordable_routes=self.testnet_max_onchain_evidence_routes,
        evidence_workers=self.evidence_workers,
        trigger_context=trigger_context,
    )
        if not attempts:
            recording = {"candidate_count": 0, "recorded": 0, "source": "empty", "error": None}
            with self._lock:
                self._last_recording = recording
            return recording
        auto_execute_result = self._auto_execute_ready_attempts(attempts, chain_id=chain_id, observed_at=observed_at)
        if not self.record_opportunities:
            recording = {
                "candidate_count": len(attempts),
                "recorded": 0,
                "source": "disabled",
                "error": None,
            }
            if auto_execute_result.get("autoExecuteEnabled"):
                recording.update(auto_execute_result)
            with self._lock:
                self._last_recording = recording
            return recording
        now = time.monotonic()
        cooled_attempts = []
        with self._lock:
            for attempt in attempts:
                signature = (
                    attempt.get("network"),
                    attempt.get("chain_id"),
                    attempt.get("pair"),
                    _attempt_route_identity(attempt),
                    attempt.get("priority_reason"),
                )
                last_recorded_at = self._recorded_signatures.get(signature)
                if last_recorded_at is not None and now - last_recorded_at < self.record_cooldown_seconds:
                    continue
                self._recorded_signatures[signature] = now
                cooled_attempts.append(attempt)
        recording = self.record_attempts(cooled_attempts)
        recording = {
            "candidate_count": len(attempts),
            "attempted_record_count": len(cooled_attempts),
            **(recording if isinstance(recording, dict) else {"recorded": 0, "source": "unknown", "error": None}),
        }
        if auto_execute_result.get("autoExecuteEnabled"):
            recording.update(auto_execute_result)
        with self._lock:
            self._last_recording = recording
            self._recorded_attempt_count += int(recording.get("recorded") or 0)
        return recording

    def _auto_execute_ready_attempts(
        self,
        attempts: list[dict[str, Any]],
        *,
        chain_id: int | None,
        observed_at: str,
    ) -> dict[str, Any]:
        result = self._auto_execute_runner.run_ready_attempts(
            attempts,
            chain_id=chain_id,
            observed_at=observed_at,
        )
        executed_count = int(result.get("autoExecuted") or 0)
        if executed_count:
            with self._lock:
                self._auto_execute_count += executed_count
                self._last_auto_execute_at = observed_at
        return result


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
