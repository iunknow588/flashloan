from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR, utc_now
from market.tools.audit_bnb_venus_file_state import audit_file_state, write_audit_report


LATEST_STATUS = "venus_account_scan_status_latest.json"
HISTORY_STATUS = "venus_account_scan_status_history.jsonl"
MARKDOWN_STATUS = "venus_account_scan_status.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    except Exception:
        return 0


def _file_age_seconds(path: Path, now: float) -> float | None:
    if not path.exists():
        return None
    try:
        return max(0.0, now - path.stat().st_mtime)
    except Exception:
        return None


def _process_alive(pid: int | None) -> bool | None:
    if not pid:
        return None
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return None
        return result.returncode == 0 and bool(result.stdout.strip())
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None
    return True


def _status_from_inputs(
    *,
    process_alive: bool | None,
    market_progress: dict[str, Any],
    borrow_progress: dict[str, Any],
    registry: dict[str, Any],
    audit: dict[str, Any],
    progress_age_seconds: float | None,
    stale_after_seconds: float,
) -> str:
    if audit.get("errors"):
        return "error"
    if market_progress.get("status") == "completed" and bool(registry.get("scan_complete")):
        return "completed"
    if borrow_progress.get("error"):
        if "retrying" in str(borrow_progress.get("error") or "").lower() or borrow_progress.get("retry_attempt"):
            return "rpc_retrying"
        return "rpc_blocked"
    if progress_age_seconds is not None and progress_age_seconds > stale_after_seconds:
        return "stalled"
    if process_alive is True:
        return "running"
    if process_alive is False:
        return "not_running"
    return "unknown"


def build_scan_status(output_dir: Path, *, pid: int | None = None, stale_after_seconds: float = 300.0) -> dict[str, Any]:
    now = time.time()
    registry_path = output_dir / "venus_borrower_registry.json"
    market_progress_path = output_dir / "venus_two_month_market_scan_progress.json"
    borrow_progress_path = output_dir / "venus_borrow_scan_progress.json"
    events_path = output_dir / "venus_borrower_events_2mo.jsonl"

    registry = _load_json(registry_path)
    market_progress = _load_json(market_progress_path)
    borrow_progress = _load_json(borrow_progress_path)
    audit = audit_file_state(output_dir)
    audit_paths = write_audit_report(output_dir, audit)
    process_alive = _process_alive(pid)
    progress_age_seconds = _file_age_seconds(borrow_progress_path, now)
    registry_age_seconds = _file_age_seconds(registry_path, now)
    event_count = _line_count(events_path)
    account_count = int(registry.get("account_count") or 0)
    status = _status_from_inputs(
        process_alive=process_alive,
        market_progress=market_progress,
        borrow_progress=borrow_progress,
        registry=registry,
        audit=audit,
        progress_age_seconds=progress_age_seconds,
        stale_after_seconds=stale_after_seconds,
    )
    return {
        "generated_at": utc_now(),
        "status": status,
        "ok": status not in {"error", "rpc_blocked", "stalled", "not_running"},
        "process": {"pid": pid, "alive": process_alive},
        "registry": {
            "path": str(registry_path),
            "account_count": account_count,
            "event_count": int(registry.get("event_count") or event_count),
            "scan_complete": bool(registry.get("scan_complete", False)),
            "scan_mode": registry.get("scan_mode"),
            "discovery_mode": registry.get("discovery_mode"),
            "from_block": registry.get("from_block"),
            "to_block": registry.get("to_block"),
            "age_seconds": registry_age_seconds,
        },
        "events": {"path": str(events_path), "line_count": event_count},
        "market_progress": {
            "path": str(market_progress_path),
            "status": market_progress.get("status"),
            "market_index": market_progress.get("market_index"),
            "market_count": market_progress.get("market_count"),
            "selected_market_count": market_progress.get("selected_market_count"),
            "vtoken": market_progress.get("vtoken"),
            "vtoken_count": market_progress.get("vtoken_count"),
            "market_end_index": market_progress.get("market_end_index"),
            "from_block": market_progress.get("from_block"),
            "to_block": market_progress.get("to_block"),
            "recent_blocks": market_progress.get("recent_blocks"),
            "rpc_urls": market_progress.get("rpc_urls") or [],
        },
        "borrow_progress": {
            "path": str(borrow_progress_path),
            "vtoken": borrow_progress.get("vtoken"),
            "event": borrow_progress.get("event"),
            "current_from_block": borrow_progress.get("current_from_block"),
            "current_to_block": borrow_progress.get("current_to_block"),
            "from_block": borrow_progress.get("from_block"),
            "to_block": borrow_progress.get("to_block"),
            "persisted_event_count": borrow_progress.get("persisted_event_count"),
            "age_seconds": progress_age_seconds,
            "error": borrow_progress.get("error"),
            "rpc_failures": borrow_progress.get("rpc_failures") or [],
            "retry_attempt": borrow_progress.get("retry_attempt"),
            "retry_after_seconds": borrow_progress.get("retry_after_seconds"),
        },
        "audit": {
            "status": audit.get("status"),
            "ok": audit.get("ok"),
            "warnings": audit.get("warnings") or [],
            "errors": audit.get("errors") or [],
            "paths": audit_paths,
        },
    }


def _write_markdown(path: Path, status: dict[str, Any]) -> None:
    registry = status.get("registry") or {}
    market = status.get("market_progress") or {}
    borrow = status.get("borrow_progress") or {}
    process = status.get("process") or {}
    lines = [
        "# BNB Venus Account Scan Status",
        "",
        f"- Updated: `{status.get('generated_at')}`",
        f"- Status: `{status.get('status')}`",
        f"- PID: `{process.get('pid') or ''}`",
        f"- Process alive: `{process.get('alive')}`",
        f"- Accounts: `{registry.get('account_count', 0)}`",
        f"- Events: `{registry.get('event_count', 0)}`",
        f"- Registry complete: `{registry.get('scan_complete')}`",
        f"- Market: `{market.get('market_index') or ''}/{market.get('market_count') or ''}`",
        f"- Current vToken: `{market.get('vtoken') or borrow.get('vtoken') or ''}`",
        f"- Current block range: `{borrow.get('current_from_block') or ''}-{borrow.get('current_to_block') or ''}`",
        f"- Progress age seconds: `{borrow.get('age_seconds')}`",
    ]
    if borrow.get("error"):
        lines.extend(["", "## Current Error", "", f"`{borrow.get('error')}`"])
    warnings = (status.get("audit") or {}).get("warnings") or []
    if warnings:
        lines.extend(["", "## Audit Warnings"])
        lines.extend(f"- `{item}`" for item in warnings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_scan_status(output_dir: Path, status: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / LATEST_STATUS
    history_path = output_dir / HISTORY_STATUS
    markdown_path = output_dir / MARKDOWN_STATUS
    latest_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(status, ensure_ascii=False, separators=(",", ":")) + "\n")
    _write_markdown(markdown_path, status)
    return {"latest": str(latest_path), "history": str(history_path), "markdown": str(markdown_path)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track BNB Venus borrower account scan progress from file state.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--stale-after-seconds", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    deadline = time.monotonic() + float(args.duration_seconds) if float(args.duration_seconds or 0) > 0 else None
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, request_stop)
    except (ValueError, OSError):
        previous_sigint = None
    try:
        while not stop_requested:
            status = build_scan_status(args.output_dir, pid=args.pid, stale_after_seconds=args.stale_after_seconds)
            paths = write_scan_status(args.output_dir, status)
            print(json.dumps({"ok": status.get("ok"), "status": status.get("status"), "paths": paths}, ensure_ascii=False), flush=True)
            if args.once:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(max(1.0, float(args.interval_seconds)))
    finally:
        try:
            if previous_sigint is not None:
                signal.signal(signal.SIGINT, previous_sigint)
        except (ValueError, OSError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
