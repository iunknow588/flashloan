from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from web3 import Web3

logger = logging.getLogger(__name__)


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from market.tools.audit_bnb_venus_file_state import audit_file_state, write_audit_report
from bot.execution.bnb_exit_quote import attach_exit_quotes_to_candidates
from bot.execution.bnb_runtime import connect_bnb_rpc_with_retries, read_bnb_runtime_metrics
from bot.execution.liquidation_priority import liquidation_pool_tier
from bot.execution.venus_abis import VENUS_CORE_COMPTROLLER_ADDRESS
from bot.execution.venus_accounts import empty_venus_registry, validate_venus_borrower_registry
from bot.execution.venus_liquidation_scan import VenusScanConfig, build_venus_user_report, summarize_venus_reports
from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR, utc_now
from dex.intent_trade.chains.bnb import config as bnb
from bot.tools.rebuild_bnb_venus_borrowers import main as rebuild_borrowers_main


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_accounts(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw_accounts: list[str] = []
    if path.suffix.lower() == ".json":
        data = _load_json(path, {})
        if isinstance(data, dict):
            rows = data.get("accounts") or []
            if rows and isinstance(rows[0], dict):
                raw_accounts = [str(row["account"]) for row in rows if row.get("account")]
            else:
                raw_accounts = [str(item) for item in rows]
        elif isinstance(data, list):
            raw_accounts = [str(item.get("account") if isinstance(item, dict) else item) for item in data]
    else:
        raw_accounts = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    accounts: list[str] = []
    for value in raw_accounts:
        try:
            account = Web3.to_checksum_address(value)
        except Exception:
            continue
        if account not in accounts:
            accounts.append(account)
    return accounts


def _latest_state_path(output_dir: Path) -> Path:
    return output_dir / "venus_watcher_state.json"


def _current_health_pool_path(output_dir: Path) -> Path:
    return output_dir / "venus_liquidation_current.json"


def _write_current_health_pool(output_dir: Path, current_pool: dict[str, Any]) -> Path:
    path = _current_health_pool_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(current_pool, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return path


def _load_state(output_dir: Path) -> dict[str, Any]:
    path = _latest_state_path(output_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _checksum_account(value: object) -> str:
    try:
        return Web3.to_checksum_address(str(value or "").strip())
    except Exception:
        return ""


def _load_current_health_pool(output_dir: Path) -> dict[str, Any]:
    payload = _load_json(_current_health_pool_path(output_dir), {})
    return payload if isinstance(payload, dict) else {}


def _pending_health_report(account: str, registry_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": account,
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "account_tier": registry_row.get("tier") or "ordinary",
        "summary": {
            "status": "pending",
            "health_factor": None,
            "health_factor_band": None,
            "health_factor_source": "pending_scan",
            "total_collateral_value_usd": None,
            "total_debt_value_usd": None,
        },
        "risk": {"positions": []},
        "liquidation_candidates": [],
        "recommended_candidate": None,
        "execution_enabled": False,
    }


def _report_health_factor(report: dict[str, Any]) -> float | None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    value = summary.get("health_factor")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_health_pools(accounts: list[str], registry: dict[str, Any], current_pool: dict[str, Any]) -> dict[str, list[str]]:
    registry_rows = {
        _checksum_account(row.get("account")): row
        for row in (registry.get("accounts") or [])
        if isinstance(row, dict) and _checksum_account(row.get("account"))
    }
    reports = {
        _checksum_account(report.get("account")): report
        for report in (current_pool.get("reports") or [])
        if isinstance(report, dict) and _checksum_account(report.get("account"))
    }
    pools = {"core_opportunity": [], "high_frequency": [], "borrow_health": []}
    for account in accounts:
        report = reports.get(account, {})
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        health_factor = _report_health_factor(report)
        tier = liquidation_pool_tier(health_factor)
        if tier == "core":
            pools["core_opportunity"].append(account)
        elif tier == "high_frequency":
            pools["high_frequency"].append(account)
        else:
            pools["borrow_health"].append(account)
    return pools


def _merge_current_health_pool(
    output_dir: Path,
    round_payload: dict[str, Any],
    registry: dict[str, Any],
    *,
    scanned_pools: list[str],
) -> dict[str, Any]:
    previous = _load_current_health_pool(output_dir)
    previous_reports = {
        _checksum_account(report.get("account")): dict(report)
        for report in (previous.get("reports") or [])
        if isinstance(report, dict) and _checksum_account(report.get("account"))
    }
    updated_reports = {
        _checksum_account(report.get("account")): dict(report)
        for report in (round_payload.get("reports") or [])
        if isinstance(report, dict) and _checksum_account(report.get("account"))
    }
    registry_rows = {
        _checksum_account(row.get("account")): dict(row)
        for row in (registry.get("accounts") or [])
        if isinstance(row, dict) and _checksum_account(row.get("account"))
    }
    generated_at = str(round_payload.get("generated_at") or utc_now())
    merged_reports: list[dict[str, Any]] = []
    scanned_count = 0
    for account, registry_row in registry_rows.items():
        report = updated_reports.get(account) or previous_reports.get(account) or _pending_health_report(account, registry_row)
        report["account"] = account
        report["account_tier"] = registry_row.get("tier") or "ordinary"
        if account in updated_reports:
            report["observed_at"] = generated_at
            report["scan_pool"] = ",".join(scanned_pools)
            scanned_count += 1
        merged_reports.append(report)
    merged_reports.sort(key=lambda item: item.get("account") or "")
    scanned_reports = [
        report
        for report in merged_reports
        if str((report.get("summary") or {}).get("status") or "").lower() != "pending"
    ]
    summary = summarize_venus_reports(scanned_reports)
    summary.update(
        {
            "account_count": len(merged_reports),
            "scanned_account_count": len(scanned_reports),
            "pending_account_count": len(merged_reports) - len(scanned_reports),
            "last_round_scanned_count": scanned_count,
        }
    )
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "storage_mode": "file_only",
        "generated_at": generated_at,
        "last_round": round_payload.get("round"),
        "last_block": round_payload.get("block_number"),
        "last_round_scanned_pools": list(scanned_pools),
        "registry_snapshot": {
            "account_count": len(merged_reports),
            "scan_complete": registry.get("scan_complete"),
            "discovery_mode": registry.get("discovery_mode"),
            "storage_mode": registry.get("storage_mode"),
        },
        "summary": summary,
        "reports": merged_reports,
    }


def _events_jsonl_for_startup_refresh(output_dir: Path, accounts_file: Path) -> Path:
    if accounts_file.suffix.lower() == ".json":
        registry = _load_json(accounts_file, {})
        if isinstance(registry, dict) and registry.get("events_jsonl"):
            path = Path(str(registry["events_jsonl"]))
            if path.is_absolute():
                return path
            local_path = output_dir / path.name
            if local_path.exists() or not path.exists():
                return local_path
            return path
    return output_dir / "venus_borrower_events_2mo.jsonl"


def _refresh_accounts_on_start(watch_args: argparse.Namespace) -> dict[str, Any]:
    events_jsonl = _events_jsonl_for_startup_refresh(watch_args.output_dir, watch_args.accounts_file)
    child_args = [
        "--output-dir",
        str(watch_args.output_dir),
        "--market-table",
        str(watch_args.market_table),
        "--incremental",
        "--events-jsonl",
        str(events_jsonl),
        "--progress-file",
        str(watch_args.output_dir / "venus_startup_account_refresh_progress.json"),
        "--chunk-size",
        str(watch_args.account_refresh_chunk_size),
        "--min-chunk-size",
        str(watch_args.account_refresh_min_chunk_size),
        "--address-batch-size",
        str(watch_args.account_refresh_address_batch_size),
        "--event",
        "Borrow",
    ]
    for url in watch_args.rpc_url:
        child_args.extend(["--rpc-url", url])
    if watch_args.account_refresh_adaptive_chunk:
        child_args.append("--adaptive-chunk")
    if watch_args.account_refresh_newest_first:
        child_args.append("--newest-first")
    if watch_args.account_refresh_continue_on_error:
        child_args.append("--continue-on-error")
    try:
        code = rebuild_borrowers_main(child_args)
    except SystemExit as exc:
        return {"ok": False, "exit_code": int(exc.code or 1) if isinstance(exc.code, int) else 1, "error": str(exc), "events_jsonl": str(events_jsonl)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events_jsonl": str(events_jsonl)}
    return {"ok": code == 0, "exit_code": int(code), "events_jsonl": str(events_jsonl)}


def _split_accounts_by_tier(accounts: list[str], registry: dict[str, Any]) -> dict[str, list[str]]:
    rows = {str(row.get("account") or "").lower(): row for row in (registry.get("accounts") or []) if row.get("account")}
    buckets = {"hot": [], "warm": [], "cold": []}
    for account in accounts:
        row = rows.get(account.lower(), {})
        tier = str(row.get("tier") or "cold").lower()
        if tier not in buckets:
            tier = "cold"
        buckets[tier].append(account)
    return buckets


def _write_outputs(output_dir: Path, round_payload: dict[str, Any], *, current_pool: dict[str, Any] | None = None) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "venus_liquidation_latest.json"
    history_path = output_dir / "venus_liquidation_history.jsonl"
    summary_path = output_dir / "venus_liquidation_summary.md"
    state_path = output_dir / "venus_watcher_state.json"
    current_pool_path = _current_health_pool_path(output_dir)
    latest_path.write_text(json.dumps(round_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(round_payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary = round_payload.get("summary") or {}
    registry_validation = round_payload.get("registry_validation") or {}
    registry_snapshot = round_payload.get("registry_snapshot") or {}
    lines = [
        "# BNB Venus Liquidation Watch",
        "",
        f"- Updated: `{round_payload.get('generated_at')}`",
        f"- Mode: `observation_only`",
        f"- Execution enabled: `{round_payload.get('execution_enabled')}`",
        f"- Accounts scanned: `{summary.get('account_count', 0)}`",
        f"- Registry storage mode: `{registry_snapshot.get('storage_mode') or 'unknown'}`",
        f"- Registry scan complete: `{registry_snapshot.get('scan_complete')}`",
        f"- Registry validation: `{'ok' if registry_validation.get('ok') else 'warning'}`",
        f"- Liquidatable: `{summary.get('liquidatable_accounts', 0)}`",
        f"- Warning: `{summary.get('warning_accounts', 0)}`",
        f"- Candidates: `{summary.get('candidate_count', 0)}`",
        f"- Quote success: `{round_payload.get('quote_success_count', 0)}`",
        f"- Quote failure: `{round_payload.get('quote_failure_count', 0)}`",
        f"- Positive net profit: `{summary.get('positive_net_profit_count', 0)}`",
        f"- Negative net profit: `{summary.get('negative_net_profit_count', 0)}`",
        f"- Round duration ms: `{round_payload.get('round_duration_ms', 0)}`",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    state_path.write_text(json.dumps(round_payload.get("state") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    if current_pool is not None:
        _write_current_health_pool(output_dir, current_pool)
    audit_result = audit_file_state(output_dir)
    audit_paths = write_audit_report(output_dir, audit_result)
    return {
        "latest": str(latest_path),
        "history": str(history_path),
        "summary": str(summary_path),
        "state": str(state_path),
        "current": str(current_pool_path),
        "audit_json": audit_paths["json"],
        "audit_md": audit_paths["markdown"],
    }


def _archive_existing_outputs(output_dir: Path, *, label: str | None = None) -> dict[str, str]:
    stamp = label or utc_now().replace(":", "").replace("-", "").replace("Z", "")
    try:
        data_root = output_dir.parents[1]
    except IndexError:
        data_root = output_dir.parent
    archive_dir = data_root / "back" / "BNB" / "liquidation" / stamp
    archived: dict[str, str] = {}
    for name in (
        "venus_liquidation_latest.json",
        "venus_liquidation_history.jsonl",
        "venus_liquidation_summary.md",
        "venus_watcher_state.json",
    ):
        source = output_dir / name
        if not source.exists():
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / name
        shutil.move(str(source), str(target))
        archived[name] = str(target)
    return archived


def _scan_round(
    w3,
    args: argparse.Namespace,
    accounts: list[str],
    market_table: dict[str, Any],
    round_no: int,
    *,
    on_report: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = VenusScanConfig(
        warning_liquidity_raw=args.warning_liquidity_raw,
        min_repay_raw=args.min_repay_raw,
        min_seize_raw=args.min_seize_raw,
        max_candidates=args.max_candidates,
        flashloan_fee_percent=args.flashloan_fee_percent,
        dex_swap_fee_percent=args.dex_swap_fee_percent,
        dex_slippage_percent=args.dex_slippage_percent,
        gas_cost_usd=args.gas_cost_usd,
        mev_buffer_usd=args.mev_buffer_usd,
        retry_buffer_usd=args.retry_buffer_usd,
        min_operator_net_profit_usd=args.min_operator_net_profit_usd,
        flashloan_supported_debt_assets=tuple(args.flashloan_supported_token or []),
    )
    reports = []
    quote_success = 0
    quote_failure = 0
    rejected_reasons: dict[str, int] = {}
    error_count = 0
    for account in accounts:
        try:
            report = build_venus_user_report(w3, args.comptroller, account, market_table, config=config)
            quoted_candidates = attach_exit_quotes_to_candidates(w3, report.get("liquidation_candidates") or [], router_address=args.router)
            report["liquidation_candidates"] = quoted_candidates
            report["recommended_candidate"] = next((row for row in quoted_candidates if row.get("status") == "candidate"), None)
            quote_success += sum(1 for row in quoted_candidates if (row.get("exit_quote") or {}).get("best_quote"))
            quote_failure += sum(1 for row in quoted_candidates if row.get("status") == "rejected" and row.get("rejected_reason") == "exit_quote_failed")
            for row in quoted_candidates:
                reason = row.get("rejected_reason")
                if reason:
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
        except Exception as exc:
            error_count += 1
            report = {
                "account": account,
                "network": "bnb",
                "chain_id": 56,
                "protocol": "venus_core",
                "summary": {"status": "error"},
                "error": str(exc),
                "liquidation_candidates": [],
                "recommended_candidate": None,
                "execution_enabled": False,
            }
        reports.append(report)
        if on_report is not None:
            on_report(report)
    summary = summarize_venus_reports(reports)
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": args.protocol,
        "storage_mode": "file_only",
        "generated_at": utc_now(),
        "round": int(round_no),
        "block_number": int(getattr(w3.eth, "block_number", 0) or 0),
        "mode": "observation_only",
        "execution_enabled": False,
        "summary": summary,
        "quote_success_count": quote_success,
        "quote_failure_count": quote_failure,
        "rejected_reason_counts": dict(sorted(rejected_reasons.items())),
        "error_count": error_count,
        "runtime_metrics": read_bnb_runtime_metrics(w3, router_address=args.router),
        "reports": reports,
        "state": {
            "last_round": int(round_no),
            "last_block": int(getattr(w3.eth, "block_number", 0) or 0),
            "last_generated_at": utc_now(),
            "account_count": len(accounts),
        },
        "round_duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch BNB Venus Core liquidations in observation mode.")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--core-account-interval", "--hot-account-interval", dest="core_account_interval", type=float, default=1.0)
    parser.add_argument("--high-frequency-account-interval", "--warm-account-interval", dest="high_frequency_account_interval", type=float, default=300.0)
    parser.add_argument("--borrow-health-account-interval", "--cold-account-interval", dest="borrow_health_account_interval", type=float, default=1800.0)
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--rpc-retries", type=int, default=3)
    parser.add_argument("--rpc-backoff-seconds", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--archive-existing", action="store_true")
    parser.add_argument("--protocol", default="venus_core", choices=["venus_core"])
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_borrower_registry.json")
    parser.add_argument("--market-table", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_market_static_table.json")
    parser.add_argument("--comptroller", default=VENUS_CORE_COMPTROLLER_ADDRESS)
    parser.add_argument("--router", default="0x10ED43C718714eb63d5aA57B78B54704E256024E")
    parser.add_argument("--warning-liquidity-raw", type=int, default=10**20)
    parser.add_argument("--min-repay-raw", type=int, default=1)
    parser.add_argument("--min-seize-raw", type=int, default=1)
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--flashloan-fee-percent", type=float, default=0.05)
    parser.add_argument("--dex-swap-fee-percent", type=float, default=0.25)
    parser.add_argument("--dex-slippage-percent", type=float, default=0.10)
    parser.add_argument("--gas-cost-usd", type=float, default=0.0)
    parser.add_argument("--mev-buffer-usd", type=float, default=0.0)
    parser.add_argument("--retry-buffer-usd", type=float, default=0.0)
    parser.add_argument("--min-operator-net-profit-usd", type=float, default=0.0)
    parser.add_argument(
        "--flashloan-supported-token",
        action="append",
        default=_csv_env("BNB_VENUS_FLASHLOAN_SUPPORTED_TOKENS"),
        help="Restrict executable Venus candidates to these flashloan-supported debt symbols or addresses.",
    )
    parser.add_argument("--disable-startup-account-refresh", action="store_true")
    parser.add_argument("--account-refresh-chunk-size", type=int, default=5_000)
    parser.add_argument("--account-refresh-min-chunk-size", type=int, default=100)
    parser.add_argument("--account-refresh-address-batch-size", type=int, default=8)
    parser.add_argument("--no-account-refresh-adaptive-chunk", dest="account_refresh_adaptive_chunk", action="store_false")
    parser.add_argument("--no-account-refresh-newest-first", dest="account_refresh_newest_first", action="store_false")
    parser.add_argument("--stop-on-account-refresh-error", dest="account_refresh_continue_on_error", action="store_false")
    parser.set_defaults(
        account_refresh_adaptive_chunk=True,
        account_refresh_newest_first=True,
        account_refresh_continue_on_error=True,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    startup_account_refresh = {"ok": None, "skipped": True}
    if not args.disable_startup_account_refresh:
        startup_account_refresh = _refresh_accounts_on_start(args)
    accounts = _load_accounts(args.accounts_file)
    if not accounts:
        registry = empty_venus_registry()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "venus_borrower_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": False, "error": "no accounts to watch", "registry": str(args.output_dir / "venus_borrower_registry.json")}, ensure_ascii=False, indent=2))
        return 0
    registry = _load_json(args.accounts_file, {}) if args.accounts_file.suffix.lower() == ".json" else {}
    if not isinstance(registry, dict):
        registry = {}
    current_pool = _merge_current_health_pool(
        args.output_dir,
        {"generated_at": utc_now(), "reports": []},
        registry,
        scanned_pools=[],
    )
    _write_current_health_pool(args.output_dir, current_pool)
    market_table = _load_json(args.market_table, {})
    if not market_table.get("markets"):
        raise SystemExit("missing market table; run build_bnb_venus_market_static_table.py first")
    archived_outputs = _archive_existing_outputs(args.output_dir) if args.archive_existing else {}
    w3, rpc_url, rpc_connect = connect_bnb_rpc_with_retries(
        args.rpc_url or list(bnb.rpc_urls_from_env()),
        attempts=args.rpc_retries,
        backoff_seconds=args.rpc_backoff_seconds,
    )
    deadline = time.monotonic() + max(0.0, float(args.duration_seconds))
    round_no = 0
    paths = {}
    rpc_switch_count = 0
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, request_stop)
    except (ValueError, OSError):
        previous_sigint = None
    pool_intervals = {
        "core_opportunity": max(1.0, float(args.core_account_interval)),
        "high_frequency": max(1.0, float(args.high_frequency_account_interval)),
        "borrow_health": max(1.0, float(args.borrow_health_account_interval)),
    }
    last_pool_scan: dict[str, float] = {name: 0.0 for name in pool_intervals}
    first_round = True
    try:
        while not stop_requested:
            now = time.monotonic()
            if not first_round and now >= deadline:
                break
            first_round = False
            round_no += 1
            accounts = _load_accounts(args.accounts_file)
            registry = _load_json(args.accounts_file, {}) if args.accounts_file.suffix.lower() == ".json" else {}
            registry_validation = validate_venus_borrower_registry(registry)
            bucketed = _current_health_pools(accounts, registry if isinstance(registry, dict) else {}, current_pool)
            selected_accounts: list[str] = []
            selected_pools: list[str] = []
            for pool_name in ("core_opportunity", "high_frequency", "borrow_health"):
                if bucketed[pool_name] and now - last_pool_scan[pool_name] >= pool_intervals[pool_name]:
                    selected_pools.append(pool_name)
                    selected_accounts.extend(bucketed[pool_name])
            if not selected_accounts:
                sleep_for = min(
                    max(0.1, pool_intervals[pool_name] - (now - last_pool_scan[pool_name]))
                    for pool_name in ("core_opportunity", "high_frequency", "borrow_health")
                    if bucketed[pool_name]
                )
                time.sleep(min(max(0.1, float(args.interval_seconds)), sleep_for))
                continue
            def persist_current_report(report: dict[str, Any]) -> None:
                nonlocal current_pool
                current_pool = _merge_current_health_pool(
                    args.output_dir,
                    {
                        "round": round_no,
                        "generated_at": utc_now(),
                        "reports": [report],
                    },
                    registry if isinstance(registry, dict) else {},
                    scanned_pools=selected_pools,
                )
                _write_current_health_pool(args.output_dir, current_pool)

            payload = _scan_round(
                w3,
                args,
                list(dict.fromkeys(selected_accounts)),
                market_table,
                round_no,
                on_report=persist_current_report,
            )
            payload["rpc_url"] = rpc_url
            payload["rpc_connect"] = rpc_connect
            payload["rpc_switch_count"] = rpc_switch_count
            payload["scanned_tiers"] = selected_pools
            payload["archived_outputs"] = archived_outputs
            payload["registry_validation"] = registry_validation
            payload["startup_account_refresh"] = startup_account_refresh
            payload["registry_snapshot"] = {
                "path": str(args.accounts_file),
                "account_count": len(accounts),
                "scan_complete": registry_validation.get("scan_complete"),
                "storage_mode": registry_validation.get("storage_mode"),
                "discovery_mode": registry_validation.get("discovery_mode"),
                "from_block": registry.get("from_block") if isinstance(registry, dict) else None,
                "to_block": registry.get("to_block") if isinstance(registry, dict) else None,
            }
            current_pool = _merge_current_health_pool(
                args.output_dir,
                payload,
                registry if isinstance(registry, dict) else {},
                scanned_pools=selected_pools,
            )
            paths = _write_outputs(args.output_dir, payload, current_pool=current_pool)
            if payload.get("error_count") and int(payload.get("error_count") or 0) >= len(selected_accounts):
                try:
                    next_w3, next_rpc_url, next_connect = connect_bnb_rpc_with_retries(
                        args.rpc_url or list(bnb.rpc_urls_from_env()),
                        attempts=args.rpc_retries,
                        backoff_seconds=args.rpc_backoff_seconds,
                    )
                    if next_rpc_url != rpc_url:
                        rpc_switch_count += 1
                    w3, rpc_url, rpc_connect = next_w3, next_rpc_url, next_connect
                except Exception:
                    logger.warning("failed to rotate BNB RPC after high error round", exc_info=True)
                    pass
            scan_time = time.monotonic()
            for pool_name in selected_pools:
                last_pool_scan[pool_name] = scan_time
            print(json.dumps({"ok": True, "round": round_no, "summary": payload["summary"], "paths": paths}, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        stop_requested = True
    finally:
        try:
            if previous_sigint is not None:
                signal.signal(signal.SIGINT, previous_sigint)
        except (ValueError, OSError):
            pass
    if stop_requested:
        print(json.dumps({"ok": True, "stopped": True, "round": round_no}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
