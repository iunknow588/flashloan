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


class DexOpportunityScannerRecordingMixin:
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
