"""Start the Flask control panel without observer or execution daemons."""

import os
import socket
import threading
import time
import webbrowser


def _wait_for_port(host: str, port: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _open_preview_url(url: str) -> bool:
    os.environ.pop("BROWSER", None)
    try:
        if webbrowser.open(url, new=2, autoraise=True):
            return True
    except webbrowser.Error:
        pass
    if hasattr(os, "startfile"):
        try:
            os.startfile(url)
            return True
        except OSError:
            return False
    return False


def _launch_browser_when_ready(host: str, port: int) -> None:
    if os.getenv("FLASHLOAN_DISABLE_BROWSER_AUTO_OPEN", "").strip().lower() in {"1", "true", "yes"}:
        return
    if _wait_for_port(host, port):
        _open_preview_url(f"http://127.0.0.1:{port}/")


def main() -> None:
    os.environ.setdefault("PORT", "5055")
    os.environ["TRIANGULAR_DIRECT_BROADCAST_ENABLED"] = "false"
    os.environ["TRIANGULAR_AB_BROADCAST_ENABLED"] = "false"
    os.environ["UNIFIED_EXECUTOR_BROADCAST_ENABLED"] = "false"
    os.environ["COW_ORDER_SUBMISSION_ENABLED"] = "false"
    os.environ["LIQUIDATION_EXECUTION_ENABLED"] = "false"
    os.environ.setdefault("CONTROL_PANEL_DEFAULT_FEATURE", "dex_arbitrage")
    os.environ.setdefault("BNB_VENUS_WATCHER_ENABLED", "false")
    os.environ.setdefault("LIQUIDATION_UI_SCAN_ENABLED", "false")
    os.environ.setdefault("LIQUIDATION_BACKGROUND_REFRESH", "false")

    from web.control_panel import app
    from web.flash_loan_pool_prewarm import prewarm_robinhood_mainnet_v4_pool_view

    prewarm_robinhood_mainnet_v4_pool_view()

    threading.Thread(
        target=_launch_browser_when_ready,
        args=("127.0.0.1", int(os.environ["PORT"])),
        name="local-preview-browser-launcher",
        daemon=True,
    ).start()

    app.run(
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
