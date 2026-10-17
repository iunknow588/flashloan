from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
import os
import threading
import time
from typing import Any, Callable

from core.error_registry import ErrorCode
from core.parameter_config import LEGACY_STRATEGY_CONFIG_PATHS, STRATEGY_CONFIG_PATH, read_json_parameter
from market.binance_market.dex_surfaces import dex_quote_routes_payload
from dex.intent_trade.chains.robinhood_common.rpc_gate import (
    v4_live_quote_enabled as _rpc_gate_v4_live_quote_enabled,
)
from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence
from dex.runtime.dex_opportunity_helpers import (
    _apply_onchain_evidence_to_attempt,
    _chain_network,
    _evidence_ready_for_auto_execute,
    _merge_onchain_evidence_into_route,
    _network_for_chain_id,
    _recordable_route,
    _route_attempt,
    _route_evidence,
    _select_recordable_routes,
)
from dex.runtime.dex_opportunity_snapshot import _route_is_temporary


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]
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


def _closed_loop_review_group_key(route: dict[str, Any]) -> str:
    """Return a review group key that isolates one-shot replacement routes."""
    group_key = _route_closed_loop_group_key(route)
    if _route_is_temporary(route):
        pool_key = str(
            route.get("poolPathKey")
            or route.get("referencePoolPathKey")
            or ""
        ).strip().lower()
        group_key = f"temporary:{group_key}:{pool_key}"
    return group_key


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


def _broadcast_profit_value(payload: Any) -> float | None:
    """Best-effort numeric profit from an official arb broadcast payload."""
    if not isinstance(payload, dict):
        return None
    for key in ("profit", "profitHuman", "netProfitAmount", "profitAmount"):
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError):
            continue
        if value:
            return value
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        costs = evidence.get("costModel")
        if isinstance(costs, dict):
            for key in ("netProfitAmount", "netProfitUsd", "profitAmount", "profit"):
                try:
                    value = float(costs.get(key))
                except (TypeError, ValueError):
                    continue
                if value:
                    return value
    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        for key in ("referenceNetProfitAmount", "netProfitAmount", "profitAmount"):
            try:
                value = float(analysis.get(key))
            except (TypeError, ValueError):
                continue
            if value:
                return value
    return None


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
        return _quote_routes_payload_from_facade(**kwargs)
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
        return _quote_routes_payload_from_facade(
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
    return _quote_routes_payload_from_facade(
        chain_id=chain_id,
        protocols=protocols,
        # Robinhood mainnet/testnet route review is a closed-loop market observation
        # task, not a fresh computed route expansion. Keep the scanner aligned
        # with the DEX overview instead of surfacing every candidate loop.
        use_static_observation=_is_robinhood_family_network(chain_id),
    )


def _quote_routes_payload_from_facade(*args, **kwargs):
    """Prefer the scanner facade's dex_quote_routes_payload when available.

    Existing tests monkeypatch ``dex_opportunity_scanner.dex_quote_routes_payload``
    to control closed-loop refresh payloads.  Keep that seam working by routing
    through the facade when it has been loaded, falling back to the local import
    for standalone use.
    """
    try:
        import importlib

        # Tests and older callers import the scanner under the legacy
        # ``runtime.dex_opportunity_scanner`` alias, while the split package
        # lives at ``dex.runtime.dex_opportunity_scanner``.  Python treats
        # those as distinct module objects, so prefer the runtime alias when
        # it is loaded to keep monkeypatched ``dex_quote_routes_payload``
        # hooks visible to the scanner engine.
        for module_name in ("runtime.dex_opportunity_scanner", "dex.runtime.dex_opportunity_scanner"):
            try:
                scanner_module = importlib.import_module(module_name)
            except Exception:
                continue
            payload_fn = getattr(scanner_module, "dex_quote_routes_payload", None)
            if payload_fn is not None and payload_fn is not _quote_routes_payload_from_facade:
                return payload_fn(*args, **kwargs)
    except Exception:
        pass
    return dex_quote_routes_payload(*args, **kwargs)


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
    if _is_robinhood_family_network(chain_id) and not _rpc_gate_v4_live_quote_enabled(chain_id=chain_id):
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
            group_key = _closed_loop_review_group_key(_route)
            route_group_by_key.setdefault(group_key, []).append(_route)

        group_evidence_cache: dict[str, dict[str, Any]] = {}
        group_evidence_lock = threading.Lock()

        def _review_one(item: tuple[int, dict[str, Any], dict[str, Any], int]) -> tuple[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]]:
            attempt_index, route, attempt, original_index = item
            group_key = _closed_loop_review_group_key(route)
            if _route_is_closed_loop_quad(route) and not _route_is_temporary(route):
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
                if _route_is_closed_loop_quad(route) and not _route_is_temporary(route):
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
            if _route_is_closed_loop_quad(route) and not _route_is_temporary(route):
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
