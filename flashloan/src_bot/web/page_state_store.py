from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PageState:
    page: str
    status: str
    result: str | None = None
    message: str | None = None
    updated_at: str = field(default_factory=utc_iso_now)
    source_event_id: str | None = None
    last_error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "status": self.status,
            "result": self.result,
            "message": self.message,
            "updated_at": self.updated_at,
            "source_event_id": self.source_event_id,
            "last_error": self.last_error,
            "context": dict(self.context),
        }


class PageStateStore:
    def __init__(self) -> None:
        self._states: dict[str, PageState] = {}

    def set(
        self,
        page: str,
        status: str,
        *,
        result: str | None = None,
        message: str | None = None,
        source_event_id: str | None = None,
        last_error: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> PageState:
        state = PageState(
            page=page,
            status=status,
            result=result,
            message=message,
            source_event_id=source_event_id,
            last_error=last_error,
            context=dict(context or {}),
        )
        self._states[page] = state
        return state

    def get(self, page: str, default_status: str) -> PageState:
        return self._states.get(page) or PageState(page=page, status=default_status)


PAGE_STATE_STORE = PageStateStore()

