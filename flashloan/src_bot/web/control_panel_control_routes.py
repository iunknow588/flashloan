import os
import signal
import subprocess
import threading
from datetime import datetime, timezone

from flask import jsonify


def _cache_running_blocker(panel, *, label: str, cache_name: str, lock_name: str) -> str | None:
    cache = getattr(panel, cache_name)
    lock = getattr(panel, lock_name)
    if not bool(cache.get("running")):
        return None
    if hasattr(lock, "locked") and not lock.locked():
        cache["running"] = False
        cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache["stage"] = "idle"
        return None
    stage = cache.get("stage") or "unknown"
    started_at = cache.get("started_at") or "unknown"
    return f"{label}正在运行，阶段={stage}，开始={started_at}"


def clear_database_blockers(panel) -> list[str]:
    with panel.observer_start_lock:
        panel.clear_stale_observer_start()
        observer_starting = bool(panel.observer_starting)
    blockers: list[str] = []
    if observer_starting:
        blockers.append("机会观察正在启动")
    if panel.is_observer_running():
        blockers.append("机会观察正在运行")
    for label, cache_name, lock_name in (
        ("账户池发现扫描", "LIQUIDATION_DISCOVERY_CACHE", "LIQUIDATION_DISCOVERY_LOCK"),
        ("债务/健康池扫描", "LIQUIDATION_SCAN_CACHE", "LIQUIDATION_SCAN_LOCK"),
    ):
        blocker = _cache_running_blocker(panel, label=label, cache_name=cache_name, lock_name=lock_name)
        if blocker:
            blockers.append(blocker)
    return blockers


def clear_database_blocker_message(blockers: list[str]) -> str:
    if not blockers:
        return ""
    details = "；".join(blockers)
    return f"清空数据库前需要等待当前后台任务结束：{details}"


def register_control_routes(app, panel) -> None:
    @app.post("/api/start")
    def start():
        if panel.quick_observer_running():
            panel.set_observer_progress("running", "机会观察已在运行", 100)
            panel.set_control_status("success", "启动机会观察", "机会观察进程已经在运行", 100)
            return jsonify(
                {
                    "running": True,
                    "starting": False,
                    "pid": panel.quick_observer_pid(),
                    "symbols": panel.velocity_start_symbols(),
                }
            )
        with panel.observer_start_lock:
            panel.clear_stale_observer_start()
            if panel.observer_starting:
                return jsonify(
                    {
                        "running": False,
                        "starting": True,
                        "message": "机会观察正在启动，请等待状态面板更新；如长时间无进展可点击停止后重试。",
                        "symbols": panel.velocity_start_symbols(),
                    }
                ), 202
            try:
                panel.configured_database_url()
            except Exception as exc:
                panel.observer_start_error = str(exc)
                panel.set_observer_progress("error", str(exc), 0)
                panel.set_control_status("error", "启动机会观察", f"启动机会观察失败：{exc}", 0)
                return jsonify({"error": str(exc)}), 400
            panel.observer_starting = True
            panel.observer_start_error = None
            panel.selected_symbols = panel.velocity_start_symbols()
            panel.observer_start_progress["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            panel.set_control_status("initializing", "启动机会观察", "启动请求已提交，正在后台加载市场与 Aave 上下文", 5, ttl_seconds=panel.OBSERVER_START_TIMEOUT_SECONDS + 30)
            panel.set_observer_progress("initializing", "已提交启动请求", 5)
        threading.Thread(
            target=panel.start_observer_background,
            name="observer-starter",
            daemon=True,
        ).start()
        return jsonify(
            {
                "running": False,
                "starting": True,
                "message": "启动请求已提交，状态面板会显示加载进度。",
                "symbols": panel.selected_symbols,
            }
        ), 202

    @app.post("/api/stop")
    def stop():
        with panel.observer_start_lock:
            panel.observer_starting = False
            panel.set_observer_progress("stopped", "已提交停止请求", 0)
        panel.set_control_status("initializing", "停止机会观察", "停止请求已经提交", 25)
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
        panel.set_control_status("success", "停止机会观察", "机会观察已经停止", 100)
        return jsonify({"running": False})

    @app.post("/api/clear")
    def clear():
        blockers = clear_database_blockers(panel)
        if blockers:
            message = clear_database_blocker_message(blockers)
            panel.set_control_status("error", "清空数据库", message, 0)
            return jsonify({"error": message, "blockers": blockers}), 400
        panel.set_control_status("initializing", "清空数据库", "正在清空行情与观察数据", 25)
        try:
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
        panel.set_control_status("success", "清空数据库", "清空数据库已经完成", 100)
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
