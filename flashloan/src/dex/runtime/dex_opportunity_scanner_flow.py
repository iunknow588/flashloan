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


class DexOpportunityScannerFlowMixin:
        def _scan_one_inner(self, *, chain_id: int | None, protocols: list[str]) -> None:
            started_at = _now_iso()
            started = time.perf_counter()
            key = _chain_scope_key(chain_id, protocols)
            if not _rpc_gate_v4_live_quote_enabled(chain_id=chain_id):
                trigger_context = {
                    "source": "scan_trigger_chain_head",
                    "observedAt": started_at,
                    "network": _network_for_chain_id(chain_id),
                    "chainId": chain_id,
                    "available": False,
                    "blockNumber": None,
                    "triggerBlockNumber": None,
                    "blockTimestamp": None,
                    "rpcUrl": None,
                    "error": "chain_scoped_v4_rpc_unavailable",
                }
            else:
                trigger_context = self.trigger_context_provider(
                    chain_id=chain_id,
                    network=_network_for_chain_id(chain_id),
                    observed_at=started_at,
                )
            if (
                _trigger_block_number(trigger_context) is None
                and _network_for_chain_id(chain_id) == "robinhood-testnet"
                and _rpc_gate_v4_live_quote_enabled(chain_id=chain_id)
            ):
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
                    try:
                        self._record_native_worker_results()
                    except Exception as exc:
                        logger.warning(
                            "native Rust worker result recording failed for chain %s: %s",
                            chain_id,
                            exc,
                            exc_info=True,
                        )
                    if (
                        not self._native_rust_closed_loop
                        and self._background_enabled
                        and self.auto_execute_enabled
                        and (self._thread is None or not self._thread.is_alive())
                    ):
                        # If the background scanner is not running, start the
                        # closed-loop pipeline so every queued anchor group is
                        # processed instead of only the first task per scan call.
                        if self.closed_loop_review_supervisor is not None:
                            self.closed_loop_review_supervisor.start()
                        else:
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
