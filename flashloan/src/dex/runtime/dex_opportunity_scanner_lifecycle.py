from __future__ import annotations

import sys
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


def _compat_engine_attr(name, fallback):
    facade = sys.modules.get("dex.runtime.dex_opportunity_scanner_engine")
    return getattr(facade, name, fallback) if facade is not None else fallback


class DexOpportunityScannerLifecycleMixin:
        def _broadcast_supervisor_status(self) -> dict[str, Any]:
            """Read native queue state without submitting or replacing a transaction."""
            if not self._native_rust_closed_loop:
                return {"available": False, "reason": "native_worker_pool_disabled"}
            from dex.runtime import rust_closed_loop
    
            database_url = self._native_database_url()
            return rust_closed_loop.native_worker_status(
                include_payloads=False,
                database_url=database_url,
            )


        def start(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
            self.watch(chain_id=chain_id, protocols=protocols)
            if not self._background_enabled:
                return
            if self._native_rust_closed_loop:
                try:
                    from dex.runtime.rust_closed_loop import register_maintenance_scope
                    from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
    
                    normalized_protocols = [
                        str(item).strip().lower()
                        for item in (protocols or [])
                        if str(item or "").strip()
                    ]
                    if chain_id is not None:
                        with chain_database_scope(
                            chain_id=chain_id,
                            network=_network_for_chain_id(chain_id),
                            source="dex-scanner-start",
                        ):
                            _compat_engine_attr("get_native_pool", get_native_pool)()
                            register_maintenance_scope(
                                network=_network_for_chain_id(chain_id),
                                chain_id=chain_id,
                                protocol=normalized_protocols[0] if normalized_protocols else "uniswap_v4",
                            )
                    else:
                        _compat_engine_attr("get_native_pool", get_native_pool)()
                        register_maintenance_scope(
                            network=None,
                            chain_id=None,
                            protocol=normalized_protocols[0] if normalized_protocols else "uniswap_v4",
                        )
                except Exception as exc:
                    logger.error("Rust closed-loop pool startup failed: %s", exc, exc_info=True)
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    if self._native_rust_closed_loop and (
                        self._watchdog_thread is None or not self._watchdog_thread.is_alive()
                    ):
                        self._watchdog_thread = threading.Thread(
                            target=self._run_supervisor_watchdog,
                            name="dex-closed-loop-watchdog",
                            daemon=True,
                        )
                        self._watchdog_thread.start()
                    return
                self._stop.clear()
                self._thread = threading.Thread(target=self.run_forever, name="dex-opportunity-scanner", daemon=True)
                self._thread.start()
                if self._native_rust_closed_loop:
                    self._watchdog_thread = threading.Thread(
                        target=self._run_supervisor_watchdog,
                        name="dex-closed-loop-watchdog",
                        daemon=True,
                    )
                    self._watchdog_thread.start()
                if not self._native_rust_closed_loop:
                    if self.closed_loop_review_supervisor is not None:
                        self.closed_loop_review_supervisor.start()
                    else:
                        self.closed_loop_review_pipeline.start()
                elif self.closed_loop_broadcast_supervisor is not None:
                    self.closed_loop_broadcast_supervisor.start()


        def stop(self) -> None:
            self._stop.set()
            self.closed_loop_review_pipeline.stop()
            if self.closed_loop_review_supervisor is not None:
                self.closed_loop_review_supervisor.stop()
            if self.closed_loop_broadcast_supervisor is not None:
                self.closed_loop_broadcast_supervisor.stop()
            if self._native_rust_closed_loop:
                try:
                    stop_native_pool()
                except Exception as exc:
                    logger.error("Rust closed-loop pool stop failed: %s", exc, exc_info=True)


        def _run_supervisor_watchdog(self) -> None:
            """Run lease recovery independently of the potentially slow RPC scan."""
            interval = max(1.0, min(15.0, float(os.getenv("DEX_CLOSED_LOOP_WATCHDOG_INTERVAL_SECONDS", "5"))))
            while not self._stop.is_set():
                watched = self._watched_items()
                for chain_id, _protocols in watched:
                    if self._stop.is_set():
                        break
                    try:
                        self._watch_closed_loop_supervisors(chain_id=chain_id)
                    except Exception as exc:
                        logger.warning("closed-loop watchdog tick failed for chain %s: %s", chain_id, exc)
                self._stop.wait(interval)


        def watch(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
            normalized_protocols = tuple(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
            with self._lock:
                self._watched[_chain_scope_key(chain_id, list(normalized_protocols))] = (chain_id, normalized_protocols)


        def run_forever(self) -> None:
            while not self._stop.is_set():
                queued = self._pop_queued_scan()
                if queued is not None:
                    chain_id, protocols = queued
                    self._scan_one(chain_id=chain_id, protocols=list(protocols))
                    self._watch_closed_loop_supervisors(chain_id=chain_id)
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
                    self._watch_closed_loop_supervisors(chain_id=chain_id)
                if not self._stop.is_set():
                    self._stop.wait(self._queue_poll_seconds)


        def _watch_closed_loop_supervisors(self, *, chain_id: int | None = None) -> dict[str, Any]:
            if chain_id is not None:
                from core.db.chain_database_context import chain_database_scope
                from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
    
                with chain_database_scope(
                    chain_id=chain_id,
                    network=_network_for_chain_id(chain_id),
                    source="dex-scanner-watchdog",
                ):
                    return self._watch_closed_loop_supervisors_inner(chain_id=chain_id)
            return self._watch_closed_loop_supervisors_inner(chain_id=chain_id)


        def _watch_closed_loop_supervisors_inner(self, *, chain_id: int | None = None) -> dict[str, Any]:
            """Watch for stalled review/broadcast consumers and revive them."""
            review_watch: dict[str, Any] = {"enabled": False}
            broadcast_watch: dict[str, Any] = {"enabled": False}
            loop_state_summary: dict[str, Any] = {"available": False}
            stuck_recovery: dict[str, Any] = {"failed": 0, "recovered": 0, "reviewOnlyClosed": 0}
            if self.closed_loop_review_supervisor is not None:
                review_watch = self.closed_loop_review_supervisor.revive_if_stalled()
                review_watch["enabled"] = True
            if self.closed_loop_broadcast_supervisor is not None:
                broadcast_watch = self.closed_loop_broadcast_supervisor.revive_if_stalled()
                broadcast_watch["enabled"] = True
            if self._native_rust_closed_loop:
                from dex.runtime.closed_loop_consumer import (
                    recover_stuck_execution_loops,
                    runtime_state_summary,
                )
    
                try:
                    recovery_result = recover_stuck_execution_loops(
                        database_url=self._native_database_url(chain_id),
                    )
                    stuck_recovery = {
                        "failed": int(recovery_result.get("failed") or 0),
                        "recovered": len(recovery_result.get("records") or []),
                        "reviewOnlyClosed": int(recovery_result.get("reviewOnlyClosed") or 0),
                        "reviewReclaimed": int(recovery_result.get("reviewReclaimed") or 0),
                        "broadcastReclaimed": int(recovery_result.get("broadcastReclaimed") or 0),
                        "error": recovery_result.get("error"),
                    }
                    loop_state_summary = runtime_state_summary(
                        database_url=self._native_database_url(chain_id),
                    )
                except Exception as exc:
                    stuck_recovery = {
                        "failed": 0,
                        "recovered": 0,
                        "reviewOnlyClosed": 0,
                        "error": str(exc),
                    }
            with self._lock:
                review_watch["stallDetected"] = bool(review_watch.get("stallDetected"))
                broadcast_watch["stallDetected"] = bool(broadcast_watch.get("stallDetected"))
                self._last_supervisor_watch = {
                    "review": review_watch,
                    "broadcast": broadcast_watch,
                    "loopStateSummary": loop_state_summary,
                    "stuckExecutionRecovered": stuck_recovery,
                    "stallDetected": bool(
                        review_watch.get("stallDetected") or broadcast_watch.get("stallDetected")
                    ),
                    "restarted": bool(
                        review_watch.get("restarted") or broadcast_watch.get("restarted")
                    ),
                    "watchedAt": _now_iso(),
                }
            return dict(self._last_supervisor_watch)


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
            if chain_id is not None:
                with chain_database_scope(
                    chain_id=chain_id,
                    network=_network_for_chain_id(chain_id),
                    source="dex-scanner",
                ):
                    return self._scan_one_inner(chain_id=chain_id, protocols=list(protocols))
            return self._scan_one_inner(chain_id=chain_id, protocols=list(protocols))
