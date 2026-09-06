from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from bot.execution.bnb_runtime import connect_bnb_rpc_with_retries, read_bnb_runtime_metrics
from bot.execution.venus_abis import VENUS_CORE_COMPTROLLER_ADDRESS
from bot.execution.venus_liquidation_scan import VenusScanConfig, build_venus_user_report
from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR, build_venus_market_static_table, utc_now
from dex.intent_trade.chains.bnb import config as bnb


def _load_accounts(path: Path, limit: int = 5) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8")) if path.suffix.lower() == ".json" else None
    accounts: list[str]
    if isinstance(data, dict):
        rows = data.get("accounts") or []
        accounts = [row.get("account") if isinstance(row, dict) else row for row in rows]
    elif isinstance(data, list):
        accounts = [row.get("account") if isinstance(row, dict) else row for row in data]
    else:
        accounts = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [str(account) for account in accounts if str(account or "").strip()][: max(0, int(limit or 0))]


def _markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks") or {}
    lines = [
        "# BNB Venus Readonly Smoke Report",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- RPC URL: `{report.get('rpc_url')}`",
        f"- Chain ID: `{report.get('chain_id')}`",
        f"- Execution enabled: `{report.get('execution_enabled')}`",
        "",
        "| Check | OK | Detail |",
        "|---|---:|---|",
    ]
    for name, item in checks.items():
        lines.append(f"| {name} | `{bool(item.get('ok'))}` | {item.get('detail') or item.get('error') or '-'} |")
    return "\n".join(lines) + "\n"


def build_smoke_report(
    w3: Any,
    *,
    rpc_url: str,
    comptroller: str = VENUS_CORE_COMPTROLLER_ADDRESS,
    market_limit: int = 3,
    accounts: list[str] | None = None,
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    chain_id = None
    try:
        chain_id = int(w3.eth.chain_id)
        checks["bnb_rpc"] = {"ok": chain_id == 56, "detail": f"chain_id={chain_id}"}
    except Exception as exc:
        checks["bnb_rpc"] = {"ok": False, "error": str(exc)}

    market_table: dict[str, Any] = {}
    try:
        market_table = build_venus_market_static_table(
            w3,
            comptroller_address=comptroller,
            rpc_url=rpc_url,
            limit=market_limit,
        )
        checks["venus_market_table"] = {"ok": bool(market_table.get("markets")), "detail": f"markets={market_table.get('market_count', 0)}"}
        checks["venus_comptroller"] = {"ok": bool(market_table.get("comptroller_address")), "detail": str(market_table.get("comptroller_address"))}
        checks["venus_oracle"] = {"ok": bool(market_table.get("oracle_address")), "detail": str(market_table.get("oracle_address") or "")}
    except Exception as exc:
        checks["venus_market_table"] = {"ok": False, "error": str(exc)}

    runtime_metrics = read_bnb_runtime_metrics(w3)
    checks["bnb_gas_price"] = {
        "ok": bool((runtime_metrics.get("gas_price") or {}).get("ok")),
        "detail": str((runtime_metrics.get("gas_price") or {}).get("gas_price_gwei")),
        "error": (runtime_metrics.get("gas_price") or {}).get("error"),
    }
    checks["bnb_native_usd_price"] = {
        "ok": bool((runtime_metrics.get("native_usd_price") or {}).get("ok")),
        "detail": str((runtime_metrics.get("native_usd_price") or {}).get("bnb_usd")),
        "error": (runtime_metrics.get("native_usd_price") or {}).get("error"),
    }

    account_reports = []
    for account in accounts or []:
        try:
            account_reports.append({"ok": True, **build_venus_user_report(w3, comptroller, account, market_table, config=VenusScanConfig(max_candidates=5))})
        except Exception as exc:
            account_reports.append({"ok": False, "account": account, "error": str(exc)})
    if accounts:
        checks["venus_account_scan"] = {
            "ok": any(row.get("ok") for row in account_reports),
            "detail": f"accounts={len(account_reports)}",
        }

    return {
        "network": "bnb",
        "chain_id": chain_id,
        "protocol": "venus_core",
        "generated_at": utc_now(),
        "rpc_url": rpc_url,
        "execution_enabled": False,
        "checks": checks,
        "runtime_metrics": runtime_metrics,
        "market_table_preview": market_table,
        "account_reports": account_reports,
        "ok": all(item.get("ok") for item in checks.values()) if checks else False,
    }


def write_smoke_report(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "venus_readonly_smoke_report.json"
    md_path = output_dir / "venus_readonly_smoke_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only BNB Venus smoke test.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--rpc-retries", type=int, default=3)
    parser.add_argument("--rpc-backoff-seconds", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--comptroller", default=VENUS_CORE_COMPTROLLER_ADDRESS)
    parser.add_argument("--market-limit", type=int, default=3)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_borrower_registry.json")
    parser.add_argument("--account-limit", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    w3, rpc_url, rpc_connect = connect_bnb_rpc_with_retries(
        args.rpc_url or list(bnb.rpc_urls_from_env()),
        attempts=args.rpc_retries,
        backoff_seconds=args.rpc_backoff_seconds,
    )
    accounts = _load_accounts(args.accounts_file, limit=args.account_limit) if args.account_limit > 0 else []
    report = build_smoke_report(
        w3,
        rpc_url=rpc_url,
        comptroller=args.comptroller,
        market_limit=args.market_limit,
        accounts=accounts,
    )
    report["rpc_connect"] = rpc_connect
    paths = write_smoke_report(report, args.output_dir)
    print(json.dumps({"ok": report["ok"], "paths": paths, "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
