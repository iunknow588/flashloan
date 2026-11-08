"""BNB shared market database management.

BNB markets are designed as a shared quote source consumed by every chain.
Instead of copying BNB tables into each chain SQLite file, BNB gets one
dedicated ``local_store_bnb.sqlite3`` and the Rust BNB bridge defaults to it.
An independent collector thread keeps the BNB snapshot fresh.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.db.database_path import default_database_path


BNB_CHAIN_ID = 56
BNB_NETWORK = "bnb"


def bnb_shared_database_enabled() -> bool:
    raw = os.getenv("FLASHLOAN_BNB_SHARED_DATABASE", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def bnb_shared_database_path() -> Path:
    """Return the dedicated BNB SQLite path inside the SQLite database root."""

    configured = os.getenv("FLASHLOAN_BNB_DATABASE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    base_dir = default_database_path().resolve().parent
    kv_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if kv_path:
        base_dir = Path(kv_path).expanduser().resolve().parent
    return base_dir / "local_store_bnb.sqlite3"


def ensure_bnb_shared_schema(database_url: str | None = None) -> dict[str, Any]:
    """Create the BNB shared store with the same schema migrations as chain stores."""

    from core.db.storage_schema import ensure_database_schema

    path = bnb_shared_database_path() if database_url is None else Path(database_url)
    ensure_database_schema(str(path))
    return {"ok": True, "database": str(path.resolve())}


@dataclass
class BnbSharedCollectorState:
    enabled: bool = False
    running: bool = False
    interval_seconds: float = 60.0
    last_started_at: str | None = None
    last_completed_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_result: dict[str, Any] | None = None
    total_rounds: int = 0


@dataclass
class BnbSharedCollector:
    state: BnbSharedCollectorState = field(default_factory=BnbSharedCollectorState)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        with self._lock:
            self.state.enabled = True
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="bnb-shared-collector",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = None
        with self._lock:
            if self._thread is not None:
                thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.state.enabled,
                "running": self.state.running,
                "intervalSeconds": self.state.interval_seconds,
                "lastStartedAt": self.state.last_started_at,
                "lastCompletedAt": self.state.last_completed_at,
                "lastStatus": self.state.last_status,
                "lastError": self.state.last_error,
                "lastResult": self.state.last_result,
                "totalRounds": self.state.total_rounds,
                "database": str(bnb_shared_database_path().resolve()),
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            interval = max(60.0, float(os.getenv("FLASHLOAN_BNB_COLLECTOR_INTERVAL_SECONDS", "60").strip() or 60))
            deadline = time.monotonic() + interval
            try:
                self._collect_once()
            except Exception as exc:
                with self._lock:
                    self.state.last_error = f"{type(exc).__name__}: {exc}"
                    self.state.last_status = "error"
            wait_seconds = deadline - time.monotonic()
            if wait_seconds > 0:
                self._stop.wait(wait_seconds)

    def _collect_once(self) -> None:
        from core.rs_bridge import collect_bnb_market_snapshot

        now = _utc_now()
        with self._lock:
            self.state.running = True
            self.state.last_started_at = now
            self.state.last_error = None
            self.state.total_rounds += 1
        try:
            result = collect_bnb_market_snapshot()
            with self._lock:
                self.state.last_result = result if isinstance(result, dict) else {"ok": False}
                self.state.last_status = "ok"
                self.state.last_completed_at = _utc_now()
        finally:
            with self._lock:
                self.state.running = False


_COLLECTOR: BnbSharedCollector | None = None
_COLLECTOR_LOCK = threading.Lock()


def ensure_bnb_shared_collector_running() -> BnbSharedCollector:
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        if not bnb_shared_database_enabled():
            if _COLLECTOR is not None:
                _COLLECTOR.stop()
                _COLLECTOR.state.enabled = False
                return _COLLECTOR
            return BnbSharedCollector()
        if _COLLECTOR is None:
            ensure_bnb_shared_schema()
            _COLLECTOR = BnbSharedCollector()
        _COLLECTOR.start()
        return _COLLECTOR


def bnb_shared_collector_status() -> dict[str, Any]:
    with _COLLECTOR_LOCK:
        collector = _COLLECTOR
    if collector is None:
        return {"enabled": False, "running": False, "database": str(bnb_shared_database_path().resolve())}
    return collector.status()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "BNB_CHAIN_ID",
    "BNB_NETWORK",
    "bnb_shared_collector_status",
    "bnb_shared_database_enabled",
    "bnb_shared_database_path",
    "ensure_bnb_shared_collector_running",
    "ensure_bnb_shared_schema",
]
