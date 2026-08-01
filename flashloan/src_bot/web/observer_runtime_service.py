import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ObserverRuntimeService:
    observer_start_progress: dict[str, Any] = field(
        default_factory=lambda: {
            "state": "stopped",
            "stage": "未启动",
            "percent": 0,
            "started_at": None,
        }
    )
    control_status: dict[str, Any] = field(
        default_factory=lambda: {
            "state": "stopped",
            "stage": "",
            "message": "",
            "percent": 0,
            "updated_at": 0.0,
            "ttl_seconds": 0.0,
        }
    )
    supervisor_state: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "heartbeat_at": 0.0,
            "restart_count": 0,
            "last_restart_at": 0.0,
            "next_restart_at": 0.0,
            "last_exit_code": None,
            "last_error": None,
        }
    )
    supervisor_lock: threading.Lock = field(default_factory=threading.Lock)
    start_lock: threading.Lock = field(default_factory=threading.Lock)

    def set_observer_progress(self, state: str, stage: str, percent: int) -> None:
        self.observer_start_progress.update(
            {
                "state": state,
                "stage": stage,
                "percent": max(0, min(int(percent), 100)),
                "started_at": self.observer_start_progress.get("started_at"),
            }
        )

    def set_control_status(self, state: str, stage: str, message: str, percent: int, ttl_seconds: float = 0.0) -> None:
        self.control_status.update(
            {
                "state": state,
                "stage": stage,
                "message": message,
                "percent": max(0, min(int(percent), 100)),
                "updated_at": time.monotonic(),
                "ttl_seconds": max(0.0, float(ttl_seconds)),
            }
        )

    def mark_supervisor_heartbeat(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        with self.supervisor_lock:
            self.supervisor_state["enabled"] = True
            self.supervisor_state["heartbeat_at"] = current

    def supervisor_payload(self) -> dict[str, Any]:
        with self.supervisor_lock:
            state = dict(self.supervisor_state)
        heartbeat_at = float(state.get("heartbeat_at") or 0.0)
        return {
            **state,
            "healthy": bool(state.get("enabled")) and bool(state.get("heartbeat_at")),
            "heartbeat_age_seconds": round(time.monotonic() - heartbeat_at, 1) if heartbeat_at else None,
        }

    def update_supervisor_state(self, **kwargs: Any) -> None:
        with self.supervisor_lock:
            if "enabled" in kwargs:
                self.supervisor_state["enabled"] = bool(kwargs["enabled"])
            if "heartbeat_at" in kwargs:
                self.supervisor_state["heartbeat_at"] = float(kwargs["heartbeat_at"])
            if "restart_count" in kwargs:
                self.supervisor_state["restart_count"] = int(kwargs["restart_count"])
            if "last_restart_at" in kwargs:
                self.supervisor_state["last_restart_at"] = float(kwargs["last_restart_at"])
            if "next_restart_at" in kwargs:
                self.supervisor_state["next_restart_at"] = float(kwargs["next_restart_at"])
            if "last_exit_code" in kwargs:
                self.supervisor_state["last_exit_code"] = kwargs["last_exit_code"]
            if "last_error" in kwargs:
                self.supervisor_state["last_error"] = kwargs["last_error"]

    def reset_runtime_state(self) -> None:
        with self.start_lock:
            self.observer_start_progress.update(
                {
                    "state": "stopped",
                    "stage": "未启动",
                    "percent": 0,
                    "started_at": None,
                }
            )
            self.control_status.update(
                {
                    "state": "stopped",
                    "stage": "",
                    "message": "",
                    "percent": 0,
                    "updated_at": 0.0,
                    "ttl_seconds": 0.0,
                }
            )
        with self.supervisor_lock:
            self.supervisor_state.update(
                {
                    "enabled": False,
                    "heartbeat_at": 0.0,
                    "restart_count": 0,
                    "last_restart_at": 0.0,
                    "next_restart_at": 0.0,
                    "last_exit_code": None,
                    "last_error": None,
                }
            )
