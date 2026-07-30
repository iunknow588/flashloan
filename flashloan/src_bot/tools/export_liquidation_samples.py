from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from db.storage import load_latest_liquidation_account_reports
from execution.liquidation_samples import write_liquidation_sample_library


load_env_files(__file__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export liquidation sample library from the latest account reports.")
    parser.add_argument(
        "--output",
        default=str(SRC_ROOT / "runtime" / "samples" / "liquidation_candidates"),
        help="Output directory for index.json and per-sample JSON files.",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max accounts to inspect from liquidation_accounts.")
    parser.add_argument("--deadline-seconds", type=int, default=300, help="Deadline offset used when building payload samples.")
    parser.add_argument("--executor", default=os.getenv("LIQUIDATION_EXECUTOR_ADDRESS", "").strip(), help="Optional executor address for payload samples.")
    parser.add_argument("--router", default=os.getenv("DEX_ROUTER_ADDRESS", "").strip(), help="Optional router address for payload samples.")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    reports = load_latest_liquidation_account_reports(database_url, limit=args.limit)
    normalized_reports = []
    for item in reports:
        report = dict(item.get("report") or {})
        summary = dict(item.get("summary") or {})
        report.setdefault("account", item.get("account"))
        report.setdefault("summary", summary)
        report.setdefault("status", item.get("last_status"))
        report.setdefault("health_factor", item.get("last_health_factor"))
        report.setdefault("source", item.get("source"))
        report.setdefault("context", {})
        report["context"] = {
            **(report.get("context") or {}),
            "source": item.get("source"),
            "scan_start_at": item.get("scan_start_at"),
            "scan_end_at": item.get("scan_end_at"),
            "last_scanned_at": item.get("last_scanned_at"),
        }
        normalized_reports.append(report)
    manifest = write_liquidation_sample_library(
        normalized_reports,
        args.output,
        executor_address=args.executor,
        router_address=args.router,
        deadline_seconds=args.deadline_seconds,
    )
    print(
        json.dumps(
            {
                "output": args.output,
                "source_count": manifest.get("source_count"),
                "ready_labels": [item["label"] for item in manifest.get("samples", []) if item.get("status") == "ready"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
