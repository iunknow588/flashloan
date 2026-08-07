from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from web.parameter_config import load_page_state_parameter_map, save_page_state_parameter_map


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageState":
        return cls(
            page=str(data.get("page") or ""),
            status=str(data.get("status") or ""),
            result=data.get("result"),
            message=data.get("message"),
            updated_at=str(data.get("updated_at") or utc_iso_now()),
            source_event_id=data.get("source_event_id"),
            last_error=data.get("last_error"),
            context=dict(data.get("context") or {}),
        )

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


class _StateCache(dict[str, PageState]):
    def __init__(self, owner: "PageStateStore") -> None:
        super().__init__()
        self._owner = owner

    def clear(self) -> None:  # type: ignore[override]
        super().clear()
        self._owner._memory_cleared = True


class PageStateStore:
    def __init__(self) -> None:
        self._memory_cleared = False
        self._states: _StateCache = _StateCache(self)

    def _database_url_or_none(self) -> str | None:
        import os

        return os.getenv("DATABASE_URL", "").strip() or None

    def _database_sync_enabled(self) -> bool:
        import os

        return os.getenv("PAGE_STATE_DATABASE_SYNC", "true").strip().lower() not in {"0", "false", "no", "off"}

    def _load_from_database(self, page: str) -> PageState | None:
        database_url = self._database_url_or_none()
        if not database_url or not self._database_sync_enabled() or self._memory_cleared:
            return None
        if database_unavailable_reason(database_url):
            return None
        try:
            values = load_page_state_parameter_map(database_url)
            raw = values.get(page)
            if isinstance(raw, dict):
                return PageState.from_dict(raw)
        except Exception as exc:
            if database_url and is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
        return None

    def _save_to_database(self, state: PageState) -> None:
        database_url = self._database_url_or_none()
        if not database_url or not self._database_sync_enabled() or database_unavailable_reason(database_url):
            return
        try:
            values = load_page_state_parameter_map(database_url)
            values[state.page] = state.to_dict()
            save_page_state_parameter_map(database_url, values)
        except Exception as exc:
            if database_url and is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)

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
        self._memory_cleared = False
        self._save_to_database(state)
        return state

    def get(self, page: str, default_status: str) -> PageState:
        state = self._states.get(page)
        if state is not None:
            return state
        loaded = self._load_from_database(page)
        if loaded is not None:
            self._states[page] = loaded
            return loaded
        return PageState(page=page, status=default_status)


PAGE_STATE_STORE = PageStateStore()

