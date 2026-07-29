#!/usr/bin/env python3
import importlib
import os
import sys

from core.env_loader import load_env_files


load_env_files(__file__)


REQUIRED_MODULES = {
    "flask": "flask",
    "psycopg": "psycopg[binary]",
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

        from web.control_panel import app, initialize_liquidation_runtime

        initialize_liquidation_runtime()

    except Exception as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1

    print(f"opportunity console listening on 0.0.0.0:{port}")
    print(f"control panel: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
