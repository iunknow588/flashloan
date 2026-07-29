#!/usr/bin/env python3
import importlib
import os
import sys
import threading

from core.env_loader import load_env_files


load_env_files(__file__)


REQUIRED_MODULES = {
    "flask": "flask",
    "psycopg": "psycopg[binary]",
    "web3": "web3",
    "websockets": "websockets",
}


def optional_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    return database_url


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
        database_url = optional_database_url()
        require_dependencies()
        port = read_port()

        from web.control_panel import app
        from db.storage import ensure_database_schema

    except Exception as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1

    print(f"control panel listening on 0.0.0.0:{port}")
    print(f"local URL: http://127.0.0.1:{port}")
    print("open the local control panel URL in your browser")

    def initialize_database() -> None:
        if not database_url:
            print(
                "DATABASE_URL is not configured; database-backed actions will "
                "return an error until .env or the system environment provides it.",
                file=sys.stderr,
            )
            return
        try:
            ensure_database_schema(database_url)
            print("database ready")
        except Exception as exc:
            # Keep the HTTP health endpoint available so the deployment can
            # recover from a transient database connection failure. Database
            # operations expose their own errors and can be retried from the
            # control panel.
            print(f"database initialization failed: {exc}", file=sys.stderr)

    threading.Thread(
        target=initialize_database,
        name="database-initializer",
        daemon=True,
    ).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
