"""Process and lock helper functions for the control panel facade."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from core.config_schema import parse_env_float
from core.env_loader import resolve_env_path
from core.sensitive_data import redact_sensitive_text

SRC_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SRC_ROOT
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
OBSERVER_PID_PATH = RUNTIME_DIR / "observer.pid"
OBSERVER_RESTART_BASE_DELAY_SECONDS = parse_env_float("OBSERVER_RESTART_BASE_DELAY_SECONDS", 1, minimum=0.1)[0]
OBSERVER_RESTART_MAX_DELAY_SECONDS = parse_env_float("OBSERVER_RESTART_MAX_DELAY_SECONDS", 30, minimum=0.1)[0]

discovered_observer_pid: Optional[int] = None
discovered_observer_checked_at = 0.0


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        return windows_process_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False





def observer_process_exists(pid: int) -> bool:
    if not process_exists(pid):
        return False
    if os.name == "nt":
        try:
            command = (
                "Get-CimInstance Win32_Process -Filter \"ProcessId = "
                f"{int(pid)}\" | Select-Object -First 1 -ExpandProperty CommandLine"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            command_line = result.stdout.replace("\\", "/")
            return "observer.py" in command_line or "market.observer" in command_line
        except Exception:
            return False
    try:
        command_line = Path(f"/proc/{int(pid)}/cmdline").read_text(encoding="utf-8", errors="ignore")
        return "observer.py" in command_line or "market.observer" in command_line
    except OSError:
        return False





def windows_process_exists(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, int(pid))
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False





def discover_observer_pid() -> Optional[int]:
    global discovered_observer_checked_at, discovered_observer_pid
    now = time.monotonic()
    if now - discovered_observer_checked_at < 3.0:
        if discovered_observer_pid is None or observer_process_exists(discovered_observer_pid):
            return discovered_observer_pid
        discovered_observer_pid = None
    if discovered_observer_pid is not None and observer_process_exists(discovered_observer_pid):
        return discovered_observer_pid
    discovered_observer_checked_at = now
    if os.name != "nt":
        return None
    try:
        command = (
            "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*observer.py*' -or $_.CommandLine -like '*market.observer*' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        raw = result.stdout.strip().splitlines()
        pid = int(raw[0].strip()) if raw else None
        discovered_observer_pid = pid if pid and observer_process_exists(pid) else None
        if discovered_observer_pid is not None:
            write_observer_pid(discovered_observer_pid)
        return discovered_observer_pid
    except Exception:
        discovered_observer_pid = None
        return None





def read_observer_pid() -> Optional[int]:
    try:
        if not OBSERVER_PID_PATH.exists():
            return discover_observer_pid()
        pid = int(OBSERVER_PID_PATH.read_text(encoding="utf-8").strip())
        if observer_process_exists(pid):
            return pid
        OBSERVER_PID_PATH.unlink(missing_ok=True)
    except (OSError, ValueError):
        return None
    return None





def write_observer_pid(pid: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    OBSERVER_PID_PATH.write_text(str(pid), encoding="utf-8")





def observer_supervisor_delay(restart_count: int) -> float:
    base = max(0.1, float(OBSERVER_RESTART_BASE_DELAY_SECONDS))
    cap = max(base, float(OBSERVER_RESTART_MAX_DELAY_SECONDS))
    return min(cap, base * (2 ** max(0, int(restart_count) - 1)))





def database_lock_message(action: str, exc: Exception) -> str:
    detail = redact_sensitive_text(exc)
    lowered = detail.lower()
    lock_markers = (
        "deadlock detected",
        "lock timeout",
        "could not obtain lock",
        "accessexclusivelock",
        "blocked by process",
        "canceling statement due to statement timeout",
    )
    if any(marker in lowered for marker in lock_markers):
        return (
            f"{action}执行失败：数据库正在被其他会话锁定。"
            "这可能是远程应用、数据库控制台查询窗口，也可能是本应用启动后的后台初始化、账户发现、健康扫描或机会观察写入。"
            "请先停止机会观察，等待后台扫描结束，关闭数据库控制台查询窗口后重试。"
            f" 原始错误：{detail}"
        )
    return f"{action}执行失败：{detail}"


