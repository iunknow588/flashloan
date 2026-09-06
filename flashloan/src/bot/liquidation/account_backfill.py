from __future__ import annotations

import threading
from typing import Callable, Optional


class AccountBackfillService:
    def __init__(self) -> None:
        self.cache: dict[str, object] = {
            "running": False,
            "stop_requested": False,
            "started_at": None,
            "finished_at": None,
            "stage": "idle",
            "last_result": None,
            "progress": {},
            "error": None,
        }
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def status_payload(self) -> dict:
        payload = dict(self.cache)
        payload["running"] = bool(payload.get("running"))
        payload["stop_requested"] = bool(payload.get("stop_requested"))
        return payload

    def request_stop(self) -> dict:
        self.stop_event.set()
        self.cache["stop_requested"] = True
        if not self.cache.get("running"):
            self.cache["stage"] = "idle"
        return self.status_payload()

    def set_progress(self, progress: dict) -> None:
        current_to = int(progress.get("current_to_block") or 0)
        start = int(progress.get("from_block") or 0)
        end = int(progress.get("to_block") or 0)
        total = max(1, end - start + 1)
        scanned = max(0, current_to - start + 1)
        percent = max(0.0, min(100.0, scanned / total * 100.0))
        self.cache["progress"] = {
            **progress,
            "percent": round(percent, 2),
            "scanned_blocks": scanned,
            "total_blocks": total,
        }

    def start_background(self, target: Callable[[], dict]) -> dict:
        if self.thread is not None and self.thread.is_alive():
            return {"started": False, "running": True, **self.status_payload()}
        self.thread = threading.Thread(
            target=target,
            name="liquidation-account-backfill",
            daemon=True,
        )
        self.thread.start()
        return {"started": True, "running": True, **self.status_payload()}
