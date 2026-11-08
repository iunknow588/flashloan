from __future__ import annotations

from collections import deque
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable

from dex.runtime.closed_loop_supervisor import ReviewSupervisor
from dex.runtime.dex_closed_loop_review_queue import ClosedLoopReviewQueue, FixedRefreshPipeline
from dex.runtime.dex_opportunity_auto_execute import (
    DexAutoExecuteRunner,
    auto_execute_callback_from_env as default_auto_execute_callback,
    auto_execute_cooldown_seconds as default_auto_execute_cooldown_seconds,
    auto_execute_env_enabled as default_auto_execute_env_enabled,
)
from dex.runtime.dex_opportunity_helpers import _now_iso
from dex.runtime.dex_onchain_quote_evidence import capture_onchain_trigger_context
from dex.runtime.dex_opportunity_review_engine import DexOpportunityReviewEngineMixin
from dex.runtime.dex_opportunity_route_runner import (
    default_quote_payload_provider,
    default_record_attempts,
    _scanner_onchain_evidence_provider,
)
from dex.runtime.dex_opportunity_scanner_flow import DexOpportunityScannerFlowMixin
from dex.runtime.dex_opportunity_scanner_lifecycle import DexOpportunityScannerLifecycleMixin
from dex.runtime.dex_opportunity_scanner_recording import DexOpportunityScannerRecordingMixin
from dex.runtime.dex_opportunity_scanner_state import DexOpportunityScannerStateMixin
from dex.runtime.rust_closed_loop import get_native_pool, native_closed_loop_enabled
QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
logger = logging.getLogger(__name__)


class DexOpportunityScanner(
    DexOpportunityScannerLifecycleMixin,
    DexOpportunityScannerStateMixin,
    DexOpportunityScannerRecordingMixin,
    DexOpportunityScannerFlowMixin,
    DexOpportunityReviewEngineMixin,
):
        def __init__(
            self,
            *,
            interval_seconds: float = 60.0,
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
            self._watchdog_thread: threading.Thread | None = None
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
            self._last_supervisor_watch: dict[str, Any] = {
                "review": {"enabled": False, "stallDetected": False, "restarted": False},
                "broadcast": {"enabled": False, "stallDetected": False, "restarted": False},
                "loopStateSummary": {"available": False},
                "stuckExecutionRecovered": {"failed": 0, "recovered": 0, "reviewOnlyClosed": 0},
                "stallDetected": False,
                "restarted": False,
                "watchedAt": None,
            }
            self.closed_loop_review_queue = ClosedLoopReviewQueue()
            self.closed_loop_review_pipeline = FixedRefreshPipeline(
                queue=self.closed_loop_review_queue,
                refresh_fn=self._fixed_refresh_group,
            )
            self._native_rust_closed_loop = native_closed_loop_enabled()
            self._supervisor_enabled = str(
                os.getenv("DEX_CLOSED_LOOP_SUPERVISOR_ENABLED", "1")
            ).strip().lower() in {"1", "true", "yes", "on"}
            self.closed_loop_review_supervisor = (
                ReviewSupervisor(
                    queue=self.closed_loop_review_queue,
                    refresh_fn=self._fixed_refresh_group,
                    max_workers=os.getenv("DEX_CLOSED_LOOP_REVIEW_WORKERS", "16"),
                )
                if self._supervisor_enabled and not self._native_rust_closed_loop
                else None
            )
            # Native broadcast lifecycle belongs to the Rust Broadcast Manager.
            # The Python supervisor is retained only for direct tests and the
            # legacy non-native fallback.
            self.closed_loop_broadcast_supervisor = None
            self._background_enabled = str(
                os.getenv("DEX_OPPORTUNITY_SCANNER_BACKGROUND_ENABLED", "1")
            ).strip().lower() in {"1", "true", "yes", "on"}
            self._load_cache()
