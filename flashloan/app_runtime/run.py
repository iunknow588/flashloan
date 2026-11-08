#!/usr/bin/env python3
import hashlib
import importlib
import locale
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


_SPLIT_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SPLIT_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SPLIT_SRC_ROOT))

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text


REQUIRED_MODULES = {
    "flask": "flask",
    "web3": "web3",
    "websockets": "websockets",
    "dotenv": "python-dotenv",
}

_SINGLETON_LOCK: object | None = None
_SINGLETON_LOCK_FD: object | None = None
_SINGLETON_NAME_PREFIX = "Local\\flashloan_run_singleton_"
RUNTIME_PROFILE_V4_DEX = "v4_dex"
RUNTIME_PROFILE_FULL = "full"
_SUPPORTED_RUNTIME_PROFILES = {RUNTIME_PROFILE_V4_DEX, RUNTIME_PROFILE_FULL}
RUNTIME_BACKGROUND_THREAD_NAME = "runtime-background-init"


def _running_worktree_instances() -> list[dict[str, str]]:
    """Find previous run.py instances launched from this same worktree."""
    if os.name != "nt":
        return []
    text: str | None = None
    for command in (
        ["wmic", "process", "get", "ProcessId,CommandLine"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
        ],
    ):
        try:
            output = subprocess.run(
                command,
                capture_output=True,
                timeout=8,
                check=False,
            ).stdout
            if not output:
                continue
            candidate = output.decode(
                locale.getpreferredencoding(False) or "utf-8",
                errors="ignore",
            ).strip()
            if candidate:
                text = candidate
                break
        except Exception:
            continue
    if text is None or not text.strip():
        return []
    candidates = text.splitlines()
    if text.lstrip().startswith("["):
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, list):
                candidates = [
                    f"{row.get('CommandLine') or ''} {row.get('ProcessId') or ''}"
                    for row in parsed
                    if isinstance(row, dict)
                ]
            elif isinstance(parsed, dict):
                candidates = [
                    f"{parsed.get('CommandLine') or ''} {parsed.get('ProcessId') or ''}"
                ]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    repo_root = str(Path(__file__).resolve().parents[2]).lower()
    script_name = "run.py"
    instances: list[dict[str, str]] = []
    for line in candidates:
        if script_name not in line:
            continue
        if "flashloan/app_runtime/run.py" not in line.lower() and "flashloan\\app_runtime\\run.py" not in line.lower():
            if repo_root not in line.lower():
                continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            continue
        pid = parts[1].strip()
        if pid == str(os.getpid()):
            continue
        instances.append({"pid": pid, "command": line.strip()})
    return instances


def stop_previous_worktree_instances() -> list[str]:
    """Terminate stale run.py servers from this repo before binding the port."""
    stopped: list[str] = []
    for instance in _running_worktree_instances():
        pid = instance["pid"]
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True,
                    timeout=8,
                    check=False,
                )
            else:
                os.kill(int(pid), 9)
        except Exception:
            continue
        stopped.append(pid)
    if stopped:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and _running_worktree_instances():
            time.sleep(0.2)
    return stopped


def _worktree_singleton_key() -> str:
    root = str(Path(__file__).resolve().parents[2])
    if os.name == "nt":
        root = root.lower()
    return hashlib.sha1(root.encode("utf-8")).hexdigest()


def _runtime_singleton_lock_path() -> Path:
    configured = os.environ.get("FLASHLOAN_RUNTIME_DIR")
    base = Path(configured) if configured else (_SPLIT_SRC_ROOT / "runtime")
    return base / "state" / "run.singleton.lock"


def _acquire_windows_mutex(name: str) -> object | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    # ERROR_ALREADY_EXISTS means the named mutex was already created by this
    # or another process, so this caller is not the singleton owner.
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _release_windows_mutex(handle: object) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def _acquire_posix_lock(lock_path: Path) -> object | None:
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def acquire_runtime_singleton() -> bool:
    """Atomically claim the single-instance token for this worktree."""
    global _SINGLETON_LOCK, _SINGLETON_LOCK_FD
    if _SINGLETON_LOCK is not None or _SINGLETON_LOCK_FD is not None:
        return True
    key = _worktree_singleton_key()
    if os.name == "nt":
        handle = _acquire_windows_mutex(_SINGLETON_NAME_PREFIX + key)
        if handle is None:
            return False
        _SINGLETON_LOCK = handle
        return True
    fd = _acquire_posix_lock(_runtime_singleton_lock_path())
    if fd is None:
        return False
    _SINGLETON_LOCK_FD = fd
    return True


def release_runtime_singleton() -> None:
    global _SINGLETON_LOCK, _SINGLETON_LOCK_FD
    if _SINGLETON_LOCK is not None:
        try:
            _release_windows_mutex(_SINGLETON_LOCK)
        finally:
            _SINGLETON_LOCK = None
    if _SINGLETON_LOCK_FD is not None:
        try:
            import fcntl

            fcntl.flock(_SINGLETON_LOCK_FD.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        finally:
            try:
                _SINGLETON_LOCK_FD.close()
            finally:
                _SINGLETON_LOCK_FD = None


def acquire_runtime_singleton_wait(timeout_seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        if acquire_runtime_singleton():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def runtime_profile() -> str:
    raw = os.getenv("FLASHLOAN_RUNTIME_PROFILE", RUNTIME_PROFILE_V4_DEX)
    normalized = str(raw or "").strip().lower()
    if normalized in _SUPPORTED_RUNTIME_PROFILES:
        return normalized
    print(
        f"unsupported FLASHLOAN_RUNTIME_PROFILE={raw!r}; falling back to {RUNTIME_PROFILE_V4_DEX}",
        file=sys.stderr,
        flush=True,
    )
    return RUNTIME_PROFILE_V4_DEX


def dex_only_runtime_enabled() -> bool:
    return runtime_profile() == RUNTIME_PROFILE_V4_DEX


def initialize_non_dex_runtime_background(
    initialize_liquidation: Callable[[], None],
    initialize_cow: Callable[[], None],
) -> bool:
    if dex_only_runtime_enabled():
        print(
            "non-DEX background disabled by runtime profile v4_dex",
            file=sys.stderr,
            flush=True,
        )
        return False

    try:
        initialize_liquidation()
    except Exception as exc:
        print(
            f"liquidation runtime initialization failed: {redact_sensitive_text(exc)}",
            file=sys.stderr,
            flush=True,
        )
    try:
        initialize_cow()
    except Exception as exc:
        print(
            f"CoW arbitrage runtime initialization failed: {redact_sensitive_text(exc)}",
            file=sys.stderr,
            flush=True,
        )
    return True


def _apply_runtime_profile_defaults() -> dict[str, str]:
    profile = runtime_profile()
    os.environ["FLASHLOAN_RUNTIME_PROFILE"] = profile
    applied: dict[str, str] = {"FLASHLOAN_RUNTIME_PROFILE": profile}

    # DEX-only must override ambient .env values. Non-DEX profiles retain the
    # existing behavior and can opt in through their normal environment flags.
    if profile == RUNTIME_PROFILE_V4_DEX:
        applied.update(
            {
                "COW_REALTIME_QUOTE_ENABLED": "false",
                "COW_ORDER_SUBMISSION_ENABLED": "false",
                "COW_ARBITRAGE_DAEMON_ENABLED": "false",
                "LIQUIDATION_AUTO_EXECUTE": "false",
                "LIQUIDATION_EXECUTION_ENABLED": "false",
                "LIQUIDATION_ENGINE_ENABLED": "false",
                "LIQUIDATION_UI_SCAN_ENABLED": "false",
                "LIQUIDATION_BACKGROUND_REFRESH": "false",
                "BNB_VENUS_WATCHER_ENABLED": "false",
                "FLASHLOAN_BNB_SHARED_DATABASE": "0",
                "CONTROL_PANEL_DEFAULT_FEATURE": "dex_arbitrage",
            }
        )
        os.environ.update(applied)
        return applied

    os.environ.setdefault("COW_REALTIME_QUOTE_ENABLED", "true")
    os.environ.setdefault("COW_ORDER_SUBMISSION_ENABLED", "true")
    os.environ.setdefault("COW_ARBITRAGE_DAEMON_ENABLED", "true")
    os.environ.setdefault("BNB_VENUS_WATCHER_ENABLED", "false")
    os.environ.setdefault("LIQUIDATION_AUTO_EXECUTE", "false")
    os.environ.setdefault("LIQUIDATION_UI_SCAN_ENABLED", "false")
    os.environ.setdefault("LIQUIDATION_BACKGROUND_REFRESH", "false")
    os.environ.setdefault("CONTROL_PANEL_DEFAULT_FEATURE", "dex_arbitrage")
    return applied


@contextmanager
def _runtime_environment():
    original_env = os.environ.copy()
    try:
        os.environ.setdefault("FLASHLOAN_RUNTIME_DIR", str(_SPLIT_SRC_ROOT / "runtime"))
        os.environ.setdefault("FLASHLOAN_DB_ROOT", str(_SPLIT_SRC_ROOT.parent / "db"))
        load_env_files(__file__, override=False)

        profile_values = _apply_runtime_profile_defaults()

        # CoW tuning remains available for the explicit full profile. DEX-only
        # mode has already forced the feature switches off above.
        os.environ.setdefault("COW_REALTIME_QUOTE_ENABLED", "true")
        os.environ.setdefault("COW_REALTIME_QUOTE_NETWORK", "bnb")
        os.environ.setdefault("COW_FLASHLOAN_INTENT_NETWORK", "bnb")
        os.environ.setdefault("COW_ORDER_SUBMISSION_ENABLED", "true")
        os.environ.setdefault("COW_REALTIME_QUOTE_COOLDOWN_SECONDS", "0.25")
        os.environ.setdefault("COW_REALTIME_QUOTE_MAX_INFLIGHT", "2")
        os.environ.setdefault("COW_REALTIME_QUOTE_MAX_AGE_SECONDS", "0.2")
        os.environ.setdefault("COW_FLASHLOAN_LIVE_MAX_AGE_SECONDS", "0.2")
        os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_ENABLED", "true")
        os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT", "0.618")
        os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC", "0")
        os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC", "0")
        os.environ["BINANCE_SCAN_PROFILE"] = "1000ms"
        os.environ["BINANCE_CHANGE_WINDOW_SECONDS"] = "1.0"
        os.environ["SAMPLE_SECONDS"] = "1.0"
        os.environ["BINANCE_EXTREME_WRITE_SECONDS"] = "1.0"
        os.environ["BINANCE_PAIR_PRICE_WRITE_SECONDS"] = "1.0"
        # Feature default scope must follow the active chain so unscoped API/UI
        # requests cannot re-register a stale chain into the native runtime.
        active_chain_id = _dex_active_chain_id()
        active_network = _dex_active_network()
        os.environ["FLASHLOAN_DEFAULT_CHAIN_ID_DEX_ARBITRAGE"] = str(active_chain_id)
        os.environ["FLASHLOAN_DEFAULT_CHAIN_ID_FLASH_LOAN_POOL_PAGE"] = str(active_chain_id)
        os.environ["FLASHLOAN_DEFAULT_CHAIN_ID_LIQUIDATION"] = str(active_chain_id)
        from core.db.chain_database_context import chain_database_path

        os.environ["FLASHLOAN_KV_PATH"] = str(chain_database_path(active_chain_id, active_network).resolve())
        print(
            "runtime env: "
            f"profile={profile_values['FLASHLOAN_RUNTIME_PROFILE']} "
            f"dex_only={str(profile_values['FLASHLOAN_RUNTIME_PROFILE'] == RUNTIME_PROFILE_V4_DEX).lower()} "
            f"active={active_chain_id}/{active_network} "
            f"kv={os.environ.get('FLASHLOAN_KV_PATH')} "
            f"dex_default={os.environ.get('FLASHLOAN_DEFAULT_CHAIN_ID_DEX_ARBITRAGE')}",
            file=sys.stderr,
            flush=True,
        )
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def _dex_active_chain_id() -> int:
    try:
        return int(os.getenv("DEX_ACTIVE_CHAIN_ID", "4663").strip())
    except (TypeError, ValueError):
        return 4663


def _dex_active_network() -> str:
    raw = os.getenv("DEX_ACTIVE_NETWORK", "").strip().lower()
    if raw:
        return raw
    return "robinhood" if _dex_active_chain_id() == 4663 else "robinhood-testnet"


def _chain_database_ready(chain_id: int, network: str) -> bool:
    from core.db.chain_database_context import chain_database_path

    return chain_database_path(chain_id, network).exists()


def _ensure_chain_database(chain_id: int, network: str) -> dict:
    from core.db.chain_database_context import chain_database_path, chain_database_scope
    from core.db.storage_schema import ensure_database_schema
    from core.storage_seed import seed_local_kv_from_runtime_files

    path = chain_database_path(chain_id, network)
    with chain_database_scope(chain_id=chain_id, network=network, source="run-startup"):
        ensure_database_schema(str(path))
        seed_local_kv_from_runtime_files(str(path))
    return {"ok": True, "chain_id": chain_id, "network": network, "path": str(path)}


def require_dependencies() -> None:
    missing = []
    for module_name, package_name in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        packages = " ".join(missing)
        raise RuntimeError(
            f"Missing dependencies: {packages}. Replit/Nix users should run "
            "`python3 scripts/run_replit.py` from the repository root so the project "
            "virtual environment is prepared automatically. Manual install: "
            "`.venv/bin/python -m pip install -r flashloan/app_runtime/requirements.txt`."
        )


def read_port() -> int:
    raw_port = os.getenv("PORT", "5000").strip()
    return _coerce_port(raw_port, source="PORT")


def read_port_override() -> int | None:
    if "--port" not in sys.argv:
        return None
    try:
        raw_port = sys.argv[sys.argv.index("--port") + 1]
    except IndexError as exc:
        raise RuntimeError("--port requires a value, e.g. --port 5002") from exc
    return _coerce_port(raw_port, source="--port")


def _coerce_port(raw_port: str, *, source: str) -> int:
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"{source} must be an integer, got {raw_port!r}") from exc

    if not 1 <= port <= 65535:
        raise RuntimeError(f"{source} must be between 1 and 65535, got {port}")
    return port


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return True
        return False


def _port_process_ids(port: int) -> list[str]:
    """Return process ids listening on the given TCP port."""

    if os.name == "nt":
        cmd = ["netstat", "-ano", "-p", "tcp"]
    else:
        cmd = ["lsof", "-ti", f"tcp:{port}"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []
    stdout_bytes = result.stdout if isinstance(result.stdout, bytes) else b""
    if result.returncode != 0 and not stdout_bytes:
        return []
    try:
        stdout_text = stdout_bytes.decode(locale.getpreferredencoding(False) or "utf-8", errors="ignore")
    except Exception:
        stdout_text = stdout_bytes.decode("utf-8", errors="ignore")
    if os.name == "nt":
        pids: list[str] = []
        seen: set[str] = set()
        for line in stdout_text.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            if str(parts[0]).upper() != "TCP":
                continue
            local_address = str(parts[1])
            state = str(parts[3]).upper()
            if not local_address.endswith(f":{port}"):
                continue
            if state != "LISTENING":
                continue
            pid = parts[4]
            if pid.isdigit() and pid not in seen and pid != "0":
                seen.add(pid)
                pids.append(pid)
        return pids
    return stdout_text.splitlines()


def _try_close_port_owner(port: int) -> tuple[bool, str]:
    """Try to stop processes listening on ``port`` so it can be reused."""

    pids = _port_process_ids(port)
    if not pids:
        return False, f"port {port} is occupied but no listening pid could be identified"
    killed: list[str] = []
    for pid in pids:
        try:
            if os.name == "nt":
                command = ["taskkill", "/F", "/PID", str(pid)]
            else:
                command = ["kill", "-9", str(pid)]
            subprocess.run(
                command,
                capture_output=True,
                timeout=5,
                check=False,
            )
            killed.append(pid)
        except Exception:
            return False, f"port {port} pid {pid} could not be terminated"
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            return True, ",".join(killed)
        time.sleep(0.2)
    return False, f"port {port} processes terminated but port is still not reusable"


def resolve_port(preferred: int, *, explicit: bool = False) -> tuple[int, int | None]:
    """Return the port to bind and the occupied preferred port, if any.

    The launcher first tries to stop the old process that owns the preferred
    port. If that process cannot be stopped, or the port is still busy, the
    launcher selects the next free port instead.
    """
    if not _port_in_use(preferred):
        return preferred, None
    reused, _message = _try_close_port_owner(preferred)
    if reused and not _port_in_use(preferred):
        return preferred, None
    for candidate in range(preferred + 1, min(preferred + 200, 65535) + 1):
        if not _port_in_use(candidate):
            return candidate, preferred
    raise RuntimeError(
        f"port {preferred} could not be recycled and the next 200 ports are still occupied; "
        "cannot pick a free port automatically"
    )


def main() -> int:
    try:
        require_dependencies()
    except Exception as exc:
        print(f"startup failed: {redact_sensitive_text(exc)}", file=sys.stderr)
        return 1

    with _runtime_environment():
        stopped_previous = stop_previous_worktree_instances()
        if stopped_previous:
            print(
                f"stopped stale worktree server pids: {','.join(stopped_previous)}",
                file=sys.stderr,
                flush=True,
            )
        if not acquire_runtime_singleton_wait(timeout_seconds=8.0):
            print(
                "another run.py instance is already running for this worktree; "
                "refusing to start a duplicate",
                file=sys.stderr,
                flush=True,
            )
            return 1
        try:
            from core.db.storage_common import configured_database_url as storage_configured_database_url
            from core.storage_seed import seed_local_kv_from_runtime_files

            seed_local_kv_from_runtime_files(storage_configured_database_url())
            active_chain_id = _dex_active_chain_id()
            active_network = _dex_active_network()
            from core.db.chain_database_context import chain_databases_enabled

            if not chain_databases_enabled():
                if _chain_database_ready(active_chain_id, active_network):
                    os.environ["FLASHLOAN_CHAIN_DATABASES"] = "1"
                    print(
                        f"per-chain SQLite enabled for {active_network} chain {active_chain_id}",
                        file=sys.stderr,
                        flush=True,
                    )
            if str(os.getenv("FLASHLOAN_CHAIN_DATABASES", "").strip()).lower() in {"1", "true", "yes", "on"}:
                if not _chain_database_ready(active_chain_id, active_network):
                    print(
                        f"per-chain SQLite requested but {active_chain_id} database is missing; disabling",
                        file=sys.stderr,
                        flush=True,
                    )
                    os.environ["FLASHLOAN_CHAIN_DATABASES"] = "0"
                else:
                    _ensure_chain_database(active_chain_id, active_network)
            try:
                from core.runtime.chain_runtime import chain_runtime_registry

                runtime = chain_runtime_registry().register_chain(
                    active_chain_id,
                    network=active_network,
                    make_active=True,
                )
                print(
                    "chain runtime active: "
                    f"chain_id={runtime.chain_id} network={runtime.network} "
                    f"database={runtime.database_url}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"chain runtime registration failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as exc:
            print(
                f"runtime file import skipped: {redact_sensitive_text(exc)}",
                file=sys.stderr,
                flush=True,
            )

        try:
            explicit_port = read_port_override()
            port, occupied_preferred = resolve_port(explicit_port or read_port(), explicit=explicit_port is not None)
            if occupied_preferred is not None:
                print(
                    f"port {occupied_preferred} is occupied; selected port {port} instead",
                    file=sys.stderr,
                    flush=True,
                )

            from web.control_panel import app, initialize_cow_arbitrage_runtime, initialize_liquidation_runtime
            from dex.runtime.cow_submission_pause import disable_cow_submission_for_startup

            disable_cow_submission_for_startup()
        except Exception as exc:
            print(f"startup failed: {redact_sensitive_text(exc)}", file=sys.stderr)
            return 1

        def initialize_runtime_background() -> None:
            initialize_non_dex_runtime_background(
                initialize_liquidation_runtime,
                initialize_cow_arbitrage_runtime,
            )
            try:
                from dex.runtime.dex_opportunity_scanner import ensure_dex_opportunity_scanner_running
                from core.db.chain_database_context import chain_database_scope
                from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

                active_chain_id = _dex_active_chain_id()
                from core.runtime.chain_runtime import chain_runtime_registry

                runtime = chain_runtime_registry().get(active_chain_id)
                with chain_database_scope(
                    chain_id=active_chain_id,
                    network=_network_for_chain_id(active_chain_id),
                    source="run-scanner-init",
                ):
                    runtime.scanner.start(
                        chain_id=active_chain_id,
                        protocols=["uniswap_v4"],
                    )
            except Exception as exc:
                print(
                    f"DEX opportunity scanner initialization failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                from core.db.chain_database_context import chain_database_scope
                from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
                from dex.intent_trade.chains.robinhood_common.chain_config import (
                    ROBINHOOD_CHAIN_ID,
                    ROBINHOOD_NETWORK,
                    ROBINHOOD_TESTNET_CHAIN_ID,
                    ROBINHOOD_TESTNET_NETWORK,
                )
                active_chain_id = _dex_active_chain_id()
                active_network = _network_for_chain_id(active_chain_id)
                robinhood_network = {
                    ROBINHOOD_CHAIN_ID: ROBINHOOD_NETWORK,
                    ROBINHOOD_TESTNET_CHAIN_ID: ROBINHOOD_TESTNET_NETWORK,
                }.get(active_chain_id)
                if robinhood_network and active_network == robinhood_network:
                    from core.runtime.chain_runtime import chain_runtime_registry

                    runtime = chain_runtime_registry().get(active_chain_id)
                    with chain_database_scope(
                        chain_id=active_chain_id,
                        network=active_network,
                        source="run-v4-maintenance-workers",
                    ):
                        runtime.maintenance.start()
                        payload = runtime.maintenance.status_json()
                    print(
                        "V4 maintenance workers running: "
                        f"{sorted(payload.get('running') or {})}",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"V4 maintenance worker initialization failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                from core.db.bnb_database import (
                    bnb_shared_database_enabled,
                    ensure_bnb_shared_collector_running,
                )

                if bnb_shared_database_enabled():
                    ensure_bnb_shared_collector_running()
                else:
                    print(
                        "BNB shared market collector disabled by FLASHLOAN_BNB_SHARED_DATABASE",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"BNB shared market collector initialization failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                from core.db.storage_cow_execution import backfill_cow_execution_attempt_summaries
                from core.db.chain_database_context import chain_database_scope
                from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

                active_chain_id = _dex_active_chain_id()
                with chain_database_scope(
                    chain_id=active_chain_id,
                    network=_network_for_chain_id(active_chain_id),
                    source="run-summary-backfill",
                ):
                    report = backfill_cow_execution_attempt_summaries(
                        storage_configured_database_url(),
                        batch_size=1000,
                    )
                if int(report.get("updated") or 0):
                    print(
                        f"cow execution summary backfill updated {report.get('updated')} rows",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    f"cow execution summary backfill skipped: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
            # Robinhood mainnet V4 pages read business tables directly from
            # SQLite. The legacy intermediate pool JSON files are no longer
            # written at startup, so there is nothing to initialize here.

        threading.Thread(
            target=initialize_runtime_background,
            name=RUNTIME_BACKGROUND_THREAD_NAME,
            daemon=True,
        ).start()

        print(f"opportunity console listening on 0.0.0.0:{port}", flush=True)
        print(f"control panel: http://127.0.0.1:{port}", flush=True)
        print(
            "runtime profile: "
            f"profile={runtime_profile()} "
            f"cow_quote={os.getenv('COW_REALTIME_QUOTE_ENABLED')} "
            f"cow_submit={os.getenv('COW_ORDER_SUBMISSION_ENABLED')} "
            f"liquidation_auto={os.getenv('LIQUIDATION_AUTO_EXECUTE')} "
            f"bnb_collector={os.getenv('FLASHLOAN_BNB_SHARED_DATABASE')} "
            f"cooldown={os.getenv('COW_REALTIME_QUOTE_COOLDOWN_SECONDS')}s "
            f"max_inflight={os.getenv('COW_REALTIME_QUOTE_MAX_INFLIGHT')}",
            flush=True,
        )
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
