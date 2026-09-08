from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.candidate_pool_supply import summarize_robinhood_candidate_pool_supply


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether Robinhood candidate pool supply is sufficient.")
    parser.add_argument(
        "--market-cache",
        type=Path,
        default=SRC_ROOT / "runtime" / "cache" / "robinhood_dex_market_pools.json",
    )
    parser.add_argument(
        "--static-table",
        type=Path,
        default=SRC_ROOT
        / "runtime"
        / "evidence"
        / "robinhood"
        / "dex_discovery"
        / "20260822T114623Z"
        / "pool_static_table.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood" / "candidate_pool_supply",
    )
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--strict-monitoring", action="store_true")
    parser.add_argument("--strict-route-search", action="store_true")
    args = parser.parse_args()

    try:
        report = summarize_robinhood_candidate_pool_supply(
            market_cache=_read_json(args.market_cache),
            static_table=_read_json(args.static_table) if args.static_table.exists() else {},
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if not args.no_write:
        evidence_dir = args.output_dir / _stamp()
        report = report | {
            "marketCache": str(args.market_cache),
            "staticTable": str(args.static_table),
            "reportPath": str(evidence_dir / "report.json"),
        }
        _write_json(evidence_dir / "report.json", report)
        _write_json(args.output_dir / "latest.json", report)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict_route_search and not report["sufficientForRouteSearch"]:
        return 2
    if args.strict_monitoring and not report["sufficientForMonitoring"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
