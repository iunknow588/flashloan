from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path
from threading import Event, Thread

from core.env_loader import load_env_files, resolve_env_path
from core.sensitive_data import redact_sensitive_text

# Keep deployment-level environment overrides intact; .env fills missing values.
load_env_files(__file__, override=False)

APP_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
STATUS_PATH = RUNTIME_DIR / "state" / "liquidation_daemon_status.json"
LOG = logging.getLogger("liquidation-daemon")


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.5, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _write_status(payload: dict) -> bool:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    for attempt in range(6):
        temporary = STATUS_PATH.with_name(f"{STATUS_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(STATUS_PATH)
            return True
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    LOG.warning("unable to update liquidation daemon status file after retries")
    return False


def read_daemon_status(path: Path | None = None) -> dict:
    target = path or STATUS_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        if path is not None:
            return payload
        return _normalize_daemon_status(payload)
    except (OSError, json.JSONDecodeError):
        return {}


def _process_exists(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def _normalize_daemon_status(payload: dict) -> dict:
    state = str(payload.get("state") or "").lower()
    if state not in {"starting", "running", "degraded"}:
        return payload
    current_pid = payload.get("pid")
    if not _process_exists(current_pid):
        normalized = dict(payload)
        normalized["state"] = "stale"
        normalized["stale"] = True
        normalized["running"] = False
        normalized["stale_reason"] = "daemon process is not running"
        return normalized
    updated_at = payload.get("updated_at") or payload.get("started_at")
    try:
        age_seconds = time.time() - float(updated_at)
    except (TypeError, ValueError):
        age_seconds = None
    max_age = _env_float("LIQUIDATION_DAEMON_STALE_SECONDS", 30.0)
    if age_seconds is not None and age_seconds > max_age:
        normalized = dict(payload)
        normalized["state"] = "stale"
        normalized["stale"] = True
        normalized["running"] = False
        normalized["stale_reason"] = f"daemon status heartbeat is stale ({age_seconds:.1f}s)"
        normalized["heartbeat_age_seconds"] = round(age_seconds, 1)
        return normalized
    return payload


def _split_symbols(value: object) -> list[str]:
    symbols: list[str] = []
    for item in str(value or "").replace(";", ",").split(","):
        symbol = item.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def market_status_payload(env_symbols: object, display_symbols: object, snapshot: dict) -> dict:
    subscribed = _split_symbols(env_symbols)
    display = _split_symbols(display_symbols)
    snapshot_symbols = sorted(str(symbol).upper() for symbol in (snapshot or {}).keys())
    fresh = bool(snapshot_symbols)
    return {
        "subscribed_symbols": subscribed,
        "display_symbols": display,
        "snapshot_symbols": snapshot_symbols,
        "snapshot_count": len(snapshot_symbols),
        "missing_snapshot_symbols": [symbol for symbol in subscribed if symbol not in snapshot_symbols],
        "fresh": fresh,
        "state": "fresh" if fresh else ("waiting_for_snapshot" if subscribed else "idle"),
    }


def _terminate_observer(panel) -> None:
    process = getattr(panel, "observer_process", None)
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except Exception:
            process.kill()
    else:
        pid_reader = getattr(panel, "quick_observer_pid", None)
        pid = pid_reader() if callable(pid_reader) else None
        if pid and int(pid) != os.getpid():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
    stop_supervisor = getattr(panel, "stop_observer_supervisor", None)
    if callable(stop_supervisor):
        stop_supervisor()


def prepare_market_runtime(panel) -> dict:
    existing_observer = bool(panel.quick_observer_running())
    if existing_observer:
        # A daemon started after the UI must own the observer it reads from.
        # Reusing a detached process leaves this daemon with no shared price
        # snapshot and falsely reports a healthy market connection.
        _terminate_observer(panel)
    quick_running = False
    market_result: dict[str, object] = {
        "state": "starting",
        "reused": False,
        "restarted_existing": existing_observer,
    }
    try:
        env, symbols = panel.build_observer_env()
        market_result["env_symbols"] = env.get("SYMBOLS", "")
        market_result["display_symbols"] = ",".join(symbols)
        panel.launch_observer_process(env, symbols)
        panel.start_observer_supervisor()
        market_result["state"] = "completed"
        return market_result
    except Exception as exc:
        market_result["state"] = "error"
        market_result["error"] = redact_sensitive_text(exc)
        return market_result


def run(stop: Event | None = None) -> int:
    from web import control_panel as panel

    stop_event = stop or Event()
    daemon_pid = os.getpid()
    started_at = time.time()
    scan_interval = _env_float("LIQUIDATION_DAEMON_STATUS_SECONDS", 2.0)
    state = {
        "pid": daemon_pid,
        "state": "starting",
        "started_at": started_at,
        "observer": {},
        "engine": {},
        "account_scan": {"state": "not_started"},
        "last_error": None,
    }
    _write_status(state)

    try:
        panel.configured_database_url()
        engine_result = panel.start_liquidation_engine_runtime(force=True)
        state["engine"] = engine_result
        state["state"] = "running"
        state["observer"] = {"state": "starting", "stage": "market_environment"}
        state["account_scan"] = {"state": "running", "stage": "historical_account_and_core_pool"}
        _write_status(state)

        market_result: dict[str, object] = {}
        market_started_at = time.time()

        def initialize_market() -> None:
            result = prepare_market_runtime(panel)
            market_result.update(result)

        market_thread = Thread(target=initialize_market, name="liquidation-market-start", daemon=True)
        market_thread.start()
        state["state"] = "running"
        _write_status(state)

        scan_result: dict[str, str] = {}

        def initialize_scan() -> None:
            try:
                panel._scan_initialize_liquidation_runtime()
                scan_result["state"] = "completed"
            except Exception as exc:
                scan_result["state"] = "error"
                scan_result["error"] = redact_sensitive_text(exc)

        scan_thread = Thread(target=initialize_scan, name="liquidation-account-scan", daemon=True)
        scan_thread.start()
        while not stop_event.wait(scan_interval):
            engine = getattr(panel, "liquidation_engine_instance", None)
            if not market_result and market_thread.is_alive():
                init_timeout = _env_float("LIQUIDATION_MARKET_INIT_TIMEOUT_SECONDS", 60.0)
                if time.time() - market_started_at > init_timeout:
                    market_result.update(
                        {
                            "state": "error",
                            "error": f"market initialization timed out after {init_timeout:.1f}s",
                        }
                    )
            quick_running = panel.quick_observer_running()
            observer = panel.observer_supervisor_payload()
            if quick_running:
                fallback_symbols = ",".join(panel.displayed_symbols(True) or panel.velocity_start_symbols())
                observer = {
                    **observer,
                    "enabled": True,
                    "healthy": True,
                    "state": "running",
                    "pid": panel.quick_observer_pid(),
                    "env_symbols": observer.get("env_symbols") or fallback_symbols,
                    "display_symbols": observer.get("display_symbols") or fallback_symbols,
                }
            if market_result.get("state") == "error":
                observer = {
                    **observer,
                    "healthy": False,
                    "state": "error",
                    "last_error": market_result.get("error"),
                }
            elif market_result.get("state") == "completed":
                observer = {
                    **observer,
                    "state": "running",
                    "env_symbols": market_result.get("env_symbols"),
                    "display_symbols": market_result.get("display_symbols"),
                }
            scan_state = dict(state.get("account_scan") or {})
            if scan_result.get("state") == "completed":
                scan_state.update({"state": "completed", "stage": "refresh_loop_active"})
            elif scan_result.get("state") == "error":
                scan_state.update({"state": "error", "error": scan_result.get("error")})
            elif not scan_thread.is_alive():
                scan_state.update({"state": "completed", "stage": "refresh_loop_active"})
            market_snapshot = panel.liquidation_market_price_snapshot()
            market_state = market_status_payload(
                observer.get("env_symbols"),
                observer.get("display_symbols"),
                market_snapshot,
            )
            observer_healthy = bool(observer.get("healthy"))
            market_healthy = bool(market_state.get("fresh")) or not bool(market_state.get("subscribed_symbols"))
            state.update(
                {
                    "state": "running" if observer_healthy and market_healthy else "degraded",
                    "observer": observer,
                    "account_scan": scan_state,
                    "engine": {
                        "started": bool(engine),
                        "mode": engine.config.mode if engine else None,
                        "auto_execute": bool(engine and engine.config.auto_execute),
                        "manual_test_completed": bool(engine and engine.config.manual_test_completed),
                        "last_market_snapshot": market_snapshot,
                    },
                    "market": market_state,
                    "updated_at": time.time(),
                }
            )
            _write_status(state)
    except Exception as exc:
        state.update({"state": "error", "last_error": redact_sensitive_text(exc), "updated_at": time.time()})
        _write_status(state)
        LOG.exception("liquidation daemon failed")
        return 1
    finally:
        engine = getattr(panel, "liquidation_engine_instance", None)
        if engine is not None:
            engine.stop()
        _terminate_observer(panel)
        state.update({"state": "stopped", "updated_at": time.time()})
        _write_status(state)
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stop.set())
    return run(stop)


if __name__ == "__main__":
    raise SystemExit(main())
