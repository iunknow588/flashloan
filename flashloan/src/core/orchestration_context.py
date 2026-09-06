"""Pure orchestration state machine shared by workflow adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class OrchestrationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass
class OrchestrationContext:
    workflow_id: str
    deadline: datetime
    max_retries: int = 0
    retry_count: int = 0
    cancelled: bool = False
    status: OrchestrationStatus = OrchestrationStatus.PENDING

    def _now(self, now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

    def start(self, *, now: datetime | None = None) -> OrchestrationStatus:
        if self.cancelled:
            self.status = OrchestrationStatus.CANCELLED
        elif self._now(now) >= self.deadline:
            self.status = OrchestrationStatus.TIMED_OUT
        else:
            self.status = OrchestrationStatus.RUNNING
        return self.status

    def cancel(self) -> OrchestrationStatus:
        self.cancelled = True
        self.status = OrchestrationStatus.CANCELLED
        return self.status

    def retry(self, *, now: datetime | None = None) -> OrchestrationStatus:
        if self.cancelled:
            return self.cancel()
        if self._now(now) >= self.deadline:
            self.status = OrchestrationStatus.TIMED_OUT
        elif self.retry_count >= self.max_retries:
            self.status = OrchestrationStatus.FAILED
        else:
            self.retry_count += 1
            self.status = OrchestrationStatus.RETRYING
        return self.status

    def succeed(self) -> OrchestrationStatus:
        if self.status not in {OrchestrationStatus.CANCELLED, OrchestrationStatus.TIMED_OUT}:
            self.status = OrchestrationStatus.SUCCEEDED
        return self.status


__all__ = ["OrchestrationContext", "OrchestrationStatus"]
