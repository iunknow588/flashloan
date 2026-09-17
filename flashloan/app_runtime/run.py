#!/usr/bin/env python3
import importlib
import locale
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


_SPLIT_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SPLIT_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SPLIT_SRC_ROOT))

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text


REQUIRED_MODULES = {
    "flask": "flask",
    "psycopg": "psycopg[binary]",
    "psycopg_pool": "psycopg_pool",
    "web3": "web3",
    "websockets": "websockets",
    "dotenv": "python-dotenv",
}


@contextmanager
def _runtime_environment():
    original_env = os.environ.copy()
    try:
        os.environ.setdefault("FLASHLOAN_LOCAL_JSON_ONLY", "true")
        os.environ.pop("DATABASE_URL", None)
        load_env_files(__file__, override=False)

        # CoW analysis starts from the same observer cycle as the Binance signal.
        # Real order submission is additionally gated by the UI transaction switch.
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
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


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
            "`python3 run_replit.py` from the repository root so the project "
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
            try:
                initialize_liquidation_runtime()
            except Exception as exc:
                print(
                    f"liquidation runtime initialization failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                initialize_cow_arbitrage_runtime()
            except Exception as exc:
                print(
                    f"CoW arbitrage runtime initialization failed: {redact_sensitive_text(exc)}",
                    file=sys.stderr,
                    flush=True,
                )

        threading.Thread(
            target=initialize_runtime_background,
            name="liquidation-runtime-init",
            daemon=True,
        ).start()

        print(f"opportunity console listening on 0.0.0.0:{port}", flush=True)
        print(f"control panel: http://127.0.0.1:{port}", flush=True)
        print(
            "CoW realtime quote: "
            f"enabled={os.getenv('COW_REALTIME_QUOTE_ENABLED')} "
            f"cooldown={os.getenv('COW_REALTIME_QUOTE_COOLDOWN_SECONDS')}s "
            f"max_inflight={os.getenv('COW_REALTIME_QUOTE_MAX_INFLIGHT')}",
            flush=True,
        )
        app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
