from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from dex.runtime.dex_closed_loop_review_queue import ClosedLoopReviewQueue, FixedRefreshPipeline
from dex.runtime.dex_opportunity_auto_execute import (
    DexAutoExecuteRunner,
    auto_execute_callback_from_env as default_auto_execute_callback,
    auto_execute_cooldown_seconds as default_auto_execute_cooldown_seconds,
    auto_execute_env_enabled as default_auto_execute_env_enabled,
)
from dex.runtime.dex_opportunity_helpers import (
    _chain_key,
    _network_for_chain_id,
    _now_iso,
    _refresh_payload_summary,
    _snapshot_summary,
    _structured_dex_error,
    _trigger_block_number,
    merge_recent_manual_review_evidence_into_routes,
)
from dex.runtime.dex_onchain_quote_evidence import capture_onchain_trigger_context
from dex.runtime.dex_opportunity_review_engine import DexOpportunityReviewEngineMixin
from dex.runtime.dex_opportunity_route_runner import (
    _attempt_route_identity,
    _broadcast_profit_value,
    _route_closed_loop_group_key,
    _route_is_closed_loop_quad,
    build_dex_opportunity_attempts,
    default_quote_payload_provider,
    default_record_attempts,
    _scanner_onchain_evidence_provider,
)
from dex.runtime.dex_opportunity_snapshot import (
    _auto_execute_log_stats,
    _chain_scope_key,
    _dedupe_payload_routes,
    _route_is_temporary,
    _route_simple_key,
    _scanner_snapshot_ttl_seconds,
)


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
logger = logging.getLogger(__name__)


class DexOpportunityScanner(DexOpportunityReviewEngineMixin):
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
        self._chain_stats: dict[str, dict[str, Any]] = {}
        self._recorded_attempt_count = 0
        self._closed_loop_scan_count = 0
        self._closed_loop_review_count = 0
        self._auto_execute_count = 0
        self._last_auto_execute_at: str | None = None
        self._session_reset_at = _now_iso()
        self._session_executed_count = 0
        self._session_auto_executed_count = 0
        self._session_positive_profit_count = 0
        self._session_positive_profit_amount = 0.0
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

    def _merge_fixed_refresh_into_snapshot(self, task, payload: dict[str, Any]) -> None:
        key = _chain_scope_key(task.chain_id, list(task.protocols))
        refreshed_routes = payload.get("routes") or []
        if not refreshed_routes:
            return
        with self._lock:
            existing = self._snapshots.get(key)
            if existing is None:
                _refresh_payload_summary(payload)
                existing = {
                    "chain_id": task.chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "recording": payload.get("recording"),
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
                if payload.get("recording") is not None:
                    existing["recording"] = payload.get("recording")
                existing["updated_at"] = _now_iso()
                existing["error"] = None
            self._snapshots[key] = existing

    def set_auto_execute_enabled(self, enabled: bool) -> None:
        self.auto_execute_enabled = bool(enabled)
        if self.auto_execute_enabled and self.auto_execute_callback is None:
            self.auto_execute_callback = default_auto_execute_callback()
        self._auto_execute_runner.enabled = self.auto_execute_enabled
        self._auto_execute_runner.callback = self.auto_execute_callback

    def _record_session_executed(
        self,
        payload: dict[str, Any],
        *,
        auto: bool = True,
        chain_id: int | None = None,
    ) -> None:
        with self._lock:
            self._session_executed_count += 1
            if auto:
                self._session_auto_executed_count += 1
            profit = _broadcast_profit_value(payload)
            if profit is not None and profit > 0:
                self._session_positive_profit_count += 1
                self._session_positive_profit_amount += float(profit)
            if chain_id is not None:
                stats = self._get_chain_stats(chain_id)
                if stats is not None:
                    stats["session_executed_count"] += 1
                    if auto:
                        stats["session_auto_executed_count"] += 1
                    if profit is not None and profit > 0:
                        stats["session_positive_profit_count"] += 1
                        stats["session_positive_profit_amount"] += float(profit)

    def _default_chain_stats(self) -> dict[str, Any]:
        return {
            "scan_count": 0,
            "recorded_attempt_count": 0,
            "closed_loop_scan_count": 0,
            "closed_loop_review_count": 0,
            "auto_execute_count": 0,
            "last_auto_execute_at": None,
            "last_recording": {"recorded": 0, "source": "empty", "error": None},
            "session_reset_at": _now_iso(),
            "session_executed_count": 0,
            "session_auto_executed_count": 0,
            "session_positive_profit_count": 0,
            "session_positive_profit_amount": 0.0,
        }

    def _get_chain_stats(self, chain_id: int | None) -> dict[str, Any] | None:
        if chain_id is None:
            return None
        key = _chain_key(chain_id)
        existing = self._chain_stats.get(key)
        if existing is not None:
            return existing
        stats = self._default_chain_stats()
        self._chain_stats[key] = stats
        return stats

    def reset_dex_opportunity_stats(
        self,
        *,
        chain_id: int | None = None,
        protocols: list[str] | None = None,
    ) -> None:
        with self._lock:
            if chain_id is None:
                self._session_reset_at = _now_iso()
                self._session_executed_count = 0
                self._session_auto_executed_count = 0
                self._session_positive_profit_count = 0
                self._session_positive_profit_amount = 0.0
                self._closed_loop_scan_count = 0
                self._closed_loop_review_count = 0
                self._auto_execute_count = 0
                self._last_auto_execute_at = None
                self._recorded_attempt_count = 0
                self._scan_count = 0
                self._last_recording = {"recorded": 0, "source": "reset", "error": None}
                self._recorded_signatures.clear()
            self._auto_execute_runner.reset_cooldowns()
            if chain_id is None:
                self._chain_stats.clear()
                self._snapshots.clear()
            else:
                self._chain_stats[_chain_key(chain_id)] = self._default_chain_stats()
                base = _chain_key(chain_id)
                chain_scope = _chain_key(chain_id)
                chain_id_values = {chain_id, str(chain_id)}
                self._recorded_signatures = {
                    signature: recorded_at
                    for signature, recorded_at in self._recorded_signatures.items()
                    if len(signature) < 2
                    or not (
                        signature[1] in chain_id_values
                        or chain_scope in {str(signature[0]), str(signature[1])}
                    )
                }
                cleared = {
                    "chain_id": chain_id,
                    "payload": None,
                    "summary": _snapshot_summary(None),
                    "updated_at": None,
                    "error": None,
                }
                for key in list(self._snapshots):
                    if key == base or key.startswith(base + "|"):
                        self._snapshots[key] = dict(cleared)
        self._write_cache()

    def _enqueue_closed_loop_groups_from_payload(
        self,
        payload: dict[str, Any],
        *,
        chain_id: int | None,
        protocols: list[str] | None,
    ) -> None:
        if self._thread is None or not self._thread.is_alive():
            if not self.auto_execute_enabled:
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
                    mode="deep" if self.auto_execute_enabled else "scan",
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
                self._closed_loop_scan_count = int(scanner.get("closed_loop_scan_count") or 0)
                self._closed_loop_review_count = int(scanner.get("closed_loop_review_count") or 0)
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
                self._session_reset_at = str(scanner.get("session_reset_at") or _now_iso())
                self._session_executed_count = int(scanner.get("session_executed_count") or 0)
                self._session_auto_executed_count = int(scanner.get("session_auto_executed_count") or 0)
                self._session_positive_profit_count = int(scanner.get("session_positive_profit_count") or 0)
                try:
                    self._session_positive_profit_amount = float(scanner.get("session_positive_profit_amount") or 0)
                except (TypeError, ValueError):
                    self._session_positive_profit_amount = 0.0
                last_recording = scanner.get("last_recording")
                if isinstance(last_recording, dict):
                    self._last_recording = last_recording
        chain_stats = data.get("chain_stats")
        if isinstance(chain_stats, dict):
            with self._lock:
                for key, stats in chain_stats.items():
                    if isinstance(stats, dict):
                        defaults = self._default_chain_stats()
                        self._chain_stats[key] = {
                            **defaults,
                            **stats,
                            "last_recording": {
                                **defaults.get("last_recording", {}),
                                **(stats.get("last_recording") if isinstance(stats.get("last_recording"), dict) else {}),
                            },
                        }

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
        thread = self._thread
        with self._lock:
            watched_keys = sorted(self._watched)
            queued_count = len(self._queued_scans)
            snapshot = self._latest_chain_snapshot_for_status(chain_id)
            chain_stats = self._chain_stats.get(_chain_key(chain_id)) if chain_id is not None else None
            if chain_stats is not None:
                scan_count = int(chain_stats.get("scan_count") or 0)
                last_recording = deepcopy(chain_stats.get("last_recording") or {"recorded": 0, "source": "empty", "error": None})
                recorded_attempt_count = int(chain_stats.get("recorded_attempt_count") or 0)
                closed_loop_scan_count = int(chain_stats.get("closed_loop_scan_count") or 0)
                closed_loop_review_count = int(chain_stats.get("closed_loop_review_count") or 0)
                auto_execute_count = int(chain_stats.get("auto_execute_count") or 0)
                last_auto_execute_at = chain_stats.get("last_auto_execute_at")
                session_reset_at = str(chain_stats.get("session_reset_at") or _now_iso())
                session_executed_count = int(chain_stats.get("session_executed_count") or 0)
                session_auto_executed_count = int(chain_stats.get("session_auto_executed_count") or 0)
                session_positive_profit_count = int(chain_stats.get("session_positive_profit_count") or 0)
                session_positive_profit_amount = float(chain_stats.get("session_positive_profit_amount") or 0)
            elif chain_id is not None:
                # A chain with no recorded activity must not fall back to the
                # global totals. Return an empty per-chain baseline instead.
                defaults = self._default_chain_stats()
                scan_count = 0
                last_recording = deepcopy(defaults.get("last_recording") or {"recorded": 0, "source": "empty", "error": None})
                recorded_attempt_count = 0
                closed_loop_scan_count = 0
                closed_loop_review_count = 0
                auto_execute_count = 0
                last_auto_execute_at = None
                session_reset_at = str(defaults.get("session_reset_at") or _now_iso())
                session_executed_count = 0
                session_auto_executed_count = 0
                session_positive_profit_count = 0
                session_positive_profit_amount = 0.0
            else:
                scan_count = self._scan_count
                last_recording = deepcopy(self._last_recording)
                recorded_attempt_count = self._recorded_attempt_count
                closed_loop_scan_count = self._closed_loop_scan_count
                closed_loop_review_count = self._closed_loop_review_count
                auto_execute_count = self._auto_execute_count
                last_auto_execute_at = self._last_auto_execute_at
                session_reset_at = self._session_reset_at
                session_executed_count = self._session_executed_count
                session_auto_executed_count = self._session_auto_executed_count
                session_positive_profit_count = self._session_positive_profit_count
                session_positive_profit_amount = self._session_positive_profit_amount
            last_error = self._last_error
            last_error_stack = deepcopy(self._last_error_stack)
            last_started = self._last_scan_started_at
            last_finished = self._last_scan_finished_at
            last_duration = self._last_duration_ms
        log_stats = _auto_execute_log_stats(chain_id, since=session_reset_at)
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
            "closed_loop_scan_count": closed_loop_scan_count,
            "closed_loop_review_count": closed_loop_review_count,
            "auto_execute_count": auto_execute_count,
            "auto_broadcast_count": auto_execute_count,
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
            "session_stats": {
                "reset_at": session_reset_at,
                "executed_count": session_executed_count,
                "auto_executed_count": session_auto_executed_count,
                "positive_profit_count": session_positive_profit_count,
                "positive_profit_amount": session_positive_profit_amount,
            },
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
                # Keep evidence already stored in the live snapshot so a fresh
                # scan does not make previously reviewed routes look unreviewed
                # while the background closed-loop pipeline works through the
                # current queue.
                with self._lock:
                    existing_snapshot = self._snapshots.get(key)
                if isinstance(existing_snapshot, dict):
                    existing_payload = existing_snapshot.get("payload")
                    if isinstance(existing_payload, dict):
                        existing_by_key = {
                            _route_simple_key(route): route
                            for route in existing_payload.get("routes") or []
                            if isinstance(route, dict)
                        }
                        for route in working_payload.get("routes") or []:
                            if not isinstance(route, dict):
                                continue
                            if isinstance(route.get("live_onchain_evidence"), dict) and route["live_onchain_evidence"].get("status"):
                                continue
                            previous = existing_by_key.get(_route_simple_key(route))
                            if not isinstance(previous, dict):
                                continue
                            if _route_is_temporary(route) != _route_is_temporary(previous):
                                # Never reuse default-path evidence for a
                                # temporary replacement (or vice versa).
                                continue
                            previous_evidence = previous.get("live_onchain_evidence")
                            if not isinstance(previous_evidence, dict) or not previous_evidence.get("status"):
                                continue
                            route["live_onchain_evidence"] = deepcopy(previous_evidence)
                            route["onchainEvidenceStatus"] = previous_evidence.get("status")
                            route["quoteStatus"] = previous_evidence.get("status")
                merge_recent_manual_review_evidence_into_routes(
                    working_payload.setdefault("routes", []) or [],
                    chain_id=chain_id,
                )
                recording = self._record_opportunities(working_payload, chain_id=chain_id, observed_at=started_at, trigger_context=trigger_context)
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
                # Persist the full route universe before the background
                # closed-loop pipeline starts, so its first refresh never sees
                # only a single anchor group and accidentally becomes the
                # base snapshot for the next scan.
                with self._lock:
                    self._snapshots[key] = snapshot
                self._enqueue_closed_loop_groups_from_payload(
                    snapshot["payload"],
                    chain_id=chain_id,
                    protocols=protocols,
                )
                if self.auto_execute_enabled and (self._thread is None or not self._thread.is_alive()):
                    # If the background scanner is not running, start the
                    # closed-loop pipeline so every queued anchor group is
                    # processed instead of only the first task per scan call.
                    self.closed_loop_review_pipeline.start()
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
                if chain_id is not None:
                    chain_stats = self._get_chain_stats(chain_id)
                    if chain_stats is not None:
                        chain_stats["scan_count"] += 1
                self._last_error = error
                self._last_error_stack = error_stack
                self._last_scan_finished_at = finished_at
                self._last_duration_ms = duration_ms
                if snapshot is not None:
                    existing = self._snapshots.get(key)
                    if existing is None:
                        self._snapshots[key] = snapshot
                    else:
                        existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
                        by_key = {
                            _route_simple_key(route): route
                            for route in existing_payload.get("routes") or []
                            if isinstance(route, dict)
                        }
                        for route in snapshot.get("payload", {}).get("routes") or []:
                            if not isinstance(route, dict):
                                continue
                            key_route = _route_simple_key(route)
                            current = by_key.get(key_route)
                            if isinstance(current, dict):
                                current_temporary = _route_is_temporary(current)
                                route_temporary = _route_is_temporary(route)
                                if current_temporary and not route_temporary:
                                    # Fresh default scan route wins over stale
                                    # temporary snapshot data.
                                    by_key[key_route] = route
                                    continue
                                if route_temporary and not current_temporary:
                                    # A temporary replacement must not replace
                                    # the scan default in the persisted snapshot.
                                    continue
                                current_evidence = current.get("live_onchain_evidence")
                                if isinstance(current_evidence, dict) and current_evidence.get("status"):
                                    # The background closed-loop pipeline may have
                                    # already written fresher evidence for this
                                    # route while this scan was building. Keep it.
                                    continue
                            by_key[key_route] = route
                        merged_payload = dict(existing_payload)
                        merged_payload["routes"] = list(by_key.values())
                        _refresh_payload_summary(merged_payload)
                        existing["payload"] = merged_payload
                        existing["summary"] = _snapshot_summary(merged_payload)
                        existing["trigger_context"] = snapshot.get("trigger_context")
                        existing["recording"] = snapshot.get("recording")
                        existing["updated_at"] = snapshot.get("updated_at") or existing.get("updated_at")
                        existing["error"] = None
                        existing["errorStack"] = None
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
                "closed_loop_scan_count": self._closed_loop_scan_count,
                "closed_loop_review_count": self._closed_loop_review_count,
                "auto_execute_count": self._auto_execute_count,
                "last_auto_execute_at": self._last_auto_execute_at,
                "session_reset_at": self._session_reset_at,
                "session_executed_count": self._session_executed_count,
                "session_auto_executed_count": self._session_auto_executed_count,
                "session_positive_profit_count": self._session_positive_profit_count,
                "session_positive_profit_amount": self._session_positive_profit_amount,
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
                "chain_stats": deepcopy(self._chain_stats),
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
        if self.auto_execute_enabled:
            # When auto-execute is enabled, the closed-loop deep review pipeline
            # runs the manual review+broadcast path per route. Avoid the old
            # scan-time evidence callback path here, otherwise a slow RPC can
            # produce duplicate 90s timeout rows before the deep review runs.
            recording = {
                "candidate_count": 0,
                "recorded": 0,
                "source": "closed_loop_deep_review",
                "error": None,
                "autoExecuteEnabled": True,
                "autoExecuted": 0,
                "autoExecuteSkipped": 0,
                "autoExecuteFailed": 0,
                "autoExecuteErrors": [],
            }
            with self._lock:
                self._last_recording = recording
                if chain_id is not None:
                    chain_stats = self._get_chain_stats(chain_id)
                    if chain_stats is not None:
                        chain_stats["last_recording"] = recording
            return recording
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
                if chain_id is not None:
                    chain_stats = self._get_chain_stats(chain_id)
                    if chain_stats is not None:
                        chain_stats["last_recording"] = recording
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
                if chain_id is not None:
                    chain_stats = self._get_chain_stats(chain_id)
                    if chain_stats is not None:
                        chain_stats["last_recording"] = recording
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
            if chain_id is not None:
                chain_stats = self._get_chain_stats(chain_id)
                if chain_stats is not None:
                    chain_stats["last_recording"] = recording
                    chain_stats["recorded_attempt_count"] += int(recording.get("recorded") or 0)
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
            for attempt in attempts:
                auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
                if bool(auto.get("executed")):
                    result_payload = auto.get("result") if isinstance(auto.get("result"), dict) else {}
                    self._record_session_executed(result_payload, auto=True, chain_id=chain_id)
            with self._lock:
                self._auto_execute_count += executed_count
                self._last_auto_execute_at = observed_at
                if chain_id is not None:
                    chain_stats = self._get_chain_stats(chain_id)
                    if chain_stats is not None:
                        chain_stats["auto_execute_count"] += executed_count
                        chain_stats["last_auto_execute_at"] = observed_at
        return result
