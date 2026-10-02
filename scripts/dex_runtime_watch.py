#!/usr/bin/env python3
"""Keep the DEX quote-routes page's auto-execute switch enabled and watch for
submission/system errors.  When the web runtime is unhealthy or a new system
submission error appears, restart ``flashloan/app_runtime/run.py``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_PY = REPO_ROOT / "flashloan" / "app_runtime" / "run.py"
DEFAULT_PORT = 5000
QUERY_KEYS = ("chain_id", "market_id", "protocols", "dex_strategy")
SYSTEM_MARKERS = (
    "submission_failed",
    "submit_failed",
    "code",
    "broadcast_failed",
    "broadcast_error",
    "execution_failed",
    "confirmed_failed",
    "settlement_failed",
    "order_failed",
    "rpc_verification_timeout",
    "rpc_verification_failed",
)


def log_setup(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("dex_runtime_watch")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    return logger


def scoped_url(base_url: str, path: str, query: dict[str, str]) -> str:
    merged = {key: query.get(key, "") for key in QUERY_KEYS}
    return f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(merged)}"


def http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 20.0):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def listening_pids(port: int) -> list[str]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    stdout = result.stdout.decode("utf-8", errors="ignore") if isinstance(result.stdout, bytes) else (result.stdout or "")
    pids: list[str] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(f":{port}") or parts[3].upper() != "LISTENING":
            continue
        pid = parts[4]
        if pid.isdigit() and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def wait_port_free(port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listening_pids(port):
            return True
        time.sleep(0.5)
    return False


def start_runtime(logger: logging.Logger, base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    port = parsed.port or DEFAULT_PORT
    logger.info("restarting run.py (port %s)", port)
    try:
        for pid in listening_pids(port):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        wait_port_free(port, timeout=8)
        out_log = REPO_ROOT / "web-testnet-46630.watch.out.log"
        err_log = REPO_ROOT / "web-testnet-46630.watch.err.log"
        python_exe = Path(sys.executable)
        stdout = out_log.open("a", encoding="utf-8", errors="replace")
        stderr = err_log.open("a", encoding="utf-8", errors="replace")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(python_exe), str(RUN_PY), "--port", str(port)],
            cwd=str(REPO_ROOT),
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        return True
    except Exception as exc:
        logger.exception("failed to restart run.py: %s", exc)
        return False


def fetch_snapshot(base_url: str, query: dict[str, str]) -> dict | None:
    url = scoped_url(base_url, "/api/dex/opportunity-scanner/snapshot", query)
    try:
        return http_json(url, timeout=50)
    except Exception as exc:
        return None


def discover_base_url(preferred: int, query: dict[str, str], limit: int = 15) -> str:
    for offset in range(limit):
        port = preferred + offset
        candidate = f"http://127.0.0.1:{port}"
        if fetch_snapshot(candidate, query) is not None:
            return candidate
    return f"http://127.0.0.1:{preferred}"


def ensure_auto_execute(base_url: str, query: dict[str, str], logger: logging.Logger, snapshot: dict | None = None) -> bool:
    data = snapshot if snapshot is not None else fetch_snapshot(base_url, query)
    if data is None:
        return False
    scanner = data.get("scanner") or {}
    if not scanner.get("auto_execute_enabled"):
        logger.info("auto-execute was off; re-enabling")
        post_url = scoped_url(base_url, "/api/dex/opportunity-scanner/auto-execute", query)
        try:
            http_json(post_url, method="POST", payload={"enabled": True}, timeout=25)
            return True
        except Exception as exc:
            logger.warning("failed to enable auto-execute: %s", exc)
            return False
    return True


def ensure_scanner_running(base_url: str, query: dict[str, str], logger: logging.Logger, snapshot: dict | None = None) -> bool:
    data = snapshot if snapshot is not None else fetch_snapshot(base_url, query)
    if data is None:
        return False
    scanner = data.get("scanner") or {}
    if scanner.get("running"):
        return True
    logger.warning("scanner is not running; trying start endpoint")
    start_url = scoped_url(base_url, "/api/dex/opportunity-scanner/start", query)
    try:
        http_json(start_url, method="POST", payload={}, timeout=25)
    except Exception as exc:
        logger.warning("failed to start scanner: %s", exc)
        return False
    time.sleep(2)
    refreshed = fetch_snapshot(base_url, query) or {}
    return bool((refreshed.get("scanner") or {}).get("running"))


def check_submission_errors(base_url: str, query: dict[str, str], state: dict, logger: logging.Logger) -> bool:
    url = scoped_url(base_url, "/api/dex/submissions/failed", query)
    try:
        data = http_json(url, timeout=25)
    except Exception as exc:
        logger.warning("submission failure check failed: %s", exc)
        return False

    state["failed_summary"] = data.get("summary") or {}

    def _is_system_row(row: dict) -> bool:
        category = str(
            row.get("review_category")
            or row.get("category")
            or row.get("status")
            or row.get("state")
            or ""
        ).lower()
        text = str(row.get("error") or row.get("detail") or "").lower()
        if any(marker in category for marker in SYSTEM_MARKERS):
            return True
        if any(marker in text for marker in ("rpc_verification_failed", "rpc_verification_timeout", "broadcast_failed", "broadcast_error", "submission_failed", "execution_failed", "syntaxerror", "typeerror", "unexpected token", "module_not_found", "cannot find module", "stack trace", "child_process")):
            return True
        return False

    def _row_blockers(row: dict) -> list[str]:
        summary = row.get("review_summary") if isinstance(row.get("review_summary"), dict) else {}
        evidence = summary.get("onchain_evidence") if isinstance(summary.get("onchain_evidence"), dict) else {}
        blockers = evidence.get("blockers") or summary.get("blockers") or []
        if not blockers and isinstance(row.get("quote"), dict):
            live = row["quote"].get("live_onchain_evidence")
            if isinstance(live, dict):
                blockers = live.get("blockers") or []
        return [str(item) for item in blockers if str(item).strip()]

    def _fix_hint(row: dict) -> str:
        text = " ".join(_row_blockers(row)).lower()
        text += " " + str(row.get("error") or row.get("detail") or "").lower()
        if "broadcast disabled" in text or "static preflight passed" in text:
            return "check ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST and restart run.py"
        if "rpc_verification_failed" in text or "timeout" in text or "rpc" in text:
            return "check RPC connectivity/latency"
        if "nonce" in text or "replacement transaction underpriced" in text:
            return "check wallet nonce / relayer state"
        if "insufficient funds" in text or "out of gas" in text or "insufficient balance" in text:
            return "check native gas and token balances"
        return "review full attempt record for root cause"

    rows = data.get("attempts") or []
    system_rows = [row for row in rows if isinstance(row, dict) and _is_system_row(row)]
    system_error_count = len(system_rows)
    if not state.get("initialized"):
        state["initialized"] = True
        state["last_system_error_count"] = system_error_count
        if system_rows:
            for row in system_rows[:5]:
                category = row.get("review_category") or row.get("category") or row.get("status") or "-"
                error = row.get("error") or row.get("detail") or "-"
                created = row.get("created_at") or row.get("observed_at") or "-"
                tx_hash = row.get("tx_hash") or "-"
                blockers = _row_blockers(row)
                logger.warning(
                    "submission error row (existing): category=%s tx_hash=%s created=%s error=%s blockers=%s hint=%s",
                    category,
                    tx_hash,
                    created,
                    error,
                    " | ".join(blockers[:5]),
                    _fix_hint(row),
                )
        return False
    previous = int(state.get("last_system_error_count") or 0)
    state["last_system_error_count"] = system_error_count
    if not system_rows:
        return False
    for row in system_rows[:5]:
        category = row.get("review_category") or row.get("category") or row.get("status") or "-"
        error = row.get("error") or row.get("detail") or "-"
        created = row.get("created_at") or row.get("observed_at") or "-"
        tx_hash = row.get("tx_hash") or "-"
        blockers = _row_blockers(row)
        logger.warning(
            "submission error row: category=%s tx_hash=%s created=%s error=%s blockers=%s hint=%s",
            category,
            tx_hash,
            created,
            error,
            " | ".join(blockers[:5]),
            _fix_hint(row),
        )
    if system_error_count > previous:
        logger.warning("new system submission errors detected: %s -> %s", previous, system_error_count)
        return True
    return False


def run_watch(args: argparse.Namespace, logger: logging.Logger) -> None:
    query = {
        "chain_id": args.chain_id,
        "market_id": args.market_id,
        "protocols": args.protocols,
        "dex_strategy": args.dex_strategy,
    }
    state = {"last_system_error_count": 0, "last_restart_at": 0.0, "tick_count": 0}
    cooldown = max(0.0, float(getattr(args, "cooldown", 60.0)))
    while True:
        state["tick_count"] += 1
        healthy = True
        failure_reasons = []
        snapshot = fetch_snapshot(args.base_url, query)
        if snapshot is None:
            healthy = False
            failure_reasons.append("snapshot endpoint unreachable")
        else:
            scanner = snapshot.get("scanner") or {}
            if not ensure_scanner_running(args.base_url, query, logger, snapshot):
                healthy = False
                failure_reasons.append("scanner not running")
            if not ensure_auto_execute(args.base_url, query, logger, snapshot):
                healthy = False
                failure_reasons.append("auto-execute could not be re-enabled")
            if state["tick_count"] % 2 == 0:
                failed_summary = state.get("failed_summary") or {}
                logger.info(
                    "heartbeat auto_execute=%s running=%s last_error=%s scan_count=%s review_count=%s failed=%s blocked=%s",
                    scanner.get("auto_execute_enabled"),
                    scanner.get("running"),
                    scanner.get("last_error") or "",
                    scanner.get("closed_loop_scan_count"),
                    scanner.get("closed_loop_review_count"),
                    failed_summary.get("failedCount") or failed_summary.get("systemErrorCount") or 0,
                    failed_summary.get("businessBlockedCount") or 0,
                )
        if check_submission_errors(args.base_url, query, state, logger):
            healthy = False
            failure_reasons.append("new system submission errors")
        if healthy:
            state["last_restart_at"] = 0.0
        else:
            now = time.monotonic()
            if now - state["last_restart_at"] >= cooldown:
                logger.warning("unhealthy check: %s", ", ".join(failure_reasons) or "unknown")
                start_runtime(logger, args.base_url)
                state["last_restart_at"] = now
                if args.autodetect:
                    parsed = urllib.parse.urlparse(args.base_url)
                    preferred = parsed.port or DEFAULT_PORT
                    args.base_url = discover_base_url(preferred, query)
            else:
                logger.warning("unhealthy check but restart is in cooldown: %s", ", ".join(failure_reasons) or "unknown")
            time.sleep(args.restart_wait)
        time.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="DEX runtime watcher")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")
    parser.add_argument("--chain-id", default="46630")
    parser.add_argument("--market-id", default="dex-arbitrage-46630-uniswap-v4")
    parser.add_argument("--protocols", default="uniswap_v4")
    parser.add_argument("--dex-strategy", default="closed_loop_routes")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--restart-wait", type=float, default=10.0)
    parser.add_argument("--cooldown", type=float, default=60.0)
    parser.add_argument("--autodetect", action="store_true", default=True)
    parser.add_argument("--no-autodetect", dest="autodetect", action="store_false")
    parser.add_argument("--log", default=str(REPO_ROOT / "dex_runtime_watch.log"))
    args = parser.parse_args()
    if args.autodetect:
        parsed = urllib.parse.urlparse(args.base_url)
        preferred = parsed.port or DEFAULT_PORT
        discovered = discover_base_url(preferred, {
            "chain_id": args.chain_id,
            "market_id": args.market_id,
            "protocols": args.protocols,
            "dex_strategy": args.dex_strategy,
        })
        if discovered != args.base_url:
            logger = log_setup(Path(args.log))
            logger.info("auto-detected service url: %s -> %s", args.base_url, discovered)
            args.base_url = discovered
    logger = log_setup(Path(args.log))
    logger.info("dex runtime watcher started: base_url=%s chain_id=%s", args.base_url, args.chain_id)
    try:
        run_watch(args, logger)
    except KeyboardInterrupt:
        logger.info("dex runtime watcher stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
