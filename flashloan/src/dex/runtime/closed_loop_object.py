"""Object-oriented surface for SQLite-backed closed-loop state.

SQLite remains the only durable source of truth.  ``ClosedLoop`` is the
in-process domain object for a single loop and owns its status enum plus
transition guards.  ``ClosedLoopStateMachine`` is the aggregate entry point
for persistence, CAS claims, lease recovery and summaries.  Module-level
helpers in ``closed_loop_state_machine`` stay as compatibility glue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import logging
import os
import threading
import time
from typing import Any

from core.db.storage_business_io import query, query_one
from core.db.storage_common import database_url as storage_database_url
from dex.runtime import closed_loop_state_machine as _sm


_LOGGER = logging.getLogger(__name__)


def _resolve_database(database_url: str | None = None) -> str | None:
    value = str(database_url or "").strip()
    if value:
        return value
    try:
        return str(storage_database_url() or "").strip() or None
    except Exception:
        return None


class LoopState(str, enum.Enum):
    """State enum compatible with existing SQLite string values."""

    OFFCHAIN_PENDING = _sm.STATE_OFFCHAIN_PENDING
    OFFCHAIN_ESTIMATE = _sm.STATE_OFFCHAIN_ESTIMATE
    PREFLIGHT_RUNNING = _sm.STATE_PREFLIGHT_RUNNING
    PREFLIGHT_DONE = _sm.STATE_PREFLIGHT_DONE
    PREFLIGHT_FAILED = _sm.STATE_PREFLIGHT_FAILED
    EXECUTION_NOT_REQUESTED = _sm.STATE_EXECUTION_NOT_REQUESTED
    EXECUTION_PENDING = _sm.STATE_EXECUTION_PENDING
    EXECUTION_SUBMITTED = _sm.STATE_EXECUTION_SUBMITTED
    EXECUTION_SUCCESS = _sm.STATE_EXECUTION_SUCCESS
    EXECUTION_FAILED = _sm.STATE_EXECUTION_FAILED

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def from_value(cls, value: str | "LoopState" | None) -> "LoopState | None":
        if isinstance(value, LoopState):
            return value
        for member in cls:
            if str(member.value) == str(value or "").strip():
                return member
        return None

    @property
    def terminal_cycle(self) -> bool:
        return str(self.value) in _sm.CYCLE_TERMINAL_STATES

    @property
    def terminal_execution(self) -> bool:
        return str(self.value) in _sm.TERMINAL_EXECUTION_STATES


class TransitionError(ValueError):
    """Raised when a loop object attempts an illegal state transition."""


@dataclass(frozen=True)
class ClosedLoopIdentity:
    """Unique identity of one closed-loop row."""

    network: str
    chain_id: int
    protocol: str
    market_id: str
    loop_key: str

    def params(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "chain_id": self.chain_id,
            "protocol": self.protocol,
            "market_id": self.market_id,
            "loop_key": self.loop_key,
        }

    def __str__(self) -> str:
        return (
            f"{self.network}:{self.chain_id}:{self.protocol}:"
            f"{self.market_id}:{self.loop_key}"
        )


@dataclass
class ClosedLoop:
    """In-process object for one SQLite closed-loop row."""

    database_url: str
    identity: ClosedLoopIdentity
    row: dict[str, Any]
    _state: LoopState = field(init=False, repr=False)

    def __post_init__(self) -> None:
        state = LoopState.from_value(self.row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {self.row.get('state')!r}")
        self._state = state

    @property
    def state(self) -> LoopState:
        return self._state

    def refresh(self) -> "ClosedLoop":
        """Reload this loop from SQLite and replace the in-memory snapshot."""
        row = _sm.load_loop_state(self.database_url, **self.identity.params())
        if row is None:
            raise LookupError(f"closed-loop row not found: {self.identity}")
        self.row = row
        restored = LoopState.from_value(row.get("state"))
        if restored is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = restored
        return self

    def to_dict(self) -> dict[str, Any]:
        return dict(self.row)

    def transition(
        self,
        to_state: str | LoopState,
        *,
        expected_generation: int | None = None,
        owner_thread: str | None = None,
        tx_hash: str | None = None,
        last_error: str | None = None,
    ) -> "ClosedLoop | None":
        target = LoopState.from_value(to_state)
        if target is None:
            raise ValueError(f"unknown closed-loop state: {to_state!r}")
        if not _sm.can_transition(str(self._state.value), str(target.value)):
            raise TransitionError(
                f"{self._state.value} -> {target.value} is not a legal transition"
            )
        row = _sm.transition_loop_state(
            self.database_url,
            **self.identity.params(),
            from_state=str(self._state.value),
            to_state=str(target.value),
            expected_generation=expected_generation,
            owner_thread=owner_thread,
            tx_hash=tx_hash,
            last_error=last_error,
        )
        if row is None:
            return None
        self.row = row
        self._state = target
        return self

    def claim(
        self,
        *,
        operation: str,
        owner_thread: str,
        expected_generation: int | None = None,
    ) -> "ClosedLoop | None":
        row = _sm.claim_loop(
            self.database_url,
            **self.identity.params(),
            operation=operation,
            owner_thread=owner_thread,
            expected_generation=expected_generation,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self

    def complete(
        self,
        *,
        operation: str,
        outcome: str,
        owner_thread: str,
        tx_hash: str | None = None,
        error: str | None = None,
    ) -> "ClosedLoop | None":
        row = _sm.complete_loop(
            self.database_url,
            **self.identity.params(),
            operation=operation,
            outcome=outcome,
            owner_thread=owner_thread,
            tx_hash=tx_hash,
            error=error,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self

    def release(
        self,
        *,
        operation: str,
        owner_thread: str,
    ) -> "ClosedLoop | None":
        row = _sm.release_loop(
            self.database_url,
            **self.identity.params(),
            operation=operation,
            owner_thread=owner_thread,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self

    def heartbeat(
        self,
        *,
        operation: str,
        owner_thread: str,
        expected_cycle_id: str | None = None,
    ) -> "ClosedLoop | None":
        row = _sm.heartbeat_loop(
            self.database_url,
            **self.identity.params(),
            operation=operation,
            owner_thread=owner_thread,
            expected_cycle_id=expected_cycle_id,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self

    def enqueue_execution(
        self,
        *,
        expected_generation: int | None = None,
    ) -> "ClosedLoop | None":
        row = _sm.enqueue_execution(
            self.database_url,
            **self.identity.params(),
            expected_generation=expected_generation,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self

    def mark_execution_not_requested(
        self,
        *,
        expected_generation: int | None = None,
        reason: str = "execution_not_requested",
    ) -> "ClosedLoop | None":
        row = _sm.mark_execution_not_requested(
            self.database_url,
            **self.identity.params(),
            expected_generation=expected_generation,
            reason=reason,
        )
        if row is None:
            return None
        self.row = row
        state = LoopState.from_value(row.get("state"))
        if state is None:
            raise ValueError(f"unknown closed-loop state: {row.get('state')!r}")
        self._state = state
        return self


_EXECUTION_SUCCESS_STATES = frozenset(
    {
        "executed",
        "execution_success",
        "confirmed_success",
        "settled",
        "settlement_success",
        "success",
    }
)

_EXECUTION_FAILURE_STATES = frozenset(
    {
        "execution_failed",
        "settlement_failed",
        "confirmation_failed",
        "confirmed_failed",
        "failed_after_broadcast",
        "broadcast_confirmed_failed",
    }
)

_SUBMISSION_FAILURE_STATES = frozenset(
    {
        "submission_failed",
        "submit_failed",
        "order_failed",
        "broadcast_failed",
        "broadcast_error",
        "broadcast_rejected",
    }
)


def _attempt_outcome(row: dict[str, Any]) -> str:
    """Classify one execution attempt row into a per-loop counter bucket."""
    category = str(
        row.get("review_category")
        or row.get("category")
        or ""
    ).strip().lower()
    state = str(
        row.get("state")
        or row.get("status")
        or row.get("submission_status")
        or ""
    ).strip().lower()
    auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
    auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
    tx_hash = str(
        row.get("tx_hash")
        or row.get("transaction_hash")
        or auto_result.get("txHash")
        or auto_result.get("transactionHash")
        or auto_result.get("transaction_hash")
        or (row.get("quote") or {}).get("tx_hash")
        or (row.get("quote") or {}).get("transaction_hash")
        or ""
    ).strip()
    confirmation = str(
        auto_execute.get("confirmationStatus")
        or auto_result.get("confirmationStatus")
        or row.get("confirmationStatus")
        or ""
    ).strip().lower()
    receipt = str(
        auto_result.get("receiptStatus")
        or row.get("receiptStatus")
        or (row.get("quote") or {}).get("receiptStatus")
        or ""
    ).strip().lower()
    confirmed = confirmation in {"confirmed", "success"} or receipt in {
        "0x1",
        "1",
        "success",
    }
    if category == "execution_success":
        if confirmed or state in _EXECUTION_SUCCESS_STATES:
            return "execution_success"
        return "submission_success" if tx_hash else "submission_success"
    if category == "execution_failed":
        if confirmed is False and receipt in {"0x0", "0", "failed", "reverted"}:
            return "execution_failed"
        if state in _EXECUTION_FAILURE_STATES:
            return "execution_failed"
        if state in _SUBMISSION_FAILURE_STATES:
            return "submission_failed"
        if not tx_hash:
            return "submission_failed"
        return "submission_failed"
    return "submission_failed"


@dataclass
class ClosedLoopStats:
    """In-memory per-loop counters and latest outcome, owned by the runtime."""

    loop_key: str
    state: str = "unknown"
    generation: int = 1
    last_error: str | None = None
    review_outcome: str | None = None
    submission_success_count: int = 0
    submission_failed_count: int = 0
    execution_success_count: int = 0
    execution_failed_count: int = 0
    seeded: bool = False

    def record_review_outcome(self, outcome: str | None) -> None:
        if outcome is None:
            return
        self.review_outcome = str(outcome).strip().lower()

    def record_attempt_outcome(self, outcome: str) -> None:
        if outcome == "submission_success":
            self.submission_success_count += 1
        elif outcome == "submission_failed":
            self.submission_failed_count += 1
        elif outcome == "execution_success":
            self.execution_success_count += 1
        elif outcome == "execution_failed":
            self.execution_failed_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "loopKey": self.loop_key,
            "state": self.state,
            "generation": self.generation,
            "lastError": self.last_error,
            "reviewOutcome": self.review_outcome,
            "submissionSuccessCount": self.submission_success_count,
            "submissionFailedCount": self.submission_failed_count,
            "executionSuccessCount": self.execution_success_count,
            "executionFailedCount": self.execution_failed_count,
        }


def _metrics_cache_key(database_url: str | None) -> str | None:
    if not database_url:
        return None
    return os.path.normcase(os.path.abspath(str(database_url)))


class ClosedLoopRuntimeMetrics:
    """Process-global counters maintained by worker completions.

    UI pages read this object directly; they never re-count SQLite rows.
    Counters are seeded once from the durable store on first access, then only
    incremented after worker threads finish and update loop state.
    """

    _CACHE: dict[str, "ClosedLoopRuntimeMetrics"] = {}
    _CACHE_LOCK = threading.RLock()

    def __init__(self, database_url: str) -> None:
        self.database_url = str(database_url)
        self._lock = threading.RLock()
        self._initialized = False
        self.initialization_seconds = 0.0
        self._loops: dict[str, ClosedLoopStats] = {}

    @classmethod
    def cached_for(cls, database_url: str | None) -> "ClosedLoopRuntimeMetrics | None":
        key = _metrics_cache_key(database_url)
        if not key:
            return None
        with cls._CACHE_LOCK:
            metrics = cls._CACHE.get(key)
            if metrics is None:
                metrics = cls(key)
                cls._CACHE[key] = metrics
            return metrics

    @classmethod
    def clear_cache(cls) -> None:
        with cls._CACHE_LOCK:
            cls._CACHE.clear()

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            started_at = time.perf_counter()
            self._load_state_rows()
            self._load_review_outcomes()
            self._load_attempt_counters()
            self._initialized = True
            self.initialization_seconds = max(0.0, time.perf_counter() - started_at)
            _LOGGER.info(
                "closed-loop metrics initialized database=%s loops=%d seconds=%.6f",
                self.database_url,
                len(self._loops),
                self.initialization_seconds,
            )

    def _load_state_rows(self) -> None:
        from dex.runtime.closed_loop_state_machine import V4_CLOSED_LOOP_RUNTIME_STATE_TABLE

        _sm.ensure_runtime_state_table(self.database_url)
        rows = query(
            self.database_url,
            f"""
            SELECT network, chain_id, protocol, market_id, loop_key, state,
                   generation, last_error
            FROM {V4_CLOSED_LOOP_RUNTIME_STATE_TABLE}
            """,
        )
        for raw in rows:
            row = dict(raw)
            key = str(row.get("loop_key") or "").strip().lower()
            if not key:
                continue
            stats = self._loops.setdefault(key, ClosedLoopStats(loop_key=key))
            stats.state = str(row.get("state") or "unknown")
            stats.generation = int(row.get("generation") or 1)
            stats.last_error = row.get("last_error")

    def _load_review_outcomes(self) -> None:
        from core.db.storage_cow_execution import load_latest_cow_execution_rows_by_pair
        from dex.runtime.closed_loop_review_classifier import (
            loop_key_from_review_row,
            review_row_kind,
        )

        rows = load_latest_cow_execution_rows_by_pair(
            self.database_url,
            categories=["not_executable"],
        )
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = loop_key_from_review_row(row)
            if not key or key not in self._loops:
                continue
            current = latest.get(key)
            if current is None or (
                str(row.get("created_at") or ""),
                int(row.get("id") or 0),
            ) > (
                str(current.get("created_at") or ""),
                int(current.get("id") or 0),
            ):
                latest[key] = row
        for key, row in latest.items():
            kind = review_row_kind(row)
            if kind == "review_failed":
                outcome = "failed"
            elif kind == "rpc_busy_deferred":
                outcome = "deferred"
            else:
                outcome = "success"
            self._loops[key].record_review_outcome(outcome)

    def _load_attempt_counters(self) -> None:
        from core.db.storage_cow_execution import (
            load_recent_cow_execution_attempts_sqlite_grouped,
        )
        from dex.runtime.closed_loop_review_classifier import loop_key_from_review_row

        grouped = load_recent_cow_execution_attempts_sqlite_grouped(
            self.database_url,
            limit=100000,
            categories=["execution_success", "execution_failed"],
        )
        for rows in grouped.values():
            for row in rows:
                key = loop_key_from_review_row(row)
                if not key or key not in self._loops:
                    continue
                self._loops[key].record_attempt_outcome(_attempt_outcome(row))
        for stats in self._loops.values():
            stats.seeded = True

    def _seed_loop_history(self, loop_key: str) -> None:
        """Backfill one newly observed loop from durable history once.

        Loop state rows can be created after the metrics object was already
        initialized, so a late loop must seed its review outcome and attempt
        counters without re-running the page-time SQL aggregation.  A stats
        reset clears counters but keeps ``seeded`` so reset does not resurrect
        historical counters on the next state write.
        """
        stats = self._loops.get(loop_key)
        if stats is None or stats.seeded:
            return
        try:
            from core.db.storage_cow_execution import (
                load_latest_cow_execution_rows_by_pair,
                load_recent_cow_execution_attempts_sqlite_grouped,
            )
            from dex.runtime.closed_loop_review_classifier import (
                loop_key_from_review_row,
                review_row_kind,
            )

            latest = load_latest_cow_execution_rows_by_pair(
                self.database_url,
                categories=["not_executable"],
            )
            for row in latest:
                if loop_key_from_review_row(row) != loop_key:
                    continue
                kind = review_row_kind(row)
                if kind == "review_failed":
                    outcome = "failed"
                elif kind == "rpc_busy_deferred":
                    outcome = "deferred"
                else:
                    outcome = "success"
                stats.record_review_outcome(outcome)
            grouped = load_recent_cow_execution_attempts_sqlite_grouped(
                self.database_url,
                limit=100000,
                categories=["execution_success", "execution_failed"],
            )
            for rows in grouped.values():
                for row in rows:
                    if loop_key_from_review_row(row) != loop_key:
                        continue
                    stats.record_attempt_outcome(_attempt_outcome(row))
        except Exception:
            return
        stats.seeded = True

    def loop_stats(self, loop_key: str | None) -> dict[str, Any] | None:
        if not loop_key:
            return None
        key = str(loop_key).strip().lower()
        self.ensure_initialized()
        with self._lock:
            stats = self._loops.get(key)
            return stats.to_dict() if stats is not None else None

    def record_state_change(
        self,
        *,
        loop_key: str,
        old_state: str | None,
        new_state: str | None,
        last_error: str | None = None,
    ) -> None:
        if not loop_key or not new_state:
            return
        self.ensure_initialized()
        key = str(loop_key).strip().lower()
        with self._lock:
            stats = self._loops.setdefault(key, ClosedLoopStats(loop_key=key))
            stats.state = str(new_state)
            if last_error is not None:
                stats.last_error = last_error
            elif str(new_state) != _sm.STATE_EXECUTION_FAILED:
                stats.last_error = None
            if not stats.seeded:
                self._seed_loop_history(key)

    def record_review_outcome(self, loop_key: str, outcome: str | None) -> None:
        if not loop_key or not outcome:
            return
        key = str(loop_key).strip().lower()
        with self._lock:
            stats = self._loops.get(key)
            if stats is not None:
                stats.record_review_outcome(outcome)

    def record_attempt(self, loop_key: str | None, row: dict[str, Any]) -> None:
        if not loop_key:
            return
        key = str(loop_key).strip().lower()
        with self._lock:
            stats = self._loops.get(key)
            if stats is not None:
                stats.record_attempt_outcome(_attempt_outcome(row))

    def reset_counters(self) -> None:
        """Clear per-loop execution/submission counters without losing state.

        The UI clear-stats action intentionally shows every loop with zero
        counters; the loops keep their state and review outcome so a reset is
        not mistaken for a missing review or a broken state machine.  New
        worker completions continue to increment from zero.
        """
        self.ensure_initialized()
        with self._lock:
            for stats in self._loops.values():
                stats.submission_success_count = 0
                stats.submission_failed_count = 0
                stats.execution_success_count = 0
                stats.execution_failed_count = 0

    def summary(self, *, include_loops: bool = True) -> dict[str, Any]:
        self.ensure_initialized()
        states: dict[str, int] = {}
        review_success = 0
        review_failed = 0
        review_deferred = 0
        submission_success_total = 0
        submission_failed_total = 0
        execution_success_total = 0
        execution_failed_total = 0
        broadcast_loops = 0
        broadcast_success_loops = 0
        broadcast_failed_loops = 0
        with self._lock:
            for stats in self._loops.values():
                states[stats.state] = states.get(stats.state, 0) + 1
                if stats.review_outcome == "success":
                    review_success += 1
                elif stats.review_outcome == "failed":
                    review_failed += 1
                elif stats.review_outcome == "deferred":
                    review_deferred += 1
                outcome_counts = (
                    stats.submission_success_count,
                    stats.submission_failed_count,
                    stats.execution_success_count,
                    stats.execution_failed_count,
                )
                submission_success_total += stats.submission_success_count
                submission_failed_total += stats.submission_failed_count
                execution_success_total += stats.execution_success_count
                execution_failed_total += stats.execution_failed_count
                if any(outcome_counts):
                    broadcast_loops += 1
                if stats.submission_success_count or stats.execution_success_count:
                    broadcast_success_loops += 1
                if stats.submission_failed_count or stats.execution_failed_count:
                    broadcast_failed_loops += 1
            total = len(self._loops)
            missing_broadcast = sum(
                1
                for stats in self._loops.values()
                if stats.state == _sm.STATE_EXECUTION_FAILED
                and str(stats.last_error or "") == "broadcast_result_missing"
            )
            terminal_execution = states.get(_sm.STATE_EXECUTION_SUCCESS, 0) + states.get(
                _sm.STATE_EXECUTION_FAILED, 0
            )
            execution_not_requested = states.get(_sm.STATE_EXECUTION_NOT_REQUESTED, 0)
            loops_snapshot = (
                {key: stats.to_dict() for key, stats in self._loops.items()}
                if include_loops
                else {}
            )
        return {
            "available": True,
            "states": states,
            "total": total,
            "missingBroadcast": missing_broadcast,
            "terminalExecution": terminal_execution,
            "executionNotRequested": execution_not_requested,
            "cycleTerminals": terminal_execution + execution_not_requested,
            "reviewSuccessLoops": review_success,
            "reviewFailedLoops": review_failed,
            "reviewDeferredLoops": review_deferred,
            "reviewMissingLoops": max(
                0, total - review_success - review_failed - review_deferred
            ),
            "submissionSuccessTotal": submission_success_total,
            "submissionFailedTotal": submission_failed_total,
            "executionSuccessTotal": execution_success_total,
            "executionFailedTotal": execution_failed_total,
            "broadcastLoops": broadcast_loops,
            "broadcastSuccessLoops": broadcast_success_loops,
            "broadcastFailedLoops": broadcast_failed_loops,
            "loops": loops_snapshot,
        }


class ClosedLoopStateMachine:
    """Aggregate persistence entry for every closed-loop runtime state row."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = _resolve_database(database_url)

    @property
    def metrics(self) -> ClosedLoopRuntimeMetrics:
        database = self._require_database()
        metrics = ClosedLoopRuntimeMetrics.cached_for(database)
        if metrics is None:
            raise RuntimeError("closed-loop runtime metrics unavailable")
        return metrics

    def _require_database(self) -> str:
        if not self.database_url:
            raise RuntimeError("closed-loop runtime database unavailable")
        return self.database_url

    def ensure_table(self) -> None:
        _sm.ensure_runtime_state_table(self._require_database())

    def load(self, identity: ClosedLoopIdentity) -> ClosedLoop | None:
        row = _sm.load_loop_state(self._require_database(), **identity.params())
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def upsert(
        self,
        identity: ClosedLoopIdentity,
        *,
        state: str | LoopState,
        generation: int = 1,
        owner_thread: str | None = None,
        tx_hash: str | None = None,
        last_error: str | None = None,
        clear_cycle: bool = False,
    ) -> ClosedLoop:
        state_value = str(LoopState.from_value(state) or state).strip()
        row = _sm.upsert_loop_state(
            self._require_database(),
            **identity.params(),
            state=state_value,
            generation=generation,
            owner_thread=owner_thread,
            tx_hash=tx_hash,
            last_error=last_error,
            clear_cycle=clear_cycle,
        )
        return ClosedLoop(self._require_database(), identity, row)

    def claim(
        self,
        identity: ClosedLoopIdentity,
        *,
        operation: str,
        owner_thread: str,
        expected_generation: int | None = None,
    ) -> ClosedLoop | None:
        row = _sm.claim_loop(
            self._require_database(),
            **identity.params(),
            operation=operation,
            owner_thread=owner_thread,
            expected_generation=expected_generation,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def complete(
        self,
        identity: ClosedLoopIdentity,
        *,
        operation: str,
        outcome: str,
        owner_thread: str,
        tx_hash: str | None = None,
        error: str | None = None,
    ) -> ClosedLoop | None:
        row = _sm.complete_loop(
            self._require_database(),
            **identity.params(),
            operation=operation,
            outcome=outcome,
            owner_thread=owner_thread,
            tx_hash=tx_hash,
            error=error,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def release(
        self,
        identity: ClosedLoopIdentity,
        *,
        operation: str,
        owner_thread: str,
    ) -> ClosedLoop | None:
        row = _sm.release_loop(
            self._require_database(),
            **identity.params(),
            operation=operation,
            owner_thread=owner_thread,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def heartbeat(
        self,
        identity: ClosedLoopIdentity,
        *,
        operation: str,
        owner_thread: str,
        expected_cycle_id: str | None = None,
    ) -> ClosedLoop | None:
        row = _sm.heartbeat_loop(
            self._require_database(),
            **identity.params(),
            operation=operation,
            owner_thread=owner_thread,
            expected_cycle_id=expected_cycle_id,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def enqueue_execution(
        self,
        identity: ClosedLoopIdentity,
        *,
        expected_generation: int | None = None,
    ) -> ClosedLoop | None:
        row = _sm.enqueue_execution(
            self._require_database(),
            **identity.params(),
            expected_generation=expected_generation,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def reset(
        self,
        identity: ClosedLoopIdentity,
        *,
        generation: int,
    ) -> ClosedLoop:
        row = _sm.reset_loop_for_new_cycle(
            self._require_database(),
            **identity.params(),
            generation=generation,
        )
        return ClosedLoop(self._require_database(), identity, row)

    def transition(
        self,
        identity: ClosedLoopIdentity,
        *,
        from_state: str | LoopState,
        to_state: str | LoopState,
        expected_generation: int | None = None,
        owner_thread: str | None = None,
        tx_hash: str | None = None,
        last_error: str | None = None,
    ) -> ClosedLoop | None:
        row = _sm.transition_loop_state(
            self._require_database(),
            **identity.params(),
            from_state=str(LoopState.from_value(from_state) or from_state).strip(),
            to_state=str(LoopState.from_value(to_state) or to_state).strip(),
            expected_generation=expected_generation,
            owner_thread=owner_thread,
            tx_hash=tx_hash,
            last_error=last_error,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def mark_execution_not_requested(
        self,
        identity: ClosedLoopIdentity,
        *,
        expected_generation: int | None = None,
        reason: str = "execution_not_requested",
    ) -> ClosedLoop | None:
        row = _sm.mark_execution_not_requested(
            self._require_database(),
            **identity.params(),
            expected_generation=expected_generation,
            reason=reason,
        )
        if row is None:
            return None
        return ClosedLoop(self._require_database(), identity, row)

    def reclaim_expired_review_claims(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        max_age_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        return _sm.reclaim_expired_review_claims(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds,
        )

    def reclaim_expired_broadcast_claims(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        max_age_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        return _sm.reclaim_expired_broadcast_claims(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds,
        )

    def fail_stuck_execution_loops(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        loop_keys: list[str] | None = None,
        max_age_seconds: int | None = None,
        reason: str = "broadcast_result_missing",
    ) -> list[dict[str, Any]]:
        return _sm.fail_stuck_execution_loops(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_keys=loop_keys,
            max_age_seconds=max_age_seconds,
            reason=reason,
        )

    def close_stale_review_only_loops(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        loop_keys: list[str] | None = None,
        max_age_seconds: int | None = None,
        reason: str = "execution_not_requested",
    ) -> list[dict[str, Any]]:
        return _sm.close_stale_review_only_loops(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_keys=loop_keys,
            max_age_seconds=max_age_seconds,
            reason=reason,
        )

    def prune_legacy_unresolved_runtime_state(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        return _sm.prune_legacy_unresolved_runtime_state(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )

    def prune_test_residue_runtime_state(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        apply_cleanup: bool = False,
        approval_id: str | None = None,
        snapshot_sha256: str | None = None,
    ) -> dict[str, Any]:
        return _sm.prune_test_residue_runtime_state(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            apply_cleanup=apply_cleanup,
            approval_id=approval_id,
            snapshot_sha256=snapshot_sha256,
        )

    def canonicalize_runtime_state_market_ids(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        return _sm.canonicalize_runtime_state_market_ids(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )

    def backfill_runtime_state_for_closed_loops(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        return _sm.backfill_runtime_state_for_closed_loops(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )

    def prune_orphan_runtime_state(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        return _sm.prune_orphan_runtime_state(
            self._require_database(),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )

    def recover_stuck_execution_loops(
        self,
        *,
        network: str | None = None,
        chain_id: int | None = None,
        protocol: str | None = None,
        market_id: str | None = None,
        max_age_seconds: int | None = None,
    ) -> dict[str, Any]:
        unresolved_pruned = self.prune_legacy_unresolved_runtime_state(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )
        residual_pruned = self.prune_test_residue_runtime_state(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )
        orphan_pruned = self.prune_orphan_runtime_state(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
        )
        review_reclaimed = self.reclaim_expired_review_claims(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds if max_age_seconds is not None else 300,
        )
        broadcast_reclaimed = self.reclaim_expired_broadcast_claims(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds if max_age_seconds is not None else 300,
        )
        broadcast_missing = self.fail_stuck_execution_loops(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds,
        )
        review_only = self.close_stale_review_only_loops(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            max_age_seconds=max_age_seconds,
        )
        records = (
            list(review_reclaimed)
            + list(broadcast_reclaimed)
            + list(review_only)
            + list(broadcast_missing)
        )
        return {
            "failed": len(broadcast_missing),
            "reviewOnlyClosed": len(review_only),
            "recovered": len(records),
            "reviewReclaimed": len(review_reclaimed),
            "broadcastReclaimed": len(broadcast_reclaimed),
            "records": records,
            "error": None,
            "unresolvedPruned": int(unresolved_pruned.get("deleted") or 0),
            "legacyUnresolvedPrune": unresolved_pruned,
            "residualPruned": int(residual_pruned.get("residualPruned") or 0),
            "testResiduePrune": residual_pruned,
            "orphanPruned": int(orphan_pruned.get("deleted") or 0),
            "orphanRuntimeStatePrune": orphan_pruned,
        }

    def runtime_state_summary(self) -> dict[str, Any]:
        """Return the in-memory metrics maintained by worker completions."""
        return self.metrics.summary()


__all__ = [
    "ClosedLoop",
    "ClosedLoopIdentity",
    "ClosedLoopRuntimeMetrics",
    "ClosedLoopStats",
    "ClosedLoopStateMachine",
    "LoopState",
    "TransitionError",
]
