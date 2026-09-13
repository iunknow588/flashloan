from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
import time
from typing import Any, Callable

from core.chain_data_registry import chain_data_by_chain_id
from core.env_loader import local_json_only_enabled
from market.binance_market.dex_surfaces import dex_overview_payload, dex_quote_routes_payload
from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence, capture_onchain_trigger_context
from dex.runtime.dex_opportunity_helpers import (
    _apply_onchain_evidence_to_attempt,
    _chain_key,
    _chain_network,
    _env_float,
    _live_profit_percent,
    _merge_onchain_evidence_into_route,
    _network_for_chain_id,
    _now_iso,
    _number_or_none,
    _recordable_route,
    _refresh_payload_summary,
    _route_attempt,
    _route_blocked_reasons,
    _route_evidence,
    _route_priority_key,
    _select_recordable_routes,
    _snapshot_summary,
    _trigger_block_number,
)


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
_SCANNER: DexOpportunityScanner | None = None
_SCANNER_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _is_robinhood_family_network(chain_id: int | None) -> bool:
    return _network_for_chain_id(chain_id).lower() in {
        "robinhood",
        "robinhood-mainnet",
        "robinhood-testnet",
        "robinhood_testnet",
    }


def _scanner_onchain_evidence_provider(*args, **kwargs):
    # The UI review needs per-hop live Quoter/eth_call confirmation fast.
    # Full execution-size binary probing is kept available for dedicated
    # sizing flows, but is too heavy to run by default inside a page scan.
    kwargs.setdefault("skip_execution_size_probe", True)
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
    if str(evidence.get("status") or "").lower() == "live_evidence_deferred":
        trigger = kwargs.get("trigger_context") or {}
        latest_trigger = {
            key: value
            for key, value in trigger.items()
            if isinstance(key, str) and "block" not in key.lower()
        }
        latest_trigger["source"] = "latest_block_fallback_historical_state_unavailable"
        latest_trigger["observedAt"] = latest_trigger.get("observedAt") or kwargs.get("observed_at")
        retry_kwargs = {key: value for key, value in kwargs.items() if key != "trigger_context"}
        evidence = build_route_onchain_evidence(*args, trigger_context=latest_trigger, **retry_kwargs)
    return evidence


_MISSING_EFFECTIVE_POOL_STATUS = "missing_effective_pool"
_DEPTH_INSUFFICIENT_STATUS = "depth_insufficient"
_MISSING_EFFECTIVE_POOL_REASON = "on-chain reverify complete; missing effective trading pool"


def _number_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _pool_has_usable_depth(pool: dict[str, Any]) -> bool:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    tvl = _number_or_none(balances.get("tvlUsd"))
    if tvl is not None and tvl > 0:
        return True
    for token_balance in ("token0", "token1"):
        token_value = _number_or_none(balances.get(token_balance))
        if token_value is not None and token_value > 0:
            return True
    liquidity = str(pool.get("liquidity") or "").strip()
    if liquidity and liquidity != "0":
        return True
    return False


def _unreviewed_route_evidence_state(route: dict[str, Any], *, deferred_reason: str) -> tuple[str, str]:
    """Return a reviewed summary for routes that did not consume on-chain budget.

    When static pool evidence already proves there is no effective tradable pool
    or the route truncates at a zero-output hop, reporting a permanent deferral
    hides the real reason. Use a reviewed terminal state instead.
    """
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    quote_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    candidate_pools = quote_inputs.get("candidatePools") or []
    if isinstance(candidate_pools, list) and candidate_pools and not any(
        _pool_has_usable_depth(pool) for pool in candidate_pools if isinstance(pool, dict)
    ):
        return _MISSING_EFFECTIVE_POOL_STATUS, _MISSING_EFFECTIVE_POOL_REASON
    steps = analysis.get("steps") if isinstance(analysis.get("steps"), list) else []
    warnings = analysis.get("warnings") or []
    truncated_or_zero = any(
        "zero_output" in str(warning).lower() or "truncated" in str(warning).lower()
        for warning in warnings
    ) or any(
        str(step.get("outputAmount") or "").strip().lower() in {"0", "0.0"}
        for step in steps
        if isinstance(step, dict)
    )
    if truncated_or_zero:
        return _DEPTH_INSUFFICIENT_STATUS, "on-chain reverify complete; insufficient pool depth"
    return "live_evidence_deferred", deferred_reason


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
    max_recordable_routes: int | None = 5,
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
    robinhood_full_scan = _chain_network(payload, chain_id) in {"robinhood", "robinhood-testnet"}
    if os.getenv("DEX_OPPORTUNITY_TESTNET_FULL_SCAN", "1").strip().lower() in {"0", "false", "no"}:
        robinhood_full_scan = False
    selected_routes = _select_recordable_routes(
        recordable_routes,
        max_routes=max_recordable_routes,
        testnet_full_scan=robinhood_full_scan,
    )
    raw_routes = [route for _, route, _ in selected_routes]
    raw_attempts = [attempt for _, _, attempt in selected_routes]
    payload_routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    evidence_provider = onchain_evidence_provider or _scanner_onchain_evidence_provider
    # Keep the testnet candidate universe broad, but bound live RPC work per scan.
    # A full static candidate scan must not turn into an unbounded Quoter scan.
    budget = (
        len(raw_attempts)
        if max_onchain_evidence_routes is None
        else max(0, min(len(raw_attempts), int(max_onchain_evidence_routes)))
    )
    selected_indexes: set[int] = set()
    if budget:
        start = int(onchain_evidence_offset or 0) % len(raw_attempts)
        selected_indexes = {(start + offset) % len(raw_attempts) for offset in range(budget)}
    attempts = []
    deferred_reason = "reached on-chain reverify budget; deferred to next idle scan"
    evidence_results: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]] = {}
    if selected_indexes:
        evidence_workers = max(0, int(os.getenv("DEX_SCAN_WORKERS") or os.getenv("DEX_OPPORTUNITY_SCANNER_EVIDENCE_WORKERS") or evidence_workers))
        if evidence_workers > 1 and len(selected_indexes) > 1:
            futures = {}
            with ThreadPoolExecutor(max_workers=min(evidence_workers, len(selected_indexes))) as executor:
                for attempt_index, (original_index, route, attempt) in enumerate(selected_routes):
                    if attempt_index not in selected_indexes:
                        continue
                    future = executor.submit(
                        evidence_provider,
                        route,
                        payload=payload,
                        chain_id=chain_id,
                        observed_at=observed_at,
                        trigger_context=trigger_context or {},
                    )
                    futures[future] = (attempt_index, original_index, route, attempt)
                for future in as_completed(futures):
                    attempt_index, original_index, route, attempt = futures[future]
                    evidence_results[attempt_index] = (
                        future.result(),
                        route,
                        attempt,
                        original_index,
                    )
    for attempt_index, (original_index, route, attempt) in enumerate(selected_routes):
        if attempt_index in selected_indexes:
            if attempt_index in evidence_results:
                evidence, route, attempt, original_index = evidence_results[attempt_index]
            else:
                evidence = evidence_provider(
                    route,
                    payload=payload,
                    chain_id=chain_id,
                    observed_at=observed_at,
                    trigger_context=trigger_context or {},
                )
            _merge_onchain_evidence_into_route(route, evidence)
            attempts.append(_apply_onchain_evidence_to_attempt(attempt, evidence))
        else:
            evidence = {
                "evidenceVersion": 1,
                "observedAt": observed_at,
                "network": _chain_network(payload, chain_id),
                "chainId": chain_id,
                "status": "live_evidence_deferred",
                "executionTrust": "static_reference_only",
                "trustedForExecution": False,
                "blockNumber": None,
                "triggerContext": trigger_context or {},
                "triggerBlockNumber": (trigger_context or {}).get("triggerBlockNumber") or (trigger_context or {}).get("blockNumber"),
                "quoteBlockSource": "deferred",
                "routeSymbols": route.get("routeSymbols") or [],
                "routeAddresses": route.get("routeAddresses") or [],
                "hops": [],
                "costModel": {},
                "blockers": [deferred_reason],
                "transactionDevelopment": {
                    "status": "blocked_before_transaction_development",
                    "executionEnabled": False,
                    "blockers": [deferred_reason],
                },
            }
            unreviewed_status, unreviewed_reason = _unreviewed_route_evidence_state(
                route,
                deferred_reason=deferred_reason,
            )
            evidence["status"] = unreviewed_status
            evidence["quoteBlockSource"] = "deferred" if unreviewed_status == "live_evidence_deferred" else "static_depth_review"
            evidence["blockers"] = [unreviewed_reason]
            evidence["transactionDevelopment"]["blockers"] = [unreviewed_reason]
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
        quote_payload_provider: QuotePayloadProvider | None = None,
        record_attempts: RecordAttempts | None = None,
        onchain_evidence_provider: OnchainEvidenceProvider | None = None,
        trigger_context_provider: TriggerContextProvider | None = None,
        cache_path: Path | None = CACHE_PATH,
        record_opportunities: bool = True,
        max_onchain_evidence_routes: int = 5,
        max_recordable_routes: int = 5,
        testnet_max_onchain_evidence_routes: int | None = None,
        evidence_workers: int = 1,
    ) -> None:
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.record_cooldown_seconds = max(0.0, float(record_cooldown_seconds))
        self.quote_payload_provider = quote_payload_provider or default_quote_payload_provider
        self.record_attempts = record_attempts or default_record_attempts
        self.onchain_evidence_provider = onchain_evidence_provider or _scanner_onchain_evidence_provider
        self.trigger_context_provider = trigger_context_provider or capture_onchain_trigger_context
        self.cache_path = cache_path
        self.record_opportunities = bool(record_opportunities)
        self.max_onchain_evidence_routes = max(0, int(max_onchain_evidence_routes))
        self.max_recordable_routes = max(0, int(max_recordable_routes))
        self.evidence_workers = max(0, int(evidence_workers))
        self.testnet_max_onchain_evidence_routes = (
            None
            if testnet_max_onchain_evidence_routes is None
            else max(0, int(testnet_max_onchain_evidence_routes))
        )
        self._evidence_cursor: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._watched: dict[str, tuple[int | None, tuple[str, ...]]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._recorded_signatures: dict[tuple[Any, ...], float] = {}
        self._last_recording: dict[str, Any] = {"recorded": 0, "source": "empty", "error": None}
        self._recorded_attempt_count = 0
        self._last_error: str | None = None
        self._scan_count = 0
        self._last_scan_started_at: str | None = None
        self._last_scan_finished_at: str | None = None
        self._last_duration_ms: int | None = None

    def start(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        self.watch(chain_id=chain_id, protocols=protocols)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self.run_forever, name="dex-opportunity-scanner", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def watch(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        normalized_protocols = tuple(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
        with self._lock:
            self._watched[_chain_key(chain_id)] = (chain_id, normalized_protocols)

    def status(self, *, chain_id: int | None = None) -> dict[str, Any]:
        thread = self._thread
        with self._lock:
            watched_keys = sorted(self._watched)
            snapshot = deepcopy(self._snapshots.get(_chain_key(chain_id))) if chain_id is not None else None
            scan_count = self._scan_count
            last_error = self._last_error
            last_started = self._last_scan_started_at
            last_finished = self._last_scan_finished_at
            last_duration = self._last_duration_ms
            last_recording = deepcopy(self._last_recording)
            recorded_attempt_count = self._recorded_attempt_count
        return {
            "running": bool(thread and thread.is_alive()),
            "interval_seconds": self.interval_seconds,
            "record_cooldown_seconds": self.record_cooldown_seconds,
            "max_onchain_evidence_routes": self.max_onchain_evidence_routes,
            "testnet_max_onchain_evidence_routes": self.testnet_max_onchain_evidence_routes,
            "evidence_workers": self.evidence_workers,
            "watched_chain_keys": watched_keys,
            "chain_id": chain_id,
            "scan_count": scan_count,
            "recorded_attempt_count": recorded_attempt_count,
            "last_recording": last_recording,
            "last_error": last_error,
            "last_scan_started_at": last_started,
            "last_scan_finished_at": last_finished,
            "last_duration_ms": last_duration,
            "snapshot_updated_at": snapshot.get("updated_at") if snapshot else None,
        }

    def snapshot(self, *, chain_id: int | None = None) -> dict[str, Any]:
        key = _chain_key(chain_id)
        with self._lock:
            snapshot = deepcopy(self._snapshots.get(key))
        if snapshot is None:
            snapshot = {
                "chain_id": chain_id,
                "payload": None,
                "summary": _snapshot_summary(None),
                "updated_at": None,
                "error": None,
            }
        return {"ok": True, "scanner": self.status(chain_id=chain_id), "snapshot": snapshot}

    def run_forever(self) -> None:
        while not self._stop.is_set():
            watched = self._watched_items()
            for chain_id, protocols in watched:
                if self._stop.is_set():
                    break
                self._scan_one(chain_id=chain_id, protocols=list(protocols))
            self._stop.wait(self.interval_seconds)

    def scan_now(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
        self.watch(chain_id=chain_id, protocols=protocols)
        self._scan_one(chain_id=chain_id, protocols=protocols or [])
        return self.snapshot(chain_id=chain_id)

    def _watched_items(self) -> list[tuple[int | None, tuple[str, ...]]]:
        with self._lock:
            return list(self._watched.values())

    def _scan_one(self, *, chain_id: int | None, protocols: list[str]) -> None:
        started_at = _now_iso()
        started = time.perf_counter()
        key = _chain_key(chain_id)
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
                recording = self._record_opportunities(payload, chain_id=chain_id, observed_at=started_at, trigger_context=trigger_context)
                _refresh_payload_summary(payload)
                snapshot = {
                    "chain_id": chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "trigger_context": trigger_context,
                    "updated_at": _now_iso(),
                    "error": None,
                    "recording": recording,
                }
                error = None
            except Exception as exc:
                payload = None
                snapshot = None
                error = str(exc)
            finished_at = _now_iso()
            duration_ms = int((time.perf_counter() - started) * 1000)
            with self._lock:
                self._scan_count += 1
                self._last_error = error
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
                    }
                else:
                    self._snapshots[key]["error"] = error
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
                "watched_chain_keys": sorted(self._watched),
                "chain_id": None,
                "scan_count": self._scan_count,
                "recorded_attempt_count": self._recorded_attempt_count,
                "last_recording": deepcopy(self._last_recording),
                "last_error": self._last_error,
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
        max_onchain_evidence_routes = self.max_onchain_evidence_routes
        if (
            self.testnet_max_onchain_evidence_routes is not None
            and _chain_network(payload, chain_id) == "robinhood-testnet"
        ):
            max_onchain_evidence_routes = self.testnet_max_onchain_evidence_routes
        attempts = build_dex_opportunity_attempts(
            payload,
            chain_id=chain_id,
            observed_at=observed_at,
            onchain_evidence_provider=self.onchain_evidence_provider,
            max_onchain_evidence_routes=max_onchain_evidence_routes,
            max_recordable_routes=self.max_recordable_routes,
            onchain_evidence_offset=self._evidence_cursor.get(_chain_key(chain_id), 0),
            evidence_workers=self.evidence_workers,
            trigger_context=trigger_context,
        )
        if attempts and max_onchain_evidence_routes > 0:
            self._evidence_cursor[_chain_key(chain_id)] = (
                self._evidence_cursor.get(_chain_key(chain_id), 0) + max_onchain_evidence_routes
            ) % len(attempts)
        if not attempts:
            recording = {"candidate_count": 0, "recorded": 0, "source": "empty", "error": None}
            with self._lock:
                self._last_recording = recording
            return recording
        if not self.record_opportunities:
            recording = {
                "candidate_count": len(attempts),
                "recorded": 0,
                "source": "disabled",
                "error": None,
            }
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
                    tuple(attempt.get("route_path") or []),
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
        with self._lock:
            self._last_recording = recording
            self._recorded_attempt_count += int(recording.get("recorded") or 0)
        return recording


def get_dex_opportunity_scanner() -> DexOpportunityScanner:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is None:
            local_only = local_json_only_enabled()
            _SCANNER = DexOpportunityScanner(
                interval_seconds=_env_float("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", 180.0, minimum=1.0, maximum=3600.0),
                record_cooldown_seconds=_env_float(
                    "DEX_OPPORTUNITY_RECORD_COOLDOWN_SECONDS",
                    60.0,
                    minimum=0.0,
                    maximum=3600.0,
                ),
                record_opportunities=not local_only,
                max_onchain_evidence_routes=int(
                    _env_float("DEX_OPPORTUNITY_ONCHAIN_EVIDENCE_ROUTES", 1.0, minimum=0.0, maximum=10.0)
                ),
                max_recordable_routes=int(_env_float("DEX_OPPORTUNITY_SCAN_ROUTE_LIMIT", 5.0, minimum=1.0, maximum=20.0)),
                testnet_max_onchain_evidence_routes=int(
                    _env_float("DEX_OPPORTUNITY_TESTNET_ONCHAIN_EVIDENCE_ROUTES", 4.0, minimum=0.0, maximum=10.0)
                ),
            )
        return _SCANNER


def ensure_dex_opportunity_scanner_running(
    *, chain_id: int | None = None, protocols: list[str] | None = None
) -> DexOpportunityScanner:
    scanner = get_dex_opportunity_scanner()
    scanner.start(chain_id=chain_id, protocols=protocols)
    snapshot = scanner.snapshot(chain_id=chain_id)
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
                    scanner._snapshots[_chain_key(chain_id)] = warmed
        except Exception:
            logger.warning("failed to warm DEX opportunity scanner snapshot", exc_info=True)
    return scanner


def run_dex_opportunity_scan_now(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    scanner.watch(chain_id=chain_id, protocols=protocols)
    return scanner.scan_now(chain_id=chain_id, protocols=protocols)


def dex_opportunity_snapshot(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    scanner.watch(chain_id=chain_id, protocols=protocols)
    return scanner.snapshot(chain_id=chain_id)


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
