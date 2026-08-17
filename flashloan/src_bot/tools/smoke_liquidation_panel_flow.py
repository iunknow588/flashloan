from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from web.control_panel import app


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _request_json(client: Any, path: str) -> dict[str, Any]:
    response = client.get(path)
    try:
        payload = response.get_json() if response.is_json else None
    except Exception:
        payload = None
    return {
        "path": path,
        "status_code": int(response.status_code),
        "json": payload if isinstance(payload, dict) else {},
        "content_type": response.headers.get("content-type"),
    }


def _ok_status(result: dict[str, Any], *, allow_400: bool = False) -> bool:
    status = int(result.get("status_code") or 0)
    if allow_400 and status == 400:
        return True
    return 200 <= status < 300


def _first_account(accounts_payload: dict[str, Any]) -> str | None:
    for row in accounts_payload.get("accounts") or []:
        account = str((row or {}).get("account") or "").strip()
        if account:
            return account
    return None


def build_smoke_report(
    *,
    market_id: str = "bnb-venus-core",
    chain_id: int = 56,
    account: str | None = None,
    page_size: int = 20,
) -> dict[str, Any]:
    query = urlencode({"market_id": market_id, "chain_id": chain_id, "page_size": page_size})
    with app.test_client() as client:
        page = client.get("/liquidation")
        borrow_pool = _request_json(client, f"/api/liquidation/borrow-pool?{query}")
        accounts = _request_json(client, f"/api/liquidation/accounts?{query}")
        selected_account = account or _first_account(accounts.get("json") or {})
        cached = (
            _request_json(
                client,
                "/api/liquidation/account/cached?"
                + urlencode({"account": selected_account, "market_id": market_id, "chain_id": chain_id}),
            )
            if selected_account
            else {"status_code": 0, "json": {}, "path": None}
        )
        payload = (
            _request_json(
                client,
                "/api/liquidation/account/payload?"
                + urlencode({"account": selected_account, "market_id": market_id, "chain_id": chain_id}),
            )
            if selected_account
            else {"status_code": 0, "json": {}, "path": None}
        )

    borrow_summary = (borrow_pool.get("json") or {}).get("summary") or {}
    registry_window = (accounts.get("json") or {}).get("registry_window") or {}
    payload_json = payload.get("json") or {}
    current_total = int(borrow_summary.get("current_pool_account_count") or borrow_summary.get("registry_account_count") or 0)
    scanned = int(borrow_summary.get("scanned_account_count") or 0)
    pending = int(borrow_summary.get("pending_account_count") or 0)
    registry_total = int(registry_window.get("total_count") or 0)
    health_report_count = int(registry_window.get("health_report_count") or 0)
    pending_health_count = int(registry_window.get("pending_health_count") or 0)
    checks = {
        "page_load": {
            "ok": 200 <= int(page.status_code) < 300,
            "detail": f"GET /liquidation -> {page.status_code}",
        },
        "borrow_pool_api": {
            "ok": _ok_status(borrow_pool),
            "detail": f"status={borrow_pool.get('status_code')} source={borrow_summary.get('scan_response_source')}",
        },
        "current_pool_progress": {
            "ok": current_total > 0 and current_total >= scanned and current_total >= pending,
            "detail": f"current={current_total} scanned={scanned} pending={pending}",
        },
        "accounts_api_progress": {
            "ok": registry_total > 0 and registry_total >= health_report_count and registry_total >= pending_health_count,
            "detail": f"total={registry_total} health={health_report_count} pending={pending_health_count}",
        },
        "account_cached_api": {
            "ok": bool(selected_account) and _ok_status(cached),
            "detail": f"account={selected_account or '-'} status={cached.get('status_code')}",
        },
        "bnb_payload_observation_block": {
            "ok": (
                str(market_id).lower() != "bnb-venus-core"
                or (
                    int(payload.get("status_code") or 0) == 400
                    and "venus_core_observation_only" in (payload_json.get("blocked_reasons") or [])
                )
            ),
            "detail": f"status={payload.get('status_code')} reasons={payload_json.get('blocked_reasons') or []}",
        },
    }
    return {
        "generated_at": _utc_now(),
        "market_id": market_id,
        "chain_id": chain_id,
        "selected_account": selected_account,
        "ok": all(item["ok"] for item in checks.values()),
        "checks": checks,
        "responses": {
            "borrow_pool": borrow_pool,
            "accounts": accounts,
            "account_cached": cached,
            "account_payload": payload,
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Panel Flow Smoke",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Market: `{report.get('market_id')}`",
        f"- Chain ID: `{report.get('chain_id')}`",
        f"- Account: `{report.get('selected_account') or '-'}`",
        f"- OK: `{report.get('ok')}`",
        "",
        "| Check | OK | Detail |",
        "|---|---:|---|",
    ]
    for name, item in (report.get("checks") or {}).items():
        lines.append(f"| {name} | `{bool(item.get('ok'))}` | {item.get('detail') or '-'} |")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "liquidation_panel_flow_smoke.json"
    md_path = output_dir / "liquidation_panel_flow_smoke.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only liquidation panel flow smoke test.")
    parser.add_argument("--market-id", default="bnb-venus-core")
    parser.add_argument("--chain-id", type=int, default=56)
    parser.add_argument("--account", default="")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=SRC_ROOT / "runtime" / "reports" / "liquidation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    report = build_smoke_report(
        market_id=args.market_id,
        chain_id=args.chain_id,
        account=args.account.strip() or None,
        page_size=args.page_size,
    )
    paths = write_report(report, args.output_dir)
    print(json.dumps({"ok": report["ok"], "paths": paths, "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
