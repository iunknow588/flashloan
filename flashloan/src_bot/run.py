#!/usr/bin/env python3
import importlib
import os
import sys
import threading

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text


load_env_files(__file__, override=False)


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
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
