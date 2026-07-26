import importlib
import os
import sys


REQUIRED_MODULES = {
    "flask": "flask",
    "psycopg": "psycopg[binary]",
    "web3": "web3",
    "websockets": "websockets",
}


def require_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Attach Replit SQL before starting the system."
        )
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
        database_url = require_database_url()
        require_dependencies()
        port = read_port()

        from control_panel import app
        from observer import ensure_database_schema

        ensure_database_schema(database_url)
    except Exception as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1

    print("database ready")
    print(f"control panel listening on 0.0.0.0:{port}")
    print("open the Replit Webview/Preview for this port to use the control panel")
    app.run(host="0.0.0.0", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
