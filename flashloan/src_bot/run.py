#!/usr/bin/env python3
import importlib
import os
import sys
import threading

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text


load_env_files(__file__, override=False)

# Quote-only CoW analysis starts from the same observer cycle as the Binance
# signal. Order submission remains separately guarded and disabled by default.
os.environ.setdefault("COW_REALTIME_QUOTE_ENABLED", "true")
os.environ.setdefault("COW_ORDER_SUBMISSION_ENABLED", "true")
os.environ.setdefault("COW_REALTIME_QUOTE_COOLDOWN_SECONDS", "0.25")
os.environ.setdefault("COW_REALTIME_QUOTE_MAX_INFLIGHT", "2")
os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_ENABLED", "true")
os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT", "0.618")
os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC", "0")
os.environ.setdefault("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC", "0")
os.environ["BINANCE_SCAN_PROFILE"] = "1000ms"
os.environ["BINANCE_CHANGE_WINDOW_SECONDS"] = "1.0"
os.environ["SAMPLE_SECONDS"] = "1.0"
os.environ["BINANCE_EXTREME_WRITE_SECONDS"] = "1.0"
os.environ["BINANCE_PAIR_PRICE_WRITE_SECONDS"] = "1.0"


REQUIRED_MODULES = {
    "flask": "flask",
    "psycopg": "psycopg[binary]",
    "psycopg_pool": "psycopg_pool",
    "web3": "web3",
    "websockets": "websockets",
}


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
            f"Missing dependencies: {packages}. Run: pip install -r requirements.txt"
        )


def read_port() -> int:
    raw_port = os.getenv("PORT", "5000").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(f"PORT must be an integer, got {raw_port!r}") from exc

    if not 1 <= port <= 65535:
        raise RuntimeError(f"PORT must be between 1 and 65535, got {port}")
    return port


def main() -> int:
    try:
        require_dependencies()
        port = read_port()

        from web.control_panel import app, initialize_cow_arbitrage_runtime, initialize_liquidation_runtime

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
