from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_ACCOUNTS = [
    "0x5D96768D0D551C1b2CE7CFC9a5293c24a6C8229E",
    "0x5831Fb2AFCD7a79831Eb5f49929dC95046e959e2",
]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _print_record(prefix: str, data: dict[str, Any]) -> None:
    parts = [f"{key}={value}" for key, value in data.items()]
    print(f"{prefix} " + " ".join(parts), flush=True)


def _account_state(client, account: str, *, realtime: bool = False) -> tuple[dict[str, Any], dict[str, Any], int, dict[str, Any] | None]:
    cached = client.get(f"/api/liquidation/account/cached?account={account}").get_json() or {}
    realtime_report = None
    if realtime:
        realtime_response = client.get(f"/api/liquidation/account?account={account}")
        realtime_report = realtime_response.get_json() or {"error": f"HTTP {realtime_response.status_code}"}
        summary = realtime_report.get("summary") if isinstance(realtime_report, dict) else {}
        summary = summary or {}
        candidates = realtime_report.get("liquidation_candidates") if isinstance(realtime_report, dict) else []
        recommended = realtime_report.get("recommended_candidate") if isinstance(realtime_report, dict) else None
        health_factor = _float_or_none(summary.get("health_factor"))
        if health_factor is not None and health_factor >= 1:
            return cached, {
                "reason_code": "ACCOUNT_NOT_LIQUIDATABLE",
                "blocked_reasons": ["account_not_liquidatable"],
                "error": f"health_factor={health_factor:.18g} is not below 1.0",
            }, 400, realtime_report
        if not candidates and not recommended:
            return cached, {
                "reason_code": "NO_EXECUTABLE_CANDIDATE",
                "blocked_reasons": ["no_liquidation_candidate"],
                "error": "realtime report has no executable debt/collateral candidate",
            }, 400, realtime_report
    payload_path = f"/api/liquidation/account/payload?account={account}"
    if not realtime:
        payload_path += "&fast=1"
    payload_response = client.get(payload_path)
    payload = payload_response.get_json() or {}
    return cached, payload, int(payload_response.status_code), realtime_report


def _watch_account_once(app, account: str, *, hf_stop: float, realtime: bool) -> str | None:
    client = app.test_client()
    cached, payload, payload_status, realtime_report = _account_state(client, account, realtime=realtime)
    realtime_summary = (realtime_report or {}).get("summary") if isinstance(realtime_report, dict) else {}
    summary = realtime_summary or cached.get("summary") or {}
    core = ((cached.get("context") or {}).get("core_opportunity") or {})
    hf = _float_or_none(summary.get("health_factor"))
    candidate_count = len((realtime_report or {}).get("liquidation_candidates") or cached.get("liquidation_candidates") or [])
    recommended_candidate = bool((realtime_report or {}).get("recommended_candidate") or cached.get("recommended_candidate"))
    record = {
        "account": account,
        "found": cached.get("found"),
        "source": "realtime" if realtime_report else "cached",
        "hf": hf,
        "status": summary.get("status"),
        "core_rank": core.get("rank"),
        "candidate_count": candidate_count,
        "recommended_candidate": recommended_candidate,
        "net_profit_u": core.get("estimated_operator_net_profit_usd"),
        "payload_http": payload_status,
        "reason_code": payload.get("reason_code"),
        "blocked_reasons": ",".join(payload.get("blocked_reasons") or []),
        "submission_allowed": payload.get("submission_allowed"),
    }
    _print_record("account", record)
    if realtime_report and realtime_report.get("error"):
        _print_record("realtime_error", {"account": account, "error": realtime_report.get("error")})

    if cached.get("found") is False:
        _print_record("terminal", {"account": account, "reason": "not_found"})
        return "not_found"
    if hf is not None and hf > hf_stop:
        reason = f"hf_above_{hf_stop}"
        _print_record("terminal", {"account": account, "reason": reason, "hf": hf})
        return reason
    if payload.get("submission_allowed"):
        static_response = client.post(f"/api/liquidation/account/{account}/static-call-and-save", json={})
        static_data = static_response.get_json() or {}
        preflight = static_data.get("preflight") or {}
        _print_record(
            "static_call",
            {
                "account": account,
                "http": static_response.status_code,
                "status": preflight.get("static_call_status"),
                "passed": preflight.get("static_call_passed"),
                "error": preflight.get("static_call_error") or static_data.get("error"),
            },
        )
        reason = "ready_for_manual_broadcast" if preflight.get("static_call_passed") else "static_call_failed"
        _print_record("terminal", {"account": account, "reason": reason})
        return reason
    return None


def watch_accounts(accounts: list[str], *, interval_seconds: float, hf_stop: float, max_rounds: int, realtime: bool = False) -> int:
    load_dotenv(".env")
    from web.control_panel import app
    from web import control_panel

    controls = control_panel.liquidation_execution_controls()
    _print_record(
        "controls",
        {
            "execution_enabled": controls.get("execution_enabled"),
            "require_static_call": controls.get("require_static_call"),
            "flashloan_executor_configured": controls.get("flashloan_executor_configured"),
            "owner_configured": controls.get("owner_configured"),
            "private_key_configured": controls.get("private_key_configured"),
        },
    )

    terminal: dict[str, str] = {}
    round_no = 0
    while len(terminal) < len(accounts):
        round_no += 1
        print(f"round={round_no} at={datetime.now().isoformat(timespec='seconds')}", flush=True)
        active_accounts = [account for account in accounts if account not in terminal]
        if realtime and len(active_accounts) > 1:
            with ThreadPoolExecutor(max_workers=len(active_accounts)) as executor:
                futures = {
                    executor.submit(_watch_account_once, app, account, hf_stop=hf_stop, realtime=realtime): account
                    for account in active_accounts
                }
                for future in as_completed(futures):
                    account = futures[future]
                    reason = future.result()
                    if reason:
                        terminal[account] = reason
        else:
            for account in active_accounts:
                reason = _watch_account_once(app, account, hf_stop=hf_stop, realtime=realtime)
                if reason:
                    terminal[account] = reason

        if len(terminal) >= len(accounts):
            break
        if max_rounds > 0 and round_no >= max_rounds:
            _print_record("terminal", {"reason": "max_rounds_reached", "open_accounts": len(accounts) - len(terminal)})
            return 0
        time.sleep(max(1.0, float(interval_seconds)))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch only the configured target liquidation accounts.")
    parser.add_argument("--account", action="append", dest="accounts", help="Target account. Can be passed more than once.")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds.")
    parser.add_argument("--hf-stop", type=float, default=1.01, help="Stop watching an account once HF is above this value.")
    parser.add_argument("--max-rounds", type=int, default=0, help="Maximum rounds; 0 means keep watching.")
    parser.add_argument("--realtime", action="store_true", help="Refresh each target account from chain before judging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    accounts = list(dict.fromkeys(args.accounts or DEFAULT_ACCOUNTS))
    return watch_accounts(accounts, interval_seconds=args.interval, hf_stop=args.hf_stop, max_rounds=args.max_rounds, realtime=bool(args.realtime))


if __name__ == "__main__":
    raise SystemExit(main())
