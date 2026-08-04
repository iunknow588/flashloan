from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files, resolve_env_path
from db.storage import (
    EXPECTED_SCHEMA_MIGRATION_IDS,
    ensure_database_schema,
    load_latest_liquidation_account_reports,
    load_liquidation_borrow_health_pool,
    load_liquidation_borrow_health_scan_batches,
    load_liquidation_core_opportunity_pool,
    load_liquidation_execution_attempts_for_account,
    load_liquidation_high_frequency_pool,
    load_liquidation_scan_config_library,
    load_recent_liquidation_execution_attempts,
    load_recent_liquidation_failure_samples,
    liquidation_account_registry_stats,
    liquidation_discovery_scan_progress,
    liquidation_execution_attempt_stats,
    load_schema_migrations,
)


load_env_files(__file__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_core_opportunity(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    row = rows[0]
    return {
        "account": row.get("account"),
        "health_factor": row.get("health_factor"),
        "priority_score": row.get("priority_score"),
        "estimated_operator_net_profit_usd": row.get("estimated_operator_net_profit_usd"),
        "estimated_gas_cost_usd": row.get("estimated_gas_cost_usd"),
        "best_debt_asset": row.get("best_debt_asset"),
        "best_collateral_asset": row.get("best_collateral_asset"),
        "static_call_status": row.get("static_call_status"),
        "payload_state": row.get("payload_state"),
        "blocked_reasons": row.get("blocked_reasons") or [],
        "last_scanned_at": row.get("last_scanned_at"),
    }


def build_liquidation_observation_report(database_url: str) -> dict[str, Any]:
    ensure_database_schema(database_url)
    schema = load_schema_migrations(database_url)
    registry = liquidation_account_registry_stats(database_url)
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    discovery = liquidation_discovery_scan_progress(database_url, pool_address) if pool_address else {}
    borrow_rows = load_liquidation_borrow_health_pool(database_url, limit=10)
    high_frequency_rows = load_liquidation_high_frequency_pool(database_url, limit=10)
    core_rows = load_liquidation_core_opportunity_pool(database_url, limit=10)
    batches = load_liquidation_borrow_health_scan_batches(database_url, limit=10)
    attempts = load_recent_liquidation_execution_attempts(database_url, limit=10)
    failures = load_recent_liquidation_failure_samples(database_url, limit=10)
    execution_stats = liquidation_execution_attempt_stats(database_url)
    latest_reports = load_latest_liquidation_account_reports(database_url, limit=10)
    scan_configs = load_liquidation_scan_config_library(database_url, limit=20)
    recent_attempts_for_latest = []
    for item in latest_reports[:3]:
        account = str(item.get("account") or "")
        if not account:
            continue
        recent_attempts_for_latest.append(
            {
                "account": account,
                "attempts": load_liquidation_execution_attempts_for_account(database_url, account, limit=3),
            }
        )
    return {
        "generated_at": _utc_now(),
        "database": {"configured": True, "pool_address": pool_address or None},
        "schema": {
            "expected_migrations": list(EXPECTED_SCHEMA_MIGRATION_IDS),
            "applied_count": len(schema),
            "up_to_date": len({row["migration_id"] for row in schema}) >= len(EXPECTED_SCHEMA_MIGRATION_IDS),
            "latest_migrations": schema[-5:],
        },
        "registry": registry,
        "discovery": discovery,
        "borrow_pool": {"count": len(borrow_rows), "top_rows": borrow_rows},
        "high_frequency_pool": {"count": len(high_frequency_rows), "top_rows": high_frequency_rows},
        "core_opportunities": {"count": len(core_rows), "top_rows": core_rows, "top_opportunity": _latest_core_opportunity(core_rows)},
        "scan_batches": {"count": len(batches), "latest": batches[0] if batches else None},
        "scan_config_library": {"count": len(scan_configs), "configs": scan_configs},
        "execution": {
            "stats": execution_stats,
            "recent_attempts": attempts,
            "recent_failures": failures,
            "latest_account_reports": latest_reports,
            "recent_attempts_by_account": recent_attempts_for_latest,
        },
    }


def write_liquidation_observation_report(report: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def default_output_path() -> Path:
    return resolve_env_path("LIQUIDATION_OBSERVATION_REPORT_FILE", "runtime/reports/liquidation/daily.json", SRC_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only liquidation observation report.")
    parser.add_argument("--output", default=str(default_output_path()))
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    report = build_liquidation_observation_report(database_url)
    path = write_liquidation_observation_report(report, args.output)
    print(
        json.dumps(
            {
                "output": str(path),
                "generated_at": report["generated_at"],
                "borrow_count": report["borrow_pool"]["count"],
                "core_count": report["core_opportunities"]["count"],
                "attempt_count": report["execution"]["stats"]["total"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
