import os
import signal
import subprocess
import threading
from datetime import datetime, timezone

from flask import jsonify


def register_control_routes(app, panel) -> None:
    @app.post("/api/start")
    def start():
        if panel.quick_observer_running():
            panel.set_observer_progress("running", "观察器已在运行", 100)
            panel.set_control_status("success", "启动观察器", "启动观察器已经执行", 100)
            return jsonify(
                {
                    "running": True,
                    "starting": False,
                    "pid": panel.quick_observer_pid(),
                    "symbols": panel.velocity_start_symbols(),
                }
            )
        with panel.observer_start_lock:
            if panel.observer_starting:
                return jsonify({"running": False, "starting": True, "symbols": panel.velocity_start_symbols()}), 202
            try:
                panel.configured_database_url()
            except Exception as exc:
                panel.observer_start_error = str(exc)
                panel.set_observer_progress("error", str(exc), 0)
                panel.set_control_status("error", "启动观察器", f"启动观察器执行失败：{exc}", 0)
                return jsonify({"error": str(exc)}), 400
            _, symbols = panel.build_observer_env()
            panel.observer_starting = True
            panel.observer_start_error = None
            panel.selected_symbols = symbols
            panel.observer_start_progress["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            panel.set_control_status("initializing", "启动观察器", "启动观察器已经执行", 5)
            panel.set_observer_progress("initializing", "已提交启动请求", 5)
        threading.Thread(
            target=panel.start_observer_background,
            name="observer-starter",
            daemon=True,
        ).start()
        return jsonify({"running": False, "starting": True, "symbols": panel.selected_symbols}), 202

    @app.post("/api/init")
    def init_database():
        panel.set_control_status("initializing", "初始化数据库", "初始化数据库已经执行", 25)
        try:
            panel.ensure_database_schema(panel.configured_database_url())
            counts = panel.database_table_counts()
        except Exception as exc:
            message = panel.database_lock_message("初始化数据库", exc)
            panel.set_control_status("error", "初始化数据库", message, 0)
            return jsonify({"error": message}), 400
        panel.set_control_status("success", "初始化数据库", "初始化数据库已经执行", 100)
        return jsonify({"initialized": True, "rows": panel.observation_count(), "db_counts": counts})

    @app.post("/api/stop")
    def stop():
        with panel.observer_start_lock:
            panel.observer_starting = False
            panel.set_observer_progress("stopped", "已提交停止请求", 0)
        panel.set_control_status("initializing", "停止观察器", "停止观察器已经执行", 25)
        if panel.is_observer_running():
            if panel.observer_process is not None and panel.observer_process.poll() is None:
                panel.observer_process.terminate()
                try:
                    panel.observer_process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    panel.observer_process.kill()
                    panel.observer_process.wait(timeout=3)
            else:
                pid = panel.read_observer_pid()
                if pid is not None:
                    os.kill(pid, signal.SIGTERM)
        panel.observer_process = None
        panel.selected_symbols = []
        panel.OBSERVER_PID_PATH.unlink(missing_ok=True)
        panel.set_control_status("success", "停止观察器", "停止观察器已经执行", 100)
        return jsonify({"running": False})

    @app.post("/api/clear")
    def clear():
        panel.set_control_status("initializing", "清空数据库", "清空数据库已经执行", 25)
        try:
            panel.ensure_database_schema(panel.configured_database_url())
            psycopg = panel.require_psycopg()
            with psycopg.connect(panel.configured_database_url(), connect_timeout=8) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '3s'")
                    cursor.execute("SET LOCAL statement_timeout = '10s'")
                    cursor.execute(
                        "TRUNCATE TABLE observations, binance_price_history, "
                        "binance_candidate_price_history, binance_pair_price_history, "
                        "binance_window_extremes, arbitrage_simulations RESTART IDENTITY"
                    )
        except Exception as exc:
            message = panel.database_lock_message("清空数据库", exc)
            panel.set_control_status("error", "清空数据库", message, 0)
            return jsonify({"error": message}), 400
        panel.set_control_status("success", "清空数据库", "清空数据库已经执行", 100)
        return jsonify({"cleared": True, "rows": 0})

    @app.post("/api/clear-files")
    def clear_files():
        panel.set_control_status("initializing", "清空文件", "清空文件已经执行", 25)
        deleted, errors = [], []
        paths = [
            panel.APP_DIR / "observations.csv",
            panel.LATEST_ARBITRAGE_PATH,
            panel.LATEST_EXTREMES_PATH,
            panel.LATEST_EXECUTABLE_SIGNAL_PATH,
        ]
        for path in paths:
            if path.exists():
                try:
                    path.unlink()
                    deleted.append(str(path))
                except OSError as exc:
                    errors.append(f"{path}: {exc}")
        if errors:
            panel.set_control_status("error", "清空文件", f"清空文件部分失败：{len(errors)} 个错误", 0)
        else:
            panel.set_control_status("success", "清空文件", f"清空文件已经执行，删除 {len(deleted)} 个文件", 100)
        return jsonify({"deleted": deleted, "errors": errors}), 400 if errors else 200
