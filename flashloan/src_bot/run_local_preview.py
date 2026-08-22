"""Start the Flask control panel without observer or execution daemons."""

import os


def main() -> None:
    os.environ.setdefault("PORT", "5055")
    os.environ["TRIANGULAR_DIRECT_BROADCAST_ENABLED"] = "false"
    os.environ["TRIANGULAR_AB_BROADCAST_ENABLED"] = "false"
    os.environ["UNIFIED_EXECUTOR_BROADCAST_ENABLED"] = "false"
    os.environ["COW_ORDER_SUBMISSION_ENABLED"] = "false"
    os.environ["LIQUIDATION_EXECUTION_ENABLED"] = "false"

    from web.control_panel import app

    app.run(
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
