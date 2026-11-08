from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
import os
import threading
import time
from typing import Any

from core.db.chain_database_context import chain_database_scope
from dex.runtime.dex_closed_loop_review_queue import FixedRefreshPipeline
from dex.runtime.closed_loop_supervisor import ReviewSupervisor
from dex.intent_trade.chains.robinhood_common.rpc_gate import v4_live_quote_enabled as _rpc_gate_v4_live_quote_enabled
from dex.runtime.dex_opportunity_auto_execute import (
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
from dex.runtime.dex_opportunity_route_runner import (
    _attempt_route_identity,
    _broadcast_profit_value,
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
from dex.runtime.rust_closed_loop import get_native_pool, native_closed_loop_enabled, stop_native_pool


class DexOpportunityScannerStateMixin:
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


        def _latest_chain_snapshot_for_status(
            self,
            chain_id: int | None,
            *,
            include_payload: bool = True,
        ) -> dict[str, Any] | None:
            if chain_id is None:
                return None
            base = _chain_key(chain_id)
            exact = self._snapshots.get(base)
            if exact is not None:
                if include_payload:
                    return deepcopy(exact)
                return {
                    key: deepcopy(exact.get(key))
                    for key in (
                        "chain_id",
                        "summary",
                        "updated_at",
                        "error",
                        "errorStack",
                        "recording",
                    )
                    if key in exact
                }
            candidates = [
                snapshot
                for key, snapshot in self._snapshots.items()
                if key == base or key.startswith(base + "|")
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: str((item or {}).get("updated_at") or ""), reverse=True)
            if include_payload:
                return deepcopy(candidates[0])
            return {
                key: deepcopy(candidates[0].get(key))
                for key in (
                    "chain_id",
                    "summary",
                    "updated_at",
                    "error",
                    "errorStack",
                    "recording",
                )
                if key in candidates[0]
            }


        def status(self, *, chain_id: int | None = None) -> dict[str, Any]:
            thread = self._thread
            with self._lock:
                watched_keys = sorted(self._watched)
                queued_count = len(self._queued_scans)
                snapshot = self._latest_chain_snapshot_for_status(
                    chain_id,
                    include_payload=False,
                )
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
                last_supervisor_watch = deepcopy(self._last_supervisor_watch)
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
                "execution_backend": "rust" if self._native_rust_closed_loop else "python",
                "scan_count": scan_count,
                "recorded_attempt_count": recorded_attempt_count,
                "closed_loop_scan_count": closed_loop_scan_count,
                "closed_loop_review_count": closed_loop_review_count,
                "auto_execute_count": auto_execute_count,
                "auto_broadcast_count": auto_execute_count,
                "auto_execute_log_count": log_auto_execute_count,
                "closed_loop_review_queue": {
                    **self.closed_loop_review_queue.summary(),
                    "fallback_only": bool(self._native_rust_closed_loop),
                    "executionBackend": "rust" if self._native_rust_closed_loop else "python",
                    "last_supervisor_watch": last_supervisor_watch,
                    "loopStateSummary": (
                        last_supervisor_watch.get("loopStateSummary") or {"available": False}
                    ),
                    "stuckExecutionRecovered": int(
                        (last_supervisor_watch.get("stuckExecutionRecovered") or {}).get("failed") or 0
                    ),
                },
                "closed_loop_supervisors": {
                    "enabled": self.closed_loop_review_supervisor is not None
                    or self.closed_loop_broadcast_supervisor is not None,
                    "pythonFallbackEnabled": self.closed_loop_review_supervisor
                    is not None
                    or self.closed_loop_broadcast_supervisor is not None,
                    "lastSupervisorWatch": last_supervisor_watch,
                    "reviewStallDetected": bool(
                        (last_supervisor_watch.get("review") or {}).get("stallDetected")
                    ),
                    "broadcastStallDetected": bool(
                        (last_supervisor_watch.get("broadcast") or {}).get("stallDetected")
                    ),
                    "review": (
                        self.closed_loop_review_supervisor.status()
                        if self.closed_loop_review_supervisor is not None
                        else {"running": False, "mode": "native_rust" if self._native_rust_closed_loop else "legacy_pipeline"}
                    ),
                    "broadcast": (
                        self.closed_loop_broadcast_supervisor.status()
                        if self.closed_loop_broadcast_supervisor is not None
                        else {"running": False, "mode": "native_rust_manager" if self._native_rust_closed_loop else "disabled"}
                    ),
                },
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
                "closed_loop_rust_workers": self._rust_worker_status(),
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
