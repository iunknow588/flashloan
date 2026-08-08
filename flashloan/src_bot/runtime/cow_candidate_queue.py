from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import copy
import json
import threading
import time
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_key(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def candidate_signature(attempt: dict[str, Any]) -> str:
    return "|".join(
        [
            str(attempt.get("network") or "").strip().lower(),
            str(attempt.get("pair") or "").strip().upper(),
            str(attempt.get("pair_rank") or ""),
            str(attempt.get("priority_reason") or ""),
            _json_key(attempt.get("route_path") or []),
        ]
    )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _profit_hint(attempt: dict[str, Any]) -> Decimal:
    precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    for value in (
        precheck.get("pure_profit_amount"),
        precheck.get("final_delta_amount"),
        quote.get("final_delta_amount"),
        attempt.get("final_delta_amount"),
        quote.get("edge_hint_percent"),
        precheck.get("edge_hint_percent"),
        quote.get("window_spread_percent"),
        precheck.get("window_spread_percent"),
    ):
        parsed = _decimal_or_none(value)
        if parsed is not None:
            return parsed
    return Decimal("0")


@dataclass
class CowQueuedCandidate:
    signature: str
    attempt: dict[str, Any]
    sequence: int
    status: str = "pending"
    enqueued_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_error: str | None = None
    result: dict[str, Any] | None = None
    retry_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.attempt)
        return {
            "signature": self.signature,
            "status": self.status,
            "sequence": self.sequence,
            "enqueued_at": datetime.fromtimestamp(self.enqueued_at, tz=timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(self.updated_at, tz=timezone.utc).isoformat(),
            "attempts": self.attempts,
            "last_error": self.last_error,
            "retry_at": datetime.fromtimestamp(self.retry_at, tz=timezone.utc).isoformat() if self.retry_at else None,
            "network": payload.get("network"),
            "pair": payload.get("pair"),
            "pair_rank": payload.get("pair_rank"),
            "priority_reason": payload.get("priority_reason"),
            "route_path": payload.get("route_path") or [],
            "profit_hint": str(_profit_hint(payload)),
            "priority_score": str(_profit_hint(payload)),
            "candidate": payload,
            "result": copy.deepcopy(self.result) if isinstance(self.result, dict) else self.result,
        }


class CowCandidateQueue:
    def __init__(
        self,
        *,
        max_size: int = 500,
        ttl_seconds: float = 7 * 24 * 60 * 60,
        requote_cooldown_seconds: float = 30.0,
    ) -> None:
        self.max_size = max(1, int(max_size))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.requote_cooldown_seconds = max(0.0, float(requote_cooldown_seconds))
        self._lock = threading.Lock()
        self._items: dict[str, CowQueuedCandidate] = {}
        self._sequence = 0

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._sequence = 0

    def enqueue_many(self, attempts: list[dict[str, Any]], *, source: str = "") -> dict[str, Any]:
        now = time.time()
        added = 0
        updated = 0
        skipped = 0
        cooldown = 0
        with self._lock:
            self._prune_locked(now)
            for attempt in attempts or []:
                if not isinstance(attempt, dict):
                    skipped += 1
                    continue
                if str(attempt.get("execution_phase") or "") != "market_candidate":
                    skipped += 1
                    continue
                signature = candidate_signature(attempt)
                if not signature.strip("|"):
                    skipped += 1
                    continue
                payload = copy.deepcopy(attempt)
                payload["queue_source"] = source or payload.get("queue_source") or "market_candidate"
                existing = self._items.get(signature)
                if existing is not None:
                    if (
                        existing.status in {"quoted", "blocked", "failed"}
                        and now - existing.updated_at < self.requote_cooldown_seconds
                    ):
                        cooldown += 1
                        continue
                    existing.attempt = payload
                    existing.updated_at = now
                    existing.last_error = None
                    existing.result = None
                    existing.retry_at = None
                    if existing.status != "processing":
                        existing.status = "pending"
                    updated += 1
                    continue
                self._sequence += 1
                self._items[signature] = CowQueuedCandidate(
                    signature=signature,
                    attempt=payload,
                    sequence=self._sequence,
                    enqueued_at=now,
                    updated_at=now,
                )
                added += 1
                self._trim_locked()
        return {"added": added, "updated": updated, "skipped": skipped, "cooldown": cooldown, "size": self.size()}

    def claim_next(self, *, sort_key: str = "fifo") -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            for item in self._items.values():
                if item.status == "retry_wait" and item.retry_at is not None and item.retry_at <= now:
                    item.status = "pending"
                    item.retry_at = None
            pending = [item for item in self._items.values() if item.status == "pending"]
            if not pending:
                return None
            if sort_key in {"profit", "profit_desc", "expected_profit"}:
                selected = sorted(pending, key=lambda item: (-_profit_hint(item.attempt), item.sequence))[0]
            else:
                selected = sorted(pending, key=lambda item: item.sequence)[0]
            selected.status = "processing"
            selected.updated_at = now
            selected.attempts += 1
            return selected.to_dict()

    def complete(self, signature: str, *, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._lock:
            item = self._items.get(signature)
            if item is None:
                return
            item.status = status
            item.updated_at = time.time()
            item.result = copy.deepcopy(result) if isinstance(result, dict) else result
            item.last_error = error
            item.retry_at = None

    def requeue(self, signature: str, *, error: str | None = None, delay_seconds: float = 0.0) -> None:
        with self._lock:
            item = self._items.get(signature)
            if item is None:
                return
            now = time.time()
            item.status = "retry_wait" if delay_seconds > 0 else "pending"
            item.updated_at = now
            item.last_error = error
            item.retry_at = now + max(0.0, float(delay_seconds)) if delay_seconds > 0 else None

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def retain_networks(self, networks: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
        selected = {
            str(network or "").strip().lower()
            for network in networks or []
            if str(network or "").strip()
        }
        if not selected:
            return {"removed": 0, "networks": []}
        with self._lock:
            removable = [
                signature
                for signature, item in self._items.items()
                if item.status != "processing"
                and str(item.attempt.get("network") or "").strip().lower() not in selected
            ]
            for signature in removable:
                self._items.pop(signature, None)
        return {"removed": len(removable), "networks": sorted(selected), "size": self.size()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            counts: dict[str, int] = {}
            for item in self._items.values():
                counts[item.status] = counts.get(item.status, 0) + 1
            return {
                "size": len(self._items),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "counts": counts,
                "pending": counts.get("pending", 0),
                "processing": counts.get("processing", 0),
                "quoted": counts.get("quoted", 0),
                "blocked": counts.get("blocked", 0),
                "ready_not_submitted": counts.get("ready_not_submitted", 0),
                "ready_to_submit": counts.get("ready_to_submit", 0),
                "submitted_success": counts.get("submitted_success", 0),
                "submission_failed": counts.get("submission_failed", 0),
                "failed": counts.get("failed", 0) + counts.get("submission_failed", 0),
                "retry_wait": counts.get("retry_wait", 0),
                "next_retry_at": self._next_retry_at_locked(),
                "oldest_pending_at": self._oldest_pending_at_locked(),
            }

    def snapshot(self, *, limit: int = 50, networks: list[str] | tuple[str, ...] | set[str] | None = None) -> list[dict[str, Any]]:
        selected = {
            str(network or "").strip().lower()
            for network in networks or []
            if str(network or "").strip()
        }
        with self._lock:
            rows = [
                item
                for item in self._items.values()
                if not selected or str(item.attempt.get("network") or "").strip().lower() in selected
            ]
            rows = sorted(rows, key=lambda item: item.updated_at, reverse=True)
            return [item.to_dict() for item in rows[: max(1, int(limit))]]

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        expired = [
            signature
            for signature, item in self._items.items()
            if item.updated_at < cutoff and item.status != "processing"
        ]
        for signature in expired:
            self._items.pop(signature, None)

    def _trim_locked(self) -> None:
        overflow = len(self._items) - self.max_size
        if overflow <= 0:
            return
        removable = sorted(
            [item for item in self._items.values() if item.status != "processing"],
            key=lambda item: (item.status == "pending", item.updated_at),
        )
        for item in removable[:overflow]:
            self._items.pop(item.signature, None)

    def _next_retry_at_locked(self) -> str | None:
        retry_times = [
            item.retry_at
            for item in self._items.values()
            if item.status == "retry_wait" and item.retry_at is not None
        ]
        if not retry_times:
            return None
        return datetime.fromtimestamp(min(retry_times), tz=timezone.utc).isoformat()

    def _oldest_pending_at_locked(self) -> str | None:
        pending_times = [
            item.enqueued_at
            for item in self._items.values()
            if item.status == "pending"
        ]
        if not pending_times:
            return None
        return datetime.fromtimestamp(min(pending_times), tz=timezone.utc).isoformat()
