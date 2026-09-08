from __future__ import annotations

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


def default_quote_payload_provider(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=False,
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
    testnet_full_scan = _chain_network(payload, chain_id) == "robinhood-testnet"
    selected_routes = _select_recordable_routes(
        recordable_routes,
        max_routes=max_recordable_routes,
        testnet_full_scan=testnet_full_scan,
    )
    raw_routes = [route for _, route, _ in selected_routes]
    raw_attempts = [attempt for _, _, attempt in selected_routes]
    payload_routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    evidence_provider = onchain_evidence_provider or build_route_onchain_evidence
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
    for attempt_index, (original_index, route, attempt) in enumerate(selected_routes):
        if attempt_index in selected_indexes:
            evidence = evidence_provider(route, payload=payload, chain_id=chain_id, observed_at=observed_at, trigger_context=trigger_context or {})
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
    ) -> None:
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.record_cooldown_seconds = max(0.0, float(record_cooldown_seconds))
        self.quote_payload_provider = quote_payload_provider or default_quote_payload_provider
        self.record_attempts = record_attempts or default_record_attempts
        self.onchain_evidence_provider = onchain_evidence_provider or build_route_onchain_evidence
        self.trigger_context_provider = trigger_context_provider or capture_onchain_trigger_context
        self.cache_path = cache_path
        self.record_opportunities = bool(record_opportunities)
        self.max_onchain_evidence_routes = max(0, int(max_onchain_evidence_routes))
        self.max_recordable_routes = max(0, int(max_recordable_routes))
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
                use_static_observation=False,
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
